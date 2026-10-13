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

from . import __version__, gate
from .connectors import accounts, brreg
from .http import Blocked, Budget, BudgetExhausted, Fetcher, UnsafeAddress
from .model import SECTIONS, CompanyContext, utc_now
from .refresh import diff
from .spans import MAX_SPAN, repair
from .synthesis import summarise

log = logging.getLogger("kilde")
COPIES_WINDOW = 28 * 60  # seconds; the endpoint asks for about 30 requests a minute
DEFAULT_OFF = {"report_copies"}
# Open registers keyed by organisation number, each with a batch prepare and a per-company emit.
OPEN_SOURCES = [("ted", "public_contract_award", "activity"), ("smilefjes", "food_inspection", "activity"),
                ("forskningsradet", "research_project", "activity"), ("stotteregisteret", "state_aid_award", "activity"),
                ("dibk", "approval_sentral_godkjenning", "identity")]
STEP_TIMEOUT = {"website": 60, "registry": 20, "jobs": 90, "report_copies": 10}
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
                 run_id: str, out_dir: Path, cache_dir: str | None = None, modules: set[str] | None = None,
                 checkpoint: Callable[[list[dict]], None] | None = None, bulk: str | None = None):
        self.max_requests = max_requests
        self.seconds = seconds
        self.concurrency = concurrency
        self.previous = previous or {}
        self.run_id = run_id
        self.out_dir = out_dir
        self.cache_dir = cache_dir
        self.modules = modules
        self.checkpoint = checkpoint
        self.bulk = bulk


