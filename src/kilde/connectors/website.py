"""Company website: candidate discovery, identity proof and page extraction.

A site is published as the official website only when it is proven to belong to this exact
organisation number:

- exact: the site prints our organisation number (text, footer or structured data);
- strong: the registry lists this website and the site shows the legal name; or the site
  shows the registry phone number, or the full registered street address and postcode,
  together with the legal name.

A site that prints another organisation number and never ours is a related site (manager,
parent, accountant) and is reported as such, never as the official site. Name-only matches
are ambiguous and are not published. Everything else on the site (description, contact,
social profiles, structured data, sitemap dates, careers link) is extracted only after proof.

robots.txt is obeyed for every company-site path, and each company costs at most about
eight HTTP requests.
"""
from __future__ import annotations

import asyncio
import collections
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

import dns.asyncresolver
import dns.exception
import dns.resolver
from bs4 import BeautifulSoup

from ..http import Blocked, BudgetExhausted, Fetcher
from ..spans import contains_core
from ..templates import body_word_count, is_template_url, stub_posts, template_post
from ..model import COMPANY_OWNED, CompanyContext, Evidence, Snapshot
from ..words import generic_word, person_handle, regional_variant, social_tokens

METHOD = "website_identity_v1"
EXTRACT = "website_extract_v1"
LICENCE = "company-owned page; robots.txt allowed"
SECTION = "web_presence"

MAX_REQUESTS = 12         # HTTP requests per company, robots.txt and Brreg name checks included
RETRY_CAP = 20            # cap on the pipeline's second, unhurried attempt
VERIFIED_CAP = 16         # raised cap once a site is proven and exclusive (news and careers)
STEP_BUDGET = 6           # requests each for dated news and hiring on a verified exclusive site
NEWS_BUDGET = 8           # dated news: listing, its page 2 or feed, and article pages for undated listings
RELATED_STRICT = False    # True: every non-exclusive site becomes related_website; False (default): a company's own domain that only names other companies stays official, with nothing derived
RESERVE = 3               # stop when the run budget has fewer than this left
MAX_INTERNAL = 3          # extra pages per candidate when the homepage is not conclusive
DNS_TIMEOUT = 3.0
CONNECT_PROBE = 4.0

FREE_MAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "live.no", "live.com",
    "msn.com", "icloud.com", "me.com", "mac.com", "yahoo.com", "yahoo.no", "online.no", "altibox.no", "lyse.net",
    "broadpark.no", "getmail.no", "frisurf.no", "c2i.net", "start.no", "tele2.no", "post.com", "mail.com",
    "epost.no", "chello.no", "bluecom.no", "ebnett.no", "hotmail.co.uk", "aol.com", "protonmail.com", "proton.me",
    "gmx.com", "gmx.net", "loqal.no", "netcom.no", "telenor.no", "nextgentel.no", "haugnett.no", "tdcadsl.no",
    "sensewave.com", "enivest.net", "tussa.com", "mimer.no", "kvamnet.no", "neasonline.no", "fiberbredband.no",
    "vikenfiber.no", "skynet.be", "yandex.com", "mail.ru", "qq.com", "163.com", "web.de", "post.no", "posten.no",
    "c2i.no", "bluezone.no", "styrepost.no", "styreweb.no", "styrerommet.no", "hotmail.se", "outlook.dk", "live.se", "icloud.no", "pm.me", "gmx.de", "ymail.com",
}
# Hosts that are platforms, not a company's own site.
PLATFORM_HOSTS = ("facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com",
                  "tiktok.com", "finn.no", "proff.no", "gulesider.no", "1881.no", "google.com", "mittanbud.no",
                  "brreg.no", "purehelp.no", "forvalt.no")
LEGAL_SUFFIX = {"as", "asa", "sa", "ans", "da", "ba", "nuf", "ks", "sf", "enk", "a/s", "iks", "kf", "fkf", "sti",
                "ltd", "limited", "gmbh", "ab", "aps", "oy", "inc", "llc", "bv"}
# Words dropped from the second, shorter name-derived guess.
GENERIC_WORDS = {"holding", "holdings", "invest", "investment", "investering", "eiendom", "eiendommer", "gruppen",
                 "group", "norge", "norway", "norden", "nordic", "consult", "consulting", "service", "services",
                 "drift", "utvikling", "i", "og", "the", "of", "and", "co", "ny", "nye"}
# Single-word labels that are too common to guess as a domain on their own.
COMMON_LABELS = {"bygg", "eiendom", "holding", "invest", "consult", "service", "transport", "design", "media", "data",
                 "norge", "norsk", "gruppen", "group", "capital", "partners", "partner", "energi", "solutions",
                 "system", "systems", "tech", "digital", "hus", "bil", "auto", "film", "foto", "musikk", "mat",
                 "helse", "regnskap", "revisjon", "advokat", "invest", "nord", "sor", "vest", "ost", "nordic",
                 "global", "international", "marine", "maritime", "shipping", "offshore", "energy", "trading",
                 "handel", "import", "export", "management", "ventures", "venture", "kapital", "finans",
                 "borettslag", "sameie", "stiftelse", "forening", "lag", "klubb", "senter", "gard", "gaard",
                 "how", "home", "art", "one", "best", "first", "next", "smart", "pro", "max", "plus"}
PARKED = re.compile(
    r"domain (?:name )?(?:is |may be )?for sale|buy this domain|this domain (?:is|has been) (?:parked|registered)|"
    r"domenet (?:er )?til salgs|kjøp (?:dette )?domenet|dette domenet (?:er|eies)|domenet er registrert|"
    r"parked (?:free|domain)|domain parking|parkert (?:hos|domene)|is parked|under construction|"
    r"web ?hotel(?:l)? (?:er|is) (?:aktivert|ready)|default web page|website coming soon|nettsiden kommer snart|"
    r"siden er under (?:arbeid|konstruksjon)|welcome to nginx|apache2? (?:ubuntu )?default page|it works!|"
    r"this site can.t be reached|hugedomains|sedo domain|dan\.com|afternic|bodis|parkingcrew|"
    r"denne siden er under oppbygging|plesk default page|cpanel|\bis not found\b|site not found|page not found|"
    r"no such (?:site|app)|account (?:has been )?suspended|suspended (?:page|account|domain)|"
    r"there is no (?:web)?site|ingen nettside|nettstedet finnes ikke|error 40[34]|404 not found|"
    r"this (?:web)?site is (?:currently )?(?:unavailable|offline)|domain (?:has )?expired|domenet er utløpt", re.I)
PARKED_HOSTS = ("sedo.com", "dan.com", "afternic.com", "hugedomains.com", "godaddy.com", "parkingcrew.net",
                "bodis.com", "uniregistry.com", "undeveloped.com", "domainmarket.com", "sav.com", "domeneshop.no",
                "one.com", "webhuset.no", "porkbun.com", "namecheap.com")
INTERNAL_KEY = re.compile(r"kontakt|contact|om-oss|om_oss|omoss|om oss|about|personvern|privacy|vilkår|vilkar|"
                          r"impressum|firmainfo|selskapet|"
                          r"find.?us|finn.?oss|finn.?frem|besøk.?oss|besok.?oss|visit.?us|veibeskrivelse", re.I)
INTERNAL_PRIORITY = (re.compile(r"kontakt|contact", re.I), re.compile(r"om.?oss|about|firmainfo|selskapet", re.I),
                     re.compile(r"personvern|privacy", re.I), re.compile(r"vilk|impressum", re.I))
CAREERS_TEXT = re.compile(r"\b(karriere|jobb hos oss|jobbe hos oss|ledige stillinger|stillinger|careers?|jobs|"
                          r"work with us|join us)\b", re.I)
CAREERS_PATH = re.compile(r"/(karriere|jobb|jobbe-hos-oss|ledige-stillinger|stillinger|careers?|jobs?)(/|$|\?|\.)",
                          re.I)
ATS_HOSTS = ("teamtailor.com", "webcruiter.no", "jobbnorge.no", "recman.no", "recman.page", "easycruit.com",
             "hrmanager.no", "reachmee.com", "varbi.com", "jobylon.com", "smartrecruiters.com", "workday.com")
ORG_CONTEXT = re.compile(r"(org(?:anisasjons)?\.?\s*-?\s*n(?:r|o|ummer)\.?|organi[sz]ation\s*(?:no|number)\.?|"
                         r"foretaksregisteret|business\s*(?:id|reg)|\bmva\b|\bvat\b|\bno\s?(?=\d))", re.I)
NINE = re.compile(r"(?<![\d.,])(?:NO\s?)?([89]\d{2})[\s. ]?(\d{3})[\s. ]?(\d{3})(?:\s?MVA)?(?![\d,])")
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_TEXT = re.compile(r"(?<![\d])(?:\+|00)?(?:47[\s.\-\u00a0]?)?(?:\d[\s.\-\u00a0]?){7}\d(?![\s.\-\u00a0]?\d)")
PHONE_LABEL = re.compile(r"(?<![a-zæøå])(tlf|telefon|phone|mobil|mob|tel|ring oss|sentralbord)(?![a-zæøå])\.?|\+47",
                         re.I)
DATE_LIKE = re.compile(r"^\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}|\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4})\s*$")


def phone_shaped(raw: str) -> bool:
    """A written number that looks like a phone, not a date: 8 digits together, or grouped 2-2-2-2, 3-2-3,
    4-4, 3-3-2 or 2-3-3 (after an optional +47 or 0047)."""
    t = re.sub(r"^\s*(\+|00)?47[\s.\-\u00a0]*", "", raw.strip())
    if DATE_LIKE.match(t):
        return False
    groups = [len(g) for g in re.split(r"[\s.\-\u00a0]+", t) if g]
    return groups in ([8], [2, 2, 2, 2], [3, 2, 3], [4, 4], [3, 3, 2], [2, 3, 3])
PLACEHOLDER = re.compile(r"(?i)lorem ipsum|dolor sit amet|legg inn en (kort|beskrivelse)|gi en kort|skriv en kort|"
                         r"just another wordpress site|hello world!?|hei verden|sample page|eksempelside|"
                         r"this is an example page|dette er en eksempelside|your (company )?description here|"
                         r"add a (short )?description|edit this text|rediger denne teksten|velkommen til wordpress|"
                         r"welcome to wordpress|coming soon|under construction|your site title|site tagline")
PLACEHOLDER_SLUG = re.compile(r"(?i)/(hello-world|hei-verden|sample-page|eksempelside|lorem-ipsum)(/|$)")
GENERIC_DESC = re.compile(r"just another wordpress site|^(home|hjem|forside)$|coming soon|kommer snart|"
                          r"^(?:my|min) (?:site|side)$", re.I)
GENERIC_MAILBOX = ("post", "kontakt", "contact", "info", "firmapost", "mail", "hello", "hei", "office", "salg",
                   "sales", "booking", "ordre", "kundeservice", "support", "resepsjon", "press", "presse", "media",
                   "jobb", "jobs", "application", "regnskap", "faktura", "invoice", "ettermarked", "service")

SOCIAL = {
    "linkedin": ("linkedin.com",),
    "facebook": ("facebook.com", "fb.com", "fb.me"),
    "instagram": ("instagram.com",),
    "youtube": ("youtube.com",),
    "x": ("x.com", "twitter.com"),
    "tiktok": ("tiktok.com",),
    "pinterest": ("pinterest.com", "no.pinterest.com", "pinterest.no"),
}
FB_RESERVED = {"me", "sharer", "sharer.php", "share", "share.php", "dialog", "plugins", "login", "login.php", "home",
               "home.php", "groups", "watch", "events", "help", "policies", "privacy", "settings", "marketplace",
               "gaming", "hashtag", "search", "pg", "photo.php", "story.php", "permalink.php", "notes", "fundraisers", "#"}
SOCIAL_DROP = re.compile(r"sharer|/share|/intent|/dialog|/plugins|/hashtag|/watch|/embed|/results|/p/|/reel|"
                         r"/explore|/accounts|/login|/signup|/help|/policies|/privacy|/legal|/home$|/search|"
                         r"/shareArticle|/feed|/posts/|/photos|/events/|/i/|/status/|/video/|/tr\?|/tr$|/groups/",
                         re.I)
PLACEHOLDER_HANDLES = {"yourpage", "username", "your-page", "yourcompany", "company", "example", "wix", "wordpress",
                       "squarespace", "shopify", "godaddy", "elementor", "envato", "themeforest", "yourname",
                       "your-company", "pages", "page", "profile.php", "people", "in", "user", "channel", "c",
                       "#", "facebook", "instagram", "twitter", "linkedin", "youtube", "tiktok"}
ORG_TYPES = re.compile(r"Organi[sz]ation|Corporation|LocalBusiness|Business|Store|Restaurant|Company|"
                       r"ProfessionalService|LegalService|AccountingService|HomeAndConstructionBusiness|"
                       r"AutomotiveBusiness|MedicalBusiness|Dentist|Physician|HealthAndBeautyBusiness|"
                       r"FoodEstablishment|LodgingBusiness|Contractor|Plumber|Electrician|RealEstateAgent|"
                       r"TravelAgency|Hotel|CafeOrCoffeeShop|Bakery|NGO|EducationalOrganization|SportsOrganization")


# ---------------------------------------------------------------- helpers
def _translit(s: str) -> str:
    s = s.lower().replace("æ", "ae").replace("ø", "o").replace("å", "a").replace("ä", "ae").replace("ö", "o")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def name_words(name: str) -> list[str]:
    """Legal name as lower-case words, legal-form suffix removed, '&' as 'og'."""
    s = name.lower().replace("&", " og ").replace("+", " og ")
    s = re.sub(r"\b(i likvidasjon|under avvikling|konkursbo)\b", " ", s)
    words = re.findall(r"[0-9a-zæøåäöüéèáà/]+", s)
    while words and words[-1] in LEGAL_SUFFIX:
        words.pop()
    while words and words[0] in LEGAL_SUFFIX:
        words.pop(0)
    return [w for w in words if w != "/"]


def host_of(url: str) -> str:
    h = urlsplit(url if "//" in url else f"https://{url}").hostname or ""
    return h.lower().rstrip(".")


def bare(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def same_site(a: str, b: str) -> bool:
    a, b = bare(a), bare(b)
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def is_platform(host: str) -> bool:
    return any(host == p or host.endswith("." + p) for p in PLATFORM_HOSTS)


def mod11_ok(org: str) -> bool:
    if len(org) != 9 or not org.isdigit():
        return False
    s = sum(int(d) * w for d, w in zip(org[:8], (3, 2, 7, 6, 5, 4, 3, 2)))
    r = 11 - s % 11
    r = 0 if r == 11 else r
    return r != 10 and r == int(org[8])


def _ascii_variants(words: list[str]) -> list[list[str]]:
    outs = []
    for m in ({"æ": "ae", "ø": "o", "å": "a"}, {"æ": "ae", "ø": "oe", "å": "aa"}):
        v = [unicodedata.normalize("NFKD", "".join(m.get(c, c) for c in w)).encode("ascii", "ignore").decode()
             for w in words]
        v = [w for w in v if w]
        if v and v not in outs:
            outs.append(v)
    return outs


REGIONS = {"viken", "vestfold", "telemark", "agder", "rogaland", "vestland", "hordaland", "sogn", "fjordane", "more",
           "romsdal", "trondelag", "nordland", "troms", "finnmark", "innlandet", "hedmark", "oppland", "akershus",
           "ostfold", "buskerud", "oslo", "nord", "sor", "vest", "ost", "midt", "nordre", "sondre", "ostre", "vestre",
           "sunnmore", "nordmore", "hallingdal", "valdres", "gudbrandsdal", "gudbrandsdalen", "lofoten", "vesteralen",
           "helgeland", "jaeren", "ryfylke", "haugalandet", "sunnhordland", "salten", "namdal", "innherred", "fosen",
           "romerike", "hadeland", "toten", "solor", "odal", "setesdal", "lister", "dalane", "stavanger", "bergen",
           "trondheim", "tromso", "kristiansand", "drammen", "fredrikstad", "sandnes", "sarpsborg", "skien", "alesund",
           "bodo", "moss", "haugesund", "sandefjord", "arendal", "tonsberg", "porsgrunn", "larvik", "halden",
           "lillehammer", "molde", "harstad", "gjovik", "kongsberg", "mandal", "asker", "baerum", "hamar", "kongsvinger"}
FORM_NAME_WORDS = {"stiftelsen", "stiftelse", "sameiet", "sameie", "borettslag", "borettslaget", "andelslag",
                   "foreningen", "boligsameiet", "eierseksjonssameiet", "the"}


def _places(e: dict | None) -> set[str]:
    out = set(REGIONS)
    for key in ("forretningsadresse", "beliggenhetsadresse", "postadresse"):
        a = (e or {}).get(key) or {}
        for k in ("kommune", "poststed"):
            out |= {_translit(w) for w in re.findall(r"[a-zæøåäöü]+", (a.get(k) or "").lower()) if len(w) >= 3}
    return out


def _distinctive(ws: list[str], places: set[str]) -> bool:
    return any(len(w) >= 4 and w not in GENERIC_WORDS and _translit(w) not in places and not w.isdigit() for w in ws)


def _without_alias(name: str) -> list[str] | None:
    """The name words without a trailing bracketed short form of at most two words ('... INITIATIVE (IDI)'),
    when at least two words remain; None when the name has no such alias."""
    m = re.match(r"^(.*\S)\s*\(([^()]{1,24})\)\s*(?:" + "|".join(re.escape(x) for x in sorted(LEGAL_SUFFIX)) +
                 r")?\s*$", name.strip(), re.I)
    if not m or len(name_words(m.group(2))) > 2:
        return None
    rest = name_words(m.group(1))
    return rest if len(rest) >= 2 else None


def name_variants(name: str, e: dict | None = None) -> list[list[str]]:
    """The legal name and safe shortenings: leading initials joined ('P.E.' as 'pe') or dropped, form words
    ('Sameiet', 'Stiftelsen') dropped, and place or region words (registry municipality, post town, counties,
    'Viken', 'Nord') dropped. Every variant keeps at least one distinctive word."""
    base = name_words(name)
    places = _places(e)
    out: list[list[str]] = [base] if base else []

    def add(ws: list[str]) -> None:
        if ws and ws not in out and _distinctive(ws, places if len(ws) > 1 else set()):
            out.append(ws)

    lead = 0
    while lead < len(base) and len(base[lead]) == 1 and base[lead].isalpha():
        lead += 1
    stems = [base]
    alias = _without_alias(name)
    if alias and alias != base:
        # a trailing bracketed short form ('INTOSAI DEVELOPMENT INITIATIVE (IDI)') is an alias, not part of the
        # name a site writes out; the variant still needs a distinctive word and a variant registry check
        add(alias)
        stems.append(alias)
    if 0 < lead < len(base):
        joined = ["".join(base[:lead])] + base[lead:]
        dropped = base[lead:]
        add(joined)
        add(dropped)
        stems += [joined, dropped]
    for st in list(stems):
        no_form = [w for w in st if w not in FORM_NAME_WORDS]
        add(no_form)
        for ws in (st, no_form):
            no_place = [w for w in ws if _translit(w) not in places]
            if no_place and _distinctive(no_place, set()):
                add(no_place)
    return out


SHORT_FORMS: set[str] = set()  # labels made from part of the name (first word alone, initials plus surname)


def name_domains(name: str, e: dict | None = None) -> list[str]:
    """Domains guessed from the legal name, best first: full name, then the safe variants (initials, form and
    place words dropped, hyphenated words kept whole), .no before .com. A kindergarten also gets
    <name>.barnehage.no. Single common words are skipped."""
    places = _places(e)
    variants = [[w for w in ws if re.fullmatch(r"[0-9a-zæøåäöüé]+", w)] for ws in name_variants(name, e)]
    variants = [v for v in variants if v]
    if not variants:
        return []
    words = variants[0]
    seqs: list[list[str]] = []
    for ws in variants:
        for cand in (ws, [w for w in ws if w not in GENERIC_WORDS and not w.isdigit()]):
            if cand and cand not in seqs:
                seqs.append(cand)
    # hyphenated words kept whole, with place words dropped ("SJØ-SPORT MANDAL" as sjo-sport)
    raw = re.findall(r"[0-9a-zæøåäöüé]+(?:-[0-9a-zæøåäöüé]+)+", name.lower())
    labels: list[str] = []
    for ws in seqs:
        for v in _ascii_variants(ws):
            for lab in ("".join(v), "-".join(v)):
                if lab not in labels:
                    labels.append(lab)
    for r in raw:
        for v in _ascii_variants([r]):
            if v[0] not in labels:
                labels.append(v[0])
    extra: list[str] = []
    plain = [w for w in words if not w.isdigit()]
    tech = ["tech" if w in ("technologies", "technology", "teknologi", "technologi") else w for w in plain]
    if tech != plain:
        for v in _ascii_variants(tech):
            extra.append("".join(v))  # VANE OCEAN TECHNOLOGIES as vaneoceantech
    distinct = [w for w in plain if w not in GENERIC_WORDS and _translit(w) not in places]
    first_alone = None
    if len(plain) > 1 and distinct and distinct[0] not in COMMON_LABELS and (
            len(distinct[0]) >= 5 or (len(distinct[0]) >= 4 and re.search(r"\d", distinct[0]))):
        first_alone = _ascii_variants([distinct[0]])[0][0]  # SUNWIND GYLLING as sunwind; 2TAL ... as 2tal
        extra.append(first_alone)
        SHORT_FORMS.add(first_alone)
    alpha = [w for w in plain if w.isalpha()]
    tail = alpha[-3:] if len(alpha) >= 4 else []  # a person's name after at least one other word
    if tail and all(len(w) >= 3 and w not in GENERIC_WORDS and _translit(w) not in places for w in tail):
        for k in (3, 2):  # a person's name at the end: initials plus surname (Olav Martin Sørli as omsorli)
            v = _ascii_variants(alpha[-k:])[0]
            extra.append("".join(x[0] for x in v[:-1]) + v[-1])
            SHORT_FORMS.add(extra[-1])
    for lab in extra:
        if lab not in labels:
            labels.append(lab)
    good = []
    for lab in labels:
        if not (3 <= len(lab) <= 40) or lab.isdigit() or lab in COMMON_LABELS or len(lab) < 4:
            continue
        if lab.replace("-", "") in places and "-" not in lab:
            continue
        good.append(lab)
    short = [lab for lab in good if "-" not in lab and len(lab) < 5 and len(words) == 1]
    good = [lab for lab in good if lab not in short]
    # a four-letter single name (ADMA AS) is tried on .no only; a few names use the .as domain
    as_labels = good[:2] + ([first_alone] if first_alone and first_alone in good[2:] else [])
    # the whole name on .no and .com before any shortened form, so a cap is not spent on weaker guesses first
    whole = {x for v in _ascii_variants(words) for x in ("".join(v), "-".join(v))}
    head = [lab for lab in good if lab in whole]
    rest = [lab for lab in good if lab not in whole]
    out = ([f"{lab}.no" for lab in short + head] + [f"{lab}.com" for lab in head] + [f"{lab}.no" for lab in rest]
           + [f"{lab}.com" for lab in rest] + [f"{lab}.as" for lab in as_labels])
    if (((e or {}).get("naeringskode1") or {}).get("kode") or "").startswith(("88.91", "85.1")):
        kids = [lab for lab in good if "barnehage" not in lab and not any(f in lab for f in FORM_NAME_WORDS)]
        out = [f"{lab}.barnehage.no" for lab in kids[:2]] + out
    return out


def _word_rx(w: str) -> str:
    if w == "og":
        return r"(?:og|&|and|\+)"
    out = []
    for c in w:
        out.append({"æ": "(?:æ|ae|a)", "ø": "(?:ø|oe|o)", "å": "(?:å|aa|a)", "ä": "(?:ä|ae|a)",
                    "ö": "(?:ö|oe|o)", "a": "(?:a|å)", "o": "(?:o|ø)"}.get(c, re.escape(c)))
    return "".join(out)


def name_regex(words: list[str]) -> re.Pattern | None:
    if not words:
        return None
    body = r"[\s\-.]*".join(_word_rx(w) for w in words)
    return re.compile(r"(?<![0-9a-zæøåA-ZÆØÅ])" + body + r"(?![0-9a-zæøåA-ZÆØÅ])", re.I)


ENTITY = re.compile(r"((?:[0-9A-Za-zÆØÅæøåÄÖäöé&\-.]+[  ]+){1,6}?)(AS|ASA|A/S|ANS|DA)\b")


def name_check(text: str, words: list[str], conflict_words: list[str] | None = None) -> tuple[str | None, str | None]:
    """Return (span, conflict). span is the text showing the legal name; conflict is a different entity name
    on the page that shares our name's words (e.g. 'Kari Bygg AS' when we are 'Kari AS'), which blocks the
    strong methods."""
    rx = name_regex(words)
    if not rx:
        return None, None
    m = next((x for x in rx.finditer(text) if not _in_hostname(text, x.start(), x.end(), len(words) == 1)), None)
    if not m:
        return None, None
    span = _window(text, m.start(), m.end(), 40)
    norm_ours = [_translit(w) for w in (conflict_words or words)]
    legal_found = False
    conflict = None
    for em in ENTITY.finditer(text):
        last = re.split(r"[.!?:;|]\s", em.group(1))[-1]  # never across a sentence boundary
        ws = [_translit(w) for w in re.findall(r"[0-9a-zæøåäöé]+", last.lower().replace("&", " og "))]
        if norm_ours[0] not in ws:
            continue
        tail = ws[len(ws) - 1 - ws[::-1].index(norm_ours[0]):]  # from the last occurrence of our first word
        if tail == norm_ours:
            legal_found = True
        elif tail[:len(norm_ours)] == norm_ours or norm_ours[:len(tail)] == tail:
            conflict = conflict or em.group(0).strip()
    if legal_found:
        return span, None
    return span, conflict


HOSTNAME_TAIL = re.compile(r"\.(?:no|com|net|org|se|dk|io|eu|biz|info|nu)\b|\.[a-z0-9\-]+\.(?:no|com)\b", re.I)


LEGAL_AFTER = re.compile(r"[ \u00a0]+(?:as|asa|a/s|ans|da)(?![0-9a-zæøå/])", re.I)


def _in_hostname(text: str, a: int, b: int, one_word: bool = False) -> bool:
    """True when a name match is not the name written as words in visible text: part of a domain name, email
    address, URL, social handle or hashtag ('totentre.no', 'alrahmakulturhus@gmail.com', '@fishlab'), or a
    run-together lower-case token of several words ('alrahmakulturhus' as a bare word)."""
    if HOSTNAME_TAIL.match(text, b):
        return True
    before = text[max(0, a - 8):a].lower()
    if before.endswith(("www.", "@", "/", "://", ".", "#", "_")) or (a > 0 and text[a - 1] == "@"):
        return True
    # the whitespace-delimited token that holds the match
    s, e = a, b
    while s > 0 and not text[s - 1].isspace():
        s -= 1
    while e < len(text) and not text[e].isspace():
        e += 1
    token = text[s:e]
    if "@" in token or "/" in token or "_" in token or re.search(r"\.[A-Za-z]{2,}", token.rstrip(".,;:!?)»\"'")):
        return True
    m = text[a:b]
    if one_word and LEGAL_AFTER.match(text, b):
        return False  # a one-word name in lower case followed by its legal form ('audioverk as') is the name
    return len(m) >= 10 and m == m.lower() and not re.search(r"[\s\-.&]", m)


def _visible(rx: re.Pattern | None, text: str) -> bool:
    """The pattern matches somewhere as visible words (not inside a domain, email, URL or handle)."""
    return bool(rx) and any(not _in_hostname(text, m.start(), m.end()) for m in rx.finditer(text))


def _match_core(span: str, words: list[str]) -> str | None:
    """The name as the span writes it (the text a name proof's quote must contain)."""
    rx = name_regex(words)
    if not rx:
        return None
    ms = list(rx.finditer(span))
    m = next((x for x in ms if not _in_hostname(span, x.start(), x.end())), ms[0] if ms else None)
    return m.group(0).strip() if m else None


def _phone_core(span: str, wanted: set[str]) -> str | None:
    """The registry phone number as the span writes it (or the tel: link itself)."""
    if span.lower().startswith("tel:"):
        return span
    for m in PHONE_TEXT.finditer(span):
        d = re.sub(r"\D", "", m.group(0))
        if len(d) >= 8 and d[-8:] in wanted:
            return m.group(0).strip()
    return None


def _window(text: str, a: int, b: int, pad: int) -> str:
    s = max(0, a - pad)
    e = min(len(text), b + pad)
    # extend to word boundaries
    while s > 0 and not text[s - 1].isspace() and a - s < pad + 15:
        s -= 1
    while e < len(text) and not text[e].isspace() and e - b < pad + 15:
        e += 1
    return text[s:e].strip()


def _clean(s: str | None, limit: int = 400) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit] or None


# ---------------------------------------------------------------- page model
@dataclass
class Page:
    snap: Snapshot
    soup: BeautifulSoup
    text: str
    jsonld: list[dict]

    @property
    def url(self) -> str:
        return self.snap.final_url or self.snap.url

    @property
    def host(self) -> str:
        return host_of(self.url)


def parse_page(snap: Snapshot) -> Page:
    soup = BeautifulSoup(snap.content, "lxml")
    jsonld: list[dict] = []
    for s in soup.find_all("script", type=re.compile("ld\\+json", re.I)):
        try:
            data = json.loads(s.string or s.get_text() or "")
        except Exception:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                jsonld.append(d)
                if "@graph" in d:
                    stack.append(d["@graph"])
    for t in soup(["script", "style", "noscript", "template", "svg"]):
        t.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True).replace(" ", " "))
    return Page(snap, soup, text, jsonld)


def find_orgnr(page: Page, org: str) -> str | None:
    """Span showing our organisation number (label plus number), from visible text or structured data.
    A number inside a URL (e.g. a link to a register page) does not count."""
    g = f"{org[:3]}[\\s.\u00a0]?{org[3:6]}[\\s.\u00a0]?{org[6:]}"
    rx = re.compile(r"(?<![\d.,/=])(?:NO\s?)?" + g + r"(?:\s?MVA)?(?![\d,/])", re.I)
    for m in rx.finditer(page.text):
        before = page.text[max(0, m.start() - 40):m.start()]
        if re.search(r"(https?://|www\.)\S*$", before):
            continue  # part of a URL written out as text
        lab = list(re.finditer(r"(?i)(org(?:anisasjons)?\.?\s*-?\s*n(?:r|o|ummer)\.?|organi[sz]ation\s*(?:no|number)\.?|"
                               r"foretaksregisteret|mva|vat)[\s:.]*$", before))
        start = m.start() - (len(before) - lab[-1].start()) if lab else m.start()
        return page.text[start:m.end()].strip()
    for d in page.jsonld:
        for k in ("taxID", "vatID", "identifier", "leiCode", "organizationNumber", "duns"):
            v = d.get(k)
            vals = v if isinstance(v, list) else [v]
            for x in vals:
                if isinstance(x, dict):
                    x = x.get("value") or x.get("@value")
                if isinstance(x, (str, int)) and re.sub(r"\D", "", str(x)) == org:
                    return str(x)
    return None


FOREIGN_ORG = (
    (re.compile(r"(?i)\b(?:org(?:anisations)?\.?\s*(?:nr|nummer|no)?\.?|corporate id(?:entity)?(?: no\.?)?)\s*[:.]?\s*"
                r"(\d{6}-\d{4})(?!\d)"), "SE"),
    (re.compile(r"(?i)\bCVR(?:-?nr\.?|-?nummer)?\s*[:.]?\s*(\d{2}\s?\d{2}\s?\d{2}\s?\d{2})(?!\d)"), "DK"),
    (re.compile(r"(?i)\b(?:Y-tunnus|FO-nummer|business id)\s*[:.]?\s*(\d{7}-\d)(?!\d)"), "FI"),
    (re.compile(r"(?i)\b(?:VAT|moms(?:reg)?(?:\.?\s?nr)?|USt-?IdNr\.?|BTW)\s*(?:no\.?|nr\.?|number|id)?\s*[:.]?\s*"
                r"((?:SE|DK|FI|DE|GB|NL|BE|FR|EE|LT|LV|PL|AT|IE|ES|IT)\s?[0-9A-Z]{8,12})\b"), "VAT"),
    (re.compile(r"\b(SE\d{10}01)\b"), "VAT"),
)


def other_orgnrs(page: Page, org: str) -> dict[str, str]:
    """Valid organisation numbers other than ours that the page labels as an org number (or VAT)."""
    out: dict[str, str] = {}
    for m in NINE.finditer(page.text):
        num = "".join(m.groups())
        if num == org or num in out or not mod11_ok(num):
            continue
        before = page.text[max(0, m.start() - 30):m.end() + 4]
        if ORG_CONTEXT.search(before) or re.search(r"MVA", m.group(0)):
            out[num] = _window(page.text, m.start(), m.end(), 30)
    for rx, cc in FOREIGN_ORG:
        for m in rx.finditer(page.text):
            key = f"{cc}:{re.sub(r'[^0-9A-Z]', '', m.group(1).upper())}"
            out.setdefault(key, page.text[m.start():m.end()].strip())
    return out


def _digits_phones(e: dict) -> set[str]:
    out = set()
    for k in ("telefon", "mobil"):
        d = re.sub(r"\D", "", e.get(k) or "")
        if len(d) >= 8:
            out.add(d[-8:])
    return out


