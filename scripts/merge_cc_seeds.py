"""Merge Common Crawl org-number pairs into the declared website-candidate cache as source `commoncrawl`.

Input, either:
  --state  the CC build's state.sqlite (preferred; read through a read-only sqlite backup, safe while the
           build runs). One row per fetched front, contact, about or privacy page in CC-MAIN-2026-39 with every
           org number printed next to an org-number label (MOD11-valid) and the 40 characters before it.
  --pairs  cc_pairs.jsonl.gz from the build's finalize (organisation_number, domain, page_url, ...). It has
           only universe, non-ENK numbers and no context, so the shared-domain and credit-text rules see less.

The seeds stay CANDIDATES: the website connector re-fetches every domain live and publishes it only with
proof on the site (normally our org number again), the collision check and the exclusivity test.

Rules applied here, in order (each counted in stats.commoncrawl_merge.dropped):
  bad_org            not 9 digits or fails MOD11
  not_in_universe    org number not in the company universe
  enk                sole proprietorship (ENK): excluded, as in the CC build, so no personal data is harvested
  non_company_host   social, directory, booking, registry/aggregator (Brreg, Proff, Purehelp ...) or
                     government host, or not a registrable domain
  crowded_domain     a domain whose pages carry more than MAX_ORGS_PER_DOMAIN org numbers (counting every number
                     printed, also ENK and non-universe ones), or one page with 3+, is a directory, a client
                     list or a portal: every pair on it is dropped
  shared_unnamed     a domain carrying 2 org numbers keeps only the org(s) it is "named for" (the domain label
                     carries a distinctive word of the legal name, or it is the org's registry website); the
                     other org is usually the web agency, accountant, payment provider or a co-tenant
  credit_text        the text before the number credits a supplier ("utviklet av", "levert av", "behandles av",
                     "webbyra", "powered by" ...) and the domain does not carry the org's name
  credited_elsewhere an org dropped by the two rules above is a supplier printed on clients' sites, so its
                     other domains that do not carry its name are dropped too
  many_domains       an org on more than MAX_DOMAINS_PER_ORG domains keeps only the domains that carry its name
  other_site_unnamed an org with a registry website keeps only CC domains that carry its name (its registry
                     site is tried first anyway; an unnamed extra domain is more often a client or a portal)
A pair whose domain is the org's website in the universe file is kept (the live registry entry may have
changed since) and counted as pairs_equal_to_universe_website; the connector de-duplicates candidates.

Domains are normalised to the registered domain (company-specific host on site builders) with the same
`site_key` as scripts/build_website_seeds.py. Re-running replaces every `commoncrawl` source in the seed file,
so the merge is idempotent and can be repeated whenever the CC build writes newer pairs. Output order is
deterministic: orgs sorted, each org's list by (number of sources desc, domain).

Usage (tldextract gives the same bundled public-suffix list on every run, so pin it):
  uv run --frozen --with tldextract==5.4.0 python scripts/merge_cc_seeds.py \
      --state .../cc/state.sqlite --universe .../dl_signalpost-company-universe-2025.jsonl.gz
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "src" / "kilde" / "data" / "website_seeds.json.gz"
SOURCE = "commoncrawl"
CRAWL = "CC-MAIN-2026-39"
MAX_ORGS_PER_DOMAIN = 2   # 3+ org numbers across a domain's pages: directory or client list (judge() uses 3+ per page)
MAX_DOMAINS_PER_ORG = 3   # an org printed on 4+ domains is a mass credit unless the domain carries its name

# Registries, company-data aggregators and public bodies: they print many org numbers and are never a
# company's own site. (Social, directory and booking hosts come from build_website_seeds.BLOCKED.)
NON_COMPANY = {
    "brreg.no", "proff.no", "proff.se", "proff.dk", "purehelp.no", "forvalt.no", "regnskapstall.no",
    "opencorporates.com", "allabolag.se", "bedriftsdatabasen.no", "firmanett.no", "enirocom.no", "180.no",
    "gulesider.no", "1881.no", "finn.no", "kunngjoringer.no", "bizdb.no", "dnb.com", "northdata.com",
    "northdata.de", "doffin.no", "anskaffelser.no", "lovdata.no", "regjeringen.no", "stortinget.no", "ssb.no",
    "skatteetaten.no", "altinn.no", "nav.no", "norge.no", "digdir.no", "udir.no", "politiet.no", "domstol.no",
    "vegvesen.no", "statsforvalteren.no", "mattilsynet.no", "arbeidstilsynet.no", "finanstilsynet.no", "dsb.no",
    "miljodirektoratet.no", "helsedirektoratet.no", "fhi.no", "nkom.no", "kartverket.no", "patentstyret.no",
    "kommune.no", "fylkeskommune.no", "helsenorge.no", "lanekassen.no", "uio.no", "ntnu.no",
}
GOV_SUFFIXES = (".gov", ".gov.uk", ".mil", ".kommune.no", ".fylkeskommune.no")
GENERIC = {
    "as", "asa", "sa", "da", "ans", "ba", "nuf", "ks", "enk", "ltd", "og", "and", "the", "of", "i", "for", "av",
    "holding", "holdings", "invest", "investering", "eiendom", "eiendommer", "eigedom", "bygg", "byggservice",
    "elektro", "transport", "maskin", "service", "drift", "consult", "consulting", "regnskap", "norge", "norway",
    "norsk", "nordic", "group", "gruppen", "partner", "partners", "utvikling", "handel", "auto", "data", "media",
    "design", "solutions", "systems", "tech", "teknikk", "entreprenor", "borettslag", "sameiet", "stiftelsen",
    "forening", "foreningen", "selskap", "senter", "avd", "avdeling", "company", "management", "drift",
}


def mod11(org: str) -> bool:
    if len(org) != 9 or not org.isdigit():
        return False
    c = 11 - sum(int(a) * b for a, b in zip(org[:8], (3, 2, 7, 6, 5, 4, 3, 2))) % 11
    c = 0 if c == 11 else c
    return c != 10 and c == int(org[8])


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def name_forms(word: str) -> set[str]:
    """ASCII spellings of a Norwegian word as it appears in domains (å as a or aa, ø as o or oe, æ as ae or e)."""
    w = word.lower()
    out = set()
    for aa, oe, ae in (("a", "o", "ae"), ("aa", "oe", "ae"), ("a", "o", "e")):
        out.add(re.sub(r"[^a-z0-9]", "", _ascii(w.replace("å", aa).replace("ø", oe).replace("æ", ae))))
    return {x for x in out if x}


def name_tokens(name: str) -> list[set[str]]:
    words = re.findall(r"[\wæøåÆØÅ]+", (name or "").lower())
    return [f for f in (name_forms(w) for w in words if w not in GENERIC and not w.isdigit()) if f]


def domain_names_org(domain: str, name: str, registry_site: str | None) -> bool:
    """The domain is the org's own by name: its registry website, or its first label carries a distinctive
    word (4+ letters) of the legal name, or all distinctive words run together."""
    if registry_site and registry_site == domain:
        return True
    first = domain.split(".")[0]
    if first.startswith("xn--"):
        try:
            first = first.encode("ascii").decode("idna")  # kjøkkenhuset, not xn--kjkkenhuset-...
        except UnicodeError:
            pass
    labels = name_forms(first)
    toks = name_tokens(name)
    if any(len(f) >= 4 and f in label for forms in toks for f in forms for label in labels):
        return True
    if toks:
        joined = "".join(sorted(forms, key=len)[0] for forms in toks)
        if len(joined) >= 3 and any(joined in label for label in labels):
            return True
    return False


def non_company(domain: str) -> bool:
    d = domain.lower()
    return d in NON_COMPANY or any(d.endswith("." + x) for x in NON_COMPANY) or d.endswith(GOV_SUFFIXES)


def default_key() -> Callable[[str], str | None]:
    """build_website_seeds.site_key: registered domain, builder subdomain kept, social/directory hosts None."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_website_seeds as B  # noqa: E402  (needs tldextract)
    B._tld()
    return B.site_key


