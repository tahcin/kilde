"""Website identity gate and extraction, on synthetic sites for made-up companies."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

import pytest

from kilde.connectors import website as W
from kilde.http import Blocked, Budget
from kilde.model import CompanyContext, Snapshot, utc_now

_real_resolves = W.resolves

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
        if robots and origin not in self.robots:
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
    W._SISTER_CACHE.clear()
    W._NAME_SEARCH.clear()
    W._ROW_CACHE.clear()
    W._HJEM_CACHE.clear()


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
    assert site.availability == "available" and site.value == "fjellbekk.no"
    assert claims["official_website_url"].value == "https://fjellbekk.no/"
    assert site.match["method"] == "orgnr_on_page"
    span = next(e for e in ctx.evidence if e.id == site.evidence_ids[0]).claim_span
    assert "999 000 002" in span and "Org.nr" in span
    assert claims["website_description"].value == "Snekkerverksted i Testby siden 1999."
    assert claims["site_language"].value == "nb"
    assert claims["site_contact_email"].value == "post@fjellbekk.no"
    assert claims["site_contact_phone"].value == "+4741000001"
    socials = sorted(str(c.value) for c in ctx.claims if c.field == "social_profile")
    assert socials == ["facebook.com/fjellbekktrevare", "instagram.com/fjellbekktrevare",
                       "linkedin.com/company/fjellbekk-trevare"]
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
    urls = sorted(str(c.value) for c in ctx.claims if c.field == "social_profile")
    assert urls == ["instagram.com/fjellbekk.no"]  # profile.php links are never published
    assert "https://www.instagram.com/kafe_solsikke" in ctx.facts["website_social_dropped"]


def test_tel_href_is_url_decoded():
    body = '<footer>Fjellbekk Trevare AS <a href="tel:41%2000%2000%2001">Ring</a></footer>'
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
            f'<a href="mailto:post@fjellbekk.no">y</a><a href="mailto:hei@gmail.com">z</a></footer>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body))})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert [c.value for c in ctx.claims if c.field == "site_contact_email"] == ["post@fjellbekk.no"]


def test_parked_bare_host_falls_through_to_www():
    f = FakeFetcher({"https://fjellbekk.no/": (200, html("<p>Domenet er til salgs</p>", title="fjellbekk.no")),
                     "https://www.fjellbekk.no/": (200, html(f"<footer>Org.nr {OURS}</footer>"))})
    claims = run(f, ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["official_website"].value == "fjellbekk.no"


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


# ---------------------------------------------------------------- round 1 audit fixes
SISTER = "999000045"


def _group_site():
    nav = ('<nav><a href="/lyngstad-ventilasjon/">Lyngstad Ventilasjon</a>'
           '<a href="/lyngstad-ror/">Lyngstad Rør</a><a href="/kontakt-oss/">Kontakt</a></nav>')
    footer = ('<footer><div class="contact"><p>Vil du lufte noen tanker med Lyngstad Ventilasjon eller Lyngstad Rør?</p>'
              '<div class="group"><h4>Sentralbord</h4><a href="tel:41000000">41 00 00 00</a></div>'
              '<div class="group"><h4>Reklamasjon - Ventilasjon</h4><a href="tel:41000010">41 00 00 10</a></div>'
              '<div class="group"><h4>Reklamasjon - Rør</h4><a href="tel:41000020">41 00 00 20</a></div>'
              '<div class="group"><a href="mailto:post@lyngstad-v.no">post@lyngstad-v.no</a></div></div>'
              '<div class="jobs"><p>Jobb hos Lyngstad! Vi søker folk.</p><a href="/stilling/">Ledige stillinger</a></div>'
              '<a href="https://www.facebook.com/Lyngstad-Ventilasjon-AS-1234567890/"><i class="fb"></i></a></footer>')
    head = '<meta name="description" content="Vi i Lyngstad Gruppen hjelper deg med VVS.">'
    ror = html(nav + '<h1>Lyngstad Rør</h1><p>Rørlegger i Testby.</p><a href="/lyngstad-ror/">Jobb hos oss</a>' + footer,
               title="Lyngstad Rør - Lyngstad", head=head)
    kontakt = html(nav + "<p>Lyngstad Rør AS, Lyngveien 12B, 9999 Testby</p>" + footer, title="Kontakt")
    sm = ('<?xml version="1.0"?><urlset>'
          '<url><loc>https://lyngstadgruppen.no/lyngstad-ventilasjon/</loc><lastmod>2026-07-02</lastmod></url>'
          '<url><loc>https://lyngstadgruppen.no/lyngstad-ventilasjon-miljo/</loc><lastmod>2026-06-02</lastmod></url>'
          '<url><loc>https://lyngstadgruppen.no/lyngstad-ror-miljo/</loc><lastmod>2026-05-02</lastmod></url>'
          '<url><loc>https://lyngstadgruppen.no/apenhetsloven/</loc><lastmod>2026-04-02</lastmod></url></urlset>')
    brreg = json.dumps({"_embedded": {"enheter": [
        {"organisasjonsnummer": SISTER, "navn": "LYNGSTAD VENTILASJON AS"},
        {"organisasjonsnummer": "999000053", "navn": "LYNGSTAD VENTILASJON OG BAD AS"}]}})
    pages = {"https://lyngstadgruppen.no/lyngstad-ror/": (200, ror),
             "https://lyngstadgruppen.no/kontakt-oss/": (200, kontakt),
             "https://lyngstadgruppen.no/sitemap.xml": (200, sm, "application/xml"),
             W.BRREG_SEARCH + "?navn=lyngstad%20ventilasjon&size=20": (200, brreg, "application/json")}
    return FakeFetcher(pages, redirects={"https://lyngstadror.no/": "https://lyngstadgruppen.no/lyngstad-ror/"})


def test_group_site_with_sister_withholds_all_derived_items():
    """Round 1 Bjerke shape: one group site, our page plus a registered sister company's."""
    f = _group_site()
    f.pages["https://lyngstadgruppen.no/"] = f.pages.pop("https://lyngstadgruppen.no/lyngstad-ror/")
    ctx = ctx_for(navn="LYNGSTAD RØR AS", hjemmeside="lyngstadgruppen.no")
    claims = run(f, ctx)
    site = claims["official_website"]
    assert site.availability == "available" and SISTER in site.match["detail"]
    avail = {c.field for c in ctx.claims if c.availability == "available"}
    assert avail == {"official_website"}
    for fld in ("social_profile", "site_contact_phone", "site_contact_email", "site_page_updated", "hiring_signal"):
        assert claims[fld].availability == "ambiguous"
    assert ctx.facts["website_exclusive"] is False
    assert not any("sitemap" in u for u in f.calls)