CONTACT_MARK = re.compile(r"(?i)contact|kontakt|footer|header|address|adresse|vcard|company-info|firmainfo|info-box")


def _names_in(text: str, names: list[list[str]]) -> bool:
    t = _translit(text)
    for ws in names:
        rx = name_regex(ws)
        if _visible(rx, text):
            return True
        toks = [_translit(w) for w in ws if len(w) >= 3 and w not in GENERIC_WORDS and w not in FORM_NAME_WORDS]
        if toks and all(re.search(r"(?<![a-z0-9])" + re.escape(x) + r"(?![a-z0-9])", t) for x in toks):
            return True
    return False


def _headed_contact_card(node) -> bool:
    """A small block whose own heading says contact ('<h4>Kontakt oss</h4> Fet Legesenter AS ... Telefon')."""
    h = node.find(["h2", "h3", "h4", "h5", "h6", "strong"], recursive=False) if hasattr(node, "find") else None
    return bool(h) and bool(re.fullmatch(r"(?i)\s*(kontakt(\s+oss)?|kontaktinformasjon|contact(\s+us)?)\s*:?\s*",
                                         h.get_text(" ", strip=True))) and len(node.get_text(" ", strip=True)) <= 600


BLOCK_DEPTH = 16  # ancestors climbed from a phone to its contact block; site builders nest a footer 10 deep


def _phone_in_named_block(el, page: Page, names: list[list[str]]) -> bool:
    """The phone sits in a contact block, header or footer whose text also names us."""
    on_contact_page = bool(INTERNAL_PRIORITY[0].search(urlsplit(page.url).path))
    node = el.parent if el is not None and getattr(el, "name", None) is None else el
    for _ in range(BLOCK_DEPTH):
        if node is None or getattr(node, "name", None) in (None, "[document]", "html"):
            break
        marks = " ".join(node.get("class") or []) + " " + (node.get("id") or "")
        is_block = node.name in ("footer", "header", "address") or bool(CONTACT_MARK.search(marks)) or \
            _headed_contact_card(node)
        if node.name in ("body", "main"):
            is_block = on_contact_page
        if is_block:
            txt = node.get_text(" ", strip=True)
            if len(txt) <= 3000 and _names_in(txt, names):
                return True
        if node.name in ("body", "main"):
            break
        node = node.parent
    return False


def find_phone(page: Page, wanted: set[str], names: list[list[str]] | None = None) -> str | None:
    """Span showing a registry phone number. With names, only a phone inside a contact block, header or footer
    that also names us counts (a number in an arbitrary content block is not proof)."""
    if not wanted:
        return None
    seen: dict[str, int] = {}
    for m in PHONE_TEXT.finditer(page.text):
        d = re.sub(r"\D", "", m.group(0))
        if not phone_shaped(m.group(0)):
            continue
        if len(d) >= 8 and d[-8:] in wanted:
            needle = m.group(0).strip()
            k = seen[needle] = seen.get(needle, -1) + 1
            # the k-th time the number is written is judged in its own element (a header copy without our
            # name must not hide the footer copy that names us)
            if names is None or _phone_in_named_block(_nth_element_for(page, needle, k), page, names):
                return _window(page.text, m.start(), m.end(), 25)
    for a in page.soup.find_all("a", href=re.compile(r"^tel:", re.I)):
        d = re.sub(r"\D", "", unquote(a["href"]))
        if len(d) >= 8 and d[-8:] in wanted:
            if names is None or _phone_in_named_block(a, page, names):
                return a["href"]
    return None


STREET_SUFFIX = (("vegen", "veien", "veg", "vei", "vn"), ("gata", "gaten", "gate", "gt"))


def _street_word_rx(w: str) -> str:
    """A street name word; its road or street suffix may be written in either written standard or short form
    ('Vangsvegen' as 'Vangsveien', 'Storgata' as 'Storgaten' or 'Storgt.')."""
    for forms in STREET_SUFFIX:
        for suf in forms:
            if w.endswith(suf) and len(w) > len(suf) + 2:
                alts = "|".join(re.escape(x) + (r"\.?" if x in ("vn", "gt") else "") for x in forms)
                return _word_rx(w[:-len(suf)]) + "(?:" + alts + ")"
    return _word_rx(w)


def _street_regex(street: str) -> re.Pattern | None:
    street = street.strip()
    m = re.match(r"^(.*?)[\s,]*(\d+)\s*([A-Za-z])?$", street)
    if not m or len(m.group(1)) < 3:
        return None  # need a street name plus a house number
    words = re.findall(r"[0-9A-Za-zÆØÅæøåÄÖäöüé'\-]+", m.group(1))
    body = r"[\s.\-]*".join(_street_word_rx(w.lower()) for w in words)
    num = m.group(2) + (r"\s?" + re.escape(m.group(3)) if m.group(3) else r"(?![0-9])")
    return re.compile(r"(?<![0-9a-zæøå])" + body + r"[\s.,]*" + num, re.I)


def find_address(page: Page, e: dict) -> str | None:
    return find_address_core(page, e)[0]


def find_address_core(page: Page, e: dict) -> tuple[str | None, str | None]:
    """(span, core): the span shows the registered street address and postcode; the core is the street,
    number and postcode as the page writes them, which the quoted span must contain."""
    for key in ("forretningsadresse", "beliggenhetsadresse"):
        a = e.get(key) or {}
        lines = [x for x in a.get("adresse") or [] if x]
        pn = a.get("postnummer")
        if not lines or not pn:
            continue
        rx = _street_regex(lines[-1]) or (_street_regex(lines[0]) if len(lines) > 1 else None)
        if not rx:
            continue
        for m in rx.finditer(page.text):
            tail = page.text[m.end():m.end() + 80]
            pm = re.search(r"(?<!\d)" + re.escape(pn) + r"(?!\d)", tail)
            if pm:
                return (page.text[max(0, m.start() - 10):m.end() + pm.end() + 20].strip(),
                        page.text[m.start():m.end() + pm.end()].strip())
    return None, None


def is_parked(page: Page) -> bool:
    if any(page.host == h or page.host.endswith("." + h) for h in PARKED_HOSTS):
        return True
    head = (page.soup.title.get_text(" ", strip=True) if page.soup.title else "") + " " + page.text[:3000]
    if PARKED.search(head):
        return len(page.text) < 4000  # a real site may mention "under construction" somewhere
    return False


def _page_key(url: str) -> str:
    """One key per page: scheme, www and a trailing slash do not make a different page (kontakt-oss/ and
    kontakt-oss are fetched once)."""
    p = urlsplit(url)
    return bare((p.hostname or "").lower()) + (p.path.rstrip("/") or "/") + ("?" + p.query if p.query else "")


def internal_links(page: Page) -> list[str]:
    found: list[tuple[int, str]] = []
    seen = set()
    for a in page.soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        u = urljoin(page.url, href).split("#")[0]
        p = urlsplit(u)
        if p.scheme not in ("http", "https") or not same_site(host_of(u), page.host):
            continue
        if re.search(r"\.(pdf|jpe?g|png|gif|zip|docx?|xlsx?)$", p.path, re.I):
            continue
        label = a.get_text(" ", strip=True)[:60]
        probe = p.path + " " + label
        key = _page_key(u)
        if not INTERNAL_KEY.search(probe) or key == _page_key(page.url) or key in seen:
            continue
        seen.add(key)
        rank = next((i for i, rx in enumerate(INTERNAL_PRIORITY) if rx.search(probe)), 9)
        found.append((rank, u))
    found.sort(key=lambda x: x[0])
    return [u for _, u in found]


# ---------------------------------------------------------------- verdicts
@dataclass
class Verdict:
    domain: str
    source: str
    status: str = "unchecked"   # proven, related, name_only, no_match, parked, blocked, unreachable, not_html, ...
    method: str | None = None
    detail: str | None = None
    pages: list[Page] = field(default_factory=list)
    proof: list[tuple[Page, str, str | None]] = field(default_factory=list)  # (page, span, value the span must contain)
    others: dict[str, tuple[Page, str]] = field(default_factory=dict)
    final_url: str | None = None
    variant: list[str] | None = None  # set when the name matched only through a shortened name variant
    namesake: tuple[str, str] | None = None  # another registered entity with our name (registry-declared site)
    ignore_orgs: set = field(default_factory=set)  # our own group's org numbers, never "another company" here
    cross: bool = False         # redirected to another registered domain
    path_scoped: bool = False   # the site is one section of a host that serves others (chain or portal)
    transient: list[str] = field(default_factory=list)  # pages of this site that failed transiently (timeout, 429, 5xx)

    def summary(self) -> dict[str, Any]:
        return {"domain": self.domain, "source": self.source, "status": self.status, "method": self.method,
                "final_url": self.final_url, "other_orgnrs": sorted(self.others)[:5] or None,
                "cross_domain_redirect": self.cross or None, "section_of_host": self.path_scoped or None,
                "detail": self.detail}


def judge(v: Verdict, org: str, e: dict, registry_host: str | None) -> None:
    """Decide the identity status of a candidate from the pages fetched so far."""
    pages = v.pages
    v.others, v.method, v.proof, v.namesake = {}, None, [], None
    # (a) exact: our org number on any page
    for p in pages:
        span = find_orgnr(p, org)
        if span:
            others = {}
            for q in pages:
                for num, s in other_orgnrs(q, org).items():
                    others.setdefault(num, (q, s))
            if len(others) >= 3:
                v.status, v.detail = "ambiguous", "page lists several organisation numbers (directory or manager)"
                v.others = others
                return
            v.status, v.method = "proven", "orgnr_on_page"
            v.detail = f"organisation number printed on {p.url}"
            v.proof = [(p, span, org)]
            v.others = others
            return
    for p in pages:
        for num, s in other_orgnrs(p, org).items():
            if num not in v.ignore_orgs:
                v.others.setdefault(num, (p, s))
    # (d) related: another org number and never ours
    if v.others:
        # Only a domain the registry itself points to can be "related"; a guessed domain is just someone else's.
        v.status = "related" if v.source in ("registry", "email") else "other_company"
        v.detail = "site prints organisation number(s) " + ", ".join(sorted(v.others)) + " and not ours"
        return
    variants = name_variants(e.get("navn") or "", e)
    full = variants[0] if variants else []
    lead = 0
    while lead < len(full) - 1 and len(full[lead]) == 1:
        lead += 1
    conflict_words = full[lead:]
    name_span = name_page = conflict = name_core = None
    v.variant = None
    for words in variants:
        for p in pages:
            s, c = name_check(p.text, words, conflict_words)
            if s and not name_span:
                name_span, name_page = s, p
                v.variant = words if words != full and words != full[lead:] else None
            conflict = conflict or c
        if name_span:
            name_core = _match_core(name_span, words)
            break
    if not name_span:
        v.status = "no_match"
        return
    final = bare(pages[0].host)
    declared = bool(registry_host and v.source == "registry" and same_site(final, registry_host))
    if conflict and not (declared and not _related_name_operates(pages, full)):
        v.status = "name_only"
        v.detail = f"page names a different entity sharing our name: '{conflict[:80]}'"
        return
    # (b) registry website and name, same domain only (a cross-domain redirect is not enough)
    if declared:
        v.status, v.method = "proven", "registry_website_and_name"
        v.detail = f"registry lists {registry_host}; legal name shown on the site"
        if conflict:
            # The registry declares this site for us; a related name on it ('ALL10 Bemanning AS' on all10.no) is
            # only mentioned, never the operator. The shared-site and group checks still decide what is derived.
            v.detail += f"; the site also names '{conflict[:80]}'"
        v.proof = [(name_page, name_span, name_core)]
        return
    # (c) phone and name; address and name
    wanted = _digits_phones(e)
    for p in pages:
        s = find_phone(p, wanted, variants)
        if s:
            v.status, v.method = "proven", "phone_and_name"
            v.detail = "registry phone number and legal name shown on the site"
            v.proof = [(p, s, _phone_core(s, wanted)), (name_page, name_span, name_core)]
            return
    reg_email = (e.get("epostadresse") or "").strip().lower()
    if "@" in reg_email:
        for p in pages:
            i = p.text.lower().find(reg_email)
            hit = _window(p.text, i, i + len(reg_email), 25) if i >= 0 else None
            if not hit:
                a = p.soup.find("a", href=lambda h: bool(h) and unquote(h).lower().strip() == "mailto:" + reg_email)
                hit = a["href"] if a else None
            if hit:
                v.status, v.method = "proven", "email_and_name"
                v.detail = "registry email address and legal name shown on the site"
                v.proof = [(p, hit, hit if hit.lower().startswith("mailto:") else reg_email),
                           (name_page, name_span, name_core)]
                return
    for p in pages:
        s, core = find_address_core(p, e)
        if s:
            v.status, v.method = "proven", "address_and_name"
            v.detail = "registered street address, postcode and legal name shown on the site"
            v.proof = [(p, s, core), (name_page, name_span, name_core)]
            return
    # (e) the registry email's own domain (not free mail or an invoicing service), whose domain carries our
    # distinctive name, with the name shown on it: the company declared this mailbox domain itself
    if v.source == "email" and _declared_host(e, final) and _domain_has_name(final, e):
        v.status, v.method = "proven", "registry_email_domain_and_name"
        v.detail = f"the registry email address is on {final}, whose domain carries the name; legal name shown on the site"
        v.proof = [(name_page, name_span, name_core)]
        return
    v.status = "name_only"
    v.detail = "legal name shown, but no org number, registry phone or address"


# ---------------------------------------------------------------- network
class _Spend:
    """Per-company request accounting (robots.txt fetches included)."""

    def __init__(self, f: Fetcher, ctx: CompanyContext):
        self.f, self.ctx, self.used = f, ctx, 0
        self.cap = RETRY_CAP if ctx.facts.get("website_retry") else MAX_REQUESTS
        self.dns: dict[str, bool] = {}

    def can(self, n: int = 1) -> bool:
        return self.used + n <= self.cap and self.f.budget.remaining_requests() > RESERVE + n

    def can_registry(self) -> bool:
        """Registry lookups (Brreg) are not site requests: only the run budget limits them."""
        return self.f.budget.remaining_requests() > RESERVE + 1

    async def get(self, url: str, *, robots: bool = True, headers: dict | None = None) -> Snapshot:
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        before = self.f.budget.used
        new_robots = robots and origin not in self.f.robots
        if (p.hostname or "") == "data.brreg.no":
            if not self.can_registry():
                raise _OutOfRequests()
            try:
                return await self.f.get(url, robots=robots, headers=headers, reserve=RESERVE)
            finally:
                self.ctx.requests += max(0, min(self.f.budget.used - before, 1))
        if not self.can(2 if new_robots else 1):
            raise _OutOfRequests()
        try:
            return await self.f.get(url, robots=robots, headers=headers, reserve=RESERVE)
        finally:
            # count what this call cost; fall back to an estimate when the budget is shared and moving
            # The per-company cap counts every fetch the same way, cached or live, so a cached rerun follows the
            # same path as a live run (Builderr compares two runs).
            spent = self.f.budget.used - before
            est = (1 if new_robots else 0) + 1
            self.used += est
            self.ctx.requests += max(0, min(spent, est))


class _OutOfRequests(Exception):
    pass


async def _resolve(sp: "_Spend", f: Fetcher, host: str) -> bool | None:
    if host not in sp.dns:
        sp.dns[host] = await resolves(f, host)
    return sp.dns[host]


_DNS_SEM: asyncio.Semaphore | None = None
DNS_PARALLEL = 24


def proxy_mode() -> bool:
    """With an HTTP(S) proxy configured, direct DNS and TCP may be blocked: rely on HTTP through the proxy."""
    import os
    return any(os.environ.get(k) for k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                                            "https_proxy", "http_proxy", "all_proxy"))


async def resolves(f: Fetcher, host: str) -> bool | None:
    """True when the host has an A or AAAA record; False only on a genuine NXDOMAIN or an empty answer for both
    record types; None when the lookup itself failed (timeout after one retry, SERVFAIL, no reachable name
    server, network error), which is never read as absence."""
    global _DNS_SEM
    if proxy_mode():
        return True  # the HTTP request through the proxy decides
    if _DNS_SEM is None:
        _DNS_SEM = asyncio.Semaphore(DNS_PARALLEL)
    async with _DNS_SEM:
        failed = False
        for rtype in ("A", "AAAA"):
            answered = False
            for attempt in range(2):
                f.budget.dns_lookups += 1
                try:
                    ans = await dns.asyncresolver.resolve(host, rtype, lifetime=DNS_TIMEOUT * (attempt + 1))
                    if len(ans):
                        return True
                    answered = True
                    break
                except dns.resolver.NXDOMAIN:
                    return False
                except dns.resolver.NoAnswer:
                    answered = True
                    break
                except (dns.exception.Timeout, OSError):
                    continue
                except (dns.resolver.NoNameservers, dns.exception.DNSException):
                    break
            failed = failed or not answered
    return None if failed else False


async def reachable(host: str, port: int = 443) -> bool:
    """A TCP connect before any HTTP, so dead hosts do not burn HTTP retries and timeouts. A timeout is
    retried once with a longer wait, so a slow moment does not change the result between two runs; a refused
    connection is final. Skipped (True) when a proxy is configured."""
    if proxy_mode():
        return port == 443
    for wait in (CONNECT_PROBE, CONNECT_PROBE * 2):
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(host, port, happy_eyeballs_delay=0.25), wait)
            w.close()
            try:
                await w.wait_closed()
            except Exception:
                pass
            return True
        except (asyncio.TimeoutError, TimeoutError):
            continue
        except Exception:
            return False
    return False


STATUS_RANK = {"unreachable": 0, "no_dns": 0, "parked": 3, "blocked": 4, "http_429": 5, "http_503": 5}


async def _fetch_page(sp: _Spend, url: str) -> tuple[Page | None, str]:
    try:
        snap = await sp.get(url)
    except Blocked:
        return None, "blocked"
    except (BudgetExhausted, _OutOfRequests):
        raise
    except Exception:
        return None, "unreachable"
    if snap.status in (401, 403):
        return None, "blocked"  # the site refuses our crawler: not absence
    if snap.status == 429:
        return None, f"http_{snap.status}"
    if snap.status >= 400:
        return None, f"http_{snap.status}"
    ct = snap.content_type.lower()
    if ct and "html" not in ct and "xml" not in ct:
        return None, "not_html"
    if not snap.content.strip():
        return None, "empty"
    return parse_page(snap), "ok"


PAGE_TRANSIENT = {"unreachable", "http_429", "http_500", "http_502", "http_503", "http_504"}


async def _site_page(sp: _Spend, v: Verdict, url: str) -> tuple[Page | None, str]:
    """A further page of a candidate site. A transient failure (timeout, rate limit, server error) is recorded,
    because the verdict would otherwise depend on which pages happened to load."""
    page, st = await _fetch_page(sp, url)
    if st in PAGE_TRANSIENT:
        v.transient.append(f"{url} ({st})")
    return page, st


async def check_candidate(f: Fetcher, sp: _Spend, ctx: CompanyContext, v: Verdict, done_hosts: set[str],
                          registry_host: str | None, start_url: str | None = None) -> None:
    e = ctx.registry or {}
    home: Page | None = None
    status = "unreachable"
    hosts = [v.domain, "www." + v.domain] if not v.domain.startswith("www.") else [v.domain]
    ok = await asyncio.gather(*[_resolve(sp, f, h) for h in hosts])
    live = [h for h, r in zip(hosts, ok) if r]
    if not live:
        # absence only when every lookup said NXDOMAIN or no answer; a failed lookup is not absence
        v.status = "dns_error" if any(r is None for r in ok) else "no_dns"
        return
    # connect probes for every live host and port at once, so dead hosts cost one timeout, not four
    probe_keys = [(h, port) for h in live for port in (443, 80)]
    probe = dict(zip(probe_keys, await asyncio.gather(*[reachable(h, port) for h, port in probe_keys])))
    if v.source in ("registry", "email") and not any(probe.values()):
        probe[(live[0], 443)] = True  # a declared domain gets one real HTTPS attempt even if the probe timed out
    http_later: list[str] = []
    attempts = [("https", h) for h in live]
    home_parked: Page | None = None
    i = 0
    while True:
        if i == len(attempts):
            if not http_later or attempts[-1][0] == "http":
                break
            attempts += [("http", x) for x in http_later]
        scheme, h = attempts[i]
        i += 1
        if not probe.get((h, 443 if scheme == "https" else 80)):
            if scheme == "https" and h not in http_later:
                http_later.append(h)
            continue
        url = f"{scheme}://{h}/"
        if start_url and bare(host_of(start_url)) == bare(h):
            url = scheme + start_url[start_url.index("://"):]
        if not sp.can(2):
            v.status = "skipped_budget"
            return
        page, st = await _fetch_page(sp, url)
        if st == "blocked" and f.robots.get(f"{scheme}://{h}") is not None:
            status = "blocked"  # a real robots.txt disallow: respect it for the whole site
            break
        if page and not is_parked(page):
            home = page
            break
        if page:
            status = "parked"
            home_parked = page
        elif STATUS_RANK.get(st, 1) >= STATUS_RANK.get(status, 1):
            status = st  # keep the most telling outcome across bare, www and http attempts
        if scheme == "https" and st in ("blocked", "unreachable") and h not in http_later:
            http_later.append(h)  # robots.txt or TLS failed on https; plain HTTP may still work
    if not home:
        v.status = status
        if status == "parked" and home_parked:
            v.final_url = home_parked.url
        return
    v.final_url = home.url
    final = bare(home.host)
    if final in done_hosts and final != bare(v.domain):
        v.status = "duplicate"
        v.detail = f"redirects to {final}, already checked"
        return
    done_hosts.add(final)
    done_hosts.add(bare(v.domain))
    if is_platform(final):
        v.status = "platform"
        return
    v.pages = [home]
    v.cross = not same_site(final, bare(v.domain))
    e = ctx.registry or {}
    if v.cross and _declared_host(e, final) and urlsplit(home.url).path in ("", "/"):
        # a redirect to the front page of the registry's own website or email domain stays within the company
        # (a locale or section page of a bigger host, such as a group's /no page, is still judged as cross)
        v.cross = False
    v.path_scoped = _is_section(home.url)
    if v.path_scoped and not v.cross:
        lab = re.sub(r"[^a-z0-9]", "", bare(home.host).split(".")[0])
        if any(t in lab for t in _core_tokens(e.get("navn") or "") if len(t) >= 4 and t not in GENERIC_WORDS):
            # the company's own domain (sicra.no/no/kunnskap/innsikter): judge it from its front page
            root = canonical_url(home)
            if sp.can(1):
                front, _st = await _site_page(sp, v, root)
                if front and same_site(front.host, home.host) and not is_parked(front):
                    v.pages = [front, home]
                    home = front
                    v.path_scoped = False
    judge(v, ctx.org, e, registry_host)
    await _group_parent_site(sp, ctx, v, registry_host)
    if v.cross and v.status == "name_only" and _is_front_or_about(home.url) and sp.can(1):
        # the redirect target names us on its front page: its linked privacy page may print our org number
        priv = [urljoin(home.url, a["href"]).split("#")[0] for a in home.soup.find_all("a", href=True)
                if re.search(r"(?i)personvern|privacy|impressum|imprint", a["href"] + " " + a.get_text())]
        priv = [u for u in priv if same_site(host_of(u), home.host)]
        if priv:
            p, _st = await _site_page(sp, v, priv[0])
            if p and same_site(p.host, home.host):
                v.pages.append(p)
                judge(v, ctx.org, e, registry_host)
    await _check_variant(sp, ctx, v)
    await _check_collision(sp, ctx, v)
    label = re.sub(r"[^a-z0-9]", "", bare(home.host).split(".")[0])
    name_in_domain = any(t in label for t in _core_tokens(e.get("navn") or "")
                         if len(t) >= 4 and t not in GENERIC_WORDS and t not in REGIONS)
    strong_target = v.method == "orgnr_on_page" or (
        # without our org number, the target must show both registry phone and registry address, and its own
        # domain must carry our name (a portal such as idrettsskolene.no for several schools does not)
        v.method in ("phone_and_name", "address_and_name") and name_in_domain
        and bool(find_phone(home, _digits_phones(e))) and bool(find_address(home, e)))
    if v.cross and v.status == "proven" and strong_target and _is_front_or_about(home.url):
        # The domain redirects to a brand site whose front or about page prints our org number: treat the
        # target domain as ours, read its front page, and let the exclusivity test judge the whole site.
        v.cross, v.path_scoped = False, False
        v.detail = f"{v.detail} (redirected from {v.domain})"
        root = canonical_url(home)
        if home.url.rstrip("/") != root.rstrip("/") and sp.can(1):
            front, _st = await _site_page(sp, v, root)
            if front and same_site(front.host, home.host) and not is_parked(front):
                v.pages = [front, home]
        return
    if v.cross or v.path_scoped:
        return  # another domain's page, or one section of a shared host: judge that page alone
    if v.status == "proven" and v.method == "orgnr_on_page":
        return
    for link in internal_links(home)[:MAX_INTERNAL]:
        if not sp.can(1):
            break
        p, st = await _site_page(sp, v, link)
        if p and same_site(p.host, home.host):
            v.pages.append(p)
            judge(v, ctx.org, e, registry_host)
            if v.status == "proven" and v.method == "orgnr_on_page":
                return
    judge(v, ctx.org, e, registry_host)
    await _group_parent_site(sp, ctx, v, registry_host)  # a subsidiary's number may first appear on a subpage
    if v.status == "name_only" and sp.can(1) and not any(re.search(r"(?i)personvern|privacy", p.url) for p in v.pages):
        # a privacy, terms or imprint page often prints the operator's org number (ludenso.com/privacy-policy)
        fetched = {_page_key(p.url) for p in v.pages} | {_page_key(p.snap.url) for p in v.pages}
        links = [urljoin(home.url, a["href"]).split("#")[0] for a in home.soup.find_all("a", href=True)
                 if re.search(r"(?i)personvern|privacy|cookie|vilk|terms|impressum|imprint", a["href"] + " " + a.get_text())]
        links = [u for u in links if same_site(host_of(u), home.host) and _page_key(u) not in fetched]
        origin = f"{urlsplit(home.url).scheme}://{urlsplit(home.url).netloc}"
        for u in links[:1]:
            if not sp.can(1):
                break
            p, _st = await _site_page(sp, v, u)
            if p and same_site(p.host, home.host):
                v.pages.append(p)
                judge(v, ctx.org, e, registry_host)
                if v.status == "proven":
                    break
    await _check_variant(sp, ctx, v)
    await _check_collision(sp, ctx, v)
    await _check_legal_name_unique(sp, ctx, v)
    await _check_workplace(sp, ctx, v)


def _related_name_operates(pages: list[Page], words: list[str]) -> bool:
    """Another legal name sharing our distinctive words stands in an operator position on the site (page title,
    owner or controller sentence, copyright or imprint block)."""
    ours = {_translit(w) for w in words}
    distinct = {w for w in ours if len(w) >= 3 and w not in GENERIC_WORDS and not w.isdigit()} or ours
    return any(op and set(n.split()) & distinct for n, _sfx, _pos, op, _strong in other_company_names(pages, words))


def _domain_has_name(host: str, e: dict) -> bool:
    """The registered domain's label carries a distinctive word of our name (not a generic or place word)."""
    label = re.sub(r"[^a-z0-9]", "", bare(host).split(".")[0])
    return any(t in label for t in _core_tokens(e.get("navn") or "")
               if len(t) >= 4 and t not in GENERIC_WORDS and t not in REGIONS)


def _declared_host(e: dict, host: str) -> bool:
    """The host is the registry website's or the registry email address's own domain (not a free-mail or
    invoicing provider)."""
    hj = host_of((e.get("hjemmeside") or "").strip()) if (e.get("hjemmeside") or "").strip() else ""
    mail = (e.get("epostadresse") or "").strip().lower()
    dom = mail.rsplit("@", 1)[1] if "@" in mail else ""
    if dom in FREE_MAIL or any(dom.endswith("." + x) for x in FREE_MAIL) or service_mail_domain(dom):
        dom = ""
    return any(d and same_site(bare(host), bare(d)) for d in (hj, dom))


async def _group_parent_site(sp: "_Spend", ctx: CompanyContext, v: Verdict, registry_host: str | None) -> None:
    """The registry's own site (or email domain) printing only our subsidiaries' org numbers is the group
    parent's site: those numbers are set aside and the candidate is judged again."""
    if v.status == "related" and v.source in ("registry", "email") and not v.cross:
        group = await _group_members(sp, ctx)
        if group and all(k in group for k in v.others):
            v.ignore_orgs = set(group)
            judge(v, ctx.org, ctx.registry or {}, registry_host)


COMMON_NOUNS = {"forsikring", "bank", "sparebank", "kraft", "energi", "entreprenor", "tak", "maling", "frisor", "klinikk",
                "tannlege", "lege", "butikk", "kafe", "restaurant", "hotell", "skole", "barnehage", "verksted", "rorlegger",
                "elektriker", "snekker", "regnskap", "revisjon", "advokat", "eiendom", "bygg", "transport", "service",
                "senter", "sport", "trafikkskole", "taxi", "bilverksted", "utleie", "salg", "marina", "camping"}


INVOICE_MAIL = re.compile(r"(?i)poweroffice|tripletex|fiken|visma|ehf|24sevenoffice|uni-?economy|xledger|"
                          r"conta|cloudflare|wpengine|invoice|faktura\.")
# Mail domains of e-invoice inboxes, accounting, payroll, payment and booking platforms: an address there
# (faktura@..., an EHF scan inbox on ebilag.toko.no) is a service the company uses, not another organisation.
SERVICE_MAIL_DOMAINS = {
    "toko.no", "tripletex.no", "fiken.no", "visma.com", "visma.net", "visma.no", "vismaonline.com",
    "eaccounting.no", "uni-economy.no", "unimicro.no", "unieconomy.no", "poweroffice.no", "poweroffice.net",
    "24sevenoffice.com", "24sevenoffice.no", "conta.no", "xledger.net", "xledger.no", "sendregning.no",
    "e-conomic.no", "e-conomic.com", "dnbregnskap.no", "duett.no", "mamut.no", "zirius.no", "logiq.no",
    "accountor.no", "azets.com", "azets.no", "hogia.no", "huldt-lillevik.no", "zenegy.com", "zenegy.no",
    "kontali.no", "regnskapnorge.no", "aksesspunkt.no", "peppol.eu", "peppol.no", "ehf.no", "edi.no",
    "pagero.com", "pagero.no", "basware.com", "tungsten-network.com", "inexchange.se", "inexchange.no",
    "vipps.no", "vippsmobilepay.com", "stripe.com", "nets.eu", "klarna.com", "paypal.com", "svea.com",
    "timma.no", "bookmyday.no", "simplybook.me", "calendly.com", "bokadirekt.se", "hano.no", "planway.com",
    "easytimer.no", "mailgun.org", "sendgrid.net", "mailchimp.com", "mcsv.net",
}
SERVICE_MAIL_LABEL = re.compile(r"(?i)^(ebilag|bilag|e-?faktura|faktura|invoice|invoices|inbox|scan|ehf|peppol)$")


def service_mail_domain(dom: str) -> bool:
    """The mail domain belongs to an invoicing, accounting, payroll, payment or booking service."""
    d = bare((dom or "").lower().strip("."))
    if not d:
        return False
    if INVOICE_MAIL.search(d) or any(d == x or d.endswith("." + x) for x in SERVICE_MAIL_DOMAINS):
        return True
    return d.count(".") >= 2 and bool(SERVICE_MAIL_LABEL.match(d.split(".")[0]))


NON_OPERATING = {"ESEK", "SAM", "BRL"}  # owners' sections, co-owned properties, housing co-ops
_NAME_SEARCH: dict[str, list[dict]] = {}


def _core_tokens(name: str) -> set[str]:
    return {_translit(w) for w in name_words(name) if w not in FORM_NAME_WORDS and _translit(w) not in FORM_WORDS
            and not w.isdigit() and len(w) >= 2}


def _addr_key(r: dict) -> tuple[str, str] | None:
    a = r.get("forretningsadresse") or r.get("beliggenhetsadresse") or {}
    lines = [x for x in a.get("adresse") or [] if x]
    if not lines or not a.get("postnummer"):
        return None
    return re.sub(r"[^a-z0-9]", "", _translit(lines[0])), a["postnummer"]


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _near(ours: set[str], theirs: set[str]) -> bool:
    """Near-namesakes: every distinctive word of one name has a close match in the other ('Advisor' and
    'Advisors', 'Fjellbekk Trevare' and 'Fjellbekk Trevare Eiendom')."""
    a = [w for w in ours if len(w) >= 3] or list(ours)
    b = [w for w in theirs if len(w) >= 3] or list(theirs)

    def close(x: str, y: str) -> bool:
        return x == y or (min(len(x), len(y)) >= 4 and (x.startswith(y) or y.startswith(x) or _lev(x, y) <= 2))
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return all(any(close(x, y) for y in big) for x in small)


def _active(r: dict) -> bool:
    return not (r.get("slettedato") or r.get("konkurs") or r.get("underAvvikling")
                or r.get("underTvangsavviklingEllerTvangsopplosning"))


