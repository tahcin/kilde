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
    "stotte.brreg.no": ("Støtteregisteret (state aid register), Brønnøysundregistrene", "NLOD 2.0"),
    "sgregister.dibk.no": ("DiBK sentral godkjenning register", "Public register (DiBK), open data"),
    "www.forskningsradet.no": ("Research Council of Norway: SkatteFUNN lists", "NLOD"),
    "forskningsradet.no": ("Research Council of Norway: SkatteFUNN lists", "NLOD"),
    "forskningsradet-open-data": ("Research Council of Norway: funded projects (open data)", "NLOD"),
    "matnyttig.mattilsynet.no": ("Mattilsynet Smilefjes food inspections", "CC BY 4.0"),
    "data.mattilsynet.no": ("Mattilsynet Smilefjes food inspections", "CC BY 4.0"),
    "ted.europa.eu": ("EU TED public procurement notices", "Free reuse (Commission Decision 2011/833/EU)"),
    "api.ted.europa.eu": ("EU TED public procurement notices", "Free reuse (Commission Decision 2011/833/EU)"),
}
LICENCE_LINES = [
    "NLOD 2.0: Brønnøysundregistrene (Enhetsregisteret, Regnskapsregisteret, Støtteregisteret) and Research Council of Norway data.",
    "CC BY 4.0: Mattilsynet Smilefjes inspections and SSB Klass code lists.",
    "Free reuse under Commission Decision 2011/833/EU: EU TED procurement notices.",
    "Public register: DiBK sentral godkjenning.",
    "ODbL: website candidates derived from OpenStreetMap. CDLA-Permissive 2.0: Overture Maps places.",
    "CC0: GLEIF LEI records and Wikidata.",
    "NAV API terms: NAV job ads.",
]


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


PLATFORM_OF = {"linkedin.com": "linkedin", "facebook.com": "facebook", "instagram.com": "instagram",
               "youtube.com": "youtube", "x.com": "x", "twitter.com": "x", "tiktok.com": "tiktok"}


def _display_values(env: dict) -> dict:
    """Envelopes carry Builderr's canonical values (a social profile is 'platform.com/handle'); the page
    wants the platform and a link, so translate here without touching the scored output."""
    claims = []
    for c in env.get("claims") or []:
        v = c.get("value")
        if c.get("field") == "social_profile" and isinstance(v, str):
            host = v.split("/", 1)[0]
            c = {**c, "value": {"platform": PLATFORM_OF.get(host, host.split(".")[0]), "url": "https://" + v, "handle": v}}
        claims.append(c)
    return {**env, "claims": claims}


def build_data(envelopes: list[dict], span_limit: int | None = None) -> dict:
    envelopes = [_display_values(e) for e in envelopes]
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
            url = ev.get("source_url") or ""
            host = (urlsplit(url).hostname or "").lower()
            if host == "raw.githubusercontent.com" and "/forskningsradet/" in url.lower():
                host = "forskningsradet-open-data"
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
            .replace("__SSB__", html.escape(SSB))
            .replace("__LICENCES__", "".join(f"<li>{html.escape(x)}</li>" for x in LICENCE_LINES)))
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
<a class="skip" href="#main">Skip to content</a>
<header class="bar">
  <a class="word" href="#" aria-label="Kilde, all companies"><span class="mark" aria-hidden="true"></span>Kilde</a>
  <nav class="barnav" aria-label="Main">
    <a href="#">All companies</a>
    <a href="#compare" id="nav-cmp" hidden>Compare (<span id="nav-cmp-n">0</span>)</a>
    <a href="#about">About this report</a>
  </nav>
</header>
<main id="main" class="main" tabindex="-1"></main>
<footer class="foot" id="about" aria-labelledby="h-about">
  <div class="footin">
    <h2 id="h-about">About this report</h2>
    <p class="aboutlead">Kilde builds each profile from Norway&#8217;s public registers and the company&#8217;s own website. For every fact it keeps the web address, the time it was read and the exact text, so anyone can check it. Nothing is estimated: missing figures are shown as missing, never as zero.</p>
    <dl class="runinfo" id="runinfo"></dl>
    <h3>Sources and licences</h3>
    <div id="srclist"></div>
    <p>__NLOD__.</p>
    <p>__SSB__</p>
    <h3>Licences</h3>
    <ul class="lic">__LICENCES__</ul>
    <p>Typeface Figtree, SIL Open Font License 1.1.</p>
  </div>
