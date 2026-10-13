"""Build the declared Støtteregisteret cache (state-aid awards, NLOD 2.0, Brønnøysundregistrene).

Steps (resume-safe: a completed download is kept in --state and reused; the server does not support
HTTP ranges, so an interrupted download is retried from the start, up to --tries times):
  1. Download https://stotte.brreg.no/nb/oppslag/stoettetildeling/totalbestand/csv (UTF-16LE, ~300 MB),
     checking the byte count against Content-Length.
  2. Keep awards with Tildelingsdato in the last --years years, no recipient birth date (persons), and a
     recipient that is in the universe file, or is an underenhet whose Enhetsregisteret overordnetEnhet is
     in the universe (looked up 100 numbers per request; the unit record is stored for evidence).
  3. Store each kept row's CSV line verbatim (UTF-8) and write src/kilde/data/stotteregisteret.json.gz.

Usage: python scripts/build_stotteregisteret.py --universe FILE.jsonl.gz [--state DIR] [--years 5]
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kilde import __version__  # noqa: E402
from kilde.connectors._records import is_orgnr, line  # noqa: E402
from kilde.connectors.smilefjes import unit_record  # noqa: E402
from kilde.connectors.stotteregisteret import (BRREG, COL_BIRTH, COL_DATE, COL_NR, COL_ORG, CSV_URL,  # noqa: E402
                                               iso, parse_line)

UA = f"KildeSignalpost/{__version__} (+https://github.com/tahcin/kilde)"
OUT = ROOT / "src" / "kilde" / "data" / "stotteregisteret.json.gz"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def download(state: Path, tries: int) -> tuple[Path, dict]:
    done, meta_p = state / "totalbestand.csv", state / "totalbestand.json"
    if done.exists() and meta_p.exists():
        return done, json.loads(meta_p.read_text())
    part = state / "totalbestand.csv.part"
    for attempt in range(1, tries + 1):
        at = now_iso()
        try:
            with httpx.stream("GET", CSV_URL, headers={"User-Agent": UA}, timeout=httpx.Timeout(120, read=300),
                              follow_redirects=True) as r:
                r.raise_for_status()
                want = int(r.headers.get("content-length") or 0)
                h = hashlib.sha256()
                n = 0
                with open(part, "wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        fh.write(chunk)
                        h.update(chunk)
                        n += len(chunk)
                        if n % (20 << 20) < (1 << 20):
                            print(f"  {n / 1e6:.0f}/{want / 1e6:.0f} MB", file=sys.stderr)
            if want and n != want:
                raise IOError(f"short download {n} of {want} bytes")
            part.rename(done)
            meta = {"url": CSV_URL, "retrieved_at": at, "bytes": n, "sha256": h.hexdigest()}
            meta_p.write_text(json.dumps(meta))
            return done, meta
        except Exception as exc:  # noqa: BLE001
            print(f"attempt {attempt} failed: {exc!r}", file=sys.stderr)
            time.sleep(min(60, 10 * attempt))
    sys.exit("download failed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--state", default=str(Path(tempfile.gettempdir()) / "kilde-build-stotteregisteret"))
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--tries", type=int, default=5)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    state = Path(a.state)
    state.mkdir(parents=True, exist_ok=True)
    with gzip.open(a.universe, "rt") if a.universe.endswith(".gz") else open(a.universe) as fh:
        universe = {json.loads(x)["organisation_number"] for x in fh if x.strip()}
    path, meta = download(state, a.tries)
    text = path.read_bytes().decode("utf-16-le").lstrip("﻿")
    lines = text.replace("\r\n", "\n").split("\n")
    header_line = lines[0]
    header = next(csv.reader([header_line], delimiter=";"))
    today = date.today()
    since = today.replace(year=today.year - a.years).isoformat()
    kept: list[tuple[str, str, dict]] = []  # (recipient number, line, row)
    for ln in lines[1:]:
        if not ln:
            continue
        row = parse_line(header, ln)
        if not row or row.get(COL_BIRTH) or not is_orgnr(row.get(COL_ORG, "")):
            continue
        d = iso(row.get(COL_DATE, ""))
        if not d or d < since:
            continue
        kept.append((row[COL_ORG], ln, row))
    print(f"{len(kept)} awards since {since}", file=sys.stderr)
    # Resolve recipients outside the universe: are they workplaces of universe companies?
    outside = sorted({n for n, _, _ in kept if n not in universe})
    units: dict[str, str] = {}
    units_at = now_iso()
    with httpx.Client(headers={"User-Agent": UA}, timeout=60) as c:
        for i in range(0, len(outside), 100):
            part = outside[i:i + 100]
            r = c.get(f"{BRREG}/underenheter", params={"organisasjonsnummer": ",".join(part), "size": 100})
            r.raise_for_status()
            for e in (r.json().get("_embedded") or {}).get("underenheter") or []:
                if e.get("overordnetEnhet") in universe and not e.get("slettedato"):
                    units[e["organisasjonsnummer"]] = line(unit_record(e, "underenhet"))
            time.sleep(0.15)
    records: dict[str, str] = {}
    by_org: dict[str, list[list]] = {}
    for n, ln, row in kept:
        if n in universe:
            owner, unit = n, None
        elif n in units:
            owner, unit = json.loads(units[n])["overordnetEnhet"], n
        else:
            continue
        records[row[COL_NR]] = ln
        by_org.setdefault(owner, []).append([row[COL_NR], unit])
    used_units = {u for v in by_org.values() for _, u in v if u}
    out = {"built_at": now_iso(), "source": CSV_URL, "retrieved_at": meta["retrieved_at"], "file_sha256": meta["sha256"],
           "since": since, "header": header_line, "records": records, "by_org": by_org,
           "units": {u: units[u] for u in sorted(used_units)}, "units_at": units_at}
    with gzip.open(a.out, "wb", compresslevel=9) as fh:
        fh.write(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode())
    print(f"wrote {a.out}: {len(by_org)} orgs, {len(records)} awards, {len(used_units)} workplaces, "
          f"{Path(a.out).stat().st_size / 1e6:.2f} MB", file=sys.stderr)


if __name__ == "__main__":
    main()
