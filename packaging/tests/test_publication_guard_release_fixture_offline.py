"""Two source-level checks of the release fixture adapters; no published build."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tests import publication_guard_release_fixture as fixture
from tests import rebuild_preset_authenticated_package_acceptance as http
from researchhelper.service import make_server


class PublicationGuardReleaseFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='anchored-release-fixture-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        connect = socket.socket.connect
        def local_only(connection, address):
            if isinstance(address, tuple) and address[0] not in ('127.0.0.1', 'localhost', '::1'):
                raise AssertionError('External network forbidden')
            return connect(connection, address)
        network = patch('socket.socket.connect', local_only)
        network.start()
        self.addCleanup(network.stop)

    def test_source_skill_generated_program_keeps_legacy_assertions_and_archive(self):
        install = self.directory / 'installation'
        shutil.copytree(fixture.ROOT / 'skill/researchhelper', install)
        shutil.copytree(fixture.ROOT / 'core/src/researchhelper', install / 'researchhelper', ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('app/web/report.css', 'assets/vendor/echarts.min.js'):
            target = install / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fixture.ROOT / name, target)
        before = {str(p.relative_to(install)): fixture.digest(p) for p in install.rglob('*') if p.is_file()}
        project = self.directory / 'project'
        project.mkdir()
        tree = ast.parse((fixture.ROOT / fixture.OLD_HELPERS[0]).read_text())
        program = next(ast.literal_eval(node.value) for node in ast.walk(tree) if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'program' for target in node.targets))
        profile = '(version 1)(allow default)(deny network*)(deny file-write* (subpath ' + json.dumps(str(install)) + '))'
        with fixture.published_fixture_adapter(self.directory / 'adapter') as audit:
            result = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile,
                '/opt/anaconda3/envs/MachineLearning/bin/python', '-I', '-B', '-c', program,
                str(install), str(project), str(fixture.OLD_INPUT)], cwd=project,
                env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(audit['skill']), 1)
        self.assertTrue(audit['skill'][0]['frozen_report_restored'])
        self.assertTrue(audit['skill'][0]['same_input_as_app'])
        self.assertEqual(audit['old_helpers_before'], audit['old_helpers_after'])
        self.assertEqual(before, {str(p.relative_to(install)): fixture.digest(p) for p in install.rglob('*') if p.is_file()})

    def test_authenticated_source_app_transport_uses_same_original_and_freezes_report(self):
        server = make_server(self.directory / 'store', fixture.ROOT, authentication=True, preset_accounts=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        def stop():
            for worker in server.agent.workers.values():
                worker.join(5)
            server.shutdown()
            server.server_close()
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.addCleanup(stop)
        base = 'http://127.0.0.1:' + str(server.server_port)
        status = http.request(base, '/api/auth/status')[0]
        _, headers = http.request(base, '/api/auth/login', token=status['token'],
            payload={'account': 'admin', 'password': '66666666'})
        cookie = headers.get('Set-Cookie').split(';', 1)[0]
        boot = http.request(base, '/api/bootstrap', cookie=cookie)[0]
        self.assertTrue(boot['offline'])
        auth = {'cookie': cookie, 'token': boot['token']}
        audited = []
        request = fixture.adapt_request(http.request, audited)
        legacy = fixture.legacy_input()
        task = request(base, '/api/tasks', payload=legacy['create'], **auth)[0]
        route = '/api/tasks/' + task['id']
        request(base, route + '/start', payload={'host_result': legacy['host_result']}, **auth)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            done = request(base, route, cookie=cookie)[0]
            if done['status'] != '运行中':
                break
            time.sleep(.02)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        owners = {q: n for n in done['nodes'] if n['kind'] in ('answer', 'thesis') for q in n['questions']}
        self.assertEqual(len({owners[q]['id'] for q in ('q1', 'q2', 'q3')}), 3)
        self.assertIn(owners['q1']['id'], owners['q2']['dependencies'])
        self.assertIn(owners['q2']['id'], owners['q3']['dependencies'])
        self.assertTrue(all(q['status'] == '已回答' for q in done['questions']))
        self.assertEqual(done['cutoff'], legacy['create']['cutoff'])
        self.assertIsNone(done['fixture'])
        self.assertFalse(done['synthetic'])
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)
        self.assertEqual(len(done['evidence']), 1)
        self.assertEqual(len(done['materials']), 1)
        self.assertEqual(audited[0]['material_sha256'], hashlib.sha256(fixture.original_bytes()).hexdigest())
        self.assertTrue(audited[0]['answers_unchanged'] and audited[0]['theses_unchanged'])
        page = request(base, route + '/report/1', cookie=cookie, raw=True)[0]
        self.assertIn('政策影响相同'.encode(), page)
        self.assertEqual(request(base, route + '/report/1', cookie=cookie, raw=True)[0], page)
        frozen = server.agent.archive.report(done['id'], 1)
        self.assertEqual(frozen['content']['evidence'], done['evidence'])
        self.assertEqual(frozen['html'].encode(), page)
        self.assertEqual(request(base, route, cookie=cookie)[0], done)
