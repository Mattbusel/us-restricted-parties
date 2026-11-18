"""affiliates.py offline: small GLEIF, CSL and list fixtures built in a temp directory, run end to end."""
import csv
import hashlib
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
    ('L6', 'SIGMA SYSTEMS LIMITED', '', '', 'CN', ''),
    ('U1', 'ACME AMERICA HOLDINGS INC.', '', '', 'US', ''),
    ('F1', 'ACME ASIA PTE. LTD.', '', '', 'SG', ''),
    ('P1', 'ACME CARIBE LLC', '', '', 'PR', ''),
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
    ('L6', 'L1', 'IS_DIRECTLY_CONSOLIDATED_BY', '100'),  # an Entity List company owned by an SDN
    ('U1', 'L1', 'IS_DIRECTLY_CONSOLIDATED_BY', '100'),  # a US holding company ...
    ('F1', 'U1', 'IS_DIRECTLY_CONSOLIDATED_BY', '100'),  # ... and the foreign company it owns
    ('P1', 'L1', 'IS_DIRECTLY_CONSOLIDATED_BY', '100'),  # Puerto Rico is not foreign under the EAR
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
    {'source': 'Entity List (EL) - Bureau of Industry and Security', 'id': 'e2', 'name': 'Sigma Systems Ltd.',
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


def _run(cache, lists, out, prev=None, stderr=None):
    import contextlib
    argv = ['--lists', str(lists), '--out', str(out), '--cache', str(cache), '--offline']
    if prev:
        argv += ['--prev', str(prev)]
    with contextlib.redirect_stderr(stderr if stderr is not None else io.StringIO()):
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
                         {'lei_on_list_entry': 1, 'registration_number_and_country': 1, 'exact_name_and_country': 4})

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
        self.assertEqual(len(self.recs), 11)

    def test_a_seed_of_one_list_is_reported_under_the_other_rules(self):
        # Sigma is on the Entity List (a bis-affiliates seed) and 100 percent consolidated by an SDN.
        self.assertEqual(self.v('L6'), {'ofac-50': 'definitely'})
        self.assertEqual(self.recs['lei:L6']['LISTED_ON'], ['EL'])
        self.assertNotIn('bis-affiliates', self.recs['lei:L6']['OWNERSHIP_PROOF'])
        self.assertNotIn('LISTED_ON', self.recs['lei:C1'])

    def test_bis_reaches_through_a_us_intermediary_but_not_us_territories(self):
        self.assertEqual(self.v('U1'), {'ofac-50': 'definitely'})
        self.assertEqual(self.v('F1'), {'ofac-50': 'definitely', 'bis-affiliates': 'definitely'})
        steps = self.recs['lei:F1']['OWNERSHIP_PROOF']['bis-affiliates']['proof']
        self.assertTrue([s for s in steps if s['entity'] == 'lei:U1'][0]['out_of_scope'])
        self.assertEqual(self.v('P1'), {'ofac-50': 'definitely'})

    def test_share_basis_is_named(self):
        p = self.recs['lei:C1']['OWNERSHIP_PROOF']['ofac-50']
        self.assertEqual(p['share_basis'], ['accounting_consolidation'])
        self.assertEqual(p['proof'][-1]['via'][0]['basis'], 'accounting_consolidation')
        self.assertEqual(self.recs['lei:C3']['OWNERSHIP_PROOF']['ofac-50']['share_basis'], ['branch'])
        self.assertEqual(self.recs['lei:C2']['OWNERSHIP_PROOF']['ofac-50']['share_basis'], ['accounting_consolidation'])

    def test_split_files(self):
        covered = affiliates.read_jsonl(self.out / 'affiliates.covered.senzing.jsonl')
        review = affiliates.read_jsonl(self.out / 'affiliates.review.senzing.jsonl')
        self.assertEqual(sorted(r['RECORD_ID'] for r in review), ['lei:C2', 'lei:C6', 'lei:C7'])
        self.assertEqual(len(covered) + len(review), len(self.recs))
        for r in covered:
            self.assertEqual(r['VERDICT_CLASS'], 'covered')
            self.assertTrue(set(r['OWNERSHIP_VERDICTS'].values()) & {'definitely', 'possibly'})
        for r in review:
            self.assertEqual(set(r['OWNERSHIP_VERDICTS'].values()), {'control_only'})
        files = self.manifest['files']
        self.assertEqual(files['affiliates.covered.senzing.jsonl']['records'], len(covered))
        body = (self.out / 'affiliates.review.senzing.jsonl').read_bytes()
        self.assertEqual(files['affiliates.review.senzing.jsonl']['sha256'], hashlib.sha256(body).hexdigest())

    def test_release_hash_ignores_the_input_hash(self):
        recs = list(self.recs.values())
        other = [dict(r, INPUTS_SHA256='0' * 64) for r in recs]
        self.assertEqual(affiliates.records_digest(recs), affiliates.records_digest(other))
        changed = [dict(recs[0], OWNERSHIP_VERDICTS={'ofac-50': 'possibly'})] + recs[1:]
        self.assertNotEqual(affiliates.records_digest(recs), affiliates.records_digest(changed))
        m = json.loads((self.out / 'manifest.json').read_text(encoding='utf8'))
        lists_sha = ''.join(m['lists'][k]['sha256'] for k in sorted(m['lists']))
        self.assertEqual(m['content_sha256'],
                         hashlib.sha256((lists_sha + affiliates.records_digest(recs)).encode()).hexdigest())

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
        self.assertEqual(m['affiliates']['records'], 11)
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

    def test_failed_inputs_without_a_previous_feed_fail_loudly_and_write_nothing(self):
        # With no previous feed to carry forward (here: no affiliates.manifest.json in --prev), a failed build exits 2,
        # says so on stderr as a GitHub error annotation, and writes no affiliates file.
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cache, lists, out = _fixtures(d)
            (cache / 'gleif-rr.csv.zip').unlink()
            prev = d / 'prev'
            prev.mkdir()
            (prev / 'affiliates.senzing.jsonl').write_text(json.dumps(self.recs['lei:C1']) + '\n', encoding='utf8')
            err = io.StringIO()
            self.assertEqual(_run(cache, lists, out, prev, stderr=err), 2)
            self.assertIn('::error title=affiliates build failed::FileNotFoundError', err.getvalue())
            self.assertEqual(sorted(p.name for p in out.iterdir() if p.name.startswith('affiliates')), [])

    def test_data_date_unknown_offline_without_metadata(self):
        self.assertIsNone(self.manifest['data_date'])
        self.assertFalse(self.manifest['stale'])


