"""Retire superseded generated deliveries while preserving the current candidate.

Receipts and source snapshots stay in place. This module never empties Trash,
installs an application, or touches another project's files.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def verify_candidate(record, root):
    from build_current import verify_current_build
    return verify_current_build(record, root)


def ordinary_path(path, root):
    path = Path(path)
    return (path.is_relative_to(root) and path != root and
            not any(item.is_symlink() for item in (path, *path.parents) if item != root))


def app_record(path):
    """Validate a generated RH bundle, including its owned framework links."""
    path = Path(path)
    info = plistlib.loads((path / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleName') != 'ResearchHelper' or not (path / 'Contents/MacOS' / info['CFBundleExecutable']).is_file():
        raise RuntimeError('Not a compiled ResearchHelper application')
    files, size = {}, 0
    for item in sorted(path.rglob('*')):
        name = item.relative_to(path).as_posix()
        if item.is_symlink():
            if item.readlink().is_absolute() or not item.resolve().is_relative_to(path.resolve()):
                raise RuntimeError('Application framework link leaves its bundle')
            files[name] = {'link': str(item.readlink())}
        elif item.is_file():
            files[name] = {'sha256': digest(item)}
            size += item.stat().st_size
    return {'files': files, 'bytes': size}


def inventory(root, record, *, include_old_demos=False, include_test_images=False, include_old_apps=False):
    root = Path(root).resolve()
    record = Path(record).resolve()
    current = verify_candidate(record, root)
    protected = {str(Path(item['path']).resolve()) for item in current['artifacts'].values()}
    # Keep an independently built other-platform delivery's exact archive too.
    for folder in (root / 'dist', root / 'result/windows-candidate'):
        if folder.is_symlink():
            raise RuntimeError('Download directory cannot be a symlink')
        if not folder.is_dir():
            continue
        for file in folder.iterdir():
            if file.is_file() and not file.is_symlink() and file.suffix in {'.dmg', '.zip', '.exe'}:
                protected.update(str(path.resolve()) for path in (root / 'versions').glob('*/*/' + digest(file) + '/' + file.name))
    tracked = set(subprocess.run(['git', 'ls-files', '-z'], cwd=root, check=True,
                                capture_output=True).stdout.decode().split('\0'))
    targets, skipped = [], []

    def add_file(file, category):
        relative = file.relative_to(root).as_posix()
        if not ordinary_path(file, root) or relative in tracked or str(file.resolve()) in protected:
            return
        targets.append({'path': str(file), 'category': category,
                        'bytes': file.stat().st_size, 'sha256': digest(file)})

    for kind in ('app', 'skill'):
        base = root / 'versions' / kind
        if not ordinary_path(base, root):
            raise RuntimeError('Archive directory cannot be linked outside this project')
        for file in base.glob('*/*/*'):
            if not file.is_file() or str(file.resolve()) in protected:
                continue
            pattern = (r'ResearchHelper-\d+\.\d+\.\d+-arm64\.dmg' if kind == 'app'
                       else r'ResearchHelper-Skill-\d+\.\d+\.\d+\.zip')
            if not re.fullmatch(pattern, file.name):
                continue
            if not ordinary_path(file, root) or digest(file) != file.parent.name:
                skipped.append({'path': str(file), 'reason': 'archive identity mismatch'})
                continue
            add_file(file, 'superseded_archive')
    if include_test_images:
        for base in (root / 'result/build-staging', root / 'result/acceptance'):
            if not ordinary_path(base, root):
                raise RuntimeError('Diagnostic directory cannot be a symlink')
            for file in base.rglob('*.dmg'):
                if file.is_file():
                    add_file(file, 'old_diagnostic_image')
    if include_old_apps:
        apps = list((root / 'versions/app').glob('*/ResearchHelper.app'))
        apps += list((root / 'result/build-staging').glob('*/source/result/build-staging/current/ResearchHelper.app'))
        for path in apps:
            try:
                relative = path.relative_to(root).as_posix()
                if not ordinary_path(path, root) or any(name.startswith(relative + '/') for name in tracked):
                    raise RuntimeError('Linked or tracked application')
                app = app_record(path)
                targets.append({'path': str(path), 'category': 'superseded_compiled_app',
                                'bytes': app['bytes'], 'app': app})
            except (OSError, KeyError, ValueError, RuntimeError) as error:
                skipped.append({'path': str(path), 'reason': str(error)})
    if include_old_demos:
        from artifact_integrity import verify_directory
        for old_record in (root / 'result/build-candidates').glob('*/candidate.json'):
            if old_record.resolve() == record:
                continue
            try:
                if not ordinary_path(old_record, root):
                    raise RuntimeError('linked receipt')
                old = json.loads(old_record.read_text())
                demo = old['directory_artifacts']['demo']
                path = Path(demo['path'])
                if path != old_record.parent / 'demo' or not ordinary_path(path, root):
                    raise RuntimeError('demo outside its own candidate')
                if not path.exists():
                    continue
                verify_directory(demo, old_record.parent)
                relative = path.relative_to(root).as_posix()
                if any(name == relative or name.startswith(relative + '/') for name in tracked):
                    raise RuntimeError('tracked demo')
                targets.append({'path': str(path), 'category': 'superseded_generated_demo',
                                'bytes': sum(f.stat().st_size for f in path.rglob('*') if f.is_file()),
                                'directory': demo})
            except (KeyError, OSError, ValueError, RuntimeError) as error:
                skipped.append({'path': str(old_record), 'reason': str(error)})
    return {'root': str(root), 'candidate': str(record), 'protected_archives': sorted(protected),
            'include_old_demos': include_old_demos, 'include_test_images': include_test_images,
            'include_old_apps': include_old_apps,
            'targets': targets, 'skipped': skipped,
            'bytes': sum(item['bytes'] for item in targets)}



def _move_directory(source, target, entries):
    """Move verified entries without a cross-domain directory rename.

    Recovery can be partial: the original inventory describes the union of
    source and target until the final directory check succeeds. Unknown files
    stay in the source and prevent its removal.
    """
    source, target = Path(source), Path(target)
    target.mkdir(exist_ok=False)
    directories = []
    for current, names, _ in os.walk(source, followlinks=False):
        names[:] = [name for name in names if not (Path(current) / name).is_symlink()]
        directory = Path(current)
        relative = directory.relative_to(source)
        (target / relative).mkdir(parents=True, exist_ok=True)
        directories.append(directory)
    for name, expected in sorted(entries.items()):
        original, destination = source / name, target / name
        if isinstance(expected, dict) and 'link' in expected:
            if not original.is_symlink() or str(original.readlink()) != expected['link']:
                raise RuntimeError('Retirement link changed')
        else:
            value = expected['sha256'] if isinstance(expected, dict) else expected
            if original.is_symlink() or digest(original) != value:
                raise RuntimeError('Retirement file changed')
        destination.parent.mkdir(parents=True, exist_ok=True)
        original.rename(destination)
    # rmdir preserves any unknown or newly added source file. Empty directory
    # names, including ones not represented in file manifests, survive in Trash.
    for directory in reversed(directories):
        directory.rmdir()


def apply_inventory(plan, *, recovery=None):
    root = Path(plan['root']).resolve()
    # Recompute the exact selection under the caller's build lock. A changed
    # download, artifact or receipt invalidates this plan rather than broadening it.
    fresh = inventory(root, plan['candidate'],
                      include_old_demos=plan['include_old_demos'],
                      include_test_images=plan['include_test_images'], include_old_apps=plan['include_old_apps'])
    if fresh != plan:
        raise RuntimeError('Release cleanup inventory changed; nothing moved')
    if not plan['targets']:
        return {'status': 'nothing_superseded_to_remove', 'moved': 0}
    if recovery is None:
        if sys.platform != 'darwin':
            raise RuntimeError('Automatic recoverable retirement is configured only for macOS')
        recovery = Path.home() / '.Trash'
    recovery = Path(recovery)
    if recovery.is_symlink() or not recovery.is_dir() or recovery.resolve().is_relative_to(root):
        raise RuntimeError('Recovery directory must exist outside the repository')
    if recovery.stat().st_dev != root.stat().st_dev:
        raise RuntimeError('Recovery directory must be on the repository volume')
    mounts = []
    if sys.platform == 'darwin':
        info = plistlib.loads(subprocess.run(['hdiutil', 'info', '-plist'], check=True,
                                            capture_output=True, timeout=15).stdout)
        mounts = [Path(image['image-path']).resolve() for image in info.get('images', []) if image.get('image-path')]
    processes = subprocess.run(['ps', '-axo', 'pid=,command='], check=True, capture_output=True, text=True).stdout
    for item in plan['targets']:
        path = Path(item['path'])
        if path in mounts or any(m.is_relative_to(path) for m in mounts):
            raise RuntimeError('Release image is mounted; nothing moved')
        if any(str(path) in line and int(line.split(maxsplit=1)[0]) != os.getpid() for line in processes.splitlines()):
            raise RuntimeError('Release artifact has a live process; nothing moved')
    identity = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    folder = root / 'result/maintenance/release-retirement' / identity
    folder.mkdir(parents=True, exist_ok=False)
    destination = recovery / ('ResearchHelper-releases-' + identity)
    destination.mkdir(mode=0o700)
    receipt = {**plan, 'status': 'moving', 'permanent_deletion': False,
               'recovery_directory': str(destination), 'moves': []}
    from build_current import write_record
    try:
        for item in plan['targets']:
            source = Path(item['path'])
            if not ordinary_path(source, root):
                raise RuntimeError('Release target path changed')
            if 'directory' in item:
                from artifact_integrity import verify_directory
                verify_directory(item['directory'], source.parent)
            elif 'app' in item:
                if app_record(source) != item['app']:
                    raise RuntimeError('Application bundle content changed')
            elif digest(source) != item['sha256']:
                raise RuntimeError('Release target content changed')
            target = destination / source.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            move = {'source': str(source), 'destination': str(target), 'moved': False}
            if 'directory' in item or 'app' in item:
                move['method'] = 'verified_tree_entries'
            receipt['moves'].append(move)
            write_record(folder / 'receipt.json', receipt)
            if 'directory' in item or 'app' in item:
                manifest = item.get('directory', item.get('app'))
                _move_directory(source, target, manifest['files'])
                if 'directory' in item:
                    from artifact_integrity import directory_record
                    actual = directory_record(target)
                    if actual['files'] != manifest['files'] or actual['sha256'] != manifest['sha256']:
                        raise RuntimeError('Retired directory identity changed')
            else:
                source.rename(target)
            move['moved'] = True
            if 'sha256' in item and digest(target) != item['sha256']:
                raise RuntimeError('Retired artifact identity changed')
            if 'app' in item and app_record(target) != item['app']:
                raise RuntimeError('Retired application identity changed')
        verify_candidate(plan['candidate'], root)
        receipt['status'] = 'retired_recoverably'
    except BaseException as error:
        receipt.update(status='partial_retirement_requires_review', error=str(error))
        raise
    finally:
        write_record(folder / 'receipt.json', receipt)
        write_record(destination / 'ResearchHelper-recovery.json', receipt)
    return {'status': receipt['status'], 'moved': len(receipt['moves']),
            'bytes_moved': plan['bytes'], 'receipt': str(folder / 'receipt.json')}


def retire_after_build(root, record, *, timeout=25):
    """Opt-in project policy, applied only after a new delivery is fully committed."""
    root = Path(root).resolve()
    policy_path = root / 'packaging/release-retention.json'
    if not policy_path.exists():
        return {'status': 'policy_not_configured'}
    if not ordinary_path(policy_path, root):
        raise RuntimeError('Release retention policy cannot be a symlink')
    policy = json.loads(policy_path.read_text())
    if policy != {'schema': 'researchhelper.release-retention.v1',
                  'macos': 'current_candidate_only', 'recovery': 'trash'}:
        raise RuntimeError('Unknown release retention policy; no cleanup performed')
    if sys.platform != 'darwin':
        return {'status': 'macos_policy_not_applicable'}
    if type(timeout) not in (int, float) or not 0 < timeout <= 60:
        raise ValueError('清理超时须大于0且不超过60秒')
    return _bounded_retirement(root, record, timeout=timeout)


def _retire_now(root, record):
    from build_current import build_lock
    with build_lock(root):
        from reconcile_delivery import reconcile_downloads
        reconcile_downloads(root, root / 'dist')
        plan = inventory(root, record, include_old_demos=True, include_old_apps=True)
        return apply_inventory(plan)


RETIREMENT_WORKER = '''from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from release_retention import _retirement_worker
_retirement_worker(*map(Path, sys.argv[2:]))
'''


def _retirement_worker(root, record, output):
    from build_current import write_record
    try:
        result = _retire_now(root, record)
    except Exception as error:
        result = dict(status='requires_review', error=str(error))
    write_record(output, result)


def _bounded_retirement(root, record, *, timeout):
    from build_current import write_record
    from build_progress import stop_process_tree
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    folder = root / 'result/maintenance/release-retirement' / ('worker-' + stamp)
    if not ordinary_path(folder, root):
        raise RuntimeError('Cleanup attempt cannot leave the repository')
    folder.mkdir(parents=True, exist_ok=False)
    result_file, receipt = folder / 'result.json', folder / 'attempt.json'
    attempt = dict(status='running', candidate=str(record), timeout_seconds=timeout,
                   current_candidate_preserved=True, model_calls=0, data_requests=0)
    command = [sys.executable, '-I', '-B', '-c', RETIREMENT_WORKER,
               str(Path(__file__).resolve().parent), str(root), str(record), str(result_file)]
    options = {'start_new_session': True} if os.name != 'nt' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    started = time.monotonic()
    with (folder / 'worker.log').open('w') as log:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
        attempt['worker_pid'] = process.pid
        write_record(receipt, attempt)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                stop_process_tree(process)
            except Exception as error:
                attempt['stop_error'] = type(error).__name__
            attempt.update(status='cleanup_timed_out', worker_stopped=process.poll() is not None)
            print('旧交付物清理超时，现版已保留；部分转移记录可继续核对。', flush=True)
        except BaseException:
            try:
                stop_process_tree(process)
            finally:
                attempt.update(status='interrupted', worker_stopped=process.poll() is not None,
                               elapsed_seconds=round(time.monotonic()-started, 2))
                write_record(receipt, attempt)
            raise
        else:
            attempt.update(status='worker_finished', worker_stopped=True, exit_code=process.returncode)
    attempt['elapsed_seconds'] = round(time.monotonic()-started, 2)
    write_record(receipt, attempt)
    if attempt['status'] == 'worker_finished' and process.returncode == 0 and result_file.is_file():
        result = json.loads(result_file.read_text())
        if isinstance(result, dict) and result.get('status') in ('retired_recoverably', 'nothing_superseded_to_remove', 'requires_review'):
            return dict(result, worker_receipt=str(receipt))
    return dict(status='requires_review', reason=attempt['status'], worker_receipt=str(receipt),
                worker_stopped=attempt.get('worker_stopped', False))

