"""Offline tests for the TED, Forskningsrådet, Smilefjes and DiBK connectors, on made-up data.

All org numbers below are synthetic (they only pass the mod-11 check). HTTP goes through the real
Fetcher with an httpx MockTransport, so budgets, robots handling and snapshots are exercised too.
"""
from __future__ import annotations

import asyncio
import gzip
import io
import json
import zipfile

import httpx
import pytest

from kilde.connectors import _records as R
from kilde.connectors import _xlsx, dibk, forskningsradet, smilefjes, ted
from kilde.http import Budget, Fetcher
from kilde.model import CompanyContext
from kilde.spans import repair

ORG = "100200309"     # ours
OTHER = "900800703"   # someone else
SUB = "812345672"     # a workplace (underenhet) of ORG
SUB_OTHER = "923456783"  # a workplace of OTHER


def run(coro):
    return asyncio.run(coro)


def fetcher(routes: dict[str, tuple[int, object]], calls: list | None = None, max_requests: int = 50) -> Fetcher:
    """A real Fetcher whose transport answers from `routes` (url substring -> (status, body))."""
    f = Fetcher(Budget(max_requests, 120))

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if calls is not None:
            calls.append((request.method, url, request.content))
        for key, (status, body) in routes.items():
            if key in url:
                if isinstance(body, bytes):
                    return httpx.Response(status, content=body)
                if isinstance(body, str):
                    return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})
                return httpx.Response(status, json=body)
        return httpx.Response(404, text="")

    f.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    return f


def write_cache(path, obj) -> str:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return str(path)


def assert_evidence_exact(ctx: CompanyContext) -> None:
    """Every available claim has a match and evidence whose span is an exact substring of its snapshot."""
    by_id = {e.id: e for e in ctx.evidence}
    for c in ctx.claims:
        if c.availability != "available":
            continue
        assert c.match and c.match["method"].endswith("orgnr") and c.match["detail"]
        for eid in c.evidence_ids:
            ev = by_id[eid]
            snap = ctx.snapshots[eid]
            assert ev.licence and ev.extraction_method and ev.content_sha256 == snap.sha256
            span, _, exact = repair(snap.text(), ev.claim_span, snap.content_type)
            assert exact and span in snap.text(), (ev.claim_span, snap.text()[:200])


def available(ctx: CompanyContext, field: str) -> list:
    return ctx.claims_for(field)


# ---------------------------------------------------------------- helpers
def test_is_orgnr_and_excel_date():
    assert R.is_orgnr(ORG) and not R.is_orgnr("100200300") and not R.is_orgnr("12345")
    assert R.excel_date("45534") == "2024-08-30"
    assert R.excel_date("2024-01-05 00:00:00") == "2024-01-05"
    assert R.excel_date("") is None


def test_span_with_finds_late_identifier():
    ln = R.line({"a": "x" * 500, "id": ["111", ORG]})
    span = R.span_with(ln, must=f'"{ORG}"')
    assert ORG in span and span in ln and len(span) <= R.MAX_SPAN


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    def cell(ref: str, v: str) -> str:
        return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>'
    body = "".join("<row>" + "".join(cell(f"{chr(65 + j)}{i + 1}", v) for j, v in enumerate(r)) + "</row>"
                   for i, r in enumerate(rows))
    sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
             + body + "</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def test_xlsx_reader():
    data = _xlsx_bytes([["A", "B"], ["1", "x"], ["2", "y"]])
    assert _xlsx.dicts(data) == [{"A": "1", "B": "x"}, {"A": "2", "B": "y"}]


# ---------------------------------------------------------------- TED
def notice(pub, date, *, winners=(), buyer=None, ntype="can-standard", proc=None, country="NOR"):
    n = {"publication-number": pub, "publication-date": date, "notice-type": ntype,
         "title-proc": {"eng": f"Synthetic procurement {pub}", "deu": "x"}, "procedure-identifier": proc or pub,
         "links": {"xml": {"MUL": "https://example.invalid"}}}
    if winners:
        n["winner-identifier"] = list(winners)
        n["winner-country"] = [country] * len(winners)
    if buyer:
        n["organisation-identifier-buyer"] = [buyer]
        n["buyer-country"] = ["NOR"]
        n["buyer-name"] = {"eng": ["Testkommune"]}
    return n