def test_redirect_to_section_of_another_host_is_judged_on_that_page_alone():
    """Round 2 Solheim shape: the company domain redirects to its member page on a chain site."""
    member = html('<div class="kontakt"><h2>LYNGSTAD RØR AS</h2><div>Medlem siden 2009</div>'
                  '<a href="tel:41000020">41000020</a></div>'
                  '<a href="/om-oss/kontakt">Kontakt kjeden</a>'
                  '<footer><a href="https://www.facebook.com/rorkjeden">fb</a>'
                  '<a href="mailto:medlemsservice@rorkjeden.no">x</a></footer>', title="LYNGSTAD RØR AS | Rørkjeden")
    f = FakeFetcher({"https://www.rorkjeden.no/finn-rorlegger/testby/lyngstad-ror-as": (200, member),
                     "https://www.rorkjeden.no/om-oss/kontakt": (200, html("<p>Medlemsservice 41 99 99 99</p>"))},
                    redirects={"https://lyngstadror.no/": "https://www.rorkjeden.no/finn-rorlegger/testby/lyngstad-ror-as"})
    ctx = ctx_for(navn="LYNGSTAD RØR AS", hjemmeside="lyngstadror.no", mobil="41000020")
    claims = run(f, ctx)
    site = claims["official_website"]
    assert site.value == "https://www.rorkjeden.no/finn-rorlegger/testby/lyngstad-ror-as"
    assert site.match["method"] == "phone_and_name"
    assert {c.field for c in ctx.claims if c.availability == "available"} == {"official_website"}
    assert "https://www.rorkjeden.no/om-oss/kontakt" not in f.calls
    assert ctx.facts["website_exclusive"] is False


def test_redirect_to_brand_about_page_with_our_orgnr_is_exclusive():
    """Sport-Event shape: the company domain redirects to its brand site's about page, which prints our org number."""
    f = FakeFetcher({"https://turbarna.no/om-oss/": (200, html(f"<p>Turbarna drives av Fjellbekk Trevare AS, org.nr. {OURS}.</p>")),
                     "https://turbarna.no/": (200, html('<h1>Turbarna</h1><a href="mailto:post@turbarna.no">x</a>'))},
                    redirects={"https://fjellbekk.no/": "https://turbarna.no/om-oss/"})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(f, ctx)
    assert claims["official_website"].value == "turbarna.no"
    assert ctx.facts["website_exclusive"] is True
    assert claims["site_contact_email"].value == "post@turbarna.no"


def test_redirect_to_deep_page_on_another_host_stays_not_exclusive():
    f = FakeFetcher({"https://kjede.no/medlemmer/testby/fjellbekk": (200, html(f"<p>Fjellbekk Trevare AS org.nr {OURS}</p>"
                                                                               '<a href="tel:41000001">x</a>'))},
                    redirects={"https://fjellbekk.no/": "https://kjede.no/medlemmer/testby/fjellbekk"})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(f, ctx)
    assert claims["official_website"].value == "https://kjede.no/medlemmer/testby/fjellbekk"
    assert claims["site_contact_phone"].availability == "ambiguous"
    assert ctx.facts["website_exclusive"] is False


def test_web_agency_credit_and_own_name_tail_do_not_count_as_other_companies():
    pg = W.parse_page(Snapshot("https://a.no/", "https://a.no/", 200, "x", (
        "<title>Øvre Testby Salg og Utleie AS</title><p>Øvre Testby Salg og Utleie AS selger biler.</p>"
        '<footer>Nettside levert av <a href="https://byraa.no">Kvist Media AS</a> · '
        '<a href="https://byraa.no">Kvist Media AS</a></footer>').encode(), "text/html"))
    assert W.other_company_names([pg], W.name_words("ØVRE TESTBY SALG OG UTLEIE AS")) == []


def test_foreign_parent_site_with_swedish_org_number_is_related():
    """Round 2 Fruit shape: registry-listed .se site of a same-named Swedish AB."""
    body = ('<h1>Fjellbekk Trevare</h1><p>Kläder för reklam</p><footer>© 2026 Fjellbekk Trevare AB '
            'Org: 556323-0837 <a href="mailto:info@fjellbekk.se">info@fjellbekk.se</a> Tel +46 456 151 40</footer>')
    f = FakeFetcher({"https://fjellbekk.se/": (200, html(body, title="Fjellbekk Trevare"))})
    ctx = ctx_for(hjemmeside="fjellbekk.se", epostadresse="post@fjellbekk.se")
    claims = run(f, ctx)
    assert not ctx.claims_for("official_website")
    assert claims["related_website"].value["other_organisation_number"] == "SE:5563230837"
    assert not [c for c in ctx.claims if c.field in ("site_contact_email", "site_contact_phone")
                and c.availability == "available"]


def test_foreign_org_number_formats():
    pg = W.parse_page(Snapshot("https://a.no/", "https://a.no/", 200, "x", (
        "<p>Org: 556323-0837. CVR-nr. 12 34 56 78. Y-tunnus 1234567-8. VAT no: SE556323083701. "
        "Personnummer 010190-1234</p>").encode(), "text/html"))
    got = set(W.other_orgnrs(pg, OURS))
    assert {"SE:5563230837", "DK:12345678", "FI:12345678", "VAT:SE556323083701"} <= got
    assert not any("0101901234" in k for k in got)


def test_portal_with_other_schools_withholds_sitemap_pages():
    """Round 2 Nidaros shape: a portal for several schools, each its own legal entity."""
    home = html('<h3>Velg skole</h3><a href="/fjellbekk">Fjellbekk Trevare</a><a href="/kvist">Kvist Skole</a>'
                '<p>Fjellbekk Trevare AS, Lyngveien 12B, 9999 Testby. Telefon 41 00 00 01</p>'
                f'<footer>Kvist Skole AS org.nr {OTHER} · Fjellbekk Trevare AS org.nr {OURS}</footer>')
    sm = ('<?xml version="1.0"?><urlset><url><loc>https://skolene.no/soknad-kvist/</loc>'
          '<lastmod>2026-05-02</lastmod></url></urlset>')
    f = FakeFetcher({"https://skolene.no/": (200, home), "https://skolene.no/sitemap.xml": (200, sm, "application/xml")},
                    redirects={"https://fjellbekk.no/": "https://skolene.no/"})
    ctx = ctx_for(hjemmeside="fjellbekk.no", telefon="41000001")
    claims = run(f, ctx)
    assert claims["official_website"].availability == "available"
    assert claims["site_page_updated"].availability == "ambiguous"
    assert not any("sitemap" in u for u in f.calls)


