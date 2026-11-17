#!/usr/bin/env python3
"""Derive the affiliates feed: entities that listed parties own or control, with a verdict and proof path per rule.

    python affiliates.py --lists dist --out dist [--prev prev] [--cache .cache]

Seeds
  - this feed's own lists (DoD 1260H, Section 889, FCC Covered List, UFLPA, BIOSECURE), read from --lists;
  - the Consolidated Screening List from trade.gov: SDN List (with program tags), Entity List and MEU List.

Ownership and control edges
  - GLEIF Level 2 relationship records (CC0): IS_DIRECTLY_CONSOLIDATED_BY (accounting consolidation, a control
    link; carries the consolidation percentage when the child reported one), IS_INTERNATIONAL_BRANCH_OF (a branch
    is part of its head office), and IS_ULTIMATELY_CONSOLIDATED_BY where no direct parent is reported;
  - parent and subsidiary links that the 1260H notices state (share not stated).

Seeds are matched to LEIs by the LEI an SDN entry carries, by a registration number plus country, or by an exact
normalized legal or other name plus country that is unique on both sides. Entries on different lists that share a
name are one party and all keep the match. Every match records its method.

GLEIF files come from the golden copy server, else GLEIF's AWS Open Data mirror, else the per-record GLEIF API. When
the build fails (no route works, or the GLEIF data is older than --max-age-hours), the previous feed in --prev is
carried forward unchanged with stale: true, its own older data_date and the error, and the run exits 0 with a GitHub
warning, so the five lists still publish; without a previous feed it exits 2 and writes no affiliates files.
affiliates.manifest.json carries the data date (data_date, data_as_of) of every feed it describes.

Rules (ownership-rules): ofac-50 over the SDN List, bis-affiliates over the Entity List, MEU List and 744.8 SDNs,
and any-list-50 over this feed's own lists as a screening signal (those lists carry no ownership rule of their own).

Output in --out
  affiliates.senzing.jsonl          one Senzing ORGANIZATION per derived entity with its verdicts and proof paths
  affiliates.covered.senzing.jsonl  the records whose strongest verdict is definitely or possibly (50 percent or
                                    more owned, or possibly so): the file to screen against
  affiliates.review.senzing.jsonl   the records that are control_only under every rule (control or consolidation
                                    without a share that reaches 50 percent): for review, not for screening hits
  affiliates.ftm.json               the same as FollowTheMoney entities (Company + Ownership), one per line
  affiliates.manifest.json          counts, input hashes, match statistics, rule versions
  affiliates.changes.jsonl          per-record change events against --prev
  CHANGES.md                        an "Affiliates" section summarizing them (appended)
  manifest.json                     gains an "affiliates" entry; content_sha256 then covers the affiliates records,
                                    without the per-record input hash, so a day on which only the input files
                                    were republished (GLEIF does that several times a day) makes no new release

A record is written for every entity that has a verdict other than not under at least one rule where it is not
itself a seed. An entity named on one list can therefore appear because it is owned under another rule (an Entity
List company 50 percent owned by an SDN is blocked under ofac-50); LISTED_ON says which lists name it.

Each record is evidence for a compliance review, not a determination that the entity is sanctioned or restricted.
"""
import argparse, contextlib, csv, gzip, hashlib, io, json, re, sys, time, unicodedata, zipfile
import urllib.error, urllib.parse, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ownership_rules import __version__ as ENGINE_VERSION, evaluate, load_rule

CSL_URL = 'https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.json'
# GLEIF golden copy, three routes in order. (1) The golden copy API names the latest publish and its file. (2) GLEIF's
# own mirror of the same files in the AWS Open Data registry (s3://gleif, registry.opendata.aws/lei), used when the
# golden copy file server refuses the download (it has answered HTTP 403 for every client since 2026-11-17 17:00
# UTC while its API still lists the files). (3) The GLEIF API per record, for the entities this feed needs.
GLEIF_PUBLISH_URL = 'https://goldencopy.gleif.org/api/v2/golden-copies/publishes/{kind}/latest'
GLEIF_S3 = 'https://gleif.s3.eu-central-1.amazonaws.com'
GLEIF_S3_KIND = {'lei2': 'lei', 'rr': 'rr'}
GLEIF_API = 'https://api.gleif.org/api/v1'
GLEIF_KINDS = ('rr', 'lei2')
# The feed is published only from GLEIF data at most this old (hours since the GLEIF publish).
MAX_AGE_HOURS = 48
FEED_URL = 'https://github.com/Mattbusel/us-restricted-parties'
UA = 'us-restricted-parties affiliates build (+https://github.com/Mattbusel/us-restricted-parties)'
DATA_SOURCE = 'US_RESTRICTED_AFFILIATES'
EVIDENCE_STATE = 'evidence, not determination'
DISCLAIMER = ('Derived from public ownership data by applying the named rule to the named lists. It is evidence for '
              'a compliance review, not a determination by any agency that this entity is blocked, listed or '
              'restricted. Ownership percentages come from the cited sources and may be missing, stale or wrong.')
OWN_LISTS = ['us_dod_1260h', 'us_far_889', 'us_fcc_covered', 'us_dhs_uflpa', 'us_biosecure']
# Our lists name Chinese companies except Kaspersky (FCC Covered List); names are matched in these countries first.
OWN_COUNTRIES = {'CN', 'HK', 'MO'}
RULES = ['ofac-50', 'bis-affiliates', 'any-list-50']
# Senzing files beside the combined one: what to screen against, and what only calls for a review.
SPLIT_FILES = {'covered': 'affiliates.covered.senzing.jsonl', 'review': 'affiliates.review.senzing.jsonl'}
COVERED_VERDICTS = ('definitely', 'possibly')
# Short codes for what a link's share measures; copied into every proof step that uses the link.
BASIS_NOTES = {
    'branch': 'branch of the head office (same legal person)',
    'accounting_consolidation': 'accounting consolidation, consolidation percentage reported by the child',
    'accounting_consolidation_no_pct': 'accounting consolidation, no percentage reported',
    'ultimate_accounting_consolidation': 'ultimate accounting consolidation, no direct parent reported',
    'notice_subsidiary': 'subsidiary named in the notice, share not stated',
}
REG_ID_TYPES = ('Registration Number', 'Registration ID', 'Business Registration Number', 'Company Number',
                'Unified Social Credit Code (USCC)', 'Tax ID No.', 'Commercial Registry Number',
                'Government Gazette Number', 'Economic Register Number (CBLS)', 'Business Number',
                'Chamber of Commerce Number', 'Public Registration Number', 'Trade License Number')

LEGAL_FORMS = [
    (r'\bCOMPANY LIMITED\b', 'CO LTD'), (r'\bCO\s*\.?\s*,?\s*LTD\b', 'CO LTD'), (r'\bLIMITED\b', 'LTD'),
    (r'\bCORPORATION\b', 'CORP'), (r'\bINCORPORATED\b', 'INC'), (r'\bCOMPANY\b', 'CO'),
    (r'\bLIMITED LIABILITY COMPANY\b', 'LLC'), (r'\bJOINT[- ]STOCK COMPANY\b', 'JSC'),
    (r'\bPUBLIC LIMITED COMPANY\b', 'PLC'), (r'\bSOCIETE ANONYME\b', 'SA'),
]


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, file=sys.stderr, flush=True)


