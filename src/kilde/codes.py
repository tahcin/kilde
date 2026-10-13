"""Readable names for registry codes, from SSB Klass (CC BY 4.0), bundled at build time.

`scripts/build_codes.py` refreshes `data/codes.json`. The run itself makes no requests here.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "codes.json"


@lru_cache(maxsize=1)
def _codes() -> dict[str, dict[str, str]]:
    if DATA.exists():
        return json.loads(DATA.read_text(encoding="utf-8"))
    return {}


def code_name(kind: str, code: str | None) -> str | None:
    if not code:
        return None
    return _codes().get(kind, {}).get(code)
