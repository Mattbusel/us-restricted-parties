#!/usr/bin/env python3
"""Mirror a build of the feed to the Hugging Face dataset tensorust/us-restricted-parties.

    python hf_publish.py --dist dist [--repo tensorust/us-restricted-parties] [--stage DIR] [--dry-run]

Writes one parquet split per list (data/<list>.parquet, flat columns so `load_dataset` and the dataset viewer
work), the Senzing JSON Lines as published on GitHub (senzing/<list>.jsonl, senzing/all.jsonl), manifest.json,
CHANGES.md and the dataset card, then uploads them in one commit. Nothing is uploaded when the dataset already
holds the same lists (same manifest content_sha256), so running it every day is cheap.

The token comes from the HF_TOKEN environment variable. Needs huggingface_hub and pyarrow; the functions that
build rows and the card are standard library only (tests import them without either package).
"""
import argparse, json, shutil, sys, tempfile
from pathlib import Path

REPO = 'tensorust/us-restricted-parties'
GITHUB = 'https://github.com/Mattbusel/us-restricted-parties'
PAID_FEED = 'https://shop.tensorust.com/offers/enterprise-api'
EXPOSURE = 'https://shop.tensorust.com/exposure'
AUDIT = 'https://shop.tensorust.com/offers/compliance-audit-watch'

COLUMNS = [  # (name, pyarrow type name, description)
    ('list', 'string', 'Feed list key, e.g. us_dod_1260h'),
    ('record_id', 'string', 'Stable id, the same RECORD_ID as the Senzing file'),
    ('name', 'string', 'Primary name as the agency prints it'),
    ('aliases', 'list<string>', 'Every alias and former name the source gives'),
    ('parent_name', 'string', 'Listed parent when the source names this entity as its subsidiary'),
    ('parent_record_id', 'string', 'record_id of that parent'),
    ('listed_on', 'string', 'Date the entity was added to the list (YYYY-MM-DD)'),
    ('includes_affiliates', 'bool', 'The listing also covers subsidiaries and affiliates the source does not name'),
    ('basis', 'string', 'Legal basis when it differs from the list itself (BIOSECURE interim list)'),
    ('list_name', 'string', 'Full name of the list'),
    ('source_agency', 'string', 'Agency that maintains the list'),
    ('source_doc', 'string', 'Federal Register document number or CFR citation'),
    ('source_url', 'string', 'Page the entry was read from'),
    ('source_text', 'string', 'The entry exactly as printed by the agency (up to 1,000 characters)'),
]

SPLIT_LABEL = {
    'us_dod_1260h': 'Section 1260H Chinese military companies',
    'us_far_889': 'Section 889 (FAR 52.204-25)',
    'us_fcc_covered': 'FCC Covered List',
    'us_dhs_uflpa': 'UFLPA Entity List',
    'us_biosecure': 'BIOSECURE (interim)',
}


def read_jsonl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding='utf8').splitlines() if l.strip()]


def flat_rows(key, records, meta):
    """Senzing records of one list -> flat dict rows in COLUMNS order."""
    rows = []
    for r in records:
        rel = next((x for x in r.get('RELATIONSHIPS', []) if x.get('REL_POINTER_ROLE') == 'SUBSIDIARY_OF'), {})
        rows.append({
            'list': key,
            'record_id': r['RECORD_ID'],
            'name': r['NAME_ORG'],
            'aliases': [n['NAME_ORG'] for n in r.get('NAMES', []) if n.get('NAME_TYPE') != 'PRIMARY'],
            'parent_name': r.get('PARENT_NAME'),
            'parent_record_id': rel.get('REL_POINTER_KEY'),
            'listed_on': r.get('LISTED_ON'),
            'includes_affiliates': bool(r.get('INCLUDES_AFFILIATES', False)),
            'basis': r.get('BASIS'),
            'list_name': r.get('LIST_NAME') or meta.get('name'),
            'source_agency': meta.get('operator'),
            'source_doc': r.get('SOURCE_DOC'),
            'source_url': r.get('SOURCE_URL'),
            'source_text': r.get('SOURCE_TEXT'),
        })
    return rows


