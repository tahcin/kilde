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
        self.brreg_down = False  # True: every Brreg name search fails (network trouble)
        self.known_hosts = {W.bare(urlsplit(u).hostname or "") for u in list(pages) + list(self.redirects)} | {
            W.bare(urlsplit(u).hostname or "") for u in list(self.redirects.values()) + list(self.disallow)}

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
        search = final.startswith(("https://data.brreg.no/enhetsregisteret/api/enheter?",
                                   "https://data.brreg.no/enhetsregisteret/api/underenheter?"))
        if search and self.brreg_down:
            raise ConnectionError("registry search unavailable")
        if hit is None and search:
            hit = (200, json.dumps({"page": {"totalPages": 0}}), "application/json")  # nobody else registered
        if hit is None:
            raise ConnectionError("no such host")
        status, body = hit[0], hit[1]
        ctype = hit[2] if len(hit) > 2 else "text/html; charset=utf-8"
        return Snapshot(url=url, final_url=final, status=status, retrieved_at=utc_now(), content=body.encode(),
                        content_type=ctype)


@pytest.fixture(autouse=True)
def offline_dns(monkeypatch):
    async def yes(*a, **k):
        f = a[0] if a else None
        host = a[1] if len(a) > 1 else ""
        known = getattr(f, "known_hosts", None)
        if known is None or not host:
            return True
        return W.bare(host) in known  # like DNS: only hosts the fake web actually serves resolve

    async def tls_only(host, port=443):
        return port == 443
    monkeypatch.setattr(W, "resolves", yes)
    monkeypatch.setattr(W, "reachable", tls_only)
    W._SISTER_CACHE.clear()
    W._NAME_SEARCH.clear()
    W._SEARCH.clear()
    W._ROW_CACHE.clear()
    W._HJEM_CACHE.clear()
    W._GROUP_CACHE.clear()
    W._GROUP_TREE.clear()


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
    # a domain, URL or email typed where the handle belongs is a link to the site, not a profile
    ("https://www.facebook.com/www.fjellbekk.no/?locale=nb_NO", None),
    ("https://www.facebook.com/fjellbekk.com", None),
    ("https://www.facebook.com/fjellbekk.no", ("facebook", "https://www.facebook.com/fjellbekk.no")),
    ("https://www.facebook.com/https://fjellbekk.no", None),
    ("https://www.instagram.com/post@fjellbekk.no", None),
    ("https://www.instagram.com/www.fjellbekk.no/", None),
    ("https://www.instagram.com/fjellbekk.no/", ("instagram", "https://www.instagram.com/fjellbekk.no")),
    ("https://www.linkedin.com/company/fjellbekk.com/", None),
    ("https://www.youtube.com/@www.fjellbekk.no", None),
    ("https://www.pinterest.com/fjellbekk.no/", None),
    ("https://x.com/www.fjellbekk.no", None),
    ("https://www.instagram.com/fjell.bekk/", ("instagram", "https://www.instagram.com/fjell.bekk")),
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


def test_social_path_is_link():
    assert W.social_path_is_link("facebook.com/www.fjellbekk.no")
    assert W.social_path_is_link("https://www.facebook.com/fjellbekk.com/")
    assert W.social_path_is_link("https://x.com/fjellbekk.no")
    assert not W.social_path_is_link("https://www.facebook.com/fjellbekk.no")
    assert not W.social_path_is_link("https://www.facebook.com/fjellbekk")
    assert not W.social_path_is_link("https://www.instagram.com/fjell.bekk/")


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
    assert urls == ["facebook.com/profile.php?id=10001234", "instagram.com/fjellbekk.no"]
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
    site = claims["related_website"]
    assert claims["official_website"].availability == "ambiguous"
    assert site.availability == "available" and SISTER in site.match["detail"]
    avail = {c.field for c in ctx.claims if c.availability == "available"}
    assert avail == {"related_website"}
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
    site = claims["related_website"]
    assert claims["official_website"].availability == "ambiguous"
    assert site.value["url"] == "https://www.rorkjeden.no/finn-rorlegger/testby/lyngstad-ror-as"
    assert site.match["method"] == "phone_and_name"
    assert {c.field for c in ctx.claims if c.availability == "available"} == {"related_website"}
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
    assert claims["related_website"].value["url"] == "https://kjede.no/medlemmer/testby/fjellbekk"
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
    assert claims["related_website"].availability == "available"
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
    assert claims["related_website"].availability == "available"
    assert "kvist automobil" in claims["related_website"].match["detail"]
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
    assert urls == ["youtube.com/channel/ucabc123xyz"]  # an unnamed handle is not published, a numeric id is


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
    return {f"https://data.brreg.no/enhetsregisteret/api/{path}?navn={query}&size=50":
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
        W._SEARCH.clear()
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
    assert claims["related_website"].availability == "available"
    assert SISTER in claims["related_website"].match["detail"]
    assert claims["site_contact_email"].availability == "ambiguous"


def test_redirect_to_portal_without_our_name_in_domain_stays_not_exclusive():
    """Round 2 Nidaros shape seen again in round 3 testing: the portal home shows our phone and address."""
    front = html('<footer>Fjellbekk Trevare AS, Lyngveien 12B, 9999 Testby, +47 410 00 001 '
                 '<a href="mailto:post@skolene.no">post@skolene.no</a></footer>')
    f = FakeFetcher({"https://skolene.no/": (200, front)}, redirects={"https://fjellbekk.no/": "https://skolene.no/"})
    ctx = ctx_for(hjemmeside="fjellbekk.no", mobil="41000001")
    claims = run(f, ctx)
    assert claims["related_website"].value["url"] == "https://skolene.no/"
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
    # our own org number in the footer keeps the site ours; the other venue's contact only stops derived facts
    assert claims["official_website"].value == "fjellbekk.no"
    assert claims["site_contact_email"].availability == "ambiguous"
    assert "kvistmaison.no" in claims["official_website"].match["detail"]
    # without our org number printed, the same page is a related website
    f2 = FakeFetcher({"https://fjellbekk.no/": (200, html(body.replace(f"Org.nr {OURS}", "")))})
    claims2 = run(f2, ctx_for(hjemmeside="fjellbekk.no"))
    assert claims2["related_website"].availability == "available"


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
        W._SEARCH.clear()
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
    assert claims["related_website"].availability == "available"
    assert claims["related_website"].value["domain"] == "fjellbekk.no"
    assert SISTER in claims["related_website"].match["detail"]
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


# ---------------------------------------------------------------- outages are failures, not absence
def _mock_dns(monkeypatch, exc_for):
    import dns.resolver

    async def fake(host, rtype, lifetime=None):
        exc = exc_for(host)
        if exc is None:
            return [1]
        raise exc
    monkeypatch.setattr(W.dns.asyncresolver, "resolve", fake)
    monkeypatch.setattr(W, "resolves", _real_resolves)
    W._DNS_SEM = None
    W._CANARY.clear()


def test_nxdomain_is_absence(monkeypatch):
    import dns.resolver
    _mock_dns(monkeypatch, lambda h: None if h == W.CANARY_HOST else dns.resolver.NXDOMAIN())
    ctx = ctx_for(navn="FJELLBEKK TREVARE AS")
    claims = run(FakeFetcher({}), ctx)
    assert claims["official_website"].availability == "not_available"


@pytest.mark.parametrize("make_exc", [lambda: __import__("dns.exception").exception.Timeout(),
                                      lambda: __import__("dns.resolver").resolver.NoNameservers(),
                                      lambda: OSError("network is unreachable")])
def test_resolver_failures_are_failed_not_absent(monkeypatch, make_exc):
    _mock_dns(monkeypatch, lambda h: make_exc())
    ctx = ctx_for(navn="FJELLBEKK TREVARE AS")
    claims = run(FakeFetcher({}), ctx)
    assert claims["official_website"].availability == "failed"
    assert "network unavailable" in claims["official_website"].note
    assert ctx.module_states["website"] == "timeout"


def test_lookup_failure_on_declared_domain_with_network_up_is_failed(monkeypatch):
    import dns.exception
    _mock_dns(monkeypatch, lambda h: None if h == W.CANARY_HOST else dns.exception.Timeout())
    ctx = ctx_for(navn="FJELLBEKK TREVARE AS", hjemmeside="fjellbekk.no")
    f = FakeFetcher({})
    f.known_hosts = {"fjellbekk.no"}
    claims = run(f, ctx)
    assert claims["official_website"].availability == "failed"
    assert claims["official_website"].note.startswith("the registry-declared domain")


def test_dead_guessed_domain_with_network_up_is_absence(monkeypatch):
    import dns.exception
    _mock_dns(monkeypatch, lambda h: None if h == W.CANARY_HOST else dns.exception.Timeout())
    ctx = ctx_for(navn="FJELLBEKK TREVARE AS")
    claims = run(FakeFetcher({}), ctx)
    assert claims["official_website"].availability == "not_available"


def test_every_candidate_unreachable_after_dns_is_failed(monkeypatch):
    async def down(host, port=443):
        return False
    monkeypatch.setattr(W, "reachable", down)
    ctx = ctx_for(navn="FJELLBEKK TREVARE AS", hjemmeside="fjellbekk.no")
    f = FakeFetcher({})
    f.known_hosts = {"fjellbekk.no"}
    claims = run(f, ctx)
    assert claims["official_website"].availability == "failed"


# ---------------------------------------------------------------- round 5 audit fixes
def test_date_is_never_a_phone():
    """Sameiet Meto shape: '2024.03.06' after 'Telenor' was read as a phone number."""
    assert not W.phone_shaped("2024.03.06") and not W.phone_shaped("06.03.2024") and not W.phone_shaped("2024-03-06")
    assert W.phone_shaped("41 00 00 01") and W.phone_shaped("+47 410 00 001") and W.phone_shaped("41000001")
    body = (f'<p>Orgnummer: {OURS}</p><div><p>Hjelp fra Telenor bredbånd og TV</p><p class="datetime">2024.03.06</p></div>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=body), ctx)
    assert not ctx.claims_for("site_contact_phone")


def test_personal_cv_page_is_not_a_hiring_signal():
    """Flow Studio shape: the owner's own open application linked as 'Jobb med meg'."""
    body = f'<a href="/jobb-med-meg">Jobb med meg</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    cv = html("<h1>Jobb med meg</h1><p>Åpen søknad. Mulige stillinger innen reiseliv. Med vennlig hilsen Kari</p>")
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site({"https://fjellbekk.no/jobb-med-meg": (200, cv)}, body=body), ctx)
    assert not ctx.claims_for("hiring_signal")


def test_facebook_placeholders_rejected_and_profile_id_canonical():
    for u in ("https://www.facebook.com/me", "https://www.facebook.com/sharer/sharer.php?u=x", "https://facebook.com/home",
              "https://www.facebook.com/groups/123", "https://www.facebook.com/pages/Fjellbekk"):
        assert W.clean_social(u) is None, u
    c = W.clean_social("https://www.facebook.com/profile.php?id=61575193973139&ref=x")
    assert str(W.canonical_social(*c)) == "facebook.com/profile.php?id=61575193973139"
    p = W.clean_social("https://no.pinterest.com/fjellbekk/")
    assert str(W.canonical_social(*p)) == "pinterest.com/fjellbekk"


def test_shared_registry_website_with_orgnr_on_privacy_page_and_other_never_named():
    """Grafia shape: another company declares the site in Brreg; our org number is the privacy-page operator."""
    body = '<h1>Fjellbekk</h1><a href="/personvern/">Personvern</a><a href="mailto:post@fjellbekk.no">x</a>'
    priv = html(f"<p>Vi er Fjellbekk Trevare AS med org nr {OURS} og vår nettstedsadresse er https://fjellbekk.no</p>")
    rows = [{"organisasjonsnummer": SISTER, "navn": "FJELLBEKK DESIGN AS", "hjemmeside": "www.fjellbekk.no"}]
    pages = {"https://fjellbekk.no/": (200, html(body)), "https://fjellbekk.no/personvern/": (200, priv)}
    pages.update(_hjem("fjellbekk.no", rows))
    claims = run(FakeFetcher(pages), ctx_for(epostadresse="post@fjellbekk.no"))
    assert claims["site_contact_email"].value == "post@fjellbekk.no"
    # the same site, but the other declarant is named on it: derive nothing
    W._HJEM_CACHE.clear()
    body2 = body + "<footer>Design: Fjellbekk Design AS</footer>"
    pages["https://fjellbekk.no/"] = (200, html(body2))
    claims2 = run(FakeFetcher(pages), ctx_for(epostadresse="post@fjellbekk.no"))
    assert claims2["site_contact_email"].availability == "ambiguous"


def test_wordpress_rest_posts_first_and_feed_page_two():
    wp = json.dumps([{"title": {"rendered": f"Sak nummer {i}"}, "link": f"https://fjellbekk.no/sak-{i}/",
                      "date": f"2026-0{1 + i % 8}-1{i % 9}T12:00:00",
                      "date_gmt": f"2026-0{1 + i % 8}-1{i % 9}T10:00:00"} for i in range(12)])
    body = f'<link href="/wp-content/a.css"><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    f = _site({"https://fjellbekk.no/wp-json/wp/v2/posts?per_page=20&_fields=title,link,date,date_gmt,excerpt":
               (200, wp, "application/json")}, body=body)
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    news = [c for c in ctx.claims if c.field == "dated_news"]
    assert len(news) == 12
    assert all(c.value.endswith("T12:00:00+02:00)") for c in news)  # the site's own local time and offset
    for c in news:  # every span is an exact substring of the saved response
        for e in (e for e in ctx.evidence if e.id in c.evidence_ids):
            assert e.claim_span in ctx.snapshots[e.id].content.decode()


def test_squarespace_blog_collection_feed():
    body = f'<a href="/blog">Blog</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    blog = html('<p>squarespace blog</p>')
    rss = ('<rss><channel><item><title>Finn stødighet</title><link>https://fjellbekk.no/blog/finn</link>'
           '<pubDate>Thu, 06 Nov 2025 09:00:00 +0000</pubDate></item></channel></rss>')
    f = _site({"https://fjellbekk.no/blog": (200, blog), "https://fjellbekk.no/blog?format=rss": (200, rss, "application/rss+xml")},
              body=body)
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert [c.value for c in ctx.claims if c.field == "dated_news"] == ["Finn stødighet (2025-11-06T09:00:00+00:00)"]


def test_front_page_news_without_links():
    """Sameiet Meto shape: board news on the front page as a date line and a heading."""
    body = (f'<p>Orgnummer: {OURS}</p>'
            '<div class="news_description"><p class="datetime">2024.07.04</p><h5>Oppdatert informasjon til beboere</h5>'
            '<p>Tekst</p></div>'
            '<div class="news_description"><p class="datetime">2024.03.06</p><h5>Oppsummering styremøte 05.03.2024</h5></div>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=body), ctx)
    assert [c.value for c in ctx.claims if c.field == "dated_news"] == [
        "Oppdatert informasjon til beboere (2024-07-04)", "Oppsummering styremøte 05.03.2024 (2024-03-06)"]


def test_company_careers_page_inviting_open_applications_is_kept():
    body = f'<a href="/ledige-stillinger">Ledige stillinger</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    jobs = html("<h1>Ledige stillinger</h1><p>Ingen ledige stillinger nå, men send oss gjerne en åpen søknad.</p>")
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site({"https://fjellbekk.no/ledige-stillinger": (200, jobs)}, body=body), ctx)
    assert [c.value for c in ctx.claims if c.field == "hiring_signal"] == ["https://fjellbekk.no/ledige-stillinger"]


def test_open_data_seed_is_a_candidate_with_no_special_trust(monkeypatch):
    monkeypatch.setattr(W, "_SEEDS", {OURS: [{"domain": "kvist-bygg.no", "sources": ["overture_phone"]},
                                             {"domain": "annet.no", "sources": ["osm_orgnr"]}]})
    ctx = ctx_for(navn="FJELLBEKK TREVARE AS")
    cands = W.candidates(ctx)
    assert ("annet.no", "seed:osm_orgnr", None) in cands and cands.index(("annet.no", "seed:osm_orgnr", None)) < 3
    pages = {"https://kvist-bygg.no/": (200, html(f"<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>")),
             "https://annet.no/": (200, html("<h1>Fjellbekk Trevare</h1>"))}
    claims = run(FakeFetcher(pages), ctx)
    assert claims["official_website"].value == "kvist-bygg.no"  # proven by our org number, like any candidate
    ctx2 = ctx_for(navn="FJELLBEKK TREVARE AS")
    monkeypatch.setattr(W, "_SEEDS", {OURS: [{"domain": "annet.no", "sources": ["osm_orgnr"]}]})
    run(FakeFetcher({"https://annet.no/": (200, html("<h1>Fjellbekk Trevare</h1>"))}), ctx2)
    assert not ctx2.claims_for("official_website")  # a name alone never proves a seed


# ---------------------------------------------------------------- round 6 audit fixes
def test_headhunter_client_searches_are_not_a_hiring_signal():
    """Borka shape: a recruiter's list of client searches, all filled, plus a CV form."""
    body = f'<a href="/stillinger">Stillinger</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    jobs = html('<h1>Stillinger</h1><p>Registrer CV. Utvalgte kunder.</p>'
                '<a href="/stillinger/key-account-manager-kvist-pharma">Key Account Manager, Kvist Pharma</a>'
                '<a href="/stillinger/besatte-roller/2024-medical-advisor-kvist">Medical Advisor (besatt)</a>')
    ctx = ctx_for(hjemmeside="fjellbekk.no", naeringskode1={"kode": "70.200"})
    run(_site({"https://fjellbekk.no/stillinger": (200, jobs)}, body=body), ctx)
    assert not ctx.claims_for("hiring_signal")


def test_staffing_firm_needs_own_hiring_wording():
    body = f'<a href="/ledige-stillinger">Ledige stillinger</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    jobs = html("<h1>Ledige stillinger</h1><p>Vi søker lagerarbeidere til våre kunder.</p>")
    ctx = ctx_for(hjemmeside="fjellbekk.no", naeringskode1={"kode": "78.200"})
    run(_site({"https://fjellbekk.no/ledige-stillinger": (200, jobs)}, body=body), ctx)
    assert not ctx.claims_for("hiring_signal")
    jobs2 = html("<h1>Ledige stillinger</h1><p>Vil du jobbe hos oss? Vi søker en ny kollega på kontoret.</p>")
    ctx2 = ctx_for(hjemmeside="fjellbekk.no", naeringskode1={"kode": "78.200"})
    run(_site({"https://fjellbekk.no/ledige-stillinger": (200, jobs2)}, body=body), ctx2)
    assert ctx2.claims_for("hiring_signal")


