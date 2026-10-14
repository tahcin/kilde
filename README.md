# Kilde

**Kilde** (Norwegian for "source") is a company research agent for the Builderr Signalpost challenge. Give it Norwegian organisation numbers. For each one it returns a profile of public facts, and every fact carries the URL it came from, when it was retrieved, a SHA-256 of the saved response, the exact quoted text, and how it was tied to that organisation number. What it cannot find is marked as such, never as zero.

- Deterministic: no language model is needed, and the declared third-party cost is **$0**.
- Exact-entity first: a fact is published only when it can be tied to the organisation number. Anything weaker is `ambiguous` and stays out of the profile.
- One terminal envelope per input, always, even when sources fail or the budget runs out.

## Run it

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --frozen
uv run --frozen kilde run --organisations batch.jsonl --output out/envelopes.jsonl
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
uv run --frozen kilde run --organisations smoke/smoke-companies.jsonl --bulk brreg-enheter.csv \
  --profiles-output out/smoke-profiles.jsonl --output out/smoke-envelopes.jsonl \
  --report out/smoke-report.json --run-id smoke-001 --expected-count 100
```

Options: `--previous <envelopes.jsonl>` (refresh against an earlier run), `--state-dir <folder>` (off by default; keeps the latest envelopes so the next run diffs against them), `--max-requests`, `--time-budget` (seconds, default 1800), `--concurrency`, `--bulk` (Builderr's frozen registry snapshot, used for identity if the live API fails). The starter kit's `--workers`, `--resume`, `--checkpoint-every` and `--modules` are accepted and ignored. The same budgets can be set with `KILDE_MAX_REQUESTS` and `KILDE_TIME_BUDGET`. An invalid or duplicate input never stops the batch: each distinct input gets exactly one envelope.

## Results

- [`smoke/`](smoke/): the 100-company smoke test Builderr asks for, produced from a clean clone with the command above, with the HTML report, every saved snapshot, and a second run showing identical facts.
- [`profiles-1000/`](profiles-1000/): 1,000 company profiles from a clean clone (about 14 requests per company, $0).
- [EVAL.md](EVAL.md): sixteen evaluation rounds on fresh random samples, each audited against live sources, version 1 against version 2, independent judge reviews, the determinism drill, and a benchmark against Builderr's own 100-company sample.

## Envelope

```json
{
  "organisation_number": "999999999",
  "agent": {"name": "Kilde", "version": "1.0.0", "commit": "..."},
  "run": {"run_id": "...", "started_at": "...", "completed_at": "...", "terminal_status": "completed"},
  "legal_name": "...",
  "sections": {"identity": {"state": "available", "claim_ids": ["c1"]}, "financials": {}, "leadership": {},
               "locations": {}, "web_presence": {}, "hiring": {}, "activity": {}},
  "claims": [{"id": "c1", "field": "official_website", "section": "web_presence", "value": "example.no",
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

Values in the four external families use Builderr's published shapes: `official_website` is the registered domain (`example.no`, full address in `official_website_url`), `social_profile` is `platform.com/handle` in lower case without scheme or `www`, `hiring_signal` is a careers page or job posting URL, and `dated_news` is `title (datetime)` with the source's own datetime and offset. NAV ads are `job_ad` claims.

For every published (`available`) claim, `claim_span` is copied verbatim from the saved snapshot (`span_exact: true`), so it can be found with a plain text search. Evidence for an absence (`not_available`) may describe the empty result instead and is then marked `span_exact: false`. Brønnøysund role responses are saved with people's birth dates removed before hashing; such evidence says so in its note. JSON evidence quotes the exact `"key":value` pair, scoped to the right record; extra supporting quotes are in `span_context`.

## Sources

| Source | Facts | How it is tied to the org number | Licence and access |
|---|---|---|---|
| Brønnøysund Enhetsregisteret API | identity, legal form, addresses, industry, sector, registered employees, share capital, register memberships, status flags, registry website, email and phone, group parent and subsidiaries, sub-units (workplaces), roles (general manager, board, auditor, accountant), signature rules | org number in the endpoint | NLOD 2.0, open API, no key |
| Brønnøysund update feed | dated registry changes in the last 12 months, with the fields that changed | org number filter | NLOD 2.0 |
| Regnskapsregisteret API | filed accounts for the last three years: revenue, operating result, salary costs, profit before tax, annual result, total assets, cash, equity, debt, audit status; list of filed annual-report PDFs | org number in the endpoint | NLOD 2.0 |
| The company's own website | official website, contact email and phone, social profiles it links to, dated news posts, careers page or job postings, description | the site prints the org number, or shows the registry phone or the full registered address with the company name, or is the registry-listed website and shows the name, or one of the stricter name proofs (below); then exclusivity and name-collision checks | company-owned pages; robots.txt obeyed (RFC 9309), at most 16 requests per company |
| NAV job ads (pam-stilling-feed) | active job ads with title, location, published and expiry dates | employer org number in the ad (workplace numbers mapped to the parent through Enhetsregisteret) | NAV API terms; public token fetched at run time |
| TED (EU procurement journal) | public contracts won, procurement notices published as a buyer | winner or buyer national ID equals the org number | free reuse of TED notices |
| Research Council of Norway, SkatteFUNN | funded research and innovation projects and approved SkatteFUNN projects | project owner org number | NLOD 2.0 (Research Council open data); SkatteFUNN lists published by the Research Council |
| Støtteregisteret (Brønnøysund) | state aid awards with date, amount, authority and scheme | recipient org number | NLOD 2.0 |
| Mattilsynet Smilefjes | food safety inspections with date and result | org number of the inspected premises (workplace numbers mapped to the parent) | CC BY 4.0; downloaded fresh each run |
| DiBK sentral godkjenning | central approval for construction work, validity and areas | org number in the register page URL | public register page (robots.txt allows `/enterprises/`) |
| GLEIF | LEI, status, renewal date, direct and ultimate parents | `registeredAs` equals the org number | CC0 |
| Finanstilsynet registry API | financial-sector licences and agent registrations | org number field in the record | open API |
| Wikidata (via QLever, then EntityData JSON) | ticker, ISIN, website, socials, employees for larger firms | P2333 equals the org number | CC0; robots-compliant route |
| SSB Klass (bundled) | readable names for industry, sector and legal-form codes | code lookup | CC BY 4.0 |

Social profiles come from links on an exclusive verified company site, from the registry website field, or from Wikidata items matched on the org number. Not used: aggregators whose terms forbid automated copying (Proff, Forvalt, 1881, Gule Sider, regnskapstall, OpenCorporates), search-engine result pages, Brønnøysund announcement pages (robots.txt disallows them), NAV's internal search, DiBK's `/api/` path (robots.txt disallows it), and LinkedIn, Meta, Glassdoor or Indeed scraping. Kilde never crawls the platforms.

Contains data under the Norwegian licence for Open Government data (NLOD) distributed by Brønnøysundregistrene. Code names come from Statistics Norway (SSB), Klass, licensed CC BY 4.0. Website candidate seeds contain information from OpenStreetMap (© OpenStreetMap contributors, ODbL 1.0; the seeds file is shared under ODbL) and Overture Maps (CDLA-Permissive 2.0); some candidates come from Common Crawl (crawl CC-MAIN-2026-39, used under the Common Crawl Terms of Use, https://commoncrawl.org/terms-of-use), which keep only org number and domain, never page content. Kilde's own crawl (2026-10-14) fetched public company front, contact, about and privacy pages as KildeCacheBuilder/1.0, obeying robots.txt with one request at a time per host; its domain list came from the Common Crawl host graph cc-main-2026-jul-aug-sep and .no index (Common Crawl Terms of Use), Brreg websites and email domains (NLOD 2.0), the Tranco list, name-derived domains and outbound links, resolved with ProjectDiscovery dnsx 1.3.1; pages were matched against the Enhetsregisteret bulk files of 2026-10-13 (NLOD 2.0), and only domain and source tags are kept. Mattilsynet Smilefjes data is CC BY 4.0. SkatteFUNN lists: no licence stated; public information published by the Research Council of Norway (forskningsradet.no). TED notices may be freely reused. GLEIF and Wikidata data are CC0. Personal names are shown only as registered roles of the company looked up; birth dates are dropped.

## How a company match is proven

1. Registry, accounts, update-feed, state-aid, procurement, research, inspection, approval, LEI and licence facts come from records keyed by the org number.
2. A website is published only with proof on the site itself: our org number, or the registry phone in a block naming the company, or the full registered address with the company name, or it is the registry-listed website and shows the name. Three stricter name proofs cover sites that print none of these: `legal_name_unique` (the exact registered name with its legal form, on a domain that is exactly that name, with the post town or a role holder on the site and no other registered entity carrying the whole name), `registry_email_domain_and_name` (the registry email's domain carries our name and the site shows it), and a workplace-name proof (a registered sub-unit's whole name as the domain, with its registered address on the site). Error, parked, for-sale and placeholder pages never count.
3. Any proof other than our org number triggers a Brønnøysund name-collision search (retried; a search that still fails marks the step `failed`, never decided): if another active company shares the distinctive name, address or phone, the site is `ambiguous`. A name counts only as visible words, never inside an email, URL, domain or handle. An address proves nothing when another registered entity has the same street address and postcode, and a namesake at our address or in the site's title or main heading makes any name-based proof ambiguous. Housing co-ops and owners' associations get a website only from their org number on the page or their own registry entry.
4. Facts taken from a website (contacts, social profiles, news, careers pages) are published only when the site is exclusive to the company: no other org number of any country on fetched pages, no other registered company in an operator position (header, footer, contact, privacy controller, copyright), no redirect into another company's domain, and no other company declaring the same site in the registry unless our org number sits in an operator position. Platform names in badges and trademark notices never count as an operator, and contact addresses on e-invoice, accounting and payment platform domains are services the company uses, not other organisations. A phone or email in the same line, row or staff card as another company's name is withheld. Otherwise only the website itself is published and the rest is `ambiguous` with the reason.
5. An independent publication gate re-checks every derived fact against the registry before output (domain carries the company's name or is its registry website or email domain, foreign country-code domains, contact emails on the site's or registry's domain, foreign phones only when they equal the registry phone).
6. Social profiles come only from links on an exclusive verified site; placeholder handles and profiles naming another registered company are rejected.
   Dated news comes from the exclusive verified site only: its RSS or Atom feeds (WordPress, Wix, Squarespace and linked feeds), then its news listing (found by a news path or a link text such as Nyheter, Aktuelt, Blogg or Our News, also on a subdomain such as blog.example.no), and for a listing without dates the article pages' own declared publication date (article:published_time, JSON-LD datePublished, or a single labelled byline date). Every item keeps the source's date and exact title, never predates the company, and site builders' default posts (Hello world, "This is your first post", lorem ipsum, sample pages) and near-empty stub posts are never news or page updates. A post whose slug, title or text points to a later year than its publish date is a reused post and is withheld, never re-dated. A sitemap `lastmod` day shared by a large part of the sitemap is a bulk regeneration, not a page update. Slash dates are read only in the order the page itself shows (24/8/2026 or 8/24/2026). A hiring signal is a careers page on the site, or, when there is none, the company's own board on a recruitment platform (Teamtailor, Webcruiter, Jobbnorge, Recman, Lever and others) linked from the site and carrying the company's name; the platform is not fetched, and staffing firms' boards are not used.
7. Financial figures are copied exactly as filed, with currency, period and account type (company or group). Kilde never computes, converts or estimates a figure. A summary sentence uses one account type only.
8. When proof is missing the field is `ambiguous` or `not_available`. Abstaining is preferred to guessing.

## Refresh

Every run can diff against the previous envelopes (`--previous`, or automatically from `state/`). Changes are typed (`new_role`, `ended_role`, `new_filing`, `changed_address`, `new_job`, `closed_job`, `new_location`, `changed_website`, ...), marked material or not, and carry both the previous value with its original evidence and the current claim. If a source fails on a rerun, the last supported value is carried forward with its original evidence and a note, never erased. A rerun against unchanged sources yields no changes. Independently of previous runs, the Brønnøysund update feed gives dated registry changes for the last 12 months.

## Budget and cost

Every request goes through one budgeted client. Defaults: 20 outbound HTTP requests per company and 30 minutes per batch (`--time-budget 1800`), both configurable. Identity, roles, accounts and workplaces for every company run first; external sources share what is left. Each step has a timeout; a step that times out rolls back anything it published half-way, and timed-out website steps get one unhurried second pass. DNS or network failures are reported as `failed`, never as absence. Transient failures (timeouts, HTTP 429 and 5xx, failed registry searches) end as `failed` and are retried in a second pass, so a slow site never turns into a different answer on the next run. When time or requests run out, the remaining facts are marked `failed` with the reason and every envelope is still written.

Measured from a fresh clone (version 2): 1,000 companies in 20.8 minutes with 14,128 requests (about 14 per company), while other runs shared the connection; 100 companies in about 4 minutes with 1,515 requests. Details in [EVAL.md](EVAL.md). The run report gives HTTP requests and DNS lookups separately; DNS lookups are used only to drop name-derived domains that do not exist before any HTTP request. Fetches to private, loopback, link-local or other non-public addresses are refused, including on redirects. Two runs of the same batch produce identical facts (Builderr runs each entry twice and compares).

| Item | Cost |
|---|---|
| Language model | none ($0) |
| APIs | open public APIs, no keys ($0) |
| Expected cost per official run | **$0** |

## Caches

Kilde ships declared caches of public data, each with a script that rebuilds it. Every cached fact keeps its source, original retrieval time and hash; facts that can change are re-checked live during the run where the source allows it.

| File | What it holds | Rebuild | Live refresh during the run |
|---|---|---|---|
| `src/kilde/data/nav_jobs_index.json.gz` | active NAV job ads by company org number | `scripts/build_nav_index.py` | new feed pages since the index date, and each matched ad re-fetched (stopped ads are never shown) |
| `src/kilde/data/ted.json.gz` | TED notices with Norwegian org numbers as winner or buyer | `scripts/build_ted.py` | recent notices fetched |
| `src/kilde/data/smilefjes.json.gz` | place-to-company mapping for Smilefjes | `scripts/build_smilefjes.py` | the inspection file is downloaded fresh every run |
| `src/kilde/data/forskningsradet.json.gz` | Research Council projects and SkatteFUNN approvals | `scripts/build_forskningsradet.py` | newer lists fetched when published |
| `src/kilde/data/stotteregisteret.json.gz` | state aid awards since 2021 for universe companies | `scripts/build_stotteregisteret.py` | none (the full export is 306 MB) |
| `src/kilde/data/website_seeds.json.gz` | candidate website domains for 76,514 companies (1.1 MB; only domain and source tags, no page content or personal data). By source (orgs): Kilde's own crawl of 2026-10-14 (crawl_orgnr 25,601, crawl_phone_email 16,327, crawl_signals 42,770; 54,533 companies after the merge's drop rules), Common Crawl CC-MAIN-2026-39 pages printing the org number (commoncrawl 9,177; build snapshot of 2026-10-14 08:35 IST) and domain-name matches (commoncrawl_domain 30,546), NAV employer homepages (nav 727), OpenStreetMap (osm_orgnr 552, osm_phone 781), Overture Maps (overture_phone 11,358, overture_email 1,671), registered sub-units (subunit 4,663) and Wikidata (992). Licences per source in `website_seeds.LICENSE.md`. Candidates only, never published without live proof | `scripts/build_website_seeds.py`, then in order `scripts/merge_cc_seeds.py --state ...`, `scripts/merge_crawl_seeds.py --matches ... --pages ...` (every page file of the crawl) and `scripts/merge_seed_file.py --source nav`; the crawl itself is described below | every candidate is fetched and proven live |
| `src/kilde/data/codes.json` | SSB code names | `scripts/build_codes.py` | none needed |

### How the website-candidate cache was built

The crawl that produced the `crawl_*` seeds is not part of the run and its code is not in this repository; the run only reads the finished seeds file. It was a one-off job on 2026-10-14:

1. **Domain list (about 325,000 domains).** Registered domains from the Common Crawl host graph `cc-main-2026-jul-aug-sep` (`.no` hosts) and the CC-MAIN-2026-39 `.no` index, Brreg websites and email domains of entities and sub-units, the Tranco top list's `.no` entries, domains derived from registered names, and outbound links found on crawled pages. Government, social, webmail, aggregator and site-builder domains were dropped, and only domains that resolve (checked with ProjectDiscovery dnsx 1.3.1) were fetched.
2. **Crawl.** A Scrapy crawler with the user agent `KildeCacheBuilder/1.0 (+https://github.com/tahcin/kilde)` fetched front, contact, about and privacy pages, obeying robots.txt, one request at a time per host with a delay, pages capped at about 512 KB. It never fetched social networks, search engines or company aggregators.
3. **Matching.** Each page was matched against the Enhetsregisteret bulk files of 2026-10-13 (NLOD 2.0): our org number printed with a label (`crawl_orgnr`), a registry phone or email that belongs to exactly one entity (`crawl_phone_email`), or two independent signals such as the exact legal name and the street address with no other registered name on the page (`crawl_signals`). It tied 55,618 universe companies (13.5% of 411,160) to a domain. Sole proprietorships are never written to the cache.
4. **Merge.** `scripts/merge_crawl_seeds.py` applies the same drop rules as the Common Crawl merge (crowded domains, supplier and agency credits, domains printing several org numbers, multi-org matches) and stores only org number, domain and source tag.

To rebuild without the crawler, run `scripts/build_website_seeds.py` and `scripts/merge_cc_seeds.py`; the result has every source except `crawl_*`, and Kilde runs the same way with it (fewer candidates, so fewer websites found). Any crawl that writes the same match format can be merged with `scripts/merge_crawl_seeds.py`.

`KILDE_CACHE_DIR` turns on a local HTTP cache for development only; it is off by default.

## Tests and evaluation

```bash
uv run --extra dev pytest -q   # 460 tests
uv run python scorer/score.py --envelopes out/envelopes.jsonl --expected-count 100 --report-html out/report.html --out out/score.json
```

`scorer/` mirrors the Signalpost rubric (recall 50, precision and evidence 30, synthesis 12, UX 8) with hard fails for wrong-company facts and invented financial values, plus tools to sample batches and build audit packs. Evaluation rounds and audits are summarised in [EVAL.md](EVAL.md).

## Limitations

- Small companies often have no website, and many sites do not print the org number, so the website family is the main gap. Kilde chooses to miss a site rather than publish one it cannot prove, and withholds facts from shared or group sites.
- No search engine is used (official runs cannot use entrant keys), so websites are found only from the registry, the registry email domain, name-derived domains, open-data candidates and the declared crawl cache.
- Regnskapsregisteret's open API returns the last three filed years and is labelled temporary by Brønnøysund; banks and insurers are not covered by it.
- NAV, TED, Støtteregisteret and the Research Council cover only companies that appear in them, so most small companies have none of these facts.
- Wikidata and GLEIF cover mainly larger companies.

## Licence

The code is MIT (see [LICENSE](LICENSE)). Data files under `src/kilde/data/` keep the licences of their sources (listed above). Saved snapshots under `smoke/` and run outputs are third-party content kept only as evidence for the published claims.
