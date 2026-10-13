"""Batch pipeline: budgeted, two-phase, always one terminal envelope per input."""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import __version__
from .connectors import accounts, brreg
from .http import Blocked, Budget, BudgetExhausted, Fetcher
from .model import SECTIONS, CompanyContext, utc_now
from .refresh import diff
from .spans import MAX_SPAN, repair
from .synthesis import summarise

log = logging.getLogger("kilde")
STEP_TIMEOUT = {"website": 25, "registry": 20, "jobs": 15, "report_copies": 10}
STATE_RANK = ["available", "ambiguous", "blocked", "not_available", "not_applicable", "failed"]


def _optional(module: str):
    try:
        return importlib.import_module(f"kilde.connectors.{module}")
    except Exception as exc:  # noqa: BLE001 - a broken optional connector must not stop the run
        log.warning("connector %s unavailable: %r", module, exc)
        return None


def git_commit() -> str | None:
    try:
        root = Path(__file__).resolve().parents[2]
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except Exception:
        return None


class Options:
    def __init__(self, *, max_requests: int, seconds: float, concurrency: int = 16, previous: dict[str, dict] | None = None,
                 run_id: str, out_dir: Path, cache_dir: str | None = None, modules: set[str] | None = None):
        self.max_requests = max_requests
        self.seconds = seconds
        self.concurrency = concurrency
        self.previous = previous or {}
        self.run_id = run_id
        self.out_dir = out_dir
        self.cache_dir = cache_dir
        self.modules = modules


async def _step(ctx: CompanyContext, name: str, fn: Callable[[], Awaitable[None]], field: str, section: str) -> None:
    """Run one connector step; turn every failure into an explicit state, never an exception."""
    t = time.monotonic()
    try:
        await asyncio.wait_for(fn(), timeout=STEP_TIMEOUT.get(name, 20))
    except asyncio.TimeoutError:
        ctx.module_states.setdefault(name, "failed")
        ctx.error(name, f"step timed out after {STEP_TIMEOUT.get(name, 20)} s")
        if not any(c.field == field and c.availability == "available" for c in ctx.claims):
            ctx.mark(field, section, "failed", note="source too slow (step timeout)")
    except BudgetExhausted as exc:
        ctx.module_states.setdefault(name, "failed")
        if not any(c.field == field for c in ctx.claims):
            ctx.mark(field, section, "failed", note=f"budget_exhausted: {exc}")
    except Blocked as exc:
        ctx.module_states.setdefault(name, "blocked")
        if not any(c.field == field for c in ctx.claims):
            ctx.mark(field, section, "blocked", note=str(exc)[:200])
    except Exception as exc:  # noqa: BLE001 - a connector bug must not drop a company
        log.warning("%s %s: %r", ctx.org, name, exc)
        ctx.error(name, repr(exc))
        ctx.module_states.setdefault(name, "failed")
        if not any(c.field == field for c in ctx.claims):
            ctx.mark(field, section, "failed", note=f"{type(exc).__name__}")
    finally:
        ctx.facts.setdefault("timings_ms", {})[name] = int((time.monotonic() - t) * 1000)


