"""Recoverable cleanup of regenerable development outputs, not research history.

Inventory is read-only. Apply requires that exact inventory; it never infers
permission to clear Trash, release archives, test evidence, or production stores.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess

ROOT = Path('/Users/haoranxu/Desktop/ResearchHelper')
TRASH = Path('/Users/haoranxu/.Trash')
CACHE_NAMES = {'__pycache__', '.pytest_cache', 'swift-cache', 'pyinstaller-work'}
PROTECTED_NAMES = {'.git', '.skills', '.agents', '.researchhelper', 'data', 'demo',
                   'dist', 'versions', 'build-candidates', 'host-research', 'report-style-reference'}


def protected_path(relative):
    parts = relative.parts
    return (bool(parts) and parts[0] in PROTECTED_NAMES or
            len(parts) > 1 and parts[0] == 'result' and parts[1] in PROTECTED_NAMES or
            any(name in {'.git', '.skills', '.agents', '.researchhelper'} for name in parts))


def tree_stamp(path):
    """Metadata identity without printing or opening private configuration files."""
    path = Path(path)
    if not path.exists():
        return {'exists': False}
    entries = [path] + sorted(path.rglob('*')) if path.is_dir() and not path.is_symlink() else [path]
    rows = []
    for item in entries:
        info = item.lstat()
        if item.is_dir() and not item.is_symlink():
            continue
        rows.append((str(item.relative_to(path)) if item != path else '.',
                     info.st_ino, info.st_size, info.st_mtime_ns,
                     os.readlink(item) if item.is_symlink() else None))
    return {'exists': True, 'files': len(rows),
            'bytes': sum(row[2] for row in rows),
            'sha256': hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()}


def generated_demo(path):
    """Recognize a complete disposable test demo by its full generated manifest."""
    manifest = path / 'manifest.json'
    if manifest.is_symlink() or not manifest.is_file():
        return False
    try:
        record = json.loads(manifest.read_text())
        files = record['files']
        if record.get('source') != 'ResearchHelper' or record['runtime']['protocol_id'] != 'researchhelper.module-request' or not files:
            return False
        actual = {item.relative_to(path).as_posix() for item in path.rglob('*') if item.is_file()}
        if any(item.is_symlink() for item in path.rglob('*')) or actual != set(files) | {'manifest.json'}:
            return False
        for name, expected in files.items():
            file = path / name
            if not file.resolve().is_relative_to(path.resolve()) or file.suffix in {'.db', '.sqlite', '.sqlite3', '.pdf'}:
                return False
            if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def synthetic_app(path):
    """Only compiled RH application copies within acceptance workspaces qualify."""
    try:
        record = plistlib.loads((path / 'Contents/Info.plist').read_bytes())
        return record.get('CFBundleName') == 'ResearchHelper' and (
            path / 'Contents/MacOS' / record['CFBundleExecutable']).is_file()
    except (OSError, KeyError, ValueError, plistlib.InvalidFileException):
        return False


def internal_app_links(path):
    """An App's relative framework links move with it; external links never do."""
    base = path.resolve()
    return all(not child.readlink().is_absolute() and child.resolve().is_relative_to(base)
               for child in path.rglob('*') if child.is_symlink())


def permitted_compiler_cache_links(path):
    """Only Swift's own same-directory lock links may move with a cache.

    Moving the owned directory never follows a link. External links, directory
    links and arbitrary aliases remain protected; stale internal locks are not
    mistaken for external source data. Inventory and apply use this same rule.
    """
    links = [child for child in path.rglob('*') if child.is_symlink()]
    if not links:
        return True
    if path.name != 'swift-cache':
        return False
    for link in links:
        if not link.name.endswith(('.pcm.lock', '.swiftmodule.lock')):
            return False
        target = link.readlink()
        target = target if target.is_absolute() else link.parent / target
        if target.parent != link.parent or not target.is_relative_to(path):
            return False
        if not re.fullmatch(re.escape(link.name) + r'-[0-9a-f]{8}', target.name):
            return False
        if target.is_symlink() or target.exists() and not target.is_file():
            return False
    return True


