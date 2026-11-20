# us-restricted-parties (Python loader)

Loads the [US Restricted Parties feed](https://github.com/Mattbusel/us-restricted-parties): five US government
lists that are not in the trade.gov Consolidated Screening List (DoD 1260H with every named subsidiary, Section
889, the FCC Covered List, the DHS UFLPA Entity List, interim BIOSECURE), rebuilt every day from the official
sources, plus the derived affiliates (companies those parties own 50 percent or more, with proof paths). One
standard-library module, Python 3.9+, typed, no dependencies.

```
pip install "us-restricted-parties @ https://github.com/Mattbusel/us-restricted-parties/releases/download/v0.7.0/us_restricted_parties-0.7.0-py3-none-any.whl"
```

(`pip install us-restricted-parties` once the package is on PyPI. Or copy `src/us_restricted_parties/__init__.py`
into your project as `us_restricted_parties.py`.)

```python
from us_restricted_parties import latest_release, load, names, verdicts

tag = latest_release()                                    # "data-2026-11-18": keep it with your results
for rec in load("us_dhs_uflpa", release=tag, verify=True, cache_dir=".urp-cache"):
    print(rec["RECORD_ID"], names(rec), rec["SOURCE_URL"])
for rec in load("affiliates.covered", release=tag, verify=True, cache_dir=".urp-cache"):
    print(rec["NAME_ORG"], verdicts(rec))                 # {"ofac-50": "definitely", ...}
```

- `release` pins a data release (`data-YYYY-MM-DD[-HHMM]`); releases are never deleted, so the bytes never change.
- `verify=True` checks each file against the SHA-256 in the same release's `manifest.json`.
- `cache_dir` keeps a release's files on disk, so a service reads GitHub once per release, not once per start.
- `us-restricted-parties --format csv` or `--format ftm` converts to CSV or FollowTheMoney.

Code MIT; data CC BY 4.0 ("US Restricted Parties feed by Tensorust LLC"). Results are evidence for a compliance
review, not a legal determination. Docs: [integration](https://github.com/Mattbusel/us-restricted-parties/blob/main/docs/integration.md),
[lists and fields](https://github.com/Mattbusel/us-restricted-parties/blob/main/docs/lists.md),
[maintenance](https://github.com/Mattbusel/us-restricted-parties/blob/main/docs/maintenance.md).

Check that a screening tool actually has every US restricted list, current and with names (exit 1 when not):

```
us-restricted-parties coverage path/to/screening-tool
```

See [docs/coverage.md](https://github.com/Mattbusel/us-restricted-parties/blob/main/docs/coverage.md) and the GitHub
Action `Mattbusel/us-restricted-parties@v0.7.0`.
