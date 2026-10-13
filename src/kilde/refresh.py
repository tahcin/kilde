"""Refresh: compare this run with the previous envelopes, emit typed changes, and never let a
failed fetch erase the last supported value."""
from __future__ import annotations

import json
from typing import Any

from .model import CompanyContext, Evidence

# Single-valued fields: a different value is a change.
SINGLE = {
    "legal_name": ("changed_name", True), "business_address": ("changed_address", True),
    "postal_address": ("changed_postal_address", False), "legal_form": ("changed_legal_form", True),
    "industry": ("changed_industry", False), "employees_registered": ("changed_employees", False),
    "share_capital": ("changed_share_capital", False), "legal_status": ("changed_legal_status", True),
    "latest_filed_accounts_year": ("new_filing", True), "official_website": ("changed_website", True),
    "website_description": ("changed_description", False), "statutory_purpose": ("changed_purpose", False),
    "registry_website": ("changed_registry_website", False), "lei": ("changed_lei", False),
}
# Multi-valued fields: identity key per item -> (added type, removed type, material)
MULTI = {
    "ceo": ("new_role", "ended_role", True), "board_chair": ("new_role", "ended_role", True),
    "board_member": ("new_role", "ended_role", False), "board_deputy_member": ("new_role", "ended_role", False),
    "auditor": ("new_role", "ended_role", False), "accountant": ("new_role", "ended_role", False),
    "registered_workplace": ("new_location", "closed_location", False),
    "job_ad": ("new_job", "closed_job", False), "social_profile": ("new_profile", "removed_profile", False),
    "group_subsidiary": ("new_subsidiary", "removed_subsidiary", False),
}
FINANCIAL = {"revenue", "operating_result", "profit_before_tax", "annual_result", "total_assets", "equity", "debt",
             "salary_costs", "cash_and_bank"}
# Which module produced a field, so we know whether a missing value is real or a failed fetch.
FIELD_MODULE = {
    **{f: "registry" for f in SINGLE if f not in ("official_website", "website_description", "lei")},
    "official_website": "website", "website_description": "website", "social_profile": "website", "lei": "gleif",
    "ceo": "roles", "board_chair": "roles", "board_member": "roles", "board_deputy_member": "roles",
    "auditor": "roles", "accountant": "roles", "registered_workplace": "locations", "job_ad": "jobs",
    "group_subsidiary": "registry", **{f: "financials" for f in FINANCIAL},
}


def item_key(field: str, value: Any) -> str:
    if isinstance(value, dict):
        for k in ("uuid", "organisation_number", "url"):
            if value.get(k):
                return f"{k}:{value[k]}"
        if field in FINANCIAL:
            return f"{value.get('year')}:{value.get('account_type')}"
        if value.get("name"):
            return f"name:{value['name']}|{value.get('role_code', '')}"
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _index(env: dict) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for c in env.get("claims", []):
        if c.get("availability") != "available":
            continue
        out.setdefault(c["field"], {})[item_key(c["field"], c.get("value"))] = c
    return out


def _ev_ref(env: dict, claim: dict) -> list[dict]:
    evs = {e["id"]: e for e in env.get("evidence", [])}
    return [{k: evs[i].get(k) for k in ("source_url", "retrieved_at", "content_sha256", "claim_span")}
            for i in claim.get("evidence_ids", []) if i in evs]


def diff(ctx: CompanyContext, previous: dict | None) -> list[dict]:
    """Typed changes between the previous envelope and this run's claims; carries forward values on failure."""
    if not previous:
        return []
    prev = _index(previous)
    cur: dict[str, dict[str, Any]] = {}
    for c in ctx.claims:
        if c.availability == "available":
            cur.setdefault(c.field, {})[item_key(c.field, c.value)] = c
    ok_modules = {m for m, s in ctx.module_states.items() if s in ("available", "not_available")}
    changes: list[dict] = []
    prev_run = (previous.get("run") or {}).get("run_id")

    def record(kind: str, field: str, material: bool, before: dict | None, after: Any | None) -> None:
        ch = {"type": kind, "field": field, "material": material, "previous_run_id": prev_run}
        if before is not None:
            ch["previous_value"] = before.get("value")
            ch["previous_evidence"] = _ev_ref(previous, before)
            ch["first_observed"] = before.get("first_observed") or (ch["previous_evidence"][0]["retrieved_at"]
                                                                     if ch["previous_evidence"] else None)
        if after is not None:
            ch["current_value"] = after.value
            ch["current_claim_id"] = after.id
            ch["current_evidence_ids"] = after.evidence_ids
        changes.append(ch)

    for field in set(prev) | set(cur):
        module = FIELD_MODULE.get(field)
        trusted = module is None or module in ok_modules
        p, c = prev.get(field, {}), cur.get(field, {})
        if field in SINGLE:
            kind, material = SINGLE[field]
            pv = next(iter(p.values()), None)
            cv = next(iter(c.values()), None)
            if pv and cv and json.dumps(pv.get("value"), sort_keys=True) != json.dumps(cv.value, sort_keys=True):
                record(kind, field, material, pv, cv)
            elif cv and not pv and previous.get("claims") is not None:
                record(kind if field != "latest_filed_accounts_year" else "new_filing", field, material, None, cv)
            elif pv and not cv:
                if trusted:
                    record(f"removed_{field}", field, material, pv, None)
                else:
                    carry_forward(ctx, field, pv, previous)
        elif field in MULTI:
            add_t, rem_t, material = MULTI[field]
            for k in set(c) - set(p):
                record(add_t, field, material, None, c[k])
            for k in set(p) - set(c):
                if trusted:
                    record(rem_t, field, material, p[k], None)
                else:
                    carry_forward(ctx, field, p[k], previous)
        elif field in FINANCIAL:
            for k in set(c) - set(p):
                record("new_financial_year", field, field == "revenue", None, c[k])
    return changes


def carry_forward(ctx: CompanyContext, field: str, prev_claim: dict, previous: dict) -> None:
    """The source failed this run: keep the last supported value with its original evidence."""
    section = prev_claim.get("section", "identity")
    evs = []
    for ref in _ev_ref(previous, prev_claim):
        ev = ctx.add_evidence(None, source_url=ref["source_url"], source_class="previous_run",
                              span=ref.get("claim_span"), method="carried_forward_v1",
                              note=f"Retrieved {ref['retrieved_at']} in run {(previous.get('run') or {}).get('run_id')}")
        ev.retrieved_at = ref["retrieved_at"]
        ev.content_sha256 = ref.get("content_sha256")
        evs.append(ev)
    if evs:
        ctx.add_claim(field, section, prev_claim.get("value"), evs, as_of=prev_claim.get("as_of"),
                      match=prev_claim.get("match"), confidence=prev_claim.get("confidence", 1.0),
                      note="Source failed this run; last supported value carried forward, not re-verified")