def test_dealer_site_presented_by_another_company_and_privacy_phone():
    """Round 2 Toyota Voss shape: dealer group named on the site, brand owner's phone on the privacy page."""
    home = html('<h1>Fjellbekk Trevare</h1><p>Fjellbekk Trevare er Kvist Automobil AS sin avdeling i Testby.</p>'
                '<p>Velkommen til Kvist Automobil AS</p><a href="/personvern">Personvern</a>'
                '<footer>Fjellbekk Trevare AS, Lyngveien 12B, 9999 Testby</footer>')
    priv = html("<p>Har du spørsmål, ta kontakt med Kvist Norge AS på 32 20 50 00</p>")
    brreg = json.dumps({"_embedded": {"enheter": [{"organisasjonsnummer": SISTER, "navn": "KVIST AUTOMOBIL AS"}]}})
    f = FakeFetcher({"https://fjellbekk.no/": (200, home), "https://fjellbekk.no/personvern": (200, priv),
                     W.BRREG_SEARCH + "?navn=kvist%20automobil&size=20": (200, brreg, "application/json")})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(f, ctx)
    assert claims["official_website"].availability == "available"
    assert "kvist automobil" in claims["official_website"].match["detail"]
    assert claims["site_contact_phone"].availability == "ambiguous"


def test_contacts_never_from_privacy_pages():
    pages = [W.parse_page(Snapshot(u, u, 200, "x", b.encode(), "text/html")) for u, b in (
        ("https://fjellbekk.no/", html(f'<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>')),
        ("https://fjellbekk.no/personvern", html('<p>Fjellbekk Trevare AS behandler data. Kontakt '
                                                 'post@fjellbekk.no eller tlf 41 22 33 44</p>')))]
    em, ph, _ = W.extract_contacts(pages, "fjellbekk.no", set(), W.Scope(OURS, ["fjellbekk", "trevare"]))
    assert em == [] and ph == []


