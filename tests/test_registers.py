"""Offline tests for the GLEIF, Finanstilsynet and Wikidata connectors, on made-up fixtures."""
from __future__ import annotations

import asyncio
import json

import pytest

from kilde.connectors import finanstilsynet, gleif, wikidata
from kilde.http import BudgetExhausted
from kilde.model import OPEN_DATA, OFFICIAL_REGISTER_OTHER, CompanyContext, Snapshot

ORG = "100200300"
OTHER = "900800700"
LEI = "TEST00LEI00000000001"
PARENT_LEI = "TEST00LEI00000000099"


class FakeFetcher:
    """Answers from a list of (url substring, status, body); records every URL asked for."""

    def __init__(self, routes: list[tuple[str, int, object]], *, fail: Exception | None = None):
        self.routes = routes
        self.fail = fail
        self.calls: list[str] = []

    async def get(self, url: str, *, headers=None, robots=False, cacheable=True, reserve=0) -> Snapshot:
        self.calls.append(url)
        if self.fail:
            raise self.fail
        for key, status, body in self.routes:
            if key in url:
                content = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                return Snapshot(url=url, final_url=url, status=status, retrieved_at="2026-10-13T00:00:00Z",
                                content=content, content_type="application/json")
        return Snapshot(url=url, final_url=url, status=404, retrieved_at="2026-10-13T00:00:00Z", content=b"{}",
                        content_type="application/json")


def run(coro):
    return asyncio.run(coro)


def states(ctx: CompanyContext) -> dict[str, str]:
    return {c.field: c.availability for c in ctx.claims}


def check_evidence(ctx: CompanyContext, source_class: str) -> None:
    by_id = {e.id: e for e in ctx.evidence}
    for c in ctx.claims:
        if c.availability == "available":
            assert c.match and c.match["method"] and c.match["detail"]
            assert c.evidence_ids
            for eid in c.evidence_ids:
                ev = by_id[eid]
                assert ev.claim_span and ev.licence and ev.extraction_method and ev.content_sha256
                assert ev.source_class == source_class


# ---------------------------------------------------------------- GLEIF
def lei_record(org_spaced: str, ra: str = "RA000472", lei: str = LEI, status: str = "ISSUED", parent: bool = True) -> dict:
    links = ({"relationship-record": f"https://api.gleif.org/api/v1/lei-records/{lei}/direct-parent-relationship",
              "lei-record": f"https://api.gleif.org/api/v1/lei-records/{lei}/direct-parent"} if parent else
             {"reporting-exception": f"https://api.gleif.org/api/v1/lei-records/{lei}/direct-parent-reporting-exception"})
    ulinks = ({"lei-record": f"https://api.gleif.org/api/v1/lei-records/{lei}/ultimate-parent"} if parent else
              {"reporting-exception": f"https://api.gleif.org/api/v1/lei-records/{lei}/ultimate-parent-reporting-exception"})
    return {"type": "lei-records", "id": lei, "attributes": {
        "lei": lei,
        "entity": {"legalName": {"name": "TESTFIRMA EKSEMPEL AS", "language": "no"},
                   "registeredAt": {"id": ra, "other": None}, "registeredAs": org_spaced, "status": "ACTIVE",
                   "legalAddress": {"country": "NO"}},
        "registration": {"status": status, "lastUpdateDate": "2026-05-01T10:00:00Z", "nextRenewalDate": "2027-05-01T10:00:00Z"}},
        "relationships": {"direct-parent": {"links": links}, "ultimate-parent": {"links": ulinks}}}


def parent_body(country: str = "SE") -> dict:
    return {"data": {"type": "lei-records", "id": PARENT_LEI, "attributes": {
        "lei": PARENT_LEI,
        "entity": {"legalName": {"name": "OPPDIKTET MORSELSKAP AB", "language": "sv"}, "legalAddress": {"country": country},
                   "registeredAt": {"id": "RA000544"}, "registeredAs": "556000-0000"},
        "registration": {"status": "ISSUED", "lastUpdateDate": "2026-01-02T00:00:00Z"}}}}


