"""Offline tests for the NAV job-ads connector, on made-up companies and ads."""
from __future__ import annotations

import asyncio
import gzip
import json

from kilde.connectors import nav_jobs
from kilde.http import BudgetExhausted
from kilde.model import PUBLIC_JOB_FEED, CompanyContext, Snapshot

ORG = "100200300"  # Fjordlys Testbakeri AS (made up)
SUB = "100200399"  # its registered workplace
OTHER = "900800700"  # an unrelated company with a similar name
TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJl"
NAMES = {ORG: "Fjordlys Testbakeri AS", OTHER: "Nordvik Prøvemekanikk AS"}


class FakeFetcher:
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


def detail(uuid: str, orgnr: str, *, status: str = "ACTIVE", expires: str = "2099-01-01T00:00:00+01:00",
           title: str = "Baker", name: str = "Fjordlys Testbakeri AS") -> dict:
    return {"uuid": uuid, "status": status, "sistEndret": "2026-10-10T10:00:00+02:00",
            "ad_content": None if status != "ACTIVE" else {
                "uuid": uuid, "title": title, "published": "2026-10-01T08:00:00+02:00", "expires": expires,
                "employer": {"name": name, "orgnr": orgnr},
                "workLocations": [{"city": "TESTVIK", "municipal": "TESTVIK", "county": "PRØVELAND"}],
                "contactList": [{"name": "Kari Testperson", "email": "kari@example.invalid"}],
                "description": "<p>Ring Kari</p>", "link": f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"}}


def write_index(tmp_path, ads: dict, by_org: dict) -> str:
    p = tmp_path / "idx.json.gz"
    p.write_bytes(gzip.compress(json.dumps({
        "built_at": "2026-10-13T06:00:00Z", "feed_last_page": "/api/v1/feed/page-last",
        "ads": ads, "by_org": by_org}).encode()))
    return str(p)


def feed_page(items: list[tuple[str, str, str]], next_url=None) -> dict:
    return {"id": "page-last", "next_url": next_url, "items": [
        {"id": u, "url": f"/api/v1/feedentry/{u}",
         "_feed_entry": {"uuid": u, "status": s, "businessName": b, "sistEndret": "2026-10-13T09:00:00+02:00"}}
        for u, s, b in items]}


def base_routes(page: dict) -> list:
    return [("/api/publicToken", 200, f"Current public token for Nav Job Vacancy Feed:\n{TOKEN}"),
            ("/api/v1/feed/page-last", 200, page)]


def test_publishes_active_ad_matched_by_orgnr(tmp_path):
    idx_path = write_index(tmp_path, {"ad-1": {"uuid": "ad-1", "employer_orgnr": ORG, "sistEndret": "x",
                                               "published": "2026-10-01"}}, {ORG: ["ad-1"]})
    f = FakeFetcher(base_routes(feed_page([])) + [("/feedentry/ad-1", 200, detail("ad-1", ORG))])
    idx = run(nav_jobs.prepare(f, [ORG], NAMES, index_path=idx_path))
    assert idx.state == "ok" and idx.feed_complete
    ctx = CompanyContext(ORG)
    run(nav_jobs.fetch_jobs(f, ctx, idx))
    ads = ctx.claims_for("job_ad")
    assert len(ads) == 1
    v = ads[0].value
    assert v["title"] == "Baker" and v["uuid"] == "ad-1" and v["expires"] == "2099-01-01"
    assert v["url"].endswith("/stilling/ad-1") and v["location"] == "Testvik" and v["employer_name"]
    assert ads[0].match["method"] == "employer_orgnr" and ORG in ads[0].match["detail"]
    assert ads[0].as_of == "2026-10-01"
    count = ctx.claims_for("active_job_count")
    assert count and count[0].value == 1 and count[0].evidence_ids == ads[0].evidence_ids
    ev = {e.id: e for e in ctx.evidence}[ads[0].evidence_ids[0]]
    assert ev.source_class == PUBLIC_JOB_FEED and ev.licence and ev.content_sha256
    assert ORG in ev.claim_span and "Baker" in ev.claim_span
    stored = ctx.snapshots[ev.id].text()
    assert "Kari" not in stored and "contactList" not in stored  # personal data never stored
    assert "Authorization" not in stored


def test_stopped_expired_and_wrong_orgnr_never_published(tmp_path):
    ads = {u: {"uuid": u, "employer_orgnr": ORG, "sistEndret": "x"} for u in ("stop", "old", "moved", "gone")}
    idx_path = write_index(tmp_path, ads, {ORG: list(ads)})
    f = FakeFetcher(base_routes(feed_page([("gone", "INACTIVE", "Fjordlys Testbakeri AS")])) + [
        ("/feedentry/stop", 200, detail("stop", ORG, status="STOPPED")),
        ("/feedentry/old", 200, detail("old", ORG, expires="2020-01-01T00:00:00+01:00")),
        ("/feedentry/moved", 200, detail("moved", OTHER)),
    ])
    idx = run(nav_jobs.prepare(f, [ORG], NAMES, index_path=idx_path))
    ctx = CompanyContext(ORG)
    run(nav_jobs.fetch_jobs(f, ctx, idx))
    assert not ctx.claims_for("job_ad") and not ctx.claims_for("active_job_count")
    assert not any("/feedentry/gone" in c for c in f.calls)  # feed said inactive: not even fetched
    mark = [c for c in ctx.claims if c.field == "job_ad"][0]
    assert mark.availability == "not_available" and "as of" in mark.note and mark.evidence_ids


def test_new_ad_found_by_name_hint_but_matched_only_by_orgnr(tmp_path):
    idx_path = write_index(tmp_path, {}, {})
    page = feed_page([("new-1", "ACTIVE", "FJORDLYS TESTBAKERI A/S"),  # ours
                      ("new-2", "ACTIVE", "Fjordlys Testbakeri"),  # same name, other org number
                      ("new-3", "ACTIVE", "Helt Annen Bedrift AS")])  # no name hint: not fetched
    f = FakeFetcher(base_routes(page) + [
        ("/feedentry/new-1", 200, detail("new-1", ORG)),
        ("/feedentry/new-2", 200, detail("new-2", "555666777")),
    ])
    idx = run(nav_jobs.prepare(f, [ORG], NAMES, index_path=idx_path))
    assert not any("new-3" in c for c in f.calls)
    ctx = CompanyContext(ORG)
    n_before = len(f.calls)
    run(nav_jobs.fetch_jobs(f, ctx, idx))
    assert len(f.calls) == n_before  # details read in prepare are reused
    assert [c.value["uuid"] for c in ctx.claims_for("job_ad")] == ["new-1"]


def test_workplace_orgnr_matches_through_registry_parent(tmp_path):
    idx_path = write_index(tmp_path, {"ad-s": {"uuid": "ad-s", "employer_orgnr": SUB, "parent_orgnr": ORG,
                                               "sistEndret": "x"}}, {SUB: ["ad-s"], ORG: ["ad-s"]})
    brreg = {"_embedded": {"underenheter": [{"organisasjonsnummer": SUB, "navn": "FJORDLYS AVD TESTVIK",
                                             "overordnetEnhet": ORG}]}}
    f = FakeFetcher(base_routes(feed_page([])) + [("/underenheter?", 200, brreg),
                                                  ("/feedentry/ad-s", 200, detail("ad-s", SUB))])
    idx = run(nav_jobs.prepare(f, [ORG], NAMES, index_path=idx_path))
    ctx = CompanyContext(ORG)
    run(nav_jobs.fetch_jobs(f, ctx, idx))
    ad = ctx.claims_for("job_ad")[0]
    assert ad.value["employer_orgnr"] == SUB and "sub-unit" in ad.match["detail"]
    assert len(ad.evidence_ids) == 2  # the ad and the registry record tying the workplace to us


def test_workplace_not_confirmed_by_registry_is_dropped(tmp_path):
    idx_path = write_index(tmp_path, {"ad-s": {"uuid": "ad-s", "employer_orgnr": SUB, "parent_orgnr": ORG,
                                               "sistEndret": "x"}}, {ORG: ["ad-s"]})
    brreg = {"_embedded": {"underenheter": [{"organisasjonsnummer": SUB, "overordnetEnhet": OTHER}]}}
    f = FakeFetcher(base_routes(feed_page([])) + [("/underenheter?", 200, brreg),
                                                  ("/feedentry/ad-s", 200, detail("ad-s", SUB))])
    idx = run(nav_jobs.prepare(f, [ORG], NAMES, index_path=idx_path))
    ctx = CompanyContext(ORG)
    run(nav_jobs.fetch_jobs(f, ctx, idx))
    assert not ctx.claims_for("job_ad")


def test_token_failure_marks_failed_and_never_crashes(tmp_path):
    idx_path = write_index(tmp_path, {}, {})
    f = FakeFetcher([("/api/publicToken", 500, {"title": "Server Error"})])
    idx = run(nav_jobs.prepare(f, [ORG], NAMES, index_path=idx_path))
    assert idx.state == "failed"
    ctx = CompanyContext(ORG)
    run(nav_jobs.fetch_jobs(f, ctx, idx))
    assert [(c.field, c.availability) for c in ctx.claims] == [("job_ad", "failed")] and ctx.errors

    f2 = FakeFetcher([], fail=TimeoutError("slow"))
    idx2 = run(nav_jobs.prepare(f2, [ORG], NAMES, index_path=idx_path))
    assert idx2.state == "failed"
    idx3 = run(nav_jobs.prepare(f2, [ORG], NAMES, index_path=str(tmp_path / "missing.json.gz")))
    assert idx3.state == "failed" and "missing" in idx3.reason


def test_budget_exhausted_mid_company(tmp_path):
    idx_path = write_index(tmp_path, {"ad-1": {"uuid": "ad-1", "employer_orgnr": ORG, "sistEndret": "x"}},
                           {ORG: ["ad-1"]})
    f = FakeFetcher(base_routes(feed_page([])))
    idx = run(nav_jobs.prepare(f, [ORG], NAMES, index_path=idx_path))
    f.fail = BudgetExhausted("request budget exhausted")
    ctx = CompanyContext(ORG)
    run(nav_jobs.fetch_jobs(f, ctx, idx))
    c = [c for c in ctx.claims if c.field == "job_ad"][0]
    assert c.availability == "failed" and c.note == "budget_exhausted"


def test_norm_name():
    assert nav_jobs.norm_name("Fjordlys Testbakeri AS") == nav_jobs.norm_name("FJORDLYS TESTBAKERI A/S")
    assert nav_jobs.norm_name("Nordvik ASA") == "nordvik"
    assert nav_jobs.norm_name(None) == ""


def test_rotated_token_is_refetched_once(tmp_path):
    idx_path = write_index(tmp_path, {"ad-1": {"uuid": "ad-1", "employer_orgnr": ORG, "sistEndret": "x"}},
                           {ORG: ["ad-1"]})
    f = FakeFetcher(base_routes(feed_page([])) + [("/feedentry/ad-1", 200, detail("ad-1", ORG))])
    idx = run(nav_jobs.prepare(f, [ORG], NAMES, index_path=idx_path))
    first = {"done": False}
    real_get = f.get

    async def get(url, **kw):
        if "/feedentry/" in url and not first["done"]:
            first["done"] = True
            return Snapshot(url=url, final_url=url, status=401, retrieved_at="2026-10-13T00:00:00Z", content=b"{}",
                            content_type="application/json")
        return await real_get(url, **kw)

    f.get = get
    ctx = CompanyContext(ORG)
    run(nav_jobs.fetch_jobs(f, ctx, idx))
    assert len(ctx.claims_for("job_ad")) == 1
    assert sum("/api/publicToken" in c for c in f.calls) == 2


def test_partial_index_never_claims_absence(tmp_path):
    p = tmp_path / "partial.json.gz"
    p.write_bytes(gzip.compress(json.dumps({"built_at": "2026-10-13T06:00:00Z", "complete": False,
                                            "feed_last_page": "/api/v1/feed/page-last", "ads": {},
                                            "by_org": {}}).encode()))
    f = FakeFetcher(base_routes(feed_page([])))
    idx = run(nav_jobs.prepare(f, [ORG], NAMES, index_path=str(p)))
    ctx = CompanyContext(ORG)
    run(nav_jobs.fetch_jobs(f, ctx, idx))
    assert [(c.field, c.availability) for c in ctx.claims] == [("job_ad", "failed")]
