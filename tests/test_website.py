"""Website identity gate and extraction, on synthetic sites for made-up companies."""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import pytest

from kilde.connectors import website as W
from kilde.http import Blocked, Budget
from kilde.model import CompanyContext, Snapshot, utc_now

OURS = "999000002"
OTHER = "999000010"
THIRD = "999000029"
FOURTH = "999000037"


class FakeFetcher:
    """Serves canned pages; robots.txt is modelled by a set of disallowed URLs."""

    def __init__(self, pages: dict[str, tuple[int, str] | tuple[int, str, str]], disallow: set[str] = frozenset(),
                 redirects: dict[str, str] | None = None):
        self.budget = Budget(1000, 600)
        self.robots: dict = {}
        self.pages = pages
        self.disallow = set(disallow)
        self.redirects = redirects or {}
        self.calls: list[str] = []

    async def get(self, url, *, headers=None, robots=False, cacheable=True, reserve=0):
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self.robots:
            self.robots[origin] = "parsed"
            self.budget.used += 1
        if url in self.disallow:
            raise Blocked(url)
        self.budget.used += 1
        self.calls.append(url)
        final = self.redirects.get(url, url)
        hit = self.pages.get(final)
        if hit is None:
            raise ConnectionError("no such host")
        status, body = hit[0], hit[1]
        ctype = hit[2] if len(hit) > 2 else "text/html; charset=utf-8"
        return Snapshot(url=url, final_url=final, status=status, retrieved_at=utc_now(), content=body.encode(),
                        content_type=ctype)


@pytest.fixture(autouse=True)
def offline_dns(monkeypatch):
    async def yes(*a, **k):
        return True

    async def tls_only(host, port=443):
        return port == 443
    monkeypatch.setattr(W, "resolves", yes)
    monkeypatch.setattr(W, "reachable", tls_only)


def ctx_for(**rec) -> CompanyContext:
    ctx = CompanyContext(OURS)
    base = {"organisasjonsnummer": OURS, "navn": "FJELLBEKK TREVARE AS",
            "forretningsadresse": {"adresse": ["Lyngveien 12B"], "postnummer": "9999", "poststed": "TESTBY"},
            "organisasjonsform": {"kode": "AS"}}
    base.update(rec)
    ctx.registry = base
    return ctx


def run(f, ctx):
    asyncio.run(W.fetch_website(f, ctx))
    return {c.field: c for c in ctx.claims}


def html(body: str, title: str = "Fjellbekk Trevare", head: str = "") -> str:
    return f'<html lang="nb"><head><title>{title}</title>{head}</head><body>{body}</body></html>'


