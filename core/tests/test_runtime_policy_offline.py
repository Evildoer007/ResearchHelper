"""Real loopback authentication and runtime-policy checks without external calls."""
import copy
import json
import socket
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from researchhelper.contracts import ContractError
from researchhelper.service import make_server


MODEL = {"endpoint": "https://model-fixture.invalid/v1/chat/completions", "model": "runtime-policy-fixture", "api_key": "private-model-fixture"}
SOURCES = {"ifind": {"access_token": "private-access-fixture", "refresh_token": "private-refresh-fixture"}, "tinyshare": {"token": "private-tiny-fixture"}}


class RuntimePolicyTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / "result" / "acceptance" / "runtime-policy"
        root.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(prefix="loopback-", dir=root)
        self.addCleanup(self.folder.cleanup)
        self.server = None
        self.thread = None
        self.external_attempts = []
        original_connect = socket.socket.connect

        def local_connect(connection, address):
            if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
                self.external_attempts.append(address[0])
                raise AssertionError("External network forbidden in runtime-policy acceptance")
            return original_connect(connection, address)

        guard = patch("socket.socket.connect", local_connect)
        guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.stop_server)
        self.start_server()
        self.assertEqual(self.call("/api/auth/setup", {"admin_password": "admin-runtime-password", "sales_password": "sales-runtime-password"})[0], 200)
        self.admin = self.login("admin", "admin-runtime-password")
        self.sales = self.login("sales", "sales-runtime-password")

    def tearDown(self):
        self.assertEqual(self.external_attempts, [])

    def stop_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(5)
            self.assertFalse(self.thread.is_alive())
            for worker in self.server.agent.workers.values():
                worker.join(5)
                self.assertFalse(worker.is_alive())
            self.server = None

    def start_server(self, configurable=False):
        self.server = make_server(self.folder.name, authentication=True, runtime_configurable=configurable)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.token = self.call("/api/auth/status", use_token=False)[1]["token"]

    def restart(self, configurable=False):
        self.stop_server()
        self.start_server(configurable)

    def call(self, path, payload=None, cookie=None, use_token=True, workspace=False):
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
        if use_token and hasattr(self, "token"):
            headers["X-ResearchHelper-Token"] = self.token
        if workspace:
            headers["X-ResearchHelper-View"] = "workspace-v1"
        request = Request(self.base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), response.headers.get("Set-Cookie")

    def login(self, account, password):
        status, value, cookie = self.call("/api/auth/login", {"account": account, "password": password, "remember": True})
        self.assertEqual(status, 200, value)
        return cookie.split(";")[0]

    def bootstrap(self, cookie=None):
        status, result, _ = self.call("/api/bootstrap", cookie=cookie or self.admin)
        self.assertEqual(status, 200, result)
        return result

    def save_model(self):
        self.assertEqual(self.call("/api/settings/model", MODEL, self.admin)[0], 200)

    def expected_execution(self):
        state = self.bootstrap()["execution"]
        return {key: state[key] for key in ("mode", "policy_version")}

    def assert_mode(self, mode):
        state = self.bootstrap()
        self.assertEqual(state["execution"]["mode"], mode)
        self.assertEqual(state["offline"], mode == "offline")
        self.assertEqual(self.server.agent.offline, mode == "offline")
        self.assertEqual(self.server.agent.collector.offline, mode == "offline")
        self.assertEqual(self.server.agent.model.offline, mode == "offline")

    def test_default_launcher_is_locked_offline_even_after_credentials_saved(self):
        self.assert_mode("offline")
        self.assertFalse(self.bootstrap()["execution"]["configurable"])
        self.save_model()
        self.assertEqual(self.call("/api/settings/data", SOURCES, self.admin)[0], 200)
        status, value, _ = self.call("/api/settings/execution", {"enabled": True, "confirm": True}, self.admin)
        self.assertEqual(status, 400)
        self.assertIn("锁定", value["error"])
        self.assert_mode("offline")
        self.assertFalse(Path(self.folder.name, "execution-policy.json").exists())

    def test_launcher_authority_requires_authentication_and_boolean_flags(self):
        for options in ({"authentication": False, "runtime_configurable": True}, {"authentication": True, "runtime_configurable": "true"}, {"authentication": True, "offline": "false"}):
            with self.subTest(options=options), self.assertRaises(ContractError):
                make_server(self.folder.name, **options)

    def test_only_admin_with_csrf_and_explicit_confirmation_can_enable(self):
        self.restart(configurable=True)
        self.save_model()
        valid = {"enabled": True, "confirm": True}
        self.assertEqual(self.call("/api/settings/execution", valid)[0], 401)
        self.assertEqual(self.call("/api/settings/execution", valid, self.sales)[0], 403)
        self.assertEqual(self.call("/api/settings/execution", valid, self.admin, use_token=False)[0], 400)
        for invalid in ({"enabled": True}, {"enabled": True, "confirm": False}, {"enabled": True, "confirm": 1}, {"enabled": "true", "confirm": True}, {"enabled": True, "confirm": True, "configurable": True}):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.call("/api/settings/execution", invalid, self.admin)[0], 400)
                self.assert_mode("offline")
        self.assertEqual(self.call("/api/settings/execution", valid, self.admin)[0], 200)
        self.assert_mode("live")

    def test_live_direct_lookup_does_not_require_model_configuration(self):
        from tests.market_observation_fixtures import ETF_CLOSES, read_captures

        self.restart(configurable=True)
        for model in ({}, {"endpoint": MODEL["endpoint"]}, {"endpoint": MODEL["endpoint"], "model": MODEL["model"]}):
            self.assertEqual(self.call("/api/settings/model", model, self.admin)[0], 200)
            self.assertFalse(self.bootstrap()["research_readiness"]["model_configured"])
        self.assertEqual(self.call("/api/settings/execution", {"enabled": True, "confirm": True}, self.admin)[0], 200)
        self.assert_mode("live")
        self.assertEqual(self.call("/api/settings/data", {"ifind": {"access_token": SOURCES["ifind"]["access_token"]}}, self.admin)[0], 200)
        day, code = "2026-10-21", "510300.SH"
        snapshot = read_captures(ETF_CLOSES.read_bytes(), grouped=False)["snapshot"]
        original = snapshot["tables"][0]
        close = original["table"]["close"][original["time"].index(day)]

        def reply(task, request, timeout, event):
            params = json.loads(request.data)
            self.assertEqual((params["startdate"], params["enddate"]), (day, day))
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            if request.full_url.endswith("/get_trade_dates"):
                return dict(errorcode=0, tables=dict(time=[day]))
            self.assertTrue(request.full_url.endswith("/cmd_history_quotation"))
            self.assertEqual(params["codes"], code)
            return dict(snapshot, tables=[dict(thscode=code, time=[day], table=dict(close=[close]))])

        def submit(question, entry, expected_status):
            status, row, _ = self.call("/api/tasks", dict(question=question, entry=entry, research_mode="current"), self.admin)
            self.assertEqual(status, 200, row)
            status, result, _ = self.call(f'/api/tasks/{row["id"]}/start',
                dict(expected_execution=self.expected_execution()), self.admin)
            self.assertEqual(status, expected_status, result)
            if status == 200:
                worker = self.server.agent.workers.get(row["id"])
                if worker is not None:
                    worker.join(5)
                    self.assertFalse(worker.is_alive())
            return self.server.agent.archive.get(row["id"]), result

        with patch.object(self.server.agent.collector, "_http_json", side_effect=reply) as provider:
            direct, _ = submit("核对510300.SH在2026年10月21日的收盘价。", "ETF", 200)
            complex_task, blocked = submit("解释白酒行业竞争格局变化，给出投资论点。", "行业", 400)
        self.assertEqual(direct["run_mode"], "direct")
        self.assertTrue(direct.get("delivery"), direct.get("error"))
        self.assertEqual(direct["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(direct["ledger"]["used"]["data_requests"], provider.call_count)
        self.assertEqual([(item["period"], item["raw_value"]) for item in direct["draft_report"]["facts"]], [(day, close)])
        self.assertEqual(blocked["failure_code"], "research_model_required")
        self.assertEqual(complex_task["ledger"]["dispatches"], [])

    def test_save_never_activates_and_bootstrap_never_exposes_secrets(self):
        self.restart(configurable=True)
        self.assertEqual(self.call("/api/settings/data", SOURCES, self.sales)[0], 403)
        self.assertEqual(self.call("/api/settings/model", MODEL, self.sales)[0], 403)
        self.assertEqual(self.call("/api/settings/data", SOURCES, self.admin)[0], 200)
        self.assert_mode("offline")
        self.save_model()
        self.assert_mode("offline")
        admin, sales = self.bootstrap(), self.bootstrap(self.sales)
        self.assertTrue(admin["source_settings"]["ifind"]["has_refresh_token"])
        self.assertTrue(admin["model_settings"]["has_key"])
        self.assertEqual(sales["source_settings"], {})
        self.assertEqual(sales["model_settings"], {})
        serialized = json.dumps([admin, sales])
        for secret in (MODEL["api_key"], SOURCES["ifind"]["access_token"], SOURCES["ifind"]["refresh_token"], SOURCES["tinyshare"]["token"]):
            self.assertNotIn(secret, serialized)
        self.restart(configurable=True)
        self.assert_mode("offline")

    def test_enable_disable_persist_and_locked_launcher_ignores_enabled_policy(self):
        self.restart(configurable=True)
        self.save_model()
        before = self.bootstrap()["execution"]["policy_version"]
        self.assertEqual(self.call("/api/settings/execution", {"enabled": True, "confirm": True}, self.admin)[0], 200)
        enabled_version = self.bootstrap()["execution"]["policy_version"]
        self.assertGreater(enabled_version, before)
        self.assertEqual(stat.S_IMODE(Path(self.folder.name, "execution-policy.json").stat().st_mode), 0o600)
        self.restart(configurable=True)
        self.assert_mode("live")
        self.assertEqual(self.bootstrap()["execution"]["policy_version"], enabled_version)
        self.restart(configurable=False)
        self.assert_mode("offline")
        self.restart(configurable=True)
        self.assert_mode("live")
        self.assertEqual(self.call("/api/settings/execution", {"enabled": False, "confirm": True}, self.admin)[0], 200)
        disabled_version = self.bootstrap()["execution"]["policy_version"]
        self.assertGreater(disabled_version, enabled_version)
        self.restart(configurable=True)
        self.assert_mode("offline")
        self.assertEqual(self.bootstrap()["execution"]["policy_version"], disabled_version)

    def test_running_research_rejects_all_configuration_writes_without_refunding_budget(self):
        self.restart(configurable=True)
        self.save_model()
        self.assertEqual(self.call("/api/settings/data", SOURCES, self.admin)[0], 200)
        status, task, _ = self.call("/api/cases", {"id": "value"}, self.admin)
        self.assertEqual(status, 200)
        task["ledger"]["used"].update(data_requests=3, model_calls=2, seconds=7)
        self.server.agent.archive.save(task)
        started, release = threading.Event(), threading.Event()
        original_step = self.server.agent._step

        def paused_step(task, node, host):
            started.set()
            if not release.wait(5):
                raise AssertionError("Bounded execution barrier expired")
            return original_step(task, node, host)

        with patch.object(self.server.agent, "_step", paused_step):
            try:
                self.assertEqual(self.call("/api/tasks/" + task["id"] + "/start", {"expected_execution": self.expected_execution()}, self.admin)[0], 200)
                self.assertTrue(started.wait(5))
                baseline = copy.deepcopy(self.server.agent.archive.get(task["id"])["ledger"])
                for route, payload in (("model", {**MODEL, "model": "changed-model"}), ("data", {"ifind": {"clear_access_token": True}}), ("execution", {"enabled": True, "confirm": True})):
                    status, failure, _ = self.call("/api/settings/" + route, payload, self.admin)
                    self.assertEqual(status, 400, failure)
                    self.assertTrue(failure.get("error"))
                    self.assertEqual(self.server.agent.archive.get(task["id"])["ledger"], baseline)
                self.assert_mode("offline")
                self.assertEqual(self.call("/api/tasks/" + task["id"] + "/cancel", {}, self.admin)[0], 200)
            finally:
                release.set()
                worker = self.server.agent.workers.get(task["id"])
                if worker is not None:
                    worker.join(5)
        final = self.server.agent.archive.get(task["id"])
        self.assertEqual(final["status"], "已取消")
        self.assertEqual(final["ledger"]["used"]["data_requests"], 3)
        self.assertEqual(final["ledger"]["used"]["model_calls"], 2)
        self.assertGreaterEqual(final["ledger"]["used"]["seconds"], baseline["used"]["seconds"])
        self.assertEqual(self.call("/api/settings/execution", {"enabled": True, "confirm": True}, self.admin)[0], 200)
        self.assertEqual(self.server.agent.archive.get(task["id"])["ledger"], final["ledger"])

    def assert_execution_rejected_without_mutation(self, task_id, expected):
        before = self.server.agent.archive.list()
        workers_before = set(self.server.agent.workers)
        with patch.object(self.server.agent, "start", wraps=self.server.agent.start) as start, \
                patch.object(self.server.agent, "update", wraps=self.server.agent.update) as update, \
                patch.object(self.server.agent, "follow_up", wraps=self.server.agent.follow_up) as follow_up:
            for action in ("start", "resume", "update", "messages"):
                payload = {"message": "不可派发的陈旧追问", "start": True} if action == "messages" else {}
                if expected is not None:
                    payload["expected_execution"] = expected
                status, error, _ = self.call("/api/tasks/" + task_id + "/" + action, payload, self.admin)
                self.assertEqual(status, 400, (action, error))
                self.assertIn("未派发研究", error["error"])
                self.assertEqual(self.server.agent.archive.list(), before)
            start.assert_not_called()
            update.assert_not_called()
            follow_up.assert_not_called()
        self.assertEqual(set(self.server.agent.workers), workers_before)

    def test_missing_or_stale_execution_prevents_every_dispatch_and_mutation(self):
        self.restart(configurable=True)
        status, task, _ = self.call("/api/cases", {"id": "value"}, self.admin)
        self.assertEqual(status, 200)
        current = self.expected_execution()
        for expected in (None, {}, {**current, "policy_version": current["policy_version"] + 1},
                         {**current, "mode": "live"}, {**current, "policy_version": True},
                         {**current, "configurable": True}):
            with self.subTest(expected=expected):
                self.assert_execution_rejected_without_mutation(task["id"], expected)
        before_count = len(self.server.agent.archive.list())
        status, related, _ = self.call("/api/tasks/" + task["id"] + "/messages", {"message": "保存但不启动的关联问题", "start": False}, self.admin)
        self.assertEqual(status, 200, related)
        self.assertEqual(related["status"], "待开始")
        self.assertEqual(related["parent"], task["id"])
        self.assertEqual(related["ledger"]["dispatches"], [])
        self.assertEqual(related["ledger"]["used"], {"data_requests": 0, "model_calls": 0, "seconds": 0})
        self.assertNotIn(related["id"], self.server.agent.workers)
        self.assertEqual(len(self.server.agent.archive.list()), before_count + 1)
        status, started, _ = self.call("/api/tasks/" + task["id"] + "/start", {"expected_execution": current}, self.admin)
        self.assertEqual(status, 200, started)
        self.server.agent.workers[task["id"]].join(5)
        completed = self.server.agent.archive.get(task["id"])
        self.assertEqual(completed["status"], "已完成")
        self.assertEqual(completed["run_mode"], "offline")
        self.assertEqual(completed["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(completed["ledger"]["used"]["data_requests"], 0)
        self.assert_execution_rejected_without_mutation(task["id"], None)

    def test_admin_mode_switch_invalidates_old_version_in_both_directions(self):
        self.restart(configurable=True)
        self.save_model()
        status, task, _ = self.call("/api/cases", {"id": "value"}, self.admin)
        self.assertEqual(status, 200)
        original_offline = self.expected_execution()
        self.assertEqual(self.call("/api/settings/execution", {"enabled": True, "confirm": True}, self.admin)[0], 200)
        online = self.expected_execution()
        self.assertEqual(online["mode"], "live")
        self.assert_execution_rejected_without_mutation(task["id"], original_offline)
        self.assert_execution_rejected_without_mutation(task["id"], None)
        self.assertEqual(self.call("/api/settings/execution", {"enabled": False, "confirm": True}, self.admin)[0], 200)
        self.assert_execution_rejected_without_mutation(task["id"], online)
        self.assert_execution_rejected_without_mutation(task["id"], original_offline)
        self.assert_execution_rejected_without_mutation(task["id"], None)
        self.assertEqual(self.server.agent.archive.get(task["id"])["version"], 1)
        self.assertEqual(self.server.agent.archive.get(task["id"])["ledger"]["dispatches"], [])

    def test_concurrent_mode_change_cannot_reinterpret_offline_start(self):
        self.restart(configurable=True)
        self.save_model()
        status, task, _ = self.call("/api/cases", {"id": "value"}, self.admin)
        self.assertEqual(status, 200)
        expected = self.expected_execution()
        barrier, release = threading.Barrier(2), threading.Event()
        original_step = self.server.agent._step

        def paused_step(task, node, host):
            if not release.wait(5):
                raise AssertionError("Concurrent execution barrier expired")
            return original_step(task, node, host)

        def submit(route, payload):
            barrier.wait(5)
            return self.call(route, payload, self.admin)

        with patch.object(self.server.agent, "_step", paused_step):
            try:
                with ThreadPoolExecutor(max_workers=2) as workers:
                    start_future = workers.submit(submit, "/api/tasks/" + task["id"] + "/start", {"expected_execution": expected})
                    switch_future = workers.submit(submit, "/api/settings/execution", {"enabled": True, "confirm": True})
                    start_result, switch_result = start_future.result(5), switch_future.result(5)
                state = self.server.agent.archive.get(task["id"])
                if start_result[0] == 200:
                    self.assertEqual(switch_result[0], 400)
                    self.assertEqual(state["run_mode"], "offline")
                    self.assert_mode("offline")
                else:
                    self.assertEqual(start_result[0], 400)
                    self.assertIn("未派发研究", start_result[1]["error"])
                    self.assertEqual(switch_result[0], 200)
                    self.assertEqual(state, task)
                    self.assertNotIn(task["id"], self.server.agent.workers)
                    self.assert_mode("live")
                self.assertEqual(state["ledger"]["dispatches"], [])
                self.assertEqual(state["ledger"]["used"]["data_requests"], 0)
                self.assertEqual(state["ledger"]["used"]["model_calls"], 0)
            finally:
                release.set()
                for worker in self.server.agent.workers.values():
                    worker.join(5)

    def test_corrupt_policy_fails_closed_without_replacing_file(self):
        self.stop_server()
        policy = Path(self.folder.name, "execution-policy.json")
        policy.write_text('{"enabled":true,"policy_version":"invalid"}')
        original = policy.read_bytes()
        with self.assertRaises(ContractError):
            make_server(self.folder.name, authentication=True, runtime_configurable=True)
        self.assertEqual(policy.read_bytes(), original)

    def test_running_app_rejects_a_policy_replaced_outside_its_session(self):
        self.restart(configurable=True)
        self.save_model()
        status, task, _ = self.call("/api/tasks", {"question": "fixture task"}, self.admin)
        self.assertEqual(status, 200)
        original = self.expected_execution()
        policy = Path(self.folder.name, "execution-policy.json")
        external = b'{"enabled":true,"policy_version":2}'
        policy.write_bytes(external)
        with patch.object(self.server.agent, "start", side_effect=AssertionError("Research dispatched")) as start:
            for expected in (original, {"mode": "live", "policy_version": 2},
                             {"mode": "offline", "policy_version": 2}):
                status, result, _ = self.call("/api/tasks/" + task["id"] + "/start",
                    {"expected_execution": expected}, self.admin)
                self.assertEqual(status, 400)
                self.assertIn("未派发研究", result["error"])
            status, result, _ = self.call("/api/settings/execution", {"enabled": True, "confirm": True}, self.admin)
            self.assertEqual(status, 400)
            self.assertIn("未保存", result["error"])
            start.assert_not_called()
        self.assertEqual(policy.read_bytes(), external)
        self.assertEqual(self.server.agent.archive.get(task["id"]), task)
        self.assertTrue(self.server.agent.offline)

    def test_cancelled_worker_blocks_configuration_until_it_finishes(self):
        self.restart(configurable=True)
        self.save_model()
        self.assertEqual(self.call("/api/settings/data", SOURCES, self.admin)[0], 200)
        status, task, _ = self.call("/api/cases", {"id": "value"}, self.admin)
        self.assertEqual(status, 200)
        task["ledger"]["used"].update(data_requests=4, model_calls=1, seconds=9)
        self.server.agent.archive.save(task)
        started, release = threading.Event(), threading.Event()
        original_step = self.server.agent._step

        def in_flight_step(task, node, host):
            started.set()
            if not release.wait(5):
                raise AssertionError("Cancelled-worker barrier expired")
            return original_step(task, node, host)

        changes = (("model", {**MODEL, "model": "after-cancel-fixture"}),
                   ("data", {"ifind": {"clear_access_token": True}}),
                   ("execution", {"enabled": True, "confirm": True}))
        with patch.object(self.server.agent, "_step", in_flight_step):
            try:
                self.assertEqual(self.call("/api/tasks/" + task["id"] + "/start",
                                           {"expected_execution": self.expected_execution()}, self.admin)[0], 200)
                self.assertTrue(started.wait(5))
                url = "/api/tasks/" + task["id"]
                code, response, _ = self.call(url + "/cancel", {}, self.admin, workspace=True)
                self.assertEqual(code, 200)
                self.assertTrue(response.get("batch_settling"))
                self.assertTrue(self.call(url, cookie=self.admin, workspace=True)[1].get("batch_settling"))
                self.assertNotIn("batch_settling", self.call(url, cookie=self.admin)[1])
                self.assertTrue(self.server.agent.workers[task["id"]].is_alive())
                cancelled = self.server.agent.archive.get(task["id"])
                self.assertEqual(cancelled["status"], "已取消")
                self.assertNotIn("batch_settling", cancelled)
                self.assertIn("已取消", [node["status"] for node in cancelled["nodes"]])
                self.assertNotIn("运行中", [node["status"] for node in cancelled["nodes"]])
                files_before = {name: Path(self.folder.name, name).read_bytes()
                                for name in ("model-settings.json", "source-settings.json")}
                for route, payload in changes:
                    status, failure, _ = self.call("/api/settings/" + route, payload, self.admin)
                    self.assertEqual(status, 400, failure)
                    self.assertTrue(failure.get("error"))
                    self.assertEqual(self.server.agent.archive.get(task["id"])["ledger"], cancelled["ledger"])
                for name, content in files_before.items():
                    self.assertEqual(Path(self.folder.name, name).read_bytes(), content)
                self.assert_mode("offline")
            finally:
                release.set()
                worker = self.server.agent.workers.get(task["id"])
                if worker is not None:
                    worker.join(5)
                    self.assertFalse(worker.is_alive())
        settled = self.server.agent.archive.get(task["id"])
        refreshed = self.call("/api/tasks/" + task["id"], cookie=self.admin, workspace=True)[1]
        self.assertFalse(refreshed.get("batch_settling"))
        self.assertEqual(refreshed["revision"], settled["revision"])
        self.assertEqual(refreshed["ledger"], settled["ledger"])
        self.assertEqual(settled["status"], "已取消")
        self.assertEqual(settled["ledger"]["used"]["data_requests"], 4)
        self.assertEqual(settled["ledger"]["used"]["model_calls"], 1)
        self.assertGreaterEqual(settled["ledger"]["used"]["seconds"], cancelled["ledger"]["used"]["seconds"])
        for route, payload in changes:
            status, value, _ = self.call("/api/settings/" + route, payload, self.admin)
            self.assertEqual(status, 200, value)
            self.assertEqual(self.server.agent.archive.get(task["id"])["ledger"], settled["ledger"])
        self.assertEqual(self.bootstrap()["model_settings"]["model"], "after-cancel-fixture")
        self.assertFalse(self.bootstrap()["source_settings"]["ifind"]["has_access_token"])
        self.assert_mode("live")
