"""Kilde self-scorer: a mirror of Builderr Signalpost scoring version 2.

Recall 50, precision and evidence 30, synthesis 12, UX 8. Qualifying line 65.
Standard library only, so it runs in any Python 3.12.

Usage:
  python scorer/score.py --envelopes out/envelopes.jsonl [--pool pool.jsonl] [--audit audit.jsonl]
                         [--expected-count N] [--report-html out/report.html] --out score.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FAMILIES = [
    "identity_extras", "financials", "leadership", "locations", "official_website", "social_profiles",
    "contact", "hiring", "activity", "external_registers",
]

# Recall weight per family. None means "equal weight over families with at least MIN_POOL_COMPANIES".
FAMILY_WEIGHTS: dict[str, float] | None = None
MIN_POOL_COMPANIES = 3

# claim field -> family. Fields not listed here (legal_name, legal_form, addresses, ...) are base
# identity that every entry has, so they do not count towards recall.
FIELD_FAMILY: dict[str, str] = {
    # identity extras (registry facts that do not need a website)
    "share_capital": "identity_extras", "statutory_purpose": "identity_extras", "former_names": "identity_extras",
    "signature_rule": "identity_extras", "registered_activity": "identity_extras",
    "secondary_industry": "identity_extras", "register_memberships": "identity_extras",
    "part_of_group": "identity_extras", "group_parent": "identity_extras", "group_subsidiary": "identity_extras",
    "group_ultimate_parent": "identity_extras", "parent_organisation_number": "identity_extras",
    "incorporation_date": "identity_extras", "audit_opt_out_date": "identity_extras",
    # financials
    "revenue": "financials", "operating_result": "financials", "salary_costs": "financials",
    "profit_before_tax": "financials", "annual_result": "financials", "total_assets": "financials",
    "cash_and_bank": "financials", "equity": "financials", "debt": "financials",
    "accounts_audited": "financials", "annual_report_pdf": "financials", "latest_filed_accounts_year": "financials",
    # leadership
    "ceo": "leadership", "board_chair": "leadership", "board_deputy_chair": "leadership",
    "board_member": "leadership", "board_deputy_member": "leadership", "board_observer": "leadership",
    "auditor": "leadership", "accountant": "leadership", "contact_person": "leadership", "owner": "leadership",
    "partner": "leadership", "general_partner": "leadership", "business_manager": "leadership",
    "bankruptcy_trustee": "leadership", "norwegian_representative": "leadership", "co_owner": "leadership",
    "general_meeting_chair": "leadership", "other_role": "leadership",
    # locations
    "registered_workplace": "locations",
    # web
    "official_website": "official_website",
    "social_profile": "social_profiles",
    "site_email": "contact", "site_phone": "contact", "contact_email": "contact", "contact_phone": "contact",
    # hiring
    "job_ad": "hiring", "careers_page": "hiring",
    # activity
    "registry_change": "activity", "site_page_updated": "activity", "news": "activity", "news_item": "activity",
    # external registers
    "lei": "external_registers", "financial_licence": "external_registers", "ticker": "external_registers",
    "isin": "external_registers",
}
FIELD_PREFIX_FAMILY = [("social_", "social_profiles"), ("wikidata_", "external_registers"), ("lei_", "external_registers")]

BANNED_WORDS = ["leading", "best", "premier", "innovative", "trusted", "world-class", "ledende"]
# Claims whose value is the company's own words; a banned word quoted from one of these is allowed.
DESCRIPTION_FIELDS = {"website_description", "statutory_purpose", "registered_activity", "description"}
SECTIONS = ("identity", "financials", "leadership", "locations", "web_presence", "hiring", "activity")
STATES = ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed")
FINANCIAL_VALUE_FIELDS = {"revenue", "operating_result", "salary_costs", "profit_before_tax", "annual_result",
                          "total_assets", "cash_and_bank", "equity", "debt"}
AUDIT_VERDICTS = ("correct", "wrong_value", "wrong_company", "unsupported", "fabricated_financial")
HARD_FAIL_VERDICTS = {"wrong_company", "fabricated_financial"}

POINTS = {"recall": 50, "precision": 30, "synthesis": 12, "ux": 8}
QUALIFY = 65
UNSTOP_COVERAGE_LINE = 21  # out of 35, the older Unstop rubric

HEX64 = re.compile(r"^[0-9a-f]{64}$")
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|\+00:00)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{n}: invalid JSON ({exc})")
    return rows


def family_of(field: str | None) -> str | None:
    if not field:
        return None
    if field in FIELD_FAMILY:
        return FIELD_FAMILY[field]
    for prefix, fam in FIELD_PREFIX_FAMILY:
        if field.startswith(prefix):
            return fam
    return None


def _norm_text(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _norm_url(u: str) -> str:
    try:
        p = urlsplit(u.strip())
    except ValueError:
        return _norm_text(u)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/")
    return f"{host}{path}" + (f"?{p.query}" if p.query else "")


def _norm_phone(s: str) -> str:
    digits = re.sub(r"\D", "", s)
    return digits[2:] if digits.startswith("47") and len(digits) == 10 else digits


def value_key(field: str, value: Any) -> str:
    """Canonical key for one fact, used to compare our claims with auditor finds.

    Auditors writing pool.jsonl should use the same format (audit_pack.py prints it for every claim).
    """
    fam = family_of(field) or "other"
    v = value
    if fam == "financials" and isinstance(v, dict):
        kind = v.get("account_type") or "company"
        return f"{field}|{v.get('year')}|{kind}"
    if fam == "leadership" and isinstance(v, dict):
        return f"{field}|{_norm_text(v.get('organisation_number') or v.get('name') or '')}"
    if field == "registered_workplace" and isinstance(v, dict):
        return f"{field}|{_norm_text(v.get('organisation_number') or v.get('address') or v.get('name') or '')}"
    if field in ("official_website", "careers_page") and isinstance(v, str):
        return f"{field}|{_norm_url(v)}"
    if fam == "social_profiles":
        url = v.get("url") if isinstance(v, dict) else v
        return f"social_profile|{_norm_url(str(url))}"
    if field in ("site_phone", "contact_phone") and isinstance(v, str):
        return f"phone|{_norm_phone(v)}"
    if field in ("site_email", "contact_email") and isinstance(v, str):
        return f"email|{_norm_text(v)}"
    if field == "job_ad" and isinstance(v, dict):
        ident = v.get("id") or v.get("url") or f"{v.get('title')}|{v.get('published')}"
        return f"job_ad|{_norm_url(ident) if str(ident).startswith('http') else _norm_text(ident)}"
    if field == "registry_change" and isinstance(v, dict):
        return f"registry_change|{v.get('update_id') or v.get('date')}"
    if field in ("site_page_updated", "news", "news_item") and isinstance(v, dict):
        return f"{field}|{_norm_url(str(v.get('url') or v.get('title') or ''))}"
    if field == "lei" and isinstance(v, dict):
        return f"lei|{_norm_text(v.get('lei'))}"
    if isinstance(v, (dict, list)):
        return f"{field}|{_norm_text(json.dumps(v, sort_keys=True, ensure_ascii=False))}"
    return f"{field}|{_norm_text(v)}"


def span_in_snapshot(span: str, text: str) -> bool:
    """True if the span occurs in the snapshot, or (for JSON spans we rebuilt from a record) all its
    scalar essentials occur in it."""
    if span in text:
        return True
    squash = lambda s: re.sub(r"\s+", " ", s)
    if squash(span) in squash(text):
        return True
    cand = span.strip()
    if not cand.startswith("{") and not cand.startswith("["):
        cand = "{" + cand + "}"  # fragments like '"navn":"X","kapital":1'
    try:
        parsed = json.loads(cand)
    except json.JSONDecodeError:
        return False
    checks = list(_essentials(parsed))
    return bool(checks) and all(ok(text) for ok in checks)


def _essentials(obj: Any, key: str | None = None):
    """Yield predicates: a keyed scalar must appear as "key": value; a bare scalar must appear at all."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _essentials(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _essentials(v, None)
    elif obj is None or isinstance(obj, bool) or str(obj).strip() == "":
        return
    else:
        enc = json.dumps(obj, ensure_ascii=False)
        if key is not None:
            pat = re.compile(re.escape(json.dumps(key, ensure_ascii=False)) + r"\s*:\s*" + re.escape(enc) + r"(?![\w.])")
            yield lambda t, pat=pat: bool(pat.search(t))
        else:
            s = str(obj)
            yield lambda t, s=s, enc=enc: s in t or enc in t


