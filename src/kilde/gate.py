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
from .templates import template_post
from .words import generic_word, person_handle, social_tokens

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


LEGAL_FORMS = {"as", "asa", "sa", "da", "ans", "ba", "nuf", "ks", "enk", "sti", "iks", "kf"}


def _places(reg: dict) -> set[str]:
    """Place words (counties, regions, larger towns, the registry municipality and post town), folded."""
    from .connectors.website import REGIONS
    out = set(REGIONS)
    for key in ("forretningsadresse", "beliggenhetsadresse", "postadresse"):
        a = reg.get(key) or {}
        for k in ("kommune", "poststed"):
            out |= {fold(w) for w in re.findall(r"[a-zæøåäöü]+", (a.get(k) or "").lower()) if len(w) >= 3}
    return out


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fold(s))


def legal_name_site_problem(ctx: CompanyContext, site) -> str | None:
    """An official website proven only by the exact legal name (legal_name_unique) must still fit the registry:
    an AS or ASA, a registered domain that is exactly the legal name, and a proof span that quotes the name
    followed by its legal form in capitals. Returns the reason when it does not."""
    reg = ctx.registry or {}
    form = ((reg.get("organisasjonsform") or {}).get("kode") or "").upper()
    if form not in ("AS", "ASA"):
        return f"legal-name proof used for legal form {form or 'unknown'}"
    words = re.findall(r"[\wæøåÆØÅ]+", (reg.get("navn") or "").lower().replace("&", " og "))
    while words and words[-1] in ("as", "asa"):
        words.pop()
    if not words:
        return "no registered name to compare"
    alt = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa"})
    variants = [words, [w for w in words if w not in ("og", "and")]]
    names = {_alnum(t) for ws in variants for t in ("".join(ws), "".join(ws).translate(alt))}
    label = _alnum(registered_domain(str(site.value)).split(".")[0])
    if label not in names:
        return f"domain {registered_domain(str(site.value))} is not the legal name"
    # the proof's span as stored now, and the matched value it must contain (the span is made exact later)
    spans = [x for e in ctx.evidence if e.id in site.evidence_ids
             for x in (e.claim_span, (getattr(ctx, "cores", None) or {}).get(e.id)) if x]
    quoted = {_alnum(" ".join(ws)) + form.lower() for ws in variants}
    capital = form[0] + "(?:" + form[1:] + "|" + form[1:].lower() + ")"  # 'AS' or 'As', never 'as'
    if not any(re.search(r"(?<![A-Za-z])" + capital + r"(?![A-Za-z/])", s) and any(q in _alnum(s) for q in quoted)
               for s in spans):
        return "the proof does not quote the legal name with its legal form"
    return None


STRONG_SITE_PROOFS = ("orgnr_on_page", "phone_and_name", "registry_website_and_name")


def short_name_domain_proven(ctx: CompanyContext, site, label: str, tokens: list[str]) -> bool:
    """A domain whose label is exactly one short distinctive word of the legal name (ege.no for 'ING YNGVE EGE
    AS') is the company's own when the site was proven by the org number, the registry phone or the registry
    website, and that proof quotes the whole registered name (legal form aside). Only exact equality counts,
    never a substring, and generic words are not tokens, so a chain, agency or generic domain stays out."""
    if (site.match or {}).get("method") not in STRONG_SITE_PROOFS:
        return False
    if not any(len(t) >= 3 and label == t for t in tokens):
        return False
    words = re.findall(r"[\wæøåÆØÅ]+", ((ctx.registry or {}).get("navn") or "").lower())
    form = (((ctx.registry or {}).get("organisasjonsform") or {}).get("kode") or "").lower()
    while words and words[-1] in {form, "as", "asa"}:
        words.pop()
    full = _alnum(" ".join(words))
    if len(full) < 6:
        return False
    cores = getattr(ctx, "cores", None) or {}
    spans = [x for e in ctx.evidence if e.id in site.evidence_ids for x in (e.claim_span, cores.get(e.id)) if x]
    return any(full in _alnum(s) for s in spans)


