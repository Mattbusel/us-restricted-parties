#!/usr/bin/env python3
"""Build Senzing-format JSON Lines feeds of five US restricted-party lists, straight from the official sources.

    python build.py --out dist [--prev prev]

Lists (one file each, plus all.jsonl):
  us_dod_1260h.jsonl    DoD Chinese military companies (Section 1260H), Federal Register notices
  us_far_889.jsonl      Section 889 covered telecom and video surveillance companies, FAR 52.204-25 on eCFR
  us_fcc_covered.jsonl  FCC Covered List (Secure Networks Act), named companies only
  us_dhs_uflpa.jsonl    DHS UFLPA Entity List (forced labor import ban)
  us_biosecure.jsonl    BIOSECURE biotechnology companies of concern, interim: the 1260H biotech listings

Every record is a Senzing ORGANIZATION with a flat NAME_ORG (readable by any Senzing consumer) and a NAMES array
with the PRIMARY name and every ALIAS the source gives. Subsidiaries a source names are their own records with a
RELATIONSHIPS pointer to the parent. manifest.json carries counts, hashes, sources and pull errors; CHANGES.md the
names added and removed since --prev.

If a source cannot be fetched and --prev holds that list from an earlier build, the earlier file is kept and the
error is recorded in the manifest, so a feed never goes empty because an agency site had a bad day.

Standard library only. MIT licensed code; the generated data is CC BY 4.0 (the underlying lists are US government
works in the public domain).
"""
import argparse, gzip, hashlib, html, json, re, subprocess, sys, time, unicodedata, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

FR_API = 'https://www.federalregister.gov/api/v1/documents.json'
FCC_URL = 'https://www.fcc.gov/supplychain/coveredlist'
UFLPA_URL = 'https://www.dhs.gov/uflpa-entity-list'
ECFR = 'https://www.ecfr.gov/api/versioner/v1'
FEED_URL = 'https://github.com/Mattbusel/us-restricted-parties'

LISTS = {
    'us_dod_1260h': dict(data_source='US_DOD_1260H', name='DoD Chinese Military Companies (Section 1260H)',
                         operator='Department of Defense, OUSD(A&S)', source='Federal Register, DoD notices under Section 1260H'),
    'us_far_889': dict(data_source='US_FAR_889', name='Section 889 covered telecommunications and video surveillance companies',
                       operator='FAR Council (FAR 52.204-25); OMB for grants (2 CFR 200.216)', source='eCFR, 48 CFR 52.204-25'),
    'us_fcc_covered': dict(data_source='US_FCC_COVERED', name='FCC Covered List (Secure and Trusted Communications Networks Act)',
                           operator='Federal Communications Commission, PSHSB', source=FCC_URL),
    'us_dhs_uflpa': dict(data_source='US_DHS_UFLPA', name='UFLPA Entity List', operator='DHS Forced Labor Enforcement Task Force',
                         source=UFLPA_URL),
    'us_biosecure': dict(data_source='US_BIOSECURE', name='BIOSECURE biotechnology companies of concern (interim)',
                         operator='OMB (list due Dec 18 2026); until then the Section 1260H biotechnology listings',
                         source='Derived from us_dod_1260h; Federal Register watched for the OMB list'),
}

# BIOSECURE (FY2026 NDAA sec. 851) names any 1260H entity involved in biotechnology a company of concern.
# These are the 1260H listings that are biotechnology companies; OMB publishes its own list later.
BIOTECH_1260H = re.compile(r'\bBGI\b|\bMGI\b|Complete Genomics|Novogene|WuXi AppTec|Innomics|STomics|Forensic Genomics|GBI Diagnostics')

MONTHS = {m: i for i, m in enumerate(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
                                      'october', 'november', 'december'], 1)}
