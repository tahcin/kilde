"""Research and development projects: SkatteFUNN approvals and Research Council of Norway funding.

Sources (both published by Norges forskningsråd, the Research Council of Norway):
- SkatteFUNN, the R&D tax-credit scheme. The Council publishes every application with the
  company's organisation number, title, period and decision on
  https://www.forskningsradet.no/skattefunn/suksesshistorier/ as two Excel files: applications in
  the new system (May 2024 onwards, refreshed monthly, file name dated) and 2002-2024. Only rows
  marked approved ("Godkjent" / "Søknad godkjent" = JA) are used. No licence is stated on the files;
  they are published by a public body for download. robots.txt allows /skattefunn/ and /siteassets/.
- Research Council project funding, "Søknader - versjon 2": the Council's open dataset on GitHub
  (https://github.com/Forskningsradet/open-data, catalogued on data.norge.no under NLOD 2.0, updated
  quarterly). Only projects in phase "Bevilgning" (funded, running) or "Avsluttet" (funded, finished)
  are used; rejected applications are never published. Project leaders' names are not stored.

Match. The row's own organisation-number column ("Organisasjonsnr", "Organisasjonsnummer",
"organisasjonsnummer") equals ours. Names are never used.

How. `prepare_forskningsradet` loads the declared cache (scripts/build_forskningsradet.py,
src/kilde/data/forskningsradet.json.gz: one compact JSON line per approved/funded project, with the
source's own column names) and refreshes the current SkatteFUNN file when the Council has published
a newer one (1 page + at most 1 file of about 2 MB). Each matched record line is the evidence
snapshot, so the span is an exact substring of it.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from ..http import BudgetExhausted, Fetcher
from ..model import OPEN_DATA, CompanyContext
from . import _records as R
from . import _xlsx

log = logging.getLogger("kilde")
SF_PAGE = "https://www.forskningsradet.no/skattefunn/suksesshistorier/"
SF_BASE = "https://www.forskningsradet.no"
SF_HIST = "https://www.forskningsradet.no/siteassets/skattefunn/tall/skattefunn-innsendte-soknader-2002-2024.xlsx"
RCN_CSV = "https://raw.githubusercontent.com/Forskningsradet/open-data/main/datasets/soknader2/dataset.csv"
RCN_HUMAN = "https://github.com/Forskningsradet/open-data/tree/main/datasets/soknader2"
LIC_SF = ("Norges forskningsråd, SkatteFUNN application list published for download at "
          "https://www.forskningsradet.no/skattefunn/suksesshistorier/ (no licence stated)")
LIC_RCN = "NLOD 2.0, Norges forskningsråd open data (Søknader - versjon 2)"
METHOD = "forskningsradet_projects_v1"
MAX_PROJECTS = int(os.environ.get("KILDE_RCN_MAX", "8"))
WEB_LINK = re.compile(r'href="(/siteassets/skattefunn/tall/[^"]*webliste[^"]*\.xlsx)"', re.I)

SF_WEB_COLS = ["Prosjektnummer", "Organisasjonsnr", "Prosjektansvarlig", "Prosjekttittel", "Godkjent", "Vedtaksdato",
               "Søknadsdato", "Fra-år", "Til-år", "Kommune"]
SF_HIST_COLS = ["Prosjektnummer", "Organisasjonsnummer", "Bedriftsnavn", "Prosjekttittel", "Søknad godkjent", "Vedtaksdato",
                "Innsendt dato", "Prosjektets fra-år", "Prosjektets til-år", "Kommunenavn"]
RCN_COLS = ["prosjektnummer", "organisasjonsnummer", "prosjektansvarlig_navn", "prosjekttittel", "prosjektfase",
            "prosjektstart", "prosjektslutt", "soknadstype", "aktivitet", "tildelt_belop", "kommune"]
DATE_COLS = {"Vedtaksdato", "Søknadsdato", "Innsendt dato", "Fra-år", "Til-år", "Prosjektets fra-år", "Prosjektets til-år"}
FUNDED = {"Bevilgning", "Avsluttet"}


def _clean_org(v: str) -> str:
    v = (v or "").strip()
    return v[:-2] if v.endswith(".0") else v


def _val(col: str, v: str) -> str:
    v = (v or "").strip()
    if col in DATE_COLS and v:
        try:
            n = float(v)
        except ValueError:
            return v
        if n > 3000:  # an Excel serial date, not a bare year
            return R.excel_date(v) or v
        return str(int(n))
    return v


def _record(row: dict, cols: list[str]) -> dict:
    return {c: _val(c, row.get(c, "")) for c in cols if (row.get(c) or "").strip()}


def sf_web_record(row: dict) -> tuple[str, dict] | None:
    org = _clean_org(row.get("Organisasjonsnr", ""))
    if row.get("Godkjent", "").strip().upper() != "JA" or not R.is_orgnr(org) or not row.get("Prosjektnummer"):
        return None
    rec = _record({**row, "Organisasjonsnr": org}, SF_WEB_COLS)
    return f"SFW:{rec['Prosjektnummer']}", rec


def sf_hist_record(row: dict) -> tuple[str, dict] | None:
    org = _clean_org(row.get("Organisasjonsnummer", ""))
    if row.get("Søknad godkjent", "").strip().upper() != "JA" or not R.is_orgnr(org) or not row.get("Prosjektnummer"):
        return None
    rec = _record({**row, "Organisasjonsnummer": org}, SF_HIST_COLS)
    return f"SF:{rec['Prosjektnummer']}", rec


def rcn_record(row: dict) -> tuple[str, dict] | None:
    org = _clean_org(row.get("organisasjonsnummer", ""))
    if row.get("prosjektfase") not in FUNDED or not R.is_orgnr(org) or not row.get("prosjektnummer"):
        return None
    row = {**row, "organisasjonsnummer": org, "tildelt_belop": _amount(row.get("tildelt_belop"))}
    rec = _record(row, RCN_COLS)
    return f"RCN:{rec['prosjektnummer']}", rec


def _amount(v: str | None) -> str:
    try:
        return str(int(round(float(v)))) if v else ""
    except ValueError:
        return v or ""


def org_of(rid: str, rec: dict) -> str:
    return rec.get("Organisasjonsnr") or rec.get("Organisasjonsnummer") or rec.get("organisasjonsnummer") or ""


def index(records: dict[str, str]) -> dict[str, list[str]]:
    by_org: dict[str, list[str]] = {}
    for rid, ln in records.items():
        by_org.setdefault(org_of(rid, json.loads(ln)), []).append(rid)
    return by_org


def parse_sf_web(data: bytes) -> dict[str, str]:
    out = {}
    for row in _xlsx.dicts(data):
        r = sf_web_record(row)
        if r:
            out[r[0]] = R.line(r[1])
    return out


# Batch -----------------------------------------------------------------------------------------
async def prepare_forskningsradet(f: Fetcher, orgs: list[str], *, cache_path: str | None = None,
                                  refresh: bool = True) -> dict:
    cache = R.load_cache("forskningsradet", cache_path or os.environ.get("KILDE_RCN_CACHE"))
    if cache is None:
        raise RuntimeError("Forskningsrådet cache missing (run scripts/build_forskningsradet.py)")
    records: dict[str, str] = dict(cache["records"])
    sources: dict[str, dict] = {k: dict(v) for k, v in cache["sources"].items()}
    if refresh:
        try:
            await _refresh_web_list(f, records, sources)
        except BudgetExhausted:
            raise
        except Exception as exc:  # noqa: BLE001 - the cache still answers
            log.warning("SkatteFUNN refresh failed: %r", exc)
    wanted = set(orgs)
    by_org = {o: v for o, v in index(records).items() if o in wanted}
    return {"by_org": by_org, "records": records, "sources": sources, "built_at": cache["built_at"]}


async def _refresh_web_list(f: Fetcher, records: dict[str, str], sources: dict[str, dict]) -> None:
    page = await f.get(SF_PAGE, robots=True)
    if page.status != 200:
        return
    m = WEB_LINK.search(page.text())
    if not m:
        return
    url = SF_BASE + m.group(1)
    if url == sources.get("SFW", {}).get("url"):
        return  # the cache already holds the current file
    snap = await f.get(url, robots=True)
    if snap.status != 200 or len(snap.content) >= 2_990_000:  # Fetcher caps bodies at 3 MB
        return
    fresh = parse_sf_web(snap.content)
    if not fresh:
        return
    for rid in [r for r in records if r.startswith("SFW:")]:
        del records[rid]
    records.update(fresh)
    sources["SFW"] = {"url": url, "retrieved_at": snap.retrieved_at, "sha256": snap.sha256}


# Per company -----------------------------------------------------------------------------------
def _sort_key(rid: str, rec: dict) -> str:
    return (rec.get("Vedtaksdato") or rec.get("Søknadsdato") or rec.get("Innsendt dato")
            or rec.get("prosjektstart") or rec.get("Fra-år") or rec.get("Prosjektets fra-år") or "")


def _period(a: str | None, b: str | None) -> str | None:
    a, b = (a or "")[:4], (b or "")[:4]
    return f"{a}–{b}" if a and b else (a or None)


def _value(rid: str, rec: dict, source_url: str) -> tuple[dict, str | None]:
    kind = rid.split(":", 1)[0]
    if kind in ("SF", "SFW"):
        decided = rec.get("Vedtaksdato")
        value = {"scheme": "SkatteFUNN (R&D tax credit)", "status": "approved", "project_number": rec.get("Prosjektnummer"),
                 "title": rec.get("Prosjekttittel"), "decision_date": decided,
                 "period": _period(rec.get("Fra-år") or rec.get("Prosjektets fra-år"),
                                   rec.get("Til-år") or rec.get("Prosjektets til-år")),
                 "source_file": source_url}
        return {k: v for k, v in value.items() if v}, decided
    value = {"scheme": "Research Council of Norway funding", "status": "funded" if rec.get("prosjektfase") == "Bevilgning"
             else "funded, completed", "project_number": rec.get("prosjektnummer"), "title": rec.get("prosjekttittel"),
             "period": _period(rec.get("prosjektstart"), rec.get("prosjektslutt")), "programme": rec.get("aktivitet"),
             "application_type": rec.get("soknadstype"),
             "awarded_nok": int(rec["tildelt_belop"]) if (rec.get("tildelt_belop") or "").isdigit() else None,
             "source_file": source_url}
    return {k: v for k, v in value.items() if v}, None


async def fetch_forskningsradet(f: Fetcher, ctx: CompanyContext, batch: dict | None = None) -> None:
    try:
        _emit(ctx, batch)
    except Exception as exc:  # noqa: BLE001
        ctx.error("forskningsradet", f"{type(exc).__name__}: {exc}")
        ctx.module_states["forskningsradet"] = "failed"
        if not ctx.claims_for("research_project"):
            ctx.mark("research_project", "activity", "failed", note=f"Forskningsrådet step failed ({type(exc).__name__})")


def _emit(ctx: CompanyContext, batch: dict | None) -> None:
    org = ctx.org
    if not batch or "records" not in batch:
        ctx.mark("research_project", "activity", "failed", note="Forskningsrådet data unavailable this run")
        ctx.module_states["forskningsradet"] = "failed"
        return
    rids = batch["by_org"].get(org) or []
    if not rids:
        ctx.mark("research_project", "activity", "not_available",
                 note="No approved SkatteFUNN project or Research Council funding lists this organisation number "
                      f"(cache built {batch['built_at'][:10]})")
        ctx.module_states["forskningsradet"] = "not_available"
        return
    items = [(rid, json.loads(batch["records"][rid])) for rid in rids]
    items.sort(key=lambda x: (_sort_key(*x), x[0]), reverse=True)
    for rid, rec in items[:MAX_PROJECTS]:
        kind = rid.split(":", 1)[0]
        src = batch["sources"].get(kind) or {}
        url = src.get("url") or {"SF": SF_HIST, "RCN": RCN_CSV}.get(kind, SF_PAGE)
        ln = batch["records"][rid]
        value, decided = _value(rid, rec, url)
        col = "Organisasjonsnr" if kind == "SFW" else ("Organisasjonsnummer" if kind == "SF" else "organisasjonsnummer")
        snap = R.record_snapshot(url, ln, src.get("retrieved_at") or batch["built_at"])
        ev = ctx.add_evidence(
            snap, source_class=OPEN_DATA, method=METHOD, licence=LIC_RCN if kind == "RCN" else LIC_SF,
            span=R.span_with(ln, must=f'"{col}":"{org}"'), effective_date=decided,
            note=(f"Row for project {value.get('project_number')} from {url}"
                  + (f" (file sha256 {src['sha256']})" if src.get("sha256") else "")
                  + ", stored as one JSON line with the file's own column names"
                  + ("; Excel serial dates written as ISO dates" if kind != "RCN" else "")))
        m = {"method": f"{col.lower()}_equals_orgnr", "detail": f"Column {col} '{org}' equals {org}"}
        ctx.add_claim("research_project", "activity", value, ev, as_of=decided or (value.get("period") or "")[:4] or None,
                      match=m, note=(f"{len(items)} projects in total; newest {MAX_PROJECTS} shown"
                                     if len(items) > MAX_PROJECTS else None))
    ctx.module_states["forskningsradet"] = "available"
