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


if __name__ == '__main__':
    unittest.main()
