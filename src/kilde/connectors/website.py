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
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import dns.asyncresolver
import dns.exception
import dns.resolver
from bs4 import BeautifulSoup

from ..http import Blocked, BudgetExhausted, Fetcher
from ..model import COMPANY_OWNED, CompanyContext, Evidence, Snapshot

METHOD = "website_identity_v1"
EXTRACT = "website_extract_v1"
LICENCE = "company-owned page; robots.txt allowed"
SECTION = "web_presence"

MAX_REQUESTS = 10         # HTTP requests per company, robots.txt and Brreg name checks included
VERIFIED_CAP = 16         # raised cap once a site is proven and exclusive (news and careers)
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
    "vikenfiber.no", "skynet.be", "yandex.com", "mail.ru", "qq.com", "163.com", "web.de",
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
                          r"impressum|firmainfo|selskapet", re.I)
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
PHONE_LABEL = re.compile(r"(tlf|telefon|phone|mobil|tel\.?|ring oss|sentralbord)", re.I)
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
}
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
    good = []
    for lab in labels:
        if not (3 <= len(lab) <= 40) or lab.isdigit() or lab in COMMON_LABELS or len(lab) < 4:
            continue
        if "-" not in lab and len(lab) < 5 and len(words) == 1:
            continue
        if lab.replace("-", "") in places and "-" not in lab:
            continue
        good.append(lab)
    out = [f"{lab}.no" for lab in good] + [f"{lab}.com" for lab in good]
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
    m = next((x for x in rx.finditer(text) if not _in_hostname(text, x.start(), x.end())), None)
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


def _in_hostname(text: str, a: int, b: int) -> bool:
    """True when a name match is only part of a domain name or email address (e.g. 'totentre.no')."""
    if HOSTNAME_TAIL.match(text, b):
        return True
    before = text[max(0, a - 8):a].lower()
    return before.endswith(("www.", "@", "/", "://", ".")) or (a > 0 and text[a - 1] == "@")


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
        if rx and rx.search(text):
            return True
        toks = [_translit(w) for w in ws if len(w) >= 3 and w not in GENERIC_WORDS and w not in FORM_NAME_WORDS]
        if toks and all(re.search(r"(?<![a-z0-9])" + re.escape(x) + r"(?![a-z0-9])", t) for x in toks):
            return True
    return False


def _phone_in_named_block(el, page: Page, names: list[list[str]]) -> bool:
    """The phone sits in a contact block, header or footer whose text also names us."""
    on_contact_page = bool(INTERNAL_PRIORITY[0].search(urlsplit(page.url).path))
    node = el.parent if el is not None and getattr(el, "name", None) is None else el
    for _ in range(8):
        if node is None or getattr(node, "name", None) in (None, "[document]", "html"):
            break
        marks = " ".join(node.get("class") or []) + " " + (node.get("id") or "")
        is_block = node.name in ("footer", "header", "address") or bool(CONTACT_MARK.search(marks))
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
    for m in PHONE_TEXT.finditer(page.text):
        d = re.sub(r"\D", "", m.group(0))
        if len(d) >= 8 and d[-8:] in wanted:
            if names is None or _phone_in_named_block(_element_for(page, m.group(0).strip()), page, names):
                return _window(page.text, m.start(), m.end(), 25)
    for a in page.soup.find_all("a", href=re.compile(r"^tel:", re.I)):
        d = re.sub(r"\D", "", unquote(a["href"]))
        if len(d) >= 8 and d[-8:] in wanted:
            if names is None or _phone_in_named_block(a, page, names):
                return a["href"]
    return None


def _street_regex(street: str) -> re.Pattern | None:
    street = street.strip()
    m = re.match(r"^(.*?)[\s,]*(\d+)\s*([A-Za-z])?$", street)
    if not m or len(m.group(1)) < 3:
        return None  # need a street name plus a house number
    words = re.findall(r"[0-9A-Za-zÆØÅæøåÄÖäöüé'\-]+", m.group(1))
    body = r"[\s.\-]*".join(_word_rx(w.lower()) for w in words)
    num = m.group(2) + (r"\s?" + re.escape(m.group(3)) if m.group(3) else r"(?![0-9])")
    return re.compile(r"(?<![0-9a-zæøå])" + body + r"[\s.,]*" + num, re.I)


def find_address(page: Page, e: dict) -> str | None:
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
                return page.text[max(0, m.start() - 10):m.end() + pm.end() + 20].strip()
    return None


def is_parked(page: Page) -> bool:
    if any(page.host == h or page.host.endswith("." + h) for h in PARKED_HOSTS):
        return True
    head = (page.soup.title.get_text(" ", strip=True) if page.soup.title else "") + " " + page.text[:3000]
    if PARKED.search(head):
        return len(page.text) < 4000  # a real site may mention "under construction" somewhere
    return False


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
        if not INTERNAL_KEY.search(probe) or u.rstrip("/") == page.url.rstrip("/") or u in seen:
            continue
        seen.add(u)
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
    proof: list[tuple[Page, str]] = field(default_factory=list)  # (page, span)
    others: dict[str, tuple[Page, str]] = field(default_factory=dict)
    final_url: str | None = None
    variant: list[str] | None = None  # set when the name matched only through a shortened name variant
    namesake: tuple[str, str] | None = None  # another registered entity with our name (registry-declared site)
    cross: bool = False         # redirected to another registered domain
    path_scoped: bool = False   # the site is one section of a host that serves others (chain or portal)

    def summary(self) -> dict[str, Any]:
        return {"domain": self.domain, "source": self.source, "status": self.status, "method": self.method,
                "final_url": self.final_url, "other_orgnrs": sorted(self.others)[:5] or None,
                "cross_domain_redirect": self.cross or None, "section_of_host": self.path_scoped or None,
                "detail": self.detail}


