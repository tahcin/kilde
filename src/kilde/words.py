"""Word lists shared by the website connector and the publication gate.

Industry and trade words say what a company does, not which company it is ('Radio Sandefjord AS' and
'Radio Tønsberg AS' share 'radio'), so they never identify a company on their own. Person names come from
Statistics Norway's name statistics (data/person_names.json, NLOD 2.0 / CC BY 4.0): a domain or handle that is
only a common first name or surname ('maria.no', 'espen.no') says nothing about which company it is.
"""
from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path

NAMES_PATH = Path(__file__).resolve().parent / "data" / "person_names.json"

# Trade, industry and form words (folded to ASCII): shared by many unrelated companies.
INDUSTRY_WORDS = {
    "radio", "tv", "avis", "film", "foto", "photo", "musikk", "music", "media", "medie", "studio", "kunst", "art",
    "galleri", "teater", "kino", "event", "events", "bowling", "golf", "fitness", "trening", "yoga", "spa",
    "bil", "biler", "auto", "bilverksted", "verksted", "mek", "mekaniske", "mekanisk", "bygg", "byggservice",
    "bygge", "anlegg", "graving", "betong", "maskin", "maskiner", "entreprenor", "entreprenorer", "tak", "maling",
    "ror", "rorlegger", "vvs", "ventilasjon", "varme", "kulde", "elektro", "el", "elektriker", "snekker",
    "trevare", "glass", "stal", "regnskap", "regnskapskontor", "revisjon", "advokat", "advokatfirma",
    "advokatfirmaet", "eiendom", "eiendommer", "eigedom", "eiendomsmegling", "megler", "holding", "holdings",
    "invest", "investering", "investment", "capital", "kapital", "finans", "partner", "partners", "management",
    "ventures", "venture", "trading", "handel", "import", "export", "agentur", "agenturer", "produksjon",
    "industri", "industrier", "transport", "logistikk", "frakt", "taxi", "service", "services", "drift",
    "consult", "consulting", "utvikling", "data", "design", "digital", "it", "tech", "teknikk", "systems",
    "system", "solutions", "marine", "maritime", "shipping", "offshore", "energi", "energy", "kraft", "sol",
    "solar", "vind", "bank", "sparebank", "forsikring", "fisk", "sjomat", "seafood", "havbruk", "oppdrett",
    "landbruk", "gard", "gaard", "skog", "sag", "mat", "bakeri", "kafe", "cafe", "restaurant", "hotell", "hotel",
    "kiosk", "butikk", "apotek", "optiker", "frisor", "salong", "klinikk", "tannlege", "lege", "legesenter",
    "helse", "omsorg", "pleie", "fysioterapi", "bemanning", "renhold", "vakt", "sikkerhet", "skole",
    "trafikkskole", "barnehage", "senter", "sport", "utleie", "salg", "marina", "camping", "reise", "reiser",
    "travel", "interior", "mobler", "blomster", "dyreklinikk", "veterinaer", "norge", "norway", "norsk", "norske",
    "nordic", "norden", "group", "gruppen", "gruppe", "konsern", "global", "international", "selskap",
    "selskapet", "forening", "foreningen", "stiftelse", "stiftelsen", "borettslag", "sameie", "sameiet", "lag",
    "klubb", "og", "and", "the", "of", "i", "as", "asa", "official", "offisiell", "no", "hq",
}


def fold(s: str) -> str:
    s = s.lower().replace("æ", "ae").replace("ø", "o").replace("å", "a")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


@lru_cache(maxsize=1)
def _names() -> tuple[frozenset[str], frozenset[str]]:
    try:
        d = json.loads(NAMES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset(), frozenset()
    given = frozenset(fold(x) for x in d.get("given") or [] if x)
    sur = frozenset(fold(x) for x in d.get("surnames") or [] if x)
    return given, sur


def given_name(word: str) -> bool:
    return fold(word) in _names()[0]


def surname(word: str) -> bool:
    return fold(word) in _names()[1]


def person_word(word: str) -> bool:
    return given_name(word) or surname(word)


def generic_word(word: str) -> bool:
    """A trade, industry, form or filler word that does not identify a company."""
    return fold(word) in INDUSTRY_WORDS


def _alnum(s: str) -> str:
    return "".join(c for c in fold(s) if c.isalnum())


def person_runs(words: list[str]) -> list[list[str]]:
    """Runs in a name that read as a person's name: a first name followed by more first names or surnames
    ('Lisa Husby' in 'LISA HUSBY KUNST AS')."""
    out, i = [], 0
    ws = [fold(w) for w in words]
    while i < len(ws):
        if given_name(ws[i]) and i + 1 < len(ws) and person_word(ws[i + 1]):
            j = i + 1
            while j + 1 < len(ws) and person_word(ws[j + 1]):
                j += 1
            out.append(ws[i:j + 1])
            i = j + 1
        else:
            i += 1
    return out


def social_tokens(words: list[str], places: set[str] = frozenset()) -> list[str]:
    """Tokens of a legal name that identify the company in a social handle: four letters or more, not a trade,
    industry, form or place word, and not part of a person's name in the legal name."""
    persons = {w for run in person_runs(words) for w in run}
    out = []
    for w in words:
        t = _alnum(w)
        if len(t) >= 4 and not t.isdigit() and not generic_word(t) and t not in places and t not in persons:
            out.append(t)
    return out


def person_handle(handle: str, words: list[str], places: set[str] = frozenset()) -> bool:
    """The handle is a person's name taken from the legal name (first name and surname, optionally with trade
    words: 'lisa.husby.kunst' for LISA HUSBY KUNST AS) and carries no other distinctive token of the company:
    a personal account, not the company's profile."""
    h = _alnum(handle)
    own = social_tokens(words, places)
    for run in person_runs(words):
        if len(run) >= 2 and all(_alnum(w) in h for w in (run[0], run[-1])) and not any(t in h for t in own):
            tail = h[h.find(_alnum(run[-1])) + len(_alnum(run[-1])):]
            if tail in ("as", "asa"):
                continue  # the name with its legal form glued on ('leifhansenas') is the company's handle
            return True
    return False


REGION_SUFFIX = {"norway", "norge", "norden", "nordic", "nordics", "sweden", "sverige", "denmark", "danmark",
                 "finland", "suomi", "germany", "deutschland", "brasil", "brazil", "usa", "america", "americas",
                 "canada", "uk", "us", "na", "eu", "europe", "asia", "apac", "emea", "latam", "global", "intl",
                 "international", "careers", "jobs", "karriere", "jobb", "news", "nyheter", "press", "presse",
                 "ir", "investors", "support", "help", "kundeservice"}


def regional_variant(handle: str, others: list[str]) -> bool:
    """The handle is another handle of the same brand with a country, region or department ending
    ('equinor_uk', 'equinor_de', 'brandcareers' beside 'equinor', 'brand'): a regional or department
    account, which may be another legal entity's; the main brand handle is the company's profile."""
    h = handle.lower().strip("/@")
    for o in others:
        o = o.lower().strip("/@")
        if not o or o == h or not h.startswith(o):
            continue
        rest = h[len(o):].lstrip("_-.")
        if rest in ("as", "asa", "sa", "official", "offisiell"):
            continue
        if rest in REGION_SUFFIX or (len(rest) == 2 and rest.isalpha() and h[len(o)] in "_-."):
            return True
    return False
