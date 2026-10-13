"""Build the declared website-candidate cache: org number -> candidate domains with their sources.

The website connector still verifies every candidate on the live site (org number on the page, or
registry phone/address plus name, with the Brreg name-collision check). This file only adds
CANDIDATES that the registry website, registry email domain and name-derived guesses miss.

Sources (all keyless, open licences; see src/kilde/data/website_seeds.LICENSE.md):
  osm_orgnr      OpenStreetMap objects tagged ref:NO:orgnr / ref:org_nr / no:orgnr / ref:orgnr with
                 website, contact:website, url or an email (Geofabrik norway-latest.osm.pbf, ODbL).
                 Sub-unit numbers are re-keyed to the registered parent.
  osm_phone      OpenStreetMap objects with a website and a phone that matches exactly one registry
                 entity (same phone rule as Overture).
  overture_phone Overture Maps Places (CDLA-Permissive-2.0) in Norway whose phone is the registry
                 phone or mobile of exactly one Brreg entity (sub-units count towards their parent).
  overture_email Overture places whose email domain is the registry email domain of exactly one entity.
  subunit        Brreg underenheter (NLOD 2.0): sub-unit hjemmeside and email domain, keyed to the parent.
  nav            NAV job ads (pam-stilling-feed, NAV API terms): ad_content.employer.homepage for ads in
                 src/kilde/data/nav_jobs_index.json.gz (and optional build state files), keyed to the
                 employer's parent org number.
  wikidata       Wikidata P856 (official website) on items with P2333 (Norwegian org number), CC0,
                 one QLever query.

Each org's list is ordered by number of agreeing sources (desc), then domain; at most MAX_PER_ORG.

Filters: registered domain only (company-specific subdomains of site builders kept as the host);
social, directory, booking and platform hosts dropped; free-mail domains dropped; candidates equal to
the org's own registry website or registry email domain dropped (the connector already has them);
domains matched by phone or email to more than MAX_SHARED orgs dropped (chains, administrators);
orgs matched by phone or email to more than MAX_MATCHED_PER_ORG domains lose those matches (data vendors).

Usage (needs duckdb, osmium, pyarrow, ijson, httpx, tldextract; use a scratch venv, not the project):
  python scripts/build_website_seeds.py --universe U.jsonl.gz --enheter enheter.json.gz \
      --underenheter underenheter.json.gz --osm norway-latest.osm.pbf --work DIR \
      [--overture-release 2026-09-23.1] [--nav-state .cache/nav_seg_b/state.json ...] [--no-nav-fetch]

Downloads: https://download.geofabrik.de/europe/norway-latest.osm.pbf,
https://data.brreg.no/enhetsregisteret/api/enheter/lastned and .../underenheter/lastned (gz JSON).
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "kilde" / "data" / "website_seeds.json.gz"
NAV_INDEX = ROOT / "src" / "kilde" / "data" / "nav_jobs_index.json.gz"
NAV_BASE = "https://pam-stilling-feed.nav.no"
UA = "KildeSignalpost/website-seeds (+https://github.com/tahcin/kilde)"
QLEVER = "https://qlever.dev/api/wikidata"
WIKIDATA_QUERY = ("PREFIX wdt: <http://www.wikidata.org/prop/direct/> "
                  "SELECT ?item ?org ?site WHERE { ?item wdt:P2333 ?org . ?item wdt:P856 ?site }")
MAX_SHARED = 3          # a phone/email-matched domain seeded for more orgs than this is a chain or administrator
MAX_DECLARED = 10       # same for domains declared by org number (group sites stay, mass hosts go)
MAX_MATCHED_PER_ORG = 3 # an org matched by phone/email to more domains than this is a data vendor or administrator
MAX_PER_ORG = 8         # candidates kept per org (most-corroborated first)
OSM_ORG_KEYS = ("ref:NO:orgnr", "ref:org_nr", "no:orgnr", "ref:orgnr", "ref:NO:org_nr", "orgnr")
OSM_WEB_KEYS = ("website", "contact:website", "url", "operator:website", "brand:website")
OSM_MAIL_KEYS = ("email", "contact:email")
OSM_PHONE_KEYS = ("phone", "contact:phone", "contact:mobile", "mobile")

FREE_MAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "live.no", "live.com",
    "msn.com", "icloud.com", "me.com", "mac.com", "yahoo.com", "yahoo.no", "online.no", "altibox.no", "lyse.net",
    "broadpark.no", "getmail.no", "frisurf.no", "c2i.net", "start.no", "tele2.no", "post.com", "mail.com",
    "epost.no", "chello.no", "bluecom.no", "ebnett.no", "hotmail.co.uk", "aol.com", "protonmail.com", "proton.me",
    "gmx.com", "gmx.net", "loqal.no", "netcom.no", "telenor.no", "nextgentel.no", "haugnett.no", "tdcadsl.no",
    "sensewave.com", "enivest.net", "tussa.com", "mimer.no", "kvamnet.no", "neasonline.no", "fiberbredband.no",
    "vikenfiber.no", "skynet.be", "yandex.com", "mail.ru", "qq.com", "163.com", "web.de", "hotmail.se",
    "outlook.dk", "live.se", "yahoo.co.uk", "ymail.com", "tiscali.no", "sf-nett.no", "eidsiva.net", "kvinnherad.net",
}
# Never a company's own site: social, directories, maps, marketplaces, booking and link services.
BLOCKED = {
    "facebook.com", "fb.com", "fb.me", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com",
    "youtu.be", "tiktok.com", "pinterest.com", "snapchat.com", "threads.net", "vimeo.com", "flickr.com",
    "finn.no", "proff.no", "gulesider.no", "1881.no", "google.com", "google.no", "goo.gl", "g.page", "mittanbud.no",
    "brreg.no", "purehelp.no", "forvalt.no", "yelp.com", "tripadvisor.com", "tripadvisor.no", "booking.com",
    "airbnb.com", "airbnb.no", "hotels.com", "expedia.com", "bit.ly", "linktr.ee", "wikipedia.org", "wikimedia.org",
    "wikidata.org", "openstreetmap.org", "apple.com", "maps.apple", "bing.com", "kvasir.no", "180.no", "opplysningen.no",
    "eniro.no", "firmasok.no", "norgeshandelen.no", "mittanbud.no", "anbudstorget.no", "byggstart.no", "visitnorway.com",
    "visitnorway.no", "ibooking.no", "timma.no", "bestille.no", "hairtools.no", "onlinebooking.no", "bookingsystem.no",
    "fresha.com", "treatwell.no", "matchi.se", "planyo.com", "simplybook.me", "booksy.com", "wolt.com", "foodora.no",
    "just-eat.no", "uber.com", "tinyurl.com", "ow.ly", "about.me", "messenger.com", "whatsapp.com", "wa.me",
    "t.me", "spotify.com", "soundcloud.com", "github.com", "medium.com", "trustpilot.com", "legelisten.no",
    "helsenorge.no", "nav.no", "arbeidsplassen.nav.no", "altinn.no", "regjeringen.no", "doctolib.no", "jobbnorge.no",
    "teamtailor.com", "webcruiter.no", "recman.no", "easycruit.com", "smartrecruiters.com", "workday.com",
}
# Site builders and hosting platforms: keep a company-specific subdomain host, drop the bare platform.
BUILDERS = (
    "wixsite.com", "wix.com", "webnode.no", "webnode.com", "webnode.page", "weebly.com", "wordpress.com",
    "blogspot.com", "squarespace.com", "business.site", "jimdosite.com", "jimdo.com", "github.io", "netlify.app",
    "vercel.app", "mystrikingly.com", "site123.me", "simplesite.com", "123hjemmeside.no", "hjemmeside.no",
    "one.com", "webflow.io", "carrd.co", "godaddysites.com", "shopify.com", "myshopify.com", "mozello.com",
    "strikingly.com", "framer.website", "notion.site", "ucraft.site", "yolasite.com", "tripod.com", "000webhostapp.com",
    "hjemmesidehuset.no", "dinside.no", "mamutweb.com", "wpengine.com", "azurewebsites.net", "herokuapp.com",
)
BUILDER_NAMES = {"blogspot", "webnode", "wix", "wixsite", "weebly", "jimdo", "jimdosite", "simplesite", "squarespace"}
GENERIC_LABELS = {"www", "m", "mobile", "en", "no", "nb", "web", "home", "site", "shop", "app", "mail", "booking"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(*a) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}]", *a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ domains
_EXTRACT = None


def _tld():
    global _EXTRACT
    if _EXTRACT is None:
        import tldextract
        _EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)  # bundled PSL snapshot, no network
    return _EXTRACT


def host_of(url: str) -> str:
    url = (url or "").strip().strip("<>\"' ")
    if not url or " " in url.strip():
        url = url.split()[0] if url.split() else ""
    if not url:
        return ""
    if "@" in url and "//" not in url:
        url = url.rsplit("@", 1)[1]
    try:
        h = urlsplit(url if "//" in url else f"https://{url}").hostname or ""
    except ValueError:
        return ""
    h = h.lower().strip(".")
    try:
        h = h.encode("idna").decode("ascii") if any(ord(c) > 127 for c in h) else h
    except UnicodeError:
        return ""
    return h


def _under(host: str, base: str) -> bool:
    return host == base or host.endswith("." + base)


def site_key(url_or_host: str) -> str | None:
    """Registered domain, or the company-specific host on a site builder; None for blocked or junk."""
    h = host_of(url_or_host)
    if not h or "." not in h or re.fullmatch(r"[\d.]+", h):
        return None
    ext = _tld()(h)
    if not ext.suffix or not ext.domain:
        return None
    reg = f"{ext.domain}.{ext.suffix}"
    if any(_under(h, b) for b in BLOCKED) or any(_under(reg, b) for b in BLOCKED):
        return None
    builders = BUILDERS + ((reg,) if ext.domain in BUILDER_NAMES else ())  # blogspot.in, webnode.se ...
    for b in builders:
        if _under(h, b):
            sub = h[: -len(b) - 1] if h != b else ""
            labels = [x for x in sub.split(".") if x and x not in GENERIC_LABELS]
            if not labels:
                return None
            return f"{labels[-1]}.{b}"
    return reg


def mail_domain(email: str) -> str | None:
    email = (email or "").strip().lower()
    if "@" not in email:
        return None
    d = email.rsplit("@", 1)[1].strip(" .>;,")
    if d in FREE_MAIL or any(d.endswith("." + x) for x in FREE_MAIL):
        return None
    k = site_key(d)
    if k and (k in FREE_MAIL):
        return None
    return k


def phones_of(raw: str | None) -> set[str]:
    """Norwegian 8-digit numbers in a free-text phone field."""
    out: set[str] = set()
    for part in re.split(r"[;,/]|\s{2,}", raw or ""):
        d = re.sub(r"\D", "", part)
        if d.startswith("0047"):
            d = d[4:]
        elif d.startswith("47") and len(d) == 10:
            d = d[2:]
        if len(d) == 8 and d[0] in "2345679":
            out.add(d)
    return out


# ------------------------------------------------------------------ registry
def load_universe(path: Path) -> dict[str, str]:
    out = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            out[r["organisation_number"]] = r.get("website") or ""
    log("universe", len(out))
    return out


def iter_brreg(path: Path):
    import ijson
    with gzip.open(path, "rb") as fh:
        yield from ijson.items(fh, "item")


class Registry:
    def __init__(self) -> None:
        self.own: dict[str, set[str]] = defaultdict(set)      # org -> registry website/email keys (enhet only)
        self.phone: dict[str, set[str]] = defaultdict(set)    # 8-digit -> parent orgs
        self.email: dict[str, set[str]] = defaultdict(set)    # email domain -> parent orgs
        self.parent: dict[str, str] = {}                      # underenhet -> overordnet enhet
        self.sub_web: dict[str, set[str]] = defaultdict(set)  # parent -> keys from sub-units
        self.names: dict[str, str] = {}                       # enhet -> registered name (tie-breaks only)

    def load_enheter(self, path: Path) -> None:
        n = 0
        for e in iter_brreg(path):
            org = e.get("organisasjonsnummer")
            if not org:
                continue
            n += 1
            self.names[org] = e.get("navn") or ""
            k = site_key(e.get("hjemmeside") or "")
            if k:
                self.own[org].add(k)
            m = mail_domain(e.get("epostadresse") or "")
            if m:
                self.own[org].add(m)
                self.email[m].add(org)
            for p in phones_of(e.get("telefon")) | phones_of(e.get("mobil")):
                self.phone[p].add(org)
        log("enheter", n)

    def load_underenheter(self, path: Path) -> None:
        n = 0
        for u in iter_brreg(path):
            org, par = u.get("organisasjonsnummer"), u.get("overordnetEnhet")
            if not org or not par:
                continue
            n += 1
            self.parent[org] = par
            k = site_key(u.get("hjemmeside") or "")
            if k:
                self.sub_web[par].add(k)
            m = mail_domain(u.get("epostadresse") or "")
            if m:
                self.sub_web[par].add(m)
            for p in phones_of(u.get("telefon")) | phones_of(u.get("mobil")):
                self.phone[p].add(par)
        log("underenheter", n)

    def root(self, org: str) -> str:
        return self.parent.get(org, org)

    def _pick(self, orgs: set[str], label: str) -> str | None:
        """One org, or for exactly two the only one whose distinctive name words appear in the place."""
        if len(orgs) == 1:
            return next(iter(orgs))
        if len(orgs) != 2:
            return None
        lab = _fold(label)
        hits = [o for o in sorted(orgs) if (ws := name_words(self.names.get(o, ""))) and all(w in lab for w in ws[:2])]
        return hits[0] if len(hits) == 1 else None

    def by_phone(self, raws, label: str = "") -> str | None:
        """Org whose registry phone/mobile is the place's phone. A phone held by 3+ orgs is not a key;
        one held by 2 counts only when the place name or domain names exactly one of them."""
        orgs: set[str] = set()
        for raw in raws:
            for p in phones_of(raw):
                hit = self.phone.get(p)
                if not hit:
                    continue
                if len(hit) > 2:
                    return None
                orgs |= hit
        return self._pick(orgs, label) if orgs else None

    def by_email(self, emails, label: str = "") -> str | None:
        orgs: set[str] = set()
        for em in emails:
            d = mail_domain(em)
            if not d:
                continue
            hit = self.email.get(d)
            if not hit:
                continue
            if len(hit) > 2:
                return None
            orgs |= hit
        return self._pick(orgs, label) if orgs else None


LEGAL = {"as", "asa", "ans", "da", "ba", "nuf", "ks", "sa", "enk", "iks", "kf", "sf", "stiftelse", "stiftelsen",
         "og", "i", "av", "for", "the", "and", "&", "co", "holding", "norge", "norway", "avd", "avdeling"}


def _fold(s: str) -> str:
    s = (s or "").lower()
    for a, b in (("æ", "ae"), ("ø", "o"), ("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e"), ("ü", "u")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "", s)


def name_words(name: str) -> list[str]:
    return [w for w in (_fold(x) for x in re.split(r"[\s\-/.,]+", name or "")) if len(w) >= 3 and w not in LEGAL]


# ------------------------------------------------------------------ sources
class Seeds:
    def __init__(self) -> None:
        self.m: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    def add(self, org: str, url: str, source: str) -> None:
        k = site_key(url)
        if k and org and org.isdigit() and len(org) == 9:
            self.m[org][k].add(source)


def from_osm(path: Path, reg: Registry, seeds: Seeds) -> dict:
    import osmium
    import osmium.filter

    stats = {"orgnr_with_site": 0, "phone_objects": 0, "phone_matched": 0}

    def handle(tags) -> None:
        t = {k: v for k, v in tags}
        webs = [t[k] for k in OSM_WEB_KEYS if t.get(k)]
        mails = [t[k] for k in OSM_MAIL_KEYS if t.get(k)]
        orgs = set()
        for k in OSM_ORG_KEYS:
            for v in (t.get(k) or "").split(";"):
                d = re.sub(r"\D", "", v)
                if len(d) == 9:
                    orgs.add(reg.root(d))
        if orgs:
            stats["orgnr_with_site"] += 1
            for org in orgs:
                for w in webs:
                    seeds.add(org, w, "osm_orgnr")
                for m in mails:
                    d = mail_domain(m)
                    if d:
                        seeds.add(org, d, "osm_orgnr")
            return
        phones = [t[k] for k in OSM_PHONE_KEYS if t.get(k)]
        if webs and phones:
            stats["phone_objects"] += 1
            org = reg.by_phone(phones, " ".join([t.get("name") or "", *webs]))
            if org:
                stats["phone_matched"] += 1
                for w in webs:
                    seeds.add(org, w, "osm_phone")

    # Only objects carrying a website or email key matter; the C++ key filter skips the rest quickly.
    fp = osmium.FileProcessor(str(path)).with_filter(osmium.filter.KeyFilter(*OSM_WEB_KEYS, *OSM_MAIL_KEYS))
    for obj in fp:
        handle(obj.tags)
    log("osm", stats)
    return stats


def from_overture(parquet: Path, release: str, reg: Registry, seeds: Seeds) -> dict:
    import duckdb

    con = duckdb.connect()
    if not parquet.exists():
        con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
        con.execute(f"""COPY (SELECT id, names.primary AS name, websites, phones, emails, addresses
            FROM read_parquet('s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*',
                              hive_partitioning=1)
            WHERE bbox.xmin BETWEEN 4.0 AND 31.6 AND bbox.ymin BETWEEN 57.7 AND 71.4
              AND (websites IS NOT NULL OR emails IS NOT NULL)) TO '{parquet}' (FORMAT parquet)""")
    rows = con.execute(f"""SELECT websites, phones, emails, coalesce(name, '') FROM '{parquet}'
        WHERE list_contains(list_transform(addresses, a -> a.country), 'NO') AND websites IS NOT NULL
        ORDER BY id""").fetchall()
    stats = {"places_no_with_site": len(rows), "phone_matched": 0, "email_matched": 0}
    for webs, phones, emails, name in rows:
        webs = [w for w in webs or [] if w]
        if not webs:
            continue
        label = " ".join([name, *webs])
        org = reg.by_phone(phones or [], label)
        if org:
            stats["phone_matched"] += 1
            for w in webs:
                seeds.add(org, w, "overture_phone")
        org2 = reg.by_email(emails or [], label)
        if org2:
            stats["email_matched"] += 1
            for w in webs:
                seeds.add(org2, w, "overture_email")
    log("overture", stats)
    return stats


def from_subunits(reg: Registry, seeds: Seeds) -> dict:
    n = 0
    for par, keys in reg.sub_web.items():
        for k in keys:
            seeds.add(par, k, "subunit")
            n += 1
    return {"pairs": n}


def from_wikidata(tsv: Path, reg: Registry, seeds: Seeds) -> dict:
    import httpx

    if not tsv.exists():
        r = httpx.get(QLEVER, params={"query": WIKIDATA_QUERY}, headers={"Accept": "text/tab-separated-values",
                      "User-Agent": UA}, timeout=120)
        r.raise_for_status()
        tsv.write_text(r.text)
    n = 0
    for line in tsv.read_text().splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        org = re.sub(r"\D", "", parts[1].split("^^")[0])
        site = parts[2].strip("<>")
        if len(org) == 9:
            seeds.add(reg.root(org), site, "wikidata")
            n += 1
    return {"pairs": n}


def from_nav(cache_path: Path, states: list[Path], reg: Registry, seeds: Seeds, fetch: bool, sleep: float,
             workers: int = 8) -> dict:
    """employer.homepage from NAV feed entries of ads whose employer org number we know."""
    import httpx

    ads: dict[str, str] = {}  # uuid -> employer org (parent where known)
    if NAV_INDEX.exists():
        idx = json.load(gzip.open(NAV_INDEX, "rt"))
        for u, a in (idx.get("ads") or {}).items():
            org = a.get("parent_orgnr") or a.get("employer_orgnr")
            if org:
                ads[u] = org
    for sp in states:  # read-only: never written
        try:
            st = json.loads(sp.read_text())
        except (OSError, ValueError):
            continue
        for u, a in (st.get("details") or {}).items():
            if a and a.get("employer_orgnr") and u not in ads:
                ads[u] = a.get("parent_orgnr") or a["employer_orgnr"]
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = [u for u in sorted(ads) if u not in cache]
    log("nav ads", len(ads), "cached", len(cache), "to fetch", len(todo) if fetch else 0)
    if fetch and todo:
        import threading
        from concurrent.futures import ThreadPoolExecutor

        cl = httpx.Client(headers={"User-Agent": UA}, timeout=60,
                          limits=httpx.Limits(max_connections=workers * 2, max_keepalive_connections=workers * 2))
        lock = threading.Lock()
        tok = {"v": None}
        done = {"n": 0}

        def token(force: bool = False) -> str | None:
            with lock:
                if force or tok["v"] is None:
                    m = re.search(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", cl.get(f"{NAV_BASE}/api/publicToken").text)
                    tok["v"] = m.group(0) if m else None
                return tok["v"]

        def one(u: str) -> None:
            for attempt in range(5):
                try:
                    r = cl.get(f"{NAV_BASE}/api/v1/feedentry/{u}",
                               headers={"Accept": "application/json", "Authorization": f"Bearer {token()}"})
                except httpx.HTTPError:
                    time.sleep(3 * (attempt + 1))
                    continue
                if r.status_code == 401:
                    token(force=True)
                    continue
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(5 * (attempt + 1))
                    continue
                if r.status_code == 200:
                    emp = ((r.json().get("ad_content") or {}).get("employer") or {})
                    val = {"orgnr": emp.get("orgnr"), "homepage": (emp.get("homepage") or "").strip()}
                elif r.status_code == 404:
                    val = {"orgnr": None, "homepage": ""}
                else:
                    return
                with lock:
                    cache[u] = val
                    done["n"] += 1
                    if done["n"] % 500 == 0:
                        cache_path.write_text(json.dumps(cache))
                        log("nav fetched", done["n"])
                break
            time.sleep(sleep)

        token()
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(one, todo))
        cache_path.write_text(json.dumps(cache))
    n = 0
    for u, org in ads.items():
        c = cache.get(u)
        if c and c.get("homepage"):
            par = reg.root(c["orgnr"]) if c.get("orgnr") else org
            seeds.add(par, c["homepage"], "nav")
            n += 1
    return {"ads": len(ads), "with_homepage": n}


# ------------------------------------------------------------------ assemble
DECLARED = {"osm_orgnr", "subunit", "nav", "wikidata"}


def assemble(seeds: Seeds, reg: Registry, universe: dict[str, str]) -> tuple[dict, dict]:
    owners: dict[str, set[str]] = defaultdict(set)
    for org, doms in seeds.m.items():
        for d in doms:
            owners[d].add(org)
    out: dict[str, list] = {}
    dropped = defaultdict(int)
    for org in sorted(seeds.m):
        if org not in universe:
            dropped["not_in_universe"] += 1
            continue
        own = set(reg.own.get(org, ())) | ({site_key(universe[org])} - {None})
        matched = {d for d, srcs in seeds.m[org].items() if srcs - DECLARED and d not in own}
        drop_matched = len(matched) > MAX_MATCHED_PER_ORG
        if drop_matched:
            dropped["org_matched_too_many"] += 1
        items = []
        for d, srcs in sorted(seeds.m[org].items()):
            if d in own:
                dropped["same_as_registry"] += 1
                continue
            n = len(owners[d])
            srcs = {s for s in srcs if (s in DECLARED and n <= MAX_DECLARED)
                    or (s not in DECLARED and n <= MAX_SHARED and not drop_matched)}
            if not srcs:
                dropped["shared_domain"] += 1
                continue
            items.append({"domain": d, "sources": sorted(srcs)})
        items.sort(key=lambda it: (-len(it["sources"]), it["domain"]))
        if len(items) > MAX_PER_ORG:
            dropped["over_per_org_cap"] += len(items) - MAX_PER_ORG
            items = items[:MAX_PER_ORG]
        if items:
            out[org] = items
    return out, dict(dropped)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=Path, required=True)
    ap.add_argument("--enheter", type=Path, required=True)
    ap.add_argument("--underenheter", type=Path, required=True)
    ap.add_argument("--osm", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True, help="scratch dir for the Overture extract, Wikidata TSV, NAV cache")
    ap.add_argument("--overture-release", default="2026-09-23.1")
    ap.add_argument("--nav-state", type=Path, nargs="*", default=[])
    ap.add_argument("--no-nav-fetch", action="store_true")
    ap.add_argument("--nav-sleep", type=float, default=0.0)
    ap.add_argument("--nav-workers", type=int, default=8)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)

    universe = load_universe(a.universe)
    reg = Registry()
    reg.load_enheter(a.enheter)
    reg.load_underenheter(a.underenheter)
    seeds = Seeds()
    stats = {
        "subunit": from_subunits(reg, seeds),
        "wikidata": from_wikidata(a.work / "wikidata.tsv", reg, seeds),
        "nav": from_nav(a.work / "nav_homepages.json", a.nav_state, reg, seeds, not a.no_nav_fetch, a.nav_sleep,
                        a.nav_workers),
        "overture": from_overture(a.work / f"overture_{a.overture_release}_no.parquet", a.overture_release, reg, seeds),
        "osm": from_osm(a.osm, reg, seeds),
    }
    data, dropped = assemble(seeds, reg, universe)
    by_src: dict[str, int] = defaultdict(int)
    for items in data.values():
        for s in {s for it in items for s in it["sources"]}:
            by_src[s] += 1
    doc = {
        "schema": 1,
        "built_at": now_iso(),
        "note": "Website CANDIDATES only. Each must be verified on the live site by the website connector.",
        "licences": {
            "osm": "OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright); extract via Geofabrik",
            "overture": f"Overture Maps Foundation Places {a.overture_release}, CDLA-Permissive-2.0 (https://overturemaps.org)",
            "brreg": "Enhetsregisteret, Bronnoysundregistrene, NLOD 2.0 (https://data.norge.no/nlod/no/2.0)",
            "nav": "NAV pam-stilling-feed, https://arbeidsplassen.nav.no/vilkar-api",
            "wikidata": "Wikidata, CC0 1.0",
        },
        "stats": {**stats, "dropped": dropped, "orgs": len(data), "orgs_by_source": dict(sorted(by_src.items()))},
        "seeds": data,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(a.out, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    log("wrote", a.out, a.out.stat().st_size, "bytes;", len(data), "orgs;", dict(by_src))


if __name__ == "__main__":
    main()