def judge(v: Verdict, org: str, e: dict, registry_host: str | None) -> None:
    """Decide the identity status of a candidate from the pages fetched so far."""
    pages = v.pages
    v.others, v.method, v.proof = {}, None, []
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
            v.proof = [(p, span)]
            v.others = others
            return
    for p in pages:
        for num, s in other_orgnrs(p, org).items():
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
    name_span = name_page = conflict = None
    v.variant = None
    for words in variants:
        for p in pages:
            s, c = name_check(p.text, words, conflict_words)
            if s and not name_span:
                name_span, name_page = s, p
                v.variant = words if words != full and words != full[lead:] else None
            conflict = conflict or c
        if name_span:
            break
    if not name_span:
        v.status = "no_match"
        return
    if conflict:
        v.status = "name_only"
        v.detail = f"page names a different entity sharing our name: '{conflict[:80]}'"
        return
    final = bare(pages[0].host)
    # (b) registry website and name, same domain only (a cross-domain redirect is not enough)
    if registry_host and v.source == "registry" and same_site(final, registry_host):
        v.status, v.method = "proven", "registry_website_and_name"
        v.detail = f"registry lists {registry_host}; legal name shown on the site"
        v.proof = [(name_page, name_span)]
        return
    # (c) phone and name; address and name
    wanted = _digits_phones(e)
    for p in pages:
        s = find_phone(p, wanted, variants)
        if s:
            v.status, v.method = "proven", "phone_and_name"
            v.detail = "registry phone number and legal name shown on the site"
            v.proof = [(p, s), (name_page, name_span)]
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
                v.proof = [(p, hit), (name_page, name_span)]
                return
    for p in pages:
        s = find_address(p, e)
        if s:
            v.status, v.method = "proven", "address_and_name"
            v.detail = "registered street address, postcode and legal name shown on the site"
            v.proof = [(p, s), (name_page, name_span)]
            return
    v.status = "name_only"
    v.detail = "legal name shown, but no org number, registry phone or address"


# ---------------------------------------------------------------- network
class _Spend:
    """Per-company request accounting (robots.txt fetches included)."""

    def __init__(self, f: Fetcher, ctx: CompanyContext):
        self.f, self.ctx, self.used = f, ctx, 0
        self.cap = MAX_REQUESTS
        self.dns: dict[str, bool] = {}

    def can(self, n: int = 1) -> bool:
        return self.used + n <= self.cap and self.f.budget.remaining_requests() > RESERVE + n

    async def get(self, url: str, *, robots: bool = True, headers: dict | None = None) -> Snapshot:
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        before = self.f.budget.used
        new_robots = robots and origin not in self.f.robots
        if not self.can(2 if new_robots else 1):
            raise _OutOfRequests()
        try:
            return await self.f.get(url, robots=robots, headers=headers, reserve=RESERVE)
        finally:
            # count what this call cost; fall back to an estimate when the budget is shared and moving
            spent = self.f.budget.used - before
            est = (1 if new_robots else 0) + 1
            n = max(0, min(spent, est))
            self.used += n
            self.ctx.requests += n


class _OutOfRequests(Exception):
    pass


async def _resolve(sp: "_Spend", f: Fetcher, host: str) -> bool | None:
    if host not in sp.dns:
        sp.dns[host] = await resolves(f, host)
    return sp.dns[host]


_DNS_SEM: asyncio.Semaphore | None = None
DNS_PARALLEL = 24


