"""Static HTML report: one self-contained file (inline CSS, JS and data) that works offline.

The envelopes are slimmed to what the page shows and embedded as JSON; the page renders the company list,
profiles and compare view in the browser, so 1,500 companies stay well under 15 MB.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

SECTIONS = ("identity", "financials", "leadership", "locations", "web_presence", "hiring", "activity")
NLOD = ("Contains data under the Norwegian licence for Open Government data (NLOD) distributed by "
        "Brønnøysundregistrene")
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
        if (c.get("match") or {}).get("method"):
            row["m"] = c["match"]["method"]
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
        requests = sum(int(o.get("requests") or 0) for o in ops)
    cost = run_report.get("cost_usd", run_report.get("third_party_cost_usd"))
    if cost is None:
        cost = sum(float(o.get("third_party_cost_usd") or 0) for o in ops)
    statuses: dict[str, int] = {}
    for r in runs:
        statuses[r.get("terminal_status") or "missing"] = statuses.get(r.get("terminal_status") or "missing", 0) + 1
    return {
        "run_id": run_report.get("run_id") or next((r.get("run_id") for r in runs if r.get("run_id")), ""),
        "started_at": started, "completed_at": completed,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "companies": run_report.get("companies", len(envelopes)),
        "requests": requests, "runtime_ms": runtime, "cost_usd": cost, "statuses": statuses,
        "agent": run_report.get("agent") or next((e.get("agent") for e in envelopes if e.get("agent")), {}) or {},
        "sources": _sources(envelopes, run_report), "nlod": NLOD,
    }


def _embed(obj: Any) -> str:
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return s.replace("</", "<\\/").replace("<!--", "<\\!--")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def write_report(envelopes: list[dict], path: str, run_report: dict) -> None:
    run_report = run_report or {}
    meta = _run_meta(envelopes, run_report)
    data = None
    payload = ""
    for limit in (None, 400, 160, 60):
        data = build_data(envelopes, limit)
        payload = _embed(data)
        if len(payload.encode("utf-8")) < MAX_BYTES:
            break
    noscript_rows = "".join(
        f'<li id="org-{html.escape(c["o"])}">{html.escape(c["n"])} ({html.escape(c["o"])})</li>'
        for c in (data or {}).get("co", [])[:2000])
    page = (PAGE.replace("/*CSS*/", CSS).replace("/*JS*/", JS)
            .replace("__TITLE__", html.escape(f"Pata report {meta['run_id']}".strip()))
            .replace("__META__", _embed(meta)).replace("__DATA__", payload)
            .replace("__NOSCRIPT__", noscript_rows).replace("__NLOD__", html.escape(NLOD)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)


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
<header class="top">
  <div class="brand"><span class="mark" aria-hidden="true"></span><div><h1>Pata</h1><p class="sub">Norwegian company profiles with a source for every fact</p></div></div>
  <dl class="run" id="run"></dl>
  <details class="sources"><summary>Sources and licences</summary><div id="sources"></div><p class="nlod">__NLOD__</p></details>
</header>
<div class="layout" id="layout">
  <aside class="side" aria-label="Companies">
    <div class="tools" role="search">
      <label class="vh" for="q">Search by name or organisation number</label>
      <input id="q" type="search" placeholder="Search name or org number" autocomplete="off" spellcheck="false">
      <div class="chips" role="group" aria-label="Filters">
        <label class="chip"><input type="checkbox" id="f-web"> Has website</label>
        <label class="chip"><input type="checkbox" id="f-jobs"> Has jobs</label>
        <label class="chip"><input type="checkbox" id="f-chg"> Has changes</label>
      </div>
      <div class="row2">
        <label><span class="vh">Section</span><select id="f-sec" aria-label="Section to filter on"></select></label>
        <label><span class="vh">State</span><select id="f-state" aria-label="Availability state"></select></label>
      </div>
      <div class="row2">
        <label class="sortl"><span>Sort</span><select id="sort" aria-label="Sort companies">
          <option value="name">Name A to Z</option><option value="org">Org number</option>
          <option value="rev">Revenue, highest first</option><option value="facts">Most facts found</option>
          <option value="chg">Most changes</option></select></label>
        <p class="count" id="count" aria-live="polite"></p>
      </div>
      <div class="tray" id="tray" hidden><span id="tray-text"></span><a class="btn" id="tray-go" href="#">Compare</a><button type="button" class="btn ghost" id="tray-clear">Clear</button></div>
    </div>
    <ul class="list" id="list"></ul>
    <button type="button" class="btn ghost more" id="more" hidden>Show more</button>
  </aside>
  <main id="main" class="main" tabindex="-1"></main>
</div>
<noscript><p>This report needs JavaScript for search and profiles. Companies in this run:</p><ul>__NOSCRIPT__</ul></noscript>
<script type="application/json" id="meta">__META__</script>
<script type="application/json" id="data">__DATA__</script>
<script>/*JS*/</script>
</body>
</html>
"""

