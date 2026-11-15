# US Restricted Parties (Senzing JSON Lines)

Five US government restricted-party lists that are not in the Consolidated Screening List, rebuilt every day from the official sources and published as Senzing-format JSON Lines. Free for commercial use under CC BY 4.0.

| File | List | Official source | Records* |
|---|---|---|---|
| `us_dod_1260h.jsonl` | DoD Chinese Military Companies (Section 1260H), with every named subsidiary | Federal Register notices from DoD, including later removals | 174 |
| `us_far_889.jsonl` | Section 889 covered telecom and video surveillance companies (Huawei, ZTE, Hytera, Hikvision, Dahua) | eCFR, FAR 52.204-25 | 5 |
| `us_fcc_covered.jsonl` | FCC Covered List, named companies | fcc.gov/supplychain/coveredlist | 13 |
| `us_dhs_uflpa.jsonl` | UFLPA Entity List (forced labor import ban) | dhs.gov/uflpa-entity-list | 190 |
| `us_biosecure.jsonl` | BIOSECURE biotechnology companies of concern, interim (the 1260H biotech listings until OMB publishes its list) | derived from the 1260H file | 10 |
| `all.jsonl` | all of the above | | 392 |

*As of the first build on Nov 15 2026. `manifest.json` in each release has the current counts, hashes and source documents, and `CHANGES.md` lists the names added and removed.

Latest files, always at the same URL:

```
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_dod_1260h.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_far_889.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_fcc_covered.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_dhs_uflpa.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_biosecure.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/all.jsonl
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

More detail, including how aliases load, in [docs/watchman.md](docs/watchman.md).

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
