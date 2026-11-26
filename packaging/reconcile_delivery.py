"""Recognize and retire generated copies without changing canonical payloads."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging'))
from skill.install_project_skill import empty_extra_directories, manifest_record, verify_installation

NAME = re.compile(r'(?:agents|app|assets|core|licenses|modules|references|researchhelper) (?:[2-9]|[1-9]\d+)\Z')
COPY_NAME = re.compile(r'(README|SKILL|requirements|manifest) ([2-9]|[1-9]\d+)\.(md|lock|json)\Z')
COPY_EXTENSIONS = {'README': 'md', 'SKILL': 'md', 'requirements': 'lock', 'manifest': 'json'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_original(name):
    match = COPY_NAME.fullmatch(name)
    if not match or COPY_EXTENSIONS[match[1]] != match[3]:
        return None
    return match[1] + '.' + match[3]


def manifest_authority(path, root, installed_hash):
    """Valid JSON alone does not establish that an older copy was generated."""
    value = digest(path)
    if value == installed_hash:
        return 'installed_manifest'
    for candidate in sorted((root / 'result/build-candidates').glob('*/candidate.json')):
        if candidate.is_symlink() or candidate.resolve() != candidate:
            continue
        try:
            record = json.loads(candidate.read_text())
            if not isinstance(record,dict):continue
            release_path = candidate.parent / 'release-manifest.json'
            if (record.get('schema') != 'researchhelper.current-build.v1'
                    or release_path.is_symlink() or digest(release_path) != record['manifest_sha256']):
                continue
            binding = json.loads(release_path.read_text())['component_manifests']['skill']
            original = candidate.parent / 'skill-manifest.json'
            if (Path(binding['path']) == original and not original.is_symlink()
                    and binding['sha256'] == value and digest(original) == value):
                return str(candidate)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    raise RuntimeError('编号清单没有既有安装或历史候选的字节依据，保留副本')


def historical_copy_authority(path, target, root, installed_hash):
    """An older numbered file needs its own same-number recorded manifest.

    Similar filenames or any arbitrary historical hash do not suffice. This
    permits a genuine prior release's copy, while retaining unknown edits.
    """
    match = COPY_NAME.fullmatch(path.name)
    manifest_path = target / ('manifest ' + match[2] + '.json')
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError('旧编号副本缺少同编号发布清单，保留副本')
    authority = manifest_authority(manifest_path, root, installed_hash)
    record = manifest_record(manifest_path.read_bytes(), legacy=True)
    if record['files'].get(copy_original(path.name)) != digest(path):
        raise RuntimeError('编号副本与同编号历史清单内容不同，不自动清理')
    return authority


def skill_inventory(root=ROOT, *, preserve_unknown_empty=False):
    if type(preserve_unknown_empty) is not bool:
        raise ValueError("是否保留未知空目录须明确指定")
    root = Path(root).resolve()
    target = root / '.skills/researchhelper'
    if target.is_symlink() or target != target.resolve():
        raise RuntimeError('Skill目录不是受控普通路径')
    manifest_path = target / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError('Skill缺少普通清单文件，保留安装')
    manifest = manifest_record(manifest_path.read_bytes(), legacy=True)
    expected = set(manifest['files']) | {'manifest.json'}
    actual = {path.relative_to(target).as_posix() for path in target.rglob('*') if path.is_file()}
    extras = actual - expected
    if not expected.issubset(actual) or any(copy_original(name) is None for name in extras) or any(path.is_symlink() for path in target.rglob('*')):
        raise RuntimeError('存在未知文件、缺失文件或链接，保留安装')
    for name, value in manifest['files'].items():
        if digest(target / name) != value:
            raise RuntimeError('受控Skill文件已有修改，保留安装')
    manifest_hash = digest(target / 'manifest.json')
    copies = []
    for name in sorted(extras):
        path = target / name
        original = copy_original(name)
        if original == 'manifest.json':
            manifest_record(path.read_bytes(), legacy=True)
            authority = manifest_authority(path, root, manifest_hash)
        elif digest(path) != digest(target / original):
            authority = historical_copy_authority(path, target, root, manifest_hash)
        else:
            authority = 'installed_manifest'
        info = path.lstat()
        copies.append({'name': name, 'inode': info.st_ino, 'mtime_ns': info.st_mtime_ns,
                       'sha256': digest(path), 'bytes': info.st_size, 'authority': authority})
    names = empty_extra_directories(target, manifest)
    preserved=[name for name in names if not NAME.fullmatch(name)]
    if preserved and not preserve_unknown_empty:
        raise RuntimeError('存在未知空目录，不扩大清理范围')
    directories = []
    for name in names:
        if name in preserved:continue
        path = target / name
        info = path.lstat()
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise RuntimeError('副本不是普通空目录，保留原件')
        directories.append({'name': name, 'inode': info.st_ino, 'mtime_ns': info.st_mtime_ns})
    return {'schema': 'researchhelper.empty-skill-duplicates.v1', 'root': str(root),
            'manifest_sha256': manifest_hash, 'directories': directories, 'copies': copies,
            'preserved_empty_directories':preserved}



def _delete_skill_inventory(plan,root,directories,*,include_directories):
    from build_current import write_record
    target=root/'.skills/researchhelper'
    output=root/'result/maintenance/empty-skill-duplicates'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True,exist_ok=False);receipt=output/'receipt.json'
    record=dict(status='deleting_verified_generated_files',plan=plan,permanent_deletion=True,trash_used=False,deleted=[])
    write_record(receipt,record)
    try:
        for item in directories+plan['copies']:
            path=target/item['name'];info=path.lstat()
            valid=path.is_file() and digest(path)==item['sha256'] if 'sha256' in item else path.is_dir() and not any(path.iterdir())
            if path.is_symlink() or not valid or (info.st_ino,info.st_mtime_ns)!=(item['inode'],item['mtime_ns']):
                raise RuntimeError('副本已变化，保留剩余文件')
            if 'sha256' in item:path.unlink()
            else:path.rmdir()
            record['deleted'].append(item['name']);write_record(receipt,record)
        assert verify_installation(target,preserve_empty_directories=not include_directories or bool(plan['preserved_empty_directories']))==plan['manifest_sha256']
        record.update(status='retired_permanently',skill_payload_unchanged=True)
    except BaseException as error:
        record.update(status='partial_deletion_requires_review',error=str(error));raise
    finally:write_record(receipt,record)
    return dict(status=record['status'],directories=len(directories),copies=len(plan['copies']),
        pending_empty_directories=len(plan['directories'])-len(directories),
        bytes=sum(item['bytes'] for item in plan['copies']),receipt=str(receipt),skill_payload_unchanged=True)


def apply_skill_inventory(plan, *, root=ROOT, recovery=None, include_directories=True,preserve_unknown_empty=False):
    root = Path(root).resolve()
    if type(include_directories) is not bool:
        raise ValueError('是否处理空目录须明确指定')
    if skill_inventory(root,preserve_unknown_empty=preserve_unknown_empty) != plan:
        raise RuntimeError('Skill或目录状态已变化，未清理')
    directories = plan['directories'] if include_directories else []
    if not directories and not plan['copies']:
        return {'status': 'nothing_to_retire', 'directories': 0}
    from release_retention import permanent_cleanup_enabled
    if recovery is None and permanent_cleanup_enabled(root):
        return _delete_skill_inventory(plan,root,directories,include_directories=include_directories)
    recovery = Path(recovery or Path.home() / '.Trash').resolve()
    if recovery == root or (recovery.is_relative_to(root) and recovery != root / 'result/skill-installation-recovery'):
        raise RuntimeError('恢复区不能位于开发仓库')
    recovery.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='ResearchHelper-empty-skill-duplicates-', dir=recovery))
    target = root / '.skills/researchhelper'
    moved = []
    try:
        for item in directories + plan['copies']:
            path = target / item['name']
            info = path.lstat()
            valid = (path.is_file() and digest(path) == item['sha256']) if 'sha256' in item else (path.is_dir() and not any(path.iterdir()))
            if path.is_symlink() or not valid or (info.st_ino, info.st_mtime_ns) != (item['inode'], item['mtime_ns']):
                raise RuntimeError('空目录已变化，停止并恢复已移动目录')
            os.replace(path, folder / item['name'])
            moved.append(item['name'])
        assert verify_installation(target, preserve_empty_directories=not include_directories or bool(plan['preserved_empty_directories'])) == plan['manifest_sha256']
    except BaseException:
        for name in reversed(moved):
            if (target / name).exists():
                raise RuntimeError('恢复位置被占用，保留恢复区等待核对')
            os.replace(folder / name, target / name)
        raise
    record = {'status': 'retired_recoverably', 'directories': len(directories),
              'pending_empty_directories': len(plan['directories'])-len(directories),
              'copies': len(plan['copies']), 'bytes': sum(item['bytes'] for item in plan['copies']),
              'recovery': str(folder), 'plan': plan, 'skill_payload_unchanged': True}
    output = root / 'result/maintenance/empty-skill-duplicates' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    record['receipt'] = str(output / 'receipt.json')
    return record


def _recovery_location(root):
    return Path.home()/'.Trash' if sys.platform=='darwin' else root/'result/skill-installation-recovery'


def reconcile_project_skill(root=ROOT, *, recovery=None, expected_manifest=None):
    """Called under the build lock; no research store is part of this target."""
    root=Path(root).resolve();target=root/'.skills/researchhelper'
    if not target.exists() and not target.is_symlink() and expected_manifest is None:
        return dict(status='not_installed',copies=0,directories=0)
    plan = skill_inventory(root,preserve_unknown_empty=True)
    if expected_manifest is not None and plan['manifest_sha256'] != expected_manifest:
        raise RuntimeError('项目级Skill在安装后变化，保留原件')
    return apply_skill_inventory(plan,root=root,recovery=recovery,preserve_unknown_empty=True)


DOWNLOAD_COPY=re.compile(r'(ResearchHelper-Skill-\d+\.\d+\.\d+|ResearchHelper-\d+\.\d+\.\d+-(?:arm64|windows-x64-setup)) ([2-9]|[1-9]\d+)\.(zip|dmg|exe)\Z')


def _download_role(name):
    return {'.zip':'skill', '.dmg':'dmg', '.exe':'setup'}.get(Path(name).suffix)


def _archive_authority(file,original,root,value,*,accepted_only=False):
    """Use retained release identity even after superseded bytes were retired."""
    role = _download_role(original)
    from release_retention import _accepted_current_candidate
    for path in sorted((root/'result/build-candidates').glob('*/candidate.json')):
        if path.is_symlink() or path.resolve()!=path:continue
        try:
            candidate=json.loads(path.read_text())
            if not isinstance(candidate,dict):continue
            release_path=path.parent/'release-manifest.json'
            if (candidate.get('schema')!='researchhelper.current-build.v1' or release_path.is_symlink()
                    or digest(release_path)!=candidate['manifest_sha256']):continue
            release=json.loads(release_path.read_text());artifact=candidate['artifacts'][role]
            if (release['artifacts'][role]==artifact and Path(artifact['path']).name==original
                    and artifact['sha256']==value and artifact['bytes']==file.stat().st_size
                    and (not accepted_only or _accepted_current_candidate(root,path))):
                return str(path)
        except (OSError,ValueError,KeyError,TypeError):continue
    raise RuntimeError('编号交付物没有对应的发布字节依据，保留文件：'+file.name)


def download_inventory(root,target):
    """Read canonical entries and receipt-bound numbered copies in one pass."""
    from build_current import delivery_entries

    root, target = Path(root).resolve(), Path(target)
    copies = []

    def recorded_copy(file, allowed):
        if target not in (root/'dist', root/'result/windows-candidate'):
            return False
        match = DOWNLOAD_COPY.fullmatch(file.name)
        if match is None:
            return False
        original = match[1]+'.'+match[3]
        if not allowed.fullmatch(original):
            return False
        info = file.lstat()
        if not stat.S_ISREG(info.st_mode):
            return False
        value = digest(file)
        try:
            authority = _archive_authority(file, original, root, value)
        except RuntimeError:
            return False
        current = file.lstat()
        if any(getattr(current, field) != getattr(info, field)
               for field in ('st_mode','st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')):
            raise RuntimeError('编号交付物在核对期间变化，未采用：'+file.name)
        copies.append(dict(name=file.name, original=original, sha256=value, bytes=info.st_size,
            inode=info.st_ino, mtime_ns=info.st_mtime_ns, authority=authority))
        return True

    files = delivery_entries(target, root, recorded_copy=recorded_copy)
    return dict(root=str(root), target=str(target), files=[file.name for file in files], copies=copies)


def _has_download_replacement(item, root, target, files):
    original = item['original']
    if original in files:
        path = target / original
        return path.is_file() and not path.is_symlink()
    role = _download_role(original)
    record = json.loads(Path(item['authority']).read_text())
    archived = Path(record['artifacts'][role]['path'])
    if (archived.is_relative_to(root/'versions') and archived.resolve() == archived and archived.is_file()
            and archived.parent.name == item['sha256'] and digest(archived) == item['sha256']):
        return True
    # Across a version change the same name can disappear. An accepted newer
    # payload of the same kind is sufficient when old archives were retired.
    previous = tuple(map(int, re.search(r'\d+\.\d+\.\d+', original)[0].split('.')))
    for name in files:
        if _download_role(name) != role:
            continue
        version = tuple(map(int, re.search(r'\d+\.\d+\.\d+', name)[0].split('.')))
        replacement = target / name
        if version <= previous or replacement.is_symlink() or not replacement.is_file():
            continue
        try:
            _archive_authority(replacement, name, root, digest(replacement), accepted_only=True)
        except RuntimeError:
            continue
        return True
    return False



def _delete_download_copies(plan,root,target):
    from build_current import write_record
    output=root/'result/maintenance/generated-download-copies'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True,exist_ok=False);receipt=output/'receipt.json'
    record=dict(status='deleting_verified_generated_files',plan=plan,permanent_deletion=True,trash_used=False,deleted=[])
    write_record(receipt,record)
    try:
        for item in plan['copies']:
            path=target/item['name'];info=path.lstat()
            if (path.is_symlink() or (info.st_ino,info.st_mtime_ns)!=(item['inode'],item['mtime_ns'])
                    or digest(path)!=item['sha256']):raise RuntimeError('编号副本已变化，保留剩余文件')
            path.unlink();record['deleted'].append(item['name']);write_record(receipt,record)
        record['pending_copies'] = [item['name'] for item in download_inventory(root,target)['copies']]
        record['status']='retired_permanently'
    except BaseException as error:
        record.update(status='partial_deletion_requires_review',error=str(error));raise
    finally:write_record(receipt,record)
    return dict(status=record['status'],copies=len(record['deleted']),receipt=str(receipt))


def reconcile_downloads(root,target, *, recovery=None):
    """Retire only exact recorded numbered copies; never modify canonical bytes."""
    root=Path(root).resolve();target=Path(target);plan=download_inventory(root,target)
    if not plan['copies']:return dict(status='nothing_to_retire',copies=0)
    if download_inventory(root,target)!=plan:raise RuntimeError('交付副本在核对期间变化，未移动')
    from release_retention import _require_inactive, permanent_cleanup_enabled
    if sys.platform != 'win32':
        _require_inactive({'targets':[{'path':str(target/item['name'])} for item in plan['copies']]})
    if any(not _has_download_replacement(item, root, target, plan['files']) for item in plan['copies']):
        raise RuntimeError('编号交付物缺少对应正式文件、归档或已验收的新版，保留可恢复副本')
    if recovery is None and permanent_cleanup_enabled(root):
        return _delete_download_copies(plan,root,target)
    recovery=Path(recovery or _recovery_location(root))
    if recovery.is_symlink() or recovery.resolve()!=recovery:raise RuntimeError('恢复位置不是普通路径，未移动')
    if recovery==root or (recovery.is_relative_to(root) and recovery!=root/'result/skill-installation-recovery'):
        raise RuntimeError('恢复位置不能覆盖开发仓库，未移动')
    recovery.mkdir(parents=True,exist_ok=True)
    if recovery.stat().st_dev!=target.stat().st_dev:raise RuntimeError('恢复位置不在同一卷，未移动')
    folder=Path(tempfile.mkdtemp(prefix='ResearchHelper-download-copies-',dir=recovery))
    output=root/'result/maintenance/generated-download-copies'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True,exist_ok=False);receipt=output/'receipt.json'
    record=dict(status='verified_before_move',plan=plan,recovery=str(folder),moves=[],permanent_deletion=False)
    def save():receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    save()
    try:
        for item in plan['copies']:
            file=target/item['name'];info=file.lstat()
            if (file.is_symlink() or (info.st_ino,info.st_mtime_ns)!=(item['inode'],item['mtime_ns'])
                    or digest(file)!=item['sha256']):raise RuntimeError('编号副本已变化，停止移动')
            os.replace(file,folder/file.name);record['moves'].append(file.name);save()
            if digest(folder/file.name)!=item['sha256']:raise RuntimeError('恢复副本摘要不一致')
        record['pending_copies'] = [item['name'] for item in download_inventory(root,target)['copies']]
        record['status']='retired_recoverably'
    except BaseException:
        for name in reversed(record['moves']):
            if (target/name).exists() or (target/name).is_symlink():
                record['status']='recovery_conflict';save();raise RuntimeError('恢复位置已被占用，副本保留在恢复区')
            os.replace(folder/name,target/name)
        record['status']='rolled_back';save();raise
    save()
    return dict(status=record['status'],copies=len(record['moves']),receipt=str(receipt))
