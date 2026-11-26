"""Exercise inbound serving and outbound denial using only local endpoints."""
import errno
import json
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def denied_outbound(tcp_port, udp_port):
    checks = {}
    for name, kind, port in (('tcp_connect', socket.SOCK_STREAM, tcp_port),
                             ('udp_connect', socket.SOCK_DGRAM, udp_port),
                             ('udp_sendto', socket.SOCK_DGRAM, udp_port)):
        with socket.socket(socket.AF_INET, kind) as connection:
            connection.settimeout(2)
            destination = ('127.0.0.1', port)
            try:
                if name == 'udp_sendto':
                    connection.sendto(b'offline-sandbox-probe', destination)
                else:
                    connection.connect(destination)
            except OSError as error:
                if error.errno not in (errno.EPERM, errno.EACCES):
                    raise AssertionError(f'{name} failed without an OS permission denial') from error
                checks[name] = error.errno
            else:
                raise AssertionError(f'OS network policy allowed {name}')
    return checks


def probe_offline_server(tcp_port, udp_port):
    parent = denied_outbound(tcp_port, udp_port)
    program = ('import json,sys;from tests.harness_release_gate import denied_outbound;'
               'print(json.dumps(denied_outbound(*map(int,sys.argv[1:]))))')
    child = subprocess.run([sys.executable, '-B', '-c', program, str(tcp_port), str(udp_port)],
                           cwd=ROOT, text=True, capture_output=True, timeout=8, check=True)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(5)
        print(json.dumps({'port': listener.getsockname()[1], 'parent': parent,
                          'child': json.loads(child.stdout)}), flush=True)
        peer, _ = listener.accept()
        with peer:
            peer.settimeout(2)
            assert peer.recv(1) == b'Q'
            peer.sendall(b'A')
