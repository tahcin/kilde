# website_seeds.json.gz: sources and licences

`website_seeds.json.gz` maps an organisation number to candidate website domains. Each entry is a
candidate only: the website connector proves identity on the live site before anything is published.
Rebuild with `scripts/build_website_seeds.py`, then merge the Common Crawl candidates with
`scripts/merge_cc_seeds.py` (re-runnable: it replaces every `commoncrawl` source each time).

| Source tag | Data | Licence and attribution |
|---|---|---|
| `osm_orgnr`, `osm_phone` | OpenStreetMap, Norway extract from Geofabrik (`norway-latest.osm.pbf`) | © OpenStreetMap contributors. Open Database Licence (ODbL) 1.0, https://www.openstreetmap.org/copyright. The seeds file is a derived database and is shared under ODbL. |
| `overture_phone`, `overture_email` | Overture Maps Foundation, Places theme (release named in the file's `licences.overture`) | © Overture Maps Foundation. Community Data License Agreement Permissive 2.0 (CDLA-Permissive-2.0), https://overturemaps.org |
| `subunit` (and the phone/email keys used for matching) | Enhetsregisteret bulk files (`enheter`, `underenheter`), Brønnøysundregistrene | Norsk lisens for offentlige data (NLOD) 2.0, https://data.norge.no/nlod/no/2.0 |
| `nav` | NAV `pam-stilling-feed`, field `ad_content.employer.homepage` | NAV API terms, https://arbeidsplassen.nav.no/vilkar-api |
| `wikidata` | Wikidata P856 on items with P2333, queried through QLever | CC0 1.0 |
| `commoncrawl`, `commoncrawl_domain` | Common Crawl CC-MAIN-2026-39: front, contact, about and privacy pages of Norwegian hosts that print an org number (`commoncrawl`), and host names that match a company name (`commoncrawl_domain`) | Common Crawl Terms of Use, https://commoncrawl.org/terms-of-use (see below) |

Stored per org: registered domains (or a company-specific subdomain on a site builder) and the
source tags. No names, phone numbers, emails or other personal data are stored.

## Common Crawl attribution (sources `commoncrawl`, `commoncrawl_domain`)

cc_seeds.json.gz and cc_pairs.jsonl.gz are derived from Common Crawl crawl CC-MAIN-2026-39 (https://commoncrawl.org/), columnar URL index plus WARC records fetched by byte range from https://data.commoncrawl.org/.

Use is governed by the Common Crawl Terms of Use (https://commoncrawl.org/terms-of-use, last updated 2024-03-07), which grant "a limited, non-assignable, non-transferable, non-sublicensable, non-exclusive, limited license to access and use the Service". The terms do not require attribution, but we credit Common Crawl and the crawl id as the source. Crawled pages "may be subject to separate terms of use or terms of service from the owners of such Crawled Content"; we store only org number, domain, page URL and capture time, not page content. Sole proprietorships (ENK) are excluded so no personal data is harvested (ToU clause 2(l)).

The entries are candidates only: each domain must be re-fetched and verified live (respecting the site's robots.txt) before use.

### Filters in `scripts/merge_cc_seeds.py` (source `commoncrawl`)

Only org number and registered domain are stored. Pairs are dropped when the number is not in the company
universe or is a sole proprietorship (ENK); the host is social, a directory, a registry or data aggregator, or a
public body; the domain prints three or more org numbers (directories, client lists); the domain prints two and
does not carry this company's name (the other is usually the web agency, accountant or payment provider); the
text before the number credits a supplier ("utviklet av", "levert av", "behandles av" ...); the company was seen
as such a supplier elsewhere, or appears on more than three domains, or has a registry website, and this domain
does not carry its name. Counts for every rule are in the file's `stats.commoncrawl_merge`.