def _is_iso_utc(s: Any) -> bool:
    if not isinstance(s, str) or not ISO_UTC.match(s):
        return False
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def check_gates(envs: list[dict], expected: int | None) -> dict:
    problems: list[str] = []
    orgs = [str(e.get("organisation_number")) for e in envs]
    if expected is not None and len(envs) != expected:
        problems.append(f"expected {expected} envelopes, got {len(envs)}")
    dupes = sorted({o for o in orgs if orgs.count(o) > 1}) if len(orgs) != len(set(orgs)) else []
    if dupes:
        problems.append(f"duplicate organisation numbers: {', '.join(dupes[:10])}")
    for e in envs:
        org = e.get("organisation_number")
        secs = e.get("sections") or {}
        for s in SECTIONS:
            st = (secs.get(s) or {}).get("state")
            if st not in STATES:
                problems.append(f"{org}: section {s} missing or invalid state ({st!r})")
        if not (e.get("run") or {}).get("terminal_status"):
            problems.append(f"{org}: run.terminal_status missing")
        if not isinstance(e.get("operations"), dict):
            problems.append(f"{org}: operations missing")
    return {"passed": not problems, "problems": problems[:200], "problem_count": len(problems)}


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------

def score_recall(envs: list[dict], pool_rows: list[dict]) -> dict:
    ours_cos: dict[str, set[str]] = defaultdict(set)
    ours_keys: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for e in envs:
        org = str(e.get("organisation_number"))
        for c in e.get("claims") or []:
            if c.get("availability") != "available":
                continue
            fam = family_of(c.get("field"))
            if not fam or fam not in FAMILIES:
                continue
            ours_cos[fam].add(org)
            ours_keys[fam].add((org, value_key(c["field"], c.get("value"))))
    pool_cos = {f: set(ours_cos[f]) for f in FAMILIES}
    pool_keys = {f: set(ours_keys[f]) for f in FAMILIES}
    run_orgs = {str(e.get("organisation_number")) for e in envs}
    for row in pool_rows:
        fam, org = row.get("family"), str(row.get("org"))
        if fam not in FAMILIES or org not in run_orgs:
            continue
        pool_cos[fam].add(org)
        pool_keys[fam].add((org, str(row.get("value_key"))))

    per: dict[str, dict] = {}
    for f in FAMILIES:
        pc, pk = len(pool_cos[f]), len(pool_keys[f])
        cov = len(ours_cos[f]) / pc if pc else 0.0
        fact = len(ours_keys[f] & pool_keys[f]) / pk if pk else 0.0
        per[f] = {"pool_companies": pc, "our_companies": len(ours_cos[f]), "company_coverage": round(cov, 4),
                  "pool_facts": pk, "our_facts": len(ours_keys[f]), "fact_recall": round(fact, 4),
                  "family_score": round(0.7 * cov + 0.3 * fact, 4)}

    if FAMILY_WEIGHTS:
        weights = {f: FAMILY_WEIGHTS.get(f, 0.0) for f in FAMILIES if per[f]["pool_companies"] > 0}
    else:
        weights = {f: 1.0 for f in FAMILIES if per[f]["pool_companies"] >= MIN_POOL_COMPANIES}
        if not weights:  # tiny batches: fall back to every family the pool has at all
            weights = {f: 1.0 for f in FAMILIES if per[f]["pool_companies"] > 0}
    total_w = sum(weights.values())
    points = 0.0
    for f in FAMILIES:
        w = weights.get(f, 0.0) / total_w if total_w else 0.0
        contrib = POINTS["recall"] * w * per[f]["family_score"]
        per[f]["weight"] = round(w, 4)
        per[f]["points"] = round(contrib, 3)
        points += contrib
    return {"points": round(points, 3), "families": per, "weighted_families": sorted(weights)}


