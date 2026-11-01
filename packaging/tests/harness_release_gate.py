"""OS-enforced offline evidence required by new Harness candidate builders."""
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

from tests.review_report_reader_release import BASE, ROOT, digest, read, source_inventory

LOCAL_ONLY_SANDBOX = ('(version 1)(allow default)(deny network*)'
    '(allow network-inbound (local ip "localhost:*"))'
    '(allow network-outbound (remote ip "localhost:*"))'
    '(allow network-bind (local ip "localhost:*"))')
PROBE_ADDRESS = '198.51.100.1'  # RFC5737 documentation network, never a supplier.
NESTED_PROFILE_TESTS = {
    'tests.test_publication_guard_release_fixture_offline.PublicationGuardReleaseFixtureTests.test_source_skill_generated_program_keeps_legacy_assertions_and_archive',
    'tests.test_tinyshare_packaging_offline.TinysharePackagingTests.test_staged_physical_bytecode_loader_imports_real_dependencies',
}


def validate_nested_profile_execution(record, now):
    assert record['status'] == 'passed' and record['source_hashes'] == now
    assert set(record['unique_test_ids']) == NESTED_PROFILE_TESTS
    assert record['tests_run'] == len(NESTED_PROFILE_TESTS)
    assert not record['failure_records'] and not record['skipped_test_ids']
    assert record['original_sandbox_commands_unchanged'] and record['source_unchanged']


def denied_external_connections():
    """Require immediate OS denial of both TCP and UDP, not a network timeout."""
    checks = []
    for kind, port in ((socket.SOCK_STREAM, 443), (socket.SOCK_DGRAM, 53)):
        with socket.socket(socket.AF_INET, kind) as connection:
            connection.settimeout(1)
            try:
                if kind == socket.SOCK_STREAM:
                    connection.connect((PROBE_ADDRESS, port))
                else:
                    connection.sendto(b'isolated-policy-probe', (PROBE_ADDRESS, port))
            except OSError as error:
                if error.errno not in (errno.EPERM, errno.EACCES):
                    raise AssertionError('Network timeout/error is not OS isolation proof') from error
                checks.append({'protocol': 'tcp' if kind == socket.SOCK_STREAM else 'udp',
                    'errno': error.errno, 'denied': True})
            else:
                raise AssertionError('OS network policy did not deny external access')
    return checks


def prove_inherited_policy():
    parent = denied_external_connections()
    failures = []
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(2)
        listener.settimeout(5)
        port = listener.getsockname()[1]
        def serve():
            try:
                for _ in range(2):
                    connection, _ = listener.accept()
                    with connection:
                        connection.settimeout(2)
                        assert connection.recv(32) == b'local-fixture'
                        connection.sendall(b'local-fixture-ok')
            except BaseException as error:
                failures.append(str(error))
        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        with socket.create_connection(('127.0.0.1', port), timeout=2) as connection:
            connection.sendall(b'local-fixture')
            assert connection.recv(32) == b'local-fixture-ok'
        program = ('import json,socket;from tests.harness_release_gate import denied_external_connections;'
            'checks=denied_external_connections();'
            f'connection=socket.create_connection(("127.0.0.1",{port}),timeout=2);'
            'connection.sendall(b"local-fixture");assert connection.recv(32)==b"local-fixture-ok";'
            'connection.close();print(json.dumps({"checks":checks,"loopback_allowed":True}))')
        child = subprocess.run([sys.executable, '-B', '-c', program], cwd=ROOT,
                               capture_output=True, text=True, timeout=8)
        worker.join(timeout=6)
        assert child.returncode == 0, child.stderr
        assert not failures and not worker.is_alive(), failures
        descendant = json.loads(child.stdout)
    return {'parent': parent, 'child': descendant['checks'], 'child_loopback_allowed': True,
            'parent_loopback_allowed': True, 'child_finished': True,
            'server_stopped': True, 'probe_address': PROBE_ADDRESS, 'pid': os.getpid()}


def validate_network_gate(record, source, now):
    """Fail closed: a parent monkeypatch or a green-but-stale gate is insufficient."""
    assert record['status'] == 'source_network_accepted_pending_delivery'
    assert record['source_hashes'] == source['source_hashes'] == now
    assert source['status'] == 'source_accepted_pending_delivery'
    assert source['approved_for_staged_build'] and not source['approved_for_installation']
    assert not source['unexpected_records'] and not source['skipped_test_ids']
    assert source['old_tests_unchanged'] and source['source_unchanged']
    assert source['production_unchanged'] and source['closed_research_unchanged']
    assert record['sandbox_profile'] == LOCAL_ONLY_SANDBOX
    assert record['source_process_finished'] and record['source_unchanged']
    assert not record['approved_for_installation'] and not record['full_harness_complete']
    for proof in record['isolation_proofs']:
        assert proof['probe_address'] == PROBE_ADDRESS
        assert all(proof[key] for key in ('parent_loopback_allowed', 'child_loopback_allowed',
            'child_finished', 'server_stopped'))
        for generation in ('parent', 'child'):
            assert {item['protocol'] for item in proof[generation]} == {'tcp', 'udp'}
            assert all(item['denied'] and item['errno'] in (errno.EPERM, errno.EACCES)
                       for item in proof[generation])
    assert len(record['isolation_proofs']) == 2
    validate_nested_profile_execution(record['nested_profile_execution'], now)


def current_gate():
    now = source_inventory()
    candidates = []
    for path in BASE.glob('source-network-*/receipt.json'):
        record = read(path)
        if record.get('status') != 'source_network_accepted_pending_delivery' or record['source_hashes'] != now:
            continue
        source_path = Path(record['source_gate'])
        assert digest(source_path) == record['source_gate_sha256']
        source = read(source_path)
        validate_network_gate(record, source, now)
        assert all(digest(name) == value for name, value in source['protected_files'].items())
        candidates.append((path, record))
    assert candidates, 'Run the current full Harness under OS loopback-only isolation first'
    return max(candidates, key=lambda item: item[0].stat().st_mtime_ns)