def ted_cache(tmp_path, notices):
    recs = {}
    for n in notices:
        rec = ted.make_record(n)
        recs[rec["publication-number"]] = R.line(rec)
    return write_cache(tmp_path / "ted.json.gz", {"built_at": "2026-10-01T00:00:00Z", "records": recs})


def test_ted_award_and_buyer_notices(tmp_path):
    path = ted_cache(tmp_path, [
        notice("1-2025", "2025-01-03+01:00", winners=["100 200 309", OTHER], buyer="964338531"),
        notice("2-2026", "2026-03-01Z", winners=[ORG], buyer=OTHER),
        notice("3-2026", "2026-04-01Z", buyer=ORG, ntype="cn-standard"),
        notice("4-2026", "2026-05-01Z", winners=[ORG], country="SWE"),   # not a Norwegian winner: ignored
    ])
    calls: list = []
    refresh = {"notices": [notice("9-2026", "2026-10-10Z", winners=[ORG], buyer=OTHER)], "iterationNextToken": None}
    f = fetcher({"api.ted.europa.eu": (200, refresh)}, calls)
    batch = run(ted.prepare_ted(f, [ORG, OTHER], cache_path=path))
    assert calls[0][0] == "POST" and b"publication-date>=20260930" in calls[0][2]
    ctx = CompanyContext(ORG)
    run(ted.fetch_ted(f, ctx, batch))
    awards = available(ctx, "public_contract_award")
    assert [c.value["notice"] for c in awards] == ["9-2026", "2-2026", "1-2025"]
    assert awards[2].match["detail"].startswith("TED eForms winner-identifier[0] '100 200 309'")
    assert [c.value["notice"] for c in available(ctx, "procurement_notice")] == ["3-2026"]
    assert awards[0].as_of == "2026-10-10"
    assert_evidence_exact(ctx)
    # the other company only wins one and buys one; never sees ORG's notices
    ctx2 = CompanyContext(OTHER)
    run(ted.fetch_ted(f, ctx2, batch))
    assert [c.value["notice"] for c in available(ctx2, "public_contract_award")] == ["1-2025"]
    assert {c.value["notice"] for c in available(ctx2, "procurement_notice")} == {"2-2026", "9-2026"}


def test_ted_absent_and_refresh_failure_uses_cache(tmp_path):
    path = ted_cache(tmp_path, [notice("2-2026", "2026-03-01Z", winners=[ORG])])
    f = fetcher({"api.ted.europa.eu": (500, {"error": "x"})})
    batch = run(ted.prepare_ted(f, [ORG, OTHER], cache_path=path))
    ctx = CompanyContext(OTHER)
    run(ted.fetch_ted(f, ctx, batch))
    assert ctx.claims[0].availability == "not_available" and not ctx.claims_for("public_contract_award")
    ctx = CompanyContext(ORG)
    run(ted.fetch_ted(f, ctx, batch))
    assert len(available(ctx, "public_contract_award")) == 1


def test_ted_versions_of_one_procedure_collapse(tmp_path):
    path = ted_cache(tmp_path, [notice("5-2026", "2026-02-01Z", winners=[ORG], proc="P1"),
                                notice("6-2026", "2026-02-09Z", winners=[ORG], proc="P1")])
    f = fetcher({"api.ted.europa.eu": (200, {"notices": []})})
    batch = run(ted.prepare_ted(f, [ORG], cache_path=path))
    ctx = CompanyContext(ORG)
    run(ted.fetch_ted(f, ctx, batch))
    assert [c.value["notice"] for c in available(ctx, "public_contract_award")] == ["6-2026"]


def test_ted_missing_batch_marks_failed():
    ctx = CompanyContext(ORG)
    run(ted.fetch_ted(None, ctx, None))
    assert ctx.claims[0].availability == "failed"