LEGAL_TAIL = re.compile(r'^(?:Ltd\.?|Limited|Inc\.?|LLC|L\.L\.C\.|Corp\.?|Co\.?|PLC|S\.A\.|GmbH|Pty\.? Ltd\.?|Co\.,? Ltd\.?|Company Limited|L\.P\.)(?:\s*\(.*\))?\)?$', re.I)
NAME_WORDS = {'limited', 'group', 'holdings', 'shanshan', 'baotou', 'shenzhen', 'beijing', 'usa', 'americas', 'bvi', 'tianjin', 'dalian'}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
BROWSER = {'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
           'Accept-Language': 'en-US,en;q=0.9', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Site': 'none', 'Sec-Fetch-Dest': 'document',
           'Upgrade-Insecure-Requests': '1', 'Accept-Encoding': 'gzip'}


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, file=sys.stderr, flush=True)


def fetch(url, tries=4, timeout=90):
    err = None
    for attempt in range(tries):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=BROWSER), timeout=timeout)
            b = r.read()
            return gzip.decompress(b) if r.headers.get('Content-Encoding') == 'gzip' else b
        except urllib.error.HTTPError as e:
            err = e
            if e.code in (400, 404, 410, 422):
                raise
            if e.code == 403:
                break
        except Exception as e:  # network
            err = e
        time.sleep(3 * (attempt + 1))
    # some agency CDNs reject Python's TLS fingerprint but let curl through
    args = ['curl', '-sL', '--compressed', '--max-time', str(timeout), '-w', '\n%{http_code}', url]
    for k, v in BROWSER.items():
        if k != 'Accept-Encoding':
            args[1:1] = ['-H', f'{k}: {v}']
    try:
        r = subprocess.run(args, capture_output=True)
        out, _, code = r.stdout.rpartition(b'\n')
        if r.returncode == 0 and code.strip() == b'200':
            return out
    except FileNotFoundError:
        pass
    raise err or RuntimeError(f'fetch failed: {url}')


def fetch_json(url):
    return json.loads(fetch(url).decode('utf8'))


def decode(b):
    try:
        return b.decode('utf8')
    except UnicodeDecodeError:
        return b.decode('cp1252', 'replace')


def clean(s):
    s = unicodedata.normalize('NFKC', html.unescape(s)).replace('�', '').replace('’', "'").replace('“', '"').replace('”', '"')
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'\s+([,.;)])', r'\1', s)
    return re.sub(r'\(\s+', '(', s)


def strip_tags(h):
    h = re.sub(r'<(script|style)[\s\S]*?</\1>', ' ', h)
    return clean(re.sub(r'<[^>]+>', ' ', h))


def us_date(s):
    m = re.search(r'([A-Z][a-z]+)\.?\s+(\d{1,2}),\s+(\d{4})', s or '')
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return f'{int(m.group(3)):04d}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}'


def slug(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:80]