async def _step(ctx: CompanyContext, name: str, fn: Callable[[], Awaitable[None]], field: str, section: str,
                timeout: float | None = None) -> None:
    """Run one connector step; turn every failure into an explicit state, never an exception."""
    t = time.monotonic()
    n_claims, n_evidence = len(ctx.claims), len(ctx.evidence)
    try:
        await asyncio.wait_for(fn(), timeout=timeout or STEP_TIMEOUT.get(name, 20))
    except asyncio.TimeoutError:
        # Roll back whatever the step published half-way, so a timeout never leaves partial facts
        # (and a retry starts clean). The step is recorded as timed out for a possible second pass.
        for ev in ctx.evidence[n_evidence:]:
            ctx.snapshots.pop(ev.id, None)
            ctx.anchors.pop(ev.id, None)
        del ctx.claims[n_claims:]
        del ctx.evidence[n_evidence:]
        ctx.module_states[name] = "timeout"
        ctx.error(name, f"step timed out after {timeout or STEP_TIMEOUT.get(name, 20)} s")
        if not any(c.field == field and c.availability == "available" for c in ctx.claims):
            ctx.mark(field, section, "failed", note="source too slow (step timeout)")
    except BudgetExhausted as exc:
        ctx.module_states.setdefault(name, "failed")
        if not any(c.field == field for c in ctx.claims):
            ctx.mark(field, section, "failed", note=f"budget_exhausted: {exc}")
    except (Blocked, UnsafeAddress) as exc:
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
    # The annual-report copy list is off by default: its endpoint allows about 30 requests a minute, so
    # on a large batch it cannot finish the same set of companies in every run.
    want = (lambda m: (m not in DEFAULT_OFF) if opt.modules is None else (m in opt.modules))
    website = _optional("website")
    nav = _optional("nav_jobs")
    gleif = _optional("gleif")
    fin = _optional("finanstilsynet")
    wiki = _optional("wikidata")
    open_mods = {name: m for name, _f, _s in OPEN_SOURCES if (m := _optional(name))}
    batch_info: dict[str, Any] = {}

    async def guarded(coro: Awaitable[None], deadline: float) -> None:
        try:
            await asyncio.wait_for(coro, timeout=max(1.0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            pass

    # Phase A, breadth first: identity for everyone (one request per 100 companies), then filed accounts
    # for everyone, then roles, then workplaces. Under any request or time limit every company gets the
    # most valuable registry facts before any company gets the less valuable ones.
    batch_records: dict[str, Any] = {}
    try:
        batch_records = await brreg.fetch_entities_batch(f, orgs)
    except Exception as exc:  # noqa: BLE001
        log.warning("batch entity lookup failed: %r", exc)
    sem = asyncio.Semaphore(opt.concurrency)
    phase_a_deadline = budget.started + opt.seconds * 0.45

    async def each(fn) -> None:
        async def one(ctx: CompanyContext) -> None:
            async with sem:
                await fn(ctx)
        await asyncio.gather(*(one(c) for c in ctxs.values()))

    async def identity(ctx: CompanyContext) -> None:
        await _step(ctx, "registry", lambda: brreg.fetch_entity(f, ctx, batch_records.get(ctx.org)), "legal_name", "identity")
        if not ctx.registry and opt.bulk:
            bulk_identity(ctx, opt.bulk)

    async def financials(ctx: CompanyContext) -> None:
        if not ctx.registry or not want("financials"):
            return
        if ctx.facts.get("deleted") or ctx.registry.get("sisteInnsendteAarsregnskap") or \
                ctx.registry.get("organisasjonsform", {}).get("kode") in ("AS", "ASA"):
            # A deleted entity's filed accounts stay public in Regnskapsregisteret.
            await _step(ctx, "financials", lambda: accounts.fetch_accounts(f, ctx), "revenue", "financials")
        else:
            ctx.mark("revenue", "financials", "not_applicable", note="No annual accounts filed for this entity type")

    async def roles(ctx: CompanyContext) -> None:
        if ctx.registry and not ctx.facts.get("deleted") and want("roles"):
            await _step(ctx, "roles", lambda: brreg.fetch_roles(f, ctx), "ceo", "leadership")

    async def locations(ctx: CompanyContext) -> None:
        if ctx.registry and not ctx.facts.get("deleted") and want("locations"):
            await _step(ctx, "locations", lambda: brreg.fetch_subunits(f, ctx), "registered_workplace", "locations")

    async def phase_a_all() -> None:
        for stage in (identity, financials, roles, locations):
            await each(stage)

    # Batch-level preparation runs alongside phase A.
    async def prepare() -> None:
        if want("activity"):
            try:
                batch_info["change_feed"] = await brreg.fetch_change_feed(f, orgs)
            except Exception as exc:  # noqa: BLE001
                log.warning("change feed failed: %r", exc)
        if gleif and want("gleif"):
            try:
                batch_info["gleif"] = await gleif.prepare_gleif(f, orgs)
            except Exception as exc:  # noqa: BLE001
                log.warning("gleif prepare failed: %r", exc)
        for name, _field, _section in OPEN_SOURCES:
            mod = open_mods.get(name)
            if mod and want(name):
                try:
                    batch_info[name] = await getattr(mod, f"prepare_{name}")(f, orgs)
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s prepare failed: %r", name, exc)
                    batch_info[f"{name}_error"] = repr(exc)
        if wiki and want("wikidata"):
            try:
                batch_info["wikidata"] = await wiki.prepare_wikidata(f, orgs)
            except Exception as exc:  # noqa: BLE001
                log.warning("wikidata prepare failed: %r", exc)

    await guarded(asyncio.gather(prepare(), phase_a_all()), phase_a_deadline)

    # Checkpoint: provisional envelopes (registry facts so far) are written now, so an external kill
    # later in the run still leaves one envelope per company. The final write replaces them.
    commit = git_commit()
    if opt.checkpoint:
        try:
            now = utc_now()
            opt.checkpoint([assemble(c, opt, started_at, now, commit, [], opt.out_dir / "snapshots", provisional=True)
                            for c in ctxs.values()])
        except Exception as exc:  # noqa: BLE001
            log.warning("checkpoint failed: %r", exc)

    # The job index catch-up matches new ads by legal name before checking the org number, so it
    # runs once the registry phase has the names.
    if nav and want("jobs"):
        try:
            names = {o: (c.registry or {}).get("navn", "") for o, c in ctxs.items()}
            batch_info["jobs"] = await asyncio.wait_for(nav.prepare(f, orgs, names), timeout=max(30.0, opt.seconds * 0.15))
        except Exception as exc:  # noqa: BLE001
            log.warning("jobs prepare failed: %r", exc)
            batch_info["jobs_error"] = repr(exc)

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
            for name, field, section in OPEN_SOURCES:
                mod = open_mods.get(name)
                if not mod or not want(name):
                    continue
                if name == "dibk" and not mod.likely_construction(ctx.registry):
                    continue
                fetch = getattr(mod, f"fetch_{name}")
                await _step(ctx, name, lambda fetch=fetch, name=name: fetch(f, ctx, batch_info.get(name)), field, section)
            if want("signature") and budget.remaining_requests() > 3 * len(orgs) * 0.2:
                await _step(ctx, "signature", lambda: brreg.fetch_signature(f, ctx), "signature_rule", "leadership")

    # The annual-report copy endpoint asks for about 30 requests a minute, so it runs as one
    # paced background queue next to phase B and stops after COPIES_WINDOW instead of slowing the run.
    async def copies_queue() -> None:
        if not want("report_copies"):
            return
        stop = min(deadline_b, budget.started + COPIES_WINDOW)
        for ctx in ctxs.values():
            if time.monotonic() > stop - 3 or budget.remaining_requests() < 10:
                break
            if ctx.registry and (ctx.registry.get("sisteInnsendteAarsregnskap") or ctx.claims_for("revenue")
                                 or ctx.claims_for("annual_result")):
                await _step(ctx, "report_copies", lambda: accounts.fetch_report_copies(f, ctx), "annual_report_pdf", "financials")

    await guarded(asyncio.gather(copies_queue(), *(phase_b(c) for c in ctxs.values())), deadline_b)

    # Second pass: website steps that timed out under load get one more, unhurried try. This keeps
    # results stable between runs (Builderr runs each entry twice and compares the facts).
    async def retry_website(ctx: CompanyContext) -> None:
        async with retry_sem:
            ctx.claims[:] = [c for c in ctx.claims if not (c.field == "official_website" and c.availability == "failed")]
            ctx.module_states.pop("website", None)
            await _step(ctx, "website", lambda: website.fetch_website(f, ctx), "official_website", "web_presence", timeout=120)

    retry_sem = asyncio.Semaphore(8)
    retry = [c for c in ctxs.values() if c.module_states.get("website") == "timeout"]
    if website and retry and time.monotonic() < deadline_b - 60:
        await guarded(asyncio.gather(*(retry_website(c) for c in retry)), budget.started + opt.seconds * 0.97)
    batch_info["website_retries"] = len(retry)
    await f.close()

    # Assemble.
    feed = batch_info.get("change_feed") or {}
    envelopes = []
    completed_at = utc_now()
    snap_dir = opt.out_dir / "snapshots"
    gate_notes: list[str] = []
    for org in orgs:
        ctx = ctxs[org]
        # Builderr's hiring family is shaped as a URL per signal: publish each verified NAV ad as one too.
        for job in ctx.claims_for("job_ad"):
            url = (job.value or {}).get("url") if isinstance(job.value, dict) else None
            if url and url not in {str(c.value) for c in ctx.claims_for("hiring_signal")}:
                ctx.add_claim("hiring_signal", "hiring", url, [e for e in ctx.evidence if e.id in job.evidence_ids],
                              as_of=job.as_of, match=job.match, note="NAV job ad for this organisation number")
        try:
            gate_notes += gate.check(ctx)
        except Exception as exc:  # noqa: BLE001
            ctx.error("gate", repr(exc))
        if org in feed:
            try:
                brreg.emit_change_claims(ctx, *feed[org])
            except Exception as exc:  # noqa: BLE001
                ctx.error("activity", repr(exc))
        elif ctx.registry and want("activity") and "change_feed" in batch_info:
            ctx.mark("registry_change", "activity", "not_available", note="No registry updates in the last 12 months")
        if ctx.facts.get("deleted") and not any(c.field == "job_ad" for c in ctx.claims):
            ctx.mark("job_ad", "hiring", "not_applicable", note="the company is deleted from the register")
        if not ctx.claims_for("job_ad") and not any(c.field == "job_ad" for c in ctx.claims) and ctx.registry:
            ctx.mark("job_ad", "hiring", "not_available" if nav and "jobs" in batch_info else "failed",
                     note="No job ads checked" if not nav else None)
        if not any(c.section == "web_presence" for c in ctx.claims) and ctx.registry:
            if ctx.facts.get("deleted"):
                ctx.mark("official_website", "web_presence", "not_applicable", note="the company is deleted from the register")
            else:
                ctx.mark("official_website", "web_presence", "failed", note="website step did not run (budget or time)")
        changes = diff(ctx, opt.previous.get(org))
        envelopes.append(assemble(ctx, opt, started_at, completed_at, commit, changes, snap_dir))
    report = {
        "run_id": opt.run_id, "agent": "Kilde", "version": __version__, "commit": commit,
        "started_at": started_at, "completed_at": completed_at, "expected_count": len(orgs),
        "emitted_envelopes": len(envelopes), "runtime_ms": int((time.monotonic() - t0) * 1000),
        "publication_gate": {"withheld": len(gate_notes), "notes": gate_notes[:200]},
        "website_retries": batch_info.get("website_retries", 0),
        "company_runtime_ms": _percentiles([e["operations"]["runtime_ms"] for e in envelopes]),
        "operations": {"requests": budget.used, "max_requests": opt.max_requests, "dns_lookups": budget.dns_lookups,
                       "cache_hits": budget.cache_hits, "by_host": dict(sorted(budget.by_host.items(), key=lambda x: -x[1])[:25]),
                       "third_party_cost_usd": 0.0, "llm_calls": 0, "time_budget_seconds": opt.seconds},
    }
    return envelopes, report


def _percentiles(xs: list[int]) -> dict:
    xs = sorted(xs)
    if not xs:
        return {}
    pick = lambda q: xs[min(len(xs) - 1, int(q * len(xs)))]  # noqa: E731
    return {"p50": pick(0.5), "p95": pick(0.95), "max": xs[-1]}


def bulk_identity(ctx: CompanyContext, path: str) -> None:
    """Identity from Builderr's frozen Brønnøysund snapshot (`--bulk`) when the live API gave nothing.
    Reads the CSV or JSON bulk download (optionally gzipped) and emits the registry facts from that row."""
    import csv
    import gzip
    import hashlib
    from .model import Snapshot

    try:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8-sig", newline="") as fh:
            head = fh.read(1)
            fh.seek(0)
            row = None
            if head == "[":
                for rec in json.load(fh):
                    if rec.get("organisasjonsnummer") == ctx.org:
                        row = rec
                        break
                raw_line = json.dumps(row, ensure_ascii=False) if row else ""
            else:
                reader = csv.reader(fh)
                header = next(reader)
                for vals in reader:
                    if vals and vals[0].strip('"') == ctx.org or (ctx.org in vals[:2]):
                        flat = dict(zip(header, vals))
                        row = {}
                        for k, v in flat.items():
                            if v == "":
                                continue
                            cur = row
                            parts = k.split(".")
                            for p in parts[:-1]:
                                cur = cur.setdefault(p, {})
                            cur[parts[-1]] = v
                        raw_line = ",".join(vals)
                        break
    except Exception as exc:  # noqa: BLE001
        ctx.error("registry", f"bulk snapshot unreadable: {exc!r}")
        return
    if not row:
        return
    if isinstance((row.get("forretningsadresse") or {}).get("adresse"), str):
        row["forretningsadresse"]["adresse"] = [row["forretningsadresse"]["adresse"]]
    if isinstance((row.get("postadresse") or {}).get("adresse"), str):
        row["postadresse"]["adresse"] = [row["postadresse"]["adresse"]]
    content = raw_line.encode("utf-8")
    snap = Snapshot(url="https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv", final_url=path, status=200,
                    retrieved_at=utc_now(), content=content, content_type="text/csv")
    ctx.claims = [c for c in ctx.claims if not (c.field == "legal_name" and c.availability != "available")]
    ctx.registry = row
    ctx.module_states["registry"] = "available"
    ctx.facts["identity_from_bulk"] = hashlib.sha256(content).hexdigest()
    brreg.emit_entity_claims(ctx, snap, row)


def _maybe_licensed(reg: dict) -> bool:
    """Finanstilsynet licences: finance, insurance, accounting, auditing, real-estate brokerage, debt collection."""
    codes = [(reg.get(k) or {}).get("kode", "") for k in ("naeringskode1", "naeringskode2", "naeringskode3")]
    return any(c.startswith(("64", "65", "66", "68.3", "69.2", "82.91")) for c in codes)


def section_state(ctx: CompanyContext, section: str) -> str:
    states = [c.availability for c in ctx.claims if c.section == section]
    if not states:
        # Nothing was attempted for this section: say "failed" when identity itself could not be checked.
        return "failed" if not ctx.registry and ctx.module_states.get("registry") in ("failed", "timeout") else "not_available"
    return min(states, key=STATE_RANK.index)


def assemble(ctx: CompanyContext, opt: Options, started_at: str, completed_at: str, commit: str | None,
             changes: list[dict], snap_dir: Path, provisional: bool = False) -> dict:
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
    # The run completed for this input unless the registry itself could not be reached; an unknown
    # organisation number is a completed lookup with identity "not_available".
    terminal = "failed" if not ctx.registry and ctx.module_states.get("registry") in ("failed", "timeout") else "completed"
    t = ctx.facts.get("timings_ms", {})
    return {
        "organisation_number": ctx.org,
        "state": "complete" if terminal == "completed" else "submission_error",
        "agent": {"name": "Kilde", "version": __version__, "commit": commit},
        "run": {"run_id": opt.run_id, "started_at": started_at, "completed_at": completed_at, "terminal_status": terminal,
                **({"provisional": True} if provisional else {})},
        "legal_name": (ctx.registry or {}).get("navn"),
        "sections": {s: {"state": section_state(ctx, s), "claim_ids": [c.id for c in ctx.claims if c.section == s]}
                     for s in SECTIONS},
        "claims": [c.to_json() for c in ctx.claims],
        "evidence": [e.to_json() for e in ctx.evidence],
        "summary": summary,
        "changes": changes,
        "errors": ctx.errors,
        "module_states": ctx.module_states,
        "operations": {"requests": ctx.requests, "runtime_ms": sum(t.values()), "third_party_cost_usd": 0.0,
                       "llm_calls": 0, "timings_ms": t},
        "human_url": f"https://virksomhet.brreg.no/oppslag/enheter/{ctx.org}",
    }
