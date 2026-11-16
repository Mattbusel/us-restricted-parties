"""Offline tests for the Hugging Face mirror: flat rows and the dataset card (no network, no pyarrow needed)."""
import re, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import hf_publish  # noqa: E402

PARENT = {'DATA_SOURCE': 'US_DOD_1260H', 'RECORD_ID': 'us_dod_1260h-bgi-group', 'RECORD_TYPE': 'ORGANIZATION',
          'NAME_ORG': 'BGI Group', 'NAMES': [{'NAME_TYPE': 'PRIMARY', 'NAME_ORG': 'BGI Group'}],
          'LIST_NAME': 'DoD Chinese Military Companies (Section 1260H)', 'LISTED_ON': '2021-06-03',
          'SOURCE_URL': 'https://www.federalregister.gov/d/2026-11571', 'SOURCE_TEXT': 'BGI Group (and BGI Group subsidiaries: ...)',
          'SOURCE_DOC': '2026-11571', 'RELATIONSHIPS': [{'REL_ANCHOR_DOMAIN': 'US_DOD_1260H', 'REL_ANCHOR_KEY': 'us_dod_1260h-bgi-group'}]}
CHILD = {'DATA_SOURCE': 'US_DOD_1260H', 'RECORD_ID': 'us_dod_1260h-bgi-genomics-co-ltd', 'RECORD_TYPE': 'ORGANIZATION',
         'NAME_ORG': 'BGI Genomics Co., Ltd.', 'NAMES': [{'NAME_TYPE': 'PRIMARY', 'NAME_ORG': 'BGI Genomics Co., Ltd.'},
                                                         {'NAME_TYPE': 'ALIAS', 'NAME_ORG': 'BGI'}],
         'LIST_NAME': 'DoD Chinese Military Companies (Section 1260H)', 'LISTED_ON': '2026-06-10', 'SOURCE_URL': 'u',
         'SOURCE_TEXT': 't', 'PARENT_NAME': 'BGI Group', 'INCLUDES_AFFILIATES': True,
         'RELATIONSHIPS': [{'REL_POINTER_DOMAIN': 'US_DOD_1260H', 'REL_POINTER_KEY': 'us_dod_1260h-bgi-group',
                            'REL_POINTER_ROLE': 'SUBSIDIARY_OF'}]}
META = {'name': 'DoD Chinese Military Companies (Section 1260H)', 'operator': 'Department of Defense, OUSD(A&S)',
        'source': 'Federal Register, DoD notices under Section 1260H', 'file': 'us_dod_1260h.jsonl', 'records': 2, 'aliases': 1,
        'latest_listing': '2026-06-10'}


class FlatRows(unittest.TestCase):
    def test_columns_and_parent_link(self):
        parent, child = hf_publish.flat_rows('us_dod_1260h', [PARENT, CHILD], META)
        self.assertEqual(list(child), [c[0] for c in hf_publish.COLUMNS])
        self.assertEqual(child['aliases'], ['BGI'])
        self.assertEqual(child['parent_record_id'], 'us_dod_1260h-bgi-group')
        self.assertTrue(child['includes_affiliates'])
        self.assertEqual(child['source_agency'], 'Department of Defense, OUSD(A&S)')
        self.assertIsNone(parent['parent_record_id'])  # an anchor is not a parent pointer
        self.assertEqual(parent['aliases'], [])
        self.assertFalse(parent['includes_affiliates'])


class Card(unittest.TestCase):
    def setUp(self):
        bio = dict(META, name='BIOSECURE biotechnology companies of concern (interim)', file='us_biosecure.jsonl', records=1,
                   notes={'omb_list_documents': []})
        self.manifest = {'built_at': '2026-11-15T20:49:34Z', 'errors': {}, 'content_sha256': 'x',
                         'lists': {'us_dod_1260h': META, 'us_biosecure': bio}}
        self.text = hf_publish.card(self.manifest)

    def test_front_matter_declares_a_split_per_list(self):
        front = self.text.split('---')[1]
        self.assertIn('license: cc-by-4.0', front)
        for key in self.manifest['lists']:
            self.assertIn(f'- split: {key}\n    path: data/{key}.parquet', front)
            self.assertRegex(key, r'^\w+(\.\w+)*$')  # Hugging Face split name rule
        self.assertIn('- config_name: all', front)

    def test_body_has_as_of_date_agency_and_paid_links(self):
        self.assertIn('Lists as of **2026-11-15**', self.text)
        self.assertIn('Department of Defense, OUSD(A&S)', self.text)
        for url in (hf_publish.PAID_FEED, hf_publish.EXPOSURE, hf_publish.AUDIT):
            self.assertIn(url, self.text)
        self.assertNotIn('—', self.text)

    def test_source_errors_are_named(self):
        self.manifest['errors'] = {'us_dhs_uflpa': 'HTTPError 403'}
        self.assertRegex(hf_publish.card(self.manifest), re.escape('`us_dhs_uflpa`'))


if __name__ == '__main__':
    unittest.main()
