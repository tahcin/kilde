"""Merge Kilde's own polite crawl matches into the declared website-candidate cache.

Input:
  --matches  matches.jsonl from the crawl's matcher (crawler/match.py): one row per (org, domain) with the
             class it qualified for (`method`) and any further classes (`also`). `domain` is the site's
             registered domain after redirects.
  --pages    pages.jsonl.gz from the crawl (optional, recommended): every org number each crawled page printed
             with the text around it, so the crowded-domain and credit rules see all numbers, not only matches.
             Read as a stream; a file that is still being written is read up to its last complete line.

Classes become source tags:
  crawl_orgnr        our org number printed next to an org-number label (method orgnr)
  crawl_phone_email  a registry phone, mobile or email that maps to exactly one entity in the whole registry,
                     printed on the site, with the name consistent with the domain or page title (strong_unique)
  crawl_signals      two independent signals among unique legal name, street address and postcode, registry email
                     domain equal to the site domain, name-derived domain (multi_signal)

The seeds stay CANDIDATES: the website connector fetches every domain live and publishes it only with proof
on the site, the name-collision check and the exclusivity test. A seed never proves anything by itself.

Rules, in order (each counted in stats.crawl_merge.dropped, per org-domain pair):
  bad_org             not 9 digits or fails MOD11
  not_in_universe     org number not in the company universe
  enk                 sole proprietorship (ENK): never stored, so no personal data is kept
  non_company_host    social, directory, registry, aggregator or government host, or not a registrable domain
  multi_org_domain    a domain matched to 2+ different orgs (in any class): every pair on it is dropped
  crowded_domain      the domain's pages print 3+ org numbers (directory, client list, portal), as in the CC merge
  other_orgnr         a signal match (no org number) on a domain whose pages print another org number and never ours
  shared_unnamed      the domain prints 2 org numbers and does not carry this org's name (CC merge rule)
  credit_text         the text before our number credits a supplier ("utviklet av", "levert av" ...) and the domain
                      does not carry the org's name (CC merge rule)
  credited_elsewhere, many_domains, other_site_unnamed: the CC merge's per-org rules
Stored per org: the domain and its source tags only (no names, phones, emails or page text).

Re-running replaces every crawl_* source in the seed file (idempotent). Output is deterministic: orgs sorted, each
org's list by (number of sources desc, domain), the gzip written with mtime 0.

Usage (pin tldextract, as for merge_cc_seeds.py):
  uv run --frozen --with tldextract==5.4.0 python scripts/merge_crawl_seeds.py \
      --matches .../crawl/out/matches_all.jsonl --pages .../crawl/out/pages.jsonl.gz --pages .../pages_ng.jsonl.gz \
      --universe .../dl_signalpost-company-universe-2025.jsonl.gz
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_cc_seeds as CC  # noqa: E402

SEEDS = CC.SEEDS
CLASS_SOURCE = {"orgnr": "crawl_orgnr", "strong_unique": "crawl_phone_email", "multi_signal": "crawl_signals"}
SOURCES = tuple(CLASS_SOURCE.values())
LICENCE = ("Kilde's own crawl, 2026-10-14 (KildeCacheBuilder/1.0, robots.txt obeyed, 1 request at a time per "
           "host) of public company front, contact, about and privacy pages on 325,241 domains: registered domains "
           "from the Common Crawl host graph cc-main-2026-jul-aug-sep (.no block) and index, Brreg websites and email "
           "domains, and name-derived domains, all resolved with ProjectDiscovery dnsx 1.3.1 (Common Crawl Terms of "
           "Use for the graph and index); pages matched against Enhetsregisteret bulk data of 2026-10-13 (NLOD 2.0); "
           "only domain and source tags are stored; candidates only, pages remain subject to their owners' terms")


def read_jsonl_gz(path: Path) -> Iterator[dict]:
    """Records of a gzip JSONL file that may still be written to (or have several gzip members): stops at the
    last complete line instead of failing on a truncated tail."""
    with open(path, "rb") as fh:
        d = zlib.decompressobj(zlib.MAX_WBITS | 16)
        tail = b""
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            while chunk:
                try:
                    out = d.decompress(chunk)
                except zlib.error:
                    return
                tail += out
                if d.eof:
                    chunk = d.unused_data
                    d = zlib.decompressobj(zlib.MAX_WBITS | 16)
                else:
                    chunk = b""
                lines = tail.split(b"\n")
                tail = lines.pop()
                for line in lines:
                    if line.strip():
                        try:
                            yield json.loads(line)
                        except ValueError:
                            pass


def _before_number(ctx: str, org: str) -> str:
    """The text before the printed number (the crawl stores some text on both sides)."""
    m = re.search(r"(?<!\d)" + r"[\s. ]?".join((org[:3], org[3:6], org[6:])) + r"(?!\d)", ctx or "")
    return (ctx or "")[:m.start()] if m else (ctx or "")


def printed_numbers(pages: Iterable[dict], key: Callable[[str], str | None]
                    ) -> tuple[dict[str, set[str]], set[str], dict[tuple[str, str], str]]:
    """(domain -> every valid org number its pages print, domains with a page printing 3+, (org, domain) ->
    text before the number). A page counts for the domain it ended on after redirects."""
    printed: dict[str, set[str]] = defaultdict(set)
    listing: set[str] = set()
    before: dict[tuple[str, str], str] = {}
    for pg in pages:
        if pg.get("t") not in (None, "p"):
            continue
        orgs = {o: c for o, c in (pg.get("org") or {}).items() if CC.mod11(o)}
        if not orgs:
            continue
        dom = key(str(pg.get("final") or pg.get("url") or pg.get("dom") or ""))
        if not dom:
            continue
        printed[dom].update(orgs)
        if len(orgs) > CC.MAX_ORGS_PER_DOMAIN:
            listing.add(dom)
        for o, c in sorted(orgs.items()):
            before.setdefault((o, dom), _before_number(str(c or ""), o))
    return printed, listing, before


def select_pairs(matches: Iterable[dict], universe: dict[str, dict], key: Callable[[str], str | None],
                 printed: dict[str, set[str]] | None = None, listing: set[str] | None = None,
                 before: dict[tuple[str, str], str] | None = None) -> tuple[dict[str, dict[str, set[str]]], Counter]:
    """Apply the rules in the module docstring. Returns {org: {domain: source tags}} and drop counts."""
    printed, listing, before = printed or {}, listing or set(), dict(before or {})
    dropped: Counter = Counter()
    cand: dict[tuple[str, str], set[str]] = defaultdict(set)
    for m in matches:
        org = str(m.get("org") or "")
        classes = [m.get("method")] + list(m.get("also") or [])
        tags = {CLASS_SOURCE[c] for c in classes if c in CLASS_SOURCE}
        if not tags:
            continue
        if not CC.mod11(org):
            dropped["bad_org"] += 1
            continue
        u = universe.get(org)
        if u is None:
            dropped["not_in_universe"] += 1
            continue
        if u["legal_form"].upper() == "ENK":
            dropped["enk"] += 1
            continue
        dom = key(str(m.get("domain") or ""))
        if not dom or CC.non_company(dom):
            dropped["non_company_host"] += 1
            continue
        cand[(org, dom)] |= tags
        ev = m.get("evidence") or {}
        if "crawl_orgnr" in tags and isinstance(ev, dict) and ev.get("ctx"):
            before.setdefault((org, dom), _before_number(str(ev["ctx"]), org))

    orgs_on: dict[str, set[str]] = defaultdict(set)
    for org, dom in cand:
        orgs_on[dom].add(org)
    keep: dict[tuple[str, str], set[str]] = {}
    credited: set[str] = set()
    for (org, dom), tags in sorted(cand.items()):
        u = universe[org]
        if len(orgs_on[dom]) > 1:
            dropped["multi_org_domain"] += 1
            continue
        everyone = set(printed.get(dom) or ()) | ({org} if "crawl_orgnr" in tags else set())
        if dom in listing or len(everyone) > CC.MAX_ORGS_PER_DOMAIN:
            dropped["crowded_domain"] += 1
            continue
        named = CC.domain_names_org(dom, u["name"], u["site"])
        if everyone and org not in everyone:
            dropped["other_orgnr"] += 1
            continue
        if len(everyone) > 1 and not named:
            dropped["shared_unnamed"] += 1
            credited.add(org)
            continue
        if CC.CREDIT.search(before.get((org, dom)) or "") and not named:
            dropped["credit_text"] += 1
            credited.add(org)
            continue
        keep[(org, dom)] = tags

    by_org: dict[str, dict[str, set[str]]] = defaultdict(dict)
    for (org, dom), tags in keep.items():
        by_org[org][dom] = tags
    out: dict[str, dict[str, set[str]]] = {}
    for org in sorted(by_org):
        u = universe[org]
        doms = sorted(by_org[org])
        named = [d for d in doms if CC.domain_names_org(d, u["name"], u["site"])]
        if org in credited:
            dropped["credited_elsewhere"] += len(doms) - len(named)
            doms = named
        elif len(doms) > CC.MAX_DOMAINS_PER_ORG:
            dropped["many_domains"] += len(doms) - len(named)
            doms = named
        elif u["site"]:
            dropped["other_site_unnamed"] += len(doms) - len(named)
            doms = named
        if doms:
            out[org] = {d: by_org[org][d] for d in doms}
    return out, dropped


def merge(doc: dict, selected: dict[str, dict[str, set[str]]]) -> tuple[dict, dict]:
    """Replace every crawl_* source in the seed document with `selected`. Other sources are untouched."""
    seeds = doc.get("seeds") or {}
    ours = set(SOURCES)
    before = {o for o, items in seeds.items() if any(ours & set(it.get("sources", ())) for it in items)}
    had_other = {o for o, items in seeds.items() if any(set(it.get("sources", ())) - ours for it in items)}
    new: dict[str, list[dict]] = {}
    for org in sorted(set(seeds) | set(selected)):
        items: dict[str, set[str]] = {}
        for it in seeds.get(org) or []:
            srcs = set(it.get("sources") or ()) - ours
            if srcs and it.get("domain"):
                items.setdefault(it["domain"], set()).update(srcs)
        for d, tags in sorted((selected.get(org) or {}).items()):
            items.setdefault(d, set()).update(tags)
        if items:
            new[org] = [{"domain": d, "sources": sorted(s)}
                        for d, s in sorted(items.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    out = dict(doc)
    out["seeds"] = new
    sel = set(selected)
    by_class: Counter = Counter()
    for doms in selected.values():
        for tags in doms.values():
            for t in tags:
                by_class[t] += 1
    counts = {
        "orgs": len(sel),
        "pairs": sum(len(v) for v in selected.values()),
        "pairs_by_source": dict(sorted(by_class.items())),
        "domains": len({d for v in selected.values() for d in v}),
        "orgs_new_to_cache": len(sel - had_other),
        "orgs_already_with_other_seeds": len(sel & had_other),
        "orgs_previously_crawl": len(before),
        "orgs_added_since_previous_merge": len(sel - before),
        "orgs_removed_since_previous_merge": len(before - sel),
    }
    return out, counts


def write_seeds(doc: dict, path: Path) -> None:
    data = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    with open(path, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=9, mtime=0) as fh:
        fh.write(data)  # no name, mtime 0: the same input gives a byte-identical file


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--matches", type=Path, required=True, help="the crawl matcher's matches.jsonl")
    ap.add_argument("--pages", type=Path, action="append", help="the crawl's pages.jsonl.gz (all printed org "
                    "numbers, for the crowded-domain and credit rules); repeat for every page file of the crawl")
    ap.add_argument("--universe", type=Path, required=True, help="company universe jsonl.gz (name, legal_form, website)")
    ap.add_argument("--seeds", type=Path, default=SEEDS, help="seed cache to update in place")
    ap.add_argument("--out", type=Path, help="write here instead of --seeds")
    a = ap.parse_args(argv)
    key = CC.default_key()
    universe = CC.load_universe(a.universe, key)
    with open(a.matches, encoding="utf-8") as fh:
        matches = [json.loads(line) for line in fh if line.strip()]
    pages = (rec for path in sorted(a.pages or []) for rec in read_jsonl_gz(path))  # fixed order: deterministic
    printed, listing, before = printed_numbers(pages, key) if a.pages else ({}, set(), {})
    selected, dropped = select_pairs(matches, universe, key, printed, listing, before)
    with gzip.open(a.seeds, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    doc, counts = merge(doc, selected)
    stats = dict(doc.get("stats") or {})
    stats["crawl_merge"] = {
        "matches_sha256": hashlib.sha256(a.matches.read_bytes()).hexdigest(),
        "matches": len(matches), "page_files": [p.name for p in sorted(a.pages or [])],
        "domains_with_printed_orgnr": len(printed),
        "thresholds": {"max_orgs_per_domain": CC.MAX_ORGS_PER_DOMAIN, "max_domains_per_org": CC.MAX_DOMAINS_PER_ORG},
        "dropped": dict(sorted(dropped.items())), **counts,
    }
    by_src: Counter = Counter()
    for items in doc["seeds"].values():
        for s in {s for it in items for s in it["sources"]}:
            by_src[s] += 1
    stats["orgs"] = len(doc["seeds"])
    stats["orgs_by_source_all"] = dict(sorted(by_src.items()))
    doc["stats"] = stats
    doc.setdefault("licences", {})["crawl"] = LICENCE
    note = doc.get("note") or ""
    extra = (" Includes Kilde's own crawl candidates (crawl_orgnr, crawl_phone_email, crawl_signals), merged by "
             "scripts/merge_crawl_seeds.py.")
    if extra.strip() not in note:
        doc["note"] = note + extra
    write_seeds(doc, a.out or a.seeds)
    print(json.dumps(stats["crawl_merge"], indent=1))


if __name__ == "__main__":
    main()