def load_universe(path: Path, key: Callable[[str], str | None]) -> dict[str, dict]:
    out = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            out[r["organisation_number"]] = {"name": r.get("name") or "", "legal_form": r.get("legal_form") or "",
                                             "site": key(r["website"]) if r.get("website") else None}
    return out


# Text just before the number that credits a supplier rather than naming the site's operator.
CREDIT = re.compile(r"(?i)\b(utviklet|laget|designet|design|produsert|programmert|hostet|levert|behandles|"
                    r"h\u00e5ndteres)\s+(av|i samarbeid med)\b|i samarbeid med|webdesign|webbyr|nettbyr|reklamebyr|"
                    r"powered by|made by|developed by|designed by|built by")


def pages_from_pairs(pairs: Iterable[dict]) -> list[dict]:
    """cc_pairs.jsonl.gz rows as one-org pages (co-printed numbers and context are not in that file)."""
    return [{"domain": p.get("domain") or "", "orgs": {str(p.get("organisation_number") or ""): ""}} for p in pairs]


def pages_from_state(path: Path) -> tuple[list[dict], dict]:
    """Every fetched page with all org numbers it printed and the text just before each, from a read-only
    snapshot of the CC build's state.sqlite (sqlite backup API: a consistent copy, the build is never touched)."""
    import sqlite3
    import time
    mem = sqlite3.connect(":memory:")
    for attempt in range(10):  # the build commits every 100 records; a busy database is retried
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            src.backup(mem)
            break
        except sqlite3.OperationalError:
            time.sleep(3 * (attempt + 1))
        finally:
            src.close()
    else:
        raise SystemExit(f"could not snapshot {path}")
    rows = mem.execute("select dom, url, orgs from rec where status='ok' and orgs is not null and orgs != '{}' "
                       "order by dom, url").fetchall()
    stats = dict(mem.execute("select status, count(*) from rec group by status").fetchall())
    mem.close()
    pages = [{"domain": d, "orgs": json.loads(o)} for d, _u, o in rows]
    return pages, {"records_" + k: v for k, v in sorted(stats.items())}


