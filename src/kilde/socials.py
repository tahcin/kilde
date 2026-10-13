"""One social profile, one claim.

A site often links the same page in several URL forms: www., m. or mobile hosts, a trailing slash, a
query string, upper case, Facebook's /pg/<handle>/ tab paths, and the id-bearing forms Facebook gives a
page (profile.php?id=<id>, /people/<Name>/<id>, /p/<Name>-<id>, /pages/<Name>/<id>, /<Name>-<id>).
`profile_key` reduces a canonical value ('platform.com/handle') to the page it names, and `duplicates`
says which values repeat an earlier one. Nothing is fetched from the platform: two forms count as one
page only when the URLs themselves show it (the same numeric id, or an id form whose embedded page
name spells the other link's handle, both linked from the same verified site).
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlsplit

_TRANS = str.maketrans({"æ": "ae", "ø": "o", "å": "a", "ä": "a", "ö": "o", "ü": "u", "é": "e", "è": "e"})


def _squash(s: str) -> str:
    s = unicodedata.normalize("NFC", unquote(s)).lower().translate(_TRANS)
    return re.sub(r"[^a-z0-9]", "", s)


def _low(s: str) -> str:
    return unicodedata.normalize("NFC", unquote(s)).lower().lstrip("@")


def _split(value: str) -> tuple[str, list[str], str]:
    v = str(value).strip()
    if "://" not in v:
        v = "https://" + v
    p = urlsplit(v)
    host = (p.hostname or "").lower()
    host = re.sub(r"^(?:www|m|mobile|web|touch|business|[a-z]{2})\.", "", host) if host.count(".") >= 2 else host
    host = {"fb.com": "facebook.com", "fb.me": "facebook.com", "twitter.com": "x.com"}.get(host, host)
    segs = [s for s in unquote(p.path).split("/") if s]
    return host, segs, p.query


def profile_key(value: str) -> tuple[str, str, str]:
    """(host, identity, page name) for a canonical social value.

    identity is 'id:<digits>' when the URL carries a Facebook page id, else 'h:<handle>'. page name is the
    squashed name an id form embeds (empty for plain handles)."""
    host, segs, query = _split(value)
    if host == "facebook.com":
        if segs and segs[0].lower() == "pg" and len(segs) >= 2:
            segs = segs[1:]
        if segs and segs[0].lower() == "profile.php":
            ids = [v for k, v in parse_qsl(query) if k == "id" and v.isdigit()]
            if ids:
                return host, "id:" + ids[0].lstrip("0"), ""
        if len(segs) >= 3 and segs[0].lower() in ("people", "pages") and segs[-1].isdigit():
            return host, "id:" + segs[-1].lstrip("0"), _squash(segs[1])
        last = segs[1] if len(segs) >= 2 and segs[0].lower() == "p" else (segs[0] if segs else "")
        m = re.fullmatch(r"(.*?)-?(\d{9,})", last)
        if m:
            return host, "id:" + m.group(2).lstrip("0"), _squash(m.group(1))
        return host, "h:" + _low(last), ""
    if host == "linkedin.com" and len(segs) >= 2:
        return host, "h:" + segs[0].lower() + "/" + _low(segs[1]), ""
    if host == "youtube.com" and segs and segs[0].lower() in ("channel", "c", "user") and len(segs) >= 2:
        ident = segs[1] if segs[0].lower() == "channel" else _low(segs[1])  # channel ids are case sensitive
        return host, "h:" + segs[0].lower() + "/" + ident, ""
    return host, "h:" + (_low(segs[0]) if segs else ""), ""


def duplicates(values: list[str]) -> set[int]:
    """Indexes of values that name the same profile as another value in the list. Of a group, a plain handle
    is kept over an id form (it is the page's public address); otherwise the first one is kept."""
    keys = [profile_key(v) for v in values]
    order = sorted(range(len(values)), key=lambda i: (keys[i][1].startswith("id:"), i))
    kept: list[int] = []
    drop: set[int] = set()
    for i in order:
        host, ident, name = keys[i]
        same = False
        for j in kept:
            h2, id2, name2 = keys[j]
            if h2 != host:
                continue
            if id2 == ident:
                same = True
            elif ident.startswith("id:") and name and id2.startswith("h:") and _squash(id2[2:]) == name:
                same = True  # /Name-<id> whose name spells a handle the same site links
            if same:
                break
        if same:
            drop.add(i)
        else:
            kept.append(i)
    return drop