def _full_name_rx(name: str) -> re.Pattern | None:
    ws = re.findall(r"[0-9a-zæøåäöüé]+", name.lower().replace("&", " og "))
    return name_regex(ws) if ws else None


BRREG_API = "https://data.brreg.no/enhetsregisteret/api"
SEARCH_TRIES = 3            # attempts per Brreg name search (each attempt is itself retried once by the fetcher)
SEARCH_BACKOFF = (1.0, 3.0)  # seconds to wait before the second and third attempts
PHRASE_PAGE = 100           # rows per page of a phrase search
PHRASE_PAGES = 5            # pages read at most; more matches than that count as "truncated"
_SEARCH: dict[str, tuple[list[dict], bool]] = {}


class _RegistrySearchFailed(Exception):
    """A Brreg name search, or a page a verdict depends on, could not be read: the website step is a transient
    failure that the pipeline's retry pass runs again."""


async def _search_json(sp: "_Spend", url: str) -> dict:
    last = "no answer"
    for attempt in range(SEARCH_TRIES):
        if attempt:
            await asyncio.sleep(SEARCH_BACKOFF[min(attempt, len(SEARCH_BACKOFF)) - 1])
        if not sp.can_registry():
            raise _OutOfRequests()
        try:
            snap = await sp.get(url, robots=False, headers={"Accept": "application/json"})
        except (BudgetExhausted, _OutOfRequests):
            raise
        except Exception as exc:  # noqa: BLE001 - network trouble: try again
            last = type(exc).__name__
            continue
        if snap.status == 200:
            try:
                return snap.json()
            except Exception:  # noqa: BLE001
                last = "unreadable JSON"
                continue
        last = f"HTTP {snap.status}"
    raise _RegistrySearchFailed(f"Brreg name search failed ({last}): {url}")


async def brreg_name_search(sp: "_Spend", path: str, query: str, *, phrase: bool = False,
                            extra: str = "", size: int = 50) -> tuple[list[dict], bool]:
    """Rows of a Brreg name search on enheter or underenheter, with retries; raises _RegistrySearchFailed.
    The default search is fuzzy and ranked (one page). phrase=True uses navnMetodeForSoek=FORTLOEPENDE (names
    containing the words in order, also hyphenated or inflected) and pages through every match, up to
    PHRASE_PAGES pages; the flag says whether matches were left unread. Results are cached for the run."""
    q = quote(query)
    if phrase:
        base = f"{BRREG_API}/{path}?navn={q}&navnMetodeForSoek=FORTLOEPENDE{extra}&size={PHRASE_PAGE}"
    else:
        base = f"{BRREG_API}/{path}?navn={q}{extra}&size={size}"
    if base in _SEARCH:
        return _SEARCH[base]
    rows: list[dict] = []
    truncated = False
    if not phrase:
        rows = ((await _search_json(sp, base)).get("_embedded") or {}).get(path) or []
    else:
        page = 0
        while True:
            data = await _search_json(sp, f"{base}&page={page}")
            rows += (data.get("_embedded") or {}).get(path) or []
            pages = int((data.get("page") or {}).get("totalPages") or 0)
            page += 1
            if page >= pages:
                break
            if page >= PHRASE_PAGES:
                truncated = True
                break
    _SEARCH[base] = (rows, truncated)
    return rows, truncated


def _search_queries(words: list[str]) -> list[str]:
    """The name as registered (with æ, ø, å) and, when different, as transliterated (a site may write
    'Flotre' for 'Fløtre')."""
    out = []
    for q in (" ".join(words), " ".join(_translit(w) for w in words)):
        if q.strip() and q not in out:
            out.append(q)
    return out


async def registry_rows(sp: "_Spend", words: list[str], *, postcode: str | None = None) -> tuple[list[dict], bool]:
    """Main units and sub-units whose names match ours: a ranked fuzzy search and a complete phrase search, for
    the original and the transliterated spelling. Deduplicated by org number, in a fixed order."""
    rows: list[dict] = []
    seen: set[str] = set()
    truncated = False
    for q in _search_queries(words):
        for path in ("enheter", "underenheter"):
            for phrase in (False, True):
                got, cut = await brreg_name_search(sp, path, q, phrase=phrase)
                truncated = truncated or cut
                for r in got:
                    k = f"{path}:{r.get('organisasjonsnummer')}"
                    if k not in seen:
                        seen.add(k)
                        rows.append(r)
    return rows, truncated


ADDRESS_PAGE = 500          # rows per page when listing every entity at a postcode
ADDRESS_PAGES = 10          # pages read at most per listing; a longer listing cannot clear an address proof


def _street_key(lines: list[str] | None, postcode: str | None) -> tuple[str, str] | None:
    """(normalised street and number, postcode) of an address, for comparing two registry entries."""
    if not postcode:
        return None
    for line in lines or []:
        if not line or line.lower().startswith("c/o") or not re.search(r"\d", line):
            continue
        t = _translit(line)
        t = re.sub(r"(gata|gaten|gt\.?)(?=\s|\d|$)", "gate", t)
        t = re.sub(r"(vegen|veien|vegen|veg|vn\.?)(?=\s|\d|$)", "vei", t)
        return re.sub(r"[^a-z0-9]", "", t), postcode
    return None


def _entity_keys(r: dict) -> set[tuple[str, str]]:
    out = set()
    for k in ("forretningsadresse", "postadresse", "beliggenhetsadresse"):
        a = r.get(k) or {}
        key = _street_key(a.get("adresse"), a.get("postnummer"))
        if key:
            out.add(key)
    return out


def _distinct_words(name: str) -> list[str]:
    """Distinctive words of a name in registered spelling: four letters or more, not generic or form words."""
    return [w for w in name_words(name) if len(_translit(w)) >= 4 and _translit(w) not in GENERIC_WORDS
            and w not in FORM_NAME_WORDS and _translit(w) not in FORM_WORDS and not w.isdigit()
            and _translit(w) not in COMMON_NOUNS]  # a trade word ('frisør') is shared by every salon


_COLOC_ROW: dict[str, dict] = {}  # the namesake found at our address, per company


def _declares_site(r: dict, host: str) -> bool:
    """The registered entity names this site as its website or uses it as its email domain."""
    hj = bare(host_of(r.get("hjemmeside") or "")) if (r.get("hjemmeside") or "").strip() else ""
    mail = (r.get("epostadresse") or "").lower()
    dom = mail.rsplit("@", 1)[1] if "@" in mail else ""
    return any(d and same_site(bare(host), bare(d)) for d in (hj, dom))


async def _colocated_namesake(sp: "_Spend", ctx: CompanyContext, entity: dict | None = None,
                              skip: frozenset = frozenset()) -> str | None:
    """Another registered entity (main unit or another company's sub-unit) at our registered street address that
    shares a distinctive word of our name ('Al-Rahma Islamic Center' beside 'Al-Rahma Kultur Hus Stiftelsen').
    entity: another record to check instead of ours (a workplace: its name and location address)."""
    e = entity or ctx.registry or {}
    ours = _entity_keys(e)
    if not ours:
        return None
    pcs = sorted({pc for _k, pc in ours})
    places = _places(e)
    for w in _distinct_words(e.get("navn") or ""):
        t = _translit(w)
        if t in places:
            continue  # a place word ('Oslo') is shared by half the town
        for pc in pcs:
            for path, field_ in (("enheter", "forretningsadresse"), ("enheter", "postadresse"),
                                 ("underenheter", "beliggenhetsadresse")):
                for q in _search_queries([w]):
                    rows, _c = await brreg_name_search(sp, path, q, extra=f"&{field_}.postnummer={pc}")
                    for r in rows:
                        if r.get("organisasjonsnummer") == ctx.org or r.get("overordnetEnhet") == ctx.org \
                                or r.get("organisasjonsnummer") in skip or _staff_body(r) \
                                or any(_translit(x) in GROUP_WORDS + ("eiendom", "eiendommer", "eigedom")
                                       for x in name_words(r.get("navn") or "")):
                            continue  # our own units, our staff's club, and an owner's holding or property company
                        if t in {_translit(x) for x in name_words(r.get("navn") or "")} and _entity_keys(r) & ours:
                            if entity is None:
                                _COLOC_ROW[ctx.org] = r
                            return f"{r.get('navn')} ({r.get('organisasjonsnummer')})"
    return None


async def _address_shared(sp: "_Spend", ctx: CompanyContext, entity: dict | None = None,
                          skip: frozenset = frozenset()) -> str | None:
    """Another registered entity with our registered street address and postcode (any name or legal form): our
    address then cannot identify our site. Returns that entity, or a reason when the postcode is too crowded to
    read in full (also not a pass). entity: another record to check instead of ours (a workplace)."""
    e = entity or ctx.registry or {}
    ours = _entity_keys(e)
    for pc in sorted({pc for _k, pc in ours}):
        for path, field_ in (("enheter", "forretningsadresse"), ("enheter", "postadresse"),
                             ("underenheter", "beliggenhetsadresse")):
            base = f"{BRREG_API}/{path}?{field_}.postnummer={pc}&size={ADDRESS_PAGE}"
            page = 0
            while True:
                url = f"{base}&page={page}"
                if url in _SEARCH:
                    rows, pages = _SEARCH[url][0], int(_SEARCH[url][1])
                else:
                    data = await _search_json(sp, url)
                    rows = (data.get("_embedded") or {}).get(path) or []
                    pages = int((data.get("page") or {}).get("totalPages") or 0)
                    _SEARCH[url] = (rows, pages)
                for r in rows:
                    if r.get("organisasjonsnummer") == ctx.org or r.get("overordnetEnhet") == ctx.org \
                            or r.get("organisasjonsnummer") in skip:
                        continue
                    if _entity_keys(r) & ours:
                        return f"{r.get('navn')} ({r.get('organisasjonsnummer')})"
                page += 1
                if page >= pages:
                    break
                if page >= ADDRESS_PAGES:
                    return f"more entities at postcode {pc} than can be checked"
    return None


def _heading_namesake(pages: list[Page], rows: list[dict], ctx: CompanyContext) -> str | None:
    """A registered entity other than us that shares a distinctive word of our name, named as words in a page
    title or main heading (h1, h2): the site presents itself as that organisation."""
    e = ctx.registry or {}
    ours = {_translit(w) for w in _distinct_words(e.get("navn") or "")}
    our_rx = name_regex(name_words(e.get("navn") or ""))
    heads = []
    for p in pages:
        if p.soup.title:
            heads.append(p.soup.title.get_text(" ", strip=True))
        heads += [h.get_text(" ", strip=True) for h in p.soup.find_all(["h1", "h2"])]
    text = " | ".join(x for x in heads if x)
    if our_rx:
        text = our_rx.sub(" ", text)  # our own name in a heading never names the other
    for r in rows:
        if r.get("organisasjonsnummer") == ctx.org or r.get("overordnetEnhet") == ctx.org:
            continue
        if not ours & {_translit(w) for w in name_words(r.get("navn") or "")}:
            continue
        for ws in name_variants(r.get("navn") or "", r):
            if [_translit(w) for w in ws] == [_translit(w) for w in name_words(e.get("navn") or "")]:
                continue
            if len(ws) >= 2 and _visible(name_regex(ws), text):
                return f"{r.get('navn')} ({r.get('organisasjonsnummer')})"
    return None


async def _identity_conflict(sp: "_Spend", ctx: CompanyContext, v: Verdict) -> str | None:
    """Checks every proof without our org number must pass: no namesake at our address, no other registered
    namesake as the site's heading, and an address proof only when nobody else is registered at that address."""
    e = ctx.registry or {}
    words = [w for w in name_words(e.get("navn") or "") if w not in FORM_NAME_WORDS]
    rows, _cut = await registry_rows(sp, words) if words else ([], False)
    head = _heading_namesake(v.pages, rows, ctx)
    if head:
        return f"the site's title or heading names another registered entity: {head}"
    near = await _colocated_namesake(sp, ctx)
    if near:
        return f"another registered entity at our address shares our name: {near}"
    if v.method == "address_and_name":
        shared = await _address_shared(sp, ctx)
        if shared and not _shared_address_still_ours(v, ctx):
            return (f"our registered address is shared ({shared}) and the site does not show our full legal name "
                    "on a domain carrying our name, so the address cannot identify it")
    return None


def _shared_address_still_ours(v: Verdict, ctx: CompanyContext) -> bool:
    """An address that other registered entities share still identifies the site when none of them shares a
    distinctive word of our name (checked before this, at the address), the site shows our legal name with its
    legal form as visible words ('Sicra AS', 'Klipra Barnehage SA'), and the site's domain carries our name."""
    e = ctx.registry or {}
    name = e.get("navn") or ""
    form = ((e.get("organisasjonsform") or {}).get("kode") or "").upper()
    if not v.pages or not _domain_has_name(v.pages[0].host, e):
        return False
    if form in LEGAL_UNIQUE_FORMS:
        return bool(_legal_span(v.pages, name, form))
    words = name_words(name)
    full = re.findall(r"[0-9a-zæøåäöüé]+", name.lower().replace("&", " og "))
    if len(full) <= len(words):
        return False  # no legal-form word in the registered name to show
    return any(_visible(_full_name_rx(name), p.text) for p in v.pages)


async def _check_collision(sp: "_Spend", ctx: CompanyContext, v: Verdict) -> None:
    """For proofs other than our org number: reject the site when another active registered entity (or another
    company's workplace) carries the same distinctive name, unless the site names our full legal name (with its
    legal form) and not the other's. Owners' sections, co-owned properties and housing co-ops accept only an
    org number on the site or the registry-declared website."""
    if v.status != "proven" or v.method == "orgnr_on_page":
        return
    if v.source == "guess_short":
        v.status, v.method = "name_only", None
        v.detail = "a domain made from part of the name needs our org number on the site (chain or parent brand risk)"
        return
    e = ctx.registry or {}
    form = ((e.get("organisasjonsform") or {}).get("kode") or "").upper()
    if form in NON_OPERATING and v.method != "registry_website_and_name":
        v.status, v.method = "name_only", None
        v.detail = f"{form} entities need the org number on the site or a registry-declared website"
        return
    _COLOC_ROW.pop(ctx.org, None)
    conflict = await _identity_conflict(sp, ctx, v)
    if conflict and v.method == "registry_website_and_name" and conflict.startswith("another registered entity at our address") \
            and v.pages and not _declares_site(_COLOC_ROW.get(ctx.org) or {}, v.pages[0].host):
        # The shared-address rule only neutralises proof by address. A website the registry declares for us, that
        # the namesake at our address neither declares nor mails from (it has its own site), stays ours; the
        # heading-namesake and co-declared-site checks still apply.
        v.detail = f"{v.detail}; {conflict}, which does not declare this site"
        conflict = None
    if conflict and not (v.method == "registry_website_and_name" and "shared" in conflict):
        if v.method == "registry_website_and_name":
            v.namesake = (conflict, "")
            v.detail = f"{v.detail}; {conflict}"
        else:
            v.status, v.method, v.detail = "name_only", None, conflict
            return
    ours = _core_tokens(e.get("navn") or "")
    if not ours:
        return
    words = [w for w in name_words(e.get("navn") or "") if w not in FORM_NAME_WORDS and _translit(w) not in FORM_WORDS
             and not w.isdigit() and len(w) >= 2]
    rows, _cut = await registry_rows(sp, words)
    # Near-namesakes at our own postcode ('Advisors AS' beside 'Advisor AS') rank too low in a plain name
    # search, so ask once more with the postcode filter (original and transliterated spelling).
    pn = ((e.get("forretningsadresse") or e.get("beliggenhetsadresse") or {}).get("postnummer") or "")
    longest = max(words, key=lambda w: (len(_translit(w)), w)) if words else ""
    if pn and len(_translit(longest)) >= 3:
        for q in _search_queries([longest]):
            extra, _c = await brreg_name_search(sp, "enheter", q, extra=f"&forretningsadresse.postnummer={pn}")
            rows = rows + [r for r in extra if r.get("organisasjonsnummer") not in {x.get("organisasjonsnummer") for x in rows}]
    our_phones = _digits_phones(e)
    our_addr = _addr_key(e)
    for r in rows:
        org = r.get("organisasjonsnummer")
        if org == ctx.org or r.get("overordnetEnhet") == ctx.org or not _active(r):
            continue
        theirs = _core_tokens(r.get("navn") or "")
        if not theirs or theirs == ours or not _near(ours, theirs) or _staff_body(r):
            continue
        shares = (our_addr and _addr_key(r) == our_addr) or bool(our_phones & _digits_phones(r))
        if not shares:
            continue
        what = (f"near-namesake {r.get('navn')} ({org}) shares this company's registered "
                f"{'address' if our_addr and _addr_key(r) == our_addr else 'phone'}")
        if v.method == "registry_website_and_name":
            v.namesake = (r.get("navn") or "", org)
            v.detail = f"{v.detail}; {what}"
            return
        v.status, v.method, v.detail = "name_only", None, what
        v.others = {org: r.get("navn") or ""}
        return
    text = " ".join(p.text for p in v.pages)
    our_rx = _full_name_rx(e.get("navn") or "")
    names_us = _visible(our_rx, text)
    # another entity's full name seen only inside ours ('Well AS' inside 'The Well AS') does not name it
    masked = our_rx.sub(" ", text) if our_rx else text
    our_core_rx = name_regex([w for w in name_words(e.get("navn") or "") if w not in FORM_NAME_WORDS])
    for r in rows:
        org = r.get("organisasjonsnummer")
        if org == ctx.org or r.get("overordnetEnhet") == ctx.org or not _active(r):
            continue
        theirs = _core_tokens(r.get("navn") or "")
        if not theirs or not (theirs <= ours or ours <= theirs) or _staff_body(r):
            continue
        other_rx = _full_name_rx(r.get("navn") or "")
        # a foreign company's Norwegian branch (NUF) under our name is the parent: never masked away
        nuf = ((r.get("organisasjonsform") or {}).get("kode") or "").upper() == "NUF" or \
            bool(re.search(r"(?i)\bNUF\b", r.get("navn") or ""))
        names_them = bool(other_rx and other_rx.search(text if nuf else masked))
        if theirs != ours:
            # A longer or shorter namesake (a subsidiary 'Kitron Eiendom AS', a parent 'Viken AS') collides only
            # when the site shows that other name as a phrase.
            core_rx = name_regex([w for w in name_words(r.get("navn") or "") if _translit(w) in theirs])
            if not (core_rx and any(not _in_hostname(text, m.start(), m.end()) for m in core_rx.finditer(text))):
                continue
            if theirs < ours and _visible(our_core_rx, text):
                continue  # the site shows our longer name too, so the short form is just ours abbreviated
        if names_us and not names_them:
            continue
        owner = r.get("overordnetEnhet")
        what = (f"another registered entity shares the name: {r.get('navn')} ({org}"
                + (f", workplace of {owner}" if owner else "") + ")")
        if v.method == "registry_website_and_name":
            # The company declared this website itself, so it stays its website, but the site may equally speak
            # for the namesake (often a subsidiary): nothing is derived from it.
            v.namesake = (r.get("navn") or "", owner or org)
            v.detail = f"{v.detail}; {what}"
            return
        v.status, v.method = "name_only", None
        v.detail = what
        v.others = {org: r.get("navn") or ""}
        return


async def _check_variant(sp: "_Spend", ctx: CompanyContext, v: Verdict) -> None:
    """A strong proof that rests on a shortened name (place, form or initials dropped) holds only when that
    shortened name is not itself another registered company (e.g. 'Viken Forsikring AS' for 'Nordre Viken
    Forsikring AS') and is not a single common noun."""
    if v.status != "proven" or v.method == "orgnr_on_page" or not v.variant:
        return
    lab = " ".join(_translit(w) for w in v.variant)
    if len(v.variant) == 1 and _translit(v.variant[0]) in COMMON_NOUNS:
        v.status, v.method = "name_only", None
        v.detail = f"only the common word '{lab}' of the legal name is shown"
        return
    other = await confirm_sisters(sp, ctx, [lab], limit=1)
    if lab in other:
        v.status, v.method = "name_only", None
        v.detail = f"the site shows '{lab}', which is another registered company ({other[lab]})"


# ---------------------------------------------------------------- exact legal name, unique in the registry
# A site that shows only our name (no org number, registry phone or address) is accepted when every one of these
# holds: the company is an AS or ASA; the exact registered legal name with its upper-case legal form ("Fishlab AS")
# is printed on the site; the site's own domain is exactly that name (fishlab.no); no other legal name sharing our
# distinctive words, and no other company in an operator position (title, owner or copyright line), is on the
# site; the site shows the registered post town or municipality, or a current role holder's full name; and a
# registry name search (main units and sub-units, active or not, original spelling) finds no other entity whose
# name contains all of ours ('Fishlab Holding AS', a second 'Fishlab AS' elsewhere, another company's workplace).
LEGAL_UNIQUE_FORMS = {"AS": "(?:AS|As)", "ASA": "(?:ASA|Asa)"}  # capital A; never the English word "as"
LEGAL_UNIQUE_TLDS = {"no", "com", "net", "org", "io", "as", "eu", "biz", "info"}
_CREDIT_LEAD = re.compile(r"^(nettside|nettsted|nettbutikk|hjemmeside|utviklet|levert|laget|designet|design|webdesign|"
                          r"drevet|driftet|powered|made|built|hosted|hosting|produsert|publisert|tema|theme)\s+(av|by|med|with|fra)\b")


def _legal_name_rx(name: str, form: str) -> re.Pattern | None:
    """The registered name followed by its legal form with a capital A ('Fishlab AS', 'Moi Rør As'), right after
    the whole name; the lower-case English word 'as' after a name ('Faceless as part of') and a Danish 'A/S'
    never match."""
    suffix = LEGAL_UNIQUE_FORMS.get(form)
    words = name_words(name)
    rx = name_regex(words) if words and suffix else None
    if not rx:
        return None
    body = r"[\s\-.]*".join(_word_rx(w) for w in words)
    return re.compile(r"(?<![0-9a-zæøåA-ZÆØÅ])(?i:" + body + r")[  ]+" + suffix + r"(?![0-9a-zæøåA-ZÆØÅ/&])")


def _label_is_name(host: str, name: str) -> bool:
    """The site's registered domain is the whole legal name and nothing else (fishlab.no, luva-prosjekt.no)."""
    parts = bare(host).split(".")
    if len(parts) != 2 or parts[1] not in LEGAL_UNIQUE_TLDS:
        return False
    label = re.sub(r"[^a-z0-9]", "", parts[0])
    words = name_words(name)
    options = {"".join(v) for ws in (words, [w for w in words if w not in ("og", "and")]) for v in _ascii_variants(ws)}
    return bool(label) and label in options


def _legal_span(pages: list[Page], name: str, form: str) -> tuple[Page, str, str] | None:
    rx = _legal_name_rx(name, form)
    if not rx:
        return None
    for p in pages:
        for m in rx.finditer(p.text):
            if not _in_hostname(p.text, m.start(), m.end()):
                return p, _window(p.text, m.start(), m.end(), 40), m.group(0).strip()
    return None


def _credit_only(pages: list[Page], other: str) -> bool:
    """Every mention of the other company follows a web agency credit ('Nettside levert av Webby AS')."""
    rx = name_regex(other.split())
    hits = [(p.text, m) for p in pages for m in rx.finditer(p.text)] if rx else []
    return bool(hits) and all(CREDIT.search(t[max(0, m.start() - 40):m.start()]) for t, m in hits)


def _role_people(ctx: CompanyContext) -> list[str]:
    """Current role holders who are people (full names), from the registry roles read earlier in the run."""
    out = []
    for c in ctx.claims:
        v = c.value
        if c.section == "leadership" and c.availability == "available" and isinstance(v, dict) \
                and v.get("name") and not v.get("organisation_number") and len(str(v["name"]).split()) >= 2:
            if v["name"] not in out:
                out.append(v["name"])
    return out


def _corroborator(pages: list[Page], ctx: CompanyContext, name: str) -> tuple[Page, str, str] | None:
    """The registered post town or municipality (as a capitalised place name, after a post code when it is a
    short word such as 'Oslo'), or a current role holder's first and last name, somewhere on the site. Our own
    name is masked first, so a place inside the legal name does not count."""
    e = ctx.registry or {}
    addr = e.get("forretningsadresse") or e.get("beliggenhetsadresse") or {}
    places = sorted({x.strip() for x in (addr.get("poststed"), addr.get("kommune")) if x and x.strip()}, key=lambda x: (-len(x), x))
    our_rx = name_regex(name_words(name))
    people = _role_people(ctx)
    for p in pages:
        text = our_rx.sub(lambda m: " " * len(m.group(0)), p.text) if our_rx else p.text
        for place in places:
            body = r"\s+".join(re.escape(w) for w in place.split())
            for m in re.finditer(r"(?<![\wæøåÆØÅ@./-])(\d{4}[  ]+)?(" + body + r")(?![\wæøåÆØÅ])", text, re.I):
                word = m.group(2)
                if not word[:1].isupper() or _in_hostname(text, m.start(2), m.end(2)):
                    continue
                if len(place) >= 5 or m.group(1):
                    return p, _window(p.text, m.start(), m.end(), 40), p.text[m.start():m.end()].strip()
        for person in people:
            ws = person.split()
            rx = re.compile(r"(?<![\wæøåÆØÅ])" + re.escape(ws[0]) + r"(?:[  ]+[\wæøåÆØÅ.\-]+){0,2}?[  ]+"
                            + re.escape(ws[-1]) + r"(?![\wæøåÆØÅ])", re.I)
            m = rx.search(text)
            if m:
                return p, _window(p.text, m.start(), m.end(), 40), p.text[m.start():m.end()].strip()
    return None


def legal_name_unique_page(v: Verdict, ctx: CompanyContext) -> tuple[tuple, tuple] | None:
    """The page-side checks of the legal_name_unique proof: (legal-name proof, corroborator proof), or None."""
    e = ctx.registry or {}
    name = e.get("navn") or ""
    form = ((e.get("organisasjonsform") or {}).get("kode") or "").upper()
    if v.status != "name_only" or not (v.detail or "").startswith("legal name shown, but no org number") or \
            v.cross or v.path_scoped or v.others or v.namesake or v.variant or v.source == "guess_short" or \
            form not in LEGAL_UNIQUE_FORMS or not v.pages or not _label_is_name(v.pages[0].host, name):
        return None
    legal = _legal_span(v.pages, name, form)
    if not legal:
        return None
    words = name_words(name)
    ours = {_translit(w) for w in words}
    distinct = {w for w in ours if len(w) >= 3 and w not in GENERIC_WORDS and not w.isdigit()} or ours
    for other, _suffix, _positional, operator, _strong in other_company_names(v.pages, words):
        ws = set(other.split())
        if ws & distinct:
            return None  # a sister, parent or namesake with our words ('Eurotec Entreprenør AS' on eurotec.no)
        if operator and not _CREDIT_LEAD.match(other) and not _credit_only(v.pages, other):
            return None  # another company runs or owns the site
    corr = _corroborator(v.pages, ctx, name)
    return (legal, corr) if corr else None


async def _legal_namesakes(sp: "_Spend", ctx: CompanyContext) -> list[str]:
    """Other registered entities (main units and other companies' sub-units, deleted ones included) whose name
    contains every word of our legal name, from the ranked and the complete phrase search in both spellings.
    More phrase matches than can be read count as a namesake. A search that fails after its retries raises
    _RegistrySearchFailed (the website step then fails as transient), so a flaky search never flips the outcome."""
    e = ctx.registry or {}
    words = [w for w in name_words(e.get("navn") or "") if w not in FORM_NAME_WORDS]
    if not words:
        return ["(no name words to search)"]
    rows, truncated = await registry_rows(sp, words)
    ours = {_translit(w) for w in words}
    found: list[str] = []
    for r in rows:
        org = r.get("organisasjonsnummer")
        if org == ctx.org or r.get("overordnetEnhet") == ctx.org:
            continue
        theirs = {_translit(w) for w in name_words(r.get("navn") or "") if w not in FORM_NAME_WORDS}
        if ours <= theirs:
            found.append(f"{r.get('navn')} ({org})")
    if truncated:
        found.append("(more registry matches than could be read)")
    return found


async def _check_legal_name_unique(sp: "_Spend", ctx: CompanyContext, v: Verdict) -> None:
    """Upgrade a name-only candidate to proven (legal_name_unique) when every page and registry check holds."""
    got = legal_name_unique_page(v, ctx)
    if not got:
        return
    conflict = await _identity_conflict(sp, ctx, v)
    if conflict:
        v.detail = f"{v.detail}; {conflict}"
        return
    namesakes = await _legal_namesakes(sp, ctx)
    if namesakes:
        v.detail = f"{v.detail}; another registered entity carries our whole name: {namesakes[0]}"
        return
    (lp, lspan, lcore), (cp, cspan, ccore) = got
    v.status, v.method = "proven", "legal_name_unique"
    v.detail = (f"the exact registered legal name '{lcore}' is on the site, its domain is that name, no other registered "
                f"entity carries the name, and the site shows '{ccore}' from the registry")
    v.proof = [(lp, lspan, lcore), (cp, cspan, ccore)]


LANG_SEG = {"no", "nb", "nn", "en", "se", "sv", "da", "de", "fi", "home", "hjem", "forside", "index.html", "index.php", "nb-no", "no-no", "nn-no", "en-gb", "en-us", "en-no", "nb_no", "no_no", "norge", "norway", "nor", "sv-se", "da-dk", "fi-fi", "eng"}


ABOUT_SEG = re.compile(r"^(om-?oss|om|about(-us)?|kontakt(-oss)?|contact(-us)?|firma|selskapet|company)$", re.I)


def _is_front_or_about(url: str) -> bool:
    segs = [x for x in urlsplit(url).path.split("/") if x]
    return not segs or (len(segs) == 1 and bool(ABOUT_SEG.match(segs[0]) or segs[0].lower() in LANG_SEG))


def _is_section(url: str) -> bool:
    """True when the page is a deeper section of a host (a member page on a chain site or a school on a portal),
    not the site's front page or a language root."""
    segs = [x for x in urlsplit(url).path.split("/") if x]
    return bool(segs) and not (len(segs) == 1 and segs[0].lower() in LANG_SEG)


_SEEDS: dict[str, list[dict]] | None = None
# Order in which seed candidates are tried (lower first); unknown open-data sources rank 2.
SEED_STRENGTH = {"commoncrawl": 0, "crawl_orgnr": 0, "crawl_phone_email": 1, "crawl_signals": 2,
                 "commoncrawl_domain": 3}
SEEDS_PATH = __import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "website_seeds.json.gz"


def website_seeds() -> dict[str, list[dict]]:
    """Website candidates by org number (OSM, Overture, Brreg workplaces, NAV, Wikidata, Common Crawl and Kilde's
    own crawl), loaded once per process. Candidates only: they pass exactly the same proof, collision and
    exclusivity rules as a guessed domain (a seed is never the registry's declaration)."""
    global _SEEDS
    if _SEEDS is None:
        try:
            import gzip
            with gzip.open(SEEDS_PATH, "rt", encoding="utf-8") as fh:
                _SEEDS = json.load(fh).get("seeds") or {}
        except (OSError, ValueError):
            _SEEDS = {}
    return _SEEDS


def candidates(ctx: CompanyContext) -> list[tuple[str, str, str | None]]:
    """(domain, source, start_url), in order: registry website, email domain, name-derived guesses."""
    e = ctx.registry or {}
    out: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()

    def add(dom: str, src: str, start: str | None = None) -> None:
        dom = bare(dom.lower().strip(".").strip())
        if not dom or "." not in dom or dom in seen or is_platform(dom):
            return
        seen.add(dom)
        out.append((dom, src, start))

    hj = (e.get("hjemmeside") or "").strip()
    if hj:
        url = hj if re.match(r"^https?://", hj, re.I) else f"https://{hj}"
        h = host_of(url)
        path = urlsplit(url).path
        start = url.replace("http://", "https://", 1) if path not in ("", "/") else None
        if h:
            if is_platform(h):
                ctx.facts.setdefault("website_registry_platform", url)
            add(h, "registry", start)
    em = (e.get("epostadresse") or "").strip().lower()
    if "@" in em:
        d = em.rsplit("@", 1)[1]
        if d not in FREE_MAIL and not any(d.endswith("." + x) for x in FREE_MAIL):
            add(d, "email")
    def seed_rank(x: dict) -> tuple:
        # strongest first: an org number seen on the site, then a registry phone or email unique to this company,
        # then open-data declarations and two-signal crawl matches, then bare domain-name matches
        src = set(x.get("sources") or [])
        return min((SEED_STRENGTH.get(s, 2) for s in src), default=3), x.get("domain") or ""
    # Seeds whose domain carries our name come first, then the whole-name guesses, then seeds on other domains
    # (a brand, product or campaign site that prints our org number): when several sites are ours, the one named
    # after the company is the one published (betonmast.no before flexiwood.no).
    seeds = [x for x in sorted(website_seeds().get(ctx.org) or [], key=seed_rank) if x.get("domain")]
    named = [x for x in seeds if _domain_has_name(x["domain"], e)]
    for seed in named:
        add(seed["domain"], "seed:" + "+".join(sorted(seed.get("sources") or [])))
    # a legal name that is itself a domain (INNREGISTRERING.NO AS)
    for dom in re.findall(r"(?i)\b([a-z0-9æøå][a-z0-9æøå\-]*\.(?:no|com|net|org|as|io|nu|se|dk))\b", e.get("navn") or ""):
        add(_translit(dom), "guess")
    whole = {re.sub(r"[^a-z0-9]", "", "".join(v)) for v in _ascii_variants(name_words(e.get("navn") or ""))}
    for d in name_domains(e.get("navn") or "", e):
        if d.split(".")[0] in whole and d.rsplit(".", 1)[-1] in ("no", "com"):
            add(d, "guess")
    for seed in seeds:
        if seed not in named:
            add(seed["domain"], "seed:" + "+".join(sorted(seed.get("sources") or [])))
    for d in name_domains(e.get("navn") or "", e):
        full = re.sub(r"[^a-z0-9]", "", _translit(" ".join(name_words(e.get("navn") or ""))))
        lab = d.split(".")[0]
        add(d, "guess_short" if lab in SHORT_FORMS and lab != full else "guess")
    for old in e.get("historiskeNavn") or []:  # former registered names
        nm = old.get("navn") if isinstance(old, dict) else old
        for d in name_domains(nm or "", e)[:2]:
            add(d, "guess")
    for d in workplace_domains(ctx):  # last: a registered workplace's own name (a restaurant, a shop)
        add(d, "guess_workplace")
    return out


