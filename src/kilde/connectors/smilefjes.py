"""Mattilsynet Smilefjes: food-safety inspections of places that serve food.

Source. Mattilsynet's open dataset "Smilefjestilsyn på serveringssteder"
(https://data.norge.no/datasets/288aa74c-e3d3-492e-9ede-e71503b3bfd9), licence CC BY 4.0. The catalogue's
listed distribution (hotell.difi.no) is gone; Mattilsynet's own site build (github.com/Mattilsynet/smilefjes-deux,
download.sh) reads the same file from https://matnyttig.mattilsynet.no/smilefjes/tilsyn.csv (no robots.txt,
404). The dataset terms ask users to refresh daily because withdrawn inspections are removed: "det er derfor
viktig at brukere av datasettet ... holde sine data oppdatert (1 gang pr. døgn)". So every run downloads the
current file (one request, about 16 MB) and never publishes an inspection from the shipped cache alone.

Match. Each inspected place carries `orgnummer`, the Enhetsregisteret number of the business that owns it.
Usually that is the place's workplace number (underenhet). A place is ours when `orgnummer` equals our
number, or when Enhetsregisteret lists that underenhet with `overordnetEnhet` equal to our number. The
underenhet link comes from the declared cache (scripts/build_smilefjes.py, Brreg data under NLOD 2.0),
stored per unit as a JSON line and cited as a second evidence item; numbers not in the cache are looked up
in Enhetsregisteret during the run (one request per 100 numbers). Names are never used.

Per place we publish the latest inspection (date, overall grade) and the number of inspections on file.
Snapshots: the CSV header plus that place's rows, cut verbatim from the downloaded file.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from typing import Any

from ..http import BudgetExhausted, Fetcher
from ..model import OFFICIAL_REGISTRY, OPEN_DATA, CompanyContext, Snapshot
from . import _records as R

log = logging.getLogger("kilde")
CSV_URL = "https://matnyttig.mattilsynet.no/smilefjes/tilsyn.csv"
SITE = "https://smilefjes.mattilsynet.no"
BRREG = "https://data.brreg.no/enhetsregisteret/api"
LICENCE = "CC BY 4.0, Mattilsynet (Smilefjestilsyn på serveringssteder)"
BRREG_LICENCE = "NLOD 2.0, Brønnøysundregistrene"
METHOD = "mattilsynet_smilefjes_csv_v1"
MAX_PLACES = 5
GRADES = {"0": "no breaches found (big smile)", "1": "minor breaches, no follow-up needed (big smile)",
          "2": "breaches that need follow-up (straight mouth)", "3": "serious breaches (sour face)"}
VISIT = {"0": "ordinary inspection", "1": "follow-up inspection"}
UNIT_KEYS = ("organisasjonsnummer", "navn", "overordnetEnhet", "oppstartsdato", "nedleggelsesdato", "slettedato")


def unit_record(e: dict, kind: str) -> dict:
    """Stored Brreg unit: the identifying keys in Brreg's own names and order."""
    rec: dict[str, Any] = {k: e[k] for k in UNIT_KEYS if e.get(k)}
    rec["organisasjonsform"] = (e.get("organisasjonsform") or {}).get("kode")
    rec["_type"] = kind
    return rec


def parse_csv(text: str) -> tuple[str, dict[str, list[tuple[dict, str]]]]:
    """(header line, orgnummer -> [(row dict, raw line)])."""
    lines = text.lstrip("﻿").split("\n")
    header_line = lines[0].rstrip("\r")
    header = next(csv.reader([header_line], delimiter=";"))
    out: dict[str, list[tuple[dict, str]]] = {}
    for raw in lines[1:]:
        raw = raw.rstrip("\r")
        if not raw:
            continue
        vals = next(csv.reader([raw], delimiter=";"))
        if len(vals) != len(header):
            continue
        row = dict(zip(header, vals))
        num = row.get("orgnummer", "").strip()
        if R.is_orgnr(num):
            out.setdefault(num, []).append((row, raw))
    return header_line, out


def iso(ddmmyyyy: str) -> str | None:
    s = (ddmmyyyy or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[4:]}-{s[2:4]}-{s[:2]}"
    return None


# Batch -----------------------------------------------------------------------------------------
async def prepare_smilefjes(f: Fetcher, orgs: list[str], *, cache_path: str | None = None) -> dict:
    cache = R.load_cache("smilefjes", cache_path or os.environ.get("KILDE_SMILEFJES_CACHE")) or \
        {"built_at": None, "units": {}, "pages": {}}
    status, body, at = await R.raw_request(f, "GET", CSV_URL, timeout=90.0)
    if status != 200 or not body:
        raise RuntimeError(f"Smilefjes CSV HTTP {status}")
    header, by_num = parse_csv(body.decode("utf-8", errors="replace"))
    units: dict[str, str] = dict(cache["units"])
    unit_at = {n: cache.get("built_at") for n in units}
    unknown = [n for n in by_num if n not in units]
    for i in range(0, len(unknown), 100):  # places added since the cache was built
        part = unknown[i:i + 100]
        for kind, key in (("underenhet", "underenheter"), ("enhet", "enheter")):
            snap = await f.get(f"{BRREG}/{key}?organisasjonsnummer={','.join(part)}&size=100")
            if snap.status != 200:
                continue
            for e in ((snap.json().get("_embedded") or {}).get(key) or []):
                units[e["organisasjonsnummer"]] = R.line(unit_record(e, kind))
                unit_at[e["organisasjonsnummer"]] = snap.retrieved_at
    wanted = set(orgs)
    owner: dict[str, list[tuple[str, str]]] = {}  # our org -> [(place orgnummer, how)]
    for num in by_num:
        if num in wanted:
            owner.setdefault(num, []).append((num, "direct"))
        u = units.get(num)
        if u:
            rec = json.loads(u)
            parent = rec.get("overordnetEnhet")
            if rec.get("_type") == "underenhet" and parent in wanted and not rec.get("slettedato"):
                owner.setdefault(parent, []).append((num, "underenhet"))
    return {"header": header, "rows": {n: by_num[n] for v in owner.values() for n, _ in v}, "owner": owner,
            "units": {n: units[n] for v in owner.values() for n, how in v if how == "underenhet"},
            "unit_at": unit_at, "pages": cache.get("pages") or {}, "retrieved_at": at}


