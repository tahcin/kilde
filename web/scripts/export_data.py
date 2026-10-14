"""Turn Kilde envelopes into the static JSON the website reads.

    python web/scripts/export_data.py --out web/public/data \
        bench=path/to/bench/envelopes.jsonl sample=path/to/run1000/envelopes.jsonl ...

Each positional argument is `tag=path` (or just a path). The first file that has an org number wins, so put the
file whose copy you prefer first. Writes:

    index.json      one row per company, for search, lists and compare
    c/<org>.json.gz the profile the company page shows: summary, every claim with its state, and its evidence
    llm/<org>.txt.gz the same facts as plain text lines with claim ids, for the /api/ask worker (never shown)
    meta.json       totals for the home page, computed from these files only

Nothing here computes or converts a financial figure: amounts, currencies, periods and account types are copied from
the envelopes as Kilde claimed them. Values Kilde withheld (ambiguous, failed) stay withheld: only their state and
reason are kept. Envelopes carry no birth dates (Kilde drops them before hashing); the script checks that anyway.
Profiles and texts are stored gzipped (with a fixed timestamp, so the same input gives the same bytes): about a
seventh of the size. The site and the worker decompress them with the browser's own DecompressionStream.
Standard library only, so it runs without the Python package.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

FAMILIES = {"official_website": "w", "social_profile": "s", "hiring_signal": "h", "dated_news": "n"}
FIN = ["revenue", "operating_result", "salary_costs", "profit_before_tax", "annual_result", "total_assets",
       "cash_and_bank", "equity", "debt"]
BIRTH = re.compile(r"f(ø|o)dsels?dato|birth_?date|date_of_birth", re.I)

LABEL = {
    "legal_name": "Legal name", "former_names": "Former names", "legal_form": "Legal form",
    "registration_date": "Registered in Enhetsregisteret", "business_register_date": "Registered in Foretaksregisteret",
    "articles_date": "Articles of association dated", "incorporation_date": "Founded (incorporated)",
    "business_address": "Business address", "postal_address": "Postal address", "municipality": "Municipality",
    "industry": "Industry (registered)", "secondary_industry": "Secondary industry", "institutional_sector": "Institutional sector",
    "statutory_purpose": "Statutory purpose", "registered_activity": "Registered activity", "share_capital": "Share capital",
    "employees_registered": "Employees (registry)", "employees_reported": "Employees (Wikidata, often group-wide)",
    "legal_status": "Legal status", "register_memberships": "Register memberships", "signature_rule": "Signature rule",
    "part_of_group": "Part of a group", "group_parent": "Parent company", "group_subsidiary": "Subsidiary",
    "group_ultimate_parent": "Ultimate parent", "deleted_date": "Deleted from the registry",
    "registry_email": "Email (registry)", "registry_phone": "Phone (registry)", "registry_mobile": "Mobile (registry)",
    "registry_website": "Website (as registered)", "lei": "Legal Entity Identifier (LEI)", "lei_direct_parent": "LEI direct parent",
    "lei_ultimate_parent": "LEI ultimate parent", "financial_licence": "Financial licence (Finanstilsynet)",
    "ticker": "Ticker", "isin": "ISIN", "stock_listing": "Stock listing", "wikidata_item": "Wikidata item",
    "headquarters": "Headquarters (Wikidata)", "inception_date": "Inception (Wikidata)", "logo": "Logo (Wikimedia Commons)",
    "revenue": "Revenue", "operating_result": "Operating result", "salary_costs": "Salary costs",
    "profit_before_tax": "Profit before tax", "annual_result": "Annual result (net profit or loss)", "total_assets": "Total assets",
    "cash_and_bank": "Cash and bank deposits", "equity": "Equity", "debt": "Debt", "accounts_audited": "Accounts audited",
    "annual_report_pdf": "Filed annual report", "latest_filed_accounts_year": "Latest filed accounts year",
    "audit_opt_out_date": "Opted out of audit from",
    "ceo": "General manager (daglig leder)", "board_chair": "Board chair", "board_deputy_chair": "Deputy board chair",
    "board_member": "Board member", "board_deputy_member": "Deputy board member", "board_observer": "Board observer",
    "auditor": "Auditor", "accountant": "Accountant", "contact_person": "Contact person", "owner": "Owner (sole proprietor)",
    "partner": "Partner", "general_partner": "General partner", "business_manager": "Business manager",
    "norwegian_representative": "Norwegian representative", "other_role": "Other registered role",
    "registered_workplace": "Registered workplace (sub-unit)", "official_website": "Verified website",
    "official_website_url": "Verified website address", "related_website": "Related website (not the company's own)",
    "website_description": "Website description (from the site)", "website_title": "Website title",
    "site_language": "Website language", "structured_data_organization": "Organisation data on the website",
    "social_profile": "Social profile (linked from the verified site)", "site_contact_email": "Email on the website",
    "site_contact_phone": "Phone on the website", "public_contract_award": "Public contract won (EU TED)",
    "procurement_notice": "Tender published as buyer (EU TED)", "research_project": "Research project or SkatteFUNN approval",
    "food_inspection": "Food safety inspection (Mattilsynet)", "approval_sentral_godkjenning": "Central approval for building work (DiBK)",
    "state_aid_award": "State aid award (Støtteregisteret)", "job_ad": "Job ad (NAV)", "active_job_count": "Active NAV job ads",
    "hiring_signal": "Careers page or job posting", "dated_news": "Dated news post on the verified site",
    "registry_change": "Registry update (last 12 months)", "site_page_updated": "Website page updated (sitemap)",
}
SECTION = {"identity": "Identity and registration", "financials": "Accounts", "leadership": "Leadership and roles",
           "locations": "Workplaces", "web_presence": "Website and social profiles", "hiring": "Hiring",
           "activity": "Dated activity and public records"}
STATE = {"not_available": "checked, nothing found", "ambiguous": "a candidate was found but could not be proven to be this company",
         "blocked": "the source refused automated access", "failed": "the check did not finish in this run",
         "not_applicable": "does not apply"}
# Long lists are cut in the text the language model reads (never in the profile shown on the site).
LLM_CAP = {"registry_change": 12, "group_subsidiary": 25, "site_page_updated": 5, "dated_news": 15, "job_ad": 15,
           "state_aid_award": 10, "public_contract_award": 10, "research_project": 10, "registered_workplace": 15,
           "food_inspection": 5, "board_deputy_member": 10}
DROP_KEYS = {"source_file", "update_id", "exchange_qid", "buyer_organisation_number", "employer_orgnr"}


def read_envelopes(path: str):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def gz(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0)


def num(x) -> str:
    """An amount as text, exactly as filed: 194000000.0 -> 194000000, 1234.5 stays 1234.5."""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def flat(field: str, v) -> str:
    """A claim value as one line of plain text for the language model. Copies values; never computes."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return num(v)
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "; ".join(flat(field, x) for x in v)
    if field in FIN and v.get("amount") is not None:
        return f"{num(v['amount'])} {v.get('currency') or '(currency not stated)'}"
    if field == "share_capital" and v.get("amount") is not None:
        extra = f", {v['shares']} shares" if v.get("shares") is not None else ""
        return f"{num(v['amount'])} {v.get('currency') or ''}{extra}".strip()
    if field == "accounts_audited":
        return "yes" if v.get("audited") else "no"
    if "role_code" in v or ("role" in v and "name" in v):
        return v.get("name", "") + (f" (org. no. {v['organisation_number']})" if v.get("organisation_number") else "")
    if field in ("legal_form", "industry", "secondary_industry", "institutional_sector") and "name" in v:
        return f"{v['name']} (code {v.get('code')})"
    if field == "municipality":
        return f"{v.get('name')} (municipality no. {v.get('number')})"
    if field == "legal_status":
        on = [k for k, label in (("konkurs", "bankrupt"), ("underAvvikling", "being wound up"),
                                 ("underTvangsavviklingEllerTvangsopplosning", "being forcibly dissolved")) if v.get(k)]
        return ", ".join(on) if on else "active: no bankruptcy, winding-up or forced dissolution registered"
    if field == "register_memberships":
        return ", ".join(f"{k.replace('registrertI', '')}: {'yes' if b else 'no'}" for k, b in v.items())
    if field == "registry_change":
        ch = v.get("changed") or []
        return f"{v.get('date')}: {v.get('type') or 'update'}" + (f", changed: {', '.join(ch)}" if ch else "")
    parts = []
    for k, x in v.items():
        if k in DROP_KEYS or x is None or x == "" or x == []:
            continue
        if isinstance(x, list):
            x = "; ".join(json.dumps(i, ensure_ascii=False) if isinstance(i, dict) else str(i) for i in x)
        elif isinstance(x, dict):
            x = json.dumps(x, ensure_ascii=False)
        parts.append(f"{k.replace('_', ' ')}: {num(x) if isinstance(x, float) else x}")
    return "; ".join(parts)