def test_wordpress_main_feed_before_a_linked_page_feed():
    """Easy-Regn shape: the only linked feed is a page's empty feed; the posts are in /feed/."""
    head = '<link rel="alternate" type="application/rss+xml" href="https://fjellbekk.no/testside/feed/">'
    body = f'<link href="/wp-content/a.css"><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    rss = ('<rss><channel><item><title>Ny versjon 4.2</title><link>https://fjellbekk.no/ny-versjon-4-2/</link>'
           '<pubDate>Fri, 05 Jun 2026 10:00:00 +0000</pubDate></item></channel></rss>')
    f = _site({"https://fjellbekk.no/feed/": (200, rss, "text/xml"),
               "https://fjellbekk.no/testside/feed/": (200, "<rss><channel></channel></rss>", "text/xml")},
              body=body, head=head)
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert [c.value for c in ctx.claims if c.field == "dated_news"] == ["Ny versjon 4.2 (2026-06-05T10:00:00+00:00)"]


def test_facebook_p_path_and_google_redirect_wrappers():
    c = W.clean_social("https://www.facebook.com/p/DEVO-100063562930121/")
    assert str(W.canonical_social(*c)) == "facebook.com/p/devo-100063562930121"
    assert W.unwrap_redirect("https://www.google.com/url?q=https%3A%2F%2Fwww.instagram.com%2Ffjellbekk%2F&sa=D") == \
        "https://www.instagram.com/fjellbekk/"
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS} '
            '<a href="https://www.google.com/url?q=https%3A%2F%2Fwww.instagram.com%2Ffjellbekk_trevare%2F&sa=D">ig</a>'
            '<a href="https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.linkedin.com%2Fcompany%2Ffjellbekk-trevare">li</a>'
            '</footer>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=body), ctx)
    assert sorted(str(c.value) for c in ctx.claims if c.field == "social_profile") == [
        "instagram.com/fjellbekk_trevare", "linkedin.com/company/fjellbekk-trevare"]


def test_email_span_is_verbatim_in_the_snapshot():
    """Hølonda shape: the mailto link is entity-encoded in the source; the span must be exact source text."""
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS} <a href="mailto:post&#64;fjellbekk.no">Send e-post</a></footer>'
            '<script type="application/ld+json">{"@type":"Organization","name":"Fjellbekk Trevare","email":"post@fjellbekk.no"}</script>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(_site(body=body), ctx)
    ev = next(e for e in ctx.evidence if e.id in claims["site_contact_email"].evidence_ids)
    raw = ctx.snapshots[ev.id].content.decode()
    assert ev.claim_span in raw


# ---------------------------------------------------------------- red-team review fixes
def test_theme_demo_posts_are_not_news():
    head = '<link rel="alternate" type="application/rss+xml" href="/feed/">'
    rss = ('<rss><channel>'
           + "".join(f'<item><title>{t}</title><link>https://fjellbekk.no/{i}/</link><pubDate>Thu, 16 Dec 2021 06:1{i}:24 +0000'
                     f'</pubDate></item>' for i, t in enumerate(["Build Dream Home", "Construction Company", "Powerful Modern Idea"]))
           + '</channel></rss>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site({"https://fjellbekk.no/feed/": (200, rss, "application/rss+xml")}, head=head), ctx)
    assert not ctx.claims_for("dated_news")
    items = [{"title": f"Sak {i}", "datetime": f"2021-12-16T06:1{i}:00+00:00", "url": f"u{i}"} for i in range(3)]
    assert W.drop_demo(items) == []  # every post created within minutes: an import
    later = items + [{"title": "Ny avdeling", "datetime": "2023-05-01T10:00:00+02:00", "url": "u9"}]
    assert [x["title"] for x in W.drop_demo(later)] == ["Ny avdeling"]


def test_feed_spans_are_exact_raw_text_with_entities():
    head = '<link rel="alternate" type="application/rss+xml" href="/feed/">'
    rss = ('<rss><channel><item><title><![CDATA[Gran &#038; Ekran bygger demning]]></title>'
           '<link>https://fjellbekk.no/demning/</link><pubDate>Wed, 30 Oct 2019 09:48:15 +0100</pubDate></item></channel></rss>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site({"https://fjellbekk.no/feed/": (200, rss, "application/rss+xml")}, head=head), ctx)
    c = ctx.claims_for("dated_news")[0]
    assert c.value == "Gran & Ekran bygger demning (2019-10-30T09:48:15+01:00)"
    for e in (e for e in ctx.evidence if e.id in c.evidence_ids):
        assert e.claim_span in ctx.snapshots[e.id].content.decode()


def test_group_parent_keeps_its_site_when_others_are_its_subsidiaries():
    """Equinor shape: the parent's site prints a subsidiary's org number; the parent keeps its derived facts."""
    body = (f'<footer>Fjellbekk Trevare ASA · Org.nr {OURS} · Fjellbekk Energi AS org.nr {OTHER}'
            '<a href="mailto:post@fjellbekk.no">x</a></footer>')
    tree = json.dumps({"organisasjonsnummer": OURS, "navn": "FJELLBEKK TREVARE ASA",
                       "children": [{"organisasjonsnummer": OTHER, "navn": "FJELLBEKK ENERGI AS", "children": []}]})
    pages = {"https://fjellbekk.no/": (200, html(body)),
             f"https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{OURS}": (200, tree, "application/json")}
    claims = run(FakeFetcher(pages), ctx_for(navn="FJELLBEKK TREVARE ASA", hjemmeside="fjellbekk.no"))
    assert claims["site_contact_email"].value == "post@fjellbekk.no"
    # a company that is not in our group still makes the site non-exclusive
    W._GROUP_CACHE.clear()
    W._GROUP_TREE.clear()
    tree2 = json.dumps({"organisasjonsnummer": OURS, "children": [{"organisasjonsnummer": THIRD, "children": []}]})
    pages[f"https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{OURS}"] = (200, tree2, "application/json")
    claims2 = run(FakeFetcher(pages), ctx_for(navn="FJELLBEKK TREVARE ASA", hjemmeside="fjellbekk.no"))
    assert claims2["site_contact_email"].availability == "ambiguous"


def test_proxy_mode_skips_dns_and_tcp_prechecks(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.setattr(W, "resolves", _real_resolves)

    async def boom(*a, **k):
        raise AssertionError("direct DNS must not be used behind a proxy")
    monkeypatch.setattr(W.dns.asyncresolver, "resolve", boom)
    assert asyncio.run(W.resolves(FakeFetcher({}), "fjellbekk.no")) is True
    assert asyncio.run(W.reachable("fjellbekk.no")) is True


def test_budget_exhaustion_is_failed_not_absence():
    f = FakeFetcher({})
    f.budget.max_requests = 2  # the run budget is nearly spent before the registry website is checked
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(f, ctx)
    assert claims["official_website"].availability == "failed"
    assert claims["official_website"].note.startswith("budget_exhausted")


def test_ordinary_company_text_about_customers_is_not_a_recruiter_mark():
    body = f'<a href="/ledige-stillinger/">Ledige stillinger</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    jobs = html("<h1>Ledige stillinger</h1><p>Vi lager dører for våre kunder. Vi søker en snekker.</p>")
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site({"https://fjellbekk.no/ledige-stillinger/": (200, jobs)}, body=body), ctx)
    assert ctx.claims_for("hiring_signal")


# ---------------------------------------------------------------- round 7 audit fixes
def test_site_builder_template_socials_are_not_the_company():
    """Interiør og Malefag shape: a Surftown template footer links Surftown's own profiles."""
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS} <a href="mailto:post@fjellbekk.no">post@fjellbekk.no</a>'
            '<a href="https://www.facebook.com/surftown.com">f</a><a href="https://www.linkedin.com/company/surftown">l</a>'
            '<a href="https://x.com/surftown">x</a><a href="https://www.youtube.com/user/surftowncom">y</a>'
            '<a href="https://www.instagram.com/fjellbekktrevare/">i</a></footer>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=body), ctx)
    assert [str(c.value) for c in ctx.claims if c.field == "social_profile"] == ["instagram.com/fjellbekktrevare"]


def test_phone_span_contains_the_phone_not_a_neighbouring_email():
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS}<br>E-post: kari@fjellbekk.no Telefon: 977 50 160</footer>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(_site(body=body), ctx)
    c = claims["site_contact_phone"]
    span = next(e.claim_span for e in ctx.evidence if e.id in c.evidence_ids)
    assert "977 50 160" in span and "@" not in span


def test_spans_are_exact_bytes_on_a_latin1_page():
    """Fenconor shape: a page served as ISO-8859-1; every span's UTF-8 bytes must occur in the saved bytes."""
    page = ('<html><head><meta charset="iso-8859-1"><title>: : Fjellbekk Trevare : :</title>'
            '<meta name="keywords" content="fendere, pullert, kjøkken, bøyer"></head><body>'
            f'<h1>Fjellbekk Trevare</h1><p>Kjøkken og bad i Testby</p><footer>Fjellbekk Trevare AS · Org.nr {OURS} · '
            'Tlf 41 00 00 01 · <a href="mailto:post@fjellbekk.no">post@fjellbekk.no</a></footer></body></html>')

    class Latin1Fetcher(FakeFetcher):
        async def get(self, url, **kw):
            snap = await super().get(url, **kw)
            snap.content = page.encode("latin-1") if url.startswith("https://fjellbekk.no/") else snap.content
            return snap
    f = Latin1Fetcher({"https://fjellbekk.no/": (200, page, "text/html; charset=iso-8859-1")})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert ctx.claims_for("official_website")
    for e in ctx.evidence:
        snap = ctx.snapshots.get(e.id)
        if snap is not None and e.claim_span:
            assert e.claim_span.encode("utf-8") in snap.content, e.claim_span


# ---------------------------------------------------------------- round 11 audit fixes
def test_claims_line_is_not_a_contact_phone():
    """Mjøsa Forsikring shape: 'Meld skade' is the insurance partner's claims line."""
    body = (f'<footer><p>Fjellbekk Trevare AS · Org.nr {OURS} · Hunnsvegen 5, Kontortelefon: '
            '<a href="tel:61100202">61 10 02 02</a>, Meld skade: <a href="tel:22000800">22 00 08 00</a></p></footer>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=body), ctx)
    assert [c.value for c in ctx.claims if c.field == "site_contact_phone"] == ["+4761100202"]


def test_project_name_is_not_a_description():
    """HUS arkitekter shape: the homepage meta description is the name of a featured project."""
    head = '<meta name="description" content="Cissi Klein videregående skole">'
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=f"<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>", head=head), ctx)
    assert not ctx.claims_for("website_description")
    head2 = '<meta name="description" content="Fjellbekk Trevare lager kjøkken og bad på mål i Testby.">'
    ctx2 = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=f"<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>", head=head2), ctx2)
    assert ctx2.claims_for("website_description")

# ---------------------------------------------------------------- revision 2: more candidates, privacy-page proof
def test_rev2_name_derived_candidates():
    assert "adma.no" in W.name_domains("ADMA AS", {})
    assert "vaneoceantech.com" in W.name_domains("VANE OCEAN TECHNOLOGIES AS", {})
    assert not any(d.startswith(("vo", "otech")) for d in W.name_domains("VANE OCEAN TECHNOLOGIES AS", {}))
    assert "omsorli.no" in W.name_domains("AUT MASKINENTREPRENØR OLAV MARTIN SØRLI AS", {})
    assert "sunwind.no" in W.name_domains("SUNWIND GYLLING AS", {})
    assert "2tal.as" in W.name_domains("2TAL UTEMILJØ ANLEGGSGARTNER AS", {})
    ctx = ctx_for(navn="FJELLBEKK.NO AS")
    assert W.candidates(ctx)[0][:2] == ("fjellbekk.no", "guess")
    ctx2 = ctx_for(navn="KVIST GYLLING AS", historiskeNavn=[{"navn": "FJELLBEKK TREVARE AS"}])
    assert ("fjellbekktrevare.no", "guess", None) in W.candidates(ctx2)


def test_privacy_page_supplies_the_org_number_for_a_name_only_site():
    """Ludenso shape: the front page names the company; only /privacy-policy prints the org number."""
    home = html('<h1>Fjellbekk Trevare</h1><a href="/privacy-policy">Privacy policy</a>')
    priv = html(f"<p>Fjellbekk Trevare AS, Torggata 2, Oslo. Organisation Number: {OURS[:3]} {OURS[3:6]} {OURS[6:]}</p>")
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, home), "https://fjellbekktrevare.no/privacy-policy": (200, priv)})
    claims = run(f, ctx_for())
    assert claims["official_website"].match["method"] == "orgnr_on_page"


def test_domain_name_redirecting_to_brand_site_with_our_orgnr():
    """Innregistrering.no shape: the name is a domain that redirects to a brand site printing our org number."""
    f = FakeFetcher({"https://kvistmerke.no/": (200, html(f"<footer>(c) Fjellbekk.no AS, Org.nr. {OURS}</footer>"))},
                    redirects={"https://fjellbekk.no/": "https://kvistmerke.no/"})
    claims = run(f, ctx_for(navn="FJELLBEKK.NO AS"))
    assert claims["official_website"].value == "kvistmerke.no"


def test_first_word_domain_needs_our_org_number():
    """Mobarn shape: mobarn.no is the chain parent's site; it shows our address and our kindergarten's name."""
    body = ("<footer>Kontakt Kvist Hamnegata 8b 9999 Testby</footer>"
            "<p>01.02.2024 Ett år med friluftsavdeling i Kvist Lynghaug barnehage</p>")
    ctx = ctx_for(navn="KVIST LYNGHAUG BARNEHAGE AS",
                  forretningsadresse={"adresse": ["Hamnegata 8b"], "postnummer": "9999", "poststed": "TESTBY"})
    assert ("kvist.no", "guess_short", None) in W.candidates(ctx)
    run(FakeFetcher({"https://kvist.no/": (200, html(body))}), ctx)
    assert not ctx.claims_for("official_website")


def test_redirect_target_privacy_page_proves_the_brand_domain():
    """Ludenso shape: ludenso.no redirects to ludenso.com, whose privacy page prints our org number."""
    home = html('<h1>Fjellbekk Trevare</h1><a href="/privacy-policy">Privacy</a>')
    priv = html(f"<p>Fjellbekk Trevare AS ... Organisation Number: {OURS[:3]} {OURS[3:6]} {OURS[6:]}</p>")
    f = FakeFetcher({"https://fjellbekk.com/": (200, home), "https://fjellbekk.com/privacy-policy": (200, priv)},
                    redirects={"https://fjellbekk.no/": "https://fjellbekk.com/"})
    claims = run(f, ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["official_website"].value == "fjellbekk.com"
    assert claims["official_website"].match["method"] == "orgnr_on_page"


# ---------------------------------------------------------------- submission build: labelled relations, own budgets
def test_explicit_hiring_and_news_states_on_a_verified_site():
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(_site(), ctx)
    assert claims["official_website"].value == "fjellbekk.no"
    assert claims["hiring_signal"].availability == "not_available" and "checked" in claims["hiring_signal"].note
    assert claims["dated_news"].availability == "not_available"


def test_official_website_values_are_always_bare_domains():
    """Sicra shape: the registry lists a deep page on the company's own domain."""
    f = FakeFetcher({"https://fjellbekk.no/no/om-oss/historie": (200, html("<h1>Historie</h1><p>Fjellbekk Trevare</p>")),
                     "https://fjellbekk.no/": (200, html(f"<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>"))})
    claims = run(f, ctx_for(hjemmeside="https://fjellbekk.no/no/om-oss/historie"))
    assert claims["official_website"].value == "fjellbekk.no"


def test_seed_order_commoncrawl_before_other_seeds_and_domain_matches(monkeypatch):
    monkeypatch.setattr(W, "_SEEDS", {OURS: [{"domain": "a-weak.no", "sources": ["commoncrawl_domain"]},
                                             {"domain": "b-osm.no", "sources": ["osm_phone"]},
                                             {"domain": "c-cc.no", "sources": ["commoncrawl"]}]})
    order = [d for d, s, _ in W.candidates(ctx_for(navn="FJELLBEKK TREVARE AS")) if s.startswith("seed:")]
    assert order == ["c-cc.no", "b-osm.no", "a-weak.no"]


def _kvist_row(name, extra=None):
    row = {"organisasjonsnummer": SISTER, "navn": name}
    row.update(extra or {})
    q = "%20".join(W._twords(" ".join(W.name_words(name))))
    return W.BRREG_SEARCH + f"?navn={q}&size=20", (200, json.dumps({"_embedded": {"enheter": [row]}}), "application/json")


def test_staff_club_mentioned_on_own_domain_keeps_official_website():
    """Narrow rule: a staff club sharing our name, mentioned on the site, is a mention, not an operator."""
    body = (f'<h1>Fjellbekk Trevare</h1><p>Bedriftsklubben Fjellbekk Trevare AS arrangerer sommerfest. '
            f'Bedriftsklubben Fjellbekk Trevare AS ønsker alle velkommen.</p>'
            '<footer><a href="mailto:post@fjellbekk.no">post@fjellbekk.no</a></footer>')
    url, resp = _kvist_row("BEDRIFTSKLUBBEN FJELLBEKK TREVARE AS")
    claims = run(FakeFetcher({"https://fjellbekk.no/": (200, html(body)), url: resp}), ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["official_website"].value == "fjellbekk.no"


def test_copyright_owner_in_footer_makes_own_domain_a_related_website():
    body = '<h1>Fjellbekk Trevare</h1><footer>© 2026 Kvist Holding AS</footer>'
    url, resp = _kvist_row("KVIST HOLDING AS")
    claims = run(FakeFetcher({"https://fjellbekk.no/": (200, html(body)), url: resp}), ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["official_website"].availability == "ambiguous"
    assert claims["related_website"].value["domain"] == "fjellbekk.no"


# ---------------------------------------------------------------- judges' round 2 fixes
def test_group_parent_keeps_its_registry_site_despite_staff_bodies_and_subsidiaries():
    """Equinor shape: the parent's registry website names a subsidiary, its pension fund shares its address, and
    contact addresses sit on group domains; the parent keeps its site and its careers page."""
    body = ('<h1>Fjellbekk Trevare</h1><p>Fjellbekk Energi AS org.nr ' + OTHER + ' er vårt datterselskap.</p>'
            '<p>Fjellbekk Pensjon (pensjonskasse) holder til i Lyngveien 12B, 9999 Testby.</p>'
            '<footer><a href="mailto:post@fjellbekk-us.com">US office</a></footer>'
            '<a href="/careers">Careers</a>')
    tree = json.dumps({"organisasjonsnummer": OURS, "children": [{"organisasjonsnummer": OTHER, "children": []}]})
    pages = {"https://fjellbekk.no/": (200, html(body)),
             "https://fjellbekk.no/careers": (200, html("<h1>Careers</h1><p>Join our team</p>")),
             f"https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{OURS}": (200, tree, "application/json")}
    claims = run(FakeFetcher(pages), ctx_for(navn="FJELLBEKK TREVARE ASA", hjemmeside="www.fjellbekk.no"))
    assert claims["official_website"].value == "fjellbekk.no"
    assert claims["hiring_signal"].value == "https://fjellbekk.no/careers"


def test_post_no_mailbox_is_not_another_organisations_domain():
    body = f'<footer>Fjellbekk Trevare AS · Org.nr {OURS} <a href="mailto:fjellbekk@post.no">fjellbekk@post.no</a></footer>'
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=body), ctx)
    assert ctx.facts["website_exclusive"] is True


def test_foreign_group_number_beside_our_operator_orgnr_keeps_official():
    """Nika Norge shape: our org number in the footer, and the Finnish parent's Y-tunnus on the same page."""
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS} · Kvist Oy, Y-tunnus 1234567-8</footer>'
            '<a href="mailto:post@fjellbekk.no">x</a>')
    claims = run(_site(body=body), ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["official_website"].value == "fjellbekk.no"
    assert claims["site_contact_email"].availability == "ambiguous"


def test_redirect_to_group_domain_locale_page_with_our_orgnr_is_official():
    """Intility and Loomis shape: the registry domain redirects to /nb-no on a group domain that prints our org number."""
    f = FakeFetcher({"https://kvistgroup.com/nb-no": (200, html(f"<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>"))},
                    redirects={"https://fjellbekk.no/": "https://kvistgroup.com/nb-no"})
    claims = run(f, ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["official_website"].value == "kvistgroup.com"
    assert "redirected from fjellbekk.no" in claims["official_website"].match["detail"]


def test_http_403_only_candidate_is_blocked():
    f = FakeFetcher({"https://fjellbekk.no/": (403, html("<h1>Forbidden</h1>"))})
    claims = run(f, ctx_for(navn="QX AS", hjemmeside="fjellbekk.no"))
    assert claims["official_website"].availability == "blocked"


def test_cap_reached_with_only_name_guesses_unchecked_is_absence_with_note(monkeypatch):
    monkeypatch.setattr(W, "MAX_REQUESTS", 2)
    pages = {"https://fjellbekktrevare.no/": (200, html("<h1>Kvist</h1>", title="Kvist")),
             "https://fjellbekk-trevare.no/": (200, html("<h1>Kvist</h1>", title="Kvist"))}
    claims = run(FakeFetcher(pages), ctx_for())
    assert claims["official_website"].availability == "not_available"
    assert "not checked (name guesses only" in claims["official_website"].note


def test_cap_reached_with_a_seed_unchecked_is_failed(monkeypatch):
    monkeypatch.setattr(W, "MAX_REQUESTS", 2)
    monkeypatch.setattr(W, "STRONG_EXTRA", 0)
    monkeypatch.setattr(W, "website_seeds", lambda: {OURS: [{"domain": "kvistlyng.no", "sources": ["crawl_orgnr"]}]})
    pages = {"https://fjellbekktrevare.no/": (200, html("<h1>Kvist</h1>", title="Kvist")),
             "https://kvistlyng.no/": (200, html("<h1>Kvist</h1>", title="Kvist"))}
    claims = run(FakeFetcher(pages), ctx_for(hjemmeside="fjellbekktrevare.no"))
    assert claims["official_website"].availability == "failed"
    assert claims["official_website"].note.startswith("budget_exhausted")


def test_name_guesses_wait_for_a_seed_when_their_share_is_spent(monkeypatch):
    """Guesses use at most GUESS_BUDGET requests while a seed with crawl proof still waits."""
    monkeypatch.setattr(W, "GUESS_BUDGET", 2)
    monkeypatch.setattr(W, "website_seeds", lambda: {OURS: [{"domain": "kvistlyng.no", "sources": ["crawl_orgnr"]}]})
    page = html("<h1>Hei</h1>", title="Hei")
    pages = {"https://fjellbekktrevare.no/": (200, page), "https://fjellbekktrevare.com/": (200, page),
             "https://fjellbekk-trevare.no/": (200, page),
             "https://kvistlyng.no/": (200, html(f"<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>"))}
    f = FakeFetcher(pages)
    claims = run(f, ctx_for())
    assert claims["official_website"].value == "kvistlyng.no"
    assert "https://fjellbekk-trevare.no/" not in f.calls


def test_blocked_name_guess_is_not_reported_as_blocked():
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, "x")}, disallow={"https://fjellbekktrevare.no/"})
    claims = run(f, ctx_for())
    assert claims["official_website"].availability == "not_available"
    assert "fjellbekktrevare.no (blocked)" in claims["official_website"].note


