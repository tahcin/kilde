"""DiBK sentral godkjenning: the national register of approved construction enterprises.

Source. Direktoratet for byggkvalitet's public register https://sgregister.dibk.no. Each enterprise page is
addressed by organisation number (https://sgregister.dibk.no/enterprises/<orgnr>, HTTP 404 when the
enterprise is not in the register). robots.txt disallows /search and /api/ but says "/enterprises/ must stay
crawlable", so only that page is read, with robots.txt checked by the fetcher. The register is public by
law (byggesaksforskriften kapittel 13); DiBK states no licence for reuse. Pages of enterprises without a
valid approval carry `X-Robots-Tag: noindex`; those are not published.

Match. The page's "Organisasjonsnummer" equals ours (the URL is also keyed on it). Names are never used.

Fact. Approval status, approval period (valid from / to) and the approved areas (function, subject area,
tiltaksklasse). Evidence is the page itself, with exact spans for the org number and the approval period.

Requests: 1 per company (plus robots.txt once). `likely_construction` lets the orchestrator skip companies
whose industry codes make an approval implausible when the budget is tight.
"""
from __future__ import annotations

import html
import re

from ..http import Blocked, BudgetExhausted, Fetcher
from ..model import OFFICIAL_REGISTER_OTHER, CompanyContext

BASE = "https://sgregister.dibk.no/enterprises/{org}"
LICENCE = "Direktoratet for byggkvalitet, public register of central approval (no reuse licence stated)"
METHOD = "dibk_sgregister_page_v1"
FIELD = "approval_sentral_godkjenning"
MAX_AREAS = 25

_ORG = re.compile(r"Organisasjonsnummer</b>\s*<p[^>]*>\s*(\d{9})\s*</p>")
_STATUS = re.compile(r"<h3[^>]*>\s*(Foretaket[^<]{3,120})</h3>")
_PERIOD = re.compile(r"Godkjenningsperiode:\s*</b>\s*<span[^>]*>\s*(\d{2}\.\d{2}\.\d{4})\s*(?:&ndash;|–|-)\s*(\d{2}\.\d{2}\.\d{4})\s*</span>")
_NAME = re.compile(r"<h2[^>]*>([^<]{2,200})</h2>")
_ROW = re.compile(r'<tr><td class="py-2 text-xl">([^<]+)</td><td class="py-2 text-xl">([^<]+)</td>'
                  r'<td class="py-2 text-xl">([^<]+)</td>')
# NACE prefixes where central approval is common: construction, architecture/engineering, installation.
LIKELY = ("41", "42", "43", "71", "68.1", "33", "28.2", "81.3", "74.1")


def likely_construction(reg: dict | None) -> bool:
    codes = [((reg or {}).get(k) or {}).get("kode", "") for k in ("naeringskode1", "naeringskode2", "naeringskode3")]
    return any(c.startswith(LIKELY) for c in codes if c)


def _iso(d: str) -> str:
    dd, mm, yyyy = d.split(".")
    return f"{yyyy}-{mm}-{dd}"


def parse(page: str) -> dict:
    out: dict = {}
    m = _ORG.search(page)
    out["organisation_number"] = m.group(1) if m else None
    m = _NAME.search(page)
    out["name"] = html.unescape(m.group(1).strip()) if m else None
    m = _STATUS.search(page)
    out["status_text"] = html.unescape(m.group(1).strip()) if m else None
    m = _PERIOD.search(page)
    if m:
        out["valid_from"], out["valid_to"] = _iso(m.group(1)), _iso(m.group(2))
        out["valid_from_raw"], out["valid_to_raw"] = m.group(1), m.group(2)
    areas = []
    for f_, area, cls in _ROW.findall(page):
        areas.append({"function": html.unescape(f_.strip()), "area": html.unescape(area.strip()), "class": cls.strip()})
    out["areas"] = areas
    return out


async def prepare_dibk(f: Fetcher, orgs: list[str]) -> dict:
    """No batch source exists; this only sets a polite pace for the per-company page reads."""
    f.set_rate("sgregister.dibk.no", 0.25)
    return {"orgs": set(orgs)}


async def fetch_dibk(f: Fetcher, ctx: CompanyContext, batch: dict | None = None) -> None:
    try:
        await _run(f, ctx)
    except Blocked as exc:
        ctx.module_states["dibk"] = "blocked"
        ctx.mark(FIELD, "identity", "blocked", note=str(exc)[:200])
    except Exception as exc:  # noqa: BLE001
        reason = "budget_exhausted" if isinstance(exc, BudgetExhausted) else f"{type(exc).__name__}: {exc}"
        ctx.error("dibk", reason)
        ctx.module_states["dibk"] = "failed"
        if not ctx.claims_for(FIELD):
            ctx.mark(FIELD, "identity", "failed", note=f"DiBK register lookup failed ({reason})")


async def _run(f: Fetcher, ctx: CompanyContext) -> None:
    org = ctx.org
    url = BASE.format(org=org)
    snap = await f.get(url, robots=True)
    ctx.requests += 1
    if snap.status == 404:
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTER_OTHER, method=METHOD, licence=LICENCE, span="HTTP 404")
        ctx.mark(FIELD, "identity", "not_available", ev, note="Not in DiBK's register of centrally approved enterprises")
        ctx.module_states["dibk"] = "not_available"
        return
    if snap.status != 200:
        raise RuntimeError(f"HTTP {snap.status} for {url}")
    page = snap.text()
    info = parse(page)
    if info["organisation_number"] != org:
        raise RuntimeError(f"page organisation number {info['organisation_number']!r} does not equal {org}")
    approved = bool(info.get("valid_to")) and (info.get("status_text") or "").startswith("Foretaket har sentral godkjenning")
    if not approved:
        # Includes pages DiBK marks noindex (no valid approval): checked, but nothing is published.
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTER_OTHER, method=METHOD, licence=LICENCE,
                              span=f"Organisasjonsnummer {org}")
        ctx.mark(FIELD, "identity", "not_available", ev,
                 note="Listed in DiBK's register without a valid central approval (page marked noindex)")
        ctx.module_states["dibk"] = "not_available"
        return
    m = {"method": "organisasjonsnummer_equals_orgnr",
         "detail": f"sgregister.dibk.no/enterprises/{org} shows Organisasjonsnummer {org}"}
    ev_org = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTER_OTHER, method=METHOD, licence=LICENCE,
                              span=f"Organisasjonsnummer {org}")
    ev_period = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTER_OTHER, method=METHOD, licence=LICENCE,
                                 span=f"Godkjenningsperiode: {info['valid_from_raw']}–{info['valid_to_raw']}",
                                 effective_date=info["valid_from"])
    value = {"register": "Sentral godkjenning (DiBK)", "status": "approved", "valid_from": info["valid_from"],
             "valid_to": info["valid_to"], "areas": info["areas"][:MAX_AREAS], "url": url}
    if len(info["areas"]) > MAX_AREAS:
        value["areas_total"] = len(info["areas"])
    ctx.add_claim(FIELD, "identity", value, [ev_org, ev_period], as_of=info["valid_from"], match=m)
    ctx.module_states["dibk"] = "available"
