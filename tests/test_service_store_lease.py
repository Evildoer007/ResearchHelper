"""Bounded local ownership checks, isolated Stores, no supplier/model calls."""
import copy
import http.client
import json
import os
from pathlib import Path
import queue
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, LocalStateError
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

    def test_unavailable_reconnect_port_does_not_settle_task_until_bind_succeeds(self):
        first = self.server()
        task = self.task_running(first)
        archive = first.agent.archive
        original = archive.get(task['id'])
        first.server_close()
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            with self.assertRaises(LocalStateError) as failure:
                self.server(port=port)
            self.assertEqual(failure.exception.code, 'local_listener_unavailable')
            self.assertEqual(archive.get(task['id']), original)
        reopened = self.server(port=port)
        recovered = reopened.agent.archive.get(task['id'])
        self.assertEqual(reopened.server_port, port)
        self.assertEqual(recovered['status'], '失败')
        self.assertEqual(recovered['batch'], original['batch'])
        self.assertEqual(recovered['ledger'], original['ledger'])

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

    def test_parent_pipe_stops_only_desktop_owned_services(self):
        entry = ROOT/'products/app/backend/entry.py'
        for owned in (True, False):
            with self.subTest(owned=owned):
                store = self.store.parent/('desktop' if owned else 'standalone')
                environment = dict(os.environ, RESEARCHHELPER_STORE=str(store))
                environment.pop('RESEARCHHELPER_DESKTOP_LIFETIME', None)
                if owned:
                    environment['RESEARCHHELPER_DESKTOP_LIFETIME'] = 'stdin'
                child = subprocess.Popen([sys.executable, '-IB', str(entry)], cwd=ROOT, env=environment,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                events = queue.Queue()
                reader = threading.Thread(target=lambda: events.put(child.stdout.readline()), daemon=True)
                reader.start()
                try:
                    ready = json.loads(events.get(timeout=10))
                    self.assertTrue(ready['ready'])
                    child.stdin.close()
                    if owned:
                        self.assertEqual(child.wait(timeout=4), 0)
                        lease = StoreLease(store)
                        lease.close()
                    else:
                        address = ready['url'].removeprefix('http://').rstrip('/')
                        connection = http.client.HTTPConnection(address, timeout=3)
                        try:
                            connection.request('GET', '/api/auth/status')
                            self.assertEqual(connection.getresponse().status, 200)
                        finally:
                            connection.close()
                        self.assertIsNone(child.poll())
                finally:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=5)
                    for stream in (child.stdin, child.stdout, child.stderr):
                        stream.close()
                    reader.join(2)

    def test_shutdown_signals_running_work_before_waiting_on_a_local_write(self):
        server = self.server()
        agent = server.agent
        task = agent.create_case('etf')
        task['batch'] = 1
        key = (task['id'], task['version'], task['batch'])
        agent._begin_run_control(*key)
        control = agent.run_controls[key]
        shutdown = threading.Thread(target=server.server_close)
        try:
            with agent.lock:
                shutdown.start()
                self.assertTrue(control.wait(3))
                self.assertFalse(server.runtime_settled.is_set())
                with self.assertRaises(ContractError):
                    agent._check_run_control(task)
                # A start which reached its archive write just as shutdown
                # began still must not dispatch after that write completes.
                task['batch'] += 1
                agent._begin_run_control(task['id'], task['version'], task['batch'])
                with self.assertRaises(ContractError):
                    agent._check_run_control(task)
        finally:
            shutdown.join(5)
        self.assertFalse(shutdown.is_alive())
        self.assertTrue(server.runtime_settled.wait(3))

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
        serving = self.listen(first)
        token = self.token(first)
        entered, release = threading.Event(), threading.Event()
        original = first.agent.archive.save
        outcomes = []

        def delayed(task):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test write did not receive its release signal')
            return original(task)

        def submit():
            connection = http.client.HTTPConnection('127.0.0.1', first.server_port, timeout=5)
            try:
                connection.request('POST', '/api/tasks', body=b'{"question":"valid local write"}',
                    headers={'X-ResearchHelper-Token': token})
                outcomes.append(connection.getresponse().status)
            except ConnectionError:
                outcomes.append('disconnected')
            finally:
                connection.close()

        with patch.object(first.agent.archive, 'save', side_effect=delayed):
            sender = threading.Thread(target=submit)
            sender.start()
            try:
                self.assertTrue(entered.wait(2))
                self.stop_listener(first, serving)
                self.assertFalse(first.runtime_settled.is_set())
                with self.assertRaises(ContractError):
                    self.server()
            finally:
                release.set()
                sender.join(5)
                self.assertFalse(sender.is_alive())
            self.assertTrue(first.runtime_settled.wait(3))
        self.assertEqual(outcomes, ['disconnected'])
        reopened = self.server()
        self.assertEqual([task['question'] for task in reopened.agent.archive.list()], ['valid local write'])

    def listen(self, server):
        serving = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02})
        serving.start()
        self.addCleanup(self.stop_listener, server, serving)
        return serving

    def stop_listener(self, server, serving):
        if serving.is_alive():
            server.shutdown()
        server.server_close()
        serving.join(3)
        self.assertFalse(serving.is_alive())

    def token(self, server):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request('GET', '/api/auth/status')
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            return json.loads(response.read())['token']
        finally:
            connection.close()

    def test_shutdown_releases_store_with_incomplete_headers_and_body(self):
        first = self.server()
        serving = self.listen(first)
        retained = first.agent.create('preserved task')
        host = f'127.0.0.1:{first.server_port}'
        token = self.token(first)
        body = b'{"question":"must not create"}'
        prefix = f'POST /api/tasks HTTP/1.1\r\nHost: {host}\r\n'.encode()
        with (socket.create_connection(('127.0.0.1', first.server_port), timeout=3) as header_peer,
              socket.create_connection(('127.0.0.1', first.server_port), timeout=3) as body_peer):
            header_peer.sendall(prefix)
            body_peer.sendall(prefix + (f'X-ResearchHelper-Token: {token}\r\n'
                f'Content-Length: {len(body) + 8}\r\n\r\n').encode() + body)
            deadline = time.monotonic() + 3
            with first.runtime_condition:
                while len(first.runtime_connections) != 2 and time.monotonic() < deadline:
                    first.runtime_condition.wait(.02)
                self.assertEqual(len(first.runtime_connections), 2)
            self.stop_listener(first, serving)
            self.assertTrue(first.runtime_settled.wait(2))
            reopened = self.server()
            self.assertEqual(reopened.agent.archive.list(), [retained])
            self.assertEqual(header_peer.recv(1), b'')
            self.assertEqual(body_peer.recv(1), b'')

    def test_complete_body_waiting_for_admission_cannot_write_after_shutdown(self):
        first = self.server()
        serving = self.listen(first)
        token = self.token(first)
        entered, release = threading.Event(), threading.Event()
        admit = first.begin_operation

        def delayed_admission():
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test admission did not receive its release signal')
            admit()

        body = b'{"question":"too late to save"}'
        header = (f'POST /api/tasks HTTP/1.1\r\nHost: 127.0.0.1:{first.server_port}\r\n'
                  f'X-ResearchHelper-Token: {token}\r\nContent-Length: {len(body)}\r\n\r\n')
        with (patch.object(first, 'begin_operation', side_effect=delayed_admission),
              socket.create_connection(('127.0.0.1', first.server_port), timeout=3) as peer):
            peer.sendall(header.encode() + body)
            try:
                self.assertTrue(entered.wait(2))
                self.stop_listener(first, serving)
                self.assertFalse(first.runtime_settled.is_set())
            finally:
                release.set()
            self.assertTrue(first.runtime_settled.wait(2))
            self.assertEqual(peer.recv(1), b'')
        self.assertEqual(self.server().agent.archive.list(), [])

    def test_real_loopback_requests_settle_then_closed_service_can_reopen(self):
        first = self.server()
        serving = self.listen(first)
        connection = http.client.HTTPConnection('127.0.0.1', first.server_port, timeout=5)
        try:
            connection.request('GET', '/api/bootstrap')
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn(b'"offline": true', response.read())
        finally:
            connection.close()
            self.stop_listener(first, serving)
        self.assertTrue(first.runtime_settled.wait(3))
        self.assertIsNone(first.runtime_lease.stream)
        self.server()


if __name__ == '__main__':
    unittest.main()