def select_pairs(pages: Iterable[dict], universe: dict[str, dict], key: Callable[[str], str | None]
                 ) -> tuple[dict[str, list[str]], Counter]:
    """Apply the rules in the module docstring to pages ({"domain", "orgs": {org: text before it}}).
    Returns {org: sorted domains} and drop counts (per org-domain pair)."""
    dropped: Counter = Counter()
    printed: dict[str, set[str]] = defaultdict(set)   # domain -> every valid org number on its pages
    listing: set[str] = set()                         # domains with a page that prints 3+ org numbers
    credit_ctx: set[tuple[str, str]] = set()
    cand: set[tuple[str, str]] = set()
    for pg in pages:
        orgs = sorted(o for o in (pg.get("orgs") or {}) if mod11(o))
        dropped["bad_org"] += len(pg.get("orgs") or {}) - len(orgs)
        dom = key(str(pg.get("domain") or ""))
        if not dom or non_company(dom):
            dropped["non_company_host"] += len(orgs)
            continue
        printed[dom].update(orgs)
        if len(orgs) > MAX_ORGS_PER_DOMAIN:
            listing.add(dom)
        for org in orgs:
            u = universe.get(org)
            if u is None:
                dropped["not_in_universe"] += 1
            elif u["legal_form"].upper() == "ENK":
                dropped["enk"] += 1
            else:
                cand.add((org, dom))
                if CREDIT.search(pg["orgs"].get(org) or ""):
                    credit_ctx.add((org, dom))

    keep: set[tuple[str, str]] = set()
    credited: set[str] = set()
    for org, dom in sorted(cand):
        u = universe[org]
        everyone = printed[dom]
        if dom in listing or len(everyone) > MAX_ORGS_PER_DOMAIN:
            dropped["crowded_domain"] += 1
            continue
        named = domain_names_org(dom, u["name"], u["site"])
        if len(everyone) > 1 and not named:
            dropped["shared_unnamed"] += 1
            credited.add(org)
            continue
        if (org, dom) in credit_ctx and not named:
            dropped["credit_text"] += 1
            credited.add(org)
            continue
        keep.add((org, dom))

    by_org: dict[str, set[str]] = defaultdict(set)
    for org, dom in keep:
        by_org[org].add(dom)
    out: dict[str, list[str]] = {}
    for org in sorted(by_org):
        u = universe[org]
        doms = sorted(by_org[org])
        named = [d for d in doms if domain_names_org(d, u["name"], u["site"])]
        if org in credited:
            dropped["credited_elsewhere"] += len(doms) - len(named)
            doms = named
        elif len(doms) > MAX_DOMAINS_PER_ORG:
            dropped["many_domains"] += len(doms) - len(named)
            doms = named
        elif u["site"]:
            dropped["other_site_unnamed"] += len(doms) - len(named)
            doms = named
        if doms:
            out[org] = doms
    return out, dropped


