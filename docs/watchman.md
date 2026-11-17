# Loading the feed into moov-io/watchman

[Watchman](https://github.com/moov-io/watchman) reads Senzing JSON Lines through its `Download.Senzing` config
(`internal/download/download_senzing.go`). Each entry becomes its own source list, is downloaded at startup and
again every `DATA_REFRESH_INTERVAL` (12h by default).

## Config

```yaml
Watchman:
  Download:
    RefreshInterval: "12h"
    # Keep your other lists; Senzing lists are added to whatever IncludedLists already loads.
    IncludedLists: ["us_ofac", "us_csl"]
    Senzing:
      - SourceList: "us_dod_1260h"
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_dod_1260h.jsonl"
      - SourceList: "us_dhs_uflpa"
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_dhs_uflpa.jsonl"
      - SourceList: "us_fcc_covered"
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_fcc_covered.jsonl"
      - SourceList: "us_far_889"
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_far_889.jsonl"
      - SourceList: "us_biosecure"
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/us_biosecure.jsonl"
    # Optional: a GitHub outage then only logs a warning instead of failing the refresh.
    IgnoredDownloadErrors: ["us_dod_1260h", "us_dhs_uflpa", "us_fcc_covered", "us_far_889", "us_biosecure"]
```

Point `APP_CONFIG` at the file and start Watchman as usual, then search:

```
curl "http://localhost:8084/v2/search?name=Hikvision&type=business&limit=3"
```

For air-gapped deployments, download the files on a schedule and use `file:///path/to/us_dod_1260h.jsonl` as
the `Location`.

## Aliases

Every record carries its primary name twice: as the flat `NAME_ORG` field and as the `PRIMARY` entry of the `NAMES`
array, followed by the `ALIAS` entries. Watchman v1.2.2 and earlier read the flat `NAME_ORG` (so the primary name
always loads) but not the `NAME_ORG` values inside `NAMES`, so aliases such as "DJI", "Hikvision" and "XPCC" are not
yet searchable as alt names. A fix that reads them into `Business.AltNames` is proposed upstream; once it lands,
the 229 aliases in this feed load with no change to your config.

## Checking a load

```
curl -s "http://localhost:8084/v2/listinfo"   # list names, record counts and sha256 per list
```

`manifest.json` in each release has the record count and sha256 for every file. They match what `/v2/listinfo`
reports (`lists` and `listHashes`), so you can confirm a deployment loaded the current release.

## Derived affiliates

The affiliates files add companies that listed parties own or control (OFAC 50 Percent Rule, BIS Affiliates Rule,
from GLEIF ownership links), each with a verdict and proof path. Watchman keeps only the names, so it cannot show
the verdict. Load `affiliates.covered.senzing.jsonl` (owned 50 percent or more, or possibly so) as the screening
source; `affiliates.review.senzing.jsonl` (control without a 50 percent share, which the rules do not reach) is
optional and belongs under its own source list name so its hits go to review:

```yaml
      - SourceList: "us_restricted_affiliates"
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/affiliates.covered.senzing.jsonl"
      - SourceList: "us_restricted_affiliates_review"   # optional
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/affiliates.review.senzing.jsonl"
```

These records are evidence for review, not agency determinations; see [affiliates.md](affiliates.md).