def fin_label(field: str, v: dict) -> str:
    kind = "group accounts" if v.get("account_type") == "group" else "company accounts"
    period = f"{v.get('period_start')} to {v.get('period_end')}" if v.get("period_start") else ""
    return f"{LABEL.get(field, field)}, {kind} {v.get('year')}" + (f" ({period})" if period else "")


def llm_text(env: dict) -> str:
    org = str(env["organisation_number"])
    run = env.get("run") or {}
    lines = [f"COMPANY {org}: {env.get('legal_name') or ''}",
             f"Kilde profile retrieved {str(run.get('completed_at') or '')[:10]} from public sources. "
             f"Cite facts as [{org}:<claim id>]."]
    summary = (env.get("summary") or {}).get("sentences") or []
    if summary:
        lines.append("")
        lines.append("Kilde's own summary (each sentence ends with the claims it rests on):")
        for s in summary:
            ids = ",".join(f"{org}:{i}" for i in (s.get("claim_ids") or [])[:8])
            lines.append(f"- {s.get('text', '')} [{ids}]")
    claims = env.get("claims") or []
    for sec, title in SECTION.items():
        sc = [c for c in claims if c.get("section") == sec]
        if not sc:
            continue
        lines.append("")
        lines.append(f"## {title}")
        seen: Counter = Counter()
        hidden: Counter = Counter()
        for c in sc:
            f = c.get("field")
            if c.get("availability") == "available":
                seen[f] += 1
                if f in LLM_CAP and seen[f] > LLM_CAP[f]:
                    hidden[f] += 1
                    continue
                v = c.get("value")
                label = fin_label(f, v) if f in FIN and isinstance(v, dict) else LABEL.get(f, f.replace("_", " "))
                when = f" (as of {c['as_of']})" if c.get("as_of") and f not in FIN and f not in ("registry_change",) else ""
                note = f" Note: {c['note']}" if c.get("note") else ""
                lines.append(f"[{org}:{c['id']}] {label}{when}: {flat(f, v)}{note}")
            else:
                state = STATE.get(c.get("availability"), c.get("availability"))
                note = f" Reason: {c['note']}" if c.get("note") else ""
                lines.append(f"[{org}:{c['id']}] {LABEL.get(f, f.replace('_', ' '))}: UNKNOWN, {state}.{note}")
        for f, n in hidden.items():
            lines.append(f"({n} more '{LABEL.get(f, f)}' facts exist in the profile but are not listed here.)")
    return "\n".join(lines) + "\n"


