"""affiliates.py offline: small GLEIF, CSL and list fixtures built in a temp directory, run end to end."""
import csv
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import affiliates  # noqa: E402  (needs the ownership-rules package)
except ImportError as e:  # pragma: no cover
    affiliates = None
    IMPORT_ERROR = e

LEI2_COLS = ['LEI', 'Entity.LegalName', 'Entity.OtherEntityNames.OtherEntityName.1',
             'Entity.TransliteratedOtherEntityNames.TransliteratedOtherEntityName.1', 'Entity.LegalAddress.Country',
             'Entity.HeadquartersAddress.Country', 'Entity.RegistrationAuthority.RegistrationAuthorityEntityID',
             'Entity.EntityStatus', 'Registration.RegistrationStatus']
RR_COLS = ['Relationship.StartNode.NodeID', 'Relationship.StartNode.NodeIDType', 'Relationship.EndNode.NodeID',
           'Relationship.EndNode.NodeIDType', 'Relationship.RelationshipType', 'Relationship.RelationshipStatus'] + \
          [f'Relationship.Quantifiers.{i}.{k}' for i in range(1, 6) for k in ('MeasurementMethod', 'QuantifierAmount', 'QuantifierUnits')] + \
          ['Registration.LastUpdateDate', 'Registration.ValidationSources']

ENTITIES = [
    # lei, legal name, other name, translit, country, reg id
    ('L1', 'Публичное акционерное общество "Акме Банк"', '', 'ACME BANK PJSC', 'RU', '1027700000001'),
    ('L2', 'BETA TRADING L.L.C.', '', '', 'AE', 'R123'),
    ('L3', '甘玛科技有限公司', 'GAMMA TECH CO., LTD.', '', 'CN', '91110000X'),
    ('L4', 'DELTA S.A.', '', '', 'PA', ''),
    ('L5', 'OMEGA GROUP CORPORATION', '', '', 'CN', ''),
    ('C1', 'ACME CAPITAL LIMITED', '', '', 'CY', ''),
    ('C2', 'ACME CAPITAL NOMINEES LIMITED', '', '', 'CY', ''),
    ('C3', 'BETA TRADING LLC (LONDON BRANCH)', '', '', 'GB', ''),
    ('C4', 'DELTA HOLDINGS INC.', '', '', 'PA', ''),
    ('C5', 'OMEGA EUROPE B.V.', '', '', 'NL', ''),
    ('C6', 'GAMMA TECH (HK) LIMITED', '', '', 'HK', ''),
    ('C7', 'ACME LEASING LLC', '', '', 'RU', ''),
    ('C8', 'GAMMA TECH USA INC.', '', '', 'US', ''),
    ('X1', 'UNRELATED HOLDINGS LIMITED', '', '', 'GB', ''),
]
RELS = [
    # child, parent, type, pct
    ('C1', 'L1', 'IS_DIRECTLY_CONSOLIDATED_BY', '100.00'),
    ('C2', 'C1', 'IS_DIRECTLY_CONSOLIDATED_BY', ''),
    ('C3', 'L2', 'IS_INTERNATIONAL_BRANCH_OF', ''),
    ('C4', 'L4', 'IS_DIRECTLY_CONSOLIDATED_BY', '80'),
    ('C5', 'L5', 'IS_DIRECTLY_CONSOLIDATED_BY', '60'),
    ('C6', 'L3', 'IS_ULTIMATELY_CONSOLIDATED_BY', ''),
    ('C7', 'L1', 'IS_DIRECTLY_CONSOLIDATED_BY', '30'),
    ('C8', 'L3', 'IS_DIRECTLY_CONSOLIDATED_BY', '100'),
    ('X1', 'C9', 'IS_DIRECTLY_CONSOLIDATED_BY', '100'),
]
CSL = {'results': [
    {'source': 'Specially Designated Nationals (SDN) - Treasury Department', 'id': '101', 'name': 'ACME BANK PJSC',
     'type': 'Entity', 'programs': ['RUSSIA-EO14024'], 'addresses': [{'country': 'RU'}],
     'ids': [{'type': 'Legal Entity Number', 'number': 'L1'}]},
    {'source': 'Specially Designated Nationals (SDN) - Treasury Department', 'id': '102', 'name': 'BETA TRADING LLC',
     'type': 'Entity', 'programs': ['SDGT'], 'addresses': [{'country': 'AE'}],
     'ids': [{'type': 'Registration Number', 'number': 'R-123', 'country': 'AE'}]},
    {'source': 'Specially Designated Nationals (SDN) - Treasury Department', 'id': '103', 'name': 'DELTA S.A.',
     'type': 'Entity', 'programs': ['CUBA'], 'addresses': [{'country': 'PA'}], 'ids': []},
    {'source': 'Specially Designated Nationals (SDN) - Treasury Department', 'id': '104', 'name': 'JOHN DOE',
     'type': 'Individual', 'programs': ['SDGT'], 'addresses': [], 'ids': []},
    {'source': 'Entity List (EL) - Bureau of Industry and Security', 'id': 'e1', 'name': 'Gamma Tech Co., Ltd.',
     'addresses': [{'country': 'CN'}], 'alt_names': None},
    {'source': 'Denied Persons List (DPL) - Bureau of Industry and Security', 'id': 'd1', 'name': 'Omega Group Corporation'},
]}