</footer>
<div class="cmpbar" id="cmpbar" hidden></div>
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
  --wrap:1040px;--gut:28px;
  --bg:#f6f4ef;--raise:#fcfbf8;--well:#edeae2;--ink:#1c262b;--ink2:#3a464c;--muted:#5a666c;--faint:#a3aaa9;
  --line:#d9d5ca;--line2:#e6e2d9;--edge:#b9b4a8;--accent:#1f5673;--accent2:#163f55;--on:#ffffff;
  --sel:#e5e9e6;--hover:#efede6;--hl:#f1e2b0;--tag:#e9e6dd;
  --ok:#3b6b4c;--amb:#8a6410;--blk:#6d4b7f;--fail:#a12b3b;--na:#7c868b;
  --scrim:rgba(28,38,43,.28);
}
@media (prefers-color-scheme:dark){:root{
  --bg:#13181b;--raise:#1a2125;--well:#222a2e;--ink:#e6e3dc;--ink2:#c9cdcb;--muted:#9da7ab;--faint:#58636a;
  --line:#2c353a;--line2:#232b30;--edge:#46525a;--accent:#8cc3de;--accent2:#b3d8ea;--on:#0f1a20;
  --sel:#1f2a30;--hover:#192024;--hl:#4b4020;--tag:#242d32;
  --ok:#8dc49f;--amb:#ddb663;--blk:#c4a6d6;--fail:#ef939f;--na:#8b969b;
  --scrim:rgba(0,0,0,.5);
}}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Figtree",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  -webkit-font-smoothing:antialiased;font-kerning:normal}
em,i,cite,dfn,address,var{font-style:normal}
a{color:var(--accent);text-decoration-thickness:1px;text-underline-offset:3px}
a:hover{color:var(--accent2)}
button,input,select{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}
h1,h2,h3{margin:0;line-height:1.25;font-weight:600}
p{margin:0}
.num,table{font-variant-numeric:tabular-nums}
.vh{position:absolute!important;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.skip{position:absolute;left:12px;top:-48px;background:var(--raise);padding:8px 12px;z-index:60;border:1px solid var(--edge)}
.skip:focus{top:12px}
.ic{width:16px;height:16px;flex:none}
.muted{color:var(--muted)}
.sub2{color:var(--muted);font-size:13.5px}
.fine{color:var(--muted);font-size:13.5px;margin-top:12px;max-width:68ch}
.loading{padding:48px 0;color:var(--muted)}

/* top bar */
.mark{display:inline-block;width:22px;height:22px;border-radius:6px;background:var(--accent);box-shadow:inset -7px -7px 0 0 color-mix(in srgb,var(--accent) 38%,#ffffff);margin-right:9px;vertical-align:-4px;flex:none}
.bar{display:flex;align-items:center;gap:10px 28px;flex-wrap:wrap;padding:14px max(var(--gut),calc((100% - var(--wrap))/2));border-bottom:1px solid var(--line)}
.word{font-size:19px;font-weight:700;letter-spacing:-.02em;color:var(--ink);text-decoration:none}
.word:hover{color:var(--ink)}
.barnav{display:flex;gap:4px 22px;margin-left:auto;font-size:14px;flex-wrap:wrap}
.barnav a{color:var(--ink2);text-decoration:none}
.barnav a:hover{color:var(--accent);text-decoration:underline}
.main{max-width:calc(var(--wrap) + 2*var(--gut));margin:0 auto;padding:0 var(--gut) 96px;min-height:70vh}
.main:focus{outline:none}

/* buttons */
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:7px;border:1px solid var(--accent);background:var(--accent);color:var(--on);font-size:14.5px;font-weight:500;text-decoration:none;cursor:pointer;transition:background-color .12s,border-color .12s,color .12s;white-space:nowrap}
.btn:hover{color:var(--on);background:var(--accent2);border-color:var(--accent2)}
.btn.quiet{background:transparent;color:var(--accent);border-color:var(--line)}
.btn.quiet:hover{border-color:var(--accent);color:var(--accent)}
.btn.quiet[aria-pressed=true]{background:var(--sel);border-color:var(--sel);color:var(--ink)}
.btn .ic{width:16px;height:16px}
.tbtn{display:inline-flex;align-items:center;gap:6px;background:none;border:0;padding:4px 0;font-size:14px;color:var(--accent);cursor:pointer}
.tbtn:hover{text-decoration:underline;text-underline-offset:3px}

/* home */
.hero{padding:56px 0 8px}
.hero h1{font-size:34px;line-height:1.18;letter-spacing:-.02em}
.lead{font-size:17px;color:var(--ink2);margin-top:12px;max-width:62ch;line-height:1.55}
.bigsearch{position:relative;margin-top:28px}
.bigsearch .ic{position:absolute;left:16px;top:17px;width:20px;height:20px;color:var(--muted);pointer-events:none}
.bigsearch input{width:100%;height:54px;padding:0 16px 0 48px;border:1px solid var(--edge);border-radius:10px;background:var(--raise);font-size:17px;transition:border-color .12s}
.bigsearch input:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
.controls{display:flex;flex-wrap:wrap;align-items:center;gap:10px 24px;margin-top:16px;font-size:14px}
.cks{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center}
.ck{display:inline-flex;align-items:center;gap:7px;cursor:pointer;color:var(--ink2)}
.ck input{width:16px;height:16px;margin:0;accent-color:var(--accent)}
.sort{display:inline-flex;align-items:center;gap:8px;margin-left:auto}
select{border:1px solid var(--line);border-radius:6px;padding:5px 8px;background:var(--raise);font-size:14px;color:var(--ink);cursor:pointer}
.count{margin:36px 0 10px;color:var(--muted);font-size:14px}
.clist{list-style:none;margin:0;padding:0;border-top:1px solid var(--line)}
.crow{display:grid;grid-template-columns:minmax(0,1fr) 120px 250px 112px;gap:6px 24px;align-items:center;padding:14px 0;border-bottom:1px solid var(--line2);transition:background-color .12s}
.crow:hover{background:var(--hover)}
.cl{display:block;min-width:0;text-decoration:none;color:var(--ink)}
.cl:hover{color:var(--ink)}
.cl:hover .cn{color:var(--accent);text-decoration:underline;text-underline-offset:3px}
.cn{display:block;font-size:16px;font-weight:600;line-height:1.3;overflow-wrap:anywhere}
.cw{display:block;color:var(--muted);font-size:14px;margin-top:2px}
.cr{text-align:right;font-size:15px;font-weight:500;line-height:1.3}
.cr .num{display:block}
.cy{display:block;font-size:12.5px;font-weight:400;color:var(--muted)}
.tags{display:flex;flex-wrap:wrap;gap:5px}
.tag{font-size:12.5px;line-height:1;padding:5px 7px;border-radius:4px;background:var(--tag);color:var(--ink2);white-space:nowrap}
.tag.warn{background:transparent;color:var(--fail);border:1px solid var(--fail);padding:4px 6px}
.addc{justify-self:end;display:inline-flex;align-items:center;gap:5px;background:none;border:1px solid var(--line);border-radius:6px;padding:5px 9px;font-size:13.5px;color:var(--accent);cursor:pointer;white-space:nowrap}
.addc:hover{border-color:var(--accent)}
.addc[aria-pressed=true]{background:var(--sel);border-color:var(--sel);color:var(--ink)}
.addc .ic{width:14px;height:14px}
.empty{padding:28px 4px;color:var(--muted);list-style:none}
.more{margin-top:20px}
.cmpbar{position:fixed;left:0;right:0;bottom:0;z-index:30;background:var(--raise);border-top:1px solid var(--edge)}
.cbin{max-width:calc(var(--wrap) + 2*var(--gut));margin:0 auto;padding:12px var(--gut);display:flex;align-items:center;gap:10px 18px;flex-wrap:wrap;font-size:14.5px}
.cbin .msg{flex:1;min-width:200px}
.ccount{display:none}

/* company page */
.back{display:inline-flex;align-items:center;gap:4px;margin:24px 0 0 -4px;padding:4px;font-size:14.5px;color:var(--accent);text-decoration:none}
.back:hover{text-decoration:underline}
.co{max-width:none}
.cohead{display:flex;flex-wrap:wrap;align-items:flex-start;justify-content:space-between;gap:14px 24px;margin-top:14px}
.cohead h1{font-size:34px;line-height:1.15;letter-spacing:-.02em;overflow-wrap:anywhere}
.cosub{color:var(--ink2);font-size:16px;margin-top:8px}
.alert{display:flex;gap:10px;align-items:flex-start;margin-top:20px;padding:12px 14px;border:1px solid var(--fail);border-radius:8px;color:var(--ink)}
.alert .ic{color:var(--fail);margin-top:3px}
.intro{margin-top:24px}
.sumtext{font-size:17px;line-height:1.65;max-width:68ch}
.sumtext p+p{margin-top:12px}
.sumtext .cites{display:none}
.sumtext.showsrc .cites{display:inline}
.cites{font-size:11px;line-height:0;margin-left:2px;color:var(--muted);white-space:nowrap;vertical-align:super}
.cite{background:none;border:0;padding:2px 1px;font:600 11px/1 inherit;color:var(--accent);cursor:pointer;font-variant-numeric:tabular-nums}
.cite:hover{text-decoration:underline}
.sumctl{display:flex;flex-wrap:wrap;gap:4px 20px;margin-top:8px}
.sumctl .tbtn{font-size:14px}
.ask{margin-top:26px}
.askrow{display:flex;gap:8px;max-width:640px}
.askrow input{flex:1;min-width:0;height:44px;padding:0 14px;border:1px solid var(--edge);border-radius:8px;background:var(--raise);font-size:16px}
.askrow input:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
.askrow .btn{height:44px}
.sugg{display:flex;flex-wrap:wrap;gap:4px 16px;margin-top:10px;font-size:14px;align-items:baseline}
.sugg button{background:none;border:0;padding:2px 0;color:var(--accent);cursor:pointer;text-align:left}
.sugg button:hover{text-decoration:underline;text-underline-offset:3px}
.answer{margin-top:14px;max-width:640px;padding:14px 16px;border:1px solid var(--line);border-radius:8px;background:var(--raise);font-size:15.5px;line-height:1.55}
.answer .q{color:var(--muted);font-size:13.5px;margin-bottom:6px}
.answer p+p{margin-top:6px}
.cards{display:grid;gap:16px;margin-top:32px}
.card{background:var(--raise);border:1px solid var(--line);border-radius:10px;padding:20px 22px 18px;min-width:0;scroll-margin-top:16px}
.ch{display:flex;align-items:baseline;justify-content:space-between;gap:12px}
.card h2{font-size:19px;letter-spacing:-.01em}
.ans{margin-top:8px;font-size:16.5px;line-height:1.55;max-width:66ch}
.ans p+p{margin-top:8px}
.ans .quote{color:var(--ink2);font-size:15.5px}
.srcl{background:none;border:0;padding:2px 0;font-size:13px;color:var(--muted);cursor:pointer;white-space:nowrap;text-decoration:underline;text-decoration-color:var(--line);text-underline-offset:3px}
.srcl:hover{color:var(--accent);text-decoration-color:currentColor}
.ans .srcl{margin-left:4px}
.miss{color:var(--ink2)}
.whyb{background:none;border:0;padding:0 2px;cursor:pointer;color:var(--muted);font-size:13.5px;text-decoration:underline;text-decoration-color:var(--edge);text-underline-offset:3px}
.whyb:hover,.whyb[aria-expanded=true]{color:var(--accent)}
.whyt{display:block;color:var(--muted);font-size:14px;margin:4px 0 2px;max-width:62ch}
dl.det{margin:16px 0 0;border-top:1px solid var(--line2)}
.dr{display:grid;grid-template-columns:200px minmax(0,1fr) auto;gap:2px 18px;padding:9px 0;border-bottom:1px solid var(--line2);font-size:15px}
.dr:last-child{border-bottom:0}
.dr dt{color:var(--muted);font-size:14px;padding-top:1px}
.dr dd{margin:0;min-width:0;overflow-wrap:anywhere}
.dr .ds{text-align:right}
.yl{display:flex;flex-wrap:wrap;gap:2px 12px}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:12px 28px;margin-top:18px}
.chart{margin:0;min-width:0}
.chart figcaption{font-size:14px;font-weight:600;margin-bottom:4px}
.chart svg{display:block;width:100%;height:auto;overflow:visible}
.bpos{fill:var(--accent)}
.bneg{fill:var(--fail)}
.base{stroke:var(--edge);stroke-width:1}
.bl{font-size:12px;fill:var(--ink2);font-variant-numeric:tabular-nums;font-weight:500}
.bx{font-size:12px;fill:var(--muted)}
details.more{margin-top:14px}
details.more>summary,details.allfacts>summary{cursor:pointer;color:var(--accent);font-size:14.5px;width:max-content;max-width:100%}
details.more>summary:hover{text-decoration:underline;text-underline-offset:3px}
details.more[open]>summary{margin-bottom:12px}
details.allfacts{margin-top:40px;border-top:1px solid var(--line);padding-top:20px}
details.allfacts>summary{display:block;width:auto;list-style:none}
details.allfacts>summary::-webkit-details-marker{display:none}
.st1{display:block;font-size:19px;font-weight:600;color:var(--ink)}
.st1::after{content:" +";color:var(--muted);font-weight:400}
details.allfacts[open] .st1::after{content:" \2212"}
.st2{display:block;font-size:14px;color:var(--muted);margin-top:2px}
.afbody{margin-top:8px}

/* all facts (technical) */
.mk{width:9px;height:9px;flex:none;vertical-align:-1px}
.st-available{color:var(--ok)}.st-not_available{color:var(--na)}.st-ambiguous{color:var(--amb)}
.st-blocked{color:var(--blk)}.st-failed{color:var(--fail)}.st-not_applicable{color:var(--na)}
.badge{display:inline-flex;align-items:center;gap:6px;font-weight:500;margin-right:10px}
.badge .w{color:var(--ink2)}
.sec{padding-top:28px;scroll-margin-top:16px}
.sh{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;margin-bottom:10px}
.sh h3{font-size:16.5px}
.sh .badge{font-size:13.5px}
dl.facts{margin:0}
.fr,.it{display:grid;grid-template-columns:190px minmax(0,1fr) auto;gap:2px 20px;padding:8px 0;border-top:1px solid var(--line2);font-size:14.5px;transition:background-color .6s}
.fr dt,.it .k{color:var(--muted);font-size:13.5px;padding-top:1px}
.it .k.num{color:var(--ink2)}
.fr dd{margin:0;min-width:0;overflow-wrap:anywhere}
.it .v{min-width:0;overflow-wrap:anywhere}
.fr dd.sc,.it .sc{text-align:right}
.grp{font-size:14.5px;margin:18px 0 4px}
.note{display:block;color:var(--muted);font-size:13.5px;margin-top:2px}
.items{list-style:none;margin:0;padding:0}
.t{font-weight:500}
button.src{background:none;border:0;padding:1px 0;font-size:12.5px;color:var(--muted);cursor:pointer;white-space:nowrap;text-align:right}
button.src:hover{color:var(--accent);text-decoration:underline;text-underline-offset:3px}
button.vbtn{background:none;border:0;padding:0;text-align:left;cursor:pointer;color:inherit;font:inherit;text-decoration:underline;text-decoration-color:var(--line);text-underline-offset:4px;text-decoration-thickness:1px}
button.vbtn:hover{text-decoration-color:var(--accent)}
.flash{background:var(--hl)!important;transition:none}
.matchp{max-width:68ch;font-size:14.5px}
.matchp p+p{margin-top:8px}
.errs{margin-top:20px;color:var(--muted);font-size:13.5px}
table{border-collapse:collapse;width:100%}
.tcap{display:flex;flex-wrap:wrap;gap:2px 12px;align-items:baseline;margin:0 0 6px}
.tcap h3{font-size:15px}
.tcap p{color:var(--muted);font-size:13.5px}
.fin{width:auto;min-width:min(100%,480px)}
.fin td,.fin thead th:not(:first-child){min-width:120px}
.fin th[scope=row]{min-width:170px}
.fin th,.fin td{padding:7px 0 7px 18px;text-align:right;vertical-align:baseline;border-top:1px solid var(--line2);font-size:14.5px}
.fin th[scope=row]{padding-left:0;text-align:left;font-weight:400;color:var(--muted);font-size:14px}
.fin thead th{border-top:0;border-bottom:1px solid var(--line);font-weight:600;padding-top:0}
.fin thead th:first-child{padding-left:0}
.fin .per{display:block;font-weight:400;font-size:12px;color:var(--muted)}
.fin tr.brk th,.fin tr.brk td{padding-top:18px}
.fin tfoot td,.fin tfoot th{border-top:1px solid var(--line);padding-top:8px}
.nr{color:var(--muted);font-size:13px}
.tablewrap+.tablewrap{margin-top:24px}
.facts.after{margin-top:18px}
.srct{max-width:760px;margin:6px 0 14px;font-size:14px}
.srct th,.srct td{text-align:left;padding:6px 16px 6px 0;border-top:1px solid var(--line2);vertical-align:top}
.srct thead th{border-top:0;font-weight:600;color:var(--ink)}
.srct td.r,.srct th.r{text-align:right;padding-right:0}
.srcs{margin-top:16px;max-width:620px}

/* compare */
.cmpv h1{font-size:30px;letter-spacing:-.02em;margin-top:14px}
.cmpv .lead{margin-bottom:24px}
.cmp{table-layout:fixed}
.cmp .c0{width:200px}
.cmp th,.cmp td{padding:10px 18px 10px 0;text-align:left;vertical-align:top;border-top:1px solid var(--line2);overflow-wrap:anywhere;font-size:15px}
.cmp thead th{border-top:0;padding-bottom:12px;font-weight:600;vertical-align:bottom}
.cmp thead th a{color:var(--ink);text-decoration:none;font-size:17px;display:block;line-height:1.3}
.cmp thead th a:hover{color:var(--accent);text-decoration:underline}
.cmp thead .sub2{display:flex;gap:6px 10px;align-items:center;font-weight:400}
.cmp .num{white-space:nowrap}
.cmp thead .rm{background:none;border:0;padding:2px;color:var(--muted);cursor:pointer;line-height:0}
.cmp thead .rm:hover{color:var(--fail)}
.cmp th[scope=row]{font-weight:400;color:var(--muted);font-size:14px}
.cmp tr.g th{border-top:0;padding-top:28px;color:var(--ink);font-weight:600;font-size:16px}
.cmp .yr{display:block;color:var(--muted);font-size:12.5px}

/* popover */
.pop{position:absolute;z-index:50;width:440px;max-width:calc(100vw - 24px);max-height:min(72vh,600px);overflow:auto;
  background:var(--raise);border:1px solid var(--edge);border-radius:10px;padding:14px 16px 16px;font-size:14px;
  opacity:0;transform:translateY(-3px);transition:opacity .12s ease-out,transform .12s ease-out}
.pop.open{opacity:1;transform:none}
.pop:focus{outline:none}
.pop-h{display:flex;align-items:flex-start;gap:12px;margin-bottom:6px}
.pop-h strong{flex:1;font-weight:600;font-size:15px}
.pop .x{background:none;border:0;padding:2px;margin:-2px -4px 0 0;color:var(--muted);cursor:pointer;line-height:0}
.pop .x:hover{color:var(--ink)}
.pop-v{color:var(--ink);overflow-wrap:anywhere}
.pop-c{padding-top:12px;margin-top:12px;border-top:1px solid var(--line)}
.pop-c:first-of-type{border-top:0;margin-top:0;padding-top:4px}
.pop-cl{font-weight:600;margin-bottom:2px}
.pop-e{margin-top:10px;padding-top:10px;border-top:1px solid var(--line2)}
.pop-e a.u{display:inline-flex;gap:5px;align-items:baseline;word-break:break-all}
.pop-e a.u .ic{width:13px;height:13px;flex:none;align-self:center}
.pop-m{color:var(--muted);font-size:13px;margin-top:3px}
.pop-match{margin-top:10px}
.pop-ql{color:var(--muted);font-size:13px;margin-top:10px}
pre.q{margin:4px 0 0;padding:8px 10px;background:var(--well);border-radius:4px;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:200px;overflow:auto;color:var(--ink)}
pre.q mark{background:var(--hl);color:var(--ink);border-radius:2px}
.scrim{position:fixed;inset:0;z-index:40;background:var(--scrim)}
.pop.sheet{position:fixed;left:0;right:0;bottom:0;top:auto!important;width:auto;max-width:none;max-height:80vh;border-radius:12px 12px 0 0;border-bottom:0;padding:16px 16px 24px;transform:translateY(8px)}
.pop.sheet.open{transform:none}

/* footer */
.foot{border-top:1px solid var(--line);margin-top:24px;color:var(--ink2);font-size:14px}
.footin{max-width:calc(var(--wrap) + 2*var(--gut));margin:0 auto;padding:32px var(--gut) 120px}
.foot h2{font-size:19px;color:var(--ink);margin-bottom:8px}
.foot h3{font-size:15px;color:var(--ink);margin-top:24px}
.foot p{max-width:80ch;margin-top:8px}
.aboutlead{font-size:15px}
.lic{margin:8px 0 0;padding-left:18px;max-width:80ch}
.lic li{margin-top:4px}
.runinfo{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px 24px;margin:18px 0 0;max-width:900px}
.runinfo dt{color:var(--muted);font-size:13px}
.runinfo dd{margin:2px 0 0;font-weight:500;font-variant-numeric:tabular-nums}

@media (max-width:900px){
  .crow{grid-template-columns:minmax(0,1fr) auto;gap:8px 12px}
  .cl{grid-column:1}
  .addc{grid-column:2;grid-row:1;align-self:start}
  .cr{grid-column:1;grid-row:2;text-align:left;display:flex;gap:8px;align-items:baseline}
  .cr .num{display:inline}
  .cy{display:inline}
  .tags{grid-column:1/-1;grid-row:3}
  .tags:empty{display:none}
  .sort{margin-left:0}
}
@media (max-width:640px){
:root{--gut:16px}
  .bar{gap:8px 16px}
  .barnav{gap:4px 14px;font-size:13.5px;margin-left:0;width:100%}
  .main{padding-bottom:96px}
  .hero{padding-top:28px}
  .hero h1{font-size:25px}
  .lead{font-size:15.5px}
  .bigsearch input{font-size:16px;height:50px}
  .bigsearch .ic{top:15px}
  .controls{gap:10px 16px}
  .count{margin-top:24px}
  .cohead h1{font-size:26px}
  .cosub{font-size:15px}
  .sumtext{font-size:16px}
  .card{padding:16px 16px 14px}
  .card h2{font-size:18px}
  .ans{font-size:16px}
  .dr,.fr,.it{grid-template-columns:minmax(0,1fr);gap:1px}
  .dr dt,.fr dt,.it .k{font-size:13px}
  .dr .ds,.fr dd.sc,.it .sc{text-align:left}
  button.src{text-align:left;white-space:normal}
  .charts{grid-template-columns:1fr}
  .askrow .btn{padding:8px 12px}
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
  .fin tfoot tr{border-top:1px solid var(--line);padding-top:6px}
  .fin.many td::before{content:attr(data-y);display:block;font-size:11.5px;color:var(--muted)}
  .cmpv h1{font-size:24px}
  .cmp,.cmp thead,.cmp tbody{display:block}
  .cmp colgroup{display:none}
  .cmp thead{position:sticky;top:0;z-index:2;background:var(--bg)}
  .cmp tr{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));column-gap:12px;border-top:1px solid var(--line2);padding:7px 0}
  .cmp thead tr{border-top:0;border-bottom:1px solid var(--line)}
  .cmp thead th:first-child{display:none}
  .cmp th,.cmp td{display:block;border:0;padding:0 0 2px;font-size:14px}
  .cmp thead th a{font-size:14px}
  .cmp thead .sub2{font-size:12px}
  .cmp th[scope=row]{grid-column:1/-1;font-size:12.5px}
  .cmp tr.g{border-top:0;padding-top:18px}
  .cmp tr.g th{padding-top:0;grid-column:1/-1}
  .cbin{font-size:14px;flex-wrap:nowrap}
  .cbin .msg{min-width:0}
  .cnames{display:none}
  .ccount{display:inline}
  .footin{padding-top:28px}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{transition:none!important;animation:none!important;scroll-behavior:auto!important}
}
@media print{.bar,.back,.skip,.ask,.pop,.scrim,.cmpbar,.addc{display:none!important}}
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
 public_contract_award:'Public contract won',procurement_notice:'Tender notice (as buyer)',research_project:'Research project',food_inspection:'Food safety inspection',
 approval_sentral_godkjenning:'Approved for building work (DiBK)',state_aid_award:'State aid award',
 job_ad:'Job ads',careers_page:'Careers page',hiring_signal:'Careers page or job posting',dated_news:'Dated news post',official_website_url:'Website address',registry_change:'Registry update',site_page_updated:'Website page updated',news:'News',news_item:'News'};
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
 plus:SVG+'<path d="M5 12h14"/><path d="M12 5v14"/></svg>',check:SVG+'<path d="M20 6 9 17l-5-5"/></svg>',
 search:SVG+'<path d="m21 21-4.3-4.3"/><circle cx="11" cy="11" r="8"/></svg>',
 alert:SVG+'<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>'};
