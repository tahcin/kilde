"""Rules of scripts/merge_cc_seeds.py on synthetic pages for made-up companies."""
from __future__ import annotations

import gzip
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "merge_cc_seeds", Path(__file__).resolve().parents[1] / "scripts" / "merge_cc_seeds.py")
M = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(M)

A, B, C, D, E, F, G, H = ("999000002", "999000010", "999000029", "999000037", "999000045", "999000053",
                          "999000061", "999000088")


def key(d: str) -> str | None:
    """Stand-in for build_website_seeds.site_key (no tldextract in the test env): bare lower-case host."""
    d = d.lower().strip().strip(".")
    d = d[4:] if d.startswith("www.") else d
    return None if not d or "." not in d or d.endswith("facebook.com") else d


def uni(**kw) -> dict:
    base = {
        A: {"name": "KORNBLOM BAKERI AS", "legal_form": "AS", "site": None},
        B: {"name": "PIKSELVERK AS", "legal_form": "AS", "site": None},       # a web agency
        C: {"name": "TINDEFJELL TURLAG SA", "legal_form": "SA", "site": None},
        D: {"name": "OLA NORDMANN", "legal_form": "ENK", "site": None},
        E: {"name": "GRANLI MEKANISKE AS", "legal_form": "AS", "site": None},
        F: {"name": "BETALPUNKT AS", "legal_form": "AS", "site": "betalpunkt.no"},
        G: {"name": "SOLBRATT HOLDING AS", "legal_form": "AS", "site": None},
    }
    base.update(kw)
    return base


def page(dom: str, **orgs: str) -> dict:
    return {"domain": dom, "orgs": {o.lstrip("_"): ctx for o, ctx in orgs.items()}}


def pg(dom: str, *orgs: str, ctx: str = "Org.nr. ") -> dict:
    return {"domain": dom, "orgs": {o: ctx for o in orgs}}


def test_single_org_page_is_kept_and_normalised():
    out, dropped = M.select_pairs([pg("www.Kornblom.no", A)], uni(), key)
    assert out == {A: ["kornblom.no"]}
    assert not any(dropped.values())


def test_enk_unknown_and_bad_numbers_are_dropped():
    pages = [pg("nordmann.no", D), pg("x.no", H), pg("y.no", "999000003")]
    out, dropped = M.select_pairs(pages, uni(), key)
    assert out == {}
    assert dropped["enk"] == 1 and dropped["not_in_universe"] == 1 and dropped["bad_org"] == 1


def test_non_company_hosts_are_dropped():
    pages = [pg("proff.no", A), pg("oslo.kommune.no", A), pg("www.facebook.com", A), pg("regjeringen.no", E)]
    out, dropped = M.select_pairs(pages, uni(), key)
    assert out == {}
    assert dropped["non_company_host"] == 4


def test_domain_with_three_org_numbers_is_a_listing():
    # across pages: three different numbers on one domain (an accountant's client list)
    pages = [pg("regnskapshuset.no", A), pg("regnskapshuset.no", E), pg("regnskapshuset.no", G)]
    out, dropped = M.select_pairs(pages, uni(), key)
    assert out == {} and dropped["crowded_domain"] == 3
    # one page that prints three numbers
    out, _ = M.select_pairs([pg("kornblom.no", A, E, G)], uni(), key)
    assert out == {}


def test_shared_domain_keeps_the_org_it_is_named_for_and_flags_the_other_as_a_supplier():
    pages = [pg("kornblom.no", A, B), pg("granli-mek.no", E, B), pg("pikselverk.no", B),
             pg("tindefjell.no", B)]
    out, dropped = M.select_pairs(pages, uni(), key)
    assert out[A] == ["kornblom.no"] and out[E] == ["granli-mek.no"]
    # the agency keeps only its own named domain, not the client site it alone appears on
    assert out[B] == ["pikselverk.no"]
    assert dropped["shared_unnamed"] == 2 and dropped["credited_elsewhere"] == 1


def test_shared_domain_counts_numbers_outside_the_universe_and_enk():
    # the site owner is a sole proprietor (dropped), the agency is the only universe number left
    out, dropped = M.select_pairs([pg("nordmannsmia.no", D, B)], uni(), key)
    assert out == {} and dropped["shared_unnamed"] == 1


def test_two_group_companies_both_named_are_kept():
    u = uni(**{G: {"name": "GRANLI MEKANISKE HOLDING AS", "legal_form": "AS", "site": None}})
    out, _ = M.select_pairs([pg("granlimekaniske.no", E, G)], u, key)
    assert out == {E: ["granlimekaniske.no"], G: ["granlimekaniske.no"]}


def test_credit_text_before_the_number_drops_an_unnamed_supplier():
    pages = [page("kafe-sol.no", **{B: "Nettside utviklet av Pikselverk AS, org.nr. "}),
             page("pikselverk.no", **{B: "Pikselverk AS laget av oss, org.nr. "})]
    out, dropped = M.select_pairs(pages, uni(), key)
    assert out == {B: ["pikselverk.no"]}  # named domain survives even with credit-like wording
    assert dropped["credit_text"] == 1
    out, dropped = M.select_pairs([page("butikk.no", **{F: "Betalinger behandles av Betalpunkt AS (org.nr "})],
                                  uni(**{F: {"name": "BETALPUNKT AS", "legal_form": "AS", "site": None}}), key)
    assert out == {} and dropped["credit_text"] == 1


def test_operator_wording_is_not_a_credit():
    out, _ = M.select_pairs([page("tursiden.no", **{C: "Siden drives av Tindefjell Turlag SA, org.nr "})], uni(), key)
    assert out == {C: ["tursiden.no"]}


