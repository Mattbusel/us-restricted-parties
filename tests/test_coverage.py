"""us-restricted-parties coverage: offline, against real excerpts of the trade.gov CSL, OFAC's SDN.CSV, this feed's
release and the OpenSanctions index (tests/fixtures/coverage, all taken on 2026-11-20)."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from us_restricted_parties import coverage as cov  # noqa: E402
import us_restricted_parties  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "coverage"
SOURCES = json.loads((FIX / "sources.json").read_text(encoding="utf-8"))
INDEX = json.loads((FIX / "opensanctions_index.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
FIVE = ("dod_1260h", "far_889", "fcc_covered", "dhs_uflpa")


def status(rep):
    return {r.id: r.status for r in rep.results}


class Classify(unittest.TestCase):
    def test_csl_sources(self):
        self.assertEqual(cov.classify_value("Specially Designated Nationals (SDN) - Treasury Department"), "ofac_sdn")
        self.assertEqual(cov.classify_value("Non-SDN Chinese Military-Industrial Complex Companies List (CMIC) - "
                                            "Treasury Department"), "ofac_nonsdn")
        self.assertEqual(cov.classify_value("Sectoral Sanctions Identifications List (SSI) - Treasury Department"),
                         "ofac_nonsdn")
        self.assertEqual(cov.classify_value("Entity List (EL) - Bureau of Industry and Security"), "bis_entity")
        self.assertEqual(cov.classify_value("Military End User (MEU) List - Bureau of Industry and Security"),
                         "bis_meu")
        self.assertEqual(cov.classify_value("Unverified List (UVL) - Bureau of Industry and Security"), "bis_uvl")
        self.assertIsNone(cov.classify_value("Denied Persons List (DPL) - Bureau of Industry and Security"))

    def test_feed_and_opensanctions_names(self):
        self.assertEqual(cov.classify_value("US_DOD_1260H"), "dod_1260h")
        self.assertEqual(cov.classify_value("us_ofac_cons"), "ofac_nonsdn")
        self.assertEqual(cov.classify_value("us_trade_csl"), "csl")
        self.assertEqual(cov.classify_value("UFLPA Entity List"), "dhs_uflpa")
        self.assertEqual(cov.classify_value("https://www.treasury.gov/ofac/downloads/sdn.csv"), "ofac_sdn")
        self.assertIsNone(cov.classify_value("889 Main Street"))

    def test_row_names(self):
        self.assertEqual(cov.row_names({"NAME_ORG": "", "NAMES": [{"NAME_TYPE": "PRIMARY", "NAME_ORG": "Huawei"}]}),
                         ["Huawei"])
        self.assertEqual(cov.row_names({"caption": "", "properties": {"name": ["ZTE Corporation"]}}),
                         ["ZTE Corporation"])
        self.assertEqual(cov.row_names({"NAME_ORG": " ", "NAMES": []}), [])
        self.assertEqual(cov.row_names({"id": "x", "datasets": ["us_dod_chinese_milcorps"]}), [])


class SourceDates(unittest.TestCase):
    def test_feed_manifest(self):
        man = json.loads((FIX / "feed" / "manifest.json").read_text(encoding="utf-8"))
        got = cov.feed_source_dates(man)
        # the WuXi AppTec removal (FR Doc 2026-22840) is the newest 1260H change, not the June base notice
        self.assertEqual(got["dod_1260h"]["updated"], "2026-11-10T00:00:00Z")
        self.assertEqual(got["dhs_uflpa"]["updated"], "2026-10-29T00:00:00Z")
        # FCC categories (power inverters, robotic devices) were added after the newest named company
        self.assertEqual(got["fcc_covered"]["updated"], "2026-07-28T00:00:00Z")
        self.assertFalse(got["biosecure"]["published"])
        self.assertIsNone(got["biosecure"]["updated"])

    def test_csl_sources_used(self):
        csl = json.loads((FIX / "csl" / "consolidated.json").read_text(encoding="utf-8"))
        got = cov.csl_source_dates(csl)
        self.assertEqual(got["bis_meu"]["updated"], "2026-02-20T18:34:52Z")
        self.assertEqual(got["bis_uvl"]["updated"], "2026-02-10T23:02:53Z")
        self.assertEqual(got["csl"]["updated"], "2026-11-19T14:32:00Z")


class Check(unittest.TestCase):
    def run_check(self, *paths, **kw):
        kw.setdefault("now", NOW)
        return cov.check([str(p) for p in paths], SOURCES, **kw)

    def test_everything_present_passes(self):
        rep = self.run_check(FIX / "feed", FIX / "csl")
        st = status(rep)
        for lid in ("ofac_sdn", "ofac_nonsdn", "csl", "bis_entity", "bis_meu", "bis_uvl") + FIVE:
            self.assertEqual(st[lid], "ok", (lid, rep.results))
        self.assertEqual(rep.failed, [])

    def test_csl_alone_misses_the_four_lists_outside_it(self):
        rep = self.run_check(FIX / "csl")
        st = status(rep)
        self.assertEqual({r.id for r in rep.failed}, set(FIVE))
        for lid in FIVE:
            self.assertEqual(st[lid], "missing")
        self.assertEqual(st["biosecure"], "not_published")
        self.assertEqual(st["bis_meu"], "ok")

    def test_stale_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "lists"
            shutil.copytree(FIX / "feed", d)
            man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            for v in man["lists"].values():
                v["data_as_of"] = "2026-11-01T06:00:00Z"
            (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
            rep = self.run_check(d, FIX / "csl")
        st = status(rep)
        self.assertEqual(st["dod_1260h"], "stale")  # removal on 2026-11-10 is not in data from 2026-11-01
        self.assertEqual(st["dhs_uflpa"], "ok")     # UFLPA last changed 2026-10-29
        self.assertIn("2026-11-10", next(r for r in rep.results if r.id == "dod_1260h").reason)

    def test_empty_names_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "index.jsonl"
            # what a loader that reads only the top-level NAME_ORG indexed from a Senzing export that keeps names
            # in NAMES: the records, without a single name
            p.write_text("\n".join(json.dumps({"DATA_SOURCE": "US_DOD_1260H", "RECORD_ID": f"r{i}", "NAME_ORG": ""})
                                   for i in range(3)) + "\n", encoding="utf-8")
            os.utime(p, (NOW.timestamp(), NOW.timestamp()))
            rep = self.run_check(p, FIX / "csl")
        r = next(r for r in rep.results if r.id == "dod_1260h")
        self.assertEqual(r.status, "empty_names")
        self.assertIn("3 of 3 records have no name", r.reason)

    def test_sdn_csv_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            shutil.copy(FIX / "ofac" / "sdn.csv", d / "sdn.csv")
            old = datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()
            os.utime(d / "sdn.csv", (old, old))
            rep = self.run_check(d)
        r = next(r for r in rep.results if r.id == "ofac_sdn")
        self.assertEqual(r.status, "stale")      # OFAC published on 2026-11-19
        self.assertEqual(r.evidence[0].rows, 5)
        self.assertEqual(status(rep)["csl"], "missing")

    def test_config_references_are_declared(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "watchman.yml"
            cfg.write_text(
                "Download:\n"
                "  InitialDataDirectory: ''\n"
                "  IncludedLists: [us_ofac, us_csl]\n"
                "  CSL: https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.json\n"
                "  SDN: https://www.treasury.gov/ofac/downloads/sdn.csv\n", encoding="utf-8")
            rep = self.run_check(cfg)
        st = status(rep)
        self.assertEqual(st["csl"], "declared")
        self.assertEqual(st["ofac_sdn"], "declared")
        self.assertEqual(st["bis_meu"], "declared")  # in the CSL
        self.assertEqual({r.id for r in rep.failed}, set(FIVE))

    def test_tests_and_docs_do_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_lists.py").write_text("UFLPA = 'uflpa entity list'\n", encoding="utf-8")
            (root / "README.md").write_text("We also screen the UFLPA Entity List.\n", encoding="utf-8")
            (root / "app.py").write_text("SDN = 'https://www.treasury.gov/ofac/downloads/sdn.csv'\n", encoding="utf-8")
            rep = self.run_check(root)
            self.assertEqual(status(rep)["dhs_uflpa"], "missing")
            self.assertIn("only in tests or docs", next(r for r in rep.results if r.id == "dhs_uflpa").reason)
            rep2 = self.run_check(root, include_tests=True)
            self.assertEqual(status(rep2)["dhs_uflpa"], "declared")

    def test_opensanctions_sanctions_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "gateway.ts"
            src.write_text("// OpenSanctions\nconst URL = 'https://api.opensanctions.org/match/sanctions';\n",
                           encoding="utf-8")
            rep = self.run_check(src, opensanctions_index=INDEX)
            st = status(rep)
            self.assertEqual(st["dod_1260h"], "ok")
            self.assertEqual(st["dhs_uflpa"], "ok")
            self.assertEqual(st["bis_meu"], "ok")       # us_trade_csl carries the BIS lists
            self.assertEqual(st["fcc_covered"], "missing")  # not in the sanctions collection
            self.assertEqual(st["far_889"], "missing")      # no OpenSanctions dataset at all
            src.write_text("// OpenSanctions\nconst URL = 'https://api.opensanctions.org/match/default';\n",
                           encoding="utf-8")
            rep = self.run_check(src, opensanctions_index=INDEX)
            self.assertEqual({r.id for r in rep.failed}, {"far_889"})

    def test_opensanctions_topics_filter_drops_export_control_and_debarment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gateway.ts").write_text(
                "const url = new URL(`https://api.opensanctions.org/match/${policy.dataset}`);\n", encoding="utf-8")
            (root / "screening.ts").write_text(
                "export const policy = { dataset: 'default', topics: ['sanction', 'sanction.linked'] };\n",
                encoding="utf-8")
            rep = self.run_check(root, opensanctions_index=INDEX)
        st = status(rep)
        self.assertEqual(st["ofac_sdn"], "ok")
        self.assertEqual(st["dhs_uflpa"], "ok")
        for lid in ("bis_meu", "bis_uvl", "bis_entity", "dod_1260h", "fcc_covered"):
            self.assertEqual(st[lid], "missing", lid)
        reason = next(r for r in rep.results if r.id == "dod_1260h").reason
        self.assertIn("topics filter", reason)
        self.assertIn("'debarment'", reason)

    def test_snapshot_in_code_is_dated(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "config.go"
            src.write_text("// RestrictedAddresses were populated from OFAC's 2026-08-28 SDN.XML: "
                           "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML\n"
                           "var RestrictedAddresses = map[string]struct{}{}\n", encoding="utf-8")
            rep = self.run_check(src)
        r = next(r for r in rep.results if r.id == "ofac_sdn")
        self.assertEqual(r.status, "stale")
        self.assertIn("2026-08-28", r.reason)

    def test_change_feed_removals_are_not_empty_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "delta.ndjson"
            p.write_text(json.dumps({"op": "removed", "list": "OFAC-SDN", "entity": None}) + "\n"
                         + json.dumps({"op": "added", "list": "OFAC-SDN", "entity": {"names": ["ACME LLC"]}}) + "\n",
                         encoding="utf-8")
            rep = self.run_check(p)
        r = next(r for r in rep.results if r.id == "ofac_sdn")
        self.assertEqual(r.status, "ok", r.reason)
        self.assertEqual(r.evidence[0].rows, 1)

    def test_skip_and_require(self):
        rep = self.run_check(FIX / "csl", skip=FIVE)
        self.assertEqual(rep.failed, [])
        rep = self.run_check(FIX / "csl", require=["ofac_sdn", "dhs_uflpa"])
        self.assertEqual([r.id for r in rep.failed], ["dhs_uflpa"])
        with self.assertRaises(ValueError):
            self.run_check(FIX / "csl", skip=["nope"])

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_shallow_clone_is_undated(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                       GIT_COMMITTER_EMAIL="t@t", GIT_AUTHOR_DATE="2026-01-05T00:00:00Z",
                       GIT_COMMITTER_DATE="2026-01-05T00:00:00Z")
            shutil.copy(FIX / "ofac" / "sdn.csv", repo / "sdn.csv")
            for cmd in (["git", "init", "-q"], ["git", "add", "sdn.csv"], ["git", "commit", "-qm", "sdn"]):
                subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True)
            rep = self.run_check(repo)
            r = next(r for r in rep.results if r.id == "ofac_sdn")
            self.assertEqual(r.status, "stale")
            self.assertIn("2026-01-05", r.reason)
            self.assertIn("git: last commit", r.reason)
            shallow = Path(tmp) / "shallow"
            subprocess.run(["git", "clone", "-q", "--depth", "1", repo.as_uri(), str(shallow)], check=True,
                           capture_output=True)
            rep = self.run_check(shallow)
            r = next(r for r in rep.results if r.id == "ofac_sdn")
            self.assertEqual(r.status, "stale")
            self.assertIn("shallow", r.reason)


class Cli(unittest.TestCase):
    def cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = us_restricted_parties.main(["coverage", *args])
        return code, buf.getvalue()

    def test_exit_codes_and_json(self):
        code, out = self.cli(str(FIX / "csl"), "--sources", str(FIX / "sources.json"), "--json")
        self.assertEqual(code, 1)
        rep = json.loads(out)
        self.assertFalse(rep["ok"])
        self.assertEqual(set(rep["failed"]), set(FIVE))
        code, out = self.cli(str(FIX / "feed"), str(FIX / "csl"), "--sources", str(FIX / "sources.json"))
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)

    def test_bad_input(self):
        code, _ = self.cli(str(FIX / "nope"), "--sources", str(FIX / "sources.json"))
        self.assertEqual(code, 2)

    def test_github_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            old = os.environ.get("GITHUB_STEP_SUMMARY")
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
            try:
                code, out = self.cli(str(FIX / "csl"), "--sources", str(FIX / "sources.json"), "--github")
            finally:
                if old is None:
                    del os.environ["GITHUB_STEP_SUMMARY"]
                else:
                    os.environ["GITHUB_STEP_SUMMARY"] = old
            self.assertEqual(code, 1)
            self.assertIn("::error title=dhs_uflpa missing::", out)
            self.assertIn("| UFLPA Entity List", summary.read_text(encoding="utf-8"))

    def test_list_ids(self):
        code, out = self.cli("--list-ids")
        self.assertEqual(code, 0)
        for lid in cov.ORDER:
            self.assertIn(lid, out)


if __name__ == "__main__":
    unittest.main()