def _meta(cache, kind, publish_date, route='aws-open-data'):
    (cache / f'gleif-{kind}.meta.json').write_text(json.dumps(
        {'route': route, 'url': f'https://example.invalid/{kind}', 'publish_date': publish_date, 'coverage': 'full'}),
        encoding='utf8')


@unittest.skipIf(affiliates is None, 'ownership-rules is not installed')
class RuleStatus(unittest.TestCase):
    def test_rule_info_reports_the_bis_stay(self):
        info = affiliates.rule_info('2026-11-18')
        self.assertEqual(info['ofac-50']['status'], 'in_force')
        self.assertEqual(info['bis-affiliates']['status'], 'stayed')
        self.assertEqual(info['bis-affiliates']['stayed_until'], '2027-01-10')
        self.assertIn('2026-22793', info['bis-affiliates']['stay_cite'])
        self.assertEqual(affiliates.rule_info('2027-01-11')['bis-affiliates'], {'version': '2025-09-30', 'status': 'in_force'})

    def test_the_manifests_carry_the_rules(self):
        with tempfile.TemporaryDirectory() as d:
            cache, lists, _ = _fixtures(_mk(Path(d) / 'b'))
            self.assertEqual(_run(cache, lists, lists), 0)
            am = json.loads((lists / 'affiliates.manifest.json').read_text(encoding='utf8'))
            m = json.loads((lists / 'manifest.json').read_text(encoding='utf8'))
            self.assertEqual(set(am['rules']), set(affiliates.RULES))
            self.assertEqual(m['affiliates']['rules'], am['rules'])