# ---------------------------------------------------------------------------
# Precision and evidence
# ---------------------------------------------------------------------------

def check_claim_structure(c: dict, evmap: dict[str, dict], base_dir: str, sha_cache: dict) -> list[str]:
    errs: list[str] = []
    ids = c.get("evidence_ids") or []
    evs = [evmap[i] for i in ids if i in evmap]
    if not ids:
        errs.append("no evidence id")
    elif len(evs) != len(ids):
        errs.append("evidence id does not resolve")
    for ev in evs:
        url = ev.get("source_url") or ""
        if not re.match(r"^https?://", url):
            errs.append(f"{ev.get('id')}: source_url not http(s)")
        if not _is_iso_utc(ev.get("retrieved_at")):
            errs.append(f"{ev.get('id')}: retrieved_at not ISO UTC")
        if not HEX64.match(str(ev.get("content_sha256") or "")):
            errs.append(f"{ev.get('id')}: content_sha256 not 64 hex")
        span = ev.get("claim_span")
        if not (isinstance(span, str) and span.strip()):
            errs.append(f"{ev.get('id')}: claim_span empty")
        snap = ev.get("snapshot_path")
        if snap:
            full = os.path.normpath(os.path.join(base_dir, snap))
            if os.path.isfile(full) and os.path.realpath(full).startswith(os.path.realpath(base_dir)):
                if full not in sha_cache:
                    with open(full, "rb") as fh:
                        data = fh.read()
                    sha_cache[full] = (hashlib.sha256(data).hexdigest(), data.decode("utf-8", errors="replace"))
                digest, text = sha_cache[full]
                if digest != ev.get("content_sha256"):
                    errs.append(f"{ev.get('id')}: snapshot sha256 mismatch")
                if isinstance(span, str) and span.strip() and not span_in_snapshot(span, text):
                    errs.append(f"{ev.get('id')}: claim_span not found in snapshot")
    if c.get("field") in FINANCIAL_VALUE_FIELDS:
        v = c.get("value") if isinstance(c.get("value"), dict) else {}
        if not v.get("currency"):
            errs.append("financial claim without currency")
        if not (v.get("period_end") or v.get("year") or v.get("period")):
            errs.append("financial claim without period")
    if not c.get("match"):
        errs.append("match missing")
    return errs


