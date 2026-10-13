# Kilde

**Kilde** (Norwegian for "source") is a company research agent for the Builderr Signalpost challenge. Give it Norwegian organisation numbers. For each one it returns a profile of public facts, and every fact carries the URL it came from, when it was retrieved, a SHA-256 of the saved response, the exact quoted text, and how it was tied to that organisation number. What it cannot find is marked as such, never as zero.

- Deterministic: no language model is needed, and the declared third-party cost is **$0**.
- Exact-entity first: a fact is published only when it can be tied to the organisation number. Anything weaker is `ambiguous` and stays out of the profile.
- One terminal envelope per input, always, even when sources fail or the budget runs out.

## Run it

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --frozen
uv run kilde run --organisations batch.jsonl --output out/envelopes.jsonl
```

That is the whole command. `batch.jsonl` can be JSONL rows (the public universe format, or `{"organisation_number": "..."}`), a JSON list, or plain text with one number per line.

It writes, next to `--output`:

| File | What it is |
|---|---|
| `envelopes.jsonl` | one terminal envelope per input company (the scored output) |
| `profiles.jsonl` | the same facts flattened for reading |
| `run-report.json` | runtime, outbound requests by host, DNS lookups, third-party cost, coverage by section, validation checks |
| `report.html` | a self-contained report: search, filters, compare up to three companies, every value linked to its source and quote; works offline and on a phone |
| `snapshots/` | every response that backs a claim, named by its SHA-256 |

The flags of the Signalpost starter kit are accepted too, so a harness written for the starter can call Kilde unchanged:

```bash
uv run kilde run --organisations smoke-companies.jsonl --bulk brreg-enheter.csv \
  --profiles-output out/smoke-profiles.jsonl --output out/smoke-envelopes.jsonl \
  --report out/smoke-report.json --run-id smoke-001 --expected-count 100