class DataDate(unittest.TestCase):
    def test_manifest_carries_the_gleif_data_date(self):
        from datetime import datetime, timedelta, timezone
        pub = (datetime.now(timezone.utc) - timedelta(hours=7)).strftime('%Y-%m-%dT%H:00:00Z')
        with tempfile.TemporaryDirectory() as d:
            cache, lists, out = _fixtures(Path(d))
            _meta(cache, 'rr', pub)
            _meta(cache, 'lei2', pub)
            self.assertEqual(_run(cache, lists, lists), 0)
            am = json.loads((lists / 'affiliates.manifest.json').read_text(encoding='utf8'))
            self.assertEqual(am['data_date'], pub[:10])
            self.assertEqual(am['data_as_of']['gleif_rr'], pub)
            self.assertEqual(am['gleif_route'], 'aws-open-data')
            self.assertEqual(am['gleif_coverage'], 'full')
            self.assertFalse(am['stale'])
            m = json.loads((lists / 'manifest.json').read_text(encoding='utf8'))
            self.assertEqual(m['affiliates']['data_date'], pub[:10])
            self.assertIn(f'GLEIF data of {pub}', (lists / 'CHANGES.md').read_text(encoding='utf8'))
            # the data day is part of the release decision, so a new day of data makes a new release
            other = affiliates.content_sha256(m, am['records_sha256'], '1999-01-01')
            self.assertNotEqual(m['content_sha256'], other)

    def test_stale_gleif_data_fails(self):
        with tempfile.TemporaryDirectory() as d:
            cache, lists, out = _fixtures(Path(d))
            _meta(cache, 'rr', '2026-01-01T00:00:00Z')
            _meta(cache, 'lei2', '2026-01-01T00:00:00Z')
            err = io.StringIO()
            self.assertEqual(_run(cache, lists, out, stderr=err), 2)
            self.assertIn('StaleData', err.getvalue())
            self.assertFalse((out / 'affiliates.senzing.jsonl').exists())


def _mk(p):
    p.mkdir()
    return p