def test_gleif_spaced_query_and_parents():
    f = FakeFetcher([("/direct-parent", 200, parent_body()), ("/ultimate-parent", 200, parent_body()),
                     ("lei-records?filter", 200, {"meta": {"pagination": {"total": 1}}, "data": [lei_record("100 200 300")]})])
    ctx = CompanyContext(ORG)
    run(gleif.fetch_gleif(f, ctx))
    assert "100%20200%20300,100200300" in f.calls[0]
    assert len(f.calls) == 3 and ctx.requests == 3
    lei = ctx.claims_for("lei")[0]
    assert lei.value == {"lei": LEI, "status": "ACTIVE", "registration_status": "ISSUED",
                         "next_renewal": "2027-05-01", "last_update": "2026-05-01"}
    assert lei.match["method"] == "registered_as_equals_orgnr"
    dp = ctx.claims_for("lei_direct_parent")[0].value
    assert dp == {"lei": PARENT_LEI, "name": "OPPDIKTET MORSELSKAP AB", "country": "SE"}
    assert ctx.claims_for("lei_ultimate_parent")
    check_evidence(ctx, OPEN_DATA)
    assert not ctx.errors


def test_gleif_unspaced_registered_as_matches():
    f = FakeFetcher([("lei-records?filter", 200, {"data": [lei_record("100200300", parent=False)]})])
    ctx = CompanyContext(ORG)
    run(gleif.fetch_gleif(f, ctx))
    assert ctx.claims_for("lei")


def test_gleif_reporting_exception_costs_no_requests():
    f = FakeFetcher([("lei-records?filter", 200, {"data": [lei_record("100 200 300", parent=False)]})])
    ctx = CompanyContext(ORG)
    run(gleif.fetch_gleif(f, ctx))
    assert len(f.calls) == 1
    s = states(ctx)
    assert s["lei_direct_parent"] == "not_available" and s["lei_ultimate_parent"] == "not_available"
    notes = [c.note for c in ctx.claims if c.field == "lei_direct_parent"]
    assert "reporting exception" in notes[0]
    exc_ev = next(e for e in ctx.evidence if e.id == ctx.claims[1].evidence_ids[0])
    assert "reporting-exception" in exc_ev.claim_span


def test_gleif_rejects_other_number_and_other_authority():
    data = [lei_record("100 200 301"), lei_record("100 200 300", ra="RA000999")]
    f = FakeFetcher([("lei-records?filter", 200, {"meta": {"pagination": {"total": 2}}, "data": data})])
    ctx = CompanyContext(ORG)
    run(gleif.fetch_gleif(f, ctx))
    assert states(ctx) == {"lei": "not_available"}
    ev = ctx.evidence[0]
    assert '"100 200 300"' in ev.claim_span and ev.http_status == 200


def test_gleif_duplicate_is_ignored_but_two_live_records_are_ambiguous():
    dup = lei_record("100 200 300", lei="TEST00LEI00000000002", status="DUPLICATE")
    f = FakeFetcher([("lei-records?filter", 200, {"data": [dup, lei_record("100 200 300", parent=False)]})])
    ctx = CompanyContext(ORG)
    run(gleif.fetch_gleif(f, ctx))
    assert ctx.claims_for("lei")[0].value["lei"] == LEI

    two = [lei_record("100 200 300", parent=False), lei_record("100 200 300", lei="TEST00LEI00000000003", parent=False)]
    ctx = CompanyContext(ORG)
    run(gleif.fetch_gleif(FakeFetcher([("lei-records?filter", 200, {"data": two})]), ctx))
    assert states(ctx) == {"lei": "ambiguous"}


