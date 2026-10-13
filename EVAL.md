# Evaluation

How Kilde was measured before submission, and what each round changed. Every round used a fresh random sample of 100 companies from Builderr's public universe (411,160 companies with 2025 accounts), never reused across rounds.

## Method

1. Run the exact submission command on the sample, uncached.
2. Score with `scorer/score.py`, a mirror of Builderr's rubric: recall 50 (per field family, 70% company coverage and 30% fact recall against a reference pool), precision and evidence 30, synthesis 12, UX 8, with hard fails for any wrong-company fact or invented financial value. The default view weights the four families Builderr reports in its feedback (company website, social profile, hiring signal, dated news); `--broad` weights every family equally.
3. An independent auditor (a separate agent that did not build Kilde) checks 30 companies by hand against the live sources: every non-registry claim, every summary sentence, and all registry and accounts claims by script against the live Brønnøysund APIs. It also reviews every website-derived and public-record claim across all 100 companies for wrong-company risk.
4. The auditor searches the web by hand like a competing crawler for facts Kilde missed. Those finds join the reference pool, so recall is measured against more than Kilde's own output.
5. Fix the weakest area with a general rule, never a one-off patch, and move to a new sample.

Stop rule: two consecutive rounds with zero wrong-company facts and clearly above the qualifying line.

## Rounds

| Round | Recall /50 | Evidence /30 | Synthesis /12 | UX /8 | Wrong-company facts | Cause and fix |
|---|---|---|---|---|---|---|
| 1 | 42.2 (broad) | 29.8 | 12 | 8 | 4 | A group website shared by sister companies. Fix: derived facts only from sites exclusive to the company. |
| 2 | 48.6 (broad) | 29.7 | 12 | 8 | 14 | A domain redirecting into a chain's site, a Swedish sister company's site, a shared school site, a phone from a privacy page. Fix: redirect and foreign org-number rules, contacts never from legal pages, independent publication gate. |
| 3 | 47.7 (broad) | 29.8 | 12 | 8 | 8 | An owners' association matched by name and address to a namesake hotel operator at the same address. Fix: Brønnøysund name-collision check for any proof other than the org number; housing entities only by org number or registry declaration. |
| 4 | 43.9 | 30.0 | 12 | 8 | 2 | A registry website shared by four group companies; the parent's social profiles published. Fix: shared-registry-website check, privacy-page operator check. |
| 5 | 37.4 | 30.0 | 12 | 8 | **0** | Clean. Total 79.4 (four families), 86.7 (broad). Three wrong values fixed: a phone parsed from a date, a personal page taken as a careers page, a placeholder Facebook link. |
| 6 | 32.9 (45.7 broad) | 30.0 | 12 | 8 | **0** | Clean. Total 74.8 (four families), 87.6 (broad). Second clean round in a row. One wrong value fixed: a headhunter's client-assignment page taken as its careers page. |

Across all rounds every registry, accounts and update-feed claim checked against the live APIs was correct (about 9,000 claims), and no financial value was ever invented.

## Determinism

Builderr runs each entry twice and compares the facts. The same 100 companies were run twice, uncached: the first drill found 4 companies differing because website steps timed out under load; after the fixes (rollback on timeout, a retry pass, a larger connection pool) two runs produced identical facts.

## Benchmark against Builderr's own sample

On the 100 companies of Builderr's public sample (https://builderr.ai/signalpost), compared with Builderr's own crawler:

| Family | Builderr's crawler | Kilde |
|---|---|---|
| Verified websites | 84 | measured at the final commit |
| Social profiles | 87 across 43 companies | measured at the final commit |
| Leadership roles | 714 | 707 |
| Workplaces | 180 | 180 |
| Companies with filed financials | 97 | 97 |
| Dated news posts | not in sample | measured at the final commit |
| LinkedIn data | collected by Builderr as "experimental" | not collected (unofficial access is not permitted) |