WORKPLACE_NAMES = 2    # workplaces whose names give domain guesses
WORKPLACE_DOMAINS = 3  # guesses per workplace name: joined .no, hyphenated .no, joined .com


def _workplace_label(name: str) -> str | None:
    """The domain label a workplace name gives (all its words joined), or None when the name is too weak to
    stand for a site: a single common word, digits only, or under six letters."""
    words = [w for w in name_words(name) if not w.isdigit()]
    if not words or (len(words) == 1 and (_translit(words[0]) in COMMON_NOUNS or words[0] in GENERIC_WORDS)):
        return None
    lab = _ascii_variants(words)[0]
    joined = "".join(lab)
    return joined if len(re.sub(r"[^a-z]", "", joined)) >= 6 else None


def workplace_domains(ctx: CompanyContext) -> list[str]:
    """Domain guesses from the names of the company's registered workplaces (underenheter) whose names differ
    from the legal name: 'BELLA BAMBINA' of 'EIK BELLA BAMBINA AS' gives bellabambina.no. Full names only
    (never a shortened part), in registry order, at most WORKPLACE_NAMES names and WORKPLACE_DOMAINS each."""
    e = ctx.registry or {}
    legal = _workplace_label(e.get("navn") or "") or ""
    out: list[str] = []
    names = 0
    for w in ctx.facts.get("workplaces") or []:
        lab = _workplace_label(w.get("navn") or "")
        if not lab or lab == legal or names >= WORKPLACE_NAMES:
            continue
        names += 1
        words = _ascii_variants([x for x in name_words(w["navn"]) if not x.isdigit()])[0]
        for d in ("".join(words) + ".no", "-".join(words) + ".no", "".join(words) + ".com")[:WORKPLACE_DOMAINS]:
            if d not in out:
                out.append(d)
                ctx.facts.setdefault("website_workplace_domains", {})[d] = w.get("organisasjonsnummer")
    return out


async def _check_workplace(sp: "_Spend", ctx: CompanyContext, v: Verdict) -> None:
    """A domain guessed from a registered workplace's name is ours when the site prints that workplace's full
    name and its registered location address (street, number and postcode), prints no other organisation
    number, and no other registered entity or another company's workplace carries the workplace's name (a
    chain or franchise under that name, or a namesake elsewhere): the same registry name search that guards
    name-and-address proofs for the legal name, with no exception for shared addresses."""
    if v.source != "guess_workplace" or v.cross or v.path_scoped or v.others or v.namesake or v.method:
        return
    if v.status not in ("no_match", "name_only") or (v.status == "name_only" and not str(v.detail or "").startswith("legal name shown")):
        return
    e = ctx.registry or {}
    if ((e.get("organisasjonsform") or {}).get("kode") or "").upper() in NON_OPERATING:
        return
    wp_org = (ctx.facts.get("website_workplace_domains") or {}).get(bare(v.domain))
    w = next((x for x in ctx.facts.get("workplaces") or [] if x.get("organisasjonsnummer") == wp_org), None)
    if not w or not w.get("beliggenhetsadresse"):
        return
    label = re.sub(r"[^a-z0-9]", "", bare(v.pages[0].host).split(".")[0]) if v.pages else ""
    if label != _workplace_label(w["navn"]):
        return  # the site's own domain must be the workplace's whole name
    words = [x for x in name_words(w["navn"]) if not x.isdigit()]
    name_span = name_page = None
    for p in v.pages:
        s, conflict = name_check(p.text, words)
        if s and not conflict:
            name_span, name_page = s, p
            break
    addr = None
    for p in v.pages:
        s, core = find_address_core(p, {"forretningsadresse": w["beliggenhetsadresse"]})
        if s:
            addr = (p, s, core)
            break
    if not name_span or not addr:
        return
    ours = _core_tokens(w["navn"])
    wp_addr = _addr_key({"beliggenhetsadresse": w["beliggenhetsadresse"]})
    rows, cut = await registry_rows(sp, words)
    if cut:
        v.detail = f"workplace name {w['navn']} is too common in the registry to decide"
        return
    for r in rows:
        org = r.get("organisasjonsnummer")
        if org in (ctx.org, wp_org) or r.get("overordnetEnhet") == ctx.org or not _active(r):
            continue
        theirs = _core_tokens(r.get("navn") or "")
        # the whole workplace name inside theirs (a chain's 'Bella Solsikke Storo AS'), or the same number of words
        # each a near match ('Bela Solsike') at the workplace's address; a shorter unrelated name ('Bell & Bell AS',
        # 'Bella AS') is not it
        # (as for the legal name: a near-namesake, one letter off, collides only at the same address)
        long_t, long_o = {w for w in theirs if len(w) >= 3}, {w for w in ours if len(w) >= 3}
        near = bool(long_t) and len(long_t) == len(long_o) and _near(long_o, long_t)
        if theirs and (ours <= theirs or (near and _addr_key(r) == wp_addr)):
            owner = r.get("overordnetEnhet")
            v.detail = (f"another registered entity carries the workplace name: {r.get('navn')} ({org}"
                        + (f", workplace of {owner}" if owner else "") + ")")
            return
    # the same rules as for the legal name: the workplace's address must be nobody else's, no namesake sits at
    # it, and no other registered namesake heads the site
    wp_rec = {"navn": w["navn"], "beliggenhetsadresse": w["beliggenhetsadresse"]}
    blocker = (_heading_namesake(v.pages, [r for r in rows if r.get("organisasjonsnummer") != wp_org], ctx)
               or await _colocated_namesake(sp, ctx, wp_rec, frozenset({wp_org}))
               or await _address_shared(sp, ctx, wp_rec, frozenset({wp_org})))
    if blocker:
        v.detail = f"workplace {w['navn']}: {blocker} (another registered entity at or under its name or address)"
        return
    v.status, v.method = "proven", "workplace_address_and_name"
    v.detail = f"registered workplace {w['navn']} ({wp_org}): its name and location address shown on its own domain"
    v.proof = [addr, (name_page, name_span, _match_core(name_span, words))]


# ---------------------------------------------------------------- extraction
def _span_with_value(page: Page, span: str, phone: str) -> str:
    """A phone claim's span must contain the number itself: keep the span only if its digits hold the number."""
    digits = re.sub(r"\D", "", phone)[-8:]
    if digits and digits in re.sub(r"\D", "", span or ""):
        return span
    raw = page.snap.content.decode("utf-8", "replace")
    for m in PHONE_TEXT.finditer(raw):
        if re.sub(r"\D", "", m.group(0))[-8:] == digits:
            return m.group(0).strip()
    m = re.search(r'tel:[^"\'<> ]*', raw)
    return m.group(0) if m and digits in re.sub(r"\D", "", m.group(0)) else span


def bytes_exact(snap: Snapshot | None, span: str | None, prefer: str = "") -> str | None:
    """A span whose UTF-8 bytes occur in the saved response. Pages in another charset (Latin-1) store bytes
    that differ for non-ASCII text, so fall back to the longest ASCII run of the span that is in the bytes,
    preferring one that holds `prefer` (e.g. the number a phone claim is about)."""
    if not span or snap is None:
        return span
    raw = snap.content
    if span.encode("utf-8") in raw:
        return span
    try:
        raw.decode("utf-8")
        return span  # a UTF-8 page: the pipeline maps text spans onto the saved bytes
    except UnicodeDecodeError:
        pass
    runs = [r.strip() for r in re.split(r"[^\x20-\x7e\n\t]+", span) if len(r.strip()) >= 4]
    found = [r for r in runs if r.encode("utf-8") in raw]
    if not found:
        # text windows cross tags: take the longest run of consecutive words that occurs in the bytes
        for r in runs:
            words = r.split()
            for size in range(len(words), 0, -1):
                for i in range(len(words) - size + 1):
                    cand = " ".join(words[i:i + size])
                    if len(cand) >= 4 and cand.encode("utf-8") in raw:
                        found.append(cand)
                        break
                if found and len(found[-1].split()) == size:
                    break
    runs = found
    if not runs:
        return span
    if prefer:
        hit = [r for r in runs if prefer in re.sub(r"\D", "", r) or prefer in r or contains_core(r, prefer, True)]
        if hit:
            return max(hit, key=len)
    return max(runs, key=len)


def _ev(ctx: CompanyContext, page: Page, span: str | None, method: str = EXTRACT,
        effective: str | None = None, note: str | None = None) -> Evidence:
    if span:
        raw = page.snap.content.decode("utf-8", "replace")
        if span not in raw:
            import html as _html
            span = (verbatim(raw, span) or verbatim(raw, _html.unescape(span))
                    or _verbatim_core(raw, span) or span)
    span = bytes_exact(page.snap, span)
    return ctx.add_evidence(page.snap, source_class=COMPANY_OWNED, span=span, method=method, licence=LICENCE,
                            effective_date=effective, note=note)


def _meta(page: Page, *names: str) -> tuple[str | None, str | None]:
    for n in names:
        tag = page.soup.find("meta", attrs={"name": re.compile(f"^{re.escape(n)}$", re.I)}) or \
            page.soup.find("meta", attrs={"property": re.compile(f"^{re.escape(n)}$", re.I)})
        if tag and tag.get("content"):
            c = _clean(tag["content"])
            if c:
                return c, c
    return None, None


def canonical_url(page: Page) -> str:
    p = urlsplit(page.url)
    return urlunsplit((p.scheme, p.netloc.lower(), "/", "", ""))


def unwrap_redirect(url: str) -> str:
    """The target of a click-tracking wrapper (google.com/url?q=, l.facebook.com/l.php?u=, lm.facebook.com)."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url
    host = (p.hostname or "").lower()
    q = dict(parse_qsl(p.query))
    if host.endswith("google.com") and p.path == "/url":
        return q.get("q") or q.get("url") or url
    if host in ("l.facebook.com", "lm.facebook.com") and p.path.startswith("/l.php"):
        return q.get("u") or url
    return url


HANDLE_TLD = re.compile(r"(?i)\.(?:no|com|net|org|se|dk|fi|is|de|uk|eu|io|biz|info|nu|as|co|app|online|shop|"
                        r"store|site|web|xyz|me|tv)(?:$|[.?#:/])")


# Instagram, TikTok, YouTube and Facebook names may contain dots, and Norwegian pages often use their domain as
# the name ('fjellbekk.no'); Facebook refuses names ending in .com or .net. LinkedIn, X and Pinterest names
# have no dots, so a domain there is a pasted link.
DOMAIN_HANDLES_OK = ("instagram", "tiktok", "youtube", "facebook")
FB_BAD_ENDING = re.compile(r"(?i)\.(?:com|net)$")


def _handle_is_link(seg: str, platform: str = "") -> bool:
    """A profile path segment that is really a link: starts with www., carries a scheme or is an email address,
    or (on platforms where a handle is not normally written as a domain) ends in a domain name."""
    s = unquote(seg).strip().lower().lstrip("@")
    if s.startswith(("www.", "http:", "https:")) or ":" in s or "@" in s:
        return True
    if platform == "facebook" and FB_BAD_ENDING.search(s):
        return True
    return platform not in DOMAIN_HANDLES_OK and bool(HANDLE_TLD.search(s))


def social_path_is_link(url: str) -> bool:
    """True when a profile URL's path holds a domain, URL or email instead of a handle."""
    try:
        path = urlsplit(url.strip() if "://" in url else "https://" + url.strip()).path
    except ValueError:
        return True
    host = host_of(url)
    plat = next((k for k, hs in SOCIAL.items() if any(host == h or host.endswith("." + h) for h in hs)), "")
    return any(_handle_is_link(x, plat) for x in re.sub(r"/+", "/", path).split("/") if x)