def test_org_on_many_domains_keeps_only_named_ones():
    pages = [pg(d, G) for d in ("a-brand.no", "b-brand.no", "c-brand.no", "solbratt.no")]
    out, dropped = M.select_pairs(pages, uni(), key)
    assert out == {G: ["solbratt.no"]} and dropped["many_domains"] == 3
    out, _ = M.select_pairs(pages[:3], uni(), key)  # three brand sites: kept
    assert out == {G: ["a-brand.no", "b-brand.no", "c-brand.no"]}


def test_org_with_a_registry_site_keeps_only_named_cc_domains():
    pages = [pg("nettbutikken.no", F), pg("betalpunkt-pay.no", F), pg("betalpunkt.no", F)]
    out, dropped = M.select_pairs(pages, uni(), key)
    assert out == {F: ["betalpunkt-pay.no", "betalpunkt.no"]} and dropped["other_site_unnamed"] == 1


@pytest.mark.parametrize("dom,name,ok", [
    ("kornblom.no", "KORNBLOM BAKERI AS", True),
    ("xn--sosrr-yua.no", "SOS RØR AS", True),
    ("vaapenloftet.no", "VÅPENLOFTET AS", True),
    ("vapenloftet.no", "VÅPENLOFTET AS", True),
    ("tabs.no", "AUTOSKOLEN AS", False),
    ("bygg.no", "BYGG HOLDING AS", False),       # generic words only
    ("abcbygg.no", "A B C BYGG AS", True),       # initials run together
])
def test_domain_names_org(dom, name, ok):
    assert M.domain_names_org(dom, name, None) is ok


def _doc(seeds):
    return {"schema": 1, "stats": {}, "licences": {}, "seeds": seeds}


def test_merge_replaces_commoncrawl_and_keeps_other_sources():
    doc = _doc({A: [{"domain": "kornblom.no", "sources": ["commoncrawl_domain", "osm_phone"]},
                    {"domain": "gammel.no", "sources": ["commoncrawl"]}],
                E: [{"domain": "granli.no", "sources": ["commoncrawl", "subunit"]}],
                G: [{"domain": "solbratt.no", "sources": ["commoncrawl"]}]})
    new, counts = M.merge(doc, {A: ["kornblom.no"], C: ["tindefjell.no"]})
    assert new["seeds"][A] == [{"domain": "kornblom.no", "sources": ["commoncrawl", "commoncrawl_domain", "osm_phone"]}]
    assert new["seeds"][E] == [{"domain": "granli.no", "sources": ["subunit"]}]
    assert G not in new["seeds"]
    assert new["seeds"][C] == [{"domain": "tindefjell.no", "sources": ["commoncrawl"]}]
    assert counts["orgs_new_to_cache"] == 1 and counts["pairs_domain_already_seeded"] == 1
    again, _ = M.merge(new, {A: ["kornblom.no"], C: ["tindefjell.no"]})
    assert json.dumps(again["seeds"]) == json.dumps(new["seeds"])  # idempotent and ordered


def _state(path: Path, rows):
    db = sqlite3.connect(path)
    db.execute("create table rec(id integer primary key, dom text, url text, fn text, off integer, len integer, "
               "status text, ts text, orgs text, tries integer default 0)")
    db.executemany("insert into rec(dom,url,status,orgs) values (?,?,?,?)", rows)
    db.commit()
    db.close()


def test_pages_from_state_reads_every_number_and_skips_unfinished(tmp_path):
    p = tmp_path / "state.sqlite"
    _state(p, [("kornblom.no", "https://kornblom.no/", "ok", json.dumps({A: "Org.nr ", B: "levert av "})),
               ("x.no", "https://x.no/", "todo", None), ("y.no", "https://y.no/", "ok", "{}")])
    pages, records = M.pages_from_state(p)
    assert pages == [{"domain": "kornblom.no", "orgs": {A: "Org.nr ", B: "levert av "}}]
    assert records == {"records_ok": 2, "records_todo": 1}


def test_main_end_to_end_is_byte_identical_on_rerun(tmp_path):
    pytest.importorskip("tldextract")
    st = tmp_path / "state.sqlite"
    _state(st, [("kornblom.no", "https://www.kornblom.no/kontakt", "ok", json.dumps({A: "Org.nr "}))])
    un = tmp_path / "u.jsonl.gz"
    with gzip.open(un, "wt") as fh:
        fh.write(json.dumps({"organisation_number": A, "name": "KORNBLOM BAKERI AS", "legal_form": "AS",
                             "website": ""}) + "\n")
    seeds = tmp_path / "seeds.json.gz"
    with gzip.open(seeds, "wt") as fh:
        json.dump(_doc({}), fh)
    out1, out2, out3 = tmp_path / "o1.json.gz", tmp_path / "o2.json.gz", tmp_path / "o3.json.gz"
    for out in (out1, out2):
        M.main(["--state", str(st), "--universe", str(un), "--seeds", str(seeds), "--out", str(out)])
    M.main(["--state", str(st), "--universe", str(un), "--seeds", str(out1), "--out", str(out3)])
    assert json.load(gzip.open(out1, "rt"))["seeds"] == {A: [{"domain": "kornblom.no", "sources": ["commoncrawl"]}]}
    assert out1.read_bytes() == out2.read_bytes()  # same inputs, same bytes
    assert json.load(gzip.open(out3, "rt"))["seeds"] == json.load(gzip.open(out1, "rt"))["seeds"]  # re-merge
