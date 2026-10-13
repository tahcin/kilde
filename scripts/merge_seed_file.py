"""Add the entries of a partial seed file (for example the NAV employer homepages that build_website_seeds.py
found) to the website-candidate cache, without touching any other source.

Input: a JSON (or .json.gz) file {"seeds": {org: [{"domain": d, "sources": [...]}]}}. Only the sources named
with --source are taken from it. Each domain is normalised with build_website_seeds.site_key and passes the
same filters as the Common Crawl merge: org numbers that fail MOD11, are outside the company universe or are
sole proprietorships (ENK) are dropped, and so are social, directory, registry, aggregator and government
hosts. Entries are added to an org's existing list (deduplicated by domain, sources unioned); nothing is
removed, so re-running with the same input gives a byte-identical file.

Usage:
  uv run --frozen --with tldextract==5.4.0 python scripts/merge_seed_file.py --input nav_only.json --source nav \
      --universe .../dl_signalpost-company-universe-2025.jsonl.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_cc_seeds as CC  # noqa: E402
import merge_crawl_seeds as MC  # noqa: E402


def select(extra: dict, sources: set[str], universe: dict[str, dict], key) -> tuple[dict[str, dict[str, set[str]]], Counter]:
    dropped: Counter = Counter()
    out: dict[str, dict[str, set[str]]] = {}
    for org, items in sorted((extra.get("seeds") or {}).items()):
        for it in items or []:
            tags = set(it.get("sources") or ()) & sources
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
            dom = key(str(it.get("domain") or ""))
            if not dom or CC.non_company(dom):
                dropped["non_company_host"] += 1
                continue
            out.setdefault(org, {}).setdefault(dom, set()).update(tags)
    return out, dropped


def add(doc: dict, selected: dict[str, dict[str, set[str]]]) -> tuple[dict, dict]:
    seeds = doc.get("seeds") or {}
    new: dict[str, list[dict]] = {}
    added = Counter()
    for org in sorted(set(seeds) | set(selected)):
        items: dict[str, set[str]] = {}
        for it in seeds.get(org) or []:
            if it.get("domain"):
                items.setdefault(it["domain"], set()).update(it.get("sources") or ())
        if org in selected and org not in seeds:
            added["orgs_new_to_cache"] += 1
        for d, tags in sorted((selected.get(org) or {}).items()):
            if d not in items:
                added["domains_new_for_org"] += 1
            elif not tags <= items[d]:
                added["tag_added_to_existing_domain"] += 1
            items.setdefault(d, set()).update(tags)
        if items:
            new[org] = [{"domain": d, "sources": sorted(s)}
                        for d, s in sorted(items.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    out = dict(doc)
    out["seeds"] = new
    return out, dict(sorted(added.items()))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--source", action="append", required=True, help="source tag(s) to take from the input")
    ap.add_argument("--universe", type=Path, required=True)
    ap.add_argument("--seeds", type=Path, default=CC.SEEDS)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    key = CC.default_key()
    universe = CC.load_universe(a.universe, key)
    opener = gzip.open if a.input.suffix == ".gz" else open
    with opener(a.input, "rt", encoding="utf-8") as fh:
        extra = json.load(fh)
    selected, dropped = select(extra, set(a.source), universe, key)
    with gzip.open(a.seeds, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    doc, added = add(doc, selected)
    by_src: Counter = Counter()
    for items in doc["seeds"].values():
        for s in {s for it in items for s in it["sources"]}:
            by_src[s] += 1
    stats = dict(doc.get("stats") or {})
    for s in sorted(a.source):
        stats[f"{s}_file_merge"] = {"orgs": len(selected), "pairs": sum(len(v) for v in selected.values()),
                                   "dropped": dict(sorted(dropped.items()))}
    stats["orgs"] = len(doc["seeds"])
    stats["orgs_by_source_all"] = dict(sorted(by_src.items()))
    doc["stats"] = stats
    MC.write_seeds(doc, a.out or a.seeds)
    # what changed in this run is printed, not stored, so a re-run leaves the file byte-identical
    print(json.dumps({**{s: stats[f"{s}_file_merge"] for s in sorted(a.source)}, "this_run": added}, indent=1))


if __name__ == "__main__":
    main()
