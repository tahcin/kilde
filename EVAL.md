# Evaluation

How Kilde was measured before submission, and what each round changed. Every round used a fresh random sample of 100 companies from Builderr's public universe (411,160 companies with 2025 accounts), never reused across rounds.

## Method

1. Run the exact submission command on the sample, uncached.
2. Score with `scorer/score.py`, a mirror of Builderr's rubric: recall 50 (per field family, 70% company coverage and 30% fact recall against a reference pool), precision and evidence 30, synthesis 12, UX 8, with hard fails for any wrong-company fact or invented financial value. The default view weights the four families Builderr reports in its feedback (company website, social profile, hiring signal, dated news); `--broad` weights every family equally.
3. An independent auditor (a separate agent that did not build Kilde) checks 30 companies by hand against the live sources: every non-registry claim, every summary sentence, and all registry and accounts claims by script against the live Brønnøysund APIs. It also reviews every website-derived and public-record claim across all 100 companies for wrong-company risk.
4. The auditor searches the web by hand like a competing crawler for facts Kilde missed. Those finds join the reference pool, so recall is measured against more than Kilde's own output.
5. Fix the weakest area with a general rule, never a one-off patch, and move to a new sample.

Stop rule: two consecutive rounds with zero wrong-company facts and clearly above the qualifying line.

Important: the recall figures below are measured against our own reference pool (Kilde's finds plus what the auditors found by hand for 30 companies per round). Builderr's pool combines every entrant's verified finds and its own crawlers, so recall, and therefore the total, will be lower against it. These numbers are for comparing our own versions, not for comparing with Builderr's board.

## Rounds

| Round | Recall /50 | Evidence /30 | Synthesis /12 | UX /8 | Wrong-company facts | Cause and fix |
|---|---|---|---|---|---|---|
| 1 | 42.2 (broad) | 29.8 | 12 | 8 | 4 | A group website shared by sister companies. Fix: derived facts only from sites exclusive to the company. |
| 2 | 48.6 (broad) | 29.7 | 12 | 8 | 14 | A domain redirecting into a chain's site, a Swedish sister company's site, a shared school site, a phone from a privacy page. Fix: redirect and foreign org-number rules, contacts never from legal pages, independent publication gate. |
| 3 | 47.7 (broad) | 29.8 | 12 | 8 | 8 | An owners' association matched by name and address to a namesake hotel operator at the same address. Fix: Brønnøysund name-collision check for any proof other than the org number; housing entities only by org number or registry declaration. |
| 4 | 43.9 | 30.0 | 12 | 8 | 2 | A registry website shared by four group companies; the parent's social profiles published. Fix: shared-registry-website check, privacy-page operator check. |
| 5 | 37.4 | 30.0 | 12 | 8 | **0** | Clean. Total 79.4 (four families), 86.7 (broad). Three wrong values fixed: a phone parsed from a date, a personal page taken as a careers page, a placeholder Facebook link. |
| 6 | 32.9 (45.7 broad) | 30.0 | 12 | 8 | **0** | Clean. Total 74.8 (four families), 87.6 (broad). Second clean round in a row. One wrong value fixed: a headhunter's client-assignment page taken as its careers page. |
| 7 | (final smoke) | 29.9 | 12 | 8 | 4 | A site built with the Surftown site builder carried Surftown's own social links in its footer. Fix: a site social handle must carry the company's name or domain (or be a numeric ID); profiles shared across companies in a batch withheld; site-builder and agency brands blocked. |
| 8 | (final smoke, 3 parallel auditors) | 30.0 | 12 | 8 | **0** | Clean. 2,212 audited items; the Surftown links confirmed gone. |
| 9 | (3 parallel auditors) | 30.0 | 12 | 8 | 4 | A Wikidata item conflating a pension foundation with its operating company; a foreign sister's YouTube channel on a branch's site. Fix: Wikidata websites and socials only when they match the site Kilde verified; handles naming another legal entity (GmbH, AB, Ltd...) withheld; no site news for foreign branches (NUF). |
| 10 | (3 parallel auditors) | 30.0 | 12 | 8 | **0** | Clean. 2,336 audited items. |
| 11 | (3 parallel auditors) | 30.0 | 12 | 8 | **0** | Clean. 3,028 audited items. Second clean round in a row after the last fix. |
| 12 | (3 parallel auditors) | 30.0 | 12 | 8 | 1 | A Shopify theme's default structured data (naming Shopify and its social accounts) published for a store. Fix: structured data must name the company; platform defaults withheld. |
| 13 | (version 1, 3 parallel auditors) | 30.0 | 12 | 8 | **0** | Clean: all 2,525 audited items correct. |
| 14 | (version 2 build, seed 1414) | not scored | n/a | n/a | 1 | A foundation was given the mosque website of a different entity at the same address, proved only by the address and the foundation's name run together inside an email address. Also 4 wrong or unsupported values. Fix (general rules): a name counts only as visible words, never inside an email, URL, domain or handle; an address proves nothing when another registered entity has the same street address and postcode; a namesake at our address or in the site's title or main heading makes any name-based proof ambiguous; contacts in the same line, row or card as another company's name are withheld. |
| 15 | (version 2 build, seed 1515) | not scored | n/a | n/a | **0** | Clean: 0 wrong-company facts, 0 invented financials. 3 wrong values, all fixed: a capped count given as the total, a reused post's date, a missing reason for a deceased general manager. |
| 16 | (version 2 build d3461f4, seed 1616) | not scored | n/a | n/a | **0** | Clean: 0 wrong-company facts, 0 invented financials. 1 wrong value, 1 unsupported quoted span, 3 summary sentences that did not cite every claim they counted and 1 missed news listing, all fixed: summaries now cite every counted claim. |
| 17 | (confirmation, code 853e897, seed 1717, 3 parallel auditors) | not scored | n/a | n/a | **0** | Clean: 0 wrong-company facts, 0 invented financials across 2,778 audited items. 4 wrong values and 2 wordings, all fixed in 5bfbb24: a product brand taken as the site's presenter, a site-builder host URL given as the website, a broken +53 phone, an error-redirect URL. The final release rerun of seed 1717 changed only those facts, plus two sites recovered (abcorneliussen.no's facts and tellierservice.no) that the auditors had confirmed as the companies' own. |
| 18 | (code 45e8ad3, seed 1818, 3 parallel auditors) | not scored | n/a | n/a | **0** | 0 wrong-company facts, 0 invented financials across about 2,700 audited items. 3 of the companies' own sites were mislabelled as shared through false name-part matches (Optikk 2020, Agder Bolig Entreprenør, Høilund Bygg). Fix: another entity counts on a site only where its own name or org number appears. |
| 19 | (code e1375ce, seed 1919, web-fact audit of all 252 web claims and 2,328 financial amounts) | not scored | n/a | n/a | 1 | A housing co-op's homepage quoted a festival hotline in a notice, taken as the co-op's contact phone; a rescan found a second case (a consumer council number on a terms page). Also 14 non-news items (concert listings, FAQ pages). Fix: contacts only from contact positions, never beside another organisation's address; FAQ, event, artist and search entries are not news. |
| 20 | (final code 12b459d, seed 2020) | not scored | n/a | n/a | **0** | Clean: all 159 web claims, 42 citing summary sentences, 2,478 financial amounts, 96 share-capital values and 138 summary NOK figures correct. 0 wrong-company facts, 0 invented financials, 0 wrong values. |

