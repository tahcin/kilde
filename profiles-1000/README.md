# 1,000 company profiles

Produced on Oct 13, 2026 from a fresh clone of this repository at commit `557f0a5`, on 1,000 random companies from Builderr's public universe (seed 1000, none used during development):

```bash
uv run --frozen kilde run --organisations companies-1000.jsonl --output run1000/envelopes.jsonl --run-id profiles-1000 --expected-count 1000
```

| File | What it is |
|---|---|
| `envelopes.jsonl.gz` | one terminal envelope per company (gzip) |
| `profiles.jsonl.gz` | the same facts flattened for reading (gzip) |
| `run-report.json` | runtime, requests, coverage, validation |
| `report.html` | the self-contained report for all 1,000 companies |
| `snapshots.tar.gz` | the saved responses behind every quoted span |

Result: 1,000 of 1,000 envelopes, validation passed, 8,916 outbound HTTP requests (about 9 per company), 12.5 minutes, $0.

| Fact family | Companies | Facts |
|---|---|---|
| Verified official website | 120 | 120 |
| Social profiles | 51 | 108 |
| Hiring signals (careers pages, postings, NAV ads) | 19 | 32 |
| NAV job ads | 8 | 19 |
| Dated news posts | 22 | 249 |
| State aid awards (Støtteregisteret) | 94 | 578 |
| Research and SkatteFUNN projects | 23 | 60 |
| Public contracts won (TED) | 8 | 22 |
| Food safety inspections (Smilefjes) | 15 | 18 |
| DiBK construction approvals | 16 | 16 |
| LEI (GLEIF) | 125 | 127 |
| Financial-sector licences | 13 | 21 |

Every company also has registry identity, roles, workplaces where registered, filed accounts where filed, and dated registry changes.