def merge(doc: dict, selected: dict[str, list[str]]) -> tuple[dict, dict]:
    """Replace every `commoncrawl` source in the seed document with `selected`. Other sources are untouched."""
    seeds = doc.get("seeds") or {}
    before = {o for o, items in seeds.items() if any(SOURCE in it.get("sources", ()) for it in items)}
    had_other = {o for o, items in seeds.items() if any(set(it.get("sources", ())) - {SOURCE} for it in items)}
    new: dict[str, list[dict]] = {}
    for org in sorted(set(seeds) | set(selected)):
        items: dict[str, set[str]] = {}
        for it in seeds.get(org) or []:
            srcs = set(it.get("sources") or ()) - {SOURCE}
            if srcs and it.get("domain"):
                items.setdefault(it["domain"], set()).update(srcs)
        overlap_dom = 0
        for d in selected.get(org, ()):
            if d in items:
                overlap_dom += 1
            items.setdefault(d, set()).add(SOURCE)
        if items:
            new[org] = [{"domain": d, "sources": sorted(s)}
                        for d, s in sorted(items.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    out = dict(doc)
    out["seeds"] = new
    sel = set(selected)
    counts = {
        "orgs": len(sel),
        "pairs": sum(len(v) for v in selected.values()),
        "domains": len({d for v in selected.values() for d in v}),
        "orgs_new_to_cache": len(sel - had_other),
        "orgs_already_with_other_seeds": len(sel & had_other),
        "pairs_domain_already_seeded": sum(1 for o, v in selected.items() for d in v
                                           if any(it["domain"] == d and set(it.get("sources", ())) - {SOURCE}
                                                  for it in seeds.get(o) or [])),
        "pairs_equal_to_universe_website": 0,
        "orgs_previously_commoncrawl": len(before),
        "orgs_added_since_previous_merge": len(sel - before),
        "orgs_removed_since_previous_merge": len(before - sel),
    }
    return out, counts


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--state", type=Path, help="the CC build's state.sqlite (read-only snapshot; preferred: it "
                     "keeps every org number printed on a page and the text before it)")
    src.add_argument("--pairs", type=Path, help="cc_pairs.jsonl.gz from the CC build's finalize")
    ap.add_argument("--universe", type=Path, required=True, help="company universe jsonl.gz (name, legal_form, website)")
    ap.add_argument("--cc-seeds", type=Path, help="cc_seeds.json.gz from the same finalize (copies its build stats)")
    ap.add_argument("--seeds", type=Path, default=SEEDS, help="seed cache to update in place")
    ap.add_argument("--out", type=Path, help="write here instead of --seeds")
    a = ap.parse_args(argv)
    key = default_key()
    universe = load_universe(a.universe, key)
    if a.state:
        pages, records = pages_from_state(a.state)
        digest = hashlib.sha256(json.dumps(pages, sort_keys=True).encode()).hexdigest()
    else:
        with gzip.open(a.pairs, "rt", encoding="utf-8") as fh:
            pages = pages_from_pairs(json.loads(line) for line in fh if line.strip())
        records, digest = {}, sha256(a.pairs)
    selected, dropped = select_pairs(pages, universe, key)
    with gzip.open(a.seeds, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    doc, counts = merge(doc, selected)
    counts["pairs_equal_to_universe_website"] = sum(1 for o, v in selected.items() if universe[o]["site"] in v)
    build = {}
    if a.cc_seeds:
        with gzip.open(a.cc_seeds, "rt", encoding="utf-8") as fh:
            build = (json.load(fh).get("stats") or {}).get("commoncrawl") or {}
    stats = dict(doc.get("stats") or {})
    stats.pop("commoncrawl_added", None)
    stats["commoncrawl_merge"] = {
        "crawl_id": CRAWL, "input": "state" if a.state else "pairs", "input_sha256": digest,
        "pages_with_orgnr": len(pages), **records,
        "thresholds": {"max_orgs_per_domain": MAX_ORGS_PER_DOMAIN, "max_domains_per_org": MAX_DOMAINS_PER_ORG},
        "dropped": dict(sorted(dropped.items())), **counts,
        "build": {k: build[k] for k in sorted(build)},
    }
    by_src: Counter = Counter()
    for items in doc["seeds"].values():
        for s in {s for it in items for s in it["sources"]}:
            by_src[s] += 1
    stats["orgs"] = len(doc["seeds"])
    stats["orgs_by_source_all"] = dict(sorted(by_src.items()))
    doc["stats"] = stats
    doc.setdefault("licences", {})["commoncrawl"] = (
        f"Common Crawl {CRAWL} (https://commoncrawl.org), used under the Common Crawl Terms of Use "
        "(https://commoncrawl.org/terms-of-use); candidates only, crawled pages remain subject to their owners' terms")
    doc["note"] = ("Website CANDIDATES only. Each must be verified on the live site by the website connector. "
                   "Includes Common Crawl candidates (commoncrawl: org number printed on the page, merged by "
                   "scripts/merge_cc_seeds.py; commoncrawl_domain: domain-name match).")
    out = a.out or a.seeds
    data = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    with open(out, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=9, mtime=0) as fh:
        fh.write(data)  # no name, mtime 0: the same input gives a byte-identical file
    print(json.dumps(stats["commoncrawl_merge"], indent=1))


if __name__ == "__main__":
    main()
