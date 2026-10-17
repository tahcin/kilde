# 100-company smoke test

Produced on Oct 17, 2026 from a fresh clone of this repository at commit `0d4caa7` (revision 2), with the documented command:

```bash
uv sync --frozen
uv run --frozen kilde run --organisations smoke/smoke-companies.jsonl --output smoke/envelopes.jsonl --run-id smoke-100 --expected-count 100
```

`smoke-companies.jsonl` is a random sample of 100 companies from Builderr's public universe (seed 2026), none used during development.

| File | What it is |
|---|---|
| `envelopes.jsonl` | one terminal envelope per company (the scored output) |
| `profiles.jsonl` | the same facts flattened for reading |
| `run-report.json` | runtime, requests by host, DNS lookups, coverage, validation checks, p50/p95 per company |
| `report.html` | the self-contained report; open it in a browser |
| `snapshots/` | every saved response behind a quoted span, named by SHA-256 (Brønnøysund role responses have birth dates removed before hashing) |
| `determinism.json` | the same command run twice: 0 companies with different facts (6,410 available facts in each run) |

Result: 100 of 100 envelopes, validation passed, 1,421 outbound HTTP requests, about 6.6 minutes while five other runs shared the same connection, $0.
