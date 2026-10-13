"""NAV job ads (pam-stilling-feed): currently active ads whose employer org number is ours.

Source and terms. NAV's job vacancy feed (https://navikt.github.io/pam-stilling-feed/) says
"Anyone can use this API free of charge" under https://arbeidsplassen.nav.no/vilkar-api. We read
it with the public token NAV publishes at /api/publicToken, which is not tied to any account, so
anyone can reproduce a run. The terms require that ads are removed as soon as they become
inactive or deleted, so an ad is published only after a fresh detail request in this run shows
status ACTIVE and an expiry in the future. Contact persons and the free-text description are
stripped before the response is stored as a snapshot.

How. The feed has no employer filter and the org number is only in each ad's detail
(`ad_content.employer.orgnr`), so a full walk is far beyond a run's budget. `prepare` loads a
declared prebuilt index (scripts/build_nav_index.py, src/kilde/data/nav_jobs_index.json.gz), walks
the feed pages added since the index was built, and fetches details only for new or changed
active ads whose business name looks like a batch company's legal name. The name is only a hint
for which details to fetch; a match is always the exact org number in the ad. NAV ads often name
the workplace (underenhet) number, so an ad also matches when Enhetsregisteret, checked in this
run, lists that workplace under our org number.

Request cost per batch: 1 token + new feed pages (about 1 per 1,000 feed changes since the
index was built, capped) + details for name-hinted new ads + 1 Enhetsregisteret call per 100
workplace numbers + 1 detail per published ad (capped per company).
"""
from __future__ import annotations

import gzip
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..http import BudgetExhausted, Fetcher
from ..model import OFFICIAL_REGISTRY, PUBLIC_JOB_FEED, CompanyContext, Evidence, Snapshot

BASE = "https://pam-stilling-feed.nav.no"
TOKEN_URL = f"{BASE}/api/publicToken"
BRREG = "https://data.brreg.no/enhetsregisteret/api"
HUMAN = "https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"
INDEX_PATH = Path(__file__).resolve().parents[1] / "data" / "nav_jobs_index.json.gz"
LICENCE = ("NAV pam-stilling-feed, free use under https://arbeidsplassen.nav.no/vilkar-api; "
           "shown only while the ad is ACTIVE at NAV")
BRREG_LICENCE = "NLOD 2.0, Brønnøysundregistrene"
METHOD = "nav_feedentry_v1"
JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")

MAX_FEED_PAGES = int(os.environ.get("KILDE_NAV_MAX_PAGES", "100"))
MAX_VERIFY_PER_COMPANY = int(os.environ.get("KILDE_NAV_MAX_ADS", "10"))
MAX_NEW_DETAILS_PER_COMPANY = 3

SUFFIXES = {"as", "asa", "ans", "da", "sa", "ba", "ks", "iks", "kf", "nuf", "enk", "sf", "fkf", "a/s", "stiftelse",
            "stiftelsen", "the", "ltd", "ab", "aps", "gmbh"}


def norm_name(name: str | None) -> str:
    """Lowercase, drop legal-form words and punctuation: 'Fjord Bakeri AS' -> 'fjord bakeri'."""
    if not name:
        return ""
    words = re.findall(r"[0-9a-zæøåäöü]+(?:/[a-z]+)?", name.lower())
    return " ".join(w for w in words if w not in SUFFIXES)


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))  # trimmed after exact-span repair


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _location(locs: list[dict] | None) -> str | None:
    out: list[str] = []
    for loc in locs or []:
        place = loc.get("city") or loc.get("municipal") or loc.get("county")
        if not place:
            continue
        label = place.title()
        if loc.get("municipal") and loc["municipal"].lower() != place.lower():
            label += f" ({loc['municipal'].title()})"
        if label not in out:
            out.append(label)
    return "; ".join(out) or None


@dataclass
class JobIndex:
    state: str = "ok"  # "ok" or "failed"
    reason: str | None = None
    built_at: str | None = None
    complete: bool = True  # False while the index still has active ads without details
    feed_last_page: str | None = None
    ads: dict[str, dict] = field(default_factory=dict)  # uuid -> indexed ad
    by_org: dict[str, list[str]] = field(default_factory=dict)
    token: str | None = None
    feed_snaps: list[Snapshot] = field(default_factory=list)  # feed pages read in this run
    feed_complete: bool = False
    stopped: set[str] = field(default_factory=set)  # uuids the feed now shows as not ACTIVE
    details: dict[str, tuple[Snapshot, dict]] = field(default_factory=dict)  # uuid -> detail read this run
    parents: dict[str, tuple[Snapshot, dict]] = field(default_factory=dict)  # workplace orgnr -> brreg record
    requests: int = 0

    @property
    def as_of(self) -> str:
        # The index build date, never a run timestamp: two runs must produce identical notes.
        return (self.built_at or "unknown")[:10]

    def headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Authorization": f"Bearer {self.token}"}