def clean_social(url: str) -> tuple[str, str] | None:
    """(platform, cleaned url) for a company profile link, or None for share links and generic pages."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https", ""):
        return None
    host = (p.hostname or "").lower()
    plat = next((k for k, hs in SOCIAL.items() if any(host == h or host.endswith("." + h) for h in hs)), None)
    if not plat:
        return None
    path = re.sub(r"/+", "/", p.path).rstrip("/")
    if plat == "facebook":
        path = re.sub(r"(?i)^/pg(?=/[^/]+)", "", path)  # facebook.com/pg/<handle>/about is the page <handle>
    fb_p = plat == "facebook" and bool(re.match(r"^/p/[^/]+-\d{6,}$", path))
    if not path or (SOCIAL_DROP.search(path + ("?" + p.query if p.query else "")) and not fb_p):
        return None
    segs = [s for s in path.split("/") if s]
    query = ""
    if plat == "linkedin":
        if len(segs) < 2 or segs[0] not in ("company", "school", "showcase"):
            return None
        segs = segs[:2]
    elif plat == "facebook":
        if segs[0].lower() in FB_RESERVED or (segs[0].lower() == "pages" and not any(x.isdigit() for x in segs)):
            return None  # theme placeholders and reserved paths (facebook.com/me resolves to the viewer)
        if segs[0] == "people" and len(segs) >= 3 and segs[2].isdigit():
            segs = segs[:3]
        elif segs[0] == "p" and len(segs) >= 2 and re.search(r"-\d{6,}$", segs[1]):
            segs = segs[:2]  # facebook.com/p/<Name>-<id>, a page's public profile path
        elif segs[0] == "profile.php":
            ids = [v for k, v in parse_qsl(p.query) if k == "id" and v.isdigit()]
            if not ids:
                return None
            query = urlencode({"id": ids[0]})
            segs = ["profile.php"]
        elif segs[0] == "pages" and len(segs) >= 2:
            segs = segs[:3]
        else:
            segs = segs[:1]
    elif plat == "youtube":
        if segs[0] in ("channel", "c", "user") and len(segs) >= 2:
            segs = segs[:2]
        elif segs[0].startswith("@"):
            segs = segs[:1]
        else:
            return None
    elif plat == "pinterest":
        if segs[0].lower() in ("pin", "ideas", "search", "today", "business", "login", "_") or len(segs) > 2:
            return None
        segs = segs[:1]
    elif plat == "tiktok":
        if not segs[0].startswith("@"):
            return None
        segs = segs[:1]
    else:  # instagram, x
        segs = segs[:1]
    handle = (segs[1] if segs[0] in ("people", "p") and len(segs) >= 2 else segs[-1]).lstrip("@").lower()
    if segs[0] != "profile.php" and handle in PLACEHOLDER_HANDLES:
        return None
    if any(_handle_is_link(x, plat) for x in segs):
        return None  # a domain, URL or email typed where the handle belongs (facebook.com/www.petol.no)
    canon_host = {"linkedin": "www.linkedin.com", "facebook": "www.facebook.com", "instagram": "www.instagram.com",
                  "youtube": "www.youtube.com", "x": "x.com", "tiktok": "www.tiktok.com",
                  "pinterest": "www.pinterest.com"}[plat]
    return plat, urlunsplit(("https", canon_host, "/" + "/".join(segs), query, ""))


def _in_chrome(a) -> bool:
    """True when a link sits in the page header, footer, navigation or a social-links block."""
    for parent in a.parents:
        if parent.name in ("footer", "header", "nav"):
            return True
        marks = " ".join(parent.get("class") or []) + " " + (parent.get("id") or "") if hasattr(parent, "get") else ""
        if re.search(r"footer|header|social|some|navbar|topbar", marks, re.I):
            return True
    return False


# ---------------------------------------------------------------- shared (multi-company) sites
BRREG_SEARCH = "https://data.brreg.no/enhetsregisteret/api/enheter"
FORM_WORDS = {"stiftelsen", "stiftelse", "sameiet", "sameie", "borettslag", "borettslaget", "andelslag", "forening",
              "foreningen", "selskapet", "firma"}
_SISTER_CACHE: dict[str, list[tuple[str, str]]] = {}
_ROW_CACHE: dict[str, dict] = {}  # Brreg search rows by org number, for tie checks  # label -> [(org, registered name)] across companies


def _twords(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _translit(s))


def _word_hit(words: list[str], text_t: str) -> bool:
    return any(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text_t) for w in words)


@dataclass
class Scope:
    """Decides whether an item on the verified site belongs to our company.

    A site is shared when it prints another organisation number or names a sister company that the
    registry confirms (e.g. a group site for 'Bjerke Ror AS' and 'Bjerke Ventilasjon AS'). On a shared
    site an item is ours only when it sits on a page that prints our org number and no other, or inside
    a block that names us and not the sister."""
    org: str
    words: list[str]
    others: dict[str, str] = field(default_factory=dict)    # other org number -> span
    sisters: dict[str, str] = field(default_factory=dict)   # sister label -> its org number
    dominant: dict[str, str] = field(default_factory=dict)  # other company named prominently -> org or country
    cross: bool = False
    path_scoped: bool = False
    other_mail: set[str] = field(default_factory=set)  # contact addresses on another organisation's domain
    codeclared: dict[str, str] = field(default_factory=dict)  # other org -> name, declaring the same website
    dominant_kind: dict[str, str] = field(default_factory=dict)  # dominant name -> operator, namesake or mention
    ours_printed: bool = False  # our org number in an operator position, no other operator: the site is ours
    group_root: bool = False  # we are the top parent of a registry group, on our own proven domain
    addr: tuple | None = None  # (street regex, postcode) of our registered address
    handle_namesakes: list = field(default_factory=list)  # registered names that social handles on the site carry

    def __post_init__(self) -> None:
        ours_t = [_translit(w) for w in self.words]
        sister_t = {w for lab in self.sisters for w in lab.split()}
        generic = GENERIC_WORDS | FORM_WORDS
        self.ours_rx = name_regex(self.words)
        self.ours_unique = [w for w in ours_t if w not in sister_t and w not in generic and len(w) >= 3]
        self.sister_unique = sorted({w for w in sister_t if w not in ours_t and w not in generic and len(w) >= 3})
        self.slug = "-".join(ours_t)

    @property
    def shared(self) -> bool:
        return bool(self.others or self.sisters)

    @property
    def exclusive(self) -> bool:
        """The site is ours alone: no other org number of any country, no confirmed sister or dominating
        company, no redirect to another domain, and not one section of a shared host."""
        return not (self.others or self.sisters or self.dominant or self.cross or self.path_scoped or self.other_mail
                    or self.codeclared)

    def reasons(self) -> str:
        out = []
        if self.cross:
            out.append("the company's domain redirects to another registered domain")
        if self.path_scoped:
            out.append("the page is one section of a host that serves others (chain or portal)")
        if self.others:
            out.append("the site prints other organisation numbers " + ", ".join(sorted(self.others)[:4]))
        if self.sisters:
            out.append("it names sister companies " + ", ".join(f"{k} ({v})" for k, v in self.sisters.items()))
        if self.dominant:
            out.append("it is presented by other companies " + ", ".join(f"{k} ({v})" for k, v in self.dominant.items()))
        if self.codeclared:
            out.append("other registered companies declare the same website: "
                       + ", ".join(f"{n} ({o})" for o, n in sorted(self.codeclared.items())))
        if self.other_mail:
            out.append("it lists contact addresses on other organisations' domains " + ", ".join(sorted(self.other_mail)[:3]))
        return "; ".join(out)

    def _num_hit(self, text: str, nums: list[str]) -> bool:
        return any(re.search(rf"(?<!\d){n[:3]}[\s.\u00a0]?{n[3:6]}[\s.\u00a0]?{n[6:]}(?!\d)", text)
                   for n in nums if len(n) == 9)

    def names_ours(self, text: str) -> bool:
        if self._num_hit(text, [self.org]):
            return True
        if self.addr and self.addr[0].search(text) and re.search(r"(?<!\d)" + self.addr[1] + r"(?!\d)", text):
            return True  # our registered street address and postcode
        if self.ours_rx and any(not _in_hostname(text, m.start(), m.end()) for m in self.ours_rx.finditer(text)):
            return True
        return bool(self.sisters) and _word_hit(self.ours_unique, _translit(text))

    def names_sister(self, text: str) -> bool:
        if self._num_hit(text, list(self.others)):
            return True
        return _word_hit(self.sister_unique, _translit(text.replace("-", " ").replace("_", " ")))

    def owner(self, el) -> str:
        """'ours', 'sister', 'both' or 'unknown', from the smallest enclosing block that names a company."""
        node = el
        for level in range(6):
            if node is None or not hasattr(node, "get_text") or node.name in ("body", "html", "main", "[document]"):
                break
            own = node.get_text(" ", strip=True)
            texts = [own]
            if level == 0 and hasattr(node, "get"):
                texts += [unquote(str(node.get("href") or "")), str(node.get("aria-label") or ""), str(node.get("title") or "")]
            if level <= 1:  # a short heading or label right before the item (e.g. "Reklamasjon - Rør")
                sib = node.find_previous_sibling()
                if sib is not None and getattr(sib, "name", None) in ("h1", "h2", "h3", "h4", "h5", "h6", "strong", "b",
                                                                       "dt", "label", "span", "p", "div") \
                        and len(sib.get_text(" ", strip=True)) <= 80:
                    texts.append(sib.get_text(" ", strip=True))
            t = " ".join(texts)
            o, s = self.names_ours(t), self.names_sister(t)
            if o and s:
                return "both"
            if s:
                return "sister"
            if o:
                return "ours"
            if len(own) > 400:
                break
            node = node.parent
        return "unknown"

    def page_ours_only(self, page: Page) -> bool:
        return bool(find_orgnr(page, self.org)) and not other_orgnrs(page, self.org) and not self.names_sister(page.text)

    def accept(self, el, page: Page) -> bool:
        if el is not None and hasattr(el, "get") and self.sisters and self.names_sister(
                unquote(str(el.get("href") or "")) + " " + el.get_text(" ", strip=True)):
            return False  # the link itself names a sister company
        if not self.shared:
            return True
        if el is not None and self.owner(el) in ("sister", "both"):
            return False
        if self.page_ours_only(page):
            return True
        return el is not None and self.owner(el) == "ours"

    def url_ours(self, url: str) -> bool:
        """For sitemap and news URLs on a shared site: the path must carry our name and no sister name."""
        if not self.shared:
            return True
        path = _translit(unquote(urlsplit(url).path)).replace("_", "-")
        return self.slug in path and not _word_hit(self.sister_unique, path.replace("-", " "))

    def describe(self) -> str:
        if not self.shared:
            return "site not exclusive to this company: " + self.reasons()
        parts = []
        if self.sisters:
            parts.append("sister companies " + ", ".join(f"{k} ({v})" for k, v in self.sisters.items()))
        if self.others:
            parts.append("other organisation numbers " + ", ".join(sorted(self.others)))
        return "shared site that also serves " + "; ".join(parts)


def _handle_words(url: str) -> list[str]:
    seg = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]).lstrip("@")
    seg = re.sub(r"([a-zæøå])([A-ZÆØÅ])", r"\1 \2", seg)
    ws = [w for w in _twords(seg.replace("-", " ").replace("_", " ").replace(".", " ")) if not w.isdigit()]
    while ws and ws[-1] in LEGAL_SUFFIX:
        ws.pop()
    return ws


def sister_labels(pages: list[Page], words: list[str]) -> list[str]:
    """Names on the site that start like ours but are a different company name (to confirm in Brreg)."""
    ours = [_translit(w) for w in words]
    dist = [w for w in ours if w not in GENERIC_WORDS | FORM_WORDS and not w.isdigit()]
    if not dist:
        return []
    first = dist[0]
    labels: list[str] = []

    def add(ws: list[str]) -> None:
        if first not in ws:
            return
        tail = ws[ws.index(first):][:4]
        while tail and tail[-1] in LEGAL_SUFFIX:
            tail.pop()
        if len(tail) < 2 or tail == ours[ours.index(first):] or tail == ours:
            return
        if all(w in GENERIC_WORDS | FORM_WORDS or w.isdigit() for w in tail[1:]):
            return
        lab = " ".join(tail)
        if lab not in labels:
            labels.append(lab)

    for p in pages:
        for em in ENTITY.finditer(p.text):
            add(_twords(em.group(1)))
        for a in p.soup.find_all("a", href=True):
            c = clean_social(a["href"])
            if c:
                add(_handle_words(c[1]))
            elif _in_chrome(a) and 2 <= len(a.get_text(" ", strip=True).split()) <= 4:
                add(_twords(a.get_text(" ", strip=True)))
    return labels


CREDIT = re.compile(r"(?i)(levert|utviklet|utvikla|laget|designet|design|webdesign|nettside|nettsted|nettbutikk|"
                    r"drevet|driftet|powered|made|built|hosted|hosting|produsert|publisert|tema|theme|webbyrå|byrå|"
                    r"utvikling|levert av|fra)\s*(av|by|med|with|fra|:)?\s*:?\s*$")
OWNER_CONTEXT = re.compile(r"(?i)(behandlingsansvarlig|data controller|controller|eies av|eid av|drives av|driftes av|"
                           r"operated by|owned by|run by|nettstedet eies|nettsiden eies|siden drives|"
                           r"er en del av|en avdeling|sin avdeling|avdeling av|datterselskap|subsidiary of|part of|©|copyright|"
                           r"ta kontakt med)")
SUPPLIER_CONTEXT = re.compile(r"(?i)leverandør|leverandor|supplier|vendor|databehandler|data processor|processor|"
                              r"tredjepart|third[ -]part|betalingsløsning|payment provider")


SUPPLIER_AFTER = re.compile(r"(?i)^\s*,?\s*(?:er|is|som er|as|acts as)?\s*(?:også|also|vår|our|var)?\s*"
                            r"(?:leverandør|leverandor|supplier|vendor|databehandler|data processor|processor|webbyrå|"
                            r"byrå|agency|samarbeidspartner|partner)")
SUPPLIER_BEFORE = re.compile(r"(?i)(?:server(?:e|ne|s)?|hosting|databehandler(?:avtale)?|data processor|leverandør|"
                             r"leverandor|supplier|vendor|tredjepart|third[ -]part)[^.!?]{0,40}$")
BIO_BEFORE = re.compile(r"(?i)\b(?:ceo|cfo|cto|coo|daglig leder|adm\.? dir\.?|administrerende direktør|direktør|director|"
                        r"styreleder|styremedlem|chairman|chair|board member|founder|grunnlegger|gründer|"
                        r"partner|jobbet|jobber|arbeidet|arbeider|ansatt|employed|worked|works|erfaring|experience|"
                        r"bakgrunn|background|tidligere|formerly|previously)\b[^.!?]{0,40}$")


def _supplier_or_bio(before: str, after: str) -> bool:
    """A company named as the site's supplier or data processor ('Adjoy AS er leverandør', 'servere som driftes av
    X AS'), or as a person's employer in a bio ('CEO of Norway King Crab AS and Troika Seafood AS')."""
    return bool(SUPPLIER_AFTER.search(after) or SUPPLIER_BEFORE.search(before) or BIO_BEFORE.search(before))


def _owner_statement(before: str, at: str) -> bool:
    """An ownership or controller phrase names this company as the site's operator, unless a supplier or data
    processor word stands between that phrase and the name ('servers operated by the supplier for our
    e-commerce: Visbook AS' names a supplier, not the operator)."""
    if OWNER_CONTEXT.search(at):
        return True
    before = re.split(r"[.!?]\s", before)[-1]  # an owner phrase in the previous sentence names someone else
    hits = list(OWNER_CONTEXT.finditer(before))
    if not hits:
        return False
    return not SUPPLIER_CONTEXT.search(before[hits[-1].end():])


FOREIGN_SUFFIX = {"ab", "aps", "oy", "oyj", "gmbh", "ltd", "limited", "inc", "llc", "bv", "srl", "sarl", "ou", "uab"}
ENTITY_ANY = re.compile(r"((?:[0-9A-Za-zÆØÅæøåÄÖäöüé&\-]+[ \u00a0]+){1,5}?)"
                        r"(AS|ASA|A/S|ANS|DA|SA|AB|ApS|Oy|OY|Oyj|GmbH|Ltd|Limited|Inc|LLC|BV|OÜ|UAB|STI)\b\.?")


CONNECTORS = {"og", "&", "i", "for", "av", "de", "la", "von", "van", "der", "and", "of"}
# Foreign platform companies that sites name in badges, embeds and trademark notices (maps, app stores,
# payment, analytics, social networks, site builders, cookie banners): never a company presenting the site.
PLATFORM_COMPANIES = {"google", "apple", "microsoft", "meta", "meta platforms", "facebook", "instagram", "linkedin",
                      "youtube", "twitter", "x corp", "tiktok", "amazon", "amazon web services", "stripe", "paypal",
                      "klarna", "adyen", "visa", "mastercard", "wix", "wix com", "squarespace", "shopify", "automattic",
                      "wordpress", "godaddy", "cloudflare", "cookiebot", "usercentrics", "onetrust", "hubspot",
                      "mailchimp", "the rocket science group", "vimeo", "spotify", "zoom video communications",
                      "booking com", "tripadvisor", "airbnb", "uber", "alphabet"}
TRADEMARK_CONTEXT = re.compile(r"(?i)(trademarks?|varemerker?|registered|registrert|®|™|logo|badge|"
                               r"get it on|download on|last ned)\W*(of|for|av|til|tilhører|belongs to)?\W*$")


OPERATOR_PAGE = re.compile(r"(?i)om-?oss|about|kontakt|contact|personvern|privacy|impressum|imprint|firmainfo|selskapet")
OPERATOR_MARK = re.compile(r"(?i)footer|header|contact|kontakt|imprint|impressum|copyright|address|adresse|company-info")


STATE_CLASS = re.compile(r"(?i)(^|[-_])(has|no|with|without|hide|show|is|sticky|fixed)[-_]")


def _block_marks(el) -> str:
    """Class and id words that mark an element as a block (footer, header, contact). The page-wide <html> and
    <body> never are, nor are state classes that only say a page has a block ('et-tb-has-footer')."""
    if el.name in ("html", "body"):
        return ""
    toks = [t for t in (el.get("class") or []) + [el.get("id") or ""] if t and not STATE_CLASS.search(t)]
    return " ".join(toks)


def _owner_text(p: Page) -> str:
    """Text where an operator is named: footer, imprint and copyright blocks."""
    parts = []
    for el in p.soup.find_all(True):
        marks = _block_marks(el)
        if el.name == "footer" or re.search(r"(?i)footer|imprint|impressum|copyright", marks):
            parts.append(el.get_text(" ", strip=True))
    return " | ".join(parts)


def _operator_text(p: Page) -> str:
    """Text in operator positions: header, footer, navigation, address and contact or imprint blocks."""
    parts = []
    for el in p.soup.find_all(True):
        marks = _block_marks(el)
        if el.name in ("header", "footer", "address") or OPERATOR_MARK.search(marks):
            parts.append(el.get_text(" ", strip=True))
    return " | ".join(parts)


def other_company_names(pages: list[Page], words: list[str]) -> list[tuple[str, str, bool]]:
    """Other legal names (with a legal-form suffix) on the site, as (name words, suffix, positional).
    Positional means an operator position: page title, header, footer, contact or imprint block, an about,
    contact or privacy page, a copyright line or an ownership or controller sentence. Names inside links to
    other sites (web agency and partner credits) are skipped. Positional names come first."""
    ours = [_translit(w) for w in words]
    best: dict[tuple[str, str], int] = {}
    side: set[tuple[str, str]] = set()
    owned: set[tuple[str, str]] = set()
    for p in pages:
        optext = _operator_text(p)
        ownertext = _owner_text(p)
        path_txt = unquote(urlsplit(p.url).path)
        op_page = bool(OPERATOR_PAGE.search(path_txt)) and not re.search(r"(?i)personvern|privacy", path_txt)
        offsite = " | ".join(a.get_text(" ", strip=True) for a in p.soup.find_all("a", href=True)
                             if urlsplit(urljoin(p.url, a["href"])).scheme in ("http", "https")
                             and not same_site(host_of(urljoin(p.url, a["href"])), p.host))
        title = p.soup.title.get_text(" ", strip=True) if p.soup.title else ""
        score: dict[tuple[str, str], int] = {}
        for txt, is_title in ((title, True), (p.text, False)):
            for m in ENTITY_ANY.finditer(txt):
                raw = re.split(r"[.!?:;|,()\[\]]\s", m.group(1))[-1]
                toks = raw.split()
                run: list[str] = []
                for i in range(len(toks) - 1, -1, -1):  # the trailing run of capitalised words is the name
                    t = toks[i]
                    if t[:1].isupper() or t[:1].isdigit() or t == "&":
                        run.insert(0, t)
                    elif t.lower() in CONNECTORS and run and i > 0 and toks[i - 1][:1].isupper():
                        run.insert(0, t)
                    else:
                        break
                ws = _twords(" ".join(run).replace("&", " og "))
                while ws and ws[0].isdigit():
                    ws.pop(0)
                foreign = m.group(2).lower() in FOREIGN_SUFFIX
                if not ws or len(ws) > 6:
                    continue
                if foreign and (" ".join(ws) in PLATFORM_COMPANIES
                                or TRADEMARK_CONTEXT.search(txt[max(0, m.start() - 40):m.start()] + m.group(1))):
                    continue  # a platform in a badge, embed or trademark notice, not a company presenting the site
                if not foreign and (ws == ours or ws[-len(ours):] == ours or ours[-len(ws):] == ws):
                    continue  # our own name or its tail (a same-named foreign AB or GmbH is a different company)
                before = txt[max(0, m.start() - 40):m.start()]
                if CREDIT.search(before) or (not is_title and " ".join(run) and " ".join(run) in offsite):
                    continue  # a web agency or partner credit
                key = (" ".join(ws), m.group(2).lower())
                ctx_hit = _owner_statement(txt[max(0, m.start() - 60):m.start()], txt[m.start():m.end() + 16])
                mention = m.group(0).strip()
                if _supplier_or_bio(txt[max(0, m.start() - 60):m.start()], txt[m.end():m.end() + 40]):
                    side.add(key)
                elif ctx_hit or is_title:
                    owned.add(key)
                positional = is_title or ctx_hit or op_page or (mention in optext)
                operator = is_title or ctx_hit or (mention in ownertext)
                score[key] = max(score.get(key, 0), 4 if (is_title or ctx_hit) else (3 if operator else (2 if positional else 1)))
        for k, v in score.items():
            best[k] = max(best.get(k, 0), v)
    for k in side - owned:
        best[k] = 1  # a supplier, data processor or a person's other employer: named, never presenting the site
    return [(k[0], k[1], best[k] >= 2, best[k] >= 3, best[k] >= 4) for k in sorted(best, key=lambda k: -best[k])]


STAFF_ONLY = re.compile(r"(?i)bedriftsidrettslag|bedriftsklubb|personalforening|velferdsforening|fagforening|"
                        r"pensjonskasse|ansattes|personalklubb")


def _staff_body(row: dict) -> bool:
    """A registered association of the company's own staff (a union club such as 'Tekna Leirvik', a company
    sports club, a staff welfare fund): an association (FLI) named as a staff body or registered as a trade
    union (NACE 94.2). It never runs the company's website, so it is no namesake for it. An ordinary sports
    club sharing a name (a club and its elite-team company) is not a staff body."""
    if ((row.get("organisasjonsform") or {}).get("kode") or "").upper() != "FLI":
        return False
    nace = ((row.get("naeringskode1") or {}).get("kode") or "")
    return bool(STAFF_ONLY.search(row.get("navn") or "")) or nace.startswith("94.2")


STAFF_BODIES = re.compile(r"(?i)bedriftsklubb|bedriftsidrettslag|bil(klubb)?\b|velferd|personalforening|fagforening|"
                          r"pensjonskasse|pensjon|idrettslag|photo club|fotoklubb|kunstforening|vennefor|"
                          r"feriested|feriehjem|ferieheim|hytteforening|velferdsstiftelse")


def _tied(row: dict, e: dict, site_host: str) -> str | None:
    """How a registered company is tied to us: shared address, phone, email domain or distinctive name words."""
    if _addr_key(row) and _addr_key(row) == _addr_key(e):
        return "shares our registered address"
    if _digits_phones(row) & _digits_phones(e):
        return "shares our registered phone"
    dom = (row.get("epostadresse") or "@").rsplit("@", 1)[1].lower()
    ours = (e.get("epostadresse") or "@").rsplit("@", 1)[1].lower()
    if dom and dom not in FREE_MAIL and (dom == ours or same_site(dom, site_host)):
        return "shares our email domain"
    if _core_tokens(row.get("navn") or "") & {t for t in _core_tokens(e.get("navn") or "")
                                              if len(t) >= 4 and t not in GENERIC_WORDS}:
        return "shares our distinctive name words"
    return None


async def confirm_dominant(sp: "_Spend", ctx: CompanyContext, names: list[tuple], limit: int = 3,
                           site_host: str = "", kinds: dict[str, str] | None = None) -> dict[str, str]:
    """Registered companies that count against exclusivity: named in an operator position, or tied to us
    (address, phone, email domain or name words). Clients, partners and suppliers in body text do not count."""
    out: dict[str, str] = {}
    e = ctx.registry or {}
    ours = {t for t in _core_tokens(e.get("navn") or "") if len(t) >= 4 and t not in GENERIC_WORDS}
    # positional names first, then body names that share our name words, then the rest
    ordered = sorted(names, key=lambda n: (not n[2], not (set(n[0].split()) & ours)))
    kinds = {} if kinds is None else kinds
    for lab, suffix, positional, *rest in ordered:
        operator = bool(rest and rest[0])
        stated = bool(len(rest) > 1 and rest[1])
        if suffix in FOREIGN_SUFFIX:
            if positional or set(lab.split()) & ours:
                out[f"{lab} {suffix}"] = "foreign"
                kinds[f"{lab} {suffix}"] = "stated" if stated else ("operator" if operator else "mention")
            continue
        if limit <= 0:
            break
        limit -= 1
        got = await confirm_sisters(sp, ctx, [lab], limit=1)
        if lab not in got:
            continue
        row = _ROW_CACHE.get(got[lab]) or {}
        tie = _tied(row, e, site_host)
        if positional or tie:
            key = f"{lab} {suffix}"
            out[key] = got[lab]
            if stated:
                kinds[key] = "stated"  # named as the site's operator, owner or controller, or in the page title
            elif _staff_body(row):
                kinds[key] = "mention"  # our staff's union club or sports club
            elif operator:
                kinds[key] = "operator"
            elif tie and tie != "shares our distinctive name words":
                kinds[key] = "namesake"  # shares our address, phone or email domain
            elif tie and not STAFF_BODIES.search(row.get("navn") or lab):
                kinds[key] = "namesake"
            else:
                kinds[key] = "mention"
    return out


async def confirm_sisters(sp: "_Spend", ctx: CompanyContext, labels: list[str], limit: int = 2) -> dict[str, str]:
    """Labels that the registry knows as a different company: label -> org number."""
    out: dict[str, str] = {}
    for lab in labels[:limit]:
        hits = _SISTER_CACHE.get(lab)
        if hits is None:
            if not sp.can_registry():
                break
            # retried; a search that still fails makes the website step a transient failure
            rows, _cut = await brreg_name_search(sp, "enheter", lab, size=20)
            hits = [(r.get("organisasjonsnummer"), r.get("navn") or "") for r in rows
                    if " ".join(_translit(w) for w in name_words(r.get("navn") or "")) == lab]
            for r in rows:
                if r.get("organisasjonsnummer"):
                    _ROW_CACHE[r["organisasjonsnummer"]] = r
            _SISTER_CACHE[lab] = hits
        for org, _name in hits:
            if org and org != ctx.org:
                out[lab] = org
                break
    return out


# ---------------------------------------------------------------- derived items
GROUP_WORDS = ("group", "gruppen", "gruppe", "konsern", "holding", "holdings", "invest")
BUILDER_BRANDS = ("surftown", "wix", "squarespace", "webnode", "onecom", "one.com", "domeneshop", "jimdo",
                  "123hjemmeside", "makeweb", "websidebygger", "godaddy", "hostinger", "weebly", "wordpress",
                  "elementor", "shopify", "webflow", "hubspot", "mailchimp", "webhuset", "proisp", "ministry",
                  "netthandelsbyraa", "nettsidemaker", "smartweb", "idium", "nettfabrikken", "frontkom", "vev.design",
                  "24so", "sitebuilder", "strikingly", "ukit", "zyro", "ionos", "hostpoint", "loopia", "simply")


def _names_other_company(handle: str, words: list[str], other_names: list[str], group_root: bool = False) -> bool:
    """A handle that names a different company: our distinctive word plus a group or holding word our own name
    lacks ('elprogroupas' for Elpro Electro AS), or the name of another registered company we know of. When we
    are ourselves the top parent of a registry group, '<our name>group' is our own handle."""
    ours = re.sub(r"[^a-z0-9]", "", _translit("".join(words)))
    lead = next((_translit(w) for w in words if len(w) >= 3 and w not in GENERIC_WORDS), "")
    if lead and lead in handle and not group_root:
        for gw in GROUP_WORDS:
            if gw in handle and gw not in ours:
                return True
    for name in other_names:
        sq = re.sub(r"[^a-z0-9]", "", _translit(name))
        if len(sq) >= 5 and sq in handle and sq not in ours and not ours.startswith(sq):
            return True
    return False


def _bare_page_id(handle: str) -> bool:
    """A Facebook page addressed by its numeric id alone ('100087098506924') or by an old vanity path ending in
    the id ('Rørlegger-100656395325438'). Placeholder runs such as 123456789 do not count."""
    h = unquote(handle)
    m = re.fullmatch(r"(?:[^/]+-)?(\d{9,20})", h)
    if not m:
        return False
    d = m.group(1)
    return not (len(set(d)) <= 2 or d in "1234567890123456789012" or d in "9876543210987654321098")


def _mixed_block(page: Page, keys: set[str]) -> bool:
    """The page links a named social profile that carries neither our name nor our domain: its social links
    mix in another brand's (a chain's, group's or web agency's), so an unnamed id beside them is not provably
    ours."""
    for a in page.soup.find_all("a", href=True):
        c = clean_social(unwrap_redirect(a["href"]))
        if not c:
            continue
        segs = [x for x in urlsplit(c[1]).path.split("/") if x]
        raw = segs[1] if len(segs) >= 2 and segs[0] in ("people", "p", "company", "school", "showcase", "channel",
                                                         "user", "c") else (segs[-1] if segs else "")
        if c[0] == "facebook" and (_bare_page_id(raw) or "profile.php" in c[1]):
            continue
        if re.match(r"(?i)UC[\w-]{10,}$", raw):
            continue
        h = re.sub(r"[^a-z0-9]", "", _translit(unquote(raw)))
        if h and not any(k in h for k in keys):
            return True
    return False


def _social_keys(words: list[str], domains: set[str], places: set[str] | None = None) -> set[str]:
    """What a site-linked social handle must contain to name us: a distinctive token of the legal name (never a
    trade, industry, form, place or person-name word: 'radio' is shared by Radio Sandefjord and Radio Tønsberg),
    the whole name run together, or the site's domain label when that label is not itself only such a word."""
    pl = set(places or ()) | REGIONS
    keys = set(social_tokens(words, pl))
    keys.add(re.sub(r"[^a-z0-9]", "", _translit("".join(words))))
    for d in domains:
        lab = re.sub(r"[^a-z0-9]", "", (d or "").split(".")[0])
        if lab and not generic_word(lab) and lab not in pl and lab not in GENERIC_WORDS:
            keys.add(lab)
    return {k for k in keys if len(k) >= 4}


def collect_socials(pages: list[Page], words: list[str], domains: set[str], scope: Scope | None = None,
                    other_names: list[str] | None = None,
                    places: set[str] | None = None) -> tuple[list[tuple[str, str, Page, str]], list[str]]:
    """Social profile links from the verified site's pages and JSON-LD sameAs.

    A link is kept when its handle carries the company's name or domain; or, for handle-less ids
    (profile.php?id=, numeric LinkedIn ids, YouTube channel ids) and icon links with no other text, when
    it is the only link for that platform and sits in the site's header, footer or structured data.
    Links to partners, clients, venues and sister companies are dropped."""
    scope = scope or Scope("", words)
    other_names = list(other_names or []) + list(scope.sisters) + list(scope.dominant) + list(scope.codeclared.values())
    found: dict[str, dict[str, list]] = {}
    dropped: list[str] = []
    ours_sq = _squash(" ".join(words))
    for p in pages:
        links = list(p.soup.find_all("a", href=True))
        # Facebook page-plugin iframes and data-href embeds count as links from the site
        for el in p.soup.find_all(attrs={"data-href": True}):
            links.append(el)
        for fr in p.soup.find_all("iframe", src=True):
            q = dict(parse_qsl(urlsplit(fr["src"]).query))
            if "facebook.com/plugins" in fr["src"] and q.get("href"):
                fr["data-href"] = q["href"]
                links.append(fr)
        for a in links:
            written = (a.get("href") if a.name == "a" else a.get("data-href")) or ""
            raw = unwrap_redirect(written)
            c = clean_social(raw)
            if not c:
                continue
            if not scope.accept(a, p):
                dropped.append(c[1])
                continue
            label = a.get_text(" ", strip=True)
            iconic = not label or bool(re.fullmatch(r"(?i)(f[øo]lg oss( på)?|follow us( on)?)?\s*(facebook|instagram|"
                                                    r"linkedin|youtube|twitter|x|tiktok)?", label))
            rec = found.setdefault(c[0], {}).setdefault(c[1], [p, written[:300], False, False])
            rec[2] = rec[2] or _in_chrome(a) or a.name != "a"
            rec[3] = rec[3] or iconic or a.name != "a"
        for d in p.jsonld:
            same = d.get("sameAs")
            if scope.shared:
                nm = d.get("legalName") if isinstance(d.get("legalName"), str) else d.get("name")
                if not isinstance(nm, str) or not ours_sq or _squash(nm) not in (ours_sq,):
                    continue  # on a shared site, only our own Organization record counts
            for s in (same if isinstance(same, list) else [same] if same else []):
                if isinstance(s, str):
                    c = clean_social(s)
                    if c and not (scope.sisters and scope.names_sister(s)):
                        found.setdefault(c[0], {}).setdefault(c[1], [p, s[:300], True, True])
    keys = _social_keys(words, domains, places)
    out = []
    for plat in sorted(found):
        urls = found[plat]
        kept = []
        for u in sorted(urls):
            p, s, chrome, iconic = urls[u]
            path = urlsplit(u).path
            segs = [x for x in path.split("/") if x]
            raw_handle = segs[1] if len(segs) >= 2 and segs[0] in ("people", "p") else (segs[-1] if segs else "")
            handle = re.sub(r"[^a-z0-9]", "", _translit(unquote(raw_handle)))
            if person_handle(handle, words, set(places or ()) | REGIONS) or _names_other_company(handle, words, other_names, scope.group_root) or any(
                    re.sub(r"[^a-z0-9]", "", b) in handle for b in BUILDER_BRANDS if not any(
                        re.sub(r"[^a-z0-9]", "", b) in k for k in keys)):
                dropped.append(u)  # another company's, or the site builder's or web agency's own profile
                continue
            bare_id = plat == "facebook" and bool(segs) and segs[0] not in ("p", "people", "pages", "profile.php") \
                and bool(re.fullmatch(r"(?:[^/]+-)?\d{6,20}", unquote(segs[0])))
            id_only = handle.isdigit() or bool(re.match(r"/channel/UC", path)) or "profile.php" in u or \
                (len(segs) >= 2 and segs[0] in ("people", "p")) or bare_id
            named = any(k in handle for k in keys)
            # like the publication gate: the handle carries our name or domain, or it is a numeric profile id
            # linked from the site's header, footer or structured data as the only one for its platform
            ok = named or (id_only and chrome and len(urls) == 1)
            if not named and bare_id:
                # a bare Facebook page id (facebook.com/1000..., facebook.com/Name-1000...) names nobody: keep it
                # only as the site's single Facebook link, as an icon or in the page chrome, on a page that prints
                # our org number, beside no profile of another name (a group's or agency's link block)
                ok = (_bare_page_id(segs[0]) and len(urls) == 1 and (chrome or iconic) and bool(scope.org)
                      and bool(find_orgnr(p, scope.org)) and not _mixed_block(p, keys))
            if ok:
                kept.append((not named, u, (plat, u, p, s)))
            else:
                dropped.append(u)
        hs = [re.sub(r"[^a-z0-9_.\-]", "", _translit(unquote([x for x in urlsplit(u).path.split("/") if x][-1])))
              if [x for x in urlsplit(u).path.split("/") if x] else "" for _n, u, _r in kept]
        regional = [u for (_n, u, _r), h in zip(kept, hs) if h and regional_variant(h, hs)]
        dropped += regional
        kept = [x for x in kept if x[1] not in regional]
        out += [x[2] for x in sorted(kept)[:2]]  # at most two per platform, name-matched handles first
    return out, sorted(dropped)


def norm_phone(raw: str) -> str | None:
    """E.164-style number. A written country code is kept; +47 is added only to bare 8-digit Norwegian numbers."""
    s = unquote(raw).strip()
    s = re.sub(r"(?i)^tel:", "", s).strip()
    intl = s.startswith("+") or s.startswith("00")
    d = re.sub(r"\D", "", s)
    if s.startswith("00"):
        d = d[2:]
    if intl:
        if d.startswith("47"):
            return "+" + d if len(d) == 10 and d[2] in "2345679" else None
        return "+" + d if 7 <= len(d) <= 15 else None
    if len(d) == 8 and d[0] in "2345679":
        return "+47" + d
    return None


def raw_span(page: Page, value: str, fallback: str) -> str:
    """A span that occurs verbatim in the snapshot: the decoded text if it is there, else the raw form of the
    value as the source writes it (entity-encoded mailto, JSON-LD field)."""
    raw = page.snap.content.decode("utf-8", "replace")
    if fallback and fallback in raw:
        return fallback
    if value in raw:
        return value
    enc = re.escape(value).replace("@", "(?:@|&#64;|&#x40;|%40|&commat;)").replace("\\.", "(?:\\.|&#46;|&#x2e;)")
    m = re.search(enc, raw, re.I)
    return m.group(0) if m else fallback


def _element_for(page: Page, needle: str):
    if not needle:
        return None
    return page.soup.find(string=lambda x: bool(x) and needle in x)


def _nth_element_for(page: Page, needle: str, k: int):
    """The text node holding the k-th written occurrence of needle (the last one when there are fewer)."""
    if not needle:
        return None
    nodes = page.soup.find_all(string=lambda x: bool(x) and needle in x)
    seq = [n for n in nodes for _ in range(n.count(needle))]
    return seq[min(k, len(seq) - 1)] if seq else None


LEGAL_PAGE = re.compile(r"(?i)personvern|privacy|cookie|informasjonskapsl|vilk[aå]r|terms|legal|gdpr|"
                        r"salgsbetingelser|kj[oø]psbetingelser|angrerett|apenhetsloven|åpenhetsloven|disclaimer|impressum")


SERVICE_LINE = re.compile(r"(?i)meld skade|skademelding|skadetelefon|skade|claims?|vakttelefon|døgnvakt|vakt|"
                          r"nødnummer|nødtelefon|legevakt|bestilling|booking|support for|kundeservice for|"
                          r"emergency|on-?call|hotline")


def _service_line(page: Page, el, written: str) -> bool:
    """True when the label right before a number names a service or partner line (Meld skade, vakt, ...)."""
    texts = []
    if el is not None:
        node = el if getattr(el, "name", None) else el.parent
        par = node.parent if node is not None else None
        if par is not None:
            t = par.get_text(" ", strip=True)
            i = t.find(written.strip()) if written.strip() in t else t.find(node.get_text(" ", strip=True))
            texts.append(t[max(0, i - 40):i] if i >= 0 else "")
        sib = node.find_previous_sibling() if node is not None else None
        if sib is not None:
            texts.append(sib.get_text(" ", strip=True)[-40:])
    i = page.text.find(written.strip())
    if i >= 0:
        texts.append(page.text[max(0, i - 40):i])
    for t in texts:
        labels = list(re.finditer(r"(?i)(meld skade|skademelding|skadetelefon|skade|claims?|vakttelefon|døgnvakt|vakt|"
                                  r"nødnummer|nødtelefon|legevakt|bestilling|booking|emergency|on-?call|hotline|"
                                  r"kontortelefon|sentralbord|telefon|tlf|kontakt|kundeservice|mobil|phone)", t))
        if labels and SERVICE_LINE.fullmatch(labels[-1].group(0)):
            return True
    return False


CARD_TAGS = ("li", "tr", "td", "p", "address", "div", "section", "article", "figure", "dd", "dl")


def _contact_card(page: Page, el, span: str) -> str:
    """Text of the smallest block around a contact (its line, list item, table row or staff card), at most a few
    hundred characters; without an element, the text around the span."""
    if el is not None:
        node = el if getattr(el, "name", None) else el.parent
        while node is not None and getattr(node, "name", None) not in (None, "body", "html"):
            if node.name in CARD_TAGS:
                txt = node.get_text(" ", strip=True)
                if len(txt) > 400:
                    break
                if len(txt) >= 25 or node.name in ("li", "tr", "address"):
                    return txt
            node = node.parent
    i = page.text.find(span) if span else -1
    return page.text[max(0, i - 120):i + len(span) + 120] if i >= 0 else ""


def _card_names_other_company(card: str, words: list[str]) -> str | None:
    """Another legal name (with its legal form) in a contact's own block ('Nestleder Atico Solør AS' beside a
    phone number): the contact may belong to that company or to a person working there."""
    ours = [_translit(w) for w in words]
    for m in ENTITY_ANY.finditer(card):
        toks = re.split(r"[.!?:;|,()\[\]]\s", m.group(1))[-1].split()
        run: list[str] = []
        for t in reversed(toks):
            if t[:1].isupper() or t[:1].isdigit() or t == "&":
                run.insert(0, t)
            else:
                break
        ws = _twords(" ".join(run).replace("&", " og "))
        while ws and ws[0].isdigit():
            ws.pop(0)
        if not ws:
            continue
        foreign = m.group(2).lower() in FOREIGN_SUFFIX
        # our own name, or ours after a role word ('Daglig leder Fjellbekk Trevare AS'), is no other company
        if not foreign and ours and any(ws[i:] == ours for i in range(len(ws))):
            continue
        return m.group(0).strip()
    return None


def extract_contacts(pages: list[Page], domain: str, registry_phones: set[str] = frozenset(),
                     scope: Scope | None = None, email_domains: set[str] = frozenset(),
                     registry_emails: set[str] = frozenset()) -> tuple[list, list, int]:
    """(emails, phones, how many were dropped as not provably ours) from the verified site's pages.

    Never from privacy, cookie, terms or legal pages. Emails only on the site's own domain or the registry
    email domain. Phones only when they are the registry number or sit in a block that names us."""
    scope = scope or Scope("", [])
    pages = [p for p in pages if not LEGAL_PAGE.search(unquote(urlsplit(p.url).path))]
    email_domains = {bare(d) for d in email_domains if d} | {domain}
    name_key = re.sub(r"[^a-z0-9]", "", _translit("".join(w for w in scope.words if w not in GENERIC_WORDS)))
    emails: dict[str, tuple[Page, str]] = {}
    phones: dict[str, tuple[Page, str]] = {}
    skipped: set[str] = set()

    def put(store: dict, key: str, page: Page, span: str, el) -> None:
        if key in store or key in skipped:
            return
        ok = scope.accept(el, page)
        registry_value = (store is phones and key[-8:] in registry_phones) or (store is emails and key in registry_emails)
        if ok and not registry_value and scope.words:
            other = _card_names_other_company(_contact_card(page, el, span), scope.words)
            if other:
                ok = False  # the contact sits beside another company's name (its staff or its own line)
        if ok and store is phones and scope.org and key[-8:] not in registry_phones:
            ok = el is not None and scope.owner(el) == "ours"  # a block that names us (legal name or org number)
            if ok and _service_line(page, el, span):
                ok = False  # a claims, on-call or partner line is not the company's contact phone
        if ok:
            store[key] = (page, span)
        else:
            skipped.add(key)

    for p in pages:
        for a in p.soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
            addr = unquote(a["href"][7:]).split("?")[0].strip().lower()
            if EMAIL.fullmatch(addr):
                put(emails, addr, p, a["href"][:200], a)
        for m in EMAIL.finditer(p.text):
            addr = m.group(0).lower().rstrip(".")
            if addr not in emails and addr not in skipped:
                put(emails, addr, p, _window(p.text, m.start(), m.end(), 20), _element_for(p, m.group(0)))
        for a in p.soup.find_all("a", href=re.compile(r"^tel:", re.I)):
            n = norm_phone(a["href"])
            if n:
                put(phones, n, p, a["href"][:100], a)
        for m in PHONE_TEXT.finditer(p.text):
            if not phone_shaped(m.group(0)):
                continue  # a date such as 2024.03.06 is not a phone number
            before = p.text[max(0, m.start() - 40):m.start()]
            if re.search(r"(\+|00)(?!47)\d{1,3}[\s.\-\u00a0]?$", before):
                continue  # the rest of a foreign number written with its country code
            n = norm_phone(m.group(0))
            labels = re.findall(r"(?i)(?<![a-zæøå])(?:tlf|telefon|phone|mobil|mob|tel|ring oss|sentralbord|faks|fax|"
                                r"telefaks)(?![a-zæøå])", before)
            if labels and re.fullmatch(r"(?i)faks|fax|telefaks", labels[-1]):
                continue  # the nearest label says this is a fax number
            if n and PHONE_LABEL.search(before) and n not in phones and n not in skipped:
                put(phones, n, p, m.group(0).strip(), _element_for(p, m.group(0).strip()))
    # only addresses on the company's own domain; generic mailboxes first
    def ours_mail(a: str) -> bool:
        local, dom = a.rsplit("@", 1)
        if any(same_site(dom, d) for d in email_domains):
            return True
        # a free-mail box (birifotklinikk@gmail.com) counts when its mailbox name is our distinctive name
        lq = re.sub(r"[^a-z0-9]", "", _translit(local))
        site_key = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
        return dom in FREE_MAIL and ((bool(name_key) and len(name_key) >= 6 and name_key in lq)
                                     or (len(site_key) >= 6 and lq == site_key))
    own = [(a, ps) for a, ps in emails.items() if ours_mail(a)]
    generic = [x for x in own if x[0].split("@")[0] in GENERIC_MAILBOX]
    # personal mailboxes only when the site publishes no generic one, and then just one
    picked = generic[:2] if generic else own[:1]
    em = [(a, p, raw_span(p, a, s)) for a, (p, s) in picked]
    order = sorted(phones.items(), key=lambda x: x[0][-8:] not in registry_phones)
    ph = [(n, p, s) for n, (p, s) in order][:2]
    return em, ph, len(skipped)


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _translit(" ".join(name_words(s))))


def org_structured(page: Page, legal_name: str) -> tuple[dict, str] | None:
    """The site's own Organization record, only when its name agrees with our legal name."""
    ours = _squash(legal_name)
    for d in page.jsonld:
        t = d.get("@type")
        ts = t if isinstance(t, list) else [t]
        if not any(isinstance(x, str) and ORG_TYPES.search(x) for x in ts):
            continue
        if not isinstance(d.get("name"), str) or not ours:
            continue
        theirs = _squash(d.get("legalName") if isinstance(d.get("legalName"), str) else d["name"])
        if not theirs or not (theirs in ours or ours in theirs):
            continue
        val = {"type": t, "name": d.get("name")}
        for k in ("legalName", "url", "telephone", "email", "taxID", "vatID", "sameAs", "address", "logo"):
            if d.get(k):
                val[k] = d[k] if k != "logo" else (d[k].get("url") if isinstance(d[k], dict) else d[k])
        return val, json.dumps({k: d[k] for k in ("@type", "name") if k in d}, ensure_ascii=False)[:300]
    return None


JOBS_TOKENS = {"stilling", "stillinger", "karriere", "jobb", "jobbe", "jobs", "job", "career", "careers",
               "vacancies", "vacancy", "rekruttering", "rekrutering", "stillingsannonser", "stillingsannonse",
               "jobbmuligheter"}
JOIN_PATH = re.compile(r"(?i)(^|[/\-_])(join-?us|join-our-team|work-with-us|bli-en-av-oss|bli-med-oss)([/\-_.]|$)")
NOT_JOBS_PATH = re.compile(r"/(nyheter|nyhet|news|aktuelt|blogg?|artikler|presse|tag|kategori|category)/", re.I)


def jobs_url(path: str) -> bool:
    """A jobs page by its URL path: a whole path token must be a jobs word (not 'utstillingsareal')."""
    path = unquote(path).lower()
    if NOT_JOBS_PATH.search(path + "/"):
        return False
    return bool(JOIN_PATH.search(path)) or any(t in JOBS_TOKENS for t in re.split(r"[/\-_.]+", _translit(path)) if t)


def careers_link(pages: list[Page], domain: str, scope: Scope | None = None) -> tuple[str, Page, str] | str | None:
    """A jobs page on the verified domain, judged by its URL path. Returns 'ambiguous' when the only one
    found is on a shared site and cannot be tied to our company."""
    scope = scope or Scope("", [])
    unsure = False
    for p in pages:
        for a in p.soup.find_all("a", href=True):
            u = urljoin(p.url, a["href"].strip()).split("#")[0]
            pu = urlsplit(u)
            if pu.scheme not in ("http", "https") or not same_site(host_of(u), domain):
                continue
            if pu.path in ("", "/") or not jobs_url(pu.path) or NOT_CAREERS.search(unquote(pu.path)):
                continue
            if re.search(r"\.(pdf|jpe?g|png|docx?)$", pu.path, re.I):
                continue
            if not scope.accept(a, p):
                unsure = True
                continue
            label = a.get_text(" ", strip=True)[:80]
            return u, p, f'<a href="{a["href"][:200]}">{label}</a>' if label else a["href"][:200]
    return "ambiguous" if unsure else None