def name_key(s):
    """Exact-match key: ASCII-folded, uppercase, legal forms in one spelling, punctuation and spaces removed."""
    if not s:
        return ''
    s = unicodedata.normalize('NFKC', s)
    if re.search(r'[㐀-鿿]', s):  # keep CJK names as they are (no transliteration)
        return re.sub(r'[\s\(\)（）.,\-]', '', s)
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().upper().replace('&', ' AND ')
    s = re.sub(r'[.,]', ' ', s)
    for pat, rep in LEGAL_FORMS:
        s = re.sub(pat, rep, s)
    s = re.sub(r'^THE\s+', '', s)
    return re.sub(r'[^A-Z0-9]', '', s)


def reg_key(s):
    return re.sub(r'[^A-Z0-9]', '', (s or '').upper())


class FetchError(RuntimeError):
    pass


def _request(url, headers=None):
    h = {'User-Agent': UA, 'Accept': '*/*', 'Accept-Encoding': 'identity'}
    h.update(headers or {})
    return urllib.request.Request(url, headers=h)


def http_open(url, headers=None, timeout=600, tries=4, backoff=5.0):
    """urlopen with polite retries: 429, 5xx and network errors are retried with backoff (Retry-After honored);
    other 4xx are not, since asking again will not change the answer."""
    last = None
    for attempt in range(tries):
        try:
            return urllib.request.urlopen(_request(url, headers), timeout=timeout)
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 429 and e.code < 500:
                raise FetchError(f'HTTP {e.code} from {url}') from e
            wait = backoff * 2 ** attempt
            try:
                wait = max(wait, float(e.headers.get('Retry-After') or 0))
            except ValueError:
                pass
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last, wait = e, backoff * 2 ** attempt
        if attempt < tries - 1:
            log(f'retry {attempt + 1}/{tries - 1} in {wait:.0f}s: {url} ({last})')
            time.sleep(wait)
    raise FetchError(f'{url}: {last}')


def fetch(url, dest, timeout=600, headers=None):
    """Download to dest through a .part file, so a failed download never leaves a truncated file behind.
    Returns the response headers."""
    tmp = Path(str(dest) + '.part')
    with http_open(url, headers, timeout) as r, open(tmp, 'wb') as f:
        hdrs = dict(r.headers)
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    tmp.replace(dest)
    return hdrs


def get_json(url, timeout=120):
    with http_open(url, {'Accept': 'application/vnd.api+json, application/json'}, timeout) as r:
        return json.loads(r.read().decode('utf8'))


def iso_utc(s):
    """'2026-11-17 16:00:00', '2026-11-17T16:00:00Z' or '20261117-1600' -> '2026-11-17T16:00:00Z'."""
    s = (s or '').strip()
    m = re.match(r'^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})$', s)
    if m:
        return f'{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:00Z'
    m = re.match(r'^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})', s)
    return f'{m[1]}T{m[2]}Z' if m else None


@contextlib.contextmanager
def open_csv(path):
    """A text stream over a GLEIF CSV, zipped (golden copy server, API fallback) or gzipped (AWS mirror)."""
    with open(path, 'rb') as f:
        magic = f.read(2)
    if magic == b'\x1f\x8b':
        with gzip.open(path, 'rt', encoding='utf-8', newline='') as fh:
            yield fh
    else:
        with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as fh:
            yield io.TextIOWrapper(fh, encoding='utf-8', newline='')


def gleif_from_golden_copy(kind, dest):
    meta = get_json(GLEIF_PUBLISH_URL.format(kind=kind))['data']
    f = meta['full_file']['csv']
    fetch(f['url'], dest)
    return dict(route='golden-copy', url=f['url'], publish_date=iso_utc(meta['publish_date']),
                record_count=f.get('record_count'), coverage='full')


def s3_latest_key(kind, today=None, days=4):
    """Newest golden copy CSV in GLEIF's AWS Open Data mirror: data/csv/<kind>/date=YYYY-MM-DD/time=HH:MM/..."""
    today = today or datetime.now(timezone.utc).date()
    for d in range(days):
        day = (today - timedelta(days=d)).isoformat()
        q = urllib.parse.urlencode({'list-type': '2', 'prefix': f'data/csv/{GLEIF_S3_KIND[kind]}/date={day}/'})
        with http_open(f'{GLEIF_S3}/?{q}', timeout=60) as r:
            keys = re.findall(r'<Key>([^<]+\.csv\.gz)</Key>', r.read().decode('utf8'))
        if keys:
            return sorted(keys)[-1]
    raise FetchError(f'no {kind} file in {GLEIF_S3} for the last {days} days')


def gleif_from_s3(kind, dest):
    key = s3_latest_key(kind)
    url = f'{GLEIF_S3}/{key}'
    fetch(url, dest)
    m = re.search(r'/(\d{8}-\d{4})-gleif-goldencopy', key)
    return dict(route='aws-open-data', url=url, publish_date=iso_utc(m[1]) if m else None, coverage='full')


def fetch_gleif(cache):
    """Download the Level 2 relationship (rr) and Level 1 (lei2) golden copies from the golden copy server, else
    from GLEIF's AWS mirror. Returns {kind: (path, meta)}; both files come from the same route."""
    errors = []
    for route, fn, ext in (('golden-copy', gleif_from_golden_copy, 'zip'), ('aws-open-data', gleif_from_s3, 'gz')):
        out = {}
        try:
            for kind in GLEIF_KINDS:
                dest = Path(cache) / f'gleif-{kind}.csv.{ext}'
                log('GLEIF', kind, 'via', route)
                out[kind] = (dest, fn(kind, dest))
            return out
        except Exception as e:  # noqa: BLE001  (try the next route; all failures are reported together)
            errors.append(f'{route}: {type(e).__name__}: {e}')
            log('GLEIF route failed:', errors[-1])
    raise FetchError('GLEIF golden copy unavailable; ' + ' | '.join(errors))


# ---------------------------------------------------------------- GLEIF API fallback (per record)

RR_API_COLS = ['Relationship.StartNode.NodeID', 'Relationship.StartNode.NodeIDType', 'Relationship.EndNode.NodeID',
               'Relationship.EndNode.NodeIDType', 'Relationship.RelationshipType', 'Relationship.RelationshipStatus'] + \
              [f'Relationship.Quantifiers.{i}.{k}' for i in range(1, 6)
               for k in ('MeasurementMethod', 'QuantifierAmount', 'QuantifierUnits')] + \
              ['Registration.LastUpdateDate', 'Registration.ValidationSources']
