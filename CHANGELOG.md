# Changelog

## 0.7.0 (2026-11-20)

Why: "screened against sanctions" usually means screened against the lists that were easy to download, and nothing
in a screening pipeline fails when a list is missing, behind the agency, or loaded without names. On Nov 20 2026 we
ran the new check on 31 open-source name screens and sanctions datasets on GitHub: all 31 failed, 22 had none of DoD
1260H, the FCC Covered List and the UFLPA Entity List, and 20 never screen the BIS Entity List. The loader and the
data files are unchanged; this release adds the check.

- `us-restricted-parties coverage PATH...` (module `us_restricted_parties.coverage`, standard library only): reads a
  screening tool's config, list directory, index dump or whole source tree and reports each US restricted list as
  `ok`, `declared`, `missing`, `stale`, `empty_names` or `not_published`. Exit 1 when a required list is missing,
  stale or has records without names; 2 on bad input or when the agency dates cannot be read. Lists: OFAC SDN and
  non-SDN, the trade.gov CSL, BIS Entity List, MEU and UVL, DoD 1260H, Section 889, FCC Covered List, UFLPA, and
  BIOSECURE once OMB publishes it. `--skip`, `--require`, `--json`, `--report`, `--github`, `--list-ids`.
- Data dates come from the file itself (OFAC XML publish date, the CSL's `sources_used`, FollowTheMoney `last_seen`,
  this feed's `data_as_of`), a copy date named in the code, the last git commit, or the modification time. A shallow
  clone cannot be dated and fails.
- OpenSanctions scopes (`/match/default`, yente `scope`, `datasets/latest/<name>/`) are expanded through the
  OpenSanctions index, and a literal topics filter drops the lists whose entities carry other topics (1260H and the
  FCC Covered List are `debarment`, MEU and UVL `export.control`).
- GitHub Action (`action.yml`): `uses: Mattbusel/us-restricted-parties@v0.7.0` with `path`, `skip`, `require`,
  `sources`, `include-tests`; error annotations, a job-summary table, and the JSON report as output `report`.
- The daily build writes `sources.json` (each list's latest agency update: OFAC Sanctions List Service
  Last-Modified, the CSL's `sources_used`, the Federal Register notices in the manifest) to the `status` branch next
  to `status.json`.
- `docs/coverage.md`. README opens with the measured results.

## 0.6.1 (2026-11-18)

- Freshness heartbeat. Every build run, including days with no list change, days an agency pull failed and runs
  where the build failed, commits `status.json` to the `status` branch at a URL that does not move:
  https://raw.githubusercontent.com/Mattbusel/us-restricted-parties/status/status.json. It carries
  `generated_at`, `last_release_tag`, whether this run released, the run URL, every error, and per list
  `fetched_at`, `upstream_ok`, `rows`, `sha256` and `data_date`, plus the affiliates' state. A consumer can confirm
  coverage without a new release and treat a heartbeat older than 30 hours as unconfirmed. `docs/status.md`.
- `manifest.json` lists carry `data_as_of`: when the served data was read from the agency. For a list kept from the
  previous release after a failed pull it is that earlier time, not the build time. `us_biosecure` now has
  `fetched_at: null` when the 1260H pull it is derived from failed.
- Data releases carry `all.csv`: every record of `all.jsonl`, one row per name (primary and each alias), columns
  `RECORD_ID,NAME,NAME_TYPE,DATA_SOURCE,LIST_NAME,LISTED_ON,PARENT_NAME,SOURCE_URL`, at
  `releases/latest/download/all.csv`. For screening tools that load a delimited file with one name per row (for
  example Jube's sanctions loader). The first daily run after this change publishes a release even if no list
  changed, so the file exists at the latest URL.

## 0.6.0 (2026-11-18)

- The Python loader is a package: `us-restricted-parties` (`src/us_restricted_parties`, one standard-library
  module, `py.typed`, `mypy --strict` clean, console command `us-restricted-parties`). Install from the wheel on
  the `v0.6.0` GitHub release; PyPI once the project exists. `tools/load.py` is now a shim over the package, so
  `python tools/load.py` and `from load import ...` keep working.
- `latest_release()` returns the newest data release tag from the `releases/latest` redirect (one HEAD request,
  no API token). `load(..., verify=True)` without `release` now looks the tag up first and reads the file and
  `manifest.json` from that one release; before, the two came from two separate `latest` requests and a release
  published in between made a correct file fail the hash check.
- `load(source=<URL>, verify=True)` checks against the `manifest.json` next to that URL. Before, it used the
  latest release's manifest, so a file of an older release failed the check whenever that list had changed since.
- `cache_dir=`: files of a pinned release (and its manifest) are kept in `cache_dir/<release>/` and read from there
  next time; with `verify=True` they are checked on every read, and a file that fails the check is not cached.
- `load("all", verify=True)` fails before downloading anything (it has no hash of its own). Release tags are
  checked (`data-...`), so a value such as `../x` cannot reach a URL or a cache path.
- The command reports HTTP and network errors as one line and exit code 2 instead of a traceback; `--cache-dir`,
  `--latest-release` and `--version`.
- Go `go/v0.3.0`: `LatestRelease(ctx, client)`, `Source.Resolve`; `Source{Verify: true}` with no `Release`
  pins the latest tag first (same race as above); `Load(ctx, All)` with `Verify` fails before any request;
  `AffiliatesManifest.Rules` (`RuleInfo`); `Version`.
- Affiliates: ownership-rules 0.3.0. `affiliates.manifest.json` and the `affiliates` entry of `manifest.json`
  carry `rules`, each rule's version and whether it binds on the build day (`in_force`, `stayed` with
  `stayed_until` and `stay_cite`, or `not_yet_effective`). The BIS Affiliates Rule is `stayed` until 2027-01-10.
  The verdicts do not change.
- `docs/maintenance.md`: how the data is built and kept, data release history, CI, API stability and how to remove
  the loaders. README: CI, build and data-date badges, and a short Python and Go section.

## 0.5.0 (2026-11-17)

- Loaders read a fixed data release and check hashes. `tools/load.py`: `load(name, release="data-...",
  verify=True)`, `url()`, `file_name()`, `manifest(release=...)`, `--release` and `--verify`. Go (`go/v0.2.0`):
  `Source{Client, Release, Verify}` with `Load`, `LoadAffiliates`, `Manifest` and `URL`; `Manifest.SHA256`;
  `FileName`. `Load`, `LoadFrom` and `LoadManifest` are unchanged.
- Both loaders read the affiliates files: `affiliates.covered`, `affiliates.review` and `affiliates`. Python adds
  `verdicts(rec)`; Go adds the `Affiliate` type (verdicts, proofs, LEI, `LISTED_ON` as the lists the company is on)
  and `ReadAffiliates`. The Go `Record` type could not read affiliates records, whose `LISTED_ON` is a list.
- The affiliates build uses ownership-rules 0.2.0. Its BIS rule now starts from SDNs under
  `ILLICIT-DRUGS-EO14059` as the SDN List writes the tag (0.1.x had a space instead of the first hyphen and
  skipped them), so the next build can add `bis-affiliates` verdicts for companies those SDNs own.
- `docs/integration.md`: what an integrator depends on, five-line Python and Go examples, which file to screen
  against.

## 0.4.3 (2026-11-17)

- A GLEIF failure no longer stops the five lists from publishing. When the affiliates build fails, the release
  carries the previous affiliates files unchanged with `stale: true`, the real older `data_date` and an `error` note
  in `affiliates.manifest.json` and the `affiliates` entry of `manifest.json`, and the job emits a warning annotation
  instead of failing.

## 0.4.2 (2026-11-17)

- GLEIF downloads: goldencopy.gleif.org answers HTTP 403 for every golden copy file since 2026-11-17 17:00 UTC
  (from GitHub Actions and elsewhere, with any headers) while its API still lists them. The build now reads the
  file URL and publish date from the golden copy API, falls back to the same files in GLEIF's AWS Open Data mirror
  (`s3://gleif`), and then to the per-record GLEIF API for the entities the feed needs. Downloads retry 429 and 5xx
  with backoff and go through a `.part` file.
- A failed or stale affiliates build fails the workflow (exit 2, error annotation) instead of re-serving the
  previous feed under a new release. `affiliates.manifest.json` and `manifest.json` carry `data_date`,
  `data_as_of`, `stale`, `gleif_route` and `gleif_coverage`, and the day of the GLEIF data is part of the release
  decision.
- A company named on several lists keeps all its list hits. Entries on different lists that share an exact name
  are one party, so Huawei and Hikvision (Entity List and 1260H) are matched to their LEIs instead of both
  entries being dropped as ambiguous, and DJI's 1260H entry takes the LEI of its Entity List entry
  (`same_party_on_other_list`). Regression tests for Huawei, Hikvision and DJI.

## 0.4.1 (2026-11-17)

- Affiliates are split into `affiliates.covered.senzing.jsonl` (definitely or possibly 50 percent owned: screen
  against this) and `affiliates.review.senzing.jsonl` (control without a 50 percent share, which the rules do not
  reach). `affiliates.senzing.jsonl` keeps both, and each record has `VERDICT_CLASS`. Watchman keeps only names, so
  `docs/watchman.md` now points at the covered file.
- A party named on one list now gets a record when another rule reaches it (an Entity List company 50 percent owned
  by an SDN is blocked under `ofac-50`); `LISTED_ON` names its lists.
- ownership-rules 0.1.1: the BIS rule reaches foreign companies owned through a US holding company, and Puerto Rico
  and the US territories count as home. The engine is installed from its commit on GitHub, pinned by hash, not from
  PyPI.
- Proofs name the `share_basis` behind each verdict, and each link its `basis` (`accounting_consolidation`,
  `branch`, ...): GLEIF consolidation percentages are not equity filings.
- A release is published only when a list or an affiliates record changed. The release decision ignores the
  per-record input hash, which changed every time GLEIF republished its files.
- Empty files are left out of a release (GitHub rejects empty assets).

## 0.4.0 (2026-11-17)

- Derived affiliates feed: `affiliates.py` runs after the list build and publishes `affiliates.senzing.jsonl`,
  `affiliates.ftm.json`, `affiliates.manifest.json` and `affiliates.changes.jsonl` with each release. Seeds are
  this repository's lists plus the SDN, Entity and MEU lists from the trade.gov Consolidated Screening List; links
  are GLEIF Level 2 relationship records (CC0) and the 1260H parent/subsidiary links; verdicts come from
  [ownership-rules](https://github.com/Mattbusel/ownership-rules) (`ofac-50`, `bis-affiliates`, and `any-list-50`
  as a screening signal over this repository's lists). Every record carries its proof path, the input hash and
  the state "evidence, not determination". `CHANGES.md` gets an Affiliates section with per-record events.
- A failed affiliates build keeps the previous affiliates files and never blocks the lists.
- `docs/affiliates.md`: method, fields, limits, and Watchman and yente setup.

## 0.3.1 (2026-11-16)

- FCC Covered List: the category entries in `manifest.json` (`notes.category_entries`) no longer carry the
  page's footnote markers in their text. The power inverter entry read "Foreign-produced power inverters, £
  except ..." and the robotic devices entry "..., 3 except ..." (a superscript 3 after Unicode normalization),
  which looked like encoding damage. The markers are removed and each entry gets a `footnotes` list with the
  footnote text they point to. Test against the full page as archived on 2026-11-07.

## 0.3.0 (2026-11-16)

- `tools/load.py`: a standard-library Python loader. `load(list)` reads the latest release (or a local file),
  `names()` and `parent_id()` read a record, and `--format csv` / `--format ftm` convert the files to flat CSV or
  to FollowTheMoney entities (`Company`, `Sanction`, and `Ownership` from each listed parent to its named
  subsidiaries).
- Go loader: `go get github.com/Mattbusel/us-restricted-parties/go` (module tag `go/v0.1.0`), with `Load`,
  `Read`, `LoadManifest` and typed records.
- `docs/lists.md`: one section per list with the source, operator, how often it changes, what the build does to
  it, and a field table mapping every field to Senzing and to FollowTheMoney.
- CI checks both loaders offline against a six-record excerpt of a real release and against the latest release
  assets.

## 0.2.0 (2026-11-15)

- Hugging Face mirror: `hf_publish.py` writes one parquet split per list (flat columns: name, aliases, parent,
  listed date, source agency, source document and text) plus the Senzing files, manifest and dataset card to
  huggingface.co/datasets/tensorust/us-restricted-parties. `load_dataset("tensorust/us-restricted-parties",
  split="us_dhs_uflpa")` loads one list; the `all` config loads every list.
- The daily Action runs the mirror after the release step when the `HF_TOKEN` secret is set, and uploads only when
  the dataset does not already hold the same lists.
- Offline tests for the flat rows and the dataset card.

## 0.1.0 (2026-11-15)

- First release. Builds Senzing JSON Lines for the DoD 1260H list (with named subsidiaries and later removal
  notices applied), Section 889 (FAR 52.204-25), the FCC Covered List, the DHS UFLPA Entity List and an interim
  BIOSECURE list derived from the 1260H biotechnology listings.
- Daily GitHub Action publishes a release when any list changes; `releases/latest/download/<file>` always serves
  the current files.
- dhs.gov blocks GitHub's runners, so the UFLPA list falls back to the full list DHS publishes as the appendix
  of its latest Federal Register notice (same 190 entities), keeping the per-entity dates from the last dhs.gov build.
- Offline tests against real excerpts of each source.