async def resolves(f: Fetcher, host: str) -> bool | None:
    """True when the host has an A or AAAA record; False only on a genuine NXDOMAIN or an empty answer for both
    record types; None when the lookup itself failed (timeout after one retry, SERVFAIL, no reachable name
    server, network error), which is never read as absence."""
    global _DNS_SEM
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
    connection is final."""
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


async def _fetch_page(sp: _Spend, url: str) -> tuple[Page | None, str]:
    try:
        snap = await sp.get(url)
    except Blocked:
        return None, "blocked"
    except (BudgetExhausted, _OutOfRequests):
        raise
    except Exception:
        return None, "unreachable"
    if snap.status in (401, 403, 429):
        return None, f"http_{snap.status}"
    if snap.status >= 400:
        return None, f"http_{snap.status}"
    ct = snap.content_type.lower()
    if ct and "html" not in ct and "xml" not in ct:
        return None, "not_html"
    if not snap.content.strip():
        return None, "empty"
    return parse_page(snap), "ok"


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
        elif status != "parked":
            status = st
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
    v.path_scoped = _is_section(home.url)
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
            front, _st = await _fetch_page(sp, root)
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
        p, st = await _fetch_page(sp, link)
        if p and same_site(p.host, home.host):
            v.pages.append(p)
            judge(v, ctx.org, e, registry_host)
            if v.status == "proven" and v.method == "orgnr_on_page":
                return
    judge(v, ctx.org, e, registry_host)
    await _check_variant(sp, ctx, v)
    await _check_collision(sp, ctx, v)


COMMON_NOUNS = {"forsikring", "bank", "sparebank", "kraft", "energi", "entreprenor", "tak", "maling", "frisor", "klinikk",
                "tannlege", "lege", "butikk", "kafe", "restaurant", "hotell", "skole", "barnehage", "verksted", "rorlegger",
                "elektriker", "snekker", "regnskap", "revisjon", "advokat", "eiendom", "bygg", "transport", "service",
                "senter", "sport", "trafikkskole", "taxi", "bilverksted", "utleie", "salg", "marina", "camping"}


INVOICE_MAIL = re.compile(r"(?i)poweroffice|tripletex|fiken|visma|ehf|24sevenoffice|uni-?economy|xledger|"
                          r"conta|cloudflare|wpengine|invoice|faktura\.")
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


async def _check_collision(sp: "_Spend", ctx: CompanyContext, v: Verdict) -> None:
    """For proofs other than our org number: reject the site when another active registered entity (or another
    company's workplace) carries the same distinctive name, unless the site names our full legal name (with its
    legal form) and not the other's. Owners' sections, co-owned properties and housing co-ops accept only an
    org number on the site or the registry-declared website."""
    if v.status != "proven" or v.method == "orgnr_on_page":
        return
    e = ctx.registry or {}
    form = ((e.get("organisasjonsform") or {}).get("kode") or "").upper()
    if form in NON_OPERATING and v.method != "registry_website_and_name":
        v.status, v.method = "name_only", None
        v.detail = f"{form} entities need the org number on the site or a registry-declared website"
        return
    ours = _core_tokens(e.get("navn") or "")
    if not ours:
        return
    query = " ".join(sorted(ours))
    rows = _NAME_SEARCH.get(query)
    if rows is None:
        rows = []
        for path in ("enheter", "underenheter"):
            if not sp.can(1):
                break
            try:
                snap = await sp.get(f"https://data.brreg.no/enhetsregisteret/api/{path}?navn={query.replace(' ', '%20')}"
                                    f"&size=20", robots=False, headers={"Accept": "application/json"})
                if snap.status == 200:
                    rows += (snap.json().get("_embedded") or {}).get(path) or []
            except (BudgetExhausted, _OutOfRequests):
                raise
            except Exception:
                pass
        _NAME_SEARCH[query] = rows
    # Near-namesakes at our own postcode ('Advisors AS' beside 'Advisor AS') rank too low in a plain name
    # search, so ask once more with the postcode filter.
    pn = ((e.get("forretningsadresse") or e.get("beliggenhetsadresse") or {}).get("postnummer") or "")
    longest = max(ours, key=len)
    key2 = f"{longest}@{pn}"
    if pn and len(longest) >= 3:
        extra = _NAME_SEARCH.get(key2)
        if extra is None:
            extra = []
            if sp.can(1):
                try:
                    snap = await sp.get(f"https://data.brreg.no/enhetsregisteret/api/enheter?navn={longest}"
                                        f"&forretningsadresse.postnummer={pn}&size=50", robots=False,
                                        headers={"Accept": "application/json"})
                    if snap.status == 200:
                        extra = (snap.json().get("_embedded") or {}).get("enheter") or []
                except (BudgetExhausted, _OutOfRequests):
                    raise
                except Exception:
                    pass
            _NAME_SEARCH[key2] = extra
        rows = rows + [r for r in extra if r.get("organisasjonsnummer") not in {x.get("organisasjonsnummer") for x in rows}]
    our_phones = _digits_phones(e)
    our_addr = _addr_key(e)
    for r in rows:
        org = r.get("organisasjonsnummer")
        if org == ctx.org or r.get("overordnetEnhet") == ctx.org or not _active(r):
            continue
        theirs = _core_tokens(r.get("navn") or "")
        if not theirs or theirs == ours or not _near(ours, theirs):
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
    names_us = bool(our_rx and our_rx.search(text))
    our_core_rx = name_regex([w for w in name_words(e.get("navn") or "") if w not in FORM_NAME_WORDS])
    for r in rows:
        org = r.get("organisasjonsnummer")
        if org == ctx.org or r.get("overordnetEnhet") == ctx.org or not _active(r):
            continue
        theirs = _core_tokens(r.get("navn") or "")
        if not theirs or not (theirs <= ours or ours <= theirs):
            continue
        other_rx = _full_name_rx(r.get("navn") or "")
        names_them = bool(other_rx and other_rx.search(text))
        if theirs != ours:
            # A longer or shorter namesake (a subsidiary 'Kitron Eiendom AS', a parent 'Viken AS') collides only
            # when the site shows that other name as a phrase.
            core_rx = name_regex([w for w in name_words(r.get("navn") or "") if _translit(w) in theirs])
            if not (core_rx and any(not _in_hostname(text, m.start(), m.end()) for m in core_rx.finditer(text))):
                continue
            if theirs < ours and our_core_rx and our_core_rx.search(text):
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


LANG_SEG = {"no", "nb", "nn", "en", "se", "sv", "da", "de", "fi", "home", "hjem", "forside", "index.html", "index.php"}


ABOUT_SEG = re.compile(r"^(om-?oss|om|about(-us)?|kontakt(-oss)?|contact(-us)?|firma|selskapet|company)$", re.I)


def _is_front_or_about(url: str) -> bool:
    segs = [x for x in urlsplit(url).path.split("/") if x]
    return not segs or (len(segs) == 1 and bool(ABOUT_SEG.match(segs[0]) or segs[0].lower() in LANG_SEG))


def _is_section(url: str) -> bool:
    """True when the page is a deeper section of a host (a member page on a chain site or a school on a portal),
    not the site's front page or a language root."""
    segs = [x for x in urlsplit(url).path.split("/") if x]
    return bool(segs) and not (len(segs) == 1 and segs[0].lower() in LANG_SEG)


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
    for d in name_domains(e.get("navn") or "", e):
        add(d, "guess")
    return out


# ---------------------------------------------------------------- extraction
def _ev(ctx: CompanyContext, page: Page, span: str | None, method: str = EXTRACT,
        effective: str | None = None, note: str | None = None) -> Evidence:
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
    if not path or SOCIAL_DROP.search(path + ("?" + p.query if p.query else "")):
        return None
    segs = [s for s in path.split("/") if s]
    query = ""
    if plat == "linkedin":
        if len(segs) < 2 or segs[0] not in ("company", "school", "showcase"):
            return None
        segs = segs[:2]
    elif plat == "facebook":
        if segs[0] == "people" and len(segs) >= 3 and segs[2].isdigit():
            segs = segs[:3]
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
    elif plat == "tiktok":
        if not segs[0].startswith("@"):
            return None
        segs = segs[:1]
    else:  # instagram, x
        segs = segs[:1]
    handle = (segs[1] if segs[0] == "people" and len(segs) == 3 else segs[-1]).lstrip("@").lower()
    if segs[0] != "profile.php" and handle in PLACEHOLDER_HANDLES:
        return None
    canon_host = {"linkedin": "www.linkedin.com", "facebook": "www.facebook.com", "instagram": "www.instagram.com",
                  "youtube": "www.youtube.com", "x": "x.com", "tiktok": "www.tiktok.com"}[plat]
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
    addr: tuple | None = None  # (street regex, postcode) of our registered address

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
FOREIGN_SUFFIX = {"ab", "aps", "oy", "oyj", "gmbh", "ltd", "limited", "inc", "llc", "bv", "srl", "sarl", "ou", "uab"}
ENTITY_ANY = re.compile(r"((?:[0-9A-Za-zÆØÅæøåÄÖäöüé&\-]+[ \u00a0]+){1,5}?)"
                        r"(AS|ASA|A/S|ANS|DA|SA|AB|ApS|Oy|OY|Oyj|GmbH|Ltd|Limited|Inc|LLC|BV|OÜ|UAB|STI)\b\.?")


