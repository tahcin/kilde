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
from bs4 import BeautifulSoup

from ..http import Blocked, BudgetExhausted, Fetcher
from ..model import COMPANY_OWNED, CompanyContext, Evidence, Snapshot

METHOD = "website_identity_v1"
EXTRACT = "website_extract_v1"
LICENCE = "company-owned page; robots.txt allowed"
SECTION = "web_presence"

MAX_REQUESTS = 10         # HTTP requests per company, robots.txt and Brreg name checks included
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
PHONE_TEXT = re.compile(r"(?<![\d])(?:\+|00)?(?:47[\s.\- ]?)?(?:\d[\s.\- ]?){7}\d(?!\d)")
PHONE_LABEL = re.compile(r"(tlf|telefon|phone|mobil|tel\.?|ring oss|sentralbord)", re.I)
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


def name_domains(name: str) -> list[str]:
    """Domains guessed from the legal name, best first: full name then name without generic words, .no then .com."""
    words = [w for w in name_words(name) if re.fullmatch(r"[0-9a-zæøåäöüé]+", w)]
    if not words:
        return []
    core = [w for w in words if w not in GENERIC_WORDS and not w.isdigit()]
    labels: list[str] = []
    for ws in (words, core):
        if not ws:
            continue
        for v in _ascii_variants(ws):
            for lab in ("".join(v), "-".join(v)):
                if lab not in labels:
                    labels.append(lab)
    good = []
    for lab in labels:
        if not (3 <= len(lab) <= 40):
            continue
        if "-" not in lab and (len(lab) < 5 or lab in COMMON_LABELS) and len(words) == 1:
            continue
        if lab in COMMON_LABELS or (len(lab) < 4):
            continue
        if lab.isdigit():
            continue
        good.append(lab)
    return [f"{lab}.{tld}" for tld in ("no", "com") for lab in good]


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


def name_check(text: str, words: list[str]) -> tuple[str | None, str | None]:
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
    norm_ours = [_translit(w) for w in words]
    legal_found = False
    conflict = None
    for em in ENTITY.finditer(text):
        ws = [_translit(w) for w in re.findall(r"[0-9a-zæøåäöé]+", em.group(1).lower().replace("&", " og "))]
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
    """Span showing our organisation number, from visible text or structured data."""
    g = f"{org[:3]}[\\s. ]?{org[3:6]}[\\s. ]?{org[6:]}"
    rx = re.compile(r"(?<![\d.,])(?:NO\s?)?" + g + r"(?:\s?MVA)?(?![\d,])", re.I)
    m = rx.search(page.text)
    if m:
        return _window(page.text, m.start(), m.end(), 30)
    for d in page.jsonld:
        for k in ("taxID", "vatID", "identifier", "leiCode", "organizationNumber", "duns"):
            v = d.get(k)
            vals = v if isinstance(v, list) else [v]
            for x in vals:
                if isinstance(x, dict):
                    x = x.get("value") or x.get("@value")
                if isinstance(x, (str, int)) and re.sub(r"\D", "", str(x)) == org:
                    return json.dumps({k: v}, ensure_ascii=False)[:200]
    return None


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
    return out


def _digits_phones(e: dict) -> set[str]:
    out = set()
    for k in ("telefon", "mobil"):
        d = re.sub(r"\D", "", e.get(k) or "")
        if len(d) >= 8:
            out.add(d[-8:])
    return out