LEI_API_COLS = ['LEI', 'Entity.LegalName'] + [f'Entity.OtherEntityNames.OtherEntityName.{i}' for i in range(1, 6)] + \
               [f'Entity.TransliteratedOtherEntityNames.TransliteratedOtherEntityName.{i}' for i in range(1, 6)] + \
               ['Entity.LegalAddress.Country', 'Entity.HeadquartersAddress.Country',
                'Entity.RegistrationAuthority.RegistrationAuthorityEntityID', 'Entity.EntityStatus',
                'Registration.RegistrationStatus']


class GleifApi:
    """The GLEIF API, politely: one request at a time, about 50 a minute (GLEIF allows 60), retries on 429."""

    def __init__(self, max_requests=4000, interval=1.2):
        self.n, self.max, self.interval, self.last, self.publish_date = 0, max_requests, interval, 0.0, None

    def get(self, path, **params):
        if self.n >= self.max:
            raise FetchError(f'GLEIF API request budget ({self.max}) spent')
        wait = self.last + self.interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.n += 1
        self.last = time.monotonic()
        url = f'{GLEIF_API}/{path}' + ('?' + urllib.parse.urlencode(params) if params else '')
        d = get_json(url)
        pd = ((d.get('meta') or {}).get('goldenCopy') or {}).get('publishDate')
        if pd:
            self.publish_date = iso_utc(pd)
        return d

    def first_page(self, path, size=50, **params):
        return self.get(path, **{**params, 'page[size]': size, 'page[number]': 1}).get('data') or []

    def pages(self, path, **params):
        page = 1
        while True:
            d = self.get(path, **{**params, 'page[size]': 200, 'page[number]': page})
            yield from d.get('data') or []
            pg = (d.get('meta') or {}).get('pagination') or {}
            if page >= int(pg.get('lastPage') or 1):
                return
            page += 1


def _pad(xs, n):
    xs = list(xs)[:n]
    return xs + [''] * (n - len(xs))


def _lei_row(rec):
    a = rec['attributes']
    e, reg = a['entity'], a['registration']
    return ([a['lei'], (e.get('legalName') or {}).get('name') or '']
            + _pad((x['name'] for x in e.get('otherNames') or []), 5)
            + _pad((x['name'] for x in e.get('transliteratedOtherNames') or []), 5)
            + [(e.get('legalAddress') or {}).get('country') or '', (e.get('headquartersAddress') or {}).get('country') or '',
               e.get('registeredAs') or '', e.get('status') or '', reg.get('status') or ''])


def _rr_row(rec):
    a = rec['attributes']
    rel, reg = a['relationship'], a.get('registration') or {}
    q = []
    for x in (rel.get('quantifiers') or [])[:5]:
        amt = x.get('quantifierAmount')
        q += [x.get('measurementMethod') or '', '' if amt is None else str(amt), x.get('quantifierUnits') or '']
    return ([rel['startNode']['id'], rel['startNode']['type'], rel['endNode']['id'], rel['endNode']['type'],
             rel['type'], rel.get('status') or ''] + _pad(q, 15)
            + [reg.get('lastUpdateDate') or '', reg.get('corroborationLevel') or ''])


def _write_zip(path, cols, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(Path(path).name[:-4], buf.getvalue())


def needed_leis(seeds, prev_dir):
    """The LEIs the feed needs without the bulk files: LEIs printed on list entries, and every LEI in the previous
    feed (its records, their seeds and every entity and owner on their proof paths)."""
    want = {num.strip().upper() for s in seeds for typ, num, _ in s['ids'] if typ == 'Legal Entity Number'}
    p = Path(prev_dir) / 'affiliates.senzing.jsonl' if prev_dir else None
    if p and p.exists():
        for r in read_jsonl(p):
            ids = [r.get('RECORD_ID', '')]
            for pr in (r.get('OWNERSHIP_PROOF') or {}).values():
                ids += pr.get('seeds', [])
                for st in pr.get('proof', []):
                    ids.append(st.get('entity', ''))
                    ids += [v.get('owner', '') for v in st.get('via', [])]
            want |= {i[4:] for i in ids if i.startswith('lei:')}
    return want


def gleif_from_api(seeds, prev_dir, cache, api=None, max_depth=4):
    """Per-record fallback: Level 1 records for the needed LEIs and for the names on this feed's own lists, and the
    child relationships below them (to max_depth), written in the golden copy CSV layout. Partial by nature: an SDN
    or Entity List party matched only by name and absent from the previous feed is not looked up."""
    api = api or GleifApi()
    want = needed_leis(seeds, prev_dir)
    names = sorted({n for s in seeds if s['list'].startswith('US_') for n in s['names'] if len(name_key(n)) >= 6})
    lei_rows, rr_rows, have = {}, {}, set()

    def load(leis):
        leis = sorted({l for l in leis if l not in lei_rows})
        for i in range(0, len(leis), 100):
            for rec in api.pages('lei-records', **{'filter[lei]': ','.join(leis[i:i + 100])}):
                lei_rows[rec['attributes']['lei']] = _lei_row(rec)

    for n in names:  # the search is fuzzy and ranked: read its first page, keep exact normalized-name hits only
        for rec in api.first_page('lei-records', **{'filter[entity.names]': n}):
            row = _lei_row(rec)
            if name_key(n) in {name_key(x) for x in row[1:12] if x}:
                lei_rows[row[0]] = row
    load(want)
    frontier, depth = set(lei_rows), 0
    while frontier and depth < max_depth:
        nxt = set()
        for lei in sorted(frontier):
            have.add(lei)
            for kind in ('direct-child-relationships', 'ultimate-child-relationships'):
                for rec in api.pages(f'lei-records/{lei}/{kind}'):
                    row = _rr_row(rec)
                    rr_rows[tuple(row[:6])] = row
                    if row[1] == 'LEI' and row[0] not in have:
                        nxt.add(row[0])
        load(nxt)
        frontier, depth = nxt - have, depth + 1
    out = {}
    for kind, cols, rows in (('rr', RR_API_COLS, list(rr_rows.values())), ('lei2', LEI_API_COLS, list(lei_rows.values()))):
        dest = Path(cache) / f'gleif-{kind}.csv.zip'
        _write_zip(dest, cols, rows)
        out[kind] = (dest, dict(route='gleif-api', url=GLEIF_API, publish_date=api.publish_date, coverage='partial',
                                records=len(rows), requests=api.n))
    log('GLEIF API fallback:', len(lei_rows), 'entities,', len(rr_rows), 'relationships,', api.n, 'requests')
    return out


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def read_jsonl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding='utf8').splitlines() if l.strip()]


# ---------------------------------------------------------------- seeds

def own_seeds(lists_dir):
    """Our lists as seed parties: [{id, lists, names, country(None), parent}]"""
    seeds = []
    for key in OWN_LISTS:
        p = Path(lists_dir) / f'{key}.jsonl'
        if not p.exists():
            continue
        for r in read_jsonl(p):
            names = [n['NAME_ORG'] for n in r.get('NAMES', []) if n.get('NAME_ORG')] or [r['NAME_ORG']]
            parent = None
            for rel in r.get('RELATIONSHIPS', []):
                if rel.get('REL_POINTER_ROLE') == 'SUBSIDIARY_OF':
                    parent = rel['REL_POINTER_KEY']
            seeds.append(dict(src_id=r['RECORD_ID'], list=key.upper(), name=r['NAME_ORG'], names=names,
                              countries=set(), parent=parent, programs=[], entry_type='entity', ids=[],
                              url=r.get('SOURCE_URL')))
    return seeds


