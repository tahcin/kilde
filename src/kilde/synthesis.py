"""Deterministic summary: every sentence is built from claims that already have evidence, and cites them.

No adjectives that the evidence does not carry. Company-owned descriptions are quoted and
labelled as the company's own words. Unknowns are listed explicitly.
"""
from __future__ import annotations

from typing import Any

from .model import Claim, CompanyContext

SECTION_WORDS = {
    "financials": "Filed financial figures", "leadership": "Registered leadership", "locations": "Registered workplaces",
    "web_presence": "Verified company website", "hiring": "Active job ads", "activity": "Dated public activity",
}
LEGAL_FORMS = {
    "AS": "private limited company", "ASA": "public limited company", "ENK": "sole proprietorship",
    "BRL": "housing cooperative", "ESEK": "jointly owned property section", "STI": "foundation",
    "NUF": "Norwegian branch of a foreign company", "FLI": "association", "SA": "cooperative", "DA": "partnership (shared liability)",
    "ANS": "general partnership", "KS": "limited partnership", "SAM": "co-ownership", "IKS": "inter-municipal company",
    "KF": "municipal enterprise", "SF": "state enterprise", "STAT": "state body", "KOMM": "municipality",
    "ORGL": "public body", "BA": "company with limited liability", "PK": "pension fund", "GFS": "mutual insurance company",
    "SPA": "savings bank", "FKF": "county municipal enterprise", "PRE": "shipping partnership", "UTLA": "foreign entity",
}
PLATFORM = {"linkedin": "LinkedIn", "facebook": "Facebook", "instagram": "Instagram", "youtube": "YouTube", "x": "X",
            "twitter": "X", "tiktok": "TikTok"}


def money(amount: Any, cur: str | None) -> str:
    try:
        a = float(amount)
    except (TypeError, ValueError):
        return f"{amount} {cur or ''}".strip()
    sign = "-" if a < 0 else ""
    a = abs(a)
    if a >= 1e9:
        s = f"{a / 1e9:.2f} billion"
    elif a >= 1e6:
        s = f"{a / 1e6:.1f} million"
    else:
        s = f"{a:,.0f}".replace(",", " ")
    return f"{sign}{s} {cur or ''}".strip()


def first(ctx: CompanyContext, field: str) -> Claim | None:
    xs = ctx.claims_for(field)
    return xs[0] if xs else None