def test_phone_needs_registry_match_or_a_block_naming_us():
    body = (f'<footer><p>Fjellbekk Trevare AS · Org.nr {OURS} · Tlf 41 00 00 01</p></footer>'
            '<aside><p>Samarbeidspartner Kvist: tlf 41 99 99 99</p></aside>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body))})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert [c.value for c in ctx.claims if c.field == "site_contact_phone"] == ["+4741000001"]


def test_orgnr_inside_a_url_is_not_proof_and_span_is_label_plus_number():
    pg = W.parse_page(Snapshot("https://a.no/", "https://a.no/", 200, "x",
                               f"<p>https://sgregister.dibk.no/enterprises/{OURS} This message is only visible</p>"
                               .encode(), "text/html"))
    assert W.find_orgnr(pg, OURS) is None
    pg2 = W.parse_page(Snapshot("https://a.no/", "https://a.no/", 200, "x",
                                f"<p>Kontakt oss. Org.nr: {OURS[:3]} {OURS[3:6]} {OURS[6:]} MVA</p>".encode(),
                                "text/html"))
    assert W.find_orgnr(pg2, OURS) == f"Org.nr: {OURS[:3]} {OURS[3:6]} {OURS[6:]} MVA"


def test_exclusive_site_with_orgnr_on_about_page_sets_flag():
    """Sport-Event shape: org number on /om-oss/ only, nothing else on the site."""
    home = html('<h1>Turbarna</h1><a href="/om-oss/">Om oss</a>')
    om = html(f"<p>Turbarna drives av Fjellbekk Trevare AS, org.nr. {OURS}.</p>")
    f = FakeFetcher({"https://turbarna.no/": (200, home), "https://turbarna.no/om-oss/": (200, om)})
    ctx = ctx_for(hjemmeside="turbarna.no")
    claims = run(f, ctx)
    assert claims["official_website"].match["method"] == "orgnr_on_page"
    assert ctx.facts["website_exclusive"] is True


def test_foreign_country_code_is_kept():
    body = ('<h1>Fjellbekk Trevare</h1><a href="tel:+372 506 9999">Helista</a>'
            '<a href="tel:+37259180640">Helista</a><a href="tel:004741000001">x</a>')
    f = FakeFetcher({"https://fjellbekk.ee/": (200, html(body))})
    ctx = ctx_for(hjemmeside="fjellbekk.ee", mobil="+372 5069999")
    run(f, ctx)
    phones = sorted(c.value for c in ctx.claims if c.field == "site_contact_phone")
    assert phones == ["+3725069999"]  # the registry number; the other line is not in a block naming us
    assert W.norm_phone("tel:004741000001") == "+4741000001"
    assert W.norm_phone("41 00 00 01") == "+4741000001"
    assert W.norm_phone("1234 5678") is None


def test_careers_page_needs_a_jobs_url():
    body = (f'<footer>Org.nr {OURS}</footer><a href="/tjenester/">Jobb hos oss</a>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body))})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert not ctx.claims_for("hiring_signal")
    body2 = (f'<footer>Org.nr {OURS}</footer><a href="/om-oss/ledige-stillinger/">Bli med</a>'
             '<a href="https://annet.teamtailor.com/jobs">Jobs</a>')
    f2 = FakeFetcher({"https://fjellbekk.no/": (200, html(body2)),
                      "https://fjellbekk.no/om-oss/ledige-stillinger/": (200, html("<h1>Ledige stillinger</h1>"))})
    ctx2 = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(f2, ctx2)
    assert claims["hiring_signal"].value == "https://fjellbekk.no/om-oss/ledige-stillinger/"


def test_contact_page_fetched_after_proof():
    home = html(f'<footer>Org.nr {OURS}</footer><a href="/kontakt">Kontakt</a>')
    kontakt = html('<p>Skriv til <a href="mailto:post@fjellbekk.no">post@fjellbekk.no</a></p>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, home), "https://fjellbekk.no/kontakt": (200, kontakt)})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(f, ctx)
    assert "https://fjellbekk.no/kontakt" in f.calls
    assert claims["site_contact_email"].value == "post@fjellbekk.no"


def test_footer_icon_links_for_youtube_and_instagram():
    body = (f'<main><a href="https://www.instagram.com/kafe_solsikke/">Kafe Solsikke</a></main>'
            f'<footer>Org.nr {OURS}<a href="https://www.youtube.com/channel/UCabc123xyz"><svg></svg></a>'
            f'<a href="https://www.tiktok.com/@lyd_og_lys" aria-label="TikTok"></a></footer>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body))})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    urls = sorted(str(c.value) for c in ctx.claims if c.field == "social_profile")
    assert urls == ["tiktok.com/@lyd_og_lys", "youtube.com/channel/ucabc123xyz"]


def test_news_items_with_literal_dates_only():
    home = html(f'<footer>Org.nr {OURS}</footer><a href="/nyheter">Nyheter</a>')
    listing = html(
        '<article><h3><a href="/nyheter/ny-hall">Vi åpner ny hall i Testby</a></h3>'
        '<time datetime="2026-01-08">8. januar 2026</time></article>'
        '<article><h3><a href="/nyheter/nye-kjokken">Nye kjøkkenmodeller er her</a></h3>'
        '<p>Publisert 20.08.2026</p></article>'
        '<article><h3><a href="/nyheter/udatert">Et innlegg uten dato i det hele tatt</a></h3></article>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, home), "https://fjellbekk.no/nyheter": (200, listing)})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    news = [c for c in ctx.claims if c.field == "dated_news"]
    assert [c.value for c in news] == ["Nye kjøkkenmodeller er her (2026-08-20)", "Vi åpner ny hall i Testby (2026-01-08)"]
    assert all(c.section == "activity" for c in news)


def test_jobs_url_tokens():
    assert W.jobs_url("/stilling/")
    assert W.jobs_url("/om-oss/ledige-stillinger/")
    assert W.jobs_url("/vacancies/")
    assert not W.jobs_url("/nyheter/nytt-samarbeid-gir-20-000-kvm-utstillingsareal-pa-sjoen/")
    assert not W.jobs_url("/bjerke-ror/")


def test_fax_number_is_not_a_contact_phone():
    body = (f"<footer>Org.nr {OURS} Tlf: 74 00 00 01 Faks: 74 00 00 02</footer>")
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body))})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert [c.value for c in ctx.claims if c.field == "site_contact_phone"] == ["+4774000001"]


def test_news_ignores_event_dates_in_sentences_and_category_links():
    home = html(f'<footer>Org.nr {OURS}</footer><a href="/nyheter">Nyheter</a>')
    listing = html(
        '<div class="post"><h3><a href="/nyheter/festival">Bidrag til utstillingen?</a></h3>'
        '<p>Har du noe du vil stille ut under festivalen 25.-28. september 2025?</p></div>'
        '<div class="post"><h3><a href="/nyheter/ny-modell">Ny modell av kjøkkenbenken</a></h3>'
        '<p><a href="/blog/tips">Tips og Guider</a> <span>2026-05-11</span></p></div>'
        '<div class="post"><h3><a href="/nyheter/andre">Andre nyhet om verkstedet</a></h3>'
        '<p><a href="/blog/tips">Tips og Guider</a> <span>2025-08-11</span></p></div>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, home), "https://fjellbekk.no/nyheter": (200, listing)})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    news = [c.value for c in ctx.claims if c.field == "dated_news"]
    assert news == ["Ny modell av kjøkkenbenken (2026-05-11)", "Andre nyhet om verkstedet (2025-08-11)"]


# ---------------------------------------------------------------- benchmark naming work
def test_name_variants_and_domain_guesses():
    reg = {"forretningsadresse": {"kommune": "LINDESNES", "poststed": "TESTBY"}}
    assert ["fjellbekk", "maskin"] in W.name_variants("L.A FJELLBEKK MASKIN AS", reg)
    assert "pe-fjellbekk.no" in W.name_domains("P.E. FJELLBEKK AS", reg)
    assert "sjo-kvist.no" in W.name_domains("SJØ-KVIST TESTBY AS", reg)
    assert "fjellbekknett.no" in W.name_domains("FJELLBEKKNETT VIKEN AS", reg)
    assert "st-kvist.no" in W.name_domains("SAMEIET ST KVIST", reg)
    kids = W.name_domains("STIFTELSEN FJELLBEKK", {"naeringskode1": {"kode": "85.100"}})
    assert kids[0] == "fjellbekk.barnehage.no"
    assert W.name_domains("OSLO AS", reg) == []


def test_registry_website_with_initials_dropped_name():
    body = "<h1>Fjellbekk Maskin</h1><p>Maskinentreprenør i Testby</p>"
    f = FakeFetcher({"https://fjellbekkmaskin.no/": (200, html(body))})
    claims = run(f, ctx_for(navn="L.A FJELLBEKK MASKIN AS", hjemmeside="fjellbekkmaskin.no"))
    assert claims["official_website"].match["method"] == "registry_website_and_name"


def test_variant_that_is_another_registered_company_is_rejected():
    """'Nordre Viken Forsikring AS' must not be proven by a site that shows 'Viken Forsikring'."""
    body = "<h1>Viken Forsikring</h1><p>Viken Forsikring tilbyr skadeforsikring.</p>"
    brreg = json.dumps({"_embedded": {"enheter": [{"organisasjonsnummer": SISTER, "navn": "VIKEN FORSIKRING AS"}]}})
    f = FakeFetcher({"https://vikenforsikring.no/": (200, html(body)),
                     W.BRREG_SEARCH + "?navn=viken%20forsikring&size=20": (200, brreg, "application/json")})
    ctx = ctx_for(navn="NORDRE VIKEN FORSIKRING AS", hjemmeside="vikenforsikring.no")
    run(f, ctx)
    assert not ctx.claims_for("official_website")


def test_single_common_noun_variant_is_not_proof():
    body = "<h1>Forsikring</h1><p>Vi selger forsikring.</p>"
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body))})
    ctx = ctx_for(navn="NORDRE VIKEN FORSIKRING AS", hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert not ctx.claims_for("official_website")


def test_registry_email_on_site_is_strong_with_name():
    body = '<h1>Fjellbekk Trevare</h1><footer>Skriv til <a href="mailto:Ola@Fjellbekk-post.no">oss</a></footer>'
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    claims = run(f, ctx_for(epostadresse="ola@fjellbekk-post.no"))
    assert claims["official_website"].match["method"] == "email_and_name"


def test_place_word_is_not_a_social_handle_key():
    body = (f'<footer>Org.nr {OURS} <a href="https://www.facebook.com/fjellbekktrevare">fb</a>'
            '<a href="https://www.facebook.com/testbymaritime">Testby Maritime</a></footer>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body))})
    ctx = ctx_for(navn="FJELLBEKK TREVARE MANDAL AS", hjemmeside="fjellbekk.no")
    W.REGIONS.add("testby")
    try:
        run(f, ctx)
    finally:
        W.REGIONS.discard("testby")
    assert [str(c.value) for c in ctx.claims if c.field == "social_profile"] == ["facebook.com/fjellbekktrevare"]


def test_dns_timeout_is_retried_not_treated_as_missing(monkeypatch):
    import dns.exception
    calls = {"n": 0}

    async def flaky(host, rtype, lifetime=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise dns.exception.Timeout()
        return [1]
    monkeypatch.setattr(W.dns.asyncresolver, "resolve", flaky)
    f = FakeFetcher({})
    W._DNS_SEM = None
    assert asyncio.run(_real_resolves(f, "x.no")) is True
    assert calls["n"] == 2


# ---------------------------------------------------------------- round 3 audit fixes
def _brreg(path, query, rows):
    return {f"https://data.brreg.no/enhetsregisteret/api/{path}?navn={query}&size=20":
            (200, json.dumps({"_embedded": {path: rows}}), "application/json")}


def test_owners_association_needs_orgnr_not_address():
    """Round 3 Bardøla shape: a sameie at the same address as the hotel company whose name the site carries."""
    body = "<h1>Fjellbekk Trevare</h1><footer>Fjellbekk Trevare, Lyngveien 12B, 9999 Testby</footer>"
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    ctx = ctx_for(navn="SAMEIET FJELLBEKK TREVARE", organisasjonsform={"kode": "SAM"})
    run(f, ctx)
    assert not ctx.claims_for("official_website")


def test_name_collision_with_another_active_entity_rejects_non_orgnr_proof():
    body = "<h1>Fjellbekk Trevare</h1><footer>Fjellbekk Trevare · Tlf 41 00 00 01</footer>"
    pages = {"https://fjellbekktrevare.no/": (200, html(body))}
    pages.update(_brreg("enheter", "fjellbekk%20trevare", [
        {"organisasjonsnummer": SISTER, "navn": "FJELLBEKK TREVARE SA"},
        {"organisasjonsnummer": "999000053", "navn": "FJELLBEKK TREVARE GAMLE AS", "slettedato": "2020-01-01"}]))
    f = FakeFetcher(pages)
    ctx = ctx_for(telefon="41000001")
    run(f, ctx)
    assert not ctx.claims_for("official_website")
    assert SISTER in ctx.facts["website_candidates"][0]["other_orgnrs"]


def test_longer_namesake_collides_only_when_the_site_shows_its_name():
    shown = "<h1>Fjellbekk Trevare Hotell</h1><footer>Fjellbekk Trevare · Tlf 41 00 00 01</footer>"
    plain = "<h1>Fjellbekk Trevare</h1><footer>Fjellbekk Trevare · Tlf 41 00 00 01</footer>"
    rows = [{"organisasjonsnummer": SISTER, "navn": "FJELLBEKK TREVARE HOTELL AS"}]
    for body, proven in ((shown, False), (plain, True)):
        W._NAME_SEARCH.clear()
        pages = {"https://fjellbekktrevare.no/": (200, html(body))}
        pages.update(_brreg("enheter", "fjellbekk%20trevare", rows))
        ctx = ctx_for(telefon="41000001")
        run(FakeFetcher(pages), ctx)
        assert bool(ctx.claims_for("official_website")) is proven


def test_name_collision_passes_when_site_names_our_full_legal_name_only():
    body = "<h1>Fjellbekk Trevare</h1><footer>Fjellbekk Trevare AS · Tlf 41 00 00 01</footer>"
    pages = {"https://fjellbekktrevare.no/": (200, html(body))}
    pages.update(_brreg("enheter", "fjellbekk%20trevare", [
        {"organisasjonsnummer": SISTER, "navn": "FJELLBEKK TREVARE HOTELL AS"}]))
    f = FakeFetcher(pages)
    claims = run(f, ctx_for(telefon="41000001"))
    assert claims["official_website"].match["method"] == "phone_and_name"


def test_workplace_of_another_company_is_a_collision():
    body = "<h1>Fjellbekk Trevare</h1><footer>Tlf 41 00 00 01</footer>"
    pages = {"https://fjellbekktrevare.no/": (200, html(body))}
    pages.update(_brreg("underenheter", "fjellbekk%20trevare", [
        {"organisasjonsnummer": "999000061", "navn": "FJELLBEKK TREVARE", "overordnetEnhet": SISTER}]))
    f = FakeFetcher(pages)
    ctx = ctx_for(telefon="41000001")
    run(f, ctx)
    assert not ctx.claims_for("official_website")


def test_placeholder_description_and_template_activity_are_dropped():
    """Round 3 Galleri Hanna shape: theme placeholder text and lorem-ipsum demo posts."""
    head = '<meta name="description" content="Et galleri. Legg inn en kort og fengende beskrivelse av galleriet.">'
    home = html(f"<p>Tittel 1 Lorem ipsum dolor sit amet</p><footer>Org.nr {OURS}</footer>", head=head)
    sm = ('<?xml version="1.0"?><urlset><url><loc>https://fjellbekk.no/2018/10/mal-for-2019/</loc>'
          '<lastmod>2019-01-02</lastmod></url><url><loc>https://fjellbekk.no/hello-world/</loc>'
          '<lastmod>2019-01-03</lastmod></url></urlset>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, home), "https://fjellbekk.no/sitemap.xml": (200, sm, "application/xml")})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(f, ctx)
    assert claims["official_website"].availability == "available"
    assert "website_description" not in claims or claims["website_description"].availability != "available"
    assert claims["site_page_updated"].availability == "ambiguous"


def test_activity_before_the_company_existed_is_dropped():
    """Round 3 Coffteck shape: a 2016 site inherited by a company founded in 2023."""
    sm = ('<?xml version="1.0"?><urlset>'
          '<url><loc>https://fjellbekk.no/kontakt-oss/</loc><lastmod>2016-04-15</lastmod></url>'
          '<url><loc>https://fjellbekk.no/nytt/</loc><lastmod>2024-02-01</lastmod></url></urlset>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(f"<footer>MVA {OURS}</footer>")),
                     "https://fjellbekk.no/sitemap.xml": (200, sm, "application/xml")})
    ctx = ctx_for(hjemmeside="fjellbekk.no", stiftelsesdato="2023-10-26", registreringsdatoEnhetsregisteret="2023-11-01")
    run(f, ctx)
    assert [c.as_of for c in ctx.claims if c.field == "site_page_updated" and c.availability == "available"] == ["2024-02-01"]


def test_redirect_to_brand_site_proven_by_address_and_phone_keeps_derived_facts():
    """Round 3 Fablab shape: .no redirects to the company's own .com, whose footer has the registry address and phone."""
    front = html('<footer>Fjellbekk Trevare AS, Lyngveien 12B, 9999 Testby, +47 410 00 001 '
                 '<a href="mailto:post@fjellbekk.com">post@fjellbekk.com</a>'
                 '<a href="https://www.instagram.com/fjellbekktrevare/">ig</a></footer>')
    f = FakeFetcher({"https://fjellbekk.com/": (200, front)}, redirects={"https://fjellbekk.no/": "https://fjellbekk.com/"})
    ctx = ctx_for(hjemmeside="fjellbekk.no", mobil="41000001")
    claims = run(f, ctx)
    assert claims["official_website"].value == "fjellbekk.com"
    assert claims["site_contact_email"].value == "post@fjellbekk.com"
    assert claims["social_profile"].value == "instagram.com/fjellbekktrevare"
    assert ctx.facts["website_exclusive"] is False  # the gate's brand flag still needs org-number proof


def test_registry_declared_site_with_namesake_keeps_website_but_derives_nothing():
    body = '<h1>Fjellbekk Trevare</h1><footer><a href="mailto:post@fjellbekk.no">post@fjellbekk.no</a></footer>'
    pages = {"https://fjellbekk.no/": (200, html(body))}
    pages.update(_brreg("enheter", "fjellbekk%20trevare", [{"organisasjonsnummer": SISTER, "navn": "FJELLBEKK TREVARE AS"}]))
    ctx = ctx_for(navn="FJELLBEKK TREVARE ASA", hjemmeside="fjellbekk.no")
    claims = run(FakeFetcher(pages), ctx)
    assert claims["official_website"].availability == "available"
    assert SISTER in claims["official_website"].match["detail"]
    assert claims["site_contact_email"].availability == "ambiguous"


def test_redirect_to_portal_without_our_name_in_domain_stays_not_exclusive():
    """Round 2 Nidaros shape seen again in round 3 testing: the portal home shows our phone and address."""
    front = html('<footer>Fjellbekk Trevare AS, Lyngveien 12B, 9999 Testby, +47 410 00 001 '
                 '<a href="mailto:post@skolene.no">post@skolene.no</a></footer>')
    f = FakeFetcher({"https://skolene.no/": (200, front)}, redirects={"https://fjellbekk.no/": "https://skolene.no/"})
    ctx = ctx_for(hjemmeside="fjellbekk.no", mobil="41000001")
    claims = run(f, ctx)
    assert claims["official_website"].value == "https://skolene.no/"
    assert claims["site_contact_email"].availability == "ambiguous"


# ---------------------------------------------------------------- round 3 follow-up decisions
def test_near_namesake_sharing_our_address_rejects_non_orgnr_proof():
    """Advisor shape: ADVISORS AS shares the address of ADVISOR AS."""
    body = "<h1>Fjellbekk</h1><footer>Fjellbekk · Lyngveien 12B, 9999 Testby</footer>"
    pages = {"https://fjellbekk.com/": (200, html(body))}
    pages["https://data.brreg.no/enhetsregisteret/api/enheter?navn=fjellbekk&forretningsadresse.postnummer=9999&size=50"] = (
        200, json.dumps({"_embedded": {"enheter": [{"organisasjonsnummer": SISTER, "navn": "FJELLBEKKS AS",
                         "forretningsadresse": {"adresse": ["Lyngveien 12B"], "postnummer": "9999"}}]}}),
        "application/json")
    ctx = ctx_for(navn="FJELLBEKK AS")
    run(FakeFetcher(pages), ctx)
    assert not ctx.claims_for("official_website")


def _kvist(row_extra=None):
    row = {"organisasjonsnummer": SISTER, "navn": "KVIST MAISON AS"}
    row.update(row_extra or {})
    return W.BRREG_SEARCH + "?navn=kvist%20maison&size=20", (200, json.dumps({"_embedded": {"enheter": [row]}}),
                                                               "application/json")


def test_company_named_in_body_text_does_not_count():
    """A client or partner named in body content leaves the site exclusive."""
    body = (f'<main><p>Vi leverte nytt kjøkken til Kvist Maison AS i 2025.</p></main>'
            f'<footer>Fjellbekk Trevare AS · Org.nr {OURS} <a href="mailto:post@fjellbekk.no">x</a></footer>')
    url, resp = _kvist()
    claims = run(FakeFetcher({"https://fjellbekk.no/": (200, html(body)), url: resp}), ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["site_contact_email"].availability == "available"


def test_company_named_in_footer_makes_site_not_exclusive():
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS} · Kvist Maison AS '
            f'<a href="mailto:post@fjellbekk.no">x</a></footer>')
    url, resp = _kvist()
    claims = run(FakeFetcher({"https://fjellbekk.no/": (200, html(body)), url: resp}), ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["site_contact_email"].availability == "ambiguous"


def test_company_in_body_tied_to_our_address_makes_site_not_exclusive():
    body = (f'<main><p>Besøk også Kvist Maison AS i Storgata.</p></main>'
            f'<footer>Fjellbekk Trevare AS · Org.nr {OURS} <a href="mailto:post@fjellbekk.no">x</a></footer>')
    url, resp = _kvist({"forretningsadresse": {"adresse": ["Lyngveien 12B"], "postnummer": "9999"}})
    claims = run(FakeFetcher({"https://fjellbekk.no/": (200, html(body)), url: resp}), ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["site_contact_email"].availability == "ambiguous"


def test_phone_in_a_content_block_is_not_proof():
    """Starina shape: the registry phone appears in a menu text block, not a named contact block."""
    body = ("<h1>Fjellbekk Trevare</h1><section><p>Lunsjservice: ring 41 00 00 01 for bestilling</p></section>"
            "<footer>Velkommen innom</footer>")
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    ctx = ctx_for(telefon="41000001")
    run(f, ctx)
    assert not ctx.claims_for("official_website")


def test_contact_address_on_another_organisations_domain_makes_site_not_exclusive():
    """Banh Mi XO shape: the second venue is listed with its own company's email domain."""
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS} <a href="mailto:post@fjellbekk.no">post@fjellbekk.no</a>'
            '<p>Kvist (Storgata) <a href="mailto:post@kvistmaison.no">post@kvistmaison.no</a></p></footer>')
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body))})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(f, ctx)
    assert claims["official_website"].availability == "available"
    assert claims["site_contact_email"].availability == "ambiguous"
    assert "kvistmaison.no" in claims["official_website"].match["detail"]