CONNECTORS = {"og", "&", "i", "for", "av", "de", "la", "von", "van", "der", "and", "of"}


OPERATOR_PAGE = re.compile(r"(?i)om-?oss|about|kontakt|contact|personvern|privacy|impressum|imprint|firmainfo|selskapet")
OPERATOR_MARK = re.compile(r"(?i)footer|header|contact|kontakt|imprint|impressum|copyright|address|adresse|company-info")


def _operator_text(p: Page) -> str:
    """Text in operator positions: header, footer, navigation, address and contact or imprint blocks."""
    parts = []
    for el in p.soup.find_all(True):
        marks = " ".join(el.get("class") or []) + " " + (el.get("id") or "")
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
    for p in pages:
        optext = _operator_text(p)
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
                if not foreign and (ws == ours or ws[-len(ours):] == ours or ours[-len(ws):] == ws):
                    continue  # our own name or its tail (a same-named foreign AB or GmbH is a different company)
                before = txt[max(0, m.start() - 40):m.start()]
                if CREDIT.search(before) or (not is_title and " ".join(run) and " ".join(run) in offsite):
                    continue  # a web agency or partner credit
                key = (" ".join(ws), m.group(2).lower())
                ctx_hit = bool(OWNER_CONTEXT.search(txt[max(0, m.start() - 60):m.end() + 10]))
                mention = m.group(0).strip()
                positional = is_title or ctx_hit or op_page or (mention in optext)
                score[key] = max(score.get(key, 0), 2 if positional else 1)
        for k, v in score.items():
            best[k] = max(best.get(k, 0), v)
    return [(k[0], k[1], best[k] >= 2) for k in sorted(best, key=lambda k: -best[k])]


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


async def confirm_dominant(sp: "_Spend", ctx: CompanyContext, names: list[tuple[str, str, bool]], limit: int = 3,
                           site_host: str = "") -> dict[str, str]:
    """Registered companies that count against exclusivity: named in an operator position, or tied to us
    (address, phone, email domain or name words). Clients, partners and suppliers in body text do not count."""
    out: dict[str, str] = {}
    e = ctx.registry or {}
    ours = {t for t in _core_tokens(e.get("navn") or "") if len(t) >= 4 and t not in GENERIC_WORDS}
    # positional names first, then body names that share our name words, then the rest
    ordered = sorted(names, key=lambda n: (not n[2], not (set(n[0].split()) & ours)))
    for lab, suffix, positional in ordered:
        if suffix in FOREIGN_SUFFIX:
            if positional or set(lab.split()) & ours:
                out[f"{lab} {suffix}"] = "foreign"
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
            out[f"{lab} {suffix}"] = got[lab]
    return out


async def confirm_sisters(sp: "_Spend", ctx: CompanyContext, labels: list[str], limit: int = 2) -> dict[str, str]:
    """Labels that the registry knows as a different company: label -> org number."""
    out: dict[str, str] = {}
    for lab in labels[:limit]:
        hits = _SISTER_CACHE.get(lab)
        if hits is None:
            if not sp.can(1):
                break
            try:
                snap = await sp.get(f"{BRREG_SEARCH}?navn={lab.replace(' ', '%20')}&size=20", robots=False,
                                    headers={"Accept": "application/json"})
                rows = (snap.json().get("_embedded") or {}).get("enheter") or [] if snap.status == 200 else []
            except (BudgetExhausted, _OutOfRequests):
                raise
            except Exception:
                rows = []
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


def _names_other_company(handle: str, words: list[str], other_names: list[str]) -> bool:
    """A handle that names a different company: our distinctive word plus a group or holding word our own name
    lacks ('elprogroupas' for Elpro Electro AS), or the name of another registered company we know of."""
    ours = re.sub(r"[^a-z0-9]", "", _translit("".join(words)))
    lead = next((_translit(w) for w in words if len(w) >= 3 and w not in GENERIC_WORDS), "")
    if lead and lead in handle:
        for gw in GROUP_WORDS:
            if gw in handle and gw not in ours:
                return True
    for name in other_names:
        sq = re.sub(r"[^a-z0-9]", "", _translit(name))
        if len(sq) >= 5 and sq in handle and sq not in ours and not ours.startswith(sq):
            return True
    return False


def collect_socials(pages: list[Page], words: list[str], domains: set[str], scope: Scope | None = None,
                    other_names: list[str] | None = None) -> tuple[list[tuple[str, str, Page, str]], list[str]]:
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
            raw = a.get("href") if a.name == "a" else a.get("data-href")
            c = clean_social(raw or "")
            if not c or "profile.php" in c[1]:
                continue
            if not scope.accept(a, p):
                dropped.append(c[1])
                continue
            label = a.get_text(" ", strip=True)
            iconic = not label or bool(re.fullmatch(r"(?i)(f[øo]lg oss( på)?|follow us( on)?)?\s*(facebook|instagram|"
                                                    r"linkedin|youtube|twitter|x|tiktok)?", label))
            rec = found.setdefault(c[0], {}).setdefault(c[1], [p, (raw or "")[:300], False, False])
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
                    if c and "profile.php" not in c[1] and not (scope.sisters and scope.names_sister(s)):
                        found.setdefault(c[0], {}).setdefault(c[1], [p, json.dumps({"sameAs": s})[:300], True, True])
    keys = {re.sub(r"[^a-z0-9]", "", _translit(w)) for w in words if len(w) >= 4 and _translit(w) not in REGIONS
            and w not in GENERIC_WORDS and w not in FORM_NAME_WORDS}
    keys.add(re.sub(r"[^a-z0-9]", "", _translit("".join(words))))
    keys |= {re.sub(r"[^a-z0-9]", "", d.split(".")[0]) for d in domains if d}
    keys = {k for k in keys if len(k) >= 4}
    out = []
    for plat in sorted(found):
        urls = found[plat]
        kept = []
        for u in sorted(urls):
            p, s, chrome, iconic = urls[u]
            path = urlsplit(u).path
            segs = [x for x in path.split("/") if x]
            raw_handle = segs[1] if len(segs) == 3 and segs[0] == "people" else (segs[-1] if segs else "")
            handle = re.sub(r"[^a-z0-9]", "", _translit(unquote(raw_handle)))
            if _names_other_company(handle, words, other_names):
                dropped.append(u)
                continue
            id_only = handle.isdigit() or bool(re.match(r"/channel/UC", path))
            named = any(k in handle for k in keys)
            if named or (chrome and len(urls) == 1 and (id_only or iconic)):
                kept.append((not named, u, (plat, u, p, s)))
            else:
                dropped.append(u)
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