def load_index(path: Path | str | None = None) -> dict | None:
    p = Path(path or os.environ.get("KILDE_NAV_INDEX") or INDEX_PATH)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def redact(snap: Snapshot) -> tuple[Snapshot, dict]:
    """Drop contact persons and the description before the detail is stored as evidence."""
    data = snap.json()
    ad = data.get("ad_content")
    if isinstance(ad, dict):
        ad.pop("contactList", None)
        ad.pop("description", None)
    content = json.dumps(data, ensure_ascii=False, indent=1).encode()
    return Snapshot(url=snap.url, final_url=snap.final_url, status=snap.status, retrieved_at=snap.retrieved_at,
                    content=content, content_type=snap.content_type, redirects=snap.redirects,
                    from_cache=snap.from_cache), data


async def _fetch_token(f: Fetcher, idx: JobIndex) -> str | None:
    snap = await f.get(TOKEN_URL, cacheable=False)
    idx.requests += 1
    m = JWT.search(snap.text()) if snap.status == 200 else None
    return m.group(0) if m else None


async def _nav_get(f: Fetcher, idx: JobIndex, url: str, *, cacheable: bool = True) -> Snapshot:
    """GET with the public token; NAV rotates it without notice (seen within an hour), so a 401 refetches it once."""
    snap = await f.get(url, headers=idx.headers(), cacheable=cacheable)
    if snap.status == 401:
        token = await _fetch_token(f, idx)
        if token:
            idx.token = token
            snap = await f.get(url, headers=idx.headers(), cacheable=cacheable)
    if snap.status in (500, 502, 504):
        # NAV's database was overloaded on 2026-10-13 (sporadic 500s); one retry recovers most.
        snap = await f.get(url, headers=idx.headers(), cacheable=cacheable)
    return snap


