"""Cosmetic rules: one claim per social profile, platform template pages, exact spans, clean values and
summary punctuation. All on made-up companies."""
import pytest

from kilde import gate
from kilde.connectors import website as W
from kilde.model import CompanyContext, Snapshot
from kilde.socials import duplicates, profile_key
from kilde.spans import clip, encoded_span, repair
from kilde.synthesis import summarise
from kilde.templates import is_template_title, is_template_url


# ---------------------------------------------------------------- social profiles
@pytest.mark.parametrize("a,b", [
    ("facebook.com/fjelltopptest-123456789012345", "facebook.com/fjelltopptest"),   # legacy Name-<id> and handle
    ("facebook.com/fjell-topp-test-123456789012345", "facebook.com/fjelltopptest"),
    ("facebook.com/p/fjelltopp-test-123456789012345", "facebook.com/fjelltopptest"),
    ("facebook.com/profile.php?id=123456789012345", "facebook.com/p/fjelltopp-123456789012345"),  # same id
    ("facebook.com/people/fjelltopp-test/123456789012345", "facebook.com/profile.php?id=123456789012345"),
    ("facebook.com/pages/fjelltopp-test/123456789012345", "facebook.com/123456789012345"),
    ("facebook.com/pg/fjelltopptest", "facebook.com/fjelltopptest"),
    ("https://m.facebook.com/FjelltoppTest/?ref=page", "facebook.com/fjelltopptest"),
    ("instagram.com/fjelltopp/", "https://www.instagram.com/Fjelltopp?hl=nb"),
    ("twitter.com/fjelltopp", "x.com/fjelltopp"),
    ("linkedin.com/company/fjelltopp", "no.linkedin.com/company/Fjelltopp/"),
])
def test_same_social_profile_in_two_url_forms_is_one(a, b):
    assert duplicates([a, b]) == {0} or duplicates([a, b]) == {1}


def test_social_dedupe_keeps_the_handle_and_keeps_distinct_pages():
    vals = ["facebook.com/fjelltopptest-123456789012345", "facebook.com/fjelltopptest", "instagram.com/fjelltopptest"]
    assert duplicates(vals) == {0}  # the plain handle is kept over the id form
    # different ids, different handles, a name that does not spell the handle, other platforms: all kept
    assert duplicates(["facebook.com/profile.php?id=123456789", "facebook.com/profile.php?id=987654321"]) == set()
    assert duplicates(["facebook.com/fjelltopp-bygg-123456789012345", "facebook.com/fjelltopptest"]) == set()
    assert duplicates(["linkedin.com/company/fjell-topp", "linkedin.com/company/fjelltopp"]) == set()
    assert duplicates(["facebook.com/fjelltopp", "instagram.com/fjelltopp"]) == set()
    assert profile_key("youtube.com/channel/UCabcDEF")[1] != profile_key("youtube.com/channel/UCABCdef")[1]


def test_facebook_pg_tab_path_is_the_page():
    assert W.clean_social("https://www.facebook.com/pg/FjelltoppTest/about/") == \
        ("facebook", "https://www.facebook.com/FjelltoppTest")
    assert W.clean_social("https://www.facebook.com/pg/") is None
    assert str(W.canonical_social("facebook", "https://www.facebook.com/sj%C3%B8topp")) == "facebook.com/sjøtopp"


def _ctx(derived):
    ctx = CompanyContext("999999999")
    ctx.registry = {"navn": "FJELLTOPP TEST AS"}
    s = Snapshot(url="https://fjelltopptest.no/", final_url="https://fjelltopptest.no/", status=200,
                 retrieved_at="2026-10-13T08:00:00Z", content=b"x", content_type="text/html")
    ev = ctx.add_evidence(s, source_class="company_owned", span="x", method="t")
    ctx.add_claim("legal_name", "identity", "FJELLTOPP TEST AS", ev)
    ctx.add_claim("official_website", "web_presence", "fjelltopptest.no", ev, match={"method": "orgnr_on_page"})
    for f, v in derived:
        ctx.add_claim(f, "web_presence", v, ev, match={"method": "linked_from_verified_site"})
    return ctx