def test_orgnr_in_footer_with_spaces_and_extraction():
    head = ('<meta name="description" content="Snekkerverksted i Testby siden 1999.">'
            '<script type="application/ld+json">{"@type":"LocalBusiness","name":"Fjellbekk Trevare",'
            '"sameAs":["https://www.instagram.com/fjellbekktrevare/"]}</script>')
    body = ('<nav><a href="/kontakt">Kontakt</a><a href="/karriere">Karriere</a></nav>'
            '<p>Vi lager kjøkken.</p>'
            '<a href="https://www.facebook.com/fjellbekktrevare?ref=footer">Facebook</a>'
            '<a href="https://www.facebook.com/sharer/sharer.php?u=x">Del</a>'
            '<a href="https://twitter.com/intent/tweet?text=x">Tweet</a>'
            '<a href="https://www.linkedin.com/company/fjellbekk-trevare/about/?utm_source=site">LinkedIn</a>'
            '<a href="https://www.youtube.com/">YouTube</a>'
            '<a href="mailto:post@fjellbekk.no">post@fjellbekk.no</a>'
            '<footer>Fjellbekk Trevare AS · Org.nr: 999 000 002 MVA · Tlf. 41 00 00 01</footer>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body, head=head))})
    ctx = ctx_for(hjemmeside="www.fjellbekk.no")
    claims = run(f, ctx)
    site = claims["official_website"]
    assert site.availability == "available" and site.value == "https://fjellbekk.no/"
    assert site.match["method"] == "orgnr_on_page"
    span = next(e for e in ctx.evidence if e.id == site.evidence_ids[0]).claim_span
    assert "999 000 002" in span and "Org.nr" in span
    assert claims["website_description"].value == "Snekkerverksted i Testby siden 1999."
    assert claims["site_language"].value == "nb"
    assert claims["site_contact_email"].value == "post@fjellbekk.no"
    assert claims["site_contact_phone"].value == "+4741000001"
    assert claims["careers_page"].value == "https://fjellbekk.no/karriere"
    socials = {c.value["platform"]: c.value["url"] for c in ctx.claims if c.field == "social_profile"}
    assert socials == {"facebook": "https://www.facebook.com/fjellbekktrevare",
                       "linkedin": "https://www.linkedin.com/company/fjellbekk-trevare",
                       "instagram": "https://www.instagram.com/fjellbekktrevare"}
    assert claims["structured_data_organization"].value["name"] == "Fjellbekk Trevare"
    for c in ctx.claims:
        assert all(e.source_class == "company_owned" for e in ctx.evidence if e.id in c.evidence_ids)


def test_orgnr_in_jsonld_and_no_prefix():
    head = f'<script type="application/ld+json">{{"@type":"Organization","name":"X","vatID":"NO{OURS}MVA"}}</script>'
    f = FakeFetcher({"https://fjellbekk.no/": (200, html("<p>Hei</p>", head=head))})
    claims = run(f, ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["official_website"].match["method"] == "orgnr_on_page"


def test_parked_domain_is_rejected():
    parked = html("<h1>This domain is for sale!</h1><p>Fjellbekk Trevare AS 41 00 00 01</p>", title="fjellbekk.no")
    f = FakeFetcher({"https://fjellbekk.no/": (200, parked)})
    ctx = ctx_for(hjemmeside="fjellbekk.no", telefon="41 00 00 01")
    claims = run(f, ctx)
    assert claims["official_website"].availability == "not_available"
    assert ctx.facts["website_candidates"][0]["status"] == "parked"
    assert not ctx.claims_for("official_website")


def test_related_site_with_other_orgnr():
    body = (f"<p>Boligbyggelaget Testby forvalter Fjellbekk Trevare AS.</p>"
            f"<footer>Testby Forvaltning AS, org.nr. {OTHER[:3]} {OTHER[3:6]} {OTHER[6:]}. Tlf 41 00 00 01</footer>")
    f = FakeFetcher({"https://forvalter.no/": (200, html(body))})
    ctx = ctx_for(epostadresse="styret@forvalter.no", telefon="41000001")
    claims = run(f, ctx)
    assert claims["official_website"].availability == "ambiguous"
    rel = claims["related_website"]
    assert rel.availability == "ambiguous"
    assert rel.value == {"url": "https://forvalter.no/", "other_organisation_number": OTHER}


def test_name_only_match_rejected():
    body = "<h1>Fjellbekk Trevare</h1><p>Vi lager møbler i Annenby.</p>"
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    ctx = ctx_for(telefon="41000001")
    claims = run(f, ctx)
    assert claims["official_website"].availability == "ambiguous"
    assert not ctx.claims_for("official_website")
    assert not ctx.claims_for("social_profile")


def test_phone_and_name_on_contact_page():
    home = html('<h1>Fjellbekk Trevare</h1><a href="/kontakt-oss">Kontakt oss</a>')
    kontakt = html("<p>Ring oss på +47 410 00 001, Fjellbekk Trevare AS</p>")
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, home),
                     "https://fjellbekktrevare.no/kontakt-oss": (200, kontakt)})
    ctx = ctx_for(mobil="41000001")
    claims = run(f, ctx)
    site = claims["official_website"]
    assert site.match["method"] == "phone_and_name"
    spans = [e.claim_span for e in ctx.evidence if e.id in site.evidence_ids]
    assert any("410 00 001" in s for s in spans)


def test_address_and_name():
    body = "<footer>Fjellbekk Trevare AS, Lyngveien 12 B, 9999 Testby</footer>"
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    claims = run(f, ctx_for())
    assert claims["official_website"].match["method"] == "address_and_name"


def test_sister_company_name_blocks_strong_proof():
    """We are 'FJELLBEKK AS'; the site belongs to 'Fjellbekk Trevare AS' at the same phone."""
    body = "<footer>Fjellbekk Trevare AS · Tlf 41 00 00 01</footer>"
    f = FakeFetcher({"https://fjellbekk.com/": (200, html(body))})
    ctx = ctx_for(navn="FJELLBEKK AS", telefon="41000001")
    claims = run(f, ctx)
    assert not ctx.claims_for("official_website")
    assert claims["official_website"].availability == "ambiguous"