def find_phone(page: Page, wanted: set[str]) -> str | None:
    if not wanted:
        return None
    for m in PHONE_TEXT.finditer(page.text):
        d = re.sub(r"\D", "", m.group(0))
        if len(d) >= 8 and d[-8:] in wanted:
            return _window(page.text, m.start(), m.end(), 25)
    for a in page.soup.find_all("a", href=re.compile(r"^tel:", re.I)):
        d = re.sub(r"\D", "", unquote(a["href"]))
        if len(d) >= 8 and d[-8:] in wanted:
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

    def summary(self) -> dict[str, Any]:
        return {"domain": self.domain, "source": self.source, "status": self.status, "method": self.method,
                "final_url": self.final_url, "other_orgnrs": sorted(self.others)[:5] or None}


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
    words = name_words(e.get("navn") or "")
    name_span = name_page = conflict = None
    for p in pages:
        s, c = name_check(p.text, words)
        if s and not name_span:
            name_span, name_page = s, p
        conflict = conflict or c
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
        s = find_phone(p, wanted)
        if s:
            v.status, v.method = "proven", "phone_and_name"
            v.detail = "registry phone number and legal name shown on the site"
            v.proof = [(p, s), (name_page, name_span)]
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

    def can(self, n: int = 1) -> bool:
        return self.used + n <= MAX_REQUESTS and self.f.budget.remaining_requests() > RESERVE + n

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


async def resolves(f: Fetcher, host: str) -> bool:
    for rtype in ("A", "AAAA"):
        f.budget.dns_lookups += 1
        try:
            ans = await dns.asyncresolver.resolve(host, rtype, lifetime=DNS_TIMEOUT)
            if len(ans):
                return True
        except (dns.exception.DNSException, OSError):
            continue
    return False


async def reachable(host: str, port: int = 443) -> bool:
    """A TCP connect before any HTTP, so dead hosts do not burn HTTP retries and timeouts."""
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_PROBE)
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
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
    live: list[str] = []
    for h in hosts:
        if await resolves(f, h):
            live.append(h)
    if not live:
        v.status = "no_dns"
        return
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
        if not await reachable(h, 443 if scheme == "https" else 80):
            if scheme == "https" and h not in http_later:
                http_later.append(h)
            continue
        url = f"{scheme}://{h}/"
        if start_url and host_of(start_url) == h:
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
    judge(v, ctx.org, e, registry_host)
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
    for d in name_domains(e.get("navn") or ""):
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
        if segs[0] == "profile.php":
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
    handle = segs[-1].lstrip("@").lower()
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
_SISTER_CACHE: dict[str, list[tuple[str, str]]] = {}  # label -> [(org, registered name)] across companies


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

    def _num_hit(self, text: str, nums: list[str]) -> bool:
        return any(re.search(rf"(?<!\d){n[:3]}[\s.\u00a0]?{n[3:6]}[\s.\u00a0]?{n[6:]}(?!\d)", text)
                   for n in nums if len(n) == 9)

    def names_ours(self, text: str) -> bool:
        if self._num_hit(text, [self.org]):
            return True
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
        for level in range(8):
            if node is None or not hasattr(node, "get_text"):
                break
            own = node.get_text(" ", strip=True)
            texts = [own]
            if level == 0 and hasattr(node, "get"):
                texts += [unquote(str(node.get("href") or "")), str(node.get("aria-label") or ""), str(node.get("title") or "")]
            if level <= 2:
                sib = node.find_previous_sibling()
                if sib is not None and hasattr(sib, "get_text"):
                    texts.append(sib.get_text(" ", strip=True)[:200])
            t = " ".join(texts)
            o, s = self.names_ours(t), self.names_sister(t)
            if o and s:
                return "both"
            if s:
                return "sister"
            if o:
                return "ours"
            if len(own) > 800:
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
            _SISTER_CACHE[lab] = hits
        for org, _name in hits:
            if org and org != ctx.org:
                out[lab] = org
                break
    return out


