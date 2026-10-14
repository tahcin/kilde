"""Command line: `kilde run --organisations batch.jsonl` (one command, one envelope per input)."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from . import __version__ as _v  # noqa: F401
from .pipeline import Options, run_batch

DEFAULT_REQUESTS_PER_COMPANY = 20  # Unstop: 2,000 requests per 100 companies
MIN_REQUESTS = 300  # floor for small batches (one large company alone needs more than 20)
DEFAULT_TIME_BUDGET = 30 * 60  # Unstop publishes 45 minutes; finish well inside it


def read_organisations(path: str) -> list[str]:
    """Accepts JSONL rows (universe format or {"organisation_number": ...}), a JSON list, or plain text."""
    text = Path(path).read_text(encoding="utf-8-sig")
    values: list = []
    stripped = text.strip()
    if stripped.startswith("["):
        values = json.loads(stripped)
    elif stripped.startswith("{") and "\n" not in stripped:
        body = json.loads(stripped)
        values = body.get("organisation_numbers") or body.get("organisations") or [body]
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                values.append(json.loads(line))
            else:
                values.extend(x for x in line.replace(";", ",").split(",") if x.strip())
    orgs: list[str] = []
    seen = set()
    INVALID.clear()
    for v in values:
        raw = v.get("organisation_number") or v.get("orgnr") or v.get("organisasjonsnummer") if isinstance(v, dict) else v
        org = "".join(ch for ch in str(raw or "") if ch.isdigit())
        if len(org) != 9 or not _mod11_ok(org):
            # Never abort the batch: an unusable input still gets its own terminal envelope.
            key = str(raw if raw is not None else v)
            if key not in seen:
                seen.add(key)
                INVALID.append(key)
            continue
        if org not in seen:  # one envelope per distinct organisation number
            seen.add(org)
            orgs.append(org)
    return orgs


INVALID: list[str] = []


def _mod11_ok(org: str) -> bool:
    """The organisation number's last digit is its MOD11 check digit (weights 3 2 7 6 5 4 3 2)."""
    r = 11 - sum(int(d) * w for d, w in zip(org[:8], (3, 2, 7, 6, 5, 4, 3, 2))) % 11
    return (0 if r == 11 else r) == int(org[8]) and r != 10


def invalid_envelope(raw: str, run_id: str, at: str) -> dict:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    sections = {s: {"state": "failed", "claim_ids": []} for s in
                ("identity", "financials", "leadership", "locations", "web_presence", "hiring", "activity")}
    return {"organisation_number": raw, "agent": {"name": "Kilde", "version": __version__},
            "state": "submission_error",
            "run": {"run_id": run_id, "started_at": at, "completed_at": at, "terminal_status": "failed"},
            "legal_name": None, "sections": sections, "claims": [], "evidence": [],
            "summary": {"text": "", "sentences": [], "unknowns": ["The input is not a valid nine-digit Norwegian organisation number."]},
            "changes": [], "errors": [{"module": "input", "message": f"invalid organisation number: {raw!r}" + (
                " (nine digits, but the check digit is wrong)" if len(digits) == 9 else ""), "at": at}],
            "operations": {"requests": 0, "third_party_cost_usd": 0.0, "llm_calls": 0}}


