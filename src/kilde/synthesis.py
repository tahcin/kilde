"""Deterministic summary: every sentence is built from claims that already have evidence, and cites them.

No adjectives that the evidence does not carry. Company-owned descriptions are quoted and
labelled as the company's own words. Unknowns are listed explicitly.
"""
from __future__ import annotations

import re
from typing import Any

from .model import Claim, CompanyContext

SECTION_WORDS = {
    "financials": "Filed financial figures", "leadership": "Registered leadership", "locations": "Registered workplaces",
    "web_presence": "Verified company website", "hiring": "Job ads or a careers page", "activity": "Dated public activity",
}
LEGAL_FORMS = {
    "AS": "private limited company", "ASA": "public limited company", "ENK": "sole proprietorship",
    "BRL": "housing cooperative", "ESEK": "condominium owners' association", "STI": "foundation",
    "NUF": "Norwegian branch of a foreign company", "FLI": "association", "SA": "cooperative", "DA": "partnership (shared liability)",
    "ANS": "general partnership", "KS": "limited partnership", "SAM": "co-ownership", "IKS": "inter-municipal company",
    "KF": "municipal enterprise", "SF": "state enterprise", "STAT": "state body", "KOMM": "municipality",
    "ORGL": "public body", "BA": "company with limited liability", "PK": "pension fund", "GFS": "mutual insurance company",
    "SPA": "savings bank", "FKF": "county municipal enterprise", "PRE": "shipping partnership", "UTLA": "foreign entity",
}
PLATFORM = {"linkedin": "LinkedIn", "facebook": "Facebook", "instagram": "Instagram", "youtube": "YouTube", "x": "X",
            "twitter": "X", "tiktok": "TikTok"}


def clip(text: str, n: int = 220) -> str:
    """Shorten at a word boundary and say so with an ellipsis."""
    text = " ".join(str(text).split())
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + " ..."


def quoted(text: str) -> str:
    """A closing quotation that ends its sentence: a full stop follows the quote unless the quoted text
    already ends with one (or with ! ? or an ellipsis)."""
    return f'"{text}"' + ("" if re.search(r"[.!?]$", text) else ".")