CSS = r"""
:root{
  --bg:#f5f4f0;--surface:#ffffff;--surface2:#eeece6;--ink:#1b2125;--muted:#525d66;--line:#d9d6cd;
  --accent:#1d5a73;--accent-soft:#e1edf2;--accent-ink:#ffffff;--focus:#b4470f;--hl:#fff1c7;
  --ok-bg:#dcefe2;--ok-ink:#13502b;--na-bg:#e8e6e0;--na-ink:#3f454b;--blk-bg:#fbe5c6;--blk-ink:#753a00;
  --amb-bg:#e8e0f4;--amb-ink:#4a2a78;--fail-bg:#f7dada;--fail-ink:#861b1b;--napp-ink:#4f5860;
  --pos:#13502b;--neg:#8a1c1c;--radius:10px;
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#111417;--surface:#191d21;--surface2:#21262b;--ink:#e7e4dd;--muted:#a3abb3;--line:#2f363c;
  --accent:#86c4da;--accent-soft:#1d3440;--accent-ink:#0b1a20;--focus:#f0a35e;--hl:#3b3418;
  --ok-bg:#173823;--ok-ink:#a9e4c0;--na-bg:#2a2f34;--na-ink:#cdd1d5;--blk-bg:#3b2b12;--blk-ink:#f4cb8f;
  --amb-bg:#2f2542;--amb-ink:#d6c6f3;--fail-bg:#3e1c1c;--fail-ink:#f3b6b6;--napp-ink:#b3bac1;--pos:#a9e4c0;--neg:#f3b6b6;
}}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-size:15px;line-height:1.5}
em,i,cite,dfn,address{font-style:normal}
a{color:var(--accent);text-underline-offset:2px}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:4px}
h1,h2,h3{line-height:1.25;margin:0}
.vh{position:absolute!important;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.skip{position:absolute;left:8px;top:-40px;background:var(--surface);padding:6px 10px;z-index:9;border-radius:6px}
.skip:focus{top:8px}
.top{display:grid;grid-template-columns:auto 1fr;gap:12px 28px;align-items:center;padding:14px 24px;background:var(--surface);border-bottom:1px solid var(--line)}
.brand{display:flex;gap:10px;align-items:center}
.brand h1{font-size:20px;letter-spacing:.01em}
.sub{margin:0;color:var(--muted);font-size:13px}
.mark{width:26px;height:26px;border-radius:7px;background:var(--accent);box-shadow:inset -8px -8px 0 0 var(--accent-soft)}
.run{display:flex;flex-wrap:wrap;gap:4px 22px;margin:0;justify-content:flex-end}
.run div{min-width:0}
.run dt{font-size:12px;color:var(--muted)}
.run dd{margin:0;font-variant-numeric:tabular-nums;font-weight:600;overflow-wrap:anywhere}
.sources{grid-column:1/-1;font-size:13px;color:var(--muted)}
.sources summary{cursor:pointer;width:max-content;max-width:100%}
.sources ul{margin:6px 0;padding-left:18px}
.nlod{margin:6px 0 0}
.layout{display:grid;grid-template-columns:minmax(300px,360px) 1fr;min-height:calc(100vh - 90px)}
.side{border-right:1px solid var(--line);background:var(--surface);display:flex;flex-direction:column;min-width:0}
.tools{padding:14px 16px 10px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--surface);z-index:2}
input[type=search],select{font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;width:100%;min-width:0}
input[type=search]{font-size:16px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 8px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:13px;cursor:pointer;background:var(--bg)}
.chip:has(input:checked){background:var(--accent-soft);border-color:var(--accent)}
.chip input{margin:0;accent-color:var(--accent)}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:8px;align-items:center;margin-top:6px}
.row2 label{min-width:0}
.sortl{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--muted)}
.count{margin:0;font-size:13px;color:var(--muted);text-align:right}
.tray{display:flex;align-items:center;gap:8px;margin-top:10px;padding:8px 10px;border-radius:8px;background:var(--accent-soft);font-size:13px}
.tray span{flex:1}
.btn{font:inherit;font-size:13px;display:inline-block;padding:6px 12px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:var(--accent-ink);text-decoration:none;cursor:pointer}
.btn.ghost{background:transparent;color:var(--accent)}
.list{list-style:none;margin:0;padding:0;overflow:auto;flex:1}
.item{display:grid;grid-template-columns:1fr auto;gap:2px 8px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line)}
.item.cur{background:var(--accent-soft)}
.item a{color:var(--ink);text-decoration:none;font-weight:600;overflow-wrap:anywhere}
.item a:hover{text-decoration:underline}
.item .meta{grid-column:1;font-size:12px;color:var(--muted)}
.item .cmp{grid-row:1/3;grid-column:2;display:flex;align-items:center;gap:4px;font-size:12px;color:var(--muted)}
.item .cmp input{width:18px;height:18px;accent-color:var(--accent)}
.dots{display:inline-flex;gap:3px;vertical-align:middle;margin-left:6px}
.dot{width:8px;height:8px;border-radius:2px;background:var(--na-bg);border:1px solid var(--line)}
.dot.available{background:var(--ok-ink);border-color:var(--ok-ink)}
.dot.blocked{background:var(--blk-ink);border-color:var(--blk-ink)}
.dot.ambiguous{background:var(--amb-ink);border-color:var(--amb-ink)}
.dot.failed{background:var(--fail-ink);border-color:var(--fail-ink)}
.dot.not_applicable{background:transparent;border-style:dashed}
.more{margin:12px 16px}
.main{padding:22px 28px 60px;min-width:0}
.main>article,.main>.cmpgrid{max-width:1240px}
@media (min-width:901px){.side{position:sticky;top:0;height:100vh;align-self:start}}
.main:focus{outline:none}
.back{display:none;margin-bottom:12px}
.head{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:baseline;margin-bottom:6px}
.head h2{font-size:24px;overflow-wrap:anywhere}
.org{color:var(--muted);font-variant-numeric:tabular-nums}
.strip{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 18px;padding:0;list-style:none}
.strip li{display:flex;gap:6px;align-items:center;font-size:13px}
.strip a{color:var(--ink);text-decoration:none}
.badge{display:inline-block;font-size:12px;font-weight:600;padding:1px 8px;border-radius:999px;white-space:nowrap;border:1px solid transparent}
.badge.available{background:var(--ok-bg);color:var(--ok-ink)}
.badge.not_available{background:var(--na-bg);color:var(--na-ink)}
.badge.blocked{background:var(--blk-bg);color:var(--blk-ink)}
.badge.ambiguous{background:var(--amb-bg);color:var(--amb-ink)}
.badge.failed{background:var(--fail-bg);color:var(--fail-ink)}
.badge.not_applicable{background:transparent;color:var(--napp-ink);border:1px dashed var(--napp-ink)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:16px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;min-width:0}
.card.wide{grid-column:1/-1}
.card h3{font-size:16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.facts{margin:0;display:grid;grid-template-columns:minmax(110px,170px) 1fr;gap:8px 14px}
.facts dt{color:var(--muted);font-size:13px;padding-top:1px}
.facts dd{margin:0;min-width:0;overflow-wrap:anywhere}
.val{display:block}
.src{display:flex;flex-wrap:wrap;gap:2px 8px;align-items:center;font-size:12px;color:var(--muted);margin-top:1px}
.src a{color:var(--muted)}
.src a:hover{color:var(--accent)}
.qbtn{font:inherit;font-size:12px;background:none;border:0;padding:0;color:var(--accent);cursor:pointer;text-decoration:underline;text-underline-offset:2px}
.quote{display:block;margin:4px 0 2px;padding:6px 8px;border-left:3px solid var(--accent);background:var(--surface2);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink)}
.quote[hidden]{display:none}
.note{color:var(--muted);font-size:13px}
.flag{scroll-margin-top:16px}
.flag.hl{background:var(--hl);border-radius:6px;box-shadow:0 0 0 4px var(--hl)}
.summary p{margin:0 0 8px}
.cite{font-size:11px;vertical-align:super;line-height:0;margin-left:1px}
.cite a{text-decoration:none;padding:0 1px}
.unk{margin:8px 0 0;padding-left:18px;color:var(--muted);font-size:14px}
.tablewrap{max-width:100%;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
caption{text-align:left;color:var(--muted);font-size:13px;padding-bottom:6px}
th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top}
th:first-child,td:first-child{text-align:left}
thead th{font-size:12px;color:var(--muted);font-weight:600}
td .src{justify-content:flex-end}
.neg{color:var(--neg)}
.items{list-style:none;margin:0;padding:0}
.items li{padding:8px 0;border-bottom:1px solid var(--line)}
.items li:last-child{border-bottom:0}
.items .t{font-weight:600}
.changes li{display:grid;grid-template-columns:auto 1fr;gap:2px 10px}
.kind{font-size:12px;color:var(--muted);text-transform:none;padding-top:2px;min-width:92px}
.empty{color:var(--muted)}
.cmpgrid{display:grid;gap:0;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface);overflow:hidden}
.crow{display:grid;grid-template-columns:200px repeat(var(--n),minmax(0,1fr));border-bottom:1px solid var(--line)}
.crow:last-child{border-bottom:0}
.crow>div{padding:9px 12px;min-width:0;overflow-wrap:anywhere}
.crow .lab{color:var(--muted);font-size:13px;background:var(--surface2)}
.crow.hd>div{font-weight:600}
.crow.hd a{color:var(--ink)}
.errs{color:var(--fail-ink);font-size:13px}
@media (max-width:900px){
  .top{grid-template-columns:1fr;padding:12px 16px}
  .run{justify-content:flex-start;gap:4px 16px}
  .layout{grid-template-columns:1fr}
  .side{border-right:0}
  .layout.detail .side{display:none}
  .layout:not(.detail) .main{display:none}
  .back{display:inline-block}
  .main{padding:16px 16px 48px}
  .tools{padding:12px 16px}
  .crow{grid-template-columns:repeat(var(--n),minmax(0,1fr))}
  .crow .lab{grid-column:1/-1;padding:6px 12px 2px;font-size:12px}
  .crow.hd .lab{display:none}
}
@media (max-width:600px){
  .fin thead{display:none}
  .fin,.fin tbody,.fin tr,.fin th,.fin td{display:block;width:100%}
  .fin caption{display:block}
  .fin tr{padding:8px 0;border-bottom:1px solid var(--line)}
  .fin th[scope=row]{border:0;padding:0 0 4px;font-size:14px}
  .fin td{display:grid;grid-template-columns:52px 1fr;gap:0 10px;text-align:left;border:0;padding:3px 0}
  .fin td::before{content:attr(data-y);color:var(--muted);font-size:13px;grid-row:1/3}
  .fin td .src{justify-content:flex-start}
  .run{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px 12px}
  .run dd{font-size:14px}
  .sub{font-size:12px}
}
@media (max-width:480px){
  .facts{grid-template-columns:1fr;gap:2px}
  .facts dd{margin-bottom:8px}
  .head h2{font-size:20px}
  .card{padding:14px}
  th,td{padding:6px 4px;font-size:13px}
  .crow>div{padding:8px}
}
@media print{.side,.back,.skip{display:none}.layout{display:block}}
"""