# ---------------------------------------------------------------- reported families (Builderr's shapes)
def _site(extra_pages=None, body=None, head=""):
    pages = {"https://fjellbekk.no/": (200, html(body or f"<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>", head=head))}
    pages.update(extra_pages or {})
    return FakeFetcher(pages)


def test_dated_news_from_rss_keeps_source_datetime_with_offset():
    head = '<link rel="alternate" type="application/rss+xml" href="/feed/">'
    rss = ('<?xml version="1.0"?><rss><channel><title>Fjellbekk</title>'
           '<item><title>Bedre kjøkken etter ombygging</title><link>https://fjellbekk.no/2025/09/bedre-kjokken/</link>'
           '<pubDate>Mon, 22 Sep 2025 20:00:00 +0200</pubDate></item>'
           '<item><title>Hello world!</title><link>https://fjellbekk.no/hello-world/</link>'
           '<pubDate>Tue, 01 Jan 2019 10:00:00 +0000</pubDate></item>'
           '<item><title>Fra fremtiden</title><link>https://fjellbekk.no/2099/x/</link>'
           '<pubDate>Tue, 01 Jan 2099 10:00:00 +0000</pubDate></item></channel></rss>')
    f = _site({"https://fjellbekk.no/feed/": (200, rss, "application/rss+xml")}, head=head)
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    news = [c for c in ctx.claims if c.field == "dated_news"]
    assert [c.value for c in news] == ["Bedre kjøkken etter ombygging (2025-09-22T20:00:00+02:00)"]
    spans = {e.claim_span for e in ctx.evidence if e.id in news[0].evidence_ids}
    assert "<pubDate>Mon, 22 Sep 2025 20:00:00 +0200</pubDate>" in spans