# ---------------------------------------------------------------- Forskningsrådet
def rcn_cache(tmp_path, extra_web_url="https://www.forskningsradet.no/siteassets/skattefunn/tall/260902-webliste-skattefunn.xlsx"):
    recs = {}
    for r in (forskningsradet.sf_hist_record({"Prosjektnummer": "300001", "Organisasjonsnummer": ORG,
                                              "Bedriftsnavn": "TESTFIRMA AS", "Prosjekttittel": "Smart sensor",
                                              "Søknad godkjent": "JA", "Søknad avslått": "NEI",
                                              "Vedtaksdato": "45000", "Prosjektets fra-år": "2023",
                                              "Prosjektets til-år": "2025"}),
              forskningsradet.sf_hist_record({"Prosjektnummer": "300002", "Organisasjonsnummer": ORG,
                                              "Prosjekttittel": "Rejected idea", "Søknad godkjent": "NEI"}),
              forskningsradet.rcn_record({"prosjektnummer": "264011", "organisasjonsnummer": ORG + ".0",
                                          "prosjekttittel": "Ocean tech", "prosjektfase": "Avsluttet",
                                          "prosjektstart": "2016", "prosjektslutt": "2017", "tildelt_belop": "250000.0",
                                          "prosjektleder": "Ola Nordmann"}),
              forskningsradet.rcn_record({"prosjektnummer": "264012", "organisasjonsnummer": ORG,
                                          "prosjekttittel": "Declined", "prosjektfase": "Avslag"}),
              forskningsradet.rcn_record({"prosjektnummer": "264013", "organisasjonsnummer": OTHER,
                                          "prosjekttittel": "Other firm", "prosjektfase": "Bevilgning"})):
        if r:
            recs[r[0]] = R.line(r[1])
    sources = {"SFW": {"url": extra_web_url, "retrieved_at": "2026-10-01T00:00:00Z"},
               "SF": {"url": forskningsradet.SF_HIST, "retrieved_at": "2026-10-01T00:00:00Z"},
               "RCN": {"url": forskningsradet.RCN_CSV, "retrieved_at": "2026-10-01T00:00:00Z"}}
    return write_cache(tmp_path / "rcn.json.gz", {"built_at": "2026-10-01T00:00:00Z", "sources": sources, "records": recs})


def test_forskningsradet_approved_and_funded_only(tmp_path):
    path = rcn_cache(tmp_path)
    page = '<a href="/siteassets/skattefunn/tall/261001-webliste-skattefunn.xlsx">Excel</a>'
    web = _xlsx_bytes([forskningsradet.SF_WEB_COLS,
                       ["90000738", ORG, "TESTFIRMA AS", "Ny prosess", "JA", "46000", "45950", "45658", "46387", "Oslo"],
                       ["90000739", ORG, "TESTFIRMA AS", "Pending", "", "", "45950", "", "", "Oslo"]])
    f = fetcher({"robots.txt": (200, "User-agent: *\nDisallow: /episerver/\n"),
                 "suksesshistorier": (200, page), "261001-webliste": (200, web)})
    batch = run(forskningsradet.prepare_forskningsradet(f, [ORG, OTHER], cache_path=path))
    assert batch["sources"]["SFW"]["url"].endswith("261001-webliste-skattefunn.xlsx")
    ctx = CompanyContext(ORG)
    run(forskningsradet.fetch_forskningsradet(f, ctx, batch))
    got = {c.value["project_number"]: c for c in available(ctx, "research_project")}
    assert set(got) == {"90000738", "300001", "264011"}  # rejected, pending and other firm's projects never appear
    assert got["90000738"].value["decision_date"] == "2025-12-09" and got["90000738"].value["period"] == "2025–2026"
    assert got["264011"].value["awarded_nok"] == 250000 and got["264011"].value["status"] == "funded, completed"
    assert "Ola" not in json.dumps([e.to_json() for e in ctx.evidence]) + str(batch["records"])
    assert got["300001"].match["method"] == "organisasjonsnummer_equals_orgnr"
    assert_evidence_exact(ctx)


def test_forskningsradet_none_and_refresh_skip(tmp_path):
    path = rcn_cache(tmp_path)
    calls: list = []
    f = fetcher({"robots.txt": (404, ""), "suksesshistorier": (200, '<a href="/siteassets/skattefunn/tall/260902-webliste-skattefunn.xlsx">')}, calls)
    batch = run(forskningsradet.prepare_forskningsradet(f, ["923456783"], cache_path=path))
    assert not any("260902-webliste" in u for _, u, _ in calls)  # same file as the cache: not downloaded again
    ctx = CompanyContext("923456783")
    run(forskningsradet.fetch_forskningsradet(f, ctx, batch))
    assert [c.availability for c in ctx.claims] == ["not_available"]


