"""Exact evidence spans.

Every `claim_span` we publish must be a literal substring of the saved snapshot, so a judge (or a
validator) can find it with a plain text search. Connectors describe what supports a claim; this
module turns that description into exact raw substrings:

- JSON: each `"key": value` pair is located in the raw response (whitespace tolerant), inside the
  record named by an optional anchor such as `("id", 7124721)`.
- HTML and XML: the span's words are matched across tags, whitespace and entities, and the raw
  slice is returned.
- A core (the value a proof rests on: a name, phone, email, address or org number) must survive in the
  returned span: a shrunken window that lost it is never quoted (no "Skip to content" for a name proof).
"""
from __future__ import annotations

import json
import re
from typing import Any

MAX_SPAN = 400


def _value_pattern(v: Any) -> str:
    text = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    if isinstance(v, (dict, list)):
        # allow optional whitespace after structural characters
        out, in_str, esc = [], False, False
        for ch in text:
            if in_str:
                out.append(re.escape(ch))
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                out.append('"')
            elif ch in "{}[],:":
                out.append(re.escape(ch) + r"\s*")
            else:
                out.append(re.escape(ch))
        return "".join(out)
    if isinstance(v, float) and v.is_integer():
        return re.escape(str(int(v))) + r"(?:\.0+)?(?![\d.])"
    if isinstance(v, int) and not isinstance(v, bool):
        return re.escape(str(v)) + r"(?:\.0+)?(?![\d.])"
    return re.escape(text)


def pair_pattern(k: str, v: Any) -> re.Pattern:
    return re.compile(r'"' + re.escape(k) + r'"\s*:\s*' + _value_pattern(v))


def _objects(raw: str) -> list[tuple[int, int]]:
    """(start, end) of every JSON object in the raw text, string aware."""
    spans, stack, in_str, esc = [], [], False, False
    for i, ch in enumerate(raw):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            spans.append((stack.pop(), i + 1))
    return spans


def region(raw: str, anchor: tuple[str, Any] | None) -> tuple[int, int]:
    if not anchor:
        return 0, len(raw)
    m = pair_pattern(*anchor).search(raw)
    if not m:
        return 0, len(raw)
    containing = [(s, e) for s, e in _objects(raw) if s <= m.start() and e >= m.end()]
    return min(containing, key=lambda x: x[1] - x[0]) if containing else (0, len(raw))


def _pairs_from(span_obj: Any) -> list[tuple[str, Any]]:
    """Top-level pairs first (in the connector's order), then scalar leaves."""
    if not isinstance(span_obj, dict):
        return []
    top = list(span_obj.items())
    leaves: list[tuple[str, Any]] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                else:
                    leaves.append((k, v))
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(span_obj)
    return top + [p for p in leaves if p not in top]


def json_spans(raw: str, span: str, anchor: tuple[str, Any] | None = None) -> list[str]:
    try:
        obj = json.loads(span)
    except (ValueError, TypeError):
        return []
    lo, hi = region(raw, anchor)
    window = raw[lo:hi]
    found: list[str] = []
    if isinstance(obj, list):
        m = re.compile(_value_pattern(obj)).search(window)
        return [m.group(0)] if m else []
    for k, v in _pairs_from(obj):
        m = pair_pattern(k, v).search(window)
        if m and m.group(0) not in found and not any(m.group(0) in f for f in found):
            found.append(m.group(0))
        if len(found) >= 5:
            break
    if not found:
        # Strings inside lists, or values the server escaped differently: match the bare value.
        for v in _string_leaves(obj):
            for text in (json.dumps(v, ensure_ascii=False), json.dumps(v), v):
                i = window.find(text)
                if i >= 0 and len(text) >= 6:
                    found.append(window[i:i + MAX_SPAN])
                    break
            if found:
                break
    return found


