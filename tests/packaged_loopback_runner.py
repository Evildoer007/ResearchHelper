"""Narrow sandbox adapter for private HTTP tests, not research networking."""
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from tests.accept_delivered_ifind_no_data import run_packaged_tests

LOOPBACK_MODULES = frozenset({'test_task_read_snapshot', 'test_workspace_task_view'})
NETWORK_PREFIX = '(version 1)(allow default)(deny network*)'
LOOPBACK_RULES = ('(allow network-inbound (local tcp "localhost:*"))'
                  '(allow network-outbound (remote tcp "localhost:*"))')
SERVER_GUARD = '''
import socket, socketserver
_original_tcp_bind = socketserver.TCPServer.server_bind
def _private_tcp_bind(server):
 if server.address_family != socket.AF_INET or server.server_address[0] != '127.0.0.1':
  raise AssertionError('HTTP test server must bind only IPv4 loopback')
 return _original_tcp_bind(server)
socketserver.TCPServer.server_bind = _private_tcp_bind
'''


def loopback_profile(profile):
    if not profile.startswith(NETWORK_PREFIX) or '(allow network' in profile:
        raise ValueError('Only the unchanged external-network-denying profile may be adapted')
    return NETWORK_PREFIX + LOOPBACK_RULES + profile[len(NETWORK_PREFIX):]


def run(kind, location, root, output, modules, count, *, runner=run_packaged_tests):
    if not modules or any(name.split('.')[0] not in LOOPBACK_MODULES for name in modules):
        raise ValueError('Loopback permission is limited to the registered HTTP view tests')
    original = subprocess.run
    observed = []

    def sandbox(command, *args, **kwargs):
        if (not isinstance(command, list) or command[:2] != ['/usr/bin/sandbox-exec', '-p']
                or command[3:7] != [sys.executable, '-I', '-B', '-c']):
            raise RuntimeError('The HTTP test launcher changed; no permission was added')
        command = list(command)
        command[2] = loopback_profile(command[2])
        command[7] = SERVER_GUARD + command[7]
        observed.append(command[2])
        return original(command, *args, **kwargs)

    with patch.object(subprocess, 'run', sandbox):
        result = runner(kind, location, root, output, modules, count)
    if len(observed) != 1:
        raise RuntimeError('Exactly one scoped sandbox dispatch is required')
    result['network_profile'] = 'loopback_only'
    result['external_network_allowed'] = False
    result['tcp_server_loopback_guard'] = True
    (Path(output) / 'loopback-policy.json').write_text(json.dumps(dict(
        modules=modules, profile=observed[0], external_network_allowed=False,
        tcp_server_loopback_guard=True), indent=2))
    return result


def verify_operating_system_policy():
    program = SERVER_GUARD + '''
import json, socket
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
client=socket.socket();client.connect(listener.getsockname());peer,_=listener.accept()
peer.close();client.close();listener.close()
s=socket.socket();s.settimeout(.5)
try: s.connect(('203.0.113.1',443))
except PermissionError: external_denied=True
else: external_denied=False
finally: s.close()
try: socketserver.TCPServer(('0.0.0.0',0),socketserver.BaseRequestHandler)
except AssertionError: wildcard_guard=True
else: wildcard_guard=False
assert external_denied and wildcard_guard
print(json.dumps({'loopback_roundtrip':True,'external_connection_denied':True,
 'all_interface_server_rejected_by_test_guard':True,'os_wildcard_bind_denial_claimed':False}))
'''
    result = subprocess.run(['/usr/bin/sandbox-exec', '-p', loopback_profile(NETWORK_PREFIX),
        sys.executable, '-I', '-B', '-c', program], capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError('Loopback-only sandbox did not pass its actual OS negative checks')
    return json.loads(result.stdout)