def test_own_printed_orgnr_beats_sister_mentions_and_mail_services():
    """Hava Omsorg and Selsbakkhøgda shapes: sisters named, a board mailbox on styrepost.no."""
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS} · Søsterselskaper: Kvist Omsorg AS, Lyng Omsorg AS '
            '<a href="mailto:styret@styrepost.no">styret@styrepost.no</a></footer>')
    claims = run(_site(body=body), ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["official_website"].value == "fjellbekk.no"


def test_another_operator_org_number_still_makes_it_related():
    body = f'<footer>Fjellbekk Trevare AS · Org.nr {OURS} · Drives av Kvist Drift AS, org.nr {OTHER}</footer>'
    claims = run(_site(body=body), ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["official_website"].availability == "ambiguous"


def test_rate_limited_candidate_is_a_transient_failure():
    """Bergen Rubber shape: a 429 on a candidate must not become 'not available' (two runs must agree)."""
    pages = {"https://fjellbekktrevare.no/": (429, html("Too many requests"))}
    ctx = ctx_for()
    claims = run(FakeFetcher(pages), ctx)
    assert claims["official_website"].availability == "failed"
    assert ctx.module_states["website"] == "timeout"


# ---------------------------------------------------------------- v2: more dated news and hiring signals
def _news(ctx):
    return [c.value for c in ctx.claims if c.field == "dated_news" and c.availability == "available"]


def test_template_posts_are_never_news_or_page_updates():
    from kilde import gate
    for t in ("Hello world!", "This is your first post", "This Is the Title of Your First Post", "Welcome to your blog",
              "Velkommen til bloggen", "Design a Stunning Blog", "Lorem ipsum dolor sit amet", "Sample Page"):
        assert gate.template_post(t, None), t
    for u in ("https://x.no/post/this-is-your-first-post", "https://x.no/hello-world/", "https://x.no/sample-page/",
              "https://x.no/blog/welcome-to-your-blog-2", "https://x.no/velkommen-til-bloggen/"):
        assert gate.template_post(None, u), u
    for t, u in (("Velkommen til vår nye butikk i Testby", "https://x.no/nyheter/ny-butikk"),
                 ("Our first post-pandemic fair", "https://x.no/blog/first-fair")):
        assert not gate.template_post(t, u)
    head = '<link rel="alternate" type="application/rss+xml" href="/blog-feed.xml">'
    rss = ('<rss><channel>'
           '<item><title>This Is Your First Post</title><link>https://fjellbekk.no/post/this-is-your-first-post</link>'
           '<pubDate>Mon, 02 Mar 2026 10:00:00 +0000</pubDate></item>'
           '<item><title>Velkommen til bloggen</title><link>https://fjellbekk.no/post/velkommen</link>'
           '<pubDate>Mon, 02 Feb 2026 10:00:00 +0000</pubDate></item>'
           '<item><title>Nytt verksted i Testby</title><link>https://fjellbekk.no/post/nytt-verksted</link>'
           '<pubDate>Tue, 03 Mar 2026 10:00:00 +0000</pubDate></item></channel></rss>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site({"https://fjellbekk.no/blog-feed.xml": (200, rss, "application/rss+xml")}, head=head), ctx)
    assert _news(ctx) == ["Nytt verksted i Testby (2026-03-03T10:00:00+00:00)"]


def test_wordpress_plain_permalink_posts_are_news():
    head = '<link rel="alternate" type="application/rss+xml" href="/?feed=rss2">'
    rss = ('<rss><channel><item><title>Har skapt mange nye arbeidsplasser</title><link>https://fjellbekk.no/?p=1042</link>'
           '<pubDate>Mon, 06 May 2024 08:08:04 +0000</pubDate></item>'
           '<item><title>Om oss</title><link>https://fjellbekk.no/?page_id=2</link>'
           '<pubDate>Mon, 06 May 2024 08:08:04 +0000</pubDate></item></channel></rss>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site({"https://fjellbekk.no/?feed=rss2": (200, rss, "application/rss+xml")}, head=head), ctx)
    assert _news(ctx) == ["Har skapt mange nye arbeidsplasser (2024-05-06T08:08:04+00:00)"]


def test_slash_dates_read_only_in_the_order_the_page_uses():
    def page(body, lang="nb"):
        return W.parse_page(Snapshot("https://x.no/", "https://x.no/", 200, utc_now(),
                                     f'<html lang="{lang}"><body>{body}</body></html>'.encode(), "text/html"))
    assert W.slash_order(page("<p>20/04/2026</p><p>3/4/2026</p>", "en")) == "dmy"
    assert W.slash_order(page("<p>7/30/2026</p><p>3/4/2026</p>")) == "mdy"
    assert W.slash_order(page("<p>3/4/2026</p>")) == "dmy"           # Norwegian page, nothing decisive
    assert W.slash_order(page("<p>3/4/2026</p>", "en")) is None      # English page, nothing decisive: unread
    assert W.slash_order(page("<p>20/04/2026</p><p>7/30/2026</p>")) is None  # mixed: unread
    listing = html('<h1>Aktuelle saker</h1>'
                   '<div class="item"><span>20/04/2026 - mra</span><h3><a href="/b/protokoll-2026">Protokoll årsmøte 2026</a></h3></div>'
                   '<div class="item"><span>3/2/2026 - mra</span><h3><a href="/b/innkalling-2026">Innkalling til årsmøte 2026</a></h3></div>')
    body = f'<nav><a href="/aktuelt">Aktuelt</a></nav><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site({"https://fjellbekk.no/aktuelt": (200, listing)}, body=body), ctx)
    assert _news(ctx) == ["Protokoll årsmøte 2026 (2026-04-20)", "Innkalling til årsmøte 2026 (2026-02-03)"]
    first = ctx.claims_for("dated_news")[0]
    assert "20/04/2026" in {e.claim_span for e in ctx.evidence if e.id in first.evidence_ids}  # the listing's date cited


def test_news_listing_found_by_link_text_on_deeper_paths_files_and_subdomains():
    def pages(body):
        return [W.parse_page(Snapshot("https://fjellbekk.no/", "https://fjellbekk.no/", 200, utc_now(),
                                      html(body).encode(), "text/html"))]
    assert W.news_listings(pages('<a href="/our-news">Our News</a>'), "fjellbekk.no") == ["https://fjellbekk.no/our-news"]
    assert W.news_listings(pages('<a href="/no/kunnskap/nyheter">Nyheter</a>'), "fjellbekk.no") == [
        "https://fjellbekk.no/no/kunnskap/nyheter"]
    assert W.news_listings(pages('<a href="/nyheter.html">Siste</a>'), "fjellbekk.no") == ["https://fjellbekk.no/nyheter.html"]
    assert W.news_listings(pages('<a href="https://blog.fjellbekk.no/?lang=nb">Blogg</a>'), "fjellbekk.no") == [
        "https://blog.fjellbekk.no/"]
    # never the front page, another site, a category, or a file
    assert W.news_listings(pages('<a href="/">Nyheter</a><a href="https://avis.no/nyheter">Nyheter</a>'
                                 '<a href="/category/nyheter/">Nyheter</a><a href="/nyheter.pdf">Nyheter</a>'),
                           "fjellbekk.no") == []


def test_news_listing_with_dates_beside_headings_only():
    listing = html('<h1>Nyheter</h1>'
                   '<div><p>1. oktober 2026 · Lansering</p><h3>Ny lås uten batteri til Norge</h3><p>Tekst</p></div>'
                   '<div><p>12. juni 2024 · Agentur</p><h3>Fjellbekk inngår agenturavtale</h3><p>Tekst</p></div>')
    body = f'<a href="/nyheter.html">Nyheter</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site({"https://fjellbekk.no/nyheter.html": (200, listing)}, body=body), ctx)
    assert _news(ctx) == ["Ny lås uten batteri til Norge (2026-10-01)", "Fjellbekk inngår agenturavtale (2024-06-12)"]


def test_undated_news_listing_reads_article_pages_that_declare_a_date():
    listing = html('<h1>Aktuelt</h1><main>'
                   '<article><a href="/frokostseminar-om-kjokkendesign/">Frokostseminar om kjøkkendesign i Testby</a>'
                   '<a href="/category/event/">Event</a></article>'
                   '<article><a href="/vi-tilbyr-ogsa-baderom/">Vi tilbyr også nye baderom til private</a></article>'
                   '<article><a href="/aktuelt/ny-daglig-leder">Ny daglig leder fra 1. mars i år</a></article>'
                   '<article><a href="/aktuelt/apent-hus-i-verkstedet">Åpent hus i verkstedet vårt</a></article>'
                   '</main>')
    post = html('<h1>Frokostseminar om kjøkkendesign</h1>',
                head='<meta property="article:published_time" content="2026-09-10T08:00:00+02:00">')
    service = html('<h1>Baderom</h1>', head='<script type="application/ld+json">{"@type":"WebPage",'
                                            '"datePublished":"2020-01-01T10:00:00+00:00"}</script>')
    under = html('<h1>Ny daglig leder</h1>', head='<script type="application/ld+json">{"@type":"WebPage",'
                                                  '"datePublished":"2026-02-20T09:00:00+01:00"}</script>')
    byline = html('<h1>Åpent hus i verkstedet</h1><div><p>18/8/2025</p><p>Skrevet av Kari</p></div><p>Velkommen!</p>')
    body = f'<a href="/aktuelt/">Aktuelt</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site({"https://fjellbekk.no/aktuelt/": (200, listing),
               "https://fjellbekk.no/frokostseminar-om-kjokkendesign/": (200, post),
               "https://fjellbekk.no/vi-tilbyr-ogsa-baderom/": (200, service),
               "https://fjellbekk.no/aktuelt/ny-daglig-leder": (200, under),
               "https://fjellbekk.no/aktuelt/apent-hus-i-verkstedet": (200, byline)}, body=body), ctx)
    # the service page outside the listing's path has only a WebPage date: not taken
    assert _news(ctx) == ["Frokostseminar om kjøkkendesign (2026-09-10T08:00:00+02:00)",
                          "Ny daglig leder (2026-02-20T09:00:00+01:00)", "Åpent hus i verkstedet (2025-08-18)"]
    for c in ctx.claims_for("dated_news"):
        for e in (e for e in ctx.evidence if e.id in c.evidence_ids):
            assert e.claim_span in ctx.snapshots[e.id].content.decode()


def test_article_page_with_several_dates_or_no_label_gives_no_date():
    def page(body):
        return W.parse_page(Snapshot("https://x.no/aktuelt/a", "https://x.no/aktuelt/a", 200, utc_now(),
                                     html(body).encode(), "text/html"))
    assert W.article_published(page('<h1>Kurs</h1><p>Publisert 3. mars 2026</p><p>Kursdato: 12. mars 2026</p>'
                                     '<p>Påmelding innen 10. mars 2026</p>'), True) is None
    assert W.article_published(page('<h1>Kurs</h1><div><p>12. mars 2026</p><p>Sted: Testby</p></div>'), True) is None
    assert W.article_published(page('<h1>Kurs</h1><div><span>Publisert</span> <span>3. mars 2026</span></div>'), True) == (
        "2026-03-03", "3. mars 2026")
    assert W.article_published(page('<h1>Kurs</h1><div><span>Publisert</span> <span>3. mars 2026</span></div>'), False) is None


def test_job_ad_linked_as_stillingsannonse_is_a_careers_page():
    assert W.jobs_url("/nettside/stillingsannonse") and W.jobs_url("/join-us") and W.jobs_url("/om/bli-en-av-oss/")
    assert not W.jobs_url("/ledig-kapasitet") and not W.jobs_url("/utstillingsareal")


def test_hiring_signal_from_a_recruitment_platform_board_linked_from_the_site():
    body = (f'<nav><a href="https://fjellbekk.teamtailor.com/jobs">Ledige stillinger</a>'
            f'<a href="https://www.teamtailor.com/">Teamtailor</a></nav>'
            f'<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=body), ctx)
    hs = ctx.claims_for("hiring_signal")
    assert [c.value for c in hs] == ["https://fjellbekk.teamtailor.com/jobs"]
    assert hs[0].match["method"] == "ats_link_on_verified_site"
    assert not any("teamtailor" in u for u in f_calls(ctx))


def f_calls(ctx):
    return [e.source_url for e in ctx.evidence]


def test_recruitment_platform_links_that_are_not_our_board_are_ignored():
    assert not W.ats_board("https://www.teamtailor.com/")
    assert not W.ats_board("https://fjellbekk.recman.no/customer/login")
    assert W.ats_board("https://jobs.lever.co/fjellbekk") and W.ats_board("https://fjellbekk.teamtailor.com/")
    assert W.ats_board("https://candidate.hr-manager.net/vacancies/list.aspx?customer=fjellbekk")
    assert not W.ats_board("https://kjedenas.teamtailor.com/jobs", ["fjellbekk", "trevare"])
    # a chain's board, and a staffing firm's board (its clients' jobs), are not this company hiring
    body = (f'<a href="https://kjeden.teamtailor.com/jobs">Ledige stillinger</a>'
            f'<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=body), ctx)
    assert not ctx.claims_for("hiring_signal")
    body2 = (f'<a href="https://fjellbekk.recman.page/jobs">Ledige stillinger</a>'
             f'<footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>')
    ctx2 = ctx_for(hjemmeside="fjellbekk.no", naeringskode1={"kode": "78.200"})
    run(_site(body=body2), ctx2)
    assert not ctx2.claims_for("hiring_signal")


def test_spans_match_titles_written_with_numeric_entities():
    raw = '<h3 class="t">Moi R&#xF8;r banet vei for prosjekter med b&#xE6;rekraft &amp; &#248;kt fart</h3>'
    assert W.verbatim(raw, "Moi Rør banet vei for prosjekter med bærekraft & økt fart") == \
        "Moi R&#xF8;r banet vei for prosjekter med b&#xE6;rekraft &amp; &#248;kt fart"
    assert W.verbatim(raw, "Moi Rør banet vei mot noe annet") is None


# ---------------------------------------------------------------- v2 website and social recall rules
def test_find_us_page_is_read_for_the_registered_address():
    """A 'Find us' page is a contact page: our registered address there proves the site with the legal name."""
    home = html('<nav><a href="/om">Om</a><a href="/findus">Find us</a></nav><h1>Fjellbekk Trevare AS</h1>')
    findus = html("<p>Fjellbekk Trevare AS, Lyngveien 12B, 9999 Testby</p>")
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, home), "https://fjellbekktrevare.no/findus": (200, findus),
                     "https://fjellbekktrevare.no/om": (200, html("<p>Om oss</p>"))})
    claims = run(f, ctx_for())
    assert claims["official_website"].value == "fjellbekktrevare.no"
    assert claims["official_website"].match["method"] == "address_and_name"