def compact(env: dict) -> dict:
    """The company page's data: envelope claims and evidence with short keys, URLs listed once."""
    urls: list[str] = []
    uix: dict[str, int] = {}
    evs = []
    eix: dict[str, int] = {}
    for ev in env.get("evidence") or []:
        u = ev.get("source_url") or ""
        if u not in uix:
            uix[u] = len(urls)
            urls.append(u)
        row = {"u": uix[u], "t": ev.get("retrieved_at") or "", "q": ev.get("claim_span") or "",
               "x": 1 if ev.get("span_exact") else 0, "k": ev.get("source_class") or "", "l": ev.get("licence") or "",
               "h": (ev.get("content_sha256") or "")[:16], "m": ev.get("extraction_method") or ""}
        if ev.get("effective_date"):
            row["d"] = ev["effective_date"]
        if ev.get("note"):
            row["nt"] = ev["note"]
        if ev.get("final_url") and ev["final_url"] != u:
            row["fu"] = ev["final_url"]
        if ev.get("span_context"):
            row["sc"] = ev["span_context"]
        eix[ev.get("id")] = len(evs)
        evs.append(row)
    cl = []
    for c in env.get("claims") or []:
        row = {"i": c.get("id"), "f": c.get("field"), "s": c.get("section"), "a": c.get("availability"),
               "e": [eix[x] for x in c.get("evidence_ids") or [] if x in eix]}
        if c.get("value") is not None and c.get("availability") == "available":
            row["v"] = c["value"]
        if c.get("as_of"):
            row["d"] = c["as_of"]
        if c.get("note"):
            row["n"] = c["note"]
        m = c.get("match") or {}
        if m.get("method"):
            row["m"] = m["method"]
            if m["method"] != "registry_endpoint" and m.get("detail"):
                row["md"] = m["detail"]
        cl.append(row)
    summary = env.get("summary") or {}
    run = env.get("run") or {}
    return {
        "o": str(env["organisation_number"]), "n": env.get("legal_name") or "",
        "run": {"at": run.get("completed_at") or "", "commit": ((env.get("agent") or {}).get("commit") or "")[:7],
                "requests": (env.get("operations") or {}).get("requests")},
        "sec": {s: ((env.get("sections") or {}).get(s) or {}).get("state") or "failed" for s in SECTION},
        "sum": [[s.get("text") or "", s.get("claim_ids") or []] for s in summary.get("sentences") or []],
        "unk": summary.get("unknowns") or [],
        "c": cl, "u": urls, "ev": evs,
    }