def _element_for(page: Page, needle: str):
    if not needle:
        return None
    return page.soup.find(string=lambda x: bool(x) and needle in x)


LEGAL_PAGE = re.compile(r"(?i)personvern|privacy|cookie|informasjonskapsl|vilk[aå]r|terms|legal|gdpr|"
                        r"salgsbetingelser|kj[oø]psbetingelser|angrerett|apenhetsloven|åpenhetsloven|disclaimer|impressum")


def extract_contacts(pages: list[Page], domain: str, registry_phones: set[str] = frozenset(),
                     scope: Scope | None = None, email_domains: set[str] = frozenset()
                     ) -> tuple[list, list, int]:
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
        if ok and store is phones and scope.org and key[-8:] not in registry_phones:
            ok = el is not None and scope.owner(el) == "ours"  # a block that names us (legal name or org number)
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
            before = p.text[max(0, m.start() - 25):m.start()]
            if re.search(r"(\+|00)(?!47)\d{1,3}[\s.\-\u00a0]?$", before):
                continue  # the rest of a foreign number written with its country code
            n = norm_phone(m.group(0))
            labels = re.findall(r"(?i)tlf|telefon|phone|mobil|tel\b|ring oss|sentralbord|faks|fax|telefaks", before)
            if labels and re.fullmatch(r"(?i)faks|fax|telefaks", labels[-1]):
                continue  # the nearest label says this is a fax number
            if n and PHONE_LABEL.search(before) and n not in phones and n not in skipped:
                put(phones, n, p, _window(p.text, m.start(), m.end(), 20), _element_for(p, m.group(0).strip()))
    # only addresses on the company's own domain; generic mailboxes first
    def ours_mail(a: str) -> bool:
        local, dom = a.rsplit("@", 1)
        if any(same_site(dom, d) for d in email_domains):
            return True
        # a free-mail box (birifotklinikk@gmail.com) counts when its mailbox name is our distinctive name
        lq = re.sub(r"[^a-z0-9]", "", _translit(local))
        return dom in FREE_MAIL and bool(name_key) and len(name_key) >= 6 and name_key in lq
    own = [(a, ps) for a, ps in emails.items() if ours_mail(a)]
    generic = [x for x in own if x[0].split("@")[0] in GENERIC_MAILBOX]
    # personal mailboxes only when the site publishes no generic one, and then just one
    picked = generic[:2] if generic else own[:1]
    em = [(a, p, s) for a, (p, s) in picked]
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
               "vacancies", "vacancy", "rekruttering", "rekrutering", "stillingsannonser", "jobbmuligheter"}
NOT_JOBS_PATH = re.compile(r"/(nyheter|nyhet|news|aktuelt|blogg?|artikler|presse|tag|kategori|category)/", re.I)


def jobs_url(path: str) -> bool:
    """A jobs page by its URL path: a whole path token must be a jobs word (not 'utstillingsareal')."""
    path = unquote(path).lower()
    if NOT_JOBS_PATH.search(path + "/"):
        return False
    return any(t in JOBS_TOKENS for t in re.split(r"[/\-_.]+", _translit(path)) if t)


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
            if pu.path in ("", "/") or not jobs_url(pu.path):
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
    (re.compile(rf"(?<!\d)(\d{{1,2}})\.?\s+({_MON})\.?\s+(20\d\d|19\d\d)(?!\d)", re.I),
     lambda m: (m[3], MONTHS[m[2].lower()], m[1])),
    (re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}}),?\s+(20\d\d|19\d\d)(?!\d)", re.I),
     lambda m: (m[3], MONTHS[m[1].lower()], m[2])),
)


def find_date(block) -> tuple[str, str] | None:
    """(ISO date, the literal text it came from) from a news item block; never guessed."""
    t = block.find("time") if hasattr(block, "find") else None
    if t is not None and t.get("datetime"):
        d = _date(t["datetime"])
        if d:
            return d, t["datetime"]
    # Only a short, standalone date line counts (e.g. "20.01.2026" or "Publisert 8. januar 2026"), never a
    # date inside a sentence, which is usually an event date rather than the publication date.
    for node in block.find_all(string=True):
        text = re.sub(r"\s+", " ", str(node)).strip()
        if not text or len(text) > 40:
            continue
        for rx, parts in DATE_PATTERNS:
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
        block, found = a, None
        for _ in range(5):
            if block.parent is None:
                break
            block = block.parent
            txt = block.get_text(" ", strip=True)
            links = {urljoin(page.url, x["href"]).split("#")[0].split("?")[0] for x in block.find_all("a", href=True)}
            if len(txt) > 700 or len({x for x in links if same_site(host_of(x), domain)} - {u}) > 1:
                found = None
                break  # the block holds more than one item: a date here may belong to another post
            found = find_date(block)
            if found:
                break
        if not found:
            continue
        same = [x for x in page.soup.find_all("a", href=True)
                if urljoin(page.url, x["href"].strip()).split("#")[0].split("?")[0] == u]
        if any(block not in x.parents for x in same):
            continue  # linked from several items: a category or tag, not a post
        head = block.find(re.compile(r"^h[1-5]$"))
        anchors = [x for x in block.find_all("a", href=True) if same_site(host_of(urljoin(page.url, x["href"])), domain)]
        main = (head.find("a", href=True) if head else None) or max(anchors, key=lambda x: len(x.get_text(strip=True)))
        if urljoin(page.url, main["href"].strip()).split("#")[0].split("?")[0] != u:
            continue  # a tag, category or author link inside someone else's item block
        title = _clean((head.get_text(" ", strip=True) if head else "") or a.get_text(" ", strip=True), 200)
        if not title or len(title) < 8 or re.fullmatch(r"(?i)(les mer|read more|mer|more|→|»)", title):
            continue
        if scope.shared and not (scope.url_ours(u) or scope.accept(block, page)):
            continue
        span = _clean(block.get_text(" ", strip=True), 300)
        items[u] = ({"title": title, "url": u, "date": found[0]}, span or found[1])
    ranked = sorted(items.values(), key=lambda x: x[0]["date"], reverse=True)
    return ranked[:limit]


