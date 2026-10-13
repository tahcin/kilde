"""Build a Markdown audit pack for a human or agent auditor.

Usage:
  python scorer/audit_pack.py --envelopes out/envelopes.jsonl --n 30 --seed 7 --out audit_pack.md

Picks N envelopes, stratified so that companies with a verified website, job ads and social profiles are
all represented, then lists every available claim with its value, source URL and quoted span and a blank
verdict. A JSONL template (audit_pack.md -> audit_pack.audit.jsonl and audit_pack.pool.jsonl) is written
next to it: fill `verdict` in the audit file (correct | wrong_value | wrong_company | unsupported |
fabricated_financial) and add facts we missed to the pool file, then pass both to score.py.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score import AUDIT_VERDICTS, FAMILIES, family_of, read_jsonl, value_key  # noqa: E402


def _has(env: dict, fields: set[str]) -> bool:
    return any(c.get("availability") == "available" and (c.get("field") in fields or family_of(c.get("field")) == "social_profiles"
               and "social_profile" in fields) for c in env.get("claims") or [])


STRATA = [("website", {"official_website"}), ("jobs", {"job_ad", "careers_page"}), ("socials", {"social_profile"})]


def pick(envs: list[dict], n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    chosen: dict[str, dict] = {}
    per_stratum = max(1, n // 6)
    for _, fields in STRATA:
        pool = [e for e in envs if _has(e, fields) and e["organisation_number"] not in chosen]
        for e in rng.sample(pool, min(per_stratum, len(pool))):
            chosen[e["organisation_number"]] = e
    rest = [e for e in envs if e["organisation_number"] not in chosen]
    for e in rng.sample(rest, max(0, min(n - len(chosen), len(rest)))):
        chosen[e["organisation_number"]] = e
    return sorted(chosen.values(), key=lambda e: e["organisation_number"])


def _fmt(v) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s.replace("|", "\\|").replace("\n", " ")[:300]


def build(envs: list[dict]) -> tuple[str, list[dict]]:
    lines = ["# Pata audit pack", "",
             "For each claim: open the source URL, check the quoted span is there, check the value matches it and",
             "that the fact belongs to this organisation number. Verdicts: " + ", ".join(f"`{v}`" for v in AUDIT_VERDICTS) + ".",
             "`wrong_company` or `fabricated_financial` is a hard fail, so note the reason.", "",
             "Then search by hand for facts we missed (official website, social profiles linked from it, job ads,",
             "contact details, LEI, licences, news) and add them to the pool file as `{org, family, value_key}`.",
             "Families: " + ", ".join(f"`{f}`" for f in FAMILIES) + ". Value keys follow the `key` column format.", ""]
    template: list[dict] = []
    for e in envs:
        org = e["organisation_number"]
        evmap = {ev.get("id"): ev for ev in e.get("evidence") or []}
        lines += [f"## {e.get('legal_name') or org} ({org})", "",
                  "Sections: " + ", ".join(f"{k} {v.get('state')}" for k, v in (e.get("sections") or {}).items()), "",
                  "| claim | field | value | source | span | key | verdict |", "|---|---|---|---|---|---|---|"]
        for c in e.get("claims") or []:
            if c.get("availability") != "available":
                continue
            evs = [evmap[i] for i in c.get("evidence_ids") or [] if i in evmap]
            src = "<br>".join(ev.get("source_url", "") for ev in evs)
            span = "<br>".join(_fmt(ev.get("claim_span") or "") for ev in evs)
            key = value_key(c["field"], c.get("value"))
            lines.append(f"| {c['id']} | {c['field']} | {_fmt(c.get('value'))} | {src} | {span} | `{_fmt(key)}` | |")
            template.append({"org": org, "claim_id": c["id"], "field": c["field"], "verdict": "", "note": ""})
        lines += ["", "Missed facts (family, value key, source URL):", "", "- ", ""]
    return "\n".join(lines) + "\n", template


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write a Markdown audit pack")
    ap.add_argument("--envelopes", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    envs = pick(read_jsonl(a.envelopes), a.n, a.seed)
    md, template = build(envs)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(md)
    stem = os.path.splitext(a.out)[0]
    with open(stem + ".audit.jsonl", "w", encoding="utf-8") as fh:
        for row in template:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(stem + ".pool.jsonl", "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"org": "", "family": "", "value_key": "", "source_url": "", "note": "example row, delete"}) + "\n")
    print(f"audit pack: {len(envs)} companies, {len(template)} claims -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