# ---------------------------------------------------------------- derived items
def collect_socials(pages: list[Page], words: list[str], domains: set[str], scope: Scope | None = None
                    ) -> tuple[list[tuple[str, str, Page, str]], list[str]]:
    """Social profile links from the verified site's pages and JSON-LD sameAs.

    A link is kept when its handle carries the company's name or domain; or, for handle-less ids
    (profile.php?id=, numeric LinkedIn ids, YouTube channel ids) and icon links with no other text, when
    it is the only link for that platform and sits in the site's header, footer or structured data.
    Links to partners, clients, venues and sister companies are dropped."""
    scope = scope or Scope("", words)
    found: dict[str, dict[str, list]] = {}
    dropped: list[str] = []
    ours_sq = _squash(" ".join(words))
    for p in pages:
        for a in p.soup.find_all("a", href=True):
            c = clean_social(a["href"])
            if not c:
                continue
            if not scope.accept(a, p):
                dropped.append(c[1])
                continue
            label = a.get_text(" ", strip=True)
            iconic = not label or bool(re.fullmatch(r"(?i)(f[øo]lg oss( på)?|follow us( on)?)?\s*(facebook|instagram|"
                                                    r"linkedin|youtube|twitter|x|tiktok)?", label))
            rec = found.setdefault(c[0], {}).setdefault(c[1], [p, a["href"][:300], False, False])
            rec[2] = rec[2] or _in_chrome(a)
            rec[3] = rec[3] or iconic
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
                        found.setdefault(c[0], {}).setdefault(c[1], [p, json.dumps({"sameAs": s})[:300], True, True])
    keys = {re.sub(r"[^a-z0-9]", "", _translit(w)) for w in words if len(w) >= 4}
    keys.add(re.sub(r"[^a-z0-9]", "", _translit("".join(words))))
    keys |= {re.sub(r"[^a-z0-9]", "", d.split(".")[0]) for d in domains if d}
    keys = {k for k in keys if len(k) >= 4}
    out = []
    for plat, urls in found.items():
        for u, (p, s, chrome, iconic) in urls.items():
            path = urlsplit(u).path
            handle = re.sub(r"[^a-z0-9]", "", _translit(unquote(path.rsplit("/", 1)[-1])))
            id_only = "profile.php" in path or handle.isdigit() or bool(re.match(r"/channel/UC", path))
            named = any(k in handle for k in keys)
            if named or (chrome and len(urls) == 1 and (id_only or iconic)):
                out.append((plat, u, p, s))
            else:
                dropped.append(u)
    return out, dropped


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


def extract_contacts(pages: list[Page], domain: str, registry_phones: set[str] = frozenset(),
                     scope: Scope | None = None) -> tuple[list, list, int]:
    """(emails, phones, how many were dropped as not provably ours) from the verified site's pages."""
    scope = scope or Scope("", [])
    emails: dict[str, tuple[Page, str]] = {}
    phones: dict[str, tuple[Page, str]] = {}
    skipped: set[str] = set()

    def put(store: dict, key: str, page: Page, span: str, el) -> None:
        if key in store or key in skipped:
            return
        if scope.accept(el, page):
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
            n = norm_phone(m.group(0))
            labels = re.findall(r"(?i)tlf|telefon|phone|mobil|tel\b|ring oss|sentralbord|faks|fax|telefaks", before)
            if labels and re.fullmatch(r"(?i)faks|fax|telefaks", labels[-1]):
                continue  # the nearest label says this is a fax number
            if n and PHONE_LABEL.search(before) and n not in phones and n not in skipped:
                put(phones, n, p, _window(p.text, m.start(), m.end(), 20), _element_for(p, m.group(0).strip()))
    # only addresses on the company's own domain; generic mailboxes first
    own = [(a, ps) for a, ps in emails.items() if same_site(a.rsplit("@", 1)[1], domain)]
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


