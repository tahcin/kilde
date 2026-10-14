"""Brønnøysund Støtteregisteret: state-aid awards (statsstøtte) granted to a company.

Source. Brønnøysundregistrene's public register of state aid, full export
https://stotte.brreg.no/nb/oppslag/stoettetildeling/totalbestand/csv (UTF-16LE, ";" separated, about 300 MB,
no key). Brønnøysundregistrene: "Datasettene følger Norsk lisens for åpne data (NLOD)" and "Det er ikke
nødvendig å registrere seg for å ta datasettet i bruk" (https://www.brreg.no/bruke-data-fra-bronnoysundregistrene/apne-data/).
stotte.brreg.no/robots.txt redirects to the lookup page (no rules). Each award has a public page,
https://stotte.brreg.no/nb/oppslag/stoettetildeling/<Støttetiltaksnummer>.

Declared cache, no run-time refresh: the export takes many minutes to download, so
scripts/build_stotteregisteret.py downloads it once, keeps awards from the last 5 years whose recipient
is in the Signalpost company universe (directly, or as an underenhet whose Enhetsregisteret
`overordnetEnhet` is in the universe), and stores each award's CSV line verbatim (converted to UTF-8).
That line plus the CSV header is the evidence snapshot, so spans are exact substrings.

Match. Column "Organisasjonsnummer støttemottaker" equals our number, or is our registered underenhet
(the Brreg unit record is a second evidence item). Names are never used. Rows with a recipient birth
date (persons) are never stored.

Value fields are copied verbatim from the row (date and amount exactly as Brønnøysund writes them).
Requests per run: 0.
"""
from __future__ import annotations

import csv
import os

from ..http import Fetcher
from ..model import OFFICIAL_REGISTRY, OFFICIAL_REGISTER_OTHER, CompanyContext, Snapshot
from . import _records as R

CSV_URL = "https://stotte.brreg.no/nb/oppslag/stoettetildeling/totalbestand/csv"
RECORD = "https://stotte.brreg.no/nb/oppslag/stoettetildeling/{nr}"
BRREG = "https://data.brreg.no/enhetsregisteret/api"
LICENCE = "NLOD 2.0, Brønnøysundregistrene (Støtteregisteret)"
BRREG_LICENCE = "NLOD 2.0, Brønnøysundregistrene"
METHOD = "brreg_stotteregisteret_csv_v1"
FIELD = "state_aid_award"
MAX_AWARDS = int(os.environ.get("KILDE_STOTTE_MAX", "10"))
COL_NR = "Støttetiltaksnummer"
COL_ORG = "Organisasjonsnummer støttemottaker"
COL_BIRTH = "Fødselsdato støttemottaker"
COL_DATE = "Tildelingsdato"


def parse_line(header: list[str], line: str) -> dict | None:
    vals = next(csv.reader([line], delimiter=";"))
    return dict(zip(header, vals)) if len(vals) == len(header) else None


def iso(ddmmyyyy: str) -> str | None:
    p = (ddmmyyyy or "").strip().split(".")
    return f"{p[2]}-{p[1]}-{p[0]}" if len(p) == 3 and all(x.isdigit() for x in p) else None


def value_of(row: dict) -> dict:
    """Verbatim copies of the row's own cells; empty cells are left out."""
    v = {"date": row.get(COL_DATE), "amount": row.get("Tildelt beløp"), "currency": row.get("Tildelt beløp valuta"),
         "amount_range_from": row.get("Beløpsintervall fra beløp"), "amount_range_to": row.get("Beløpsintervall til beløp"),
         "amount_range_currency": row.get("Beløpsintervall valuta"),
         "granting_authority": row.get("Navn støttegiver"), "scheme": row.get("Tilknyttet støtteordning"),
         "purpose": row.get("Formål"), "instrument": row.get("Støtteinstrument"),
         "category": row.get("Statsstøttekategori"), "award_number": row.get(COL_NR),
         "page_url": RECORD.format(nr=row.get(COL_NR))}
    return {k: x for k, x in v.items() if x}


