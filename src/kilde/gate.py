"""Publication gate: a last, independent check on facts derived from websites.

The website connector proves which site belongs to a company. This gate re-checks the
derived facts (contacts, profiles, pages, careers, news, description) against the registry
before anything is published, so one connector mistake cannot become a wrong-company fact.
A fact that fails is withheld: its value is removed and it is marked `ambiguous` with the reason.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

from .model import CompanyContext

DERIVED = {"site_contact_email", "site_contact_phone", "social_profile", "site_page_updated", "site_news",
           "careers_page", "hiring_signal", "dated_news", "website_description", "website_title", "structured_data_organization"}
GENERIC = {
    "as", "asa", "sa", "da", "ans", "ba", "nuf", "ks", "enk", "ltd", "og", "and", "the", "of", "i",
    "holding", "holdings", "invest", "investering", "eiendom", "eiendommer", "eigedom", "bygg", "byggservice",
    "ror", "rør", "elektro", "el", "transport", "maskin", "service", "drift", "consult", "consulting", "regnskap",
    "norge", "norway", "norsk", "nordic", "group", "gruppen", "partner", "partners", "utvikling", "handel",
    "auto", "bil", "data", "media", "design", "solutions", "systems", "tech", "teknikk", "entreprenor",
    "entreprenør", "borettslag", "sameiet", "stiftelsen", "forening", "foreningen", "selskap", "senter",
}
FOREIGN_TLDS = {"se", "dk", "fi", "de", "ee", "lt", "lv", "pl", "uk", "nl", "fr", "es", "it", "ch", "at", "be", "ru"}
FREE_MAIL = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "online.no", "live.no", "icloud.com", "hotmail.no"}


def fold(s: str) -> str:
    s = s.lower().replace("æ", "ae").replace("ø", "o").replace("å", "a")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def registered_domain(url_or_host: str) -> str:
    host = urlsplit(url_or_host if "://" in url_or_host else "http://" + url_or_host).netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    return ".".join(parts[-3:]) if len(parts) > 2 and parts[-2] in ("co", "com") else ".".join(parts[-2:])


def distinctive_tokens(name: str) -> list[str]:
    toks = [fold(t) for t in re.findall(r"[\wæøåÆØÅ]+", name)]
    return [t for t in toks if len(t) >= 3 and fold(t) not in {fold(g) for g in GENERIC} and not t.isdigit()]


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def check(ctx: CompanyContext) -> list[str]:
    """Withhold derived web facts that fail consistency checks. Returns notes for the run report."""
    reg = ctx.registry or {}
    sites = ctx.claims_for("official_website")
    derived = [c for c in ctx.claims if c.field in DERIVED and c.availability == "available"
               and (c.match or {}).get("method") != "registry_declared"
               and "wikidata" not in (c.match or {}).get("method", "")]
    if not derived:
        return []
    notes: list[str] = []

    def withhold(c, why: str) -> None:
        c.availability = "ambiguous"
        c.note = f"Withheld by the publication gate: {why}"
        c.value = None
        c.confidence = 0.0
        notes.append(f"{ctx.org} {c.field}: {why}")

    if not sites:
        for c in derived:
            withhold(c, "no verified official website")
        return notes
    site_dom = registered_domain(str(sites[0].value))
    label = fold(site_dom.split(".")[0])
    tokens = distinctive_tokens(reg.get("navn", ""))
    reg_site = registered_domain(reg["hjemmeside"]) if reg.get("hjemmeside") else None
    reg_mail = (reg.get("epostadresse") or "").split("@")[-1].lower() or None
    country = ((reg.get("forretningsadresse") or reg.get("postadresse") or {}).get("landkode") or "NO")

    # 1. The site must plausibly be the company's own: its domain carries a distinctive name
    #    token, or it is the registry website, or the registry email domain.
    exclusive_brand = ((sites[0].match or {}).get("method") == "orgnr_on_page"
                       and ctx.facts.get("website_exclusive") is True)
    owned = (exclusive_brand or site_dom == reg_site or site_dom == reg_mail or
             any(t in label for t in tokens if len(t) >= 4) or
             (tokens and fold("".join(tokens)) in label))
    if not owned:
        for c in derived:
            withhold(c, f"site domain {site_dom} does not carry the company's name and is not its registry website or email domain")
        return notes
    # 2. A foreign country-code domain for a Norwegian-registered company: derived facts withheld.
    tld = site_dom.rsplit(".", 1)[-1]
    if country == "NO" and tld in FOREIGN_TLDS:
        for c in derived:
            withhold(c, f"foreign domain .{tld} for a Norwegian-registered company (possibly a foreign sister company)")
        return notes
    # 3. Contacts must match the site domain or the registry.
    reg_phones = {_digits(reg.get(k))[-8:] for k in ("telefon", "mobil") if reg.get(k)}
    for c in derived:
        if c.availability != "available":
            continue
        if c.field == "site_contact_email":
            dom = str(c.value).split("@")[-1].lower()
            managed = ((reg.get("organisasjonsform") or {}).get("kode") in ("ESEK", "BRL", "SAM", "BBL"))
            if managed and registered_domain(dom) != site_dom:
                withhold(c, "housing and co-ownership entities: contact emails only on the site's own domain (others are usually the manager's)")
            elif dom != site_dom and dom != reg_mail and registered_domain(dom) != site_dom:
                withhold(c, f"email domain {dom} differs from the verified site {site_dom} and the registry email")
        elif c.field == "site_contact_phone":
            d = _digits(str(c.value))
            norwegian = str(c.value).startswith("+47") or (len(d) == 8)
            if not norwegian and d[-8:] not in reg_phones:
                withhold(c, "foreign phone number that is not the registry phone")
    return notes