def test_trailing_slash_variants_are_one_page():
    home = html('<a href="/kontakt-oss/">Kontakt</a><a href="/kontakt-oss">Kontakt oss</a><a href="/om-oss">Om</a>')
    page = W.parse_page(Snapshot(url="https://fjellbekk.no/", final_url="https://fjellbekk.no/", status=200,
                                 retrieved_at=utc_now(), content=home.encode(), content_type="text/html"))
    assert W.internal_links(page) == ["https://fjellbekk.no/kontakt-oss/", "https://fjellbekk.no/om-oss"]


def test_whole_name_on_com_is_tried_before_shortened_names():
    doms = W.name_domains("TESTBY MOUNTAIN GUIDES AS", {"forretningsadresse": {"poststed": "TESTBY"}})
    assert doms.index("testbymountainguides.com") < doms.index("mountainguides.no")
    assert doms[:2] == ["testbymountainguides.no", "testby-mountain-guides.no"]


def test_group_parent_site_whose_subsidiary_number_is_only_on_the_contact_page():
    """AF Gruppen shape: the front page names us, the contact page prints a subsidiary's org number."""
    home = html('<nav><a href="/kontakt-oss/">Kontakt</a></nav><h1>Fjellbekk Trevare ASA</h1>'
                '<footer><a href="mailto:post@fjellbekk.no">post@fjellbekk.no</a></footer>')
    contact = html(f"<p>Fjellbekk Energi AS, org.nr {OTHER}</p>")
    tree = json.dumps({"organisasjonsnummer": OURS, "children": [{"organisasjonsnummer": OTHER, "children": []}]})
    pages = {"https://fjellbekk.no/": (200, home), "https://fjellbekk.no/kontakt-oss/": (200, contact),
             f"https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{OURS}": (200, tree, "application/json")}
    claims = run(FakeFetcher(pages), ctx_for(navn="FJELLBEKK TREVARE ASA", hjemmeside="fjellbekk.no"))
    assert claims["official_website"].value == "fjellbekk.no"
    # without the group tie the same site stays a related site
    W._GROUP_CACHE.clear()
    W._GROUP_TREE.clear()
    pages[f"https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{OURS}"] = (404, "{}", "application/json")
    claims = run(FakeFetcher(pages), ctx_for(navn="FJELLBEKK TREVARE ASA", hjemmeside="fjellbekk.no"))
    assert claims["official_website"].availability != "available"


def _near_row(name, form, nace):
    return {"organisasjonsnummer": SISTER, "navn": name, "organisasjonsform": {"kode": form},
            "naeringskode1": {"kode": nace}, "forretningsadresse": {"adresse": ["Lyngveien 12B"], "postnummer": "9999"}}


def test_staff_union_club_sharing_our_address_is_no_namesake():
    """Leirvik and Consto shapes: 'Tekna Fjellbekk' or a company sports club at our address."""
    body = "<h1>Fjellbekk</h1><footer>Fjellbekk AS · Lyngveien 12B, 9999 Testby</footer>"
    q = "https://data.brreg.no/enhetsregisteret/api/enheter?navn=fjellbekk&forretningsadresse.postnummer=9999&size=50"
    for row, published in ((_near_row("TEKNA FJELLBEKK", "FLI", "94.200"), True),
                           (_near_row("FJELLBEKK BEDRIFTSIDRETTSLAG", "FLI", "93.120"), True),
                           (_near_row("FJELLBEKK IDRETTSLAG", "FLI", "93.120"), False),  # a club, not staff
                           (_near_row("FJELLBEKKS AS", "AS", "41.000"), False)):
        W._NAME_SEARCH.clear()
        W._SEARCH.clear()
        pages = {"https://fjellbekk.com/": (200, html(body)),
                 q: (200, json.dumps({"_embedded": {"enheter": [row]}}), "application/json")}
        ctx = ctx_for(navn="FJELLBEKK AS")
        run(FakeFetcher(pages), ctx)
        assert bool(ctx.claims_for("official_website")) is published, row["navn"]


def test_namesake_seen_only_inside_our_own_name_does_not_collide():
    """The Well shape: 'WELL AS' is registered too, but the site only ever writes 'The Well AS'."""
    body = '<h1>The Well</h1><footer>The Well AS · <a href="mailto:post@thewell.no">post@thewell.no</a></footer>'
    pages = {"https://thewell.no/": (200, html(body, title="The Well"))}
    pages.update(_brreg("enheter", "well", [{"organisasjonsnummer": SISTER, "navn": "WELL AS"}]))
    ctx = ctx_for(navn="THE WELL AS", hjemmeside="thewell.no")
    claims = run(FakeFetcher(pages), ctx)
    assert claims["official_website"].value == "thewell.no"
    assert claims["site_contact_email"].value == "post@thewell.no"
    # the namesake written on its own still collides
    W._NAME_SEARCH.clear()
    W._SEARCH.clear()
    body2 = body.replace("</footer>", " Samarbeid med Well AS</footer>")
    pages["https://thewell.no/"] = (200, html(body2, title="The Well"))
    claims = run(FakeFetcher(pages), ctx_for(navn="THE WELL AS", hjemmeside="thewell.no"))
    assert claims.get("site_contact_email") is None or claims["site_contact_email"].availability != "available"


def test_redirect_into_the_registry_email_domain_is_the_company_site():
    """Hjelpemiddelspesialisten shape: the registry website redirects to the registry email domain."""
    body = '<h1>Fjellbekk Trevare AS</h1><footer><a href="mailto:post@fjb.no">post@fjb.no</a></footer>'
    f = FakeFetcher({"https://fjb.no/": (200, html(body))}, redirects={"https://fjellbekk.no/": "https://fjb.no/"})
    claims = run(f, ctx_for(hjemmeside="fjellbekk.no", epostadresse="post@fjb.no"))
    assert claims["official_website"].value == "fjb.no"
    assert claims["site_contact_email"].value == "post@fjb.no"
    # a redirect into an undeclared domain is still a related page
    W._HJEM_CACHE.clear()
    f2 = FakeFetcher({"https://fjb.no/": (200, html(body))}, redirects={"https://fjellbekk.no/": "https://fjb.no/"})
    claims2 = run(f2, ctx_for(hjemmeside="fjellbekk.no", epostadresse="post@fjellbekk.no"))
    assert claims2["official_website"].availability != "available"


def test_supplier_named_after_an_operated_by_phrase_is_not_the_operator():
    """Norway Active shape: 'servers operated by the supplier for our e-commerce: Kvist Booking AS'."""
    words = W.name_words("FJELLBEKK TREVARE AS")
    priv = W.parse_page(Snapshot(url="https://fjellbekk.no/privacy-policy/", final_url="https://fjellbekk.no/privacy-policy/",
                                 status=200, retrieved_at=utc_now(), content=html(
        "<p>Data is stored on servers which are operated by the supplier for our e-commerce: Kvist Booking AS.</p>"
        "<p>The site is operated by Lyng Drift AS.</p>").encode(), content_type="text/html"))
    names = {n[0]: n for n in W.other_company_names([priv], words)}
    assert names["kvist booking"][4] is False
    assert names["lyng drift"][4] is True


def _fb_site(fb, footer_org=True, extra=""):
    org = f"Org.nr {OURS}" if footer_org else ""
    body = (f'<h1>Fjellbekk Trevare AS</h1><footer>Fjellbekk Trevare AS {org} · Lyngveien 12B, 9999 Testby '
            f'<a href="{fb}"></a>{extra}</footer>')
    return FakeFetcher({"https://fjellbekk.no/": (200, html(body))})


def test_bare_facebook_page_id_on_an_orgnr_page_is_kept():
    for fb, value in (("https://facebook.com/100087098506924", "facebook.com/100087098506924"),
                      ("https://www.facebook.com/R%C3%B8rlegger-100656395325438", None)):
        ctx = ctx_for(hjemmeside="fjellbekk.no")
        run(_fb_site(fb), ctx)
        got = [str(c.value) for c in ctx.claims if c.field == "social_profile"]
        assert len(got) == 1 and (value is None or got == [value])


def test_bare_facebook_page_id_needs_our_orgnr_and_an_unmixed_block():
    # proven by address and name only: the page does not print our org number
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_fb_site("https://facebook.com/100087098506924", footer_org=False), ctx)
    assert not ctx.claims_for("social_profile")
    # beside another brand's profile (a chain's or agency's link block)
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_fb_site("https://facebook.com/100087098506924", extra='<a href="https://www.instagram.com/kvistgruppen/"></a>'), ctx)
    assert not ctx.claims_for("social_profile")
    # placeholder ids
    for fb in ("https://facebook.com/123456789", "https://facebook.com/111111111"):
        ctx = ctx_for(hjemmeside="fjellbekk.no")
        run(_fb_site(fb), ctx)
        assert not ctx.claims_for("social_profile")


def test_group_handle_is_ours_when_we_are_the_registry_group_parent():
    """Kitron shape: the listed parent links facebook.com/<name>group from its own org-number-proven site."""
    body = (f'<footer>Fjellbekk Trevare ASA · Org.nr {OURS}'
            '<a href="https://www.facebook.com/fjellbekkgroup/"></a></footer>')
    tree = json.dumps({"organisasjonsnummer": OURS, "children": [{"organisasjonsnummer": OTHER, "children": []}]})
    pages = {f"https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{OURS}": (200, tree, "application/json")}
    ctx = ctx_for(navn="FJELLBEKK TREVARE ASA", hjemmeside="fjellbekk.no")
    run(_site(pages, body=body), ctx)
    assert [str(c.value) for c in ctx.claims if c.field == "social_profile"] == ["facebook.com/fjellbekkgroup"]
    # not a group parent: the same handle is another company's
    W._GROUP_CACHE.clear()
    W._GROUP_TREE.clear()
    ctx = ctx_for(navn="FJELLBEKK TREVARE ASA", hjemmeside="fjellbekk.no")
    run(_site(body=body), ctx)
    assert not [c for c in ctx.claims if c.field == "social_profile" and c.availability == "available"]


def test_foreign_branch_under_our_name_still_collides_inside_our_name():
    """Lundbeck shape: 'FJELLBEKK' is also the NUF of a foreign parent; the site writing 'Fjellbekk AS' is no proof."""
    body = '<h1>Fjellbekk</h1><footer>Fjellbekk AS · <a href="mailto:post@fjellbekk.no">post@fjellbekk.no</a></footer>'
    pages = {"https://fjellbekk.no/": (200, html(body, title="Fjellbekk"))}
    pages.update(_brreg("enheter", "fjellbekk", [{"organisasjonsnummer": SISTER, "navn": "FJELLBEKK",
                                                   "organisasjonsform": {"kode": "NUF"}}]))
    claims = run(FakeFetcher(pages), ctx_for(navn="FJELLBEKK AS", hjemmeside="fjellbekk.no"))
    assert claims.get("site_contact_email") is None or claims["site_contact_email"].availability != "available"


def test_redirect_to_a_locale_page_of_the_registry_email_domain_stays_cross():
    body = '<h1>Fjellbekk Trevare AS</h1><footer><a href="mailto:post@fjb.no">post@fjb.no</a></footer>'
    f = FakeFetcher({"https://fjb.no/no": (200, html(body))}, redirects={"https://fjellbekk.no/": "https://fjb.no/no"})
    claims = run(f, ctx_for(hjemmeside="fjellbekk.no", epostadresse="post@fjb.no"))
    assert claims["official_website"].availability != "available"


# ---------------------------------------------------------------- v2: proof spans hold the matched value
def _assembled_spans(ctx, claim):
    """Spans as the pipeline publishes them (exact-span repair at assembly, with each evidence's core)."""
    from kilde.spans import repair
    out = []
    for ev in ctx.evidence:
        if ev.id in claim.evidence_ids:
            snap = ctx.snapshots[ev.id]
            span, _, exact = repair(snap.content.decode("utf-8", "replace"), ev.claim_span, snap.content_type,
                                    ctx.anchors.get(ev.id), ctx.cores.get(ev.id))
            out.append((span, exact, ctx.cores.get(ev.id)))
    return out


def test_phone_proof_span_contains_the_number_not_nearby_navigation():
    pad = "y" * 320  # long attributes on both sides: no multi-word window around the number survives
    body = (f'<footer><a class="skip-link" href="#main">Skip to content</a><div class="bar" data-x="{pad}">'
            f'<span>41000001</span></div><div class="brand" data-x="{pad}">Fjellbekk Trevare AS</div></footer>')
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    ctx = ctx_for(telefon="41000001")
    site = run(f, ctx)["official_website"]
    assert site.match["method"] == "phone_and_name"
    got = _assembled_spans(ctx, site)
    assert [c for _, _, c in got] == ["41000001", "Fjellbekk Trevare"]
    for span, exact, core in got:
        assert exact and core.lower() in span.lower(), span


def test_name_and_address_proof_spans_contain_name_and_address():
    pad = "z" * 320
    body = (f'<p>Hopp til innhold</p><nav data-x="{pad}">Meny Hjem Om oss</nav><h2>Fjellbekk Trevare AS</h2>'
            f'<p data-x="{pad}">Lyngveien 12 B,</p><p>9999 Testby</p>')
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body, title="Velkommen"))})
    ctx = ctx_for()
    site = run(f, ctx)["official_website"]
    assert site.match["method"] == "address_and_name"
    from kilde.spans import contains_core
    for span, exact, core in _assembled_spans(ctx, site):
        assert core and exact and contains_core(span, core), (span, core)
    cores = [c for _, _, c in _assembled_spans(ctx, site)]
    assert "9999" in cores[0] and "Lyngveien" in cores[0]


def test_contains_core_ignores_markup_spacing_case_and_entities():
    from kilde.spans import contains_core
    assert contains_core('<span>+47&nbsp;41 00</span> <b>00 01</b>', "+47 41 00 00 01")
    assert contains_core("FJELLBEKK&#160;Trevare AS", "Fjellbekk Trevare")
    assert not contains_core("Skip to content", "41000001")
    assert not contains_core("Fjellbekk", "Fjellbekk Trevare")
    # a page saved in a legacy charset: only the ASCII part of a non-ASCII core can be quoted
    assert contains_core("rlegger Testby", "Rørlegger Testby", legacy=True)
    assert not contains_core("rlegger Testby", "Rørlegger Testby")


def test_repair_with_core_never_quotes_a_window_without_it():
    from kilde.spans import repair
    pad = "q" * 320
    raw = (f'<html><body><a>Skip to content</a><div data-x="{pad}"><span>41000001</span></div>'
           f'<div data-x="{pad}">post@x.no</div></body></html>')
    window = "Skip to content 41000001 post@x.no"
    old, _, _ = repair(raw, window, "text/html")
    assert "41000001" not in old  # the failure mode: a shrunken window without the value
    span, _, exact = repair(raw, window, "text/html", None, "41000001")
    assert exact and span == "41000001"
    # a core the page does not hold verbatim is described, never replaced by unrelated text
    span, _, exact = repair(raw, window, "text/html", None, "Fjellbekk Trevare")
    assert span == "Fjellbekk Trevare" and not exact


def test_crawl_seeds_ordered_by_strength_after_registry_and_email(monkeypatch):
    monkeypatch.setattr(W, "_SEEDS", {OURS: [{"domain": "a-signals.no", "sources": ["crawl_signals"]},
                                             {"domain": "b-osm.no", "sources": ["osm_phone"]},
                                             {"domain": "c-phone.no", "sources": ["crawl_phone_email"]},
                                             {"domain": "d-weak.no", "sources": ["commoncrawl_domain"]},
                                             {"domain": "e-orgnr.no", "sources": ["crawl_orgnr", "crawl_signals"]}]})
    cands = W.candidates(ctx_for(navn="FJELLBEKK TREVARE AS", hjemmeside="fjellbekk.no",
                                 epostadresse="post@fjellbekk-post.no"))
    assert [d for d, _s, _u in cands[:2]] == ["fjellbekk.no", "fjellbekk-post.no"]  # declared sites first
    order = [d for d, s, _ in cands if s.startswith("seed:")]
    assert order == ["e-orgnr.no", "c-phone.no", "a-signals.no", "b-osm.no", "d-weak.no"]


def test_crawl_seed_without_proof_on_the_live_site_publishes_nothing(monkeypatch):
    monkeypatch.setattr(W, "_SEEDS", {OURS: [{"domain": "fjellbekk-sag.no", "sources": ["crawl_orgnr", "crawl_signals"]}]})
    ctx = ctx_for()
    run(FakeFetcher({"https://fjellbekk-sag.no/": (200, html("<h1>Fjellbekk Trevare</h1><p>Velkommen</p>"))}), ctx)
    assert not ctx.claims_for("official_website")  # the seed's crawl-time evidence is not re-used


def test_legacy_charset_value_with_a_non_breaking_space_is_quoted_in_exact_pieces():
    from kilde.spans import repair
    raw_bytes = '<p><span>Lyngveien</span> 12\xa0 </span><span>9999 Testby</span></p>'.encode("latin-1")
    raw = raw_bytes.decode("utf-8", "replace")
    span, extra, exact = repair(raw, "Lyngveien 12 9999 Testby", "text/html", None, "Lyngveien 12 9999")
    assert exact and all(x.encode() in raw_bytes for x in [span] + extra)
    joined = " ".join([span] + extra)
    assert "Lyngveien" in joined and "9999" in joined


# ---------------------------------------------------------------- exact legal name, unique in the registry
def _legal_search(rows_main=(), rows_sub=(), query="fjellbekk%20trevare"):
    out = {}
    for path, rows in (("enheter", rows_main), ("underenheter", rows_sub)):
        out[f"https://data.brreg.no/enhetsregisteret/api/{path}?navn={query}&size=50"] = (
            200, json.dumps({"_embedded": {path: list(rows)}}), "application/json")
    return out


LEGAL_SITE = html("<h1>Snekkerverksted</h1><p>Vi lager kjøkken på mål.</p>"
                  "<footer>© 2025 Fjellbekk Trevare AS · 9999 Testby</footer>", title="Fjellbekk Trevare AS | Kjøkken")


def _legal_run(body=LEGAL_SITE, host="https://fjellbekktrevare.no/", search=None, **rec):
    pages = {host: (200, body)}
    pages.update(_legal_search() if search is None else search)
    ctx = ctx_for(**rec)
    run(FakeFetcher(pages), ctx)
    return ctx