def test_registry_website_and_name_same_domain_only():
    body = "<h1>Fjellbekk Trevare</h1><p>Kjøkken og bad</p>"
    f = FakeFetcher({"https://www.fjellbekk.no/": (200, html(body))},
                    redirects={"https://fjellbekk.no/": "https://www.fjellbekk.no/"})
    claims = run(f, ctx_for(hjemmeside="https://fjellbekk.no"))
    assert claims["official_website"].match["method"] == "registry_website_and_name"
    # A cross-domain redirect from the registry website is not enough on its own.
    f2 = FakeFetcher({"https://storkonsern.com/": (200, html(body))},
                     redirects={"https://fjellbekk.no/": "https://storkonsern.com/"})
    ctx2 = ctx_for(hjemmeside="fjellbekk.no")
    run(f2, ctx2)
    assert not ctx2.claims_for("official_website")


def test_directory_page_with_many_orgnrs_is_ambiguous():
    body = "".join(f"<li>Lag {i} org.nr {n}</li>" for i, n in enumerate((OURS, OTHER, THIRD, FOURTH)))
    f = FakeFetcher({"https://forvalter.no/": (200, html(body))})
    ctx = ctx_for(epostadresse="post@forvalter.no")
    run(f, ctx)
    assert not ctx.claims_for("official_website")


def test_robots_blocked_registry_site():
    f = FakeFetcher({}, disallow={"https://fjellbekk.no/"})
    ctx = ctx_for(navn="QX AS", hjemmeside="fjellbekk.no")
    claims = run(f, ctx)
    assert claims["official_website"].availability == "blocked"


def test_sitemap_activity():
    body = f"<footer>Org nr {OURS}</footer>"
    sm = ('<?xml version="1.0"?><urlset>'
          + "".join(f"<url><loc>https://fjellbekk.no/p{i}</loc><lastmod>2025-0{i}-15</lastmod></url>" for i in range(1, 8))
          + "</urlset>")
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body)),
                     "https://fjellbekk.no/sitemap.xml": (200, sm, "application/xml")})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    acts = [c for c in ctx.claims if c.field == "site_page_updated"]
    assert [c.as_of for c in acts] == ["2025-07-15", "2025-06-15", "2025-05-15", "2025-04-15", "2025-03-15"]


def test_request_cap():
    f = FakeFetcher({})
    ctx = ctx_for(navn="FJELLBEKK SNEKKERI OG TREVARE AS")
    run(f, ctx)
    assert ctx.facts["website_requests"] <= W.MAX_REQUESTS


@pytest.mark.parametrize("url,expected", [
    ("https://www.facebook.com/fjellbekk/?ref=page", ("facebook", "https://www.facebook.com/fjellbekk")),
    ("https://facebook.com/profile.php?id=123&ref=x", ("facebook", "https://www.facebook.com/profile.php?id=123")),
    ("https://www.facebook.com/sharer.php?u=x", None),
    ("https://www.facebook.com/", None),
    ("https://www.facebook.com/yourpage", None),
    ("https://no.linkedin.com/company/fjellbekk/", ("linkedin", "https://www.linkedin.com/company/fjellbekk")),
    ("https://www.linkedin.com/in/someone", None),
    ("https://www.linkedin.com/shareArticle?url=x", None),
    ("https://twitter.com/fjellbekk", ("x", "https://x.com/fjellbekk")),
    ("https://twitter.com/intent/tweet", None),
    ("https://www.youtube.com/@fjellbekk", ("youtube", "https://www.youtube.com/@fjellbekk")),
    ("https://www.youtube.com/watch?v=abc", None),
    ("https://www.instagram.com/p/abc/", None),
    ("https://www.tiktok.com/@fjellbekk?lang=nb", ("tiktok", "https://www.tiktok.com/@fjellbekk")),
])
def test_clean_social(url, expected):
    assert W.clean_social(url) == expected


def test_candidates_order_and_free_mail():
    ctx = ctx_for(hjemmeside="http://www.fjellbekk.no", epostadresse="ola@gmail.com")
    c = W.candidates(ctx)
    assert c[0][:2] == ("fjellbekk.no", "registry")
    assert all(s != "email" for _, s, _ in c)
    assert ("fjellbekktrevare.no", "guess", None) in c
    ctx2 = ctx_for(epostadresse="post@fjellbekk-trevare.no")
    assert W.candidates(ctx2)[0][:2] == ("fjellbekk-trevare.no", "email")