JS = r"""
(function(){
'use strict';
var META=JSON.parse(document.getElementById('meta').textContent);
var DATA=JSON.parse(document.getElementById('data').textContent);
var U=DATA.u, CO=DATA.co, BY={};
CO.forEach(function(c){BY[c.o]=c;});
var SECTIONS=['identity','financials','leadership','locations','web_presence','hiring','activity'];
var SEC_LABEL={identity:'Identity',financials:'Financials',leadership:'Leadership',locations:'Locations',web_presence:'Web presence',hiring:'Hiring',activity:'Activity'};
var STATES=['available','not_available','blocked','ambiguous','failed','not_applicable'];
var STATE_LABEL={available:'Available',not_available:'Not available',blocked:'Blocked',ambiguous:'Ambiguous',failed:'Failed',not_applicable:'Not applicable'};
var FIELD={legal_name:'Legal name',former_names:'Former names',legal_form:'Legal form',registration_date:'Registered',incorporation_date:'Incorporated',
 business_address:'Business address',postal_address:'Postal address',municipality:'Municipality',industry:'Industry',secondary_industry:'Secondary industry',
 institutional_sector:'Institutional sector',statutory_purpose:'Statutory purpose',registered_activity:'Registered activity',share_capital:'Share capital',
 employees_registered:'Employees (registry)',legal_status:'Legal status',register_memberships:'Registers',signature_rule:'Signature rule',
 part_of_group:'Part of group',group_parent:'Group parent',group_subsidiary:'Subsidiary',group_ultimate_parent:'Ultimate parent',
 parent_organisation_number:'Parent org number',deleted_date:'Deleted',registry_email:'Email (registry)',registry_phone:'Phone (registry)',registry_website:'Website (registry)',
 lei:'LEI',financial_licence:'Financial licence',ticker:'Ticker',isin:'ISIN',
 revenue:'Revenue',operating_result:'Operating result',salary_costs:'Salary costs',profit_before_tax:'Profit before tax',annual_result:'Annual result',
 total_assets:'Total assets',cash_and_bank:'Cash and bank',equity:'Equity',debt:'Debt',accounts_audited:'Audited accounts',annual_report_pdf:'Annual report',
 latest_filed_accounts_year:'Latest filed accounts',audit_opt_out_date:'Audit opt-out',
 ceo:'General manager',board_chair:'Chair',board_deputy_chair:'Deputy chair',board_member:'Board member',board_deputy_member:'Deputy board member',
 board_observer:'Observer',auditor:'Auditor',accountant:'Accountant',contact_person:'Contact person',owner:'Owner',partner:'Partner',other_role:'Other role',
 registered_workplace:'Registered workplace',official_website:'Official website',website_description:'Description (own words)',social_profile:'Social profile',
 site_email:'Email (website)',site_phone:'Phone (website)',contact_email:'Email (website)',contact_phone:'Phone (website)',
 job_ad:'Job ad',careers_page:'Careers page',registry_change:'Registry change',site_page_updated:'Website update',news:'News',news_item:'News'};
var PLAT={linkedin:'LinkedIn',facebook:'Facebook',instagram:'Instagram',youtube:'YouTube',x:'X',twitter:'X',tiktok:'TikTok',github:'GitHub'};
var FSEC={revenue:'financials',annual_result:'financials',equity:'financials',ceo:'leadership',board_chair:'leadership',official_website:'web_presence',social_profile:'web_presence',job_ad:'hiring',registry_change:'activity'};
var FIN=['revenue','operating_result','salary_costs','profit_before_tax','annual_result','total_assets','cash_and_bank','equity','debt'];
var MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
var PAGE=200, shown=PAGE, filtered=[], compare=[], current=null;

function $(id){return document.getElementById(id);}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];});}
function safeUrl(u){return /^https?:\/\//i.test(u||'')?u:null;}
function label(f){return FIELD[f]||String(f||'').replace(/_/g,' ').replace(/^./,function(x){return x.toUpperCase();});}
function date(s){if(!s)return '';var m=/^(\d{4})-(\d{2})-(\d{2})/.exec(s);return m?(+m[3])+' '+MON[+m[2]-1]+' '+m[1]:esc(s);}
function num(n){var x=Number(n);if(!isFinite(x))return esc(n);var s=Math.abs(x).toLocaleString('en-GB',{maximumFractionDigits:2});return x<0?'−'+s:s;}
function money(v){if(v==null||v.amount==null)return '';return '<span class="'+(Number(v.amount)<0?'neg':'')+'">'+(v.currency?esc(v.currency)+' ':'')+num(v.amount)+'</span>';}
function host(u){try{return new URL(u).hostname.replace(/^www\./,'');}catch(e){return u;}}
function badge(st){st=STATES.indexOf(st)>=0?st:'failed';return '<span class="badge '+st+'">'+STATE_LABEL[st]+'</span>';}

/* ----- value rendering ----- */
function link(u,text){var s=safeUrl(u);return s?'<a href="'+esc(s)+'" target="_blank" rel="noopener noreferrer">'+esc(text||u)+'</a>':esc(text||u);}
function render(f,v){
  if(v==null)return '';
  if(typeof v==='string'){return safeUrl(v)?link(v,v.replace(/^https?:\/\/(www\.)?/,'').replace(/\/$/,'')):esc(f.indexOf('date')>=0?date(v):v);}
  if(typeof v==='number')return num(v);
  if(typeof v==='boolean')return v?'Yes':'No';
  if(Array.isArray(v))return v.map(function(x){return render(f,x);}).join(', ');
  if(v.amount!=null&&v.currency!=null)return money(v)+(v.year?' <span class="note">('+esc(v.year)+')</span>':'');
  if(f==='social_profile'||(v.platform&&v.url))return esc(PLAT[String(v.platform||'').toLowerCase()]||cap(v.platform||'Profile'))+': '+link(v.url,(v.url||'').replace(/^https?:\/\/(www\.)?/,''));
  if(f==='job_ad')return '<span class="t">'+(v.url?link(v.url,v.title||'Job ad'):esc(v.title||'Job ad'))+'</span>'+
     '<span class="note"> '+[v.location,v.published?'published '+date(v.published):'',v.expires?'closes '+date(v.expires):''].filter(Boolean).map(esc).join(' · ')+'</span>';
  if(f==='registered_workplace')return '<span class="t">'+esc(v.name||'')+'</span> <span class="note">'+esc(v.organisation_number||'')+'</span><br>'+
     esc(v.address||'')+(v.employees!=null?' · '+num(v.employees)+' employees':'')+(v.start_date?' · since '+date(v.start_date):'');
  if(v.name&&(v.role||v.role_code))return '<span class="t">'+esc(v.name)+'</span>'+(v.organisation_number?' <span class="note">'+esc(v.organisation_number)+'</span>':'');
  if(f==='registry_change')return esc(cap(v.type||'change'))+(v.changed&&v.changed.length?': '+esc(v.changed.join(', ')):'');
  if(v.url&&(v.title||v.date))return link(v.url,v.title||v.url);
  if(v.code!=null&&v.name)return esc(v.name)+' <span class="note">'+esc(v.code)+'</span>';
  if(v.lei)return esc(v.lei)+(v.status?' <span class="note">'+esc(v.status)+(v.next_renewal?', renew by '+date(v.next_renewal):'')+'</span>':'');
  if(v.year&&v.url)return link(v.url,'Annual report '+v.year);
  if(v.year!=null&&v.audited!=null)return (v.audited?'Audited':'Not audited')+' ('+esc(v.year)+')';
  return Object.keys(v).filter(function(k){return v[k]!=null&&typeof v[k]!=='object';}).map(function(k){return esc(k.replace(/_/g,' '))+': '+esc(v[k]);}).join(' · ');
}
function cap(s){s=String(s);return s.charAt(0).toUpperCase()+s.slice(1);}
var qn=0;
function srcs(co,cl,compact){
  return (cl.e||[]).map(function(ix){
    var ev=co.ev[ix];if(!ev)return '';var u=U[ev[0]]||'';var id='q'+(++qn);
    var parts=['<a href="'+esc(safeUrl(u)||'#')+'" target="_blank" rel="noopener noreferrer" title="'+esc(ev[3]||u)+'">'+esc(host(u))+'</a>'];
    if(ev[1])parts.push('<span>retrieved '+date(ev[1])+'</span>');
    if(ev[2]&&!compact)parts.push('<span>dated '+date(ev[2])+'</span>');
    if(ev[3])parts.push('<button type="button" class="qbtn" aria-expanded="false" aria-controls="'+id+'">Quote</button>');
    return '<span class="src">'+parts.join('')+'</span>'+(ev[3]?'<code class="quote" id="'+id+'" hidden>'+esc(ev[3])+'</code>':'');
  }).join('');
}
function valueBlock(co,cl){
  if(cl.a!=='available')return '<span class="val">'+badge(cl.a)+(cl.n?' <span class="note">'+esc(cl.n)+'</span>':'')+'</span>'+srcs(co,cl);
  return '<span class="val">'+render(cl.f,cl.v)+'</span>'+srcs(co,cl);
}

/* ----- list ----- */
function initControls(){
  var sec=$('f-sec'),st=$('f-state');
  sec.innerHTML='<option value="">Any section</option>'+SECTIONS.map(function(s){return '<option value="'+s+'">'+SEC_LABEL[s]+'</option>';}).join('');
  st.innerHTML='<option value="">Any state</option>'+STATES.map(function(s){return '<option value="'+s+'">'+STATE_LABEL[s]+'</option>';}).join('');
  ['q','f-web','f-jobs','f-chg','f-sec','f-state','sort'].forEach(function(id){$(id).addEventListener(id==='q'?'input':'change',function(){shown=PAGE;applyFilters();});});
  $('more').addEventListener('click',function(){shown+=PAGE;drawList();});
  $('tray-clear').addEventListener('click',function(){compare=[];drawList();drawTray();});
}
function applyFilters(){
  var q=$('q').value.trim().toLowerCase(),qd=q.replace(/\s/g,'');
  var w=$('f-web').checked,j=$('f-jobs').checked,ch=$('f-chg').checked,sec=$('f-sec').value,st=$('f-state').value;
  filtered=CO.filter(function(c){
    if(q&&c.n.toLowerCase().indexOf(q)<0&&(!/^\d+$/.test(qd)||c.o.indexOf(qd)<0))return false;
    if(w&&!c.fl.web)return false;if(j&&!c.fl.jobs)return false;if(ch&&!c.fl.chg)return false;
    if(st){if(sec){if(c.sec[sec]!==st)return false;}else if(!SECTIONS.some(function(s){return c.sec[s]===st;}))return false;}
    return true;});
  var s=$('sort').value;
  filtered.sort(function(a,b){
    if(s==='org')return a.o<b.o?-1:1;
    if(s==='rev'){var x=a.rev==null?-Infinity:a.rev,y=b.rev==null?-Infinity:b.rev;if(x!==y)return y-x;}
    if(s==='facts'&&a.nf!==b.nf)return b.nf-a.nf;
    if(s==='chg'){var d=(b.ch.length+countF(b,'registry_change'))-(a.ch.length+countF(a,'registry_change'));if(d)return d;}
    return a.n.localeCompare(b.n,'nb');});
  drawList();
}
function countF(c,f){var n=0;c.c.forEach(function(x){if(x.f===f&&x.a==='available')n++;});return n;}
function drawList(){
  var html=filtered.slice(0,shown).map(function(c){
    var on=compare.indexOf(c.o)>=0;
    return '<li class="item'+(current===c.o?' cur':'')+'"><a href="#org-'+c.o+'"'+(current===c.o?' aria-current="true"':'')+'>'+esc(c.n||c.o)+'</a>'+
      '<label class="cmp"><input type="checkbox" data-cmp="'+c.o+'"'+(on?' checked':'')+' aria-label="Compare '+esc(c.n)+'"> Compare</label>'+
      '<span class="meta">'+c.o+' · '+esc(muni(c))+'<span class="dots" aria-label="'+SECTIONS.map(function(s){return SEC_LABEL[s]+' '+STATE_LABEL[c.sec[s]];}).join(', ')+'">'+
      SECTIONS.map(function(s){return '<span class="dot '+esc(c.sec[s])+'" title="'+SEC_LABEL[s]+': '+STATE_LABEL[c.sec[s]]+'"></span>';}).join('')+'</span></span></li>';}).join('');
  $('list').innerHTML=html||'<li class="item"><span class="empty">No companies match these filters.</span></li>';
  $('count').textContent=filtered.length+' of '+CO.length+' companies';
  $('more').hidden=filtered.length<=shown;
}
function muni(c){for(var i=0;i<c.c.length;i++)if(c.c[i].f==='municipality'&&c.c[i].a==='available')return typeof c.c[i].v==='string'?cap(c.c[i].v.toLowerCase()):'';return '';}
function drawTray(){
  var t=$('tray');t.hidden=compare.length===0;
  $('tray-text').textContent=compare.length+' of 3 selected'+(compare.length<2?', pick at least 2':'');
  $('tray-go').href='#compare='+compare.join(',');
  $('tray-go').setAttribute('aria-disabled',compare.length<2?'true':'false');
}
document.addEventListener('change',function(e){
  var o=e.target.getAttribute&&e.target.getAttribute('data-cmp');if(!o)return;
  if(e.target.checked){if(compare.length>=3){e.target.checked=false;$('tray-text').textContent='Compare up to 3 companies';return;}compare.push(o);}
  else compare=compare.filter(function(x){return x!==o;});
  drawTray();
});
document.addEventListener('click',function(e){
  var b=e.target.closest&&e.target.closest('.qbtn');
  if(b){var q=$(b.getAttribute('aria-controls'));var open=b.getAttribute('aria-expanded')!=='true';b.setAttribute('aria-expanded',open?'true':'false');q.hidden=!open;b.textContent=open?'Hide quote':'Quote';return;}
  var c=e.target.closest&&e.target.closest('a[data-claim]');
  if(c){e.preventDefault();var el=document.getElementById(c.getAttribute('data-claim'));if(el){el.scrollIntoView({behavior:'smooth',block:'center'});el.classList.add('hl');setTimeout(function(){el.classList.remove('hl');},1800);}}
  var g=e.target.closest&&e.target.closest('#tray-go');if(g&&compare.length<2)e.preventDefault();
});

/* ----- profile ----- */
function claimsIn(co,sec){return co.c.filter(function(c){return c.s===sec;});}
function fid(co,cl){return 'cl-'+co.o+'-'+cl.i;}
function factList(co,cls){
  if(!cls.length)return '';
  return '<dl class="facts">'+cls.map(function(cl){return '<dt>'+esc(label(cl.f))+'</dt><dd class="flag" id="'+fid(co,cl)+'">'+valueBlock(co,cl)+'</dd>';}).join('')+'</dl>';
}
function itemList(co,cls,cls2){
  if(!cls.length)return '';
  return '<ul class="items '+(cls2||'')+'">'+cls.map(function(cl){return '<li class="flag" id="'+fid(co,cl)+'">'+(cl.a==='available'?'':'<span class="note">'+esc(label(cl.f))+' </span>')+valueBlock(co,cl)+'</li>';}).join('')+'</ul>';
}
function finTable(co,cls){
  var fig=cls.filter(function(c){return FIN.indexOf(c.f)>=0&&c.a==='available'&&c.v&&typeof c.v==='object';});
  var other=cls.filter(function(c){return fig.indexOf(c)<0;});
  var out='';
  ['company','group'].forEach(function(kind){
    var rows=fig.filter(function(c){return (c.v.account_type||'company')===kind;});if(!rows.length)return;
    var years=[];var per={};rows.forEach(function(c){var y=String(c.v.year||'');if(years.indexOf(y)<0){years.push(y);per[y]=c.v;}});
    years.sort().reverse();
    var cur={};rows.forEach(function(c){if(c.v.currency)cur[c.v.currency]=1;});
    var fields=FIN.filter(function(f){return rows.some(function(c){return c.f===f;});});
    out+='<div class="tablewrap"><table class="fin"><caption>'+(kind==='group'?'Group accounts':'Company accounts')+'. Currency '+esc(Object.keys(cur).join(', ')||'not stated')+
      '. Amounts as filed, never estimated.</caption><thead><tr><th scope="col">Figure</th>'+
      years.map(function(y){var p=per[y];return '<th scope="col">'+esc(y)+'<br><span class="note">'+(p.period_start?date(p.period_start)+' to ':'')+date(p.period_end)+'</span></th>';}).join('')+'</tr></thead><tbody>'+
      fields.map(function(f){return '<tr><th scope="row">'+esc(label(f))+'</th>'+years.map(function(y){
        var c=rows.filter(function(x){return x.f===f&&String(x.v.year)===y;})[0];
        return '<td data-y="'+esc(y)+'"'+(c?' class="flag" id="'+fid(co,c)+'"':'')+'>'+(c?money(c.v)+srcs(co,c,true):'<span class="badge not_available">Not filed</span>')+'</td>';}).join('')+'</tr>';}).join('')+
      '</tbody></table></div>';
  });
  return out+itemList(co,other);
}
function sectionCard(co,sec,wide){
  var cls=claimsIn(co,sec),body;
  if(sec==='financials')body=finTable(co,cls);
  else if(sec==='identity'||sec==='web_presence')body=factList(co,cls);
  else body=itemList(co,cls.slice().sort(function(a,b){return (b.d||'').localeCompare(a.d||'');}));
  if(!body)body='<p class="empty">Nothing recorded for this section.</p>';
  return '<section class="card'+(wide?' wide':'')+'" id="sec-'+co.o+'-'+sec+'" aria-labelledby="h-'+co.o+'-'+sec+'"><h3 id="h-'+co.o+'-'+sec+'">'+SEC_LABEL[sec]+' '+badge(co.sec[sec])+'</h3>'+body+'</section>';
}
function summaryCard(co){
  var idx={};co.c.forEach(function(c){idx[c.i]=c;});var n=0,refs={};
  var sents=co.sum.t.map(function(s){
    var cites=(s[1]||[]).filter(function(id){return idx[id];}).map(function(id){if(!refs[id])refs[id]=++n;
      return '<a href="#'+fid(co,idx[id])+'" data-claim="'+fid(co,idx[id])+'" title="'+esc(label(idx[id].f))+'">['+refs[id]+']</a>';}).join('');
    return '<p>'+esc(s[0])+(cites?'<span class="cite">'+cites+'</span>':'')+'</p>';}).join('');
  var unk=co.sum.u.length?'<p class="note">Not found or not checked:</p><ul class="unk">'+co.sum.u.map(function(u){return '<li>'+esc(u)+'</li>';}).join('')+'</ul>':'';
  return '<section class="card wide summary" aria-labelledby="sum-'+co.o+'"><h3 id="sum-'+co.o+'">Summary</h3>'+(sents||'<p class="empty">No summary.</p>')+unk+'</section>';
}
function changesCard(co){
  var items=co.ch.map(function(ch){
    var txt=ch.summary||(label(ch.field)+(ch.before!=null||ch.after!=null?': '+(ch.before==null?'none':JSON.stringify(ch.before))+' to '+(ch.after==null?'none':JSON.stringify(ch.after)):''));
    return '<li><span class="kind">'+esc(cap(String(ch.type||'change').replace(/_/g,' ')))+'</span><span>'+esc(txt)+(ch.detected_at?'<span class="src">detected '+date(ch.detected_at)+'</span>':'')+'</span></li>';}).join('');
  var reg=co.c.filter(function(c){return c.f==='registry_change'&&c.a==='available';}).length;
  var body=items?'<ul class="items changes">'+items+'</ul>':'<p class="empty">No changes detected against the previous run'+(reg?'. '+reg+' dated registry change'+(reg>1?'s are':' is')+' listed under Activity.':'.')+'</p>';
  return '<section class="card wide" aria-labelledby="chg-'+co.o+'"><h3 id="chg-'+co.o+'">What changed</h3>'+body+'</section>';
}
function profile(o){
  var co=BY[o];if(!co){return notFound(o);}
  current=o;qn=0;
  var lf=co.c.filter(function(c){return c.f==='legal_form'&&c.a==='available';})[0];
  var h='<a class="btn ghost back" href="#">Back to list</a>'+
    '<article id="org-'+co.o+'"><div class="head"><h2>'+esc(co.n||co.o)+'</h2><span class="org">Org. no. '+esc(co.o.replace(/(\d{3})(\d{3})(\d{3})/,'$1 $2 $3'))+'</span>'+
    (lf?'<span class="note">'+render('legal_form',lf.v)+'</span>':'')+'<span class="note">'+esc(muni(co))+'</span></div>'+
    '<ul class="strip" aria-label="Section availability">'+SECTIONS.map(function(s){return '<li><a href="#sec-'+co.o+'-'+s+'" data-claim="sec-'+co.o+'-'+s+'">'+SEC_LABEL[s]+'</a> '+badge(co.sec[s])+'</li>';}).join('')+'</ul>'+
    (co.err.length?'<p class="errs">Run notes: '+co.err.map(esc).join('; ')+'</p>':'')+
    '<div class="grid">'+summaryCard(co)+changesCard(co)+
    SECTIONS.map(function(s){return sectionCard(co,s,s==='financials'||s==='identity');}).join('')+'</div></article>';
  return h;
}
function notFound(o){return '<a class="btn ghost back" href="#">Back to list</a><p class="empty">No company '+esc(o)+' in this run.</p>';}

/* ----- compare ----- */
function first(co,f){return co.c.filter(function(c){return c.f===f&&c.a==='available';});}
function latest(co,f){var xs=first(co,f).filter(function(c){return c.v&&(c.v.account_type||'company')==='company';});xs.sort(function(a,b){return String(b.v.year).localeCompare(String(a.v.year));});return xs[0];}
function cell(co,cl,fallbackField){
  if(cl)return '<div class="flag">'+render(cl.f,cl.v)+srcs(co,cl,true)+'</div>';
  var st=co.c.filter(function(c){return c.f===fallbackField;})[0];
  if(st)return '<div>'+badge(st.a)+'</div>';
  var sec=FSEC[fallbackField]||'identity',ss=co.sec[sec];
  return '<div>'+badge(ss==='available'?'not_available':ss)+'</div>';
}
function compareView(list){
  var cos=list.map(function(o){return BY[o];}).filter(Boolean).slice(0,3);
  if(cos.length<2)return '<a class="btn ghost back" href="#">Back to list</a><p class="empty">Pick 2 or 3 companies with the Compare boxes to compare them.</p>';
  current=null;qn=0;
  var n=cos.length;
  function row(lab,fn){return '<div class="crow" style="--n:'+n+'"><div class="lab">'+esc(lab)+'</div>'+cos.map(fn).join('')+'</div>';}
  function one(f){return function(co){return cell(co,first(co,f)[0],f);};}
  function fin(f){return function(co){return cell(co,latest(co,f),f);};}
  function cnt(f,word){return function(co){var k=first(co,f).length;var st=co.c.filter(function(c){return c.f===f;})[0];
    return '<div>'+(k?k+' '+word+(k>1?'s':''):badge(st?st.a:(co.sec[FSEC[f]]==='available'?'not_available':co.sec[FSEC[f]])))+'</div>';};}
  var h='<a class="btn ghost back" href="#">Back to list</a><h2 style="margin-bottom:12px">Compare '+n+' companies</h2><div class="cmpgrid">'+
    '<div class="crow hd" style="--n:'+n+'"><div class="lab">Company</div>'+cos.map(function(co){return '<div><a href="#org-'+co.o+'">'+esc(co.n)+'</a><br><span class="note">'+co.o+'</span></div>';}).join('')+'</div>'+
    row('Legal form',one('legal_form'))+row('Municipality',one('municipality'))+row('Industry',one('industry'))+row('Employees (registry)',one('employees_registered'))+
    row('Revenue, latest year',fin('revenue'))+row('Annual result, latest year',fin('annual_result'))+row('Equity, latest year',fin('equity'))+
    row('General manager',one('ceo'))+row('Chair',one('board_chair'))+row('Official website',one('official_website'))+
    row('Social profiles',cnt('social_profile','profile'))+row('Job ads',cnt('job_ad','ad'))+row('Registry changes',cnt('registry_change','change'))+
    SECTIONS.map(function(s){return row(SEC_LABEL[s],function(co){return '<div>'+badge(co.sec[s])+'</div>';});}).join('')+'</div>';
  return h;
}

/* ----- routing ----- */
function wide(){return window.matchMedia('(min-width:901px)').matches;}
function route(){
  var hsh=decodeURIComponent(location.hash.slice(1)),main=$('main'),lay=$('layout');
  var m=/^org-(\d{9})$/.exec(hsh),c=/^compare=([\d,]+)$/.exec(hsh);
  if(m){main.innerHTML=profile(m[1]);lay.classList.add('detail');}
  else if(c){var l=c[1].split(',');compare=l.filter(function(o){return BY[o];}).slice(0,3);main.innerHTML=compareView(l);lay.classList.add('detail');drawTray();}
  else{lay.classList.remove('detail');current=null;
    if(wide()&&filtered.length){main.innerHTML=profile(filtered[0].o);current=filtered[0].o;}else main.innerHTML='';}
  drawList();
  if(m||c){if(!wide())window.scrollTo(0,0);else main.scrollTop=0;main.focus({preventScroll:true});}
}
function runHeader(){
  var r=META,rt=r.runtime_ms!=null?(r.runtime_ms>=60000?(r.runtime_ms/60000).toFixed(1)+' min':(r.runtime_ms/1000).toFixed(1)+' s'):'not recorded';
  var items=[['Run',r.run_id||'not recorded'],['Started',r.started_at?date(r.started_at)+' '+String(r.started_at).slice(11,16)+' UTC':'not recorded'],
    ['Companies',num(r.companies)],['Requests',num(r.requests)],['Runtime',rt],['Third-party cost','$'+Number(r.cost_usd||0).toFixed(2)]];
  if(r.agent&&r.agent.version)items.push(['Agent',(r.agent.name||'Pata')+' '+r.agent.version]);
  $('run').innerHTML=items.map(function(x){return '<div><dt>'+esc(x[0])+'</dt><dd>'+(x[0]==='Started'||x[0]==='Companies'||x[0]==='Requests'?x[1]:esc(x[1]))+'</dd></div>';}).join('');
  $('sources').innerHTML='<ul>'+(r.sources||[]).map(function(s){return '<li>'+esc(s.name)+(s.licence?' ('+esc(s.licence)+')':'')+(s.evidence?', '+num(s.evidence)+' evidence records':'')+'</li>';}).join('')+
    '</ul><p>Report generated '+date(r.generated_at)+'. Each value links to the page or API response it was read from; "Quote" shows the exact text used.</p>';
}
initControls();runHeader();applyFilters();drawTray();route();
window.addEventListener('hashchange',route);
window.matchMedia('(min-width:901px)').addEventListener('change',function(){if(!location.hash||location.hash==='#')route();});
})();
"""