def test_tidy_drops_duplicate_profiles_and_the_summary_cites_one():
    ctx = _ctx([("social_profile", W.canonical_social("facebook", "https://www.facebook.com/Fjelltopp-Test-123456789012345")),
                ("social_profile", W.canonical_social("facebook", "https://www.facebook.com/fjelltopptest")),
                ("social_profile", W.canonical_social("instagram", "https://www.instagram.com/fjelltopptest"))])
    gate.tidy(ctx)
    socials = [c for c in ctx.claims if c.field == "social_profile"]
    assert [str(c.value) for c in socials] == ["facebook.com/fjelltopptest", "instagram.com/fjelltopptest"]
    assert socials[0].value.get("platform") == "facebook"  # the canonical value keeps its shape
    sent = [s for s in summarise(ctx, [])["sentences"] if "profiles on" in s["text"]][0]
    assert sent["claim_ids"] == [c.id for c in socials]


# ---------------------------------------------------------------- template pages
@pytest.mark.parametrize("url", [
    "https://fjelltopp.no/post/this-is-your-first-post", "https://fjelltopp.no/hello-world/",
    "https://fjelltopp.no/sample-page", "https://fjelltopp.no/blank-1", "https://fjelltopp.no/copy-of-home",
    "https://fjelltopp.no/testside", "https://fjelltopp.no/2021/03/hei-verden/", "https://fjelltopp.no/untitled-2",
    "https://fjelltopp.no/post/grow-your-blog-community", "https://fjelltopp.no/elementor-123/",
])
def test_template_urls(url):
    assert is_template_url(url)


@pytest.mark.parametrize("url", [
    "https://fjelltopp.no/projects/hydraulictestsystem", "https://fjelltopp.no/test-av-varmepumper",
    "https://fjelltopp.no/", "https://fjelltopp.no/post/first-post-about-our-new-office",
    "https://fjelltopp.no/blankvask", "https://fjelltopp.no/kopi-tjenester",
])
def test_real_pages_are_not_templates(url):
    assert not is_template_url(url)


def test_template_titles():
    assert is_template_title("This Is Your First Post") and is_template_title("Hello world!")
    assert is_template_title("Copy of Home") and is_template_title("Blank")
    assert not is_template_title("Vi har flyttet") and not is_template_title("Test av nye varmepumper i Bergen")


def test_sitemap_drops_template_pages(monkeypatch):
    import asyncio
    sm = ('<?xml version="1.0"?><urlset>'
          '<url><loc>https://fjelltopp.no/post/this-is-your-first-post</loc><lastmod>2025-03-16</lastmod></url>'
          '<url><loc>https://fjelltopp.no/copy-of-om-oss</loc><lastmod>2025-04-01</lastmod></url>'
          '<url><loc>https://fjelltopp.no/tjenester</loc><lastmod>2025-05-01</lastmod></url>'
          '<url><loc>https://fjelltopp.no/kontakt</loc><lastmod>2025-06-01</lastmod></url></urlset>').encode()
    snap = Snapshot(url="https://fjelltopp.no/sitemap.xml", final_url="https://fjelltopp.no/sitemap.xml", status=200,
                    retrieved_at="2026-10-13T08:00:00Z", content=sm, content_type="application/xml")

    class Sp:
        def can(self, n):
            return True

        async def get(self, url):
            return snap

    class F:
        robots = {}

    ctx = CompanyContext("999999999")
    ctx.registry = {"navn": "FJELLTOPP TEST AS", "stiftelsesdato": "2020-01-01"}
    home = type("H", (), {"url": "https://fjelltopp.no/"})()
    asyncio.run(W.sitemap_activity(F(), Sp(), ctx, home, "fjelltopp.no"))
    assert [c.value["url"] for c in ctx.claims if c.field == "site_page_updated"] == \
        ["https://fjelltopp.no/kontakt", "https://fjelltopp.no/tjenester"]


# ---------------------------------------------------------------- spans
@pytest.mark.parametrize("raw,span,want", [
    ("<html lang='nb'><body>", 'lang="nb"', "lang='nb'"),
    ("<html lang=nb><body>", 'lang="nb"', "lang=nb"),
    ('<HTML LANG="nb-NO">', 'lang="nb-NO"', 'LANG="nb-NO"'),
    ("<html lang='nb' class=x>", '<html lang="nb">', "lang='nb'"),
])
def test_attribute_spans_in_any_quoting(raw, span, want):
    assert repair(raw, span, "text/html") == (want, [], True)


def test_attribute_span_does_not_match_a_longer_value():
    assert repair("<html lang=nbx>", 'lang="nb"', "text/html")[2] is False