def summarise(ctx: CompanyContext, changes: list[dict]) -> dict[str, Any]:
    S: list[dict[str, Any]] = []

    def say(text: str, *claims: Claim | None) -> None:
        ids = [c.id for c in claims if c is not None]
        if ids:
            S.append({"text": text, "claim_ids": ids})

    name = first(ctx, "legal_name")
    if not name:
        return {"text": "No registry record was returned for this organisation number.", "sentences": [],
                "unknowns": ["Identity could not be established, so no other facts were published."]}
    n = name.value
    form, muni, reg, ind = (first(ctx, f) for f in ("legal_form", "municipality", "registration_date", "industry"))
    deleted = first(ctx, "deleted_date")
    if deleted:
        say(f"{n} was deleted from the Central Coordinating Register on {deleted.value}.", name, deleted)
    else:
        bits = [n, "is"]
        kind = LEGAL_FORMS.get(form.value.get("code"), (form.value.get("name") or "entity").lower()) if form else "entity"
        bits.append(f"{'an' if kind[0] in 'aeiou' else 'a'} {kind}")
        if muni:
            bits.append(f"based in {muni.value['name'].title()}")
        if reg:
            bits.append(f"registered on {reg.value}")
        text = " ".join(bits)
        if ind:
            text += f", in the industry \"{ind.value['name']}\" ({ind.value['code']})"
        say(text + ".", name, form, muni, reg, ind)

    status = first(ctx, "legal_status")
    if status and any(status.value.get(k) for k in ("konkurs", "underAvvikling", "underTvangsavviklingEllerTvangsopplosning")):
        flags = [w for k, w in (("konkurs", "bankruptcy"), ("underAvvikling", "liquidation"),
                                ("underTvangsavviklingEllerTvangsopplosning", "forced dissolution")) if status.value.get(k)]
        say(f"The registry flags it as under {' and '.join(flags)}.", status)

    desc = first(ctx, "website_description")
    purpose = first(ctx, "statutory_purpose") or first(ctx, "registered_activity")
    if desc:
        say(f"Its own website describes it as: \"{desc.value[:220]}\"", desc)
    elif purpose:
        say(f"Its registered purpose is: \"{purpose.value[:220]}\"", purpose)

    emp = first(ctx, "employees_registered")
    if emp:
        if isinstance(emp.value, int):
            say(f"It has {emp.value:,} registered employee{'' if emp.value == 1 else 's'}" + (f" (as of {emp.as_of})." if emp.as_of else "."), emp)
        else:
            say("It has registered employees, but fewer than five, so Brreg does not publish the exact number.", emp)

    # Latest company accounts, else group accounts.
    fin = [c for c in ctx.claims_for("revenue") + ctx.claims_for("annual_result")]
    if fin:
        latest = max(c.value.get("year") or "" for c in fin)
        rev = next((c for c in ctx.claims_for("revenue") if c.value.get("year") == latest and c.value["account_type"] == "company"), None)
        res = next((c for c in ctx.claims_for("annual_result") if c.value.get("year") == latest and c.value["account_type"] == "company"), None)
        rev = rev or next((c for c in ctx.claims_for("revenue") if c.value.get("year") == latest), None)
        res = res or next((c for c in ctx.claims_for("annual_result") if c.value.get("year") == latest), None)
        parts = []
        if rev:
            parts.append(f"revenue of {money(rev.value['amount'], rev.value['currency'])}")
        if res:
            parts.append(f"an annual result of {money(res.value['amount'], res.value['currency'])}")
        if parts:
            kind = (rev or res).value["account_type"]
            say(f"In its {latest} {kind} accounts it reported {' and '.join(parts)}.", rev, res)
        prev_year = str(int(latest) - 1) if latest.isdigit() else None
        prev_rev = next((c for c in ctx.claims_for("revenue") if c.value.get("year") == prev_year
                         and rev and c.value["account_type"] == rev.value["account_type"]), None)
        if rev and prev_rev:
            say(f"Filed revenue the year before ({prev_year}) was {money(prev_rev.value['amount'], prev_rev.value['currency'])}.", prev_rev)

    ceo, chair = first(ctx, "ceo"), first(ctx, "board_chair")
    if ceo and chair and ceo.value.get("name") == chair.value.get("name"):
        say(f"{ceo.value['name']} is registered as both general manager and board chair.", ceo, chair)
    else:
        if ceo:
            say(f"{ceo.value['name']} is registered as general manager.", ceo)
        if chair:
            say(f"{chair.value['name']} chairs the board.", chair)
    owner = first(ctx, "owner")
    if owner:
        say(f"{owner.value['name']} is the registered owner.", owner)
    aud = first(ctx, "auditor")
    if aud:
        say(f"The registered auditor is {aud.value['name']}.", aud)

    parent = first(ctx, "group_parent")
    if parent:
        say(f"It is part of a group whose direct parent is {parent.value.get('name')} ({parent.value.get('organisation_number')}).", parent)
    subs = ctx.claims_for("group_subsidiary")
    if subs:
        say(f"The registry lists {len(subs)} subsidiar{'y' if len(subs) == 1 else 'ies'}.", *subs[:5])
    wp = ctx.claims_for("registered_workplace")
    if wp:
        munis = sorted({c.value.get("municipality", "").title() for c in wp if c.value.get("municipality")})
        say(f"It has {len(wp)} registered workplace{'s' if len(wp) != 1 else ''}"
            + (f" in {', '.join(munis[:5])}" if munis else "") + ".", *wp[:5])

    site = first(ctx, "official_website")
    if site:
        how = {"orgnr_on_page": "the site shows its organisation number",
               "registry_website_and_name": "the registry lists this site and it shows the company name",
               "phone_and_name": "the site shows the registered phone number and the company name",
               "address_and_name": "the site shows the registered address and the company name"}.get((site.match or {}).get("method"), "identity checks")
        say(f"Its website is {site.value} (verified because {how}).", site)
    socials = ctx.claims_for("social_profile")
    by_source: dict[str, list[Claim]] = {}
    for c in socials:
        method = (c.match or {}).get("method", "")
        src = "registry" if method == "registry_declared" else ("wikidata" if "wikidata" in method or "p2333" in method else "site")
        by_source.setdefault(src, []).append(c)
    lead = {"site": "The verified site links to its profiles on", "registry": "Its registry entry lists its profile on",
            "wikidata": "Wikidata (matched on the organisation number) lists its profiles on"}
    for src, items in by_source.items():
        names = sorted({PLATFORM.get(c.value.get("platform", ""), c.value.get("platform", "")) for c in items})
        say(f"{lead[src]} {', '.join(names)}.", *items)
    lei = first(ctx, "lei")
    if lei:
        reg = (lei.value.get("registration_status") or "").upper()
        state = {"ISSUED": "registration current", "LAPSED": "registration lapsed", "PENDING_TRANSFER": "transfer pending",
                 "RETIRED": "retired"}.get(reg, f"registration status {reg.lower()}" if reg else "status not stated")
        say(f"It holds the Legal Entity Identifier {lei.value.get('lei')} ({state}).", lei)
    lic = ctx.claims_for("financial_licence")
    if lic:
        say(f"Finanstilsynet lists {len(lic)} licence{'s' if len(lic) != 1 else ''} for it.", *lic[:3])

    jobs = ctx.claims_for("job_ad")
    if jobs:
        newest = max(jobs, key=lambda c: c.value.get("published") or "")
        say(f"NAV lists {len(jobs)} active job ad{'s' if len(jobs) != 1 else ''} with its organisation number; the newest, "
            f"\"{newest.value.get('title')}\", was published {(newest.value.get('published') or '')[:10]}.", *jobs[:5])

    reg_changes = ctx.claims_for("registry_change")
    if reg_changes:
        latest = max(reg_changes, key=lambda c: c.value.get("date") or "")
        what = ", ".join(latest.value.get("changed") or []) or latest.value.get("type")
        say(f"The registry recorded {len(reg_changes)} update{'s' if len(reg_changes) != 1 else ''} in the last 12 months; "
            f"the latest, on {latest.value.get('date')}, changed: {what}.", *reg_changes[:5])
    if changes:
        material = [c for c in changes if c.get("material")]
        kinds = sorted({c["type"].replace("_", " ") for c in (material or changes)})
        ids = [c["current_claim_id"] for c in changes if c.get("current_claim_id")]
        ids_claims = [cl for cl in ctx.claims if cl.id in ids][:5]
        text = f"Since the previous run: {len(changes)} change{'s' if len(changes) != 1 else ''} ({', '.join(kinds[:6])})."
        if ids_claims:
            say(text, *ids_claims)
        else:
            S.append({"text": text, "claim_ids": [], "change_refs": [c["type"] for c in changes][:10]})

    unknowns = []
    sections = {c.section for c in ctx.claims if c.availability == "available"}
    for sec, words in SECTION_WORDS.items():
        if sec in sections:
            continue
        marks = [c for c in ctx.claims if c.section == sec and c.availability != "available"]
        reason = marks[0].note if marks and marks[0].note else None
        state = marks[0].availability if marks else "not_available"
        unknowns.append(f"{words}: {state.replace('_', ' ')}" + (f" ({reason})." if reason else "."))
    return {"text": " ".join(s["text"] for s in S), "sentences": S, "unknowns": unknowns}
