"""status.py: the heartbeat reports every run, including no-change days, agency failures and failed builds."""
import json, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build, status  # noqa: E402

T0, T1 = '2026-11-17T06:20:00Z', '2026-11-18T06:20:00Z'


def manifest(built_at, errors=None, kept=None):
    errors = errors or {}
    lists = {}
    for k in status.LISTS:
        up = build.fetch_key(k)
        lists[k] = dict(records=10, sha256='h-' + k, latest_listing='2026-06-10',
                        fetched_at=None if up in errors else built_at,
                        data_as_of=(kept or {}).get(k, built_at) if up in errors else built_at, notes={})
    return dict(built_at=built_at, errors=errors, lists=lists, content_sha256='c')


class Heartbeat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dist, self.prev = Path(self.tmp.name, 'dist'), Path(self.tmp.name, 'prev')
        self.dist.mkdir(); self.prev.mkdir()
        (self.prev / 'manifest.json').write_text(json.dumps(manifest(T0)))

    def tearDown(self):
        self.tmp.cleanup()

    def run_hb(self, **kw):
        out = Path(self.tmp.name, 'status.json')
        args = ['--dist', str(self.dist), '--prev', str(self.prev), '--last-release', 'data-2026-11-17',
                '--released', 'false', '--run-url', 'https://example/runs/1', '--job-status', kw.get('job', 'success'),
                '--out', str(out)]
        self.assertEqual(status.main(args), 0)
        return json.loads(out.read_text())

    def test_unchanged_day_is_still_confirmed(self):
        (self.dist / 'manifest.json').write_text(json.dumps(manifest(T1)))
        (self.dist / 'affiliates.manifest.json').write_text(json.dumps(dict(stale=False, data_date='2026-11-18', records=5, sha256='a')))
        hb = self.run_hb()
        self.assertTrue(hb['ok'])
        self.assertFalse(hb['released_this_run'])
        self.assertTrue(hb['lists_unchanged_since_last_release'])
        self.assertEqual(hb['last_release_tag'], 'data-2026-11-17')
        self.assertEqual(hb['run_url'], 'https://example/runs/1')
        for k in status.LISTS:
            l = hb['lists'][k]
            self.assertEqual((l['upstream_ok'], l['fetched_at'], l['data_date'], l['rows']), (True, T1, '2026-11-18', 10))
        self.assertEqual(hb['errors'], {})

    def test_agency_failure_shows_the_same_day(self):
        err = {'us_dhs_uflpa': 'HTTPError: HTTP Error 403: Forbidden'}
        (self.dist / 'manifest.json').write_text(json.dumps(manifest(T1, err, kept={'us_dhs_uflpa': T0})))
        (self.dist / 'affiliates.manifest.json').write_text(json.dumps(dict(stale=False, data_date='2026-11-18')))
        hb = self.run_hb()
        self.assertFalse(hb['ok'])
        u = hb['lists']['us_dhs_uflpa']
        self.assertEqual((u['upstream_ok'], u['fetched_at'], u['data_date']), (False, None, '2026-11-17'))
        self.assertIn('403', u['error'])
        self.assertIn('us_dhs_uflpa', hb['errors'])
        self.assertTrue(hb['lists']['us_far_889']['upstream_ok'])

    def test_1260h_failure_marks_biosecure(self):
        (self.dist / 'manifest.json').write_text(json.dumps(manifest(T1, {'us_dod_1260h': 'URLError: timed out'})))
        (self.dist / 'affiliates.manifest.json').write_text(json.dumps(dict(stale=False, data_date='2026-11-18')))
        hb = self.run_hb()
        self.assertFalse(hb['lists']['us_biosecure']['upstream_ok'])
        self.assertIsNone(hb['lists']['us_biosecure']['fetched_at'])

    def test_stale_affiliates_are_an_error(self):
        (self.dist / 'manifest.json').write_text(json.dumps(manifest(T1)))
        (self.dist / 'affiliates.manifest.json').write_text(json.dumps(dict(stale=True, data_date='2026-11-16', error='GLEIF 403')))
        hb = self.run_hb()
        self.assertFalse(hb['ok'])
        self.assertEqual(hb['affiliates']['data_date'], '2026-11-16')
        self.assertEqual(hb['errors']['affiliates'], 'GLEIF 403')

    def test_failed_build_still_writes_a_heartbeat(self):
        hb = self.run_hb(job='failure')
        self.assertFalse(hb['ok'])
        self.assertFalse(hb['build_ok'])
        self.assertIn('build', hb['errors'])
        l = hb['lists']['us_far_889']
        self.assertEqual((l['upstream_ok'], l['fetched_at'], l['data_date']), (False, None, '2026-11-17'))

    def test_nothing_at_all(self):
        hb = status.heartbeat(Path(self.tmp.name, 'missing'), None, now=T1)
        self.assertFalse(hb['ok'])
        self.assertEqual(hb['generated_at'], T1)
        self.assertEqual(set(hb['lists']), set(status.LISTS))


class DataAsOf(unittest.TestCase):
    def test_kept_list_keeps_its_first_good_date(self):
        prev = dict(lists={'us_fcc_covered': dict(fetched_at=None, data_as_of=T0)})
        self.assertEqual(build.data_as_of('us_fcc_covered', {'us_fcc_covered': 'x'}, prev, T1), T0)
        self.assertEqual(build.data_as_of('us_fcc_covered', {}, prev, T1), T1)
        self.assertEqual(build.data_as_of('us_biosecure', {'us_dod_1260h': 'x'}, dict(lists={'us_biosecure': dict(fetched_at=T0)}), T1), T0)


if __name__ == '__main__':
    unittest.main()