def csl_seeds(csl_path):
    data = json.loads(Path(csl_path).read_text(encoding='utf8'))
    seeds = []
    for r in data['results']:
        src = r.get('source', '')
        if src.startswith('Specially Designated Nationals'):
            lst = 'SDN'
        elif src.startswith('Entity List'):
            lst = 'EL'
        elif src.startswith('Military End User'):
            lst = 'MEU'
        else:
            continue
        typ = (r.get('type') or '').lower()
        if lst == 'SDN' and typ in ('vessel', 'aircraft'):
            continue
        entry_type = 'individual' if typ == 'individual' else 'entity'
        if lst == 'EL' and re.match(r'^\s*(address|addresses)\b', r.get('name') or '', re.I):
            entry_type = 'address'
        names = [r['name']] + [a for a in (r.get('alt_names') or []) if a]
        countries = {a.get('country') for a in (r.get('addresses') or []) if a.get('country')}
        ids = [(i.get('type'), i.get('number'), i.get('country')) for i in (r.get('ids') or []) if i.get('number')]
        seeds.append(dict(src_id=f"csl:{lst.lower()}:{r.get('id') or r.get('entity_number')}", list=lst, name=r['name'],
                          names=names, countries=countries, parent=None, programs=list(r.get('programs') or []),
                          entry_type=entry_type, ids=ids, url=r.get('source_list_url')))
    return seeds


# ---------------------------------------------------------------- GLEIF

def gleif_relationships(rr_zip):
    """parent LEI -> [(child LEI, kind, pct or None, last update)] for consolidation and branch links."""
    direct, ultimate, branches = [], [], []
    has_direct_parent = set()
    with open_csv(rr_zip) as fh:
        for d in csv.DictReader(fh):
            if d['Relationship.RelationshipStatus'] != 'ACTIVE':
                continue
            kind = d['Relationship.RelationshipType']
            child, parent = d['Relationship.StartNode.NodeID'], d['Relationship.EndNode.NodeID']
            if d['Relationship.StartNode.NodeIDType'] != 'LEI' or d['Relationship.EndNode.NodeIDType'] != 'LEI':
                continue
            pct = None
            for i in range(1, 6):
                if (d[f'Relationship.Quantifiers.{i}.MeasurementMethod'] == 'ACCOUNTING_CONSOLIDATION'
                        and d[f'Relationship.Quantifiers.{i}.QuantifierUnits'] == 'PERCENTAGE'):
                    try:
                        v = float(d[f'Relationship.Quantifiers.{i}.QuantifierAmount'])
                        if 0 <= v <= 100:
                            pct = v
                    except ValueError:
                        pass
            updated = (d['Registration.LastUpdateDate'] or '')[:10] or None
            row = (parent, child, kind, pct, updated, d['Registration.ValidationSources'])
            if kind == 'IS_DIRECTLY_CONSOLIDATED_BY':
                direct.append(row)
                has_direct_parent.add(child)
            elif kind == 'IS_ULTIMATELY_CONSOLIDATED_BY':
                ultimate.append(row)
            elif kind == 'IS_INTERNATIONAL_BRANCH_OF':
                branches.append(row)
    return direct, [r for r in ultimate if r[1] not in has_direct_parent], branches


NAME_COLS = ['Entity.LegalName'] + [f'Entity.OtherEntityNames.OtherEntityName.{i}' for i in range(1, 6)] + \
            [f'Entity.TransliteratedOtherEntityNames.TransliteratedOtherEntityName.{i}' for i in range(1, 6)]


def gleif_entities(lei2_zip, want_leis, name_keys, reg_keys):
    """Stream the Level 1 file once. Returns (info for want_leis and any LEI matching a seed key, name index,
    registration index). Only rows that can matter are kept."""
    info, by_name, by_reg = {}, defaultdict(set), defaultdict(set)
    n = 0
    with open_csv(lei2_zip) as fh:
        rd = csv.reader(fh)
        h = next(rd)
        ix = {c: i for i, c in enumerate(h)}
        i_lei, i_country, i_status = ix['LEI'], ix['Entity.LegalAddress.Country'], ix['Entity.EntityStatus']
        i_reg, i_regstat = ix['Entity.RegistrationAuthority.RegistrationAuthorityEntityID'], ix['Registration.RegistrationStatus']
        i_hq = ix['Entity.HeadquartersAddress.Country']
        i_names = [ix[c] for c in NAME_COLS if c in ix]
        for row in rd:
            n += 1
            lei = row[i_lei]
            names = [row[i] for i in i_names if row[i]]
            keys = {name_key(x) for x in names}
            rk = reg_key(row[i_reg])
            hit_name = bool(keys & name_keys)
            hit_reg = bool(rk) and rk in reg_keys
            if lei in want_leis or hit_name or hit_reg:
                info[lei] = dict(lei=lei, name=row[ix['Entity.LegalName']], names=names, country=row[i_country],
                                 hq_country=row[i_hq], status=row[i_status], reg_id=row[i_reg],
                                 registration_status=row[i_regstat])
                if hit_name:
                    for k in keys & name_keys:
                        by_name[k].add(lei)
                if hit_reg:
                    by_reg[rk].add(lei)
    log('GLEIF level 1 rows read:', n)
    return info, by_name, by_reg


# ---------------------------------------------------------------- matching

def seed_countries(s):
    """Countries a seed can be in: its addresses, else (for this feed's own lists, which carry none) China, Hong
    Kong and Macau, else unknown (empty: compatible with any country)."""
    return s['countries'] or (OWN_COUNTRIES if s['list'].startswith('US_') else set())


def same_party_groups(seeds):
    """Group list entries that name the same party: entries on different lists that share an exact normalized name
    (3 characters or more) in a compatible country. Huawei is on the Entity List, the 1260H list, Section 889 and
    the FCC Covered List; those entries are one party, not four rival claims to the name. Two entries on the same
    list that share a name stay apart (a list names distinct parties). Returns {src_id: group id}."""
    ents = [s for s in seeds if s['entry_type'] == 'entity']
    parent = {s['src_id']: s['src_id'] for s in ents}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_key = defaultdict(list)
    for s in ents:
        for k in {name_key(n) for n in s['names']}:
            if len(k) >= 3:
                by_key[k].append(s)
    for group in by_key.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a['list'] == b['list'] or a['src_id'] == b['src_id']:
                    continue
                ca, cb = seed_countries(a), seed_countries(b)
                if ca and cb and not (ca & cb):
                    continue
                ra, rb = find(a['src_id']), find(b['src_id'])
                if ra != rb:
                    parent[rb] = ra
    groups = {sid: find(sid) for sid in parent}
    # A union through several names must not put two entries of one list together; if it did, undo for that group.
    members = defaultdict(list)
    for s in ents:
        members[groups[s['src_id']]].append(s)
    for root, ms in members.items():
        lists = Counter(s['list'] for s in ms)
        if any(n > 1 for n in lists.values()):
            for s in ms:
                groups[s['src_id']] = s['src_id']
    return groups