def check(ctx: CompanyContext) -> list[str]:
    """Withhold derived web facts that fail consistency checks. Returns notes for the run report."""
    reg = ctx.registry or {}
    sites = ctx.claims_for("official_website")
    notes: list[str] = []
    if sites and (sites[0].match or {}).get("method") == "legal_name_unique":
        why = legal_name_site_problem(ctx, sites[0])
        if why:
            for c in ctx.claims:
                if c.field in ("official_website", "official_website_url") and c.availability == "available":
                    c.availability, c.value, c.confidence = "ambiguous", None, 0.0
                    c.note = f"Withheld by the publication gate: {why}"
            notes.append(f"{ctx.org} official_website: {why}")
            sites = []
    derived = [c for c in ctx.claims if c.field in DERIVED and c.availability == "available"
               and (c.match or {}).get("method") != "registry_declared"
               and (c.match or {}).get("method") != "employer_orgnr"]
    if not derived:
        return notes

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
    if not owned and short_name_domain_proven(ctx, sites[0], label, tokens):
        owned = True
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
    # social handles: only tokens that identify this company (no trade, industry, place or person-name word)
    places = _places(reg)
    name_ws = [w for w in re.findall(r"[\wæøåÆØÅ]+", (reg.get("navn") or "").lower()) if fold(w) not in LEGAL_FORMS]
    stoks = social_tokens(name_ws, places)
    # 3. Social profiles from the site must name the company: the handle carries a distinctive token of
    #    the legal name or the site's domain label, or is a pure numeric ID. Site builders and agencies
    #    leave their own profiles in templates (a Surftown-built site linked Surftown's profiles).
    for c in derived:
        if c.field != "social_profile" or c.availability != "available":
            continue
        handle = fold(str(c.value).split("/", 1)[1] if "/" in str(c.value) else str(c.value))
        handle_alnum = re.sub(r"[^a-z0-9]", "", handle)
        numeric_id = bool(re.search(r"(profile\.php\?id=\d+|channel/uc[\w-]{10,}|/\d{6,}$|-\d{9,}$|^\d{9,}$)", handle))
        named = any(t in handle_alnum for t in stoks) or \
            (len(label_alnum) >= 3 and label_alnum in handle_alnum and not generic_word(label_alnum)
             and label_alnum not in places) or \
            (tokens and re.sub(r"[^a-z0-9]", "", "".join(tokens)) in handle_alnum)
        if person_handle(handle_alnum, name_ws, places) and not numeric_id:
            withhold(c, f"social handle '{handle}' is a person's name from the legal name, not a company profile")
            continue
        if not (named or numeric_id):
            withhold(c, f"social handle '{handle}' does not carry the company's name or its site's domain (possible template or agency link)")
            continue
        # A handle naming another legal entity (a foreign sister such as "hobartgmbh") is not ours.
        # a form glued to the end of the handle counts once, as its longest match ('entraasa' ends in asa, not sa)
        glued = max((f for f in FOREIGN_FORMS if handle_alnum.endswith(f) and len(handle_alnum) > len(f) + 3),
                    key=len, default=None)
        # the ending is read on token boundaries first ('arctic-feed-ingredients-as' ends in 'as', not 'sas'),
        # then right after our own name run together ('ingredientsas' after 'ingredients' is our 'as')
        toks = [t for t in re.split(r"[-_.]+", handle) if t]
        name_sq = re.sub(r"[^a-z0-9]", "", "".join(fold(w) for w in name_ws))
        if len(toks) > 1 and toks[-1] in FOREIGN_FORMS:
            glued = toks[-1]
        elif name_sq and name_sq in handle_alnum:
            rest = handle_alnum[handle_alnum.rfind(name_sq) + len(name_sq):]
            if rest in FOREIGN_FORMS or not rest:
                glued = rest or None
        forms = set(re.findall(r"(?<![a-z])(" + "|".join(FOREIGN_FORMS) + r")(?![a-z])", re.sub(r"[^a-z]+", " ", handle))) | \
            ({glued} if glued else set())
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

    # 5b. Site builders' default posts (hello world, "this is your first post", lorem ipsum) are never news.
    for c in derived:
        if c.availability != "available" or c.field not in ("dated_news", "site_page_updated", "site_news"):
            continue
        v = c.value
        title = re.sub(r"\s*\([^()]*\)\s*$", "", v) if isinstance(v, str) else (v or {}).get("title")
        url = (v or {}).get("url") if isinstance(v, dict) else None
        detail = str((c.match or {}).get("detail") or "").split(" ")[0]
        if template_post(title, url) or template_post(None, detail if detail.startswith("http") else None):
            withhold(c, "a site builder's default post or page, not company news")

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


# Site text fields whose values are page text: tags and (also double-encoded) HTML entities are decoded.
SITE_TEXT = ("website_title", "website_description", "dated_news")
_ENTITY = re.compile(r"&(?:#\d{1,7}|#x[0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")


def _plain(text: str) -> str:
    """Page text as a reader sees it: no tags, entities decoded (twice when the page double-encoded them)."""
    import html as _html
    out = re.sub(r"<[^>]+>", " ", text) if re.search(r"<[A-Za-z/!][^>]*>", text) else text
    for _ in range(2):
        if not _ENTITY.search(out):
            break
        out = _html.unescape(out)
    return " ".join(out.split())


def tidy(ctx: CompanyContext) -> None:
    """Cosmetic hygiene after the gate: one claim per (field, value) and one per social profile, page text as
    plain text, and string values with whitespace runs collapsed."""
    from .socials import duplicates
    for c in ctx.claims:
        if c.availability != "available" or type(c.value) is not str:
            continue  # a CanonicalSocial (str subclass) keeps its shape
        v = _plain(c.value) if c.field in SITE_TEXT else " ".join(c.value.split())
        if not v:
            c.availability, c.value, c.note = "not_available", None, "description was markup only"
        else:
            c.value = v
    socials = [c for c in ctx.claims if c.field == "social_profile" and c.availability == "available"]
    dup_ids = {id(socials[i]) for i in duplicates([str(c.value) for c in socials])}
    seen = set()
    keep = []
    for c in ctx.claims:
        if c.availability == "available":
            key = (c.field, str(c.value).lower())
            if key in seen or id(c) in dup_ids:
                continue
            seen.add(key)
        keep.append(c)
    ctx.claims[:] = keep
