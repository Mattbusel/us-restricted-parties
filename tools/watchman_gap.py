#!/usr/bin/env python3
"""Ask a running Watchman (v0.5x/v0.6x/v1.x, /v2/search) for every name in one or more Senzing
JSON Lines files and report which names it matches and from which source list.

    python watchman_gap.py --watchman http://localhost:8084 us_dod_1260h.jsonl us_dhs_uflpa.jsonl > out.csv

A record counts as found when Watchman's best match for its primary name scores at or above
--min (default 0.7, the minMatch many integrations send) AND that match is not one of the records the files themselves loaded
(source list given with --ignore-source, e.g. us_dod_1260h). Run it once against the config you
screen with today and once with the extra lists loaded to see what changes. Standard library only.
"""
import argparse, csv, json, sys, urllib.parse, urllib.request


def search(base, name, kind, limit, min_match):
    q = urllib.parse.urlencode({'name': name, 'type': kind, 'limit': limit, 'minMatch': min_match})
    with urllib.request.urlopen(f'{base.rstrip("/")}/v2/search?{q}', timeout=60) as r:
        return json.load(r).get('entities') or []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+')
    ap.add_argument('--watchman', default='http://localhost:8084')
    ap.add_argument('--min', type=float, default=0.7,
                    help='minMatch sent to Watchman and used as the hit cutoff (default 0.7)')
    ap.add_argument('--ignore-source', action='append', default=[],
                    help='source list ids to ignore when deciding "found" (the lists under test)')
    a = ap.parse_args()
    ignore = {s.lower() for s in a.ignore_source}
    w = csv.writer(sys.stdout)
    w.writerow(['list', 'name', 'parent', 'source_url', 'found', 'best_match', 'best_score', 'best_source',
                'test_list_match', 'test_list_score'])
    found = total = 0
    for path in a.files:
        for line in open(path, encoding='utf-8'):
            if not line.strip():
                continue
            r = json.loads(line)
            name = r['NAME_ORG']
            kind = 'business' if r.get('RECORD_TYPE', 'ORGANIZATION') == 'ORGANIZATION' else 'person'
            ents = search(a.watchman, name, kind, 10, a.min)
            other = [e for e in ents if str(e.get('sourceList', '')).lower() not in ignore]
            mine = [e for e in ents if str(e.get('sourceList', '')).lower() in ignore]
            b = other[0] if other else {}
            m = mine[0] if mine else {}
            ok = bool(b) and float(b.get('match', 0)) >= a.min
            found += ok
            total += 1
            w.writerow([r.get('DATA_SOURCE'), name, r.get('PARENT_NAME', ''), r.get('SOURCE_URL', ''), ok,
                        b.get('name', ''), round(float(b.get('match', 0)), 3), b.get('sourceList', ''),
                        m.get('name', ''), round(float(m.get('match', 0)), 3) if m else ''])
    print(f'# {total} names, {found} matched an existing list at >= {a.min}, {total - found} missed',
          file=sys.stderr)


if __name__ == '__main__':
    main()