def read_previous(path: str | None) -> dict[str, dict]:
    if not path or not Path(path).exists():
        return {}
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            env = json.loads(line)
            out[env["organisation_number"]] = env
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def profile_of(env: dict) -> dict:
    """A compact, human-readable profile: one value list per field, each with its source."""
    evs = {e["id"]: e for e in env["evidence"]}
    facts: dict[str, list] = {}
    for c in env["claims"]:
        if c["availability"] != "available":
            continue
        src = [evs[i]["source_url"] for i in c["evidence_ids"] if i in evs]
        facts.setdefault(c["field"], []).append({"value": c["value"], "as_of": c.get("as_of"), "source": src[0] if src else None})
    return {"organisation_number": env["organisation_number"], "legal_name": env.get("legal_name"),
            "summary": env["summary"]["text"], "unknowns": env["summary"]["unknowns"],
            "sections": {k: v["state"] for k, v in env["sections"].items()}, "facts": facts,
            "changes": env["changes"], "run_id": env["run"]["run_id"]}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kilde", description="Kilde: evidence-first Norwegian company research agent")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="research a batch of organisation numbers")
    r.add_argument("--organisations", "--input", required=True, help="JSONL, JSON or text list of organisation numbers")
    r.add_argument("--output", default="out/envelopes.jsonl", help="terminal envelopes, one JSON object per input")
    r.add_argument("--profiles-output", default=None, help="compact profiles JSONL (default: next to --output)")
    r.add_argument("--report", default=None, help="machine-readable run report JSON (default: next to --output)")
    r.add_argument("--report-html", default=None, help="static HTML report (default: next to --output)")
    r.add_argument("--run-id", default=None)
    r.add_argument("--expected-count", type=int, default=None)
    r.add_argument("--previous", default=None, help="previous envelopes JSONL for refresh diffs")
    r.add_argument("--state-dir", default="", help="optional folder that keeps the latest envelopes so the next run diffs against them (off by default)")
    r.add_argument("--bulk", default=None, help="frozen Brreg snapshot (CSV or JSON, optionally .gz); used for identity when the live API fails")
    r.add_argument("--max-requests", type=int, default=None, help="outbound HTTP request budget (default 20 per company)")
    r.add_argument("--time-budget", type=float, default=None, help="seconds (default 1800)")
    r.add_argument("--concurrency", type=int, default=40)
    r.add_argument("--only", default=None, help="comma list of Kilde modules to run (debugging)")
    # Accepted for compatibility with the starter kit's command; Kilde schedules its own work.
    r.add_argument("--modules", default=None, help="accepted for starter-kit compatibility; ignored")
    r.add_argument("--workers", type=int, default=None, help="accepted for starter-kit compatibility; see --concurrency")
    r.add_argument("--checkpoint-every", type=int, default=None, help="accepted for starter-kit compatibility")
    r.add_argument("--resume", action="store_true", help="accepted for starter-kit compatibility")
    r.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd != "run":
        ap.print_help()
        sys.exit(2)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    orgs = read_organisations(args.organisations)
    expected_note = None
    if args.expected_count is not None and len(orgs) + len(INVALID) != args.expected_count:
        # Do not abort: every distinct input still gets an envelope; the mismatch is reported.
        expected_note = f"expected {args.expected_count} organisations, received {len(orgs) + len(INVALID)} distinct inputs"
        logging.warning(expected_note)
    out = Path(args.output)
    out_dir = out.parent
    run_id = args.run_id or datetime.now(timezone.utc).strftime("kilde-%Y%m%dT%H%M%SZ")
    state = Path(args.state_dir) / "latest-envelopes.jsonl" if args.state_dir else None
    previous = read_previous(args.previous or (str(state) if state else None))
    max_requests = args.max_requests or int(os.environ.get("KILDE_MAX_REQUESTS", 0)) or max(MIN_REQUESTS, DEFAULT_REQUESTS_PER_COMPANY * len(orgs))
    seconds = args.time_budget or float(os.environ.get("KILDE_TIME_BUDGET", 0)) or DEFAULT_TIME_BUDGET
    opt = Options(max_requests=max_requests, seconds=seconds, concurrency=args.concurrency, previous=previous,
                  run_id=run_id, out_dir=out_dir, cache_dir=os.environ.get("KILDE_CACHE_DIR"),
                  modules=set(args.only.split(",")) if args.only else None,
                  bulk=args.bulk,
                  checkpoint=lambda envs: write_jsonl(out, envs + [invalid_envelope(r, run_id, envs[0]["run"]["started_at"] if envs else "") for r in INVALID]))
    envelopes, report = asyncio.run(run_batch(orgs, opt))
    envelopes += [invalid_envelope(r, run_id, report["started_at"]) for r in INVALID]
    report["emitted_envelopes"] = len(envelopes)
    report["expected_count"] = args.expected_count if args.expected_count is not None else len(envelopes)
    if expected_note:
        report["expected_count_note"] = expected_note

    write_jsonl(out, envelopes)
    write_jsonl(Path(args.profiles_output or out_dir / "profiles.jsonl"), [profile_of(e) for e in envelopes])
    report["validation"] = validate(envelopes, orgs + INVALID)
    report["inputs"] = {"organisations": Path(args.organisations).name, "bulk": Path(args.bulk).name if args.bulk else None,
                        "previous_envelopes": len(previous), "distinct_inputs": len(orgs) + len(INVALID)}
    report["coverage"] = coverage(envelopes)
    Path(args.report or out_dir / "run-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    try:
        from .report import write_report

        write_report(envelopes, str(args.report_html or out_dir / "report.html"), report)
    except ImportError:
        pass
    if state:
        write_jsonl(state, envelopes)
    print(json.dumps({k: report[k] for k in ("run_id", "expected_count", "emitted_envelopes", "runtime_ms")}
                     | {"requests": report["operations"]["requests"], "valid": report["validation"]["passed"]}, indent=2))
    sys.exit(0 if report["validation"]["passed"] else 1)


def validate(envelopes: list[dict], orgs: list[str]) -> dict:
    got = [e["organisation_number"] for e in envelopes]
    checks = {
        "one_envelope_per_input": sorted(got) == sorted(orgs),
        "unique": len(got) == len(set(got)),
        "all_terminal": all(e["run"]["terminal_status"] in ("completed", "failed") for e in envelopes),
        "all_sections_present": all(len(e["sections"]) == 7 for e in envelopes),
        "available_claims_have_evidence": all(c["evidence_ids"] for e in envelopes for c in e["claims"]
                                              if c["availability"] == "available"),
    }
    return {"passed": all(checks.values()), "checks": checks}


def coverage(envelopes: list[dict]) -> dict:
    n = max(1, len(envelopes))
    out = {}
    for sec in ("identity", "financials", "leadership", "locations", "web_presence", "hiring", "activity"):
        out[sec] = round(sum(e["sections"][sec]["state"] == "available" for e in envelopes) / n, 3)
    return out


if __name__ == "__main__":
    main()
