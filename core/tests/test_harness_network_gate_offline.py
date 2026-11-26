"""The signed App acceptance policy allows serving but denies new connections."""
import errno
import json
from pathlib import Path
import selectors
import socket
import subprocess
import sys
import unittest

from tests.accept_native_workbench_render import OFFLINE_APP_SERVER_SANDBOX
from tests.harness_release_gate import ROOT


@unittest.skipUnless(sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').is_file(),
                     'macOS package acceptance sandbox required')
class NetworkGateTests(unittest.TestCase):
    def test_server_and_child_cannot_start_connections_but_client_can_reach_server(self):
        program = ('import sys;from tests.harness_release_gate import probe_offline_server;'
                   'probe_offline_server(*map(int,sys.argv[1:]))')
        with socket.socket() as tcp, socket.socket(type=socket.SOCK_DGRAM) as udp:
            tcp.bind(('127.0.0.1', 0))
            tcp.listen(2)
            udp.bind(('127.0.0.1', 0))
            ports = [str(endpoint.getsockname()[1]) for endpoint in (tcp, udp)]
            with subprocess.Popen(['/usr/bin/sandbox-exec', '-p', OFFLINE_APP_SERVER_SANDBOX,
                    sys.executable, '-B', '-c', program, *ports], cwd=ROOT, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) as worker:
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(worker.stdout, selectors.EVENT_READ)
                        self.assertTrue(selector.select(12), 'offline server did not become ready')
                    ready = worker.stdout.readline()
                    if not ready:
                        self.fail(worker.communicate(timeout=3)[1])
                    proof = json.loads(ready)
                    for generation in ('parent', 'child'):
                        self.assertEqual(set(proof[generation]), {'tcp_connect', 'udp_connect', 'udp_sendto'})
                        self.assertTrue(set(proof[generation].values()) <= {errno.EPERM, errno.EACCES})
                    with socket.create_connection(('127.0.0.1', proof['port']), timeout=3) as client:
                        client.sendall(b'Q')
                        self.assertEqual(client.recv(1), b'A')
                    self.assertEqual(worker.wait(timeout=3), 0)
                finally:
                    if worker.poll() is None:
                        worker.kill()