def verified_native_module_cache(path, root):
    """Recognize the historical test cache by placement, names and formats.

    A cache-like directory name alone is insufficient. Unknown files, links,
    nested research data and non-module binaries keep the entire target intact.
    This narrow recognizer is intentionally separate from ordinary Swift caches.
    """
    path, root = Path(path), Path(root)
    if path != root / 'result/acceptance/native-module-cache' or path.is_symlink() or not path.is_dir():
        return False
    try:
        formats = set()
        entries = list(path.rglob('*'))
        if not entries:
            return False
        for entry in entries:
            relative = entry.relative_to(path)
            if entry.is_symlink() or entry.stat().st_dev != path.stat().st_dev:
                return False
            if entry.is_dir():
                if len(relative.parts) != 1 or not re.fullmatch(r'[A-Z0-9]{8,20}', entry.name):
                    return False
                continue
            if not entry.is_file():
                return False
            if relative.as_posix() == 'modules.timestamp':
                if entry.stat().st_size != 0:
                    return False
                formats.add('timestamp')
                continue
            if (len(relative.parts) > 2 or not re.fullmatch(
                    r'[A-Za-z_][A-Za-z0-9_]*-[A-Z0-9]{8,20}\.(pcm|swiftmodule)', entry.name)):
                return False
            with entry.open('rb') as stream:
                header = stream.read(16384)
            if entry.suffix == '.swiftmodule':
                if not header.startswith(bytes.fromhex('e29ca80e')):
                    return False
            elif (not header.startswith(bytes.fromhex('cffaedfe0c0000010000000001000000'))
                    or b'__clangast\0' not in header or b'__CLANG\0' not in header):
                return False
            formats.add(entry.suffix)
        return formats == {'.pcm', '.swiftmodule', 'timestamp'}
    except OSError:
        return False


def legacy_staging_artifact(path, root):
    """Recognize only completed old builds' generated demo or PyInstaller cache.

    The frozen source, candidate and log remain in place. A complete source
    inventory and exact payload binding are required, never just a folder name.
    """
    relative = path.relative_to(root)
    parts = relative.parts
    if (len(parts) < 5 or parts[:2] != ('result', 'build-staging') or
            not re.fullmatch(r'\d{8}-\d{6}-\d{6}', parts[2])):
        return None
    stage = root / 'result/build-staging' / parts[2]
    source = stage / 'source'
    payload = source / 'result/build-staging/current'
    cache = source / '.cache/pyinstaller'
    demo = payload / 'demo'
    if path not in (cache, demo):
        return None
    receipt = stage / 'candidate.json'
    try:
        if any(item.is_symlink() for item in (receipt, *receipt.parents) if item != root):
            return None
        raw = receipt.read_bytes()
        record = json.loads(raw)
        if (record.get('status') != 'built_not_published' or
                record.get('snapshot') != str(source) or record.get('payload') != str(payload)):
            return None
        files = record.get('source_hashes')
        if not isinstance(files, dict) or not files:
            return None
        for name, expected in files.items():
            relative_source = Path(name)
            if (relative_source.is_absolute() or '..' in relative_source.parts or
                    not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected)):
                return None
            file = source / relative_source
            if (file.is_relative_to(cache) or file.is_relative_to(payload) or
                    file.resolve() != file or not file.is_file() or
                    hashlib.sha256(file.read_bytes()).hexdigest() != expected):
                return None
        if path == cache:
            entries = list(cache.rglob('*'))
            if (not entries or any(item.is_symlink() for item in entries) or
                    not any(item.is_file() for item in entries) or
                    any(item.suffix not in {'.dat', '.dylib', '.so'} for item in entries if item.is_file())):
                return None
            category = 'legacy_staging_compiler_cache'
        else:
            declared = record.get('directory_artifacts', {}).get('demo', {})
            if declared.get('path') != str(demo) or not generated_demo(demo):
                return None
            actual = {item.relative_to(demo).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
                      for item in demo.rglob('*') if item.is_file()}
            if declared.get('files') != actual:
                return None
            category = 'legacy_staging_generated_demo'
        return {'category': category, 'authority': {
            'receipt': str(receipt), 'sha256': hashlib.sha256(raw).hexdigest()}}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def inventory(root=ROOT):
    root = Path(root).resolve()
    tracked = set(subprocess.run(['git', 'ls-files', '-z'], cwd=root, check=True,
                                capture_output=True).stdout.decode().split('\0'))
    candidates, skipped = [], []
    for directory, folders, _ in os.walk(root, followlinks=False):
        parent = Path(directory)
        folders[:] = [name for name in folders if not protected_path((parent/name).relative_to(root)) and not (parent / name).is_symlink()]
        for name in tuple(folders):
            path = parent / name
            relative = path.relative_to(root)
            acceptance = relative.parts[:2] == ('result', 'acceptance')
            category = None
            legacy = legacy_staging_artifact(path, root)
            if name in CACHE_NAMES:
                category = 'regenerable_compiler_cache'
            elif verified_native_module_cache(path, root):
                category = 'verified_native_module_cache'
            elif acceptance and name == 'demo' and parent.name == 'build' and generated_demo(path):
                category = 'manifest_verified_test_demo'
            elif acceptance and name.endswith('.app') and synthetic_app(path):
                category = 'compiled_test_app_copy'
            elif legacy:
                category = legacy['category']
            if category:
                if any(file == relative.as_posix() or file.startswith(relative.as_posix() + '/') for file in tracked):
                    continue
                if category == 'compiled_test_app_copy' and not internal_app_links(path):
                    continue
                if category != 'compiled_test_app_copy' and any(child.is_symlink() for child in path.rglob('*')):
                    if category != 'regenerable_compiler_cache' or not permitted_compiler_cache_links(path):
                        skipped.append({'source': str(path), 'reason': 'unverified_symlink_in_generated_output'})
                        continue
                item = {'source': str(path), 'category': category, 'stamp': tree_stamp(path)}
                if legacy:
                    item['authority'] = legacy['authority']
                candidates.append(item)
                folders.remove(name)
    return {'schema': 'researchhelper.development-cleanup.v1', 'root': str(root),
            'targets': candidates, 'skipped': skipped, 'bytes': sum(item['stamp']['bytes'] for item in candidates)}


