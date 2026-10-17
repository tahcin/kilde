"""Registry events published as dated public activity, from data Kilde already holds."""
import json

from kilde.connectors import brreg
from kilde.model import CompanyContext, Snapshot
from kilde.spans import repair

ENTITY = {
    "organisasjonsnummer": "982170451", "navn": "STRAND SHIPPING INVEST AS", "stiftelsesdato": "2000-07-01",
    "registreringsdatoEnhetsregisteret": "2000-08-15", "registreringsdatoForetaksregisteret": "2000-08-15",
    "historiskeNavn": [
        {"fraDato": "2009-12-23 20:24:07", "navn": "STRAND SHIPPING AS", "tilDato": "2021-01-21 16:40:42"},
        {"fraDato": "2000-08-15 18:19:03", "navn": "STRAND SHIPHOLDING AS", "tilDato": "2009-12-23 20:24:07"},
    ],
}
FEED = {"_embedded": {"oppdaterteEnheter": [
    {"dato": "2026-07-18T12:21:16.201Z", "endringer": [{"op": "replace", "path": "/sisteInnsendteAarsregnskap", "value": "2025"}],
     "endringstype": "Endring", "oppdateringsid": 24903238, "organisasjonsnummer": "982170451"},
    {"dato": "2025-11-13T05:00:00.000Z", "endringer": [], "endringstype": "Ukjent", "oppdateringsid": 22813200,
     "organisasjonsnummer": "982170451"},
]}}


def _snap(url: str, body: dict) -> Snapshot:
    return Snapshot(url=url, final_url=url, status=200, retrieved_at="2026-10-17T10:00:00Z",
                    content=json.dumps(body, ensure_ascii=False).encode(), content_type="application/json")


def _ctx() -> CompanyContext:
    ctx = CompanyContext("982170451")
    ent = _snap("https://data.brreg.no/enhetsregisteret/api/enheter/982170451", ENTITY)
    ctx.registry = ENTITY
    brreg.emit_entity_claims(ctx, ent, ENTITY)
    feed = _snap("https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter?x", FEED)
    brreg.emit_change_claims(ctx, feed, FEED["_embedded"]["oppdaterteEnheter"])
    brreg.emit_dated_activity(ctx)
    return ctx


def test_registry_events_become_dated_activity_with_source_time():
    ctx = _ctx()
    values = [c.value for c in ctx.claims_for("dated_activity")]
    assert values == [
        "Registry record updated: latest filed annual accounts (2026-07-18T12:21:16.201Z)",
        "Registry record updated (2025-11-13T05:00:00.000Z)",
        "Renamed from STRAND SHIPPING AS to STRAND SHIPPING INVEST AS (2021-01-21T16:40:42)",
        "Renamed from STRAND SHIPHOLDING AS to STRAND SHIPPING AS (2009-12-23T20:24:07)",
        "Registered in the Central Coordinating Register for Legal Entities (2000-08-15)",
        "Founded (2000-07-01)",
    ]
    # registry_change claims stay as they were
    assert len(ctx.claims_for("registry_change")) == 2


def test_every_dated_activity_cites_an_exact_span_in_its_source():
    ctx = _ctx()
    by_id = {e.id: e for e in ctx.evidence}
    for c in ctx.claims_for("dated_activity"):
        assert c.as_of and c.evidence_ids and c.section == "activity"
        for i in c.evidence_ids:
            ev = by_id[i]
            raw = ctx.snapshots[i].text()
            span, *_ = repair(raw, ev.claim_span, "application/json", ctx.anchors.get(i))
            assert span and span in raw
            assert ev.source_url.startswith("https://data.brreg.no/") and ev.content_sha256 and ev.retrieved_at


def test_dated_activity_is_deterministic():
    a = [c.value for c in _ctx().claims_for("dated_activity")]
    b = [c.value for c in _ctx().claims_for("dated_activity")]
    assert a == b
