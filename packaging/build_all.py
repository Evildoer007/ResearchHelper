"""Build an isolated development candidate without replacing a released version."""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_snapshot import source_files, freeze_sources, digest
from build_environment import verify_environment
from artifact_integrity import verify_directory
from build_progress import stage as build_stage, run_logged
from persist_build_python import persist

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
BUILD_SANDBOX = ('(version 1)(allow default)(deny network*)'
                 '(allow network* (local unix-socket))(allow network* (remote unix-socket))')
_ACTIVE_BUILD_WORKSPACES = {}


@contextmanager
def temporary_build_workspace(root):
    """Own one ephemeral compiler tree; durable journals stay in the project."""
    root = Path(root).resolve()
    with tempfile.TemporaryDirectory(prefix='researchhelper-current-') as directory:
        folder = Path(directory).resolve()
        _ACTIVE_BUILD_WORKSPACES[folder] = root
        try:
            yield folder
        finally:
            _ACTIVE_BUILD_WORKSPACES.pop(folder, None)


def is_owned_build_workspace(folder, root):
    """A directory name alone never grants permission to write or clean it."""
    return _ACTIVE_BUILD_WORKSPACES.get(Path(folder).resolve()) == Path(root).resolve()


def build_candidate(root=ROOT, runner=None, *, workspace=None):
    root = Path(root).resolve()
    identity = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    if workspace is not None:
        workspace = Path(workspace).resolve()
        legacy_workspace = (workspace.is_relative_to(root / 'result/build-work')
                            and workspace != root / 'result/build-work')
        if not is_owned_build_workspace(workspace, root) and not legacy_workspace:
            raise ValueError('构建目录须为本次拥有的临时工作区或本项目result/build-work内的诊断目录')
    stage = root / ('result/build-records' if workspace else 'result/build-staging') / identity
    started = time.monotonic()
    stage.mkdir(parents=True, exist_ok=False)
    snapshot = (workspace if workspace else stage) / 'source'
    receipt = {'status': 'building', 'source_hashes': {}, 'snapshot': str(snapshot),
               'environment': {}, 'phase': 'preflight', 'log': str(stage / 'build.log'),
               'published': False, 'installed': False, 'demos_updated': False,
               'real_model_verified': False, 'real_provider_verified': False}
    if workspace is not None:
        receipt.update(snapshot_retained=False, temporary_payload_retained=False)
    record = stage / 'candidate.json'
    record.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    (stage / 'build.log').write_text('ResearchHelper候选构建\nPython：' + str(PYTHON) + '\n', encoding='utf-8')
    print('ResearchHelper候选构建\nPython：' + str(PYTHON) + '\n日志：' + receipt['log'], flush=True)
    try:
        with build_stage('1/7', '检查Python、锁定依赖、平台工具和受控图标'):
            environment = verify_environment(root)
            source_files(root)
            target = environment.get('platform', 'macos')
            receipt.update(environment=environment, platform=target)
            persist(root, PYTHON)  # Never replace the last good choice after failed preflight.
        receipt['phase'] = 'snapshot'
        with build_stage('2/7', '冻结明确白名单中的源码'):
            inventory = freeze_sources(root, snapshot)
        receipt['source_hashes'] = inventory
        record.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        receipt['phase'] = 'assembly'
        with (stage / 'build.log').open('a', encoding='utf-8') as log:
            command = [str(PYTHON), '-B', '-u', str(snapshot / 'packaging/assemble_release.py')]
            if target == 'macos':
                command = ['/usr/bin/sandbox-exec', '-p', BUILD_SANDBOX, *command]
            if runner is None:
                run_logged(command, cwd=snapshot, stdout=log, check=True,
                           on_progress=lambda line: receipt.update(last_progress=line))
            else:
                runner(command, cwd=snapshot, stdout=log, stderr=subprocess.STDOUT, check=True)
        receipt['phase'] = 'verification'
        receipt.pop('last_progress', None)
        verification_started = time.monotonic()
        print('[6/7] 核对安装物、Skill、demo及同源清单', flush=True)
        payload = snapshot / 'result/build-staging/current'
        manifest = payload / 'release-manifest.json'
        release = json.loads(manifest.read_text())
        expected = {'skill', 'dmg' if target == 'macos' else 'setup'}
        if not expected.issubset(release.get('artifacts', {})):
            raise RuntimeError('Candidate distributables are incomplete')
        verify_release_binding(release, payload, target)
        for artifact in release['artifacts'].values():
            path = Path(artifact['path'])
            if not path.resolve().is_relative_to(payload) or digest(path) != artifact['sha256']:
                raise RuntimeError('Candidate manifest does not match its artifacts')
        directories = release.get('directory_artifacts', {})
        if 'demo' not in directories:
            raise RuntimeError('Candidate demo delivery is absent from its manifest')
        for item in directories.values():
            verify_directory(item, payload)
        if any(digest(snapshot / name) != value for name, value in inventory.items()):
            raise RuntimeError('Source snapshot was modified by the build')
        if any(digest(root / name) != value for name, value in inventory.items()):
            raise RuntimeError('开发源码在构建期间变化，拒绝交付旧快照')
        print('[6/7] 完成，用时' + str(round(time.monotonic() - verification_started, 1)) + '秒', flush=True)
        receipt.update(status='built_not_published', platform=target, payload=str(payload),
                       artifacts=release['artifacts'], directory_artifacts=directories,
                       manifest_sha256=digest(manifest), elapsed_seconds=round(time.monotonic() - started, 1), phase='completed')
    except BaseException as error:
        cancelled = isinstance(error, KeyboardInterrupt)
        reason = '用户取消构建' if cancelled else ('子构建退出码' + str(error.returncode)
                 if isinstance(error, subprocess.CalledProcessError) else str(error)[:1200])
        receipt.update(status='cancelled_not_published' if cancelled else 'failed_not_published',
                       error_type=type(error).__name__, error_message=reason,
                       elapsed_seconds=round(time.monotonic() - started, 1))
        with (stage / 'build.log').open('a', encoding='utf-8') as log:
            traceback.print_exc(file=log)
        record.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        phase_label = {'preflight': '环境预检', 'snapshot': '源码冻结', 'assembly': '产品构建', 'verification': '完整性校验'}[receipt['phase']]
        print(('构建已取消' if cancelled else '构建未完成') + '。阶段：' + receipt.get('last_progress', phase_label) +
              '\n原因：' + reason + '\n未发布、安装或覆盖旧demo。\n日志：' + receipt['log'] + '\n回执：' + str(record), flush=True)
        raise
    record.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    if workspace is None:
        print('[7/7] 候选构建完成，用时' + str(receipt['elapsed_seconds']) + '秒。尚未发布或安装。', flush=True)
        for key, item in receipt['artifacts'].items():
            print({'dmg': 'macOS安装包', 'setup': 'Windows安装包', 'skill': 'Skill'}.get(key, key) + '：' + item['path'], flush=True)
        print('demo：' + receipt['directory_artifacts']['demo']['path'] + '\n回执：' + str(record), flush=True)
    return receipt