def test_entity_and_percent_encoded_email_span_is_found_exactly():
    raw = '<a href="mailto:post&#64;&#102;jell&#x74;opp.no">skriv</a> <a href="mailto:salg%40fjelltopp.no">'
    span, _, exact = repair(raw, "mailto:post@fjelltopp.no", "text/html")
    assert exact and span == "mailto:post&#64;&#102;jell&#x74;opp.no" and span in raw
    span, _, exact = repair(raw, "mailto:salg@fjelltopp.no", "text/html")
    assert exact and span == "mailto:salg%40fjelltopp.no"
    assert encoded_span(raw, "mailto:annen@fjelltopp.no") is None


def test_spans_have_no_edge_whitespace():
    raw = '<a href="tel:22 33 44 55 ">ring</a>'
    assert repair(raw, "tel:22 33 44 55 ", "text/html") == ("tel:22 33 44 55", [], True)
    assert clip("x" * 399 + " y") == "x" * 399


# ---------------------------------------------------------------- values
def test_values_are_plain_text_with_single_spaces():
    ctx = _ctx([("website_description", "Vi selger kompetanse &amp;circ; r&aring;d &amp; tekst."),
                ("website_title", "Fjelltopp &amp; Co &ndash; Bygg"),
                ("site_contact_email", "post@fjelltopptest.no")])
    ctx.add_claim("statutory_purpose", "identity", "Drift av verksted.  Utleie   av utstyr.  ", ctx.evidence[0])
    gate.tidy(ctx)
    v = {c.field: c.value for c in ctx.claims}
    assert v["website_description"] == "Vi selger kompetanse ˆ råd & tekst."
    assert v["website_title"] == "Fjelltopp & Co – Bygg"
    assert v["statutory_purpose"] == "Drift av verksted. Utleie av utstyr."
    assert v["site_contact_email"] == "post@fjelltopptest.no"


# ---------------------------------------------------------------- summary
def _summary_ctx(purpose=None, desc=None, industry=("62.010", "Programmeringstjenester")):
    ctx = _ctx([])
    ev = ctx.evidence[0]
    ctx.add_claim("legal_form", "identity", {"code": "AS", "name": "Aksjeselskap"}, ev)
    ctx.add_claim("industry", "identity", {"code": industry[0], "name": industry[1]}, ev)
    if purpose:
        ctx.add_claim("statutory_purpose", "identity", purpose, ev)
    if desc:
        ctx.add_claim("website_description", "web_presence", desc, ev)
    ctx.module_states["registry"] = "available"
    return summarise(ctx, [])


def test_quoted_purpose_ends_its_sentence():
    out = _summary_ctx(purpose="Holdingselskap")
    assert 'Its registered purpose is: "Holdingselskap".' in out["text"]
    out = _summary_ctx(purpose="Drift av verksted.")
    assert 'Its registered purpose is: "Drift av verksted." ' in out["text"] + " "
    out = _summary_ctx(purpose="ord " * 80)
    assert '..." ' in out["text"] + " " and '...".' not in out["text"]


def test_summary_has_no_em_dash_and_states_unknown_industry_plainly():
    out = _summary_ctx(desc="Fjelltopp Test \u2014 bygg og anlegg i hele landet", industry=("00.000", "Uoppgitt"))
    assert "\u2014" not in out["text"] and "Fjelltopp Test \u2013 bygg" in out["text"]
    assert "Uoppgitt" not in out["text"] and "no industry stated in the registry (code 00.000)" in out["text"]


def test_summary_lists_read_as_prose():
    from kilde.synthesis import join_and
    assert join_and(["Facebook"]) == "Facebook"
    assert join_and(["Facebook", "Instagram"]) == "Facebook and Instagram"
    assert join_and(["A", "B", "C"]) == "A, B and C"
    ctx = _ctx([("social_profile", W.canonical_social("facebook", "https://www.facebook.com/fjelltopptest")),
                ("social_profile", W.canonical_social("instagram", "https://www.instagram.com/fjelltopptest"))])
    ev = ctx.evidence[0]
    for i, m in enumerate(["OSLO", "BERGEN", "BODØ", "MOLDE", "SKIEN", "HAMAR", "VOSS"]):
        ctx.add_claim("registered_workplace", "locations", {"name": f"Avd {i}", "municipality": m}, ev)
    text = summarise(ctx, [])["text"]
    assert "profiles on Facebook and Instagram." in text
    assert "It has 7 registered workplaces in Bergen, Bodø, Hamar, Molde, Oslo and 2 other municipalities." in text