def test_exact_legal_name_unique_in_registry_with_post_town_proves_the_site():
    ctx = _legal_run()
    site = ctx.claims_for("official_website")
    assert site and site[0].value == "fjellbekktrevare.no" and site[0].match["method"] == "legal_name_unique"
    spans = [e.claim_span for e in ctx.evidence if e.id in site[0].evidence_ids]
    assert any("Fjellbekk Trevare AS" in s for s in spans) and any("Testby" in s for s in spans)


def test_legal_name_proof_rejected_when_another_entity_carries_the_whole_name():
    for rows_main, rows_sub in (
            ([{"organisasjonsnummer": OTHER, "navn": "FJELLBEKK TREVARE HOLDING AS"}], []),   # holding or eiendom pair
            ([{"organisasjonsnummer": OTHER, "navn": "FJELLBEKK TREVARE AS"}], []),           # same name, another town
            ([{"organisasjonsnummer": OTHER, "navn": "FJELLBEKK-TREVARE AS"}], []),           # hyphen variant
            ([{"organisasjonsnummer": OTHER, "navn": "FJELLBEKK TREVARE", "slettedato": "2019-01-01"}], []),  # deleted
            ([], [{"organisasjonsnummer": "999000061", "navn": "BYGG AS AVD FJELLBEKK TREVARE",
                   "overordnetEnhet": THIRD}])):                                          # another company's workplace
        W._SEARCH.clear()
        W._NAME_SEARCH.clear()
        W._SEARCH.clear()
        ctx = _legal_run(search=_legal_search(rows_main, rows_sub))
        assert not ctx.claims_for("official_website"), rows_main or rows_sub


def test_legal_name_proof_ignores_our_own_workplaces_and_shorter_names():
    ctx = _legal_run(search=_legal_search([{"organisasjonsnummer": OTHER, "navn": "FJELLBEKK AS"}],
                                          [{"organisasjonsnummer": "999000061", "navn": "FJELLBEKK TREVARE AS AVD NORD",
                                            "overordnetEnhet": OURS}]))
    assert ctx.claims_for("official_website")[0].match["method"] == "legal_name_unique"


def test_failed_registry_search_marks_the_website_step_failed_not_ambiguous(monkeypatch):
    """Builderr compares two runs: a search that cannot be read must not decide the outcome either way."""
    monkeypatch.setattr(W, "SEARCH_BACKOFF", (0.0, 0.0))
    pages = {"https://fjellbekktrevare.no/": (200, LEGAL_SITE)}
    f = FakeFetcher(pages)
    f.brreg_down = True
    ctx = ctx_for()
    claims = run(f, ctx)
    assert not ctx.claims_for("official_website")
    assert claims["official_website"].availability == "failed"
    assert claims["official_website"].note.startswith("transient: Brreg name search failed")
    assert ctx.module_states["website"] == "timeout"  # the pipeline's retry pass runs it again
    searches = [u for u in f.calls if "navn=" in u]
    assert len(searches) == W.SEARCH_TRIES  # bounded retries, then stop


def test_failed_search_in_the_collision_check_also_fails_the_step(monkeypatch):
    monkeypatch.setattr(W, "SEARCH_BACKOFF", (0.0, 0.0))
    body = "<footer>Fjellbekk Trevare AS, Lyngveien 12 B, 9999 Testby</footer>"
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    f.brreg_down = True
    ctx = ctx_for()
    claims = run(f, ctx)
    assert claims["official_website"].availability == "failed" and ctx.module_states["website"] == "timeout"


def test_namesake_found_only_on_a_later_page_of_the_phrase_search():
    """The ranked search shows 50 rows; the phrase search is read to its last page, so a namesake cannot hide."""
    base = "https://data.brreg.no/enhetsregisteret/api/enheter?navn=fjellbekk%20trevare&navnMetodeForSoek=FORTLOEPENDE&size=100"
    filler = [{"organisasjonsnummer": f"9{i:08d}", "navn": f"FJELLBEKK TREVARESENTER {i}"} for i in range(100)]
    search = {base + "&page=0": (200, json.dumps({"_embedded": {"enheter": filler}, "page": {"totalPages": 2}}), "application/json"),
              base + "&page=1": (200, json.dumps({"_embedded": {"enheter": [
                  {"organisasjonsnummer": OTHER, "navn": "FJELLBEKK TREVARE HOLDING AS"}]}, "page": {"totalPages": 2}}),
                  "application/json")}
    ctx = _legal_run(search=search)
    assert not ctx.claims_for("official_website")


def test_too_many_phrase_matches_count_as_a_namesake(monkeypatch):
    monkeypatch.setattr(W, "PHRASE_PAGES", 1)
    base = "https://data.brreg.no/enhetsregisteret/api/enheter?navn=fjellbekk%20trevare&navnMetodeForSoek=FORTLOEPENDE&size=100"
    search = {base + "&page=0": (200, json.dumps({"_embedded": {"enheter": []}, "page": {"totalPages": 3}}), "application/json")}
    ctx = _legal_run(search=search)
    assert not ctx.claims_for("official_website")


def test_collision_search_uses_the_registered_spelling_with_ae_o_a():
    """'FLØTRE' is not found by a search for 'flotre': both spellings are searched."""
    body = "<footer>Fløtre Bygg · Lyngveien 12 B, 9999 Testby</footer>"
    q = "https://data.brreg.no/enhetsregisteret/api/enheter?navn=fl%C3%B8tre%20bygg&size=50"
    pages = {"https://flotrebygg.no/": (200, html(body, title="Fløtre Bygg")),
             q: (200, json.dumps({"_embedded": {"enheter": [{"organisasjonsnummer": OTHER, "navn": "FLØTRE BYGG SA"}]}}),
                 "application/json")}
    ctx = ctx_for(navn="FLØTRE BYGG AS")
    run(FakeFetcher(pages), ctx)
    assert not ctx.claims_for("official_website")
    W._SEARCH.clear()
    pages.pop(q)
    ctx = ctx_for(navn="FLØTRE BYGG AS")
    run(FakeFetcher(pages), ctx)
    assert ctx.claims_for("official_website")[0].match["method"] == "address_and_name"


def test_registry_declared_site_naming_a_related_legal_name_is_proven():
    """all10.no shape: the registry declares the site; it also names 'Fjellbekk Trevare Bemanning AS' in text."""
    body = ("<h1>Fjellbekk Trevare</h1><p>Ny rekrutterer i Fjellbekk Trevare Bemanning AS.</p>"
            "<footer>Fjellbekk Trevare</footer>")
    ctx = ctx_for(hjemmeside="fjellbekktrevare.no")
    run(FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))}), ctx)
    site = ctx.claims_for("official_website")
    assert site and site[0].match["method"] == "registry_website_and_name"
    # not when the related name runs the site, and never for a guessed domain
    owned = body.replace("<footer>Fjellbekk Trevare</footer>",
                         "<footer>© 2026 Fjellbekk Trevare Bemanning AS</footer>")
    ctx = ctx_for(hjemmeside="fjellbekktrevare.no")
    run(FakeFetcher({"https://fjellbekktrevare.no/": (200, html(owned))}), ctx)
    assert not ctx.claims_for("official_website")
    ctx = ctx_for(telefon="41000001")
    run(FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body.replace("</footer>", " · Tlf 41 00 00 01</footer>")))}), ctx)
    assert not ctx.claims_for("official_website")


def test_legal_name_proof_needs_the_town_or_a_role_holder():
    body = html("<h1>Snekkerverksted</h1><footer>© 2025 Fjellbekk Trevare AS · Daglig leder Kari Nordmann</footer>")
    ctx = _legal_run(body=body)
    assert not ctx.claims_for("official_website")
    W._SEARCH.clear()
    pages = {"https://fjellbekktrevare.no/": (200, body)}
    pages.update(_legal_search())
    ctx = ctx_for()
    ev = ctx.add_evidence(None, source_url="https://data.brreg.no/enhetsregisteret/api/enheter/999000002/roller",
                          source_class="official_registry", span="x", method="t")
    ctx.add_claim("ceo", "leadership", {"role": "Daglig leder", "role_code": "DAGL", "name": "Kari Anne Nordmann"}, ev)
    run(FakeFetcher(pages), ctx)
    site = ctx.claims_for("official_website")
    assert site and site[0].match["method"] == "legal_name_unique"
    assert any("Kari Nordmann" in e.claim_span for e in ctx.evidence if e.id in site[0].evidence_ids)


def test_short_town_name_counts_only_after_a_post_code():
    ctx = _legal_run(body=html("<footer>Fjellbekk Trevare AS. Vi har kunder i Moss.</footer>"),
                     forretningsadresse={"adresse": ["Lyngveien 12B"], "postnummer": "1599", "poststed": "MOSS"})
    assert not ctx.claims_for("official_website")
    W._SEARCH.clear()
    ctx = _legal_run(body=html("<footer>Fjellbekk Trevare AS, Postboks 4, 1501 Moss</footer>"),
                     forretningsadresse={"adresse": ["Lyngveien 12B"], "postnummer": "1599", "poststed": "MOSS"})
    assert ctx.claims_for("official_website")[0].match["method"] == "legal_name_unique"


def test_legal_name_proof_needs_the_whole_name_as_the_domain():
    for host in ("https://fjellbekk.no/", "https://fjellbekktrevare.se/", "https://trevare.fjellbekk.no/"):
        W._SEARCH.clear()
        ctx = _legal_run(host=host)
        assert not ctx.claims_for("official_website"), host


def test_legal_name_proof_needs_the_legal_form_in_capitals():
    for footer in ("Kjøkken fra Fjellbekk Trevare as part of our range. 9999 Testby",   # English 'as'
                   "Fjellbekk Trevare A/S · 9999 Testby",                               # Danish form
                   "Fjellbekk Trevare · 9999 Testby"):                                  # trade name only
        W._SEARCH.clear()
        ctx = _legal_run(body=html(f"<footer>{footer}</footer>"))
        assert not ctx.claims_for("official_website"), footer


def test_legal_name_proof_rejects_sister_names_and_other_operators():
    for footer in ("Fjellbekk Trevare AS er eid av Fjellbekk Gruppen AS · 9999 Testby",
                   "Fjellbekk Trevare AS · 9999 Testby · Nettstedet eies av Testby Bygg AS"):
        W._SEARCH.clear()
        ctx = _legal_run(body=html(f"<footer>{footer}</footer>"))
        assert not ctx.claims_for("official_website"), footer
    W._SEARCH.clear()
    ctx = _legal_run(body=html("<footer>© Fjellbekk Trevare AS · 9999 Testby · Nettside levert av Webby AS</footer>"))
    assert ctx.claims_for("official_website")[0].match["method"] == "legal_name_unique"  # an agency credit is fine


def test_legal_name_proof_only_for_limited_companies():
    for form, name in (("NUF", "FJELLBEKK TREVARE AS"), ("SAM", "FJELLBEKK TREVARE AS"), ("ENK", "FJELLBEKK TREVARE AS")):
        W._SEARCH.clear()
        ctx = _legal_run(navn=name, organisasjonsform={"kode": form})
        assert not ctx.claims_for("official_website"), form


# ---------------------------------------------------------------- contacts attributable to us
def _contact_site(cards: str):
    body = (f"<h1>Fjellbekk Trevare</h1><ul>{cards}</ul>"
            f"<footer>Fjellbekk Trevare AS · Org.nr {OURS} · <a href='mailto:post@fjellbekk.no'>post@fjellbekk.no</a></footer>")
    ctx = ctx_for(hjemmeside="fjellbekk.no", telefon="41000001")
    claims = run(FakeFetcher({"https://fjellbekk.no/": (200, html(body))}), ctx)
    return claims, ctx


def test_contact_beside_another_companys_name_is_withheld():
    """Atico shape: a staff card 'Nestleder Fjellbekk Bygg AS, tlf 95 20 88 25' on the company's own site."""
    claims, ctx = _contact_site(
        "<li>Kari Nordmann, Nestleder Fjellbekk Bygg AS, tlf 952 08 825, "
        "<a href='mailto:kari@fjellbekk.no'>kari@fjellbekk.no</a></li>"
        "<li>Daglig leder Fjellbekk Trevare AS, Ola Nordmann, tlf 930 00 001</li>")
    phones = [c.value for c in ctx.claims if c.field == "site_contact_phone" and c.availability == "available"]
    emails = [c.value for c in ctx.claims if c.field == "site_contact_email" and c.availability == "available"]
    assert "+4795208825" not in phones and "kari@fjellbekk.no" not in emails
    assert "post@fjellbekk.no" in emails


def test_registry_phone_is_kept_even_beside_another_name():
    claims, ctx = _contact_site("<li>Sentralbord for Fjellbekk Bygg AS og oss: tlf 410 00 001</li>")
    phones = [c.value for c in ctx.claims if c.field == "site_contact_phone" and c.availability == "available"]
    assert "+4741000001" in phones


def test_contact_card_helper_ignores_our_own_name_after_a_role():
    assert _card_other("Daglig leder Fjellbekk Trevare AS, tlf 930 00 001") is None
    assert _card_other("Nestleder Fjellbekk Bygg AS, tlf 952 08 825") == "Nestleder Fjellbekk Bygg AS"


def _card_other(text):
    return W._card_names_other_company(text, ["fjellbekk", "trevare"])


def test_failed_sister_search_fails_the_step(monkeypatch):
    monkeypatch.setattr(W, "SEARCH_BACKOFF", (0.0, 0.0))
    body = f"<h1>Fjellbekk Trevare</h1><footer>Fjellbekk Trevare AS · Org.nr {OURS} · Fjellbekk Bygg AS</footer>"
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body))})
    f.brreg_down = True
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(f, ctx)
    assert claims["official_website"].availability == "failed" and ctx.module_states["website"] == "timeout"


# ---------------------------------------------------------------- round 14: name as words, shared addresses
def _row(org, name, street="Lyngveien 12B", pc="9999", **extra):
    return {"organisasjonsnummer": org, "navn": name,
            "forretningsadresse": {"adresse": [street], "postnummer": pc}, **extra}


def _listing(path, field_, rows, pc="9999", pages=1):
    return {f"https://data.brreg.no/enhetsregisteret/api/{path}?{field_}.postnummer={pc}&size=500&page=0":
            (200, json.dumps({"_embedded": {path: rows}, "page": {"totalPages": pages}}), "application/json")}


def test_name_inside_an_email_url_or_handle_is_not_a_name():
    t = "Kontakt: fjellbekktrevare@gmail.com, www.fjellbekktrevare.no/kontakt, @fjellbekktrevare og fjellbekktrevare"
    rx = W.name_regex(["fjellbekk", "trevare"])
    assert not W._visible(rx, t)
    assert W._visible(rx, t + " · Fjellbekk Trevare, Testby")
    assert W._visible(rx, "Velkommen til FjellbekkTrevare")  # a brand written together, capitalised


def test_al_rahma_shape_email_and_shared_address_is_not_proof():
    """Round 14: the legal name only inside the contact email, and the registered address, which a different
    organisation at the same address (the site's real operator) also has."""
    body = ("<h2>Fjellbekk Moské Testby</h2><p>Om oss: Fjellbekk Islamsk Senter, etablert i 2010.</p>"
            "<p>EMAIL fjellbekktrevare@gmail.com ADDRESS Lyngveien 12B, 9999 Testby</p>")
    pages = {"https://fjellbekktrevare.no/": (200, html(body, title="fjellbekktrevare.no"))}
    pages.update(_listing("enheter", "forretningsadresse", [_row(OTHER, "FJELLBEKK ISLAMSK SENTER TESTBY")]))
    ctx = ctx_for(navn="FJELLBEKK TREVARE STIFTELSEN", organisasjonsform={"kode": "STI"})
    run(FakeFetcher(pages), ctx)
    assert not ctx.claims_for("official_website")


def test_colocated_namesake_makes_any_name_proof_ambiguous():
    body = "<h1>Fjellbekk Trevare</h1><footer>Fjellbekk Trevare AS · Tlf 41 00 00 01</footer>"
    q = "https://data.brreg.no/enhetsregisteret/api/enheter?navn=fjellbekk&forretningsadresse.postnummer=9999&size=50"
    pages = {"https://fjellbekktrevare.no/": (200, html(body)),
             q: (200, json.dumps({"_embedded": {"enheter": [_row(OTHER, "FJELLBEKK KULTURSENTER")]}}), "application/json")}
    ctx = ctx_for(telefon="41000001")
    run(FakeFetcher(pages), ctx)
    assert not ctx.claims_for("official_website")
    assert "at our address shares our name" in ctx.facts["website_candidates"][0]["detail"]


def test_address_proof_needs_an_address_nobody_else_has():
    body = "<footer>Fjellbekk Trevare AS, Lyngveien 12 B, 9999 Testby</footer>"
    for rows, proven in (([], True), ([_row(OTHER, "TESTBY REVISJON AS")], True),   # shown as 'Fjellbekk Trevare AS'
                         ([_row(OTHER, "TESTBY REVISJON AS", street="Lyngveien 14")], True)):
        W._SEARCH.clear()
        pages = {"https://fjellbekktrevare.no/": (200, html(body))}
        pages.update(_listing("enheter", "forretningsadresse", rows))
        ctx = ctx_for()
        run(FakeFetcher(pages), ctx)
        assert bool(ctx.claims_for("official_website")) is proven, rows
    # a co-tenant does not touch a proof by our org number
    W._SEARCH.clear()
    pages = {"https://fjellbekktrevare.no/": (200, html(body.replace("</footer>", f" · Org.nr {OURS}</footer>")))}
    pages.update(_listing("enheter", "forretningsadresse", [_row(OTHER, "TESTBY REVISJON AS")]))
    ctx = ctx_for()
    run(FakeFetcher(pages), ctx)
    assert ctx.claims_for("official_website")[0].match["method"] == "orgnr_on_page"


def test_address_proof_fails_closed_on_a_crowded_postcode(monkeypatch):
    monkeypatch.setattr(W, "ADDRESS_PAGES", 1)
    # (with the legal name and its form on our own domain, a crowded postcode would not matter)
    pages = {"https://fjellbekktrevare.no/": (200, html("<footer>Fjellbekk Trevare, Lyngveien 12 B, 9999 Testby</footer>"))}
    pages.update(_listing("enheter", "forretningsadresse", [], pages=4))
    ctx = ctx_for()
    run(FakeFetcher(pages), ctx)
    assert not ctx.claims_for("official_website")


def test_heading_naming_another_registered_namesake_is_ambiguous():
    body = "<h1>Fjellbekk Senter</h1><footer>Fjellbekk Trevare AS · Tlf 41 00 00 01</footer>"
    pages = {"https://fjellbekktrevare.no/": (200, html(body))}
    pages.update(_brreg("enheter", "fjellbekk%20trevare", [_row(OTHER, "FJELLBEKK SENTER AS", street="Annen vei 1", pc="1111")]))
    ctx = ctx_for(telefon="41000001")
    run(FakeFetcher(pages), ctx)
    assert not ctx.claims_for("official_website")
    assert "title or heading" in ctx.facts["website_candidates"][0]["detail"]