def validate(plan, root, *, mounted=(), processes=''):
    root = Path(root).resolve()
    if plan.get('root') != str(root):
        raise RuntimeError('Cleanup plan belongs to a different repository')
    paths = []
    tracked = set(subprocess.run(['git', 'ls-files', '-z'], cwd=root, check=True,
                                capture_output=True).stdout.decode().split('\0'))
    for item in plan['targets']:
        path = Path(item['source'])
        if not path.is_relative_to(root) or path == root or path.is_symlink() or path.resolve() != path:
            raise RuntimeError('Unresolved or external cleanup target')
        relative = path.relative_to(root)
        if protected_path(relative):
            raise RuntimeError('Cleanup overlaps protected assets')
        if any(file == relative.as_posix() or file.startswith(relative.as_posix() + '/') for file in tracked):
            raise RuntimeError('Cleanup overlaps tracked source')
        if any(path == prior or path.is_relative_to(prior) or prior.is_relative_to(path) for prior in paths):
            raise RuntimeError('Overlapping cleanup targets')
        if any(path.is_relative_to(Path(point)) or Path(point).is_relative_to(path) for point in mounted):
            raise RuntimeError('Cleanup target is mounted')
        if str(path) in processes:
            raise RuntimeError('Cleanup target has a live process')
        category = item['category']
        acceptance = relative.parts[:2] == ('result', 'acceptance')
        allowed = category == 'regenerable_compiler_cache' and path.name in CACHE_NAMES
        allowed |= category == 'verified_native_module_cache' and verified_native_module_cache(path, root)
        allowed |= category == 'manifest_verified_test_demo' and acceptance and path.name == 'demo' and path.parent.name == 'build' and generated_demo(path)
        allowed |= category == 'compiled_test_app_copy' and acceptance and path.name.endswith('.app') and synthetic_app(path)
        if category.startswith('legacy_staging_'):
            legacy = legacy_staging_artifact(path, root)
            allowed = bool(legacy and legacy['category'] == category and
                           legacy['authority'] == item.get('authority'))
            stage = root / 'result/build-staging' / relative.parts[2] if legacy else None
            if stage and (str(stage) in processes or any(Path(point).is_relative_to(stage) for point in mounted)):
                raise RuntimeError('Historical build still has a live process or mount')
        if not allowed or tree_stamp(path) != item['stamp']:
            raise RuntimeError('Cleanup target changed or is not regenerable')
        if category == 'compiled_test_app_copy' and not internal_app_links(path):
            raise RuntimeError('Test application links leave their owned bundle')
        if category != 'compiled_test_app_copy' and any(child.is_symlink() for child in path.rglob('*')):
            if category != 'regenerable_compiler_cache' or not permitted_compiler_cache_links(path):
                raise RuntimeError('Cleanup target contains an unverified symlink')
        paths.append(path)
    return paths


