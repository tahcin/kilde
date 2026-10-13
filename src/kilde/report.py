"""Static HTML report: one self-contained file (inline CSS, JS, font and data) that works offline.

The envelopes are slimmed to what the page shows and embedded as JSON; the page renders the company list,
profiles, the ask panel and the compare view in the browser, so 1,500 companies stay well under 15 MB.

Typeface: Figtree (Copyright 2022 The Figtree Project Authors, https://github.com/erikdkennedy/figtree),
licensed under the SIL Open Font License 1.1. Embedded as a Latin subset of the variable font (weights 400 to
700); the copyright and licence are kept in the font's name table.
"""
from __future__ import annotations

import base64
import gzip
import html
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

SECTIONS = ("identity", "financials", "leadership", "locations", "web_presence", "hiring", "activity")
NLOD = ("Contains data under the Norwegian licence for Open Government data (NLOD) distributed by "
        "Brønnøysundregistrene")
SSB = "Industry, sector and legal-form names from Statistics Norway (SSB) Klass, licensed CC BY 4.0."
MAX_BYTES = 14 * 1024 * 1024
FIN_FIELDS = {"revenue", "operating_result", "salary_costs", "profit_before_tax", "annual_result", "total_assets",
              "cash_and_bank", "equity", "debt"}

KNOWN_SOURCES = {
    "data.brreg.no": ("Brønnøysundregistrene: Enhetsregisteret and Regnskapsregisteret", "NLOD 2.0"),
    "api.gleif.org": ("GLEIF LEI records", "CC0"),
    "arbeidsplassen.nav.no": ("NAV job ads (arbeidsplassen)", "NAV public feed terms"),
    "pam-stilling-feed.nav.no": ("NAV job ads (public feed)", "NAV public feed terms"),
    "api.finanstilsynet.no": ("Finanstilsynet licence register", "open API"),
    "www.wikidata.org": ("Wikidata", "CC0"),
    "qlever.cs.uni-freiburg.de": ("Wikidata via QLever", "CC0"),
    "qlever.dev": ("Wikidata via QLever", "CC0"),
}


# ---------------------------------------------------------------------------
# Data shaping
# ---------------------------------------------------------------------------

def _slim_envelope(env: dict, urls: dict[str, int], span_limit: int | None) -> dict:
    ev_index: dict[str, int] = {}
    evs: list[list] = []
    for ev in env.get("evidence") or []:
        url = ev.get("source_url") or ""
        if url not in urls:
            urls[url] = len(urls)
        span = ev.get("claim_span") or ""
        if span_limit is not None and len(span) > span_limit:
            span = span[:span_limit] + " [...]"
        ev_index[ev.get("id")] = len(evs)
        evs.append([urls[url], ev.get("retrieved_at") or "", ev.get("effective_date") or "", span,
                    ev.get("source_class") or ""])
    claims = []
    for c in env.get("claims") or []:
        row = {"i": c.get("id"), "f": c.get("field"), "s": c.get("section"), "a": c.get("availability"),
               "e": [ev_index[x] for x in c.get("evidence_ids") or [] if x in ev_index]}
        if c.get("value") is not None:
            row["v"] = c["value"]
        if c.get("as_of"):
            row["d"] = c["as_of"]
        if c.get("note"):
            row["n"] = c["note"]
        match = c.get("match") or {}
        if match.get("method"):
            row["m"] = match["method"]
            # Registry claims share one generic detail; keep the detail only where it explains a match.
            if match["method"] != "registry_endpoint" and match.get("detail"):
                row["md"] = str(match["detail"])[:240]
        claims.append(row)
    avail = [c for c in env.get("claims") or [] if c.get("availability") == "available"]
    fields = {c.get("field") for c in avail}
    revenue = None
    for c in avail:
        v = c.get("value") if isinstance(c.get("value"), dict) else {}
        if c.get("field") == "revenue" and v.get("amount") is not None:
            if revenue is None or str(v.get("year")) > str(revenue[1]):
                revenue = (v.get("amount"), v.get("year"))
    secs = env.get("sections") or {}
    summary = env.get("summary") or {}
    return {
        "o": str(env.get("organisation_number")),
        "n": env.get("legal_name") or "",
        "st": (env.get("run") or {}).get("terminal_status") or "",
        "sec": {s: (secs.get(s) or {}).get("state") or "failed" for s in SECTIONS},
        "c": claims,
        "ev": evs,
        "sum": {"t": [[x.get("text") or "", x.get("claim_ids") or []] for x in summary.get("sentences") or []],
                "u": summary.get("unknowns") or []},
        "ch": env.get("changes") or [],
        "err": [e.get("message") if isinstance(e, dict) else str(e) for e in env.get("errors") or []][:10],
        "op": env.get("operations") or {},
        "fl": {"web": "official_website" in fields, "jobs": "job_ad" in fields,
               "chg": bool(env.get("changes")) or "registry_change" in fields},
        "rev": revenue[0] if revenue else None,
        "nf": len(avail),
    }


def build_data(envelopes: list[dict], span_limit: int | None = None) -> dict:
    urls: dict[str, int] = {}
    companies = [_slim_envelope(e, urls, span_limit) for e in envelopes]
    return {"u": list(urls), "co": companies}


def _sources(envelopes: list[dict], run_report: dict) -> list[dict]:
    if run_report.get("sources"):
        out = []
        for s in run_report["sources"]:
            out.append(s if isinstance(s, dict) else {"name": str(s), "licence": ""})
        return out
    counts: dict[str, int] = {}
    for e in envelopes:
        for ev in e.get("evidence") or []:
            host = (urlsplit(ev.get("source_url") or "").hostname or "").lower()
            if host:
                key = host if host in KNOWN_SOURCES else "company_owned" if ev.get("source_class") == "company_owned" else host
                counts[key] = counts.get(key, 0) + 1
    out = []
    for key, n in sorted(counts.items(), key=lambda x: -x[1]):
        if key in KNOWN_SOURCES:
            name, lic = KNOWN_SOURCES[key]
        elif key == "company_owned":
            name, lic = "Company websites (pages that prove the org number; robots.txt obeyed)", "company-owned pages, quoted for evidence"
        else:
            name, lic = key, ""
        out.append({"name": name, "licence": lic, "evidence": n})
    return out


def _run_meta(envelopes: list[dict], run_report: dict) -> dict:
    runs = [e.get("run") or {} for e in envelopes]
    ops = [e.get("operations") or {} for e in envelopes]
    started = run_report.get("started_at") or min((r.get("started_at") for r in runs if r.get("started_at")), default="")
    completed = run_report.get("completed_at") or max((r.get("completed_at") for r in runs if r.get("completed_at")), default="")
    runtime = run_report.get("runtime_ms")
    if runtime is None and run_report.get("runtime_s") is not None:
        runtime = float(run_report["runtime_s"]) * 1000
    if runtime is None and started and completed:
        try:
            a = datetime.fromisoformat(started.replace("Z", "+00:00"))
            b = datetime.fromisoformat(completed.replace("Z", "+00:00"))
            runtime = (b - a).total_seconds() * 1000
        except ValueError:
            runtime = None
    requests = run_report.get("requests")
    if requests is None:
        requests = (run_report.get("operations") or {}).get("requests")
    if requests is None:
        requests = sum(int(o.get("requests") or 0) for o in ops)
    cost = run_report.get("cost_usd", run_report.get("third_party_cost_usd"))
    if cost is None:
        cost = (run_report.get("operations") or {}).get("third_party_cost_usd")
    if cost is None:
        cost = sum(float(o.get("third_party_cost_usd") or 0) for o in ops)
    statuses: dict[str, int] = {}
    for r in runs:
        statuses[r.get("terminal_status") or "missing"] = statuses.get(r.get("terminal_status") or "missing", 0) + 1
    agent = run_report.get("agent") or next((e.get("agent") for e in envelopes if e.get("agent")), {}) or {}
    if isinstance(agent, str):
        agent = {"name": agent, "version": run_report.get("version"), "commit": run_report.get("commit")}
    previous = (run_report.get("inputs") or {}).get("previous_envelopes")
    return {
        "run_id": run_report.get("run_id") or next((r.get("run_id") for r in runs if r.get("run_id")), ""),
        "started_at": started, "completed_at": completed,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "companies": run_report.get("companies", len(envelopes)),
        "requests": requests, "runtime_ms": runtime, "cost_usd": cost, "statuses": statuses,
        "agent": agent, "previous": previous,
        "sources": _sources(envelopes, run_report), "nlod": NLOD,
    }