def card(manifest):
    """Dataset card (README.md of the Hugging Face dataset repo) for a build manifest."""
    lists = manifest['lists']
    built = manifest['built_at'][:10]
    total = sum(m['records'] for m in lists.values())
    y = ['---', 'license: cc-by-4.0',
         'pretty_name: "US Restricted Parties: 1260H, Section 889, FCC Covered List, UFLPA, BIOSECURE"',
         'language:', '- en',
         'tags:'] + [f'- {t}' for t in ('sanctions', 'compliance', 'kyc', 'entity-resolution', 'senzing', 'watchman',
                                         'uflpa', 'export-controls', 'supply-chain', 'government-data', 'tensorust')] + [
         'size_categories:', '- n<1K' if total < 1000 else '- 1K<n<10K',
         'configs:', '- config_name: default', '  data_files:']
    for key in lists:
        y += [f'  - split: {key}', f'    path: data/{key}.parquet']
    y += ['- config_name: all', '  data_files:', '  - split: train', '    path: data/*.parquet', '---', '']

    b = ['# US Restricted Parties', '',
         'The US government lists that ban a company from federal contracts, grants, networks or imports, rebuilt '
         'every day straight from the agency sources and kept as one clean table per list. Every entity has its '
         'aliases, the parent the agency names, the date it was listed and the exact sentence it came from.', '',
         f'Lists as of **{built}** ({total} records). A GitHub Action re-reads the sources daily at '
         f'[{GITHUB.split("github.com/")[1]}]({GITHUB}) and this dataset is updated the same run whenever a list '
         'changes.', '',
         '```python',
         'from datasets import load_dataset',
         '',
         f'uflpa = load_dataset("{REPO}", split="us_dhs_uflpa")',
         f'every_list = load_dataset("{REPO}", "all", split="train")',
         '```', '',
         '## Lists', '',
         '| split | list | records | aliases | latest listing | source agency | read from |',
         '|---|---|---|---|---|---|---|']
    for key, m in lists.items():
        src, notes = m.get('source', ''), m.get('notes') or {}
        if notes.get('source') == 'federal_register' and notes.get('notice'):  # UFLPA fallback when dhs.gov blocks the runner
            src = f"Federal Register notice [{notes['notice']}](https://www.federalregister.gov/d/{notes['notice']})"
        elif src.startswith('http'):
            src = f'[{src}]({src})'
        b.append(f"| `{key}` | {SPLIT_LABEL.get(key, m['name'])} | {m['records']} | {m['aliases']} | "
                 f"{m.get('latest_listing') or ''} | {m.get('operator', '')} | {src} |")
    b.append('')
    bio = lists.get('us_biosecure')
    if bio:
        docs = (bio.get('notes') or {}).get('omb_list_documents') or []
        b += ['BIOSECURE: until OMB publishes its list of biotechnology companies of concern (due December 18 2026), '
              'the split holds the Section 1260H listings that are biotechnology companies, which the FY2026 NDAA '
              'sec. 851 already names companies of concern. '
              + (f'OMB documents seen so far: {", ".join(d["doc"] for d in docs)}.' if docs else
                 'The OMB list will replace it in the first build after it is published.'), '']
    errs = manifest.get('errors') or {}
    if errs:
        b += ['Sources that could not be read in this build (the previous build of that list is kept): '
              + ', '.join(f'`{k}`' for k in errs) + '.', '']
    b += ['## Columns', '', '| column | type | meaning |', '|---|---|---|']
    b += [f'| `{n}` | {t} | {d} |' for n, t, d in COLUMNS]
    b += ['',
          '## Senzing and Watchman files', '',
          'The same records in Senzing JSON Lines (one ORGANIZATION per line, NAMES array, RELATIONSHIPS to the '
          'parent) are in `senzing/<list>.jsonl` and `senzing/all.jsonl`, with `manifest.json` (counts, hashes, '
          'sources, pull errors) and `CHANGES.md` (names added and removed since the previous build).', '',
          '```python',
          'from huggingface_hub import hf_hub_download',
          '',
          f'path = hf_hub_download("{REPO}", "senzing/us_dod_1260h.jsonl", repo_type="dataset")',
          '```', '',
          f'moov-io/watchman can load the GitHub release URLs directly; see [docs/watchman.md]({GITHUB}/blob/main/docs/watchman.md).', '',
          '## What these lists do not tell you', '',
          'The agencies name parents and a few subsidiaries. The US companies that sign the contracts and receive '
          'the federal money are often several layers down, under other names. These splits are the raw public lists '
          'only.', '',
          f'- **Ownership-expanded feed**: every listed parent expanded through state business registries, SEC '
          f'subsidiary lists and FCC ownership filings, through the [Tensorust Ontology and Data API]({PAID_FEED}).',
          f'- **Exposure evidence files**: which federal awards and purchases pay a listed vendor, with the source '
          f'records for each hit, from $2,500. Free ledger and lookups at [{EXPOSURE.split("//")[1]}]({EXPOSURE}).',
          f'- **Compliance Audit and Watch**: your own awards, vendors or suppliers screened against every list, '
          f'with quarterly re-screens, from $25,000 ([details]({AUDIT})).', '',
          '## License and credit', '',
          'Data: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Credit "US Restricted Parties feed by '
          'Tensorust LLC". The underlying lists are US government works in the public domain; the compilation, '
          'parsing and alias splitting are ours. Build code: MIT, at ' + GITHUB + '.', '',
          'This is data, not legal advice. Check the linked agency source before acting on a match.', '',
          'Maintained by Matthew Busel (Tensorust LLC). Corrections: open an issue on the GitHub repo.', '']
    return '\n'.join(y + b)


