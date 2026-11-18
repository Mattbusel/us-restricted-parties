"""Offline tests: parsers run against real excerpts of each official source (tests/fixtures)."""
import csv, io, json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

FIX = ROOT / 'tests' / 'fixtures'


class ParseEntry(unittest.TestCase):
    def test_subsidiaries_keep_legal_suffixes(self):
        e = build.parse_entry('BGI Group (and BGI Group subsidiaries: BGI Americas Corporation, BGI Genomics Co., Ltd. (BGI), '
                              'Complete Genomics, Inc., Forensic Genomics International (FGI), GBI Diagnostics, Inc., Innomics, Inc., '
                              'and STomics Americas, Ltd.)')
        self.assertEqual(e['primary'], 'BGI Group')
        self.assertEqual(e['subsidiaries'], ['BGI Americas Corporation', 'BGI Genomics Co., Ltd. (BGI)', 'Complete Genomics, Inc.',
                                             'Forensic Genomics International (FGI)', 'GBI Diagnostics, Inc.', 'Innomics, Inc.',
                                             'STomics Americas, Ltd.'])

    def test_short_name_and_formerly_are_aliases(self):
        self.assertEqual(build.parse_entry('360 Security Technology, Inc. (Qihoo 360)'),
                         dict(primary='360 Security Technology, Inc.', aliases=['Qihoo 360'], subsidiaries=[], includes_affiliates=False))
        e = build.parse_entry('CETC Digital Technology Co., Ltd. (formerly Shanghai East China Computer Co., Ltd.)')
        self.assertEqual(e['aliases'], ['Shanghai East China Computer Co., Ltd.'])

    def test_parenthesized_name_parts_stay(self):
        e = build.parse_entry('China Communications Construction Group (Limited) (CCCG)')
        self.assertEqual((e['primary'], e['aliases']), ('China Communications Construction Group (Limited)', ['CCCG']))
        e = build.parse_entry('China International Marine Containers (Group) Co., Ltd. (CIMC)')
        self.assertEqual((e['primary'], e['aliases']), ('China International Marine Containers (Group) Co., Ltd.', ['CIMC']))

    def test_uflpa_alias_forms(self):
        e = build.parse_entry('Hetian Haolin Hair Accessories Co. Ltd. (and two aliases: Hotan Haolin Hair Accessories; and Hollin Hair Accessories)')
        self.assertEqual(e['aliases'], ['Hotan Haolin Hair Accessories', 'Hollin Hair Accessories'])
        e = build.parse_entry('Hoshine Silicon Industry (Shanshan) Co., Ltd (including one alias: Hesheng Silicon Industry (Shanshan) Co.) and subsidiaries')
        self.assertEqual(e['primary'], 'Hoshine Silicon Industry (Shanshan) Co., Ltd')
        self.assertEqual(e['aliases'], ['Hesheng Silicon Industry (Shanshan) Co.'])
        self.assertTrue(e['includes_affiliates'])
        e = build.parse_entry('Sichuan Jingweida Technology Group Co., Ltd. (also known as Sichuan Mianyang Jingweida Technology Co., Ltd. and '
                              'JWD Technology; and formerly known as Mianyang High-tech Zone Jingweida Technology Co., Ltd.)')
        self.assertEqual(e['aliases'], ['Sichuan Mianyang Jingweida Technology Co., Ltd.', 'JWD Technology',
                                        'Mianyang High-tech Zone Jingweida Technology Co., Ltd.'])
        e = build.parse_entry('Xinjiang Production and Construction Corps (including three aliases: XPCC; Xinjiang Corps; and Bingtuan) '
                              'and its subordinate and affiliated entities')
        self.assertEqual((e['primary'], e['aliases'], e['includes_affiliates']),
                         ('Xinjiang Production and Construction Corps', ['XPCC', 'Xinjiang Corps', 'Bingtuan'], True))


