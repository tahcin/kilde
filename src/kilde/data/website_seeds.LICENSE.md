# website_seeds.json.gz: sources and licences

`website_seeds.json.gz` maps an organisation number to candidate website domains. Each entry is a
candidate only: the website connector proves identity on the live site before anything is published.
Rebuild with `scripts/build_website_seeds.py`.

| Source tag | Data | Licence and attribution |
|---|---|---|
| `osm_orgnr`, `osm_phone` | OpenStreetMap, Norway extract from Geofabrik (`norway-latest.osm.pbf`) | © OpenStreetMap contributors. Open Database Licence (ODbL) 1.0, https://www.openstreetmap.org/copyright. The seeds file is a derived database and is shared under ODbL. |
| `overture_phone`, `overture_email` | Overture Maps Foundation, Places theme (release named in the file's `licences.overture`) | © Overture Maps Foundation. Community Data License Agreement Permissive 2.0 (CDLA-Permissive-2.0), https://overturemaps.org |
| `subunit` (and the phone/email keys used for matching) | Enhetsregisteret bulk files (`enheter`, `underenheter`), Brønnøysundregistrene | Norsk lisens for offentlige data (NLOD) 2.0, https://data.norge.no/nlod/no/2.0 |
| `nav` | NAV `pam-stilling-feed`, field `ad_content.employer.homepage` | NAV API terms, https://arbeidsplassen.nav.no/vilkar-api |
| `wikidata` | Wikidata P856 on items with P2333, queried through QLever | CC0 1.0 |

Stored per org: registered domains (or a company-specific subdomain on a site builder) and the
source tags. No names, phone numbers, emails or other personal data are stored.
