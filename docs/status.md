# Freshness heartbeat (status.json)

The daily build cuts a data release only when a list changed. A quiet week therefore has no new release, and the
release alone cannot tell three cases apart: the build ran and nothing changed, the build stopped running, or an
agency site failed and the build kept the previous file. The heartbeat answers that.

    https://raw.githubusercontent.com/Mattbusel/us-restricted-parties/status/status.json

Every run of [build.yml](../.github/workflows/build.yml) rewrites this file and commits it to the `status` branch:
no-change days, days an agency pull failed, and runs where the build itself failed. The URL does not move. The
branch history is the log of every run (`git log origin/status`).

## What to check

1. **Is it recent?** `generated_at` is the time of the last run. The build runs daily at 06:17 UTC. If
   `generated_at` is more than **30 hours** old, treat the feed as unconfirmed: the build did not run, and nothing
   says the lists are still current. (raw.githubusercontent.com caches for up to 5 minutes.)
2. **Did every agency answer?** `ok` is `true` only when the build wrote its manifest, every list's pull worked,
   the affiliates are fresh and the job succeeded. When `ok` is `false`, `errors` names what failed, keyed by list
   (`us_dhs_uflpa`, ...), `affiliates`, or `build`.
3. **How old is each list's data?** `lists.<key>.data_date` is the day the served data was read from the agency.
   On a day the pull failed, `upstream_ok` is `false`, `fetched_at` is `null`, `error` gives the reason, and
   `data_date` stays the earlier day the kept data comes from. Pick your own limit per list; a list whose
   `data_date` stops moving while `upstream_ok` is `false` is being served from an older pull.
4. **Is the release I hold the current one?** `last_release_tag` is the newest data release. If
   `lists_unchanged_since_last_release` is `true`, the lists built today are byte-identical to that release
   (`content_sha256` matches its `manifest.json`), and each `lists.<key>.sha256` equals the `sha256` of that file in
   the release's `manifest.json`.

## Schema (schema 1)

| field | meaning |
|---|---|
| `schema` | `1`. Fields are only added within a schema number. |
| `url` | this file's stable URL |
| `generated_at` | UTC time of this run (ISO 8601, `Z`) |
| `ok` | `true` when nothing below is in `errors` and the job succeeded |
| `build_ok` | `false` when the run wrote no `manifest.json` (tests or the build failed); the lists then describe the latest release and are not re-checked |
| `job_status` | the GitHub Actions job status when the heartbeat was written (`success`, `failure`, `cancelled`) |
| `run_url` | the Actions run that wrote this file |
| `last_release_tag` | newest data release after this run, for example `data-2026-11-18` |
| `released_this_run` | `true` if this run cut that release, `false` if nothing changed, `null` if the publish step did not run |
| `lists_unchanged_since_last_release` | `true` when today's lists equal the release that existed when the run started |
| `content_sha256` | this run's combined hash, as in `manifest.json` |
| `lists.<key>.fetched_at` | when this run read the agency, or `null` if the pull failed |
| `lists.<key>.upstream_ok` | whether this run's pull worked (`us_biosecure` follows `us_dod_1260h`, which it is derived from) |
| `lists.<key>.rows` | records in the served file |
| `lists.<key>.sha256` | SHA-256 of the served file, same value as in the release's `manifest.json` |
| `lists.<key>.data_date` | UTC day the served data was read from the agency |
| `lists.<key>.latest_listing` | latest listing date in the data |
| `lists.<key>.error` | the pull error, when `upstream_ok` is `false` |
| `affiliates` | `built`, `upstream_ok`, `stale`, `data_date`, `rows`, `sha256`, `gleif_route`, `gleif_coverage`, `error`, from `affiliates.manifest.json` |
| `errors` | everything that failed in this run, keyed by list, `affiliates` or `build` |

## Example check

```python
import json, urllib.request
from datetime import datetime, timezone, timedelta

URL = "https://raw.githubusercontent.com/Mattbusel/us-restricted-parties/status/status.json"
hb = json.load(urllib.request.urlopen(URL, timeout=30))
age = datetime.now(timezone.utc) - datetime.fromisoformat(hb["generated_at"].replace("Z", "+00:00"))
if age > timedelta(hours=30):
    print("unconfirmed: no build since", hb["generated_at"])
for key, l in hb["lists"].items():
    if not l["upstream_ok"]:
        print(key, "served from", l["data_date"], "because", l.get("error"))
```

```sh
curl -s https://raw.githubusercontent.com/Mattbusel/us-restricted-parties/status/status.json \
  | jq '{generated_at, ok, errors, last_release_tag}'
```

The heartbeat is written by [status.py](../status.py) (tested in [tests/test_status.py](../tests/test_status.py)).
