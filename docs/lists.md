# The lists, one by one

Every list file is Senzing JSON Lines: one record per line, the same fields in every file.
This page says where each list comes from, how often it changes, what the build does to it,
and how its fields map to Senzing and to FollowTheMoney (the OpenSanctions data model).
Record counts are from the 2026-11-16 build; `manifest.json` in each release has the current
counts, the source documents and any fetch error.

All files are rebuilt every day at 06:17 UTC by `.github/workflows/build.yml`. A new release is
published only when a list changed. If a source cannot be fetched, the previous release of
that list is kept and the error is written to `manifest.json` under `errors`.

## Loading the files

- Python: [`tools/load.py`](../tools/load.py), standard library only. `load("us_dhs_uflpa")`
  returns the records; `python tools/load.py --format csv` or `--format ftm` converts them.
  `load(name, release="data-2026-11-17-2241", verify=True)` reads a fixed release and checks the
  file against its `manifest.json`; `load("affiliates.covered")` reads the derived affiliates.
- Go: `go get github.com/Mattbusel/us-restricted-parties/go`, then
  `restrictedparties.Load(ctx, restrictedparties.UFLPA)`, or
  `restrictedparties.Source{Release: "...", Verify: true}.Load(...)` and `.LoadAffiliates(...)`.
- More: [integration.md](integration.md) (pinning, hashes, which file to screen against).
- Watchman: see [watchman.md](watchman.md).
- Anything else: `https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/<file>`.

## Fields (all lists)

| Field | Meaning | Senzing | FollowTheMoney (`tools/load.py --format ftm`) |
|---|---|---|---|
| `DATA_SOURCE` | the list, upper case (`US_DOD_1260H`, ...) | data source code (register it before loading) | the dataset |
| `RECORD_ID` | stable id derived from the list and the name | record id | `Company` entity id |
| `RECORD_TYPE` | always `ORGANIZATION` | record type | schema `Company` |
| `NAME_ORG` | primary name | `NAME_ORG` (also in `NAMES` as `PRIMARY`) | `Company.name` |
| `NAMES` | `PRIMARY` name plus every `ALIAS` (short names, former names, "also known as") | name list, `NAME_TYPE` PRIMARY / ALIAS | `Company.alias` |
| `LIST_NAME` | the list's full name | payload attribute | `Sanction.program` |
| `LISTED_ON` | date of the listing that added the entity (YYYY-MM-DD) | payload attribute | `Sanction.listingDate` |
| `SOURCE_URL` | the government page or Federal Register document the record came from | payload attribute | `Company.sourceUrl`, `Sanction.sourceUrl` |
| `SOURCE_DOC` | Federal Register document number, eCFR section, or page id | payload attribute | (kept in the Senzing file only) |
| `SOURCE_TEXT` | the list entry as the source prints it | payload attribute | `Sanction.reason` |
| `PARENT_NAME` | for a named subsidiary: the listed parent | payload attribute | (see `Ownership`) |
| `RELATIONSHIPS` | subsidiary: `REL_POINTER_*` with role `SUBSIDIARY_OF` to the parent's `RECORD_ID`; parent: `REL_ANCHOR_*` | relationship anchor and pointer, so Senzing links the two | `Ownership` with `owner` = parent, `asset` = subsidiary |
| `INCLUDES_AFFILIATES` | `true` when the listing covers unnamed subsidiaries or affiliates (screen by ownership, not by name alone) | payload attribute | (kept in the Senzing file only) |
| `BASIS` | why a derived record is on the list (BIOSECURE only) | payload attribute | `Sanction.reason` |

`Sanction.authority` in the FtM output is the list's operator from the table below.

## us_dod_1260h: DoD Chinese Military Companies (Section 1260H)