# ---------------------------------------------------------------- Smilefjes
HEADER = ("tilsynsobjektid;orgnummer;navn;adrlinje1;adrlinje2;postnr;poststed;tilsynid;sakref;status;dato;"
          "total_karakter;tilsynsbesoektype;tema1_no;tema1_nn;karakter1;tema2_no;tema2_nn;karakter2;tema3_no;"
          "tema3_nn;karakter3;tema4_no;tema4_nn;karakter4")


def srow(obj, num, name, date, grade, visit="0"):
    return (f"{obj}_Tilsynsobjekt;{num};{name};Gate 1;;0150;Oslo;T{obj}{date}_TilsynAvtale;2026/1;0;{date};{grade};{visit};"
            "Rutiner og ledelse;Rutiner og leiing;0;Lokaler og utstyr;Lokaler og utstyr;0;Mathåndtering;Mat;0;Merking;Merking;0")


def smile_cache(tmp_path):
    units = {SUB: R.line(smilefjes.unit_record({"organisasjonsnummer": SUB, "navn": "TESTKAFE", "overordnetEnhet": ORG,
                                                "organisasjonsform": {"kode": "BEDR"}}, "underenhet")),
             SUB_OTHER: R.line(smilefjes.unit_record({"organisasjonsnummer": SUB_OTHER, "navn": "ANNEN KAFE",
                                                      "overordnetEnhet": OTHER, "organisasjonsform": {"kode": "BEDR"}},
                                                     "underenhet"))}
    return write_cache(tmp_path / "smile.json.gz", {"built_at": "2026-10-01T00:00:00Z", "units": units,
                                                    "pages": {"ZA1": "/spisested/oslo/testkafe.ZA1/"}})


def test_smilefjes_through_underenhet_and_direct(tmp_path):
    path = smile_cache(tmp_path)
    csv_text = "﻿" + "\n".join([HEADER, srow("ZA1", SUB, "Testkafe", "05012024", "1"),
                                      srow("ZA1", SUB, "Testkafe", "12062026", "0"),
                                      srow("ZB2", ORG, "Testkiosk", "01022025", "2"),
                                      srow("ZC3", SUB_OTHER, "Annen kafe", "01012026", "3")]) + "\n"
    f = fetcher({"tilsyn.csv": (200, csv_text.encode())})
    batch = run(smilefjes.prepare_smilefjes(f, [ORG], cache_path=path))
    ctx = CompanyContext(ORG)
    run(smilefjes.fetch_smilefjes(f, ctx, batch))
    got = {c.value["place"]: c for c in available(ctx, "food_inspection")}
    assert set(got) == {"Testkafe", "Testkiosk"}
    kafe = got["Testkafe"]
    assert kafe.value["date"] == "2026-06-12" and kafe.value["grade"] == 0 and kafe.value["inspections_on_file"] == 2
    assert kafe.value["url"] == "https://smilefjes.mattilsynet.no/spisested/oslo/testkafe.ZA1/"
    assert kafe.match["method"] == "orgnummer_underenhet_of_orgnr" and len(kafe.evidence_ids) == 2
    assert got["Testkiosk"].match["method"] == "orgnummer_equals_orgnr"
    assert_evidence_exact(ctx)


def test_smilefjes_unknown_number_resolved_live_and_deleted_unit_ignored(tmp_path):
    path = smile_cache(tmp_path)
    new_sub = "900800703"  # not in the cache; Brreg says it is a workplace of ORG
    csv_text = "\n".join([HEADER, srow("ZD4", new_sub, "Ny kafe", "03032026", "0")])
    brreg = {"_embedded": {"underenheter": [{"organisasjonsnummer": new_sub, "navn": "NY KAFE", "overordnetEnhet": ORG,
                                             "organisasjonsform": {"kode": "BEDR"}}]}}
    f = fetcher({"tilsyn.csv": (200, csv_text.encode()), "api/underenheter?": (200, brreg),
                 "api/enheter?": (200, {"_embedded": {}})})
    batch = run(smilefjes.prepare_smilefjes(f, [ORG], cache_path=path))
    ctx = CompanyContext(ORG)
    run(smilefjes.fetch_smilefjes(f, ctx, batch))
    assert [c.value["place"] for c in available(ctx, "food_inspection")] == ["Ny kafe"]
    assert_evidence_exact(ctx)