```

Options: `--previous <envelopes.jsonl>` (refresh against an earlier run), `--state-dir` (default `state/`, keeps the latest envelopes so the next run diffs against them), `--max-requests`, `--time-budget` (seconds), `--concurrency`. The same budgets can be set with `KILDE_MAX_REQUESTS` and `KILDE_TIME_BUDGET`.

## Envelope

```json
{
  "organisation_number": "999999999",
  "agent": {"name": "Kilde", "version": "1.0.0", "commit": "..."},
  "run": {"run_id": "...", "started_at": "...", "completed_at": "...", "terminal_status": "completed"},
  "legal_name": "...",
  "sections": {"identity": {"state": "available", "claim_ids": ["c1"]}, "financials": {}, "leadership": {},
               "locations": {}, "web_presence": {}, "hiring": {}, "activity": {}},
  "claims": [{"id": "c1", "field": "official_website", "section": "web_presence", "value": "https://...",
              "availability": "available", "confidence": 0.99, "as_of": "2026-10-13",
              "match": {"method": "orgnr_on_page", "detail": "..."}, "evidence_ids": ["e4"]}],
  "evidence": [{"id": "e4", "source_url": "https://...", "source_class": "company_owned", "retrieved_at": "...",
                "content_sha256": "...", "snapshot_path": "snapshots/ab/ab....html", "claim_span": "Org.nr: 999 999 999",
                "span_exact": true, "extraction_method": "website_identity_v1", "licence": "..."}],
  "summary": {"text": "...", "sentences": [{"text": "...", "claim_ids": ["c1"]}], "unknowns": ["..."]},
  "changes": [],
  "errors": [],
  "operations": {"requests": 14, "third_party_cost_usd": 0, "llm_calls": 0}
}
```

Availability states: `available`, `not_available` (checked, nothing there), `blocked` (robots.txt or the source refused), `not_applicable`, `ambiguous` (could not prove it is this company), `failed` (source error, timeout or budget). The seven sections follow the brief: identity and public brand, latest accounts and history, leadership and workplaces, verified website and company-owned profiles, hiring, dated public activity, and refresh changes.

`claim_span` is always copied verbatim from the saved snapshot (`span_exact: true`), so it can be found with a plain text search. JSON evidence quotes the exact `"key":value` pair, scoped to the right record; extra supporting quotes are in `span_context`.

## Sources

| Source | Facts | How it is tied to the org number | Licence and access |
|---|---|---|---|
| Brønnøysund Enhetsregisteret API | identity, legal form, addresses, industry, sector, registered employees, share capital, register memberships, status flags, registry website, email and phone, group parent and subsidiaries, sub-units (workplaces), roles (general manager, board, auditor, accountant), signature rules | org number in the endpoint | NLOD 2.0, open API, no key |
| Brønnøysund update feed | dated registry changes in the last 12 months, with the fields that changed | org number filter | NLOD 2.0 |
| Regnskapsregisteret API | filed accounts for the last three years: revenue, operating result, salary costs, profit before tax, annual result, total assets, cash, equity, debt, audit status; annual-report PDF list | org number in the endpoint | NLOD 2.0 |
| The company's own website | official website, description, contact email and phone, social profiles it links to, structured data, sitemap dates, careers page | the site prints the org number, or shows the registry phone, or the full registered address with the company name, or is the registry-listed website and shows the name | company-owned pages; robots.txt obeyed (RFC 9309), at most about 8 requests per company |
| NAV job ads (pam-stilling-feed) | active job ads with title, location, published and expiry dates | employer org number in the ad | NAV terms of use, public token |
| GLEIF | LEI, status, renewal date, direct and ultimate parents | `registeredAs` equals the org number | CC0 |
| Finanstilsynet registry API | financial-sector licences | org number field in the record | open API |
| Wikidata (via QLever, then EntityData JSON) | ticker, ISIN, website, socials, employees for larger firms | P2333 equals the org number | CC0; robots-compliant route |
| SSB Klass (bundled) | readable names for industry, sector and legal-form codes | code lookup | CC BY 4.0 |

Not used: aggregators whose terms forbid automated copying (Proff, Forvalt, 1881, Gule Sider, regnskapstall, OpenCorporates), search-engine result pages, Brønnøysund announcement pages (robots.txt disallows them), and LinkedIn, Meta, Glassdoor or Indeed scraping. Social profiles are reported only when the verified company site links to them; Kilde never crawls the platforms.

Contains data under the Norwegian licence for Open Government data (NLOD) distributed by Brønnøysundregistrene. Code names come from Statistics Norway (SSB), Klass, licensed CC BY 4.0. Personal names are shown only as registered roles of the company looked up; birth dates are dropped.

## How a company match is proven

1. Registry, accounts and update-feed facts come from endpoints keyed by the org number.
2. A website is published only with proof on the site itself: our org number, the registry phone number, or the full registered street address and postcode together with the company name, or it is the registry-listed website and shows the name. Error, parked and for-sale pages never count.
3. A site that prints another company's org number and not ours (a housing-co-op manager, parent or accountant) is reported as a related site with that number, never as the official site.
4. Social profiles come only from links on a verified site, and only when the handle carries the company's name or domain or sits in the site's header, footer or structured data.
5. Job ads, LEI records, licences and Wikidata items match on an org-number field.
6. Financial figures are copied exactly as filed, with currency, period and account type (company or group). Kilde never computes, converts or estimates a figure.
7. When proof is missing the field is `ambiguous` or `not_available`. Abstaining is preferred to guessing.

## Refresh

Every run can diff against the previous envelopes (`--previous`, or automatically from `state/`). Changes are typed (`new_role`, `ended_role`, `new_filing`, `changed_address`, `new_job`, `closed_job`, `new_location`, `changed_website`, ...), marked material or not, and carry both the previous value with its original evidence and the current claim. If a source fails on a rerun, the last supported value is carried forward with its original evidence and a note, never erased. A rerun against unchanged sources yields no changes. Independently of previous runs, the Brønnøysund update feed gives dated registry changes for the last 12 months.

## Budget and cost

Every request goes through one budgeted client. Defaults: 20 outbound HTTP requests per company and 45 minutes per batch, both configurable. Identity, roles, accounts and workplaces for every company run first; external sources share what is left, and every step has a timeout, so a slow website cannot stall the batch. When time or requests run out, the remaining facts are marked `failed` with the reason and every envelope is still written.

| Item | Cost |
|---|---|
| Language model | none ($0) |
| APIs | open public APIs, no keys ($0) |
| Expected cost per official run | **$0** |

## Caches

No precomputed profiles are shipped. `KILDE_CACHE_DIR` turns on a local HTTP cache for development only; it is off by default and cached responses keep their original retrieval time.

## Tests and evaluation

```bash
uv run --extra dev pytest -q
uv run python scorer/score.py --envelopes out/envelopes.jsonl --expected-count 100 --report-html out/report.html --out out/score.json
```

`scorer/` mirrors the Signalpost rubric (recall 50, precision and evidence 30, synthesis 12, UX 8) with hard fails for wrong-company facts and invented financial values, plus tools to sample batches and build audit packs. Evaluation rounds and audits are summarised in [EVAL.md](EVAL.md).

## Limitations

- Small companies often have no website, and many sites do not print the org number, so the website family is the main gap. Kilde chooses to miss a site rather than publish one it cannot prove.
- Regnskapsregisteret's open API returns the last three filed years and is labelled temporary by Brønnøysund; banks and insurers are not covered by it.
- Wikidata and GLEIF cover mainly larger companies.

## Licence

MIT. See [LICENSE](LICENSE).