def score_precision(envs: list[dict], audit_rows: list[dict], base_dir: str) -> dict:
    total = passed = 0
    failures: list[dict] = []
    reasons: dict[str, int] = defaultdict(int)
    sha_cache: dict = {}
    claim_index: set[tuple[str, str]] = set()
    for e in envs:
        org = str(e.get("organisation_number"))
        evmap = {ev.get("id"): ev for ev in e.get("evidence") or []}
        for c in e.get("claims") or []:
            if c.get("availability") != "available":
                continue
            claim_index.add((org, c.get("id")))
            total += 1
            errs = check_claim_structure(c, evmap, base_dir, sha_cache)
            if errs:
                for r in errs:
                    reasons[re.sub(r"^e\d+: ", "", r)] += 1
                if len(failures) < 50:
                    failures.append({"org": org, "claim_id": c.get("id"), "field": c.get("field"), "errors": errs})
            else:
                passed += 1
    structural = passed / total if total else 0.0

    verdicts: dict[str, int] = defaultdict(int)
    hard: list[str] = []
    unknown_claims = 0
    for row in audit_rows:
        v = row.get("verdict")
        if v not in AUDIT_VERDICTS:
            continue
        if (str(row.get("org")), row.get("claim_id")) not in claim_index:
            unknown_claims += 1
        verdicts[v] += 1
        if v in HARD_FAIL_VERDICTS:
            hard.append(f"{row.get('org')} {row.get('claim_id')}: {v} ({row.get('note') or 'no note'})")
    audited = sum(verdicts.values())
    correct_rate = verdicts["correct"] / audited if audited else 1.0
    pts = POINTS["precision"] * structural * correct_rate
    return {"points": round(pts, 3), "available_claims": total, "structural_pass": passed,
            "structural_rate": round(structural, 4), "failure_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])),
            "failures_sample": failures, "audited": audited, "audit_verdicts": dict(verdicts),
            "audited_correct_rate": round(correct_rate, 4), "audit_unmatched_claims": unknown_claims,
            "audit_note": None if audited else "no audit file: audited rate assumed 1.0",
            "hard_fail_reasons": hard}


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

QUOTE_RE = re.compile(r"\"([^\"]+)\"|“([^”]+)”|«([^»]+)»")
UNKNOWN_HINTS = re.compile(r"\b(not|no|unknown|unavailable|could not|blocked|ikke)\b", re.I)


