"""GLEIF LEI records: legal entity identifier, its status, and reported direct and ultimate parents.

GLEIF stores Norwegian organisation numbers in `entity.registeredAs`, usually spaced
("923 609 016") and sometimes unspaced. One request asks for both spellings. A record is
used only when its `registeredAs` equals our number and the registration authority is
Brønnøysund (RA000472 Enhetsregisteret, RA000473 Foretaksregisteret). Parents are followed
only through the relationship links the record itself carries, so at most 3 requests per
company. Data is CC0 (https://www.gleif.org/en/meta/lei-data-terms-of-use).
"""
from __future__ import annotations

import json
from typing import Any

from ..http import BudgetExhausted, Fetcher
from ..model import OPEN_DATA, CompanyContext, Evidence, Snapshot

API = "https://api.gleif.org/api/v1"
LICENCE = "CC0 1.0, GLEIF LEI data"
METHOD = "gleif_lei_v1"
JSON_HEADERS = {"Accept": "application/vnd.api+json"}
BRREG_RAS = {"RA000472", "RA000473"}
INACTIVE_REG = {"DUPLICATE", "ANNULLED", "MERGED", "RETIRED", "TRANSFERRED"}
PARENTS = (("direct-parent", "lei_direct_parent"), ("ultimate-parent", "lei_ultimate_parent"))


def spaced(org: str) -> str:
    return f"{org[:3]} {org[3:6]} {org[6:]}"


def search_url(org: str) -> str:
    return f"{API}/lei-records?filter%5Bentity.registeredAs%5D={spaced(org).replace(' ', '%20')},{org}"


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))[:400]


def _record_span(rec: dict) -> str:
    a = rec.get("attributes") or {}
    e = a.get("entity") or {}
    r = a.get("registration") or {}
    return _dump({"lei": a.get("lei"),
                  "entity": {"legalName": (e.get("legalName") or {}).get("name"), "registeredAt": e.get("registeredAt"),
                             "registeredAs": e.get("registeredAs"), "status": e.get("status")},
                  "registration": {k: r.get(k) for k in ("status", "nextRenewalDate", "lastUpdateDate")}})


def matches_org(rec: dict, org: str) -> bool:
    e = (rec.get("attributes") or {}).get("entity") or {}
    number = (e.get("registeredAs") or "").replace(" ", "")
    ra = (e.get("registeredAt") or {}).get("id")
    return number == org and ra in BRREG_RAS


def pick_record(data: list[dict], org: str) -> tuple[dict | None, str | None]:
    """The single matching record, or (None, reason)."""
    hits = [r for r in data if matches_org(r, org)]
    if not hits:
        return None, "no LEI record with this organisation number"
    live = [r for r in hits if ((r.get("attributes") or {}).get("registration") or {}).get("status") not in INACTIVE_REG]
    pool = live or hits
    if len(pool) > 1:
        return None, f"{len(pool)} LEI records carry this organisation number"
    return pool[0], None


async def fetch_gleif(f: Fetcher, ctx: CompanyContext) -> None:
    state = {"lei_done": False, "pending": [field for _, field in PARENTS]}
    try:
        await _run(f, ctx, state)
    except Exception as exc:  # never raise into the orchestrator
        reason = "budget_exhausted" if isinstance(exc, BudgetExhausted) else f"{type(exc).__name__}: {exc}"
        ctx.error("gleif", reason)
        ctx.module_states["gleif"] = "failed"
        if not state["lei_done"]:
            ctx.mark("lei", "identity", "failed", note=f"GLEIF lookup failed ({reason})")
        else:
            for field in state["pending"]:
                ctx.mark(field, "identity", "failed", note=f"GLEIF parent lookup failed ({reason})")


