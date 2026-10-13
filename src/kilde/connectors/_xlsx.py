"""Minimal .xlsx reader (standard library only): yields each row of a worksheet as a list of strings.

Enough for the plain tabular exports public agencies publish (shared strings, inline strings and
numbers). Dates come back as Excel serial numbers; see `_records.excel_date`.
"""
from __future__ import annotations

import io
import re
import zipfile
from typing import Iterator
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def rows(data: bytes, sheet: int = 1) -> Iterator[list[str]]:
    z = zipfile.ZipFile(io.BytesIO(data))
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
    root = ET.fromstring(z.read(f"xl/worksheets/sheet{sheet}.xml"))
    for r in root.iter(f"{NS}row"):
        cells: dict[int, str] = {}
        for c in r.iter(f"{NS}c"):
            t = c.get("t")
            if t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{NS}t"))
            else:
                v = c.find(f"{NS}v")
                if v is None or v.text is None:
                    continue
                val = shared[int(v.text)] if t == "s" else v.text
            cells[_col(c.get("r"))] = val
        if cells:
            yield [cells.get(i, "") for i in range(max(cells) + 1)]


def dicts(data: bytes, sheet: int = 1) -> list[dict[str, str]]:
    it = rows(data, sheet)
    header = [h.strip() for h in next(it, [])]
    return [{h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(header) if h} for r in it]
