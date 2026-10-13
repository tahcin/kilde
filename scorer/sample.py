"""Draw a fresh random evaluation batch from the company universe.

Usage:
  python scorer/sample.py --universe dl_signalpost-company-universe-2025.jsonl.gz --n 100 --seed 7 \
      [--exclude previous_samples.txt] --out batch.jsonl

Each output line is the full universe row. --exclude takes a text file of organisation numbers (one per
line) or a JSONL file of earlier batches; those companies are never drawn again. Standard library only.
"""
from __future__ import annotations

import argparse
import gzip
import json
import random
import re
import sys


def _open(path: str):
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")


def load_excluded(paths: list[str]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        with _open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("{"):
                    row = json.loads(line)
                    line = str(row.get("organisation_number") or row.get("org") or "")
                digits = re.sub(r"\D", "", line)
                if len(digits) == 9:
                    out.add(digits)
    return out


def sample(universe: str, n: int, seed: int, excluded: set[str]) -> list[dict]:
    """Reservoir sample, so the 400k-row universe is streamed rather than held in memory."""
    rng = random.Random(seed)
    picked: list[dict] = []
    seen = 0
    with _open(universe) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if str(row.get("organisation_number")) in excluded:
                continue
            seen += 1
            if len(picked) < n:
                picked.append(row)
            else:
                j = rng.randrange(seen)
                if j < n:
                    picked[j] = row
    rng.shuffle(picked)
    return picked


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--universe", required=True)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--exclude", action="append", default=[], help="file of previously sampled org numbers (repeatable)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rows = sample(a.universe, a.n, a.seed, load_excluded(a.exclude))
    with open(a.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} companies to {a.out} (seed {a.seed}, excluded {len(load_excluded(a.exclude))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