def test_dated_news_from_atom_and_wordpress_feed_probe():
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Ny hall</title>'
            '<link rel="alternate" href="https://fjellbekk.no/ny-hall/"/><published>2026-03-01T09:30:00+01:00</published>'
            '</entry></feed>')
    f = _site({"https://fjellbekk.no/feed/": (200, atom, "application/atom+xml")},
              body=f'<link href="/wp-content/x.css"><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert [c.value for c in ctx.claims if c.field == "dated_news"] == ["Ny hall (2026-03-01T09:30:00+01:00)"]


def test_dated_news_listing_page_two_and_article_datetime():
    body = f'<a href="/nyheter/">Nyheter</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    page1 = html('<article><h3><a href="/nyheter/a">Første sak om verkstedet</a></h3><time>2026-02-01</time></article>'
                 '<a rel="next" href="/nyheter/page/2/">Neste</a>')
    page2 = html('<article><h3><a href="/nyheter/b">Andre sak om verkstedet</a></h3><span>2025-11-03</span></article>')
    art = html('<h1>Første sak</h1>', head='<meta property="article:published_time" content="2026-02-01T08:15:00+01:00">')
    f = _site({"https://fjellbekk.no/nyheter/": (200, page1), "https://fjellbekk.no/nyheter/page/2/": (200, page2),
               "https://fjellbekk.no/nyheter/a": (200, art)}, body=body)
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert [c.value for c in ctx.claims if c.field == "dated_news"] == [
        "Første sak om verkstedet (2026-02-01T08:15:00+01:00)", "Andre sak om verkstedet (2025-11-03)"]