class Sources(unittest.TestCase):
    def test_1260h_notice_with_page_break(self):
        ents, removed = build.parse_1260h_list(build.fr_paragraphs((FIX / 'fr_1260h_excerpt.txt').read_bytes()))
        self.assertEqual(len(ents), 8)
        self.assertIn('Autel Robotics Co., Ltd. (Autel Robotics)', ents)  # indented entry
        avic = next(e for e in ents if e.startswith('Aviation Industry'))
        self.assertTrue(avic.endswith('and Shenyang Aircraft Design Institute)'))  # rejoined across [[Page 35190]]
        subs = build.parse_entry(avic)['subsidiaries']
        self.assertIn('Jiangxi Hongdu Aviation Industry Co., Ltd. (Hongdu Aviation)', subs)
        self.assertIn('AVIC Chengdu Aircraft Co., Ltd. (formerly Zhonghang Electronic Measuring Instruments Company Limited (ZEMIC))', subs)
        norinco = build.parse_entry(next(e for e in ents if e.startswith('China North')))['subsidiaries']
        self.assertIn('Kiekert USA Inc.', norinco)  # source typo "Co., Ltd. . Kiekert"
        self.assertEqual(len(removed), 10)

    def test_1260h_entry_without_blank_line_before_or_after(self):
        # FR Doc 2026-11571, page 35191: COSCO SHIPPING follows the CCTC bullet with no blank line and runs
        # straight into its own bullet; China Unicom follows the CTG bullet the same way.
        raw = ('<pre>The Deputy Secretary of Defense has determined that the following entities qualify:\n\n'
               '    <bullet> CCTC is directly controlled by SASAC (Section \n1260H(g)(2)(B)(i)(I)).\n'
               'China COSCO SHIPPING Corporation Limited (COSCO SHIPPING) (and COSCO \n'
               'SHIPPING subsidiaries: China Ocean Shipping Company Ltd., COSCO \n'
               'SHIPPING (North America) Inc., COSCO SHIPPING Logistics Supply Chain \n'
               'Co., Ltd., Shanghai Ship and Shipping Research)\n'
               '    <bullet> COSCO SHIPPING is directly owned and controlled by SASAC; \n'
               'it officially acts on behalf of the PLA (Section 1260H(g)(2)(B)(i)(I)). COSCO SHIPPING \n'
               'is a military-civil fusion contributor (Section 1260H(g)(3)(A)).\n'
               '    <bullet> CTG is directly owned and controlled by SASAC (Section \n1260H(g)(2)(B)(i)(I)).\n'
               'China United Network Communications Group Co., Ltd. (China Unicom) (and \n'
               'China Unicom subsidiaries: China Unicom (Hong Kong) Limited)\n\n'
               '    <bullet> China Unicom is directly controlled by SASAC (Section \n1260H(g)(2)(B)(i)(I)).\n</pre>')
        ents, _ = build.parse_1260h_list(build.fr_paragraphs(raw.encode()))
        self.assertEqual([e.split(' (')[0] for e in ents],
                         ['China COSCO SHIPPING Corporation Limited', 'China United Network Communications Group Co., Ltd.'])
        self.assertIn('COSCO SHIPPING (North America) Inc.', build.parse_entry(ents[0])['subsidiaries'])

    def test_fcc_named_entities(self):
        page = (FIX / 'fcc_excerpt.html').read_bytes()
        with mock.patch.object(build, 'fetch', return_value=page):
            rows, notes = build.pull_fcc()
        names = [r['text'] for r in rows]
        self.assertIn('Huawei Technologies Company', names)
        self.assertIn('Kaspersky Lab, Inc', names)
        self.assertIn('ComNet (USA) LLC', names)
        self.assertEqual(len(notes['category_entries']), 4)  # routers, drones, ... have no named company

    def test_fcc_category_text_has_no_footnote_markers(self):
        # The full page as archived on 2026-11-07: the power inverter entry carries a pound
        # sign footnote marker that used to end up in the category text.
        page = (FIX / 'fcc_page_2026-11-07.html').read_bytes()
        with mock.patch.object(build, 'fetch', return_value=page):
            rows, notes = build.pull_fcc()
        self.assertIn('Huawei Technologies Company', [r['text'] for r in rows])
        cats = notes['category_entries']
        for c in cats:
            self.assertIsNone(build.FCC_MARKER.search(c['entry']), c['entry'])
            self.assertNotIn('\u00a3', c['entry'])
        inv = next(c for c in cats if c['entry'].startswith('Foreign-produced power inverters'))
        self.assertTrue(inv['entry'].startswith('Foreign-produced power inverters, except power inverters which have'), inv['entry'])
        self.assertTrue(any(f.startswith('For purposes of inclusion of power inverters') for f in inv['footnotes']), inv)
        uas = next(c for c in cats if c['entry'].startswith('Uncrewed aircraft systems'))
        self.assertTrue(any('UAS' in f for f in uas['footnotes']), uas)

    def test_uflpa_rows(self):
        page = (FIX / 'uflpa_excerpt.html').read_bytes() * 6  # the parser insists on at least 50 rows
        with mock.patch.object(build, 'fetch', return_value=page):
            rows, _ = build.pull_uflpa()
        recs = build.build_records('us_dhs_uflpa', rows)
        self.assertEqual(len(recs), 9)  # duplicates across tables merge into one record
        daqo = next(r for r in recs if r['NAME_ORG'].startswith('Xinjiang Daqo'))
        self.assertEqual([n['NAME_TYPE'] for n in daqo['NAMES']], ['PRIMARY', 'ALIAS', 'ALIAS', 'ALIAS'])

    def test_uflpa_federal_register_appendix(self):
        as_of, ents = build.parse_uflpa_appendix((FIX / 'fr_uflpa_excerpt.txt').read_bytes())
        self.assertEqual(as_of, '2026-10-29')
        self.assertEqual(len(ents), 18)
        self.assertIn('Xinjiang Production and Construction Corps (including three aliases: XPCC; Xinjiang Corps; and Bingtuan) '
                      'and its subordinate and affiliated entities', ents)
        xuanda = next(e for e in ents if e.startswith('Inner Mongolia Xuanda'))  # split by [[Page 68164]]
        self.assertEqual(build.parse_entry(xuanda)['aliases'], ['Xuanda Food', 'Wuyuan County Xuanda Cereals, Oils and Foods Co., Ltd.'])
        aihua = next(e for e in ents if e.startswith('Hunan Aihua'))  # trailing period after the parenthesis
        self.assertEqual(build.parse_entry(aihua)['aliases'], ['Aihua Group', 'AiSHi'])