def news_listing_link(pages: list[Page], domain: str) -> str | None:
    for p in pages:
        for a in p.soup.find_all("a", href=True):
            u = urljoin(p.url, a["href"].strip()).split("#")[0].split("?")[0]
            if same_site(host_of(u), domain) and NEWS_PATH.search(urlsplit(u).path + "/") \
                    and urlsplit(u).path.strip("/").count("/") == 0:
                return u
    return None


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
            floor = _date_floor(ctx)
            early = [x for x in dated if x[1] < floor]
            if early:
                ctx.facts["website_activity_before_founding"] = len(early)
            dated = [x for x in dated if x[1] >= floor and not PLACEHOLDER_SLUG.search(x[0])]
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
                if sp.can(1) and link not in {p.url for p in v.pages}:
                    p, _st = await _fetch_page(sp, link)
                    if p and same_site(p.host, home.host):
                        v.pages.append(p)
                break
    if not (v.cross or v.path_scoped):
        priv = next((u for u in internal_links(home) if re.search(r"(?i)personvern|privacy", u)), None)
        if priv and sp.can(1) and priv not in {p.url for p in v.pages}:
            p, _st = await _fetch_page(sp, priv)
            if p and same_site(p.host, home.host):
                v.pages.append(p)  # read for operator and controller names; never a contact source
    words = name_words((ctx.registry or {}).get("navn") or "")
    others = dict(v.others)
    for p in v.pages:
        for num, s in other_orgnrs(p, ctx.org).items():
            others.setdefault(num, s)
    others = {k: (x[1] if isinstance(x, tuple) else x) for k, x in others.items()}
    sisters = await confirm_sisters(sp, ctx, sister_labels(v.pages, words))
    dominant = await confirm_dominant(sp, ctx, [n for n in other_company_names(v.pages, words)
                                                if n[0] not in sisters][:6], limit=3, site_host=home.host)
    if v.namesake:
        dominant[v.namesake[0].lower()] = v.namesake[1]
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
                    or (reg_mail and same_site(dom, reg_mail)) or INVOICE_MAIL.search(dom):
                continue
            label = re.sub(r"[^a-z0-9]", "", dom.split(".")[-2] if dom.count(".") >= 1 else dom)
            site_label = re.sub(r"[^a-z0-9]", "", bare(home.host).split(".")[0])
            if label == site_label or any(t in label for t in own_tokens):
                continue  # the company's own alternate domain
            other_mail.add(dom)
    codeclared = await _codeclared(sp, ctx, home)
    if codeclared and v.method == "orgnr_on_page" and not others:
        codeclared = {}  # the site prints our org number and no other: it speaks for us
    e = ctx.registry or {}
    a = e.get("forretningsadresse") or {}
    srx = _street_regex(([x for x in a.get("adresse") or [] if x] or [""])[-1]) if a.get("adresse") else None
    addr = (srx, a["postnummer"]) if srx and a.get("postnummer") else None
    return Scope(ctx.org, words, others, sisters, dominant, v.cross, v.path_scoped, other_mail, codeclared, addr)


_HJEM_CACHE: dict[str, list[dict]] = {}


async def _codeclared(sp: "_Spend", ctx: CompanyContext, home: Page) -> dict[str, str]:
    """Other active registered entities that declare this same website in the registry (a group site)."""
    host = bare(home.host)
    rows = _HJEM_CACHE.get(host)
    if rows is None:
        rows = []
        if sp.can(1):
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
    proof_evs = [ctx.add_evidence(p.snap, source_class=COMPANY_OWNED, span=s, method=METHOD, licence=LICENCE)
                 for p, s in v.proof]
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
    if not scope.exclusive:
        m["detail"] = f"{m['detail']}; not exclusive: {scope.reasons()}"
        m["url"] = url
        # the front page of a site is written as its registered domain; a section page keeps its full URL
        value = site_domain(home.host) if urlsplit(url).path in ("", "/") and not (v.cross or v.path_scoped) else url
        ctx.add_claim("official_website", SECTION, value, proof_evs, confidence=conf, match=m,
                      note="Only the website itself is published; facts from this site are withheld because "
                           + scope.reasons())
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
    if desc and not GENERIC_DESC.search(desc) and not PLACEHOLDER.search(desc) and len(desc) >= 15:
        if scope.shared and not scope.page_ours_only(home) and not (
                scope.names_ours(desc) and not scope.names_sister(desc)):
            ctx.mark("website_description", SECTION, "ambiguous", note=unsure)
        else:
            ctx.add_claim("website_description", SECTION, desc, _ev(ctx, home, span), match=site_match)
    lang = home.soup.find("html")
    if lang and lang.get("lang"):
        ctx.add_claim("site_language", SECTION, lang["lang"].strip()[:12],
                      _ev(ctx, home, f'<html lang="{lang["lang"].strip()[:12]}">'), match=site_match)

    reg_mail = ((ctx.registry or {}).get("epostadresse") or "@").rsplit("@", 1)[1].lower()
    emails, phones, skipped = extract_contacts(v.pages, domain, _digits_phones(ctx.registry or {}), scope,
                                               {reg_mail} if reg_mail and reg_mail not in FREE_MAIL else set())
    for a, p, s in emails:
        ctx.add_claim("site_contact_email", SECTION, a, _ev(ctx, p, s), match=site_match)
    for n, p, s in phones:
        ctx.add_claim("site_contact_phone", SECTION, n, _ev(ctx, p, s), match=site_match)
    if scope.shared and skipped:
        if not emails:
            ctx.mark("site_contact_email", SECTION, "ambiguous", note=unsure)
        if not phones:
            ctx.mark("site_contact_phone", SECTION, "ambiguous", note=unsure)

    words = scope.words
    reg = ctx.registry or {}
    doms = {domain, host_of(reg.get("hjemmeside") or ""), (reg.get("epostadresse") or "@").rsplit("@", 1)[1]}
    doms = {bare(d) for d in doms if d and d not in FREE_MAIL and not is_platform(d)}
    socials, dropped = collect_socials(v.pages, words, doms, scope)
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
                "youtube": "youtube.com", "x": "x.com", "tiktok": "tiktok.com"}


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
    value = (SOCIAL_HOSTS[platform] + p.path.rstrip("/")).lower()
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
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            from email.utils import parsedate_to_datetime
            d = parsedate_to_datetime(raw)
    except (ValueError, TypeError, IndexError):
        return None
    if d.tzinfo is None:
        return d.date().isoformat() if len(raw) <= 10 else d.isoformat()
    return d.isoformat()


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
        out.append({"title": _xml_text(t.group(1)), "url": url.strip(), "datetime": when,
                    "title_span": t.group(0), "date_span": dm.group(0)})
    return out