def test_dated_news_never_before_the_company_existed():
    head = '<link rel="alternate" type="application/rss+xml" href="/feed/">'
    rss = ('<rss><channel><item><title>Gammel nyhet fra forgjengeren</title><link>https://fjellbekk.no/gammel/</link>'
           '<pubDate>Fri, 15 Apr 2016 10:00:00 +0200</pubDate></item></channel></rss>')
    f = _site({"https://fjellbekk.no/feed/": (200, rss, "application/rss+xml")}, head=head)
    ctx = ctx_for(hjemmeside="fjellbekk.no", stiftelsesdato="2023-10-26")
    run(f, ctx)
    assert not [c for c in ctx.claims if c.field == "dated_news" and c.availability == "available"]


def test_hiring_signal_by_probe_with_postings():
    jobs = html('<h1>Karriere</h1>'
                '<script type="application/ld+json">{"@type":"JobPosting","title":"Snekker","url":"/karriere/snekker",'
                '"hiringOrganization":{"@type":"Organization","name":"Fjellbekk Trevare AS"}}</script>'
                '<script type="application/ld+json">{"@type":"JobPosting","title":"Kokk","url":"/karriere/kokk",'
                '"hiringOrganization":{"name":"Kvist Hotell AS"}}</script>'
                '<main><a href="https://fjellbekk.webcruiter.no/main/recruit/public/4567">Lærling tømrer 2026</a></main>')
    f = _site({"https://fjellbekk.no/karriere/": (200, jobs)})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    vals = [c.value for c in ctx.claims if c.field == "hiring_signal"]
    assert vals == ["https://fjellbekk.no/karriere/", "https://fjellbekk.no/karriere/snekker",
                    "https://fjellbekk.webcruiter.no/main/recruit/public/4567"]


def test_hiring_signal_rejects_a_careers_link_that_lands_on_home():
    body = f'<a href="/jobb/">Jobb hos oss</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body))}, redirects={"https://fjellbekk.no/jobb/": "https://fjellbekk.no/"})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert not ctx.claims_for("hiring_signal")


def test_socials_from_plugin_iframe_and_data_href_capped_at_two():
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
            '<iframe src="https://www.facebook.com/plugins/page.php?href=https%3A%2F%2Fwww.facebook.com%2FFjellbekkTrevare&tabs"></iframe>'
            '<div class="fb-page" data-href="https://www.facebook.com/fjellbekk.trevare.testby"></div>'
            '<a href="https://www.facebook.com/fjellbekktrevareas">fb</a>'
            '<a href="https://www.facebook.com/profile.php?id=123">fb</a>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=body), ctx)
    vals = sorted(str(c.value) for c in ctx.claims if c.field == "social_profile")
    assert len(vals) == 2 and all(v.startswith("facebook.com/") and v == v.lower() for v in vals)
    assert all(c.value.get("platform") == "facebook" for c in ctx.claims if c.field == "social_profile")