def test_name_domain_guesses_skip_generic():
    assert W.name_domains("BYGG AS") == []
    assert "bjornoya-fisk.no" in W.name_domains("BJØRNØYA FISK AS")
    assert "bjoernoeyafisk.no" in W.name_domains("BJØRNØYA FISK AS")


def test_partner_social_links_dropped_and_footer_id_kept():
    body = (f'<main><p>Vi leverte lyd til <a href="https://www.instagram.com/kafe_solsikke/">Kafe Solsikke</a></p></main>'
            f'<footer>Org.nr {OURS} <a href="https://www.facebook.com/profile.php?id=10001234">Facebook</a>'
            f'<a href="https://www.instagram.com/fjellbekk.no/">Instagram</a></footer>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body))})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    urls = sorted(c.value["url"] for c in ctx.claims if c.field == "social_profile")
    assert urls == ["https://www.facebook.com/profile.php?id=10001234", "https://www.instagram.com/fjellbekk.no"]
    assert "https://www.instagram.com/kafe_solsikke" in ctx.facts["website_social_dropped"]


def test_tel_href_is_url_decoded():
    body = '<h1>Fjellbekk Trevare AS</h1><a href="tel:41%2000%2000%2001">Ring</a>'
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    claims = run(f, ctx_for(telefon="41000001"))
    assert claims["official_website"].match["method"] == "phone_and_name"
    assert claims["site_contact_phone"].value == "+4741000001"


def test_structured_data_with_other_entity_name_skipped():
    head = '<script type="application/ld+json">{"@type":"Organization","name":"Fjellbekk Holding Norge AS"}</script>'
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(f"<footer>Org.nr {OURS}</footer>", head=head))})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert ctx.claims_for("official_website") and not ctx.claims_for("structured_data_organization")


def test_guessed_domain_of_another_company_is_not_related():
    body = f"<h1>Fjellbekk</h1><footer>Fjellbekk Sport AS org.nr {OTHER}</footer>"
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    ctx = ctx_for()
    claims = run(f, ctx)
    assert "related_website" not in claims
    assert claims["official_website"].availability == "not_available"
    assert ctx.facts["website_candidates"][0]["status"] == "other_company"


def test_personal_mailboxes_only_without_generic():
    body = (f'<footer>Org.nr {OURS} <a href="mailto:kari.nordmann@fjellbekk.no">x</a>'
            f'<a href="mailto:post@fjellbekk.no">y</a><a href="mailto:hei@annet.no">z</a></footer>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body))})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert [c.value for c in ctx.claims if c.field == "site_contact_email"] == ["post@fjellbekk.no"]


def test_parked_bare_host_falls_through_to_www():
    f = FakeFetcher({"https://fjellbekk.no/": (200, html("<p>Domenet er til salgs</p>", title="fjellbekk.no")),
                     "https://www.fjellbekk.no/": (200, html(f"<footer>Org.nr {OURS}</footer>"))})
    claims = run(f, ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["official_website"].value == "https://www.fjellbekk.no/"


def test_real_robots_disallow_stops_the_candidate():
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(f"<footer>Org.nr {OURS}</footer>")),
                     "https://www.fjellbekk.no/": (200, html(f"<footer>Org.nr {OURS}</footer>"))},
                    disallow={"https://fjellbekk.no/"})
    ctx = ctx_for(navn="QX AS", hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert "https://www.fjellbekk.no/" not in f.calls
    assert ctx.facts["website_candidates"][0]["status"] == "blocked"


def test_hosting_error_page_with_domain_name_is_not_proof():
    body = "<h1>fjellbekktrevare.no is not found</h1><p>fjellbekktrevare.no is not found</p>"
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body, title="fjellbekktrevare.no"))})
    ctx = ctx_for(hjemmeside="fjellbekktrevare.no")
    run(f, ctx)
    assert not ctx.claims_for("official_website")


def test_name_inside_domain_or_email_does_not_count():
    body = "<p>Velkommen til www.fjellbekktrevare.no, skriv til post@fjellbekktrevare.no</p>"
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body, title="Velkommen"))})
    ctx = ctx_for(hjemmeside="fjellbekktrevare.no")
    run(f, ctx)
    assert not ctx.claims_for("official_website")