def match_seeds(seeds, info, by_name, by_reg):
    """Attach an LEI to each seed where the evidence is unambiguous. Returns {src_id: (lei, method)}.

    A name is ambiguous only when it is shared by different parties. Entries on several lists that name the same
    party (same_party_groups) all keep their match, so an entity on two lists keeps both list hits; and an entry
    matched by its own evidence passes its LEI to the other entries of its party that could not be matched alone
    (method same_party_on_other_list)."""
    out = {}
    groups = same_party_groups(seeds)
    key_parties = defaultdict(set)  # name key -> parties naming it, to reject keys shared by different parties
    for s in seeds:
        for nm in s['names']:
            key_parties[name_key(nm)].add(groups.get(s['src_id'], s['src_id']))
    for s in seeds:
        if s['entry_type'] not in ('entity',):
            continue
        # 1. an LEI on the SDN entry itself
        for typ, num, _ in s['ids']:
            if typ == 'Legal Entity Number' and num.strip().upper() in info:
                out[s['src_id']] = (num.strip().upper(), 'lei_on_list_entry')
                break
        if s['src_id'] in out:
            continue
        # 2. registration number + country
        cands = set()
        for typ, num, idc in s['ids']:
            if typ in REG_ID_TYPES:
                for lei in by_reg.get(reg_key(num), ()):
                    c = info[lei]['country']
                    if (idc and c == idc) or (not idc and s['countries'] and c in s['countries']):
                        cands.add(lei)
        if len(cands) == 1:
            out[s['src_id']] = (cands.pop(), 'registration_number_and_country')
            continue
        # 3. exact normalized name + country, unique on both sides
        countries = seed_countries(s)
        cands = set()
        for nm in s['names']:
            k = name_key(nm)
            if len(k) < 6 or len(key_parties[k]) > 1:
                continue
            for lei in by_name.get(k, ()):
                c = info[lei]['country']
                if countries and c not in countries and info[lei]['hq_country'] not in countries:
                    continue
                if info[lei]['registration_status'] in ('RETIRED', 'ANNULLED', 'DUPLICATE', 'MERGED'):
                    continue
                cands.add(lei)
        if len(cands) == 1:
            out[s['src_id']] = (cands.pop(), 'exact_name_and_country')
    # 4. the LEI of another entry naming the same party, when that party's entries agree on one LEI
    party_leis = defaultdict(set)
    for sid, (lei, _) in out.items():
        if sid in groups:
            party_leis[groups[sid]].add(lei)
    for s in seeds:
        g = groups.get(s['src_id'])
        if s['src_id'] not in out and g is not None and len(party_leis.get(g, ())) == 1:
            out[s['src_id']] = (next(iter(party_leis[g])), 'same_party_on_other_list')
    return out


# ---------------------------------------------------------------- build

def entity_id(seed, matches):
    m = matches.get(seed['src_id'])
    return f"lei:{m[0]}" if m else seed['src_id']


def build(lists_dir, csl_path, rr_zip, lei2_zip):
    seeds = own_seeds(lists_dir) + csl_seeds(csl_path)
    log('seeds:', Counter(s['list'] for s in seeds))
    direct, ultimate, branches = gleif_relationships(rr_zip)
    log('GLEIF relationships: direct', len(direct), 'ultimate-only', len(ultimate), 'branches', len(branches))
    children = defaultdict(list)
    for row in direct + ultimate + branches:
        children[row[0]].append(row)
    name_keys = {name_key(n) for s in seeds if s['entry_type'] == 'entity' for n in s['names']} - {''}
    reg_keys = {reg_key(num) for s in seeds for typ, num, _ in s['ids'] if typ in REG_ID_TYPES} - {''}
    want = {num.strip().upper() for s in seeds for typ, num, _ in s['ids'] if typ == 'Legal Entity Number'}
    rr_nodes = {r[0] for r in direct + ultimate + branches} | {r[1] for r in direct + ultimate + branches}
    info, by_name, by_reg = gleif_entities(lei2_zip, want | rr_nodes, name_keys, reg_keys)
    matches = match_seeds(seeds, info, by_name, by_reg)
    log('seed matches:', Counter(m for _, m in matches.values()))

    # Edges: GLEIF links reachable from a matched seed, plus the 1260H parent/subsidiary links.
    edges, seen = [], set()
    frontier = [lei for lei, _ in matches.values()]
    reach = set(frontier)
    while frontier:
        nxt = []
        for p in frontier:
            for parent, child, kind, pct, updated, validation in children.get(p, ()):
                key = (parent, child, kind)
                if key in seen:
                    continue
                seen.add(key)
                e = dict(owner=f'lei:{parent}', owned=f'lei:{child}', as_of=updated,
                         source=f'GLEIF {kind}', validation=validation)
                if kind == 'IS_INTERNATIONAL_BRANCH_OF':
                    e.update(pct=100, control=True, basis='branch')
                elif kind == 'IS_DIRECTLY_CONSOLIDATED_BY':
                    e.update(pct=pct, control=True,
                             basis='accounting_consolidation' if pct is not None else 'accounting_consolidation_no_pct')
                else:
                    e.update(pct=None, control=True, basis='ultimate_accounting_consolidation')
                edges.append(e)
                if child not in reach:
                    reach.add(child)
                    nxt.append(child)
        frontier = nxt
    by_src = {s['src_id']: s for s in seeds}
    for s in seeds:
        if s['parent'] and s['parent'] in by_src:
            edges.append(dict(owner=entity_id(by_src[s['parent']], matches), owned=entity_id(s, matches), pct=None,
                              control=False, source='DoD Section 1260H notice', basis='notice_subsidiary',
                              url=s['url']))
    log('edges:', len(edges), 'entities reachable:', len(reach))

    listed = []
    for s in seeds:
        listed.append(dict(id=entity_id(s, matches), lists=[s['list']], programs=s['programs'],
                           entry_type=s['entry_type'], name=s['name']))
    entities = {f"lei:{l}": {'country': i['country']} for l, i in info.items() if i.get('country')}
    results = {}
    for rid in RULES:
        rule = load_rule(rid)
        rl = [p for p in listed if rid != 'any-list-50' or p['lists'][0].startswith('US_')]
        ev = evaluate(edges, rl, rule, entities=entities)
        results[rid] = ev
        log(rid, Counter(r.verdict for r in ev.flagged()))
    return seeds, matches, info, edges, results


def latin_share(s):
    letters = [c for c in s if c.isalpha()]
    return sum(1 for c in letters if c < 'ɐ') / len(letters) if letters else 0.0


def primary_name(names):
    """The first mostly-Latin name (GLEIF often has the legal name in Cyrillic, Chinese or Arabic and a
    transliterated or English name among the other names), else the legal name."""
    for n in names:
        if latin_share(n) >= 0.9:
            return n
    return names[0]