def test_same_inputs_give_identical_claims():
    head = '<link rel="alternate" type="application/rss+xml" href="/feed/">'
    rss = ('<rss><channel><item><title>A</title><link>https://fjellbekk.no/a/</link><pubDate>Mon, 22 Sep 2025 20:00:00 +0200'
           '</pubDate></item><item><title>B</title><link>https://fjellbekk.no/b/</link><pubDate>Mon, 22 Sep 2025 20:00:00 +0200'
           '</pubDate></item></channel></rss>')
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS} <a href="https://www.instagram.com/fjellbekk/">i</a>'
            '<a href="https://www.linkedin.com/company/fjellbekk/">l</a></footer>')
    outs = []
    for _ in range(2):
        W._NAME_SEARCH.clear()
        ctx = ctx_for(hjemmeside="fjellbekk.no")
        run(_site({"https://fjellbekk.no/feed/": (200, rss, "application/rss+xml")}, body=body, head=head), ctx)
        outs.append([(c.field, str(c.value), c.availability) for c in ctx.claims])
    assert outs[0] == outs[1]


def test_hiring_ignores_vacant_premises_and_probed_pages_without_jobs_heading():
    assert not W.jobs_url("/vare-eiendommer/ledige-lokaler")
    body = f'<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    f = _site({"https://fjellbekk.no/karriere/": (200, html("<h1>Velkommen til Fjellbekk</h1>", title="Forside"))}, body=body)
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert not ctx.claims_for("hiring_signal")


def test_spam_feed_and_sample_posts_are_dropped():
    head = '<link rel="alternate" type="application/rss+xml" href="/feed/">'
    rss = ('<rss><channel><item><title>Cheap replica Birkin bags here</title><link>https://fjellbekk.no/a/</link>'
           '<pubDate>Mon, 22 Sep 2025 20:00:00 +0200</pubDate></item><item><title>Ny hall</title>'
           '<link>https://fjellbekk.no/b/</link><pubDate>Mon, 22 Sep 2025 20:00:00 +0200</pubDate></item></channel></rss>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site({"https://fjellbekk.no/feed/": (200, rss, "application/rss+xml")}, head=head), ctx)
    assert not ctx.claims_for("dated_news")
    assert W.SAMPLE_POST.match("Hei, verden!")


# ---------------------------------------------------------------- round 4 audit (Elpro, Biri)
def _hjem(host, rows):
    return {f"https://data.brreg.no/enhetsregisteret/api/enheter?hjemmeside={host}&size=20":
            (200, json.dumps({"_embedded": {"enheter": rows}}), "application/json")}


def test_website_declared_by_several_registered_companies_is_a_group_site():
    """Elpro shape: four companies declare www.elpro.no; we are one of them and the site shows no org number."""
    body = '<h1>Fjellbekk Trevare</h1><footer><a href="mailto:post@fjellbekk.no">x</a></footer>'
    pages = {"https://fjellbekk.no/": (200, html(body))}
    pages.update(_hjem("fjellbekk.no", [
        {"organisasjonsnummer": OURS, "navn": "FJELLBEKK TREVARE AS", "hjemmeside": "www.fjellbekk.no"},
        {"organisasjonsnummer": SISTER, "navn": "FJELLBEKK GROUP AS", "hjemmeside": "www.fjellbekk.no"},
        {"organisasjonsnummer": "999000053", "navn": "SFJELLBEKK AS", "hjemmeside": "www.sfjellbekk.no"}]))
    ctx = ctx_for(hjemmeside="www.fjellbekk.no")
    claims = run(FakeFetcher(pages), ctx)
    assert claims["official_website"].availability == "available"
    assert claims["official_website"].value == "fjellbekk.no"
    assert SISTER in claims["official_website"].match["detail"]
    assert claims["site_contact_email"].availability == "ambiguous"


def test_privacy_page_controller_naming_another_company_makes_site_not_exclusive():
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer><a href="/personvern/">Personvern</a>'
            '<a href="mailto:post@fjellbekk.no">x</a>')
    priv = html(f"<p>Kvist Gruppen AS (org.nr {OTHER}) er behandlingsansvarlig for dette nettstedet.</p>")
    f = _site({"https://fjellbekk.no/personvern/": (200, priv)}, body=body)
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(f, ctx)
    assert "https://fjellbekk.no/personvern/" in f.calls
    assert claims["site_contact_email"].availability == "ambiguous"


def test_privacy_page_processors_do_not_count():
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer><a href="/personvern/">Personvern</a>'
            '<a href="mailto:post@fjellbekk.no">x</a>')
    priv = html("<p>Vi bruker Google Ireland Limited og Kvist Analyse AS som databehandlere.</p>")
    claims = run(_site({"https://fjellbekk.no/personvern/": (200, priv)}, body=body), ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["site_contact_email"].value == "post@fjellbekk.no"


def test_social_handle_naming_the_group_is_dropped():
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS}'
            '<a href="https://www.instagram.com/fjellbekkgroupas/">ig</a>'
            '<a href="https://www.facebook.com/fjellbekktrevare/">fb</a></footer>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=body), ctx)
    assert [str(c.value) for c in ctx.claims if c.field == "social_profile"] == ["facebook.com/fjellbekktrevare"]


def test_biri_shape_free_mail_address_phone_and_people_profile():
    """Biri Fotklinikk shape: gmail box named after the company, phone beside the registered address,
    Facebook 'people' profile URL."""
    body = (f'<p>Org.nr: {OURS}</p>'
            '<footer><p class="footertext">Klinikken Lyngveien 12B, 9999 Testby Kontakt Telefon: '
            '<a href="tel:+4741000001">+47 410 00 001</a> E-post: '
            '<a href="mailto:fjellbekktrevare@gmail.com">fjellbekktrevare@gmail.com</a></p>'
            '<ul class="socialmedia"><li><a href="https://www.facebook.com/people/Fjellbekk-Trevare-As/100083089938875/">'
            '</a></li></ul></footer>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(_site(body=body), ctx)
    assert claims["site_contact_email"].value == "fjellbekktrevare@gmail.com"
    assert claims["site_contact_phone"].value == "+4741000001"
    assert claims["social_profile"].value == "facebook.com/people/fjellbekk-trevare-as/100083089938875"
