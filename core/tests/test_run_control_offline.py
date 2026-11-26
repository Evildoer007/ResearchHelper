"""Independent run-control checks; local TLS, SQLite and fabricated tasks only.

Archive gates remain authoritative. Runtime stop signals only keep transport
polling away from slow storage and research locks; no external calls are used.
"""

import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.model import IsolatedModelService
from researchhelper.service import make_server
from tests import test_isolated_model_transport_offline as transport

ROOT = Path(__file__).resolve().parents[2]


class RunControlTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="run-control-", dir=ROOT / "result/acceptance")
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True)

    def test_control_polling_reads_no_archive_and_ignores_research_lock(self):
        task = {"id": "task-a", "version": 1, "batch": 1}
        self.agent._begin_run_control("task-a", 1, 1)
        completed = threading.Event()
        errors = []
        def check():
            try:
                self.agent._check_run_control(task)
            except Exception as error:
                errors.append(error)
            finally:
                completed.set()
        with patch.object(self.agent.archive, "get", side_effect=AssertionError("Archive forbidden")):
            with self.agent.lock:
                worker = threading.Thread(target=check)
                worker.start()
                self.assertTrue(completed.wait(.3), "Control blocked behind research lock")
            worker.join(1)
        self.assertFalse(errors)
        self.assertFalse(worker.is_alive())

    def test_unknown_task_batch_and_finished_run_fail_closed(self):
        self.agent._begin_run_control("task-a", 1, 1)
        for task in ({"id": "task-b", "version": 1, "batch": 1}, {"id": "task-a", "version": 1, "batch": 2}):
            with self.assertRaises(ContractError):
                self.agent._check_run_control(task)
        self.agent._finish_run_control("task-a", 1, 1)
        with self.assertRaises(ContractError):
            self.agent._check_run_control({"id": "task-a", "version": 1, "batch": 1})
        self.assertEqual(self.agent.run_controls, {})

    def test_new_batch_revokes_old_without_old_cleanup_stopping_new(self):
        self.agent._begin_run_control("task-a", 1, 1)
        prior = self.agent.run_controls[("task-a", 1, 1)]
        self.agent._begin_run_control("task-a", 1, 2)
        self.assertTrue(prior.is_set())
        self.agent._finish_run_control("task-a", 1, 1)
        self.agent._check_run_control({"id": "task-a", "version": 1, "batch": 2})
        with self.assertRaises(ContractError):
            self.agent._check_run_control({"id": "task-a", "version": 1, "batch": 1})

    def test_other_task_control_is_unchanged(self):
        for ident in ("task-a", "task-b"):
            self.agent._begin_run_control(ident, 1, 1)
        self.agent._finish_run_control("task-a", 1, 1)
        self.agent._check_run_control({"id": "task-b", "version": 1, "batch": 1})

    def test_completed_fixed_case_releases_control_without_serializing_it(self):
        task = self.agent.create_case("value")
        done = self.agent.start(task["id"])
        self.assertEqual(done["status"], "已完成")
        self.assertEqual(self.agent.run_controls, {})
        self.assertNotIn("run_controls", done)
        self.assertNotIn("control_lock", json.dumps(done))

    def test_failed_initial_archive_read_still_releases_control(self):
        self.agent._begin_run_control("task-a", 1, 1)
        with patch.object(self.agent.archive, "get", side_effect=RuntimeError("Synthetic archive failure")):
            with self.assertRaises(RuntimeError):
                self.agent._run("task-a", 1, None, 1)
        self.assertEqual(self.agent.run_controls, {})

    def test_thread_start_failure_retains_checkpoint_and_clears_control(self):
        task = self.agent.create_case("value")
        with patch("researchhelper.engine.threading.Thread.start", side_effect=RuntimeError("Cannot start")):
            with self.assertRaisesRegex(ContractError, "无法启动"):
                self.agent.start(task["id"], asynchronous=True)
        stored = self.agent.archive.get(task["id"])
        self.assertEqual(stored["status"], "失败")
        self.assertEqual(stored["batch"], 1)
        self.assertEqual(stored["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(self.agent.run_controls, {})
        self.assertNotIn(task["id"], self.agent.workers)
        done = self.agent.start(task["id"])
        self.assertEqual(done["status"], "已完成")
        self.assertEqual(done["batch"], 2)
        self.assertEqual(self.agent.run_controls, {})

    def test_failed_cancel_save_does_not_claim_committed_cancellation(self):
        task = self.agent.create("测试取消保存失败")
        task.update(status="运行中", batch=1)
        self.agent.archive.save(task)
        self.agent._begin_run_control(task["id"], task["version"], 1)
        with patch.object(self.agent.archive, "save", side_effect=OSError("Synthetic save failure")):
            with self.assertRaises(OSError):
                self.agent.cancel(task["id"])
        self.agent._check_run_control(task)
        self.assertEqual(self.agent.archive.get(task["id"])["status"], "运行中")
        self.agent.cancel(task["id"])
        with self.assertRaises(ContractError):
            self.agent._check_run_control(task)

    def test_cancelled_dispatch_keeps_committed_usage_without_spending_unsent_reservations(self):
        from researchhelper.contracts import charge

        for stop in (self.agent.cancel, lambda _ident: self.agent.close_runtime()):
            with self.subTest(stop=stop):
                task = self.agent.create("测试请求登记与取消的顺序")
                task.update(status="运行中", batch=1)
                ledger = task["ledger"]
                for kind in ("data_requests", "model_calls"):
                    charge(ledger, kind)
                    ledger["dispatches"].append(dict(kind=kind, batch=1, status="已派发"))
                self.agent.archive.save(task)
                committed = self.agent.archive.get(task["id"])["ledger"]
                for kind in ("data_requests", "model_calls"):
                    charge(ledger, kind)
                    ledger["dispatches"].append(dict(kind=kind, batch=1, status="已派发"))
                stop(task["id"])
                with self.assertRaisesRegex(ContractError, "任务已停止"):
                    self.agent._journal(task)
                self.assertEqual(self.agent.archive.get(task["id"])["ledger"], committed)
                self.assertEqual(ledger, committed)

    def test_production_model_uses_non_io_guard(self):
        server = make_server(self.agent.archive.root / "server", ROOT, authentication=True, runtime_configurable=True)
        self.addCleanup(server.server_close)
        self.assertIsInstance(server.agent.model, IsolatedModelService)
        self.assertEqual(server.agent.model.active, server.agent._check_run_control)

    def test_reused_batch_in_new_research_version_rejects_old_control(self):
        self.agent._begin_run_control("task-a", 1, 1)
        self.agent._begin_run_control("task-a", 2, 1)
        self.agent._finish_run_control("task-a", 1, 1)
        self.agent._check_run_control({"id": "task-a", "version": 2, "batch": 1})
        with self.assertRaises(ContractError):
            self.agent._check_run_control({"id": "task-a", "version": 1, "batch": 1})

    def test_stale_worker_does_not_fail_or_charge_current_research_version(self):
        task = self.agent.create_case("value")
        task.update(version=2, batch=1, status="运行中")
        self.agent.archive.save(task)
        self.agent._begin_run_control(task["id"], 2, 1)
        before = self.agent.archive.get(task["id"])
        self.agent._run(task["id"], 1, None, 1)
        self.assertEqual(self.agent.archive.get(task["id"]), before)
        self.agent._check_run_control(task)

    def test_synchronous_cancel_settles_before_the_same_task_can_resume(self):
        task = self.agent.create_case("value")
        ident = task["id"]
        entered, release = threading.Event(), threading.Event()
        original_step = self.agent._step
        returned = []

        def wait_in_step(active, node, host):
            if active["batch"] == 1:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("Controlled step did not release")
            return original_step(active, node, host)

        with patch.object(self.agent, "_step", side_effect=wait_in_step):
            worker = threading.Thread(target=lambda: returned.append(self.agent.start(ident)))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                cancelled = self.agent.cancel(ident)
                with self.assertRaisesRegex(ContractError, "旧批次仍在停止"):
                    self.agent.start(ident)
                self.assertEqual(self.agent.archive.get(ident), cancelled)
            finally:
                release.set()
                worker.join(5)
                self.assertFalse(worker.is_alive())
        settled, = returned
        self.assertEqual(settled["status"], "已取消")
        self.assertFalse(settled["delivery"])
        self.assertEqual(settled["nodes"][0]["status"], "已取消")
        self.assertGreater(settled["ledger"]["used"]["seconds"], cancelled["ledger"]["used"]["seconds"])
        resumed = self.agent.start(ident)
        self.assertEqual((resumed["version"], resumed["batch"], resumed["cutoff"]), (1, 2, task["cutoff"]))
        self.assertEqual(resumed["status"], "已完成")
        self.assertGreaterEqual(resumed["ledger"]["used"]["seconds"], settled["ledger"]["used"]["seconds"])

    def test_update_does_not_publish_a_new_version_before_the_previous_worker_exits(self):
        task = self.agent.create_case("value")
        ident = task["id"]
        finished_report, release = threading.Event(), threading.Event()
        original_run = self.agent._run

        def hold_after_delivery(*args):
            result = original_run(*args)
            finished_report.set()
            if not release.wait(5):
                raise AssertionError("Controlled worker did not exit")
            return result

        cutoff = "2027-01-01T00:00:00+08:00"
        with patch.object(self.agent, "_run", side_effect=hold_after_delivery):
            self.agent.start(ident, asynchronous=True)
            try:
                self.assertTrue(finished_report.wait(3))
                before = self.agent.archive.get(ident)
                frozen = self.agent.export(ident, 1)
                with self.assertRaisesRegex(ContractError, "旧批次仍在停止"):
                    self.agent.update(ident, cutoff, asynchronous=True)
                self.assertEqual(self.agent.archive.get(ident), before)
                with self.assertRaisesRegex(ContractError, "旧批次仍在停止"):
                    self.agent.prepare_update(ident, cutoff)
                self.assertEqual(self.agent.export(ident, 1), frozen)
            finally:
                release.set()
                self.agent.workers[ident].join(5)
                self.assertFalse(self.agent.workers[ident].is_alive())
        update = self.agent.prepare_update(ident, cutoff)
        self.assertEqual((update["version"], update["batch"], update["status"]), (2, 0, "待开始"))
        self.assertEqual(self.agent.export(ident, 1), frozen)


class RunControlTransportTests(unittest.TestCase):
    setUpClass = classmethod(transport.IsolatedModelTransportTests.setUpClass.__func__)
    tearDownClass = classmethod(transport.IsolatedModelTransportTests.tearDownClass.__func__)
    setUp = transport.IsolatedModelTransportTests.setUp
    server = transport.IsolatedModelTransportTests.server

    def test_exclusive_sqlite_lock_cannot_hold_network_worker_past_deadline(self):
        entered = self.server("silent")
        archive = ResearchArchive(self.folder / "locked-store")
        agent = ResearchAgent(archive, ROOT, offline=False)
        agent.model = IsolatedModelService(self.settings, offline=False, journal=agent._journal, active=agent._check_run_control)
        agent.model.worker_target = transport.model_fixture_worker
        self.addCleanup(agent.close_runtime)
        agent.model.MAX_WAIT_SECONDS = .8
        references = []
        original = agent.model.reason
        def capture(task, *args, **kwargs):
            references.append(task)
            return original(task, *args, **kwargs)
        agent.model.reason = capture
        task = agent.create("测试网络时限，不生成投资结论", objects=["fixture"])
        agent.start(task["id"], asynchronous=True)
        database = None
        try:
            self.assertTrue(entered.wait(3), "Fixture transport never connected")
            database = sqlite3.connect(archive.path, timeout=1)
            database.execute("BEGIN EXCLUSIVE")
            began = time.monotonic()
            event = references[0]["ledger"]["dispatches"][0]
            stop_flags = ("network_worker_reaped", "transport_sender_stopped", "transport_receiver_stopped")
            while not all(event.get(key) for key in stop_flags) and time.monotonic() - began < 1.4:
                time.sleep(.02)
            self.assertTrue(event.get("network_worker_reaped"), "Archive lock held network controller")
            self.assertEqual(event["failure_code"], "response_deadline_exceeded")
            self.assertTrue(event["transport_sender_stopped"])
            self.assertTrue(event["transport_receiver_stopped"])
            # Persistence is deliberately still blocked: stopping the network
            # must not masquerade as a committed result or waive archive gates.
            self.assertTrue(agent.workers[task["id"]].is_alive())
        finally:
            if database is not None:
                database.rollback()
                database.close()
            agent.workers[task["id"]].join(5)
            self.assertFalse(agent.workers[task["id"]].is_alive())
        stored = archive.get(task["id"])
        self.assertEqual(stored["status"], "失败")
        self.assertEqual(stored["ledger"]["used"]["model_calls"], 1)
        self.assertTrue(stored["ledger"]["dispatches"][0]["network_worker_reaped"])
        self.assertFalse(stored["delivery"])
        self.assertEqual(agent.run_controls, {})

    def test_real_running_cancel_releases_control_and_preserves_ledger(self):
        entered = self.server("silent")
        archive = ResearchArchive(self.folder / "canceled-store")
        agent = ResearchAgent(archive, ROOT, offline=False)
        agent.model = IsolatedModelService(self.settings, offline=False, journal=agent._journal, active=agent._check_run_control)
        agent.model.worker_target = transport.model_fixture_worker
        self.addCleanup(agent.close_runtime)
        task = agent.create("测试研究取消", objects=["fixture"])
        agent.start(task["id"], asynchronous=True)
        try:
            self.assertTrue(entered.wait(3))
            agent.cancel(task["id"])
        finally:
            agent.workers[task["id"]].join(3)
            self.assertFalse(agent.workers[task["id"]].is_alive())
        stored = archive.get(task["id"])
        self.assertEqual(stored["status"], "已取消")
        self.assertEqual(stored["ledger"]["used"]["model_calls"], 1)
        self.assertEqual(stored["ledger"]["dispatches"][0]["failure_code"], "transport_cancelled")
        self.assertEqual(agent.run_controls, {})
        before = stored["ledger"]["used"]["model_calls"]
        # The same research resumes with a new signal and the old ledger.
        resumed = agent.start(task["id"], asynchronous=True)
        try:
            self.assertEqual(resumed["batch"], 2)
            self.assertGreaterEqual(resumed["ledger"]["used"]["model_calls"], before)
            agent.cancel(task["id"])
        finally:
            agent.workers[task["id"]].join(3)
            self.assertFalse(agent.workers[task["id"]].is_alive())
        self.assertEqual(agent.run_controls, {})


if __name__ == "__main__":
    unittest.main()