class Records(unittest.TestCase):
    def test_senzing_shape_and_relationships(self):
        rows = [dict(text='Shenzhen DJI Technology Co., Ltd. (DJI) (and DJI subsidiary: Shenzhen Dajiang Baiwang Technology Co., Ltd.)',
                     effective='2026-06-10', doc='2026-11571', url='https://www.federalregister.gov/d/2026-11571')]
        recs = build.build_records('us_dod_1260h', rows)
        dji = next(r for r in recs if r['NAME_ORG'] == 'Shenzhen DJI Technology Co., Ltd.')
        sub = next(r for r in recs if r is not dji)
        self.assertEqual(dji['RECORD_TYPE'], 'ORGANIZATION')
        self.assertEqual(dji['NAMES'], [{'NAME_TYPE': 'PRIMARY', 'NAME_ORG': 'Shenzhen DJI Technology Co., Ltd.'},
                                        {'NAME_TYPE': 'ALIAS', 'NAME_ORG': 'DJI'}])
        self.assertEqual(sub['RELATIONSHIPS'][0], {'REL_POINTER_DOMAIN': 'US_DOD_1260H', 'REL_POINTER_KEY': dji['RECORD_ID'],
                                                   'REL_POINTER_ROLE': 'SUBSIDIARY_OF'})
        self.assertEqual(dji['RELATIONSHIPS'][0]['REL_ANCHOR_KEY'], dji['RECORD_ID'])

    def test_failed_source_keeps_previous_build(self):
        with tempfile.TemporaryDirectory() as d:
            prev, out = Path(d) / 'prev', Path(d) / 'out'
            prev.mkdir()
            kept = {'DATA_SOURCE': 'US_FCC_COVERED', 'RECORD_ID': 'x', 'RECORD_TYPE': 'ORGANIZATION', 'NAME_ORG': 'ZTE Corporation',
                    'NAMES': [{'NAME_TYPE': 'PRIMARY', 'NAME_ORG': 'ZTE Corporation'}], 'LISTED_ON': '2021-03-12'}
            (prev / 'us_fcc_covered.jsonl').write_text(json.dumps(kept) + '\n', encoding='utf8')

            def boom():
                raise RuntimeError('403')
            with mock.patch.object(build, 'pull_1260h', side_effect=boom), mock.patch.object(build, 'pull_889', side_effect=boom), \
                    mock.patch.object(build, 'pull_fcc', side_effect=boom), mock.patch.object(build, 'pull_uflpa', side_effect=boom):
                self.assertEqual(build.main(['--out', str(out), '--prev', str(prev)]), 0)
            m = json.loads((out / 'manifest.json').read_text(encoding='utf8'))
            self.assertEqual(list(m['lists']), ['us_fcc_covered'])
            self.assertIn('us_fcc_covered', m['errors'])
            self.assertEqual(build.read_jsonl(out / 'us_fcc_covered.jsonl'), [kept])
            self.assertEqual((out / 'all.csv').read_text(encoding='utf8').splitlines(),
                             [','.join(build.CSV_COLUMNS), 'x,ZTE Corporation,PRIMARY,US_FCC_COVERED,,2021-03-12,,'])

    def test_all_csv_has_one_row_per_name(self):
        rows = [dict(text='Shenzhen DJI Technology Co., Ltd. (DJI) (and DJI subsidiary: Shenzhen Dajiang Baiwang Technology Co., Ltd.)',
                     effective='2026-06-10', doc='2026-11571', url='https://www.federalregister.gov/d/2026-11571')]
        recs = build.build_records('us_dod_1260h', rows)
        got = list(csv.reader(io.StringIO(build.names_csv(recs))))
        self.assertEqual(got[0], build.CSV_COLUMNS)
        by_name = {r[1]: r for r in got[1:]}
        self.assertEqual(len(got) - 1, sum(len(r['NAMES']) for r in recs))
        self.assertEqual(by_name['DJI'][0], by_name['Shenzhen DJI Technology Co., Ltd.'][0])
        self.assertEqual(by_name['DJI'][2], 'ALIAS')
        self.assertEqual(by_name['Shenzhen Dajiang Baiwang Technology Co., Ltd.'][6], 'Shenzhen DJI Technology Co., Ltd.')


if __name__ == '__main__':
    unittest.main()
