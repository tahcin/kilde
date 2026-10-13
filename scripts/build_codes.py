"""Build src/pata/data/codes.json from SSB Klass (run once; the agent makes no Klass requests at run time).

Classifications (https://data.ssb.no/api/klass/v1/):
  6   Standard for næringsgruppering (NACE): SN2025 at today's date, plus SN2007 (valid to
      2024-12-31) for codes the registry may still carry. "nace" holds SN2025 names with SN2007
      names filled in only for codes that SN2025 does not define; "nace_sn2007" keeps SN2007 as is.
  39  Institusjonell sektorgruppering ("sector")
  35  Organisasjonsform ("legal_form")

Licence: CC BY 4.0, Statistics Norway (SSB). Attribution: "Kilde: Statistisk sentralbyrå (SSB), Klass".

Usage: python scripts/build_codes.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pata.http import USER_AGENT  # noqa: E402

API = "https://data.ssb.no/api/klass/v1/classifications/{id}/codesAt?date={date}"
OUT = ROOT / "src" / "pata" / "data" / "codes.json"
SN2007_DATE = "2024-12-31"
ATTRIBUTION = "Kilde: Statistisk sentralbyrå (SSB), Klass. Licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)."


def codes_at(client: httpx.Client, cid: int, when: str) -> dict[str, str]:
    url = API.format(id=cid, date=when)
    r = client.get(url)
    r.raise_for_status()
    time.sleep(0.5)  # polite pacing between the four calls
    return {c["code"]: c["name"].strip() for c in r.json()["codes"] if c.get("code") and c.get("name")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args()
    with httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=60) as client:
        sn2025 = codes_at(client, 6, args.date)
        sn2007 = codes_at(client, 6, SN2007_DATE)
        sector = codes_at(client, 39, args.date)
        legal_form = codes_at(client, 35, args.date)
    nace = dict(sn2025)
    for code, name in sn2007.items():
        nace.setdefault(code, name)
    data = {
        "_meta": {
            "source": "https://data.ssb.no/api/klass/v1/",
            "classifications": {"nace": f"6 at {args.date} (SN2025), SN2007 at {SN2007_DATE} for codes not in SN2025",
                                "nace_sn2007": f"6 at {SN2007_DATE}", "sector": f"39 at {args.date}",
                                "legal_form": f"35 at {args.date}"},
            "licence": "CC BY 4.0",
            "attribution": ATTRIBUTION,
            "built": date.today().isoformat(),
        },
        "nace": nace,
        "nace_sn2007": sn2007,
        "sector": sector,
        "legal_form": legal_form,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=0, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: nace {len(nace)} (SN2025 {len(sn2025)}, SN2007 {len(sn2007)}), "
          f"sector {len(sector)}, legal_form {len(legal_form)}")


if __name__ == "__main__":
    main()