def share_basis(proof):
    """The bases of every link with a percentage that the proof uses (what the shares behind the verdict measure)."""
    return sorted({v['basis'] for st in proof for v in st.get('via', ()) if v.get('pct') is not None and v.get('basis')})


def verdict_class(rec):
    """covered: definitely or possibly under some rule; review: control_only under every rule it appears in."""
    return 'covered' if any(v in COVERED_VERDICTS for v in rec['OWNERSHIP_VERDICTS'].values()) else 'review'


def senzing_records(seeds, matches, info, results, inputs_sha):
    listed_ids = {entity_id(s, matches) for s in seeds}
    names_by_id, lists_by_id = {}, defaultdict(set)
    for s in seeds:
        names_by_id.setdefault(entity_id(s, matches), s['name'])
        lists_by_id[entity_id(s, matches)].add(s['list'])
    # flagged() leaves out each rule's own seeds (verdict listed), so a party named on one list still gets a record
    # when another rule reaches it, e.g. an Entity List company owned 50 percent or more by an SDN.
    ids = sorted({r.entity for ev in results.values() for r in ev.flagged()})
    recs = []
    for eid in ids:
        lei = eid[4:] if eid.startswith('lei:') else None
        gi = info.get(lei, {}) if lei else {}
        all_names = list(dict.fromkeys(gi.get('names') or [names_by_id.get(eid) or eid]))
        name = primary_name(all_names)
        names = [{'NAME_TYPE': 'PRIMARY', 'NAME_ORG': name}] + [
            {'NAME_TYPE': 'ALIAS', 'NAME_ORG': n} for n in all_names if n != name]
        verdicts, proofs, rels = {}, {}, []
        for rid, ev in results.items():
            r = ev[eid]
            if r.verdict == 'not':
                continue
            if r.verdict == 'listed':
                continue
            verdicts[rid] = r.verdict
            proofs[rid] = dict(verdict=r.verdict, share=[round(x, 4) for x in r.share], reasons=list(r.reasons),
                               share_basis=share_basis(r.proof), seeds=list(r.seeds), proof=r.proof)
            for v in (r.proof[-1].get('via', []) if r.proof else []):
                rels.append(v['owner'])
        rec = {
            'DATA_SOURCE': DATA_SOURCE,
            'RECORD_ID': eid,
            'RECORD_TYPE': 'ORGANIZATION',
            'NAME_ORG': name,
            'NAMES': names,
        }
        if lei:
            rec['LEI_NUMBER'] = lei
        if gi.get('country'):
            rec['REGISTRATION_COUNTRY'] = gi['country']
        if gi.get('status'):
            rec['GLEIF_ENTITY_STATUS'] = gi['status']
        if eid in lists_by_id:
            rec['LISTED_ON'] = sorted(lists_by_id[eid])
        rec.update({
            'LIST_NAME': 'Affiliates of US restricted parties (derived)',
            'EVIDENCE_STATE': EVIDENCE_STATE,
            'OWNERSHIP_VERDICTS': verdicts,
            'OWNERSHIP_PROOF': proofs,
            'INPUTS_SHA256': inputs_sha,
            'RELATIONSHIPS': [{'REL_ANCHOR_DOMAIN': DATA_SOURCE, 'REL_ANCHOR_KEY': eid}] + [
                {'REL_POINTER_DOMAIN': 'LISTED_PARTY' if o in listed_ids else DATA_SOURCE, 'REL_POINTER_KEY': o,
                 'REL_POINTER_ROLE': 'OWNED_OR_CONTROLLED_BY'} for o in sorted(set(rels))],
        })
        rec['VERDICT_CLASS'] = verdict_class(rec)
        recs.append(rec)
    return recs


def content_sha256(manifest, records_sha, data_date):
    """What decides whether the workflow publishes a release: the lists, the affiliates records (without the
    per-record input hash) and the day of the GLEIF data. With the day in it, the latest release's data_date is at
    most a day behind the last successful build, so a consumer can tell current data from stale."""
    lists_sha = ''.join(manifest['lists'][k]['sha256'] for k in sorted(manifest['lists']))
    return hashlib.sha256((lists_sha + records_sha + (data_date or '')).encode()).hexdigest()


def records_digest(recs):
    """Hash of the records without INPUTS_SHA256, which changes whenever GLEIF republishes its files even when no
    record changed. This is what decides whether the build publishes a new release."""
    body = ''.join(json.dumps({k: v for k, v in r.items() if k != 'INPUTS_SHA256'}, ensure_ascii=False, sort_keys=True)
                   + '\n' for r in recs)
    return hashlib.sha256(body.encode('utf8')).hexdigest()


# FollowTheMoney topics per rule: OFAC ownership or control, export-control link, trade risk (our own lists).
FTM_TOPICS = {'ofac-50': 'sanction.control', 'bis-affiliates': 'export.control.linked', 'any-list-50': 'export.risk'}


def ftm_id(eid):
    return 'usrp-aff-' + re.sub(r'[^a-zA-Z0-9.-]', '-', eid)


def ftm_entities(recs, seeds, matches, info, edges):
    """FollowTheMoney: a Company per derived record, a Company per owner it cites, and an Ownership per edge."""
    out, done = [], set()
    ids = {r['RECORD_ID'] for r in recs}
    seed_names = {}
    for s in seeds:
        seed_names.setdefault(entity_id(s, matches), (s['name'], s['list']))
    for r in recs:
        props = {'name': [r['NAME_ORG']], 'alias': [n['NAME_ORG'] for n in r['NAMES'][1:]],
                 'topics': sorted({FTM_TOPICS[k] for k in r['OWNERSHIP_VERDICTS']}), 'notes': [DISCLAIMER],
                 'summary': ['; '.join(f'{k}: {v}' for k, v in r['OWNERSHIP_VERDICTS'].items())]}
        if r.get('LEI_NUMBER'):
            props['leiCode'] = [r['LEI_NUMBER']]
        if r.get('REGISTRATION_COUNTRY'):
            props['jurisdiction'] = [r['REGISTRATION_COUNTRY'].lower()]
        out.append({'id': ftm_id(r['RECORD_ID']), 'schema': 'Company', 'properties': props})
        done.add(r['RECORD_ID'])
    for e in edges:
        if e['owned'] not in ids:
            continue
        if e['owner'] not in done:
            lei = e['owner'][4:] if e['owner'].startswith('lei:') else None
            nm = (info.get(lei) or {}).get('names') or [seed_names.get(e['owner'], (e['owner'],))[0]]
            props = {'name': [primary_name(nm)]}
            if lei:
                props['leiCode'] = [lei]
            out.append({'id': ftm_id(e['owner']), 'schema': 'Company', 'properties': props})
            done.add(e['owner'])
        props = {'owner': [ftm_id(e['owner'])], 'asset': [ftm_id(e['owned'])],
                 'role': [BASIS_NOTES.get(e.get('basis'), e.get('basis') or '')], 'publisher': [e.get('source', '')]}
        if e.get('pct') is not None:
            props['percentage'] = [str(e['pct'])]
        if e.get('as_of'):
            props['date'] = [e['as_of']]
        out.append({'id': 'usrp-own-' + hashlib.sha1(f"{e['owner']}>{e['owned']}>{e.get('source')}".encode()).hexdigest()[:16],
                    'schema': 'Ownership', 'properties': props})
    return out


