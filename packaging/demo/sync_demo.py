"""Sync one accepted offline candidate to both retained demos, never production.

Exact package/browser receipts are required. Copying does not install, publish,
or establish native or financial quality. Unknown/private files are untouched.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packaging'))
from artifact_integrity import verify_directory
from build_current import build_lock, verify_current_build, write_record
from release_retention import permanent_cleanup_enabled

DESTINATION = Path('/Users/haoranxu/Desktop/demo/ResearchHelper demo')
PRIVATE_FILES = {'config.local.json', 'model-settings.json', 'source-settings.json',
                 'execution-policy.json', 'local-account.json', 'local-sessions.json', 'passwords.json', '.env'}
PRIVATE_PARTS = {'__pycache__', '.local', 'credentials', 'installation-backups', 'demo-backups', 'live-connectivity', '.git', 'data', 'result'}
TOP_LEVEL = {'README.md', 'index.html', 'main.py', 'manifest.json', 'app', 'assets', 'licenses', 'public', 'researchhelper'}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def ordinary(path):
    path = Path(path)
    if not path.is_absolute() or path != path.resolve():
        raise ValueError('同步路径必须是无软链接的绝对路径')
    return path


def release_name(name):
    path = PurePosixPath(name)
    parts = path.parts
    private_parts = set(parts) & PRIVATE_PARTS
    if parts[:2] == ('app', 'data') and (parts[2:] == ('report_catalog.json',) or
            len(parts) == 4 and parts[2] == 'reports' and path.suffix == '.html'):
        private_parts.discard('data')  # Public report catalogue, never a research Store.
    if (not name or path.is_absolute() or '..' in parts or str(path) != name or
            '\\' in name or ':' in name or any(ord(char) < 32 for char in name) or
            not parts or parts[0] not in TOP_LEVEL or private_parts or
            path.name in PRIVATE_FILES or path.name.startswith('.env.') or
            path.suffix in {'.secret', '.sqlite', '.sqlite3', '.db', '.db-wal', '.db-shm'}):
        raise ValueError('发布清单包含私有或非受控路径：' + name)
    return name


def read_receipt(path, root):
    path = ordinary(path)
    if not path.is_file() or not path.is_relative_to(root / 'result'):
        raise ValueError('验收回执不属于本项目的result目录')
    raw = path.read_bytes()
    return json.loads(raw), {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def acceptance(candidate_path, package_path, browser_path, root=ROOT):
    root, candidate_path = ordinary(root), ordinary(candidate_path)
    candidate = verify_current_build(candidate_path, root)
    package, package_identity = read_receipt(package_path, root)
    browser, browser_identity = read_receipt(browser_path, root)
    for record in (package, browser):
        if (record.get('status') != 'scoped_passed' or record.get('candidate') != str(candidate_path) or
                any(record.get(flag) is not False for flag in ('native_window_opened', 'production_store_touched', 'installed')) or
                any(type(record.get(key)) is not int or record[key] != 0 for key in ('model_calls', 'real_data_requests')) or
                record.get('mount_detached') is not True):
            raise ValueError('验收回执失败、范围不符或不属于当前候选')
    flags = ('headless', 'backend_stopped', 'skill_zip_inventory_verified', 'demo_private_files_absent',
             'app_complete_inventory_verified', 'vendor_licenses_verified', 'app_private_files_absent',
             'signature_verified', 'readonly_image_verified', 'development_and_shared_products_denied', 'anaconda_denied')
    if any(package.get(flag) is not True for flag in flags) or package.get('public_reports') != 6:
        raise ValueError('实际安装包清单、隔离或资源验收尚未通过')
    if (browser.get('backend_mode') != 'readonly_dmg' or browser.get('system_dialog_presented') is not False or
            any(browser.get(flag) is not True for flag in ('bundled_backend_isolated', 'signature_verified', 'service_stopped'))):
        raise ValueError('实际包内浏览器验收范围不符')
    web = browser.get('browser', {})
    if (web.get('status') != 'passed' or web.get('errors') != [] or
            type(web.get('externalRequests')) is not int or web['externalRequests'] != 0 or
            any(web.get(flag) is not True for flag in ('headless', 'browserClosed')) or
            any(web.get(flag) is not False for flag in ('systemClipboardTouched', 'systemPrintOpened')) or
            not web.get('checks') or not web.get('screens')):
        raise ValueError('包内界面检查未通过或后台浏览器未回收')
    cases = web.get('syntheticCases', [])
    if ({case.get('fixture') for case in cases} != {'mechanism', 'etf'} or
            any(not case.get('id') or case.get('version') != 1 or
                case.get('ledger', {}).get('used', {}).get('model_calls') != 0 or
                case.get('ledger', {}).get('used', {}).get('data_requests') != 0 for case in cases)):
        raise ValueError('实际固定案例及离线账本证据不完整')
    return candidate, {'candidate': str(candidate_path), 'package': package_identity, 'browser': browser_identity,
                       'native_ui_verified': False, 'real_model_verified': False, 'published': False, 'installed': False}


def release_files(candidate, version):
    artifact = candidate['directory_artifacts']['demo']
    source = ordinary(artifact['path'])
    verified = verify_directory(artifact, candidate['payload'])
    files = {release_name(name): value for name, value in verified['files'].items()}
    if any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value) for value in files.values()):
        raise ValueError('发布文件摘要格式不符')
    manifest = json.loads((source / 'manifest.json').read_text())
    runtime = manifest.get('runtime', {})
    if (manifest.get('source') != 'ResearchHelper' or manifest.get('version') != version or
            runtime.get('version') != version or runtime.get('protocol_id') != 'researchhelper.module-request' or
            manifest.get('files') != {name: value for name, value in files.items() if name != 'manifest.json'}):
        raise ValueError('demo内外清单不一致')
    files['researchhelper-release.json'] = files['manifest.json']
    return source, files


def file_state(path):
    ordinary(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError('同步目标存在文件与目录冲突：' + str(path))
    info = path.stat()
    return {'sha256': digest(path), 'inode': info.st_ino, 'size': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'mode': info.st_mode & 0o777}


def untouched_state(target, files):
    """Protect unknown/private files; allow independent regular Finder metadata.

    Finder's .DS_Store is neither a release input nor research data. Its own
    concurrent writes must not roll back an otherwise consistent release.
    """
    result = {}
    for directory, folders, names in os.walk(target, followlinks=False):
        parent = Path(directory)
        linked = [name for name in folders if (parent / name).is_symlink()]
        folders[:] = [name for name in folders if name not in linked]
        for name in names + linked:
            path = parent / name
            relative = path.relative_to(target).as_posix()
            if relative not in files:
                # Finder changes view metadata when a folder's published files
                # change. We never read or overwrite it. Links with this name
                # remain protected like every other unknown/private file.
                if name == '.DS_Store' and not path.is_symlink() and path.is_file():
                    continue
                info = path.lstat()
                result[relative] = [info.st_ino, info.st_size, info.st_mtime_ns,
                                    os.readlink(path) if path.is_symlink() else None]
    return result


def destination_plan(target, files):
    target = ordinary(target)
    if not target.is_dir():
        raise ValueError('必须保留并使用既有demo文件夹：' + str(target))
    states = {}
    for name in files:
        path = ordinary(target / name)
        for parent in path.parents:
            if parent == target:
                break
            if parent.exists() and not parent.is_dir():
                raise ValueError('同步父路径不是文件夹：' + str(parent))
        states[name] = file_state(path)
    return {'target': target, 'states': states, 'untouched': untouched_state(target, files)}



def sync_recovery_directory(root, recovery):
    """Use the owned transaction namespace; retain explicit legacy recovery."""
    owned=root/'result/demo-sync-recovery'
    if recovery is None:
        recovery=owned if permanent_cleanup_enabled(root) else Path.home()/'.Trash'
    recovery=ordinary(recovery)
    if recovery.is_relative_to(root) and recovery!=owned:
        raise ValueError('仓库内仅允许result/demo-sync-recovery作为同步恢复目录')
    if recovery==owned:
        if recovery.exists() and not recovery.is_dir():
            raise ValueError('项目同步恢复路径不是普通目录')
        recovery.mkdir(mode=0o700,parents=True,exist_ok=True)
    if not recovery.is_dir():
        raise ValueError('同步恢复目录不存在或不可用')
    return recovery


def commit_file(staged, target):
    os.replace(staged, target)


def sync_current(candidate_path, package_path, browser_path, *, root=ROOT, recovery=None):
    root = ordinary(root)
    recovery = sync_recovery_directory(root, recovery)
    with build_lock(root):
        from reconcile_delivery import reconcile_downloads
        reconcile_downloads(root,root/'dist')
        candidate, evidence = acceptance(candidate_path, package_path, browser_path, root)
        version = (root / 'VERSION').read_text(encoding='utf-8').strip()
        source, files = release_files(candidate, version)
        targets = (root / 'demo', DESTINATION)
        if targets[0] == targets[1] or any(target.is_relative_to(source) for target in targets):
            raise ValueError('同步目标重叠')
        plans = [destination_plan(target, files) for target in targets]
        if any(target.stat().st_dev != root.stat().st_dev for target in (*targets, recovery)):
            raise ValueError('同步目标和恢复目录必须与仓库同卷')
        changes = [(index, name, state) for index, plan in enumerate(plans)
                   for name, state in plan['states'].items() if state is None or state['sha256'] != files[name]]
        identity = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        output = root / 'result/maintenance/demo-sync' / identity
        output.mkdir(parents=True, exist_ok=False)
        receipt_path = output / 'receipt.json'
        backup = recovery / ('ResearchHelper-demo-sync-' + identity) if any(state for _, _, state in changes) else None
        record = {'schema': 'researchhelper.demo-sync.v2', 'status': 'preparing', **evidence,
                  'destinations': [str(target) for target in targets], 'version': version,
                  'recovery_directory': str(backup) if backup else None, 'changes': [],
                  'files_per_destination': len(files), 'finder_metadata_excluded': ['regular .DS_Store'],
                  'permanent_deletion': False,
                  'production_store_touched': False, 'model_calls': 0, 'real_data_requests': 0}
        record['planned_changes'] = [{'destination': index, 'file': name,
                                     'before': state, 'after': files[name]} for index, name, state in changes]
        committed, created_dirs = [], []
        try:
            write_record(receipt_path, record)
            if backup:
                backup.mkdir(mode=0o700, exist_ok=False)
            with tempfile.TemporaryDirectory(prefix='demo-sync-', dir=root / 'result') as directory:
                stage = Path(directory)
                for index, name, state in changes:
                    original = targets[index] / name
                    if file_state(original) != state:
                        raise RuntimeError('目标在盘点后变化，未覆盖：' + str(original))
                    if state:
                        saved = backup / str(index) / name
                        saved.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(original, saved)
                        saved.chmod(0o600)
                        if digest(saved) != state['sha256'] or file_state(original) != state:
                            raise RuntimeError('恢复副本与原文件不一致')
                    staged = stage / str(index) / name
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source / ('manifest.json' if name == 'researchhelper-release.json' else name), staged)
                    if digest(staged) != files[name]:
                        raise RuntimeError('暂存文件与发布清单不一致')
                if acceptance(candidate_path, package_path, browser_path, root) != (candidate, evidence):
                    raise RuntimeError('候选或验收回执在复制前变化')
                record['status'] = 'copying'
                write_record(receipt_path, record)
                changes.sort(key=lambda row: (row[1] in {'index.html', 'manifest.json', 'researchhelper-release.json'}, row[0], row[1]))
                for index, name, state in changes:
                    target = targets[index] / name
                    if file_state(target) != state:
                        raise RuntimeError('目标在复制前变化，未覆盖：' + str(target))
                    missing, parent = [], target.parent
                    while not parent.exists():
                        missing.append(parent)
                        parent = parent.parent
                    for parent in reversed(missing):
                        ordinary(parent).mkdir()
                        created_dirs.append(parent)
                    commit_file(stage / str(index) / name, target)
                    committed.append((index, name, state))
                    record['changes'].append({'destination': index, 'file': name,
                                              'before': state['sha256'] if state else None, 'after': files[name]})
                    write_record(receipt_path, record)
                for plan in plans:
                    if any(digest(plan['target'] / name) != expected for name, expected in files.items()):
                        raise RuntimeError('同步后的发布文件校验失败')
                    if untouched_state(plan['target'], files) != plan['untouched']:
                        raise RuntimeError('非发布文件在同步期间变化')
                if acceptance(candidate_path, package_path, browser_path, root) != (candidate, evidence):
                    raise RuntimeError('候选或验收回执在复制后变化')
            record.update(status='synced_offline_candidate', untouched_files_unchanged=True,
                          files_verified=len(files) * len(targets), changed_files=len(committed))
        except BaseException as error:
            conflicts = []
            for index, name, state in reversed(committed):
                target = targets[index] / name
                try:
                    current = file_state(target)
                    if current is None or current['sha256'] != files[name]:
                        conflicts.append(str(target))
                        continue
                    if state:
                        saved = backup / str(index) / name
                        shutil.copy2(saved, target)
                        target.chmod(state['mode'])
                        if digest(target) != state['sha256']:
                            conflicts.append(str(target))
                    else:
                        target.unlink()
                except (OSError, ValueError):
                    conflicts.append(str(target))
            for parent in reversed(created_dirs):
                try:
                    parent.rmdir()
                except OSError:
                    pass
            record.update(status='failed', error=str(error), rollback_conflicts=conflicts)
            raise
        finally:
            write_record(receipt_path, record)
            if backup:
                write_record(backup / 'ResearchHelper-recovery.json', record)
        return {**record, 'receipt': str(receipt_path)}