def _string_leaves(o: Any) -> list[str]:
    out: list[str] = []
    if isinstance(o, dict):
        for v in o.values():
            out += _string_leaves(v)
    elif isinstance(o, list):
        for v in o:
            out += _string_leaves(v)
    elif isinstance(o, str):
        out.append(o)
    return out


_TOKEN = re.compile(r"<[^>]+>|[^\s<]+")
_GAP = r"(?:\s|<[^>]{0,300}>|&[#a-zA-Z0-9]{1,10};)*"


def _html_entities(word: str) -> str:
    """Pattern for a word whose characters may be written as HTML entities in the raw page."""
    out = []
    for ch in word:
        if ch.isascii() and (ch.isalnum() or ch in "@.-_:/"):
            out.append(re.escape(ch))
        else:
            out.append(f"(?:{re.escape(ch)}|&[#a-zA-Z0-9]{{1,10}};)")
    return "".join(out)


def _core_text(s: str) -> str:
    """Lower-case letters and digits only, after decoding entities and dropping tags."""
    import html as _html
    s = _html.unescape(re.sub(r"<[^>]*>", " ", s or ""))
    return re.sub(r"[^0-9a-z\u00c0-\u024f]", "", s.lower())


def contains_core(hit: str | None, core: str | None, legacy: bool = False) -> bool:
    """True when a quoted span holds the core value it is evidence for. Tags, entities, case, spacing and
    punctuation are ignored (+47 22 33 44 55 holds 22334455). On a page in a legacy charset (legacy=True)
    the saved text has no readable non-ASCII letters, so there the longest ASCII part of the core must be
    present instead."""
    if not core:
        return True
    if not hit:
        return False
    h, c = _core_text(hit), _core_text(core)
    if c and c in h:
        return True
    if legacy and not core.isascii():
        parts = [x for x in (_core_text(x) for x in re.split(r"[^\x20-\x7e]+", core)) if len(x) >= 3]
        return bool(parts) and max(parts, key=len) in h
    return False


def text_span(raw: str, span: str, core: str | None = None, legacy: bool = False) -> str | None:
    tokens = _TOKEN.findall(span)
    if not tokens:
        return None
    n = len(tokens)
    # Try the whole span, then shrinking windows (longest first; prefer windows with digits).
    sizes = [n] + [k for k in (12, 8, 6, 4, 3, 2, 1) if k < n]
    for size in sizes:
        starts = range(0, n - size + 1)
        order = sorted(starts, key=lambda s: -sum(any(c.isdigit() for c in t) for t in tokens[s:s + size]))
        for s in order[:40]:
            window = tokens[s:s + size]
            if size == 1 and len(window[0]) < 6:
                continue
            pat = _GAP.join(_html_entities(t) for t in window)
            try:
                m = re.search(pat, raw)
            except re.error:
                continue
            if m and len(m.group(0)) <= 2 * MAX_SPAN and contains_core(m.group(0), core, legacy):
                return m.group(0)
    return None


def _char_forms(ch: str) -> str:
    """Pattern for one character written literally, as a numeric or named HTML entity, or %-encoded (ASCII
    punctuation only)."""
    from html.entities import codepoint2name
    cp = ord(ch)
    alts = [re.escape(ch), f"&#0*{cp};", f"&#[xX]0*(?i:{cp:x});"]
    if cp < 128 and not ch.isalnum() and not ch.isspace():
        alts.append(f"%(?i:{cp:02x})")
    if cp in codepoint2name:
        alts.append(f"&{codepoint2name[cp]};")
    if ch == "@":
        alts.append("&commat;")
    return "(?:" + "|".join(alts) + ")"


def encoded_span(raw: str, text: str) -> str | None:
    """The raw form of `text` when the page writes some of its characters as entities or %-escapes, as
    anti-spam email links do (mailto:kontor&#64;&#115;ka...). Only for short texts; whitespace may be any run
    of spaces or &nbsp;."""
    text = text.strip()
    if not text or len(text) > 200:
        return None
    parts = []
    for ch in text:
        parts.append(r"(?:\s|&nbsp;|&#160;)+" if ch.isspace() else _char_forms(ch))
    try:
        m = re.search("".join(parts), raw)
    except re.error:
        return None
    return m.group(0) if m else None