# ---------------------------------------------------------------- round 14 part 1
def test_platform_names_in_trademark_notices_do_not_present_the_site():
    """Fet Legesenter shape: app-store badges and 'Google Play is a trademark of Google Inc.' in the footer."""
    body = ("<h1>Fjellbekk Trevare</h1><div class='info-text'><h4>Kontakt oss</h4>Fjellbekk Trevare AS<br>Postboks 4<br>"
            "9999 TESTBY<br><b>Telefon</b>: <a href='tel:+4741000001'>41 00 00 01</a></div>"
            "<footer>Google Play is a trademark of Google Inc. Apple and the Apple logo are trademarks of Apple Inc.</footer>")
    ctx = ctx_for(telefon="41000001")
    claims = run(FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))}), ctx)
    site = ctx.claims_for("official_website")
    assert site and site[0].match["method"] == "phone_and_name"  # the phone sits in a card headed 'Kontakt oss'
    assert "google" not in json.dumps(site[0].match).lower()
    assert "related_website" not in claims


def test_registry_email_domain_with_our_name_and_the_name_on_the_site_is_proof():
    body = "<h1>Fjellbekk Trevare</h1><p>Kontakt: post@fjellbekktrevare.no</p>"
    pages = {"https://fjellbekktrevare.no/": (200, html(body))}
    ctx = ctx_for(epostadresse="kari@fjellbekktrevare.no")
    run(FakeFetcher(pages), ctx)
    assert ctx.claims_for("official_website")[0].match["method"] == "registry_email_domain_and_name"
    # a mailbox domain that does not carry our name (a manager's or accountant's) proves nothing
    pages = {"https://testbyforvaltning.no/": (200, html(body))}
    ctx = ctx_for(epostadresse="kari@testbyforvaltning.no")
    run(FakeFetcher(pages), ctx)
    assert not ctx.claims_for("official_website")


def test_contact_at_our_group_parents_domain_does_not_share_the_site():
    """Sørlandschips shape: a brand site whose press contact is at the group parent's domain."""
    body = (f"<h1>Fjellbekk Trevare</h1><footer>Org.nr {OURS} · Presse: "
            "<a href='mailto:kari@nordtopp.no'>kari@nordtopp.no</a></footer>")
    tree = json.dumps({"navn": "NORDTOPP INVEST AS", "organisasjonsnummer": OTHER,
                       "children": [{"navn": "FJELLBEKK TREVARE AS", "organisasjonsnummer": OURS}]})
    pages = {"https://fjellbekktrevare.no/": (200, html(body)),
             f"https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{OURS}": (200, tree, "application/json")}
    ctx = ctx_for(erIKonsern=True)
    run(FakeFetcher(pages), ctx)
    assert ctx.claims_for("official_website")
    assert "nordtopp" not in json.dumps(ctx.claims_for("official_website")[0].match)
    # the same contact domain outside our group still shares the site
    W._GROUP_TREE.clear()
    W._SEARCH.clear()
    ctx = ctx_for()
    run(FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))}), ctx)
    assert "nordtopp" in json.dumps([c.match for c in ctx.claims if c.field in ("official_website", "related_website")])

def _card(slug: str, title: str, iso: str, shown: str) -> str:
    """One item card shaped like a CMS article list: the <a> wraps image, heading, summary and date."""
    return (f'<a href="/aktuelt/{slug}" class="flow list-item"><img src="/img/{slug}.jpg" alt="">'
            f'<div class="kicker"><div class="kicker--text"></div><h2 class="heading-md">{title}</h2></div>'
            f'<div class="article-summary">Kort ingress om {title.lower()} fra verkstedet.</div>'
            f'<div class="avatar"><div class="avatar--name">Ola Testesen</div><div class="avatar--divider">|</div>'
            f'<time class="avatar--date" datetime="{iso}">{shown}</time></div></a>')


def test_card_listing_ties_title_date_and_link_to_the_same_item_and_reads_page_one():
    """Regression: on a card list the date and title of one card were paired with the next card's link, and
    only page 2 (two cards) yielded items while the newer posts on page 1 were missed."""
    body = f'<a href="/aktuelt">Aktuelt</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    nav = ('<nav aria-label="Søkeresultat navigasjon" class="pagination"><ul>'
           '<li><a rel="prev" href="/aktuelt?page={p}"><span></span></a></li>'
           '<li><a rel="next" href="/aktuelt?page={n}"><span></span></a></li></ul></nav>')
    page1 = html('<main><h1>Aktuelt</h1><div class="article-list-items">'
                 + _card("ny-selger", "Ny selger i teamet vårt", "2026-09-09T16:30:25.444893748Z", "9. september 2026")
                 + _card("sommerapent", "Sommeråpent i butikken", "2026-06-03T11:40:19.7892Z", "3. juni 2026")
                 + _card("ny-maskin", "Ny maskin på verkstedet", "2026-03-18T06:07:10.7479Z", "18. mars 2026")
                 + '</div>' + nav.format(p=1, n=2) + '</main>')
    page2 = html('<main><div class="article-list-items">'
                 + _card("sterk-partner", "Sterk partner for fremtiden", "2025-02-25T07:36:46.1099Z", "25. februar 2025")
                 + _card("presisjon", "Presisjon som grunnlag", "2025-03-05T19:54:20.4817Z", "5. mars 2025")
                 + '</div>' + nav.format(p=1, n=3) + '</main>')
    f = _site({"https://fjellbekk.no/aktuelt": (200, page1), "https://fjellbekk.no/aktuelt?page=2": (200, page2)}, body=body)
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    news = [c for c in ctx.claims if c.field == "dated_news"]
    got = {c.match["detail"].split(" ")[0].rsplit("/", 1)[-1]: c.value for c in news}
    # the card's own <time datetime> (fractions dropped, offset kept)
    assert got == {"ny-selger": "Ny selger i teamet vårt (2026-09-09T16:30:25+00:00)",
                   "sommerapent": "Sommeråpent i butikken (2026-06-03T11:40:19+00:00)",
                   "ny-maskin": "Ny maskin på verkstedet (2026-03-18T06:07:10+00:00)",
                   "presisjon": "Presisjon som grunnlag (2025-03-05T19:54:20+00:00)",
                   "sterk-partner": "Sterk partner for fremtiden (2025-02-25T07:36:46+00:00)"}
    assert f.calls.index("https://fjellbekk.no/aktuelt") < f.calls.index("https://fjellbekk.no/aktuelt?page=2")
    for c in news:  # the quoted title and date are the claimed item's own
        spans = " ".join(e.claim_span for e in ctx.evidence if e.id in c.evidence_ids)
        assert c.value.rsplit(" (", 1)[0] in spans or c.value.rsplit(" (", 1)[1][:19] in spans


def test_list_without_item_containers_drops_dates_it_cannot_tie_to_one_link():
    """Two headed links share one block with a single date: the date could be either post's, so neither gets it."""
    body = f'<a href="/nyheter">Nyheter</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    listing = html('<main><div class="list"><h3><a href="/nyheter/a">Første sak om verkstedet</a></h3>'
                   '<h3><a href="/nyheter/b">Andre sak om verkstedet</a></h3><time datetime="2026-05-02">2. mai 2026</time>'
                   '</div></main>')
    page = W.parse_page(Snapshot(url="https://fjellbekk.no/nyheter", final_url="https://fjellbekk.no/nyheter", status=200,
                                 retrieved_at=utc_now(), content=listing.encode(), content_type="text/html"))
    assert W.news_items(page, "fjellbekk.no", W.Scope(OURS, ["fjellbekk"])) == []


def _dated(rows):
    return [(f"https://fjellbekk.no{p}", d, d) for p, d in rows]


def test_bulk_lastmod_days_and_chain_sections_are_not_page_updates():
    # chain platform: 60 service pages pushed on one day plus a supplier section, a few own pages on other days
    chain = ([(f"/tjenester/t{i}", "2026-10-05") for i in range(60)] + [(f"/tjenester/n{i}", "2026-10-12") for i in range(3)]
             + [(f"/leverandorer/l{i}", "2026-10-05") for i in range(8)] + [("/leverandorer", "2026-10-12")]
             + [(f"/produkt/p{i}", f"2026-0{1 + i % 8}-1{i % 9}") for i in range(80)] + [("/om-oss", "2026-09-01")])
    kept, note = W.drop_bulk_lastmods(_dated(chain))
    paths = {urlsplit(u).path for u, _, _ in kept}
    assert note and "2026-10-05" in note and "/tjenester" in note
    assert not any(p.startswith(("/tjenester", "/leverandorer")) for p in paths)  # section and its index page
    assert "/om-oss" in paths and "/produkt/p3" in paths
    # mostly stamped in bulk: nothing from the sitemap is a page update
    mostly = [(f"/tjenester/t{i}", "2026-10-05") for i in range(40)] + [(f"/x{i}", f"2026-0{1 + i}-01") for i in range(5)]
    assert W.drop_bulk_lastmods(_dated(mostly))[0] == []
    small_refresh = [(f"/side{i}", "2026-09-28") for i in range(12)] + [("/en/a", "2026-10-01"), ("/b", "2026-07-02")]
    assert W.drop_bulk_lastmods(_dated(small_refresh))[0] == []


def test_ordinary_and_large_active_sitemaps_keep_their_dates():
    spread = [(f"/p{i}", f"2026-{1 + i % 9:02d}-{1 + i % 27:02d}") for i in range(40)]
    assert W.drop_bulk_lastmods(_dated(spread)) == (_dated(spread), None)
    # a 1,000-page site touching 80 pages (8%) on its busiest day is an active site, not a platform push
    big = [(f"/kunnskap/a{i}", "2026-05-08") for i in range(80)] + \
        [(f"/kunnskap/b{i}", f"2026-{1 + i % 9:02d}-{1 + i % 28:02d}") for i in range(920)]
    kept, note = W.drop_bulk_lastmods(_dated(big))
    assert note is None and len(kept) == 1000
    assert W.drop_bulk_lastmods(_dated(spread[:5]))[1] is None  # too few URLs to judge


def test_chain_sitemap_gives_no_site_page_updates():
    body = f"<footer>Org nr {OURS}</footer>"
    rows = [(f"/tjenester/t{i}", "2026-10-05") for i in range(30)] + [(f"/tjenester/n{i}", "2026-10-13") for i in range(4)]
    sm = ('<?xml version="1.0"?><urlset>' + "".join(f"<url><loc>https://fjellbekk.no{p}</loc><lastmod>{d}</lastmod></url>"
                                                   for p, d in rows) + "</urlset>")
    f = FakeFetcher({"https://fjellbekk.no/": (200, html(body)), "https://fjellbekk.no/sitemap.xml": (200, sm, "application/xml")})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(f, ctx)
    assert not [c for c in ctx.claims if c.field == "site_page_updated" and c.availability == "available"]
    assert "bulk" in ctx.facts["website_sitemap_bulk"]


WP = "999000045"


def _workplace_ctx(**rec) -> CompanyContext:
    ctx = ctx_for(navn="LYNGSTAD BELLA SOLSIKKE AS", **rec)
    ctx.facts["workplaces"] = [{"organisasjonsnummer": WP, "navn": "BELLA SOLSIKKE",
                                "beliggenhetsadresse": {"adresse": ["Torget 3"], "postnummer": "9998", "poststed": "TESTBY"}}]
    return ctx


def _workplace_site(body: str) -> dict:
    return {"https://bellasolsikke.no/": (200, html(body, title="Bella Solsikke"))}


def test_workplace_name_gives_domain_candidates_after_the_legal_name():
    ctx = _workplace_ctx()
    cands = W.candidates(ctx)
    srcs = [s for _d, s, _u in cands]
    assert [d for d, s, _u in cands if s == "guess_workplace"] == ["bellasolsikke.no", "bella-solsikke.no", "bellasolsikke.com"]
    assert srcs.index("guess_workplace") > max(i for i, s in enumerate(srcs) if s == "guess")
    # a workplace named like the company, or by a single common word, gives no extra guesses
    ctx.facts["workplaces"] = [{"organisasjonsnummer": WP, "navn": "LYNGSTAD BELLA SOLSIKKE AS"},
                               {"organisasjonsnummer": THIRD, "navn": "FRISOR"}]
    assert not [d for d, s, _u in W.candidates(ctx) if s == "guess_workplace"]


def test_workplace_site_proven_by_workplace_name_and_location_address():
    body = "<main><h1>Bella Solsikke</h1><p>Besøksadresse: Torget 3, 9998 Testby</p><a href='mailto:post@bellasolsikke.no'>e</a></main>"
    f = FakeFetcher(_workplace_site(body))
    ctx = _workplace_ctx()
    claims = run(f, ctx)
    site = claims["official_website"]
    assert site.availability == "available" and site.value == "bellasolsikke.no"
    assert site.match["method"] == "workplace_address_and_name"
    spans = " | ".join(e.claim_span for e in ctx.evidence if e.id in site.evidence_ids)
    assert "Torget 3" in spans and "Bella Solsikke" in spans


@pytest.mark.parametrize("body,namesake", [
    # the workplace's name without its registered address
    ("<main><h1>Bella Solsikke</h1><p>Storgata 1, 9998 Testby</p></main>", None),
    # another company's organisation number on the site
    (f"<main><h1>Bella Solsikke</h1><p>Torget 3, 9998 Testby</p><footer>Org.nr {OTHER}</footer></main>", None),
    # another registered entity carries the workplace name (a chain or a namesake): never ours by name
    ("<main><h1>Bella Solsikke</h1><p>Torget 3, 9998 Testby</p></main>", "BELLA SOLSIKKE KJEDEN AS"),
])
def test_workplace_site_needs_address_no_other_number_and_no_namesake(body, namesake):
    pages = _workplace_site(body)
    if namesake:
        row = {"organisasjonsnummer": FOURTH, "navn": namesake, "organisasjonsform": {"kode": "AS"},
               "forretningsadresse": {"adresse": ["Torget 3"], "postnummer": "9998"}}
        pages["https://data.brreg.no/enhetsregisteret/api/enheter?navn=bella%20solsikke&size=50"] = (
            200, json.dumps({"_embedded": {"enheter": [row]}, "page": {"totalPages": 1}}), "application/json")
    ctx = _workplace_ctx()
    claims = run(FakeFetcher(pages), ctx)
    assert claims["official_website"].availability != "available"
    got = next(x for x in ctx.facts["website_candidates"] if x["domain"] == "bellasolsikke.no")
    if namesake:
        assert "carries the workplace name" in got["detail"]
    else:
        assert got["status"] in ("no_match", "other_company")


def _wp_row(title, slug, day, excerpt):
    return {"date": f"{day}T13:39:31", "date_gmt": f"{day}T11:39:31", "link": f"https://fjellbekk.no/{slug}/",
            "title": {"rendered": title}, "excerpt": {"rendered": excerpt, "protected": False}}


def test_bodiless_demo_post_beside_hello_world_is_not_news_nor_a_page_update():
    wp = json.dumps([_wp_row("Purevegg", "butikk-demo/tienda/purevegg", "2024-10-03", ""),
                     _wp_row("Hello world!", "hello-world", "2024-09-17",
                             "<p>Welcome to WordPress. This is your first post. Edit or delete it, then start writing!</p>")])
    sm = ('<?xml version="1.0"?><urlset><url><loc>https://fjellbekk.no/butikk-demo/tienda/purevegg/</loc>'
          '<lastmod>2024-10-03T11:39:33+00:00</lastmod></url></urlset>')
    body = f'<link href="/wp-content/a.css"><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    f = _site({"https://fjellbekk.no/wp-json/wp/v2/posts?per_page=20&_fields=title,link,date,date_gmt,excerpt":
               (200, wp, "application/json"), "https://fjellbekk.no/sitemap.xml": (200, sm, "application/xml")}, body=body)
    ctx = ctx_for(hjemmeside="fjellbekk.no", stiftelsesdato="2020-01-01")
    run(f, ctx)
    assert not [c for c in ctx.claims if c.field in ("dated_news", "site_page_updated") and c.availability == "available"]
    from kilde.synthesis import summarise
    ctx.add_claim("legal_name", "identity", "FJELLBEKK TREVARE AS", ctx.evidence[0])
    assert "news post" not in summarise(ctx, [])["text"]  # no sentence rests on a withheld or dropped post


def test_bodiless_posts_with_real_titles_on_page_builder_sites_stay_news():
    from kilde.templates import stub_posts
    items = [{"title": "Strandrydding på Steilene", "url": "https://x.no/strandrydding/", "body_words": 0},
             {"title": "Investeringsrapport 30.06.25", "url": "https://x.no/rapport/", "body_words": 0},
             {"title": "Nytt verksted", "url": "https://x.no/nytt/", "body_words": 40}]
    assert stub_posts(items) == set()
    # a one-word title with no body, or a bodiless post whose only sibling is a builder default, is filler
    assert stub_posts([{"title": "Purevegg", "url": "https://x.no/p/", "body_words": 2}]) == {"https://x.no/p/"}
    assert stub_posts([{"title": "Ny sak om oss", "url": "https://x.no/a/", "body_words": 0},
                       {"title": "Hello world!", "url": "https://x.no/hello-world/", "body_words": 15}]) == {"https://x.no/a/"}
    # unknown body (a titles-only feed) never decides
    assert stub_posts([{"title": "Purevegg", "url": "https://x.no/p/", "body_words": None}]) == set()


def test_feed_plugin_footer_is_not_body_text_and_rest_stub_stays_out_of_the_feed():
    footer = ('<p>The post <a href="https://fjellbekk.no/demo/purevegg/">Purevegg</a> appeared first on '
              '<a href="https://fjellbekk.no">Fjellbekk</a>.</p>')
    rss = ('<rss><channel><item><title>Purevegg</title><link>https://fjellbekk.no/demo/purevegg/</link>'
           '<pubDate>Thu, 03 Oct 2024 11:39:31 +0000</pubDate>'
           f'<description><![CDATA[{footer}]]></description></item>'
           '<item><title>Nytt verksted i Testby</title><link>https://fjellbekk.no/nytt-verksted/</link>'
           '<pubDate>Thu, 03 Oct 2024 11:39:31 +0000</pubDate><description><![CDATA[<p>Vi har flyttet inn i et nytt '
           'og større verksted på Testby.</p>' + footer.replace("demo/purevegg", "nytt-verksted") + ']]></description></item>'
           '</channel></rss>')
    items = W.parse_feed(rss)
    assert [x["body_words"] for x in items] == [0, 11]
    from kilde.templates import stub_posts
    assert stub_posts(items) == {"https://fjellbekk.no/demo/purevegg/"}
    # the REST list found only a stub and "Hello world!", so the feed is read next: the stub stays out of it
    wp = json.dumps([_wp_row("Purevegg", "demo/purevegg", "2024-10-03", ""),
                     _wp_row("Hello world!", "hello-world", "2024-09-17", "<p>Welcome to WordPress. This is your first post.</p>")])
    body = f'<link href="/wp-content/a.css"><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    f = _site({"https://fjellbekk.no/wp-json/wp/v2/posts?per_page=20&_fields=title,link,date,date_gmt,excerpt":
               (200, wp, "application/json"),
               "https://fjellbekk.no/feed/": (200, rss.replace("<item><title>Nytt", "<itemX><title>Nytt").replace(
                   "</item></channel>", "</itemX></channel>"), "application/rss+xml")}, body=body)
    ctx = ctx_for(hjemmeside="fjellbekk.no", stiftelsesdato="2020-01-01")
    run(f, ctx)
    assert not [c for c in ctx.claims if c.field == "dated_news" and c.availability == "available"]


