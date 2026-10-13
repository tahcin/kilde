"""The publication gate withholds website-derived facts that do not fit the registry."""
from kilde import gate
from kilde.model import CompanyContext, Snapshot


def ctx_with(site: str, registry: dict, derived: list[tuple[str, object]]) -> CompanyContext:
    ctx = CompanyContext("999999999")
    ctx.registry = registry
    s = Snapshot(url=site, final_url=site, status=200, retrieved_at="2026-10-13T08:00:00Z", content=b"x", content_type="text/html")
    ev = ctx.add_evidence(s, source_class="company_owned", span="x", method="t")
    ctx.add_claim("official_website", "web_presence", site, ev, match={"method": "orgnr_on_page"})
    for field, value in derived:
        ctx.add_claim(field, "web_presence", value, ev, match={"method": "site_link"})
    return ctx


def states(ctx):
    return {c.field: c.availability for c in ctx.claims if c.field != "official_website"}


def test_chain_site_without_our_name_withholds_everything():
    ctx = ctx_with("https://www.kjedetest.no/", {"navn": "FJELLTOPP RØR AS"},
                   [("social_profile", {"platform": "facebook", "url": "https://facebook.com/kjedetest"})])
    gate.check(ctx)
    assert states(ctx) == {"social_profile": "ambiguous"}
    assert ctx.claims[-1].value is None


def test_foreign_domain_for_norwegian_company_withholds_derived():
    ctx = ctx_with("https://fjelltopp.se/", {"navn": "FJELLTOPP AS", "hjemmeside": "fjelltopp.se",
                                             "forretningsadresse": {"landkode": "NO"}},
                   [("site_contact_email", "info@fjelltopp.se")])
    gate.check(ctx)
    assert states(ctx) == {"site_contact_email": "ambiguous"}


def test_own_site_keeps_matching_contacts_and_drops_others():
    ctx = ctx_with("https://www.fjelltopp.no/", {"navn": "FJELLTOPP AS", "telefon": "+47 22 33 44 55"},
                   [("site_contact_email", "post@fjelltopp.no"), ("site_contact_email", "x@kjedetest.no"),
                    ("site_contact_phone", "+4722334455"), ("site_contact_phone", "+46812345678")])
    gate.check(ctx)
    got = [(c.value, c.availability) for c in ctx.claims if c.field.startswith("site_contact")]
    assert got == [("post@fjelltopp.no", "available"), (None, "ambiguous"), ("+4722334455", "available"), (None, "ambiguous")]


def test_brand_domain_kept_only_when_connector_marks_it_exclusive():
    reg = {"navn": "SPORTTEST AS"}
    ctx = ctx_with("https://brandtest.no/", reg, [("site_contact_email", "post@brandtest.no")])
    ctx.facts["website_exclusive"] = True
    gate.check(ctx)
    assert states(ctx) == {"site_contact_email": "available"}
    ctx = ctx_with("https://brandtest.no/", reg, [("site_contact_email", "post@brandtest.no")])
    gate.check(ctx)
    assert states(ctx) == {"site_contact_email": "ambiguous"}


def test_template_social_links_are_withheld():
    from kilde.connectors.website import canonical_social
    ctx = ctx_with("https://www.hmdtest.no/", {"navn": "INTERIØR OG MALEFAG AS", "hjemmeside": "www.hmdtest.no"},
                   [("social_profile", canonical_social("facebook", "https://www.facebook.com/surftown.com")),
                    ("social_profile", canonical_social("facebook", "https://www.facebook.com/hmdtest")),
                    ("social_profile", canonical_social("facebook", "https://www.facebook.com/profile.php?id=100063562930121"))])
    gate.check(ctx)
    got = [(c.availability) for c in ctx.claims if c.field == "social_profile"]
    assert got == ["ambiguous", "available", "available"]


def test_profile_shared_by_two_companies_is_withheld():
    from kilde.connectors.website import canonical_social
    a = ctx_with("https://alfa.no/", {"navn": "ALFA TEST AS"}, [("social_profile", canonical_social("instagram", "https://instagram.com/byraet"))])
    b = ctx_with("https://beta.no/", {"navn": "BETA TEST AS"}, [("social_profile", canonical_social("instagram", "https://instagram.com/byraet"))])
    b.org = "888888888"
    gate.check_batch([a, b])
    assert all(c.availability == "ambiguous" for x in (a, b) for c in x.claims if c.field == "social_profile")


def test_handle_naming_a_foreign_sister_entity_is_withheld():
    from kilde.connectors.website import canonical_social
    ctx = ctx_with("https://www.hobarttest.no/", {"navn": "HOBARTTEST NORGE NUF", "organisasjonsform": {"kode": "NUF"}},
                   [("social_profile", canonical_social("youtube", "https://www.youtube.com/user/hobarttestgmbh")),
                    ("social_profile", canonical_social("facebook", "https://www.facebook.com/hobarttestnorge"))])
    gate.check(ctx)
    assert [c.availability for c in ctx.claims if c.field == "social_profile"] == ["ambiguous", "available"]


def test_platform_default_structured_data_is_withheld():
    ctx = ctx_with("https://www.ferskentest.no/", {"navn": "FERSKENTEST AS"},
                   [("structured_data_organization", {"name": "Shopify", "sameAs": ["https://twitter.com/shopify"]}),
                    ("structured_data_organization", {"name": "Ferskentest", "sameAs": ["https://facebook.com/ferskentest", "https://twitter.com/shopify"]})])
    gate.check(ctx)
    sd = [c for c in ctx.claims if c.field == "structured_data_organization"]
    assert sd[0].availability == "ambiguous" and sd[1].availability == "available"
    assert sd[1].value["sameAs"] == ["https://facebook.com/ferskentest"]


def test_tidy_dedupes_and_strips_markup():
    from kilde.connectors.website import canonical_social
    ctx = ctx_with("https://www.awattest.no/", {"navn": "AWATTEST AS"},
                   [("social_profile", canonical_social("facebook", "https://facebook.com/awattest")),
                    ("social_profile", canonical_social("facebook", "https://www.facebook.com/awattest/")),
                    ("website_description", "<p>Vi lager <b>gode</b> r&oslash;r.</p>")])
    gate.tidy(ctx)
    assert len([c for c in ctx.claims if c.field == "social_profile"]) == 1
    assert [c.value for c in ctx.claims if c.field == "website_description"] == ["Vi lager gode rør."]


def test_template_posts_are_withheld_as_news_and_page_updates():
    ctx = ctx_with("https://www.fjelltopp.no/", {"navn": "FJELLTOPP AS"},
                   [("dated_news", "Hello world! (2026-01-02T10:00:00+00:00)"),
                    ("site_page_updated", {"url": "https://www.fjelltopp.no/post/this-is-your-first-post", "lastmod": "2026-01-02"}),
                    ("dated_news", "Velkommen til bloggen (2026-01-02)"),
                    ("dated_news", "Ny hall åpnet (2026-01-03)")])
    gate.check(ctx)
    got = [(c.value, c.availability) for c in ctx.claims if c.field != "official_website"]
    assert got == [(None, "ambiguous"), (None, "ambiguous"), (None, "ambiguous"), ("Ny hall åpnet (2026-01-03)", "available")]