_ATTR = re.compile(r'([\w:-]+)="([^"<>]*)"')


def attribute_span(raw: str, span: str) -> str | None:
    """A name="value" span as the page writes it: double, single or no quotes, any case of the name."""
    m = _ATTR.fullmatch(span.strip())
    if not m:
        return None
    name, value = m.groups()
    v = re.escape(value)
    pat = rf'(?<![\w:-])(?i:{re.escape(name)})\s*=\s*(?:"{v}"|\'{v}\'|{v}(?=[\s/>]))' if value else None
    hit = re.search(pat, raw) if pat else None
    return hit.group(0) if hit else None


def _trim(span: str) -> str:
    """Spans never start or end with whitespace (the trimmed text is still a substring of the raw)."""
    return span.strip() or span


def repair(raw: str, span: str | None, content_type: str, anchor: tuple[str, Any] | None = None,
           core: str | None = None) -> tuple[str | None, list[str], bool]:
    """Returns (claim_span, extra exact spans, exact?). With a core, the exact span must contain it: the
    window is tried first, then the core alone; if neither can be quoted, the core is returned as a
    non-exact description rather than a quote of unrelated text."""
    if core and span and "json" not in content_type and raw.lstrip()[:1] not in ("{", "["):
        legacy = "\ufffd" in raw  # saved bytes were not UTF-8 (the page's text was decoded with replacements)
        hits = [h for h in (_core_repair(raw, span, core, legacy), _core_repair(raw, core, core, legacy)) if h]
        if hits:
            short = [h for h in hits if len(h.strip()) <= MAX_SPAN]
            return _trim(short[0] if short else min(hits, key=len)), [], True
        return core, [], False
    span, extra, exact = _repair(raw, span, content_type, anchor)
    if exact and span:
        span = _trim(span)
    return span, extra, exact


def _core_repair(raw: str, span: str, core: str, legacy: bool) -> str | None:
    if span in raw and contains_core(span, core, legacy):
        return span
    for hit in (attribute_span(raw, span), text_span(raw, span, core, legacy), encoded_span(raw, span)):
        if hit and contains_core(hit, core, legacy):
            return hit
    return None


def clip(span: str | None) -> str | None:
    """A span cut to MAX_SPAN characters, with no whitespace left at either end."""
    return _trim(span[:MAX_SPAN]) if span else span


def _repair(raw: str, span: str | None, content_type: str, anchor: tuple[str, Any] | None = None) -> tuple[str | None, list[str], bool]:
    if not span:
        return span, [], False
    if span in raw:
        return span, [], True
    if not raw.strip():  # empty body (for example a 404): keep the description, flagged as not a quote
        return span, [], False
    if "json" in content_type or raw.lstrip()[:1] in ("{", "["):
        found = json_spans(raw, span, anchor)
        if found:
            return found[0], found[1:], True
        if len(raw) <= MAX_SPAN:
            return raw, [], True
        return span, [], False
    tag = re.fullmatch(r"<\w+((?:\s+[\w:-]+=\"[^\"]*\")+)\s*/?>", span.strip())
    if tag:  # a single tag: quote its attributes as they appear in the raw page
        for attr in re.findall(r'[\w:-]+="[^"]*"', tag.group(1)):
            hit = attr if attr in raw else attribute_span(raw, attr)
            if hit:
                return hit, [], True
    hit = attribute_span(raw, span)
    if hit:
        return hit, [], True
    hit = text_span(raw, span)
    if hit:
        return hit, [], True
    hit = encoded_span(raw, span)
    if hit:
        return hit, [], True
    return span, [], False
