"""Exercise actual installed runtime with a private Store and no foreground UI."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
INSTALLATION = Path('/Applications/ResearchHelper.app')
PRODUCTION_STORE = Path('/Users/haoranxu/Library/Application Support/ResearchHelper')
sys.path[:0] = [str(ROOT / 'packaging'), str(ROOT / 'packaging/app/macos')]
from build_current import verify_current_build
from install_app import recorded_manifest, verify_bundle, signature


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def store_identity():
    # Hash only; never output or reuse private settings or credentials.
    inventory = {str(file.relative_to(PRODUCTION_STORE)): digest(file)
        for file in PRODUCTION_STORE.rglob('*') if file.is_file()}
    return hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()


class InstalledRuntime:
    """Use only installed binaries; deny source, production data and remote I/O."""
    def __init__(self, output, record):
        self.output, self.record = output, record
        self.process = self.errors = None

    def start(self):
        selected = Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
        verify_current_build(selected, ROOT)
        verify_bundle(INSTALLATION, recorded_manifest(selected))
        signature(INSTALLATION)
        policy = '(version 1)(allow default)(deny network*)'
        policy += '(allow network* (local ip "localhost:*"))(allow network* (remote ip "localhost:*"))'
        policy += '(allow network* (local unix-socket))(allow network* (remote unix-socket))'
        policy += '(deny file-write* (subpath ' + json.dumps(str(INSTALLATION)) + '))'
        for directory in (ROOT / 'core', ROOT / 'modules', ROOT / 'products',
                Path('/opt/anaconda3'), Path('/Users/haoranxu/Desktop/OptionHelper'),
                Path('/Users/haoranxu/Desktop/DataFetcher'), PRODUCTION_STORE):
            policy += '(deny file-read* (subpath ' + json.dumps(str(directory)) + '))'
        executable = INSTALLATION / 'Contents/Resources/backend/researchhelper-server/researchhelper-server'
        self.errors = (self.output / 'installed-backend.log').open('a')
        environment = dict(PATH='/usr/bin:/bin', LANG='en_US.UTF-8',
            RESEARCHHELPER_STORE=str(self.output / 'store'), PYTHONDONTWRITEBYTECODE='1',
            PYTHONTZPATH=str(self.output / 'no-system-tzdata'))
        self.process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', policy, str(executable)],
            cwd=self.output, env=environment, stdout=subprocess.PIPE, stderr=self.errors)
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=20):
                raise RuntimeError('已安装运行组件未就绪，请查看独立验收日志')
            ready = json.loads(self.process.stdout.readline())
        if not ready['url'].startswith('http://127.0.0.1:'):
            raise RuntimeError('运行组件未使用回环地址')
        self.record.update(backend_mode='actual_installed_bundle', installed=True,
            runtime_executable=str(executable), no_native_window=True, service_stopped=False,
            real_data_requests=0, model_calls=0, source_and_production_store_denied=True)
        return ready['url']

    def stop(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process.stdout.close()
            self.record['service_stopped'] = self.process.poll() is not None
            self.process = None
        if self.errors:
            self.errors.close()
            self.errors = None


def run(*, reader_version=None):
    selected = Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    candidate = verify_current_build(selected, ROOT)
    # The verified source manifest binds this constant to the candidate;
    # never hardcode an old reader or accept whatever the HTML happens to say.
    from researchhelper.reports import READER_VERSION
    reader_version = READER_VERSION if reader_version is None else reader_version
    if reader_version not in ('rh-reader-10','rh-reader-11'):
        raise ValueError('Unsupported explicit reader fixture version')
    manifest = recorded_manifest(selected)
    before = verify_bundle(INSTALLATION, manifest)
    production_before = store_identity()
    output = Path(tempfile.mkdtemp(prefix='installed-client-research-', dir=ROOT / 'result/acceptance'))
    record = dict(status='failed', candidate=str(selected), model_calls=0, provider_calls=0,
        native_window_opened=False, native_print_accepted=False, financial_quality_accepted=False,
        full_goal_complete=False, scope='installed_runtime_offline_eight_branches_and_restore')
    originals = [ROOT / 'tests/research_matrix_skill_fixture.py',
        ROOT / 'tests/accept_current_research_matrix.py']
    record['original_test_hashes'] = {str(file): digest(file) for file in originals}
    try:
        fixture = originals[0].read_text()
        old_marker = "data-reader-version='rh-reader-7'"
        assert fixture.count(old_marker) == 1
        fixture = fixture.replace(old_marker, "data-reader-version='" + reader_version + "'")
        fixture_path = output / 'installed_matrix_fixture.py'
        fixture_path.write_text(fixture)
        matrix = originals[1].read_text()
        substitutions = {
            'from tests.accept_native_workbench_render import RenderRuntime':
                'from tests.accept_installed_client_research import InstalledRuntime as RenderRuntime',
            'from tests.research_matrix_skill_fixture import CASE_EXPECTATIONS, verify_case':
                'from installed_matrix_fixture import CASE_EXPECTATIONS, verify_case',
            'ROOT = Path(__file__).resolve().parents[1]': 'ROOT = Path(' + repr(str(ROOT)) + ')',
            "str(ROOT / 'tests/research_matrix_skill_fixture.py')": 'str(Path(' + repr(str(fixture_path)) + '))',
            "assert installation_hash == candidate['project_skill_installation']['manifest_sha256']":
                "assert installation_hash == hashlib.sha256((Path(candidate['payload']) / 'skill-manifest.json').read_bytes()).hexdigest()",
        }
        for old, replacement in substitutions.items():
            assert matrix.count(old) == 1
            matrix = matrix.replace(old, replacement)
        probe = output / 'installed_matrix_probe.py'
        probe.write_text(matrix)
        result = subprocess.run([sys.executable, '-B', str(probe)], cwd=output,
            env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1'),
            capture_output=True, text=True, timeout=120)
        (output / 'matrix.log').write_text(result.stdout + result.stderr)
        summary = json.loads(result.stdout.strip().splitlines()[-1])
        matrix_receipt = Path(summary['receipt'])
        assert matrix_receipt.is_relative_to(ROOT / 'result/acceptance')
        details = json.loads(matrix_receipt.read_text())
        assert result.returncode == 0 and details['status'] == 'scoped_passed'
        assert details['backend_mode'] == 'actual_installed_bundle'
        assert details['service_stopped'] and details['offline_restart_verified']
        assert details['entries_verified'] == 8 and details['intents_verified'] == 5
        assert details['model_calls'] == details['real_data_requests'] == 0
        for case in details['cases'].values():
            assert case['app_skill_semantics_equal']
            assert case['ledger']['used']['model_calls'] == case['ledger']['used']['data_requests'] == 0
        assert verify_bundle(INSTALLATION, manifest) == before
        assert store_identity() == production_before
        assert verify_current_build(selected, ROOT) == candidate
        assert all(digest(file) == record['original_test_hashes'][str(file)] for file in originals)
        record.update(status='scoped_passed', matrix_receipt=str(matrix_receipt),
            cases=8, intents=5, app_skill_semantics_equal=True, offline_restart_verified=True,
            installed_files=len(before), installed_app_unchanged_by_tests=True,
            production_store_unchanged=True, original_tests_unchanged=True,
            fixture_reader_runtime_wiring_and_installation_binding_adapted=True,
            production_store_identity=production_before)
    except Exception as error:
        record['error'] = str(error)
    receipt = output / 'receipt.json'
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], receipt=str(receipt)), ensure_ascii=False))
    return record


if __name__ == '__main__':
    raise SystemExit(0 if run()['status'] == 'scoped_passed' else 1)