def test_gleif_never_raises():
    ctx = CompanyContext(ORG)
    run(gleif.fetch_gleif(FakeFetcher([("lei-records", 500, "oops")]), ctx))
    assert states(ctx) == {"lei": "failed"} and ctx.errors
    ctx = CompanyContext(ORG)
    run(gleif.fetch_gleif(FakeFetcher([], fail=BudgetExhausted("request budget exhausted")), ctx))
    assert states(ctx) == {"lei": "failed"} and "budget_exhausted" in ctx.errors[0]["message"]


def test_gleif_parent_failure_keeps_lei():
    f = FakeFetcher([("/direct-parent", 502, "bad gateway"),
                     ("lei-records?filter", 200, {"data": [lei_record("100 200 300")]})])
    ctx = CompanyContext(ORG)
    run(gleif.fetch_gleif(f, ctx))
    s = states(ctx)
    assert s["lei"] == "available" and s["lei_direct_parent"] == "failed" and s["lei_ultimate_parent"] == "failed"


# ---------------------------------------------------------------- Finanstilsynet
def ft_entity(org: str, licences: list[dict] | None = None) -> dict:
    return {"legalEntityId": 1, "finanstilsynetId": "FT00000001", "organisationNumber": org,
            "name": "PRØVEFINANS AS", "licences": licences if licences is not None else [
                {"licensedEntity": {"legalEntityId": 1, "legalEntityName": "PRØVEFINANS AS"},
                 "licenceType": {"code": "BANK", "name": {"norwegian": "Bank", "english": "Bank"},
                                 "description": {"norwegian": "Lov om finansforetak § 2-1", "english": None}},
                 "supervisoryAuthority": {"country": {"iso3": "NOR"}},
                 "registeredDate": "2001-02-03T00:00:00", "serviceProviderType": "Licensed"},
                {"licensedEntity": {"legalEntityId": 2, "legalEntityName": "FIKTIV FORSIKRING ASA"},
                 "licenceType": {"code": "LIVSFORSIK", "name": {"norwegian": "Livsforsikringsforetak",
                                                                "english": "Life insurance company"}},
                 "registeredDate": "2020-01-01T00:00:00", "serviceProviderType": "Agent"}]}


def test_finanstilsynet_licences():
    f = FakeFetcher([("legal-entities/search", 200, {"total": 1, "legalEntities": [ft_entity(ORG)]})])
    ctx = CompanyContext(ORG)
    run(finanstilsynet.fetch_finanstilsynet(f, ctx))
    assert f.calls == [f"{finanstilsynet.API}/legal-entities/search?query={ORG}"]
    lic = ctx.claims_for("financial_licence")
    assert len(lic) == 2
    assert lic[0].value["type"] == "Bank" and lic[0].value["legal_basis"] == "Lov om finansforetak § 2-1"
    assert lic[0].value["registered_date"] == "2001-02-03" and lic[0].value["role"] == "licence_holder"
    assert lic[1].value["role"] == "agent" and lic[1].value["on_behalf_of"] == "FIKTIV FORSIKRING ASA"
    assert lic[0].as_of == "2001-02-03" and lic[0].section == "identity"
    check_evidence(ctx, OFFICIAL_REGISTER_OTHER)


def test_finanstilsynet_requires_org_number_field_match():
    f = FakeFetcher([("legal-entities/search", 200, {"total": 1, "legalEntities": [ft_entity(OTHER)]})])
    ctx = CompanyContext(ORG)
    run(finanstilsynet.fetch_finanstilsynet(f, ctx))
    assert states(ctx) == {"financial_licence": "not_available"}
    assert OTHER in ctx.evidence[0].claim_span


