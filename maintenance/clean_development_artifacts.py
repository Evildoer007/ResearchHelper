"""One-time, recoverable cleanup of inspected ResearchHelper development debris.

No recursive deletion, release publication or backup compression is performed.
The explicit accepted candidate and all production/report assets stay in place.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
from candidate_acceptance import verify_candidate
from retire_closed_test_stores import referenced_cases

ROOT = Path('/Users/haoranxu/Desktop/ResearchHelper')
CANDIDATE = ROOT / 'result/build-staging/20261011-010238-284488/candidate.json'
TRASH = Path('/Users/haoranxu/.Trash')
BACKUPS = ('installation-backups', 'release-backups', 'demo-backups',
           'icon-release-backups', 'build-archives', 'development-archives')
RETAINED_UI = {'development-layout-ui-cGTQH3', 'native-session-WMR9sJ'}
PRESERVED_ROOTS = {'.git', '.skills', '.researchhelper', 'data', 'demo', 'dist', 'versions', 'result'}
BUILD_INTERMEDIATES = ('swift-cache', 'pyinstaller-work', 'runtime-source',
                      'researchhelper-server.spec', 'swift-overlay.json', 'redundant-module.modulemap')


def tree_identity(directory):
    files = {}
    for path in Path(directory).rglob('*'):
        if '__pycache__' in path.parts:
            continue
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink():
            files[relative] = 'symlink:' + os.readlink(path)
        elif path.is_file():
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def cleanup_plan(root=ROOT, candidate=CANDIDATE):
    root, candidate = Path(root).resolve(), Path(candidate).resolve()
    selected = []
    def add(path, category):
        if path.exists() and not path.is_symlink():
            selected.append({'source': str(path), 'category': category})
    for name in BACKUPS:
        add(root / 'result' / name, 'superseded_backup')
    staging = root / 'result/build-staging'
    for path in sorted(staging.iterdir()):
        if path.is_dir() and path != candidate.parent:
            add(path, 'superseded_candidate')
    acceptance = root / 'result/acceptance'
    folders = sorted(path for path in acceptance.iterdir() if path.is_dir() and not path.is_symlink())
    referenced = referenced_cases(root, (path.name for path in folders))
    for path in folders:
        if path.name not in RETAINED_UI and path.name not in referenced and not re.search(
                r'(?:^|[-_])(?:live|ifind|real|current|installed|retained)(?:[-_]|$)|^thesis-harness-delivery$',
                path.name, re.I):
            add(path, 'historical_test_workspace')
    add(root / 'result/maintenance/swift-cold-compile', 'compiler_cache')
    add(root / 'result/skill-source-test-lxxhv89b', 'temporary_test_workspace')
    add(root / '.cache', 'development_cache')
    add(candidate.parent / 'source/.cache', 'candidate_development_cache')
    payload = candidate.parent / 'source/result/build-staging/current'
    for name in BUILD_INTERMEDIATES:
        add(payload / name, 'candidate_build_intermediate')
    # Do not walk old backups, installed assets or candidate snapshots.
    for directory, folders, files in os.walk(root, followlinks=False):
        base = Path(directory)
        if base == root:
            folders[:] = [name for name in folders if name not in PRESERVED_ROOTS and name != '.cache']
        folders[:] = [name for name in folders if not (base / name).is_symlink()]
        for name in tuple(folders):
            if name in {'__pycache__', '.pytest_cache'}:
                add(base / name, 'python_cache'); folders.remove(name)
        if '.DS_Store' in files:
            add(base / '.DS_Store', 'finder_metadata')
    return selected


def validate_targets(plan, root, candidate, mounted=()):
    root, candidate = Path(root).resolve(), Path(candidate).resolve()
    protected = [root / name for name in PRESERVED_ROOTS - {'result'}]
    protected.extend((root / 'result/host-research', root / 'result/report-style-reference',
                      candidate, candidate.parent / 'source/packaging',
                      candidate.parent / 'source/core', candidate.parent / 'source/modules'))
    targets = []
    allowed_intermediates = {candidate.parent / 'source/result/build-staging/current' / name
                            for name in BUILD_INTERMEDIATES}
    allowed_intermediates.add(candidate.parent / 'source/.cache')
    for item in plan:
        path = Path(item['source'])
        if path.is_symlink() or path.resolve() != path or not path.is_relative_to(root) or path == root:
            raise RuntimeError('清理目标未解析或越界：' + str(path))
        if path.is_relative_to(candidate.parent) and path not in allowed_intermediates:
            raise RuntimeError('不能清理当前候选的源码或交付物：' + str(path))
        if any(path == keep or path.is_relative_to(keep) or keep.is_relative_to(path) for keep in protected):
            raise RuntimeError('清理目标覆盖保留资产：' + str(path))
        if any(Path(point).is_relative_to(path) or path.is_relative_to(Path(point)) for point in mounted):
            raise RuntimeError('清理目标仍被磁盘映像挂载：' + str(path))
        if any(path.is_relative_to(other) or other.is_relative_to(path) for other in targets):
            raise RuntimeError('清理目标相互重叠：' + str(path))
        targets.append(path)
    acceptance = root / 'result/acceptance'
    if acceptance in targets:
        raise RuntimeError('不能清理整个验收目录')
    cases = {path.relative_to(acceptance).parts[0] for path in targets if path.is_relative_to(acceptance)}
    if referenced_cases(root, cases):
        raise RuntimeError('清理目标仍被回归引用')


def main():
    verify_candidate(CANDIDATE, ROOT)
    plan = cleanup_plan()
    images = plistlib.loads(subprocess.run(['/usr/bin/hdiutil', 'info', '-plist'],
                           check=True, capture_output=True, timeout=15).stdout)
    mounted = [entity['mount-point'] for image in images.get('images', [])
               for entity in image.get('system-entities', []) if entity.get('mount-point')]
    validate_targets(plan, ROOT, CANDIDATE, mounted)
    processes = subprocess.run(['ps', '-axo', 'pid=,command='], check=True,
                               capture_output=True, text=True).stdout.splitlines()
    if any(item['source'] in line for item in plan for line in processes
           if int(line.strip().split(maxsplit=1)[0]) != os.getpid()):
        raise RuntimeError('目标目录存在活动进程，停止清理')
    protected = (ROOT / 'demo', Path('/Users/haoranxu/Desktop/demo/ResearchHelper demo'),
                 Path('/Applications/ResearchHelper.app'), ROOT / '.skills/researchhelper',
                 ROOT / 'dist', ROOT / 'versions', ROOT / 'data', ROOT / 'result/host-research')
    before = {str(path): tree_identity(path) for path in protected}
    identity = datetime.now().strftime('%Y%m%d-%H%M%S')
    output = ROOT / 'result/maintenance' / ('deep-clean-' + identity)
    destination = TRASH / ('ResearchHelper-cleanup-' + identity)
    output.mkdir(exist_ok=False); destination.mkdir(mode=0o700, exist_ok=False)
    if ROOT.stat().st_dev != destination.stat().st_dev:
        raise RuntimeError('清理恢复区不在同一磁盘，拒绝复制大量备份')
    size_before = int(subprocess.run(['du', '-sk', str(ROOT)], check=True, capture_output=True,
                                    text=True).stdout.split()[0]) * 1024
    for item in plan:
        item.update(destination=str(destination / Path(item['source']).relative_to(ROOT)), moved=False)
    receipt = {'status': 'moving_to_trash', 'candidate': str(CANDIDATE),
               'recovery_directory': str(destination), 'permanent_deletion': False,
               'project_bytes_before': size_before, 'moves': plan,
               'protected_before': before, 'optionhelper_modified': False}
    record = output / 'cleanup.json'
    record.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    try:
        for index, item in enumerate(plan, 1):
            source, target = Path(item['source']), Path(item['destination'])
            if not source.exists() or target.exists():
                raise RuntimeError('目标在盘点后变化：' + str(source))
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            item['moved'] = True
            if index % 100 == 0:
                record.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
                print('已清理' + str(index) + '项，恢复清单持续保存。', flush=True)
        verify_candidate(CANDIDATE, ROOT)
        after = {str(path): tree_identity(path) for path in protected}
        if after != before:
            raise RuntimeError('保留资产校验失败，详见恢复清单')
        receipt.update(status='cleaned_recoverably', protected_unchanged=True,
                       candidate_integrity_verified=True)
    except BaseException as error:
        receipt.update(status='partial_cleanup_requires_review', error=str(error))
        raise
    finally:
        receipt['project_bytes_after'] = int(subprocess.run(['du', '-sk', str(ROOT)], check=True,
            capture_output=True, text=True).stdout.split()[0]) * 1024
        receipt['removed_from_project_bytes'] = size_before - receipt['project_bytes_after']
        record.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        (destination / 'ResearchHelper-recovery.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({'status': receipt['status'], 'moved': len(plan),
                      'removed_from_project_bytes': receipt['removed_from_project_bytes'],
                      'receipt': str(record), 'recovery_directory': str(destination)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
