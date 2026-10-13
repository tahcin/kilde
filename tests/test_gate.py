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