def _zip(path, cols, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(path.name.replace('.zip', ''), buf.getvalue())


def _fixtures(tmp):
    cache, lists, out = tmp / 'cache', tmp / 'lists', tmp / 'out'
    for d in (cache, lists, out):
        d.mkdir()
    _zip(cache / 'gleif-lei2.csv.zip', LEI2_COLS,
         [[lei, n, o, t, c, c, r, 'ACTIVE', 'ISSUED'] for lei, n, o, t, c, r in ENTITIES])
    rows = []
    for child, parent, typ, pct in RELS:
        q = ['ACCOUNTING_CONSOLIDATION', pct, 'PERCENTAGE'] if pct else ['', '', '']
        rows.append([child, 'LEI', parent, 'LEI', typ, 'ACTIVE'] + q + [''] * 12 + ['2026-01-02T00:00:00Z', 'FULLY_CORROBORATED'])
    _zip(cache / 'gleif-rr.csv.zip', RR_COLS, rows)
    (cache / 'consolidated.json').write_text(json.dumps(CSL), encoding='utf8')
    recs = [
        {'DATA_SOURCE': 'US_DOD_1260H', 'RECORD_ID': 'us_dod_1260h-omega-group-corporation', 'RECORD_TYPE': 'ORGANIZATION',
         'NAME_ORG': 'Omega Group Corporation', 'NAMES': [{'NAME_TYPE': 'PRIMARY', 'NAME_ORG': 'Omega Group Corporation'}],
         'RELATIONSHIPS': [{'REL_ANCHOR_DOMAIN': 'US_DOD_1260H', 'REL_ANCHOR_KEY': 'us_dod_1260h-omega-group-corporation'}]},
        {'DATA_SOURCE': 'US_DOD_1260H', 'RECORD_ID': 'us_dod_1260h-omega-sub', 'RECORD_TYPE': 'ORGANIZATION',
         'NAME_ORG': 'Omega Sub', 'NAMES': [{'NAME_TYPE': 'PRIMARY', 'NAME_ORG': 'Omega Sub'}],
         'RELATIONSHIPS': [{'REL_POINTER_DOMAIN': 'US_DOD_1260H', 'REL_POINTER_KEY': 'us_dod_1260h-omega-group-corporation',
                            'REL_POINTER_ROLE': 'SUBSIDIARY_OF'}]},
    ]
    (lists / 'us_dod_1260h.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in recs), encoding='utf8')
    (lists / 'manifest.json').write_text(json.dumps({'lists': {'us_dod_1260h': {'sha256': 'ab'}}, 'content_sha256': 'x'}),
                                         encoding='utf8')
    (lists / 'CHANGES.md').write_text('# Build\n', encoding='utf8')
    return cache, lists, out


def _run(cache, lists, out, prev=None):
    import contextlib
    argv = ['--lists', str(lists), '--out', str(out), '--cache', str(cache), '--offline']
    if prev:
        argv += ['--prev', str(prev)]
    with contextlib.redirect_stderr(io.StringIO()):
        return affiliates.main(argv)


@unittest.skipIf(affiliates is None, 'ownership-rules is not installed')
class Affiliates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        cls.cache, cls.lists, _ = _fixtures(tmp)
        cls.out = cls.lists  # the workflow writes into the same dist directory
        assert _run(cls.cache, cls.lists, cls.out) == 0
        cls.recs = {r['RECORD_ID']: r for r in affiliates.read_jsonl(cls.out / 'affiliates.senzing.jsonl')}
        cls.manifest = json.loads((cls.out / 'affiliates.manifest.json').read_text(encoding='utf8'))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def v(self, lei):
        return self.recs.get(f'lei:{lei}', {}).get('OWNERSHIP_VERDICTS')

    def test_matching_methods(self):
        self.assertEqual(self.manifest['seed_matches'],
                         {'lei_on_list_entry': 1, 'registration_number_and_country': 1, 'exact_name_and_country': 3})

    def test_verdicts(self):
        self.assertEqual(self.v('C1'), {'ofac-50': 'definitely', 'bis-affiliates': 'definitely'})
        self.assertEqual(self.v('C2'), {'ofac-50': 'control_only', 'bis-affiliates': 'control_only'})
        self.assertEqual(self.v('C3'), {'ofac-50': 'definitely', 'bis-affiliates': 'definitely'})
        self.assertEqual(self.v('C4'), {'ofac-50': 'definitely'})  # CUBA is not a 744.8 program
        self.assertEqual(self.v('C5'), {'any-list-50': 'definitely'})
        self.assertEqual(self.v('C6'), {'bis-affiliates': 'control_only'})
        self.assertEqual(self.v('C7'), {'ofac-50': 'control_only', 'bis-affiliates': 'control_only'})
        self.assertIsNone(self.v('C8'))  # US entity, and only the BIS rule (foreign entities) has a seed above it
        self.assertIsNone(self.v('X1'))
        self.assertEqual(len(self.recs), 7)

    def test_record_shape(self):
        r = self.recs['lei:C1']
        for k in ('DATA_SOURCE', 'RECORD_ID', 'RECORD_TYPE', 'NAME_ORG', 'NAMES', 'LEI_NUMBER', 'REGISTRATION_COUNTRY',
                  'EVIDENCE_STATE', 'OWNERSHIP_VERDICTS', 'OWNERSHIP_PROOF', 'INPUTS_SHA256', 'RELATIONSHIPS'):
            self.assertIn(k, r)
        self.assertEqual(r['EVIDENCE_STATE'], 'evidence, not determination')
        self.assertEqual(r['INPUTS_SHA256'], self.manifest['inputs_sha256'])
        proof = r['OWNERSHIP_PROOF']['ofac-50']['proof']
        self.assertEqual(proof[0]['verdict'], 'listed')
        self.assertEqual(proof[0]['name'], 'ACME BANK PJSC')
        self.assertEqual(proof[-1]['via'][0]['source'], 'GLEIF IS_DIRECTLY_CONSOLIDATED_BY')
        self.assertEqual(r['RELATIONSHIPS'][0], {'REL_ANCHOR_DOMAIN': 'US_RESTRICTED_AFFILIATES', 'REL_ANCHOR_KEY': 'lei:C1'})
        self.assertIn({'REL_POINTER_DOMAIN': 'LISTED_PARTY', 'REL_POINTER_KEY': 'lei:L1',
                       'REL_POINTER_ROLE': 'OWNED_OR_CONTROLLED_BY'}, r['RELATIONSHIPS'])

    def test_latin_name_is_primary(self):
        self.assertEqual(affiliates.primary_name(['Акме Банк', 'ACME BANK PJSC']), 'ACME BANK PJSC')
        self.assertEqual(affiliates.primary_name(['甘玛科技有限公司']), '甘玛科技有限公司')

    def test_name_key(self):
        k = affiliates.name_key
        self.assertEqual(k('Gamma Tech Co., Ltd.'), k('GAMMA TECH COMPANY LIMITED'))
        self.assertEqual(k('Beta Trading L.L.C.'), k('BETA TRADING LLC'))
        self.assertNotEqual(k('Beta Trading LLC'), k('Beta Trading Inc'))

    def test_ftm_references_resolve(self):
        ents = [json.loads(l) for l in (self.out / 'affiliates.ftm.json').read_text(encoding='utf8').splitlines()]
        ids = {e['id'] for e in ents}
        owns = [e for e in ents if e['schema'] == 'Ownership']
        self.assertTrue(owns)
        for o in owns:
            self.assertIn(o['properties']['owner'][0], ids)
            self.assertIn(o['properties']['asset'][0], ids)

    def test_manifest_and_changes(self):
        m = json.loads((self.out / 'manifest.json').read_text(encoding='utf8'))
        self.assertEqual(m['affiliates']['records'], 7)
        self.assertNotEqual(m['content_sha256'], 'x')
        self.assertIn('## Affiliates', (self.out / 'CHANGES.md').read_text(encoding='utf8'))
        events = affiliates.read_jsonl(self.out / 'affiliates.changes.jsonl')
        self.assertEqual({e['event'] for e in events}, {'added'})

    def test_change_events_against_previous_build(self):
        prev = [dict(self.recs['lei:C1'], OWNERSHIP_VERDICTS={'ofac-50': 'possibly'}),
                {'RECORD_ID': 'lei:GONE', 'NAME_ORG': 'Gone Ltd', 'OWNERSHIP_VERDICTS': {'ofac-50': 'definitely'}}]
        ev = affiliates.change_events(prev, list(self.recs.values()))
        kinds = {e['record_id']: e['event'] for e in ev}
        self.assertEqual(kinds['lei:C1'], 'verdict_changed')
        self.assertEqual(kinds['lei:GONE'], 'removed')
        self.assertEqual(kinds['lei:C2'], 'added')

    def test_failed_inputs_keep_the_previous_feed(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cache, lists, out = _fixtures(d)
            (cache / 'gleif-rr.csv.zip').unlink()
            self.assertEqual(_run(cache, lists, out), 1)  # nothing to fall back to
            prev = d / 'prev'
            prev.mkdir()
            (prev / 'affiliates.senzing.jsonl').write_text(json.dumps(self.recs['lei:C1']) + '\n', encoding='utf8')
            self.assertEqual(_run(cache, lists, out, prev), 0)
            kept = affiliates.read_jsonl(out / 'affiliates.senzing.jsonl')
            self.assertEqual([r['RECORD_ID'] for r in kept], ['lei:C1'])
            self.assertIn('FileNotFoundError', json.loads((out / 'affiliates.manifest.json').read_text(encoding='utf8'))['error'])


if __name__ == '__main__':
    unittest.main()
