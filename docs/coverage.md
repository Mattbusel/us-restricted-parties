# Coverage check: does your screen actually have the US lists?

`us-restricted-parties coverage` reads what a sanctions screen uses (its config, its list directory, a dump of its
index, or its whole source tree) and fails unless every US restricted list is there, current, and has names.

```
pip install "us-restricted-parties @ https://github.com/Mattbusel/us-restricted-parties/releases/download/v0.7.0/us_restricted_parties-0.7.0-py3-none-any.whl"
us-restricted-parties coverage path/to/screening-tool        # exit 1 when a list is missing, stale or empty
us-restricted-parties coverage lists/ --json --report out.json
us-restricted-parties coverage . --skip far_889              # you are not a federal contractor
us-restricted-parties coverage --list-ids
```

As a GitHub Action, in the repository of the screen (check out the full history so file dates are real):

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- uses: Mattbusel/us-restricted-parties@v0.7.0
  with:
    path: .            # or the list directory / config file
    # skip: far_889
```

The job fails with an annotation per list and writes a table to the job summary. Output `report` is the path of
the JSON report.

## What it checks

| id | List | Agency | In the CSL |
|---|---|---|---|
| `ofac_sdn` | Specially Designated Nationals | Treasury OFAC | yes |
| `ofac_nonsdn` | Non-SDN lists (SSI, FSE, NS-MBS, NS-CMIC, PLC, CAPTA) | Treasury OFAC | yes |
| `csl` | Consolidated Screening List | Commerce ITA | |
| `bis_entity` | Entity List | Commerce BIS | yes |
| `bis_meu` | Military End User List | Commerce BIS | yes |
| `bis_uvl` | Unverified List | Commerce BIS | yes |
| `dod_1260h` | Chinese Military Companies (Section 1260H) | DoD | no |
| `far_889` | Section 889 covered telecom and video surveillance companies | FAR Council | no |
| `fcc_covered` | FCC Covered List | FCC | no |
| `dhs_uflpa` | UFLPA Entity List | DHS | no |
| `biosecure` | BIOSECURE biotechnology companies of concern | OMB | no; required once OMB publishes it |

A CSL file or reference counts for the lists inside the CSL. When a CSL file is split by `source` (the trade.gov
JSON and CSV are), each list inside it is checked on its own rows, so a CSL export filtered down to the SDN does not
pass for the Entity List.

## Statuses

- `ok`: list data found, it has names, and its data date is not older than the agency's latest update (one hour of
  slack; three days for lists published as Federal Register notices, whose agency pages can change first).
- `declared`: the code or config names the list or its URL and fetches it at run time. Nothing local to inspect.
- `missing` (fails): nothing names, references or contains the list. Lists named only in tests, fixtures or
  documentation do not count (`--include-tests`, `--include-docs` to change that).
- `stale` (fails): the newest data found for the list is older than the agency's latest update, or it cannot be
  dated (a shallow clone).
- `empty_names` (fails): some records of the list carry no name at all. A screen cannot match those records.
- `not_published`: BIOSECURE until OMB publishes its list (due Dec 18 2026).

## Where the dates come from

The agencies' latest update dates are in `sources.json` on the `status` branch,
https://raw.githubusercontent.com/Mattbusel/us-restricted-parties/status/sources.json, rewritten by every daily
build: `Last-Modified` of OFAC's `SDN.CSV` and `CONSOLIDATED.XML` on the Sanctions List Service, the CSL's own
`sources_used` dates for the BIS lists, and the newest listing or later Federal Register notice of 1260H, Section
889, the FCC Covered List and UFLPA (removals count: WuXi AppTec left the 1260H list on 2026-11-10).
`--sources-live` reads the agencies directly; `--sources FILE` reads a saved copy (for an offline or pinned run).

A list's own data date is, in this order: the date inside the file (OFAC XML `Publish_Date` or `DateOfIssue`, the
CSL's `sources_used`, FollowTheMoney `last_seen`, this feed's `manifest.json` `data_as_of`), a date the code says it
was copied on ("populated from OFAC's 2026-08-28 SDN.XML"), the last git commit that touched the file, or its
modification time outside git.

## OpenSanctions scopes and topic filters

A screen that queries OpenSanctions (`api.opensanctions.org/match/<scope>`, a yente `scope`, or
`data.opensanctions.org/datasets/<version>/<dataset>/`) is expanded through the OpenSanctions index
(`--opensanctions-index`, fetched when needed) into the source datasets of that scope, dated by each dataset's
`last_change`. As of 2026-11-20:

- `default` carries every list above except Section 889, which has no OpenSanctions dataset.
- `sanctions` also leaves out the FCC Covered List (`us_fcc_covered_list` is in `default`, `debarment`, `crime`,
  `regulatory`, not in `sanctions`).
- A topics filter drops whole lists: OpenSanctions tags all 207 DoD 1260H entities and all 13 FCC Covered List
  entities `debarment`, and 66 of 70 MEU and 106 of 107 UVL entities only `export.control`. A query limited to
  `sanction` returns none of them. The check reads literal topic lists in OpenSanctions code and in files whose path
  says screening, sanctions, KYC, AML, compliance, watchlist or policy.

## Limits

- It reads code and config statically. A list URL built at run time from parts, or a list chosen by an environment
  variable, shows as missing; point the check at the config file or a dump of the loaded index instead.
- `declared` means the code names the list, not that the fetch works; run the check against the downloaded files in
  CI to check freshness and names too.
- Names only: it does not test matching quality. A list can be present and still be matched badly.