def test_finanstilsynet_entity_without_licences_and_errors():
    f = FakeFetcher([("legal-entities/search", 200, {"total": 1, "legalEntities": [ft_entity(ORG, licences=[])]})])
    ctx = CompanyContext(ORG)
    run(finanstilsynet.fetch_finanstilsynet(f, ctx))
    assert states(ctx) == {"financial_licence": "not_available"}
    ctx = CompanyContext(ORG)
    run(finanstilsynet.fetch_finanstilsynet(FakeFetcher([("search", 429, "slow down")]), ctx))
    assert states(ctx) == {"financial_licence": "failed"} and ctx.errors


# ---------------------------------------------------------------- Wikidata
QID = "Q999000111"


def qlever_body(rows: list[tuple[str, str]], labels: list[tuple[str, str, str, str]] = ()) -> dict:
    b = [{"org": {"type": "literal", "value": o}, "item": {"type": "uri", "value": f"http://www.wikidata.org/entity/{q}"}}
         for o, q in rows]
    for o, q, ref, lab in labels:
        b.append({"org": {"type": "literal", "value": o}, "item": {"type": "uri", "value": f"http://www.wikidata.org/entity/{q}"},
                  "prop": {"type": "literal", "value": "P169"},
                  "ref": {"type": "uri", "value": f"http://www.wikidata.org/entity/{ref}"},
                  "label": {"type": "literal", "value": lab, "xml:lang": "nb"}})
    return {"head": {"vars": ["org", "item"]}, "results": {"bindings": b}}


def snak(value, rank="normal", quals: dict | None = None, sid="S1") -> dict:
    s = {"id": sid, "rank": rank, "mainsnak": {"snaktype": "value", "datavalue": {"value": value}}}
    if quals:
        s["qualifiers"] = {p: [{"snaktype": "value", "datavalue": {"value": v}} for v in vs] for p, vs in quals.items()}
    return s


def t(time: str, precision: int = 11) -> dict:
    return {"time": time, "precision": precision}


def entity(org: str = ORG) -> dict:
    item = lambda q: {"entity-type": "item", "id": q}  # noqa: E731
    return {"entities": {QID: {"id": QID, "modified": "2026-09-01T00:00:00Z",
                               "labels": {"nb": {"language": "nb", "value": "Eksempelselskapet"}},
                               "claims": {
        "P2333": [snak(org)],
        "P856": [snak("https://example-abroad.test", quals={"P1001": [item("Q30")]}, sid="W1"),
                 snak("https://eksempel.test", quals={"P1001": [item("Q20")]}, sid="W2"),
                 snak("https://old.eksempel.test", rank="deprecated", sid="W3")],
        "P571": [snak(t("+1999-04-00T00:00:00Z", 10))],
        "P414": [snak(item("Q111"), quals={"P249": ["EKS"], "P580": [t("+2010-01-05T00:00:00Z")]}),
                 snak(item("Q222"), quals={"P249": ["GONE"], "P582": [t("+2015-01-01T00:00:00Z")]})],
        "P946": [snak("NO0000000001")],
        "P1278": [snak(LEI)],
        "P169": [snak(item("Q501"), quals={"P582": [t("+2019-00-00T00:00:00Z", 9)]}),
                 snak(item("Q502"), rank="preferred", quals={"P580": [t("+2019-00-00T00:00:00Z", 9)]})],
        "P1128": [snak({"amount": "+120", "unit": "1"}, quals={"P585": [t("+2021-00-00T00:00:00Z", 9)]}),
                  snak({"amount": "+150", "unit": "1"}, quals={"P585": [t("+2024-12-31T00:00:00Z")]}),
                  snak({"amount": "+999", "unit": "1"})],
        "P2002": [snak("eksempel_no")],
        "P4264": [snak("eksempelselskapet")],
        "P154": [snak("Eksempel logo.svg")],
    }}}}


ROBOTS = """User-agent: *
Disallow: /w/
Disallow: /wiki/Special%3A
Disallow: /wiki/Special:EntityData/
Allow: /wiki/Special:EntityData/*.
"""


