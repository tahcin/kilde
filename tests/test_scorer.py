"""Tests for the self-scorer (scorer/score.py), sampler and audit pack, using the synthetic fixture."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scorer"))

import audit_pack  # noqa: E402
import sample  # noqa: E402
import score  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "envelopes_sample.jsonl")


@pytest.fixture
def envs() -> list[dict]:
    return score.read_jsonl(FIXTURE)


def by_org(envs, org):
    return next(e for e in envs if e["organisation_number"] == org)


def test_fixture_scores_clean(envs):
    res = score.score(envs, expected=6)
    assert res["gates"]["passed"]
    assert not res["hard_fail"]
    assert res["precision"]["structural_rate"] == 1.0
    assert res["synthesis"]["pass_rate"] == 1.0
    assert res["recall"]["points"] == pytest.approx(50.0)  # no auditor pool: we are the whole pool
    assert res["unstop_view"]["coverage_35"] == pytest.approx(35.0)


def test_field_family_mapping():
    assert score.family_of("revenue") == "financials"
    assert score.family_of("social_profile") == "social_profiles"
    assert score.family_of("wikidata_ticker") == "external_registers"
    assert score.family_of("legal_name") is None


def test_pool_lowers_recall(envs):
    pool = [
        # a website for a company we found none for, and an extra social for one we cover
        {"org": "999300001", "family": "official_website", "value_key": "official_website|tindefjell.example"},
        {"org": "999100001", "family": "social_profiles", "value_key": "social_profile|instagram.com/fjordlys"},
    ]
    res = score.score(envs, pool_rows=pool)
    web = res["recall"]["families"]["official_website"]
    assert web["pool_companies"] == 4 and web["our_companies"] == 3
    assert web["company_coverage"] == pytest.approx(0.75)
    assert web["fact_recall"] == pytest.approx(0.75)
    soc = res["recall"]["families"]["social_profiles"]
    assert soc["company_coverage"] == 1.0 and soc["fact_recall"] == pytest.approx(0.8)
    assert res["recall"]["points"] < 50


def test_pool_rows_for_orgs_outside_run_are_ignored(envs):
    res = score.score(envs, pool_rows=[{"org": "123456789", "family": "official_website", "value_key": "x"}])
    assert res["recall"]["families"]["official_website"]["pool_companies"] == 3


def test_value_key_matches_auditor_format():
    assert score.value_key("official_website", "https://www.Example.no/") == "official_website|example.no"
    assert score.value_key("site_phone", "+47 55 00 00 01") == "phone|55000001"
    assert score.value_key("revenue", {"amount": 1, "currency": "NOK", "year": "2025"}) == "revenue|2025|company"


def test_structural_failures(envs):
    e = by_org(envs, "999100001")
    rev = next(c for c in e["claims"] if c["field"] == "revenue")
    rev["value"].pop("currency")
    ev = next(x for x in e["evidence"] if x["id"] == e["claims"][0]["evidence_ids"][0])
    ev["content_sha256"] = "abc"
    e["claims"][1]["evidence_ids"] = ["e999"]
    res = score.score(envs)
    reasons = res["precision"]["failure_reasons"]
    assert "financial claim without currency" in reasons
    assert "content_sha256 not 64 hex" in reasons
    assert "evidence id does not resolve" in reasons
    assert res["precision"]["structural_rate"] < 1.0


def test_snapshot_hash_and_span(envs, tmp_path):
    e = by_org(envs, "999600001")
    web = next(c for c in e["claims"] if c["field"] == "official_website")
    ev = next(x for x in e["evidence"] if x["id"] == web["evidence_ids"][0])
    body = b"<html><footer>Lysaker Data AS - Org.nr 999 600 001 MVA</footer></html>"
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "a.html").write_bytes(body)
    ev["snapshot_path"] = "snapshots/a.html"
    ev["content_sha256"] = hashlib.sha256(body).hexdigest()
    ok = score.score(envs, base_dir=str(tmp_path))
    assert ok["precision"]["structural_rate"] == 1.0
    ev["claim_span"] = "Org.nr 111 111 111"
    bad = score.score(envs, base_dir=str(tmp_path))
    assert "claim_span not found in snapshot" in bad["precision"]["failure_reasons"]
    ev["claim_span"] = "Lysaker Data AS - Org.nr 999 600 001 MVA"
    ev["content_sha256"] = "0" * 64
    bad = score.score(envs, base_dir=str(tmp_path))
    assert "snapshot sha256 mismatch" in bad["precision"]["failure_reasons"]


def test_json_span_essentials():
    snap = '{"id":1,"valuta":"NOK","resultatregnskapResultat":{"driftsresultat":{"driftsinntekter":{"sumDriftsinntekter":48210000}}}}'
    assert score.span_in_snapshot('{"valuta": "NOK", "sumDriftsinntekter": 48210000}', snap)
    assert not score.span_in_snapshot('{"valuta": "NOK", "sumDriftsinntekter": 1}', snap)
    assert score.span_in_snapshot('"valuta":"NOK"', snap)


def test_audit_verdicts_and_hard_fail(envs):
    audit = [{"org": "999100001", "claim_id": "c1", "verdict": "correct"},
             {"org": "999100001", "claim_id": "c2", "verdict": "correct"},
             {"org": "999100001", "claim_id": "c3", "verdict": "wrong_value", "note": "code typo"},
             {"org": "999100001", "claim_id": "c4", "verdict": "correct"}]
    res = score.score(envs, audit_rows=audit)
    assert res["precision"]["audited_correct_rate"] == pytest.approx(0.75)
    assert res["precision"]["points"] == pytest.approx(22.5)
    assert not res["hard_fail"]
    audit.append({"org": "999200001", "claim_id": "c20", "verdict": "wrong_company", "note": "other bakery"})
    res = score.score(envs, audit_rows=audit)
    assert res["hard_fail"] and not res["qualifies"]
    assert "wrong_company" in res["hard_fail_reasons"][0]


def test_synthesis_rules(envs):
    e = by_org(envs, "999200001")
    e["summary"]["sentences"].append({"text": "It is the best bakery in Tromsø.", "claim_ids": ["c1"]})
    res = score.score(envs)
    assert res["synthesis"]["pass_rate"] == pytest.approx(5 / 6, abs=1e-3)
    e["summary"]["sentences"].pop()
    e["summary"]["sentences"].append({"text": "No job ads.", "claim_ids": []})
    assert score.check_summary(e) == ["sentence 4 cites no claim"]
    e["summary"]["sentences"][-1]["claim_ids"] = ["c999"]
    assert any("unknown claims" in x for x in score.check_summary(e))
    e["summary"]["sentences"].pop()
    e["summary"]["unknowns"] = []
    e["summary"]["text"] = "Bakery."
    e["summary"]["sentences"] = [{"text": "A bakery.", "claim_ids": ["c1"]}]
    assert any("unknowns" in x for x in score.check_summary(e))


def test_banned_word_allowed_inside_quoted_description(envs):
    e = by_org(envs, "999100001")
    desc = next(c for c in e["claims"] if c["field"] == "website_description")
    desc["value"] = "Ledende leverandør av løfteverktøy."
    e["summary"]["sentences"].append({"text": 'The site calls itself "Ledende leverandør av løfteverktøy."', "claim_ids": [desc["id"]]})
    assert score.check_summary(e) == []
    e["summary"]["sentences"].append({"text": "It is a leading supplier.", "claim_ids": [desc["id"]]})
    assert score.check_summary(e) == ["sentence 8 uses unsupported word 'leading'"]


def test_gates(envs):
    broken = copy.deepcopy(envs)
    broken.append(copy.deepcopy(broken[0]))
    del broken[1]["sections"]["hiring"]
    broken[2]["run"].pop("terminal_status")
    broken[3].pop("operations")
    g = score.check_gates(broken, 6)
    text = " ".join(g["problems"])
    assert not g["passed"]
    for needle in ("expected 6", "duplicate", "section hiring", "terminal_status", "operations missing"):
        assert needle in text


def test_ux_checklist(tmp_path, envs):
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from pata.report import write_report
    path = tmp_path / "report.html"
    write_report(envs, str(path), {"run_id": "t"})
    res = score.score_ux(str(path))
    assert res["checks"] and all(res["checks"].values()), res["checks"]
    assert res["points"] == 8
    (tmp_path / "bare.html").write_text("<html><body>hi</body></html>")
    assert score.score_ux(str(tmp_path / "bare.html"))["points"] < 3


def test_cli_writes_json(tmp_path):
    out = tmp_path / "score.json"
    assert score.main(["--envelopes", FIXTURE, "--expected-count", "6", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["companies"] == 6 and "recall" in data


def test_sample_excludes_previous(tmp_path):
    uni = tmp_path / "u.jsonl.gz"
    with gzip.open(uni, "wt", encoding="utf-8") as fh:
        for i in range(500):
            fh.write(json.dumps({"organisation_number": f"{900000000 + i}", "name": f"CO {i} AS"}) + "\n")
    first = sample.sample(str(uni), 100, 1, set())
    assert len(first) == 100 and len({r["organisation_number"] for r in first}) == 100
    assert "name" in first[0]
    prev = tmp_path / "prev.txt"
    prev.write_text("\n".join(r["organisation_number"] for r in first))
    second = sample.sample(str(uni), 100, 2, sample.load_excluded([str(prev)]))
    assert not {r["organisation_number"] for r in first} & {r["organisation_number"] for r in second}
    assert sample.sample(str(uni), 100, 1, set()) == first  # deterministic per seed


def test_audit_pack(tmp_path, envs):
    picked = audit_pack.pick(envs, 3, 1)
    orgs = {e["organisation_number"] for e in picked}
    assert len(picked) == 3
    assert orgs & {"999100001", "999200001", "999600001"}  # website stratum represented
    out = tmp_path / "pack.md"
    assert audit_pack.main(["--envelopes", FIXTURE, "--n", "4", "--seed", "2", "--out", str(out)]) == 0
    md = out.read_text()
    assert "| claim | field | value | source | span | key | verdict |" in md
    rows = [json.loads(l) for l in (tmp_path / "pack.audit.jsonl").read_text().splitlines()]
    assert rows and all(r["verdict"] == "" for r in rows)