# Per company -----------------------------------------------------------------------------------
async def fetch_smilefjes(f: Fetcher, ctx: CompanyContext, batch: dict | None = None) -> None:
    try:
        _emit(ctx, batch)
    except Exception as exc:  # noqa: BLE001
        ctx.error("smilefjes", f"{type(exc).__name__}: {exc}")
        ctx.module_states["smilefjes"] = "failed"
        if not ctx.claims_for("food_inspection"):
            ctx.mark("food_inspection", "activity", "failed", note=f"Smilefjes step failed ({type(exc).__name__})")


def _emit(ctx: CompanyContext, batch: dict | None) -> None:
    org = ctx.org
    if not batch or "owner" not in batch:
        ctx.mark("food_inspection", "activity", "failed", note="Smilefjes data unavailable this run (no same-day download)")
        ctx.module_states["smilefjes"] = "failed"
        return
    links = batch["owner"].get(org) or []
    if not links:
        ctx.mark("food_inspection", "activity", "not_available",
                 note="No Mattilsynet smilefjes inspection lists this organisation or one of its workplaces")
        ctx.module_states["smilefjes"] = "not_available"
        return
    places: list[tuple[str, str, str, list[tuple[dict, str]]]] = []
    for num, how in links:
        groups: dict[str, list[tuple[dict, str]]] = {}
        for row, raw in batch["rows"][num]:
            groups.setdefault(row["tilsynsobjektid"], []).append((row, raw))
        for obj, rows in groups.items():
            rows.sort(key=lambda x: iso(x[0]["dato"]) or "", reverse=True)
            places.append((num, how, obj, rows))
    places.sort(key=lambda p: iso(p[3][0][0]["dato"]) or "", reverse=True)
    for num, how, obj, rows in places[:MAX_PLACES]:
        row, raw = rows[0]
        date = iso(row["dato"])
        content = batch["header"] + "\n" + "\n".join(r for _, r in rows)
        snap = Snapshot(url=CSV_URL, final_url=CSV_URL, status=200, retrieved_at=batch["retrieved_at"],
                        content=content.encode("utf-8"), content_type="text/csv")
        page = batch["pages"].get(obj.split("_")[0])
        grade = row.get("total_karakter")
        value = {"place": row.get("navn"), "address": ", ".join(x for x in (row.get("adrlinje1"), row.get("postnr"),
                                                                             row.get("poststed")) if x),
                 "date": date, "grade": int(grade) if grade.isdigit() else grade, "result": GRADES.get(grade),
                 "visit": VISIT.get(row.get("tilsynsbesoektype")), "inspections_on_file": len(rows),
                 "place_organisation_number": num, "page_url": f"{SITE}{page}" if page else SITE}
        ev = [ctx.add_evidence(snap, source_class=OPEN_DATA, method=METHOD, licence=LICENCE, span=raw[:R.MAX_SPAN],
                               effective_date=date,
                               note=R.extract_note(batch["retrieved_at"], value["page_url"],
                                                   f"header and the {len(rows)} rows for place {obj}, downloaded this run",
                                                   kind="export"))]
        if how == "underenhet":
            uln = batch["units"][num]
            usnap = R.record_snapshot(f"{BRREG}/underenheter/{num}", uln, batch["unit_at"].get(num) or batch["retrieved_at"])
            ev.append(ctx.add_evidence(usnap, source_class=OFFICIAL_REGISTRY, method="brreg_underenhet_v1",
                                       licence=BRREG_LICENCE, span=R.head_span(uln),
                                       note=R.extract_note(batch["unit_at"].get(num) or batch["retrieved_at"],
                                                           f"{R.BRREG_HUMAN}{num}",
                                                           "Enhetsregisteret underenhet, identifying keys only")))
            m = {"method": "orgnummer_underenhet_of_orgnr",
                 "detail": f"Smilefjes orgnummer {num} is an Enhetsregisteret underenhet with overordnetEnhet {org}"}
        else:
            m = {"method": "orgnummer_equals_orgnr", "detail": f"Smilefjes orgnummer '{num}' equals {org}"}
        ctx.add_claim("food_inspection", "activity", value, ev, as_of=date, match=m)
    ctx.module_states["smilefjes"] = "available"
