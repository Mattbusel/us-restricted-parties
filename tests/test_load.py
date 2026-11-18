"""tools/load.py: offline against a six-record excerpt of a real release (tests/fixtures/
feed_excerpt.jsonl), and, with URP_LIVE=1, against the latest release assets."""
import csv
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import load  # noqa: E402

FEED = str(ROOT / 'tests' / 'fixtures' / 'feed_excerpt.jsonl')
AFF = str(ROOT / 'tests' / 'fixtures' / 'affiliates_excerpt.senzing.jsonl')


class Offline(unittest.TestCase):
    def test_load_all_and_one_list(self):
        self.assertEqual(len(load.load('all', FEED)), 6)
        recs = load.load('us_dod_1260h', FEED)
        self.assertEqual([r['DATA_SOURCE'] for r in recs], ['US_DOD_1260H', 'US_DOD_1260H'])
        with self.assertRaises(ValueError):
            load.load('ofac_sdn', FEED)

    def test_names_and_parent(self):
        parent, sub = load.load('us_dod_1260h', FEED)
        self.assertEqual(load.names(parent)[0], parent['NAME_ORG'])
        self.assertEqual(len(load.names(parent)), len(set(load.names(parent))))
        self.assertIsNone(load.parent_id(parent))
        self.assertEqual(load.parent_id(sub), parent['RECORD_ID'])

    def test_csv_rows(self):
        rows = list(load.to_rows(load.load('all', FEED)))
        self.assertEqual(len(rows), 6)
        self.assertEqual(set(rows[0]), set(load.CSV_FIELDS))
        sub = rows[1]
        self.assertEqual(sub['parent_name'], 'China Electronics Technology Group Corporation')
        self.assertEqual(sub['parent_record_id'], rows[0]['record_id'])
        far = next(r for r in rows if r['data_source'] == 'US_FAR_889')
        self.assertEqual(far['includes_affiliates'], 'true')
        buf = io.StringIO()
        with redirect_stdout(buf):
            load.main(['--feed', FEED, '--format', 'csv'])
        parsed = list(csv.DictReader(io.StringIO(buf.getvalue())))
        self.assertEqual(len(parsed), 6)

    def test_ftm_entities(self):
        ents = list(load.to_ftm(load.load('all', FEED)))
        schemas = [e['schema'] for e in ents]
        self.assertEqual(schemas.count('Company'), 6)
        self.assertEqual(schemas.count('Sanction'), 6)
        # Two named subsidiaries in the excerpt: one 1260H, one BIOSECURE.
        self.assertEqual(schemas.count('Ownership'), 2)
        ids = [e['id'] for e in ents]
        self.assertEqual(len(ids), len(set(ids)))
        sanction = next(e for e in ents if e['schema'] == 'Sanction')
        self.assertEqual(sanction['properties']['authority'], ['Department of Defense, OUSD(A&S)'])
        for e in ents:
            for values in e['properties'].values():
                self.assertIsInstance(values, list)
                self.assertTrue(all(isinstance(v, str) and v for v in values))

    def test_file_names_and_pinned_urls(self):
        self.assertEqual(load.file_name('us_far_889'), 'us_far_889.jsonl')
        self.assertEqual(load.file_name('affiliates.covered'), 'affiliates.covered.senzing.jsonl')
        self.assertEqual(load.url('all'), load.LATEST + 'all.jsonl')
        self.assertEqual(load.url('affiliates.review', 'data-2026-11-17-2241'),
                         load.RELEASES + 'data-2026-11-17-2241/affiliates.review.senzing.jsonl')
        with self.assertRaises(ValueError):
            load.file_name('affiliates.senzing')

    def test_affiliates_records(self):
        recs = load.load('affiliates.covered', AFF)
        self.assertEqual(len(recs), 2)
        for r in recs:
            self.assertEqual(r['DATA_SOURCE'], 'US_RESTRICTED_AFFILIATES')
            self.assertEqual(r['VERDICT_CLASS'], 'covered')
            self.assertTrue(set(load.verdicts(r).values()) & {'definitely', 'possibly'})
        self.assertEqual(load.verdicts(load.load('all', FEED)[0]), {})

    def test_verify_against_a_local_manifest(self):
        import hashlib
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'us_far_889.jsonl'
            shutil.copy(FEED, f)
            sha = hashlib.sha256(f.read_bytes()).hexdigest()
            man = {'lists': {'us_far_889': {'sha256': sha}}}
            (Path(d) / 'manifest.json').write_text(json.dumps(man), encoding='utf-8')
            self.assertEqual(len(load.load('us_far_889', str(f), verify=True)), 1)
            man['lists']['us_far_889']['sha256'] = '0' * 64
            (Path(d) / 'manifest.json').write_text(json.dumps(man), encoding='utf-8')
            with self.assertRaises(ValueError):
                load.load('us_far_889', str(f), verify=True)
            with self.assertRaises(ValueError):
                load.load('all', str(f), verify=True)

    def test_summary(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            load.main(['--feed', FEED])
        self.assertIn('total                6 records', buf.getvalue())


@unittest.skipUnless(os.environ.get('URP_LIVE') == '1', 'set URP_LIVE=1 to read the latest release')
class LatestRelease(unittest.TestCase):
    def test_every_list_matches_the_manifest(self):
        man = load.manifest()
        total = 0
        for name in load.LISTS:
            recs = load.load(name)
            self.assertEqual(len(recs), man['lists'][name]['records'], name)
            self.assertTrue(all(r['DATA_SOURCE'] == name.upper() for r in recs))
            total += len(recs)
        self.assertEqual(len(load.load('all')), total)
        json.dumps(list(load.to_ftm(load.load('all'))))

    def test_hashes_and_affiliates_match_the_manifest(self):
        man = load.manifest()
        for name in load.LISTS:
            load.load(name, verify=True)
        for name in load.AFFILIATES:
            fname = load.file_name(name)
            if fname not in man.get('affiliates', {}).get('files', {}):
                continue
            recs = load.load(name, verify=True)
            self.assertEqual(len(recs), man['affiliates']['files'][fname]['records'], name)

    def test_a_pinned_release_still_loads(self):
        recs = load.load('us_dod_1260h', release='data-2026-11-17-2241', verify=True)
        self.assertGreater(len(recs), 100)


if __name__ == '__main__':
    unittest.main()
