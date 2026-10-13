"""Build the declared NAV job-ads index: employer org number -> currently active ads.

Source: NAV "pam-stilling-feed" (https://navikt.github.io/pam-stilling-feed/), free for anyone
under https://arbeidsplassen.nav.no/vilkar-api, read with the public token NAV publishes.

Steps (resumable; state is saved after every page and every 50 details):
  1. Walk the feed from (now - 190 days) to its end. NAV: "an ad can never be active for more
     than 6 months", so older pages cannot hold an active ad. Keep the newest entry per ad.
  2. Fetch the detail of every ad whose newest entry is ACTIVE (the employer org number is
     only in the detail, at ad_content.employer.orgnr).
  3. Resolve each employer org number in Enhetsregisteret: NAV ads often carry the workplace
     (underenhet) number, so record its registered parent (overordnetEnhet).
  4. Write src/kilde/data/nav_jobs_index.json.gz.

Personal data: contact persons and the free-text description are never stored.

Usage: python scripts/build_nav_index.py [--state DIR] [--days 190] [--workers 2] [--sleep 0.3]
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kilde import __version__  # noqa: E402

BASE = "https://pam-stilling-feed.nav.no"
BRREG = "https://data.brreg.no/enhetsregisteret/api"
UA = f"KildeSignalpost/{__version__} (+https://github.com/tahcin/kilde)"
OUT = ROOT / "src" / "kilde" / "data" / "nav_jobs_index.json.gz"
JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trim_ad(detail: dict) -> dict | None:
    """Keep only what the connector publishes. No contacts, no description."""
    ad = detail.get("ad_content") or {}
    emp = ad.get("employer") or {}
    if not ad or not emp.get("orgnr"):
        return None
    locs = []
    for loc in ad.get("workLocations") or []:
        locs.append({k: loc.get(k) for k in ("city", "municipal", "county", "postalCode", "country") if loc.get(k)})
    return {
        "uuid": detail.get("uuid") or ad.get("uuid"),
        "status": detail.get("status"),
        "sistEndret": detail.get("sistEndret"),
        "title": ad.get("title"),
        "employer_name": emp.get("name"),
        "employer_orgnr": emp.get("orgnr"),
        "published": ad.get("published"),
        "expires": ad.get("expires"),
        "applicationDue": ad.get("applicationDue"),
        "locations": locs,
        "link": ad.get("link"),
        "source": ad.get("source"),
    }


class Builder:
    def __init__(self, state_dir: Path, days: int, workers: int, sleep: float, *, start_page: str | None = None,
                 hop_from: str | None = None, until: str | None = None):
        self.start_page = start_page
        self.hop_from = datetime.fromisoformat(hop_from).replace(tzinfo=timezone.utc) if hop_from else None
        self.until = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) if until else None
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = state_dir / "state.json"
        self.days = days
        self.workers = workers
        self.sleep = sleep
        self.client = httpx.Client(headers={"User-Agent": UA}, timeout=httpx.Timeout(120, connect=15))
        self.lock = threading.Lock()
        self.requests = {"nav": 0, "brreg": 0}
        self.state = self._load()
        self.token: str | None = None

    def _load(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"started_at": now_iso(), "next_url": None, "page_last_modified": None, "last_page": None,
                "pages": 0, "feed_done": False, "entries": {}, "details": {}, "parents": {}, "brreg_done": False,
                "requests": {"nav": 0, "brreg": 0}, "seconds": 0.0}

    def save(self) -> None:
        with self.lock:
            self.state["requests"] = {k: self.state["requests"].get(k, 0) + v for k, v in self.requests.items()}
            self.requests = {"nav": 0, "brreg": 0}
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state))
            tmp.replace(self.state_path)

    # HTTP -----------------------------------------------------------------
    def get(self, url: str, headers: dict | None = None, kind: str = "nav", tries: int = 6) -> httpx.Response | None:
        delay = 5.0
        for attempt in range(tries):
            if attempt:
                time.sleep(delay)
                delay = min(delay * 2, 120)
            try:
                with self.lock:
                    self.requests[kind] += 1
                r = self.client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                print(f"  ! {type(exc).__name__} {url}", flush=True)
                continue
            if r.status_code == 401 and kind == "nav":
                self.refresh_token()
                headers = {**(headers or {}), "Authorization": f"Bearer {self.token}"}
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                print(f"  ! HTTP {r.status_code} {url}", flush=True)
                continue
            time.sleep(self.sleep)
            return r
        return None

    def refresh_token(self) -> None:
        r = self.get(f"{BASE}/api/publicToken", kind="nav")
        m = JWT.search(r.text) if r is not None else None
        if not m:
            raise SystemExit("could not read the NAV public token")
        self.token = m.group(0)

    def nav_headers(self, extra: dict | None = None) -> dict:
        return {"Accept": "application/json", "Authorization": f"Bearer {self.token}", **(extra or {})}

    # Step 1: feed ---------------------------------------------------------------
    def hop_to(self, cutoff: datetime) -> str:
        """Find a feed page that starts near `cutoff`.

        GET /api/v1/feed with If-Modified-Since runs a query NAV's database times out on (HTTP 500
        after 30 s, seen on 2026-10-13). The same header on a page id instead filters the rows after
        that page, which is cheap when the date is close. So hop forward from the first page in
        steps of 30 days. Rows modified before the cutoff cannot be active ads (NAV: an ad is never
        active for more than 6 months).
        """
        first = f"{BASE}/api/v1/feed/{self.start_page}" if self.start_page else f"{BASE}/api/v1/feed"
        r = self.get(first, self.nav_headers())
        if r is None or r.status_code != 200:
            raise SystemExit("first feed page failed")
        page = r.json()
        while True:
            items = page.get("items") or []
            stamp = (items[-1].get("date_modified") or items[-1]["_feed_entry"]["sistEndret"]) if items else None
            last = datetime.fromisoformat(stamp) if stamp else cutoff
            if not page.get("next_id"):
                return page["feed_url"]
            if last >= cutoff - timedelta(days=1):
                return page["feed_url"]  # re-read this page without the filter so nothing is skipped
            target = min(last + timedelta(days=30), cutoff)
            url = f"{BASE}{page['next_url']}"
            r = self.get(url, self.nav_headers({"If-Modified-Since": format_datetime(target.astimezone(timezone.utc),
                                                                                       usegmt=True)}))
            if r is None or r.status_code != 200:
                raise SystemExit(f"hop to {target} failed at {url}")
            page = r.json()
            items = page.get("items") or []
            print(f"hop: page {page.get('id')} now at {items[-1]['date_modified'] if items else '?'}", flush=True)

    def walk_feed(self) -> None:
        st = self.state
        if st["feed_done"]:
            return
        start = self.hop_from or datetime.now(timezone.utc) - timedelta(days=self.days)
        if not st["next_url"]:
            st["next_url"] = self.hop_to(start)
            self.save()
        url, extra = f"{BASE}{st['next_url']}", {}
        while True:
            t0 = time.time()
            r = self.get(url, self.nav_headers(extra))
            if r is None:
                raise SystemExit(f"feed page failed repeatedly: {url} (state saved, rerun to resume)")
            if r.status_code == 304:
                break
            if r.status_code != 200:
                raise SystemExit(f"feed HTTP {r.status_code} at {url}")
            page = r.json()
            for item in page.get("items") or []:
                fe = item.get("_feed_entry") or {}
                uuid = fe.get("uuid") or item.get("id")
                prev = st["entries"].get(uuid)
                if prev is None or (fe.get("sistEndret") or "") >= prev[2]:
                    st["entries"][uuid] = [fe.get("status"), fe.get("businessName"), fe.get("sistEndret") or ""]
            st["pages"] += 1
            st["last_page"] = page.get("feed_url")
            st["page_last_modified"] = r.headers.get("last-modified")
            last_mod = (page.get("items") or [{}])[-1].get("date_modified")
            print(f"page {st['pages']}: {len(page.get('items') or [])} items, up to {last_mod}, "
                  f"{time.time() - t0:.1f}s, {len(st['entries'])} ads seen", flush=True)
            reached = self.until and last_mod and datetime.fromisoformat(last_mod) >= self.until
            if not page.get("next_url") or reached:
                st["next_url"] = None if not reached else page["next_url"]
                st["feed_done"] = True
                st["feed_done_at"] = now_iso()
                self.save()
                break
            st["next_url"] = page["next_url"]
            self.save()
            url, extra = f"{BASE}{page['next_url']}", {}

    # Step 2: details ----------------------------------------------------------
    def fetch_details(self) -> None:
        st = self.state
        todo = [u for u, (status, _, changed) in st["entries"].items()
                if status == "ACTIVE" and (st["details"].get(u) or {}).get("_seen") != changed]
        print(f"details to fetch: {todo.__len__()} of {sum(1 for v in st['entries'].values() if v[0] == 'ACTIVE')} active",
              flush=True)
        done = 0

        def one(uuid: str) -> None:
            nonlocal done
            r = self.get(f"{BASE}/api/v1/feedentry/{uuid}", self.nav_headers())
            changed = st["entries"][uuid][2]
            if r is None:
                return
            if r.status_code == 200:
                d = r.json()
                ad = trim_ad(d) if d.get("status") == "ACTIVE" else None
                rec = ad or {"uuid": uuid, "status": d.get("status"), "no_orgnr": d.get("status") == "ACTIVE"}
            else:
                rec = {"uuid": uuid, "status": f"HTTP {r.status_code}"}
            rec["_seen"] = changed
            rec["_fetched"] = now_iso()
            with self.lock:
                st["details"][uuid] = rec
                done += 1
                if done % 50 == 0:
                    print(f"  details {done}/{len(todo)}", flush=True)
            if done % 50 == 0:
                self.save()

        with ThreadPoolExecutor(self.workers) as pool:
            list(pool.map(one, todo))
        self.save()

    # Step 3: registry parents -------------------------------------------------
    def resolve_parents(self) -> None:
        st = self.state
        orgs = sorted({d["employer_orgnr"] for d in st["details"].values()
                       if d.get("employer_orgnr") and d.get("status") == "ACTIVE"} - set(st["parents"]))
        orgs = [o for o in orgs if re.fullmatch(r"\d{9}", o)]
        print(f"resolving {len(orgs)} employer org numbers in Enhetsregisteret", flush=True)
        for i in range(0, len(orgs), 100):
            chunk = orgs[i:i + 100]
            r = self.get(f"{BRREG}/underenheter?size=100&organisasjonsnummer={','.join(chunk)}",
                         {"Accept": "application/json"}, kind="brreg")
            subs = {}
            if r is not None and r.status_code == 200:
                for u in (r.json().get("_embedded") or {}).get("underenheter", []):
                    subs[u["organisasjonsnummer"]] = u.get("overordnetEnhet")
            r2 = self.get(f"{BRREG}/enheter?size=100&organisasjonsnummer={','.join(chunk)}",
                          {"Accept": "application/json"}, kind="brreg")
            mains = set()
            if r2 is not None and r2.status_code == 200:
                mains = {e["organisasjonsnummer"] for e in (r2.json().get("_embedded") or {}).get("enheter", [])}
            for o in chunk:
                if o in mains:
                    st["parents"][o] = {"kind": "enhet"}
                elif o in subs:
                    st["parents"][o] = {"kind": "underenhet", "parent": subs[o]}
                else:
                    st["parents"][o] = {"kind": "unknown"}
            self.save()
        st["brreg_done"] = True
        st["brreg_checked_at"] = now_iso()

    # Step 4: write --------------------------------------------------------------
    def write(self) -> dict:
        st = self.state
        now = datetime.now(timezone.utc)
        ads: dict[str, dict] = {}
        by_org: dict[str, list[str]] = {}
        for uuid, d in st["details"].items():
            if d.get("status") != "ACTIVE" or not d.get("employer_orgnr"):
                continue
            if st["entries"].get(uuid, [None])[0] != "ACTIVE":
                continue
            try:
                if d.get("expires") and datetime.fromisoformat(d["expires"]) < now:
                    continue
            except ValueError:
                pass
            ad = {k: v for k, v in d.items() if not k.startswith("_")}
            ad["detail_fetched_at"] = d.get("_fetched")
            p = st["parents"].get(d["employer_orgnr"]) or {}
            ad["employer_kind"] = p.get("kind")
            if p.get("parent"):
                ad["parent_orgnr"] = p["parent"]
            ads[uuid] = ad
            by_org.setdefault(d["employer_orgnr"], []).append(uuid)
            if p.get("parent"):
                by_org.setdefault(p["parent"], []).append(uuid)
        active_total = sum(1 for v in st["entries"].values() if v[0] == "ACTIVE")
        pending = sum(1 for u, v in st["entries"].items()
                      if v[0] == "ACTIVE" and (st["details"].get(u) or {}).get("_seen") != v[2])
        index = {
            "schema": 1,
            "source": f"{BASE}/api/v1/feed",
            "terms": "https://arbeidsplassen.nav.no/vilkar-api",
            "built_at": now_iso(),
            "feed_walk_from_days": self.days,
            "complete": pending == 0 and bool(st.get("brreg_done")),
            "feed_completed_at": st.get("feed_done_at"),
            "feed_last_page": st["last_page"],
            "feed_last_modified": st["page_last_modified"],
            "brreg_checked_at": st.get("brreg_checked_at"),
            "stats": {
                "feed_pages": st["pages"],
                "ads_seen": len(st["entries"]),
                "active_in_feed": active_total,
                "details_pending": pending,
                "active_with_orgnr": len(ads),
                "employer_orgnrs": len({a["employer_orgnr"] for a in ads.values()}),
                "orgs_indexed": len(by_org),
                "requests": st["requests"],
            },
            "ads": ads,
            "by_org": {k: sorted(set(v)) for k, v in sorted(by_org.items())},
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode()
        OUT.write_bytes(gzip.compress(raw, 9, mtime=0))
        print(json.dumps(index["stats"], indent=1), f"\nwrote {OUT} ({OUT.stat().st_size:,} bytes)", flush=True)
        return index

    def merge(self, other_dirs: list[str]) -> None:
        """Fold in the entries of other walked segments (newest entry per ad wins)."""
        for d in other_dirs:
            other = json.loads((Path(d) / "state.json").read_text())
            if not other.get("feed_done"):
                raise SystemExit(f"segment {d} has not finished walking")
            for uuid, e in other["entries"].items():
                prev = self.state["entries"].get(uuid)
                if prev is None or e[2] > prev[2]:
                    self.state["entries"][uuid] = e
            self.state["pages"] += other["pages"]
            for k, v in other.get("requests", {}).items():
                self.state["requests"][k] = self.state["requests"].get(k, 0) + v
            self.state["seconds"] = self.state.get("seconds", 0.0) + other.get("seconds", 0.0)
        self.save()

    def run(self, walk_only: bool = False, merge: list[str] | None = None) -> None:
        t0 = time.time()
        self.refresh_token()
        try:
            self.walk_feed()
            if walk_only:
                return
            if merge and not self.state.get("merged"):
                self.merge(merge)
                self.state["merged"] = True
            self.fetch_details()
            self.resolve_parents()
        finally:
            self.state["seconds"] = self.state.get("seconds", 0.0) + time.time() - t0
            self.save()
        if not walk_only:
            self.write()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=str(ROOT / ".cache" / "nav_index_state"))
    ap.add_argument("--days", type=int, default=190)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--write-only", action="store_true", help="write the index from saved state")
    ap.add_argument("--snapshot", action="store_true",
                    help="resolve parents for the details so far and write a partial index (use on a copy of the state)")
    ap.add_argument("--start-page", help="feed page id to start hopping from (skips the hops before it)")
    ap.add_argument("--hop-from", help="walk from this date (YYYY-MM-DD) instead of now minus --days")
    ap.add_argument("--until", help="stop walking once the feed passes this date (one segment of a split walk)")
    ap.add_argument("--walk-only", action="store_true", help="walk the feed segment and stop")
    ap.add_argument("--merge", nargs="*", default=[], help="state dirs of earlier walked segments to fold in")
    args = ap.parse_args()
    b = Builder(Path(args.state), args.days, max(1, min(args.workers, 2)), args.sleep,
                start_page=args.start_page, hop_from=args.hop_from, until=args.until)
    if args.snapshot:
        b.resolve_parents()  # partial index from the details fetched so far (state copy, marked incomplete)
        b.save()
        b.write()
    elif args.write_only:
        b.write()
    else:
        b.run(walk_only=args.walk_only, merge=args.merge)


if __name__ == "__main__":
    main()