def check_summary(e: dict) -> list[str]:
    errs: list[str] = []
    s = e.get("summary") or {}
    sentences = s.get("sentences") or []
    claims = {c.get("id"): c for c in e.get("claims") or []}
    if not sentences:
        return ["no summary sentences"]
    for i, sent in enumerate(sentences, 1):
        text = sent.get("text") or ""
        ids = sent.get("claim_ids") or []
        if not ids:
            errs.append(f"sentence {i} cites no claim")
            continue
        missing = [cid for cid in ids if cid not in claims]
        if missing:
            errs.append(f"sentence {i} cites unknown claims {missing}")
        if not any(claims.get(cid, {}).get("availability") == "available" for cid in ids):
            errs.append(f"sentence {i} cites no available claim (gaps belong in summary.unknowns)")
        cited_desc = " ".join(_norm_text(json.dumps(claims[c].get("value"), ensure_ascii=False))
                              for c in ids if c in claims and claims[c].get("field") in DESCRIPTION_FIELDS)
        stripped = text
        for m in QUOTE_RE.finditer(text):
            quoted = next(g for g in m.groups() if g)
            if cited_desc and _norm_text(quoted).strip(" .") in cited_desc:
                stripped = stripped.replace(m.group(0), " ")
        for w in BANNED_WORDS:
            if re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", stripped, re.I):
                errs.append(f"sentence {i} uses unsupported word '{w}'")
    secs = e.get("sections") or {}
    has_gap = any((secs.get(k) or {}).get("state") == "not_available" for k in SECTIONS)
    if has_gap:
        unknowns = s.get("unknowns") or []
        full = s.get("text") or " ".join(x.get("text") or "" for x in sentences)
        if not unknowns and not UNKNOWN_HINTS.search(full):
            errs.append("a section is not_available but the summary names no unknowns")
    return errs


def score_synthesis(envs: list[dict]) -> dict:
    ok = 0
    fails: list[dict] = []
    for e in envs:
        errs = check_summary(e)
        if errs:
            if len(fails) < 50:
                fails.append({"org": e.get("organisation_number"), "errors": errs})
        else:
            ok += 1
    rate = ok / len(envs) if envs else 0.0
    return {"points": round(POINTS["synthesis"] * rate, 3), "pass_rate": round(rate, 4), "passed": ok,
            "failures_sample": fails}


# ---------------------------------------------------------------------------
# UX
# ---------------------------------------------------------------------------

def score_ux(path: str | None) -> dict:
    if not path:
        return {"points": 0.0, "checked": False, "note": "no --report-html given", "checks": {}}
    checks: dict[str, bool] = {}
    exists = os.path.isfile(path)
    checks["exists"] = exists
    html = ""
    size = 0
    if exists:
        size = os.path.getsize(path)
        with open(path, encoding="utf-8", errors="replace") as fh:
            html = fh.read()
    low = html.lower()
    checks["viewport_meta"] = bool(re.search(r"<meta[^>]+name=[\"']viewport[\"']", low))
    checks["search_input"] = bool(re.search(r"<input[^>]+type=[\"']search[\"']", low)) or 'role="search"' in low
    checks["company_anchors"] = bool(re.search(r"id=[\"']org-\d{9}[\"']", html)) or "#org-" in html or "'org-'" in html or '"org-"' in html
    checks["evidence_links"] = (re.search(r"<a[^>]+href=[\"']https?://", html) is not None
                                or ("href" in low and re.search(r"[\"']https?://[^\"']+[\"']", html) is not None))
    checks["availability_badges"] = "badge" in low and all(s in html for s in ("not_available", "blocked", "ambiguous"))
    checks["responsive_css"] = "@media" in low
    checks["under_15mb"] = exists and size < 15 * 1024 * 1024
    rate = sum(checks.values()) / len(checks)
    return {"points": round(POINTS["ux"] * rate, 3), "checked": True, "checks": checks, "size_bytes": size}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def score(envs: list[dict], *, pool_rows: list[dict] | None = None, audit_rows: list[dict] | None = None,
          expected: int | None = None, report_html: str | None = None, base_dir: str = ".") -> dict:
    gates = check_gates(envs, expected)
    recall = score_recall(envs, pool_rows or [])
    precision = score_precision(envs, audit_rows or [], base_dir)
    synthesis = score_synthesis(envs)
    ux = score_ux(report_html)
    total = recall["points"] + precision["points"] + synthesis["points"] + ux["points"]
    hard_reasons = list(precision["hard_fail_reasons"])
    hard = bool(hard_reasons)
    coverage35 = recall["points"] / POINTS["recall"] * 35
    return {
        "companies": len(envs),
        "total": round(total, 3),
        "qualifies": total >= QUALIFY and gates["passed"] and not hard,
        "qualify_line": QUALIFY,
        "hard_fail": hard,
        "hard_fail_reasons": hard_reasons,
        "gates": gates,
        "recall": recall,
        "precision": precision,
        "synthesis": synthesis,
        "ux": ux,
        "unstop_view": {"coverage_35": round(coverage35, 3), "line": UNSTOP_COVERAGE_LINE,
                        "meets_line": coverage35 >= UNSTOP_COVERAGE_LINE},
    }


