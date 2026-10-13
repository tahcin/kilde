"""Tests for the static HTML report (src/pata/report.py)."""
from __future__ import annotations

import copy
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from pata.report import NLOD, build_data, write_report  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "envelopes_sample.jsonl")


def load():
    with open(FIXTURE, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def embedded(html: str, el_id: str):
    m = re.search(rf'<script type="application/json" id="{el_id}">(.*?)</script>', html, re.S)
    return json.loads(m.group(1).replace("<\\/", "</"))


def test_report_is_self_contained(tmp_path):
    p = tmp_path / "r.html"
    write_report(load(), str(p), {"run_id": "run-x", "requests": 77, "runtime_ms": 1000})
    html = p.read_text(encoding="utf-8")
    assert '<meta name="viewport"' in html
    assert 'type="search"' in html
    assert "@media (prefers-color-scheme:dark)" in html and "@media (max-width" in html
    assert not re.search(r'<(script|link|img)[^>]+(src|href)="https?://', html)  # no external assets
    assert NLOD.replace("ø", "&#248;") in html or NLOD in html
    assert "\u2014" not in html  # no em dashes
    assert "font-style:italic" not in html
    meta = embedded(html, "meta")
    assert meta["run_id"] == "run-x" and meta["requests"] == 77 and meta["cost_usd"] == 0
    assert meta["companies"] == 6
    data = embedded(html, "data")
    assert len(data["co"]) == 6
    assert all(set(c["sec"]) == {"identity", "financials", "leadership", "locations", "web_presence", "hiring", "activity"}
               for c in data["co"])


def test_missing_never_zero():
    data = build_data(load())
    sk = next(c for c in data["co"] if c["o"] == "999500001")
    assert sk["sec"]["financials"] == "failed"
    assert sk["rev"] is None  # failed financials stay unknown, not 0
    rev = next(c for c in sk["c"] if c["f"] == "revenue")
    assert rev["a"] == "failed" and "v" not in rev


def test_evidence_kept_per_value():
    data = build_data(load())
    fj = next(c for c in data["co"] if c["o"] == "999100001")
    web = next(c for c in fj["c"] if c["f"] == "official_website")
    ev = fj["ev"][web["e"][0]]
    assert data["u"][ev[0]].startswith("https://")
    assert ev[1].endswith("Z") and "999 100 001" in ev[3]
    assert fj["fl"] == {"web": True, "jobs": True, "chg": True}


def test_script_breakout_is_escaped(tmp_path):
    envs = load()
    envs[0]["legal_name"] = "EVIL </script><script>alert(1)</script> AS"
    p = tmp_path / "r.html"
    write_report(envs, str(p), {})
    html = p.read_text(encoding="utf-8")
    assert html.count("</script>") == 3  # meta, data and the app script only
    assert embedded(html, "data")["co"][0]["n"].startswith("EVIL </script>")


def test_size_budget_for_1500_companies(tmp_path):
    base = load()
    envs = []
    for i in range(1500):
        e = copy.deepcopy(base[i % len(base)])
        e["organisation_number"] = f"{910000000 + i}"
        envs.append(e)
    p = tmp_path / "big.html"
    write_report(envs, str(p), {"run_id": "big"})
    assert os.path.getsize(p) < 15 * 1024 * 1024