def wd_routes(qlever: dict, ent: dict | None = None) -> list:
    return [("qlever.dev", 200, qlever), ("robots.txt", 200, ROBOTS),
            ("Special:EntityData", 200, ent if ent is not None else entity())]


def test_wikidata_robots_rfc9309():
    rules = wikidata.parse_robots(ROBOTS, "KildeSignalpost")
    ok = wikidata.rfc9309_allowed
    assert ok(rules, f"https://www.wikidata.org/wiki/Special:EntityData/{QID}.json")
    assert not ok(rules, f"https://www.wikidata.org/wiki/Special:EntityData/{QID}")
    assert not ok(rules, "https://www.wikidata.org/w/api.php?action=wbsearchentities")
    assert ok(rules, f"https://www.wikidata.org/wiki/{QID}")
    named = wikidata.parse_robots("User-agent: KildeSignalpost\nDisallow: /\n\nUser-agent: *\nAllow: /\n", "KildeSignalpost")
    assert not ok(named, "https://www.wikidata.org/wiki/Q1")


def test_wikidata_batch_is_one_query():
    f = FakeFetcher([("qlever.dev", 200, qlever_body([(ORG, QID)], [(ORG, QID, "Q502", "Kari Oppdiktet")]))])
    batch = run(wikidata.prepare_wikidata(f, [ORG, OTHER, ORG, "bad"]))
    assert len(f.calls) == 1
    assert dict(batch) == {ORG: QID}
    assert batch.queried == {ORG, OTHER}
    assert batch.labels["Q502"]["nb"] == "Kari Oppdiktet"
    assert "P2333" in f.calls[0] and ORG in f.calls[0] and OTHER in f.calls[0]


def test_wikidata_entity_claims():
    q = qlever_body([(ORG, QID)], [(ORG, QID, "Q502", "Kari Oppdiktet")])
    f = FakeFetcher(wd_routes(q))
    batch = run(wikidata.prepare_wikidata(f, [ORG]))
    ctx = CompanyContext(ORG)
    run(wikidata.fetch_wikidata(f, ctx, batch))
    assert ctx.requests == 1 and not ctx.errors
    by = {}
    for c in ctx.claims:
        by.setdefault(c.field, []).append(c)
    assert by["wikidata_item"][0].value["qid"] == QID and len(by["wikidata_item"][0].evidence_ids) == 2
    # Wikidata's website is only a candidate: not published unless Kilde verified the same site itself.
    assert "official_website" not in by and ctx.facts["wikidata_website_candidate"] == "eksempel.test"
    assert by["inception_date"][0].value == "1999-04"
    assert [c.value["ticker"] for c in by["stock_listing"]] == ["EKS"]
    assert by["isin"][0].value == "NO0000000001"
    assert by["lei"][0].value == {"lei": LEI}
    ceo = by["reported_ceo"][0]
    assert ceo.value == {"name": "Kari Oppdiktet", "qid": "Q502", "since": "2019"} and ceo.section == "leadership"
    assert by["employees_reported"][0].value == {"count": 150, "point_in_time": "2024-12-31"}
    assert "social_profile" not in by  # socials only when Wikidata's site is the verified site
    assert by["logo"][0].value["url"] == "https://commons.wikimedia.org/wiki/File:Eksempel_logo.svg"
    assert "headquarters" not in by
    check_evidence(ctx, OPEN_DATA)
    assert all(e.licence == wikidata.LICENCE for e in ctx.evidence)


def test_wikidata_ceo_conflicting_with_registry_is_not_published():
    q = qlever_body([(ORG, QID)], [(ORG, QID, "Q502", "Kari Oppdiktet")])
    f = FakeFetcher(wd_routes(q))
    ctx = CompanyContext(ORG)
    ev = ctx.add_evidence(None, source_class="official_registry", method="test", span="x")
    ctx.add_claim("ceo", "leadership", {"name": "Ola Annenmann"}, ev)
    run(wikidata.fetch_wikidata(f, ctx, run(wikidata.prepare_wikidata(f, [ORG]))))
    assert ctx.claims_for("reported_ceo") == []


