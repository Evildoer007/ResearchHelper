"""Explicit Skill source execution using fabricated transports and isolated stores."""

import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.evidence import EvidenceCollector
from researchhelper.settings import DataSettings


ROOT = Path(__file__).resolve().parents[2]
ACCESS = "skill-fabricated-access-only"
REFRESH = "skill-fabricated-refresh-only"
OTHER = "different-store-fabricated-secret"
PARAMETERS = {"codes": "600000.SH", "indicators": "close", "startdate": "2026-09-01",
              "enddate": "2026-09-02", "functionpara": {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}}


def response(value):
    return io.BytesIO(json.dumps(value).encode())


class SkillSourceExecutionTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "result").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="skill-source-test-", dir=ROOT / "result")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.installation = self.root / "installation"
        # This parent transport test reuses the imported runtime. The
        # standalone package acceptance covers the bundled runtime itself.
        target = self.installation / "core/session.py"
        target.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "core/session.py", target)
        shutil.copytree(ROOT / "products/app/frontend", self.installation / "app/web")
        shutil.copytree(ROOT / "modules/visualrenderer/assets", self.installation / "app/web/report-design")
        shutil.copytree(ROOT / "assets/vendor", self.installation / "assets/vendor")
        specification = importlib.util.spec_from_file_location("isolated_skill_source_audit", target)
        self.module = importlib.util.module_from_spec(specification)
        original_path, original_bytecode = list(sys.path), sys.dont_write_bytecode
        try:
            specification.loader.exec_module(self.module)
        finally:
            sys.path[:] = original_path
            sys.dont_write_bytecode = original_bytecode
        network = patch("socket.socket.connect", side_effect=AssertionError("Real network forbidden"))
        network.start()
        self.addCleanup(network.stop)
        model = patch("researchhelper.model.ModelService.reason", side_effect=AssertionError("Skill must use its host agent, not a second model"))
        self.model = model.start()
        self.addCleanup(model.stop)
        self.before = self.files(self.installation)

    @staticmethod
    def files(directory):
        return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in directory.rglob("*") if path.is_file()}

    def tearDown(self):
        self.model.assert_not_called()
        self.assertEqual(self.files(self.installation), self.before)

    def run_source(self, session, limits=None):
        self.addCleanup(session.agent.close_runtime)
        task = session.create("核验指定证券的历史收盘数据", objects=["600000.SH"], cutoff="2026-09-03T18:00:00+08:00",
                              questions=[{"id": "q1", "question": "指定交易日收盘价是多少", "kind": "fact"}],
                              limits=limits)
        collector = session.agent.collector
        # Use the real request validator and budget. Only this fixture's
        # HTTP seam stays in the socket-guarded process.
        with patch.object(collector, "_http_json", side_effect=lambda *args: EvidenceCollector._http_json(collector, *args)):
            return session.run(task["id"], data_requests=[{
                "id": "daily_close", "source": "ifind", "operation": "cmd_history_quotation",
                "parameters": copy.deepcopy(PARAMETERS), "question_ids": ["q1"],
            }])

    @staticmethod
    def replies():
        return [response({"errorcode": 0, "data": {"access_token": ACCESS, "refresh_token": REFRESH}}),
                response({"errorcode": 0, "tables": [{"thscode": "600000.SH", "time": ["2026-09-01"],
                                                     "table": {"close": [10]}}],
                          "note": ACCESS + " " + REFRESH, "access_token": ACCESS})]

    def test_default_offline_blocks_transport_and_rejects_unapproved_credentials(self):
        with self.assertRaises(ContractError):
            self.module.Session(self.project, source_credentials={"ifind": {"refresh_token": REFRESH}})
        session = self.module.Session(self.project)
        with patch("urllib.request.urlopen", side_effect=AssertionError("Offline must not dispatch")) as transport:
            final = self.run_source(session)
        transport.assert_not_called()
        self.assertTrue(session.agent.offline)
        self.assertIsNone(session.agent.model)
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)
        self.assertEqual(final["ledger"]["used"]["model_calls"], 0)
        self.assertTrue(any("离线" in text for text in final["gaps"]))

    def test_allow_network_requires_exact_boolean(self):
        for flag in ("true", "false", 0, 1, None, [], {}):
            with self.subTest(flag=flag), self.assertRaises(ContractError):
                self.module.Session(self.project, allow_network=flag)

    @unittest.skipUnless(os.name == 'posix', 'Requires local symlink support')
    def test_store_paths_stay_in_the_selected_project(self):
        self.project.mkdir()
        outside = self.root / 'another-project'
        outside.mkdir()
        for destination in (self.installation, outside):
            data = self.project / 'data'
            data.symlink_to(destination, target_is_directory=True)
            try:
                with self.subTest(destination=destination.name), self.assertRaises(ContractError):
                    self.module.Session(self.project)
                self.assertFalse((destination / 'researchhelper').exists())
            finally:
                data.unlink()

    @unittest.skipUnless(os.name == 'posix', 'Requires local symlink support')
    def test_archive_rejects_linked_database_files(self):
        database = self.project / 'data/researchhelper/researchhelper.sqlite3'
        database.parent.mkdir(parents=True)
        outside = ResearchArchive(self.root / 'another-project')
        original = outside.path.read_bytes()
        for suffix in ('', '-journal', '-wal', '-shm'):
            alias = database.with_name(database.name + suffix)
            alias.symlink_to(outside.path)
            try:
                with self.subTest(suffix=suffix), self.assertRaises(ContractError):
                    self.module.Session(self.project)
                self.assertEqual(outside.path.read_bytes(), original)
                if suffix:
                    self.assertFalse(database.exists())
            finally:
                alias.unlink()

    @unittest.skipUnless(os.name == 'posix', 'Requires local symlink support')
    def test_export_path_is_checked_again_after_research_has_saved(self):
        session = self.module.Session(self.project)
        self.addCleanup(session.agent.close_runtime)
        done = session.agent.start(session.agent.create_case('mechanism')['id'])
        ident = done['id']
        self.assertTrue(done['delivery'])
        report_directory = self.project / 'result/researchhelper' / ident
        report_directory.parent.mkdir(parents=True, exist_ok=True)
        report_directory.symlink_to(self.installation, target_is_directory=True)
        for options in ({}, {'profile': 'detailed'}):
            with self.subTest(options=options), self.assertRaises(ContractError):
                session.export(ident, **options)
        self.assertEqual(session.report_history(ident)[0]['version'], 1)
        report_directory.unlink()
        for options in ({}, {'profile': 'detailed'}):
            path = session.export(ident, **options)
            self.assertTrue(path.resolve().is_relative_to(report_directory) and path.is_file())

    def test_explicit_refresh_token_two_dispatches_and_no_secret_in_task_or_report(self):
        credentials = {"ifind": {"refresh_token": REFRESH}}
        session = self.module.Session(self.project, allow_network=True, source_credentials=credentials)
        with patch("urllib.request.urlopen", side_effect=self.replies()) as transport:
            final = self.run_source(session)
        self.assertIsNone(session.agent.model)
        self.assertFalse(session.agent.offline)
        self.assertEqual(transport.call_count, 2)
        auth, query = [call.args[0] for call in transport.call_args_list]
        self.assertTrue(auth.full_url.endswith("/get_access_token"))
        self.assertEqual(auth.get_header("Refresh_token"), REFRESH)
        self.assertTrue(query.full_url.endswith("/cmd_history_quotation"))
        self.assertEqual(query.get_header("Access_token"), ACCESS)
        self.assertEqual(final["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(final["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(final["run_mode"], "host")
        self.assertEqual(len(final["source_records"]), 1)
        exported = session.export(final["id"])
        serialized = json.dumps(session.archive.get(final["id"]), ensure_ascii=False) + exported.read_text() + exported.with_suffix(".json").read_text()
        for secret in (ACCESS, REFRESH):
            self.assertNotIn(secret, serialized)
        self.assertFalse((self.project / "data/researchhelper/source-settings.json").exists())

    def test_auth_failure_keeps_spent_budget_and_never_switches_source(self):
        session = self.module.Session(self.project, allow_network=True,
                                      source_credentials={"ifind": {"refresh_token": REFRESH}, "tinyshare": {"token": OTHER}})
        with patch("urllib.request.urlopen", return_value=response({"errorcode": -1, "errmsg": REFRESH})) as transport:
            final = self.run_source(session)
        transport.assert_called_once()
        self.assertTrue(transport.call_args.args[0].full_url.endswith("/get_access_token"))
        self.assertEqual(final["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(final["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(final["source_records"], [])
        self.assertNotIn(REFRESH, json.dumps(final))
        self.assertTrue(any("鉴权" in message for message in final["gaps"]))

    def test_authentication_uses_request_budget_and_does_not_refund(self):
        session = self.module.Session(self.project, allow_network=True, source_credentials={"ifind": {"refresh_token": REFRESH}})
        with patch("urllib.request.urlopen", return_value=response({"errorcode": 0, "data": {"access_token": ACCESS}})) as transport:
            final = self.run_source(session, limits={"data_requests": 1})
        transport.assert_called_once()
        self.assertEqual(final["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(final["source_records"], [])
        self.assertTrue(any("预算" in message for message in final["gaps"]))

    def test_only_project_source_settings_are_read_not_app_installation_or_environment(self):
        DataSettings(self.project / "data/researchhelper").save({"ifind": {"refresh_token": REFRESH}})
        fake_app = self.root / "legacy-app"
        DataSettings(fake_app).save({"ifind": {"refresh_token": OTHER}})
        DataSettings(self.installation).save({"ifind": {"refresh_token": OTHER}})
        self.before = self.files(self.installation)
        reads = []
        original = DataSettings.read

        def tracked(settings, *args, **kwargs):
            reads.append(settings.path.resolve())
            self.assertEqual(settings.path.resolve(), (self.project / "data/researchhelper/source-settings.json").resolve())
            return original(settings, *args, **kwargs)

        with patch.dict(os.environ, {"RESEARCHHELPER_STORE": str(fake_app), "IFIND_REFRESH_TOKEN": OTHER,
                                     "IFIND_ACCESS_TOKEN": OTHER, "TINYSHARE_TOKEN": OTHER}), patch.object(DataSettings, "read", tracked):
            session = self.module.Session(self.project, allow_network=True)
            with patch("urllib.request.urlopen", side_effect=self.replies()) as transport:
                final = self.run_source(session)
        self.assertTrue(reads)
        self.assertEqual(transport.call_args_list[0].args[0].get_header("Refresh_token"), REFRESH)
        self.assertNotIn(OTHER, repr(transport.call_args_list))
        self.assertEqual(final["ledger"]["used"]["data_requests"], 2)

    def test_no_project_credentials_does_not_use_environment_tokens(self):
        with patch.dict(os.environ, {"IFIND_ACCESS_TOKEN": OTHER, "IFIND_REFRESH_TOKEN": OTHER,
                                     "TINYSHARE_TOKEN": OTHER, "TUSHARE_TOKEN": OTHER}):
            session = self.module.Session(self.project, allow_network=True)
            with patch("urllib.request.urlopen", side_effect=AssertionError("No project credential")) as transport:
                final = self.run_source(session)
        transport.assert_not_called()
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)
        self.assertNotIn(OTHER, json.dumps(final))


if __name__ == "__main__":
    unittest.main()