# ---------------------------------------------------------------- batch level
async def prepare(f: Fetcher, orgs: list[str], names: dict[str, str], *, index_path: Path | str | None = None,
                  max_pages: int | None = None) -> JobIndex:
    idx = JobIndex()
    raw = load_index(index_path)
    if raw is None:
        idx.state, idx.reason = "failed", "NAV job index file missing"
        return idx
    idx.built_at = raw.get("built_at")
    idx.complete = bool(raw.get("complete", True))
    idx.feed_last_page = raw.get("feed_last_page")
    idx.ads = raw.get("ads") or {}
    idx.by_org = raw.get("by_org") or {}

    # Public token (rotates; fetched at run start).
    try:
        idx.token = await _fetch_token(f, idx)
        if not idx.token:
            idx.state, idx.reason = "failed", "NAV public token unavailable"
            return idx
    except BudgetExhausted:
        idx.state, idx.reason = "failed", "budget_exhausted"
        return idx
    except Exception as exc:  # noqa: BLE001
        idx.state, idx.reason = "failed", f"NAV public token request failed: {type(exc).__name__}"
        return idx

    batch = set(orgs)
    wanted = {norm_name(names.get(o)): o for o in orgs if norm_name(names.get(o))}
    candidates: dict[str, str] = {}  # uuid -> org whose name matched (a hint only)
    changed: dict[str, str] = {}  # uuid -> newest sistEndret seen in this run

    # Feed pages since the index was built.
    url = f"{BASE}{idx.feed_last_page}" if idx.feed_last_page else None
    pages = 0
    try:
        while url and pages < (max_pages or MAX_FEED_PAGES):
            snap = await _nav_get(f, idx, url, cacheable=False)
            idx.requests += 1
            pages += 1
            if snap.status != 200:
                break
            page = snap.json()
            idx.feed_snaps.append(snap)
            for item in page.get("items") or []:
                fe = item.get("_feed_entry") or {}
                uuid = fe.get("uuid") or item.get("id")
                if not uuid:
                    continue
                changed[uuid] = fe.get("sistEndret") or ""
                if fe.get("status") != "ACTIVE":
                    idx.stopped.add(uuid)
                    candidates.pop(uuid, None)
                    continue
                idx.stopped.discard(uuid)
                known = idx.ads.get(uuid)
                if known and known.get("sistEndret") == fe.get("sistEndret"):
                    continue
                hit = wanted.get(norm_name(fe.get("businessName")))
                if hit and not known:
                    candidates[uuid] = hit
            nxt = page.get("next_url")
            if not nxt:
                idx.feed_complete = True
                break
            url = f"{BASE}{nxt}"
    except BudgetExhausted:
        pass
    except Exception:  # noqa: BLE001
        pass  # an incomplete walk only means fewer new ads; every published ad is re-verified below

    # Details for name-hinted new ads, capped per company.
    per_org: dict[str, int] = {}
    for uuid, org in candidates.items():
        if per_org.get(org, 0) >= MAX_NEW_DETAILS_PER_COMPANY:
            continue
        per_org[org] = per_org.get(org, 0) + 1
        try:
            snap = await _nav_get(f, idx, f"{BASE}/api/v1/feedentry/{uuid}")
            idx.requests += 1
        except BudgetExhausted:
            break
        except Exception:  # noqa: BLE001
            continue
        if snap.status != 200:
            continue
        rsnap, data = redact(snap)
        idx.details[uuid] = (rsnap, data)
        ad = data.get("ad_content") or {}
        orgnr = (ad.get("employer") or {}).get("orgnr")
        if data.get("status") == "ACTIVE" and orgnr:
            idx.ads[uuid] = {"uuid": uuid, "employer_orgnr": orgnr, "sistEndret": data.get("sistEndret"),
                             "title": ad.get("title"), "published": ad.get("published"), "new_in_run": True}
            idx.by_org.setdefault(orgnr, []).append(uuid)

    # Workplace numbers that may belong to a batch company: confirm the parent in Enhetsregisteret now.
    workplaces = sorted({a["employer_orgnr"] for a in idx.ads.values()
                         if a.get("employer_orgnr") and a["employer_orgnr"] not in batch
                         and (a.get("parent_orgnr") in batch or a.get("new_in_run"))})
    for i in range(0, len(workplaces), 100):
        chunk = workplaces[i:i + 100]
        try:
            snap = await f.get(f"{BRREG}/underenheter?size=100&organisasjonsnummer={','.join(chunk)}",
                               headers={"Accept": "application/json"})
            idx.requests += 1
        except BudgetExhausted:
            break
        except Exception:  # noqa: BLE001
            continue
        if snap.status != 200:
            continue
        for u in (snap.json().get("_embedded") or {}).get("underenheter", []):
            idx.parents[u["organisasjonsnummer"]] = (snap, u)
            parent = u.get("overordnetEnhet")
            if parent in batch:
                for uuid, a in idx.ads.items():
                    if a.get("employer_orgnr") == u["organisasjonsnummer"] and uuid not in idx.by_org.get(parent, []):
                        idx.by_org.setdefault(parent, []).append(uuid)
    return idx


# ---------------------------------------------------------------- per company
def _not_available(ctx: CompanyContext, idx: JobIndex, extra: str = "") -> None:
    snap = idx.feed_snaps[-1] if idx.feed_snaps else None
    span = _dump({"index_built_at": idx.built_at, "feed_walk_complete": idx.feed_complete,
                  "employer_orgnr": ctx.org, "active_ads": 0, "excludes_source": "FINN"})
    ev = ctx.add_evidence(snap, source_url=snap.url if snap else f"{BASE}/api/v1/feed", source_class=PUBLIC_JOB_FEED,
                          span=span, method="nav_index_lookup_v1", licence=LICENCE,
                          note=("Prebuilt index of every ACTIVE ad in NAV's feed (scripts/build_nav_index.py), "
                                "updated with the feed pages read in this run" + extra))
    ctx.mark("job_ad", "hiring", "not_available", ev,
             note=f"No active NAV job ads with this employer org number (index built {idx.as_of}, "
                  "updated from the feed in this run). NAV's feed excludes ads sourced from Finn.no, which "
                  "arbeidsplassen.nav.no still lists, so Finn-only ads are not checked here")


