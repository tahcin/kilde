"""Build the declared Smilefjes cache: inspected places' org numbers -> Enhetsregisteret unit records.

The inspections themselves are NOT cached: Mattilsynet asks users to refresh daily, so the connector
downloads https://matnyttig.mattilsynet.no/smilefjes/tilsyn.csv on every run. This cache only saves
the run from resolving about 8,000 workplace (underenhet) numbers in Enhetsregisteret, and maps each
place to its public page on smilefjes.mattilsynet.no (from the site's own search index).

Steps:
  1. Download tilsyn.csv and collect every orgnummer.
  2. Look each up in Enhetsregisteret (underenheter, then enheter; 100 numbers per request, NLOD 2.0).
  3. Read https://smilefjes.mattilsynet.no/search/index/nb.json for place page paths.
  4. Write src/kilde/data/smilefjes.json.gz.

Usage: python scripts/build_smilefjes.py [--sleep 0.2]
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kilde import __version__  # noqa: E402
from kilde.connectors._records import line  # noqa: E402
from kilde.connectors.smilefjes import BRREG, CSV_URL, SITE, parse_csv, unit_record  # noqa: E402

UA = f"KildeSignalpost/{__version__} (+https://github.com/tahcin/kilde)"
OUT = ROOT / "src" / "kilde" / "data" / "smilefjes.json.gz"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    with httpx.Client(headers={"User-Agent": UA}, timeout=120, follow_redirects=True) as c:
        _, by_num = parse_csv(c.get(CSV_URL).content.decode("utf-8", errors="replace"))
        nums = sorted(by_num)
        units: dict[str, str] = {}
        built = now_iso()
        for i in range(0, len(nums), 100):
            part = nums[i:i + 100]
            for kind, key in (("underenhet", "underenheter"), ("enhet", "enheter")):
                r = c.get(f"{BRREG}/{key}", params={"organisasjonsnummer": ",".join(part), "size": 100})
                r.raise_for_status()
                for e in (r.json().get("_embedded") or {}).get(key) or []:
                    units[e["organisasjonsnummer"]] = line(unit_record(e, kind))
            time.sleep(a.sleep)
        idx = c.get(f"{SITE}/search/index/nb.json").json()
        pages = {}
        for item in idx.get("lookup") or []:
            path = item[0]
            obj = path.rstrip("/").rsplit(".", 1)[-1]
            pages[obj] = path
    out = {"built_at": built, "source": CSV_URL, "units": units, "pages": pages}
    with gzip.open(a.out, "wb", compresslevel=9) as fh:
        fh.write(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode())
    print(f"wrote {a.out}: {len(nums)} numbers, {len(units)} resolved, {len(pages)} pages, "
          f"{Path(a.out).stat().st_size / 1e6:.2f} MB", file=sys.stderr)


if __name__ == "__main__":
    main()
