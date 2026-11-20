# US Restricted Parties: "screened against sanctions" usually means screened against the easy lists

[![build](https://github.com/Mattbusel/us-restricted-parties/actions/workflows/build.yml/badge.svg)](https://github.com/Mattbusel/us-restricted-parties/actions/workflows/build.yml)
[![test](https://github.com/Mattbusel/us-restricted-parties/actions/workflows/test.yml/badge.svg)](https://github.com/Mattbusel/us-restricted-parties/actions/workflows/test.yml)
[![latest data](https://img.shields.io/github/release-date/Mattbusel/us-restricted-parties?label=latest%20data)](https://github.com/Mattbusel/us-restricted-parties/releases/latest)

"Screened against sanctions" usually means screened against the lists that were easy to download. We built a check that fails a screen unless every US restricted list is there, current and has names, and ran it on 31 open-source name screens and sanctions datasets on GitHub on Nov 20 2026. All 31 failed. 22 had none of DoD 1260H, the FCC Covered List and the UFLPA Entity List. 20 never screen the BIS Entity List. A 29.7K-star KYC template filters its OpenSanctions query to topics that drop 3,691 export-control parties, Military End Users included. A 14.7K-star chain hardcodes an August copy of OFAC's addresses and misses three designated since. Before our [merged fix](https://github.com/koala73/worldmonitor/pull/10998), a 90.9K-star dashboard returned no match for 153 of 190 UFLPA entities; through one standard loader, all 207 DoD 1260H entities loaded with empty names ([merged fix](https://github.com/moov-io/watchman/pull/1019)). Both jobs went green.

This repo is the fix for both halves. It rebuilds the five lists that are in neither OFAC nor the trade.gov Consolidated Screening List every day from the official sources (three of them exist only as Federal Register prose or an HTML table), as Senzing-format JSON Lines, free for commercial use under CC BY 4.0. And [`us-restricted-parties coverage`](docs/coverage.md) fails your build when a list is missing, older than the agency's last update, or loaded without names:

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }
- uses: Mattbusel/us-restricted-parties@v0.7.0   # OFAC SDN + non-SDN, CSL, BIS EL/MEU/UVL, 1260H, 889, FCC, UFLPA
  with: { path: . }
```

Not legal advice; a list can be present and still be matched badly.

| File | List | Official source | Records* |
|---|---|---|---|
| `us_dod_1260h.jsonl` | DoD Chinese Military Companies (Section 1260H), with every named subsidiary | Federal Register notices from DoD, including later removals | 174 |
| `us_far_889.jsonl` | Section 889 covered telecom and video surveillance companies (Huawei, ZTE, Hytera, Hikvision, Dahua) | eCFR, FAR 52.204-25 | 5 |
| `us_fcc_covered.jsonl` | FCC Covered List, named companies | fcc.gov/supplychain/coveredlist | 13 |
| `us_dhs_uflpa.jsonl` | UFLPA Entity List (forced labor import ban) | dhs.gov/uflpa-entity-list, or the full list in the appendix of DHS's latest Federal Register notice when dhs.gov blocks the build | 190 |
| `us_biosecure.jsonl` | BIOSECURE biotechnology companies of concern, interim (the 1260H biotech listings until OMB publishes its list) | derived from the 1260H file | 10 |
| `all.jsonl` | all of the above | | 392 |
| `all.csv` | all of the above as CSV, one row per name (primary name and each alias): `RECORD_ID,NAME,NAME_TYPE,DATA_SOURCE,LIST_NAME,LISTED_ON,PARENT_NAME,SOURCE_URL`, for screening tools that load one name per row | | one row per name |
| `affiliates.senzing.jsonl` | companies listed parties own or control (OFAC 50 Percent Rule, BIS Affiliates Rule), derived from GLEIF ownership links, with proof paths; evidence, not determination ([docs](docs/affiliates.md)) | GLEIF Level 2, trade.gov CSL, the files above | varies |

*As of the first build on Nov 15 2026. `manifest.json` in each release has the current counts, hashes and source documents, and `CHANGES.md` lists the names added and removed.

Latest files, always at the same URL:

```
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_dod_1260h.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_far_889.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_fcc_covered.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_dhs_uflpa.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_biosecure.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/all.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/all.csv
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/manifest.json
```

## Why

If you sell to the US government, take federal grant money, import into the US, or bank someone who does, these lists decide whether a counterparty is a problem. None of them is in the trade.gov Consolidated Screening List, three of them are only published as prose in the Federal Register or as an HTML table, and the 1260H notice buries 98 subsidiaries inside parentheses. This repo turns them into records a screening engine can load.

What the build does that a quick scrape does not:

- Splits the 1260H entries into the parent and each named subsidiary (AVIC alone has 17), keeps the legal suffixes together ("Co., Ltd."), and links every subsidiary to its parent with a Senzing `RELATIONSHIPS` pointer.
- Keeps every short name, "formerly known as" and "also known as" name as an `ALIAS` (229 aliases across the files).
- Applies later Federal Register notices on top of the last full list. Example: WuXi AppTec was removed from the 1260H list on Nov 10 2026 (FR Doc 2026-22840) and is not in the current files.
- Merges entities the DHS page lists more than once, keeping the earliest effective date.
- If an agency site is down or blocks the run, the previous release of that list is kept and the error is written to `manifest.json`. A feed never goes empty because of a bad fetch.

## Use it with moov-io/watchman

Watchman loads Senzing lists by URL and refreshes them every 12 hours:

```yaml
Watchman:
  Download:
    Senzing:
      - SourceList: "us_dod_1260h"
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_dod_1260h.jsonl"
      - SourceList: "us_dhs_uflpa"
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_dhs_uflpa.jsonl"
      - SourceList: "us_fcc_covered"
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_fcc_covered.jsonl"
      - SourceList: "us_far_889"
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_far_889.jsonl"
```

More detail, including how aliases load, in [docs/watchman.md](docs/watchman.md). Loading the files from Python or Go, pinning a
dated release and checking hashes: [docs/integration.md](docs/integration.md).

## Load it from Python or Go

```
pip install "us-restricted-parties @ https://github.com/Mattbusel/us-restricted-parties/releases/download/v0.7.0/us_restricted_parties-0.7.0-py3-none-any.whl"
```

```python
from us_restricted_parties import latest_release, load
tag = latest_release()                                   # pin one release per run and keep the tag
for rec in load("us_dhs_uflpa", release=tag, verify=True, cache_dir=".urp-cache"):
    print(rec["NAME_ORG"], rec["SOURCE_URL"])
```

Standard library only, typed. Go: `go get github.com/Mattbusel/us-restricted-parties/go`. Details, including the
affiliates and hash checks, in [docs/integration.md](docs/integration.md); release history and provenance in
[docs/maintenance.md](docs/maintenance.md).
Freshness: every daily run, even with no new release, writes
[status.json](https://raw.githubusercontent.com/Mattbusel/us-restricted-parties/status/status.json) with the run time and each
agency's pull result ([docs/status.md](docs/status.md)).

## Use it with Senzing or anything else

Each line is one Senzing record:

```json
{"DATA_SOURCE": "US_DOD_1260H", "RECORD_ID": "us_dod_1260h-bgi-genomics-co-ltd", "RECORD_TYPE": "ORGANIZATION",
 "NAME_ORG": "BGI Genomics Co., Ltd.",
 "NAMES": [{"NAME_TYPE": "PRIMARY", "NAME_ORG": "BGI Genomics Co., Ltd."}, {"NAME_TYPE": "ALIAS", "NAME_ORG": "BGI"}],
 "LIST_NAME": "DoD Chinese Military Companies (Section 1260H)", "LISTED_ON": "2026-06-10",
 "SOURCE_URL": "https://www.federalregister.gov/documents/2026/06/10/2026-11571/...", "SOURCE_DOC": "2026-11571",
 "SOURCE_TEXT": "BGI Group (and BGI Group subsidiaries: ...)", "PARENT_NAME": "BGI Group",
 "RELATIONSHIPS": [{"REL_POINTER_DOMAIN": "US_DOD_1260H", "REL_POINTER_KEY": "us_dod_1260h-bgi-group", "REL_POINTER_ROLE": "SUBSIDIARY_OF"}]}
```

`NAME_ORG` at the top level is the primary name, so readers that ignore `NAMES` still get it. `RECORD_ID`s are derived from the name and stay stable from build to build. `INCLUDES_AFFILIATES: true` marks entries whose listing covers unnamed subsidiaries or affiliates (Section 889, Hoshine, XPCC): screen those by ownership, not by name alone.

## Check your screen's coverage

```
us-restricted-parties coverage path/to/screening-tool      # config, list directory, index dump or source tree
us-restricted-parties coverage lists/ --skip far_889 --report coverage.json
```

Exit 1 when a required list is `missing`, `stale` or has `empty_names`; OpenSanctions scopes and topic filters are expanded to the lists they really return. Details, statuses and where the agency dates come from: [docs/coverage.md](docs/coverage.md).

## Check your own list

Screen a CSV of company names against the current files in one command (Python 3.9+, standard library only):

```
curl -sLO https://raw.githubusercontent.com/Mattbusel/us-restricted-parties/main/tools/check_names.py
python check_names.py vendors.csv            # first column = name; --column NAME to pick one
```

Each hit prints the list, the listed name, the parent for a named subsidiary and the government source URL.
`tools/watchman_gap.py` asks a running Watchman for every record in these files and reports which ones your current
lists already match, so you can see the gap before loading anything.

## Build it yourself

Python 3.10+, standard library only:

```
python build.py --out dist            # fetch all five sources and write dist/
python build.py --out dist --prev old # same, falling back to old/ for any source that fails, and writing CHANGES.md
python -m unittest discover -s tests  # offline tests against real excerpts of each source
```

The daily GitHub Action (`.github/workflows/build.yml`) runs exactly this and publishes a release only when a list changed.

## Limits

- The FCC Covered List also covers whole product categories (foreign-made drones, routers, power inverters, robotic devices). Those have no company name, so they are listed in `manifest.json` under `notes.category_entries` instead of as records.
- `us_biosecure.jsonl` is interim. The statute (FY2026 NDAA sec. 851) makes 1260H biotechnology companies companies of concern, and OMB's own list is due Dec 18 2026. The build watches the Federal Register for it and reports any OMB document it finds in `manifest.json`.
- Names only. These lists do not publish addresses or identifiers for most entities.

## Ownership beyond the list

The lists name parents and a few subsidiaries. The US companies that actually sign contracts and receive federal money are often several layers down. The [Tensorust Ontology](https://tensorust.com) expands each listed parent through state business registries, SEC subsidiary lists and FCC ownership filings, and scores federal awards and purchases against these lists. That expanded feed is a paid product; this one is free.

## License

Data: CC BY 4.0, see [LICENSE-DATA](LICENSE-DATA). Credit "US Restricted Parties feed by Tensorust LLC". Code: MIT, see [LICENSE](LICENSE).

Not legal advice. Confirm every match against the record's `SOURCE_URL`.

Maintained by Matthew Busel (Tensorust LLC).