async def _run(f: Fetcher, ctx: CompanyContext, state: dict) -> None:
    org = ctx.org
    url = search_url(org)
    snap = await f.get(url, headers=JSON_HEADERS)
    ctx.requests += 1
    if snap.status != 200:
        raise RuntimeError(f"HTTP {snap.status} for {url}")
    data = snap.json().get("data") or []
    rec, why = pick_record(data, org)
    if rec is None:
        total = ((snap.json().get("meta") or {}).get("pagination") or {}).get("total", len(data))
        ev = ctx.add_evidence(snap, source_class=OPEN_DATA, method=METHOD, licence=LICENCE,
                              span=_dump({"filter[entity.registeredAs]": [spaced(org), org], "total": total}),
                              note=why)
        availability = "ambiguous" if why and "records carry" in why else "not_available"
        ctx.mark("lei", "identity", availability, ev, note=f"GLEIF: {why}")
        state["lei_done"] = True
        state["pending"] = []
        ctx.module_states["gleif"] = availability
        return

    a = rec["attributes"]
    e = a.get("entity") or {}
    r = a.get("registration") or {}
    m = {"method": "registered_as_equals_orgnr",
         "detail": f"GLEIF entity.registeredAs '{e.get('registeredAs')}' at {(e.get('registeredAt') or {}).get('id')} equals {org}"}
    last = (r.get("lastUpdateDate") or "")[:10] or None
    ev = ctx.add_evidence(snap, source_class=OPEN_DATA, method=METHOD, licence=LICENCE, span=_record_span(rec),
                          effective_date=last, note=f"Single record: {API}/lei-records/{a['lei']}")
    value = {"lei": a["lei"], "status": e.get("status"), "registration_status": r.get("status"),
             "next_renewal": (r.get("nextRenewalDate") or "")[:10] or None, "last_update": last}
    ctx.add_claim("lei", "identity", value, ev, as_of=last, match=m)
    ctx.facts["lei"] = a["lei"]
    state["lei_done"] = True
    ctx.module_states["gleif"] = "available"

    rels = rec.get("relationships") or {}
    for key, field in PARENTS:
        links = (rels.get(key) or {}).get("links") or {}
        span = _dump({key: {"links": links}})
        if links.get("lei-record"):
            await _parent(f, ctx, links["lei-record"], field, m)
        elif links.get("reporting-exception"):
            ev = ctx.add_evidence(snap, source_class=OPEN_DATA, method=METHOD, licence=LICENCE, span=span)
            ctx.mark(field, "identity", "not_available", ev,
                     note="GLEIF holds a reporting exception instead of a parent (reason not fetched)")
        else:
            ev = ctx.add_evidence(snap, source_class=OPEN_DATA, method=METHOD, licence=LICENCE, span=span)
            ctx.mark(field, "identity", "not_available", ev, note="GLEIF record links no parent")
        state["pending"].remove(field)


async def _parent(f: Fetcher, ctx: CompanyContext, url: str, field: str, m: dict) -> None:
    snap: Snapshot = await f.get(url, headers=JSON_HEADERS)
    ctx.requests += 1
    if snap.status == 404:
        ev = ctx.add_evidence(snap, source_class=OPEN_DATA, method=METHOD, licence=LICENCE, span="HTTP 404")
        ctx.mark(field, "identity", "not_available", ev, note="GLEIF relationship link returned no parent record")
        return
    if snap.status != 200:
        raise RuntimeError(f"HTTP {snap.status} for {url}")
    p = (snap.json().get("data") or {})
    a = p.get("attributes") or {}
    e = a.get("entity") or {}
    if not a.get("lei"):
        raise RuntimeError(f"no LEI in parent response {url}")
    value: dict[str, Any] = {"lei": a["lei"], "name": (e.get("legalName") or {}).get("name"),
                             "country": (e.get("legalAddress") or {}).get("country")}
    if (e.get("registeredAt") or {}).get("id") in BRREG_RAS and e.get("registeredAs"):
        value["organisation_number"] = e["registeredAs"].replace(" ", "")
    ev: Evidence = ctx.add_evidence(
        snap, source_class=OPEN_DATA, method=METHOD, licence=LICENCE,
        span=_dump({"lei": a["lei"], "entity": {"legalName": e.get("legalName"),
                                               "legalAddress": {"country": value["country"]},
                                               "registeredAs": e.get("registeredAs")}}),
        effective_date=((a.get("registration") or {}).get("lastUpdateDate") or "")[:10] or None)
    ctx.add_claim(field, "identity", value, ev, match={**m, "detail": m["detail"] + f"; parent via relationship link of {ctx.facts.get('lei')}"})
