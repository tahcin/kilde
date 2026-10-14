"""Wikidata: notable-company facts (website, inception, HQ, listing, ISIN, LEI, CEO, staff, socials, logo).

Robots-compliant route only. query.wikidata.org disallows /sparql in robots.txt, so the
organisation number is resolved to a QID through QLever's public Wikidata mirror (no
robots.txt), once per batch, on property P2333 (Norwegian organisation number). The same
query bundles labels for the HQ, CEO and stock exchange items, so no label lookups are needed.
Then each matched company costs one request to Special:EntityData/<QID>.json, which
www.wikidata.org's robots.txt allows (`Allow: /wiki/Special:EntityData/*.`). The entity JSON
must itself carry P2333 = our number before anything is published. Structured data is CC0.
Logos are linked on Commons, never copied (they carry their own licences).
"""
from __future__ import annotations

import asyncio
import json
import re
import weakref
from typing import Any, Iterable
from urllib.parse import quote, unquote, urlencode, urlsplit

from ..http import ROBOTS_TOKEN, Blocked, BudgetExhausted, Fetcher
from ..model import OPEN_DATA, CompanyContext, Evidence, Snapshot
from ..words import regional_variant

QLEVER = "https://qlever.dev/api/wikidata"
ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
ITEM = "https://www.wikidata.org/wiki/{qid}"
LICENCE = "CC0 1.0, Wikidata"
QUERY_METHOD = "wikidata_qlever_p2333_v1"
ENTITY_METHOD = "wikidata_entitydata_v1"
SPARQL_HEADERS = {"Accept": "application/sparql-results+json"}
JSON_HEADERS = {"Accept": "application/json"}
CHUNK = 200  # org numbers per QLever GET (keeps the URL well under 8 KB)
NORWAY = "Q20"
LABEL_PROPS = ("P159", "P169", "P414")

QUERY = """PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX ps: <http://www.wikidata.org/prop/statement/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?org ?item ?prop ?ref ?label WHERE {
  VALUES ?org { %s }
  ?item wdt:P2333 ?org .
  OPTIONAL {
    { ?item p:P159/ps:P159 ?ref . BIND("P159" AS ?prop) } UNION
    { ?item p:P169/ps:P169 ?ref . BIND("P169" AS ?prop) } UNION
    { ?item p:P414/ps:P414 ?ref . BIND("P414" AS ?prop) }
    ?ref rdfs:label ?label . FILTER(LANG(?label) = "nb" || LANG(?label) = "en")
  }
}"""

SOCIAL = {
    "P2002": ("x", "https://x.com/{}"),
    "P4264": ("linkedin", "https://www.linkedin.com/company/{}/"),
    "P2013": ("facebook", "https://www.facebook.com/{}"),
    "P2003": ("instagram", "https://www.instagram.com/{}/"),
}


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))  # trimmed after exact-span repair


# ---------------------------------------------------------------- robots.txt (RFC 9309)
# Python 3.12's urllib.robotparser applies the first matching rule and ignores wildcards, so it
# reads `Disallow: /wiki/Special:EntityData/` as blocking `Allow: /wiki/Special:EntityData/*.`.
# RFC 9309 says the longest match wins (Allow on a tie), which allows the .json path. This
# check implements that rule for www.wikidata.org only; other hosts keep the Fetcher's check.
WIKIDATA_ROBOTS = "https://www.wikidata.org/robots.txt"
_robots: "weakref.WeakKeyDictionary[Fetcher, tuple[bool, list[tuple[bool, str]]]]" = weakref.WeakKeyDictionary()
_robots_locks: "weakref.WeakKeyDictionary[Fetcher, asyncio.Lock]" = weakref.WeakKeyDictionary()


def parse_robots(text: str, agent: str) -> list[tuple[bool, str]]:
    """Rules (allow, pattern) of the groups that apply to `agent`, or of `*` if none name it."""
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    last_was_agent = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not last_was_agent:
                agents, rules = [], []
                groups.append((agents, rules))
            agents.append(val.lower())
            last_was_agent = True
        elif key in ("allow", "disallow"):
            last_was_agent = False
            if groups and val:
                rules.append((key == "allow", val))
        else:
            last_was_agent = False
    me = agent.lower()
    mine = [r for a, rs in groups if any(x != "*" and me.startswith(x) for x in a) for r in rs]
    if any(any(x != "*" and me.startswith(x) for x in a) for a, _ in groups):
        return mine
    return [r for a, rs in groups if "*" in a for r in rs]