async def fetch_jobs(f: Fetcher, ctx: CompanyContext, index: JobIndex) -> None:
    if index.state != "ok":
        ctx.error("hiring", f"NAV jobs: {index.reason}")
        ctx.mark("job_ad", "hiring", "failed", note=f"NAV job feed unavailable: {index.reason}")
        ctx.module_states["hiring"] = "failed"
        return
    uuids = [u for u in dict.fromkeys(index.by_org.get(ctx.org, [])) if u not in index.stopped]
    uuids.sort(key=lambda u: (index.ads.get(u) or {}).get("published") or "", reverse=True)
    now = datetime.now(timezone.utc)
    published: list[Evidence] = []
    failures = 0
    budget_out = False
    for uuid in uuids[:MAX_VERIFY_PER_COMPANY]:
        got = index.details.get(uuid)
        if got is None:
            try:
                snap = await _nav_get(f, index, f"{BASE}/api/v1/feedentry/{uuid}")
                ctx.requests += 1
            except BudgetExhausted:
                budget_out = True
                break
            except Exception:  # noqa: BLE001
                failures += 1
                continue
            if snap.status != 200:
                failures += 1
                continue
            got = redact(snap)
            index.details[uuid] = got
        snap, data = got
        ad = data.get("ad_content") or {}
        emp = ad.get("employer") or {}
        orgnr = emp.get("orgnr")
        if data.get("status") != "ACTIVE" or not ad:
            continue  # stopped or inactive: never shown
        expires = _parse_dt(ad.get("expires"))
        if expires is not None and expires < now:
            continue
        parent_ev: Evidence | None = None
        if orgnr == ctx.org:
            match = {"method": "employer_orgnr", "detail": f"ad_content.employer.orgnr = {orgnr}"}
        elif orgnr in index.parents and index.parents[orgnr][1].get("overordnetEnhet") == ctx.org:
            psnap, prec = index.parents[orgnr]
            parent_ev = ctx.add_evidence(psnap, source_class=OFFICIAL_REGISTRY, method="brreg_subunits_v1",
                                         licence=BRREG_LICENCE,
                                         span=_dump({k: prec.get(k) for k in ("organisasjonsnummer", "navn",
                                                                                "overordnetEnhet")}))
            match = {"method": "employer_orgnr",
                     "detail": f"ad_content.employer.orgnr = {orgnr}, a registered sub-unit of {ctx.org} "
                               f"(Enhetsregisteret overordnetEnhet)"}
        else:
            continue  # the org number does not tie this ad to us: never matched by name
        published_at = ad.get("published")
        span = _dump({"uuid": data.get("uuid"), "status": data.get("status"),
                      "employer": {"name": emp.get("name"), "orgnr": orgnr}, "title": ad.get("title"),
                      "published": published_at, "expires": ad.get("expires")})
        ev = ctx.add_evidence(snap, source_class=PUBLIC_JOB_FEED, span=span,
                              effective_date=(published_at or "")[:10] or None, method=METHOD, licence=LICENCE,
                              note=("Contact persons and description removed from the stored copy. "
                                    f"Public ad page: {ad.get('link') or HUMAN.format(uuid=uuid)}"))
        value = {"title": ad.get("title"), "location": _location(ad.get("workLocations")),
                 "published": (published_at or "")[:10] or None, "expires": (ad.get("expires") or "")[:10] or None,
                 "employer_name": emp.get("name"), "employer_orgnr": orgnr,
                 "url": ad.get("link") or HUMAN.format(uuid=uuid), "uuid": uuid}
        ctx.add_claim("job_ad", "hiring", {k: v for k, v in value.items() if v is not None},
                      [ev] + ([parent_ev] if parent_ev else []), as_of=value.get("published"), match=match)
        published.append(ev)
    if published:
        more = len(uuids) - min(len(uuids), MAX_VERIFY_PER_COMPANY)
        ctx.add_claim("active_job_count", "hiring", len(published), published, as_of=index.as_of,
                      match={"method": "employer_orgnr",
                             "detail": f"Active NAV ads re-checked in this run with employer org number {ctx.org}"
                                       " or one of its registered sub-units"},
                      note=(f"At least {len(published)}: {more} more indexed ads were not re-checked "
                            "within this run's per-company cap") if more or budget_out else None)
        ctx.module_states["hiring"] = "available"
        return
    if budget_out or (failures and failures == len(uuids[:MAX_VERIFY_PER_COMPANY])):
        reason = "budget_exhausted" if budget_out else "NAV ad detail requests failed"
        ctx.error("hiring", reason)
        ctx.mark("job_ad", "hiring", "failed", note=reason)
        ctx.module_states["hiring"] = "failed"
        return
    if not index.complete or not index.feed_complete:
        # A partial index, or a catch-up walk that hit its cap or failed, cannot prove absence.
        ctx.mark("job_ad", "hiring", "failed", note="absence not verified")
        ctx.module_states["hiring"] = "failed"
        return
    _not_available(ctx, index, "; indexed ads for this org number are no longer active" if uuids else "")
    ctx.module_states["hiring"] = "not_available"