def latest(claims: list[dict], field: str):
    """The latest filed figure of one field from the company's own accounts (falls back to group), as claimed."""
    best = None
    for c in claims:
        v = c.get("value")
        if c.get("field") != field or c.get("availability") != "available" or not isinstance(v, dict):
            continue
        if v.get("amount") is None:
            continue
        key = (v.get("account_type") != "group", str(v.get("year")))
        if best is None or key > best[0]:
            best = (key, {"a": v["amount"], "c": v.get("currency"), "y": v.get("year"), "t": v.get("account_type") or "company",
                          "i": c.get("id")})
    return best[1] if best else None


def index_row(env: dict, tag: str) -> dict:
    claims = env.get("claims") or []
    av = [c for c in claims if c.get("availability") == "available"]

    def one(f):
        return next((c.get("value") for c in av if c.get("field") == f), None)

    lf, ind, mun = one("legal_form") or {}, one("industry") or {}, one("municipality") or {}
    status = one("legal_status") or {}
    fam = {k: sum(1 for c in av if c.get("field") == f) for f, k in FAMILIES.items()}
    jobs = sum(1 for c in av if c.get("field") == "job_ad")
    row = {"o": str(env["organisation_number"]), "n": env.get("legal_name") or "",
           "lf": lf.get("code") if isinstance(lf, dict) else None,
           "m": mun.get("name") if isinstance(mun, dict) else None,
           "ic": ind.get("code") if isinstance(ind, dict) else None, "in": ind.get("name") if isinstance(ind, dict) else None,
           "e": one("employees_registered"), "w": one("official_website"),
           "rev": latest(claims, "revenue"), "res": latest(claims, "annual_result"),
           "f": fam, "j": jobs, "nf": len(av), "src": tag}
    if isinstance(status, dict) and any(status.get(k) for k in ("konkurs", "underAvvikling", "underTvangsavviklingEllerTvangsopplosning")):
        row["x"] = "bankrupt" if status.get("konkurs") else "dissolving"
    if (env.get("run") or {}).get("terminal_status") != "completed":
        row["st"] = (env.get("run") or {}).get("terminal_status")
    return {k: v for k, v in row.items() if v is not None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+", help="tag=path/to/envelopes.jsonl[.gz] (first one wins on duplicates)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "public" / "data"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    (out / "c").mkdir(parents=True, exist_ok=True)
    (out / "llm").mkdir(parents=True, exist_ok=True)
    for old in [p for d in ("c", "llm") for p in (out / d).iterdir() if p.is_file()]:
        old.unlink()

    seen: set[str] = set()
    rows, fam_companies, fam_facts = [], Counter(), Counter()
    facts = withheld = dups = 0
    commits: Counter = Counter()
    biggest = 0
    for spec in args.sources:
        tag, _, path = spec.rpartition("=") if "=" in spec else ("", "", spec)
        for env in read_envelopes(path):
            org = str(env.get("organisation_number") or "")
            if not re.fullmatch(r"\d{9}", org):
                continue
            if org in seen:
                dups += 1
                continue
            seen.add(org)
            raw = json.dumps(env, ensure_ascii=False)
            if BIRTH.search(raw):
                print(f"refusing: {org} carries a birth-date field", file=sys.stderr)
                return 2
            commits[((env.get("agent") or {}).get("commit") or "")[:7]] += 1
            data = json.dumps(compact(env), ensure_ascii=False, separators=(",", ":"))
            (out / "c" / f"{org}.json.gz").write_bytes(gz(data))
            (out / "llm" / f"{org}.txt.gz").write_bytes(gz(llm_text(env)))
            biggest = max(biggest, len(data.encode()))
            rows.append(index_row(env, tag or Path(path).parent.name))
            for c in env.get("claims") or []:
                if c.get("availability") == "available":
                    facts += 1
                    if c.get("field") in FAMILIES:
                        fam_facts[c["field"]] += 1
                elif c.get("availability") in ("ambiguous", "failed", "blocked"):
                    withheld += 1
            for f in FAMILIES:
                if any(c.get("field") == f and c.get("availability") == "available" for c in env.get("claims") or []):
                    fam_companies[f] += 1
    rows.sort(key=lambda r: r["n"])
    (out / "index.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    meta = {"companies": len(rows), "facts": facts, "withheld": withheld,
            "families": {f: {"companies": fam_companies[f], "facts": fam_facts[f]} for f in FAMILIES},
            "commits": dict(commits)}
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"{len(rows)} companies ({dups} duplicates skipped), {facts} facts, commits {dict(commits)}; "
          f"{total / 1e6:.1f} MB total, largest profile {biggest / 1e3:.0f} kB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