| | |
|---|---|
| Operator | Department of Defense, OUSD(A&S) |
| Source | Federal Register, DoD notices under Section 1260H of the FY2021 NDAA: https://www.federalregister.gov/ (base notice 2026-11571, 2026-06-10) |
| How often it changes | DoD publishes the full list about once a year, plus removal notices in between |
| Records (2026-11-16) | 186, with 97 aliases |
| `DATA_SOURCE` | `US_DOD_1260H` |

What the build does: reads the latest full-list notice, splits each entry into the parent and
every subsidiary named in its parentheses (keeping legal suffixes such as "Co., Ltd." together),
links each subsidiary to its parent with `RELATIONSHIPS`, keeps short and former names as
aliases, and applies later removal notices on top (for example WuXi AppTec, removed on
2026-11-10 by FR Doc 2026-22840). The notice's own removals (10 in the 2026 base notice) are in
`manifest.json` under `notes.removed_in_base`.

## us_far_889: Section 889 covered telecommunications and video surveillance companies

| | |
|---|---|
| Operator | FAR Council (FAR 52.204-25); OMB for grants (2 CFR 200.216) |
| Source | eCFR, 48 CFR 52.204-25: https://www.ecfr.gov/current/title-48/section-52.204-25 |
| How often it changes | Rarely: the five names come from the statute (Section 889 of the FY2019 NDAA) |
| Records (2026-11-16) | 5 (Huawei, ZTE, Hytera, Hikvision, Dahua) |
| `DATA_SOURCE` | `US_FAR_889` |

Every record has `INCLUDES_AFFILIATES: true`: the rule covers each company's subsidiaries and
affiliates, so a name match is not enough. `manifest.json` records the eCFR date read
(`notes.ecfr_as_of`).

## us_fcc_covered: FCC Covered List

| | |
|---|---|
| Operator | Federal Communications Commission, Public Safety and Homeland Security Bureau |
| Source | https://www.fcc.gov/supplychain/coveredlist |
| How often it changes | A few times a year |
| Records (2026-11-16) | 13 named companies |
| `DATA_SOURCE` | `US_FCC_COVERED` |

The Covered List also covers whole product categories (foreign-made drones, routers, power
inverters, robotic devices). They name no company, so they are in `manifest.json` under
`notes.category_entries` with their dates instead of in the records. The page marks these entries
with footnote symbols (a pound sign, daggers, `#`, a superscript); those are taken out of `entry`, and the
footnote each one points to is in that entry's `footnotes` list.

## us_dhs_uflpa: UFLPA Entity List

| | |
|---|---|
| Operator | DHS Forced Labor Enforcement Task Force |
| Source | https://www.dhs.gov/uflpa-entity-list, or the full list in the appendix of DHS's latest Federal Register notice when dhs.gov blocks the build (2026-11-16: notice 2026-22094, list as of 2026-10-29) |
| How often it changes | Every few months, by Federal Register notice |
| Records (2026-11-16) | 190, with 131 aliases |
| `DATA_SOURCE` | `US_DHS_UFLPA` |

Entities DHS lists more than once are merged, keeping the earliest effective date in
`LISTED_ON`. Which source the build used is in `manifest.json` under `notes.source`.

## us_biosecure: BIOSECURE biotechnology companies of concern (interim)

| | |
|---|---|
| Operator | OMB (its list is due 2026-12-18); until then, the Section 1260H biotechnology listings |
| Source | derived from `us_dod_1260h`; the build watches the Federal Register for OMB's list |
| How often it changes | With the 1260H list, until OMB publishes |
| Records (2026-11-16) | 10, with 4 aliases |
| `DATA_SOURCE` | `US_BIOSECURE` |

The statute (FY2026 NDAA sec. 851) makes 1260H entities involved in biotechnology companies of
concern; every record says so in `BASIS`. Any OMB document the build finds is listed in
`manifest.json` under `notes.omb_list_documents` (empty on 2026-11-16).

## all.jsonl

Every record of the five lists, 404 on 2026-11-16. `DATA_SOURCE` tells them apart. An entity on
two lists (a 1260H biotech company is also in BIOSECURE) has one record per list.
