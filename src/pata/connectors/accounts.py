"""Regnskapsregisteret: filed annual-account figures and the list of filed annual-report copies.

Values are copied exactly as filed, with currency, period and account type. Nothing is
computed, rounded, converted or estimated. A 404 means "no record returned", never zero.
"""
from __future__ import annotations

import asyncio
import json
import time

from ..http import Fetcher
from ..model import OFFICIAL_ACCOUNTS, CompanyContext

API = "https://data.brreg.no/regnskapsregisteret/regnskap"
LICENCE = "NLOD 2.0, Brønnøysundregistrene (Regnskapsregisteret)"

# field -> path inside one Regnskap object
FIGURES = {
    "revenue": ("resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter"),
    "operating_result": ("resultatregnskapResultat", "driftsresultat", "driftsresultat"),
    "salary_costs": ("resultatregnskapResultat", "driftsresultat", "driftskostnad", "loennskostnad"),
    "profit_before_tax": ("resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad"),
    "annual_result": ("resultatregnskapResultat", "aarsresultat"),
    "total_assets": ("eiendeler", "sumEiendeler"),
    "cash_and_bank": ("eiendeler", "sumBankinnskuddOgKontanter"),
    "equity": ("egenkapitalGjeld", "egenkapital", "sumEgenkapital"),
    "debt": ("egenkapitalGjeld", "gjeldOversikt", "sumGjeld"),
}

_copy_lock = asyncio.Lock()
_copy_last = 0.0


def _dig(obj: dict, path: tuple[str, ...]):
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


async def fetch_accounts(f: Fetcher, ctx: CompanyContext) -> None:
    url = f"{API}/{ctx.org}"
    snap = await f.get(url, headers={"Accept": "application/json"})
    ctx.requests += 1
    m = {"method": "registry_endpoint", "detail": f"Regnskapsregisteret record for {ctx.org}"}
    if snap.status == 404 or (snap.status == 200 and not snap.json()):
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_ACCOUNTS, method="brreg_accounts_v1", licence=LICENCE,
                              span=f"HTTP {snap.status}: no account figures returned")
        ctx.mark("revenue", "financials", "not_available", ev,
                 note="Regnskapsregisteret returned no figures (not the same as zero)")
        ctx.module_states["financials"] = "not_available"
        return
    if snap.status != 200:
        ctx.error("financials", f"HTTP {snap.status}")
        ctx.mark("revenue", "financials", "failed", note=f"accounts HTTP {snap.status}")
        ctx.module_states["financials"] = "failed"
        return
    rows = snap.json()
    rows = rows if isinstance(rows, list) else [rows]
    rows.sort(key=lambda r: ((r.get("regnskapsperiode") or {}).get("tilDato") or "", r.get("regnskapstype") or ""), reverse=True)
    for r in rows:
        period = r.get("regnskapsperiode") or {}
        end = period.get("tilDato")
        year = (end or "")[:4]
        kind = r.get("regnskapstype")  # SELSKAP (company) or KONSERN (group)
        cur = r.get("valuta")
        for field, path in FIGURES.items():
            val = _dig(r, path)
            if val is None:
                continue
            span = json.dumps({path[-1]: val, "valuta": cur, "regnskapsperiode": period, "regnskapstype": kind,
                               "id": r.get("id")}, ensure_ascii=False)
            ev = ctx.add_evidence(snap, source_class=OFFICIAL_ACCOUNTS, span=span, effective_date=end,
                                  method="brreg_accounts_v1", licence=LICENCE, anchor=("id", r.get("id")))
            ctx.add_claim(field, "financials",
                          {"amount": val, "currency": cur, "year": year, "period_start": period.get("fraDato"),
                           "period_end": end, "account_type": "group" if kind == "KONSERN" else "company",
                           "source_field": path[-1]},
                          ev, as_of=end, match=m)
        audit = r.get("revisjon") or {}
        if audit.get("fravalgRevisjon") is not None or audit.get("ikkeRevidertAarsregnskap") is not None:
            ev = ctx.add_evidence(snap, source_class=OFFICIAL_ACCOUNTS, method="brreg_accounts_v1", licence=LICENCE,
                                  span=json.dumps({"revisjon": audit, "regnskapsperiode": period}), effective_date=end,
                                  anchor=("id", r.get("id")))
            ctx.add_claim("accounts_audited", "financials",
                          {"year": year, "account_type": "group" if kind == "KONSERN" else "company",
                           "audited": not (audit.get("fravalgRevisjon") or audit.get("ikkeRevidertAarsregnskap"))},
                          ev, as_of=end, match=m)
    ctx.module_states["financials"] = "available"


async def fetch_report_copies(f: Fetcher, ctx: CompanyContext) -> None:
    """Years with a filed annual-report PDF (the endpoint asks for about 30 requests/minute)."""
    global _copy_last
    url = f"{API}/aarsregnskap/kopi/{ctx.org}/aar"
    async with _copy_lock:
        wait = _copy_last + 2.0 - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _copy_last = time.monotonic()
    snap = await f.get(url, headers={"Accept": "application/json"})
    ctx.requests += 1
    if snap.status != 200:
        return
    try:
        years = snap.json()
    except Exception:
        return
    if isinstance(years, dict):
        years = years.get("aar") or years.get("years") or []
    years = sorted({str(y) for y in years}, reverse=True)
    m = {"method": "registry_endpoint", "detail": f"Annual-report copies for {ctx.org}"}
    for y in years[:10]:
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_ACCOUNTS, method="brreg_report_copies_v1", licence=LICENCE,
                              span=json.dumps(years), effective_date=f"{y}-12-31")
        ctx.add_claim("annual_report_pdf", "financials", {"year": y, "url": f"{API}/aarsregnskap/kopi/{ctx.org}/{y}"}, ev,
                      as_of=f"{y}-12-31", match=m)