def verify_release_binding(release, payload, target, *, source_root=None):
    from shared_payload import runtime_identity
    expected = runtime_identity(Path(source_root) if source_root else Path(payload).parents[2])
    if release.get('platform') != target or release.get('runtime') != expected or release.get('version') != expected['version']:
        raise RuntimeError('发行平台或共享内核身份不一致')
    expected_arch = 'arm64' if target == 'macos' else 'x64'
    if release.get('architecture') != expected_arch:
        raise RuntimeError('发行架构不一致')
    for component in ('skill', 'demo', 'app'):
        item = release.get('component_manifests', {}).get(component)
        if not item:
            raise RuntimeError('缺少产品验收清单：' + component)
        path = Path(item['path'])
        if not path.resolve().is_relative_to(payload) or digest(path) != item['sha256']:
            raise RuntimeError('产品验收清单变化或越界：' + component)
        identity = json.loads(path.read_text())
        if identity.get('runtime') != expected or identity.get('version') != release['version']:
            raise RuntimeError('产品版本或内核身份不一致：' + component)
    app = json.loads(Path(release['component_manifests']['app']['path']).read_text())
    if app.get('platform') != target or app.get('architecture') != expected_arch:
        raise RuntimeError('App验收清单的平台或架构不一致')
    if target == 'windows' and app.get('installer_sha256') != release['artifacts']['setup']['sha256']:
        raise RuntimeError('Windows安装器与App验收清单不一致')


if __name__ == '__main__':
    try:
        build_candidate()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        sys.exit(1)