def test_smilefjes_download_failure_publishes_nothing(tmp_path):
    path = smile_cache(tmp_path)
    f = fetcher({"tilsyn.csv": (503, b"")})
    with pytest.raises(RuntimeError):
        run(smilefjes.prepare_smilefjes(f, [ORG], cache_path=path))
    ctx = CompanyContext(ORG)
    run(smilefjes.fetch_smilefjes(f, ctx, None))
    assert [c.availability for c in ctx.claims] == ["failed"]


# ---------------------------------------------------------------- DiBK
def dibk_page(org, approved=True):
    status = ("<h3 class=\"text-3xl\">Foretaket har sentral godkjenning</h3><b class=\"text-secondary\">Godkjenningsperiode:</b> "
              "<span class=\"text-xl\">02.08.2024&ndash;02.08.2027</span>") if approved else \
        "<h3 class=\"text-3xl\">Foretaket har ikke gyldig sentral godkjenning</h3>"
    return (f"<html><body><h2 class=\"x\">TESTBYGG AS</h2><div><b class=\"text-secondary text-xl\">Organisasjonsnummer</b>"
            f"<p class=\"text-xl\">{org}</p></div>{status}<table><tbody>"
            "<tr><td class=\"py-2 text-xl\">Utførende</td><td class=\"py-2 text-xl\">Tømrerarbeid</td>"
            "<td class=\"py-2 text-xl\">1</td><td></td></tr></tbody></table></body></html>")


def test_dibk_approved(tmp_path):
    f = fetcher({"robots.txt": (200, "User-agent: *\nDisallow: /search\nDisallow: /api/\n"),
                 f"/enterprises/{ORG}": (200, dibk_page(ORG))})
    run(dibk.prepare_dibk(f, [ORG]))
    ctx = CompanyContext(ORG)
    run(dibk.fetch_dibk(f, ctx))
    (c,) = available(ctx, dibk.FIELD)
    assert c.value["valid_from"] == "2024-08-02" and c.value["valid_to"] == "2027-08-02"
    assert c.value["areas"] == [{"function": "Utførende", "area": "Tømrerarbeid", "class": "1"}]
    assert_evidence_exact(ctx)


def test_dibk_absent_invalid_and_wrong_org(tmp_path):
    robots = (200, "User-agent: *\nDisallow: /api/\n")
    f = fetcher({"robots.txt": robots})
    ctx = CompanyContext(ORG)
    run(dibk.fetch_dibk(f, ctx))
    assert [c.availability for c in ctx.claims] == ["not_available"]
    f = fetcher({"robots.txt": robots, f"/enterprises/{ORG}": (200, dibk_page(ORG, approved=False))})
    ctx = CompanyContext(ORG)
    run(dibk.fetch_dibk(f, ctx))
    assert [c.availability for c in ctx.claims] == ["not_available"]
    f = fetcher({"robots.txt": robots, f"/enterprises/{ORG}": (200, dibk_page(OTHER))})
    ctx = CompanyContext(ORG)
    run(dibk.fetch_dibk(f, ctx))
    assert [c.availability for c in ctx.claims] == ["failed"] and not ctx.claims_for(dibk.FIELD)


def test_dibk_robots_block(tmp_path):
    f = fetcher({"robots.txt": (200, "User-agent: *\nDisallow: /enterprises/\n")})
    ctx = CompanyContext(ORG)
    run(dibk.fetch_dibk(f, ctx))
    assert [c.availability for c in ctx.claims] == ["blocked"]


def test_likely_construction():
    assert dibk.likely_construction({"naeringskode1": {"kode": "43.210"}})
    assert not dibk.likely_construction({"naeringskode1": {"kode": "56.101"}})