JOBS_TOKENS = {"stilling", "stillinger", "ledige", "karriere", "jobb", "jobbe", "jobs", "job", "career", "careers",
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


async def fetch_news(sp: _Spend, ctx: CompanyContext, pages: list[Page], domain: str, scope: Scope) -> None:
    link = news_listing_link(pages, domain)
    if not link or not sp.can(1):
        return
    page = next((p for p in pages if p.url.rstrip("/") == link.rstrip("/")), None)
    if page is None:
        page, _st = await _fetch_page(sp, link)
        if page is None or not same_site(page.host, domain):
            return
    for val, span in news_items(page, domain, scope):
        ev = ctx.add_evidence(page.snap, source_class=COMPANY_OWNED, span=span, effective_date=val["date"],
                              method="website_news_v1", licence=LICENCE)
        ctx.add_claim("site_news", "activity", val, ev, as_of=val["date"],
                      match={"method": "verified_site_news", "detail": f"news listing {page.url} on verified site"})


async def prepare_scope(sp: _Spend, ctx: CompanyContext, v: Verdict) -> Scope:
    """Fetch a contact page if none was read yet, then decide whether the site is shared."""
    home = v.pages[0]
    if not any(INTERNAL_PRIORITY[0].search(urlsplit(p.url).path) for p in v.pages):
        for link in internal_links(home):
            if INTERNAL_PRIORITY[0].search(link) or INTERNAL_PRIORITY[1].search(link):
                if sp.can(1) and link not in {p.url for p in v.pages}:
                    p, _st = await _fetch_page(sp, link)
                    if p and same_site(p.host, home.host):
                        v.pages.append(p)
                break
    words = name_words((ctx.registry or {}).get("navn") or "")
    others = dict(v.others)
    for p in v.pages:
        for num, s in other_orgnrs(p, ctx.org).items():
            others.setdefault(num, s)
    others = {k: (x[1] if isinstance(x, tuple) else x) for k, x in others.items()}
    sisters = await confirm_sisters(sp, ctx, sister_labels(v.pages, words))
    return Scope(ctx.org, words, others, sisters)


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
    ctx.add_claim("official_website", SECTION, url, proof_evs, confidence=conf, match=m)
    site_match = {"method": "verified_site", "detail": f"from {domain}, proven by {v.method}"}
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
    if desc and not GENERIC_DESC.search(desc) and len(desc) >= 15:
        if scope.shared and not scope.page_ours_only(home) and not (
                scope.names_ours(desc) and not scope.names_sister(desc)):
            ctx.mark("website_description", SECTION, "ambiguous", note=unsure)
        else:
            ctx.add_claim("website_description", SECTION, desc, _ev(ctx, home, span), match=site_match)
    lang = home.soup.find("html")
    if lang and lang.get("lang"):
        ctx.add_claim("site_language", SECTION, lang["lang"].strip()[:12],
                      _ev(ctx, home, f'<html lang="{lang["lang"].strip()[:12]}">'), match=site_match)

    emails, phones, skipped = extract_contacts(v.pages, domain, _digits_phones(ctx.registry or {}), scope)
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
        ctx.add_claim("social_profile", SECTION, {"platform": plat, "url": u}, _ev(ctx, p, s),
                      match={"method": "linked_from_verified_site", "detail": f"outbound link on {p.url}"})
    if scope.shared and not socials and dropped:
        ctx.mark("social_profile", SECTION, "ambiguous", note=unsure)
    for p in v.pages:
        so = org_structured(p, (ctx.registry or {}).get("navn") or "")
        if so:
            ctx.add_claim("structured_data_organization", SECTION, so[0], _ev(ctx, p, so[1]), match=site_match)
            break
    car = careers_link(v.pages, domain, scope)
    if car == "ambiguous":
        ctx.mark("careers_page", "hiring", "ambiguous", note=unsure)
    elif car:
        ctx.add_claim("careers_page", "hiring", car[0], _ev(ctx, car[1], car[2]), match=site_match)
    return v.pages


# ---------------------------------------------------------------- entry point
async def fetch_website(f: Fetcher, ctx: CompanyContext) -> None:
    e = ctx.registry or {}
    if not e or e.get("_gone") or ctx.facts.get("deleted"):
        ctx.mark("official_website", SECTION, "not_applicable", note="no live registry record")
        return
    cands = candidates(ctx)
    registry_host = next((bare(d) for d, s, _ in cands if s == "registry"), None)
    sp = _Spend(f, ctx)
    verdicts: list[Verdict] = []
    done: set[str] = set()
    proven: Verdict | None = None
    budget_note = None
    try:
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
            emit_site(ctx, proven, scope)
            dom = bare(proven.pages[0].host)
            try:
                await fetch_news(sp, ctx, proven.pages, dom, scope)
                await sitemap_activity(f, sp, ctx, proven.pages[0], dom, scope)
            except (_OutOfRequests,):
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
