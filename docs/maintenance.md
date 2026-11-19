# Maintenance and provenance

What a team taking on this feed, or its loaders, can check for itself.

## Data

- **Built by CI, every day.** [build.yml](../.github/workflows/build.yml) runs on a daily schedule (and by hand
  after a source changes). It rebuilds the five lists from the official sources, derives the affiliates, and
  publishes a release only when a list, the affiliates records or the GLEIF data day changed. The run log of every
  build is public under Actions.
- **A heartbeat on every run.** Each run, including no-change days and failed agency pulls, rewrites
  [status.json](https://raw.githubusercontent.com/Mattbusel/us-restricted-parties/status/status.json) on the
  `status` branch: when the run happened, which agencies answered, and each list's row count, hash and data date.
  If it is more than 30 hours old the build has stopped ([docs/status.md](status.md)).
- **Every release is kept.** Tags are `data-YYYY-MM-DD[-HHMM]`; the build never deletes or edits a data release, so
  a pinned URL returns the same bytes. `manifest.json` in each release gives each file's SHA-256, record counts,
  `fetched_at`, the source URL or Federal Register document per list, and any fetch error.
- **A failed source does not empty a list.** The previous release's data for that list is kept and the error is
  written to `manifest.json` (example: dhs.gov answers HTTP 403 to the build, so UFLPA comes from the list in the
  appendix of DHS's latest Federal Register notice, and `notes.dhs_error` says so). A failed GLEIF download keeps
  the previous affiliates with `stale: true` and their real, older `data_date`.
- **Rule status.** The affiliates manifest names the ownership-rules version and, per rule, whether it binds on
  the build day (`rules`; the BIS Affiliates Rule is `stayed` until January 10, 2027).
- **Changes are listed.** `CHANGES.md` in each release lists names added and removed; `affiliates.changes.jsonl`
  lists affiliate records added, removed or with a changed verdict.

Data releases so far: data-2026-11-15 (first), data-2026-11-15-2049, data-2026-11-16, data-2026-11-17,
data-2026-11-17-1709, data-2026-11-17-2241, data-2026-11-18. The feed is new; the list above is its whole history.

## Code

- Python loader `us-restricted-parties` (this repository's `src/us_restricted_parties`, MIT): standard library
  only, typed (`py.typed`, `mypy --strict` in CI), tested on Python 3.9 to 3.13 on Linux and 3.12 on Windows and
  macOS, with an offline stand-in for the release server and a live check against the latest release.
- Go package `github.com/Mattbusel/us-restricted-parties/go`: standard library only; CI runs `go vet`, `gofmt`,
  the tests and a live check against the latest and a pinned release.
- Releases: `vX.Y.Z` tags build and test the Python wheel and attach it to a GitHub release that is never marked
  latest (so `releases/latest/download/` keeps serving the newest data); PyPI upload by trusted publishing once
  the PyPI project exists. Go versions are `go/vX.Y.Z` tags. Every change is in [CHANGELOG.md](../CHANGELOG.md).

## Stability

- Within a data release nothing changes. Across releases, `RECORD_ID` of a listed party stays the same (it is
  derived from the listed name), and affiliates use `lei:<LEI>` where an LEI exists.
- Record fields are only added, not renamed or removed, without a changelog entry and a minor version.
- Loader API: the names in `us_restricted_parties.__all__` and the exported Go identifiers.

## Taking it out again

The loaders have no dependencies and return plain dicts (Python) or structs (Go) of the Senzing JSON in the
files. Removing them is one requirement line and the call sites; the files themselves keep loading in Watchman,
Senzing or any JSON Lines reader without them.

Maintained by Matthew Busel (Tensorust LLC). Issues and pull requests on GitHub. Results are evidence for a
compliance review, not a legal determination.
