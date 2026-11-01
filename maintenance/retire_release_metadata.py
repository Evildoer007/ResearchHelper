"""Retire receipt-bound, metadata-only old archive directories recoverably."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'packaging'))
from build_current import verify_current_build, write_record, build_lock


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def archive_owners(root):
    owners = {}
    for receipt in (root/'result/build-candidates').glob('*/candidate.json'):
        if receipt.is_symlink():
            continue
        try:
            record = json.loads(receipt.read_text())
            manifest = receipt.parent/'release-manifest.json'
            if (record.get('schema') != 'researchhelper.current-build.v1' or manifest.is_symlink()
                    or digest(manifest) != record['manifest_sha256']):
                continue
            for key, kind in (('dmg', 'app'), ('skill', 'skill')):
                artifact = record.get('artifacts', {}).get(key, {})
                path = Path(artifact.get('path', ''))
                if (path.is_absolute() and path.parent.parent.parent == root/'versions'/kind
                        and artifact.get('sha256') == path.parent.name):
                    owners.setdefault(str(path.parent), dict(filename=path.name, sha256=artifact['sha256'],
                        version=path.parent.parent.name.removeprefix('v'), receipt=str(receipt)))
        except (OSError, KeyError, ValueError, TypeError):
            continue
    # Early layouts lack retained candidate manifests but have exact archive
    # retirement receipts. A completed, matching move is sufficient authority
    # for its remaining metadata; unknown paths and modified records stay put.
    for receipt in (root/'result/maintenance/release-retirement').glob('*/receipt.json'):
        if receipt.is_symlink():
            continue
        try:
            record = json.loads(receipt.read_text())
            if record.get('status') != 'retired_recoverably':
                continue
            moved = {item['source'] for item in record.get('moves', []) if item.get('moved') is True}
            for item in record.get('targets', []):
                path = Path(item.get('path', ''))
                if (item.get('category') != 'superseded_archive' or str(path) not in moved
                        or not path.is_absolute() or not path.parent.exists()
                        or str(path.parent) in owners or item.get('sha256') != path.parent.name):
                    continue
                kind = path.parent.parent.parent
                if kind not in (root/'versions/app', root/'versions/skill'):
                    continue
                owner = dict(filename=path.name, sha256=item['sha256'],
                    version=path.parent.parent.name.removeprefix('v'), receipt=str(receipt))
                if metadata_only(path.parent, owner):
                    owners[str(path.parent)] = owner
        except (OSError, KeyError, ValueError, TypeError):
            continue
    return owners


def metadata_only(folder, owner):
    """A matching record is required; edits and unknown files are retained."""
    if (folder.is_symlink() or not folder.is_dir() or not re.fullmatch('[0-9a-f]{64}', folder.name)
            or {entry.name for entry in folder.iterdir()} != {'artifact.json'}):
        return False
    file = folder/'artifact.json'
    if file.is_symlink() or not file.is_file():
        return False
    expected = {key: owner[key] for key in ('filename', 'sha256', 'version')}
    expected.update(status='retained_bytes', formal_release_verified=False)
    try:
        return json.loads(file.read_text()) == expected
    except (OSError, ValueError):
        return False


def inventory(candidate, root=ROOT):
    root, candidate = Path(root).resolve(), Path(candidate).resolve()
    if root != ROOT and not root.is_relative_to(ROOT/'result/acceptance'):
        raise RuntimeError('只清理ResearchHelper或其独立验收目录')
    current = verify_current_build(candidate, root)
    protected = {str(Path(item['path']).parent) for item in current['artifacts'].values()}
    owners, targets, skipped = archive_owners(root), [], []
    for kind in ('app', 'skill'):
        for folder in (root/'versions'/kind).glob('*/*'):
            if str(folder) in protected or not folder.is_dir():
                continue
            owner = owners.get(str(folder))
            if owner and metadata_only(folder, owner):
                stamp = folder.stat()
                targets.append(dict(source=str(folder), metadata_sha256=digest(folder/'artifact.json'),
                    inode=stamp.st_ino, modified_ns=stamp.st_mtime_ns, authority=owner['receipt']))
            else:
                skipped.append(str(folder))
    return dict(root=str(root), candidate=str(candidate), targets=sorted(targets, key=lambda row:row['source']),
                skipped=sorted(skipped))


def retire(candidate, root=ROOT, recovery=None):
    root = Path(root).resolve()
    if root != ROOT and not root.is_relative_to(ROOT/'result/acceptance'):
        raise RuntimeError('只清理ResearchHelper或其独立验收目录')
    with build_lock(root):
        plan = inventory(candidate, root)
        if not plan['targets']:
            return dict(status='nothing_to_retire', skipped=plan['skipped'])
        trash = Path.home()/'.Trash' if recovery is None else Path(recovery)
        if trash.is_symlink() or not trash.is_dir() or trash.stat().st_dev != root.stat().st_dev:
            raise RuntimeError('可恢复废纸篓不可用，未清理旧发布目录')
        output = root/'result/maintenance/release-metadata'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')/'receipt.json'
        output.parent.mkdir(parents=True, exist_ok=False)
        recovery = Path(tempfile.mkdtemp(prefix='ResearchHelper-release-metadata-', dir=trash))
        record = dict(plan, status='moving', permanent_deletion=False, recovery=str(recovery), moves=[])
        write_record(output, record)
        try:
            for item in plan['targets']:
                source = Path(item['source'])
                stamp = source.stat()
                if (source.is_symlink() or source.resolve() != source
                        or stamp.st_ino != item['inode'] or stamp.st_mtime_ns != item['modified_ns']
                        or {entry.name for entry in source.iterdir()} != {'artifact.json'}
                        or digest(source/'artifact.json') != item['metadata_sha256']):
                    raise RuntimeError('旧发布目录发生变化，停止清理')
                target = recovery/source.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
                if digest(target/'artifact.json') != item['metadata_sha256']:
                    raise RuntimeError('退役描述文件哈希不一致')
                record['moves'].append(dict(source=str(source), destination=str(target)))
                write_record(output, record)
            verify_current_build(candidate, root)
            record['status'] = 'retired_recoverably'
        except BaseException:
            record['status'] = 'partial_retirement_requires_review'
            raise
        finally:
            write_record(output, record)
        return dict(status=record['status'], moved=len(record['moves']), skipped=plan['skipped'], receipt=str(output))