def change_events(prev_recs, recs):
    before = {r['RECORD_ID']: r for r in prev_recs}
    now = {r['RECORD_ID']: r for r in recs}
    ev = []
    for rid in sorted(set(before) | set(now)):
        a, b = before.get(rid), now.get(rid)
        if a is None:
            ev.append(dict(event='added', record_id=rid, name=b['NAME_ORG'], verdicts=b['OWNERSHIP_VERDICTS']))
        elif b is None:
            ev.append(dict(event='removed', record_id=rid, name=a['NAME_ORG'], verdicts_before=a['OWNERSHIP_VERDICTS']))
        elif a['OWNERSHIP_VERDICTS'] != b['OWNERSHIP_VERDICTS']:
            ev.append(dict(event='verdict_changed', record_id=rid, name=b['NAME_ORG'],
                           verdicts_before=a['OWNERSHIP_VERDICTS'], verdicts=b['OWNERSHIP_VERDICTS']))
    return ev


class StaleData(RuntimeError):
    pass


def cached_gleif(cache, kind):
    """The GLEIF file of this kind already in the cache (zip from the server or the API fallback, gz from AWS)."""
    for ext in ('zip', 'gz'):
        p = Path(cache) / f'gleif-{kind}.csv.{ext}'
        if p.exists():
            return p
    raise FileNotFoundError(f'no gleif-{kind}.csv.zip or .gz in {cache}')


def read_meta(cache, key):
    p = Path(cache) / f'{key}.meta.json'
    return json.loads(p.read_text(encoding='utf8')) if p.exists() else {}


def write_meta(cache, key, meta):
    (Path(cache) / f'{key}.meta.json').write_text(json.dumps(meta, indent=1) + '\n', encoding='utf8')


def check_fresh(gleif_meta, now, max_age_hours, required):
    """The GLEIF data date: the older publish of the two files. Raises StaleData when it is unknown (and required)
    or older than max_age_hours, so a stale feed is never published as if it were current."""
    dates = {k: m.get('publish_date') for k, m in gleif_meta.items()}
    if any(d is None for d in dates.values()):
        if required:
            raise StaleData(f'GLEIF publish date unknown: {dates}')
        return None
    oldest = min(dates.values())
    age = (now - datetime.strptime(oldest, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)).total_seconds() / 3600
    if age > max_age_hours:
        raise StaleData(f'GLEIF data published {oldest} is {age:.0f} hours old (limit {max_age_hours:g})')
    return oldest


def carry_forward(prev, out, error, now):
    """The affiliates build failed. The five lists still publish: this copies the previous release's affiliates files
    into --out unchanged, with affiliates.manifest.json and the manifest.json `affiliates` entry set to stale: true,
    the previous data_date (the real, older day of the GLEIF data) and the error. content_sha256 uses the previous
    records and data date, so a day on which only the affiliates failed makes no new release. Prints a GitHub warning
    annotation and returns 0. Without a previous feed, writes nothing and returns 2 (error annotation)."""
    pman = prev / 'affiliates.manifest.json' if prev else None
    if not (pman and pman.exists() and (prev / 'affiliates.senzing.jsonl').exists()):
        log('ERROR affiliates build failed and there is no previous feed to carry:', error)
        print(f'::error title=affiliates build failed::{error}. There is no previous affiliates feed to carry '
              f'forward, so this build has no affiliates files.', file=sys.stderr, flush=True)
        return 2
    am = json.loads(pman.read_text(encoding='utf8'))
    carried = ['affiliates.senzing.jsonl', *SPLIT_FILES.values(), 'affiliates.ftm.json']
    for name in carried:
        if (prev / name).exists():
            (out / name).write_bytes((prev / name).read_bytes())
    # The previous release's change events are not today's: this build has none.
    (out / 'affiliates.changes.jsonl').write_text('', encoding='utf8')
    am.update(stale=True, error=error, stale_since=am.get('stale_since') or now,
              carried_from_built_at=am.get('carried_from_built_at') or am.get('built_at'), built_at=now)
    (out / 'affiliates.manifest.json').write_text(json.dumps(am, indent=1, ensure_ascii=False) + '\n',
                                                  encoding='utf8', newline='\n')
    mpath = out / 'manifest.json'
    if mpath.exists():
        m = json.loads(mpath.read_text(encoding='utf8'))
        m['affiliates'] = {k: am.get(k) for k in MANIFEST_KEYS + ('error', 'stale_since')}
        m['content_sha256'] = content_sha256(m, am.get('records_sha256') or '', am.get('data_date'))
        mpath.write_text(json.dumps(m, indent=1, ensure_ascii=False) + '\n', encoding='utf8', newline='\n')
    with open(out / 'CHANGES.md', 'a', encoding='utf8', newline='\n') as f:
        f.write('\n'.join(['', '## Affiliates (derived, evidence not determination)', '',
                           f"- STALE: this build could not refresh the affiliates ({error}). The files are the previous "
                           f"feed's, unchanged, with GLEIF data of {am.get('data_date') or 'unknown date'}.",
                           '- changes: none']) + '\n')
    log('affiliates build failed, carried the previous feed forward as stale:', error)
    print(f"::warning title=affiliates stale::{error}. The lists were built and publish as usual; the affiliates "
          f"files are the previous feed's, marked stale: true, data_date {am.get('data_date')}.",
          file=sys.stderr, flush=True)
    return 0