def parse_sitemap(content: bytes) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(urls with lastmod, child sitemaps with lastmod)."""
    soup = BeautifulSoup(content, "xml")
    urls, subs = [], []
    for u in soup.find_all("url"):
        loc, lm = u.find("loc"), u.find("lastmod")
        if loc and lm and loc.text.strip() and lm.text.strip():
            urls.append((loc.text.strip(), lm.text.strip()))
    for s in soup.find_all("sitemap"):
        loc, lm = s.find("loc"), s.find("lastmod")
        if loc and loc.text.strip():
            subs.append((loc.text.strip(), lm.text.strip() if lm else ""))
    return urls, subs


def _valid(y: int, m: int, d: int) -> str | None:
    try:
        x = date(y, m, d)
    except ValueError:
        return None
    if x > datetime.now(timezone.utc).date() or x.year < 1995:
        return None
    return x.isoformat()


def _date(s: str) -> str | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s.strip())
    return _valid(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


MONTHS = {"jan": 1, "januar": 1, "january": 1, "feb": 2, "februar": 2, "february": 2, "mar": 3, "mars": 3,
          "march": 3, "apr": 4, "april": 4, "mai": 5, "may": 5, "jun": 6, "juni": 6, "june": 6, "jul": 7,
          "juli": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10,
          "oktober": 10, "oct": 10, "october": 10, "nov": 11, "november": 11, "des": 12, "desember": 12,
          "dec": 12, "december": 12}
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_PATTERNS = (
    (re.compile(r"(?<!\d)(20\d\d|19\d\d)-(\d\d)-(\d\d)(?!\d)"), lambda m: (m[1], m[2], m[3])),
    (re.compile(r"(?<![\d.])(\d{1,2})\.\s?(\d{1,2})\.\s?(20\d\d|19\d\d)(?!\d)"), lambda m: (m[3], m[2], m[1])),
    (re.compile(r"(?<![\d.])(20\d\d|19\d\d)\.(\d{2})\.(\d{2})(?![\d.])"), lambda m: (m[1], m[2], m[3])),
    (re.compile(rf"(?<!\d)(\d{{1,2}})\.?\s+({_MON})\.?\s+(20\d\d|19\d\d)(?!\d)", re.I),
     lambda m: (m[3], MONTHS[m[2].lower()], m[1])),
    (re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}}),?\s+(20\d\d|19\d\d)(?!\d)", re.I),
     lambda m: (m[3], MONTHS[m[1].lower()], m[2])),
)


SLASH_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(20\d\d|19\d\d)(?![\d/])")


def slash_order(page: "Page") -> str | None:
    """How a page writes slash dates: 'dmy' (Norwegian 24/8/2026) or 'mdy' (US 8/24/2026). Decided from the
    page's own unambiguous dates (a part above 12); when none is, a Norwegian-language page means 'dmy'.
    Mixed or unknown: None, and no slash date on the page is read."""
    first = second = False
    for m in SLASH_DATE.finditer(page.text):
        a, b = int(m.group(1)), int(m.group(2))
        first |= a > 12
        second |= b > 12
    if first and second:
        return None
    if first:
        return "dmy"
    if second:
        return "mdy"
    html_el = page.soup.find("html")
    lang = (html_el.get("lang") if html_el else "") or ""
    return "dmy" if re.match(r"(?i)(nb|nn|no)\b", lang) else None


def find_date(block, slash: str | None = None) -> tuple[str, str] | None:
    """(ISO date, the literal text it came from) from a news item block; never guessed. Slash dates are read
    only in the order the page was shown to use (`slash`, from slash_order)."""
    t = block.find("time") if hasattr(block, "find") else None
    if t is not None and t.get("datetime"):
        d = _date(t["datetime"])
        if d:
            return d, t["datetime"]
    # Only a short, standalone date line counts (e.g. "20.01.2026" or "Publisert 8. januar 2026"), never a
    # date inside a sentence, which is usually an event date rather than the publication date.
    patterns = list(DATE_PATTERNS)
    if slash == "dmy":
        patterns.append((SLASH_DATE, lambda m: (m[3], m[2], m[1])))
    elif slash == "mdy":
        patterns.append((SLASH_DATE, lambda m: (m[3], m[1], m[2])))
    for node in block.find_all(string=True):
        text = re.sub(r"\s+", " ", str(node)).strip()
        if not text or len(text) > 40:
            continue
        for rx, parts in patterns:
            m = rx.search(text)
            if m and len(re.sub(r"(?i)publisert|published|posted|oppdatert|updated|dato|date|[:|\-·•,\s]", "",
                                rx.sub("", text, count=1))) <= 12:
                y, mo, dd = parts(m)
                d = _valid(int(y), int(mo), int(dd))
                if d:
                    return d, m.group(0)
    return None


NEWS_PATH = re.compile(r"/(nyheter|nyhet|news|aktuelt|blogg?|artikler|presse|nytt|siste-nytt|arkiv)(/|$)", re.I)


def news_items(page: Page, domain: str, scope: Scope, limit: int = 5) -> list[tuple[dict, str]]:
    """Dated posts on a news or blog listing page: ({title, url, date}, span)."""
    listing = urlsplit(page.url).path.rstrip("/")
    slash = slash_order(page)
    items: dict[str, tuple[dict, str]] = {}
    for a in page.soup.find_all("a", href=True):
        if _in_chrome(a):
            continue
        u = urljoin(page.url, a["href"].strip()).split("#")[0].split("?")[0]
        pu = urlsplit(u)
        if not same_site(host_of(u), domain) or pu.path.rstrip("/") in ("", listing) or u in items:
            continue
        if NEWS_PATH.fullmatch(pu.path.rstrip("/") + "/") or re.search(r"/(page|side|tag|kategori|category)/", pu.path):
            continue
        # The item block is the nearest element holding the link and a date: the link itself first (a card
        # whose <a> wraps heading and date), then up to five ancestors. A block that also holds another
        # post's card (a link of its own with a heading or a date) is a list of items: the date and title
        # there may belong to the neighbour, so the item is dropped rather than stitched together.
        block, found = a, None
        for level in range(6):
            if level:
                if block.parent is None:
                    break
                block = block.parent
            txt = block.get_text(" ", strip=True)
            links = {urljoin(page.url, x["href"]).split("#")[0].split("?")[0] for x in block.find_all("a", href=True)}
            if len(txt) > 700 or len({x for x in links if same_site(host_of(x), domain)} - {u}) > 1 \
                    or _holds_other_item(page, block, u, domain, slash):
                found = None
                break  # the block holds more than one item: a date here may belong to another post
            found = find_date(block, slash)
            if found:
                break
        if not found:
            continue
        same = [x for x in page.soup.find_all("a", href=True)
                if urljoin(page.url, x["href"].strip()).split("#")[0].split("?")[0] == u]
        if any(x is not block and block not in x.parents for x in same):
            continue  # linked from several items: a category or tag, not a post
        head = block.find(re.compile(r"^h[1-5]$"))
        anchors = ([block] if block.name == "a" else []) + \
            [x for x in block.find_all("a", href=True) if same_site(host_of(urljoin(page.url, x["href"])), domain)]
        main = (head.find("a", href=True) if head else None) or max(anchors, key=lambda x: len(x.get_text(strip=True)))
        if urljoin(page.url, main["href"].strip()).split("#")[0].split("?")[0] != u:
            continue  # a tag, category or author link inside someone else's item block
        title = _clean((head.get_text(" ", strip=True) if head else "") or a.get_text(" ", strip=True), 200)
        if not title or len(title) < 8 or re.fullmatch(r"(?i)(les mer|read more|mer|more|→|»)", title):
            continue
        if scope.shared and not (scope.url_ours(u) or scope.accept(block, page)):
            continue
        span = _clean(block.get_text(" ", strip=True), 300)
        full = _iso_with_offset(found[1]) if re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", found[1]) else None
        when = full if full and full[:10] == found[0] and re.search(r"([+-]\d{2}:\d{2})$", full) else found[0]
        items[u] = ({"title": title, "url": u, "date": when, "date_text": found[1]}, span or found[1])
    ranked = sorted(items.values(), key=lambda x: x[0]["date"], reverse=True)
    return ranked[:limit]


def _holds_other_item(page: Page, block, u: str, domain: str, slash: str | None) -> bool:
    """True when `block` contains a link to another page of the site that heads an item of its own: the link
    wraps a heading or a date, or sits in a heading. Then the block is a list, and its first heading or date
    may not be ours."""
    heading = re.compile(r"^h[1-6]$")
    for x in block.find_all("a", href=True):
        v = urljoin(page.url, x["href"].strip()).split("#")[0].split("?")[0]
        if v == u or not same_site(host_of(v), domain):
            continue
        if x.find(heading) is not None or x.find("time") is not None or find_date(x, slash) \
                or x.find_parent(heading) is not None:
            return True
    return False


def news_listing_link(pages: list[Page], domain: str) -> str | None:
    for p in pages:
        for a in p.soup.find_all("a", href=True):
            u = urljoin(p.url, a["href"].strip()).split("#")[0].split("?")[0]
            if same_site(host_of(u), domain) and NEWS_PATH.search(urlsplit(u).path + "/") \
                    and urlsplit(u).path.strip("/").count("/") == 0:
                return u
    return None


NEWS_TEXT = re.compile(r"(?i)^(siste |våre |vare |our |latest |alle |all )?(nyheter|nyhet|news|aktuelt|blogg?|artikler|"
                       r"articles|presse(rom|meldinger)?|press|nyhetsarkiv|fagartikler|fagnotater|notes|innsikt|insights|"
                       r"siste nytt|nytt|news ?(&|og|and) ?(media|presse|press)|"
                       r"nyheter ?(&|og) ?(presse|media|aktuelt))$")
NEWS_SEG = re.compile(r"(?i)^(nyheter|nyhet|news|aktuelt|blogg?|artikler|articles|presse|presserom|pressemeldinger|"
                      r"nytt|siste-nytt|nyhetsarkiv|fagartikler|fagnotater|notes|innsikt|insights|our-news|latest-news|"
                      r"nyheter-og-(presse|media)|news-and-(media|press))(\.(html?|php|aspx?))?$")
FILE_EXT = re.compile(r"(?i)\.(pdf|jpe?g|png|gif|webp|docx?|xlsx?|pptx?|zip|mp4|mp3)$")


def news_listings(pages: list[Page], domain: str) -> list[str]:
    """Candidate news listing pages on the verified site, in a fixed order: a one-segment news path (as
    /nyheter/), then a link whose text is a news word (Nyheter, Aktuelt, Blogg, Our News) at any depth or on a
    subdomain of the verified domain (blog.example.no), then a deeper path ending in a news word (/no/kunnskap/
    nyheter, /nyheter.html). Never the front page of the verified host, a category, a tag or a file."""
    home = pages[0]
    first = news_listing_link(pages, domain)
    by_text: list[str] = []
    by_seg: list[str] = []
    for p in pages:
        for a in p.soup.find_all("a", href=True):
            u = urljoin(p.url, a["href"].strip()).split("#")[0].split("?")[0]
            pu = urlsplit(u)
            if pu.scheme not in ("http", "https") or not same_site(host_of(u), domain) or FILE_EXT.search(pu.path):
                continue
            if pu.path in ("", "/") and bare(pu.hostname or "") == bare(home.host):
                continue
            if re.search(r"(?i)/(tag|tags|kategori|category|categories|author|forfatter|page|side)/", pu.path + "/"):
                continue
            label = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip(" >»→›")
            seg = unquote(pu.path.rstrip("/").rsplit("/", 1)[-1])
            if label and len(label) <= 30 and NEWS_TEXT.match(label):
                by_text.append(u)
            elif seg and NEWS_SEG.match(seg):
                by_seg.append(u)
    out: list[str] = []
    for u in ([first] if first else []) + by_text + by_seg:
        if u not in out:
            out.append(u)
    return out


ARTICLE_TYPES = {"article", "blogposting", "newsarticle", "report", "techarticle", "scholarlyarticle", "pressrelease",
                 "analysisnewsarticle", "opinionnewsarticle", "reportagenewsarticle", "socialmediaposting",
                 "liveblogposting", "posting"}
PUB_LABEL = re.compile(r"(?i)publisert|skrevet|posted|published|\bdato\b|\bdate\b")


def _ld_types(d: dict) -> set[str]:
    t = d.get("@type")
    return {str(x).lower() for x in (t if isinstance(t, list) else [t]) if x}


def article_published(page: Page, loose: bool) -> tuple[str, str] | None:
    """The publication date of one article page, from what the page itself declares: meta
    article:published_time, or datePublished of a JSON-LD Article (BlogPosting, NewsArticle and the like).
    With `loose` (the page sits under the news listing's own path) also a JSON-LD datePublished of any type, or
    a single standalone date on the page labelled as a publication or byline date ("Publisert 3. mars 2026",
    "18/8/2025 Skrevet av ..."). Returns (iso date or datetime, the exact source text)."""
    tag = page.soup.find("meta", attrs={"property": re.compile(r"^article:published_time$", re.I)})
    if tag and tag.get("content"):
        iso = _iso_with_offset(tag["content"])
        if iso:
            return iso, tag["content"]
    for strict in (True, False):
        if not strict and not loose:
            break
        for d in page.jsonld:
            if isinstance(d.get("datePublished"), str) and (not strict or _ld_types(d) & ARTICLE_TYPES):
                iso = _iso_with_offset(d["datePublished"])
                if iso:
                    return iso, d["datePublished"]
    if not loose:
        return None
    slash = slash_order(page)
    seen: dict[str, tuple[str, Any]] = {}
    for node in page.soup.find_all(string=True):
        if node.parent is None or _in_chrome(node.parent):
            continue
        text = re.sub(r"\s+", " ", str(node)).strip()
        if not text or len(text) > 40:
            continue
        got = find_date(node.parent, slash) if node.parent.name not in ("a",) else None
        if got and got[1] in text:
            seen.setdefault(got[0], (got[1], node))
    if len(seen) != 1:
        return None  # no date, or several (event dates, other posts): never guessed
    day, (raw, node) = next(iter(seen.items()))
    near = node.parent.parent if node.parent.parent is not None else node.parent
    if node.find_parent("time") is None and not (len(near.get_text(" ", strip=True)) <= 200
                                                 and PUB_LABEL.search(near.get_text(" ", strip=True))):
        return None
    return day, raw


def listing_articles(page: Page, domain: str) -> list[tuple[str, str, bool]]:
    """Article links on a news listing that shows no dates: (url, link text, loose) in page order, where
    loose means the article sits under the listing's own path or a news path (e.g. /nyheter/x, /post/x).
    Only links in the page body with a title-like text (or a heading in their item block)."""
    listing = urlsplit(page.url).path.rstrip("/")
    out: dict[str, tuple[str, str, bool]] = {}
    menu = {urljoin(page.url, a["href"].strip()).split("#")[0].split("?")[0].rstrip("/")
            for a in page.soup.find_all("a", href=True) if _in_chrome(a)}
    for a in page.soup.find_all("a", href=True):
        if _in_chrome(a):
            continue
        u = urljoin(page.url, a["href"].strip()).split("#")[0].split("?")[0]
        pu = urlsplit(u)
        path = pu.path.rstrip("/")
        if pu.scheme not in ("http", "https") or not same_site(host_of(u), domain) or path in ("", listing):
            continue
        if FILE_EXT.search(path) or NOT_POST.search(path + "/") or NEWS_SEG.match(path.rsplit("/", 1)[-1]):
            continue
        under = (listing and path.startswith(listing + "/")) or bool(
            re.search(r"(?i)/(post|posts|blog|blogg|nyheter|nyhet|news|aktuelt|artikler|articles|notes|presse)/[^/]+$", path))
        if not under and (bare(pu.hostname or "") != bare(page.host) or u.rstrip("/") in menu):
            continue  # a page the site menu also links to is a section, not a post
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if len(text) < 15:
            block, head = a, None
            for _ in range(3):
                block = block.parent
                if block is None:
                    break
                head = block.find(re.compile(r"^h[1-5]$"))
                if head is not None:
                    break
            text = re.sub(r"\s+", " ", head.get_text(" ", strip=True)) if head is not None else text
        if len(text) < 15 or re.fullmatch(r"(?i)(les mer|read more|mer|more|se alle|vis alle).*", text):
            continue
        if u not in out:
            out[u] = (u, text[:200], under)
    items = list(out.values())
    return items if len(items) >= 2 else []  # a listing lists several posts


def _date_floor(ctx: CompanyContext) -> str:
    """Site activity cannot predate the entity: the earliest of incorporation and registry registration."""
    e = ctx.registry or {}
    ds = [str(e.get(k))[:10] for k in ("stiftelsesdato", "registreringsdatoEnhetsregisteret") if e.get(k)]
    return min(ds) if ds else "0000-00-00"


def _activity_blocked(ctx: CompanyContext, field_name: str) -> bool:
    """True (and the field marked ambiguous) when the verified site is still a theme template."""
    why = ctx.facts.get("website_placeholder")
    if why:
        ctx.mark(field_name, "activity", "ambiguous", note=f"the site contains template placeholder text ({why})")
        return True
    return False


BULK_MIN_URLS = 10      # a day stamped on at least this many URLs ...
BULK_MIN_SHARE = 0.25   # ... that are at least this share of the sitemap is a bulk regeneration
BULK_MANY_URLS = 25     # and 25 or more URLs on one day is one when they are at least a tenth of the sitemap
BULK_MANY_SHARE = 0.1   # (a large active site touching 3-8% of its pages on a busy day is not)
BULK_SECTION_SHARE = 0.5  # a path section mostly stamped on bulk days is platform or chain content
BULK_SITE_SHARE = 0.5     # and a sitemap mostly stamped on bulk days gives no page update at all


def drop_bulk_lastmods(dated: list[tuple[str, str, str]]) -> tuple[list[tuple[str, str, str]], str | None]:
    """Sitemap lastmod values that show a platform, not the company, at work. A day carried by many URLs at
    once (a theme or CMS regeneration, a chain pushing shared pages to every member site) is not a page
    update, and neither is any page in a path section that is mostly stamped on such days (the section is
    shared content: a chain's service or supplier pages, refreshed again on other days by the chain).
    Thresholds from sitemaps seen across 1,000-company runs: chain sites put 52-55% of 300 URLs on one day,
    template refreshes on small sites 70-90%, CMS migrations 25-40% of 80-470 URLs and chain pushes 11-14%
    (25-45 URLs); an active 1,000-page site put at most 8% (84 URLs) on its busiest day, which stays."""
    if len(dated) < BULK_MIN_URLS:
        return dated, None
    days = collections.Counter(d for _, d, _ in dated)
    bulk = {d for d, n in days.items()
            if (n >= BULK_MANY_URLS and n / len(dated) >= BULK_MANY_SHARE)
            or (n >= BULK_MIN_URLS and n / len(dated) >= BULK_MIN_SHARE)}
    if not bulk:
        return dated, None

    def section(u: str) -> str:
        parts = [x for x in urlsplit(u).path.split("/") if x]
        return parts[0].lower() if len(parts) > 1 else ""  # top-level pages are judged one by one

    per: dict[str, list[int]] = {}
    for loc, d, _ in dated:
        per.setdefault(section(loc), [0, 0])
        per[section(loc)][0] += 1
        per[section(loc)][1] += d in bulk
    shared = {sec for sec, (n, b) in per.items() if sec and n >= 5 and b / n >= BULK_SECTION_SHARE}
    n_bulk = sum(days[d] for d in bulk)
    if n_bulk / len(dated) >= BULK_SITE_SHARE:
        kept = []  # most of the sitemap is stamped in bulk: its lastmod values are the platform's, page by page too
    else:
        kept = [x for x in dated if x[1] not in bulk and section(x[0]) not in shared
                and urlsplit(x[0]).path.strip("/").lower() not in shared]  # a shared section's own index page
    note = (f"{n_bulk} of {len(dated)} sitemap URLs share bulk lastmod day(s) {', '.join(sorted(bulk))}"
            + (f"; sections {', '.join('/' + x for x in sorted(shared))} are platform or chain content" if shared else "")
            + "; not used as page updates")
    return kept, note


async def sitemap_activity(f: Fetcher, sp: _Spend, ctx: CompanyContext, home: Page, domain: str,
                           scope: Scope | None = None) -> None:
    scope = scope or Scope("", [])
    p = urlsplit(home.url)
    origin = f"{p.scheme}://{p.netloc}"
    maps: list[str] = []
    rp = f.robots.get(origin)
    if rp is not None:
        try:  # Protego exposes .sitemaps; urllib's RobotFileParser has .site_maps()
            listed = rp.sitemaps if hasattr(rp, "sitemaps") else (rp.site_maps() or [])
            maps = [m for m in listed if same_site(host_of(m), domain)]
        except Exception:
            maps = []
    url = maps[0] if maps else origin + "/sitemap.xml"
    for _ in range(2):
        if not sp.can(1):
            return
        page_snap = None
        try:
            page_snap = await sp.get(url)
        except (BudgetExhausted, _OutOfRequests):
            raise
        except Exception:
            return
        if page_snap.status != 200 or b"<" not in page_snap.content[:200]:
            return
        urls, subs = parse_sitemap(page_snap.content)
        if urls:
            dated = [(loc, d, lm) for loc, lm in urls if (d := _date(lm)) and same_site(host_of(loc), domain)]
            if not dated:
                return
            stamps = {lm for _, _, lm in dated}
            today = datetime.now(timezone.utc).date().isoformat()
            if len(dated) >= 3 and (len(stamps) == 1 or sum(d == today for _, d, _ in dated) > len(dated) / 2):
                ctx.facts["website_sitemap_note"] = "sitemap lastmod looks generated at request time; not used"
                return
            dated = [x for x in dated if not re.search(r"/(tag|tags|kategori|category|author|forfatter)/", x[0], re.I)]
            dated, bulk_note = drop_bulk_lastmods(dated)
            if bulk_note:
                ctx.facts["website_sitemap_bulk"] = bulk_note
            floor = _date_floor(ctx)
            early = [x for x in dated if x[1] < floor]
            if early:
                ctx.facts["website_activity_before_founding"] = len(early)
            stubs = {u.rstrip("/") for u in ctx.facts.get("website_stub_posts") or []}
            dated = [x for x in dated if x[1] >= floor and not PLACEHOLDER_SLUG.search(x[0]) and not is_template_url(x[0])
                     and x[0].rstrip("/") not in stubs]
            if not dated:
                return
            if _activity_blocked(ctx, "site_page_updated"):
                return
            ours = [x for x in dated if scope.url_ours(x[0])]
            if scope.shared and not ours:
                ctx.mark("site_page_updated", "activity", "ambiguous",
                         note=f"{scope.describe()}; no sitemap page can be tied to this company")
                return
            ours.sort(key=lambda x: x[2], reverse=True)
            for loc, d, lm in ours[:5]:
                ev = ctx.add_evidence(page_snap, source_class=COMPANY_OWNED, span=f"<loc>{loc}</loc><lastmod>{lm}</lastmod>",
                                      effective_date=d, method="website_sitemap_v1", licence=LICENCE)
                ctx.add_claim("site_page_updated", "activity", {"url": loc, "lastmod": lm}, ev, as_of=d,
                              match={"method": "verified_site_sitemap", "detail": f"sitemap of verified site {domain}"})
            return
        if subs:
            subs = [s for s in subs if same_site(host_of(s[0]), domain)]
            if not subs:
                return
            subs.sort(key=lambda s: s[1], reverse=True)
            # prefer a posts/news sitemap when one is present
            news = [s for s in subs if re.search(r"post|news|nyhet|artik|blog", s[0], re.I)]
            url = (news or subs)[0][0]
            continue
        return


async def prepare_scope(sp: _Spend, ctx: CompanyContext, v: Verdict) -> Scope:
    """Fetch a contact page if none was read yet, then decide whether the site is shared."""
    home = v.pages[0]
    if not (v.cross or v.path_scoped) and not any(INTERNAL_PRIORITY[0].search(urlsplit(p.url).path) for p in v.pages):
        for link in internal_links(home):
            if INTERNAL_PRIORITY[0].search(link) or INTERNAL_PRIORITY[1].search(link):
                if sp.can(1) and _page_key(link) not in {_page_key(p.url) for p in v.pages}:
                    p, _st = await _site_page(sp, v, link)
                    if p and same_site(p.host, home.host):
                        v.pages.append(p)
                break
    if not (v.cross or v.path_scoped):
        priv = next((u for u in internal_links(home) if re.search(r"(?i)personvern|privacy", u)), None)
        if priv and sp.can(1) and _page_key(priv) not in {_page_key(p.url) for p in v.pages}:
            p, _st = await _site_page(sp, v, priv)
            if p and same_site(p.host, home.host):
                v.pages.append(p)  # read for operator and controller names; never a contact source
    words = name_words((ctx.registry or {}).get("navn") or "")
    others = dict(v.others)
    for p in v.pages:
        for num, s in other_orgnrs(p, ctx.org).items():
            others.setdefault(num, s)
    others = {k: (x[1] if isinstance(x, tuple) else x) for k, x in others.items()}
    sisters = await confirm_sisters(sp, ctx, sister_labels(v.pages, words))
    kinds: dict[str, str] = {}
    named = [n for n in other_company_names(v.pages, words) if n[0] not in sisters][:6]
    dominant = await confirm_dominant(sp, ctx, named, limit=3, site_host=home.host, kinds=kinds)
    positional_names = {f"{n[0]} {n[1]}" for n in named if n[2]}
    if v.namesake:
        dominant[v.namesake[0].lower()] = v.namesake[1]
        kinds[v.namesake[0].lower()] = "namesake"
    reg_mail = ((ctx.registry or {}).get("epostadresse") or "@").rsplit("@", 1)[1].lower()
    own_tokens = [t for t in _core_tokens((ctx.registry or {}).get("navn") or "")
                  if len(t) >= 4 and t not in GENERIC_WORDS and t not in REGIONS]
    other_mail: set[str] = set()
    for p in v.pages:
        if LEGAL_PAGE.search(unquote(urlsplit(p.url).path)):
            continue
        for a in p.soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
            dom = unquote(a["href"][7:]).split("?")[0].strip().lower().rsplit("@", 1)[-1]
            if not re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}", dom) or dom in FREE_MAIL or same_site(dom, home.host) \
                    or (reg_mail and same_site(dom, reg_mail)) or service_mail_domain(dom):
                continue
            label = re.sub(r"[^a-z0-9]", "", dom.split(".")[-2] if dom.count(".") >= 1 else dom)
            site_label = re.sub(r"[^a-z0-9]", "", bare(home.host).split(".")[0])
            if label == site_label or any(t in label for t in own_tokens):
                continue  # the company's own alternate domain
            if _initials_label(label, (ctx.registry or {}).get("navn") or "") and \
                    not await _declared_by_other(sp, ctx, dom):
                continue  # our name's initials ('laa.no' for Larhammer Aarseth Advokatfirma), declared by nobody else
            other_mail.add(dom)
    if other_mail:
        # a contact at our own registry group's domain (a parent's press desk on a brand site) is the group's,
        # not another organisation's: the domain label is a distinctive word of a group company's name
        group_tokens = {t for n in await _group_names(sp, ctx) for t in _core_tokens(n)
                        if len(t) >= 5 and t not in GENERIC_WORDS and t not in REGIONS}
        other_mail = {d for d in other_mail
                      if re.sub(r"[^a-z0-9]", "", d.split(".")[-2] if "." in d else d) not in group_tokens}
    codeclared = await _codeclared(sp, ctx, home)
    if codeclared and v.method == "orgnr_on_page" and not others and _orgnr_in_operator_position(v, ctx.org):
        alltext = " ".join(p.text for p in v.pages)
        if not any((rx := _full_name_rx(n)) and rx.search(alltext) for n in codeclared.values()):
            codeclared = {}  # our org number in an operator position, and the other declarant is never named
    reg_host = bare(host_of((ctx.registry or {}).get("hjemmeside") or ""))
    own_proof = (not (v.cross or v.path_scoped)) and (
        _orgnr_in_operator_position(v, ctx.org) or (reg_host and same_site(bare(home.host), reg_host)))
    blockers = others or dominant or sisters or codeclared or other_mail
    group = await _group_members(sp, ctx) if (blockers and own_proof) else set()
    if group:
        # We are the group's parent, on our own registry-declared or org-number-proven domain: our subsidiaries,
        # our staff bodies (pension fund, sports and staff clubs) and group mailboxes on other domains do not take
        # the site away from us. They do not get it either: a subsidiary still sees a related website.
        def ours_or_staff(org: str, name: str) -> bool:
            return org in group or bool(STAFF_BODIES.search(name or ""))
        others = {k: x for k, x in others.items() if k not in group}
        sisters = {k: x for k, x in sisters.items() if not ours_or_staff(str(x), k)}
        dominant = {k: x for k, x in dominant.items() if not ours_or_staff(str(x), k)
                    and kinds.get(k) != "mention"}  # partners merely mentioned on the parent's own site
        codeclared = {k: x for k, x in codeclared.items() if not ours_or_staff(k, x)}
        other_mail = set()
        ctx.facts["website_group_parent"] = sorted(group)[:20]
    if own_proof and (dominant or sisters or others) and (ctx.registry or {}).get("erIKonsern"):
        # Our own registry group's companies (parent, sisters, subsidiaries) named on a site that is ours by
        # registry declaration or by our org number do not take it away from us; a group company stated as the
        # site's operator still does unless our org number sits in an operator position. Unrelated companies,
        # org numbers in operator positions and co-declarations are judged as before.
        fam = await _group_orgs(sp, ctx)
        op_ours = _orgnr_in_operator_position(v, ctx.org)
        if fam:
            def in_family(k: str, x) -> bool:
                return str(x) in fam and (kinds.get(k) != "stated" or op_ours)
            gone = sorted({str(x) for k, x in dominant.items() if in_family(k, x)}
                          | {str(x) for k, x in sisters.items() if str(x) in fam}
                          | {k for k in others if k in fam and not _num_in_operator_position(v, k)})
            dominant = {k: x for k, x in dominant.items() if not in_family(k, x)}
            sisters = {k: x for k, x in sisters.items() if str(x) not in fam}
            others = {k: x for k, x in others.items() if not (k in fam and not _num_in_operator_position(v, k))}
            if gone:
                ctx.facts["website_group_mentions"] = gone[:20]
    if codeclared and v.source == "registry" and not (v.cross or v.path_scoped) and \
            reg_host and same_site(bare(home.host), reg_host):
        codeclared = await _family_codeclared(sp, ctx, codeclared, home.host)
    if others and _orgnr_in_operator_position(v, ctx.org) and all(not re.fullmatch(r"\d{9}", k) for k in others):
        # only foreign VAT or company numbers (a foreign parent or group) beside our own operator-position org
        # number: the site stays ours, but nothing is derived from it
        for k in list(others):
            dominant[f"foreign group number {k}"] = "foreign"
            kinds[f"foreign group number {k}"] = "mention"
        others = {}
    ours_printed = False
    if not (v.cross or v.path_scoped) and _orgnr_in_operator_position(v, ctx.org):
        # Our own org number in an operator position makes the site ours, unless another Norwegian org number
        # is also in an operator position or another company is the stated operator. Other mentions,
        # namesakes and group members elsewhere do not override it; they only stop facts being derived.
        op_others = {k for k in others if re.fullmatch(r"\d{9}", k) and _num_in_operator_position(v, k)}
        full = re.sub(r"[^a-z0-9]", "", _translit("".join(w for w in words if w not in GENERIC_WORDS)))
        if len(full) >= 6 and full in re.sub(r"[^a-z0-9]", "", bare(home.host).split(".")[0]):
            # on a domain named after us, a registry-confirmed sister's number beside ours is a group footer
            op_others -= {str(x) for x in sisters.values()}
        stated = [k for k in dominant if kinds.get(k) == "stated"]
        if not op_others and not stated and not codeclared:
            ours_printed = True
            for k, x in list(others.items()):
                dominant[f"org number {k}"] = k
                kinds[f"org number {k}"] = "mention"
            for k, x in list(sisters.items()):
                dominant[k] = x
                kinds[k] = "mention"
            for d in sorted(other_mail):
                dominant[f"contact on {d}"] = d
                kinds[f"contact on {d}"] = "mention"
            # companies only named in passing (suppliers, clients, partners, a person's other employer) are not
            # presenters on a site that prints our org number as its operator: they no longer block derived facts
            dominant = {k: x for k, x in dominant.items() if kinds.get(k) != "mention" or k in sisters
                        or k.startswith(("org number ", "contact on ", "foreign group number "))}
            for k in dominant:
                kinds[k] = "mention"
            others, sisters, other_mail = {}, {}, set()
    e = ctx.registry or {}
    a = e.get("forretningsadresse") or {}
    srx = _street_regex(([x for x in a.get("adresse") or [] if x] or [""])[-1]) if a.get("adresse") else None
    addr = (srx, a["postnummer"]) if srx and a.get("postnummer") else None
    sc = Scope(ctx.org, words, others, sisters, dominant, v.cross, v.path_scoped, other_mail, codeclared, addr=addr)
    sc.dominant_kind = kinds
    sc.ours_printed = ours_printed
    sc.handle_namesakes = await _handle_namesakes(sp, ctx, v.pages, words)
    if own_proof and _group_handle_linked(v.pages, words):
        sc.group_root = bool(await _group_members(sp, ctx))  # one cached registry lookup, only when needed
    return sc


HANDLE_FILLER = {"norge", "norway", "official", "offisiell", "offisielt", "hjem", "home", "page", "side"}
HANDLE_LABELS = 3  # registry name checks per company for social handles that extend a word of our name


def _handle_labels(pages: list[Page], words: list[str]) -> list[str]:
    """Registered-name labels that social handles on the site may spell: a word of our name (often a generic one,
    'radio') joined with another word ('radiotonsberg' as 'radio tonsberg'), in page order."""
    ours = [re.sub(r"[^a-z0-9]", "", _translit(w)) for w in words]
    ours = [w for w in ours if len(w) >= 3]
    full = "".join(ours)
    out: list[str] = []
    for p in pages:
        for a in p.soup.find_all("a", href=True):
            c = clean_social(unwrap_redirect(a["href"]))
            if not c:
                continue
            segs = [x for x in urlsplit(c[1]).path.split("/") if x]
            h = re.sub(r"[^a-z0-9]", "", _translit(unquote(segs[-1] if segs else "")))
            if not h or (full and full in h):
                continue
            for w in ours:
                rest, lab = "", ""
                if h.startswith(w):
                    rest = h[len(w):]
                    lab = f"{w} {rest}"
                elif h.endswith(w):
                    rest = h[:-len(w)]
                    lab = f"{rest} {w}"
                if len(rest) >= 4 and not rest.isdigit() and rest not in ours and rest not in HANDLE_FILLER \
                        and lab not in out:
                    out.append(lab)
    return out


async def _handle_namesakes(sp: "_Spend", ctx: CompanyContext, pages: list[Page], words: list[str]) -> list[str]:
    """Other registered companies whose names social handles on our site spell ('radio tonsberg' for RADIO
    TØNSBERG AS on Radio Sandefjord's site): such a profile is theirs, not ours."""
    labels = _handle_labels(pages, words)[:HANDLE_LABELS]
    if not labels:
        return []
    got = await confirm_sisters(sp, ctx, labels, limit=HANDLE_LABELS)
    return [lab for lab in labels if lab in got]


def _group_handle_linked(pages: list[Page], words: list[str]) -> bool:
    """A social link whose handle joins our distinctive word with a group or holding word ('kvistgroup')."""
    lead = next((_translit(w) for w in words if len(w) >= 3 and w not in GENERIC_WORDS), "")
    if not lead:
        return False
    for p in pages:
        for a in p.soup.find_all("a", href=True):
            c = clean_social(unwrap_redirect(a["href"]))
            h = re.sub(r"[^a-z0-9]", "", _translit(unquote(urlsplit(c[1]).path))) if c else ""
            if lead in h and any(g in h for g in GROUP_WORDS):
                return True
    return False


def _num_in_operator_position(v: Verdict, num: str) -> bool:
    """Another org number printed in the footer, header, contact or imprint block, or on an operator page."""
    g = f"{num[:3]}[\\s.\u00a0]?{num[3:6]}[\\s.\u00a0]?{num[6:]}"
    for p in v.pages:
        if not re.search(g, p.text):
            continue
        path = unquote(urlsplit(p.url).path)
        if OPERATOR_PAGE.search(path) or re.search(r"(?i)personvern|privacy", path) or re.search(g, _operator_text(p)):
            return True
    return False


def _orgnr_in_operator_position(v: Verdict, org: str) -> bool:
    """Our org number sits in the footer, header, contact or imprint block, or on a contact, about or privacy page."""
    for p in v.pages:
        if not find_orgnr(p, org):
            continue
        path = unquote(urlsplit(p.url).path)
        if OPERATOR_PAGE.search(path) or re.search(r"(?i)personvern|privacy", path):
            return True
        g = f"{org[:3]}[\\s.\u00a0]?{org[3:6]}[\\s.\u00a0]?{org[6:]}"
        if re.search(g, _operator_text(p)):
            return True
    return False


_GROUP_CACHE: dict[str, set[str]] = {}


async def _group_members(sp: "_Spend", ctx: CompanyContext) -> set[str]:
    """Org numbers below us in the registry group structure, when we are the group's top parent (one Brreg
    request, cached). Empty when we are not the root."""
    if ctx.org in _GROUP_CACHE:
        return _GROUP_CACHE[ctx.org]
    members: set[str] = set()
    if sp.can_registry():
        try:
            snap = await sp.get(f"https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{ctx.org}", robots=False,
                                headers={"Accept": "application/json"})
            tree = snap.json() if snap.status == 200 else {}
        except (BudgetExhausted, _OutOfRequests):
            raise
        except Exception:
            tree = {}
        if tree.get("organisasjonsnummer") == ctx.org:
            stack = list(tree.get("children") or [])
            while stack:
                n = stack.pop()
                if n.get("organisasjonsnummer"):
                    members.add(n["organisasjonsnummer"])
                stack.extend(n.get("children") or [])
    _GROUP_CACHE[ctx.org] = members
    return members


_HJEM_CACHE: dict[str, list[dict]] = {}
_GROUP_TREE: dict[str, list[str]] = {}


async def _group_names(sp: "_Spend", ctx: CompanyContext) -> list[str]:
    """Names of every company in our registry group (root and all members), when the registry says we are in
    one (one cached request). Used to recognise a parent's or sister's mail domain as the group's own."""
    if not (ctx.registry or {}).get("erIKonsern"):
        return []
    if ctx.org in _GROUP_TREE:
        return _GROUP_TREE[ctx.org]
    names: list[str] = []
    data = await _search_json(sp, f"{BRREG_API}/konsernstruktur/{ctx.org}")
    stack = [data]
    while stack:
        n = stack.pop()
        if n.get("navn"):
            names.append(n["navn"])
        stack.extend(n.get("children") or [])
    _GROUP_TREE[ctx.org] = sorted(set(names))
    return _GROUP_TREE[ctx.org]


def _initials_label(label: str, name: str) -> bool:
    """The domain label is the initials of the legal name's words, three letters or more ('laa' for LARHAMMER
    AARSETH ADVOKATFIRMA AS), with or without the legal form's letter."""
    ws = [w for w in name_words(name) if w and w[0].isalnum()]
    if len(ws) < 3:
        return False
    ini = "".join(_translit(w)[0] for w in ws if _translit(w))
    return len(ini) >= 3 and label in (ini, ini + "as")


async def _declared_by_other(sp: "_Spend", ctx: CompanyContext, dom: str) -> bool:
    """Another registered entity declares this domain as its website (one cached registry lookup). A failed
    lookup counts as declared, so the domain stays another organisation's."""
    host = bare(dom)
    rows = _HJEM_CACHE.get(host)
    if rows is None:
        if not sp.can_registry():
            return True
        try:
            snap = await sp.get(f"https://data.brreg.no/enhetsregisteret/api/enheter?hjemmeside={host}&size=20",
                                robots=False, headers={"Accept": "application/json"})
        except (BudgetExhausted, _OutOfRequests):
            raise
        except Exception:
            return True
        if snap.status != 200:
            return True
        rows = (snap.json().get("_embedded") or {}).get("enheter") or []
        _HJEM_CACHE[host] = rows
    return any(bare(host_of(r.get("hjemmeside") or "")) == host and r.get("organisasjonsnummer") != ctx.org
               for r in rows)


_GROUP_ORGS: dict[str, set[str]] = {}


async def _group_orgs(sp: "_Spend", ctx: CompanyContext) -> set[str]:
    """Org numbers of every company in our registry group (root and all members), from the cached
    konsernstruktur response; empty when the registry has no group for us."""
    if ctx.org in _GROUP_ORGS:
        return _GROUP_ORGS[ctx.org]
    out: set[str] = set()
    if (ctx.registry or {}).get("erIKonsern"):
        data = await _search_json(sp, f"{BRREG_API}/konsernstruktur/{ctx.org}")
        stack = [data]
        while stack:
            n = stack.pop()
            if n.get("organisasjonsnummer"):
                out.add(n["organisasjonsnummer"])
            stack.extend(n.get("children") or [])
    out.discard(ctx.org)
    _GROUP_ORGS[ctx.org] = out
    return out


FOREIGN_UNIT_FORMS = {"NUF", "UTLA"}


async def _family_codeclared(sp: "_Spend", ctx: CompanyContext, codeclared: dict[str, str],
                             host: str) -> dict[str, str]:
    """Co-declarers of our registry-declared website that do not stop us keeping it as the group's top parent.
    When the registry makes us the root of a group (we have members, nobody above us) and another declarer of
    the site is one of our group companies, the site is the group's and ours as its parent: declarers carrying
    our distinctive name word that are foreign units (NUF, UTLA: a foreign group company's registration) or
    Norwegian companies in no group at all (so parent of nobody) are the group's too. Returns the declarers
    that still count: any other company, and every declarer when no group company declares the site."""
    members = await _group_members(sp, ctx)
    rows = {r.get("organisasjonsnummer"): r for r in _HJEM_CACHE.get(bare(host)) or []}
    if not members or not any(o in members and bare(host_of(r.get("hjemmeside") or "")) == bare(host)
                              for o, r in rows.items()):
        return codeclared
    ours = {t for t in _core_tokens((ctx.registry or {}).get("navn") or "") if len(t) >= 4 and t not in GENERIC_WORDS}
    left = {}
    for org, name in codeclared.items():
        r = rows.get(org) or {}
        form = ((r.get("organisasjonsform") or {}).get("kode") or "").upper()
        family = bool(ours and _core_tokens(name) & ours)
        if family and (form in FOREIGN_UNIT_FORMS or r.get("erIKonsern") is False):
            continue
        left[org] = name
    if len(left) < len(codeclared):
        ctx.facts["website_group_codeclared"] = sorted(set(codeclared) - set(left))[:20]
    return left


async def _codeclared(sp: "_Spend", ctx: CompanyContext, home: Page) -> dict[str, str]:
    """Other active registered entities that declare this same website in the registry (a group site)."""
    host = bare(home.host)
    rows = _HJEM_CACHE.get(host)
    if rows is None:
        rows = []
        if sp.can_registry():
            try:
                snap = await sp.get(f"https://data.brreg.no/enhetsregisteret/api/enheter?hjemmeside={host}&size=20",
                                    robots=False, headers={"Accept": "application/json"})
                if snap.status == 200:
                    rows = (snap.json().get("_embedded") or {}).get("enheter") or []
            except (BudgetExhausted, _OutOfRequests):
                raise
            except Exception:
                rows = []
        _HJEM_CACHE[host] = rows
    out = {}
    for r in rows:
        hj = bare(host_of(r.get("hjemmeside") or ""))
        if hj == host and r.get("organisasjonsnummer") != ctx.org and _active(r):
            out[r["organisasjonsnummer"]] = r.get("navn") or ""
    return out


DERIVED_FIELDS = (("website_description", SECTION), ("site_contact_email", SECTION), ("site_contact_phone", SECTION),
                  ("social_profile", SECTION), ("structured_data_organization", SECTION), ("hiring_signal", "hiring"),
                  ("site_page_updated", "activity"), ("dated_news", "activity"))


def emit_site(ctx: CompanyContext, v: Verdict, scope: Scope | None = None) -> list[Page]:
    home = v.pages[0]
    domain = bare(home.host)
    scope = scope or Scope(ctx.org, name_words((ctx.registry or {}).get("navn") or ""))
    url = canonical_url(home)
    proof_evs = [ctx.add_evidence(p.snap, source_class=COMPANY_OWNED, span=bytes_exact(p.snap, s, core or ctx.org),
                                  method=METHOD, licence=LICENCE, core=core)
                 for p, s, core in v.proof]
    conf = 0.99 if v.method == "orgnr_on_page" else 0.9
    m = {"method": v.method, "detail": v.detail or ""}
    if scope.shared:
        path = urlsplit(home.url).path
        if path not in ("", "/") and scope.slug and scope.slug in _translit(unquote(path)).replace("_", "-"):
            url = home.url.split("?")[0].split("#")[0]  # our own section of the shared site
        m["detail"] = f"{m['detail']}; {scope.describe()}"
        ctx.facts["website_shared"] = {"others": sorted(scope.others), "sisters": scope.sisters}
    if v.cross or v.path_scoped:
        url = home.url.split("#")[0]  # the exact page that proves us, not the host's front page
    # A company's own domain stays official only when other companies are merely mentioned there (clients,
    # partners, projects, staff clubs). An operator, a namesake, contacts on another organisation's domain,
    # a redirect, a section page, a co-declaring company or another org number make it a related website.
    own_domain_only = (not RELATED_STRICT and not (v.cross or v.path_scoped or scope.others or scope.codeclared
                                                   or scope.sisters or scope.other_mail)
                       and all(scope.dominant_kind.get(k) == "mention" for k in scope.dominant)) or scope.ours_printed
    if not scope.exclusive and own_domain_only:
        # the company's own domain, non-exclusive only because other companies are named on it: the site is the
        # company's, but nothing is derived from it
        m["detail"] = f"{m['detail']}; not exclusive: {scope.reasons()}"
        m["url"] = url
        ctx.add_claim("official_website", SECTION, site_domain(home.host), proof_evs, confidence=conf, match=m,
                      note="Only the website itself is published; facts from this site are withheld because "
                           + scope.reasons())
        for fld, sec in DERIVED_FIELDS:
            ctx.mark(fld, sec, "ambiguous", note="site not exclusive to this company (" + scope.reasons() + ")")
        return v.pages
    if not scope.exclusive:
        # A shared, chain, portal or parent site is labelled as related, not collapsed into the company:
        # the proven page goes in related_website, and official_website is ambiguous with the reason.
        others = sorted({k for k in scope.others if re.fullmatch(r"\d{9}", k)}
                        | {x for x in scope.sisters.values() if re.fullmatch(r"\d{9}", str(x))}
                        | {x for x in scope.dominant.values() if re.fullmatch(r"\d{9}", str(x))}
                        | set(scope.codeclared))
        m["detail"] = f"{m['detail']}; not exclusive: {scope.reasons()}"
        m["url"] = url
        ctx.add_claim("related_website", SECTION,
                      {"url": url, "domain": site_domain(home.host),
                       "relationship": "page on a shared or group site", "other_organisation_numbers": others},
                      proof_evs, confidence=conf, match=m,
                      note="The page is proven to name this company, but the site also serves others: " + scope.reasons())
        ctx.mark("official_website", SECTION, "ambiguous",
                 note="only a page on a shared, chain, portal or group site was found (" + scope.reasons()
                      + "); see related_website")
        why = "site not exclusive to this company (" + scope.reasons() + "); derived facts withheld"
        for fld, sec in DERIVED_FIELDS:
            ctx.mark(fld, sec, "ambiguous", note=why)
        return v.pages
    m["url"] = url
    ctx.add_claim("official_website", SECTION, site_domain(home.host), proof_evs, confidence=conf, match=m)
    ctx.add_claim("official_website_url", SECTION, url, proof_evs, confidence=conf, match=dict(m))
    site_match = {"method": "verified_site", "detail": f"from {domain}, proven by {v.method}"}
    for p in v.pages:
        ph = PLACEHOLDER.search(p.text)
        if ph and re.search(r"(?i)lorem ipsum|dolor sit amet|legg inn en|gi en kort|skriv en kort|eksempelside|sample page",
                            ph.group(0)):
            ctx.facts["website_placeholder"] = ph.group(0)
            break
    unsure = f"{scope.describe()}; not provably this company's" if scope.shared else ""

    if home.soup.title and home.soup.title.get_text(strip=True):
        t = _clean(home.soup.title.get_text(" ", strip=True), 200)
        ctx.add_claim("website_title", SECTION, t, _ev(ctx, home, t), match=site_match)
    desc, span = _meta(home, "description", "og:description")
    if not desc:
        for d in home.jsonld:
            if isinstance(d.get("description"), str) and ORG_TYPES.search(str(d.get("@type"))):
                desc = _clean(d["description"])
                span = desc
                break
    words_in = len(re.findall(r"[\wæøåÆØÅ]+", desc or ""))
    names_us = bool(desc) and _names_in(desc, [scope.words]) if scope.words else False
    if desc and not GENERIC_DESC.search(desc) and not PLACEHOLDER.search(desc) and len(desc) >= 15 and (
            words_in >= 5 or names_us):  # a project or page name is not a self-description
        if scope.shared and not scope.page_ours_only(home) and not (
                scope.names_ours(desc) and not scope.names_sister(desc)):
            ctx.mark("website_description", SECTION, "ambiguous", note=unsure)
        else:
            ctx.add_claim("website_description", SECTION, desc, _ev(ctx, home, span), match=site_match)
    lang = home.soup.find("html")
    if lang and lang.get("lang"):
        ctx.add_claim("site_language", SECTION, lang["lang"].strip()[:12],
                      _ev(ctx, home, f'lang="{lang["lang"].strip()[:12]}"'), match=site_match)

    reg_mail = ((ctx.registry or {}).get("epostadresse") or "@").rsplit("@", 1)[1].lower()
    reg_email = ((ctx.registry or {}).get("epostadresse") or "").strip().lower()
    emails, phones, skipped = extract_contacts(v.pages, domain, _digits_phones(ctx.registry or {}), scope,
                                               {reg_mail} if reg_mail and reg_mail not in FREE_MAIL else set(),
                                               {reg_email} if "@" in reg_email else set())
    for a, p, s in emails:
        ctx.add_claim("site_contact_email", SECTION, a, _ev(ctx, p, s), match=site_match)
    for n, p, s in phones:
        ctx.add_claim("site_contact_phone", SECTION, n, _ev(ctx, p, _span_with_value(p, s, n)), match=site_match)
    if scope.shared and skipped:
        if not emails:
            ctx.mark("site_contact_email", SECTION, "ambiguous", note=unsure)
        if not phones:
            ctx.mark("site_contact_phone", SECTION, "ambiguous", note=unsure)

    words = scope.words
    reg = ctx.registry or {}
    doms = {domain, host_of(reg.get("hjemmeside") or ""), (reg.get("epostadresse") or "@").rsplit("@", 1)[1]}
    doms = {bare(d) for d in doms if d and d not in FREE_MAIL and not is_platform(d)}
    socials, dropped = collect_socials(v.pages, words, doms, scope, list(scope.handle_namesakes),
                                       places=_places(ctx.registry or {}))
    if dropped:
        ctx.facts["website_social_dropped"] = dropped
    for plat, u, p, s in socials:
        ctx.add_claim("social_profile", SECTION, canonical_social(plat, u), _ev(ctx, p, s),
                      match={"method": "linked_from_verified_site", "detail": f"outbound link {u} on {p.url}"})
    if scope.shared and not socials and dropped:
        ctx.mark("social_profile", SECTION, "ambiguous", note=unsure)
    for p in v.pages:
        so = org_structured(p, (ctx.registry or {}).get("navn") or "")
        if so:
            ctx.add_claim("structured_data_organization", SECTION, so[0], _ev(ctx, p, so[1]), match=site_match)
            break
    return v.pages


# ---------------------------------------------------------------- canonical values (Builderr's shapes)
SOCIAL_HOSTS = {"linkedin": "linkedin.com", "facebook": "facebook.com", "instagram": "instagram.com",
                "youtube": "youtube.com", "x": "x.com", "tiktok": "tiktok.com", "pinterest": "pinterest.com"}


class CanonicalSocial(str):
    """A social profile as 'platform.com/handle' (lower case, no scheme, no www). `.get('platform')` and
    `.get('url')` still answer for readers that expect the older dict shape; it serialises as a plain string."""

    def __new__(cls, value: str, platform: str, url: str):
        obj = str.__new__(cls, value)
        obj.platform, obj.url = platform, url
        return obj

    def get(self, key: str, default=None):
        return {"platform": self.platform, "url": self.url}.get(key, default)

    def __getitem__(self, key):
        if isinstance(key, str):  # dict-style access for older readers; string slicing still works
            return {"platform": self.platform, "url": self.url}[key]
        return str.__getitem__(self, key)

    def __reduce__(self):
        return (CanonicalSocial, (str(self), self.platform, self.url))

    def __deepcopy__(self, memo):
        return self


def canonical_social(platform: str, url: str) -> CanonicalSocial:
    p = urlsplit(url)
    value = (SOCIAL_HOSTS[platform] + unquote(p.path).rstrip("/")).lower()  # 'Rørlegger', not 'R%C3%B8rlegger'
    ids = [v for k, v in parse_qsl(p.query) if k == "id" and v.isdigit()]
    if p.path.endswith("profile.php") and ids:
        value = f"facebook.com/profile.php?id={ids[0]}"
    return CanonicalSocial(value, platform, url)


def site_domain(host: str) -> str:
    """The site's registered domain as Builderr writes it (no scheme, no www); a hosted subdomain such as
    name.barnehage.no is kept whole."""
    return bare(host.lower())


# ---------------------------------------------------------------- dated news
FEED_TYPES = re.compile(r"(?i)application/(rss|atom)\+xml")
NEWS_PROBES = ("/nyheter/", "/aktuelt/", "/news/", "/presse/", "/blogg/")
NOT_POST = re.compile(r"(?i)/(tag|tags|kategori|category|categories|author|forfatter|page|side|feed|comments?)/")
SAMPLE_POST = re.compile(r"(?i)^(hello,? world!?|hei,? verden!?|tere-tere!?|sample page|eksempelside|lorem ipsum.*)$")
SPAM = re.compile(r"(?i)\b(replica|birkin|aliexpress|designer reproduction|casino|viagra|cialis|payday|escort|betting|"
                  r"porn|crypto ?currency|bitcoin|loan[s]? online|cheap jerseys|louis vuitton|rolex|essay writing)\b")


def _iso_with_offset(raw: str) -> str | None:
    """The source's own datetime as ISO 8601 with its offset (RFC 822 feed dates converted, offsets kept)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}", raw):
            # fractions of a second (up to nanoseconds from some CMSs) are dropped: the value is to the second
            d = datetime.fromisoformat(re.sub(r"(T\d{2}:\d{2}:\d{2})\.\d+", r"\1", raw).replace("Z", "+00:00"))
        else:
            from email.utils import parsedate_to_datetime
            d = parsedate_to_datetime(raw)
    except (ValueError, TypeError, IndexError):
        return None
    if d.tzinfo is None:
        return d.date().isoformat() if len(raw) <= 10 else d.replace(microsecond=0).isoformat()
    return d.replace(microsecond=0).isoformat()


def _cdata(s: str) -> str:
    """An XML element's content as markup: CDATA unwrapped, or entity-escaped HTML unescaped once."""
    import html as _html
    m = re.fullmatch(r"\s*<!\[CDATA\[(.*?)\]\]>\s*", s, re.S)
    return m.group(1) if m else _html.unescape(s)


def _xml_text(s: str) -> str:
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    import html as _html
    return re.sub(r"\s+", " ", _html.unescape(s)).strip()


def parse_feed(raw: str) -> list[dict]:
    """Items of an RSS or Atom feed: title, url, datetime, and the exact raw title and date elements."""
    out = []
    for blk in re.finditer(r"<(item|entry)\b.*?</\1>", raw, re.S | re.I):
        b = blk.group(0)
        t = re.search(r"<title\b[^>]*>(.*?)</title>", b, re.S | re.I)
        if not t:
            continue
        link = re.search(r"<link\b[^>]*>(.*?)</link>", b, re.S | re.I)
        url = _xml_text(link.group(1)) if link and link.group(1).strip() else None
        if not url:
            for lk in re.finditer(r"<link\b([^>]*)/?>", b, re.I):
                attrs = lk.group(1)
                if re.search(r'rel=["\'](alternate)["\']', attrs) or "rel=" not in attrs:
                    h = re.search(r'href=["\']([^"\']+)["\']', attrs)
                    if h:
                        url = h.group(1)
                        break
        dm = None
        for tag in ("pubDate", "published", "dc:date", "updated"):
            dm = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", b, re.S | re.I)
            if dm:
                break
        if not dm or not url:
            continue
        when = _iso_with_offset(_xml_text(dm.group(1)))
        if not when:
            continue
        bm = re.search(r"<(content:encoded|description|summary|content)\b[^>]*>(.*?)</\1>", b, re.S | re.I)
        out.append({"title": _xml_text(t.group(1)), "url": url.strip(), "datetime": when,
                    "title_span": t.group(0), "date_span": dm.group(0),
                    "body_words": body_word_count(_cdata(bm.group(2)), url.strip()) if bm else None,
                    "text": _plain(_cdata(bm.group(2))) if bm else ""})
    return out


def _offset(local: str, gmt: str) -> str | None:
    """The site's UTC offset from WordPress's local `date` and `date_gmt`, as +HH:MM."""
    try:
        diff = datetime.fromisoformat(local) - datetime.fromisoformat(gmt)
    except ValueError:
        return None
    mins = int(diff.total_seconds() // 60)
    if abs(mins) > 14 * 60:
        return None
    sign = "+" if mins >= 0 else "-"
    return f"{sign}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"


def _raw_field(raw: str, key: str, value: str, start: int = 0) -> tuple[str | None, int]:
    """The exact bytes of a JSON field as served (e.g. '"date":"2026-01-02T10:00:00"'), and its position."""
    for ascii_only in (True, False):
        enc = json.dumps(value, ensure_ascii=ascii_only)[1:-1]
        m = re.compile('"' + re.escape(key) + r'"\s*:\s*"' + re.escape(enc) + '"').search(raw, start)
        if m:
            return m.group(0), m.start()
    return None, start


def _wp_posts(raw: str) -> list[dict]:
    """WordPress REST posts. The datetime is the post's own local time with the site's offset (from `date`
    and `date_gmt`); spans are the raw JSON fields exactly as served."""
    try:
        rows = json.loads(raw)
    except ValueError:
        return []
    out = []
    pos = 0
    for r in rows if isinstance(rows, list) else []:
        rendered = ((r.get("title") or {}).get("rendered")) or ""
        title = _xml_text(rendered)
        local, gmt = r.get("date"), r.get("date_gmt")
        if not (title and r.get("link") and (local or gmt)):
            continue
        off = _offset(local, gmt) if local and gmt else None
        when = f"{local}{off}" if local and off else (f"{gmt}+00:00" if gmt else None)
        if not when:
            continue
        tspan, tpos = _raw_field(raw, "rendered", rendered, pos)
        dspan, dpos = _raw_field(raw, "date" if local and off else "date_gmt", local if local and off else gmt, pos)
        pos = max(pos, tpos, dpos)
        body = (r.get("excerpt") or {}).get("rendered") if isinstance(r.get("excerpt"), dict) else None
        out.append({"title": title, "url": r["link"], "datetime": when, "title_span": tspan, "date_span": dspan,
                    "body_words": body_word_count(body, r["link"]), "text": _plain(body)})
    return out


def article_datetime(page: Page) -> tuple[str, str] | None:
    """Publication datetime from an article page: meta article:published_time, JSON-LD datePublished or
    time[datetime]. Returns (iso datetime, the exact source text)."""
    tag = page.soup.find("meta", attrs={"property": re.compile(r"^article:published_time$", re.I)})
    if tag and tag.get("content"):
        iso = _iso_with_offset(tag["content"])
        if iso:
            return iso, tag["content"]
    for d in page.jsonld:
        if isinstance(d.get("datePublished"), str):
            iso = _iso_with_offset(d["datePublished"])
            if iso:
                return iso, d["datePublished"]
    t = page.soup.find("time", attrs={"datetime": True})
    if t:
        iso = _iso_with_offset(t["datetime"])
        if iso:
            return iso, t["datetime"]
    return None


def inline_news(page: Page) -> list[dict]:
    """Dated items listed on a page without links of their own: a short standalone date next to a heading
    (e.g. <p class="datetime">2024.07.04</p><h5>title</h5>). The item URL is the page with a fragment."""
    out, seen = [], set()
    slash = slash_order(page)
    for node in page.soup.find_all(string=True):
        text = re.sub(r"\s+", " ", str(node)).strip()
        if not text or len(text) > 40 or not (any(rx.search(text) for rx, _ in DATE_PATTERNS)
                                              or (slash and SLASH_DATE.search(text))):
            continue
        block = node.parent.parent if node.parent is not None else None
        if block is None or len(block.get_text(" ", strip=True)) > 1500:
            continue
        heads = block.find_all(re.compile(r"^h[2-6]$"))
        if len(heads) != 1:
            continue
        found = find_date(node.parent, slash)
        title = _clean(heads[0].get_text(" ", strip=True), 200)
        if not found or not title or len(title) < 8 or (title, found[0]) in seen:
            continue
        seen.add((title, found[0]))
        slug = re.sub(r"[^a-z0-9]+", "-", _translit(title)).strip("-")[:60]
        out.append({"title": title, "url": page.url.split("#")[0] + "#" + slug, "datetime": found[0],
                    "title_span": title, "date_span": found[1], "inline": True})
    return out


def _verbatim_core(raw: str, text: str) -> str | None:
    """The longest phone-number or email-like core of a text window that occurs verbatim."""
    for m in sorted(re.findall(r"[\w.+\-]+@[\w.\-]+\.[a-z]{2,}|\+?[\d][\d \u00a0.\-]{6,}\d", text), key=len, reverse=True):
        v = verbatim(raw, m.strip())
        if v:
            return v
    return None


def verbatim(raw: str, text: str | None) -> str | None:
    """`text` as it occurs in the raw response: itself, its HTML-escaped forms, or the raw <title> element of a
    feed. None when no exact substring exists (the span must be copied from the saved bytes)."""
    if not text:
        return None
    if text in raw:
        return text
    import html as _html
    cands = {_html.escape(text, quote=False), _html.escape(text, quote=True),
             text.replace("–", "&#8211;").replace("\u2014", "&#8212;").replace("’", "&#8217;").replace("&", "&amp;"),
             _html.escape(text, quote=False).replace("–", "&#8211;").replace("’", "&#8217;").replace("“", "&#8220;")
             .replace("”", "&#8221;").replace("…", "&#8230;")}
    named = text.replace("&", "&amp;").replace("–", "&ndash;").replace("\u2014", "&mdash;").replace("’", "&rsquo;") \
        .replace("‘", "&lsquo;").replace("“", "&ldquo;").replace("”", "&rdquo;").replace("«", "&laquo;") \
        .replace("»", "&raquo;").replace("…", "&hellip;").replace("\u00a0", "&nbsp;")
    cands |= {named, text.replace("/", "\\/"), json.dumps(text)[1:-1], json.dumps(text, ensure_ascii=False)[1:-1]}
    for c in sorted(cands):
        if c and c in raw:
            return c
    # a whitespace-tolerant match of the plain text, allowing HTML entities for punctuation
    alts = {"–": "(?:–|&ndash;|&#8211;|&#x2013;)", "\u2014": "(?:\u2014|&mdash;|&#8212;|&#x2014;)",
            "’": "(?:’|&rsquo;|&#8217;|&#x2019;)", "&": "(?:&|&amp;|&#038;|&#38;)", "…": "(?:…|&hellip;|&#8230;)",
            "“": "(?:“|&ldquo;|&#8220;)", "”": "(?:”|&rdquo;|&#8221;)", "«": "(?:«|&laquo;)", "»": "(?:»|&raquo;)"}
    parts = ["".join(alts.get(ch, re.escape(ch)) for ch in w) for w in text.split()]
    if parts:
        m = re.search(r"\s+".join(parts), raw)
        if m:
            return m.group(0)
    # any non-ASCII letter or punctuation written as a numeric (&#248; &#xF8;) or named (&oslash;) entity
    parts = ["".join(_entity_alt(ch) for ch in w) for w in text.split()]
    if parts:
        m = re.search(r"\s+".join(parts), raw)
        if m:
            return m.group(0)
    return None


def _entity_alt(ch: str) -> str:
    if ch.isascii() and ch.isalnum():
        return re.escape(ch)
    from html.entities import codepoint2name
    opts = [re.escape(ch), f"&#0*{ord(ch)};", f"&#[xX]0*(?i:{ord(ch):x});"]
    if ord(ch) in codepoint2name:
        opts.append(f"&{codepoint2name[ord(ch)]};")
    return "(?:" + "|".join(opts) + ")"


DEMO_TITLES = re.compile(r"(?i)^(hello,? world!?|hei,? verden!?|build dream home|construction company|powerful modern idea|"
                         r"sample post|sample page|blog post title|my first blog post|test post|testinnlegg|"
                         r"eksempelinnlegg|post format.*|markup: .*|template: .*|lorem ipsum.*|"
                         r"(the )?(best|top) \d+ .*(theme|template)s?|welcome to (my|our) (new )?(blog|website)|"
                         r"just another .*|minimalist design|modern architecture|interior design ideas|"
                         r"creative (agency|design)|business (consulting|growth|strategy)|digital marketing tips|"
                         r"new project launch|our new office|project management|how to start a business|"
                         r"tips for (a|your) .*|blog post \d+|news \d+|nyhet \d+|post \d+)$")


def drop_demo(items: list[dict]) -> list[dict]:
    """Theme demo content is not company news: drop known demo titles; if two or more demo titles appear the
    whole list is a theme import and nothing is kept; a cluster of three or more of the site's oldest posts
    published within ten minutes is an import too."""
    demo = [it for it in items if DEMO_TITLES.match(it["title"].strip())]
    if len(demo) >= 2:
        return []
    items = [it for it in items if it not in demo]
    asc = sorted(items, key=lambda it: it["datetime"][:19])
    if len(asc) >= 3:
        def t(it):
            try:
                return datetime.fromisoformat(it["datetime"][:19])
            except ValueError:
                return None
        first = t(asc[0])
        cluster = [it for it in asc if first and t(it) and (t(it) - first).total_seconds() <= 600]
        if len(cluster) >= 3 and len(cluster) == len(asc):
            return []  # every post was created within minutes: a demo import
        if len(cluster) >= 3:
            items = [it for it in items if it not in cluster]
    return items


WEEKDAYS = {"mandag": 0, "tirsdag": 1, "onsdag": 2, "torsdag": 3, "fredag": 4, "lørdag": 5, "lordag": 5,
            "søndag": 6, "sondag": 6, "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
            "saturday": 5, "sunday": 6}
WEEKDAY_DATE = re.compile(r"(?i)\b(" + "|".join(WEEKDAYS) + r")\s+(?:den\s+)?(\d{1,2})\.?\s*(" + _MON +
                          r")\.?(?:\s+(20\d\d))?(?![\w])")
YEAR_WORD = re.compile(r"(?<![\d/.\-])(20\d\d)(?![\d/.\-])")


def _plain(html_text: str | None) -> str:
    import html as _html
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", html_text or "")).split())[:2000]


def date_contradicted(item: dict, today: str) -> str | None:
    """Why a post's publish date cannot be its real date, or None. A reused post (an annual meeting notice
    edited every year) keeps its first publish date: its slug, title or text then names a year two or more
    years after the publish year that has already come, or a weekday and date that fall on that weekday in
    none of the year before, the publish year and the year after. Precision first: such an item is withheld, never re-dated."""
    from datetime import date as _date
    pub = int(item["datetime"][:4])
    slug = unquote(urlsplit(item["url"]).path)
    hay = " ".join((item.get("title") or "", item.get("text") or ""))
    own = slug.replace("-", " ").replace("/", " ").replace("_", " ") + " " + (item.get("title") or "")
    body = item.get("text") or ""
    # a year two or more years after the publish year (next year's plans, 'Påsken 2025' written in 2024, are common)
    for y in [int(x) for x in YEAR_WORD.findall(own + " " + body)]:
        if pub + 1 < y <= int(today[:4]):
            return f"names the year {y}, after its publish date"
    for m in WEEKDAY_DATE.finditer(hay):
        wd, d, mon = WEEKDAYS[m.group(1).lower()], int(m.group(2)), MONTHS.get(m.group(3).lower().rstrip("."))
        if not mon:
            continue
        years = [int(m.group(4))] if m.group(4) else [pub - 1, pub, pub + 1]  # a slip of one year is tolerated
        ok = False
        for y in years:
            try:
                ok = ok or _date(y, mon, d).weekday() == wd
            except ValueError:
                pass
        if not ok:
            return f"'{m.group(0)}' does not fall on that weekday in {'/'.join(map(str, years))}"
    return None


def _news_ok(item: dict, floor: str, domain: str) -> bool:
    day = item["datetime"][:10]
    today = datetime.now(timezone.utc).date().isoformat()
    if date_contradicted(item, today):
        return False
    path = urlsplit(item["url"]).path
    if item.get("inline"):
        path = "/#inline"
    elif path in ("", "/") and re.fullmatch(r"p=\d+", urlsplit(item["url"]).query):
        path = "/?p"  # a WordPress post under plain permalinks (/?p=1042)
    return (floor <= day <= today and same_site(host_of(item["url"]), domain) and path not in ("", "/")
            and not NOT_POST.search(path + "/") and not SAMPLE_POST.match(item["title"])
            and not PLACEHOLDER.search(item["title"]) and not PLACEHOLDER_SLUG.search(item["url"])
            and not template_post(item["title"], item["url"]) and len(item["title"]) >= 4)


def _is_wordpress(page: Page) -> bool:
    gen = page.soup.find("meta", attrs={"name": re.compile("^generator$", re.I)})
    return bool((gen and "wordpress" in (gen.get("content") or "").lower()) or b"/wp-content/" in page.snap.content)


def _is_wix(page: Page) -> bool:
    gen = page.soup.find("meta", attrs={"name": re.compile("^generator$", re.I)})
    return bool((gen and "wix" in (gen.get("content") or "").lower()) or b"static.wixstatic.com" in page.snap.content)


async def _undated_listing(sp: "_Spend", page: Page, domain: str, floor: str, found: dict) -> None:
    """A news listing without dates: read its newest article pages (in page order, while the budget allows,
    at most five) and keep those that declare their own publication date."""
    for url, text, loose in listing_articles(page, domain)[:5]:
        if not sp.can(1):
            break
        art, _st = await _fetch_page(sp, url)
        if not art or not same_site(art.host, domain) or urlsplit(art.url).path in ("", "/") \
                or art.url.rstrip("/") == page.url.rstrip("/"):
            continue
        got = article_published(art, loose)
        if not got:
            continue
        h1 = art.soup.find("h1")
        title = _clean(h1.get_text(" ", strip=True), 200) if h1 is not None else None
        if not title or len(title) < 8:
            title = _clean(text, 200)
        it = {"title": title, "url": url, "datetime": got[0], "title_span": title, "date_span": got[1]}
        if title and _news_ok(it, floor, domain) and url not in found:
            found[url] = (it, art.snap, None)


async def collect_dated_news(sp: "_Spend", ctx: CompanyContext, pages: list[Page], domain: str,
                             scope: "Scope", limit: int = 20) -> None:
    """Dated news from the verified site: feeds first (RSS or Atom link, WordPress /feed/ or REST posts,
    Wix /blog-feed.xml), then the news listing (and its page 2). Each item keeps the source's own datetime.
    Value "title (datetime)", newest first, at most 20."""
    home = pages[0]
    origin = f"{urlsplit(home.url).scheme}://{urlsplit(home.url).netloc}"
    floor = _date_floor(ctx)
    found: dict[str, tuple[dict, Snapshot, Page | None]] = {}

    def clean_feed(items: list[dict]) -> list[dict]:
        # a hacked site's feed full of spam is not company news: drop the whole feed
        if any(SPAM.search(it["title"]) for it in items):
            return []
        # bodiless filler posts (a demo product beside "Hello world!"); a stub found in one source (the REST
        # list) stays a stub when a feed lists it again
        stubs = stub_posts(items) | set(ctx.facts.get("website_stub_posts") or [])
        if stubs:
            ctx.facts["website_stub_posts"] = sorted(stubs)
        return [it for it in items if it["url"] not in stubs]

    def keep(items: list[dict], snap: Snapshot) -> None:
        for it in items:
            it["url"] = urljoin(origin + "/", it["url"])
            if _news_ok(it, floor, domain) and it["url"] not in found:
                found[it["url"]] = (it, snap, None)

    if _is_wordpress(home) and sp.can(1):
        try:  # WordPress: the REST list gives the newest 20 posts in one request
            snap = await sp.get(origin + "/wp-json/wp/v2/posts?per_page=20&_fields=title,link,date,date_gmt,excerpt")
            if snap.status == 200:
                rows = _wp_posts(snap.content.decode("utf-8", "replace"))
                keep(clean_feed(rows), snap)
                if len(rows) >= 20 and len(found) < limit and sp.can(1):
                    snap2 = await sp.get(origin + "/wp-json/wp/v2/posts?per_page=20&page=2&_fields=title,link,date,date_gmt,excerpt")
                    if snap2.status == 200:
                        keep(clean_feed(_wp_posts(snap2.content.decode("utf-8", "replace"))), snap2)
        except (BudgetExhausted, _OutOfRequests):
            raise
        except Exception:
            pass
    feeds: list[str] = []
    for p in pages:
        for ln in p.soup.find_all("link", attrs={"type": FEED_TYPES}):
            href = urljoin(p.url, ln.get("href") or "")
            if href and same_site(host_of(href), domain) and "comments" not in href and href not in feeds:
                feeds.append(href)
    if _is_wordpress(home):
        # the main posts feed first; a linked feed may be a single page's (e.g. /testside/feed/) and empty
        feeds = [origin + "/feed/"] + [x for x in feeds if x.rstrip("/") != origin + "/feed"]
    if not feeds and _is_wix(home):
        feeds.append(origin + "/blog-feed.xml")
    ctx.facts["website_dated_news_checked"] = feeds[:2] or ["no feed linked"]
    for feed in ([] if found else feeds[:2]):
        if not sp.can(1):
            break
        try:
            snap = await sp.get(feed)
        except (BudgetExhausted, _OutOfRequests):
            raise
        except Exception:
            continue
        if snap.status == 200 and b"<" in snap.content[:300]:
            items = clean_feed(parse_feed(snap.content.decode("utf-8", "replace")))
            keep(items, snap)
            # a WordPress feed holds 10 posts per page: read page 2 as well
            if items and len(found) < limit and urlsplit(feed).path.rstrip("/").endswith("feed") and sp.can(1):
                try:
                    snap2 = await sp.get(feed + ("&" if "?" in feed else "?") + "paged=2")
                    if snap2.status == 200:
                        keep(clean_feed(parse_feed(snap2.content.decode("utf-8", "replace"))), snap2)
                except (BudgetExhausted, _OutOfRequests):
                    raise
                except Exception:
                    pass
        if found:
            break
    if not found:
        listings = news_listings(pages, domain)
        probes = listings[:2] if listings else [origin + p for p in NEWS_PROBES]
        for url in probes:
            if not sp.can(1):
                break
            page, _st = await _fetch_page(sp, url)
            if not page or not same_site(page.host, domain) or (
                    urlsplit(page.url).path in ("", "/") and bare(page.host) == bare(home.host)):
                continue
            # a blog collection often has its own feed (Squarespace: <collection>?format=rss)
            lfeeds = [urljoin(page.url, ln.get("href") or "") for ln in page.soup.find_all("link", attrs={"type": FEED_TYPES})]
            lfeeds = [u for u in lfeeds if same_site(host_of(u), domain) and "comments" not in u]
            if not lfeeds and b"squarespace" in page.snap.content.lower():
                lfeeds = [page.url.split("?")[0] + "?format=rss"]
            for lf in lfeeds[:1]:
                if not sp.can(1):
                    break
                try:
                    fs = await sp.get(lf)
                    if fs.status == 200:
                        keep(clean_feed(parse_feed(fs.content.decode("utf-8", "replace"))), fs)
                except (BudgetExhausted, _OutOfRequests):
                    raise
                except Exception:
                    pass
            if found:
                break
            listing_pages = [page]
            nxt = page.soup.find("a", attrs={"rel": "next"}, href=True) or page.soup.find(
                "a", href=re.compile(r"(/page/2/?$|[?&]page=2$|/side/2/?$)"))
            if nxt and sp.can(1):
                p2, _ = await _fetch_page(sp, urljoin(page.url, nxt["href"]))
                if p2 and same_site(p2.host, domain):
                    listing_pages.append(p2)
            for lp in listing_pages:
                for val, span in news_items(lp, domain, scope, limit=limit):
                    it = {"title": val["title"], "url": val["url"], "datetime": val["date"],
                          "title_span": span, "date_span": val.get("date_text")}
                    if _news_ok(it, floor, domain) and it["url"] not in found:
                        found[it["url"]] = (it, lp.snap, lp)
            if not found and not scope.shared:
                # dates written beside headings, without links of their own
                for it in inline_news(page):
                    if _news_ok(it, floor, domain) and it["url"] not in found:
                        found[it["url"]] = (it, page.snap, None)
            if not found and not scope.shared:
                await _undated_listing(sp, page, domain, floor, found)
            ctx.facts["website_dated_news_checked"] = (ctx.facts.get("website_dated_news_checked") or [])[:2] + [page.url]
            if found or not listings:
                break
        # listing dates are dates only: read the article page for the full datetime while the budget allows
        for url in sorted(found, key=lambda u: found[u][0]["datetime"], reverse=True)[:4]:
            it, snap, lp = found[url]
            if lp is None or not sp.can(1):
                continue
            art, _ = await _fetch_page(sp, url)
            if art and same_site(art.host, domain):
                got = article_datetime(art)
                if got and got[0][:10] == it["datetime"][:10]:
                    it.update(datetime=got[0], date_span=got[1], title_span=it["title"])
                    found[url] = (it, art.snap, None)
    if not found:
        for p in pages[:1]:  # dated items listed on the front page itself
            for it in inline_news(p):
                if _news_ok(it, floor, domain) and it["url"] not in found:
                    found[it["url"]] = (it, p.snap, None)
    if not found:
        return
    if _activity_blocked(ctx, "dated_news"):
        return
    items = drop_demo([x[0] for x in found.values()])
    if not items:
        ctx.facts["website_news_demo"] = True
        return
    keep_urls = {it["url"] for it in items}
    ranked = sorted((v for v in found.values() if v[0]["url"] in keep_urls),
                    key=lambda x: (x[0]["datetime"][:19], x[0]["title"]), reverse=True)[:limit]
    for it, snap, _lp in ranked:
        raw = snap.content.decode("utf-8", "replace")
        spans = [verbatim(raw, it.get("title_span")) or verbatim(raw, it["title"]),
                 verbatim(raw, it.get("date_span"))]
        spans = [x for x in spans if x]
        if not spans:
            continue  # nothing quotable exactly from the saved response: do not publish
        evs = [ctx.add_evidence(snap, source_class=COMPANY_OWNED, span=bytes_exact(snap, x), effective_date=it["datetime"][:10],
                                method="website_news_v2", licence=LICENCE) for x in spans]
        ctx.add_claim("dated_news", "activity", f"{it['title']} ({it['datetime']})", evs, as_of=it["datetime"][:10],
                      match={"method": "verified_site_news", "detail": f"{it['url']} on the verified site"})


# ---------------------------------------------------------------- hiring signal
JOB_PROBES = ("/karriere/", "/jobb/", "/jobbe-hos-oss/", "/ledige-stillinger/", "/stillinger/", "/careers/", "/jobs/")
JOB_HEADING = re.compile(r"(?i)\b(karriere|ledige stillinger|stillinger|jobb(e)? hos oss|careers?|jobs|vacancies|"
                         r"rekruttering)\b")
ATS_POSTING = re.compile(r"(?i)(webcruiter\.no|jobbnorge\.no|recman\.(no|page)|teamtailor\.com|hrmanager\.no|"
                         r"easycruit\.com|jobylon\.com|reachmee\.com|varbi\.com|smartrecruiters\.com)")


RECRUIT_TEXT = re.compile(r"(?i)ledige stillinger|ledig stilling|vi søker|vi ser etter|jobb hos oss|jobbe hos oss|karriere|"
                          r"\bcareers?\b|vacanc|\bapply\b|søk nå|søk her|søknadsfrist|send (oss )?(en )?søknad|"
                          r"open positions|join (our|the) team|stillingsannonse")
NOT_CAREERS = re.compile(r"(?i)/(jobb-med-meg|cv|om-meg|portfolio|portefolje|min-cv|resume)(/|$)")
PERSONAL_LETTER = re.compile(r"(?i)med vennlig hilsen|mitt cv|my cv|my resume|curriculum vitae")
COMPANY_RECRUITING = re.compile(r"(?i)ledige stillinger|ledig stilling|vi søker|jobbe hos oss|jobb hos oss|\bcareers?\b|"
                                r"vacanc|open positions|join (our|the) team")


RECRUITER_MARKS = re.compile(r"(?i)registrer (din )?cv|cv submission|executive search|headhunt|rekrutteringsbyrå|"
                             r"søker (vi )?(etter )?(\w+ )?for (vår|våre) kunder?|på vegne av (vår|våre) kunde|vår oppdragsgiver|"
                             r"our client|on behalf of (our|a) client|vikarbyrå|bemanningsbyrå|utvalgte kunder")
OWN_HIRING = re.compile(r"(?i)jobb(e)? hos oss|jobbe i |bli en av oss|bli med på laget|ny kollega|nye kollegaer|"
                        r"join (our|the) team|work (with|at) us|vi ansetter|vi har ledig")
FILLED = re.compile(r"(?i)besatt|besatte|avsluttet|filled|closed|utløpt|expired")


def _careers_page_ok(page: Page, domain: str, home: Page, probed: bool, nace: str = "", our_name: str = "") -> bool:
    """A real careers page: on the verified domain, not the front page (or a copy of it served at another
    path), and with a jobs word in its path or heading; a probed page needs the jobs word in its heading."""
    path = urlsplit(page.url).path
    if not same_site(page.host, domain) or path in ("", "/") or is_parked(page):
        return False
    if page.snap.sha256 == home.snap.sha256 or page.text == home.text:
        return False
    if NOT_CAREERS.search(unquote(path)):
        return False  # a person's own CV or portfolio page, not a company hiring
    if PERSONAL_LETTER.search(page.text[:6000]) and not COMPANY_RECRUITING.search(page.text[:8000]):
        return False  # a personal application letter, not a company hiring
    if not RECRUIT_TEXT.search(page.text[:8000]) and not any(str(d.get("@type")) == "JobPosting" for d in page.jsonld):
        return False
    # a recruiter's or staffing firm's list of client searches is not this company hiring
    if nace.startswith("78") or RECRUITER_MARKS.search(page.text[:12000]):
        own = OWN_HIRING.search(page.text[:12000])
        if our_name:
            rx = name_regex(name_words(our_name))
            own = own or (rx and re.search(r"(?i)jobbe (i|hos) ", page.text) and rx.search(page.text))
        if not own:
            return False
    # every listed role filled or closed: nothing open here
    jobs = [a for a in page.soup.find_all("a", href=True)
            if same_site(host_of(urljoin(page.url, a["href"])), domain) and jobs_url(urlsplit(urljoin(page.url, a["href"])).path)
            and urlsplit(urljoin(page.url, a["href"])).path.rstrip("/") != path.rstrip("/")]
    if jobs and all(FILLED.search(a["href"] + " " + a.get_text(" ", strip=True)) for a in jobs):
        return False
    head = " ".join([page.soup.title.get_text(" ", strip=True) if page.soup.title else ""] +
                    [h.get_text(" ", strip=True) for h in page.soup.find_all(["h1", "h2"])[:3]])
    if probed:
        return bool(JOB_HEADING.search(head))
    return jobs_url(path) or bool(JOB_HEADING.search(head))


# Recruitment platforms: a company's own job board there (subdomain, or a company path or id), never the
# platform's front page, login or candidate pages. Linked from the verified exclusive site, it is the company's
# careers page. The platform itself is not fetched.
ATS_BOARDS = ("teamtailor.com", "webcruiter.no", "webcruiter.com", "jobbnorge.no", "recman.no", "recman.page",
              "easycruit.com", "hrmanager.no", "hr-manager.net", "reachmee.com", "varbi.com", "jobylon.com",
              "smartrecruiters.com", "workable.com", "lever.co", "greenhouse.io", "talentech.io", "emply.com",
              "jobteaser.com", "personio.de", "personio.com", "homerun.co", "recruitee.com", "breezy.hr")
ATS_GENERIC_SUB = {"www", "app", "api", "help", "support", "status", "blog", "login", "auth", "cdn", "static",
                   "candidate", "career", "careers", "jobs", "apply", "boards", "job-boards", "emp", "web"}
ATS_NOT_BOARD = re.compile(r"(?i)/(login|logg-?inn|signin|sign-in|register|registrer|signup|customer|kunde|admin|"
                           r"candidate/profile|profile|my-?page|minside|privacy|personvern|cookie|terms)(/|$)")
ATS_JOBS_TEXT = re.compile(r"(?i)\b(karriere|ledige? stilling(er)?|stillinger|jobb(e)?( hos oss)?|jobbmuligheter|"
                           r"careers?|jobs?|vacanc(y|ies)|open positions|join (us|our team)|work (with|at) us|"
                           r"søk (jobb|stilling)|apply)\b")


def ats_board(url: str, names: list[str] | None = None) -> bool:
    """A company's own board on a recruitment platform (e.g. acme.teamtailor.com, jobs.lever.co/acme,
    candidate.hr-manager.net/...?customer=123), not the platform's own pages. With `names` (distinctive name
    words and the site's domain label), the board's subdomain or path must carry one of them, so a link to a
    chain's or partner's board is not taken as ours."""
    pu = urlsplit(url)
    host = (pu.hostname or "").lower()
    plat = next((h for h in ATS_BOARDS if host == h or host.endswith("." + h)), None)
    if not plat or ATS_NOT_BOARD.search(pu.path):
        return False
    if names is not None:
        ident = re.sub(r"[^a-z0-9]", "", _translit(unquote(host[: -len(plat)] + pu.path).lower()))
        if not any(n and len(n) >= 4 and n in ident for n in names):
            return False
    sub = host[: -len(plat)].rstrip(".")
    sub = sub[4:] if sub.startswith("www.") else sub
    own_sub = bool(sub) and sub.split(".")[-1] not in ATS_GENERIC_SUB
    segs = [x for x in pu.path.split("/") if x]
    own_path = bool(segs) and segs[0].lower() not in {"", "jobs", "job", "search", "sok", "nb", "en", "no", "nb-no",
                                                     "en-us", "home", "index", "about", "pricing", "blog"}
    own_query = bool(re.search(r"(?i)(customer|company|companylock|cid|employer|kunde|org)[a-z_]*=\w+", pu.query))
    return own_sub or own_path or own_query


def ats_link(pages: list[Page], scope: "Scope", legal_name: str = "") -> tuple[str, Page, str] | None:
    """The first link on the verified site's pages to the company's own board on a recruitment platform, with a
    jobs wording in the link text or the URL, whose board names the company (a name word or the domain label)."""
    names = [re.sub(r"[^a-z0-9]", "", _translit(w.lower())) for w in name_words(legal_name)
             if w.lower() not in GENERIC_WORDS and w.lower() not in LEGAL_SUFFIX]
    names.append(re.sub(r"[^a-z0-9]", "", _translit(bare(pages[0].host).split(".")[0])))
    for p in pages:
        for a in p.soup.find_all("a", href=True):
            u = urljoin(p.url, a["href"].strip()).split("#")[0]
            if urlsplit(u).scheme not in ("http", "https") or not ats_board(u, names):
                continue
            label = re.sub(r"\s+", " ", a.get_text(" ", strip=True))[:80]
            if not (ATS_JOBS_TEXT.search(label) or ATS_JOBS_TEXT.search(unquote(urlsplit(u).path).replace("-", " "))
                    or ATS_JOBS_TEXT.search((a.get("title") or "") + " " + (a.get("aria-label") or ""))):
                continue
            if not scope.accept(a, p):
                continue
            return u, p, f'<a href="{a["href"][:200]}">{label}</a>' if label else a["href"][:200]
    return None


async def collect_hiring(sp: "_Spend", ctx: CompanyContext, pages: list[Page], domain: str, scope: "Scope") -> None:
    """hiring_signal: the careers page URL (linked, or found by probing the usual paths), plus one claim per
    real posting on it (JSON-LD JobPosting naming us, or a linked ATS posting)."""
    site_match = {"method": "verified_site", "detail": f"careers page on verified site {domain}"}
    link = careers_link(pages, domain, scope)
    reg = ctx.registry or {}
    if link is None and not ((reg.get("naeringskode1") or {}).get("kode") or "").startswith("78"):
        # no careers page of its own, but a link to its board on a recruitment platform (a staffing firm's
        # board lists its clients' jobs, so not for NACE 78)
        ats = ats_link(pages, scope, reg.get("navn") or "")
        if ats:
            url, p, span = ats
            ctx.facts["website_hiring_signal_checked"] = [url]
            ctx.add_claim("hiring_signal", "hiring", url, _ev(ctx, p, span),
                          match={"method": "ats_link_on_verified_site",
                                 "detail": f"job board on a recruitment platform, linked from the verified site {domain}"})
            return
    candidates = [link[0]] if isinstance(link, tuple) else []
    origin = f"{urlsplit(pages[0].url).scheme}://{urlsplit(pages[0].url).netloc}"
    probed = not candidates
    if probed:
        candidates = [origin + p for p in JOB_PROBES]
    page = None
    ctx.facts["website_hiring_signal_checked"] = [urlsplit(u).path or "/" for u in candidates[:7]]
    for url in candidates:
        if not sp.can(1):
            break
        known = next((p for p in pages if p.url.rstrip("/") == url.rstrip("/")), None)
        got = known or (await _fetch_page(sp, url))[0]
        reg = ctx.registry or {}
        if got and _careers_page_ok(got, domain, pages[0], probed, ((reg.get("naeringskode1") or {}).get("kode") or ""),
                                    reg.get("navn") or ""):
            page = got
            break
        if len(candidates) > 1 and got is None:
            continue
    if not page:
        return
    url = page.url.split("#")[0]
    head = page.soup.title.get_text(" ", strip=True) if page.soup.title else url
    ctx.add_claim("hiring_signal", "hiring", url, _ev(ctx, page, _clean(head, 200) or url), match=site_match)
    postings: dict[str, tuple[str, str]] = {}
    ours = _squash((ctx.registry or {}).get("navn") or "")
    for d in page.jsonld:
        if str(d.get("@type")) != "JobPosting":
            continue
        org = d.get("hiringOrganization")
        name = org.get("name") if isinstance(org, dict) else org
        if not isinstance(name, str) or not ours or not (_squash(name) in ours or ours in _squash(name)):
            continue
        purl = d.get("url") or ""
        if purl and isinstance(d.get("title"), str):
            postings.setdefault(urljoin(url, purl), (d["title"], json.dumps({"title": d["title"]}, ensure_ascii=False)))
    for a in page.soup.find_all("a", href=True):
        h = urljoin(url, a["href"].strip()).split("#")[0]
        p = urlsplit(h)
        label = a.get_text(" ", strip=True)
        posting_path = re.search(r"(?i)(job|jobs|stilling|recruit|position|vacanc|annonse)", p.path + "?" + p.query) \
            and not re.search(r"(?i)(profile|login|register|signup|subscribe|alert|search|list|candidate)", p.path + p.query)
        if ATS_POSTING.search(p.netloc) and posting_path and 4 <= len(label) <= 120 and not _in_chrome(a):
            postings.setdefault(h, (label, f'<a href="{a["href"][:200]}">{label}</a>'))
    for purl in sorted(postings)[:10]:
        title, span = postings[purl]
        ctx.add_claim("hiring_signal", "hiring", purl, _ev(ctx, page, span),
                      match={"method": "posting_on_verified_careers_page", "detail": title[:200]})


# ---------------------------------------------------------------- entry point
CANARY_HOST = "data.brreg.no"
_CANARY: dict[str, Any] = {}
TRANSIENT = {"dns_error", "unreachable", "http_500", "http_502", "http_503", "http_504", "http_429", "skipped_budget"}


async def network_up(f: Fetcher) -> bool:
    """Outage guard: resolve a host that is always there. Checked lazily on the first lookup or connection
    failure, and remembered for a minute."""
    import time as _time
    now = _time.monotonic()
    if _CANARY and now - _CANARY["at"] < 60:
        return _CANARY["up"]
    up = bool(await resolves(f, CANARY_HOST)) and await reachable(CANARY_HOST, 443)
    _CANARY.update(at=now, up=up)
    return up


TRIED_LIST = 10  # domains named in a website note


def _tried_note(verdicts: list["Verdict"]) -> str:
    """Every candidate domain the step looked up or fetched, in the order tried, with its outcome."""
    seen = [v for v in verdicts if v.status not in ("duplicate", "unchecked", "not_checked", "skipped_budget")]
    if not seen:
        return ""
    out = ", ".join(f"{v.domain} ({v.status})" for v in seen[:TRIED_LIST])
    return "domains tried: " + out + (f" and {len(seen) - TRIED_LIST} more" if len(seen) > TRIED_LIST else "")


async def fetch_website(f: Fetcher, ctx: CompanyContext) -> None:
    e = ctx.registry or {}
    if not e or e.get("_gone") or ctx.facts.get("deleted"):
        ctx.mark("official_website", SECTION, "not_applicable", note="no live registry record")
        return
    ctx.facts["website_exclusive"] = False
    cands = candidates(ctx)
    registry_host = next((bare(d) for d, s, _ in cands if s == "registry"), None)
    sp = _Spend(f, ctx)
    verdicts: list[Verdict] = []
    done: set[str] = set()
    proven: Verdict | None = None
    budget_note = None
    search_failed = None
    try:
        hosts = [h for d, _s, _u in cands[:24] for h in (d, "www." + d)]
        await asyncio.gather(*[_resolve(sp, f, h) for h in hosts])
        for dom, src, start in cands:
            if not sp.can(2):
                budget_note = "per-company request cap reached"
                break
            if src.startswith("guess") and len([v for v in verdicts if v.source.startswith("guess")
                                                and v.status not in ("no_dns", "duplicate")]) >= 10:
                break
            v = Verdict(dom, src)
            verdicts.append(v)
            if bare(dom) in done:
                v.status = "duplicate"
                continue
            await check_candidate(f, sp, ctx, v, done, registry_host, start)
            if v.transient and (v.status == "name_only" or (v.status == "proven" and v.method != "orgnr_on_page")):
                # (a site that never shows our name is no closer to proof with one more page)
                # a page of this site failed transiently: its verdict could differ on the next run, so the step
                # fails (the pipeline's retry pass reads the site again) instead of deciding on part of the site
                raise _RegistrySearchFailed(f"pages of {v.domain} could not all be read: {v.transient[0]}")
            if v.status == "proven":
                proven = v
                break
        if proven:
            scope = await prepare_scope(sp, ctx, proven)
            if proven.transient and proven.method != "orgnr_on_page":
                raise _RegistrySearchFailed(f"pages of {proven.domain} could not all be read: {proven.transient[0]}")
            ctx.facts["website_exclusive"] = bool(scope.exclusive and proven.method == "orgnr_on_page")
            ctx.facts["website_exclusive_reason"] = scope.reasons() or "exclusive"
            emit_site(ctx, proven, scope)
            dom = bare(proven.pages[0].host)
            if scope.exclusive:
                for step, fld, sec in ((collect_dated_news, "dated_news", "activity"),
                                       (collect_hiring, "hiring_signal", "hiring")):
                    sp.cap = sp.used + (NEWS_BUDGET if fld == "dated_news" else STEP_BUDGET)  # its own budget
                    before = len(ctx.claims)
                    starved = False
                    try:
                        await step(sp, ctx, proven.pages, dom, scope)
                    except _OutOfRequests:
                        starved = True
                    except BudgetExhausted:
                        starved = True
                    if not any(c.field == fld for c in ctx.claims[before:]):
                        checked = ", ".join(ctx.facts.get(f"website_{fld}_checked", [])[:6]) or "the verified site"
                        if starved or f.budget.remaining_requests() <= RESERVE:
                            ctx.mark(fld, sec, "failed", note=f"budget_exhausted: checked {checked}")
                        else:
                            ctx.mark(fld, sec, "not_available",
                                     note=f"none found on the verified site {dom}; checked {checked}")
                sp.cap = sp.used + 2
                try:
                    await sitemap_activity(f, sp, ctx, proven.pages[0], dom, scope)
                except _OutOfRequests:
                    pass
    except BudgetExhausted:
        budget_note = "budget_exhausted"
    except _OutOfRequests:
        budget_note = "per-company request cap reached"
    except _RegistrySearchFailed as exc:
        search_failed = str(exc)
    finally:
        ctx.facts["website_candidates"] = [v.summary() for v in verdicts] + \
            [{"domain": d, "source": s, "status": "not_checked"} for d, s, _ in cands[len(verdicts):]]
        ctx.facts["website_requests"] = sp.used

    if search_failed:
        # A registry name search that decides a candidate could not be read: the outcome is unknown, not
        # ambiguous, so it is a transient failure that the pipeline's retry pass runs again (the search runs
        # before any site is emitted, so nothing was published by this attempt).
        ctx.mark("official_website", SECTION, "failed", note=f"transient: {search_failed}")
        ctx.module_states["website"] = "timeout"
        return
    if proven:
        ctx.module_states["website"] = "available"
        return
    unchecked = cands[len(verdicts):] + [(v.domain, v.source, None) for v in verdicts if v.status == "skipped_budget"]
    run_short = f.budget.remaining_requests() <= RESERVE + 2 or f.budget.remaining_seconds() < 10
    # Weak candidates (domain-name matches from Common Crawl's host list) are hints, not declarations:
    # leaving them unchecked does not turn the result into a failure.
    declared_left = [d for d, src, _ in unchecked if src in ("registry", "email")
                     or (src.startswith("seed:") and src != "seed:commoncrawl_domain")]
    tried = _tried_note(verdicts)
    if budget_note == "budget_exhausted" or (budget_note and unchecked) or any(v.status == "skipped_budget" for v in verdicts):
        ctx.mark("official_website", SECTION, "failed",
                 note="budget_exhausted: not checked " + ", ".join(d for d, _s, _u in unchecked[:5]) +
                      (f"; {tried}" if tried else ""))
        ctx.module_states["website"] = "failed"
        return

    # Absence is claimed only from definitive answers. A failed DNS lookup, or a site that resolved but
    # could not be reached on every candidate, is a failure (transient: the retry pass tries again).
    answered = [v for v in verdicts if v.status not in ("no_dns", "dns_error", "duplicate", "not_checked")]
    limited = [v for v in verdicts if v.status in ("http_429", "http_503")]
    if limited:
        # a rate-limited or temporarily unavailable candidate (after honouring Retry-After) may be the site:
        # a transient failure for the retry pass, never absence, so two runs agree
        ctx.mark("official_website", SECTION, "failed",
                 note="transient: " + ", ".join(f"{v.domain} ({v.status})" for v in limited[:4]) + " answered "
                      "rate-limited or unavailable; checked: " + ", ".join(f"{v.domain} ({v.status})" for v in verdicts[:6]))
        ctx.module_states["website"] = "timeout"
        return
    errors = [v for v in verdicts if v.status in TRANSIENT]
    transient = False
    if errors and not any(v.status in ("name_only", "related", "ambiguous") for v in verdicts):
        up = await network_up(f)
        # With the network down nothing is absent. With it up, a declared domain (registry website or email)
        # that cannot be looked up or reached is a failure too; a dead name-guessed domain is just not ours.
        declared_err = [v for v in errors if v.source in ("registry", "email")]
        all_failed = bool(answered) and all(v.status in TRANSIENT for v in answered) and \
            any(v.source in ("registry", "email") for v in answered)
        transient = (not up) or bool(declared_err) or all_failed
        why = "network unavailable" if not up else (
            "the registry-declared domain could not be looked up or reached" if declared_err
            else "every candidate site resolved but could not be reached")
        if transient:
            ctx.mark("official_website", SECTION, "failed", note=f"{why}; checked: " +
                     ", ".join(f"{v.domain} ({v.status})" for v in verdicts[:6]))
            ctx.module_states["website"] = "timeout"  # transient: the pipeline's retry pass runs it again
            return

    # Related sites: another organisation number printed, ours never.
    for v in verdicts:
        if v.status == "related" and v.pages and v.source in ("registry", "email"):
            num, (p, s) = next(iter(v.others.items()))
            ev = ctx.add_evidence(p.snap, source_class=COMPANY_OWNED, span=bytes_exact(p.snap, s), method=METHOD,
                                  licence=LICENCE)
            ctx.add_claim("related_website", SECTION, {"url": canonical_url(v.pages[0]), "other_organisation_number": num},
                          ev, availability="ambiguous", confidence=0.0,
                          note=f"the registry {'website' if v.source == 'registry' else 'email'} points to this "
                               f"domain, but {v.detail}; likely a manager, parent, employer or service provider. "
                               "Not this company's official site.")

    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.source] = counts.get(v.source, 0) + 1
    checked = []
    if "registry" in counts:
        checked.append("registry website")
    if "email" in counts:
        checked.append("email domain")
    if counts.get("guess"):
        checked.append(f"{counts['guess']} name-derived domain(s)")
    statuses = sorted({v.status for v in verdicts})
    note = ("checked " + ", ".join(checked) if checked else "no candidate domains (no registry website, company email "
            "domain or usable name)") + (f"; outcomes: {', '.join(statuses)}" if statuses else "") + \
        "; none showed our org number, or the registry phone or address together with the legal name"
    if budget_note:
        note += f"; stopped early: {budget_note}"
    if tried:
        note += f"; {tried}"
    ambiguous = [v for v in verdicts if v.status in ("name_only", "related", "ambiguous")]
    looked = [v for v in verdicts if v.status not in ("no_dns", "duplicate")]
    blocked = [v for v in verdicts if v.status == "blocked" and (v.source == "registry" or looked == [v])]
    if ambiguous:
        names = ", ".join(f"{v.final_url or v.domain} ({v.status})" for v in ambiguous[:3])
        ctx.mark("official_website", SECTION, "ambiguous", note=f"{note}. Unproven candidates: {names}")
        ctx.module_states["website"] = "ambiguous"
    elif blocked:
        ctx.mark("official_website", SECTION, "blocked",
                 note=f"{note}. robots.txt disallows {blocked[0].domain}")
        ctx.module_states["website"] = "blocked"
    else:
        ctx.mark("official_website", SECTION, "not_available", note=note)
        ctx.module_states["website"] = "not_available"