def _pattern_re(pattern: str) -> re.Pattern:
    pat = unquote(pattern)
    anchored = pat.endswith("$")
    body = re.escape(pat[:-1] if anchored else pat).replace(r"\*", ".*")
    return re.compile(body + ("$" if anchored else ""))


def rfc9309_allowed(rules: list[tuple[bool, str]], url: str) -> bool:
    parts = urlsplit(url)
    path = unquote(parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    best: tuple[int, bool] | None = None
    for allow, pattern in rules:
        if _pattern_re(pattern).match(path):
            key = (len(pattern), allow)
            if best is None or key > best:
                best = key
    return True if best is None else best[1]


async def wikidata_allowed(f: Fetcher, url: str) -> bool:
    async with _robots_locks.setdefault(f, asyncio.Lock()):
        if f not in _robots:
            snap = await f.get(WIKIDATA_ROBOTS)
            if snap.status in (401, 403) or snap.status >= 500:
                _robots[f] = (False, [])
            elif snap.status >= 400:
                _robots[f] = (True, [])
            else:
                _robots[f] = (True, parse_robots(snap.text(), ROBOTS_TOKEN))
    ok, rules = _robots[f]
    return ok and rfc9309_allowed(rules, url)


# ---------------------------------------------------------------- batch lookup
class WikidataBatch(dict):
    """org number -> QID, plus what is needed to cite the lookup."""

    def __init__(self) -> None:
        super().__init__()
        self.snapshots: dict[str, Snapshot] = {}   # org -> QLever response that covered it
        self.rows: dict[str, dict] = {}            # org -> {"org": ..., "item": ...} binding
        self.ambiguous: dict[str, list[str]] = {}  # org -> several QIDs
        self.labels: dict[str, dict[str, str]] = {}  # item QID -> {lang: label}
        self.queried: set[str] = set()
        self.failed: dict[str, str] = {}           # org -> reason


def query_url(orgs: Iterable[str]) -> str:
    values = " ".join(f'"{o}"' for o in orgs)
    return f"{QLEVER}?{urlencode({'query': QUERY % values}, quote_via=quote)}"


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def parse_bindings(batch: WikidataBatch, snap: Snapshot, chunk: list[str]) -> None:
    found: dict[str, set[str]] = {}
    for b in (snap.json().get("results") or {}).get("bindings") or []:
        org = (b.get("org") or {}).get("value")
        item = (b.get("item") or {}).get("value")
        if org not in chunk or not item:
            continue
        qid = _qid(item)
        found.setdefault(org, set()).add(qid)
        if b.get("ref") and b.get("label"):
            lang = b["label"].get("xml:lang", "")
            batch.labels.setdefault(_qid(b["ref"]["value"]), {})[lang] = b["label"]["value"]
    for org, qids in found.items():
        batch.snapshots[org] = snap
        if len(qids) == 1:
            qid = next(iter(qids))
            batch[org] = qid
            batch.rows[org] = {"org": org, "item": ITEM.format(qid=qid)}
        else:
            batch.ambiguous[org] = sorted(qids)
    for org in chunk:
        batch.snapshots.setdefault(org, snap)


async def prepare_wikidata(f: Fetcher, orgs: list[str]) -> WikidataBatch:
    """One QLever query per 200 org numbers (one for a normal batch). Never raises."""
    batch = WikidataBatch()
    uniq = list(dict.fromkeys(o for o in orgs if o and o.isdigit() and len(o) == 9))
    for i in range(0, len(uniq), CHUNK):
        chunk = uniq[i:i + CHUNK]
        try:
            snap = await f.get(query_url(chunk), headers=SPARQL_HEADERS, robots=True)
            if snap.status != 200:
                raise RuntimeError(f"QLever HTTP {snap.status}")
            parse_bindings(batch, snap, chunk)
            batch.queried.update(chunk)
        except Exception as exc:
            reason = "budget_exhausted" if isinstance(exc, BudgetExhausted) else f"{type(exc).__name__}: {exc}"
            for org in chunk:
                batch.failed[org] = reason
    return batch


# ---------------------------------------------------------------- statement helpers
def _value(s: dict) -> Any:
    return ((s.get("mainsnak") or {}).get("datavalue") or {}).get("value")


def _quals(s: dict, p: str) -> list[Any]:
    return [((q.get("datavalue") or {}).get("value")) for q in (s.get("qualifiers") or {}).get(p, [])
            if q.get("snaktype") == "value"]


def statements(claims: dict, p: str, *, current: bool = False) -> list[dict]:
    """Usable statements: preferred rank if any, else normal; never deprecated."""
    out = [s for s in claims.get(p, []) if s.get("rank") != "deprecated"
           and (s.get("mainsnak") or {}).get("snaktype") == "value"]
    if current:
        out = [s for s in out if not _quals(s, "P582")]
    pref = [s for s in out if s.get("rank") == "preferred"]
    return pref or out


def wd_date(v: Any) -> str | None:
    """Wikidata time value to ISO text at its stated precision (day, month or year)."""
    if not isinstance(v, dict) or not v.get("time"):
        return None
    t = v["time"].lstrip("+")
    if t.startswith("-"):
        return None
    prec = v.get("precision", 11)
    y, mo, d = t[:4], t[5:7], t[8:10]
    if prec >= 11 and mo != "00" and d != "00":
        return f"{y}-{mo}-{d}"
    if prec >= 10 and mo != "00":
        return f"{y}-{mo}"
    if prec >= 9:
        return y
    return None


def stmt_span(p: str, s: dict, quals: Iterable[str] = ()) -> str:
    q = {k: _quals(s, k) for k in quals if _quals(s, k)}
    out: dict[str, Any] = {"property": p, "id": s.get("id"), "rank": s.get("rank"), "value": _value(s)}
    if q:
        out["qualifiers"] = q
    return _dump(out)


def label(labels: dict[str, dict[str, str]], qid: str | None) -> str | None:
    lab = labels.get(qid or "") or {}
    return lab.get("nb") or lab.get("en")


REGIONAL_QUALS = ("P1001", "P518", "P407", "P642", "P3831", "P17", "P131", "P276")


def pick_website(stmts: list[dict]) -> tuple[dict | None, int]:
    """Preferred rank wins; otherwise the first statement valid for Norway or with no jurisdiction."""
    stmts = [s for s in stmts if isinstance(_value(s), str) and not _quals(s, "P582")]
    if not stmts:
        return None, 0
    if any(s.get("rank") == "preferred" for s in stmts):
        return stmts[0], len(stmts)
    for s in stmts:
        juris = [q.get("id") for q in _quals(s, "P1001") if isinstance(q, dict)]
        if not juris or NORWAY in juris:
            return s, len(stmts)
    return None, len(stmts)  # every listed site is for another country


# ---------------------------------------------------------------- per company
async def fetch_wikidata(f: Fetcher, ctx: CompanyContext, batch: WikidataBatch | None = None) -> None:
    try:
        await _run(f, ctx, batch)
    except Exception as exc:  # never raise into the orchestrator
        reason = "budget_exhausted" if isinstance(exc, BudgetExhausted) else f"{type(exc).__name__}: {exc}"
        state = "blocked" if isinstance(exc, Blocked) else "failed"
        ctx.error("wikidata", reason)
        ctx.module_states["wikidata"] = state
        if not ctx.claims_for("wikidata_item"):
            ctx.mark("wikidata_item", "identity", state, note=f"Wikidata lookup {state} ({reason})")


async def _run(f: Fetcher, ctx: CompanyContext, batch: WikidataBatch | None) -> None:
    org = ctx.org
    if batch is None or (org not in batch.queried and org not in batch.failed):
        batch = await prepare_wikidata(f, [org])
        ctx.requests += 1
    if org in batch.failed:
        raise RuntimeError(f"QLever lookup failed: {batch.failed[org]}")
    qsnap = batch.snapshots[org]
    if org in batch.ambiguous:
        ev = ctx.add_evidence(qsnap, source_class=OPEN_DATA, method=QUERY_METHOD, licence=LICENCE,
                              span=_dump({"org": org, "items": batch.ambiguous[org]}))
        ctx.mark("wikidata_item", "identity", "ambiguous", ev, note="Several Wikidata items carry this organisation number")
        ctx.module_states["wikidata"] = "ambiguous"
        return
    qid = batch.get(org)
    if not qid:
        ev = ctx.add_evidence(qsnap, source_class=OPEN_DATA, method=QUERY_METHOD, licence=LICENCE,
                              span=_dump({"query": f'?item wdt:P2333 "{org}"', "bindings": 0}))
        ctx.mark("wikidata_item", "identity", "not_available", ev, note="No Wikidata item has P2333 = this number")
        ctx.module_states["wikidata"] = "not_available"
        return

    url = ENTITY.format(qid=qid)
    if not await wikidata_allowed(f, url):
        raise Blocked(f"robots.txt disallows {url}")
    snap = await f.get(url, headers=JSON_HEADERS)
    ctx.requests += 1
    if snap.status != 200:
        raise RuntimeError(f"HTTP {snap.status} for {url}")
    emit_entity(ctx, batch, qsnap, snap)


def emit_entity(ctx: CompanyContext, batch: WikidataBatch, qsnap: Snapshot, snap: Snapshot) -> None:
    org = ctx.org
    entities = snap.json().get("entities") or {}
    if not entities:
        raise RuntimeError("entity JSON without entities")
    ent = next(iter(entities.values()))
    qid = ent.get("id") or batch.get(org)
    claims = ent.get("claims") or {}
    modified = (ent.get("modified") or "")[:10] or None
    own = [s for s in claims.get("P2333", []) if _value(s) == org and s.get("rank") != "deprecated"]
    if not own:
        ev = ctx.add_evidence(snap, source_class=OPEN_DATA, method=ENTITY_METHOD, licence=LICENCE,
                              span=_dump({"id": qid, "P2333": [_value(s) for s in claims.get("P2333", [])]}))
        ctx.mark("wikidata_item", "identity", "ambiguous", ev,
                 note="QLever mirror matched this item, but the live entity no longer carries this number")
        ctx.module_states["wikidata"] = "ambiguous"
        return

    m = {"method": "wikidata_p2333_equals_orgnr",
         "detail": f"Wikidata {qid} has P2333 (Norwegian organisation number) = {org}"}
    note = f"Item page: {ITEM.format(qid=qid)}"

    def ev(p: str, s: dict, quals: Iterable[str] = (), effective: str | None = None) -> Evidence:
        return ctx.add_evidence(snap, source_class=OPEN_DATA, method=ENTITY_METHOD, licence=LICENCE,
                                span=stmt_span(p, s, quals), effective_date=effective or modified, note=note)

    # The match itself: QLever row plus the live P2333 statement.
    q_ev = ctx.add_evidence(qsnap, source_class=OPEN_DATA, method=QUERY_METHOD, licence=LICENCE,
                            span=_dump(batch.rows.get(org) or {"org": org, "item": ITEM.format(qid=qid)}))
    labels = ent.get("labels") or {}
    name = (labels.get("nb") or labels.get("en") or {}).get("value")
    ctx.add_claim("wikidata_item", "identity", {"qid": qid, "url": ITEM.format(qid=qid), "label": name},
                  [q_ev, ev("P2333", own[0])], as_of=modified, match=m)
    ctx.facts["wikidata_qid"] = qid
    ctx.module_states["wikidata"] = "available"

    # Official website (P856)
    site, n = pick_website(statements(claims, "P856"))
    from .website import canonical_social, site_domain, social_path_is_link
    from urllib.parse import urlsplit as _us
    known = {str(c.value) for c in ctx.claims_for("official_website")}
    site_dom = site_domain(_us(str(_value(site))).netloc) if site and isinstance(_value(site), str) else None
    # Wikidata items can conflate related entities (a pension foundation's item listed the operating
    # company's site), so its website is only a candidate: never published unless Kilde verified that
    # same site itself, in which case it is already published.
    ctx.facts["wikidata_website_candidate"] = site_dom
    if False and site and site_dom and site_dom not in known:
        ctx.add_claim("official_website", "web_presence", site_dom, ev("P856", site, ("P407", "P1001")),
                      confidence=0.9, match=m,
                      note=f"Wikidata lists {n} website statements; this one is preferred or valid for Norway" if n > 1 else None)
        ctx.facts["wikidata_website"] = _value(site)

    # Inception (P571)
    for s in statements(claims, "P571")[:1]:
        d = wd_date(_value(s))
        reg = [c.value for c in ctx.claims_for("incorporation_date")]
        if d and (not reg or d in reg):  # never contradict the registry's incorporation date
            ctx.add_claim("inception_date", "identity", d, ev("P571", s), as_of=d, match=m)

    # Headquarters (P159), label bundled in the batch query
    for s in statements(claims, "P159", current=True)[:1]:
        ref = (_value(s) or {}).get("id")
        if ref:
            value = {"qid": ref, "name": label(batch.labels, ref)}
            street = [x.get("text") for x in _quals(s, "P6375") if isinstance(x, dict)]
            if street:
                value["street"] = street[0]
            ctx.add_claim("headquarters", "identity", {k: v for k, v in value.items() if v}, ev("P159", s, ("P6375", "P281")),
                          match=m)

    # Stock listings (P414) with ticker (P249)
    for s in statements(claims, "P414", current=True):
        tickers = [t for t in _quals(s, "P249") if isinstance(t, str)]
        ex = (_value(s) or {}).get("id")
        if not tickers or not ex:
            continue
        since = next((wd_date(v) for v in _quals(s, "P580")), None)
        value = {"exchange": label(batch.labels, ex), "exchange_qid": ex, "ticker": tickers[0], "since": since}
        ctx.add_claim("stock_listing", "identity", {k: v for k, v in value.items() if v}, ev("P414", s, ("P249", "P580")),
                      as_of=since, match=m)

    # ISIN (P946) and LEI (P1278)
    for s in statements(claims, "P946")[:5]:
        if isinstance(_value(s), str):
            ctx.add_claim("isin", "identity", _value(s), ev("P946", s), match=m)
    for s in statements(claims, "P1278")[:1]:
        if isinstance(_value(s), str):
            ctx.add_claim("lei", "identity", {"lei": _value(s)}, ev("P1278", s), match=m,
                          note="LEI as recorded on Wikidata; GLEIF is the authoritative source")

    # CEO (P169): current statements only, label bundled; compared with the registry's general manager
    for s in statements(claims, "P169", current=True)[:1]:
        ref = (_value(s) or {}).get("id")
        who = label(batch.labels, ref)
        if not who:
            continue
        since = next((wd_date(v) for v in _quals(s, "P580")), None)
        reg = [str((c.value or {}).get("name", "")).casefold() for c in ctx.claims_for("ceo")]
        if reg and who.casefold() not in reg:
            continue  # stale community data must not contradict the registry's general manager
        ctx.add_claim("reported_ceo", "leadership", {"name": who, "qid": ref, "since": since},
                      ev("P169", s, ("P580",)), as_of=since, match=m, confidence=0.8,
                      note="Community-maintained; the registry's general manager is authoritative")

    # Employees (P1128) with point in time (P585)
    dated = []
    for s in statements(claims, "P1128"):
        when = next((wd_date(v) for v in _quals(s, "P585")), None)
        amount = (_value(s) or {}).get("amount")
        if when and amount:
            try:
                dated.append((when, int(float(amount)), s))
            except ValueError:
                continue
    if dated:
        when, count, s = max(dated, key=lambda x: x[0])
        ctx.add_claim("employees_reported", "identity", {"count": count, "point_in_time": when},
                      ev("P1128", s, ("P585",), effective=when), as_of=when, match=m,
                      note="Wikidata figure, often group-wide; the registry count covers this legal entity only")

    # Social profiles
    for p, (platform, pattern) in SOCIAL.items():
        stmts = statements(claims, p, current=True)[:5]
        handles = [h.strip() for h in (_value(x) for x in stmts) if isinstance(h, str) and h.strip()]
        if len(stmts) > 1:
            # several accounts on one platform: those qualified for a place, language or part of the company are
            # regional or department accounts (often another group entity's); keep the main brand account
            plain = [x for x in stmts if not any(_quals(x, q) for q in REGIONAL_QUALS)]
            stmts = plain or stmts[:1]
        for s in stmts:
            handle = _value(s)
            if isinstance(handle, str) and handle.strip() and regional_variant(handle.strip(), handles):
                continue
            if isinstance(handle, str) and handle.strip():
                # Socials only when Wikidata's website is the site Kilde verified for this company.
                if not site_dom or site_dom not in known:
                    continue
                link = pattern.format(quote(handle.strip(), safe="-_./"))
                if social_path_is_link(link):
                    continue  # a domain, URL or email entered as the handle
                social = canonical_social(platform, link)
                if str(social) in {str(c.value) for c in ctx.claims_for("social_profile")}:
                    continue
                ctx.add_claim("social_profile", "web_presence", social, ev(p, s), match=m)

    # Logo (P154): link to the Commons file page
    for s in statements(claims, "P154")[:1]:
        fname = _value(s)
        if isinstance(fname, str):
            url = "https://commons.wikimedia.org/wiki/File:" + quote(fname.replace(" ", "_"))
            ctx.add_claim("logo", "web_presence", {"file": fname, "url": url}, ev("P154", s), match=m,
                          note="Hosted on Wikimedia Commons under the file's own licence; linked, not copied")