def render_table(res: dict) -> str:
    lines = []
    lines.append(f"Companies {res['companies']}   gates {'PASS' if res['gates']['passed'] else 'FAIL'}"
                 f"   hard fail {'YES' if res['hard_fail'] else 'no'}")
    lines.append("")
    lines.append(f"{'family':<20}{'pool cos':>9}{'ours':>6}{'cov':>7}{'pool facts':>11}{'ours':>6}{'fact':>7}{'wt':>6}{'pts':>7}")
    for f, d in res["recall"]["families"].items():
        lines.append(f"{f:<20}{d['pool_companies']:>9}{d['our_companies']:>6}{d['company_coverage']:>7.2f}"
                     f"{d['pool_facts']:>11}{d['our_facts']:>6}{d['fact_recall']:>7.2f}{d['weight']:>6.2f}{d['points']:>7.2f}")
    lines.append("")
    p = res["precision"]
    lines.append(f"{'Recall':<22}{res['recall']['points']:>7.2f} / 50")
    lines.append(f"{'Precision/evidence':<22}{p['points']:>7.2f} / 30   structural {p['structural_rate']:.3f}"
                 f" ({p['structural_pass']}/{p['available_claims']}), audited correct {p['audited_correct_rate']:.3f}"
                 f" (n={p['audited']})")
    lines.append(f"{'Synthesis':<22}{res['synthesis']['points']:>7.2f} / 12   pass {res['synthesis']['pass_rate']:.3f}")
    ux = res["ux"]
    lines.append(f"{'UX':<22}{ux['points']:>7.2f} / 8    " +
                 (", ".join(k for k, v in ux["checks"].items() if not v) or "all checks pass" if ux["checked"] else ux["note"]))
    lines.append(f"{'TOTAL':<22}{res['total']:>7.2f} / 100  {'QUALIFIES' if res['qualifies'] else 'below line'} (65)")
    u = res["unstop_view"]
    lines.append(f"Unstop view: coverage {u['coverage_35']:.2f} / 35 (line 21: {'met' if u['meets_line'] else 'not met'})")
    if res["hard_fail_reasons"]:
        lines.append("HARD FAIL: " + "; ".join(res["hard_fail_reasons"][:5]))
    if not res["gates"]["passed"]:
        lines.append("Gate problems: " + "; ".join(res["gates"]["problems"][:5]))
    if p["failure_reasons"]:
        lines.append("Top evidence issues: " + "; ".join(f"{k} x{v}" for k, v in list(p["failure_reasons"].items())[:5]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kilde self-scorer (Builderr Signalpost scoring v2 mirror)")
    ap.add_argument("--envelopes", required=True)
    ap.add_argument("--pool", help="JSONL of auditor finds: {org, family, value_key}")
    ap.add_argument("--audit", help="JSONL of audit verdicts: {org, claim_id, verdict, note}")
    ap.add_argument("--expected-count", type=int)
    ap.add_argument("--report-html")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    envs = read_jsonl(a.envelopes)
    res = score(envs, pool_rows=read_jsonl(a.pool) if a.pool else None,
                audit_rows=read_jsonl(a.audit) if a.audit else None, expected=a.expected_count,
                report_html=a.report_html, base_dir=os.path.dirname(os.path.abspath(a.envelopes)))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, ensure_ascii=False)
    print(render_table(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
