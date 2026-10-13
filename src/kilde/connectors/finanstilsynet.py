"""Finanstilsynet virksomhetsregister: licences held by the company (banks, insurers, investment
firms, payment institutions, debt collectors, accountants, auditors, estate agents and so on).

One keyless request per company to the v1 search endpoint, which returns licences inline (v2
needs a second call). Free-text search can return other entities, so a result is used only
when its `organisationNumber` field equals ours. Open data published by Finanstilsynet;
no licence is named in the API spec, so we attribute "Kilde: Finanstilsynet".
"""
from __future__ import annotations

import json
from typing import Any

from ..http import BudgetExhausted, Fetcher
from ..model import OFFICIAL_REGISTER_OTHER, CompanyContext

API = "https://api.finanstilsynet.no/registry/v1"
HUMAN = "https://www.finanstilsynet.no/virksomhetsregisteret/"
LICENCE = "Open data, Kilde: Finanstilsynet (licence not stated)"
METHOD = "finanstilsynet_registry_v1"
JSON_HEADERS = {"Accept": "application/json"}
MAX_LICENCES = 40


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))[:400]


def _text(obj: dict | None, *keys: str) -> str | None:
    """Norwegian/English text pair, Norwegian first."""
    if not obj:
        return None
    for k in keys or ("norwegian", "english"):
        if obj.get(k):
            return obj[k].strip()
    return None


def licence_value(lic: dict, our_name: str | None) -> dict[str, Any]:
    t = lic.get("licenceType") or {}
    licensed = (lic.get("licensedEntity") or {}).get("legalEntityName")
    role = {"Licensed": "licence_holder", "Agent": "agent", "Branch": "branch"}.get(lic.get("serviceProviderType"),
                                                                                 lic.get("serviceProviderType"))
    value: dict[str, Any] = {
        "type": _text(t.get("name"), "english", "norwegian"),
        "type_no": _text(t.get("name"), "norwegian"),
        "code": t.get("code"),
        "legal_basis": _text(t.get("description")),
        "registered_date": (lic.get("registeredDate") or "")[:10] or None,
        "role": role,
        "supervisor_country": ((lic.get("supervisoryAuthority") or {}).get("country") or {}).get("iso3"),
    }
    if licensed and licensed != our_name:
        value["on_behalf_of"] = licensed
    return {k: v for k, v in value.items() if v is not None}


async def fetch_finanstilsynet(f: Fetcher, ctx: CompanyContext) -> None:
    try:
        await _run(f, ctx)
    except Exception as exc:  # never raise into the orchestrator
        reason = "budget_exhausted" if isinstance(exc, BudgetExhausted) else f"{type(exc).__name__}: {exc}"
        ctx.error("finanstilsynet", reason)
        ctx.module_states["finanstilsynet"] = "failed"
        if not ctx.claims_for("financial_licence"):
            ctx.mark("financial_licence", "identity", "failed", note=f"Finanstilsynet lookup failed ({reason})")


async def _run(f: Fetcher, ctx: CompanyContext) -> None:
    org = ctx.org
    url = f"{API}/legal-entities/search?query={org}"
    snap = await f.get(url, headers=JSON_HEADERS)
    ctx.requests += 1
    if snap.status != 200:
        raise RuntimeError(f"HTTP {snap.status} for {url}")
    body = snap.json()
    entities = [e for e in body.get("legalEntities") or [] if e.get("organisationNumber") == org]
    if not entities:
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTER_OTHER, method=METHOD, licence=LICENCE,
                              span=_dump({"query": org, "total": body.get("total"),
                                          "organisationNumbers": [e.get("organisationNumber") for e in
                                                                  (body.get("legalEntities") or [])][:5]}))
        ctx.mark("financial_licence", "identity", "not_available", ev,
                 note="No entity with this organisation number in Finanstilsynet's registry")
        ctx.module_states["finanstilsynet"] = "not_available"
        return
    emitted = 0
    for ent in entities:
        m = {"method": "organisation_number_field",
             "detail": f"Finanstilsynet organisationNumber {ent.get('organisationNumber')} "
                       f"({ent.get('finanstilsynetId')}) equals {org}"}
        head = {"organisationNumber": ent.get("organisationNumber"), "finanstilsynetId": ent.get("finanstilsynetId"),
                "name": ent.get("name")}
        for lic in (ent.get("licences") or [])[:MAX_LICENCES]:
            value = licence_value(lic, ent.get("name"))
            if not value.get("type") and not value.get("code"):
                continue
            value["finanstilsynet_id"] = ent.get("finanstilsynetId")
            span = _dump({**head, "licence": {"licenceType": {"code": value.get("code"),
                                                              "name": (lic.get("licenceType") or {}).get("name")},
                                              "registeredDate": lic.get("registeredDate"),
                                              "serviceProviderType": lic.get("serviceProviderType"),
                                              "licensedEntity": (lic.get("licensedEntity") or {}).get("legalEntityName")}})
            ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTER_OTHER, method=METHOD, licence=LICENCE, span=span,
                                  effective_date=value.get("registered_date"),
                                  note=f"Human-readable register: {HUMAN}")
            ctx.add_claim("financial_licence", "identity", value, ev, as_of=value.get("registered_date"), match=m)
            emitted += 1
    if emitted:
        ctx.module_states["finanstilsynet"] = "available"
        return
    ent = entities[0]
    ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTER_OTHER, method=METHOD, licence=LICENCE,
                          span=_dump({"organisationNumber": ent.get("organisationNumber"),
                                      "finanstilsynetId": ent.get("finanstilsynetId"), "licences": []}))
    ctx.mark("financial_licence", "identity", "not_available", ev,
             note="Listed in Finanstilsynet's registry without an active licence")
    ctx.module_states["finanstilsynet"] = "not_available"