async def run_batch(orgs: list[str], opt: Options) -> tuple[list[dict], dict]:
    started_at = utc_now()
    t0 = time.monotonic()
    budget = Budget(opt.max_requests, opt.seconds)
    f = Fetcher(budget, cache_dir=opt.cache_dir)
    ctxs = {o: CompanyContext(o) for o in orgs}
    want = (lambda m: opt.modules is None or m in opt.modules)
    website = _optional("website")
    nav = _optional("nav_jobs")
    gleif = _optional("gleif")
    fin = _optional("finanstilsynet")
    wiki = _optional("wikidata")
    batch_info: dict[str, Any] = {}

    async def guarded(coro: Awaitable[None], deadline: float) -> None:
        try:
            await asyncio.wait_for(coro, timeout=max(1.0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            pass

    per_company = opt.max_requests / max(1, len(orgs))
    # Phase A: identity for everyone. Single lookups give one clean source URL per company; with a
    # tight budget, fall back to batch lookups (one request per 100 organisations).
    batch_records: dict[str, Any] = {}
    if per_company < 15:
        try:
            batch_records = await brreg.fetch_entities_batch(f, orgs)
        except Exception as exc:  # noqa: BLE001
            log.warning("batch entity lookup failed: %r", exc)
    sem = asyncio.Semaphore(opt.concurrency)
    phase_a_deadline = budget.started + opt.seconds * 0.45

    async def phase_a(ctx: CompanyContext) -> None:
        async with sem:
            await _step(ctx, "registry", lambda: brreg.fetch_entity(f, ctx, batch_records.get(ctx.org)), "legal_name", "identity")
            if not ctx.registry or ctx.facts.get("deleted"):
                return
            if want("roles"):
                await _step(ctx, "roles", lambda: brreg.fetch_roles(f, ctx), "ceo", "leadership")
            if want("financials"):
                if ctx.registry.get("sisteInnsendteAarsregnskap") or ctx.registry.get("organisasjonsform", {}).get("kode") in ("AS", "ASA"):
                    await _step(ctx, "financials", lambda: accounts.fetch_accounts(f, ctx), "revenue", "financials")
                else:
                    ctx.mark("revenue", "financials", "not_applicable", note="No annual accounts filed for this entity type")
            if want("locations"):
                await _step(ctx, "locations", lambda: brreg.fetch_subunits(f, ctx), "registered_workplace", "locations")

    # Batch-level preparation runs alongside phase A.
    async def prepare() -> None:
        if want("activity"):
            try:
                batch_info["change_feed"] = await brreg.fetch_change_feed(f, orgs)
            except Exception as exc:  # noqa: BLE001
                log.warning("change feed failed: %r", exc)
        if nav and want("jobs"):
            try:
                names = {o: "" for o in orgs}
                batch_info["jobs"] = await nav.prepare(f, orgs, names)
            except Exception as exc:  # noqa: BLE001
                log.warning("jobs prepare failed: %r", exc)
                batch_info["jobs_error"] = repr(exc)
        if gleif and want("gleif"):
            try:
                batch_info["gleif"] = await gleif.prepare_gleif(f, orgs)
            except Exception as exc:  # noqa: BLE001
                log.warning("gleif prepare failed: %r", exc)
        if wiki and want("wikidata"):
            try:
                batch_info["wikidata"] = await wiki.prepare_wikidata(f, orgs)
            except Exception as exc:  # noqa: BLE001
                log.warning("wikidata prepare failed: %r", exc)

    await guarded(asyncio.gather(prepare(), *(phase_a(c) for c in ctxs.values())), phase_a_deadline)

    # Phase B: external sources, in priority order, sharing what is left of the budget.
    deadline_b = budget.started + opt.seconds * 0.92

    async def phase_b(ctx: CompanyContext) -> None:
        if not ctx.registry or ctx.facts.get("deleted"):
            return
        async with sem:
            if nav and "jobs" in batch_info:
                await _step(ctx, "jobs", lambda: nav.fetch_jobs(f, ctx, batch_info["jobs"]), "job_ad", "hiring")
            elif nav and want("jobs"):
                ctx.mark("job_ad", "hiring", "failed", note="job feed unavailable this run")
            if website and want("website"):
                await _step(ctx, "website", lambda: website.fetch_website(f, ctx), "official_website", "web_presence")
            if gleif and want("gleif"):
                await _step(ctx, "gleif", lambda: gleif.fetch_gleif(f, ctx, batch_info.get("gleif")), "lei", "identity")
            if ctx.registry.get("erIKonsern") and want("group"):
                await _step(ctx, "group", lambda: brreg.fetch_group(f, ctx), "group_parent", "identity")
            if fin and want("finanstilsynet") and _maybe_licensed(ctx.registry):
                await _step(ctx, "finanstilsynet", lambda: fin.fetch_finanstilsynet(f, ctx), "financial_licence", "identity")
            if wiki and batch_info.get("wikidata", {}).get(ctx.org):
                await _step(ctx, "wikidata", lambda: wiki.fetch_wikidata(f, ctx, batch_info["wikidata"]),
                            "wikidata_item", "identity")
            if want("signature") and budget.remaining_requests() > 3 * len(orgs) * 0.2:
                await _step(ctx, "signature", lambda: brreg.fetch_signature(f, ctx), "signature_rule", "leadership")

    # The annual-report copy endpoint asks for about 30 requests a minute, so it runs as one
    # paced background queue next to phase B and stops after 15 minutes instead of slowing the run.
    async def copies_queue() -> None:
        if not want("report_copies"):
            return
        stop = min(deadline_b, budget.started + 15 * 60)
        for ctx in ctxs.values():
            if time.monotonic() > stop - 3 or budget.remaining_requests() < 10:
                break
            if ctx.registry and ctx.registry.get("sisteInnsendteAarsregnskap") and not ctx.facts.get("deleted"):
                await _step(ctx, "report_copies", lambda: accounts.fetch_report_copies(f, ctx), "annual_report_pdf", "financials")

    await guarded(asyncio.gather(copies_queue(), *(phase_b(c) for c in ctxs.values())), deadline_b)
    await f.close()

    # Assemble.
    feed = batch_info.get("change_feed") or {}
    envelopes = []
    completed_at = utc_now()
    commit = git_commit()
    snap_dir = opt.out_dir / "snapshots"
    for org in orgs:
        ctx = ctxs[org]
        if org in feed:
            try:
                brreg.emit_change_claims(ctx, *feed[org])
            except Exception as exc:  # noqa: BLE001
                ctx.error("activity", repr(exc))
        elif ctx.registry and want("activity") and "change_feed" in batch_info:
            ctx.mark("registry_change", "activity", "not_available", note="No registry updates in the last 12 months")
        if not ctx.claims_for("job_ad") and not any(c.field == "job_ad" for c in ctx.claims) and ctx.registry:
            ctx.mark("job_ad", "hiring", "not_available" if nav and "jobs" in batch_info else "failed",
                     note="No job ads checked" if not nav else None)
        if not any(c.section == "web_presence" for c in ctx.claims) and ctx.registry:
            ctx.mark("official_website", "web_presence", "failed", note="website step did not run (budget or time)")
        changes = diff(ctx, opt.previous.get(org))
        envelopes.append(assemble(ctx, opt, started_at, completed_at, commit, changes, snap_dir))
    report = {
        "run_id": opt.run_id, "agent": "Kilde", "version": __version__, "commit": commit,
        "started_at": started_at, "completed_at": completed_at, "expected_count": len(orgs),
        "emitted_envelopes": len(envelopes), "runtime_ms": int((time.monotonic() - t0) * 1000),
        "operations": {"requests": budget.used, "max_requests": opt.max_requests, "dns_lookups": budget.dns_lookups,
                       "cache_hits": budget.cache_hits, "by_host": dict(sorted(budget.by_host.items(), key=lambda x: -x[1])[:25]),
                       "third_party_cost_usd": 0.0, "llm_calls": 0, "time_budget_seconds": opt.seconds},
    }
    return envelopes, report


def _maybe_licensed(reg: dict) -> bool:
    """Finanstilsynet licences: finance, insurance, accounting, auditing, real-estate brokerage, debt collection."""
    codes = [(reg.get(k) or {}).get("kode", "") for k in ("naeringskode1", "naeringskode2", "naeringskode3")]
    return any(c.startswith(("64", "65", "66", "68.3", "69.2", "82.91")) for c in codes)


def section_state(ctx: CompanyContext, section: str) -> str:
    states = [c.availability for c in ctx.claims if c.section == section]
    if not states:
        return "not_available"
    return min(states, key=STATE_RANK.index)


def assemble(ctx: CompanyContext, opt: Options, started_at: str, completed_at: str, commit: str | None,
             changes: list[dict], snap_dir: Path) -> dict:
    # Persist snapshots for every evidence item that has one (deduplicated by hash).
    for ev in ctx.evidence:
        snap = ctx.snapshots.get(ev.id)
        if not snap:
            continue
        ext = "json" if "json" in snap.content_type else ("html" if "html" in snap.content_type else "bin")
        rel = Path("snapshots") / snap.sha256[:2] / f"{snap.sha256}.{ext}"
        p = opt.out_dir / rel
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(snap.content)
        ev.snapshot_path = str(rel)
        raw = snap.content.decode("utf-8", errors="replace")
        span, extra, exact = repair(raw, ev.claim_span, snap.content_type, ctx.anchors.get(ev.id))
        ev.claim_span = span[:MAX_SPAN] if span else span
        ev.span_context = [x[:MAX_SPAN] for x in extra] or None
        ev.span_exact = exact
    for ev in ctx.evidence:
        if ev.id not in ctx.snapshots and ev.claim_span:
            ev.claim_span = ev.claim_span[:MAX_SPAN]
    summary = summarise(ctx, changes)
    terminal = "completed" if ctx.registry else ("not_available" if any(c.availability == "not_available" for c in ctx.claims) else "failed")
    return {
        "organisation_number": ctx.org,
        "agent": {"name": "Kilde", "version": __version__, "commit": commit},
        "run": {"run_id": opt.run_id, "started_at": started_at, "completed_at": completed_at, "terminal_status": terminal},
        "legal_name": (ctx.registry or {}).get("navn"),
        "sections": {s: {"state": section_state(ctx, s), "claim_ids": [c.id for c in ctx.claims if c.section == s]}
                     for s in SECTIONS},
        "claims": [c.to_json() for c in ctx.claims],
        "evidence": [e.to_json() for e in ctx.evidence],
        "summary": summary,
        "changes": changes,
        "errors": ctx.errors,
        "module_states": ctx.module_states,
        "operations": {"requests": ctx.requests, "third_party_cost_usd": 0.0, "llm_calls": 0,
                       "timings_ms": ctx.facts.get("timings_ms", {})},
        "human_url": f"https://virksomhet.brreg.no/oppslag/enheter/{ctx.org}",
    }
