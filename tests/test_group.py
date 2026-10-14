"""Group structure (konsernstruktur): one subsidiary per organisation number, direct vs indirect, and
sentences that count companies, not registry lines. Made-up tree in the shape Brreg returns."""
from __future__ import annotations

import asyncio
import json

from kilde.connectors import brreg
from kilde.model import CompanyContext, Snapshot
from kilde.spans import repair
from kilde.synthesis import summarise

TOP = "100000001"
SUB_A, SUB_B, SUB_C = "100000002", "100000003", "100000004"
SHARED = "100000009"  # held in slices by the parent and three subsidiaries: an indirect subsidiary


def line(org, name, parent, parent_name, code, desc, share, children=None):
    n = {"dato": "2024-11-30", "grunnlag": share, "knytningsform": {"beskrivelse": desc, "kode": code},
         "navn": name, "nivaa": 1, "organisasjonsform": {"beskrivelse": "Aksjeselskap", "kode": "AS"},
         "organisasjonsnummer": org, "parentNavn": parent_name, "parentOrganisasjonsnummer": parent}
    if children:
        n["children"] = children
    return n


def stake(holder, holder_name, share):
    return line(SHARED, "DELT VERKTØY AS", holder, holder_name, "KGRL", "Konsern grunnlag", share)


TREE = {"organisasjonsnummer": TOP, "navn": "TOPP HOLDING AS", "children": [
    line(SUB_A, "ALFA AS", TOP, "TOPP HOLDING AS", "KDAT", "Konsern datter", "100 %",
         [stake(SUB_A, "ALFA AS", "14,3%")]),
    line(SUB_B, "BETA AS", TOP, "TOPP HOLDING AS", "KDAT", "Konsern datter", "100%",
         [stake(SUB_B, "BETA AS", "14,3%")]),
    line(SUB_C, "GAMMA AS", TOP, "TOPP HOLDING AS", "KDAT", "Konsern datter", "100 %",
         [stake(SUB_C, "GAMMA AS", "28,5%")]),
    line(SHARED, "DELT VERKTØY AS", TOP, "TOPP HOLDING AS", "KMOR", "Konsern mor", "Indirekte mor"),
    stake(TOP, "TOPP HOLDING AS", "14,3%"),
]}


class Fetch:
    async def get(self, url, headers=None, **k):
        return Snapshot(url=url, final_url=url, status=200, retrieved_at="2026-10-14T00:00:00Z",
                        content=json.dumps(TREE, ensure_ascii=False, indent=2).encode(), content_type="application/json")


def group_ctx(org: str) -> CompanyContext:
    ctx = CompanyContext(org)
    asyncio.run(brreg.fetch_group(Fetch(), ctx))
    return ctx


def test_each_subsidiary_once_with_direct_and_indirect_labels():
    ctx = group_ctx(TOP)
    subs = ctx.claims_for("group_subsidiary")
    assert [c.value["organisation_number"] for c in subs] == [SUB_A, SUB_B, SUB_C, SHARED]
    rel = {c.value["organisation_number"]: c.value for c in subs}
    assert rel[SUB_A]["relationship"] == "direct subsidiary" and rel[SUB_A]["ownership"] == "100 %"
    shared = rel[SHARED]
    # never labelled with the registry's "Konsern mor", which reads as if the subsidiary were the parent
    assert shared["relationship"] == "indirect subsidiary" and shared["ownership"] is None
    assert shared["direct_stake"] == "14,3%"
    assert "mor" not in json.dumps([c.value for c in subs], ensure_ascii=False).lower()


def test_indirect_subsidiary_spans_quote_its_own_lines_not_the_first_namesake():
    ctx = group_ctx(TOP)
    c = next(c for c in ctx.claims_for("group_subsidiary") if c.value["organisation_number"] == SHARED)
    for indent in (2, None):  # pretty-printed and compact (Brreg sends compact JSON)
        raw = json.dumps(TREE, ensure_ascii=False, indent=indent)
        spans = quoted(ctx, c, raw)
        assert '"KMOR"' in spans[0] and f'"parentOrganisasjonsnummer":"{TOP}"' in spans[0].replace(" ", "")
        assert '"14,3%"' in spans[1] and f'"parentOrganisasjonsnummer":"{TOP}"' in spans[1].replace(" ", "")


def quoted(ctx, c, raw):
    spans = []
    for eid in c.evidence_ids:
        ev = next(e for e in ctx.evidence if e.id == eid)
        span, extra, exact = repair(raw, ev.claim_span, "application/json", ctx.anchors.get(eid))
        assert exact and span in raw and all(x in raw for x in extra)
        spans.append(" | ".join([span] + extra))
    return spans


def test_summary_counts_companies_and_splits_direct_from_indirect():
    ctx = group_ctx(TOP)
    # a duplicate line for the same company must not raise the count either
    ctx.add_claim("group_subsidiary", "identity", {"organisation_number": SUB_A, "name": "ALFA AS",
                                                    "relationship": "direct subsidiary", "ownership": "100 %"},
                  ctx.evidence[0])
    ctx.add_claim("legal_name", "identity", "TOPP HOLDING AS", ctx.evidence[0])
    text = summarise(ctx, [])["text"]
    assert "The registry lists 4 subsidiaries (3 held directly and 1 indirectly, through its subsidiaries)." in text


def test_minority_stake_only_is_not_a_subsidiary_and_never_makes_a_parent():
    # From the shared company's side: three subsidiaries hold KGRL slices of it; none is its parent.
    ctx = group_ctx(SHARED)
    p = ctx.claims_for("group_parent")
    assert len(p) == 1 and p[0].value["organisation_number"] == TOP
    assert p[0].value["relationship"] == "indirect parent"
    assert not ctx.claims_for("group_ultimate_parent")
    ctx.add_claim("legal_name", "identity", "DELT VERKTØY AS", ctx.evidence[0])
    text = summarise(ctx, [])["text"]
    assert "TOPP HOLDING AS (100000001) controls it indirectly" in text and "direct parent" not in text
    # a direct subsidiary keeps its plain direct parent
    ctx2 = group_ctx(SUB_A)
    assert ctx2.claims_for("group_parent")[0].value == {"organisation_number": TOP, "name": "TOPP HOLDING AS",
                                                        "relationship": "direct parent", "ownership": "100 %"}
    assert not ctx2.claims_for("group_subsidiary")  # its KGRL slice of the shared company is not a subsidiary


def test_distinct_keeps_claims_without_a_key():
    from kilde.synthesis import distinct
    ctx = CompanyContext(TOP)
    ev = ctx.add_evidence(None, source_url="https://x", source_class="official_registry", method="t", span="x")
    a = ctx.add_claim("job_ad", "hiring", {"uuid": "u1"}, ev)
    b = ctx.add_claim("job_ad", "hiring", {"uuid": "u1"}, ev)
    c = ctx.add_claim("job_ad", "hiring", {"title": "no id"}, ev)
    assert distinct([a, b, c], "uuid") == [a, c]
