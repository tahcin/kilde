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
FOREIGN_FORMS = ("gmbh", "ag", "ab", "oy", "oyj", "aps", "sia", "uab", "ltd", "limited", "inc", "llc", "bv", "nv",
                 "sarl", "sas", "spa", "srl", "plc", "corp", "kft", "sro", "doo", "ehf", "kg", "asa", "as", "sa")
FOREIGN_TLDS = {"se", "dk", "fi", "de", "ee", "lt", "lv", "pl", "uk", "nl", "fr", "es", "it", "ch", "at", "be", "ru"}
FREE_MAIL = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "online.no", "live.no", "icloud.com", "hotmail.no"}


def fold(s: str) -> str:
    s = s.lower().replace("æ", "ae").replace("ø", "o").replace("å", "a")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


# Shared hosting platforms: the company's own site is the subdomain, not the platform.
HOSTED = ("barnehage.no", "wixsite.com", "squarespace.com", "webnode.no", "webnode.page", "blogspot.com",
          "wordpress.com", "weebly.com", "jimdofree.com", "jimdo.com", "123hjemmeside.no", "my.canva.site",
          "github.io", "netlify.app", "vercel.app", "business.site", "godaddysites.com", "mozello.com")


def registered_domain(url_or_host: str) -> str:
    host = urlsplit(url_or_host if "://" in url_or_host else "http://" + url_or_host).netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    for h in HOSTED:
        if host.endswith("." + h):
            return host
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
               and (c.match or {}).get("method") != "employer_orgnr"]
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
    own_forms = {fold(t) for t in re.findall(r"[\wæøå]+", reg.get("navn", "").lower())}
    label_alnum = re.sub(r"[^a-z0-9]", "", label)
    # 3. Social profiles from the site must name the company: the handle carries a distinctive token of
    #    the legal name or the site's domain label, or is a pure numeric ID. Site builders and agencies
    #    leave their own profiles in templates (a Surftown-built site linked Surftown's profiles).
    for c in derived:
        if c.field != "social_profile" or c.availability != "available":
            continue
        handle = fold(str(c.value).split("/", 1)[1] if "/" in str(c.value) else str(c.value))
        handle_alnum = re.sub(r"[^a-z0-9]", "", handle)
        numeric_id = bool(re.search(r"(profile\.php\?id=\d+|channel/uc[\w-]{10,}|/\d{6,}$|-\d{9,}$)", handle))
        named = any(re.sub(r"[^a-z0-9]", "", t) in handle_alnum for t in tokens if len(t) >= 4) or \
            (len(label_alnum) >= 3 and label_alnum in handle_alnum) or \
            (tokens and re.sub(r"[^a-z0-9]", "", "".join(tokens)) in handle_alnum)
        if not (named or numeric_id):
            withhold(c, f"social handle '{handle}' does not carry the company's name or its site's domain (possible template or agency link)")
            continue
        # A handle naming another legal entity (a foreign sister such as "hobartgmbh") is not ours.
        forms = set(re.findall(r"(?<![a-z])(" + "|".join(FOREIGN_FORMS) + r")(?![a-z])", re.sub(r"[^a-z]+", " ", handle))) | \
            {f for f in FOREIGN_FORMS if handle_alnum.endswith(f) and len(handle_alnum) > len(f) + 3}
        if forms - own_forms:
            withhold(c, f"social handle '{handle}' names another legal entity ({', '.join(sorted(forms - own_forms))})")

    # 4. Structured data (JSON-LD Organization) must describe this company: platform defaults such as a
    #    Shopify theme's own Organization block (name "Shopify", Shopify's social accounts) are withheld.
    for c in derived:
        if c.field != "structured_data_organization" or c.availability != "available":
            continue
        v = c.value if isinstance(c.value, dict) else {}
        name = fold(str(v.get("name") or ""))
        name_alnum = re.sub(r"[^a-z0-9]", "", name)
        named = bool(name_alnum) and (any(re.sub(r"[^a-z0-9]", "", t) in name_alnum for t in tokens if len(t) >= 4)
                                      or (len(label_alnum) >= 3 and label_alnum in name_alnum))
        if not named:
            withhold(c, f"structured data names '{v.get('name')}', not this company (theme or platform default)")
            continue
        same = v.get("sameAs")
        if isinstance(same, list):
            keep = [u for u in same if any(re.sub(r"[^a-z0-9]", "", t) in re.sub(r"[^a-z0-9]", "", fold(str(u)))
                                         for t in tokens if len(t) >= 4) or label_alnum in re.sub(r"[^a-z0-9]", "", fold(str(u)))]
            if len(keep) != len(same):
                c.value = {**v, "sameAs": keep}

    # 5. Norwegian branches of foreign companies (NUF) usually publish on a group site: no site news or pages.
    if (reg.get("organisasjonsform") or {}).get("kode") == "NUF":
        for c in derived:
            if c.availability == "available" and c.field in ("dated_news", "site_page_updated", "site_news"):
                withhold(c, "branch of a foreign company: site news usually belongs to the wider group")

    # 6. Contacts must match the site domain or the registry.
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


def check_batch(ctxs: list[CompanyContext]) -> list[str]:
    """A social profile published for two or more companies in one batch is a template or agency link:
    withhold it wherever the handle does not carry that company's own name."""
    owners: dict[str, list] = {}
    for ctx in ctxs:
        for c in ctx.claims:
            if c.field == "social_profile" and c.availability == "available":
                owners.setdefault(str(c.value), []).append((ctx, c))
    notes = []
    for value, items in owners.items():
        if len({ctx.org for ctx, _ in items}) < 2:
            continue
        for ctx, c in items:
            handle = re.sub(r"[^a-z0-9]", "", fold(value.split("/", 1)[-1]))
            tokens = distinctive_tokens((ctx.registry or {}).get("navn", ""))
            if not any(re.sub(r"[^a-z0-9]", "", t) in handle for t in tokens if len(t) >= 4):
                c.availability, c.value, c.confidence = "ambiguous", None, 0.0
                c.note = "Withheld by the publication gate: the same profile appears for several companies in this batch"
                notes.append(f"{ctx.org} social_profile: shared across companies ({value})")
    return notes