def stage(dist, out):
    """Write everything the dataset repo should hold into out. Returns the manifest."""
    import pyarrow as pa, pyarrow.parquet as pq
    dist, out = Path(dist), Path(out)
    manifest = json.loads((dist / 'manifest.json').read_text(encoding='utf8'))
    types = {'string': pa.string(), 'bool': pa.bool_(), 'list<string>': pa.list_(pa.string())}
    schema = pa.schema([(n, types[t]) for n, t, _ in COLUMNS])
    (out / 'data').mkdir(parents=True, exist_ok=True)
    (out / 'senzing').mkdir(parents=True, exist_ok=True)
    for key, meta in manifest['lists'].items():
        recs = read_jsonl(dist / meta['file'])
        pq.write_table(pa.Table.from_pylist(flat_rows(key, recs, meta), schema=schema), out / 'data' / f'{key}.parquet')
        shutil.copyfile(dist / meta['file'], out / 'senzing' / meta['file'])
    if (dist / 'all.jsonl').exists():
        shutil.copyfile(dist / 'all.jsonl', out / 'senzing' / 'all.jsonl')
    for f in ('manifest.json', 'CHANGES.md'):
        if (dist / f).exists():
            shutil.copyfile(dist / f, out / f)
    (out / 'README.md').write_text(card(manifest), encoding='utf8', newline='\n')
    return manifest


def remote_sha(api, repo):
    from huggingface_hub import hf_hub_download
    try:
        p = hf_hub_download(repo, 'manifest.json', repo_type='dataset', token=api.token)
        return json.loads(Path(p).read_text(encoding='utf8')).get('content_sha256')
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dist', default='dist')
    ap.add_argument('--repo', default=REPO)
    ap.add_argument('--stage', default=None, help='staging directory (default: a temporary one)')
    ap.add_argument('--dry-run', action='store_true', help='stage the files but do not upload')
    ap.add_argument('--force', action='store_true', help='upload even when the dataset already has these lists')
    a = ap.parse_args(argv)
    out = Path(a.stage or tempfile.mkdtemp(prefix='hf-urp-'))
    manifest = stage(a.dist, out)
    print('staged', out, {k: v['records'] for k, v in manifest['lists'].items()}, file=sys.stderr)
    if a.dry_run:
        return 0
    from huggingface_hub import HfApi
    api = HfApi()
    api.create_repo(a.repo, repo_type='dataset', exist_ok=True)
    if not a.force and remote_sha(api, a.repo) == manifest['content_sha256']:
        print('Hugging Face dataset already has these lists; nothing to upload.', file=sys.stderr)
        return 0
    info = api.upload_folder(repo_id=a.repo, repo_type='dataset', folder_path=str(out),
                             commit_message=f"Lists as of {manifest['built_at'][:10]}",
                             delete_patterns=['data/*.parquet', 'senzing/*.jsonl'])
    print('uploaded', getattr(info, 'commit_url', info), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