Across all rounds every registry, accounts and update-feed claim checked against the live APIs was correct (more than 15,000 claims in rounds 1 to 13), and no financial value was ever invented. Rounds 8 to 11 split each 30-company audit across three independent auditors running in parallel.

## Version 2

Version 1 was frozen at d48937e (round 13 clean). Version 2 (final code 12b459d, artefacts at 45283d0) raises recall in the four reported families without loosening any proof. Both versions were run on the same 1,000 random companies (and the same 100 smoke-test companies); the figures come from each version's committed `profiles-1000/` and `smoke/` artefacts. Registry, accounts and public-record families are unchanged between the two, except financial-sector licences (13 to 17 companies, 21 to 50 licences), now also looked up for motor vehicle traders and travel agencies, which often hold credit or insurance agent licences.

| Family (1,000 random companies) | Version 1 (d48937e) | Version 2 final (12b459d) |
|---|---|---|
| Official website (companies) | 98 | 120 |
| Social profiles (companies / facts) | 55 / 113 | 65 / 126 |
| Hiring signals (companies / facts) | 22 / 34 | 22 / 35 |
| NAV job ads (companies / ads) | 8 / 19 | 8 / 19 |
| Dated news (companies / posts) | 22 / 249 | 31 / 370 |
| Related website, labelled (companies) | 29 | 29 |
| Website title (companies) | 88 | 109 |
| Website description (companies) | 58 | 76 |

On the 100-company smoke test: official website 13 to 16 companies, social profiles 5 / 13 to 7 / 16, dated news 3 / 48 to 4 / 57.

What changed, by theme:

- **Website candidates.** A declared candidate cache built from our own robots.txt-respecting crawl of about 325,000 public domains (Common Crawl host graph `.no` hosts, Brreg websites and email domains, name-derived domains resolved with dnsx, and outbound links), whose pages were matched to org numbers, registry phones, emails and names against the Brønnøysund bulk register. The crawl tied 55,618 universe companies (13.5%) to a domain. Also Common Crawl pages printing the org number, and NAV ad employer homepages (727 companies). Seeds only propose candidates: the live run fetches and proves every site, and seeds on domains carrying the company's name are tried first.
- **New proofs, with strict collision rules.** `legal_name_unique` (the exact registered name with its legal form on a domain that is exactly that name, and no other registered entity carrying the whole name), `registry_email_domain_and_name`, and a workplace-name proof for registered sub-units. Each runs the Brønnøysund name-collision search with retries; a search that fails marks the step failed, never decided.
- **Precision rules from rounds 14 to 17.** Names count only as visible text; a shared registered address proves nothing; a namesake in the site's heading or at our address makes name proofs ambiguous; contacts beside another company's name are withheld; platform names in badges and trademark notices (Google Inc, Apple Inc) never present a site; e-invoice, accounting and payment platform domains are services the company uses, not other organisations.
- **Dated news.** Feed discovery, listing pages found by link text and on blog subdomains, slash dates read in the page's own order, card lists parsed one item at a time, stub and template posts dropped, and a check that withholds a reused post whose text points to a later year.
- **Hiring.** The company's own board on a recruitment platform (Teamtailor, Webcruiter, Jobbnorge and others) when it is linked from the verified site.
- **Page updates.** A sitemap `lastmod` day shared by a large part of the sitemap is a bulk regeneration, not a page update.
- **Synthesis.** Distinct counts, capped sources state their true total ("lists 150 projects; the 8 most recent are shown"), subsidiaries labelled direct or indirect, and a general manager marked deceased or resigned named as such in the reason.
- **Evidence and reliability.** Proof spans hold the matched value; robots.txt answered with 401 or 403 means no rules (RFC 9309), while the refused pages stay blocked; transient failures (timeouts, 429, 5xx, failed registry searches) end as `failed` and are retried, so two runs give the same facts.

Fixes after the independent judges and round 17, each a general rule:

- **Social handles.** A handle taken from a site must carry a distinctive token of the company's name, and is checked against other registered names (a judge found Radio Sandefjord given Radio Tønsberg's handles).
- **Who presents a site.** The company's own email domain or initials, a website the registry declares for it, and a group's top parent count as its own; suppliers, clients, product brands, staff bios and invoice services never present a site.
- **robots.txt.** Checked on every redirect hop; an unreadable robots.txt is a transient failure, not permission (RFC 9309).
- **Request budget.** Strong candidates are tried first, guessed domains have their own sub-budget, common names are not guessed, accents are folded, and short acronyms are guessed alone.
- **Dated news.** English long dates, time cards and a posts-sitemap fallback are read; project pages are not news.
- **Summaries.** Every counted claim is cited, capped totals are stated, indirect subsidiaries are labelled, and periods of different lengths are stated.
- **Inputs and values.** An invalid org number gets a `failed` envelope; site-builder hosts and error URLs are normalised; a careers page with no openings is labelled as such.

Fixes from rounds 18 to 20, each a general rule:

- **Shared-site labels.** Another entity counts on a site only where its own name or org number appears, so false name-part matches no longer mark a company's own site as shared.
- **Contacts.** Phones and emails come only from contact positions (header, footer, contact block or page), never from notices, terms pages or text beside another organisation's address.
- **News.** FAQ, event, artist and search-overlay entries are not news.
- **Name-only recovery without new trust.** An org number on the privacy page of a site already proven counts; a one-word domain counts only when the word is unique in the registry; staff bodies, place-and-trade names and holding companies are not treated as namesakes.
- **Group parents.** A group parent keeps its own site when a namesake is only its subsidiary's workplace (StrongPoint).

## Determinism

Builderr runs each entry twice and compares the facts. The same 100 companies were run twice, uncached: the first drill found 4 companies differing because website steps timed out under load; after the fixes (rollback on timeout, a retry pass, a larger connection pool) two runs produced identical facts. Version 2's smoke test was run twice in parallel from a fresh clone: 0 companies differ, 5,845 facts in each run.

## Benchmark against Builderr's own sample

On the 100 companies of Builderr's public sample (https://builderr.ai/signalpost), which lean towards larger companies, compared with Builderr's own crawler. Run on the final version 2 code:

| Family | Builderr's crawler | Kilde |
|---|---|---|
| Verified websites | 84 | 64 (61 the same domain as Builderr's; the rest withheld by the stricter proof and shared-site rules) |
| Social profiles | 99 | 76 facts; 58 of Builderr's 99 matched |
| Dated news | not in sample | news for 18 companies |
| Hiring signals | none on company sites in the sample | 13 companies |
| LinkedIn data | collected by Builderr as "experimental" | not collected (unofficial access is not permitted) |

Registry families (leadership roles, workplaces, filed financials) come from the org-number-keyed Brønnøysund records and matched Builderr's sample closely in version 1 (707 of 714 roles, 180 of 180 workplaces, 97 of 97 companies with filed financials); those sources did not change in version 2.

## Independent judges

Six fresh agents acting as Builderr judges, given only the public challenge pages, the submission email and the repo, estimated the score twice. On an earlier commit (85fe2f8): 55 to 63, 59 to 67 and 58 to 64, with two of three flagging pages on shared or chain sites published as official websites. After moving those to a labelled `related_website` field and other fixes (8a82a07): 57 to 64, 60 to 67 and 54 to 62, with no wrong-company matches found. All six agreed that precision, synthesis and UX are close to full marks and that recall decides whether an entry clears 65.

Five more fresh judges reviewed version 2 under the same conditions (only the public rules, the repo and the submission email). On 7955bac: 64, 64.5 and 65. One of them found a wrong-company social profile (Radio Sandefjord given Radio Tønsberg's handles), fixed by the distinctive-token rule above. On 635b90c: 69 and 64.5, with no wrong-company facts found. All five again agreed that precision, synthesis and UX are near full marks and that recall decides the total.

Later fixes (after round 17 and in rounds 18 to 20) addressed the judges' findings with general rules, and round 20 on the final code found no wrong-company facts, invented financials or wrong values. The judges have not rescored the final code.

## Final runs (version 2, code 12b459d, fresh clone; artefacts committed at 45283d0)

- Tests: 538 pass.
- Smoke test, 100 new companies: valid, run twice with identical facts (5,845 each), 1,388 requests, about 4 minutes. See `smoke/`.
- 1,000 new companies: valid, 12,765 requests (about 13 per company), 12.9 minutes while other runs shared the same connection. See `profiles-1000/`.
- A further 100 fresh companies (seed 2020, the round 20 audit sample): valid, 1,415 requests.
- Output check on all runs: no problems except date-only news values (`title (YYYY-MM-DD)`), used where the source gives only a date.

Version 1's final runs (commit 94a2493): smoke 100 valid with identical facts (5,797 each); 1,000 companies valid at about 13 requests per company in 22.5 minutes.

All scores above are from our own scorer and auditors, not Builderr's. Builderr's reference pool includes every entrant's verified finds and its own crawlers, so recall against it will be lower.

## Revision 2 (Oct 17, code 0d4caa7, fresh clone)

Changes, all precision-first:
- **Registry events as dated public activity.** Update-feed entries, founding, registration and renamings are also published as `dated_activity` in the `title (time)` shape with the register's own timestamp, citing the same Brønnøysund responses as the `registry_change` and identity claims. No new requests. Every company with a registry record gets dated activity (997 of 1,000; the rest are deleted entities).
- **Invoice inboxes no longer veto a proven site.** An e-invoice or bookkeeping address on another domain (fakturapost.no, ebilag.com, an accountant's invoice inbox) no longer marks a site as presented by another company, unless that domain's owner appears as operator.
- **Registry-declared website as a fallback.** A company's own declared site is published (domain only, nothing derived) when it is live, no other entity declares it, it shows the registry phone or e-mail (not an address on the site's own domain) or the whole name, no other org number sits in an operator position, and no other registered company is named while ours is not. Never for holding, investment and property companies, funds or co-owned properties.
- **Registry phone or e-mail as proof** for part-of-name domains when the whole legal name is also shown and no namesake shares the contact; a guess from the name before "og" or "&".
- **Precision fixes found by this round's audit (also present in version 2):** a `tel:` link's number is used only when the same number is written as visible text, and news articles whose canonical address is on another domain (chain articles copied onto a member site) are not the company's news.

Runs from a fresh clone at 0d4caa7: 569 tests pass; smoke 100 valid and run twice with identical facts (6,410 each); seed-1717017 head-to-head batch 1,275 requests; two fresh 100-company samples 1,402 and 1,286 requests; 1,000 companies valid, 12,882 requests, 18.2 minutes (see `profiles-1000/`).

Audit: every published web-family claim on the two fresh samples (111 and 122 claims) checked live by two independent auditors. On the first, 108 own and 3 correctly labelled related websites. On the second, one wrong-company phone (a hidden `tel:` number) and five syndicated chain articles, both present in version 2 as well and fixed above; after the fix no wrong-company fact remains. Remaining wrong values: a booking agency's performer pages read as news posts (right company). Every `dated_activity` claim (8,000+ across the runs) was checked by script against its saved response and a sample against the live register: 0 problems.