@unittest.skipIf(affiliates is None, 'ownership-rules is not installed')
class StaleCarryForward(unittest.TestCase):
    """GLEIF fails: the lists still publish, and the release carries the previous affiliates marked stale."""

    def test_failed_gleif_carries_the_previous_feed_as_stale(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # yesterday's release: a good build with GLEIF data of 2026-11-16
            cache, lists, _ = _fixtures(_mk(d / 'y'))
            _meta(cache, 'rr', '2026-11-16T08:00:00Z')
            _meta(cache, 'lei2', '2026-11-16T08:00:00Z')
            self.assertEqual(affiliates.main(['--lists', str(lists), '--out', str(lists), '--cache', str(cache),
                                              '--offline', '--max-age-hours', '1e9']), 0)
            prev = lists
            prev_manifest = json.loads((prev / 'manifest.json').read_text(encoding='utf8'))
            # today: the lists build, GLEIF fails
            cache2, lists2, _ = _fixtures(_mk(d / 't'))
            (cache2 / 'gleif-rr.csv.zip').unlink()
            err = io.StringIO()
            self.assertEqual(_run(cache2, lists2, lists2, prev, stderr=err), 0)
            self.assertIn('::warning title=affiliates stale::FileNotFoundError', err.getvalue())
            self.assertNotIn('::error', err.getvalue())
            for name in ('affiliates.senzing.jsonl', 'affiliates.covered.senzing.jsonl',
                         'affiliates.review.senzing.jsonl', 'affiliates.ftm.json'):
                self.assertEqual((lists2 / name).read_bytes(), (prev / name).read_bytes(), name)
            self.assertEqual((lists2 / 'affiliates.changes.jsonl').read_text(encoding='utf8'), '')
            am = json.loads((lists2 / 'affiliates.manifest.json').read_text(encoding='utf8'))
            self.assertIs(am['stale'], True)
            self.assertEqual(am['data_date'], '2026-11-16')
            self.assertEqual(set(am['rules']), set(affiliates.RULES))
            self.assertIn('FileNotFoundError', am['error'])
            m = json.loads((lists2 / 'manifest.json').read_text(encoding='utf8'))
            self.assertIs(m['affiliates']['stale'], True)
            self.assertEqual(m['affiliates']['data_date'], '2026-11-16')
            self.assertIn('FileNotFoundError', m['affiliates']['error'])
            self.assertEqual(m['lists'], json.loads((lists2 / 'manifest.json').read_text(encoding='utf8'))['lists'])
            # same lists, same carried records and data date: no new release just because GLEIF failed
            self.assertEqual(m['content_sha256'], prev_manifest['content_sha256'])
            self.assertIn('STALE', (lists2 / 'CHANGES.md').read_text(encoding='utf8'))
            # a second failed day keeps the original data date and when it went stale
            cache3, lists3, _ = _fixtures(_mk(d / 'u'))
            (cache3 / 'gleif-lei2.csv.zip').unlink()
            self.assertEqual(_run(cache3, lists3, lists3, lists2), 0)
            am3 = json.loads((lists3 / 'affiliates.manifest.json').read_text(encoding='utf8'))
            self.assertEqual(am3['data_date'], '2026-11-16')
            self.assertEqual(am3['stale_since'], am['stale_since'])
            self.assertTrue(am3['stale'])

    def test_stale_gleif_data_carries_too(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cache, lists, _ = _fixtures(_mk(d / 'y'))
            self.assertEqual(_run(cache, lists, lists), 0)
            cache2, lists2, _ = _fixtures(_mk(d / 't'))
            _meta(cache2, 'rr', '2026-01-01T00:00:00Z')
            _meta(cache2, 'lei2', '2026-01-01T00:00:00Z')
            err = io.StringIO()
            self.assertEqual(_run(cache2, lists2, lists2, lists, stderr=err), 0)
            self.assertIn('StaleData', err.getvalue())
            self.assertTrue(json.loads((lists2 / 'affiliates.manifest.json').read_text(encoding='utf8'))['stale'])


class _Resp(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}


def _http_error(url, code):
    import urllib.error
    return urllib.error.HTTPError(url, code, 'Forbidden', {}, io.BytesIO(b'<h1>Forbidden</h1>'))


@unittest.skipIf(affiliates is None, 'ownership-rules is not installed')
class GleifRoutes(unittest.TestCase):
    """The golden copy file server answers 403 (as it has since 2026-11-17); the AWS mirror serves the same file."""

    def _fake(self, calls, storage_status=403):
        import gzip as gz
        rr_csv = 'Relationship.StartNode.NodeID,Relationship.EndNode.NodeID\nA,B\n'.encode()

        def urlopen(req, timeout=None):
            url = req.full_url
            calls.append(url)
            if '/golden-copies/publishes/' in url:
                kind = url.split('/publishes/')[1].split('/')[0]
                return _Resp(json.dumps({'data': {'publish_date': '2026-11-17 16:00:00', 'full_file': {'csv': {
                    'url': f'https://goldencopy.gleif.org/storage/golden-copy-files/x/{kind}.csv.zip',
                    'record_count': 1}}}}).encode())
            if '/storage/' in url:
                if storage_status == 200:
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, 'w') as z:
                        z.writestr('x.csv', rr_csv)
                    return _Resp(buf.getvalue())
                raise _http_error(url, storage_status)
            if 'gleif.s3.eu-central-1.amazonaws.com/?' in url:
                kind = 'rr' if '%2Frr%2F' in url else 'lei'
                return _Resp((f'<ListBucketResult><Contents><Key>data/csv/{kind}/date=2026-11-17/time=08:00/'
                              f'20261117-0800-gleif-goldencopy-{kind}.csv.gz</Key></Contents><Contents><Key>'
                              f'data/csv/{kind}/date=2026-11-17/time=16:00/20261117-1600-gleif-goldencopy-{kind}'
                              f'.csv.gz</Key></Contents></ListBucketResult>').encode())
            if url.endswith('.csv.gz'):
                return _Resp(gz.compress(rr_csv))
            raise AssertionError(url)
        return urlopen

    def test_falls_back_to_the_aws_mirror_on_403(self):
        from unittest import mock
        calls = []
        with tempfile.TemporaryDirectory() as d, mock.patch('urllib.request.urlopen', self._fake(calls)), \
                mock.patch.object(affiliates, 'log'):
            got = affiliates.fetch_gleif(d)
            self.assertEqual({k: m['route'] for k, (_, m) in got.items()}, {'rr': 'aws-open-data', 'lei2': 'aws-open-data'})
            self.assertEqual(got['rr'][1]['publish_date'], '2026-11-17T16:00:00Z')  # the newest of the day
            self.assertTrue(got['lei2'][1]['url'].endswith('/time=16:00/20261117-1600-gleif-goldencopy-lei.csv.gz'))
            with affiliates.open_csv(got['rr'][0]) as fh:
                self.assertEqual(list(csv.DictReader(fh)), [{'Relationship.StartNode.NodeID': 'A',
                                                             'Relationship.EndNode.NodeID': 'B'}])
            self.assertFalse(list(Path(d).glob('*.part')))
        # a 403 is not retried: one request per storage URL
        self.assertEqual(sum('/storage/' in u for u in calls), 1)
        for u in calls:
            self.assertTrue(u.startswith('https://'))

    def test_uses_the_golden_copy_server_when_it_works(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, mock.patch('urllib.request.urlopen', self._fake([], 200)), \
                mock.patch.object(affiliates, 'log'):
            got = affiliates.fetch_gleif(d)
            self.assertEqual(got['rr'][1]['route'], 'golden-copy')
            self.assertEqual(got['rr'][1]['publish_date'], '2026-11-17T16:00:00Z')

    def test_retries_server_errors_then_gives_up(self):
        from unittest import mock
        calls = []

        def urlopen(req, timeout=None):
            calls.append(req.full_url)
            raise _http_error(req.full_url, 503)
        with mock.patch('urllib.request.urlopen', urlopen), mock.patch.object(affiliates.time, 'sleep'), \
                mock.patch.object(affiliates, 'log'):
            with self.assertRaises(affiliates.FetchError):
                affiliates.http_open('https://example.invalid/x', tries=3)
        self.assertEqual(len(calls), 3)

    def test_requests_identify_the_build(self):
        req = affiliates._request('https://example.invalid/')
        self.assertIn('us-restricted-parties', req.get_header('User-agent'))


class _FakeApi:
    publish_date = '2026-11-17T16:00:00Z'
    n = 0

    def __init__(self, records, rels):
        self.records, self.rels, self.paths = records, rels, []

    def first_page(self, path, **params):
        return self.pages(path, **params)

    def pages(self, path, **params):
        self.paths.append((path, params))
        self.n += 1
        if path == 'lei-records':
            if 'filter[lei]' in params:
                want = params['filter[lei]'].split(',')
                return [self.records[l] for l in want if l in self.records]
            q = affiliates.name_key(params['filter[entity.names]'])
            return [r for r in self.records.values()
                    if q in {affiliates.name_key(n) for n in affiliates._lei_row(r)[1:12] if n}]
        lei, kind = path.split('/')[1:]
        return [r for r in self.rels if r['attributes']['relationship']['endNode']['id'] == lei
                and r['_kind'] == kind]


def _api_rec(lei, name, country, other=()):
    return {'attributes': {'lei': lei, 'entity': {
        'legalName': {'name': name}, 'otherNames': [{'name': o} for o in other], 'transliteratedOtherNames': [],
        'legalAddress': {'country': country}, 'headquartersAddress': {'country': country}, 'registeredAs': None,
        'status': 'ACTIVE'}, 'registration': {'status': 'ISSUED'}}}


def _api_rel(child, parent, typ='IS_DIRECTLY_CONSOLIDATED_BY', pct=None):
    q = [{'measurementMethod': 'ACCOUNTING_CONSOLIDATION', 'quantifierAmount': pct, 'quantifierUnits': 'PERCENTAGE'}] \
        if pct is not None else []
    return {'_kind': 'direct-child-relationships', 'attributes': {
        'relationship': {'startNode': {'id': child, 'type': 'LEI'}, 'endNode': {'id': parent, 'type': 'LEI'},
                         'type': typ, 'status': 'ACTIVE', 'quantifiers': q},
        'registration': {'lastUpdateDate': '2026-01-02T00:00:00Z', 'corroborationLevel': 'FULLY_CORROBORATED'}}}


@unittest.skipIf(affiliates is None, 'ownership-rules is not installed')
class GleifApiFallback(unittest.TestCase):
    def test_per_record_files_feed_the_same_build(self):
        api = _FakeApi({'L1': _api_rec('L1', 'ACME BANK PJSC', 'RU'),
                        'C1': _api_rec('C1', 'ACME CAPITAL LIMITED', 'CY'),
                        'C2': _api_rec('C2', 'ACME CAPITAL NOMINEES LIMITED', 'CY'),
                        'O1': _api_rec('O1', 'OMEGA GROUP CORPORATION', 'CN'),
                        'O2': _api_rec('O2', 'OMEGA EUROPE B.V.', 'NL')},
                       [_api_rel('C1', 'L1', pct=100.0), _api_rel('C2', 'C1'), _api_rel('O2', 'O1', pct=60)])
        with tempfile.TemporaryDirectory() as d:
            cache, lists, out = _fixtures(Path(d))
            prev = Path(d) / 'prev'
            prev.mkdir()
            (prev / 'affiliates.senzing.jsonl').write_text(json.dumps({'RECORD_ID': 'lei:C1', 'OWNERSHIP_PROOF': {
                'ofac-50': {'seeds': ['lei:L1'], 'proof': [{'entity': 'lei:C1', 'via': [{'owner': 'lei:L1'}]}]}}}) + '\n',
                encoding='utf8')
            seeds = affiliates.own_seeds(lists) + affiliates.csl_seeds(cache / 'consolidated.json')
            with mock_log():
                got = affiliates.gleif_from_api(seeds, prev, cache, api=api)
            self.assertEqual({k: m['coverage'] for k, (_, m) in got.items()}, {'rr': 'partial', 'lei2': 'partial'})
            self.assertEqual(got['rr'][1]['publish_date'], '2026-11-17T16:00:00Z')
            direct, _, _ = affiliates.gleif_relationships(got['rr'][0])
            self.assertEqual(sorted((p, c, pct) for p, c, _, pct, _, _ in direct),
                             [('C1', 'C2', None), ('L1', 'C1', 100.0), ('O1', 'O2', 60.0)])
            info, by_name, _ = affiliates.gleif_entities(got['lei2'][0], {'C1'}, {affiliates.name_key('Omega Group Corporation')}, set())
            self.assertIn('O1', info)  # found by the 1260H name, not by an LEI
            self.assertEqual(info['C1']['country'], 'CY')
            # the build runs on these files as on the golden copy
            with mock_log():
                seeds, matches, info, edges, results = affiliates.build(lists, cache / 'consolidated.json',
                                                                         got['rr'][0], got['lei2'][0])
            self.assertEqual(results['ofac-50']['lei:C1'].verdict, 'definitely')
            self.assertEqual(results['any-list-50']['lei:O2'].verdict, 'definitely')


def mock_log():
    from unittest import mock
    return mock.patch.object(affiliates, 'log')


# ---------------------------------------------------------------- one company on several lists

MULTI_ENTITIES = [
    ('HW', '华为技术有限公司', 'Huawei Technologies Co., Ltd.', 'hua wei ji shu you xian gong si', 'CN', '914403001922038216'),
    ('HWD', 'HUAWEI TECHNOLOGIES DEUTSCHLAND GMBH', '', '', 'DE', ''),
    ('HK', '杭州海康威视数字技术股份有限公司', 'Hangzhou HIKVISION Digital Technology Co.,Ltd.', '', 'CN', ''),
    ('HKE', 'HIKVISION EUROPE B.V.', '', '', 'NL', ''),
    ('DJ', '深圳市大疆创新科技有限公司', '', 'SZ DJI Technology Co., Ltd.', 'CN', ''),
    ('DJE', 'DJI EUROPE B.V.', '', '', 'NL', ''),
    ('OM', 'OMNI TRADING LLC', '', '', 'AE', ''),
    ('OMS', 'OMNI TRADING SUB LLC', '', '', 'AE', ''),
]
MULTI_RELS = [('HWD', 'HW', '100'), ('HKE', 'HK', '100'), ('DJE', 'DJ', '100'), ('OMS', 'OM', '100')]
EL = 'Entity List (EL) - Bureau of Industry and Security'
SDN = 'Specially Designated Nationals (SDN) - Treasury Department'
MULTI_CSL = {'results': [
    {'source': EL, 'id': 'el-hw', 'name': 'Huawei Technologies Co., Ltd.', 'addresses': [{'country': 'CN'}, {'country': 'HK'}]},
    {'source': EL, 'id': 'el-hk', 'name': 'Hangzhou Hikvision Digital Technology Co., Ltd.', 'alt_names': ['Hikvision'],
     'addresses': [{'country': 'CN'}]},
    {'source': EL, 'id': 'el-dji', 'name': 'DJI', 'addresses': [{'country': 'CN'}],
     'alt_names': ['Shenzhen DJI Innovation Technology Co., Ltd.', 'SZ DJI Technology Co., Ltd.',
                   'Shenzhen DJI Sciences and Technologies Ltd.']},
    # two different SDN entries with one name: still ambiguous, never matched
    {'source': SDN, 'id': 's1', 'name': 'OMNI TRADING LLC', 'type': 'Entity', 'programs': ['SDGT'], 'addresses': [{'country': 'AE'}]},
    {'source': SDN, 'id': 's2', 'name': 'Omni Trading L.L.C.', 'type': 'Entity', 'programs': ['IRAN'], 'addresses': [{'country': 'AE'}]},
]}


def _own(key, rows):
    return ''.join(json.dumps({'DATA_SOURCE': key.upper(), 'RECORD_ID': f'{key}-{i}', 'RECORD_TYPE': 'ORGANIZATION',
                               'NAME_ORG': names[0], 'NAMES': [{'NAME_TYPE': 'PRIMARY' if j == 0 else 'ALIAS', 'NAME_ORG': n}
                                                               for j, n in enumerate(names)], 'RELATIONSHIPS': []}) + '\n'
                   for i, names in enumerate(rows))


@unittest.skipIf(affiliates is None, 'ownership-rules is not installed')
class CompanyOnSeveralLists(unittest.TestCase):
    """Huawei and Hikvision are on the Entity List and the 1260H list (and Section 889); DJI is on the Entity List and
    the 1260H list under different names. The name used to map to two list entries and both were dropped."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cache, lists = d / 'cache', d / 'lists'
        cache.mkdir()
        lists.mkdir()
        _zip(cache / 'gleif-lei2.csv.zip', LEI2_COLS,
             [[lei, n, o, t, c, c, r, 'ACTIVE', 'ISSUED'] for lei, n, o, t, c, r in MULTI_ENTITIES])
        _zip(cache / 'gleif-rr.csv.zip', RR_COLS,
             [[ch, 'LEI', p, 'LEI', 'IS_DIRECTLY_CONSOLIDATED_BY', 'ACTIVE', 'ACCOUNTING_CONSOLIDATION', pct, 'PERCENTAGE']
              + [''] * 12 + ['2026-01-02T00:00:00Z', 'FULLY_CORROBORATED'] for ch, p, pct in MULTI_RELS])
        (cache / 'consolidated.json').write_text(json.dumps(MULTI_CSL), encoding='utf8')
        (lists / 'us_dod_1260h.jsonl').write_text(_own('us_dod_1260h', [
            ['Huawei Technologies Co., Ltd.'], ['Hangzhou Hikvision Digital Technology Co., Ltd.', 'Hikvision'],
            ['Shenzhen DJI Technology Co., Ltd.', 'DJI']]), encoding='utf8')
        (lists / 'us_far_889.jsonl').write_text(_own('us_far_889', [
            ['Huawei Technologies Company'], ['Hangzhou Hikvision Digital Technology Company']]), encoding='utf8')
        (lists / 'manifest.json').write_text(json.dumps({'lists': {'us_dod_1260h': {'sha256': 'ab'}}}), encoding='utf8')
        assert _run(cache, lists, lists) == 0
        cls.recs = {r['RECORD_ID']: r for r in affiliates.read_jsonl(lists / 'affiliates.senzing.jsonl')}
        cls.manifest = json.loads((lists / 'affiliates.manifest.json').read_text(encoding='utf8'))
        cls.seeds = affiliates.own_seeds(lists) + affiliates.csl_seeds(cache / 'consolidated.json')
        with mock_log():
            _, cls.matches, *_ = affiliates.build(lists, cache / 'consolidated.json', cache / 'gleif-rr.csv.zip',
                                                  cache / 'gleif-lei2.csv.zip')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def matched(self, list_code, name):
        s = [s for s in self.seeds if s['list'] == list_code and s['name'] == name][0]
        return self.matches.get(s['src_id'])

    def assert_both_lists(self, parent, child, el_name, h_name):
        self.assertEqual(self.matched('EL', el_name)[0], parent)
        self.assertEqual(self.matched('US_DOD_1260H', h_name)[0], parent)
        r = self.recs[f'lei:{child}']
        self.assertEqual(r['OWNERSHIP_VERDICTS'], {'bis-affiliates': 'definitely', 'any-list-50': 'definitely'})
        bis = r['OWNERSHIP_PROOF']['bis-affiliates']['proof'][0]
        own = r['OWNERSHIP_PROOF']['any-list-50']['proof'][0]
        self.assertEqual((bis['entity'], own['entity']), (f'lei:{parent}', f'lei:{parent}'))
        self.assertIn('EL', bis['lists'])
        self.assertIn('US_DOD_1260H', own['lists'])

    def test_huawei(self):
        self.assert_both_lists('HW', 'HWD', 'Huawei Technologies Co., Ltd.', 'Huawei Technologies Co., Ltd.')
        self.assertEqual(self.matched('EL', 'Huawei Technologies Co., Ltd.')[1], 'exact_name_and_country')
        self.assertEqual(self.matched('US_DOD_1260H', 'Huawei Technologies Co., Ltd.')[1], 'exact_name_and_country')

    def test_hikvision(self):
        self.assert_both_lists('HK', 'HKE', 'Hangzhou Hikvision Digital Technology Co., Ltd.',
                               'Hangzhou Hikvision Digital Technology Co., Ltd.')
        # the Section 889 entry spells it "Company" and shares no exact name with the others or GLEIF
        self.assertIsNone(self.matched('US_FAR_889', 'Hangzhou Hikvision Digital Technology Company'))

    def test_dji(self):
        self.assert_both_lists('DJ', 'DJE', 'DJI', 'Shenzhen DJI Technology Co., Ltd.')
        self.assertEqual(self.matched('EL', 'DJI')[1], 'exact_name_and_country')
        self.assertEqual(self.matched('US_DOD_1260H', 'Shenzhen DJI Technology Co., Ltd.')[1], 'same_party_on_other_list')

    def test_two_entries_of_one_list_with_one_name_stay_ambiguous(self):
        self.assertIsNone(self.matched('SDN', 'OMNI TRADING LLC'))
        self.assertIsNone(self.matched('SDN', 'Omni Trading L.L.C.'))
        self.assertNotIn('lei:OMS', self.recs)

    def test_manifest_counts_both_lists(self):
        by_list = self.manifest['seeds_matched_by_list']
        self.assertEqual(by_list['EL'], 3)
        self.assertEqual(by_list['US_DOD_1260H'], 3)


if __name__ == '__main__':
    unittest.main()