async def prepare_stotteregisteret(f: Fetcher | None, orgs: list[str], *, cache_path: str | None = None) -> dict:
    cache = R.load_cache("stotteregisteret", cache_path or os.environ.get("KILDE_STOTTE_CACHE"))
    if cache is None:
        raise RuntimeError("Støtteregisteret cache missing (run scripts/build_stotteregisteret.py)")
    wanted = set(orgs)
    by_org = {o: v for o, v in cache["by_org"].items() if o in wanted}
    keep = {rid for v in by_org.values() for rid, _ in v}
    units = {n for v in by_org.values() for _, n in v if n}
    return {"header": cache["header"], "by_org": by_org, "records": {r: cache["records"][r] for r in keep},
            "units": {n: cache["units"][n] for n in units}, "retrieved_at": cache["retrieved_at"],
            "units_at": cache.get("units_at") or cache["retrieved_at"], "since": cache["since"],
            "file_sha256": cache.get("file_sha256")}


async def fetch_stotteregisteret(f: Fetcher | None, ctx: CompanyContext, batch: dict | None = None) -> None:
    try:
        _emit(ctx, batch)
    except Exception as exc:  # noqa: BLE001
        ctx.error("stotteregisteret", f"{type(exc).__name__}: {exc}")
        ctx.module_states["stotteregisteret"] = "failed"
        if not ctx.claims_for(FIELD):
            ctx.mark(FIELD, "activity", "failed", note=f"Støtteregisteret step failed ({type(exc).__name__})")


def _emit(ctx: CompanyContext, batch: dict | None) -> None:
    org = ctx.org
    if not batch or "by_org" not in batch:
        ctx.mark(FIELD, "activity", "failed", note="Støtteregisteret data unavailable this run")
        ctx.module_states["stotteregisteret"] = "failed"
        return
    items = batch["by_org"].get(org) or []
    if not items:
        ctx.mark(FIELD, "activity", "not_available",
                 note=f"No state-aid award since {batch['since']} names this organisation or one of its workplaces "
                      f"(Støtteregisteret export of {batch['retrieved_at'][:10]})")
        ctx.module_states["stotteregisteret"] = "not_available"
        return
    header = next(csv.reader([batch["header"]], delimiter=";"))
    rows = []
    for rid, unit in items:
        ln = batch["records"][rid]
        row = parse_line(header, ln)
        if row:
            rows.append((iso(row[COL_DATE]) or "", rid, unit, ln, row))
    rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
    ctx.set_total(FIELD, len(rows))
    for date, rid, unit, ln, row in rows[:MAX_AWARDS]:
        num = row[COL_ORG]
        url = RECORD.format(nr=row[COL_NR])
        snap = Snapshot(url=CSV_URL, final_url=CSV_URL, status=200, retrieved_at=batch["retrieved_at"],
                        content=(batch["header"] + "\n" + ln).encode("utf-8"), content_type="text/csv")
        start = ln.find(f'"{num}"')
        span = ln[start:start + R.MAX_SPAN] if start >= 0 else ln[:R.MAX_SPAN]
        ev = [ctx.add_evidence(snap, source_class=OFFICIAL_REGISTER_OTHER, method=METHOD, licence=LICENCE, span=span,
                               effective_date=date or None,
                               note=R.extract_note(batch["retrieved_at"], url,
                                                   f"header and row of award {row[COL_NR]}, UTF-16 converted to UTF-8"
                                                   + (f"; file sha256 {batch['file_sha256']}" if batch.get("file_sha256") else "")))]
        if unit:
            uln = batch["units"][unit]
            usnap = R.record_snapshot(f"{BRREG}/underenheter/{unit}", uln, batch["units_at"])
            ev.append(ctx.add_evidence(usnap, source_class=OFFICIAL_REGISTRY, method="brreg_underenhet_v1",
                                       licence=BRREG_LICENCE, span=R.head_span(uln),
                                       note=R.extract_note(batch["units_at"], f"{R.BRREG_HUMAN}{unit}",
                                                           "Enhetsregisteret underenhet, identifying keys only")))
            m = {"method": "organisasjonsnummer_stottemottaker_underenhet_of_orgnr",
                 "detail": f"Recipient {unit} is an Enhetsregisteret underenhet with overordnetEnhet {org}"}
        else:
            m = {"method": "organisasjonsnummer_stottemottaker_equals_orgnr",
                 "detail": f"Column '{COL_ORG}' '{num}' equals {org}"}
        ctx.add_claim(FIELD, "activity", value_of(row), ev, as_of=date or None, match=m,
                      note=(f"{len(rows)} awards since {batch['since']}; newest {MAX_AWARDS} shown"
                            if len(rows) > MAX_AWARDS else None))
    ctx.module_states["stotteregisteret"] = "available"
