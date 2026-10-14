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


def test_summary_never_mixes_group_and_company_accounts():
    ctx = build("Kari Testesen", [])
    ev = ctx.evidence[0]
    for kind, rev, res in (("group", 5_000_000.0, 900_000.0), ("company", None, 2_400_000.0)):
        if rev is not None:
            ctx.add_claim("revenue", "financials", {"amount": rev, "currency": "NOK", "year": "2025", "account_type": kind}, ev)
        ctx.add_claim("annual_result", "financials", {"amount": res, "currency": "NOK", "year": "2025", "account_type": kind}, ev)
    text = summarise(ctx, [])["text"]
    assert "company accounts it reported an annual result of 2.4 million NOK" in text
    assert "5.0 million" not in text.split("company accounts")[1].split(".")[0]


def test_birth_dates_never_reach_snapshots():
    from kilde.http import redact_personal
    raw = b'{"rollegrupper":[{"roller":[{"person":{"fodselsdato":"1970-01-01","navn":{"fornavn":"Kari","etternavn":"Testesen"}}}]}]}'
    s = redact_personal(Snapshot(url="u", final_url="u", status=200, retrieved_at="t", content=raw, content_type="application/json"))
    assert b"fodselsdato" not in s.content and b"Testesen" in s.content and s.redacted


def test_private_addresses_are_refused():
    import asyncio, httpx
    from kilde.http import Budget, Fetcher, UnsafeAddress
    f = Fetcher(Budget(10, 60))
    async def go():
        try:
            await f._guard_address(httpx.Request("GET", "http://169.254.169.254/latest/meta-data"))
        finally:
            await f.close()
    try:
        asyncio.run(go())
        raise AssertionError("metadata address was not refused")
    except UnsafeAddress:
        pass


def test_capped_counts_say_the_source_total():
    ctx = build("Kari Testesen", ["a", "b"])
    ctx.set_total("job_ad", 14)
    text = summarise(ctx, [])["text"]
    assert "NAV's feed holds 14 active job ads" in text and "the 2 re-checked in this run are shown" in text
    ctx2 = build("Kari Testesen", ["a"])
    ev = ctx2.evidence[0]
    for i in range(3):
        ctx2.add_claim("registered_workplace", "locations", {"organisation_number": f"99999990{i}", "municipality": "OSLO"}, ev)
    ctx2.set_total("registered_workplace", 3, exact=False)
    assert "It has at least 3 registered workplaces in Oslo." in summarise(ctx2, [])["text"]
    ctx3 = build("Kari Testesen", [])
    for i in range(2):
        ctx3.add_claim("public_contract_award", "activity", {"notice": f"n{i}"}, ev, as_of=f"2026-0{i + 1}-01")
    ctx3.set_total("public_contract_award", 25)
    assert "lists 25 contract awards won with its organisation number, the latest on 2026-02-01; the 2 most recent " \
           "are shown." in summarise(ctx3, [])["text"]
    ctx4 = build("Kari Testesen", ["a"])  # no cap: the plain count
    assert "NAV lists 1 active job ad with its organisation number" in summarise(ctx4, [])["text"]