def test_wikidata_no_item_and_fallback_query():
    f = FakeFetcher([("qlever.dev", 200, qlever_body([]))])
    ctx = CompanyContext(ORG)
    run(wikidata.fetch_wikidata(f, ctx))  # no batch: one single-company query
    assert ctx.requests == 1 and len(f.calls) == 1
    assert states(ctx) == {"wikidata_item": "not_available"}
    assert ctx.evidence[0].claim_span and ORG in ctx.evidence[0].claim_span


def test_wikidata_live_entity_must_carry_number():
    f = FakeFetcher(wd_routes(qlever_body([(ORG, QID)]), entity(org=OTHER)))
    ctx = CompanyContext(ORG)
    run(wikidata.fetch_wikidata(f, ctx, run(wikidata.prepare_wikidata(f, [ORG]))))
    assert states(ctx) == {"wikidata_item": "ambiguous"}


def test_wikidata_two_items_is_ambiguous_and_robots_block_is_blocked():
    f = FakeFetcher([("qlever.dev", 200, qlever_body([(ORG, QID), (ORG, "Q999000222")]))])
    ctx = CompanyContext(ORG)
    run(wikidata.fetch_wikidata(f, ctx, run(wikidata.prepare_wikidata(f, [ORG]))))
    assert states(ctx) == {"wikidata_item": "ambiguous"}

    f = FakeFetcher([("qlever.dev", 200, qlever_body([(ORG, QID)])), ("robots.txt", 200, "User-agent: *\nDisallow: /\n")])
    ctx = CompanyContext(ORG)
    run(wikidata.fetch_wikidata(f, ctx, run(wikidata.prepare_wikidata(f, [ORG]))))
    assert states(ctx) == {"wikidata_item": "blocked"}
    assert not any("EntityData" in u for u in f.calls)


def test_wikidata_batch_failure_never_raises():
    f = FakeFetcher([("qlever.dev", 503, "down")])
    batch = run(wikidata.prepare_wikidata(f, [ORG]))
    ctx = CompanyContext(ORG)
    run(wikidata.fetch_wikidata(f, ctx, batch))
    assert states(ctx) == {"wikidata_item": "failed"} and ctx.errors
    assert len(f.calls) == 1  # no retry per company after a failed batch query


@pytest.mark.parametrize("value,expected", [
    (t("+2001-06-18T00:00:00Z"), "2001-06-18"), (t("+2019-10-00T00:00:00Z", 10), "2019-10"),
    (t("+2018-00-00T00:00:00Z", 9), "2018"), (t("+1800-00-00T00:00:00Z", 7), None), (None, None)])
def test_wd_date(value, expected):
    assert wikidata.wd_date(value) == expected


@pytest.mark.parametrize("site,expected", [
    ("www.facebook.com/fjellbekk", ["facebook.com/fjellbekk"]),
    ("facebook.com/www.fjellbekk.no", []),
    ("https://www.instagram.com/www.fjellbekk.no/", []),
    ("https://www.linkedin.com/company/fjellbekk.no/", []),
])
def test_registry_declared_social_must_be_a_handle(site, expected):
    from kilde.connectors import brreg
    e = {"organisasjonsnummer": ORG, "navn": "FJELLBEKK AS", "hjemmeside": site}
    snap = Snapshot(url=f"https://data.brreg.no/enhetsregisteret/api/enheter/{ORG}", final_url="", status=200,
                    retrieved_at="2026-10-13T00:00:00Z", content=json.dumps(e).encode(), content_type="application/json")
    ctx = CompanyContext(ORG)
    brreg.emit_entity_claims(ctx, snap, e)
    assert [str(c.value) for c in ctx.claims_for("social_profile")] == expected