def test_workplace_collision_ignores_shorter_unrelated_names_but_not_chains():
    body = "<main><h1>Bella Solsikke</h1><p>Torget 3, 9998 Testby</p></main>"
    for name, addr, blocked in (("BELL & BELL AS", "Torget 3", False), ("BELLA AS", "Torget 3", False),
                                ("BELLA SOLSIKKE STORO AS", "Storoveien 1", True),
                                ("BELA SOLSIKE AS", "Storoveien 1", False), ("BELA SOLSIKE AS", "Torget 3", True)):
        W._SEARCH.clear()
        pages = _workplace_site(body)
        row = {"organisasjonsnummer": FOURTH, "navn": name, "organisasjonsform": {"kode": "AS"},
               "forretningsadresse": {"adresse": [addr], "postnummer": "9998"}}
        pages["https://data.brreg.no/enhetsregisteret/api/enheter?navn=bella%20solsikke&size=50"] = (
            200, json.dumps({"_embedded": {"enheter": [row]}, "page": {"totalPages": 1}}), "application/json")
        claims = run(FakeFetcher(pages), _workplace_ctx())
        assert (claims["official_website"].availability != "available") is blocked, name


def test_workplace_proof_follows_the_address_and_visible_name_rules():
    ok = "<main><h1>Bella Solsikke</h1><p>Besøksadresse: Torget 3, 9998 Testby</p></main>"
    # another registered entity at the workplace's address: the address identifies nothing
    pages = _workplace_site(ok)
    pages.update(_listing("enheter", "forretningsadresse", [_row(OTHER, "TESTBY TORG AS", street="Torget 3", pc="9998")],
                          pc="9998"))
    ctx = _workplace_ctx()
    run(FakeFetcher(pages), ctx)
    assert not ctx.claims_for("official_website")
    # the workplace name only inside an email address is no name
    W._SEARCH.clear()
    ctx = _workplace_ctx()
    body = html("<main><p>bellasolsikke@gmail.com · Torget 3, 9998 Testby</p></main>", title="Velkommen")
    run(FakeFetcher({"https://bellasolsikke.no/": (200, body)}), ctx)
    assert not ctx.claims_for("official_website")


def test_colocated_place_word_or_owner_holding_is_no_namesake():
    body = "<h1>Fjellbekk Trevare</h1><footer>Fjellbekk Trevare Testby AS · Tlf 41 00 00 01</footer>"
    for name in ("TESTBY SJAKKLUBB", "FJELLBEKK HOLDING AS"):
        W._SEARCH.clear()
        pages = {"https://fjellbekktrevaretestby.no/": (200, html(body))}
        for w in ("testby", "fjellbekk", "trevare"):
            pages[f"https://data.brreg.no/enhetsregisteret/api/enheter?navn={w}&forretningsadresse.postnummer=9999&size=50"] = (
                200, json.dumps({"_embedded": {"enheter": [_row(OTHER, name)]}}), "application/json")
        ctx = ctx_for(navn="FJELLBEKK TREVARE TESTBY AS", telefon="41000001")
        run(FakeFetcher(pages), ctx)
        assert ctx.claims_for("official_website"), name


def test_shared_address_needs_the_legal_name_with_its_form_on_a_domain_carrying_our_name():
    cotenant = _listing("enheter", "forretningsadresse", [_row(OTHER, "TESTBY REVISJON AS")])
    for host, footer, proven in (
            ("https://fjellbekktrevare.no/", "Fjellbekk Trevare AS, Lyngveien 12 B, 9999 Testby", True),
            ("https://fjellbekktrevare.no/", "Fjellbekk Trevare, Lyngveien 12 B, 9999 Testby", False),   # trade name only
            ("https://testbysnekker.no/", "Fjellbekk Trevare AS, Lyngveien 12 B, 9999 Testby", False)):  # domain without our name
        W._SEARCH.clear()
        pages = {host: (200, html(f"<footer>{footer}</footer>", title="Snekker"))}
        pages.update(cotenant)
        ctx = ctx_for()
        run(FakeFetcher(pages), ctx)
        got = ctx.claims_for("official_website")
        assert bool(got and got[0].match["method"] == "address_and_name") is proven, (host, footer)


def test_a_page_that_fails_transiently_fails_the_step_instead_of_deciding():
    """Funkworks shape: identical responses give identical facts; a page that times out or answers 503 must not
    turn a site into 'ambiguous' in one run and 'proven' in the next."""
    home = html("<h1>Fjellbekk Trevare</h1><a href='/kontakt'>Kontakt</a>")
    pages = {"https://fjellbekktrevare.no/": (200, home), "https://fjellbekktrevare.no/kontakt": (503, "busy")}
    ctx = ctx_for()
    claims = run(FakeFetcher(pages), ctx)
    assert claims["official_website"].availability == "failed"
    assert "could not all be read" in claims["official_website"].note and ctx.module_states["website"] == "timeout"
    # the same site with the page answering: decided, and the same in a second run
    pages["https://fjellbekktrevare.no/kontakt"] = (200, html("<p>Fjellbekk Trevare AS · Lyngveien 12 B, 9999 Testby</p>"))
    outs = []
    for _ in range(2):
        W._SEARCH.clear()
        ctx = ctx_for()
        run(FakeFetcher(dict(pages)), ctx)
        outs.append([(c.field, str(c.value), c.availability) for c in ctx.claims])
    assert outs[0] == outs[1] and ("official_website", "fjellbekktrevare.no", "available") in outs[0]


def test_heading_with_a_shared_trade_word_is_no_namesake():
    """Studio Frisør shape: 'Din frisør på Lambertseter' in a heading is not the registered 'DIN FRISØR DA'."""
    body = "<h1>Din frisør i Testby</h1><footer>Fjellbekk Frisør AS · Tlf 41 00 00 01</footer>"
    pages = {"https://fjellbekkfrisor.no/": (200, html(body))}
    pages.update(_brreg("enheter", "fjellbekk%20fris%C3%B8r", [_row(OTHER, "DIN FRISØR DA", street="Annen vei 1", pc="1111")]))
    ctx = ctx_for(navn="FJELLBEKK FRISØR AS", telefon="41000001")
    run(FakeFetcher(pages), ctx)
    assert ctx.claims_for("official_website")


def test_legal_form_with_capital_a_right_after_the_whole_name():
    rx = W._legal_name_rx("MOI RØR AS", "AS")
    assert rx.search("Kontakt oss Moi Rør As Vi er") and rx.search("MOI RØR AS")
    for text in ("Moi Rør as part of the group", "Moi Rør, As we said", "Moi As", "Moi Rør A/S", "as Moi Rør"):
        assert not rx.search(text), text
    assert W._legal_name_rx("SOLVANG ASA", "ASA").search("Solvang Asa")


def test_domain_named_after_the_company_is_tried_before_a_brand_site(monkeypatch):
    """Betonmast/Puer Fons shape: an unnamed brand site printing our org number sorted before our own domain."""
    seeds = {OURS: [{"domain": "brandsite.no", "sources": ["crawl_orgnr"]},
                    {"domain": "fjellbekktrevare.com", "sources": ["crawl_orgnr"]}]}
    monkeypatch.setattr(W, "website_seeds", lambda: seeds)
    doms = [d for d, _s, _u in W.candidates(ctx_for())]
    assert doms.index("fjellbekktrevare.com") < doms.index("fjellbekktrevare.no") < doms.index("brandsite.no")
    seeds[OURS] = [{"domain": "brandsite.no", "sources": ["crawl_orgnr"]}]
    doms = [d for d, _s, _u in W.candidates(ctx_for())]
    assert doms.index("fjellbekktrevare.no") < doms.index("brandsite.no")

# ---------------------------------------------------------------- crawl-gap rules (v2-gap)


def test_phone_repeated_in_header_and_named_footer_counts():
    """The number is in a header bar without the name, and again in a deeply nested footer that names us."""
    deep = "<div><div><div><div><div><div><p class='footertext'><span><a href='tel:41000001'><span>41 00 00 01</span></a></span></p></div></div></div></div></div></div>"
    body = (f"<header><p>Bestill time <span>41 00 00 01</span></p></header><main><h1>Fjellbekk Trevare</h1>"
            f"<p>Snekkerverksted.</p></main><footer><div><div><p>FJELLBEKK TREVARE AS</p></div>{deep}</div></footer>")
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    claims = run(f, ctx_for(mobil="41000001"))
    assert claims["official_website"].match["method"] == "phone_and_name"


def test_phone_only_in_unnamed_header_is_not_proof():
    body = ("<header><p>Bestill time <span>41 00 00 01</span></p></header><main><h1>Fjellbekk Trevare</h1>"
            "<p>Snekkerverksted.</p></main>")
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    ctx = ctx_for(mobil="41000001")
    run(f, ctx)
    assert not [c for c in ctx.claims if c.field == "official_website" and c.availability == "available"]


@pytest.mark.parametrize("written,ok", [("Lyngvegen 12B", True), ("Lyngvei 12 B", True), ("Lyngvn. 12B", True),
                                        ("Lyngveien 120", False), ("Lyngsvingen 12B", False)])
def test_street_suffix_spellings(written, ok):
    body = f"<footer>Fjellbekk Trevare AS, {written}, 9999 Testby</footer>"
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    ctx = ctx_for()
    claims = run(f, ctx)
    got = claims.get("official_website")
    assert (got is not None and got.availability == "available" and got.match["method"] == "address_and_name") == ok


def test_street_suffix_gate_forms():
    rx = W._street_regex("Storgata 7")
    assert all(rx.search(t) for t in ("Storgata 7", "Storgaten 7", "Storgate 7", "Storgt. 7"))
    assert not rx.search("Storgata 71")


def test_bracketed_short_form_is_an_optional_alias():
    assert ["fjellbekk", "trevare", "senter"] in W.name_variants("STIFTELSEN FJELLBEKK TREVARE SENTER (FTS)", {})
    assert W.name_variants("FJELLBEKK (NORGE) AS", {}) == [["fjellbekk", "norge"]]  # one word left: no alias variant
    body = "<h1>Fjellbekk Trevare Senter</h1><p>Lyngveien 12B, 9999 Testby</p>"
    f = FakeFetcher({"https://fts.no/": (200, html(body))})
    claims = run(f, ctx_for(navn="STIFTELSEN FJELLBEKK TREVARE SENTER (FTS)", hjemmeside="fts.no",
                            organisasjonsform={"kode": "STI"}))
    assert claims["official_website"].availability == "available"


def test_one_word_name_in_lower_case_with_legal_form():
    assert W.name_check("kontakt: fjellbekkverk as, lyngveien 12b", ["fjellbekkverk"])[0]
    assert not W.name_check("kontakt: fjellbekkverk lyngveien 12b", ["fjellbekkverk"])[0]  # run-together token
    assert not W.name_check("post@fjellbekkverk.no as", ["fjellbekkverk"])[0]
    assert not W.name_check("se fjellbekktrevare as", ["fjellbekk", "trevare"])[0]  # several words run together


@pytest.mark.parametrize("inbox,exclusive", [
    ("fjellbekk@ebilag.toko.no", True),       # e-invoice scan inbox
    ("faktura.fjellbekk@tripletex.no", True),  # accounting platform
    ("kvittering@faktura.example-regnskap.no", True),  # a 'faktura' inbox subdomain
    ("post@nabosnekker.no", False),            # a genuine other company's address still counts
])
def test_service_mail_domains_do_not_make_a_site_shared(inbox, exclusive):
    body = (f'<h1>Fjellbekk Trevare</h1><footer>Fjellbekk Trevare AS, org.nr. {OURS} '
            f'<a href="mailto:post@fjellbekktrevare.no">post</a> Faktura: <a href="mailto:{inbox}">{inbox}</a></footer>')
    f = FakeFetcher({"https://fjellbekktrevare.no/": (200, html(body))})
    ctx = ctx_for(hjemmeside="fjellbekktrevare.no")
    run(f, ctx)
    assert ctx.facts["website_exclusive"] is exclusive


def test_service_mail_domain():
    assert W.service_mail_domain("ebilag.toko.no") and W.service_mail_domain("vipps.no")
    assert not W.service_mail_domain("nabosnekker.no") and not W.service_mail_domain("ebilag-snekker.no")


def test_website_note_lists_every_domain_tried():
    f = FakeFetcher({"https://fjellbekk.no/": (200, html("<h1>Velkommen</h1><p>Noe helt annet.</p>", title="Velkommen")),
                     "https://fjellbekktrevare.no/": (200, html("<h1>Hei</h1>", title="Hei"))})
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(f, ctx)
    note = claims["official_website"].note
    assert "domains tried: fjellbekk.no (no_match), fjellbekktrevare.no (no_match)" in note
    assert "fjellbekktrevare.com (no_dns)" in note


def test_budget_note_lists_domains_tried(monkeypatch):
    monkeypatch.setattr(W, "MAX_REQUESTS", 4)
    page = html("<h1>Hei</h1>", title="Hei")
    f = FakeFetcher({"https://fjellbekk.no/": (200, page), "https://fjellbekktrevare.no/": (200, page),
                     "https://fjellbekktrevare.com/": (200, page), "https://fjellbekk-trevare.no/": (200, page)})
    claims = run(f, ctx_for(hjemmeside="fjellbekk.no"))
    note = claims["official_website"].note
    assert claims["official_website"].availability == "not_available"
    assert "domains tried: fjellbekk.no (no_match)" in note and "not checked (name guesses only" in note


def test_rate_limited_proving_page_fails_the_step_and_the_retry_proves_it():
    """Aabakken shape: the contact page answers 429 on the first attempt; the step fails (retry pass), and the
    retry, with the page answering, proves the site by registry phone and legal name."""
    home = html("<h1>Fjellbekk Trevare</h1><a href='/kontakt-oss'>Kontakt oss</a>")
    pages = {"https://fjellbekktrevare.no/": (200, home),
             "https://fjellbekktrevare.no/kontakt-oss": (429, "slow down")}
    ctx = ctx_for(mobil="41000001")
    claims = run(FakeFetcher(pages), ctx)
    assert claims["official_website"].availability == "failed" and ctx.module_states["website"] == "timeout"
    pages["https://fjellbekktrevare.no/kontakt-oss"] = (200, html("<p>Fjellbekk Trevare AS, tlf 41 00 00 01</p>"))
    W._SEARCH.clear()
    ctx2 = ctx_for(mobil="41000001")
    ctx2.facts["website_retry"] = True
    claims = run(FakeFetcher(pages), ctx2)
    assert claims["official_website"].availability == "available"
    assert claims["official_website"].match["method"] == "phone_and_name"


def test_reused_post_with_a_later_year_or_wrong_weekday_is_withheld():
    """Sognefjord shape: a 2024 WordPress post reused for the 2026 annual meeting keeps its 2024 date."""
    wp = json.dumps([
        _wp_row("Årsmøte i foreningen", "arsmote-2026", "2024-04-03", "<p>Velkommen til årsmøte torsdag 16. april kl 18.</p>"),
        _wp_row("Innkalling til dugnad", "dugnad", "2024-05-02", "<p>Dugnad torsdag 16. april, ta med hansker.</p>"),
        _wp_row("Ny brygge ferdig", "ny-brygge", "2024-06-12", "<p>Brygga sto ferdig tirsdag 11. juni 2024.</p>"),
        _wp_row("Planer for neste år", "planer", "2024-11-12", "<p>I 2025 bygger vi nytt naust.</p>"),
        _wp_row("Påsken 2025 i Florida", "pasken-2025", "2024-08-30", "<p>Bli med på tur.</p>")])
    body = f'<link href="/wp-content/a.css"><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    f = _site({"https://fjellbekk.no/wp-json/wp/v2/posts?per_page=20&_fields=title,link,date,date_gmt,excerpt":
               (200, wp, "application/json")}, body=body)
    ctx = ctx_for(hjemmeside="fjellbekk.no", stiftelsesdato="2020-01-01")
    run(f, ctx)
    titles = sorted(str(c.value).rsplit(" (", 1)[0] for c in ctx.claims if c.field == "dated_news" and c.availability == "available")
    # the slug naming 2026 and a 'torsdag 16. april' that is no Thursday in 2024 or 2025 are withheld;
    # a weekday that matches the year, and next year's plans in the body, stay
    assert titles == ["Ny brygge ferdig", "Planer for neste år", "Påsken 2025 i Florida"]


def test_date_contradicted_rules():
    item = {"title": "Sommerfest", "url": "https://x.no/sommerfest/", "datetime": "2023-06-01T10:00:00+02:00",
            "text": "Fest mandag 16. juni"}
    assert W.date_contradicted(item, "2026-10-14")  # 16 June: Thursday 2022, Friday 2023, Sunday 2024
    item["text"] = "Fest lørdag 17. juni"
    assert W.date_contradicted(item, "2026-10-14") is None  # Saturday 17 June 2023
    item["text"] = "Fest fredag 17. juni"
    assert W.date_contradicted(item, "2026-10-14") is None  # Friday 17 June 2022: a one-year slip is tolerated
    item["title"] = "Sommerfest 2024"
    assert W.date_contradicted(item, "2026-10-14") is None  # next year's plans are common
    item["title"] = "Sommerfest 2025"
    assert W.date_contradicted(item, "2026-10-14")
    assert W.date_contradicted(item, "2024-10-14") is None  # 2025 had not come yet: a plan, not a reuse


def _sister_search(label: str, name: str, org: str = OTHER) -> dict:
    from urllib.parse import quote
    return {f"https://data.brreg.no/enhetsregisteret/api/enheter?navn={quote(label)}&size=20":
            (200, json.dumps({"_embedded": {"enheter": [{"organisasjonsnummer": org, "navn": name}]}}), "application/json")}


