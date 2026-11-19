#!/usr/bin/env python3
"""Write status.json, the freshness heartbeat of the daily build.

    python status.py --dist dist --prev prev --last-release data-2026-11-18 --released false \\
        --run-url https://github.com/.../actions/runs/1 --job-status success --out status.json

The build cuts a data release only when a list changed, so the release alone cannot tell "checked today, nothing
changed" from "the build stopped" or from "an agency site failed and the previous file was kept". The workflow runs
this on every run (also when the build or an earlier step failed) and commits the result to the `status` branch:

    https://raw.githubusercontent.com/Mattbusel/us-restricted-parties/status/status.json

Per list it says when the agency was last read successfully (`fetched_at`, null when this run's pull failed),
whether this run's pull worked (`upstream_ok`), how many records and which sha256 the served file has, and the day
the served data is from (`data_date`). docs/status.md has the schema and what a consumer should check.

Standard library only.
"""
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
URL = 'https://raw.githubusercontent.com/Mattbusel/us-restricted-parties/status/status.json'
LISTS = ['us_dod_1260h', 'us_far_889', 'us_fcc_covered', 'us_dhs_uflpa', 'us_biosecure']


def _read(p):
    try:
        return json.loads(Path(p).read_text(encoding='utf8'))
    except (OSError, ValueError):
        return None


def _day(ts):
    return ts[:10] if ts else None


def heartbeat(dist, prev=None, last_release=None, released=None, run_url=None, job_status=None, now=None):
    """Build the status dict from this run's dist/ (and prev/, the latest release as downloaded at the start)."""
    now = now or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    dist = Path(dist)
    man = _read(dist / 'manifest.json')
    prev_man = _read(Path(prev) / 'manifest.json') if prev else None
    errors = {}
    lists = {}
    if man is None:
        errors['build'] = 'this run produced no manifest.json (the build failed before writing it); ' \
                          'the lists below are the latest release, not re-checked today'
        src = prev_man or {}
    else:
        src = man
        errors.update(man.get('errors') or {})
    for key in LISTS:
        m = (src.get('lists') or {}).get(key)
        if m is None:
            lists[key] = dict(fetched_at=None, upstream_ok=False, rows=None, sha256=None, data_date=None,
                              in_release=False)
            if man is not None:
                errors.setdefault(key, 'no file for this list in this run')
            continue
        upstream_key = 'us_dod_1260h' if key == 'us_biosecure' else key
        ok = man is not None and upstream_key not in (man.get('errors') or {})
        as_of = m.get('data_as_of') or m.get('fetched_at') or (src.get('built_at') if upstream_key not in (src.get('errors') or {}) else None)
        lists[key] = dict(fetched_at=m.get('fetched_at') if man is not None else None, upstream_ok=ok,
                          rows=m.get('records'), sha256=m.get('sha256'), data_date=_day(as_of),
                          latest_listing=m.get('latest_listing'),
                          error=(man.get('errors') or {}).get(upstream_key) if man is not None else None)
        if key == 'us_biosecure' and (m.get('notes') or {}).get('omb_watch_error'):
            lists[key]['omb_watch_error'] = m['notes']['omb_watch_error']

    aff = _read(dist / 'affiliates.manifest.json')
    if aff is None:
        affiliates = dict(built=False, upstream_ok=False, stale=None, data_date=None, rows=None, sha256=None)
        if man is not None:
            errors['affiliates'] = 'affiliates were not built in this run'
    else:
        affiliates = dict(built=True, upstream_ok=not aff.get('stale'), stale=bool(aff.get('stale')),
                          data_date=aff.get('data_date'), rows=aff.get('records'), sha256=aff.get('sha256'),
                          gleif_route=aff.get('gleif_route'), gleif_coverage=aff.get('gleif_coverage'),
                          error=aff.get('error'))
        if aff.get('stale'):
            errors['affiliates'] = aff.get('error') or 'stale: carried from the previous release'

    content = (man or {}).get('content_sha256')
    unchanged = None if man is None or prev_man is None else content == prev_man.get('content_sha256')
    ok = man is not None and not errors and job_status in (None, 'success')
    return dict(
        schema=SCHEMA, url=URL, generated_at=now, ok=ok,
        build_ok=man is not None, job_status=job_status, run_url=run_url,
        last_release_tag=last_release or None, released_this_run=released,
        lists_unchanged_since_last_release=unchanged, content_sha256=content,
        lists=lists, affiliates=affiliates, errors=errors,
    )


def _bool(v):
    return {'true': True, 'false': False}.get((v or '').strip().lower())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dist', default='dist')
    ap.add_argument('--prev', default=None)
    ap.add_argument('--last-release', default=None)
    ap.add_argument('--released', default=None, help='true or false: whether this run cut a data release')
    ap.add_argument('--run-url', default=None)
    ap.add_argument('--job-status', default=None, help="the workflow's job.status at this point")
    ap.add_argument('--out', default='status.json')
    a = ap.parse_args(argv)
    hb = heartbeat(a.dist, a.prev, a.last_release, _bool(a.released), a.run_url, a.job_status)
    Path(a.out).write_text(json.dumps(hb, indent=1, ensure_ascii=False) + '\n', encoding='utf8', newline='\n')
    print(json.dumps(dict(ok=hb['ok'], errors=hb['errors'], last_release_tag=hb['last_release_tag'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
