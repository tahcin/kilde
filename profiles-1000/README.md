# 1,000 company profiles

Produced on Oct 14, 2026 from a fresh clone of this repository at commit `5bfbb24`, on 1,000 random companies from Builderr's public universe (seed 1000, none used during development):

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

Result: 1,000 of 1,000 envelopes, validation passed, 12,608 outbound HTTP requests (about 13 per company), 12.3 minutes while three other runs shared the same connection, $0.

| Fact family | Companies | Facts |
|---|---|---|
| Verified official website | 115 | 115 |
| Related website (page on a shared or group site, labelled) | 29 | 29 |
| Social profiles | 62 | 120 |
| Hiring signals (careers pages, postings, NAV ads) | 22 | 35 |
| NAV job ads | 8 | 19 |
| Dated news posts | 29 | 346 |
| State aid awards (Støtteregisteret) | 94 | 578 |
| Research and SkatteFUNN projects | 23 | 60 |
| Public contracts won (TED) | 8 | 22 |
| Food safety inspections (Smilefjes) | 15 | 18 |
| DiBK construction approvals | 16 | 16 |
| LEI (GLEIF) | 125 | 127 |
| Financial-sector licences | 13 | 21 |

Every company also has registry identity, roles, workplaces where registered, filed accounts where filed, and dated registry changes.
