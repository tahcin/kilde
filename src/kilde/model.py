"""Core data model: evidence, claims and the per-company working context."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

AVAILABILITY = ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed")
SECTIONS = ("identity", "financials", "leadership", "locations", "web_presence", "hiring", "activity")

# Source classes, in order of authority.
OFFICIAL_REGISTRY = "official_registry"
OFFICIAL_ACCOUNTS = "official_annual_accounts"
OFFICIAL_REGISTER_OTHER = "official_register"
COMPANY_OWNED = "company_owned"
PUBLIC_JOB_FEED = "public_job_feed"
OPEN_DATA = "open_data"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Evidence:
    id: str
    source_url: str
    source_class: str
    retrieved_at: str
    content_sha256: str | None
    claim_span: str | None = None
    final_url: str | None = None
    http_status: int | None = None
    effective_date: str | None = None
    snapshot_path: str | None = None
    extraction_method: str | None = None
    licence: str | None = None
    note: str | None = None
    span_context: list[str] | None = None
    span_exact: bool | None = None

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Claim:
    id: str
    field: str
    section: str
    value: Any
    availability: str
    evidence_ids: list[str]
    confidence: float = 1.0
    as_of: str | None = None
    match: dict[str, str] | None = None
    note: str | None = None

    def to_json(self) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if v is not None}
        if self.value is None:
            out["value"] = None
        return out


@dataclass
class Snapshot:
    """One HTTP response as captured for evidence."""

    url: str
    final_url: str
    status: int
    retrieved_at: str
    content: bytes
    content_type: str
    redirects: list[str] = field(default_factory=list)
    from_cache: bool = False
    redacted: bool = False

    @property
    def sha256(self) -> str:
        return sha256(self.content)

    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        import json

        return json.loads(self.content)


class CompanyContext:
    """Everything gathered for one organisation number during a run."""

    def __init__(self, org: str):
        self.org = org
        self.claims: list[Claim] = []
        self.evidence: list[Evidence] = []
        self.snapshots: dict[str, Snapshot] = {}  # evidence id -> snapshot to persist
        self.anchors: dict[str, tuple[str, Any]] = {}  # evidence id -> JSON pair naming the record
        self.errors: list[dict[str, Any]] = []
        self.registry: dict[str, Any] | None = None  # raw Enhetsregisteret record
        self.facts: dict[str, Any] = {}  # scratch values shared between connectors
        self.requests = 0
        self.module_states: dict[str, str] = {}
        self._ev = 0
        self._cl = 0

    # Evidence and claims -------------------------------------------------
    def add_evidence(self, snap: Snapshot | None, *, source_url: str | None = None, source_class: str,
                     span: str | None = None, effective_date: str | None = None, method: str,
                     licence: str | None = None, note: str | None = None,
                     anchor: tuple[str, Any] | None = None) -> Evidence:
        self._ev += 1
        eid = f"e{self._ev}"
        ev = Evidence(
            id=eid,
            source_url=source_url or (snap.url if snap else ""),
            final_url=snap.final_url if snap and snap.final_url != (source_url or snap.url) else None,
            http_status=snap.status if snap else None,
            source_class=source_class,
            retrieved_at=snap.retrieved_at if snap else utc_now(),
            content_sha256=snap.sha256 if snap else None,
            claim_span=span or None,  # exact-span repair and truncation happen at assembly
            effective_date=effective_date,
            extraction_method=method,
            licence=licence,
            note=note,
        )
        if snap is not None and getattr(snap, "redacted", False):
            ev.note = ((ev.note + "; ") if ev.note else "") + "birth dates removed from the saved response before hashing"
        self.evidence.append(ev)
        if snap is not None:
            self.snapshots[eid] = snap
        if anchor is not None:
            self.anchors[eid] = anchor
        return ev

    def add_claim(self, field: str, section: str, value: Any, evidence: list[Evidence] | Evidence | None, *,
                  availability: str = "available", confidence: float = 1.0, as_of: str | None = None,
                  match: dict[str, str] | None = None, note: str | None = None) -> Claim:
        assert availability in AVAILABILITY, availability
        assert section in SECTIONS, section
        if availability == "available" and value is None:
            raise ValueError(f"available claim {field} without a value")
        evs = [] if evidence is None else (evidence if isinstance(evidence, list) else [evidence])
        if availability == "available" and not evs:
            raise ValueError(f"available claim {field} without evidence")
        self._cl += 1
        claim = Claim(id=f"c{self._cl}", field=field, section=section, value=value, availability=availability,
                      evidence_ids=[e.id for e in evs], confidence=confidence, as_of=as_of, match=match, note=note)
        self.claims.append(claim)
        return claim

    def mark(self, field: str, section: str, availability: str, evidence: Evidence | None = None,
             note: str | None = None) -> Claim:
        """Record that a field was checked and is not available / blocked / failed / ambiguous."""
        return self.add_claim(field, section, None, evidence, availability=availability, note=note,
                              confidence=1.0 if availability != "ambiguous" else 0.0)

    def error(self, module: str, message: str) -> None:
        self.errors.append({"module": module, "message": message[:300], "at": utc_now()})

    def claims_for(self, field: str) -> list[Claim]:
        return [c for c in self.claims if c.field == field and c.availability == "available"]