def norm_key(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


# ---------------------------------------------------------------- name parsing

def split_top(s, seps):
    """Split s on any separator regex in seps, only outside parentheses."""
    out, depth, cur, i = [], 0, '', 0
    rx = re.compile('|'.join(seps))
    while i < len(s):
        c = s[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth = max(0, depth - 1)
        if depth == 0:
            m = rx.match(s, i)
            if m and m.end() > i:
                out.append(cur)
                cur, i = '', m.end()
                continue
        cur += c
        i += 1
    out.append(cur)
    return [x.strip() for x in out if x.strip()]


def split_names(s):
    """'A Co., Ltd., B Inc., and C LLC' -> ['A Co., Ltd.', 'B Inc.', 'C LLC'] (legal suffixes stay with their name)."""
    parts = split_top(s, [r',\s+and\s+', r';\s+and\s+', r';\s+', r',\s+', r'(?<=\.)\.\s+(?=[A-Z])'])
    out = []
    for p in parts:
        p = re.sub(r'^and\s+', '', p).strip()
        if out and (LEGAL_TAIL.match(p) or p[:1].islower() or p in ('Americas', 'America', 'USA')):  # 'SMIC, Americas'
            out[-1] += ', ' + p
        elif p:
            out.append(p)
    return out


def trailing_groups(s):
    """Peel parenthesized groups off the end: 'A (B) (C)' -> ('A', ['B', 'C'])."""
    groups = []
    while s.endswith(')'):
        depth, i = 0, len(s) - 1
        while i >= 0:
            if s[i] == ')':
                depth += 1
            elif s[i] == '(':
                depth -= 1
                if depth == 0:
                    break
            i -= 1
        if i <= 0:
            break
        inner = s[i + 1:-1].strip()
        if inner.lower() in NAME_WORDS:  # "(Group)", "(Limited)", "(Shanshan)" are part of the name
            break
        groups.insert(0, inner)
        s = s[:i].rstrip()
    return s, groups


ALIAS_LEAD = re.compile(r'^(?:(?:and|including)\s+(?:\w+\s+)?aliase?s?:|also known as|formerly known as|formerly|a\.?k\.?a\.?|f/k/a|d/b/a)\s*', re.I)


def alias_list(inner):
    """Text inside an alias parenthesis -> list of names."""
    pieces = re.split(r';?\s*\band\s+(?=(?:also|formerly) known as)|;\s*(?=(?:also|formerly) known as)', inner)
    names = []
    for piece in pieces:
        piece = ALIAS_LEAD.sub('', piece.strip()).strip()
        if piece:
            names += split_alias_names(piece)
    return names


def split_alias_names(s):
    parts = split_top(s, [r';\s+and\s+', r';\s+', r'(?:(?<=Ltd)|(?<=Inc)|(?<=Co)|(?<=LLC)|(?<=Corp)|(?<=Limited))\.?\s+and\s+(?=[A-Z])'])
    out = []
    for p in parts:
        p = re.sub(r'^and\s+', '', p).strip().rstrip(';').strip()
        if p:
            out.append(p if p.endswith(('.', ')')) or not re.search(r'\b(?:Ltd|Inc|Co|Corp)$', p) else p + '.')
    return out


def parse_entry(text):
    """One listed entity as printed by the agency -> dict(primary, aliases, subsidiaries, includes_affiliates).

    Handles the forms used by the 1260H notices and the UFLPA Entity List:
      'Aviation Industry Corporation of China, Ltd. (AVIC) (and AVIC subsidiaries: A, B Co., Ltd., and C)'
      'Hesai Group (also known as Hesai Technology Co., Ltd.)'
      'Hetian Haolin Hair Accessories Co. Ltd. (and two aliases: X; and Y)'
      'Hoshine Silicon Industry (Shanshan) Co., Ltd (including one alias: Z) and subsidiaries'
    """
    s = re.sub(r'\)\.$', ')', clean(text))
    affiliates = False
    m = re.search(r',?\s+and\s+(?:its\s+)?(?:subsidiaries|subordinate and affiliated entities)\s*$', s, re.I)
    if m:
        affiliates, s = True, s[:m.start()].rstrip()
    subs = []
    m = re.search(r'\s*\(and\s+[^():]*?subsidiar(?:y|ies):\s*', s)
    if m:
        body = s[m.end():]
        body = body[:-1] if body.endswith(')') else body
        subs = split_names(body)
        s = s[:m.start()].rstrip()
    primary, groups = trailing_groups(s)
    aliases = []
    for g in groups:
        aliases += alias_list(g) if ALIAS_LEAD.match(g) else [g]
    primary = primary.rstrip(',').strip()
    seen, out = {norm_key(primary)}, []
    for a in aliases:
        k = norm_key(a)
        if a and k and k not in seen:
            seen.add(k)
            out.append(a)
    return dict(primary=primary, aliases=out, subsidiaries=subs, includes_affiliates=affiliates)


# ---------------------------------------------------------------- sources

def fr_search(term, agency=None, doc_type=None, order='oldest'):
    q = {'conditions[term]': term, 'per_page': 100, 'order': order,
         'fields[]': ['document_number', 'title', 'publication_date', 'raw_text_url', 'html_url', 'agencies', 'type']}
    if agency:
        q['conditions[agencies][]'] = agency
    if doc_type:
        q['conditions[type][]'] = doc_type
    return fetch_json(FR_API + '?' + urllib.parse.urlencode(q, doseq=True)).get('results', [])


def fr_paragraphs(raw):
    t = decode(raw)
    m = re.search(r'<pre>([\s\S]*?)</pre>', t)
    t = html.unescape(re.sub(r'<(?!bullet>)[^>]+>', '', m.group(1) if m else t))
    paras = [p for p in re.split(r'\n\s*\n', t) if p.strip()]
    # Some notices start the next entity right after a bullet with no blank line,
    # e.g. "...(Section 1260H(g)(3)(A)).\nChina COSCO SHIPPING Corporation Limited (...".
    # Split there; a chunk that is really prose is still dropped by is_prose().
    out = []
    for p in paras:
        out += [q for q in re.split(r'(?<=\)\.)\n(?=[A-Z])(?!Section\b)|\n(?=[ \t]*<bullet>)', p) if q.strip()]
    return out


def is_prose(flat):
    return (flat.startswith(('<bullet>', 'The ', 'Section', 'Sections', '1260H(', 'Any ', 'Dated', 'This ')) or flat[:1].islower()
            or len(flat) > 1500 or re.search(r'\b(?:is|are|because|was|were)\b', re.sub(r'\([^)]*\)', '', flat)) is not None)


def parse_1260h_list(paras):
    """Entities designated in a full 'Notice of Availability' and the ones it removes."""
    start = next(i for i, p in enumerate(paras) if 'following' in p and 'entit' in p and ('qualify' in p or 'satisfy' in p))
    ents, removed, mode, prev = [], [], 'add', None
    for p in paras[start + 1:]:
        page_break = bool(re.match(r'\s*\[\[Page \d+\]\]', p))
        flat = clean(re.sub(r'\[\[Page \d+\]\]', ' ', p))
        if not flat:
            continue
        if 'should be removed' in flat or 'following previously listed' in flat:
            mode, prev = 'rm', None
            continue
        if flat.startswith(('The list of entities', 'Reconsideration', 'Dated:')):
            break
        if mode == 'rm':
            removed += [clean(x) for x in p.splitlines() if clean(x) and not clean(x).startswith('[[')]
            continue
        open_parens = prev == 'entity' and ents and ents[-1].count('(') > ents[-1].count(')')
        if open_parens:  # an entry the page break cut in two
            ents[-1] += ' ' + flat
            continue
        if page_break and prev == 'prose' and (flat[:1].islower() or flat.startswith(('1260H(', 'Section'))):
            continue
        if is_prose(flat):
            prev = 'prose'
            continue
        ents.append(flat)
        prev = 'entity'
    return ents, removed


def parse_single_notice(paras, verb):
    """Names in a one-off designation or removal notice: the lines after '... the following entit(y|ies) ...'."""
    out, on = [], False
    for p in paras:
        flat = clean(p)
        if 'following' in flat and 'entit' in flat and verb in flat.lower():
            on = True
            continue
        if not on:
            continue
        if flat.startswith(('The entit', 'Dated:', 'Any correspondence')):
            break
        if flat.startswith('<bullet>') or p.startswith('    '):
            continue
        out += [clean(x) for x in p.splitlines() if clean(x)]
    return out


def pull_1260h():
    docs = fr_search('"Chinese military companies" 1260H', agency='defense-department', doc_type='NOTICE')
    full = [d for d in docs if 'Availability of Designation' in d['title']]
    if not full:
        raise RuntimeError('1260H: no Notice of Availability found in the Federal Register')
    cur = full[-1]
    ents, removed_in_full = parse_1260h_list(fr_paragraphs(fetch(cur['raw_text_url'])))
    rows = [dict(text=e, effective=cur['publication_date'], doc=cur['document_number'], url=cur['html_url']) for e in ents]
    later = []
    for d in docs:
        if d['publication_date'] < cur['publication_date'] or d['document_number'] == cur['document_number']:
            continue
        paras = fr_paragraphs(fetch(d['raw_text_url']))
        if 'Removal' in d['title']:
            for name in parse_single_notice(paras, 'delist') + parse_single_notice(paras, 'remov'):
                later.append(dict(action='removed', name=name, date=d['publication_date'], doc=d['document_number'], url=d['html_url']))
        if 'Designation' in d['title'] and 'Availability' not in d['title']:
            for name in parse_single_notice(paras, 'design') + parse_single_notice(paras, 'qualif'):
                later.append(dict(action='designated', name=name, date=d['publication_date'], doc=d['document_number'], url=d['html_url']))
                rows.append(dict(text=name, effective=d['publication_date'], doc=d['document_number'], url=d['html_url']))
    gone = {norm_key(parse_entry(x['name'])['primary']) for x in later if x['action'] == 'removed'}
    kept = [r for r in rows if norm_key(parse_entry(r['text'])['primary']) not in gone]
    log('1260H:', cur['document_number'], len(ents), 'entries;', len(rows) - len(kept), 'removed by later notices')
    notes = dict(base_notice=cur['document_number'], base_date=cur['publication_date'], removed_in_base=removed_in_full, later_notices=later)
    return kept, notes


def pull_889():
    t48 = next(t for t in fetch_json(f'{ECFR}/titles.json')['titles'] if t['number'] == 48)
    asof = t48['up_to_date_as_of']
    text = strip_tags(decode(fetch(f'{ECFR}/full/{asof}/title-48.xml?part=52&section=52.204-25')))
    names = ['Huawei Technologies Company', 'ZTE Corporation', 'Hytera Communications Corporation',
             'Hangzhou Hikvision Digital Technology Company', 'Dahua Technology Company']
    missing = [n for n in names if n not in text]
    if missing:
        raise RuntimeError(f'SEC889: names not found in FAR 52.204-25 text: {missing}')
    url = 'https://www.ecfr.gov/current/title-48/section-52.204-25'
    rows = [dict(text=n, effective='2019-08-13', doc=f'48 CFR 52.204-25 (eCFR as of {asof})', url=url, affiliates=True) for n in names]
    log('SEC889:', len(rows), 'entities, eCFR as of', asof)
    return rows, dict(ecfr_as_of=asof)


FCC_NAME = re.compile(r'(?:produced|provided|supplied)(?:,\s*directly or indirectly,)?\s+by\s+(.+?)'
                      r'(?:\s*,(?!\s*(?:Inc|Ltd|LLC|Corp)\b)|\s+subject to|\s+or any of its|\s+to the extent|\s+including|\s*\.?$)')


def pull_fcc():
    page = decode(fetch(FCC_URL))
    table = re.findall(r'<table[\s\S]*?</table>', page)[0]  # later tables are Conditional Approvals
    rows, categories = [], []
    for tr in re.findall(r'<tr[\s\S]*?</tr>', table):
        tds = re.findall(r'<td[\s\S]*?</td>', tr)
        if len(tds) < 2:
            continue
        desc, dates = strip_tags(tds[0]), strip_tags(tds[1])
        d = us_date(dates)
        if not d:
            continue
        m = FCC_NAME.search(desc)
        if not m:
            categories.append(dict(entry=desc[:400], date=d))
            continue
        name = m.group(1).strip().rstrip('.').strip()
        for n in re.split(r'\s+and its wholly-owned subsidiary\s+', name):
            rows.append(dict(text=n.strip(), effective=d, doc=desc[:300], url=FCC_URL))
    if not rows:
        raise RuntimeError('FCC Covered List: no named entities parsed (page blocked or layout changed)')
    log('FCC:', len(rows), 'named entities,', len(categories), 'category entries')
    return rows, dict(category_entries=categories)


def pull_uflpa():
    """DHS page first (it has a date per entity); the full list in the latest Federal Register notice if DHS blocks us."""
    try:
        return pull_uflpa_dhs()
    except Exception as e:
        log('UFLPA: dhs.gov failed (', e, '), using the Federal Register appendix')
        rows, notes = pull_uflpa_fr()
        return rows, dict(notes, dhs_error=f'{type(e).__name__}: {e}')


def parse_uflpa_appendix(raw):
    """Entity lines of the 'UFLPA Entity List <date>' appendix in a DHS Federal Register notice.

    GPO text wraps long lines with a trailing space, so a line that does not end in a space ends an entry."""
    t = decode(raw)
    m = re.search(r'<pre>([\s\S]*?)</pre>', t)
    t = html.unescape(re.sub(r'<[^>]+>', '', m.group(1) if m else t))
    head = re.search(r'^UFLPA Entity List ([A-Z][a-z]+ \d{1,2}, \d{4})\s*$', t, re.M)
    if not head:
        raise RuntimeError('UFLPA: no entity list appendix in the notice')
    as_of, body = us_date(head.group(1)), t[head.end():]
    body = body.split('[FR Doc.')[0]
    ents = []
    for para in re.split(r'\n[ \t]*\n', body):
        para = re.sub(r'\[\[Page \d+\]\]', '', para)
        if not para.strip() or para.lstrip().startswith('UFLPA Section') or para.startswith('    '):
            continue
        cur = ''
        for line in para.split('\n'):
            if not line.strip():
                continue
            cur += line
            if not line.endswith(' '):
                ents.append(clean(cur))
                cur = ''
        if cur.strip():
            ents.append(clean(cur))
    merged = []
    for e in ents:  # a page break inside an entry leaves its parenthesis open
        if merged and merged[-1].count('(') > merged[-1].count(')'):
            merged[-1] += ' ' + e
        else:
            merged.append(e)
    return as_of, merged


def pull_uflpa_fr():
    docs = [d for d in fr_search('"UFLPA Entity List"', agency='homeland-security-department', doc_type='NOTICE', order='newest')
            if 'Uyghur Forced Labor Prevention Act Entity List' in d['title']]
    if not docs:
        raise RuntimeError('UFLPA: no Federal Register notice found')
    d = docs[0]
    as_of, ents = parse_uflpa_appendix(fetch(d['raw_text_url']))
    rows = [dict(text=e, effective=as_of, doc=d['document_number'], url=d['html_url'], date_from_notice=True) for e in ents]
    if len(rows) < 50:
        raise RuntimeError(f'UFLPA: only {len(rows)} entries in FR {d["document_number"]}')
    log('UFLPA:', len(rows), 'entries from FR', d['document_number'], 'as of', as_of)
    return rows, dict(source='federal_register', notice=d['document_number'], list_as_of=as_of)


def pull_uflpa_dhs():
    page = decode(fetch(UFLPA_URL))
    rows = []
    for tr in re.findall(r'<tr[\s\S]*?</tr>', page):
        tds = re.findall(r'<td[\s\S]*?</td>', tr)
        if len(tds) < 2:
            continue
        ent, d = strip_tags(tds[0]), us_date(strip_tags(tds[-1]))
        if ent and d:
            rows.append(dict(text=ent, effective=d, doc='', url=UFLPA_URL))
    if len(rows) < 50:
        raise RuntimeError(f'UFLPA: only {len(rows)} rows parsed')
    log('UFLPA:', len(rows), 'rows')
    return rows, {}


def omb_biosecure_docs():
    docs = fr_search('"biotechnology companies of concern"', order='newest')
    return [dict(doc=d['document_number'], date=d['publication_date'], title=d['title'], url=d['html_url'])
            for d in docs if any('management-and-budget' in (a.get('slug') or '') for a in d.get('agencies', []))]


# ---------------------------------------------------------------- records

def build_records(key, rows):
    """Agency rows -> Senzing records. Rows naming the same primary entity are merged (aliases unioned, earliest date)."""
    ds = LISTS[key]['data_source']
    recs, by_key = [], {}

    def add(name, aliases, row, parent=None, affiliates=False):
        k = norm_key(name)
        if not k:
            return None
        if k in by_key:
            r = by_key[k]
            known = {norm_key(n['NAME_ORG']) for n in r['NAMES']}
            r['NAMES'] += [{'NAME_TYPE': 'ALIAS', 'NAME_ORG': a} for a in aliases if norm_key(a) not in known]
            r['LISTED_ON'] = min(r['LISTED_ON'], row['effective'])
            r['INCLUDES_AFFILIATES'] = r.get('INCLUDES_AFFILIATES', False) or affiliates
            return r
        rid = f'{ds.lower()}-{slug(name)}'
        r = {'DATA_SOURCE': ds, 'RECORD_ID': rid, 'RECORD_TYPE': 'ORGANIZATION', 'NAME_ORG': name,
             'NAMES': [{'NAME_TYPE': 'PRIMARY', 'NAME_ORG': name}] + [{'NAME_TYPE': 'ALIAS', 'NAME_ORG': a} for a in aliases],
             'LIST_NAME': LISTS[key]['name'], 'LISTED_ON': row['effective'], 'SOURCE_URL': row['url'], 'SOURCE_TEXT': clean(row['text'])[:1000]}
        if row.get('doc'):
            r['SOURCE_DOC'] = row['doc']
        if affiliates:
            r['INCLUDES_AFFILIATES'] = True
        if parent:
            r['PARENT_NAME'] = parent['NAME_ORG']
            r['RELATIONSHIPS'] = [{'REL_POINTER_DOMAIN': ds, 'REL_POINTER_KEY': parent['RECORD_ID'], 'REL_POINTER_ROLE': 'SUBSIDIARY_OF'}]
            parent.setdefault('RELATIONSHIPS', [{'REL_ANCHOR_DOMAIN': ds, 'REL_ANCHOR_KEY': parent['RECORD_ID']}])
        by_key[k] = r
        recs.append(r)
        return r

    for row in rows:
        e = parse_entry(row['text'])
        parent = add(e['primary'], e['aliases'], row, affiliates=e['includes_affiliates'] or row.get('affiliates', False))
        for sub in e['subsidiaries']:
            s = parse_entry(sub)
            add(s['primary'], s['aliases'], row, parent=parent)
    for r in recs:
        if not r.get('INCLUDES_AFFILIATES'):
            r.pop('INCLUDES_AFFILIATES', None)
    return sorted(recs, key=lambda r: r['RECORD_ID'])


def biosecure_from(recs_1260h):
    out = []
    for r in recs_1260h:
        names = ' | '.join(n['NAME_ORG'] for n in r['NAMES']) + ' | ' + r.get('PARENT_NAME', '')
        if BIOTECH_1260H.search(names):
            b = json.loads(json.dumps(r))
            b['DATA_SOURCE'] = 'US_BIOSECURE'
            b['RECORD_ID'] = r['RECORD_ID'].replace('us_dod_1260h-', 'us_biosecure-')
            b['LIST_NAME'] = LISTS['us_biosecure']['name']
            b['BASIS'] = 'FY2026 NDAA sec. 851: an entity on the Section 1260H list that is involved in biotechnology'
            for rel in b.get('RELATIONSHIPS', []):
                for k in ('REL_POINTER_DOMAIN', 'REL_ANCHOR_DOMAIN'):
                    if k in rel:
                        rel[k] = 'US_BIOSECURE'
                for k in ('REL_POINTER_KEY', 'REL_ANCHOR_KEY'):
                    if k in rel:
                        rel[k] = rel[k].replace('us_dod_1260h-', 'us_biosecure-')
            out.append(b)
    return out


def jsonl(recs):
    return ''.join(json.dumps(r, ensure_ascii=False, sort_keys=False) + '\n' for r in recs)


def read_jsonl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding='utf8').splitlines() if l.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default='dist')
    ap.add_argument('--prev', default=None, help='directory with the previous build (for fallbacks and CHANGES.md)')
    a = ap.parse_args(argv)
    out, prev = Path(a.out), Path(a.prev) if a.prev else None
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    pulls = {'us_dod_1260h': pull_1260h, 'us_far_889': pull_889, 'us_fcc_covered': pull_fcc, 'us_dhs_uflpa': pull_uflpa}
    recs, notes, errors = {}, {}, {}
    for key, fn in pulls.items():
        try:
            rows, notes[key] = fn()
            recs[key] = build_records(key, rows)
        except Exception as e:
            errors[key] = f'{type(e).__name__}: {e}'
            log('ERROR', key, errors[key])
            if prev and (prev / f'{key}.jsonl').exists():
                recs[key] = read_jsonl(prev / f'{key}.jsonl')
                log(key, 'kept the previous build:', len(recs[key]), 'records')
    if prev and notes.get('us_dhs_uflpa', {}).get('source') == 'federal_register' and (prev / 'us_dhs_uflpa.jsonl').exists():
        # the notice has no per-entity dates; keep the dates from the last dhs.gov build
        dates = {r['RECORD_ID']: r['LISTED_ON'] for r in read_jsonl(prev / 'us_dhs_uflpa.jsonl')}
        for r in recs.get('us_dhs_uflpa', []):
            r['LISTED_ON'] = min(r['LISTED_ON'], dates.get(r['RECORD_ID'], r['LISTED_ON']))
    if 'us_dod_1260h' in recs:
        recs['us_biosecure'] = biosecure_from(recs['us_dod_1260h'])
        try:
            notes['us_biosecure'] = dict(omb_list_documents=omb_biosecure_docs())
        except Exception as e:
            notes['us_biosecure'] = dict(omb_list_documents=None, omb_watch_error=str(e))
    if not recs:
        log('nothing built')
        return 1

    prev_manifest = json.loads((prev / 'manifest.json').read_text(encoding='utf8')) if prev and (prev / 'manifest.json').exists() else {}
    manifest = dict(feed=FEED_URL, license='CC BY 4.0 (data compiled by Tensorust LLC from US government sources)', format='Senzing JSON Lines',
                    built_at=now, lists={}, errors=errors)
    changes = []
    for key in LISTS:
        if key not in recs:
            continue
        body = jsonl(recs[key])
        (out / f'{key}.jsonl').write_text(body, encoding='utf8', newline='\n')
        names = sorted(r['NAME_ORG'] for r in recs[key])
        digest = hashlib.sha256(body.encode('utf8')).hexdigest()
        manifest['lists'][key] = dict(**LISTS[key], file=f'{key}.jsonl', records=len(recs[key]),
                                      aliases=sum(len(r['NAMES']) - 1 for r in recs[key]), sha256=digest,
                                      names_sha256=hashlib.sha256('\n'.join(names).encode('utf8')).hexdigest(),
                                      latest_listing=max((r['LISTED_ON'] for r in recs[key]), default=None),
                                      fetched_at=None if key in errors else now, notes=notes.get(key, {}))
        if prev and (prev / f'{key}.jsonl').exists():
            before = {r['RECORD_ID']: r['NAME_ORG'] for r in read_jsonl(prev / f'{key}.jsonl')}
            now_ids = {r['RECORD_ID']: r['NAME_ORG'] for r in recs[key]}
            added = sorted(n for i, n in now_ids.items() if i not in before)
            removed = sorted(n for i, n in before.items() if i not in now_ids)
            if added or removed:
                changes.append((key, added, removed))
        elif prev_manifest:  # a list the previous release did not have (its source failed then)
            changes.append((key, sorted(names), []))
    allrecs = [r for key in LISTS if key in recs for r in recs[key]]
    (out / 'all.jsonl').write_text(jsonl(allrecs), encoding='utf8', newline='\n')
    manifest['content_sha256'] = hashlib.sha256(''.join(manifest['lists'][k]['sha256'] for k in sorted(manifest['lists'])).encode()).hexdigest()
    manifest['names_changed'] = bool(changes) or not prev_manifest
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + '\n', encoding='utf8', newline='\n')

    lines = [f'# Build {now[:10]}', '']
    for key in LISTS:
        if key in manifest['lists']:
            m = manifest['lists'][key]
            lines.append(f"- {key}: {m['records']} records, {m['aliases']} aliases" + (f" (kept previous build: {errors[key]})" if key in errors else ''))
    for key, added, removed in changes:
        lines += ['', f'## {key}', ''] + [f'- added: {n}' for n in added] + [f'- removed: {n}' for n in removed]
    if not changes:
        lines += ['', 'No names added or removed since the previous build.' if prev_manifest else 'First build.']
    (out / 'CHANGES.md').write_text('\n'.join(lines) + '\n', encoding='utf8', newline='\n')
    log('built', {k: v['records'] for k, v in manifest['lists'].items()}, 'errors:', list(errors))
    return 0


if __name__ == '__main__':
    sys.exit(main())
