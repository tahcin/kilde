"""Rules of scripts/merge_crawl_seeds.py on synthetic crawl matches for made-up companies."""
from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("merge_crawl_seeds", _SCRIPTS / "merge_crawl_seeds.py")
M = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(M)

A, B, C, D, E, F, G, H = ("999000002", "999000010", "999000029", "999000037", "999000045", "999000053",
                          "999000061", "999000088")


def key(d: str) -> str | None:
    """Stand-in for build_website_seeds.site_key (no tldextract in the test env): bare lower-case host."""
    d = d.lower().strip().strip(".")
    if "://" in d:
        d = d.split("://", 1)[1].split("/", 1)[0]
    d = d[4:] if d.startswith("www.") else d
    return None if not d or "." not in d or d.endswith("facebook.com") else d


def uni(**kw) -> dict:
    base = {
        A: {"name": "KORNBLOM BAKERI AS", "legal_form": "AS", "site": None},
        B: {"name": "PIKSELVERK AS", "legal_form": "AS", "site": None},
        C: {"name": "TINDEFJELL TURLAG SA", "legal_form": "SA", "site": None},
        D: {"name": "OLA NORDMANN", "legal_form": "ENK", "site": None},
        E: {"name": "GRANLI MEKANISKE AS", "legal_form": "AS", "site": None},
        F: {"name": "BETALPUNKT AS", "legal_form": "AS", "site": "betalpunkt.no"},
    }
    base.update(kw)
    return base


def m(org: str, dom: str, method: str = "orgnr", also=(), ctx: str = "Kornblom Bakeri AS Org.nr. ") -> dict:
    ev = {"url": f"https://{dom}/", "ctx": ctx + f"{org[:3]} {org[3:6]} {org[6:]}"} if method == "orgnr" else {}
    return {"org": org, "domain": dom, "method": method, "also": list(also), "evidence": ev,
            "name": "SHOULD NOT BE STORED", "legal_form": "AS"}


def test_classes_become_source_tags_and_only_domain_and_tags_are_stored():
    out, dropped = M.select_pairs([m(A, "kornblom.no", "orgnr", also=["multi_signal"]),
                                   m(C, "tindefjell.no", "strong_unique"), m(E, "granli.no", "multi_signal")],
                                  uni(), key)
    assert out == {A: {"kornblom.no": {"crawl_orgnr", "crawl_signals"}}, C: {"tindefjell.no": {"crawl_phone_email"}},
                   E: {"granli.no": {"crawl_signals"}}}
    assert not any(dropped.values())
    doc, counts = M.merge({"seeds": {A: [{"domain": "kornblom.no", "sources": ["osm_phone"]}]}}, out)
    assert doc["seeds"][A] == [{"domain": "kornblom.no", "sources": ["crawl_orgnr", "crawl_signals", "osm_phone"]}]
    assert "SHOULD NOT" not in json.dumps(doc)
    assert counts["orgs"] == 3 and counts["pairs_by_source"]["crawl_phone_email"] == 1


def test_enk_unknown_bad_and_non_company_hosts_are_dropped():
    out, dropped = M.select_pairs([m(D, "nordmann.no"), m(H, "x.no"), m("999000003", "y.no"),
                                   m(A, "facebook.com")], uni(), key)
    assert out == {}
    assert (dropped["enk"], dropped["not_in_universe"], dropped["bad_org"], dropped["non_company_host"]) == (1, 1, 1, 1)


def test_domain_claimed_by_two_orgs_across_classes_is_dropped():
    out, dropped = M.select_pairs([m(A, "felles.no", "orgnr"), m(C, "felles.no", "multi_signal")], uni(), key)
    assert out == {} and dropped["multi_org_domain"] == 2


def test_crowded_domain_and_other_orgnr_from_pages():
    pages = [{"t": "p", "final": "https://katalog.no/", "org": {A: "Org.nr. 999 000 002", B: "", C: ""}},
             {"t": "p", "final": "https://www.granli.no/kontakt", "org": {B: "Org.nr. 999 000 010"}}]
    printed, listing, before = M.printed_numbers(pages, key)
    out, dropped = M.select_pairs([m(A, "katalog.no"), m(E, "granli.no", "strong_unique")], uni(), key,
                                  printed, listing, before)
    assert out == {}
    assert dropped["crowded_domain"] == 1 and dropped["other_orgnr"] == 1


def test_shared_unnamed_and_credit_text_like_the_cc_merge():
    pages = [{"t": "p", "final": "https://bakeriet.no/", "org": {A: "Org.nr. 999 000 002", B: "Org.nr. 999 000 010"}}]
    printed, listing, before = M.printed_numbers(pages, key)
    out, dropped = M.select_pairs([m(B, "bakeriet.no")], uni(), key, printed, listing, before)
    assert out == {} and dropped["shared_unnamed"] == 1
    out, dropped = M.select_pairs([m(B, "kundesiden.no", ctx="Nettside utviklet av Pikselverk AS, org.nr. "),
                                   m(B, "pikselverk.no")], uni(), key)
    assert out == {B: {"pikselverk.no": {"crawl_orgnr"}}} and dropped["credit_text"] == 1


def test_org_with_registry_site_keeps_only_named_crawl_domains():
    out, dropped = M.select_pairs([m(F, "kassesystem.no", "multi_signal"), m(F, "betalpunkt-butikk.no", "multi_signal")],
                                  uni(), key)
    assert out == {F: {"betalpunkt-butikk.no": {"crawl_signals"}}} and dropped["other_site_unnamed"] == 1


def test_merge_is_idempotent_and_replaces_previous_crawl_sources(tmp_path):
    first, _ = M.select_pairs([m(A, "kornblom.no"), m(C, "tindefjell.no", "strong_unique")], uni(), key)
    doc, _ = M.merge({"seeds": {C: [{"domain": "tindefjell.no", "sources": ["overture_phone"]}]}}, first)
    second, _ = M.select_pairs([m(A, "kornblom.no")], uni(), key)
    doc2, counts = M.merge(doc, second)
    assert doc2["seeds"] == {A: [{"domain": "kornblom.no", "sources": ["crawl_orgnr"]}],
                             C: [{"domain": "tindefjell.no", "sources": ["overture_phone"]}]}
    assert counts["orgs_removed_since_previous_merge"] == 1
    doc3, _ = M.merge(doc2, second)
    p1, p2 = tmp_path / "a.json.gz", tmp_path / "b.json.gz"
    M.write_seeds(doc2, p1)
    M.write_seeds(doc3, p2)
    assert p1.read_bytes() == p2.read_bytes()


def test_truncated_pages_file_is_read_up_to_the_last_complete_line(tmp_path):
    p = tmp_path / "pages.jsonl.gz"
    data = gzip.compress(b'{"t":"p","final":"https://a.no/","org":{}}\n{"t":"p","final":"https://b.no/","org":{}}\n')
    p.write_bytes(data[:-12])
    got = [r["final"] for r in M.read_jsonl_gz(p)]
    assert got[:1] == ["https://a.no/"]