class _OneSnap:
    def __init__(self, body: dict, status: int = 200):
        self.body, self.status = body, status

    async def get(self, url, *, headers=None, robots=False, cacheable=True, reserve=0):
        return Snapshot(url=url, final_url=url, status=self.status, retrieved_at="2026-10-13T00:00:00Z",
                        content=json.dumps(self.body, separators=(",", ":")).encode(), content_type="application/json")


def _role(code, desc, fornavn, *, dead=False, resigned=False):
    r = {"type": {"kode": code, "beskrivelse": desc}, "person": {"navn": {"fornavn": fornavn, "etternavn": "Testesen"},
                                                                 "erDoed": dead}, "fratraadt": resigned}
    return r


@pytest.mark.parametrize("roles,note", [
    ([_role("DAGL", "Daglig leder", "Kari", dead=True), _role("LEDE", "Styrets leder", "Ola")],
     "marked deceased in the register"),
    ([_role("DAGL", "Daglig leder", "Kari", resigned=True), _role("LEDE", "Styrets leder", "Ola")], "has resigned"),
    ([_role("LEDE", "Styrets leder", "Ola")], "No general manager registered"),
])
def test_ceo_reason_says_what_the_register_holds(roles, note):
    from kilde.connectors import brreg
    body = {"rollegrupper": [{"type": {"beskrivelse": "Roller"}, "roller": roles}]}
    ctx = CompanyContext(ORG)
    run(brreg.fetch_roles(_OneSnap(body), ctx))
    ceo = next(c for c in ctx.claims if c.field == "ceo")
    assert ceo.availability == "not_available" and note in ceo.note
    assert "Kari" not in json.dumps([c.to_json() for c in ctx.claims] + [e.to_json() for e in ctx.evidence])
    assert [c.value["name"] for c in ctx.claims_for("board_chair")] == ["Ola Testesen"]


def test_all_closed_subunits_are_named_as_such():
    from kilde.connectors import brreg
    body = {"_embedded": {"underenheter": [{"organisasjonsnummer": "999000011", "navn": "X", "nedleggelsesdato": "2020-01-01"}]},
            "page": {"totalElements": 1}}
    ctx = CompanyContext(ORG)
    run(brreg.fetch_subunits(_OneSnap(body), ctx))
    wp = next(c for c in ctx.claims if c.field == "registered_workplace")
    assert wp.availability == "not_available" and "all closed" in wp.note


def test_motor_vehicle_dealers_are_checked_for_finanstilsynet_licences():
    from kilde.pipeline import _maybe_licensed
    assert _maybe_licensed({"naeringskode1": {"kode": "47.810"}})
    assert _maybe_licensed({"naeringskode1": {"kode": "45.111"}})
    assert not _maybe_licensed({"naeringskode1": {"kode": "41.200"}})


def test_each_accounts_year_quotes_its_own_record_even_with_a_repeated_id():
    import json as _j
    from kilde.connectors.accounts import _record_anchor
    from kilde.spans import region
    recs = [{"id": 1, "regnskapsperiode": {"fraDato": f"{y}-01-01", "tilDato": f"{y}-12-31"}, "regnskapstype": "SELSKAP",
             "resultatregnskapResultat": {"driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 0.0}}}}
            for y in (2025, 2024, 2023)]
    raw = _j.dumps(recs)
    regions = {raw[slice(*region(raw, _record_anchor(r)))] for r in recs}
    assert len(regions) == 3 and all('"tilDato": "%d-12-31"' % y in "".join(regions) for y in (2025, 2024, 2023))


def test_wikidata_statement_span_starts_with_its_value():
    from kilde.connectors.wikidata import stmt_span
    s = stmt_span("P2002", {"id": "Q1$x", "rank": "normal", "mainsnak": {"datavalue": {"value": "fjellbekk"}}})
    assert s.startswith('{"value"') and "fjellbekk" in s