# The affiliates.manifest.json fields copied into the `affiliates` entry of manifest.json.
MANIFEST_KEYS = ('file', 'records', 'sha256', 'records_sha256', 'files', 'inputs_sha256', 'evidence_state',
                 'data_date', 'data_as_of', 'stale', 'gleif_route', 'gleif_coverage')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--lists', default='dist', help='directory with this build of the five list files')
    ap.add_argument('--out', default='dist')
    ap.add_argument('--prev', default=None, help='directory with the previous release (for change events and the '
                                                 'entities the GLEIF API fallback looks up)')
    ap.add_argument('--cache', default='.cache', help='where downloads go (reused when present and --offline)')
    ap.add_argument('--offline', action='store_true', help='use the files already in --cache')
    ap.add_argument('--no-api-fallback', action='store_true', help='fail instead of using the per-record GLEIF API '
                                                                    'when the bulk files cannot be downloaded')
    ap.add_argument('--max-age-hours', type=float, default=MAX_AGE_HOURS,
                    help='fail when the GLEIF data is older than this (default %(default)s)')
    a = ap.parse_args(argv)
    out, cache = Path(a.out), Path(a.cache)
    prev = Path(a.prev) if a.prev else None
    out.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    csl_path = cache / 'consolidated.json'
    # Any failure below carries the previous release's affiliates forward, marked stale (carry_forward), so the five
    # lists still publish that day; without a previous feed the run writes no affiliates file and exits 2.
    try:
        if a.offline:
            gleif = {k: (cached_gleif(cache, k), read_meta(cache, f'gleif-{k}') or dict(route='cache'))
                     for k in GLEIF_KINDS}
            csl_meta = read_meta(cache, 'csl') or dict(url=CSL_URL)
        else:
            log('download', CSL_URL)
            fetch(CSL_URL, csl_path)
            csl_meta = dict(url=CSL_URL, downloaded_at=now)
            try:
                gleif = fetch_gleif(cache)
            except FetchError as e:
                if a.no_api_fallback:
                    raise
                log('GLEIF bulk files unavailable, falling back to the per-record API:', e)
                gleif = gleif_from_api(own_seeds(a.lists) + csl_seeds(csl_path), prev, cache)
            write_meta(cache, 'csl', csl_meta)
            for k, (_, m) in gleif.items():
                write_meta(cache, f'gleif-{k}', m)
        gleif_meta = {k: m for k, (_, m) in gleif.items()}
        gleif_as_of = check_fresh(gleif_meta, now_dt, a.max_age_hours, required=not a.offline)
        inputs = {'csl': dict(csl_meta, sha256=sha256_file(csl_path), bytes=csl_path.stat().st_size)}
        for k, (p, m) in gleif.items():
            inputs[k] = dict(m, sha256=sha256_file(p), bytes=p.stat().st_size)
        for key in OWN_LISTS:
            p = Path(a.lists) / f'{key}.jsonl'
            if p.exists():
                inputs[key] = dict(file=p.name, sha256=sha256_file(p))
        inputs_sha = hashlib.sha256(''.join(f"{k}:{v['sha256']}" for k, v in sorted(inputs.items())).encode()).hexdigest()
        seeds, matches, info, edges, results = build(a.lists, csl_path, gleif['rr'][0], gleif['lei2'][0])
        recs = senzing_records(seeds, matches, info, results, inputs_sha)
    except Exception as e:  # noqa: BLE001
        msg = f'{type(e).__name__}: {e}'
        return carry_forward(prev, out, msg, now)
    data_as_of = dict(gleif_rr=gleif_meta['rr'].get('publish_date'), gleif_lei2=gleif_meta['lei2'].get('publish_date'),
                      csl=csl_meta.get('downloaded_at'))
    data_date = gleif_as_of[:10] if gleif_as_of else None
    gleif_route = '+'.join(sorted({m.get('route', '?') for m in gleif_meta.values()}))
    coverage = 'partial' if any(m.get('coverage') == 'partial' for m in gleif_meta.values()) else 'full'

    body = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in recs)
    (out / 'affiliates.senzing.jsonl').write_text(body, encoding='utf8', newline='\n')
    files = {'affiliates.senzing.jsonl': dict(records=len(recs), sha256=hashlib.sha256(body.encode('utf8')).hexdigest())}
    for cls, fname in SPLIT_FILES.items():
        part = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in recs if r['VERDICT_CLASS'] == cls)
        (out / fname).write_text(part, encoding='utf8', newline='\n')
        files[fname] = dict(records=sum(1 for r in recs if r['VERDICT_CLASS'] == cls),
                            sha256=hashlib.sha256(part.encode('utf8')).hexdigest())
    ftm = ftm_entities(recs, seeds, matches, info, edges)
    (out / 'affiliates.ftm.json').write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in ftm),
                                             encoding='utf8', newline='\n')
    digest = files['affiliates.senzing.jsonl']['sha256']
    stable = records_digest(recs)
    verdicts = {rid: dict(Counter(r.verdict for r in ev.flagged())) for rid, ev in results.items()}
    amanifest = dict(
        feed=FEED_URL, file='affiliates.senzing.jsonl', data_source=DATA_SOURCE, built_at=now,
        data_date=data_date, data_as_of=data_as_of, stale=False, gleif_route=gleif_route, gleif_coverage=coverage,
        records=len(recs), sha256=digest, records_sha256=stable, files=files,
        verdict_classes=dict(Counter(r['VERDICT_CLASS'] for r in recs)),
        evidence_state=EVIDENCE_STATE, disclaimer=DISCLAIMER,
        engine=dict(package='ownership-rules', version=ENGINE_VERSION,
                    rules={rid: load_rule(rid).version for rid in RULES}),
        inputs=inputs, inputs_sha256=inputs_sha,
        seeds=dict(Counter(s['list'] for s in seeds)),
        seed_matches=dict(Counter(m for _, m in matches.values())),
        seeds_matched_by_list=dict(Counter(s['list'] for s in seeds if s['src_id'] in matches)),
        edges=dict(Counter(e['source'] for e in edges)),
        edges_with_percentage=sum(1 for e in edges if e.get('pct') is not None),
        verdicts=verdicts,
        licenses={'gleif': 'CC0 1.0 (GLEIF LEI data)', 'csl': 'US government work (trade.gov Consolidated Screening List)',
                  'this_file': 'CC BY 4.0'},
    )
    (out / 'affiliates.manifest.json').write_text(json.dumps(amanifest, indent=1, ensure_ascii=False) + '\n',
                                                  encoding='utf8', newline='\n')

    events = change_events(read_jsonl(prev / 'affiliates.senzing.jsonl') if prev and (prev / 'affiliates.senzing.jsonl').exists() else [], recs)
    (out / 'affiliates.changes.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events),
                                                  encoding='utf8', newline='\n')
    mpath = out / 'manifest.json'
    if mpath.exists():
        m = json.loads(mpath.read_text(encoding='utf8'))
        m['affiliates'] = {k: amanifest[k] for k in MANIFEST_KEYS}
        m['content_sha256'] = content_sha256(m, stable, data_date)
        mpath.write_text(json.dumps(m, indent=1, ensure_ascii=False) + '\n', encoding='utf8', newline='\n')
    cpath = out / 'CHANGES.md'
    lines = ['', '## Affiliates (derived, evidence not determination)', '',
             f'- {len(recs)} records from GLEIF data of {gleif_as_of or "unknown date"} ({gleif_route}'
             + (', partial coverage' if coverage == 'partial' else '') + ')',
             '- ' + ', '.join(f"{n} {SPLIT_FILES[c]}" for c, n in sorted(amanifest['verdict_classes'].items()))]
    for rid, c in verdicts.items():
        lines.append(f'- {rid}: ' + ', '.join(f'{v} {n}' for v, n in sorted(c.items())))
    counts = Counter(e['event'] for e in events)
    lines.append('- changes: ' + (', '.join(f'{k} {v}' for k, v in sorted(counts.items())) or 'none'))
    for e in events[:200]:
        lines.append(f"  - {e['event']}: {e['name']} ({e['record_id']})")
    if len(events) > 200:
        lines.append(f'  - and {len(events) - 200} more in affiliates.changes.jsonl')
    with open(cpath, 'a', encoding='utf8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    log('affiliates:', len(recs), 'records', verdicts)
    return 0


if __name__ == '__main__':
    sys.exit(main())
