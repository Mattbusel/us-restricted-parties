# Integrating the feed

For teams adding these lists, or the derived affiliates, to a screening engine, a vendor-onboarding check or a
compliance data warehouse.

## What you depend on

- **Files, not a service.** Every daily build is a GitHub release with plain JSON Lines files and a
  `manifest.json`. Nothing to call, no key, no rate limit beyond GitHub's downloads.
- **Stable ids.** `RECORD_ID` is derived from the listed name and does not change from build to build, so you can
  key alerts and case notes on it. Affiliates use `lei:<LEI>` where an LEI exists.
- **Pinnable.** Each build is tagged `data-YYYY-MM-DD[-HHMM]`. The build never deletes a data release, so
  `releases/download/<tag>/<file>` keeps returning the same bytes, and `manifest.json` in the same release gives
  each file's SHA-256.
- **Licenses.** Data CC BY 4.0 (credit "US Restricted Parties feed by Tensorust LLC"), code MIT. The affiliates
  are derived from GLEIF data (CC0) and the trade.gov Consolidated Screening List (US government work).
- **No dependencies.** The Python loader is one standard-library file; the Go package uses the standard library.
- **Maintained.** Matthew Busel (Tensorust LLC) maintains the build. A source that fails keeps its previous
  release and the error is written to `manifest.json`, so a list never goes empty because of a bad fetch.

## Five lines

Python ([`tools/load.py`](../tools/load.py), copy the file or download it in your build):

```python
from load import load, names, verdicts

for rec in load("us_dhs_uflpa", release="data-2026-11-17-2241", verify=True):
    print(rec["RECORD_ID"], names(rec), rec["SOURCE_URL"])
for rec in load("affiliates.covered"):          # owned 50 percent or more by listed parties, or possibly
    print(rec["NAME_ORG"], verdicts(rec))       # {"ofac-50": "definitely", "bis-affiliates": "possibly"}
```

Go (`go get github.com/Mattbusel/us-restricted-parties/go`):

```go
src := restrictedparties.Source{Release: "data-2026-11-17-2241", Verify: true}
recs, err := src.Load(ctx, restrictedparties.UFLPA)
affs, err := src.LoadAffiliates(ctx, restrictedparties.AffiliatesCovered)
for _, a := range affs { fmt.Println(a.NameOrg, a.OwnershipVerdicts, a.LEINumber) }
```

Leave `Release` empty (or `release=None`) for the latest build. `Verify` checks the downloaded bytes against the
SHA-256 in that release's `manifest.json` and fails on a mismatch. `all.jsonl` has no hash of its own; verify the
single lists.

Engines that load files by URL:

- Watchman: [watchman.md](watchman.md) (the Senzing files load as they are).
- yente / OpenSanctions: `affiliates.ftm.json` is FollowTheMoney, and `python tools/load.py --format ftm` turns
  the lists into FtM; the recipe is in [affiliates.md](affiliates.md).
- Senzing: the files are Senzing JSON Lines; `DATA_SOURCE` names the list.

## Which file to screen against

| File | Use |
|---|---|
| `us_dod_1260h.jsonl`, `us_far_889.jsonl`, `us_fcc_covered.jsonl`, `us_dhs_uflpa.jsonl`, `us_biosecure.jsonl` | name screening; each record cites its government source (`SOURCE_URL`, `SOURCE_DOC`) |
| `affiliates.covered.senzing.jsonl` | screening hits: companies owned 50 percent or more by listed parties, or possibly so, with the proof path per rule |
| `affiliates.review.senzing.jsonl` | a review queue: control without a 50 percent share, which the rules do not reach |

Records with `INCLUDES_AFFILIATES: true` (Section 889, Hoshine, XPCC) cover unnamed subsidiaries; screen those by
ownership, not by name alone.

## Ownership of your own data

The affiliates verdicts come from [ownership-rules](https://github.com/Mattbusel/ownership-rules), which you can
run over your own ownership data (including FollowTheMoney and BODS exports) with these files as the listed
parties.

Results are evidence for a compliance review, not a legal determination. Confirm every match against its
`SOURCE_URL`.