def test_generic_industry_word_does_not_name_a_social_handle():
    """Radio Sandefjord shape: the sister station's handles share only the trade word 'radio' with our name."""
    body = (f'<footer>Radio Testby AS · Org.nr {OURS}'
            '<a href="https://www.instagram.com/radiofjellbyen/">ig</a>'
            '<a href="https://x.com/radiofjellbyen">x</a>'
            '<a href="https://www.facebook.com/radiotestby/">fb</a></footer>')
    f = FakeFetcher({"https://radiotestby.no/": (200, html(body, title="Radio Testby"))})
    ctx = ctx_for(navn="RADIO TESTBY AS", hjemmeside="radiotestby.no")
    run(f, ctx)
    assert [str(c.value) for c in ctx.claims if c.field == "social_profile"] == ["facebook.com/radiotestby"]


def test_handle_spelling_another_registered_company_is_dropped():
    """A handle that joins our distinctive word with another word that makes a registered company's name is
    that company's profile; without such a company it stays ours."""
    body = (f'<footer>Fjellbekk Trevare AS · Org.nr {OURS}'
            '<a href="https://www.instagram.com/fjellbekkmaling/">ig</a></footer>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(_sister_search("fjellbekk maling", "FJELLBEKK MALING AS"), body=body), ctx)
    assert not [c for c in ctx.claims if c.field == "social_profile" and c.availability == "available"]
    W._SISTER_CACHE.clear()
    W._SEARCH.clear()
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    run(_site(body=body), ctx)
    assert [str(c.value) for c in ctx.claims if c.field == "social_profile"] == ["instagram.com/fjellbekkmaling"]


def test_person_name_handle_is_not_the_company_profile():
    body = (f'<footer>Lisa Husby Kunst AS · Org.nr {OURS}'
            '<a href="https://www.instagram.com/lisa.husby.kunst/">ig</a></footer>')
    f = FakeFetcher({"https://lisahusbykunst.no/": (200, html(body, title="Lisa Husby Kunst"))})
    ctx = ctx_for(navn="LISA HUSBY KUNST AS", hjemmeside="lisahusbykunst.no")
    run(f, ctx)
    assert not [c for c in ctx.claims if c.field == "social_profile" and c.availability == "available"]


def test_social_keys_leave_out_trade_place_and_person_words():
    keys = W._social_keys(["radio", "sandefjord"], {"radiosandefjord.no"})
    assert keys == {"radiosandefjord"}
    assert "radio" not in W._social_keys(["radio", "fjellbekk"], set())
    assert W._social_keys(["espen", "mortensen", "invest"], {"invest.no"}) == {"espenmortenseninvest"}


def _hjem(host: str, rows: list[dict]) -> dict:
    return {f"https://data.brreg.no/enhetsregisteret/api/enheter?hjemmeside={host}&size=20":
            (200, json.dumps({"_embedded": {"enheter": rows}}), "application/json")}


def test_initials_mail_domain_is_ours_unless_another_entity_declares_it():
    """Larhammer Aarseth shape: post@ on the initials of the legal name is the firm's own mailbox domain."""
    body = (f'<footer>Fjellbekk Trevare Testverk AS · Org.nr {OURS} <a href="mailto:post@ftt.no">post@ftt.no</a>'
            '<a href="https://www.facebook.com/fjellbekktrevare">fb</a></footer>')
    ctx = ctx_for(navn="FJELLBEKK TREVARE TESTVERK AS", hjemmeside="fjellbekk.no")
    claims = run(_site(body=body), ctx)
    assert claims["official_website"].value == "fjellbekk.no"
    assert claims["social_profile"].availability == "available"
    W._HJEM_CACHE.clear()
    ctx = ctx_for(navn="FJELLBEKK TREVARE TESTVERK AS", hjemmeside="fjellbekk.no")
    claims = run(_site(_hjem("ftt.no", [{"organisasjonsnummer": OTHER, "navn": "FTT AS", "hjemmeside": "ftt.no"}]),
                       body=body), ctx)
    assert claims["social_profile"].availability != "available"


def test_initials_label():
    assert W._initials_label("laa", "LARHAMMER AARSETH ADVOKATFIRMA AS")
    assert not W._initials_label("la", "LARHAMMER AARSETH AS")
    assert not W._initials_label("abc", "LARHAMMER AARSETH ADVOKATFIRMA AS")


def _coloc_search(row: dict) -> dict:
    out = {}
    for w in ("fjellbekk", "trevare"):
        for path, fld in (("enheter", "forretningsadresse"), ("enheter", "postadresse"), ("underenheter", "beliggenhetsadresse")):
            out[f"https://data.brreg.no/enhetsregisteret/api/{path}?navn={w}&{fld}.postnummer=9999&size=50"] = (
                200, json.dumps({"_embedded": {path: [row] if path == "enheter" and fld == "forretningsadresse" else []}}),
                "application/json")
    return out


def test_registry_site_kept_when_namesake_at_our_address_has_its_own_site():
    """Det Norske Jentekor shape: a sister foundation at the same address shares a name word but declares its
    own website; our registry-declared site stays ours with its derived facts. If the namesake mails from our
    site's domain, the site is not provably ours alone."""
    row = {"organisasjonsnummer": OTHER, "navn": "FJELLBEKK SOLISTKOR STIFTELSE", "hjemmeside": "solistkor.no",
           "forretningsadresse": {"adresse": ["Lyngveien 12B"], "postnummer": "9999"}}
    body = ('<h1>Fjellbekk Trevare</h1><footer>Fjellbekk Trevare, Lyngveien 12B, 9999 Testby'
            '<a href="https://www.instagram.com/fjellbekktrevare">ig</a></footer>')
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(_site(_coloc_search(row), body=body), ctx)
    assert claims["official_website"].value == "fjellbekk.no"
    assert claims["social_profile"].availability == "available"
    for c in (W._SEARCH, W._NAME_SEARCH, W._SISTER_CACHE, W._ROW_CACHE):
        c.clear()
    row2 = dict(row, hjemmeside=None, epostadresse="kor@fjellbekk.no")
    ctx = ctx_for(hjemmeside="fjellbekk.no")
    claims = run(_site(_coloc_search(row2), body=body), ctx)
    assert claims["social_profile"].availability != "available"


def test_group_root_keeps_site_co_declared_by_group_and_foreign_family_units():
    """Norsk Hydro shape: a subsidiary and foreign units named like us declare the parent's site."""
    body = '<h1>Fjellbekk Trevare ASA</h1><footer><a href="https://www.instagram.com/fjellbekktrevare">ig</a></footer>'
    tree = json.dumps({"organisasjonsnummer": OURS, "children": [{"organisasjonsnummer": OTHER, "children": []}]})
    rows = [{"organisasjonsnummer": OURS, "navn": "FJELLBEKK TREVARE ASA", "hjemmeside": "www.fjellbekk.no"},
            {"organisasjonsnummer": OTHER, "navn": "FJELLBEKK ENERGI AS", "hjemmeside": "www.fjellbekk.no"},
            {"organisasjonsnummer": THIRD, "navn": "FJELLBEKK CANADA INC", "hjemmeside": "www.fjellbekk.no",
             "organisasjonsform": {"kode": "UTLA"}}]
    pages = {"https://fjellbekk.no/": (200, html(body)),
             f"https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{OURS}": (200, tree, "application/json")}
    pages.update(_hjem("fjellbekk.no", rows))
    claims = run(FakeFetcher(pages), ctx_for(navn="FJELLBEKK TREVARE ASA", hjemmeside="www.fjellbekk.no", erIKonsern=True))
    assert claims["official_website"].value == "fjellbekk.no"
    assert claims["social_profile"].availability == "available"
    # a Norwegian company outside our group that is itself in a group still counts against us
    for c in (W._HJEM_CACHE, W._GROUP_CACHE, W._GROUP_TREE, W._GROUP_ORGS):
        c.clear()
    rows.append({"organisasjonsnummer": FOURTH, "navn": "FJELLBEKK KRAFT AS", "hjemmeside": "www.fjellbekk.no",
                 "erIKonsern": True})
    pages.update(_hjem("fjellbekk.no", rows))
    claims = run(FakeFetcher(pages), ctx_for(navn="FJELLBEKK TREVARE ASA", hjemmeside="www.fjellbekk.no", erIKonsern=True))
    assert "social_profile" not in claims or claims["social_profile"].availability != "available"


def test_page_wide_has_footer_class_does_not_make_body_text_an_operator():
    """Jærkraft shape: <body class="et-tb-has-footer"> and a terms page listing grid companies."""
    page = W.parse_page(Snapshot("https://fjellbekk.no/vilkar/", "https://fjellbekk.no/vilkar/", 200, "x",
                                 ('<html><body class="et-tb-has-footer"><p>Avtale med: Kvist Nett AS, Lyngnett AS</p>'
                                  f'<footer>Fjellbekk Trevare AS {OURS}</footer></body></html>').encode(), "text/html"))
    names = W.other_company_names([page], ["fjellbekk", "trevare"])
    assert names and not any(n[2] for n in names)


def test_supplier_and_bio_mentions_do_not_present_the_site():
    """Centrum Bowling and Norway Oysters shapes."""
    priv = W.parse_page(Snapshot("https://fjellbekk.no/personvern", "https://fjellbekk.no/personvern", 200, "x",
                                 ('<html><body><p>Kvist Media AS er leverandør av markedsføringstjenester. Opplysninger '
                                  'lagres på servere som driftes av Kvist Media AS. For mer informasjon om hvordan '
                                  'Kvist Media AS lagrer data, se deres personvernerklæring.</p></body></html>').encode(), "text/html"))
    about = W.parse_page(Snapshot("https://fjellbekk.no/om-oss", "https://fjellbekk.no/om-oss", 200, "x",
                                  ('<html><body><p>Styreleder Ola Nordmann er også daglig leder i Lyngfisk AS og Bakketroll AS.</p>'
                                   '</body></html>').encode(), "text/html"))
    names = {n[0]: n for n in W.other_company_names([priv, about], ["fjellbekk", "trevare"])}
    assert not names["kvist media"][2] and not names["lyngfisk"][2] and not names["bakketroll"][2]
    owner = W.parse_page(Snapshot("https://fjellbekk.no/", "https://fjellbekk.no/", 200, "x",
                                  b'<html><body><p>Nettstedet eies av Kvist Media AS.</p></body></html>', "text/html"))
    assert W.other_company_names([owner], ["fjellbekk", "trevare"])[0][4]


def test_group_parent_stated_on_subsidiary_site_with_our_org_number():
    """Tronfjell Maskin shape: our org number in the footer, the About text says we are part of our parent."""
    body = (f'<p>Fra januar blir vi et heleid datterselskap av Kvist Entreprenør AS, en del av Kvist Gruppen.</p>'
            f'<footer>Fjellbekk Trevare AS · Org.nr {OURS}<a href="https://www.instagram.com/fjellbekktrevare">ig</a></footer>')
    tree = json.dumps({"organisasjonsnummer": OTHER, "navn": "KVIST ENTREPRENØR AS",
                       "children": [{"organisasjonsnummer": OURS, "children": []}]})
    rows = {W.BRREG_SEARCH + "?navn=kvist%20entreprenor&size=20":
            (200, json.dumps({"_embedded": {"enheter": [{"organisasjonsnummer": OTHER, "navn": "KVIST ENTREPRENØR AS"}]}}),
             "application/json"),
            f"https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{OURS}": (200, tree, "application/json")}
    claims = run(_site(rows, body=body), ctx_for(hjemmeside="fjellbekk.no", erIKonsern=True))
    assert claims["social_profile"].availability == "available"
    for c in (W._SEARCH, W._GROUP_ORGS, W._SISTER_CACHE, W._ROW_CACHE):
        c.clear()
    tree2 = json.dumps({"organisasjonsnummer": THIRD, "children": [{"organisasjonsnummer": OURS, "children": []}]})
    rows[f"https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{OURS}"] = (200, tree2, "application/json")
    claims = run(_site(rows, body=body), ctx_for(hjemmeside="fjellbekk.no", erIKonsern=True))
    assert claims.get("social_profile") is None or claims["social_profile"].availability != "available"


def test_regional_variant_handles():
    from kilde.words import regional_variant
    hs = ["equinor", "equinor_uk", "equinor_na", "equinorasa", "equinorcareers"]
    assert [h for h in hs if regional_variant(h, hs)] == ["equinor_uk", "equinor_na", "equinorcareers"]
    assert not regional_variant("fjellbekk", ["fjellbekk_uk"])


def test_accented_letters_fold_in_names_and_still_match_the_page():
    assert W.name_words("ANNETTE FERRÓ AS") == ["annette", "ferro"]
    assert "annetteferro.no" in W.name_domains("ANNETTE FERRÓ AS")
    assert W.name_regex(W.name_words("CAFÉ MÜLLER AS")).search("Velkommen til Café Müller")
    assert W.name_words("BLÅBÆR ØST AS") == ["blåbær", "øst"]


def test_single_first_name_surname_or_trade_word_is_never_guessed():
    doms = W.name_domains("MARIA SCHEI BRUDESALONG AS")
    assert not any(d.split(".")[0] in ("maria", "schei", "brudesalong") for d in doms)
    assert W.name_domains("HAUGEN AS") == []
    assert "sunwind.no" in W.name_domains("SUNWIND GYLLING AS")


def test_news_cards_under_page_wide_header_classes_and_deep_builder_cards():
    """stw.no shape: <body class="ast-header-break-point"> and Elementor cards nesting the date 6 levels from
    the link, with plain <time>text</time>."""
    card = ('<div class="e-loop-item"><div class="w"><div class="w2"><div class="w3"><h2><a href="/blogg/{slug}/">{t}</a>'
            '</h2></div></div><div class="info"><div><div><ul><li><span><time>{d}</time></span></li></ul></div></div></div>'
            '</div></div>')
    body = ''.join(card.format(slug=f"post-{i}", t=f"Nyhet nummer {i} fra oss", d=d)
                   for i, d in enumerate(["september 18, 2026", "juli 3, 2026"]))
    page = W.parse_page(Snapshot("https://fjellbekk.no/blogg/", "https://fjellbekk.no/blogg/", 200, "x",
                                 f'<html><body class="blog ast-header-break-point"><main>{body}</main></body></html>'.encode(),
                                 "text/html"))
    got = [x[0]["date"] for x in W.news_items(page, "fjellbekk.no", W.Scope(OURS, ["fjellbekk"]))]
    assert got == ["2026-09-18", "2026-07-03"]


def test_english_long_date_with_time_and_zone():
    from bs4 import BeautifulSoup
    block = BeautifulSoup("<a href='/news/x'><span>14 October 2026 | 08:15 (CEST)</span><h3>Big news today</h3></a>", "html.parser")
    assert W.find_date(block)[0] == "2026-10-14"


def test_project_pages_are_not_news():
    ok = {"title": "Einunna bru er ferdig", "datetime": "2025-09-05T14:04:19+02:00"}
    assert not W._news_ok(dict(ok, url="https://fjellbekk.no/prosjekter/vei/einunna-bru/"), "2000-01-01", "fjellbekk.no")
    assert W._news_ok(dict(ok, url="https://fjellbekk.no/aktuelt/einunna-bru/"), "2000-01-01", "fjellbekk.no")


def test_news_section_without_readable_dates_uses_the_posts_sitemap():
    head = '<meta name="generator" content="WordPress 6.5">'
    listing = html('<h1>Blogg</h1><div id="app"></div>')
    smap = ('<?xml version="1.0"?><urlset><url><loc>https://fjellbekk.no/blogg/ny-hall/</loc><lastmod>2026-05-02</lastmod></url>'
            '</urlset>')
    art = html('<h1>Vi har bygget ny hall</h1>', head='<meta property="article:published_time" content="2026-05-01T10:00:00+02:00">')
    body = f'<a href="/blogg/">Blogg</a><footer>Fjellbekk Trevare AS · Org.nr {OURS}</footer>'
    f = _site({"https://fjellbekk.no/blogg/": (200, listing), "https://fjellbekk.no/post-sitemap.xml": (200, smap, "application/xml"),
               "https://fjellbekk.no/blogg/ny-hall/": (200, art)}, body=body, head=head)
    claims = run(f, ctx_for(hjemmeside="fjellbekk.no"))
    assert claims["dated_news"].value == "Vi har bygget ny hall (2026-05-01T10:00:00+02:00)"


def test_sitemap_span_quotes_cdata_entries_verbatim():
    raw = ('<urlset><url>\n\t\t<loc><![CDATA[https://fjellbekk.no/a/]]></loc>\n\t\t<lastmod><![CDATA[2026-05-01T10:00:00+00:00]]>'
           '</lastmod></url></urlset>')
    urls, _ = W.parse_sitemap(raw.encode())
    assert urls == [("https://fjellbekk.no/a/", "2026-05-01T10:00:00+00:00")]
    span = W.sitemap_span(raw, *urls[0])
    assert span in raw and span.startswith("https://fjellbekk.no/a/") and span.endswith("2026-05-01T10:00:00+00:00")


def test_internal_inbox_is_not_a_contact_email_but_a_person_or_role_box_is():
    assert W._person_mailbox("kari.hansen@fjellbekk.no") and W._person_mailbox("kn@fjellbekk.no")
    assert not W._person_mailbox("mmpdpnpipelinecross@fjellbekk.no")
    page = W.parse_page(Snapshot("https://fjellbekk.no/", "https://fjellbekk.no/", 200, "x",
                                 html('<footer>Fjellbekk Trevare AS <a href="mailto:pipelinecrossing@fjellbekk.no">x</a>'
                                      '</footer>').encode(), "text/html"))
    em, _ph, _n = W.extract_contacts([page], "fjellbekk.no", set(), W.Scope(OURS, ["fjellbekk", "trevare"]))
    assert em == []


def test_company_named_after_our_street_is_not_a_near_namesake():
    """H2 Hardanger shape: 'Lyngveien 12 AS' at our address owns the building."""
    rows = {"https://data.brreg.no/enhetsregisteret/api/enheter?navn=lyng&forretningsadresse.postnummer=9999&size=50":
            (200, json.dumps({"_embedded": {"enheter": [{"organisasjonsnummer": OTHER, "navn": "LYNGVEIEN 12 AS",
                                                          "forretningsadresse": {"adresse": ["Lyngveien 12B"], "postnummer": "9999"}}]}}),
             "application/json")}
    body = "<h1>Lyng</h1><footer>Lyng AS, Lyngveien 12B, 9999 Testby</footer>"
    f = FakeFetcher(dict({"https://lyng.no/": (200, html(body, title="Lyng"))}, **rows))
    ctx = ctx_for(navn="LYNG TESTVERK AS")
    f = FakeFetcher(dict({"https://lyngtestverk.no/": (200, html(body.replace("Lyng AS", "Lyng Testverk AS"),
                                                                  title="Lyng Testverk"))}, **{
        k.replace("navn=lyng&", "navn=testverk&"): v for k, v in rows.items()}, **rows))
    claims = run(f, ctx)
    assert claims["official_website"].value == "lyngtestverk.no"
