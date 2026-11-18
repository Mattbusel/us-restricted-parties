"""The us_restricted_parties loader (also importable as tools/load.py): offline against a six-record excerpt of a real release (tests/fixtures/
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


class _Server:
    """A local stand-in for github.com: /releases/latest redirects to /releases/tag/<tag>, and
    /releases/download/<tag>/<file> serves files from a directory. Records the requests."""

    def __init__(self, root, tag):
        import http.server
        import threading
        self.root, self.tag, self.hits = Path(root), tag, []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _serve(self, body):
                outer.hits.append(self.path)
                if self.path == '/releases/latest':
                    self.send_response(302)
                    self.send_header('Location', f'https://example.invalid/releases/tag/{outer.tag}')
                    self.end_headers()
                    return
                prefix = '/releases/download/'
                f = outer.root / self.path[len(prefix):] if self.path.startswith(prefix) else None
                if f is None or not f.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                data = f.read_bytes()
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                if body:
                    self.wfile.write(data)

            def do_GET(self):
                self._serve(True)

            def do_HEAD(self):
                self._serve(False)

        self.httpd = http.server.HTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.httpd.server_address[1]}'

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Releases(unittest.TestCase):
    """Pinned and latest releases, hash checks and the cache, against a local server."""

    def setUp(self):
        import hashlib
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'srv'
        self.tag = 'data-2026-11-18'
        far = [line for line in Path(FEED).read_text(encoding='utf-8').splitlines() if '"US_FAR_889"' in line]
        for tag in (self.tag, 'data-2026-11-17'):
            d = self.root / tag
            d.mkdir(parents=True)
            body = ('\n'.join(far) + '\n').encode('utf-8') if tag == self.tag else b''
            (d / 'us_far_889.jsonl').write_bytes(body)
            (d / 'manifest.json').write_text(json.dumps(
                {'lists': {'us_far_889': {'sha256': hashlib.sha256(body).hexdigest()}}}), encoding='utf-8')
        self.srv = _Server(self.root, self.tag)
        self.saved = (load.REPO, load.LATEST, load.RELEASES)
        load.REPO = self.srv.base
        load.LATEST = self.srv.base + '/releases/latest/download/'
        load.RELEASES = self.srv.base + '/releases/download/'
        self.cache = str(Path(self.tmp.name) / 'cache')

    def tearDown(self):
        load.REPO, load.LATEST, load.RELEASES = self.saved
        self.srv.close()
        self.tmp.cleanup()

    def test_latest_release_reads_the_redirect(self):
        self.assertEqual(load.latest_release(), self.tag)
        self.assertEqual(self.srv.hits, ['/releases/latest'])

    def test_verify_without_release_pins_file_and_manifest_to_one_release(self):
        recs = load.load('us_far_889', verify=True)
        self.assertEqual(len(recs), 1)
        self.assertEqual(self.srv.hits, ['/releases/latest', f'/releases/download/{self.tag}/us_far_889.jsonl',
                                         f'/releases/download/{self.tag}/manifest.json'])

    def test_verify_of_a_url_source_reads_the_manifest_next_to_it(self):
        src = f'{self.srv.base}/releases/download/data-2026-11-17/us_far_889.jsonl'
        self.assertEqual(load.load('us_far_889', src, verify=True), [])
        self.assertIn('/releases/download/data-2026-11-17/manifest.json', self.srv.hits)
        self.assertNotIn('/releases/latest', self.srv.hits)

    def test_cache_serves_a_pinned_release_offline(self):
        first = load.load('us_far_889', release=self.tag, verify=True, cache_dir=self.cache)
        n = len(self.srv.hits)
        again = load.load('us_far_889', release=self.tag, verify=True, cache_dir=self.cache)
        self.assertEqual(first, again)
        self.assertEqual(len(self.srv.hits), n)  # file and manifest both from the cache
        self.assertTrue((Path(self.cache) / self.tag / 'us_far_889.jsonl').is_file())

    def test_cache_without_release_is_keyed_by_the_latest_tag(self):
        load.load('us_far_889', cache_dir=self.cache)
        self.assertTrue((Path(self.cache) / self.tag / 'us_far_889.jsonl').is_file())

    def test_a_tampered_cache_fails_verification(self):
        load.load('us_far_889', release=self.tag, verify=True, cache_dir=self.cache)
        (Path(self.cache) / self.tag / 'us_far_889.jsonl').write_bytes(b'{}\n')
        with self.assertRaises(ValueError):
            load.load('us_far_889', release=self.tag, verify=True, cache_dir=self.cache)

    def test_a_hash_mismatch_is_not_cached(self):
        (self.root / self.tag / 'us_far_889.jsonl').write_bytes(b'tampered\n')
        with self.assertRaises(ValueError):
            load.load('us_far_889', release=self.tag, verify=True, cache_dir=self.cache)
        self.assertFalse((Path(self.cache) / self.tag / 'us_far_889.jsonl').exists())

    def test_bad_input_is_rejected_before_any_download(self):
        for bad in ('../x', 'data/2026', '/'):
            with self.assertRaises(ValueError):
                load.url('all', bad)
        with self.assertRaises(ValueError):
            load.load('all', release=self.tag, verify=True)
        self.assertEqual(self.srv.hits, [])

    def test_cli_reports_http_errors_without_a_traceback(self):
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            code = load.main(['--list', 'us_far_889', '--release', 'data-1999-01-01'])
        self.assertEqual(code, 2)
        self.assertIn('404', err.getvalue())

    def test_cli_prints_the_latest_tag(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(load.main(['--latest-release']), 0)
        self.assertEqual(buf.getvalue().strip(), self.tag)


class Package(unittest.TestCase):
    def test_tools_load_is_the_package(self):
        import us_restricted_parties
        self.assertIs(load, us_restricted_parties)
        self.assertTrue(set(us_restricted_parties.__all__) <= set(dir(us_restricted_parties)))


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

    def test_latest_tag_resolves_and_loads_verified(self):
        import tempfile
        tag = load.latest_release()
        self.assertTrue(tag.startswith('data-'), tag)
        with tempfile.TemporaryDirectory() as d:
            recs = load.load('us_far_889', verify=True, cache_dir=d)
            self.assertEqual(recs, load.load('us_far_889', release=tag, verify=True, cache_dir=d))

    def test_a_pinned_release_still_loads(self):
        recs = load.load('us_dod_1260h', release='data-2026-11-17-2241', verify=True)
        self.assertGreater(len(recs), 100)


if __name__ == '__main__':
    unittest.main()
