"""Build the declared Forskningsrådet cache: approved SkatteFUNN projects and funded Research Council projects.

Sources (see kilde.connectors.forskningsradet for terms):
  SFW  current SkatteFUNN list, linked from https://www.forskningsradet.no/skattefunn/suksesshistorier/
  SF   SkatteFUNN 2002-2024: .../siteassets/skattefunn/tall/skattefunn-innsendte-soknader-2002-2024.xlsx
  RCN  Research Council "Søknader - versjon 2": github.com/Forskningsradet/open-data (NLOD 2.0)

Only approved (SkatteFUNN "JA") and funded (phase Bevilgning/Avsluttet) rows are kept, as compact
JSON lines with the source's own column names. Project leaders' names are never stored.

Usage: python scripts/build_forskningsradet.py [--state DIR]
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kilde import __version__  # noqa: E402
from kilde.connectors import _xlsx  # noqa: E402
from kilde.connectors._records import line  # noqa: E402
from kilde.connectors.forskningsradet import (RCN_CSV, SF_BASE, SF_HIST, SF_PAGE, WEB_LINK, index, parse_sf_web,  # noqa: E402
                                              rcn_record, sf_hist_record)

UA = f"KildeSignalpost/{__version__} (+https://github.com/tahcin/kilde)"
OUT = ROOT / "src" / "kilde" / "data" / "forskningsradet.json.gz"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(c: httpx.Client, url: str, state: Path) -> tuple[bytes, str]:
    p = state / hashlib.sha256(url.encode()).hexdigest()[:16]
    if p.exists():
        meta = json.loads((state / (p.name + ".json")).read_text())
        return p.read_bytes(), meta["retrieved_at"]
    at = now_iso()
    r = c.get(url)
    r.raise_for_status()
    p.write_bytes(r.content)
    (state / (p.name + ".json")).write_text(json.dumps({"url": url, "retrieved_at": at}))
    return r.content, at


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(Path(tempfile.gettempdir()) / "kilde-build-forskningsradet"))
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    state = Path(a.state)
    state.mkdir(parents=True, exist_ok=True)
    records: dict[str, str] = {}
    sources: dict[str, dict] = {}
    with httpx.Client(headers={"User-Agent": UA}, timeout=120, follow_redirects=True) as c:
        page = c.get(SF_PAGE).text
        m = WEB_LINK.search(page)
        if not m:
            sys.exit("SkatteFUNN list link not found on " + SF_PAGE)
        web_url = SF_BASE + m.group(1)
        data, at = fetch(c, web_url, state)
        web = parse_sf_web(data)
        records.update(web)
        sources["SFW"] = {"url": web_url, "retrieved_at": at, "sha256": hashlib.sha256(data).hexdigest()}

        data, at = fetch(c, SF_HIST, state)
        n = 0
        for row in _xlsx.dicts(data):
            r = sf_hist_record(row)
            if r:
                records[r[0]] = line(r[1])
                n += 1
        sources["SF"] = {"url": SF_HIST, "retrieved_at": at, "sha256": hashlib.sha256(data).hexdigest()}

        data, at = fetch(c, RCN_CSV, state)
        m2 = 0
        for row in csv.DictReader(io.StringIO(data.decode("utf-8-sig"))):
            r = rcn_record(row)
            if r:
                records[r[0]] = line(r[1])
                m2 += 1
        sources["RCN"] = {"url": RCN_CSV, "retrieved_at": at, "sha256": hashlib.sha256(data).hexdigest()}
    print(f"SkatteFUNN current {len(web)}, SkatteFUNN 2002-2024 {n}, Research Council {m2}", file=sys.stderr)
    out = {"built_at": now_iso(), "sources": sources, "records": dict(sorted(records.items()))}
    raw = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()
    with gzip.open(a.out, "wb", compresslevel=9) as fh:
        fh.write(raw)
    print(f"wrote {a.out}: {len(index(records))} orgs, {len(records)} projects, "
          f"{Path(a.out).stat().st_size / 1e6:.2f} MB", file=sys.stderr)


if __name__ == "__main__":
    main()
