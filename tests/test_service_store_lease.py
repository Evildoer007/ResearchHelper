"""Bounded local ownership checks, isolated Stores, no supplier/model calls."""
import copy
import http.client
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.runtime import StoreLease
from researchhelper.service import make_server

ROOT = Path(__file__).resolve().parents[1]


class ServiceStoreLeaseTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='rh-service-ownership-')
        self.addCleanup(self.folder.cleanup)
        self.store = Path(self.folder.name).resolve() / 'store'
        self.servers = []
        self.addCleanup(self.close_servers)

    def close_servers(self):
        for server in reversed(self.servers):
            server.server_close()

    def server(self, store=None, **kwargs):
        server = make_server(store or self.store, ROOT, **kwargs)
        self.servers.append(server)
        return server

    def task_running(self, server):
        task = server.agent.create_case('etf')
        task.update(status='运行中', batch=1)
        task['ledger']['used'].update(data_requests=2, model_calls=1, seconds=3.25)
        task['ledger']['dispatches'] = [{'kind':'data_requests', 'status':'已派发', 'fingerprint':'retained-test'}]
        server.agent.archive.save(task)
        return task

    def test_second_service_rejected_before_configuration_or_recovery(self):
        first = self.server()
        task = self.task_running(first)
        before = copy.deepcopy(first.agent.archive.get(task['id']))
        with patch('researchhelper.service.ExecutionPolicy', side_effect=AssertionError('configuration touched')):
            with self.assertRaises(ContractError):
                self.server()
        self.assertEqual(first.agent.archive.get(task['id']), before)

    def test_distinct_stores_are_independent(self):
        first = self.server()
        second = self.server(self.store.parent / 'independent')
        self.assertNotEqual(first.server_port, second.server_port)
        self.assertNotEqual(first.agent.archive.path, second.agent.archive.path)

    @unittest.skipIf(os.name == 'nt', 'macOS symlink path behavior')
    def test_same_store_alias_cannot_bypass_ownership(self):
        self.server()
        alias = self.store.parent / 'alias'
        alias.symlink_to(self.store, target_is_directory=True)
        with self.assertRaises(ContractError):
            self.server(alias)

    def test_graceful_release_recovers_only_after_original_service_closes(self):
        first = self.server()
        task = self.task_running(first)
        first.server_close()
        second = self.server()
        recovered = second.agent.archive.get(task['id'])
        self.assertEqual(recovered['status'], '失败')
        for key in ('ledger', 'batch', 'version', 'cutoff', 'question', 'input_hash'):
            self.assertEqual(recovered[key], task[key])
        self.assertIsNone(recovered['delivery'])

    def test_lock_inode_is_retained_and_file_contains_no_identity_or_secret(self):
        lease = StoreLease(self.store)
        lock = self.store / '.researchhelper-service.lock'
        inode = lock.stat().st_ino
        self.assertIn(lock.read_bytes(), (b'', b'\0'))
        if os.name != 'nt':
            self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
        lease.close()
        lease.close()
        with self.assertRaises(AttributeError):
            lease.stream.fileno()
        next_lease = StoreLease(self.store)
        self.addCleanup(next_lease.close)
        self.assertEqual(lock.stat().st_ino, inode)

    @unittest.skipIf(os.name == 'nt', 'POSIX process death and select on stdout')
    def test_process_death_releases_kernel_lease_without_deleting_file(self):
        self.store.mkdir()
        lock = self.store / '.researchhelper-service.lock'
        # Independent owner uses the same kernel primitive, no development
        # bootstrap. This also works when the tested RH code is frozen.
        code = "import os,fcntl,sys; fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600); fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB); print('owned',flush=True); sys.stdin.read()"
        process = subprocess.Popen([sys.executable, '-I', '-B', '-c', code, str(lock)],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 10)[0])
            self.assertEqual(process.stdout.readline().strip(), 'owned')
            with self.assertRaises(ContractError):
                StoreLease(self.store)
            process.kill()
            process.wait(timeout=10)
            with_lease = StoreLease(self.store)
            self.addCleanup(with_lease.close)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_initialization_failure_does_not_leave_live_lease(self):
        with patch('researchhelper.service._make_server', side_effect=OSError('isolated initialization failure')):
            with self.assertRaises(OSError):
                self.server()
        self.server()

    def test_invalid_policy_does_not_create_store_or_lease(self):
        with self.assertRaises(ContractError):
            self.server(runtime_configurable=True, authentication=False)
        self.assertFalse(self.store.exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX path type checks')
    def test_symlink_or_fifo_lock_is_rejected_without_touching_target(self):
        self.store.mkdir()
        lock = self.store / '.researchhelper-service.lock'
        target = self.store.parent / 'preserved.txt'
        target.write_bytes(b'preserve-original')
        lock.symlink_to(target)
        with self.assertRaises(ContractError):
            StoreLease(self.store)
        self.assertEqual(target.read_bytes(), b'preserve-original')
        lock.unlink()
        os.mkfifo(lock)
        with self.assertRaises(ContractError):
            StoreLease(self.store)

    def test_closing_service_retains_lease_until_worker_finishes(self):
        first = self.server()
        task = first.agent.create_case('etf')
        entered, release = threading.Event(), threading.Event()
        original = first.agent._step

        def delayed(current, node, host):
            if node == 'evidence':
                entered.set()
                if not release.wait(10):
                    raise RuntimeError('bounded worker barrier expired')
            return original(current, node, host)

        with patch.object(first.agent, '_step', side_effect=delayed):
            first.agent.start(task['id'], asynchronous=True)
            self.assertTrue(entered.wait(10))
            try:
                first.server_close()
                with self.assertRaises(ContractError):
                    self.server()
                with self.assertRaises(ContractError):
                    first.agent.start(task['id'])
            finally:
                release.set()
                first.agent.workers[task['id']].join(10)
        self.assertFalse(first.agent.workers[task['id']].is_alive())
        # Settlement runs in its own thread; observe that handle's result,
        # never sleep then presume ownership has been relinquished.
        deadline = time.monotonic() + 5
        while first.runtime_lease.stream is not None and time.monotonic() < deadline:
            with first.runtime_condition:
                first.runtime_condition.wait(.02)
        self.assertIsNone(first.runtime_lease.stream)
        stopped = first.agent.archive.get(task['id'])
        self.assertEqual(stopped['status'], '失败')
        self.assertIsNone(stopped['delivery'])
        self.assertEqual(stopped['ledger']['used']['data_requests'], 0)
        self.assertEqual(stopped['ledger']['used']['model_calls'], 0)
        self.assertEqual(stopped['batch'], 1)
        self.assertEqual(stopped['cutoff'], task['cutoff'])
        reopened = self.server()
        result = reopened.agent.start(task['id'])
        self.assertEqual(result['status'], '已完成')
        self.assertEqual(result['batch'], 2)
        self.assertEqual(result['cutoff'], task['cutoff'])

    def test_closing_service_waits_for_existing_request_ownership(self):
        first = self.server()
        with first.runtime_condition:
            first.runtime_requests = 1
        first.server_close()
        try:
            with self.assertRaises(ContractError):
                self.server()
        finally:
            first._request_settled()
        deadline = time.monotonic() + 5
        while first.runtime_lease.stream is not None and time.monotonic() < deadline:
            with first.runtime_condition:
                first.runtime_condition.wait(.02)
        self.assertIsNone(first.runtime_lease.stream)
        self.server()

    def test_real_loopback_requests_settle_then_closed_service_can_reopen(self):
        first = self.server()
        serving = threading.Thread(target=first.serve_forever, kwargs={'poll_interval':.02})
        serving.start()
        connection = http.client.HTTPConnection('127.0.0.1', first.server_port, timeout=5)
        try:
            connection.request('GET', '/api/bootstrap')
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn(b'"offline": true', response.read())
        finally:
            connection.close()
            first.shutdown()
            serving.join(5)
            first.server_close()
        self.assertFalse(serving.is_alive())
        deadline = time.monotonic() + 5
        with first.runtime_condition:
            self.assertTrue(first.runtime_condition.wait_for(
                lambda:first.runtime_requests == 0, max(0,deadline-time.monotonic())))
        while first.runtime_lease.stream is not None and time.monotonic() < deadline:
            with first.runtime_condition:
                first.runtime_condition.wait(.02)
        self.assertIsNone(first.runtime_lease.stream)
        self.server()


if __name__ == '__main__':
    unittest.main()
