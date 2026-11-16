#!/usr/bin/env python3
"""Screen a CSV of company names against five US restricted-party lists that OFAC and most
default screening configs do not load: DoD 1260H (with named subsidiaries), Section 889,
the FCC Covered List, the DHS UFLPA Entity List and the interim BIOSECURE list.

    python check_names.py vendors.csv                # first column = name
    python check_names.py vendors.csv --column legal_name --min 0.90

Data: the free US Restricted Parties feed (CC BY 4.0), downloaded fresh from
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/all.jsonl
(or pass --feed path/to/all.jsonl to run offline). Standard library only, Python 3.9+.

Every hit prints the list, the listed name, the parent when the hit is a named subsidiary,
and the government source URL. A hit is a lead to review against that source, not a finding.
"""
import argparse, csv, difflib, json, re, sys, unicodedata, urllib.request

FEED = 'https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/all.jsonl'
SUFFIX = r'\b(co|company|corp|corporation|inc|incorporated|ltd|limited|llc|plc|group|holdings?|the|of|and)\b'


def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    return re.sub(r'\s+', ' ', re.sub(SUFFIX, ' ', s)).strip()


def load(src):
    if src.startswith('http'):
        req = urllib.request.Request(src, headers={'User-Agent': 'check_names/1.0'})
        lines = urllib.request.urlopen(req, timeout=60).read().decode('utf-8').splitlines()
    else:
        lines = open(src, encoding='utf-8').read().splitlines()
    out = []
    for line in lines:
        if not line.strip():
            continue
        r = json.loads(line)
        names = {r['NAME_ORG']} | {n['NAME_ORG'] for n in r.get('NAMES', []) if n.get('NAME_ORG')}
        out.append((r, [(n, norm(n)) for n in names if norm(n)]))
    return out


def best(name, recs, floor):
    q = norm(name)
    if not q:
        return []
    hits = []
    for r, names in recs:
        score, via = 0.0, ''
        for raw, n in names:
            s = 1.0 if n == q else difflib.SequenceMatcher(None, q, n).ratio()
            if s > score:
                score, via = s, raw
        if score >= floor:
            hits.append((round(score, 3), via, r))
    return sorted(hits, key=lambda h: -h[0])[:3]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('csv')
    ap.add_argument('--column', help='column holding the name (default: first column)')
    ap.add_argument('--min', type=float, default=0.88, help='minimum similarity, 0-1 (default 0.88)')
    ap.add_argument('--feed', default=FEED, help='feed URL or local all.jsonl')
    a = ap.parse_args()
    recs = load(a.feed)
    with open(a.csv, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    col = a.column or (list(rows[0].keys())[0] if rows else None)
    n_hit = 0
    for row in rows:
        name = (row.get(col) or '').strip()
        for score, via, r in best(name, recs, a.min):
            n_hit += 1
            parent = f" | subsidiary of {r['PARENT_NAME']}" if r.get('PARENT_NAME') else ''
            print(f"{name}\t{score}\t{r['LIST_NAME']}\t{r['NAME_ORG']} (matched '{via}'){parent}\t{r.get('SOURCE_URL', '')}")
    print(f'# {len(rows)} names screened against {len(recs)} records, {n_hit} hits (min {a.min})', file=sys.stderr)


if __name__ == '__main__':
    main()
