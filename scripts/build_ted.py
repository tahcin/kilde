"""Build the declared TED cache: Norwegian org number -> EU/EEA procurement notices.

Source: TED (Tenders Electronic Daily), the EU Publications Office's journal of public
procurement notices, read through the anonymous Search API v3
(POST https://api.ted.europa.eu/v3/notices/search, no key; docs https://docs.ted.europa.eu/api/).
Reuse of TED data is free with attribution (Commission Decision 2011/833/EU on the reuse of
Commission documents; TED legal notice https://ted.europa.eu/en/legal-notice).

Two queries, both limited to eForms notices (the only ones that carry org-number fields):
  won:    winner-country=NOR             (notices naming a Norwegian winner)
  bought: buyer-country=NOR              (every eForms notice a Norwegian public buyer published)

Each notice is stored as one compact JSON line (kilde.connectors.ted.make_record: multilingual
titles reduced to English and Norwegian, links dropped). The connector serves that exact line as the
evidence snapshot, so every claim span is a literal substring of a stored record. The org-number
index is rebuilt from the records at run time (kilde.connectors.ted.index_records).

Usage: python scripts/build_ted.py [--state DIR] [--universe FILE.jsonl.gz]
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kilde import __version__  # noqa: E402

from kilde.connectors._records import line  # noqa: E402
from kilde.connectors.ted import API, FIELDS, index_records, make_record, roles  # noqa: E402

UA = f"KildeSignalpost/{__version__} (+https://github.com/tahcin/kilde)"
OUT = ROOT / "src" / "kilde" / "data" / "ted.json.gz"
QUERIES = {"won": "winner-country=NOR", "bought": "buyer-country=NOR AND publication-date>=20231101"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def download(query: str, path: Path, sleep: float) -> list[dict]:
    """All notices for a query (iteration mode, 250 per page); raw pages are kept in --state."""
    if path.exists():
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    out: list[dict] = []
    token = None
    with httpx.Client(headers={"User-Agent": UA, "Content-Type": "application/json"}, timeout=60) as c:
        while True:
            body = {"query": query, "fields": FIELDS, "limit": 250, "paginationMode": "ITERATION"}
            if token:
                body["iterationNextToken"] = token
            for attempt in range(4):
                r = c.post(API, json=body)
                if r.status_code == 200:
                    break
                time.sleep(5 * (attempt + 1))
            r.raise_for_status()
            d = r.json()
            out += d.get("notices") or []
            token = d.get("iterationNextToken")
            print(f"{query[:30]}: {len(out)}/{d.get('totalNoticeCount')}", file=sys.stderr)
            if not token or not d.get("notices"):
                break
            time.sleep(sleep)
    path.write_text("\n".join(json.dumps(n, ensure_ascii=False) for n in out))
    return out


def build(state: Path, universe: set[str] | None, sleep: float) -> dict:
    state.mkdir(parents=True, exist_ok=True)
    built = now_iso()
    records: dict[str, str] = {}
    for key, q in QUERIES.items():
        for n in download(q, state / f"{key}.jsonl", sleep):
            rec = make_record(n)
            pub = rec.get("publication-number")
            r = roles(rec)
            hits = set(r["won"]) | set(r["bought"])
            if universe is not None:
                hits &= universe
            if pub and hits:
                records[pub] = line(rec)
    by_org = index_records(records)
    return {"built_at": built, "source": API, "queries": QUERIES, "fields": FIELDS,
            "records": dict(sorted(records.items())), "orgs": len(by_org)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(Path(tempfile.gettempdir()) / "kilde-build-ted"))
    ap.add_argument("--universe", default=None, help="optional JSONL(.gz) of companies to restrict the index to")
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    universe = None
    if a.universe:
        op = gzip.open if a.universe.endswith(".gz") else open
        with op(a.universe, "rt") as fh:
            universe = {json.loads(x)["organisation_number"] for x in fh if x.strip()}
    data = build(Path(a.state), universe, a.sleep)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    with gzip.open(a.out, "wb", compresslevel=9) as fh:
        fh.write(raw)
    print(f"wrote {a.out}: {data['orgs']} orgs, {len(data['records'])} notices, "
          f"{Path(a.out).stat().st_size / 1e6:.2f} MB", file=sys.stderr)


if __name__ == "__main__":
    main()