def apply_inventory(plan, root=ROOT, trash=TRASH):
    root, trash = Path(root).resolve(), Path(trash).resolve()
    mounted_record = plistlib.loads(subprocess.run(['/usr/bin/hdiutil', 'info', '-plist'],
        check=True, capture_output=True, timeout=15).stdout)
    mounted = [entry['mount-point'] for image in mounted_record.get('images', [])
               for entry in image.get('system-entities', []) if entry.get('mount-point')]
    process_lines = subprocess.run(['ps', '-axo', 'pid=,command='], check=True, capture_output=True, text=True).stdout.splitlines()
    processes = '\n'.join(line for line in process_lines if int(line.split(maxsplit=1)[0]) != os.getpid())
    paths = validate(plan, root, mounted=mounted, processes=processes)
    if not paths:
        return {'status': 'nothing_regenerable_to_remove'}
    if trash.is_symlink() or not trash.is_dir() or root.stat().st_dev != trash.stat().st_dev:
        raise RuntimeError('Recovery directory must be an existing same-volume Trash')
    # Only specific test outputs move. Everything else, including receipts,
    # screenshots, original evidence and test Stores, retains its metadata identity.
    excluded = [path.relative_to(root).as_posix() for path in paths]
    def retained_stamp():
        rows = []
        for directory, folders, files in os.walk(root, followlinks=False):
            parent = Path(directory)
            folders[:] = [name for name in folders if (parent / name).relative_to(root).as_posix() not in excluded]
            if parent.is_relative_to(root / 'result/maintenance/development-cleanup-records'):
                folders.clear();continue
            for name in files:
                item = parent / name;info = item.lstat()
                rows.append((item.relative_to(root).as_posix(),info.st_ino,info.st_size,info.st_mtime_ns))
        return hashlib.sha256(json.dumps(sorted(rows), separators=(',', ':')).encode()).hexdigest()
    before = retained_stamp()
    external = [Path('/Applications/ResearchHelper.app'), Path('/Users/haoranxu/Library/Application Support/ResearchHelper'), Path('/Users/haoranxu/Desktop/demo/ResearchHelper demo')]
    external_before = {str(path): tree_stamp(path) for path in external}
    identity = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output = root / 'result/maintenance/development-cleanup-records' / identity
    destination = trash / ('ResearchHelper-development-' + identity)
    output.mkdir(parents=True, exist_ok=False);destination.mkdir(mode=0o700, exist_ok=False)
    record = dict(plan, status='moving_to_trash', permanent_deletion=False,
                  recovery_directory=str(destination), external_before=external_before)
    for item in record['targets']:
        item.update(destination=str(destination / Path(item['source']).relative_to(root)), moved=False)
    receipt = output / 'receipt.json'
    try:
        for item in record['targets']:
            receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            source, target = Path(item['source']), Path(item['destination'])
            if tree_stamp(source) != item['stamp'] or target.exists():
                raise RuntimeError('Cleanup target changed before move')
            target.parent.mkdir(parents=True, exist_ok=True);source.rename(target);item['moved']=True
            if tree_stamp(target) != item['stamp']:
                raise RuntimeError('Recovery copy identity changed')
        after = retained_stamp()
        external_after = {str(path): tree_stamp(path) for path in external}
        if before != after or external_before != external_after:
            raise RuntimeError('Protected files changed during cleanup')
        record.update(status='cleaned_recoverably', retained_files_unchanged=True,
                      external_unchanged=True, moved=len(paths), bytes_moved=plan['bytes'])
    except BaseException as error:
        record.update(status='partial_cleanup_requires_review', error=str(error));raise
    finally:
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        (destination / 'ResearchHelper-recovery.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return {'status': record['status'], 'moved': len(paths), 'bytes_moved': plan['bytes'],
            'receipt': str(receipt), 'recovery_directory': str(destination)}
