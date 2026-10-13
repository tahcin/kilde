"""TED (Tenders Electronic Daily): EU/EEA public contracts won, and notices published as a buyer.

Source. The EU Publications Office's Search API v3 (POST https://api.ted.europa.eu/v3/notices/search),
which "does not require a key" (https://docs.ted.europa.eu/api/latest/index.html). TED legal notice
(https://ted.europa.eu/en/legal-notice): "Unless otherwise noted, the procurement notices published in
the Supplement to the Official Journal of the European Union can be freely reused, for commercial or
non-commercial purposes." api.ted.europa.eu has no robots.txt (404).

Match. eForms notices (Norway since November 2023) carry each organisation's national identifier.
A notice is a contract award for us only when `winner-identifier` holds our exact organisation number
and `winner-country` includes NOR; it is a buyer notice only when `organisation-identifier-buyer`
holds it and `buyer-country` includes NOR. Names are never used.

How. `prepare_ted` loads the declared cache (scripts/build_ted.py, src/kilde/data/ted.json.gz: every
notice with a Norwegian winner, and every Norwegian buyer notice, as compact JSON lines) and asks
TED for notices published since the cache was built (one POST per 250 notices, capped). Each matched
notice line is served as the evidence snapshot, so the span is an exact substring of it.

Requests per batch: 1 to MAX_REFRESH_PAGES, none per company.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from ..http import BudgetExhausted, Fetcher
from ..model import OPEN_DATA, CompanyContext
from . import _records as R

log = logging.getLogger("kilde")
API = "https://api.ted.europa.eu/v3/notices/search"
HUMAN = "https://ted.europa.eu/en/notice/-/detail/{pub}"
LICENCE = ("TED notices, Publications Office of the EU: freely reusable for commercial or non-commercial "
           "purposes (https://ted.europa.eu/en/legal-notice)")
METHOD = "ted_search_v3_eforms"
MAX_REFRESH_PAGES = int(os.environ.get("KILDE_TED_PAGES", "4"))
MAX_WON = 10
MAX_BOUGHT = 5
# Field order is the record line's key order: the facts a reader needs first.
ORDER = ["publication-number", "publication-date", "notice-type", "winner-identifier", "winner-country",
         "organisation-identifier-buyer", "buyer-country", "buyer-name", "title-proc", "result-value-notice",
         "result-value-cur-notice", "winner-name", "organisation-identifier-tenderer", "organisation-name-tenderer",
         "organisation-country-tenderer", "form-type", "notice-version", "procedure-identifier"]
FIELDS = ORDER
KEEP_LANG = ("eng", "nor", "nob", "nno")
REFRESH_QUERY = "(winner-country=NOR OR buyer-country=NOR) AND publication-date>={since}"
NOTICE_TYPES = {
    "can-standard": "contract award notice", "can-social": "contract award notice (social services)",
    "can-modif": "contract modification notice", "can-desg": "design contest result", "can-tran": "transport award notice",
    "cn-standard": "contract notice", "cn-social": "contract notice (social services)", "pin-only": "prior information notice",
    "pin-buyer": "prior information notice", "pin-rtl": "prior information notice (reduced time limit)",
    "veat": "voluntary ex-ante transparency notice", "qu-sy": "qualification system notice",
    "corr": "correction notice", "pin-cfc-standard": "prior information notice (call for competition)",
    "subco": "subcontracting notice", "cn-desg": "design contest notice", "pmc": "pre-market consultation notice",
    "compl": "contract completion notice",
}


# Records --------------------------------------------------------------------------------------
def _ids(v: Any) -> list[str]:
    return [str(x).replace(" ", "") for x in (v or []) if x]


def make_record(n: dict) -> dict:
    """Trim an API notice to the stored record: no links, titles in English/Norwegian only."""
    out: dict[str, Any] = {}
    for k in ORDER:
        if k not in n or n[k] in (None, [], {}):
            continue
        v = n[k]
        if isinstance(v, dict):
            v = {lang: v[lang] for lang in KEEP_LANG if lang in v} or dict(list(v.items())[:1])
        out[k] = v
    return out


def roles(rec: dict) -> dict[str, list[str]]:
    """Organisation numbers this notice can be attributed to, by role."""
    won = [i for i in _ids(rec.get("winner-identifier")) if R.is_orgnr(i)] if "NOR" in (rec.get("winner-country") or []) else []
    bought = [i for i in _ids(rec.get("organisation-identifier-buyer")) if R.is_orgnr(i)] \
        if "NOR" in (rec.get("buyer-country") or []) else []
    return {"won": sorted(set(won)), "bought": sorted(set(bought))}


def index_records(records: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    by_org: dict[str, dict[str, list[str]]] = {}
    for pub, ln in records.items():
        for role, orgs in roles(json.loads(ln)).items():
            for o in orgs:
                by_org.setdefault(o, {}).setdefault(role, []).append(pub)
    return by_org


def _date(rec: dict) -> str | None:
    d = rec.get("publication-date") or ""
    return d[:10] or None


def _text(v: Any) -> str | None:
    if isinstance(v, dict):
        for lang in KEEP_LANG:
            if lang in v:
                v = v[lang]
                break
        else:
            v = next(iter(v.values()), None)
    if isinstance(v, list):
        v = "; ".join(str(x) for x in v if x)
    return str(v) if v else None


# Batch -----------------------------------------------------------------------------------------
async def prepare_ted(f: Fetcher, orgs: list[str], *, cache_path: str | None = None) -> dict:
    cache = R.load_cache("ted", cache_path or os.environ.get("KILDE_TED_CACHE"))
    if cache is None:
        raise RuntimeError("TED cache missing (run scripts/build_ted.py)")
    records: dict[str, str] = dict(cache["records"])
    retrieved = {p: cache["built_at"] for p in records}
    refreshed = 0
    built = datetime.strptime(cache["built_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    since = (built - timedelta(days=1)).strftime("%Y%m%d")
    token = None
    try:
        for _ in range(MAX_REFRESH_PAGES):
            body: dict[str, Any] = {"query": REFRESH_QUERY.format(since=since), "fields": FIELDS, "limit": 250,
                                    "paginationMode": "ITERATION"}
            if token:
                body["iterationNextToken"] = token
            status, content, at = await R.raw_request(f, "POST", API, json_body=body)
            if status != 200:
                log.warning("TED refresh HTTP %s", status)
                break
            d = json.loads(content)
            for n in d.get("notices") or []:
                rec = make_record(n)
                pub = rec.get("publication-number")
                if pub and any(roles(rec).values()):
                    records[pub] = R.line(rec)
                    retrieved[pub] = at
                    refreshed += 1
            token = d.get("iterationNextToken")
            if not token or not d.get("notices"):
                break
    except BudgetExhausted:
        raise
    except Exception as exc:  # noqa: BLE001 - the cache still answers
        log.warning("TED refresh failed: %r", exc)
    wanted = set(orgs)
    by_org = {o: v for o, v in index_records(records).items() if o in wanted}
    return {"by_org": by_org, "records": records, "retrieved": retrieved, "built_at": cache["built_at"],
            "refreshed": refreshed}


# Per company -----------------------------------------------------------------------------------
def _newest(pubs: list[str], records: dict[str, str], cap: int) -> list[tuple[str, dict]]:
    """Newest first; one notice per procedure (the latest version wins)."""
    items = [(p, json.loads(records[p])) for p in set(pubs)]
    items.sort(key=lambda x: (_date(x[1]) or "", x[0]), reverse=True)
    seen: set[str] = set()
    out = []
    for p, rec in items:
        key = f"{rec.get('procedure-identifier')}|{rec.get('notice-type')}" if rec.get("procedure-identifier") else p
        if key in seen:
            continue
        seen.add(key)
        out.append((p, rec))
        if len(out) >= cap:
            break
    return out


async def fetch_ted(f: Fetcher, ctx: CompanyContext, batch: dict | None = None) -> None:
    try:
        _emit(ctx, batch)
    except Exception as exc:  # noqa: BLE001 - never raise into the orchestrator
        ctx.error("ted", f"{type(exc).__name__}: {exc}")
        ctx.module_states["ted"] = "failed"
        if not ctx.claims_for("public_contract_award"):
            ctx.mark("public_contract_award", "activity", "failed", note=f"TED step failed ({type(exc).__name__})")


def _emit(ctx: CompanyContext, batch: dict | None) -> None:
    org = ctx.org
    if not batch or "records" not in batch:
        ctx.mark("public_contract_award", "activity", "failed", note="TED data unavailable this run")
        ctx.module_states["ted"] = "failed"
        return
    hit = batch["by_org"].get(org) or {}
    records, retrieved = batch["records"], batch["retrieved"]
    if not hit:
        ctx.mark("public_contract_award", "activity", "not_available",
                 note=f"No TED eForms notice names {org} as winner or buyer (notices since 2023-11; checked {batch['built_at'][:10]}"
                      f"{' plus live refresh' if batch.get('refreshed') is not None else ''})")
        ctx.module_states["ted"] = "not_available"
        return
    won = _newest(hit.get("won", []), records, MAX_WON)
    for pub, rec in won:
        ln = records[pub]
        snap = R.record_snapshot(HUMAN.format(pub=pub), ln, retrieved.get(pub, batch["built_at"]))
        idx = _ids(rec.get("winner-identifier")).index(org)
        raw_id = rec["winner-identifier"][idx]
        names = (rec.get("organisation-name-tenderer") or {})
        value = {"notice": pub, "date": _date(rec), "notice_type": NOTICE_TYPES.get(rec.get("notice-type"), rec.get("notice-type")),
                 "title": _text(rec.get("title-proc")), "buyer": _text(rec.get("buyer-name")),
                 "buyer_organisation_number": next(iter(_ids(rec.get("organisation-identifier-buyer"))), None),
                 "url": HUMAN.format(pub=pub)}
        if rec.get("result-value-notice"):
            value["notice_total_value"] = rec["result-value-notice"]
            if rec.get("result-value-cur-notice"):
                value["currency"] = rec["result-value-cur-notice"]
        ev = ctx.add_evidence(snap, source_class=OPEN_DATA, method=METHOD, licence=LICENCE,
                              span=R.span_with(ln, must=f'"{raw_id}"'), effective_date=_date(rec),
                              note=(f"TED notice {pub} as returned by the TED Search API v3 ({API}, POST, query "
                                    f"publication-number={pub}); stored as one JSON line"))
        m = {"method": "winner_identifier_equals_orgnr",
             "detail": f"TED eForms winner-identifier[{idx}] '{raw_id}' (winner-country NOR) equals {org}"}
        ctx.add_claim("public_contract_award", "activity", value, ev, as_of=_date(rec), match=m)
    bought = _newest(hit.get("bought", []), records, MAX_BOUGHT)
    for pub, rec in bought:
        ln = records[pub]
        raw_id = next(i for i in rec["organisation-identifier-buyer"] if str(i).replace(" ", "") == org)
        snap = R.record_snapshot(HUMAN.format(pub=pub), ln, retrieved.get(pub, batch["built_at"]))
        value = {"notice": pub, "date": _date(rec), "notice_type": NOTICE_TYPES.get(rec.get("notice-type"), rec.get("notice-type")),
                 "title": _text(rec.get("title-proc")), "role": "buyer", "url": HUMAN.format(pub=pub)}
        ev = ctx.add_evidence(snap, source_class=OPEN_DATA, method=METHOD, licence=LICENCE,
                              span=R.span_with(ln, start_key="organisation-identifier-buyer", must=f'"{raw_id}"'),
                              effective_date=_date(rec),
                              note=f"TED notice {pub} via the TED Search API v3 ({API}, POST); stored as one JSON line")
        m = {"method": "organisation_identifier_buyer_equals_orgnr",
             "detail": f"TED eForms organisation-identifier-buyer '{raw_id}' (buyer-country NOR) equals {org}"}
        ctx.add_claim("procurement_notice", "activity", value, ev, as_of=_date(rec), match=m)
    if not won:
        ctx.mark("public_contract_award", "activity", "not_available",
                 note="TED lists this organisation only as a buyer, not as a contract winner")
    ctx.module_states["ted"] = "available"