var PAGE=60, shown=PAGE, filtered=[], compare=[], current=null;

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
function isDomain(u){return /^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(\/\S*)?$/i.test(String(u||''));}
function link(u,text){var s=safeUrl(u)||(isDomain(u)?'https://'+u:null);return s?'<a href="'+esc(s)+'" target="_blank" rel="noopener noreferrer">'+esc(text||bare(u))+'</a>':esc(text||u);}
function money(v,withCur){if(!v||v.amount==null)return '';return '<span class="num">'+(withCur&&v.currency?esc(v.currency)+'\u00a0':'')+nfmt(v.amount)+'</span>';}
var LANG={nb:'Norwegian Bokm\u00e5l',nn:'Norwegian Nynorsk',no:'Norwegian',en:'English',sv:'Swedish',da:'Danish',de:'German',fi:'Finnish'};
var CHG={updated:'Updated',ukjent:'Update',endring:'Updated',ny:'New entry',new:'New entry',sletting:'Deleted',deleted:'Deleted'};
function lfName(v){v=v||{};return LF[v.code]||(/underenhet/i.test(v.name||'')?'A registered workplace (sub-unit)':v.name||'');}
function chgUnknown(v){return !v||!v.changed||!v.changed.length||v.changed.every(function(x){return /^unspecified/i.test(String(x));});}
function endStop(t){t=String(t||'').trim();return /[.!?\u2026"\u201d)]$/.test(t)?t:t+'.';}
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
    if(safeUrl(v)||(/website|careers|hiring_signal/.test(f)&&isDomain(v)))return link(v);
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
  if(f==='public_contract_award'||f==='procurement_notice'){var bits=[v.buyer&&f==='public_contract_award'?'Buyer: '+esc(v.buyer):'',v.notice_type?esc(cap(v.notice_type)):'',
      v.notice_total_value?'notice value '+esc(v.currency||'')+' '+words(v.notice_total_value):''].filter(Boolean);
    return '<span class="t">'+link(v.url,v.title||('TED notice '+(v.notice||'')))+'</span>'+(bits.length?'<span class="note">'+bits.join(', ')+'</span>':'');}
  if(f==='research_project')return '<span class="t">'+esc(v.title||'Untitled project')+'</span><span class="note">'+[esc(v.scheme||''),v.period?esc(v.period):'',
      v.awarded_nok!=null?'NOK '+words(v.awarded_nok)+' awarded':'',v.project_number?'project '+esc(v.project_number):''].filter(Boolean).join(', ')+'</span>';
  if(f==='food_inspection')return '<span class="t">'+esc(cap(v.result||('Grade '+v.grade)))+'</span>'+(v.url?' '+link(v.url,'Mattilsynet'):'')+'<span class="note">'+
      [esc(v.place||''),esc(v.address||''),v.inspections_on_file?plural(v.inspections_on_file,'inspection')+' on file':''].filter(Boolean).join(', ')+'</span>';
  if(f==='approval_sentral_godkjenning')return '<span class="t">'+(v.status==='approved'?'Approved':esc(cap(v.status||'')))+(v.valid_to?' until '+dfmt(v.valid_to):'')+'</span>'+
      (v.areas&&v.areas.length?'<span class="note">'+v.areas.map(function(x){return esc(x.area||'')+(x.class?' (class '+esc(x.class)+')':'');}).join('; ')+'</span>':'');
  if(f==='state_aid_award'){var am=v.amount!=null?v.amount:v.amount_nok;return '<span class="t">'+(am!=null?esc(v.currency||'NOK')+' '+words(am):'State aid')+'</span><span class="note">'+
      [esc(v.authority||v.granting_authority||''),esc(v.purpose||v.title||v.instrument||''),v.date?dfmt(v.date):''].filter(Boolean).join(', ')+'</span>';}
  if(f==='job_ad')return '<span class="t">'+(v.url?link(v.url,v.title||'Job ad'):esc(v.title||'Job ad'))+'</span><span class="note">'+
     [v.location,v.published?'published '+dfmt(v.published):'',v.expires?'closes '+dfmt(v.expires):''].filter(Boolean).map(esc).join(', ')+'</span>';
  if(f==='registered_workplace')return '<span class="t">'+esc(v.name||'')+'</span>'+
     '<span class="note">'+[esc(v.address||''),v.municipality&&(v.address||'').toUpperCase().indexOf(v.municipality)<0?esc(tc(v.municipality)):'',v.industry?esc(v.industry):'',
       v.employees!=null?plural(v.employees,'employee'):'',v.start_date?'since '+dfmt(v.start_date):''].filter(Boolean).join('<br>')+'</span>';
  if(v.name&&(v.role||v.role_code))return '<span class="t">'+esc(v.name)+'</span>'+(v.organisation_number?' <span class="sub2 num">'+orgf(v.organisation_number)+'</span>':'');
  if(f==='registry_change'&&chgUnknown(v))return 'Registry update (details not published)';
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

/* ----- plain language ----- */
var IND={'00.000':'Industry not stated','01.130':'Vegetable farming','03.110':'Sea fishing','16.110':'Sawmill','33.120':'Machinery repair','35.122':'Wind power','35.210':'Gas production',
 '41.000':'Building construction','43.120':'Groundwork and site preparation','43.210':'Electrical installation','43.221':'Plumbing','43.222':'Heating and ventilation installation',
 '43.320':'Carpentry','43.340':'Painting and glazing','43.910':'Masonry','43.990':'Specialised construction',
 '46.180':'Sales agent','46.710':'Vehicle wholesale','46.720':'Vehicle parts wholesale','47.110':'Grocery store','47.300':'Fuel station','47.540':'Electrical appliance shop',
 '47.810':'Car dealer','49.330':'Taxi and passenger transport','49.410':'Road freight','53.200':'Courier and delivery','55.101':'Hotel','56.101':'Restaurant','56.110':'Restaurant',
 '62.010':'Software development','62.100':'Software development','62.020':'IT consulting','62.200':'IT consulting and operations','64.202':'Holding company','64.323':'Investment company',
 '68.100':'Buying and selling own property','68.110':'Buying and selling own property','68.120':'Property development','68.200':'Property rental','68.209':'Property rental',
 '69.100':'Legal services','69.201':'Accounting','70.100':'Head office','70.200':'Management consulting','71.112':'Architecture','71.129':'Technical consulting',
 '73.110':'Advertising agency','74.120':'Graphic design','75.000':'Veterinary services','77.320':'Construction machinery rental','79.121':'Excursions and events',
 '79.122':'Tour operator','81.100':'Facility management','81.210':'Cleaning','81.230':'Cleaning and snow clearing','85.201':'Primary school','85.531':'Driving school',
 '85.597':'Education and training','86.210':'General practitioner (doctor)','90.390':'Arts and entertainment services','94.991':'Charitable foundation',
 '95.310':'Car repair and servicing','97.001':'Housing cooperative or co-owned building'};
var DIV={'01':'Farming','02':'Forestry','03':'Fishing and aquaculture','06':'Oil and gas','09':'Oil and gas services','10':'Food production','11':'Drinks production',
 '16':'Wood products','18':'Printing','20':'Chemicals','22':'Rubber and plastics','23':'Building materials','25':'Metal products','26':'Electronics','27':'Electrical equipment',
 '28':'Machinery','29':'Vehicles','30':'Ships and transport equipment','31':'Furniture','32':'Manufacturing','33':'Machinery repair and installation','35':'Energy',
 '36':'Water supply','37':'Sewage','38':'Waste and recycling','41':'Building construction','42':'Civil engineering','43':'Construction trades','45':'Vehicle trade and repair',
 '46':'Wholesale','47':'Retail','49':'Land transport','50':'Shipping','51':'Aviation','52':'Warehousing and logistics','53':'Post and courier','55':'Accommodation',
 '56':'Restaurants and catering','58':'Publishing','59':'Film and music','60':'Broadcasting','61':'Telecoms','62':'Software and IT services','63':'Information services',
 '64':'Financial services','65':'Insurance','66':'Financial support services','68':'Real estate','69':'Legal and accounting','70':'Management consulting',
 '71':'Architecture and engineering','72':'Research','73':'Advertising and market research','74':'Design and other professional services','75':'Veterinary services',
 '77':'Rental and leasing','78':'Recruitment and staffing','79':'Travel','80':'Security','81':'Building services and cleaning','82':'Office and business support',
 '84':'Public administration','85':'Education','86':'Health care','87':'Residential care','88':'Social services','90':'Arts and entertainment','91':'Libraries and museums',
 '92':'Gambling','93':'Sports and recreation','94':'Associations and foundations','95':'Repair services','96':'Personal services','97':'Housing associations'};
var LF={AS:'Private limited company',ASA:'Public limited company',ENK:'Sole proprietorship',ANS:'General partnership',DA:'Partnership with shared liability',
 NUF:'Norwegian branch of a foreign company',SA:'Cooperative',BA:'Company with limited liability',STI:'Foundation',FLI:'Association',ESEK:'Co-owned residential building',
 BRL:'Housing cooperative',KS:'Limited partnership',IKS:'Inter-municipal company',SAM:'Co-ownership',ORGL:'Organisation',KF:'Municipal enterprise',PK:'Pension fund',
 SPA:'Savings bank',BEDR:'A registered workplace (sub-unit)',AAFY:'A registered workplace (sub-unit)',PRE:'Shipping partnership',GFS:'Mutual insurance company',KOMM:'Municipality',STAT:'State body'};
var SIG={'styrets leder alene':'the board chair alone','daglig leder alene':'the general manager alone','to styremedlemmer i fellesskap':'any two board members together',
 'to av styrets medlemmer i fellesskap':'any two board members together','styrets medlemmer hver for seg':'each board member alone',
 'styrets leder og ett styremedlem i fellesskap':'the board chair together with one board member','daglig leder og styrets leder i fellesskap':'the general manager and the board chair together',
 'daglig leder eller styrets leder hver for seg':'the general manager or the board chair, each alone','styret i fellesskap':'the full board together',
 'deltakerene hver for seg':'each partner alone','deltakerne hver for seg':'each partner alone','innehaver':'the owner','styrets leder og nestleder i fellesskap':'the board chair and deputy chair together'};
var KEEPU={AS:1,ASA:1,ANS:1,DA:1,SA:1,BA:1,NUF:1,ENK:1,KS:1,IKS:1,STI:1,LTD:1,AB:1,BRL:1,ESEK:1,II:1,III:1,IT:1,TV:1};
function dn(n){n=String(n||'');if(!n||n!==n.toUpperCase())return n;var first=true;
  return n.split(/([\s.\-\/&]+)/).map(function(w){if(!w||/^[\s.\-\/&]+$/.test(w))return w;var u=w.toUpperCase(),out;
    if(KEEPU[u]||/\d/.test(w)||(first&&w.length<=3)||(w.length<=2&&!/^(OG|AV|I|PÅ|DE|EN|ET|TIL)$/.test(u)))out=u;else out=u.charAt(0)+u.slice(1).toLowerCase();first=false;return out;}).join('');}
function what(co){var c=one(co,'industry');if(!c||!c.v)return '';var code=String(c.v.code||'');
  if(Object.prototype.hasOwnProperty.call(IND,code))return IND[code];return DIV[code.slice(0,2)]||cap(String(c.v.name||'').toLowerCase());}
function signText(v){var parts=String(v||'').split(/\.\s*/).filter(Boolean),out=[],ok=true;
  parts.forEach(function(p){var t=SIG[p.trim().toLowerCase()];if(t)out.push(t);else ok=false;});
  return ok&&out.length?cap(out.join(', or ')):'';}
function series(co,f){var xs=accounts(co).filter(function(c){return c.f===f&&c.v.amount!=null;});xs.sort(function(a,b){return String(a.v.year).localeCompare(String(b.v.year));});return xs;}
function short(x){var a=Math.abs(Number(x)),s;if(a>=1e9)s=(a/1e9).toFixed(1).replace(/\.0$/,'')+'B';else if(a>=1e6)s=(a/1e6).toFixed(1).replace(/\.0$/,'')+'M';
  else if(a>=1e3)s=Math.round(a/1e3)+'k';else s=String(Math.round(a));return (x<0?'−':'')+s;}
function words(x){var a=Math.abs(Number(x));if(a>=1e9)return (a/1e9).toFixed(1).replace(/\.0$/,'')+' billion';if(a>=1e6)return (a/1e6).toFixed(1).replace(/\.0$/,'')+' million';
  if(a>=1e4)return Math.round(a/1e3)+' thousand';return nfmt(a);}
var GEN=Date.parse(META.generated_at||'')||Date.now();
function daysAgo(d){var t=Date.parse(d);return isFinite(t)?(GEN-t)/864e5:1e9;}
function regChanges(co){return av(co,'registry_change').slice().sort(function(a,b){return (b.d||'').localeCompare(a.d||'');});}
function recent(co){var r=regChanges(co)[0];return co.ch.length>0||!!(r&&r.d&&daysAgo(r.d)<=92);}
function statusWarn(co){var s=one(co,'legal_status');if(!s||!s.v||typeof s.v!=='object')return '';var v=s.v;
  return v.konkurs?'Bankrupt':v.underTvangsavviklingEllerTvangsopplosning?'Being forcibly dissolved':v.underAvvikling?'Being wound up':'';}
function tags(co){var t=[],w=statusWarn(co);if(w)t.push('<span class="tag warn">'+w+'</span>');if(co.fl.web)t.push('<span class="tag">Website</span>');
  if(av(co,'job_ad').length)t.push('<span class="tag">Hiring</span>');if(recent(co))t.push('<span class="tag">Changed recently</span>');
  if(av(co,'dated_news').length)t.push('<span class="tag">News</span>');if(av(co,'public_contract_award').length)t.push('<span class="tag">Public contracts</span>');return t.join('');}
function empText(v){if(typeof v==='number')return nfmt(v);var s=String(v||'');return /fewer than 5/i.test(s)?'Fewer than 5':cap(s.replace(/\s*\(.*\)$/,''));}
function srcLink(co,cls){cls=(cls||[]).filter(function(c){return c&&c.e&&c.e.length;});if(!cls.length)return '';
  return '<button type="button" class="srcl" data-o="'+co.o+'" data-c="'+cls.map(function(c){return esc(c.i);}).join(',')+'" aria-haspopup="dialog">Source</button>';}
var whyN=0;
function why(n){if(!n)return '';var id='why'+(++whyN);return ' <button type="button" class="whyb" aria-expanded="false" aria-controls="'+id+'">Why?</button><span class="whyt" id="'+id+'" hidden>'+esc(cap(n))+'</span>';}

/* ----- home ----- */
var UI={q:'',web:false,hire:false,chg:false,sort:'name'},homeY=0;
function chk(id,text,on){return '<label class="ck"><input type="checkbox" id="'+id+'"'+(on?' checked':'')+'> '+text+'</label>';}
function home(){
  var checks=(CO.some(function(c){return c.fl.web;})?chk('f-web','With a website',UI.web):'')+(CO.some(function(c){return av(c,'job_ad').length;})?chk('f-hire','Hiring',UI.hire):'')+
    (CO.some(recent)?chk('f-chg','Changed in the last 3 months',UI.chg):'');
  return '<section class="hero" aria-labelledby="h-home"><h1 id="h-home">Profiles of '+plural(CO.length,'Norwegian company','Norwegian companies')+', built from public sources.</h1>'+
    '<p class="lead">Every fact links to where it came from. Open a company to see what it does, who runs it, how it is doing and what changed.</p>'+
    '<div class="bigsearch" role="search">'+IC.search+'<label class="vh" for="q">Search companies</label><input id="q" type="search" placeholder="Search name, town or industry" autocomplete="off" spellcheck="false" value="'+esc(UI.q)+'"></div>'+
    '<div class="controls"><div class="cks" role="group" aria-label="Show only">'+(checks?'<span class="muted">Show only</span>':'')+
      checks+'</div>'+
    '<label class="sort"><span class="muted">Sort by</span><select id="sort">'+[['name','Name'],['rev','Revenue, highest first'],['chg','Latest change first']].map(function(o){
      return '<option value="'+o[0]+'"'+(UI.sort===o[0]?' selected':'')+'>'+o[1]+'</option>';}).join('')+'</select></label></div></section>'+
    '<p class="count" id="count" aria-live="polite"></p><ul class="clist" id="list"></ul>'+
    '<button type="button" class="btn quiet more" id="more" hidden>Show more companies</button>';
}
function bindHome(){
  $('q').addEventListener('input',function(){UI.q=this.value;shown=PAGE;applyFilters();});
  [['f-web','web'],['f-hire','hire'],['f-chg','chg']].forEach(function(p){if($(p[0]))$(p[0]).addEventListener('change',function(){UI[p[1]]=this.checked;shown=PAGE;applyFilters();});});
  $('sort').addEventListener('change',function(){UI.sort=this.value;applyFilters();});
  $('more').addEventListener('click',function(){shown+=PAGE;drawList();});
}
function hay(c){if(!c._q){var ind=one(c,'industry');c._q=[c.n,dn(c.n),muni(c),what(c),ind&&ind.v?ind.v.name:'',c.o].join(' ').toLowerCase();}return c._q;}
function applyFilters(){
  var q=UI.q.trim().toLowerCase(),qd=q.replace(/\s/g,'');
  filtered=CO.filter(function(c){
    if(q&&hay(c).indexOf(q)<0&&!(/^\d+$/.test(qd)&&c.o.indexOf(qd)>=0))return false;
    if(UI.web&&!c.fl.web)return false;if(UI.hire&&!av(c,'job_ad').length)return false;if(UI.chg&&!recent(c))return false;return true;});
  filtered.sort(function(a,b){
    if(UI.sort==='rev'){var x=a.rev==null?-Infinity:a.rev,y=b.rev==null?-Infinity:b.rev;if(x!==y)return y-x;}
    if(UI.sort==='chg'){var p=(regChanges(a)[0]||{}).d||'',r=(regChanges(b)[0]||{}).d||'';if(p!==r)return r.localeCompare(p);}
    return dn(a.n).localeCompare(dn(b.n),'nb');});
  drawList();
}
function addBtn(c){var on=compare.indexOf(c.o)>=0;
  return '<button type="button" class="addc" data-add="'+c.o+'" aria-pressed="'+on+'" aria-label="'+(on?'Remove ':'Add ')+esc(dn(c.n))+(on?' from':' to')+' compare">'+(on?IC.check+'<span>Added</span>':IC.plus+'<span>Compare</span>')+'</button>';}
function rowHtml(c){var r=latestFig(c,'revenue'),w=what(c),t=muni(c);
  return '<li class="crow"><a class="cl" href="#org-'+c.o+'"><span class="cn">'+esc(dn(c.n||c.o))+'</span><span class="cw">'+esc([w,t].filter(Boolean).join(' · ')||('Org. no. '+orgf(c.o)))+'</span></a>'+
    '<span class="cr">'+(r?'<span class="num">'+short(r.v.amount)+' NOK</span><span class="cy">revenue '+esc(r.v.year)+'</span>':'<span class="cy">'+(any(c,'revenue').some(function(x){return x.a==='failed';})?'Accounts not checked':'No revenue filed')+'</span>')+'</span>'+
    '<span class="tags">'+tags(c)+'</span>'+addBtn(c)+'</li>';}
function drawList(){
  var list=$('list');if(!list)return;
  list.innerHTML=filtered.slice(0,shown).map(rowHtml).join('')||'<li class="empty">No companies match. Try a shorter search or clear the filters.</li>';
  $('count').textContent=(filtered.length===CO.length?'Showing all '+CO.length+' companies.':'Showing '+filtered.length+' of '+CO.length+' companies.')+' Press Compare on two or three to see them side by side.';
  $('more').hidden=filtered.length<=shown;
}
function toggleCompare(o){
  if(compare.indexOf(o)>=0)compare=compare.filter(function(x){return x!==o;});
  else{if(compare.length>=3){flash('You can compare up to 3 companies. Remove one first.');return;}compare.push(o);}
  refreshBtns(o);drawCmpBar();
}
function refreshBtns(o){[].forEach.call(document.querySelectorAll('[data-add="'+o+'"]'),function(b){var had=document.activeElement===b,tmp=document.createElement('div');
  tmp.innerHTML=b.classList.contains('big')?cmpToggle(BY[o]):addBtn(BY[o]);var nb=tmp.firstChild;b.replaceWith(nb);if(had)nb.focus();});}
function cmpToggle(co){var on=compare.indexOf(co.o)>=0;
  return '<button type="button" class="btn quiet big" data-add="'+co.o+'" aria-pressed="'+on+'">'+(on?IC.check+'Added to compare':IC.plus+'Add to compare')+'</button>';}
function flash(msg){var b=$('cmpbar');b.hidden=false;var p=b.querySelector('.msg');if(p){p.textContent=msg;setTimeout(drawCmpBar,2600);}}
function drawCmpBar(){
  var bar=$('cmpbar'),onCmp=/^#compare/.test(location.hash);
  $('nav-cmp').hidden=!compare.length;$('nav-cmp').href='#compare='+compare.join(',');$('nav-cmp-n').textContent=compare.length;
  bar.hidden=!compare.length||onCmp;if(bar.hidden)return;
  var names=compare.map(function(o){return '<strong>'+esc(dn(BY[o].n))+'</strong>';}).join(', ');
  bar.innerHTML='<div class="cbin"><p class="msg"><span class="cnames">Compare '+names+(compare.length<2?'. Add one or two more companies.':'')+'</span>'+
    '<span class="ccount">'+(compare.length<2?'1 company picked. Add one or two more.':compare.length+' companies picked')+'</span></p>'+
    (compare.length>=2?'<a class="btn" href="#compare='+compare.join(',')+'">Compare '+compare.length+' companies</a>':'')+
    '<button type="button" class="tbtn" data-clear>Clear</button></div>';
}

/* ----- profile parts ----- */
function cites(co,ids,refs){
  var I=ix(co),b=(ids||[]).filter(function(id){return I[id];}).map(function(id){if(!refs.m[id])refs.m[id]=++refs.n;
    return '<button type="button" class="cite" data-o="'+co.o+'" data-c="'+esc(id)+'" aria-haspopup="dialog" aria-label="Source '+refs.m[id]+': '+esc(label(I[id].f))+'">'+refs.m[id]+'</button>';});
  return b.length?'<sup class="cites">'+b.join('<span aria-hidden="true">,</span>')+'</sup>':'';
}
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
function finTable(co,kind,noid){
  var rows=accounts(co,kind);if(!rows.length)return '';
  var audited=co.c.filter(function(c){return c.f==='accounts_audited'&&c.a==='available'&&c.v&&(c.v.account_type||'company')===kind;});
  var years=[],per={};rows.forEach(function(c){var y=String(c.v.year||'');if(years.indexOf(y)<0){years.push(y);per[y]=c.v;}});
  years.sort().reverse();
  var cur={};rows.forEach(function(c){if(c.v.currency)cur[c.v.currency]=1;});
  var fields=FIN.filter(function(f){return rows.some(function(c){return c.f===f;});});
  var calendar=years.every(function(y){var p=per[y];return !p.period_start||(/-01-01$/.test(p.period_start)&&/-12-31$/.test(p.period_end||''));});
  function cellFor(c,y){return '<td data-y="'+esc(y)+'"'+(c&&!noid?' id="'+fid(co,c)+'"':'')+'>'+(c?vbtn(co,c,nfmt(c.v.amount)):'<span class="nr">Not reported</span>')+'</td>';}
  var n=years.length;
  var h='<div class="tablewrap"><div class="tcap"><h3>'+(kind==='group'?'Group accounts':'Company accounts')+'</h3><p>'+esc(Object.keys(cur).join(', ')||'Currency not stated')+
    ', as filed with Regnskapsregisteret'+(calendar?', calendar years':'')+'</p></div>'+
    '<table class="fin'+(n>3?' many':'')+'" style="--n:'+Math.min(n,3)+'"><thead><tr><th scope="col"><span class="vh">Figure</span></th>'+
    years.map(function(y){var p=per[y];return '<th scope="col">'+esc(y)+(calendar?'':'<span class="per">'+(p.period_start?dfmt(p.period_start)+' to ':'')+dfmt(p.period_end)+'</span>')+'</th>';}).join('')+'</tr></thead><tbody>'+
    fields.map(function(f){return '<tr'+(BREAK[f]?' class="brk"':'')+'><th scope="row">'+esc(label(f))+'</th>'+years.map(function(y){
      return cellFor(rows.filter(function(x){return x.f===f&&String(x.v.year)===y;})[0],y);}).join('')+'</tr>';}).join('')+
    (audited.length?'<tr><th scope="row">Audited</th>'+years.map(function(y){var c=audited.filter(function(x){return String(x.v.year)===y;})[0];
      return '<td data-y="'+esc(y)+'"'+(c&&!noid?' id="'+fid(co,c)+'"':'')+'>'+(c?vbtn(co,c,c.v.audited?'Yes':'No'):'<span class="nr">Not reported</span>')+'</td>';}).join('')+'</tr>':'')+
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
var SUGG=['What does it do?','Who runs it?','How did it do last year?','Is it hiring?','What changed recently?'];
var INTENTS=[
 ['what',/what does|what do|does it do|do they do|business|industry|purpose|sector|activity|hva gj\u00f8r|gj\u00f8r|driver med|bransje|form\u00e5l|virksomhet|n\u00e6ring/g],
 ['who',/who runs|runs?\b|ceo|manag|leader|leads?\b|board|chair|director|daglig|leder|styre|in charge/g],
 ['owner',/owns?\b|owner|owned|shareholder|stake|eier|eiere|aksjon\u00e6r|innehaver/g],
 ['money',/revenue|turnover|profit|loss|result|earn|money|financ|last year|perform|equity|accounts|omsetning|resultat|inntekt|overskudd|underskudd|regnskap|\u00f8konomi|egenkapital|tall/g],
 ['hiring',/hir(e|ing)|jobs?\b|vacanc|recruit|career|opening|ansett|ansatt nye|nye folk|ledig|stilling|jobb|karriere|rekrutt/g],
 ['changed',/chang|recent|news|update|happen|endr|nytt|nyhet|oppdater/g],
 ['where',/where|located|location|address|operate|office|workplace|branch|municipal|adresse|hvor|kommune|sted|avdeling|kontor/g],
 ['website',/website|web\s?site|\bweb\b|url|verif|domain|social|linkedin|facebook|instagram|nettside|hjemmeside|nettsted/g],
 ['contact',/contact|e-?mail|phone|call|reach|kontakt|telefon|e-post|epost|ring/g],
 ['size',/employ|staff|people|how big|size|workers|ansatte|st\u00f8rrelse/g],
 ['group',/parent|group|subsidiar|konsern|morselskap|datter/g]];
function intents(q){q=q.toLowerCase();var best=0,out=[];INTENTS.forEach(function(it){var m=q.match(it[1]);var s=m?m.length:0;if(s>best){best=s;out=[it[0]];}else if(s&&s===best)out.push(it[0]);});return out.slice(0,2);}
function S(text,cls){return [text,cls||[]];}
function answerFor(co,intent){
  var out=[],c,cs;
  if(intent==='what'){c=one(co,'industry');if(c)out.push(S('Registered industry: '+c.v.name+' ('+c.v.code+').',[c]));
    c=one(co,'statutory_purpose')||one(co,'registered_activity');if(c)out.push(S('Registered purpose: \u201c'+endStop(c.v)+'\u201d',[c]));
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
    c=(one(co,'hiring_signal')||one(co,'careers_page'));if(c)out.push(S('The verified website has a careers page: '+bare(c.v)+'.',[c]));}
  else if(intent==='changed'){cs=av(co,'registry_change').sort(function(a,b){return (b.d||'').localeCompare(a.d||'');}).slice(0,3);
    cs.forEach(function(x){out.push(S('On '+dfmt(x.d)+' the registry recorded '+(!chgUnknown(x.v)?'a change to '+x.v.changed.join(', '):'an update (details not published)')+'.',[x]));});
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
  else if(intent==='owner'){var own=av(co,'owner'),pt=av(co,'partner'),lf_=one(co,'legal_form'),code=lf_&&lf_.v?lf_.v.code:'';
    if(own.length)out.push(S('It is a sole proprietorship owned by '+own[0].v.name+'.',[own[0]]));
    else if(pt.length)out.push(S('It is owned by its partners: '+pt.map(function(x){return x.v.name;}).join(', ')+'.',pt));
    else if(code==='STI')out.push(S('It is a foundation, so it has no owners.',[lf_]));
    else out.push(S('Owners and shareholders are not published in the open registers Kilde checks.',[]));
    c=one(co,'group_parent');if(c)out.push(S('Its parent company is '+dn(c.v.name)+' ('+orgf(c.v.organisation_number)+')'+(c.v.ownership?', which owns '+c.v.ownership:'')+'.',[c]));
    else{c=one(co,'lei_direct_parent')||one(co,'lei_ultimate_parent');if(c)out.push(S('GLEIF lists '+dn(c.v.name)+' as its parent company.',[c]));
      else{c=one(co,'part_of_group');if(c)out.push(S(c.v?'It is registered as part of a group.':'It is not registered as part of a group.',[c]));}}
    c=one(co,'board_chair');if(c&&!own.length)out.push(S('Who controls it: the board, chaired by '+c.v.name+'.',[c]));}
  else if(intent==='group'){c=one(co,'group_parent');if(c)out.push(S('Parent company: '+c.v.name+' ('+orgf(c.v.organisation_number)+')'+(c.v.ownership?', '+c.v.ownership+' owned':'')+'.',[c]));
    cs=av(co,'group_subsidiary');if(cs.length)out.push(S('Subsidiaries: '+cs.map(function(x){return x.v.name;}).join(', ')+'.',cs));
    c=one(co,'part_of_group');if(c&&!out.length)out.push(S(c.v?'It is registered as part of a group.':'It is not registered as part of a group.',[c]));}
  return out;
}
function ask(co,q){
  var el=$('answer');if(!el)return;q=String(q||'').trim();if(!q){el.innerHTML='';el.hidden=true;return;}
  var its=intents(q),sents=[];
  its.forEach(function(it){sents=sents.concat(answerFor(co,it));});
  var html;
  if(!its.length)html='<p>I can only answer from the facts in this profile. Try asking what it does, who runs it, how it did last year, whether it is hiring, what changed, where it is, its website, contacts, size or owners.</p>';
  else if(!sents.length)html='<p>Not found in the sources Kilde checked.</p>';
  else html=sents.map(function(s){return '<p>'+prettyDates(esc(s[0]))+' '+srcLink(co,s[1])+'</p>';}).join('');
  el.innerHTML='<p class="q">'+esc(q)+'</p>'+html;el.hidden=false;
}
function askPanel(co){
  return '<section class="ask" aria-labelledby="h-ask"><h2 id="h-ask" class="vh">Ask about this company</h2>'+
    '<form id="askf" data-o="'+co.o+'"><label class="vh" for="askq">Ask anything about this company</label><div class="askrow">'+
    '<input id="askq" type="text" autocomplete="off" placeholder="Ask anything about this company"><button class="btn" type="submit">Ask</button></div></form>'+
    '<p class="sugg"><span class="muted">For example</span>'+SUGG.map(function(s){return '<button type="button" data-ask="'+esc(s)+'">'+esc(s)+'</button>';}).join('')+'</p>'+
    '<div class="answer" id="answer" aria-live="polite" hidden></div></section>';
}

/* ----- company page ----- */
function prettyDates(s){return s.replace(/an? underenhet til n\u00e6ringsdrivende og offentlig forvaltning/gi,'a registered workplace (sub-unit)').replace(/underenhet til n\u00e6ringsdrivende og offentlig forvaltning/gi,'registered workplace (sub-unit)').replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g,function(m){return dfmt(m);});}
function intro(co){
  var refs={n:0,m:{}},exact=esc(co.n),nice=esc(dn(co.n));
  var parts=co.sum.t.map(function(s){return prettyDates(esc(s[0])).split(exact).join(nice)+cites(co,s[1],refs);});
  if(!parts.length)return '';
  var head=parts.slice(0,3).join(' '),rest=parts.slice(3).join(' ');
  return '<section class="intro" aria-labelledby="h-intro"><h2 id="h-intro" class="vh">Summary</h2><div class="sumtext" id="sumtext"><p>'+head+
    (rest?' <span id="sumrest" hidden>'+rest+'</span>':'')+'</p></div><p class="sumctl">'+
    (rest?'<button type="button" class="tbtn" data-more aria-expanded="false" aria-controls="sumrest">Read the full summary</button>':'')+
    '<button type="button" class="tbtn" data-showsrc aria-pressed="false" aria-controls="sumtext">Show sources</button></p></section>';
}
function card(id,q,ans,cls,co,det,extra){
  return '<section class="card" id="q-'+id+'" aria-labelledby="hq-'+id+'"><div class="ch"><h2 id="hq-'+id+'">'+q+'</h2>'+srcLink(co,cls)+'</div>'+
    '<div class="ans">'+ans+'</div>'+(det?'<dl class="det">'+det+'</dl>':'')+(extra||'')+'</section>';
}
function drow(co,lab,val,cls){return '<div class="dr"><dt>'+esc(lab)+'</dt><dd>'+val+'</dd><dd class="ds">'+srcLink(co,cls)+'</dd></div>';}
function crow(co,cl,lab,val){if(!cl)return '';return drow(co,lab||label(cl.f),val!=null?val:(cl.a==='available'?render(cl.f,cl.v):'<span class="miss">Not found</span>'+why(cl.n)),[cl]);}
function nm(v){return esc(dn(v&&v.name||''));}
function P(s){return '<p>'+s+'</p>';}

function cardWhat(co){
  var ind=one(co,'industry'),pur=one(co,'statutory_purpose')||one(co,'registered_activity'),desc=one(co,'website_description'),
      inc=one(co,'incorporation_date')||one(co,'registration_date'),emp=any(co,'employees_registered')[0],lf=one(co,'legal_form'),
      st=one(co,'legal_status'),reg=one(co,'register_memberships'),fn=one(co,'former_names');
  var w=what(co),t=muni(co),a=[],cls=[];
  if(w&&w!=='Industry not stated'){a.push(esc(w)+(t?' in '+esc(t):'')+'.');cls.push(ind);}
  else if(w){a.push('The registry does not state its industry'+(t?'. It is based in '+esc(t):'')+'.');cls.push(ind);}
  else a.push('The registry does not say what line of business it is in.');
  var bits=[];if(inc){bits.push('founded in '+String(inc.v).slice(0,4));cls.push(inc);}
  if(emp&&emp.a==='available'){bits.push(empText(emp.v).toLowerCase()+' employees');cls.push(emp);}
  else if(emp&&/no employees/i.test(emp.n||'')){bits.push('no employees registered');cls.push(emp);}
  if(bits.length)a.push(cap(bits.join(', '))+'.');
  if(desc&&co.sum.t.some(function(x){return String(x[0]).indexOf(String(desc.v).slice(0,40))>=0;}))desc=null;
  var ans=P(a.join(' '))+(desc?'<p class="quote">In its own words: “'+esc(desc.v)+'”</p>':'');
  if(desc)cls.push(desc);
  var det=(lf?crow(co,lf,'Company type',esc(lfName(lf.v))+' <span class="sub2">'+esc(lf.v.name)+' ('+esc(lf.v.code)+')</span>'):'')+
    (ind?crow(co,ind,'Registered industry',esc(ind.v.name)+' <span class="sub2 num">'+esc(ind.v.code)+'</span>'):'')+
    (pur?crow(co,pur,'Registered purpose',esc(endStop(pur.v))+' <span class="sub2">(Norwegian)</span>'):'')+
    (st?crow(co,st,'Status'):'')+
    (reg&&reg.v?crow(co,reg,'Registered for VAT',reg.v.registrertIMvaregisteret?'Yes':'No'):'')+
    (fn?crow(co,fn,'Former names'):'');
  return card('what','What does it do?',ans,cls,co,det);
}
function roleName(c){var v=c.v||{};return esc(v.organisation_number?dn(v.name):v.name||'')+(v.organisation_number?' <span class="sub2 num">'+orgf(v.organisation_number)+'</span>':'');}
function cardWho(co){
  var ceo=one(co,'ceo'),ch=one(co,'board_chair'),own=av(co,'owner'),parts=av(co,'partner'),mem=av(co,'board_member'),dep=av(co,'board_deputy_member'),
      aud=one(co,'auditor'),acc=one(co,'accountant'),sig=one(co,'signature_rule'),bm=av(co,'business_manager'),a=[],cls=[];
  if(own.length){a.push(esc(own[0].v.name)+' owns and runs it as a sole proprietor.');cls.push(own[0]);}
  if(ceo&&ch&&ceo.v.name===ch.v.name){a.push(esc(ceo.v.name)+' is both general manager and board chair.');cls.push(ceo,ch);}
  else{if(ceo){a.push(esc(ceo.v.name)+' is the general manager.');cls.push(ceo);}if(ch){a.push(esc(ch.v.name)+' chairs the board.');cls.push(ch);}}
  if(parts.length){a.push('Partners: '+parts.map(function(p){return esc(p.v.name);}).join(', ')+'.');cls=cls.concat(parts);}
  if(bm.length){a.push(esc(dn(bm[0].v.name))+' manages the business.');cls.push(bm[0]);}
  if(mem.length){a.push(mem.length===1?'The board has one more member.':'The board has '+mem.length+' more members.');}
  if(!a.length)a.push('No general manager, board or owner is registered.');
  var ceoMiss=any(co,'ceo')[0];
  var people=co.c.filter(function(c){return c.s==='leadership'&&c.a==='available'&&c.f!=='signature_rule';});
  people.sort(function(x,y){return ROLE_ORDER.indexOf(x.f)-ROLE_ORDER.indexOf(y.f);});
  var det=people.map(function(c){return drow(co,c.f==='other_role'?cap(String(c.v.role||'Role').toLowerCase())+' (Norwegian)':label(c.f),roleName(c),[c]);}).join('')+
    (!ceo&&ceoMiss&&ceoMiss.n?drow(co,'General manager','<span class="miss">None registered</span>'+why(ceoMiss.n),[ceoMiss]):'')+
    (sig?drow(co,'Who can sign for it',(signText(sig.v)?esc(signText(sig.v))+' <span class="sub2">'+esc(sig.v)+'</span>':esc(sig.v)+' <span class="sub2">(Norwegian)</span>'),[sig]):'');
  return card('who','Who runs it?',P(a.join(' ')),cls,co,det);
}
function barChart(title,cls){
  if(!cls.length)return '';cls=cls.slice(-5);
  var W=320,H=158,pt=22,pb=24,n=cls.length,vals=cls.map(function(c){return Number(c.v.amount);});
  var max=Math.max(0,Math.max.apply(null,vals)),min=Math.min(0,Math.min.apply(null,vals)),span=(max-min)||1,ih=H-pt-pb,y0=pt+ih*(max/span);
  var slot=W/n,bw=Math.min(46,slot*0.5);
  var g=cls.map(function(c,i){var v=vals[i],x=slot*i+(slot-bw)/2,cx=x+bw/2,h=Math.abs(v)/span*ih,r=Math.min(4,h/2),d='';
    if(h>=0.5){if(v>=0){var y=y0-h;d='M'+x+' '+y0+'V'+(y+r)+'Q'+x+' '+y+' '+(x+r)+' '+y+'H'+(x+bw-r)+'Q'+(x+bw)+' '+y+' '+(x+bw)+' '+(y+r)+'V'+y0+'Z';}
      else{var yb=y0+h;d='M'+x+' '+y0+'V'+(yb-r)+'Q'+x+' '+yb+' '+(x+r)+' '+yb+'H'+(x+bw-r)+'Q'+(x+bw)+' '+yb+' '+(x+bw)+' '+(yb-r)+'V'+y0+'Z';}}
    var ly=v>=0?y0-h-7:y0-7;
    return '<g><title>'+esc(c.v.year)+': NOK '+nfmt(v)+'</title><rect x="'+(slot*i)+'" y="0" width="'+slot+'" height="'+H+'" fill="transparent"/>'+
      (d?'<path class="'+(v<0?'bneg':'bpos')+'" d="'+d+'"/>':'')+'<text class="bl" x="'+cx+'" y="'+ly+'" text-anchor="middle">'+short(v)+'</text>'+
      '<text class="bx" x="'+cx+'" y="'+(H-6)+'" text-anchor="middle">'+esc(c.v.year)+'</text></g>';}).join('');
  return '<figure class="chart"><figcaption>'+title+' <span class="muted">NOK</span></figcaption><svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+esc(title)+' by year: '+
    cls.map(function(c){return esc(c.v.year)+' NOK '+nfmt(c.v.amount);}).join('; ')+'"><line class="base" x1="0" x2="'+W+'" y1="'+y0+'" y2="'+y0+'"/>'+g+'</svg></figure>';
}
function cardMoney(co){
  var rev=series(co,'revenue'),res=series(co,'annual_result'),cls=[],a=[],pct=false;
  var lat=any(co,'latest_filed_accounts_year')[0];
  if(!rev.length&&!res.length){
    var f=co.c.filter(function(c){return c.s==='financials'&&c.a!=='available';})[0];
    a.push('No annual accounts were found for this company.'+why(f&&f.n?f.n:'Brønnøysundregistrene has no filed accounts for this organisation number.'));
    return card('money','How is it doing?',P(a.join(' ')),f?[f]:[],co,'');
  }
  var Y=String((rev.length?rev[rev.length-1]:res[res.length-1]).v.year);
  var r=rev.filter(function(c){return String(c.v.year)===Y;})[0],r0=r?rev[rev.indexOf(r)-1]:null,s=res.filter(function(c){return String(c.v.year)===Y;})[0];
  var t='In '+esc(Y);
  if(r){t+=' it had revenue of '+(Number(r.v.amount)<0?'\u2212':'')+words(r.v.amount)+' NOK';cls.push(r);
    if(r0&&Number(r0.v.amount)!==0){var ch=(Number(r.v.amount)-Number(r0.v.amount))/Math.abs(Number(r0.v.amount))*100;pct=true;cls.push(r0);
      t+=Math.abs(ch)<0.5?', about the same as in '+esc(r0.v.year):(ch>0?', up ':', down ')+(Math.abs(ch)>=10?Math.round(Math.abs(ch)):Math.abs(ch).toFixed(1))+'% from '+esc(r0.v.year);}
    t+=s?', and ':'.';}
  else t+=' it reported no revenue figure, and ';
  if(s){var v=Number(s.v.amount);t+=v>=0?'made a profit of '+words(v)+' NOK.':'made a loss of '+words(v)+' NOK.';cls.push(s);}
  a.push(t);
  var grp=accounts(co,'group').length;
  var note='<p class="fine">Amounts are copied from the accounts filed with Brønnøysundregistrene'+(pct?'. The percentage is worked out by Kilde from those filed figures':'')+'.'+
    (grp?' Group accounts are also filed and are listed under all figures.':'')+'</p>';
  var charts='<div class="charts">'+barChart('Revenue',rev)+barChart('Annual result',res)+'</div>';
  var pdfs=av(co,'annual_report_pdf').sort(function(x,y){return String(y.v.year).localeCompare(String(x.v.year));});
  var more='<details class="more"><summary>All filed figures</summary>'+finTable(co,'company',true)+finTable(co,'group',true)+
    (pdfs.length?'<dl class="det">'+drow(co,'Annual report copies','<span class="yl">'+pdfs.map(function(c){return link(c.v.url,String(c.v.year));}).join('')+'</span>',[pdfs[0]])+'</dl>':'')+'</details>';
  return card('money','How is it doing?',P(a.join(' '))+charts+note,cls,co,'',more);
}
function cardWhere(co){
  var ad=one(co,'business_address'),po=one(co,'postal_address'),mu=one(co,'municipality'),wp=av(co,'registered_workplace'),wn=any(co,'registered_workplace')[0],a=[],cls=[];
  if(ad){a.push('Registered address: '+esc(ad.v)+(mu?' ('+esc(tc(mu.v.name))+' municipality)':'')+'.');cls.push(ad);if(mu)cls.push(mu);}
  else a.push('No business address is registered.');
  if(wp.length){var ms={};wp.forEach(function(c){if(c.v.municipality)ms[tc(c.v.municipality)]=1;});var m=Object.keys(ms);
    a.push((wp.length===1?'It has one registered workplace':'It has '+wp.length+' registered workplaces')+(m.length?' in '+esc(m.slice(0,4).join(', '))+(m.length>4?' and more':''):'')+'.');cls.push(wp[0]);}
  else if(wn&&wn.a==='not_available'){a.push('No separate workplaces are registered.');cls.push(wn);}
  var det=(po?crow(co,po,'Postal address'):'')+wp.slice(0,8).map(function(c){var v=c.v;
    var ex=[v.start_date?'since '+dfmt(v.start_date):'',v.employees!=null?plural(v.employees,'employee'):''].filter(Boolean).join(', ');
    return drow(co,dn(v.name||'Workplace'),esc(v.address||'')+(ex?'<span class="note">'+ex+'</span>':''),[c]);}).join('')+
    (wp.length>8?'<p class="fine">'+plural(wp.length-8,'more workplace')+' listed under all facts.</p>':'');
  return card('where','Where is it?',P(a.join(' ')),cls,co,det);
}
function proof(c){if(c.m==='orgnr_on_page')return 'the site shows its organisation number';return c.md?mdText(c.md):'it passed the identity check';}
function cardOnline(co){
  var w=any(co,'official_website')[0],a=[],cls=[],soc=av(co,'social_profile'),rw=one(co,'registry_website'),rel=one(co,'related_website');
  if(w&&w.a==='available'){a.push('Yes, at '+link(w.v)+'. Kilde confirmed it belongs to this company because '+esc(proof(w))+'.');cls.push(w);}
  else{var reason={ambiguous:'A possible site was found, but nothing on it proves it belongs to this company.',blocked:'The likely sites block automated visits, so none could be checked.',
      failed:'The website check did not finish in this run.'}[w?w.a:'']||'';
    a.push((w&&w.a==='failed'?'Not checked.':'No website found.')+why((reason?reason+' ':'')+(w&&w.n?cap(w.n)+'.':'')));if(w)cls.push(w);}
  if(soc.length){a.push('Its site links to '+soc.map(function(c){return esc(PLAT[String(c.v.platform).toLowerCase()]||c.v.platform);}).join(', ')+'.');cls=cls.concat(soc);}
  var det=soc.map(function(c){return drow(co,PLAT[String(c.v.platform).toLowerCase()]||cap(c.v.platform),link(c.v.url,bare(c.v.url)),[c]);}).join('');
  [['site_contact_email','Email (website)'],['site_contact_phone','Phone (website)'],['registry_email','Email (registry)'],['registry_phone','Phone (registry)'],['registry_mobile','Mobile (registry)']].forEach(function(p){
    av(co,p[0]).forEach(function(c){det+=crow(co,c,p[1]);});});
  if(rw&&!(w&&w.a==='available'))det+=crow(co,rw,'Website in the registry',render('registry_website',rw.v)+' <span class="sub2">not confirmed</span>');
  if(rel)det+=crow(co,rel,'Related site',render('related_website',rel.v)+' <span class="sub2">belongs to another company</span>');
  var lang=one(co,'site_language');if(lang)det+=crow(co,lang,'Site language');
  return card('online','Is it online?',P(a.join(' ')),cls,co,det);
}
function cardHiring(co){
  var ads=av(co,'job_ad'),jn=any(co,'job_ad')[0],car=(one(co,'hiring_signal')||one(co,'careers_page')),a=[],cls=[];
  if(ads.length){a.push('Yes. '+(ads.length===1?'One open job ad':ads.length+' open job ads')+' on NAV, Norway’s public job board.');cls=cls.concat(ads);}
  else if(jn&&jn.a==='failed'){a.push('Unknown. Job ads could not be checked in this run.'+why(jn.n));cls.push(jn);}
  else if(jn){a.push('No open job ads on NAV, Norway’s public job board.');cls.push(jn);}
  else a.push('Job ads were not checked for this company.');
  if(car){a.push('Its website links to a '+(car.f==='hiring_signal'?'careers page or job posting':'careers page')+': '+link(car.v,bare(car.v))+'.');cls.push(car);}
  var det=ads.map(function(c){return drow(co,'Job ad on NAV',render('job_ad',c.v),[c]);}).join('')+(car?crow(co,car,car.f==='hiring_signal'?'Careers page or job posting':'Careers page'):'');
  return card('hiring','Is it hiring?',P(a.join(' ')),cls,co,det);
}
function cardChanged(co){
  var regs=regChanges(co),sites=av(co,'site_page_updated').sort(function(x,y){return (y.d||'').localeCompare(x.d||'');}),a=[],cls=[];
  if(regs.length){var r=regs[0];a.push((regs.length===1?'One registry update':regs.length+' registry updates')+' in the last 12 months. The latest, on '+dfmt(r.d)+', '+
      (!chgUnknown(r.v)?'changed the '+esc(r.v.changed.join(', '))+'.':'did not publish what changed.'));cls.push(r);}
  else a.push('No registry updates in the last 12 months.');
  if(co.ch.length)a.push(co.ch.length+' differences since the previous Kilde run.');
  var news=av(co,'dated_news').sort(function(x,y){return (y.d||'').localeCompare(x.d||'');});
  var ntitle=function(c){return String(c.v).replace(/\s*\([^()]*\)\s*$/,'');};
  if(news.length){a.push((news.length===1?'Kilde collected one dated news post from its website':'Kilde collected '+news.length+' dated news posts from its website')+'; the latest, "'+esc(ntitle(news[0]))+'", is from '+dfmt(news[0].d)+'.');cls.push(news[0]);}
  var det=regs.slice(0,6).map(function(c){return drow(co,dfmt(c.d),render('registry_change',c.v),[c]);}).join('')+
    co.ch.map(function(ch){var cl=ch.current_claim_id?ix(co)[ch.current_claim_id]:null;
      return drow(co,ch.detected_at?dfmt(ch.detected_at):'Since last run',esc(label(ch.field))+': '+esc(String(ch.type||'change').replace(/_/g,' ')),cl?[cl]:[]);}).join('')+
    news.slice(0,5).map(function(c){return drow(co,dfmt(c.d),esc(ntitle(c)),[c]);}).join('')+
    sites.slice(0,3).map(function(c){return drow(co,dfmt(c.d),render('site_page_updated',c.v),[c]);}).join('');
  var extra=(regs.length>6?'<p class="fine">'+(regs.length-6===1?'One older update is':(regs.length-6)+' older updates are')+' listed under all facts.</p>':'')+
    (!co.ch.length&&META.previous===0?'<p class="fine">This is the first Kilde run for these companies, so there is no earlier profile to compare with yet.</p>':'');
  return card('changed','What changed recently?',P(a.join(' ')),cls,co,det,extra);
}
var REC=['public_contract_award','procurement_notice','research_project','state_aid_award','food_inspection','approval_sentral_godkjenning'];
function cardRecords(co){
  var by={};REC.forEach(function(f){by[f]=av(co,f).slice().sort(function(x,y){return String(y.d||(y.v&&y.v.date)||'').localeCompare(String(x.d||(x.v&&x.v.date)||''));});});
  if(!REC.some(function(f){return by[f].length;}))return '';
  var a=[],cls=[],det='',more=0;
  function dt(c){var d=c.d||(c.v&&(c.v.date||c.v.decision_date));return d?dfmt(d):'';}
  function rows(f,lab,max){var xs=by[f];xs.slice(0,max).forEach(function(c){var d=dt(c);det+=drow(co,lab+(d?', '+d:''),render(f,c.v),[c]);});more+=Math.max(0,xs.length-max);}
  var k=by.public_contract_award;if(k.length){a.push((k.length===1?'Won one public contract':'Won '+k.length+' public contracts')+' listed in the EU\u2019s TED journal'+(dt(k[0])?'; latest '+dt(k[0]):'')+'.');cls.push(k[0]);}
  k=by.procurement_notice;if(k.length){a.push('Published '+(k.length===1?'one tender notice':k.length+' tender notices')+' as a buyer in TED.');cls.push(k[0]);}
  k=by.research_project;if(k.length){a.push('Has '+(k.length===1?'one publicly backed research project':k.length+' publicly backed research projects')+' (Research Council of Norway or SkatteFUNN).');cls.push(k[0]);}
  k=by.state_aid_award;if(k.length){a.push('Received '+(k.length===1?'one state aid award':k.length+' state aid awards')+' listed in the State Aid Register'+(dt(k[0])?'; latest '+dt(k[0]):'')+'.');cls.push(k[0]);}
  k=by.food_inspection;if(k.length){var fv=k[0].v;a.push('Latest food safety inspection by Mattilsynet'+(fv.date?' on '+dfmt(fv.date):'')+': '+esc(String(fv.result||('grade '+fv.grade)).toLowerCase())+(fv.place?', at '+esc(fv.place):'')+'.');cls.push(k[0]);}
  k=by.approval_sentral_godkjenning;if(k.length){var av_=k[0].v;a.push('Approved by DiBK (sentral godkjenning) to take on building work'+(av_.valid_to?', valid until '+dfmt(av_.valid_to):'')+'.');cls.push(k[0]);}
  rows('public_contract_award','Contract won',5);rows('procurement_notice','Tender notice',3);rows('research_project','Research project',5);rows('state_aid_award','State aid',5);
  rows('food_inspection','Food inspection',3);by.approval_sentral_godkjenning.forEach(function(c){det+=drow(co,'Building approval',render('approval_sentral_godkjenning',c.v),[c]);});
  return card('records','In public records',P(a.join(' ')),cls,co,det,more?'<p class="fine">'+plural(more,'more record')+' listed under all facts.</p>':'');
}
function cardOwner(co){
  var par=one(co,'group_parent'),ult=one(co,'group_ultimate_parent'),subs=av(co,'group_subsidiary'),pog=one(co,'part_of_group'),
      lp=one(co,'lei_direct_parent')||one(co,'lei_ultimate_parent'),lei=one(co,'lei'),own=av(co,'owner'),pt=av(co,'partner'),lf=one(co,'legal_form'),
      cap_=one(co,'share_capital'),wd=one(co,'wikidata_item'),a=[],cls=[],code=lf&&lf.v?lf.v.code:'';
  if(par){a.push('It is owned by '+nm(par.v)+(par.v.ownership?' ('+esc(par.v.ownership)+')':'')+'.');cls.push(par);}
  else if(lp){a.push('GLEIF lists '+nm(lp.v)+' as its parent company.');cls.push(lp);}
  else if(own.length){a.push('It belongs to its owner, '+esc(own[0].v.name)+'.');cls.push(own[0]);}
  else if(pt.length){a.push('It is owned by its partners.');cls=cls.concat(pt);}
  else if(code==='STI'){a.push('It is a foundation, so it has no owners.');cls.push(lf);}
  else if(code==='ESEK'||code==='BRL'){a.push('It is owned by the residents of the building.');cls.push(lf);}
  else if(pog&&pog.v===true){a.push('It is registered as part of a group.');cls.push(pog);}
  else if(pog){a.push('It is not registered as part of a group.');cls.push(pog);}
  if(subs.length){a.push('It owns '+(subs.length===1?'one registered subsidiary':subs.length+' registered subsidiaries')+'.');cls=cls.concat(subs);}
  var det=(par?crow(co,par,'Parent company'):'')+(ult?crow(co,ult,'Top parent company'):'')+subs.map(function(c){return crow(co,c,'Subsidiary');}).join('')+
    (lei?crow(co,lei,'LEI (global company ID)'):'')+(lp?crow(co,lp,'Parent in GLEIF'):'')+(cap_?crow(co,cap_,'Share capital'):'')+(wd?crow(co,wd,'Wikidata'):'');
  var fine=(code==='AS'||code==='ASA')&&!par?'<p class="fine">Shareholders are not in the open registers Kilde reads, so owners appear only when a group record names them.</p>':'';
  return card('owner','Who owns it?',P(a.join(' ')||'Nothing is registered about its owners.'),cls,co,det,fine);
}
function allFacts(co){
  var inner=(identity(co)+financials(co)+leadership(co)+locations(co)+web(co)+hiring(co)+activity(co)+changes(co)+matching(co)+errs(co)).replace(/<(\/?)h2/g,'<$1h3');
  return '<details class="allfacts" id="allfacts"><summary><span class="st1">All facts and evidence</span><span class="st2">'+plural(co.c.length,'fact')+
    ' with source, exact quote, state and match method. For checking the details.</span></summary><div class="afbody">'+
    '<p class="fine">Every fact has one of these states: '+STATES.map(function(s){return stateLine(s);}).join(' ')+'</p>'+inner+'</div></details>';
}
function company(o){
  var co=BY[o];if(!co)return '<div class="co"><a class="back" href="#">'+IC.back+'All companies</a><p>No company '+esc(o)+' in this report.</p></div>';
  current=o;
  var lf=one(co,'legal_form'),w=what(co),t=muni(co),warn=statusWarn(co);
  var sub=[w&&t&&w!=='Industry not stated'?w+' in '+t:(w&&t?w+' \u00b7 '+t:(w||t)),lf&&lf.v?lfName(lf.v)+' ('+lf.v.code+')':'','Org. no. '+orgf(co.o)].filter(Boolean);
  return '<article class="co"><a class="back" href="#">'+IC.back+'All companies</a>'+
    '<header class="cohead"><div><h1 id="h-name">'+esc(dn(co.n||co.o))+'</h1><p class="cosub">'+sub.map(esc).join(' · ')+'</p></div>'+cmpToggle(co)+'</header>'+
    (warn?'<p class="alert">'+IC.alert+'<span><strong>'+warn+'.</strong> This status is registered with Brønnøysundregistrene. '+srcLink(co,[one(co,'legal_status')])+'</span></p>':'')+
    intro(co)+askPanel(co)+
    '<div class="cards">'+cardWhat(co)+cardWho(co)+cardMoney(co)+cardWhere(co)+cardOnline(co)+cardHiring(co)+cardChanged(co)+cardRecords(co)+cardOwner(co)+'</div>'+
    allFacts(co)+'</article>';
}

/* ----- compare ----- */
function compareView(list){
  var cos=list.map(function(o){return BY[o];}).filter(Boolean).slice(0,3);
  if(cos.length<2)return '<div class="cmpv"><a class="back" href="#">'+IC.back+'All companies</a><h1>Compare companies</h1>'+
    '<p class="lead">See two or three companies side by side. Press <strong>Compare</strong> next to a company in the list, or <strong>Add to compare</strong> on a company page.</p>'+
    (cos.length===1?'<p>So far you have picked '+esc(dn(cos[0].n))+'. Pick one or two more.</p>':'')+'<p><a class="btn" href="#">Browse companies</a></p></div>';
  current=null;var n=cos.length;
  function cell(co,cl,html,sub){return '<td>'+(cl?vbtn(co,cl,html):'<span class="miss">'+html+'</span>')+(sub?'<span class="yr">'+sub+'</span>':'')+'</td>';}
  function row(lab,fn){return '<tr><th scope="row">'+esc(lab)+'</th>'+cos.map(fn).join('')+'</tr>';}
  function grp(t){return '<tr class="g"><th scope="rowgroup" colspan="'+(n+1)+'">'+t+'</th></tr>';}
  function fig(f){return function(co){var c=latestFig(co,f);if(!c)return cell(co,null,'Not filed');var v=Number(c.v.amount);
    var txt=f==='annual_result'?(v<0?'Loss of ':'Profit of ')+words(v)+' NOK':(v<0?'\u2212':'')+words(v)+' NOK';return cell(co,c,txt,esc(c.v.year));};}
  function person(f){return function(co){var c=one(co,f);return c?cell(co,c,esc(c.v.name)):cell(co,null,'None registered');};}
  return '<div class="cmpv"><a class="back" href="#">'+IC.back+'All companies</a><h1>Comparing '+n+' companies</h1>'+
    '<p class="lead">Select any value to see where it came from.</p>'+
    '<table class="cmp" style="--n:'+n+'"><colgroup><col class="c0">'+cos.map(function(){return '<col>';}).join('')+'</colgroup>'+
    '<thead><tr><th scope="col"><span class="vh">Question</span></th>'+cos.map(function(co){return '<th scope="col"><a href="#org-'+co.o+'">'+esc(dn(co.n))+'</a>'+
      '<span class="sub2"><span class="num">'+orgf(co.o)+'</span><button type="button" class="rm" data-rm="'+co.o+'" aria-label="Remove '+esc(dn(co.n))+' from compare">'+IC.x+'</button></span></th>';}).join('')+'</tr></thead><tbody>'+
    grp('The basics')+
    row('What it does',function(co){var c=one(co,'industry');return cell(co,c,esc(what(co)||'Not stated'));})+
    row('Town',function(co){var c=one(co,'municipality');return cell(co,c,esc(muni(co)||'Not stated'));})+
    row('Company type',function(co){var c=one(co,'legal_form');return c?cell(co,c,esc(lfName(c.v))):cell(co,null,'Not stated');})+
    row('Founded',function(co){var c=one(co,'incorporation_date')||one(co,'registration_date');return c?cell(co,c,String(c.v).slice(0,4)):cell(co,null,'Not stated');})+
    row('Employees',function(co){var c=any(co,'employees_registered')[0];return c&&c.a==='available'?cell(co,c,esc(empText(c.v))):cell(co,c&&/no employees/i.test(c.n||'')?c:null,'None registered');})+
    grp('Money, latest filed year')+row('Revenue',fig('revenue'))+
    row('Revenue change (worked out by Kilde)',function(co){var r=series(co,'revenue');if(r.length<2||!Number(r[r.length-2].v.amount))return cell(co,null,'Not enough years');
      var a=r[r.length-1],b=r[r.length-2],ch=(a.v.amount-b.v.amount)/Math.abs(b.v.amount)*100;return '<td>'+vbtn(co,a,(ch>=0?'Up ':'Down ')+Math.round(Math.abs(ch))+'%')+'<span class="yr">'+esc(b.v.year)+' to '+esc(a.v.year)+'</span></td>';})+
    row('Annual result',fig('annual_result'))+row('Equity',fig('equity'))+
    grp('People')+row('General manager',person('ceo'))+row('Board chair',person('board_chair'))+
    grp('Online and activity')+
    row('Website',function(co){var c=any(co,'official_website')[0];return c&&c.a==='available'?cell(co,c,esc(bare(c.v))):cell(co,null,'No website found');})+
    row('Open job ads',function(co){var k=av(co,'job_ad'),c=any(co,'job_ad')[0];return k.length?cell(co,k[0],String(k.length)):cell(co,null,c&&c.a==='failed'?'Not checked':'None');})+
    row('Registry updates, last 12 months',function(co){var k=regChanges(co);return k.length?cell(co,k[0],String(k.length),'latest '+dfmt(k[0].d)):cell(co,null,'None');})+
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
function claimBody(co,cl){
  var h='<div class="pop-v">'+valueOf(cl)+'</div>';
  (cl.e||[]).forEach(function(i){var ev=co.ev[i];if(!ev)return;var u=U[ev[0]]||'';
    var meta=['Retrieved '+dfmt(ev[1])+(tfmt(ev[1])?', '+tfmt(ev[1]):'')];if(ev[2])meta.push('dated '+dfmt(ev[2]));if(SRC_CLASS[ev[4]])meta.push(SRC_CLASS[ev[4]].toLowerCase());
    h+='<div class="pop-e">'+(safeUrl(u)?'<a class="u" href="'+esc(u)+'" target="_blank" rel="noopener noreferrer"><span>'+esc(bare(u).length>110?bare(u).slice(0,107)+'...':bare(u))+'</span>'+IC.ext+'</a>':esc(u))+
      '<p class="pop-m">'+meta.join(', ')+'</p>'+(ev[3]?'<p class="pop-ql">Exact text read from the source</p><pre class="q">'+highlight(ev[3],cl)+'</pre>':'')+'</div>';});
  if(cl.m&&(cl.md||METHOD[cl.m]))h+='<p class="pop-m pop-match">'+esc(cl.md?'How it was matched: '+mdText(cl.md)+'.':METHOD[cl.m])+'</p>';
  return h;
}
function popHtml(co,cls){
  var x='<button type="button" class="x" data-close aria-label="Close">'+IC.x+'</button>';
  if(cls.length===1)return '<div class="pop-h"><strong>'+esc(label(cls[0].f))+'</strong>'+x+'</div>'+claimBody(co,cls[0]);
  return '<div class="pop-h"><strong>Sources</strong>'+x+'</div>'+cls.map(function(cl){return '<div class="pop-c"><p class="pop-cl">'+esc(label(cl.f))+'</p>'+claimBody(co,cl)+'</div>';}).join('');
}
function openPop(btn){
  var co=BY[btn.getAttribute('data-o')];if(!co)return;var I=ix(co);
  var cls=String(btn.getAttribute('data-c')||'').split(',').map(function(id){return I[id];}).filter(Boolean);if(!cls.length)return;
  var pop=$('pop');
  if(popTrigger===btn&&!pop.hidden){closePop();return;}
  popTrigger=btn;pop.innerHTML=popHtml(co,cls);pop.setAttribute('aria-label','Source for '+label(cls[0].f));pop.hidden=false;pop.classList.remove('open');
  var sheet=window.innerWidth<640;pop.classList.toggle('sheet',sheet);$('scrim').hidden=!sheet;
  if(!sheet){var r=btn.getBoundingClientRect(),w=Math.min(440,window.innerWidth-24);pop.style.width=w+'px';
    var left=Math.min(Math.max(12,r.right-w),window.innerWidth-w-12);if(r.left+w<window.innerWidth-12)left=Math.max(12,r.left);
    var ph=pop.offsetHeight,top=r.bottom+6;
    if(top+ph>window.innerHeight-12&&r.top-ph-6>12)top=r.top-ph-6;
    pop.style.left=(left+window.scrollX)+'px';pop.style.top=(top+window.scrollY)+'px';}
  else{pop.style.left='';pop.style.top='';pop.style.width='';}
  void pop.offsetWidth;pop.classList.add('open');
  btn.setAttribute('aria-expanded','true');pop.focus({preventScroll:true});
}
function closePop(back){var pop=$('pop');if(pop.hidden)return;pop.hidden=true;pop.classList.remove('open');$('scrim').hidden=true;
  if(popTrigger){popTrigger.setAttribute('aria-expanded','false');if(back!==false&&document.body.contains(popTrigger))popTrigger.focus({preventScroll:true});}popTrigger=null;}
function jump(id){var el=document.getElementById(id);if(!el)return;var d=el.closest('details');while(d){d.open=true;d=d.parentElement?d.parentElement.closest('details'):null;}
  el.scrollIntoView({block:id.indexOf('f-')===0?'center':'start'});
  if(id.indexOf('f-')===0){el.classList.add('flash');setTimeout(function(){el.classList.remove('flash');},1400);}}

/* ----- events ----- */
document.addEventListener('click',function(e){
  var t=e.target;
  var b=t.closest('button[data-c]');if(b){e.preventDefault();openPop(b);return;}
  if(t.closest('[data-close]')||t.closest('#scrim')){closePop();return;}
  if(!t.closest('#pop'))closePop(false);
  var j=t.closest('[data-jump]');if(j){e.preventDefault();jump(j.getAttribute('data-jump'));return;}
  var a=t.closest('[data-ask]');if(a){var q=$('askq');q.value=a.getAttribute('data-ask');ask(BY[$('askf').getAttribute('data-o')],q.value);return;}
  var ad=t.closest('[data-add]');if(ad){toggleCompare(ad.getAttribute('data-add'));return;}
  if(t.closest('[data-clear]')){var old=compare;compare=[];old.forEach(refreshBtns);drawCmpBar();return;}
  var rm=t.closest('[data-rm]');if(rm){compare=compare.filter(function(x){return x!==rm.getAttribute('data-rm');});
    location.hash=compare.length>=2?'compare='+compare.join(','):'compare';return;}
  var wb=t.closest('.whyb');if(wb){var wt=$(wb.getAttribute('aria-controls'));var op=wb.getAttribute('aria-expanded')!=='true';wb.setAttribute('aria-expanded',String(op));wt.hidden=!op;return;}
  var mo=t.closest('[data-more]');if(mo){var op2=mo.getAttribute('aria-expanded')!=='true';mo.setAttribute('aria-expanded',String(op2));$('sumrest').hidden=!op2;mo.textContent=op2?'Show less':'Read the full summary';return;}
  var ss=t.closest('[data-showsrc]');if(ss){var on=ss.getAttribute('aria-pressed')!=='true';ss.setAttribute('aria-pressed',String(on));ss.textContent=on?'Hide sources':'Show sources';
    $('sumtext').classList.toggle('showsrc',on);return;}
});
document.addEventListener('submit',function(e){if(e.target.id==='askf'){e.preventDefault();ask(BY[e.target.getAttribute('data-o')],$('askq').value);}});
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'){closePop();return;}
  var tag=(e.target.tagName||'').toLowerCase();
  if(e.key==='/'&&tag!=='input'&&tag!=='textarea'&&tag!=='select'){e.preventDefault();if($('q'))$('q').focus();else{location.hash='';setTimeout(function(){if($('q'))$('q').focus();},0);}return;}
  if((e.key==='ArrowDown'||e.key==='ArrowUp')&&(e.target.classList.contains('cl')||e.target.id==='q')){
    var links=[].slice.call(document.querySelectorAll('.cl'));if(!links.length)return;e.preventDefault();
    var i=links.indexOf(e.target);i=e.target.id==='q'?0:Math.max(0,Math.min(links.length-1,i+(e.key==='ArrowDown'?1:-1)));links[i].focus();}
});
window.addEventListener('resize',function(){closePop(false);});

/* ----- routing ----- */
var lastView='';
function route(){
  closePop(false);
  var h=decodeURIComponent(location.hash.slice(1)),main=$('main');
  if(h==='about'){var ab=$('about');if(ab)ab.scrollIntoView();return;}
  if(/^(q-|f-|s-)/.test(h)&&main.innerHTML){jump(h);return;}
  if(lastView==='home')homeY=window.scrollY;
  var m=/^org-(\d{9})$/.exec(h),c=/^compare(?:=([\d,]*))?$/.exec(h);
  if(m){main.innerHTML=company(m[1]);lastView='co';document.title=(BY[m[1]]?dn(BY[m[1]].n)+' | ':'')+'Kilde';}
  else if(c){var l=c[1]?c[1].split(','):compare.slice();compare=l.filter(function(o){return BY[o];}).slice(0,3);main.innerHTML=compareView(compare);lastView='cmp';document.title='Compare | Kilde';}
  else{current=null;main.innerHTML=home();bindHome();applyFilters();document.title=TITLE;
    var y=lastView&&lastView!=='home'?homeY:0;lastView='home';window.scrollTo(0,y);drawCmpBar();return;}
  drawCmpBar();window.scrollTo(0,0);main.focus({preventScroll:true});
}
var TITLE=document.title;
function about(){
  var r=META,ms=r.runtime_ms,rt='not recorded';
  if(ms!=null){var s=Math.round(ms/1000);rt=s>=60?Math.floor(s/60)+' min '+(s%60)+' s':s+' s';}
  var cost=Number(r.cost_usd||0),a=r.agent||{};
  var items=[['Run',esc(r.run_id||'not recorded')],['Run started',r.started_at?dfmt(r.started_at)+', '+tfmt(r.started_at):'not recorded'],['Companies',nfmt(r.companies)],
    ['Web requests',nfmt(r.requests)],['Runtime',rt],['Cost of paid services',cost===0?'$0, none used':'$'+cost.toFixed(2)],
    ['Built by',esc(a.name||'Kilde')+(a.version?' '+esc(a.version):'')+(a.commit?', commit <span class="num">'+esc(String(a.commit).slice(0,7))+'</span>':'')]];
  $('runinfo').innerHTML=items.map(function(x){return '<div><dt>'+x[0]+'</dt><dd>'+x[1]+'</dd></div>';}).join('');
  $('srclist').innerHTML='<table class="srct"><thead><tr><th scope="col">Source</th><th scope="col">Licence or terms</th><th scope="col" class="r">Facts backed</th></tr></thead><tbody>'+
    (r.sources||[]).map(function(s){return '<tr><td>'+esc(s.name)+'</td><td>'+esc(s.licence||'')+'</td><td class="r num">'+(s.evidence!=null?nfmt(s.evidence):'')+'</td></tr>';}).join('')+'</tbody></table>';
}
$('main').innerHTML='<p class="loading">Loading companies</p>';
loadData().then(function(d){
  DATA=d;U=d.u;CO=d.co;CO.forEach(function(c){BY[c.o]=c;});
  about();route();window.addEventListener('hashchange',route);
}).catch(function(err){$('main').innerHTML='<p class="loading">This browser could not open the report data ('+esc(String(err&&err.message||err))+').</p>';});
})();
"""

# Figtree variable font, Latin subset, weights 400 to 700. SIL Open Font License 1.1 (see module docstring).
FONT_WOFF2_B64 = (
    "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"
)