def _wp_posts(raw: str) -> list[dict]:
    try:
        rows = json.loads(raw)
    except ValueError:
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        title = _xml_text(((r.get("title") or {}).get("rendered")) or "")
        gmt = r.get("date_gmt")
        if title and r.get("link") and gmt:
            out.append({"title": title, "url": r["link"], "datetime": f"{gmt}+00:00",
                        "title_span": json.dumps({"date_gmt": gmt})[1:-1], "date_span": None})
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


def _news_ok(item: dict, floor: str, domain: str) -> bool:
    day = item["datetime"][:10]
    today = datetime.now(timezone.utc).date().isoformat()
    path = urlsplit(item["url"]).path
    return (floor <= day <= today and same_site(host_of(item["url"]), domain) and path not in ("", "/")
            and not NOT_POST.search(path + "/") and not SAMPLE_POST.match(item["title"])
            and not PLACEHOLDER.search(item["title"]) and not PLACEHOLDER_SLUG.search(item["url"])
            and len(item["title"]) >= 4)


def _is_wordpress(page: Page) -> bool:
    gen = page.soup.find("meta", attrs={"name": re.compile("^generator$", re.I)})
    return bool((gen and "wordpress" in (gen.get("content") or "").lower()) or b"/wp-content/" in page.snap.content)


def _is_wix(page: Page) -> bool:
    gen = page.soup.find("meta", attrs={"name": re.compile("^generator$", re.I)})
    return bool((gen and "wix" in (gen.get("content") or "").lower()) or b"static.wixstatic.com" in page.snap.content)


async def collect_dated_news(sp: "_Spend", ctx: CompanyContext, pages: list[Page], domain: str,
                             scope: "Scope", limit: int = 20) -> None:
    """Dated news from the verified site: feeds first (RSS or Atom link, WordPress /feed/ or REST posts,
    Wix /blog-feed.xml), then the news listing (and its page 2). Each item keeps the source's own datetime.
    Value "title (datetime)", newest first, at most 20."""
    home = pages[0]
    origin = f"{urlsplit(home.url).scheme}://{urlsplit(home.url).netloc}"
    floor = _date_floor(ctx)
    found: dict[str, tuple[dict, Snapshot, Page | None]] = {}

    def keep(items: list[dict], snap: Snapshot) -> None:
        for it in items:
            it["url"] = urljoin(origin + "/", it["url"])
            if _news_ok(it, floor, domain) and it["url"] not in found:
                found[it["url"]] = (it, snap, None)

    feeds: list[str] = []
    for p in pages:
        for ln in p.soup.find_all("link", attrs={"type": FEED_TYPES}):
            href = urljoin(p.url, ln.get("href") or "")
            if href and same_site(host_of(href), domain) and "comments" not in href and href not in feeds:
                feeds.append(href)
    if not feeds and _is_wordpress(home):
        feeds.append(origin + "/feed/")
    if not feeds and _is_wix(home):
        feeds.append(origin + "/blog-feed.xml")
    def clean_feed(items: list[dict]) -> list[dict]:
        # a hacked site's feed full of spam is not company news: drop the whole feed
        return [] if any(SPAM.search(it["title"]) for it in items) else items

    for feed in feeds[:2]:
        if not sp.can(1):
            break
        try:
            snap = await sp.get(feed)
        except (BudgetExhausted, _OutOfRequests):
            raise
        except Exception:
            continue
        if snap.status == 200 and b"<" in snap.content[:300]:
            keep(clean_feed(parse_feed(snap.content.decode("utf-8", "replace"))), snap)
        if found:
            break
    if not found and _is_wordpress(home) and sp.can(1):
        try:
            snap = await sp.get(origin + "/wp-json/wp/v2/posts?per_page=20&_fields=title,link,date_gmt")
            if snap.status == 200:
                keep(clean_feed(_wp_posts(snap.content.decode("utf-8", "replace"))), snap)
        except (BudgetExhausted, _OutOfRequests):
            raise
        except Exception:
            pass
    if not found:
        listing = news_listing_link(pages, domain)
        probes = [listing] if listing else [origin + p for p in NEWS_PROBES]
        for url in probes:
            if not sp.can(1):
                break
            page, _st = await _fetch_page(sp, url)
            if not page or not same_site(page.host, domain) or urlsplit(page.url).path in ("", "/"):
                continue
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
                          "title_span": span, "date_span": None}
                    if _news_ok(it, floor, domain) and it["url"] not in found:
                        found[it["url"]] = (it, lp.snap, lp)
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
        return
    if _activity_blocked(ctx, "dated_news"):
        return
    ranked = sorted(found.values(), key=lambda x: (x[0]["datetime"][:19], x[0]["title"]), reverse=True)[:limit]
    for it, snap, _lp in ranked:
        evs = [ctx.add_evidence(snap, source_class=COMPANY_OWNED, span=it["title_span"] or it["title"],
                                effective_date=it["datetime"][:10], method="website_news_v2", licence=LICENCE)]
        if it.get("date_span"):
            evs.append(ctx.add_evidence(snap, source_class=COMPANY_OWNED, span=it["date_span"],
                                        effective_date=it["datetime"][:10], method="website_news_v2", licence=LICENCE))
        ctx.add_claim("dated_news", "activity", f"{it['title']} ({it['datetime']})", evs, as_of=it["datetime"][:10],
                      match={"method": "verified_site_news", "detail": f"{it['url']} on the verified site"})


