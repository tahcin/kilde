"""Refresh diffs and summary citations, on a made-up company."""
from kilde.model import CompanyContext, Snapshot
from kilde.refresh import diff
from kilde.synthesis import summarise


def snap(body: bytes = b"{}") -> Snapshot:
    return Snapshot(url="https://data.brreg.no/enhetsregisteret/api/enheter/999999999", final_url="https://data.brreg.no/enhetsregisteret/api/enheter/999999999",
                    status=200, retrieved_at="2026-10-13T08:00:00Z", content=body, content_type="application/json")


def build(ceo: str, jobs: list[str], *, roles_ok: bool = True) -> CompanyContext:
    ctx = CompanyContext("999999999")
    s = snap()
    ev = ctx.add_evidence(s, source_class="official_registry", span='"navn":"FJELLTOPP TESTDATA AS"', method="t")
    ctx.add_claim("legal_name", "identity", "FJELLTOPP TESTDATA AS", ev)
    ctx.add_claim("legal_form", "identity", {"code": "AS", "name": "Aksjeselskap"}, ev)
    ctx.registry = {"navn": "FJELLTOPP TESTDATA AS"}
    if roles_ok:
        ctx.add_claim("ceo", "leadership", {"name": ceo, "role_code": "DAGL"}, ev)
        ctx.module_states["roles"] = "available"
    else:
        ctx.module_states["roles"] = "failed"
    for j in jobs:
        ctx.add_claim("job_ad", "hiring", {"uuid": j, "title": f"Job {j}", "published": "2026-10-01"}, ev)
    ctx.module_states["jobs"] = "available"
    ctx.module_states["registry"] = "available"
    return ctx


def as_envelope(ctx: CompanyContext, run_id: str) -> dict:
    return {"organisation_number": ctx.org, "run": {"run_id": run_id},
            "claims": [c.to_json() for c in ctx.claims], "evidence": [e.to_json() for e in ctx.evidence]}


def test_typed_changes_and_idempotence():
    prev = as_envelope(build("Kari Testesen", ["a", "b"]), "r1")
    now = build("Ola Prøvesen", ["b", "c"])
    kinds = sorted(c["type"] for c in diff(now, prev))
    assert kinds == ["closed_job", "ended_role", "new_job", "new_role"]
    again = build("Ola Prøvesen", ["b", "c"])
    assert diff(again, as_envelope(now, "r2")) == []


def test_failed_source_carries_forward_instead_of_erasing():
    prev = as_envelope(build("Kari Testesen", []), "r1")
    now = build("unused", [], roles_ok=False)
    changes = diff(now, prev)
    assert not any(c["type"] == "ended_role" for c in changes)
    carried = [c for c in now.claims if c.field == "ceo"]
    assert carried and carried[0].value["name"] == "Kari Testesen"
    assert "carried forward" in carried[0].note


def test_every_summary_sentence_cites_existing_claims():
    ctx = build("Kari Testesen", ["a"])
    out = summarise(ctx, [])
    ids = {c.id for c in ctx.claims}
    assert out["sentences"]
    for s in out["sentences"]:
        assert s["claim_ids"] and set(s["claim_ids"]) <= ids
    assert "a private limited company" in out["text"]
    assert any(u.startswith("Verified company website") for u in out["unknowns"])


def test_spans_are_exact_raw_substrings():
    from kilde.spans import repair
    raw = '[{"id":11,"regnskapsperiode":{"fraDato":"2024-01-01","tilDato":"2024-12-31"},"valuta":"NOK","x":{"sumDriftsinntekter":500.0}},' \
          '{"id":12,"regnskapsperiode":{"fraDato":"2025-01-01","tilDato":"2025-12-31"},"valuta":"NOK","x":{"sumDriftsinntekter":900.0}}]'
    span, extra, exact = repair(raw, '{"sumDriftsinntekter": 900.0, "valuta": "NOK", "regnskapsperiode": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"}}',
                                "application/json", ("id", 12))
    assert exact and span == '"sumDriftsinntekter":900.0' and all(x in raw for x in extra)
    html = '<footer><p>Fjelltopp Testdata AS &ndash; Org.nr:&nbsp;<b>999 888 777</b></p></footer>'
    span, _, exact = repair(html, "Fjelltopp Testdata AS – Org.nr: 999 888 777", "text/html")
    assert exact and span in html and "999 888 777" in span
    span, _, exact = repair('<html lang="nb" class="x">', '<html lang="nb">', "text/html")
    assert exact and span == 'lang="nb"'