def join_and(items: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    items = [str(x) for x in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def no_em_dash(text: str) -> str:
    """Em dashes from quoted page text become en dashes (house style: no em dashes in Kilde's own prose)."""
    return text.replace("\u2014", "\u2013")


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


def distinct(claims: list[Claim], *keys: str) -> list[Claim]:
    """One claim per distinct entity: counts in sentences are of companies, places, ads or awards, never of
    registry lines (a company can be listed twice in one source). Claims without the key stay as they are."""
    out, seen = [], set()
    for c in claims:
        v = c.value if isinstance(c.value, dict) else {}
        k = tuple(str(v.get(x) or "") for x in keys)
        if any(k):
            if k in seen:
                continue
            seen.add(k)
        out.append(c)
    return out


def count(ctx: CompanyContext, field: str, shown: int, one: str, many: str) -> tuple[str, str]:
    """('150 projects', '; the 8 most recent are shown') for a capped field: the source's true total, or 'at least'
    when the source was read only in part; never a capped subset presented as the full count."""
    total, exact = ctx.source_total(field, shown)
    noun = one if total == 1 else many
    if not exact:
        return f"at least {total} {noun}", (f"; {shown} {'is' if shown == 1 else 'are'} shown" if shown < total else "")
    recent = "the most recent is shown" if shown == 1 else f"the {shown} most recent are shown"
    return f"{total} {noun}", (f"; {recent}" if shown < total else "")


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
        if ctx.module_states.get("registry") in ("failed", "timeout"):
            return {"text": "The registry could not be checked in this run (time, request budget or source error), "
                            "so no facts were published.", "sentences": [],
                    "unknowns": ["Identity not checked in this run; nothing else was attempted."]}
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
            inc = first(ctx, "incorporation_date")
            if inc and str(inc.value) < str(reg.value):
                bits.append(f"founded on {inc.value} and registered on {reg.value}")
            else:
                bits.append(f"registered on {reg.value}")
        text = " ".join(bits)
        if ind:
            from .codes import code_name
            en = code_name("nace_en", ind.value.get("code"))
            if str(ind.value.get("code")) == "00.000":  # Brreg's "Uoppgitt": no industry given
                text += f", with no industry stated in the registry (code {ind.value['code']})"
            else:
                text += f", in the industry \"{en or ind.value['name']}\" ({ind.value['code']})"
        say(text + ".", name, form, muni, reg, ind, first(ctx, "incorporation_date") if reg else None)

    status = first(ctx, "legal_status")
    if status and any(status.value.get(k) for k in ("konkurs", "underAvvikling", "underTvangsavviklingEllerTvangsopplosning")):
        flags = [w for k, w in (("konkurs", "bankruptcy"), ("underAvvikling", "liquidation"),
                                ("underTvangsavviklingEllerTvangsopplosning", "forced dissolution")) if status.value.get(k)]
        say(f"The registry flags it as under {' and '.join(flags)}.", status)

    desc = first(ctx, "website_description")
    purpose = first(ctx, "statutory_purpose") or first(ctx, "registered_activity")
    if desc:
        say(f"Its own website describes it as: {quoted(clip(desc.value))}", desc)
    elif purpose:
        say(f"Its registered purpose is: {quoted(clip(purpose.value))}", purpose)

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
        def pick(field: str, kind: str):
            return next((c for c in ctx.claims_for(field) if c.value.get("year") == latest and c.value["account_type"] == kind), None)
        # One account type per sentence: company accounts if filed for that year, else group accounts.
        kind = "company" if (pick("revenue", "company") or pick("annual_result", "company")) else "group"
        rev, res = pick("revenue", kind), pick("annual_result", kind)
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
        if parent.value.get("relationship") == "indirect parent":
            say(f"It is part of a group in which {parent.value.get('name')} ({parent.value.get('organisation_number')}) "
                f"controls it indirectly, through its own subsidiaries.", parent)
        else:
            say(f"It is part of a group whose direct parent is {parent.value.get('name')} ({parent.value.get('organisation_number')}).", parent)
    subs = distinct(ctx.claims_for("group_subsidiary"), "organisation_number")
    if subs:
        n_ind = sum(1 for c in subs if c.value.get("relationship") == "indirect subsidiary")
        split = (f" ({len(subs) - n_ind} held directly and {n_ind} indirectly, through its subsidiaries)"
                 if n_ind and n_ind < len(subs) else " (all held indirectly, through its subsidiaries)" if n_ind else "")
        n_subs, more = count(ctx, "group_subsidiary", len(subs), "subsidiary", "subsidiaries")
        if more:
            split, more = "", f"; {len(subs)} are shown"
        say(f"The registry lists {n_subs}{split}{more}.", *subs)
    wp = distinct(ctx.claims_for("registered_workplace"), "organisation_number")
    if wp:
        munis = sorted({c.value.get("municipality", "").title() for c in wp if c.value.get("municipality")})
        n_wp, more_wp = count(ctx, "registered_workplace", len(wp), "registered workplace", "registered workplaces")
        say(f"It has {n_wp}"
            + (f" in {join_and(munis[:5] + ([f'{len(munis) - 5} other municipalit' + ('y' if len(munis) == 6 else 'ies')] if len(munis) > 5 else []))}"
               if munis else "") + (more_wp.replace("; ", "; of these, ").replace(" are shown", " were read")
                                    if more_wp else "") + ".", *wp)

    site = first(ctx, "official_website")
    if site:
        how = {"orgnr_on_page": "the site shows its organisation number",
               "registry_website_and_name": "the registry lists this site and it shows the company name",
               "phone_and_name": "the site shows the registered phone number and the company name",
               "address_and_name": "the site shows the registered address and the company name",
               "registry_email_domain_and_name": "the registry email address uses this site's domain and the site "
                                                 "shows the company name",
               "workplace_address_and_name": "its domain is the name of a registered workplace of the company, "
                                             "and the site shows that workplace's name and registered address",
               "legal_name_unique": "the site shows the exact registered legal name, which no other registered "
                                    "entity carries, and its domain is that name",
               "registry_declared": "the company registered it with Brønnøysundregistrene"}.get((site.match or {}).get("method"))
        say(f"Its website is {site.value}" + (f" (verified because {how})." if how else ", matched to the registry by Kilde's identity checks."), site)
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
        say(f"{lead[src]} {join_and(names)}.", *items)
    lei = first(ctx, "lei")
    if lei:
        reg = (lei.value.get("registration_status") or "").upper()
        state = {"ISSUED": "registration current", "LAPSED": "registration lapsed", "PENDING_TRANSFER": "transfer pending",
                 "RETIRED": "retired"}.get(reg, f"registration status {reg.lower()}" if reg else "status not stated")
        say(f"It holds the Legal Entity Identifier {lei.value.get('lei')} ({state}).", lei)
    lic = distinct(ctx.claims_for("financial_licence"), "code", "role", "on_behalf_of", "finanstilsynet_id")
    if lic:
        own = [c for c in lic if (c.value or {}).get("role") not in ("agent", "branch")]
        agent = [c for c in lic if (c.value or {}).get("role") == "agent"]
        parts = []
        if own:
            parts.append(f"holds {len(own)} licence{'s' if len(own) != 1 else ''} of its own")
        if agent:
            names = sorted({str((c.value or {}).get("on_behalf_of") or "").strip() for c in agent} - {""})
            parts.append(f"is a registered agent under {len(agent)} licence{'s' if len(agent) != 1 else ''} held by others"
                         + (f" ({', '.join(names)})" if names else ""))
        n_lic, _x = ctx.source_total("financial_licence", len(lic))
        if parts:
            say("According to Finanstilsynet it " + " and ".join(parts) +
                (f" ({n_lic} licences are listed in all; {len(lic)} are shown)" if n_lic > len(lic) else "") + ".",
                *(own + agent))

    jobs = distinct(ctx.claims_for("job_ad"), "uuid")
    if jobs:
        newest = max(jobs, key=lambda c: c.value.get("published") or "")
        indexed, _x = ctx.source_total("job_ad", len(jobs))
        if indexed > len(jobs):
            lead = (f"NAV's feed holds {indexed} active job ads with its organisation number; the {len(jobs)} re-checked "
                    f"in this run are shown, and the newest of them")
        else:
            lead = f"NAV lists {len(jobs)} active job ad{'s' if len(jobs) != 1 else ''} with its organisation number; the newest"
        say(f"{lead}, \"{newest.value.get('title')}\", was published {(newest.value.get('published') or '')[:10]}.", *jobs[:5])

    careers = ctx.claims_for("hiring_signal")
    if careers and not jobs:
        say(f"Its verified website has a careers page ({careers[0].value}).", careers[0])
    news = sorted(ctx.claims_for("dated_news"), key=lambda c: c.as_of or "", reverse=True)
    if news:
        title = str(news[0].value).rsplit(" (", 1)[0]
        say(f"Kilde collected {len(news)} dated news post{'s' if len(news) != 1 else ''} from its website; the newest, "
            f"\"{clip(title, 120)}\", is from {news[0].as_of}.", *news[:3])

    awards = sorted(distinct(ctx.claims_for("public_contract_award"), "notice"), key=lambda c: c.as_of or "", reverse=True)
    if awards:
        n_aw, more = count(ctx, "public_contract_award", len(awards), "contract award", "contract awards")
        say(f"TED, the EU procurement journal, lists {n_aw} won with its "
            f"organisation number, the latest on {awards[0].as_of}{more}.", *awards)
    tenders = distinct(ctx.claims_for("procurement_notice"), "notice")
    if tenders:
        n_t, more = count(ctx, "procurement_notice", len(tenders), "procurement notice", "procurement notices")
        say(f"As a buyer it has published {n_t} on TED{more}.", *tenders)
    projects = sorted(distinct(ctx.claims_for("research_project"), "scheme", "project_number"), key=lambda c: c.as_of or "", reverse=True)
    if projects:
        n_p, more = count(ctx, "research_project", len(projects), "funded or approved R&D project",
                          "funded or approved R&D projects")
        say(f"The Research Council of Norway lists {n_p} "
            f"for it (including SkatteFUNN), the latest \"{clip(str((projects[0].value or {}).get('title', '')), 100)}\"{more}.",
            *projects)
    inspections = sorted(ctx.claims_for("food_inspection"), key=lambda c: c.as_of or "", reverse=True)
    if inspections:
        # One claim per inspected place; each carries how many inspections are on file for it.
        places = {(c.value or {}).get("place_organisation_number") or (c.value or {}).get("place") for c in inspections}
        on_file = sum(int((c.value or {}).get("inspections_on_file") or 1) for c in inspections
                      if c is max((x for x in inspections if (x.value or {}).get("place") == (c.value or {}).get("place")),
                                  key=lambda x: x.as_of or ""))
        latest = inspections[0]
        n_objects, _m = ctx.source_total("food_inspection", len(inspections))  # inspected places, one claim each
        where = ("its premises" if len(places) == 1 else str(len(places)) + " of its premises")
        capped = (f" (the register has {n_objects} inspected places for it; the {len(inspections)} most recently "
                  f"inspected are shown)") if n_objects > len(inspections) else ""
        say(f"Mattilsynet's Smilefjes register lists {on_file} food safety inspection{'s' if on_file != 1 else ''} at "
            f"{where}{capped}; the latest, on {latest.as_of}, found: {(latest.value or {}).get('result', 'see source')}.",
            *inspections[:3])
    approval = first(ctx, "approval_sentral_godkjenning")
    if approval:
        v = approval.value or {}
        say(f"It holds a central approval for construction work from DiBK"
            + (f", valid until {v.get('valid_to')}" if v.get("valid_to") else "") + ".", approval)

    reg_changes = distinct(ctx.claims_for("registry_change"), "update_id")
    if reg_changes:
        latest = max(reg_changes, key=lambda c: c.value.get("date") or "")
        what = ", ".join(latest.value.get("changed") or []) or latest.value.get("type")
        n_ch, more = count(ctx, "registry_change", len(reg_changes), "update", "updates")
        say(f"The registry recorded {n_ch} in the last 12 months; "
            f"the latest, on {latest.value.get('date')}, changed: {what}{more}.", *reg_changes)
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
    for s in S:
        s["text"] = no_em_dash(s["text"])
    return {"text": " ".join(s["text"] for s in S), "sentences": S, "unknowns": [no_em_dash(u) for u in unknowns]}