def _embed(obj: Any) -> str:
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    s = s.replace("</", "<\\/").replace("<!--", "<\\u0021--")
    # Keep the page free of raw em dashes and of line separators that break inline scripts.
    return s.replace("\u2014", "\\u2014").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def write_report(envelopes: list[dict], path: str, run_report: dict) -> None:
    run_report = run_report or {}
    meta = _run_meta(envelopes, run_report)
    data = build_data(envelopes)
    payload = _embed(data)
    data_tag = '<script type="application/json" id="data">'
    if len(payload.encode("utf-8")) >= MAX_BYTES:
        # Large runs: embed the same JSON gzip-compressed (base64) and let the browser inflate it, so every quote
        # stays exact. Spans are shortened only if even the compressed form would not fit.
        data_tag = '<script type="application/octet-stream" id="data" data-encoding="gzip+base64">'
        for limit in (None, 400, 160, 60):
            data = build_data(envelopes, limit)
            raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            payload = base64.b64encode(gzip.compress(raw, compresslevel=9, mtime=0)).decode("ascii")
            if len(payload) < MAX_BYTES:
                break
    noscript_rows = "".join(
        f'<li id="org-{html.escape(c["o"])}">{html.escape(c["n"])} ({html.escape(c["o"])})</li>'
        for c in (data or {}).get("co", [])[:2000])
    css = CSS.replace("__FONT__", FONT_WOFF2_B64)
    page = (PAGE.replace("/*CSS*/", css).replace("/*JS*/", JS)
            .replace("__TITLE__", html.escape(f"Kilde report {meta['run_id']}".strip()))
            .replace("__META__", _embed(meta)).replace("__DATATAG__", data_tag).replace("__DATA__", payload)
            .replace("__NOSCRIPT__", noscript_rows).replace("__NLOD__", html.escape(NLOD))
            .replace("__SSB__", html.escape(SSB)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)


ICON = {
    "search": '<path d="m21 21-4.3-4.3"/><circle cx="11" cy="11" r="8"/>',
    "filter": '<path d="M3 6h18"/><path d="M7 12h10"/><path d="M10 18h4"/>',
}


def _icon(name: str) -> str:
    return ('<svg class="ic" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + ICON[name] + '</svg>')


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>__TITLE__</title>
<style>/*CSS*/</style>
</head>
<body>
<a class="skip" href="#main">Skip to company profile</a>
<header class="bar">
  <a class="word" href="#" aria-label="Kilde, back to the first company"><span class="mark" aria-hidden="true"></span>Kilde</a>
  <dl class="run" id="run"></dl>
  <a class="bar-link" href="#sources">Sources and licences</a>
</header>
<div class="layout" id="layout">
  <aside class="side" aria-label="Companies">
    <div class="tools" role="search">
      <div class="search">""" + _icon("search") + """
        <label class="vh" for="q">Search by name or organisation number</label>
        <input id="q" type="search" placeholder="Search name or org number" autocomplete="off" spellcheck="false">
      </div>
      <div class="tools-row">
        <p class="count" id="count" aria-live="polite"></p>
        <button type="button" class="tbtn" id="ftoggle" aria-expanded="false" aria-controls="filters">""" + _icon("filter") + """<span>Filter</span></button>
        <label class="sortl"><span class="vh">Sort companies</span><select id="sort">
          <option value="name">Name</option><option value="org">Org number</option>
          <option value="rev">Revenue</option><option value="facts">Facts found</option>
          <option value="chg">Changes</option></select></label>
      </div>
      <div class="filters" id="filters" hidden>
        <fieldset><legend class="vh">Show only companies with</legend>
          <label><input type="checkbox" id="f-web"> Verified website</label>
          <label><input type="checkbox" id="f-jobs"> Job ads</label>
          <label><input type="checkbox" id="f-chg"> Registry changes</label>
        </fieldset>
        <div class="fsel">
          <label><span>Section</span><select id="f-sec"></select></label>
          <label><span>State</span><select id="f-state"></select></label>
        </div>
        <div class="legend" id="legend"></div>
      </div>
    </div>
    <ul class="list" id="list"></ul>
    <button type="button" class="tbtn more" id="more" hidden>Show more companies</button>
    <div class="tray" id="tray" hidden><span id="tray-text"></span><a class="btn" id="tray-go" href="#">Compare</a><button type="button" class="tbtn" id="tray-clear">Clear</button></div>
  </aside>
  <main id="main" class="main" tabindex="-1"></main>
</div>
<footer class="foot" id="sources">
  <h2>Sources and licences</h2>
  <div id="srclist"></div>
  <p>__NLOD__.</p>
  <p>__SSB__</p>
  <p id="colophon"></p>
</footer>
<div class="scrim" id="scrim" hidden></div>
<div class="pop" id="pop" role="dialog" aria-modal="false" aria-label="Source" tabindex="-1" hidden></div>
<noscript><p>This report needs JavaScript for search and profiles. Companies in this run:</p><ul>__NOSCRIPT__</ul></noscript>
<script type="application/json" id="meta">__META__</script>
__DATATAG____DATA__</script>
<script>/*JS*/</script>
</body>
</html>
"""

CSS = r"""
@font-face{font-family:"Figtree";font-style:normal;font-weight:400 700;font-display:swap;
  src:url(data:font/woff2;base64,__FONT__) format("woff2")}
:root{
  --bg:#f6f4ef;--raise:#fcfbf8;--well:#edeae2;--ink:#1c262b;--ink2:#3a464c;--muted:#5a666c;--faint:#a3aaa9;
  --line:#d9d5ca;--line2:#e6e2d9;--edge:#b9b4a8;--accent:#1f5673;--accent2:#163f55;--on:#ffffff;
  --sel:#e5e9e6;--hover:#eeece5;--hl:#f1e2b0;
  --ok:#3b6b4c;--amb:#8a6410;--blk:#6d4b7f;--fail:#a12b3b;--na:#7c868b;
  --scrim:rgba(28,38,43,.28);
}
@media (prefers-color-scheme:dark){:root{
  --bg:#13181b;--raise:#1a2125;--well:#222a2e;--ink:#e6e3dc;--ink2:#c9cdcb;--muted:#9da7ab;--faint:#58636a;
  --line:#2c353a;--line2:#232b30;--edge:#46525a;--accent:#8cc3de;--accent2:#b3d8ea;--on:#0f1a20;
  --sel:#1f2a30;--hover:#1a2226;--hl:#4b4020;
  --ok:#8dc49f;--amb:#ddb663;--blk:#c4a6d6;--fail:#ef939f;--na:#8b969b;
  --scrim:rgba(0,0,0,.5);
}}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Figtree",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  -webkit-font-smoothing:antialiased;font-kerning:normal}
em,i,cite,dfn,address,var{font-style:normal}
a{color:var(--accent);text-decoration-thickness:1px;text-underline-offset:3px}
a:hover{color:var(--accent2)}
button,input,select{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}
h1,h2,h3{margin:0;line-height:1.25;font-weight:600}
p{margin:0}
.num,table,.run dd,.stat-v{font-variant-numeric:tabular-nums}
.vh{position:absolute!important;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.skip{position:absolute;left:12px;top:-48px;background:var(--raise);padding:8px 12px;z-index:60;border:1px solid var(--edge)}
.skip:focus{top:12px}
.ic{width:16px;height:16px;flex:none}
.muted{color:var(--muted)}

/* run bar */
.mark{display:inline-block;width:22px;height:22px;border-radius:6px;background:var(--accent);box-shadow:inset -7px -7px 0 0 color-mix(in srgb,var(--accent) 38%,#ffffff);margin-right:9px;vertical-align:-4px;flex:none}
.bar{display:flex;align-items:center;gap:10px 36px;flex-wrap:wrap;padding:14px 28px;border-bottom:1px solid var(--line)}
.word{font-size:19px;font-weight:700;letter-spacing:-.02em;color:var(--ink);text-decoration:none}
.run{display:flex;flex-wrap:wrap;gap:4px 24px;margin:0;font-size:13.5px}
.run div{display:flex;gap:6px}
.run dt{color:var(--muted)}
.run dd{margin:0;font-weight:500}
.bar-link{margin-left:auto;font-size:13.5px}

/* layout */
.layout{display:grid;grid-template-columns:340px minmax(0,1fr)}
.side{border-right:1px solid var(--line);display:flex;flex-direction:column;min-width:0}
@media (min-width:901px){.side{position:sticky;top:0;height:100vh;align-self:start;overflow:hidden}}
.tools{padding:16px 16px 12px;border-bottom:1px solid var(--line)}
.search{position:relative}
.search .ic{position:absolute;left:11px;top:11px;color:var(--muted);pointer-events:none}
input[type=search],input[type=text]{width:100%;height:38px;padding:0 12px 0 34px;border:1px solid var(--line);border-radius:6px;background:var(--raise);font-size:15px;
  transition:border-color .12s}
input[type=search]:focus,input[type=text]:focus{border-color:var(--accent);outline:none}
input[type=search]:focus-visible,input[type=text]:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.tools-row{display:flex;align-items:center;gap:6px;margin-top:10px}
.count{flex:1;font-size:13px;color:var(--muted)}
select{background:transparent;border:1px solid transparent;border-radius:5px;padding:4px 6px;font-size:13px;color:var(--ink2);cursor:pointer}
select:hover{border-color:var(--line)}
.tbtn{display:inline-flex;align-items:center;gap:6px;background:none;border:1px solid transparent;border-radius:5px;padding:4px 8px;font-size:13px;color:var(--ink2);cursor:pointer}
.tbtn:hover{border-color:var(--line)}
.tbtn[aria-expanded=true]{color:var(--accent)}
.tbtn .ic{width:14px;height:14px}
.filters{padding-top:12px;font-size:13.5px}
.filters fieldset{border:0;margin:0;padding:0;display:grid;gap:6px}
.filters fieldset label{display:flex;gap:8px;align-items:center;cursor:pointer}
.filters input[type=checkbox]{width:15px;height:15px;margin:0;accent-color:var(--accent)}
.fsel{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}
.fsel label{display:grid;gap:2px;color:var(--muted);font-size:12.5px}
.fsel select{border-color:var(--line);background:var(--raise);width:100%;padding:5px 6px}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:12px;color:var(--muted);font-size:12.5px}
.legend span{display:inline-flex;align-items:center;gap:5px}

.list{list-style:none;margin:0;padding:0;overflow:auto;flex:1;min-height:0;overscroll-behavior:contain}
.row{display:grid;grid-template-columns:minmax(0,1fr) auto 22px;align-items:center;gap:0 10px;padding:0 12px 0 0;border-bottom:1px solid var(--line2);transition:background-color .12s}
.row:hover{background:var(--hover)}
.row.cur{background:var(--sel)}
.row-link{display:block;padding:10px 0 10px 16px;color:var(--ink);text-decoration:none;min-width:0}
.row-link:hover{color:var(--ink)}
.row-link:focus-visible{outline-offset:-2px}
.nm{display:block;font-size:14px;font-weight:500;line-height:1.35;overflow-wrap:anywhere}
.row.cur .nm{font-weight:600}
.sub{display:flex;gap:10px;font-size:12.5px;color:var(--muted);margin-top:2px}
.cov{display:block;line-height:0}
.pick{display:flex;align-items:center;justify-content:center;opacity:0;transition:opacity .12s}
.row:hover .pick,.pick:focus-within,.pick:has(input:checked){opacity:1}
@media (hover:none){.pick{opacity:1}}
.pick input{width:16px;height:16px;margin:0;accent-color:var(--accent);cursor:pointer}
.empty{color:var(--muted);padding:16px}
.more{margin:12px 16px;align-self:flex-start;color:var(--accent)}
.tray{position:sticky;bottom:0;display:flex;align-items:center;gap:10px;padding:10px 16px;border-top:1px solid var(--line);background:var(--bg);font-size:13px}
.tray span{flex:1}
.btn{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:6px;border:1px solid var(--accent);background:var(--accent);color:var(--on);font-size:13.5px;font-weight:500;text-decoration:none;cursor:pointer;transition:background-color .12s,color .12s}
.btn:hover{color:var(--on);background:var(--accent2);border-color:var(--accent2)}
.btn[aria-disabled=true]{opacity:.45;cursor:not-allowed}
.btn.quiet{background:transparent;color:var(--accent);border-color:var(--line)}
.btn.quiet:hover{border-color:var(--accent);color:var(--accent)}
.btn .ic{width:15px;height:15px}

/* state marks */
.mk{width:9px;height:9px;flex:none;vertical-align:-1px}
.st-available{color:var(--ok)}.st-not_available{color:var(--na)}.st-ambiguous{color:var(--amb)}
.st-blocked{color:var(--blk)}.st-failed{color:var(--fail)}.st-not_applicable{color:var(--na)}
.badge{display:inline-flex;align-items:center;gap:6px;font-weight:500}
.badge .w{color:var(--ink2)}

/* profile */
.main{padding:32px 48px 96px;min-width:0}
.main:focus{outline:none}
.pro,.cmpv{max-width:920px}
.back{display:none;align-items:center;gap:4px;margin:0 0 14px -4px;padding:4px;font-size:14px;color:var(--accent);background:none;border:0;text-decoration:none}
.head{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 24px;align-items:start}
.head h1{font-size:28px;letter-spacing:-.015em;overflow-wrap:anywhere}
.idline{grid-column:1;display:flex;flex-wrap:wrap;gap:2px 14px;color:var(--ink2);font-size:15px}
.idline .num{color:var(--ink)}
.head .btn{grid-column:2;grid-row:1}
.stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px 24px;margin:24px 0 0;padding:16px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.stat dt{font-size:13px;color:var(--muted)}
.stat dd{margin:2px 0 0}
.stat-v{display:block;font-size:20px;font-weight:600;letter-spacing:-.01em;line-height:1.3}
.stat-v.none{font-size:15px;font-weight:500;color:var(--muted);line-height:1.75}
.stat-s{display:block;font-size:12.5px;color:var(--muted)}
button.vbtn{background:none;border:0;padding:0;text-align:left;cursor:pointer;color:inherit;font:inherit;text-decoration:underline;text-decoration-color:transparent;text-underline-offset:4px;text-decoration-thickness:1px;transition:text-decoration-color .12s}
button.vbtn:hover{text-decoration-color:var(--muted)}
.toc{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:2px 18px;margin:20px 0 0;padding:10px 0;background:var(--bg);border-bottom:1px solid var(--line);font-size:13.5px}
.toc a{color:var(--ink2);text-decoration:none;padding:2px 0}
.toc a:hover{color:var(--accent)}
.toc a[aria-current=true]{color:var(--accent);font-weight:600}
.sec{padding-top:36px;scroll-margin-top:52px}
.sh{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;margin-bottom:12px}
.sh h2{font-size:19px;letter-spacing:-.01em}
.sh .badge{font-size:13.5px}
.lede{max-width:68ch;font-size:16.5px;line-height:1.65;color:var(--ink)}
.lede p+p{margin-top:10px}
.cites{font-size:11px;line-height:0;margin-left:2px;color:var(--muted);white-space:nowrap}
.cite{background:none;border:0;padding:2px 1px;font:600 11px/1 inherit;color:var(--accent);cursor:pointer;font-variant-numeric:tabular-nums}
.cite:hover{text-decoration:underline}
.gaps{margin-top:14px;color:var(--muted);font-size:14px;max-width:68ch}
.gaps a{color:var(--ink2)}
.ask form{display:flex;gap:8px;max-width:620px}
.ask input[type=text]{padding-left:12px}
.sugg{display:flex;flex-wrap:wrap;gap:4px 16px;margin-top:10px;font-size:14px}
.sugg button{background:none;border:0;padding:2px 0;color:var(--accent);cursor:pointer;text-align:left}
.sugg button:hover{text-decoration:underline;text-underline-offset:3px}
.answer{margin-top:16px;max-width:68ch;font-size:15.5px;line-height:1.6}
.answer:empty{display:none}
.answer .q{color:var(--muted);font-size:13.5px;margin-bottom:4px}
.answer p+p{margin-top:6px}
.answer .nf{color:var(--ink2)}

dl.facts{margin:0}
.fr{display:grid;grid-template-columns:190px minmax(0,1fr) auto;gap:2px 20px;padding:9px 0;border-top:1px solid var(--line2);transition:background-color .6s}
.fr dt{color:var(--muted);font-size:14px;padding-top:1px}
.fr dd{margin:0;min-width:0;overflow-wrap:anywhere}
.fr dd.sc{text-align:right}
.grp{font-size:15px;margin:22px 0 6px}
.grp:first-child{margin-top:0}
.note{display:block;color:var(--muted);font-size:13.5px;margin-top:2px}
.sub2{color:var(--muted);font-size:13.5px}
button.src{background:none;border:0;padding:1px 0;font-size:12.5px;color:var(--muted);cursor:pointer;white-space:nowrap;text-align:right}
button.src:hover{color:var(--accent);text-decoration:underline;text-underline-offset:3px}
.flash{background:var(--hl)!important;transition:none}

.tablewrap{max-width:100%}
table{border-collapse:collapse;width:100%}
.tcap{display:flex;flex-wrap:wrap;gap:2px 12px;align-items:baseline;margin:0 0 6px}
.tcap h3{font-size:15px}
.tcap p{color:var(--muted);font-size:13.5px}
.fin{width:auto;min-width:min(100%,520px)}
.fin td,.fin thead th:not(:first-child){min-width:132px}
.fin th[scope=row]{min-width:180px}
.fin th,.fin td{padding:8px 0 8px 20px;text-align:right;vertical-align:baseline;border-top:1px solid var(--line2)}
.fin th[scope=row]{padding-left:0;text-align:left;font-weight:400;color:var(--muted);font-size:14px}
.fin thead th{border-top:0;border-bottom:1px solid var(--line);font-weight:600;font-size:14px;padding-top:0}
.fin thead th:first-child{padding-left:0}
.fin .per{display:block;font-weight:400;font-size:12px;color:var(--muted)}
.fin tr.brk th,.fin tr.brk td{padding-top:20px}
.fin tfoot td,.fin tfoot th{border-top:1px solid var(--line);padding-top:8px}
.nr{color:var(--muted);font-size:13px}
.fin+.tcap,.tablewrap+.tablewrap{margin-top:28px}
.facts.after{margin-top:20px}
.yl{display:flex;flex-wrap:wrap;gap:2px 12px}

.items{list-style:none;margin:0;padding:0}
.it{display:grid;grid-template-columns:190px minmax(0,1fr) auto;gap:2px 20px;padding:9px 0;border-top:1px solid var(--line2);transition:background-color .6s}
.it .k{color:var(--muted);font-size:14px}
.it .k.num{color:var(--ink2)}
.it .v{min-width:0;overflow-wrap:anywhere}
.it .sc{text-align:right}
.t{font-weight:500}
.chg .arrow{color:var(--muted)}

.matchp{max-width:68ch}
.matchp p+p{margin-top:8px}
.srct.srcs{margin-top:18px;max-width:640px}

.errs{margin-top:28px;color:var(--muted);font-size:13.5px}

/* compare */
.cmpv h1{font-size:24px;letter-spacing:-.01em;margin-bottom:18px}
.cmp{table-layout:fixed}
.cmp th,.cmp td{padding:9px 16px 9px 0;text-align:left;vertical-align:top;border-top:1px solid var(--line2);overflow-wrap:anywhere}
.cmp thead th{border-top:0;padding-bottom:12px;font-weight:600;vertical-align:bottom}
.cmp thead th a{color:var(--ink);text-decoration:none;font-size:16px;display:block;line-height:1.3}
.cmp thead th a:hover{color:var(--accent)}
.cmp thead .sub2{display:flex;gap:6px 10px;align-items:center;font-weight:400}
.cmp .num{white-space:nowrap;overflow-wrap:normal}
.cmp thead .rm{background:none;border:0;padding:2px;color:var(--muted);cursor:pointer;line-height:0}
.cmp thead .rm:hover{color:var(--fail)}
.cmp th[scope=row]{font-weight:400;color:var(--muted);font-size:14px;width:190px}
.cmp tr.g th{border-top:0;padding-top:26px;color:var(--ink);font-weight:600;font-size:15px}
.cmp .yr{display:block;color:var(--muted);font-size:12.5px}
.cmp .cov{display:block;margin:5px 0 4px}

/* popover */
.pop{position:absolute;z-index:50;width:420px;max-width:calc(100vw - 24px);max-height:min(70vh,560px);overflow:auto;
  background:var(--raise);border:1px solid var(--edge);border-radius:8px;padding:14px 16px 16px;font-size:14px;
  opacity:0;transform:translateY(-3px);transition:opacity .12s ease-out,transform .12s ease-out}
.pop.open{opacity:1;transform:none}
.pop:focus{outline:none}
.pop-h{display:flex;align-items:flex-start;gap:12px;margin-bottom:6px}
.pop-h strong{flex:1;font-weight:600;font-size:14.5px}
.pop .x{background:none;border:0;padding:2px;margin:-2px -4px 0 0;color:var(--muted);cursor:pointer;line-height:0}
.pop .x:hover{color:var(--ink)}
.pop-v{color:var(--ink);overflow-wrap:anywhere}
.pop-e{margin-top:12px;padding-top:12px;border-top:1px solid var(--line2)}
.pop-e a.u{display:inline-flex;gap:5px;align-items:baseline;word-break:break-all}
.pop-e a.u .ic{width:13px;height:13px;flex:none;align-self:center}
.pop-m{color:var(--muted);font-size:13px;margin-top:3px}
.pop-ql{color:var(--muted);font-size:13px;margin-top:10px}
pre.q{margin:4px 0 0;padding:8px 10px;background:var(--well);border-radius:4px;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:200px;overflow:auto;color:var(--ink)}
pre.q mark{background:var(--hl);color:var(--ink);border-radius:2px}
.pop-j{display:inline-block;margin-top:12px;font-size:13.5px}
.scrim{position:fixed;inset:0;z-index:40;background:var(--scrim)}
.pop.sheet{position:fixed;left:0;right:0;bottom:0;top:auto!important;width:auto;max-width:none;max-height:78vh;border-radius:12px 12px 0 0;border-bottom:0;padding:16px 16px 24px;transform:translateY(8px)}
.pop.sheet.open{transform:none}

/* footer */
.foot{border-top:1px solid var(--line);padding:28px 28px 48px;color:var(--ink2);font-size:13.5px}
.foot h2{font-size:15px;margin-bottom:10px;color:var(--ink)}
.foot p{max-width:90ch;margin-top:8px}
.srct{max-width:900px;margin-bottom:14px}
.srct th,.srct td{text-align:left;padding:6px 16px 6px 0;border-top:1px solid var(--line2);vertical-align:top}
.srct thead th{border-top:0;font-weight:600;color:var(--ink)}
.srct td.r,.srct th.r{text-align:right;padding-right:0}

@media (max-width:900px){
  .bar{padding:12px 16px;gap:6px 20px}
  .run{gap:2px 16px;font-size:13px;order:3;width:100%}
  .layout{grid-template-columns:minmax(0,1fr)}
  .side{border-right:0}
  .layout.detail .side{display:none}
  .layout:not(.detail) .main{display:none}
  .layout:not(.detail) .list{padding-bottom:60px}
  .layout:not(.detail) .tray{position:fixed;left:0;right:0;bottom:0;z-index:20}
  .back{display:inline-flex}
  .main{padding:16px 16px 72px}
  .foot{padding:24px 16px 40px}
}
@media (max-width:640px){
  .head{grid-template-columns:minmax(0,1fr)}
  .head .btn{grid-column:1;grid-row:auto;justify-self:start;margin-top:8px}
  .head h1{font-size:23px}
  .stats{grid-template-columns:1fr 1fr;gap:14px 16px}
  .stat-v{font-size:18px}
  .toc{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;gap:16px;font-size:13.5px;margin-left:-16px;margin-right:-16px;padding-left:16px;padding-right:16px}
  .toc::-webkit-scrollbar{display:none}
  .toc a{white-space:nowrap}
  .fr,.it{grid-template-columns:minmax(0,1fr);gap:1px}
  .fr dt,.it .k{font-size:13px}
  .fr dd.sc,.it .sc{text-align:left}
  button.src{text-align:left;white-space:normal}
  .lede{font-size:16px}
  .fin{width:100%;min-width:0}
  .fin th,.fin td,.fin thead th:not(:first-child),.fin th[scope=row]{min-width:0}
  .fin thead tr,.fin tbody tr,.fin tfoot tr{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));column-gap:12px}
  .fin th,.fin td{display:block;padding:2px 0 8px;border-top:0;text-align:right}
  .fin thead th:first-child,.fin tfoot th{display:none}
  .fin thead tr{border-bottom:1px solid var(--line)}
  .fin thead th{border:0;padding-bottom:6px}
  .fin tbody tr{border-top:1px solid var(--line2);padding-top:7px}
  .fin th[scope=row]{grid-column:1/-1;padding:0 0 1px;font-size:13px;text-align:left}
  .fin tr.brk th,.fin tr.brk td{padding-top:0}
  .fin tr.brk{margin-top:12px}
  .fin tfoot tr{border-top:1px solid var(--line);padding-top:6px}
  .fin.many td::before{content:attr(data-y);display:block;font-size:11.5px;color:var(--muted)}
  .cmp,.cmp thead,.cmp tbody{display:block}
  .cmp tr{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));column-gap:12px;border-top:1px solid var(--line2);padding:7px 0}
  .cmp thead{position:sticky;top:0;z-index:2;background:var(--bg)}
  .cmp thead tr{border-top:0;border-bottom:1px solid var(--line)}
  .cmp thead th:first-child{display:none}
  .cmp th,.cmp td{display:block;border:0;padding:0 0 2px;font-size:14px}
  .cmp thead th a{font-size:14px}
  .cmp thead .sub2{font-size:12px}
  .cmp th[scope=row]{grid-column:1/-1;width:auto;font-size:12.5px}
  .cmp tr.g{border-top:0;padding-top:18px}
  .cmp tr.g th{padding-top:0;grid-column:1/-1}
  .run div:nth-child(n+2){font-size:12.5px}
  .bar-link{font-size:13px}
}
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  *,*::before,*::after{transition:none!important;animation:none!important}
}
@media print{.side,.back,.skip,.toc,.ask,.pop,.scrim{display:none!important}.layout{display:block}.main{padding:0}}
"""

JS = r"""
(function(){
'use strict';
function $(id){return document.getElementById(id);}
var META=JSON.parse($('meta').textContent);
var DATA=null, U=[], CO=[], BY={};
function loadData(){
  var el=$('data');
  if(el.getAttribute('data-encoding')!=='gzip+base64')return Promise.resolve(JSON.parse(el.textContent));
  var bin=atob(el.textContent.trim()),u8=new Uint8Array(bin.length);for(var i=0;i<bin.length;i++)u8[i]=bin.charCodeAt(i);
  return new Response(new Blob([u8]).stream().pipeThrough(new DecompressionStream('gzip'))).text().then(JSON.parse);
}
var SECTIONS=['identity','financials','leadership','locations','web_presence','hiring','activity'];
var SEC_LABEL={identity:'Identity',financials:'Financials',leadership:'Leadership',locations:'Workplaces',web_presence:'Web presence',hiring:'Hiring',activity:'Activity'};
var STATES=['available','not_available','ambiguous','blocked','failed','not_applicable'];
var STATE_LABEL={available:'Available',not_available:'Not available',ambiguous:'Ambiguous',blocked:'Blocked',failed:'Failed',not_applicable:'Not applicable'};
var FIELD={legal_name:'Legal name',former_names:'Former names',legal_form:'Legal form',registration_date:'Registered in Enhetsregisteret',
 business_register_date:'Registered in Foretaksregisteret',articles_date:'Articles of association',incorporation_date:'Incorporated',
 business_address:'Business address',postal_address:'Postal address',municipality:'Municipality',industry:'Industry',secondary_industry:'Secondary industry',
 institutional_sector:'Institutional sector',statutory_purpose:'Statutory purpose',registered_activity:'Registered activity',share_capital:'Share capital',
 employees_registered:'Employees (registry)',employees_reported:'Employees (Wikidata)',legal_status:'Legal status',register_memberships:'Registers',signature_rule:'Signature rule',
 part_of_group:'Part of a group',group_parent:'Parent company',group_subsidiary:'Subsidiary',group_ultimate_parent:'Ultimate parent',
 parent_organisation_number:'Parent org number',deleted_date:'Deleted',registry_email:'Email',registry_phone:'Phone',registry_mobile:'Mobile',registry_website:'Website (as registered)',
 lei:'LEI',lei_direct_parent:'LEI direct parent',lei_ultimate_parent:'LEI ultimate parent',financial_licence:'Financial licence',ticker:'Ticker',isin:'ISIN',
 wikidata_item:'Wikidata',headquarters:'Headquarters (Wikidata)',
 revenue:'Revenue',operating_result:'Operating result',salary_costs:'Salary costs',profit_before_tax:'Profit before tax',annual_result:'Annual result',
 total_assets:'Total assets',cash_and_bank:'Cash and bank',equity:'Equity',debt:'Debt',accounts_audited:'Audited',annual_report_pdf:'Annual report copies',
 latest_filed_accounts_year:'Latest filed accounts',audit_opt_out_date:'Opted out of audit',
 ceo:'General manager',board_chair:'Board chair',board_deputy_chair:'Deputy chair',board_member:'Board member',board_deputy_member:'Deputy board member',
 board_observer:'Observer',auditor:'Auditor',accountant:'Accountant',contact_person:'Contact person',owner:'Owner',partner:'Partner',business_manager:'Business manager',other_role:'Other role',
 registered_workplace:'Registered workplace',official_website:'Verified website',related_website:'Related website',website_description:'Description',website_title:'Page title',
 site_language:'Site language',structured_data_organization:'Structured data',social_profile:'Social profile',
 site_email:'Email on website',site_phone:'Phone on website',site_contact_email:'Email on website',site_contact_phone:'Phone on website',contact_email:'Email on website',contact_phone:'Phone on website',
 job_ad:'Job ads',careers_page:'Careers page',registry_change:'Registry update',site_page_updated:'Website page updated',news:'News',news_item:'News'};
var PLAT={linkedin:'LinkedIn',facebook:'Facebook',instagram:'Instagram',youtube:'YouTube',x:'X',twitter:'X',tiktok:'TikTok',github:'GitHub',pinterest:'Pinterest',vimeo:'Vimeo'};
var FIN=['revenue','operating_result','salary_costs','profit_before_tax','annual_result','total_assets','cash_and_bank','equity','debt'];
var BREAK={total_assets:1};
var ID_GROUPS=[
 ['Registration',['legal_name','former_names','legal_form','legal_status','register_memberships','incorporation_date','registration_date','business_register_date','articles_date','deleted_date']],
 ['Business',['industry','secondary_industry','institutional_sector','statutory_purpose','registered_activity','share_capital','employees_registered','employees_reported']],
 ['Address and contact',['business_address','postal_address','municipality','headquarters','registry_email','registry_phone','registry_mobile','registry_website']],
 ['Group and other registers',['part_of_group','group_parent','group_ultimate_parent','group_subsidiary','parent_organisation_number','lei','lei_direct_parent','lei_ultimate_parent','financial_licence','wikidata_item','ticker','isin']]];
var ROLE_ORDER=['ceo','board_chair','board_deputy_chair','board_member','board_deputy_member','board_observer','owner','partner','business_manager','contact_person','other_role','auditor','accountant','signature_rule'];
var WEB_ORDER=['official_website','related_website','website_title','website_description','site_language','site_contact_email','site_email','contact_email','site_contact_phone','site_phone','contact_phone','social_profile','structured_data_organization'];
var SRC_CLASS={official_registry:'Official registry',company_owned:'Company website',open_register:'Open register',previous_run:'Previous run',open_data:'Open data'};
var METHOD={registry_endpoint:'Read from the registry record for this organisation number.',orgnr_on_page:'The page prints this organisation number.',
 linked_from_verified_site:'Linked from the verified company website.',verified_site:'Read from the verified company website.',verified_site_sitemap:'Read from the sitemap of the verified company website.',
 registered_as_equals_orgnr:'The GLEIF record is registered under this organisation number.',wikidata_p2333_equals_orgnr:'The Wikidata item carries this organisation number (P2333).',
 nav_orgnr:'The job ad is filed under this organisation number.'};
var MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
var SVG='<svg class="ic" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">';
var IC={x:SVG+'<path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>',back:SVG+'<path d="m15 18-6-6 6-6"/></svg>',
 ext:SVG+'<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>',
 plus:SVG+'<path d="M5 12h14"/><path d="M12 5v14"/></svg>',check:SVG+'<path d="M20 6 9 17l-5-5"/></svg>'};
var PAGE=200, shown=PAGE, filtered=[], compare=[], current=null;

/* ----- formatting ----- */
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];});}
function safeUrl(u){return /^https?:\/\//i.test(u||'')?u:null;}
function label(f){return FIELD[f]||cap(String(f||'').replace(/_/g,' '));}
function cap(s){s=String(s||'');return s.charAt(0).toUpperCase()+s.slice(1);}
function dfmt(s){if(!s)return '';var m=/^(\d{4})-(\d{2})-(\d{2})/.exec(s);return m?(+m[3])+' '+MON[+m[2]-1]+' '+m[1]:esc(s);}
function tfmt(s){var m=/T(\d{2}:\d{2})/.exec(s||'');return m?m[1]+' UTC':'';}
function nfmt(n){var x=Number(n);if(!isFinite(x))return esc(n);var s=Math.abs(x).toLocaleString('en-GB',{maximumFractionDigits:2}).replace(/,/g,'\u202f');return x<0?'\u2212'+s:s;}
function orgf(o){return String(o||'').replace(/^(\d{3})(\d{3})(\d{3})$/,'$1\u00a0$2\u00a0$3');}
function host(u){try{return new URL(u).hostname.replace(/^www\./,'');}catch(e){return u;}}
function tc(s){return String(s||'').toLowerCase().replace(/(^|[\s\-\/(])(\p{L})/gu,function(m,a,b){return a+b.toUpperCase();});}
function phone(p){var s=String(p||'').replace(/\s/g,''),cc='';if(/^\+47\d{8}$/.test(s)){cc='+47 ';s=s.slice(3);}
  if(!/^\d{8}$/.test(s))return esc(p);return cc+(/^[49]/.test(s)?s.replace(/(\d{3})(\d{2})(\d{3})/,'$1 $2 $3'):s.replace(/(\d{2})(\d{2})(\d{2})(\d{2})/,'$1 $2 $3 $4'));}
function plural(n,w,ws){return n+' '+(n===1?w:(ws||w+'s'));}
function bare(u){return String(u||'').replace(/^https?:\/\/(www\.)?/,'').replace(/\/$/,'');}
function link(u,text){var s=safeUrl(u);return s?'<a href="'+esc(s)+'" target="_blank" rel="noopener noreferrer">'+esc(text||bare(u))+'</a>':esc(text||u);}
function money(v,withCur){if(!v||v.amount==null)return '';return '<span class="num">'+(withCur&&v.currency?esc(v.currency)+'\u00a0':'')+nfmt(v.amount)+'</span>';}
var LANG={nb:'Norwegian Bokm\u00e5l',nn:'Norwegian Nynorsk',no:'Norwegian',en:'English',sv:'Swedish',da:'Danish',de:'German',fi:'Finnish'};
var CHG={updated:'Updated',ukjent:'Update',endring:'Updated',ny:'New entry',new:'New entry',sletting:'Deleted',deleted:'Deleted'};
var REGS={registrertIForetaksregisteret:'Foretaksregisteret',registrertIMvaregisteret:'Merverdiavgiftsregisteret (VAT)',registrertIStiftelsesregisteret:'Stiftelsesregisteret',
 registrertIFrivillighetsregisteret:'Frivillighetsregisteret',registrertIPartiregisteret:'Partiregisteret'};

/* ----- state marks ----- */
var SHAPE={available:'<rect x=".5" y=".5" width="8" height="8" fill="currentColor"/>',
 not_available:'<rect x="1" y="1" width="7" height="7" fill="none" stroke="currentColor"/>',
 ambiguous:'<rect x="1" y="1" width="7" height="7" fill="none" stroke="currentColor"/><rect x="1" y="1" width="3.5" height="7" fill="currentColor"/>',
 blocked:'<rect x="1" y="1" width="7" height="7" fill="none" stroke="currentColor"/><path d="M1 8 8 1" stroke="currentColor"/>',
 failed:'<path d="M1.2 1.2l6.6 6.6M7.8 1.2 1.2 7.8" stroke="currentColor" stroke-width="1.6"/>',
 not_applicable:'<path d="M1 4.5h7" stroke="currentColor" stroke-width="1.5"/>'};
function st(s){return STATES.indexOf(s)>=0?s:'failed';}
function mark(s){s=st(s);return '<svg class="mk st-'+s+'" viewBox="0 0 9 9" aria-hidden="true">'+SHAPE[s]+'</svg>';}
function stateLine(s,word){s=st(s);return '<span class="badge st-'+s+'">'+mark(s)+'<span class="w">'+esc(word||STATE_LABEL[s])+'</span></span>';}
function coverage(c){
  var g=SECTIONS.map(function(s,i){var x=st(c.sec[s]);return '<g class="st-'+x+'" transform="translate('+(i*10)+' 0)"><title>'+SEC_LABEL[s]+': '+STATE_LABEL[x]+'</title>'+SHAPE[x]+'</g>';}).join('');
  return '<svg class="cov" width="56" height="7" viewBox="0 0 69 9" role="img" aria-label="Coverage: '+SECTIONS.map(function(s){return SEC_LABEL[s]+' '+STATE_LABEL[st(c.sec[s])].toLowerCase();}).join(', ')+'">'+g+'</svg>';
}

/* ----- claim access ----- */
function ix(co){if(!co._ix){co._ix={};co.c.forEach(function(c){co._ix[c.i]=c;});}return co._ix;}
function av(co,f){return co.c.filter(function(c){return c.f===f&&c.a==='available';});}
function any(co,f){return co.c.filter(function(c){return c.f===f;});}
function one(co,f){return av(co,f)[0]||null;}
function fid(co,cl){return 'f-'+co.o+'-'+cl.i;}
function muni(c){var m=one(c,'municipality');if(!m)return '';var v=m.v;return tc(typeof v==='object'?v.name:v);}
function accounts(co,kind){return co.c.filter(function(c){return FIN.indexOf(c.f)>=0&&c.a==='available'&&c.v&&typeof c.v==='object'&&(c.v.account_type||'company')===(kind||'company');});}
function latestFig(co,f){var xs=accounts(co).filter(function(c){return c.f===f;});xs.sort(function(a,b){return String(b.v.year).localeCompare(String(a.v.year));});return xs[0]||null;}

/* ----- value rendering ----- */
function render(f,v){
  if(v==null)return '';
  if(typeof v==='string'){
    if(safeUrl(v))return link(v);
    if(/date/.test(f)||/^\d{4}-\d{2}-\d{2}$/.test(v))return dfmt(v);
    if(/phone|mobile/.test(f))return '<span class="num">'+phone(v)+'</span>';
    if(/email/.test(f))return '<a href="mailto:'+esc(v)+'">'+esc(v)+'</a>';
    if(f==='registry_website')return link('https://'+v.replace(/^https?:\/\//,''),v);
    if(f==='site_language'){var l=LANG[v.slice(0,2).toLowerCase()];return l?esc(l)+' <span class="sub2">'+esc(v)+'</span>':esc(v);}
    if(f==='employees_registered'){var pm=/^(.*?)\s*\((.+)\)$/.exec(v);return pm?esc(cap(pm[1]))+' <span class="sub2">'+esc(pm[2])+'</span>':esc(cap(v));}
    return esc(v);}
  if(typeof v==='number')return '<span class="num">'+nfmt(v)+'</span>';
  if(typeof v==='boolean')return v?'Yes':'No';
  if(Array.isArray(v)){
    if(f==='former_names')return v.map(function(x){return '<span class="t">'+esc(x.navn||x.name||'')+'</span>'+(x.fraDato||x.tilDato?' <span class="sub2">'+dfmt(x.fraDato)+' to '+dfmt(x.tilDato)+'</span>':'');}).join('<br>');
    return v.map(function(x){return render(f,x);}).join(', ');}
  if(f==='municipality')return esc(tc(v.name))+(v.number?' <span class="sub2 num">'+esc(v.number)+'</span>':'');
  if(v.amount!=null&&v.currency!=null){var extra=[];if(v.shares!=null)extra.push(nfmt(v.shares)+' shares');if(v.year)extra.push(esc(v.year));
    return money(v,true)+(extra.length?' <span class="sub2">'+extra.join(', ')+'</span>':'');}
  if(f==='legal_status'){var on=[];if(v.konkurs)on.push('Bankrupt');if(v.underAvvikling)on.push('In liquidation');if(v.underTvangsavviklingEllerTvangsopplosning)on.push('In forced dissolution');
    return on.length?'<span class="t">'+on.join(', ')+'</span>':'Active, with no bankruptcy, liquidation or forced dissolution registered';}
  if(f==='register_memberships'){var yes=[],no=[];Object.keys(v).forEach(function(k){(v[k]?yes:no).push(REGS[k]||k);});
    var s=yes.length?esc(yes.join(', ')):'None';if(v.registrertIMvaregisteret===false)s+=' <span class="sub2">Not VAT registered</span>';return s;}
  if(f==='social_profile'||(v.platform&&v.url))return esc(PLAT[String(v.platform||'').toLowerCase()]||cap(v.platform||'Profile'))+' '+link(v.url,bare(v.url).replace(/^[^/]+\//,''));
  if(f==='job_ad')return '<span class="t">'+(v.url?link(v.url,v.title||'Job ad'):esc(v.title||'Job ad'))+'</span><span class="note">'+
     [v.location,v.published?'published '+dfmt(v.published):'',v.expires?'closes '+dfmt(v.expires):''].filter(Boolean).map(esc).join(', ')+'</span>';
  if(f==='registered_workplace')return '<span class="t">'+esc(v.name||'')+'</span>'+
     '<span class="note">'+[esc(v.address||''),v.municipality&&(v.address||'').toUpperCase().indexOf(v.municipality)<0?esc(tc(v.municipality)):'',v.industry?esc(v.industry):'',
       v.employees!=null?plural(v.employees,'employee'):'',v.start_date?'since '+dfmt(v.start_date):''].filter(Boolean).join('<br>')+'</span>';
  if(v.name&&(v.role||v.role_code))return '<span class="t">'+esc(v.name)+'</span>'+(v.organisation_number?' <span class="sub2 num">'+orgf(v.organisation_number)+'</span>':'');
  if(f==='registry_change')return esc(CHG[String(v.type||'').toLowerCase()]||cap(v.type||'Update'))+(v.changed&&v.changed.length?': '+esc(v.changed.join(', ')):'');
  if(f==='site_page_updated')return 'Page updated: '+link(v.url,bare(v.url).replace(/^[^/]+/,'')||'/');
  if(f==='related_website')return link(v.url)+(v.other_organisation_number?' <span class="sub2">prints org number <span class="num">'+orgf(v.other_organisation_number)+'</span></span>':'');
  if(f==='structured_data_organization')return esc(v.name||'')+(v.type?' <span class="sub2">schema.org '+esc(v.type)+'</span>':'');
  if(f==='wikidata_item')return link(v.url,v.label||v.qid)+' <span class="sub2">'+esc(v.qid||'')+'</span>';
  if(f==='employees_reported')return '<span class="num">'+nfmt(v.count)+'</span>'+(v.point_in_time?' <span class="sub2">'+dfmt(v.point_in_time)+'</span>':'');
  if(v.lei&&(f==='lei_direct_parent'||f==='lei_ultimate_parent'))return '<span class="t">'+esc(v.name||'')+'</span> <span class="sub2 num">'+(v.organisation_number?orgf(v.organisation_number)+', ':'')+'LEI '+esc(v.lei)+'</span>';
  if(v.lei)return '<span class="num">'+esc(v.lei)+'</span><span class="note">'+[v.status?'Entity '+esc(String(v.status).toLowerCase()):'',v.registration_status?'registration '+esc(String(v.registration_status).toLowerCase()):'',v.next_renewal?'next renewal '+dfmt(v.next_renewal):''].filter(Boolean).join(', ')+'</span>';
  if(f==='group_parent'||f==='group_subsidiary'||f==='group_ultimate_parent')return '<span class="t">'+esc(v.name||'')+'</span> <span class="sub2 num">'+orgf(v.organisation_number)+'</span>'+(v.ownership?' <span class="sub2">'+esc(v.ownership)+' owned</span>':'');
  if(v.url&&(v.title||v.date))return link(v.url,v.title||v.url);
  if(v.code!=null&&v.name)return esc(v.name)+' <span class="sub2 num">'+esc(v.code)+'</span>';
  if(v.year&&v.url)return link(v.url,v.year);
  if(v.year!=null&&v.audited!=null)return v.audited?'Yes':'No';
  if(v.qid&&v.name)return esc(v.name);
  return Object.keys(v).filter(function(k){return v[k]!=null&&typeof v[k]!=='object';}).map(function(k){return esc(k.replace(/_/g,' '))+': '+esc(v[k]);}).join(', ');
}
function mdText(md){return String(md||'').replace(/\borgnr_on_page\b/g,'the organisation number printed on the page').replace(/\borgnr\b/g,'organisation number').replace(/_/g,' ');}
function valueOf(cl){
  if(cl.a!=='available')return stateLine(cl.a)+(cl.n?'<span class="note">'+esc(cl.n)+'</span>':'');
  return render(cl.f,cl.v)+(cl.n?'<span class="note">'+esc(cl.n)+'</span>':'');
}
function srcBtn(co,cl){
  if(!cl.e||!cl.e.length)return '';var ev=co.ev[cl.e[0]];if(!ev)return '';
  return '<button type="button" class="src" data-o="'+co.o+'" data-c="'+esc(cl.i)+'" aria-haspopup="dialog">'+esc(host(U[ev[0]]||''))+' \u00b7 '+dfmt(ev[1])+(cl.e.length>1?' +'+(cl.e.length-1):'')+'</button>';
}
function vbtn(co,cl,inner){return '<button type="button" class="vbtn" data-o="'+co.o+'" data-c="'+esc(cl.i)+'" aria-haspopup="dialog">'+inner+'</button>';}

/* ----- list ----- */
function initControls(){
  var sec=$('f-sec'),stt=$('f-state');
  sec.innerHTML='<option value="">Any section</option>'+SECTIONS.map(function(s){return '<option value="'+s+'">'+SEC_LABEL[s]+'</option>';}).join('');
  stt.innerHTML='<option value="">Any state</option>'+STATES.map(function(s){return '<option value="'+s+'">'+STATE_LABEL[s]+'</option>';}).join('');
  $('legend').innerHTML='<span>Marks, in section order:</span>'+STATES.map(function(s){return '<span>'+mark(s)+STATE_LABEL[s]+'</span>';}).join('');
  ['q','f-web','f-jobs','f-chg','f-sec','f-state','sort'].forEach(function(id){$(id).addEventListener(id==='q'?'input':'change',function(){shown=PAGE;applyFilters();});});
  $('ftoggle').addEventListener('click',function(){var open=this.getAttribute('aria-expanded')!=='true';this.setAttribute('aria-expanded',String(open));$('filters').hidden=!open;});
  $('more').addEventListener('click',function(){shown+=PAGE;drawList();});
  $('tray-clear').addEventListener('click',function(){compare=[];drawList();drawTray();refreshCmpBtn();});
}
function applyFilters(){
  var q=$('q').value.trim().toLowerCase(),qd=q.replace(/\s/g,'');
  var w=$('f-web').checked,j=$('f-jobs').checked,ch=$('f-chg').checked,sec=$('f-sec').value,sv=$('f-state').value;
  filtered=CO.filter(function(c){
    if(q){if(!c._q)c._q=(c.n+' '+muni(c)).toLowerCase();if(c._q.indexOf(q)<0&&(!/^\d+$/.test(qd)||c.o.indexOf(qd)<0))return false;}
    if(w&&!c.fl.web)return false;if(j&&!c.fl.jobs)return false;if(ch&&!c.fl.chg)return false;
    if(sv){if(sec){if(c.sec[sec]!==sv)return false;}else if(!SECTIONS.some(function(s){return c.sec[s]===sv;}))return false;}
    else if(sec&&c.sec[sec]!=='available')return false;
    return true;});
  var s=$('sort').value;
  filtered.sort(function(a,b){
    if(s==='org')return a.o<b.o?-1:1;
    if(s==='rev'){var x=a.rev==null?-Infinity:a.rev,y=b.rev==null?-Infinity:b.rev;if(x!==y)return y-x;}
    if(s==='facts'&&a.nf!==b.nf)return b.nf-a.nf;
    if(s==='chg'){var d=(b.ch.length+av(b,'registry_change').length)-(a.ch.length+av(a,'registry_change').length);if(d)return d;}
    return a.n.localeCompare(b.n,'nb');});
  drawList();
}
function drawList(){
  var html=filtered.slice(0,shown).map(function(c){
    var on=compare.indexOf(c.o)>=0,cur=current===c.o;
    return '<li class="row'+(cur?' cur':'')+'"><a class="row-link" href="#org-'+c.o+'"'+(cur?' aria-current="page"':'')+'><span class="nm">'+esc(c.n||c.o)+'</span>'+
      '<span class="sub"><span class="num">'+orgf(c.o)+'</span><span>'+esc(muni(c))+'</span></span></a>'+coverage(c)+
      '<label class="pick"><input type="checkbox" data-cmp="'+c.o+'"'+(on?' checked':'')+'><span class="vh">Compare '+esc(c.n)+'</span></label></li>';}).join('');
  $('list').innerHTML=html||'<li class="empty">No companies match. Clear the search or filters.</li>';
  $('count').textContent=filtered.length===CO.length?plural(CO.length,'company','companies'):filtered.length+' of '+CO.length;
  $('more').hidden=filtered.length<=shown;
}
function drawTray(){
  $('tray').hidden=compare.length===0;
  $('tray-text').textContent=compare.length+' of 3 selected'+(compare.length<2?', pick one more':'');
  $('tray-go').href='#compare='+compare.join(',');
  $('tray-go').setAttribute('aria-disabled',compare.length<2?'true':'false');
}
function toggleCompare(o,on){
  if(on){if(compare.indexOf(o)<0){if(compare.length>=3){$('tray-text').textContent='Compare holds up to 3 companies';return false;}compare.push(o);}}
  else compare=compare.filter(function(x){return x!==o;});
  drawTray();return true;
}
function refreshCmpBtn(){var b=document.querySelector('[data-addcmp]');if(!b)return;var on=compare.indexOf(b.getAttribute('data-addcmp'))>=0;
  b.setAttribute('aria-pressed',String(on));b.innerHTML=(on?IC.check+'In compare':IC.plus+'Compare');}

/* ----- profile parts ----- */
function stats(co){
  function cell(lab,inner,sub,none){return '<div class="stat"><dt>'+lab+'</dt><dd>'+(none?'<span class="stat-v none">'+inner+'</span>':'<span class="stat-v">'+inner+'</span>')+(sub?'<span class="stat-s">'+sub+'</span>':'')+'</dd></div>';}
  var out=[];
  [['revenue','Revenue'],['annual_result','Annual result']].forEach(function(p){var c=latestFig(co,p[0]);
    out.push(c?cell(p[1],vbtn(co,c,nfmt(c.v.amount)),esc(c.v.currency||'')+' '+esc(c.v.year)):cell(p[1],'Not available','',true));});
  var e=any(co,'employees_registered')[0];
  if(e&&e.a==='available'){var ev=typeof e.v==='string'&&/fewer than 5/i.test(e.v)?'Under 5':(typeof e.v==='number'?nfmt(e.v):esc(String(e.v)));out.push(cell('Employees',vbtn(co,e,ev),'Registry'));}
  else out.push(cell('Employees',e&&e.a==='not_available'&&/no employees/i.test(e.n||'')?vbtn(co,e,'None registered'):'Not available',e&&e.a==='not_available'&&/no employees/i.test(e.n||'')?'Registry':'',!(e&&/no employees/i.test(e.n||''))));
  var w=av(co,'registered_workplace'),wn=any(co,'registered_workplace')[0];
  out.push(w.length?cell('Workplaces',vbtn(co,w[0],String(w.length)),'Registered'):(wn&&wn.a==='not_available'?cell('Workplaces',vbtn(co,wn,'None'),'Registered'):cell('Workplaces',STATE_LABEL[st(wn?wn.a:'failed')],'',true)));
  var j=av(co,'job_ad'),jn=any(co,'job_ad')[0];
  out.push(j.length?cell('Job ads',vbtn(co,j[0],String(j.length)),'NAV'):(jn&&jn.a==='not_available'?cell('Job ads',vbtn(co,jn,'None'),'NAV'):cell('Job ads',jn&&jn.a==='failed'?'Not checked':'Not available',jn&&jn.a==='failed'?'NAV feed failed':'',true)));
  return '<dl class="stats">'+out.join('')+'</dl>';
}
function cites(co,ids,refs){
  var I=ix(co),b=(ids||[]).filter(function(id){return I[id];}).map(function(id){if(!refs.m[id])refs.m[id]=++refs.n;
    return '<button type="button" class="cite" data-o="'+co.o+'" data-c="'+esc(id)+'" aria-haspopup="dialog" aria-label="Source '+refs.m[id]+': '+esc(label(I[id].f))+'">'+refs.m[id]+'</button>';});
  return b.length?'<sup class="cites">'+b.join('<span aria-hidden="true">,</span>')+'</sup>':'';
}
function summary(co){
  var refs={n:0,m:{}};
  var parts=co.sum.t.map(function(s){return esc(s[0])+cites(co,s[1],refs);}),cut=parts.length>4?Math.ceil(parts.length/2):parts.length;
  var body=parts.length?'<p>'+parts.slice(0,cut).join(' ')+'</p>'+(cut<parts.length?'<p>'+parts.slice(cut).join(' ')+'</p>':''):'<p class="muted">No summary was written for this company.</p>';
  var gaps=co.sum.u.map(function(u){var m=/^([^:(]+?)\s*:\s*(not available|ambiguous|blocked|failed|not applicable)/i.exec(u);
    if(!m)return esc(u);var sec=gapSection(m[1]);return (sec?'<a href="#s-'+sec+'" data-jump="s-'+sec+'">':'')+esc(m[1])+(sec?'</a>':'')+' <span>('+esc(m[2].toLowerCase())+')</span>';});
  return '<section class="sec" id="s-summary" aria-labelledby="h-summary"><div class="sh"><h2 id="h-summary">Summary</h2></div><div class="lede">'+body+'</div>'+
    (gaps.length?'<p class="gaps">Not found: '+gaps.join(', ')+'.</p>':'')+'</section>';
}
function gapSection(t){t=t.toLowerCase();if(/website|social/.test(t))return 'web_presence';if(/job|hiring|career/.test(t))return 'hiring';if(/workplace|sub-unit/.test(t))return 'locations';
  if(/account|financ|revenue/.test(t))return 'financials';if(/general manager|board|role/.test(t))return 'leadership';if(/change|update/.test(t))return 'activity';if(/lei|employee/.test(t))return 'identity';return '';}
function sectionHead(co,id,title){var s=co.sec[id],has=co.c.some(function(c){return c.s===id;});return '<div class="sh"><h2 id="h-'+id+'">'+title+'</h2>'+(s&&s!=='available'&&!has?stateLine(s):'')+'</div>';}
function factRow(co,cl,lab){return '<div class="fr" id="'+fid(co,cl)+'"><dt>'+esc(lab||label(cl.f))+'</dt><dd>'+valueOf(cl)+'</dd><dd class="sc">'+srcBtn(co,cl)+'</dd></div>';}
function identity(co){
  var used={},I=co.c.filter(function(c){return c.s==='identity';});
  var purpose=one(co,'statutory_purpose'),act=one(co,'registered_activity');
  if(purpose&&act&&String(purpose.v).trim()===String(act.v).trim())used[act.i]=1;
  var out=ID_GROUPS.map(function(g){var rows=[];g[1].forEach(function(f){I.forEach(function(c){if(c.f===f&&!used[c.i]){used[c.i]=1;
      rows.push(factRow(co,c,f==='statutory_purpose'&&act&&used[act.i]&&act!==c?'Statutory purpose and activity':null));}});});
    return rows.length?'<h3 class="grp">'+g[0]+'</h3><dl class="facts">'+rows.join('')+'</dl>':'';}).join('');
  var rest=I.filter(function(c){return !used[c.i];});
  if(rest.length)out+='<h3 class="grp">Other</h3><dl class="facts">'+rest.map(function(c){return factRow(co,c);}).join('')+'</dl>';
  return '<section class="sec" id="s-identity" aria-labelledby="h-identity">'+sectionHead(co,'identity','Identity')+out+'</section>';
}
function finTable(co,kind){
  var rows=accounts(co,kind);if(!rows.length)return '';
  var audited=co.c.filter(function(c){return c.f==='accounts_audited'&&c.a==='available'&&c.v&&(c.v.account_type||'company')===kind;});
  var years=[],per={};rows.forEach(function(c){var y=String(c.v.year||'');if(years.indexOf(y)<0){years.push(y);per[y]=c.v;}});
  years.sort().reverse();
  var cur={};rows.forEach(function(c){if(c.v.currency)cur[c.v.currency]=1;});
  var fields=FIN.filter(function(f){return rows.some(function(c){return c.f===f;});});
  var calendar=years.every(function(y){var p=per[y];return !p.period_start||(/-01-01$/.test(p.period_start)&&/-12-31$/.test(p.period_end||''));});
  function cellFor(c,y){return '<td data-y="'+esc(y)+'"'+(c?' id="'+fid(co,c)+'"':'')+'>'+(c?vbtn(co,c,nfmt(c.v.amount)):'<span class="nr">Not reported</span>')+'</td>';}
  var n=years.length;
  var h='<div class="tablewrap"><div class="tcap"><h3>'+(kind==='group'?'Group accounts':'Company accounts')+'</h3><p>'+esc(Object.keys(cur).join(', ')||'Currency not stated')+
    ', as filed with Regnskapsregisteret'+(calendar?', calendar years':'')+'</p></div>'+
    '<table class="fin'+(n>3?' many':'')+'" style="--n:'+Math.min(n,3)+'"><thead><tr><th scope="col"><span class="vh">Figure</span></th>'+
    years.map(function(y){var p=per[y];return '<th scope="col">'+esc(y)+(calendar?'':'<span class="per">'+(p.period_start?dfmt(p.period_start)+' to ':'')+dfmt(p.period_end)+'</span>')+'</th>';}).join('')+'</tr></thead><tbody>'+
    fields.map(function(f){return '<tr'+(BREAK[f]?' class="brk"':'')+'><th scope="row">'+esc(label(f))+'</th>'+years.map(function(y){
      return cellFor(rows.filter(function(x){return x.f===f&&String(x.v.year)===y;})[0],y);}).join('')+'</tr>';}).join('')+
    (audited.length?'<tr><th scope="row">Audited</th>'+years.map(function(y){var c=audited.filter(function(x){return String(x.v.year)===y;})[0];
      return '<td data-y="'+esc(y)+'"'+(c?' id="'+fid(co,c)+'"':'')+'>'+(c?vbtn(co,c,c.v.audited?'Yes':'No'):'<span class="nr">Not reported</span>')+'</td>';}).join('')+'</tr>':'')+
    '</tbody><tfoot><tr><th scope="row">Source</th>'+years.map(function(y){var c=rows.filter(function(x){return String(x.v.year)===y;})[0];return '<td>'+(c?srcBtn(co,c):'')+'</td>';}).join('')+'</tr></tfoot></table></div>';
  return h;
}
function financials(co){
  var body=finTable(co,'company')+finTable(co,'group');
  var shownF={accounts_audited:1};FIN.forEach(function(f){shownF[f]=1;});
  var rest=co.c.filter(function(c){return c.s==='financials'&&!(shownF[c.f]&&c.a==='available')&&c.f!=='annual_report_pdf';});
  var pdfs=av(co,'annual_report_pdf').sort(function(a,b){return String(b.v.year).localeCompare(String(a.v.year));});
  var rows=rest.map(function(c){return factRow(co,c);});
  if(pdfs.length)rows.push('<div class="fr" id="'+fid(co,pdfs[0])+'"><dt>'+label('annual_report_pdf')+'</dt><dd class="yl">'+pdfs.map(function(c){return link(c.v.url,String(c.v.year));}).join('')+'</dd><dd class="sc">'+srcBtn(co,pdfs[0])+'</dd></div>');
  if(rows.length)body+='<dl class="facts'+(body?' after':'')+'">'+rows.join('')+'</dl>';
  if(!body)body='<p class="muted">Nothing recorded.</p>';
  return '<section class="sec" id="s-financials" aria-labelledby="h-financials">'+sectionHead(co,'financials','Financials')+body+'</section>';
}
function itemRow(co,cl,k,kNum){return '<li class="it" id="'+fid(co,cl)+'"><span class="k'+(kNum?' num':'')+'">'+k+'</span><span class="v">'+valueOf(cl)+'</span><span class="sc">'+srcBtn(co,cl)+'</span></li>';}
function leadership(co){
  var L=co.c.filter(function(c){return c.s==='leadership';});
  L.sort(function(a,b){var x=ROLE_ORDER.indexOf(a.f),y=ROLE_ORDER.indexOf(b.f);return (x<0?99:x)-(y<0?99:y);});
  var body=L.map(function(c){
    if(c.f==='signature_rule'||c.a!=='available')return itemRow(co,c,esc(label(c.f)));
    var cl={i:c.i,f:c.f,a:c.a,v:c.v,e:c.e,n:c.n};
    var html='<li class="it" id="'+fid(co,c)+'"><span class="k">'+esc(label(c.f))+'</span><span class="v">'+render(c.f,c.v)+(c.d?'<span class="note">Role updated '+dfmt(c.d)+'</span>':'')+'</span><span class="sc">'+srcBtn(co,cl)+'</span></li>';
    return html;}).join('');
  return '<section class="sec" id="s-leadership" aria-labelledby="h-leadership">'+sectionHead(co,'leadership','Leadership')+(body?'<ul class="items">'+body+'</ul>':'<p class="muted">Nothing recorded.</p>')+'</section>';
}
function locations(co){
  var L=co.c.filter(function(c){return c.s==='locations';});
  var body=L.map(function(c){return itemRow(co,c,c.a==='available'&&c.v&&c.v.organisation_number?orgf(c.v.organisation_number):esc(label(c.f)),c.a==='available');}).join('');
  return '<section class="sec" id="s-locations" aria-labelledby="h-locations">'+sectionHead(co,'locations','Workplaces')+(body?'<ul class="items">'+body+'</ul>':'<p class="muted">Nothing recorded.</p>')+'</section>';
}
function web(co){
  var L=co.c.filter(function(c){return c.s==='web_presence';});
  L.sort(function(a,b){var x=WEB_ORDER.indexOf(a.f),y=WEB_ORDER.indexOf(b.f);return (x<0?99:x)-(y<0?99:y);});
  var body=L.map(function(c){return factRow(co,c,c.f==='social_profile'&&c.v?PLAT[String(c.v.platform||'').toLowerCase()]||'Social profile':null);}).join('')
    .replace(/<dd>(Facebook|LinkedIn|Instagram|YouTube|X|TikTok|GitHub|Pinterest|Vimeo) /g,'<dd>');
  return '<section class="sec" id="s-web_presence" aria-labelledby="h-web_presence">'+sectionHead(co,'web_presence','Web presence')+(body?'<dl class="facts">'+body+'</dl>':'<p class="muted">Nothing recorded.</p>')+'</section>';
}
function hiring(co){
  var L=co.c.filter(function(c){return c.s==='hiring';});
  var body=L.map(function(c){return factRow(co,c);}).join('');
  return '<section class="sec" id="s-hiring" aria-labelledby="h-hiring">'+sectionHead(co,'hiring','Hiring')+(body?'<dl class="facts">'+body+'</dl>':'<p class="muted">Nothing recorded.</p>')+'</section>';
}
function activity(co){
  var L=co.c.filter(function(c){return c.s==='activity';}).slice();
  L.sort(function(a,b){return (b.d||'').localeCompare(a.d||'');});
  var body=L.map(function(c){return itemRow(co,c,c.d?dfmt(c.d):'Undated',true);}).join('');
  var n=av(co,'registry_change').length;
  return '<section class="sec" id="s-activity" aria-labelledby="h-activity">'+sectionHead(co,'activity','Activity')+
    (body?'<ul class="items">'+body+'</ul>':'<p class="muted">No dated registry updates or website news found.</p>')+'</section>';
}
function changeVal(f,v){if(v==null)return '<span class="sub2">none</span>';return render(f,v)||esc(JSON.stringify(v));}
function changes(co){
  var body;
  if(co.ch.length){body='<ul class="items chg">'+co.ch.map(function(ch){
      var k=cap(String(ch.type||'change').replace(/_/g,' '));
      var t='<span class="t">'+esc(label(ch.field))+'</span><span class="note">'+(ch.previous_value!==undefined?changeVal(ch.field,ch.previous_value)+' <span class="arrow">to</span> ':'')+
        (ch.current_value!==undefined?changeVal(ch.field,ch.current_value):'<span class="sub2">removed</span>')+'</span>';
      var cl=ch.current_claim_id?ix(co)[ch.current_claim_id]:null;
      return '<li class="it"><span class="k">'+esc(k)+'</span><span class="v">'+t+'</span><span class="sc">'+(cl?srcBtn(co,cl):'')+'</span></li>';}).join('')+'</ul>';}
  else if(META.previous===0)body='<p class="muted">This run had no earlier profile to compare against, so nothing can have changed yet. Registry updates from the last 12 months are under Activity.</p>';
  else body='<p class="muted">No differences from the previous run.</p>';
  return '<section class="sec" id="s-changes" aria-labelledby="h-changes"><div class="sh"><h2 id="h-changes">What changed</h2></div>'+body+'</section>';
}
var CHECKS=[['Enhetsregisteret entity record',['legal_name']],['Roles',['ceo','board_chair','board_member','owner','partner','auditor','accountant','business_manager','signature_rule']],
 ['Sub-units',['registered_workplace']],['Regnskapsregisteret accounts',FIN.concat(['annual_report_pdf','latest_filed_accounts_year'])],['Registry update feed',['registry_change']],
 ['Company website',['official_website']],['GLEIF LEI register',['lei']],['NAV job ads',['job_ad']],['Finanstilsynet licences',['financial_licence']],['Wikidata',['wikidata_item']]];
var OUTCOME={available:'Found',not_available:'Nothing found',ambiguous:'Ambiguous',blocked:'Blocked',failed:'Failed',not_applicable:'Not applicable'};
function matching(co){
  var w=any(co,'official_website')[0],p=[];
  p.push('<p>Registry facts come from Br\u00f8nn\u00f8ysundregistrene records requested by organisation number <span class="num">'+orgf(co.o)+'</span>, so they cannot belong to another company.</p>');
  if(w&&w.a==='available'){p.push('<p>Website '+link(w.v)+': '+esc(mdText(w.md)||METHOD[w.m]||'proven by the identity check')+'. '+
    (av(co,'social_profile').length?'Social profiles are only taken from links on that site.':'')+'</p>');}
  else if(w)p.push('<p>'+({ambiguous:'A candidate website was found, but nothing on it proves it belongs to this company, so it is not shown as the official site.',
    blocked:'Candidate websites blocked automated access, so none could be proven to belong to this company.',failed:'The website check did not finish in this run.'}[w.a]||'No website proving this organisation number was found.')+
    ' A site counts only if it prints the organisation number, or shows the registry phone or address together with the legal name.</p>');
  var rows=CHECKS.map(function(ch){var cl=co.c.filter(function(c){return ch[1].indexOf(c.f)>=0;});if(!cl.length)return '';
    var ok=cl.filter(function(c){return c.a==='available';}),s=ok.length?'available':cl[0].a;
    return '<tr><td>'+esc(ch[0])+'</td><td>'+stateLine(s,OUTCOME[st(s)])+'</td><td class="r num">'+(ok.length?plural(ok.length,'fact'):'')+'</td></tr>';}).join('');
  return '<section class="sec" id="s-matching" aria-labelledby="h-matching"><div class="sh"><h2 id="h-matching">How this company was matched</h2></div><div class="matchp">'+p.join('')+'</div>'+
    '<table class="srct srcs"><thead><tr><th scope="col">Source checked</th><th scope="col">Result</th><th scope="col" class="r">Facts</th></tr></thead><tbody>'+rows+'</tbody></table></section>';
}
function errs(co){
  var notes=co.c.map(function(c){return (c.n||'').toLowerCase();}).join(' | ');
  var left=co.err.filter(function(m){var tail=String(m).split(': ').slice(-1)[0].toLowerCase();return notes.indexOf(tail)<0;});
  return left.length?'<p class="errs">Run notes: '+left.map(esc).join('; ')+'</p>':'';
}

/* ----- ask ----- */
var SUGG=['What does it do?','Who runs it?','How did it do last year?','Is it hiring?','What changed recently?','Is the website verified?'];
var INTENTS=[
 ['what',/what does|what do|does it do|do they do|business|industry|purpose|sector|activity|hva gj\u00f8r|gj\u00f8r|driver med|bransje|form\u00e5l|virksomhet|n\u00e6ring/g],
 ['who',/who|runs?\b|ceo|manag|leader|leads?\b|board|chair|owner|director|daglig|leder|styre|eier|innehaver/g],
 ['money',/revenue|turnover|profit|loss|result|earn|money|financ|last year|perform|equity|accounts|omsetning|resultat|inntekt|overskudd|underskudd|regnskap|\u00f8konomi|egenkapital|tall/g],
 ['hiring',/hir(e|ing)|jobs?\b|vacanc|recruit|career|opening|ansett|ansatt nye|nye folk|ledig|stilling|jobb|karriere|rekrutt/g],
 ['changed',/chang|recent|news|update|happen|endr|nytt|nyhet|oppdater/g],
 ['where',/where|located|location|address|operate|office|workplace|branch|municipal|adresse|hvor|kommune|sted|avdeling|kontor/g],
 ['website',/website|web\s?site|\bweb\b|url|verif|domain|social|linkedin|facebook|instagram|nettside|hjemmeside|nettsted/g],
 ['contact',/contact|e-?mail|phone|call|reach|kontakt|telefon|e-post|epost|ring/g],
 ['size',/employ|staff|people|how big|size|workers|ansatte|st\u00f8rrelse/g],
 ['group',/parent|group|subsidiar|owned by|konsern|morselskap|datter/g]];
function intents(q){q=q.toLowerCase();var best=0,out=[];INTENTS.forEach(function(it){var m=q.match(it[1]);var s=m?m.length:0;if(s>best){best=s;out=[it[0]];}else if(s&&s===best)out.push(it[0]);});return out.slice(0,2);}
function S(text,cls){return [text,cls||[]];}
function answerFor(co,intent){
  var out=[],c,cs;
  if(intent==='what'){c=one(co,'industry');if(c)out.push(S('Registered industry: '+c.v.name+' ('+c.v.code+').',[c]));
    c=one(co,'statutory_purpose')||one(co,'registered_activity');if(c)out.push(S('Registered purpose: \u201c'+String(c.v).trim()+'\u201d',[c]));
    c=one(co,'website_description');if(c)out.push(S('Its own website says: \u201c'+c.v+'\u201d',[c]));}
  else if(intent==='who'){c=one(co,'ceo');if(c)out.push(S('General manager: '+c.v.name+'.',[c]));else{c=any(co,'ceo')[0];if(c&&c.n)out.push(S(c.n+'.',[c]));}
    c=one(co,'board_chair');if(c)out.push(S('Board chair: '+c.v.name+'.',[c]));
    cs=av(co,'owner').concat(av(co,'partner'));if(cs.length)out.push(S((cs.length>1?'Owners or partners: ':'Owner: ')+cs.map(function(x){return x.v.name;}).join(', ')+'.',cs));
    cs=av(co,'board_member');if(cs.length)out.push(S((cs.length>1?'Board members: ':'Board member: ')+cs.map(function(x){return x.v.name;}).join(', ')+'.',cs));}
  else if(intent==='money'){var r=latestFig(co,'revenue'),a=latestFig(co,'annual_result'),o=latestFig(co,'operating_result');var y=(r||a||o);
    if(y){var parts=[],cl=[];if(r){parts.push('revenue was '+r.v.currency+' '+nfmt(r.v.amount));cl.push(r);}
      if(o){parts.push('the operating result was '+o.v.currency+' '+nfmt(o.v.amount));cl.push(o);}
      if(a){parts.push('the annual result was '+a.v.currency+' '+nfmt(a.v.amount));cl.push(a);}
      out.push(S('In the '+y.v.year+' company accounts, '+parts.join(', ').replace(/, ([^,]*)$/,' and $1')+'.',cl));
      if(!r)out.push(S('No revenue figure was reported for '+y.v.year+'.',[]));}}
  else if(intent==='hiring'){cs=av(co,'job_ad');if(cs.length)out.push(S(plural(cs.length,'active job ad')+' on NAV: '+cs.slice(0,3).map(function(x){return x.v.title||'untitled';}).join(', ')+'.',cs.slice(0,3)));
    else{c=any(co,'job_ad')[0];if(c&&c.a==='failed')out.push(S('Job ads could not be checked in this run ('+(c.n||'source failed')+').',[c]));else if(c)out.push(S('No active job ads on NAV for this organisation number.',[c]));}
    c=one(co,'careers_page');if(c)out.push(S('The verified website has a careers page: '+bare(c.v)+'.',[c]));}
  else if(intent==='changed'){cs=av(co,'registry_change').sort(function(a,b){return (b.d||'').localeCompare(a.d||'');}).slice(0,3);
    cs.forEach(function(x){out.push(S('On '+dfmt(x.d)+' the registry recorded '+(x.v.changed&&x.v.changed.length?'a change to '+x.v.changed.join(', '):'an update')+'.',[x]));});
    co.ch.slice(0,3).forEach(function(ch){var cl=ch.current_claim_id?ix(co)[ch.current_claim_id]:null;out.push(S('Since the previous run: '+label(ch.field)+' ('+String(ch.type||'change').replace(/_/g,' ')+').',cl?[cl]:[]));});}
  else if(intent==='where'){c=one(co,'business_address');if(c)out.push(S('Business address: '+c.v+'.',[c]));
    c=one(co,'municipality');if(c)out.push(S('Municipality: '+tc(c.v.name)+'.',[c]));
    cs=av(co,'registered_workplace');if(cs.length){var ms={};cs.forEach(function(x){if(x.v.municipality)ms[tc(x.v.municipality)]=1;});
      out.push(S(plural(cs.length,'registered workplace')+(Object.keys(ms).length?' in '+Object.keys(ms).join(', '):'')+'.',cs.slice(0,3)));}}
  else if(intent==='website'){c=any(co,'official_website')[0];
    if(c&&c.a==='available')out.push(S('Yes. The verified website is '+bare(c.v)+'. '+(c.md?'Proof: '+mdText(c.md)+'.':(METHOD[c.m]||'')),[c]));
    else if(c)out.push(S('No website is verified for this company ('+STATE_LABEL[st(c.a)].toLowerCase()+'). '+(c.n?cap(c.n)+'.':''),[c]));
    cs=av(co,'social_profile');if(cs.length)out.push(S('Social profiles linked from the site: '+cs.map(function(x){return PLAT[String(x.v.platform).toLowerCase()]||x.v.platform;}).join(', ')+'.',cs));}
  else if(intent==='contact'){['site_contact_email','site_contact_phone','registry_email','registry_phone','registry_mobile'].forEach(function(f){c=one(co,f);
    if(c)out.push(S(label(f)+(/^registry/.test(f)?' (registry)':'')+': '+(/phone|mobile/.test(f)?phone(c.v):c.v)+'.',[c]));});}
  else if(intent==='size'){c=any(co,'employees_registered')[0];if(c&&c.a==='available')out.push(S('Employees registered: '+c.v+'.',[c]));else if(c&&c.n)out.push(S(c.n+'.',[c]));
    c=one(co,'employees_reported');if(c)out.push(S('Wikidata reports '+c.v.count+' employees'+(c.v.point_in_time?' as of '+dfmt(c.v.point_in_time):'')+'.',[c]));}
  else if(intent==='group'){c=one(co,'group_parent');if(c)out.push(S('Parent company: '+c.v.name+' ('+orgf(c.v.organisation_number)+')'+(c.v.ownership?', '+c.v.ownership+' owned':'')+'.',[c]));
    cs=av(co,'group_subsidiary');if(cs.length)out.push(S('Subsidiaries: '+cs.map(function(x){return x.v.name;}).join(', ')+'.',cs));
    c=one(co,'part_of_group');if(c&&!out.length)out.push(S(c.v?'It is registered as part of a group.':'It is not registered as part of a group.',[c]));}
  return out;
}
function ask(co,q){
  var el=$('answer');if(!el)return;q=String(q||'').trim();if(!q){el.innerHTML='';return;}
  var its=intents(q),refs={n:0,m:{}},sents=[];
  its.forEach(function(it){sents=sents.concat(answerFor(co,it));});
  var html='<p class="q">'+esc(q)+'</p>';
  if(!its.length)html+='<p class="nf">This box answers from the facts in this profile only. Ask about what the company does, who runs it, its accounts, hiring, recent changes, where it is, its website, contacts, size or group.</p>';
  else if(!sents.length)html+='<p class="nf">Not found in the checked sources.</p>';
  else html+=sents.map(function(s){return '<p>'+esc(s[0])+cites(co,s[1].map(function(x){return x.i;}),refs)+'</p>';}).join('');
  el.innerHTML=html;
}
function askPanel(co){
  return '<section class="sec ask" id="s-ask" aria-labelledby="h-ask"><div class="sh"><h2 id="h-ask">Ask about this company</h2></div>'+
    '<form id="askf" data-o="'+co.o+'"><label class="vh" for="askq">Question</label><input id="askq" type="text" autocomplete="off" placeholder="For example: who is on the board?"><button class="btn" type="submit">Ask</button></form>'+
    '<div class="sugg">'+SUGG.map(function(s){return '<button type="button" data-ask="'+esc(s)+'">'+esc(s)+'</button>';}).join('')+'</div>'+
    '<div class="answer" id="answer" aria-live="polite"></div></section>';
}

/* ----- profile ----- */
var TOC=[['summary','Summary'],['ask','Ask'],['identity','Identity'],['financials','Financials'],['leadership','Leadership'],['locations','Workplaces'],['web_presence','Web'],['hiring','Hiring'],['activity','Activity'],['changes','Changes'],['matching','Matching']];
function profile(o){
  var co=BY[o];if(!co)return '<a class="back" href="#">'+IC.back+'Companies</a><p class="muted">No company '+esc(o)+' in this run.</p>';
  current=o;
  var lf=one(co,'legal_form'),on=compare.indexOf(o)>=0;
  return '<article class="pro" aria-labelledby="h-name"><a class="back" href="#">'+IC.back+'Companies</a>'+
    '<header class="head"><h1 id="h-name">'+esc(co.n||co.o)+'</h1>'+
    '<p class="idline"><span class="num">'+orgf(co.o)+'</span>'+(lf?'<span>'+esc(lf.v.name||'')+(lf.v.code?' ('+esc(lf.v.code)+')':'')+'</span>':'')+'<span>'+esc(muni(co))+'</span></p>'+
    '<button type="button" class="btn quiet" data-addcmp="'+o+'" aria-pressed="'+on+'">'+(on?IC.check+'In compare':IC.plus+'Compare')+'</button></header>'+
    stats(co)+
    '<nav class="toc" aria-label="Sections">'+TOC.map(function(t){return '<a href="#s-'+t[0]+'" data-jump="s-'+t[0]+'">'+t[1]+'</a>';}).join('')+'</nav>'+
    summary(co)+askPanel(co)+identity(co)+financials(co)+leadership(co)+locations(co)+web(co)+hiring(co)+activity(co)+changes(co)+matching(co)+errs(co)+'</article>';
}

/* ----- compare ----- */
function compareView(list){
  var cos=list.map(function(o){return BY[o];}).filter(Boolean).slice(0,3);
  if(cos.length<2)return '<a class="back" href="#">'+IC.back+'Companies</a><p class="muted">Tick the box next to 2 or 3 companies in the list, or use Compare on a profile, then press Compare.</p>';
  current=null;
  var n=cos.length;
  function cellC(co,cl,sub){return '<td>'+vbtn(co,cl,render(cl.f,cl.v))+(sub?'<span class="yr">'+sub+'</span>':'')+'</td>';}
  function miss(co,f){var c=any(co,f)[0];return '<td>'+(c?stateLine(c.a):'<span class="muted">Not available</span>')+'</td>';}
  function row(lab,fn){return '<tr><th scope="row">'+esc(lab)+'</th>'+cos.map(fn).join('')+'</tr>';}
  function f1(f){return function(co){var c=one(co,f);return c?cellC(co,c):miss(co,f);};}
  function fin(f){return function(co){var c=latestFig(co,f);return c?'<td>'+vbtn(co,c,money(c.v,true))+'<span class="yr">'+esc(c.v.year)+'</span></td>':'<td><span class="muted">Not available</span></td>';};}
  function cnt(f,w){return function(co){var k=av(co,f),c=any(co,f)[0];if(k.length)return '<td>'+vbtn(co,k[0],plural(k.length,w))+'</td>';return c?'<td>'+stateLine(c.a,c.a==='not_available'?'None':null)+'</td>':'<td><span class="muted">None found</span></td>';};}
  function grp(t){return '<tr class="g"><th scope="rowgroup" colspan="'+(n+1)+'">'+t+'</th></tr>';}
  return '<div class="cmpv"><a class="back" href="#">'+IC.back+'Companies</a><h1>Compare '+n+' companies</h1>'+
    '<table class="cmp" style="--n:'+n+'"><colgroup><col style="width:190px">'+cos.map(function(){return '<col>';}).join('')+'</colgroup>'+
    '<thead><tr><th scope="col"><span class="vh">Field</span></th>'+cos.map(function(co){return '<th scope="col"><a href="#org-'+co.o+'">'+esc(co.n)+'</a><span class="sub2"><span class="num">'+orgf(co.o)+'</span><button type="button" class="rm" data-rm="'+co.o+'" aria-label="Remove '+esc(co.n)+' from compare">'+IC.x+'</button></span></th>';}).join('')+'</tr></thead><tbody>'+
    grp('Identity')+row('Legal form',f1('legal_form'))+row('Municipality',f1('municipality'))+row('Industry',f1('industry'))+row('Incorporated',f1('incorporation_date'))+row('Employees (registry)',function(co){var c=any(co,'employees_registered')[0];return c&&c.a==='available'?cellC(co,c):c?'<td>'+stateLine(c.a)+'</td>':'<td><span class="muted">Not available</span></td>';})+
    grp('Latest company accounts')+row('Revenue',fin('revenue'))+row('Operating result',fin('operating_result'))+row('Annual result',fin('annual_result'))+row('Equity',fin('equity'))+row('Total assets',fin('total_assets'))+
    grp('People')+row('General manager',f1('ceo'))+row('Board chair',f1('board_chair'))+row('Board members',cnt('board_member','member'))+
    grp('Presence')+row('Verified website',f1('official_website'))+row('Social profiles',cnt('social_profile','profile'))+row('Workplaces',cnt('registered_workplace','workplace'))+row('Job ads',cnt('job_ad','ad'))+row('Registry updates, 12 months',cnt('registry_change','update'))+
    grp('Coverage')+row('Sections',function(co){var gaps=SECTIONS.filter(function(s){return co.sec[s]!=='available';});
      return '<td>'+coverage(co)+'<span class="yr">'+(gaps.length?gaps.map(function(s){return SEC_LABEL[s]+' '+STATE_LABEL[st(co.sec[s])].toLowerCase();}).join(', '):'All seven available')+'</span></td>';})+
    '</tbody></table></div>';
}

/* ----- popover ----- */
var popTrigger=null;
function highlight(span,cl){
  var e=esc(span),needles=[];var v=cl.v;
  if(typeof v==='string')needles.push(v);else if(typeof v==='number')needles.push(String(v));
  else if(v&&typeof v==='object'){if(v.amount!=null)needles.push(String(v.amount).replace(/\.0+$/,''));['name','url','title','lei','date'].forEach(function(k){if(typeof v[k]==='string')needles.push(v[k]);});}
  for(var i=0;i<needles.length;i++){var nd=esc(needles[i]);if(nd.length<2)continue;var k=e.toLowerCase().indexOf(nd.toLowerCase());
    if(k>=0)return e.slice(0,k)+'<mark>'+e.slice(k,k+nd.length)+'</mark>'+e.slice(k+nd.length);}
  return e;
}
function popHtml(co,cl,fromRow){
  var h='<div class="pop-h"><strong>'+esc(label(cl.f))+'</strong><button type="button" class="x" data-close aria-label="Close">'+IC.x+'</button></div><div class="pop-v">'+valueOf(cl)+'</div>';
  (cl.e||[]).forEach(function(i){var ev=co.ev[i];if(!ev)return;var u=U[ev[0]]||'';
    var meta=['Retrieved '+dfmt(ev[1])+(tfmt(ev[1])?', '+tfmt(ev[1]):'')];if(ev[2])meta.push('dated '+dfmt(ev[2]));if(SRC_CLASS[ev[4]])meta.push(SRC_CLASS[ev[4]].toLowerCase());
    h+='<div class="pop-e">'+(safeUrl(u)?'<a class="u" href="'+esc(u)+'" target="_blank" rel="noopener noreferrer"><span>'+esc(bare(u).length>110?bare(u).slice(0,107)+'...':bare(u))+'</span>'+IC.ext+'</a>':esc(u))+
      '<p class="pop-m">'+meta.join(', ')+'</p>'+(ev[3]?'<p class="pop-ql">Exact text read from the source</p><pre class="q">'+highlight(ev[3],cl)+'</pre>':'')+'</div>';});
  if(cl.m&&(cl.md||METHOD[cl.m]))h+='<p class="pop-m" style="margin-top:10px">'+esc(cl.md?'Match: '+mdText(cl.md)+'.':METHOD[cl.m])+'</p>';
  if(!fromRow&&document.getElementById(fid(co,cl)))h+='<a class="pop-j" href="#'+fid(co,cl)+'" data-jump="'+fid(co,cl)+'">Show in profile</a>';
  return h;
}
function openPop(btn){
  var co=BY[btn.getAttribute('data-o')];if(!co)return;var cl=ix(co)[btn.getAttribute('data-c')];if(!cl)return;
  var pop=$('pop'),fromRow=!!btn.closest('#'+CSS.escape(fid(co,cl)));
  if(popTrigger===btn&&!pop.hidden){closePop();return;}
  popTrigger=btn;pop.innerHTML=popHtml(co,cl,fromRow);pop.setAttribute('aria-label','Source for '+label(cl.f));pop.hidden=false;pop.classList.remove('open');
  var sheet=window.innerWidth<640;pop.classList.toggle('sheet',sheet);$('scrim').hidden=!sheet;
  if(!sheet){var r=btn.getBoundingClientRect(),w=Math.min(420,window.innerWidth-24);pop.style.width=w+'px';
    var left=Math.min(Math.max(12,r.left),window.innerWidth-w-12),ph=pop.offsetHeight,top=r.bottom+6;
    if(top+ph>window.innerHeight-12&&r.top-ph-6>12)top=r.top-ph-6;
    pop.style.left=(left+window.scrollX)+'px';pop.style.top=(top+window.scrollY)+'px';}
  else{pop.style.left='';pop.style.top='';pop.style.width='';}
  void pop.offsetWidth;pop.classList.add('open');
  btn.setAttribute('aria-expanded','true');pop.focus({preventScroll:true});
}
function closePop(back){var pop=$('pop');if(pop.hidden)return;pop.hidden=true;pop.classList.remove('open');$('scrim').hidden=true;
  if(popTrigger){popTrigger.setAttribute('aria-expanded','false');if(back!==false&&document.body.contains(popTrigger))popTrigger.focus({preventScroll:true});}popTrigger=null;}
function jump(id){var el=document.getElementById(id);if(!el)return;el.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth',block:id.indexOf('f-')===0?'center':'start'});
  if(id.indexOf('f-')===0){el.classList.add('flash');setTimeout(function(){el.classList.remove('flash');},1400);}}

/* ----- events ----- */
document.addEventListener('change',function(e){
  var o=e.target.getAttribute&&e.target.getAttribute('data-cmp');if(!o)return;
  if(!toggleCompare(o,e.target.checked))e.target.checked=false;refreshCmpBtn();
});
document.addEventListener('click',function(e){
  var t=e.target;
  var b=t.closest('button[data-c]');if(b){e.preventDefault();openPop(b);return;}
  if(t.closest('[data-close]')||t.closest('#scrim')){closePop();return;}
  var j=t.closest('[data-jump]');if(j){e.preventDefault();closePop(false);jump(j.getAttribute('data-jump'));return;}
  if(!t.closest('#pop'))closePop(false);
  var a=t.closest('[data-ask]');if(a){var q=$('askq');q.value=a.getAttribute('data-ask');ask(BY[$('askf').getAttribute('data-o')],q.value);return;}
  var ac=t.closest('[data-addcmp]');if(ac){var o=ac.getAttribute('data-addcmp');toggleCompare(o,compare.indexOf(o)<0);refreshCmpBtn();drawList();return;}
  var rm=t.closest('[data-rm]');if(rm){compare=compare.filter(function(x){return x!==rm.getAttribute('data-rm');});drawTray();drawList();
    location.hash=compare.length>=2?'compare='+compare.join(','):(compare.length?'org-'+compare[0]:'');return;}
  var g=t.closest('#tray-go');if(g&&compare.length<2)e.preventDefault();
});
document.addEventListener('submit',function(e){if(e.target.id==='askf'){e.preventDefault();ask(BY[e.target.getAttribute('data-o')],$('askq').value);}});
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'){closePop();return;}
  var tag=(e.target.tagName||'').toLowerCase();
  if(e.key==='/'&&tag!=='input'&&tag!=='textarea'&&tag!=='select'){e.preventDefault();$('q').focus();return;}
  if((e.key==='ArrowDown'||e.key==='ArrowUp')&&(e.target.classList.contains('row-link')||e.target.id==='q')){
    var links=[].slice.call(document.querySelectorAll('.row-link'));if(!links.length)return;e.preventDefault();
    var i=links.indexOf(e.target);i=e.target.id==='q'?0:Math.max(0,Math.min(links.length-1,i+(e.key==='ArrowDown'?1:-1)));links[i].focus();}
});
window.addEventListener('resize',function(){closePop(false);});

/* ----- toc highlight ----- */
var tocTick=false;
function tocUpdate(){tocTick=false;var links=document.querySelectorAll('.toc a');if(!links.length)return;var cur=null;
  [].forEach.call(document.querySelectorAll('.pro .sec'),function(sec){if(sec.getBoundingClientRect().top<=90)cur=sec.id;});
  [].forEach.call(links,function(a){if(a.getAttribute('data-jump')===cur)a.setAttribute('aria-current','true');else a.removeAttribute('aria-current');});}
function watchToc(){tocUpdate();}
window.addEventListener('scroll',function(){if(!tocTick){tocTick=true;requestAnimationFrame(tocUpdate);}},{passive:true});

/* ----- routing ----- */
function wide(){return window.matchMedia('(min-width:901px)').matches;}
function route(){
  closePop(false);
  var hsh=decodeURIComponent(location.hash.slice(1)),main=$('main'),lay=$('layout');
  var m=/^org-(\d{9})$/.exec(hsh),c=/^compare=([\d,]+)$/.exec(hsh);
  if(/^(s-|f-)/.test(hsh)&&main.innerHTML){jump(hsh);return;}
  if(m){main.innerHTML=profile(m[1]);lay.classList.add('detail');}
  else if(c){var l=c[1].split(',');compare=l.filter(function(o){return BY[o];}).slice(0,3);main.innerHTML=compareView(l);lay.classList.add('detail');drawTray();}
  else if(hsh==='sources'){return;}
  else{lay.classList.remove('detail');current=null;
    if(wide()&&filtered.length){main.innerHTML=profile(filtered[0].o);}else main.innerHTML='';}
  drawList();watchToc();
  if(m||c){if(!wide())window.scrollTo(0,0);else if(window.scrollY>200)window.scrollTo(0,0);main.focus({preventScroll:true});}
}
function runHeader(){
  var r=META,ms=r.runtime_ms,rt='not recorded';
  if(ms!=null){var s=Math.round(ms/1000);rt=s>=60?Math.floor(s/60)+' min '+(s%60)+' s':s+' s';}
  var cost=Number(r.cost_usd||0);
  var items=[['Run',esc(r.run_id||'not recorded')],['Companies',nfmt(r.companies)],['Requests',nfmt(r.requests)],['Runtime',rt],['Cost',cost===0?'$0':'$'+cost.toFixed(2)]];
  $('run').innerHTML=items.map(function(x){return '<div><dt>'+x[0]+'</dt><dd>'+x[1]+'</dd></div>';}).join('');
  $('srclist').innerHTML='<table class="srct"><thead><tr><th scope="col">Source</th><th scope="col">Licence or terms</th><th scope="col" class="r">Evidence records</th></tr></thead><tbody>'+
    (r.sources||[]).map(function(s){return '<tr><td>'+esc(s.name)+'</td><td>'+esc(s.licence||'')+'</td><td class="r num">'+(s.evidence!=null?nfmt(s.evidence):'')+'</td></tr>';}).join('')+'</tbody></table>';
  var a=r.agent||{};
  $('colophon').innerHTML='Generated '+dfmt(r.generated_at)+' by '+esc(a.name||'Kilde')+(a.version?' '+esc(a.version):'')+(a.commit?', commit <span class="num">'+esc(String(a.commit).slice(0,7))+'</span>':'')+
    (r.started_at?'. Run started '+dfmt(r.started_at)+' at '+tfmt(r.started_at):'')+'. Every value links to the page or API response it was read from, with the exact text. Typeface Figtree, SIL Open Font License 1.1.';
}
$('count').textContent='Loading companies';
loadData().then(function(d){
  DATA=d;U=d.u;CO=d.co;CO.forEach(function(c){BY[c.o]=c;});
  initControls();runHeader();applyFilters();drawTray();route();
  window.addEventListener('hashchange',route);
  window.matchMedia('(min-width:901px)').addEventListener('change',function(){if(!location.hash||location.hash==='#')route();});
}).catch(function(err){$('count').textContent='This browser could not open the report data ('+String(err&&err.message||err)+').';});
})();
"""

# Figtree variable font, Latin subset, weights 400 to 700. SIL Open Font License 1.1 (see module docstring).
FONT_WOFF2_B64 = (
    "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"
)