# ---------------------------------------------------------------- hiring signal
JOB_PROBES = ("/karriere/", "/jobb/", "/jobbe-hos-oss/", "/ledige-stillinger/", "/stillinger/", "/careers/", "/jobs/")
JOB_HEADING = re.compile(r"(?i)\b(karriere|ledige stillinger|stillinger|jobb(e)? hos oss|careers?|jobs|vacancies|"
                         r"rekruttering)\b")
ATS_POSTING = re.compile(r"(?i)(webcruiter\.no|jobbnorge\.no|recman\.(no|page)|teamtailor\.com|hrmanager\.no|"
                         r"easycruit\.com|jobylon\.com|reachmee\.com|varbi\.com|smartrecruiters\.com)")


def _careers_page_ok(page: Page, domain: str, home: Page, probed: bool) -> bool:
    """A real careers page: on the verified domain, not the front page (or a copy of it served at another
    path), and with a jobs word in its path or heading; a probed page needs the jobs word in its heading."""
    path = urlsplit(page.url).path
    if not same_site(page.host, domain) or path in ("", "/") or is_parked(page):
        return False
    if page.snap.sha256 == home.snap.sha256 or page.text == home.text:
        return False
    head = " ".join([page.soup.title.get_text(" ", strip=True) if page.soup.title else ""] +
                    [h.get_text(" ", strip=True) for h in page.soup.find_all(["h1", "h2"])[:3]])
    if probed:
        return bool(JOB_HEADING.search(head))
    return jobs_url(path) or bool(JOB_HEADING.search(head))


async def collect_hiring(sp: "_Spend", ctx: CompanyContext, pages: list[Page], domain: str, scope: "Scope") -> None:
    """hiring_signal: the careers page URL (linked, or found by probing the usual paths), plus one claim per
    real posting on it (JSON-LD JobPosting naming us, or a linked ATS posting)."""
    site_match = {"method": "verified_site", "detail": f"careers page on verified site {domain}"}
    link = careers_link(pages, domain, scope)
    candidates = [link[0]] if isinstance(link, tuple) else []
    origin = f"{urlsplit(pages[0].url).scheme}://{urlsplit(pages[0].url).netloc}"
    probed = not candidates
    if probed:
        candidates = [origin + p for p in JOB_PROBES]
    page = None
    for url in candidates:
        if not sp.can(1):
            break
        known = next((p for p in pages if p.url.rstrip("/") == url.rstrip("/")), None)
        got = known or (await _fetch_page(sp, url))[0]
        if got and _careers_page_ok(got, domain, pages[0], probed):
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
    try:
        hosts = [h for d, _s, _u in cands[:24] for h in (d, "www." + d)]
        await asyncio.gather(*[_resolve(sp, f, h) for h in hosts])
        for dom, src, start in cands:
            if not sp.can(2):
                budget_note = "per-company request cap reached"
                break
            if src == "guess" and len([v for v in verdicts if v.source == "guess"]) >= 10:
                break
            v = Verdict(dom, src)
            verdicts.append(v)
            if bare(dom) in done:
                v.status = "duplicate"
                continue
            await check_candidate(f, sp, ctx, v, done, registry_host, start)
            if v.status == "proven":
                proven = v
                break
        if proven:
            scope = await prepare_scope(sp, ctx, proven)
            ctx.facts["website_exclusive"] = bool(scope.exclusive and proven.method == "orgnr_on_page")
            ctx.facts["website_exclusive_reason"] = scope.reasons() or "exclusive"
            emit_site(ctx, proven, scope)
            dom = bare(proven.pages[0].host)
            if scope.exclusive:
                sp.cap = VERIFIED_CAP
                for step in (collect_dated_news, collect_hiring):
                    try:
                        await step(sp, ctx, proven.pages, dom, scope)
                    except _OutOfRequests:
                        pass
                try:
                    await sitemap_activity(f, sp, ctx, proven.pages[0], dom, scope)
                except _OutOfRequests:
                    pass
    except BudgetExhausted:
        budget_note = "budget_exhausted"
    except _OutOfRequests:
        budget_note = "per-company request cap reached"
    finally:
        ctx.facts["website_candidates"] = [v.summary() for v in verdicts] + \
            [{"domain": d, "source": s, "status": "not_checked"} for d, s, _ in cands[len(verdicts):]]
        ctx.facts["website_requests"] = sp.used

    if proven:
        ctx.module_states["website"] = "available"
        return
    if budget_note == "budget_exhausted":
        ctx.mark("official_website", SECTION, "failed", note="budget_exhausted")
        ctx.module_states["website"] = "failed"
        return

    # Absence is claimed only from definitive answers. A failed DNS lookup, or a site that resolved but
    # could not be reached on every candidate, is a failure (transient: the retry pass tries again).
    answered = [v for v in verdicts if v.status not in ("no_dns", "dns_error", "duplicate", "not_checked")]
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
            ev = ctx.add_evidence(p.snap, source_class=COMPANY_OWNED, span=s, method=METHOD, licence=LICENCE)
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
    ambiguous = [v for v in verdicts if v.status in ("name_only", "related", "ambiguous")]
    blocked = [v for v in verdicts if v.status == "blocked" and v.source == "registry"]
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
