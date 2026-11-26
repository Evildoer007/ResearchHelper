"""Replace only a manifest-verified generated project Skill, never its Store."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_PARTS = {'.git', 'data', 'result', 'credentials', '.researchhelper', '__pycache__'}
PRIVATE_FILES = {'config.local.json', 'model-settings.json', 'source-settings.json',
                 'execution-policy.json', 'local-account.json', 'local-sessions.json', 'passwords.json'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ordinary(path):
    path = Path(path)
    if not path.is_absolute() or path != path.resolve():
        raise RuntimeError('项目Skill路径必须是无符号链接的绝对路径')
    return path


def directory_link(path):
    return path.is_symlink() or path.is_junction()


def payload_name(name):
    if not isinstance(name, str):
        raise RuntimeError('Skill清单文件名无效')
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or path.as_posix() != name or '..' in path.parts or
            '\\' in name or ':' in name or any(ord(char) < 32 for char in name) or
            any(part.startswith('.') or part in PRIVATE_PARTS for part in path.parts) or
            path.suffix in {'.db', '.sqlite', '.sqlite3', '.secret'} or
            path.name in PRIVATE_FILES or name == 'manifest.json'):
        raise RuntimeError('Skill清单包含私有或越界文件')
    return name


def manifest_record(raw, *, legacy=False):
    try:
        record = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise RuntimeError('Skill清单无法读取') from error
    if (not isinstance(record, dict) or not re.fullmatch(r'\d+\.\d+\.\d+', str(record.get('version', ''))) or
            record.get('project_level') is not True or record.get('offline_default') is not True or
            record.get('starts_second_model') is not False or not isinstance(record.get('files'), dict) or
            not record['files']):
        raise RuntimeError('Skill清单不属于受控项目级安装')
    runtime = record.get('runtime')
    if runtime is not None and (not isinstance(runtime, dict) or runtime.get('protocol_id') != 'researchhelper.module-request'):
        raise RuntimeError('Skill运行协议不匹配')
    if not legacy and (runtime is None or 'core/session.py' not in record['files']):
        raise RuntimeError('新Skill缺少共享运行入口或协议')
    for name, expected in record['files'].items():
        payload_name(name)
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise RuntimeError('Skill文件摘要无效')
    if 'SKILL.md' not in record['files']:
        raise RuntimeError('Skill缺少指令入口')
    return record


def empty_extra_directories(target, record):
    expected = {parent.as_posix() for name in set(record['files']) | {'manifest.json'}
                for parent in PurePosixPath(name).parents if parent.as_posix() != '.'}
    extra = sorted(file.relative_to(target).as_posix() for file in target.rglob('*')
                   if file.is_dir() and not directory_link(file) and file.relative_to(target).as_posix() not in expected)
    for name in extra:
        payload_name(name + '/empty-directory-marker')
        if any((target / name).iterdir()):
            raise RuntimeError('未知目录不是空目录，保留原安装')
    return extra


def verify_installation(target, *, preserve_empty_directories=False):
    if directory_link(target) or not target.is_dir():
        raise RuntimeError('既有Skill不是受控普通目录，拒绝覆盖')
    raw = target / 'manifest.json'
    if raw.is_symlink() or not raw.is_file():
        raise RuntimeError('既有Skill缺少受控清单，拒绝覆盖')
    record = manifest_record(raw.read_bytes(), legacy=True)
    expected_files = set(record['files']) | {'manifest.json'}
    expected_directories = {parent.as_posix() for name in expected_files for parent in PurePosixPath(name).parents
                            if parent.as_posix() != '.'}
    actual_files, actual_directories = set(), set()
    for file in target.rglob('*'):
        name = file.relative_to(target).as_posix()
        if directory_link(file):
            raise RuntimeError('既有Skill含链接，拒绝覆盖')
        if file.is_file():
            actual_files.add(name)
        elif file.is_dir():
            actual_directories.add(name)
        else:
            raise RuntimeError('既有Skill含非普通文件，拒绝覆盖')
    if actual_files != expected_files or not expected_directories.issubset(actual_directories):
        raise RuntimeError('既有Skill含未知文件或目录，保留原安装')
    if actual_directories != expected_directories:
        if not preserve_empty_directories:
            raise RuntimeError('既有Skill含未知文件或目录，保留原安装')
        empty_extra_directories(target, record)
    for name, expected in record['files'].items():
        if digest(target / name) != expected:
            raise RuntimeError('既有Skill有本地修改，保留原安装')
    return digest(raw)


def verify_archive(archive, expected_sha256):
    archive = ordinary(archive)
    if not archive.is_file() or digest(archive) != expected_sha256:
        raise RuntimeError('Skill安装包摘要不匹配')
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        if len(names) != len(set(names)) or sum(item.file_size for item in package.infolist()) > 100 * 1024 * 1024:
            raise RuntimeError('Skill安装包包含重复文件或超出大小上限')
        try:
            raw = package.read('researchhelper/manifest.json')
        except KeyError as error:
            raise RuntimeError('Skill安装包缺少清单') from error
        record = manifest_record(raw)
        expected = {'researchhelper/' + name for name in record['files']} | {'researchhelper/manifest.json'}
        if set(names) != expected:
            raise RuntimeError('Skill安装包文件集合不匹配')
        for item in package.infolist():
            if item.is_dir() or (item.external_attr >> 16) & 0o170000 not in (0, 0o100000):
                raise RuntimeError('Skill安装包含非普通文件')
        for name, value in record['files'].items():
            if hashlib.sha256(package.read('researchhelper/' + name)).hexdigest() != value:
                raise RuntimeError('Skill安装包内容与清单不一致')
    return archive, raw, record


def check_discovery(discovery, target):
    for parent in (target.parent, discovery.parent.parent, discovery.parent):
        if directory_link(parent) or parent.exists() and not parent.is_dir():
            raise RuntimeError('项目Skill或发现目录被其他路径占用')
    if directory_link(discovery):
        if discovery.resolve() != target:
            raise RuntimeError('项目发现入口指向其他Skill，保留原入口')
    elif discovery.exists():
        raise RuntimeError('项目发现入口已有普通文件或目录，拒绝覆盖')


def _windows_discovery_junction(discovery, target):
    from _winapi import CreateJunction

    # The staging folder is ours even if junction creation only partially
    # succeeds. A Windows rename refuses an occupied discovery path.
    with tempfile.TemporaryDirectory(prefix='.researchhelper-discovery-', dir=discovery.parent) as folder:
        stage = Path(folder) / 'link'
        CreateJunction(str(target), str(stage))
        if not stage.is_junction() or stage.resolve() != target:
            raise RuntimeError('Windows项目Skill目录联接不匹配')
        stage.rename(discovery)


def publish_discovery(discovery, target):
    check_discovery(discovery, target)
    if directory_link(discovery):
        return False
    discovery.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == 'win32':
        _windows_discovery_junction(discovery, target)
    else:
        discovery.symlink_to('../../.skills/researchhelper', target_is_directory=True)
    return True


def transfer_installation(source, destination, expected, *, preserve_empty_directories):
    """Transfer verified files without a cross-folder directory rename.

    A partial transfer rolls back only the bytes this operation moved. Unknown
    concurrent files are never overwritten or removed. This matches the
    repository's existing release-retention approach to directory transfers.
    """
    verify=lambda path:verify_installation(path,preserve_empty_directories=preserve_empty_directories)
    if verify(source)!=expected:raise RuntimeError('Skill转移前的清单已变化')
    record=manifest_record((source/'manifest.json').read_bytes(),legacy=True)
    files=dict(record['files'],**{'manifest.json':expected})
    directories=sorted([source]+[path for path in source.rglob('*') if path.is_dir()],key=lambda path:len(path.parts))
    destination.mkdir(exist_ok=False);moved=[]
    try:
        for directory in directories:
            target=destination/directory.relative_to(source)
            target.mkdir(parents=True,exist_ok=True)
            shutil.copystat(directory,target,follow_symlinks=False)
        for name,value in sorted(files.items()):
            original=source/name;target=destination/name
            if original.is_symlink() or not original.is_file() or digest(original)!=value:
                raise RuntimeError('Skill文件在转移期间变化，停止更新')
            os.replace(original,target);moved.append(name)
        if verify(destination)!=expected:raise RuntimeError('Skill转移后的清单不一致')
        for directory in reversed(directories):directory.rmdir()
    except BaseException:
        for directory in directories:directory.mkdir(parents=True,exist_ok=True)
        for name in reversed(moved):
            original=source/name;target=destination/name
            if original.exists() or original.is_symlink() or target.is_symlink() or digest(target)!=files[name]:
                raise RuntimeError('Skill转移期间出现其他修改，保留安装与备份等待核对') from None
            os.replace(target,original)
        # rmdir preserves any unexpected file instead of erasing its directory.
        for directory in sorted([path for path in destination.rglob('*') if path.is_dir()],key=lambda path:len(path.parts),reverse=True):
            if not any(directory.iterdir()):directory.rmdir()
        if not any(destination.iterdir()):destination.rmdir()
        raise


def install_project_skill(archive, *, project_root=ROOT, expected_sha256, recovery_root=None,
                          preserve_empty_directories=False):
    """Use already verified bytes; no rebuild, model, global install or data migration."""
    if not isinstance(preserve_empty_directories, bool):
        raise RuntimeError('空目录保留选项必须为布尔值')
    root = ordinary(project_root)
    if not root.is_dir() or root == Path(root.anchor) or root == Path.home():
        raise RuntimeError('项目Skill须使用明确的研究项目目录')
    archive, raw, manifest = verify_archive(archive, expected_sha256)
    target = root / '.skills/researchhelper'
    discovery = root / '.agents/skills/researchhelper'
    check_discovery(discovery, target)
    verify = lambda path: verify_installation(path, preserve_empty_directories=preserve_empty_directories)
    before = verify(target) if target.exists() or directory_link(target) else None
    extras = empty_extra_directories(target, manifest_record((target / 'manifest.json').read_bytes(), legacy=True)) if before else []
    if any(name == extra or name.startswith(extra + '/') for extra in extras for name in manifest['files']):
        raise RuntimeError('新安装会占用未知空目录，保留原安装')
    desired = hashlib.sha256(raw).hexdigest()
    record = {'status': 'already_current' if before == desired else 'installed', 'installation': str(target),
              'manifest_sha256': desired, 'archive_sha256': expected_sha256, 'files': len(manifest['files']),
              'discovery': str(discovery), 'global_installation_changed': False,
              'research_data_changed': False, 'recovery': None, 'preserved_empty_directories': extras}
    if before == desired:
        publish_discovery(discovery, target)
        return record
    recovery_root = ordinary(recovery_root if recovery_root is not None else root / 'result/skill-installation-recovery')
    if recovery_root == target or recovery_root.is_relative_to(target) or target.is_relative_to(recovery_root):
        raise RuntimeError('Skill恢复目录不能覆盖安装目录')
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.researchhelper-install-', dir=target.parent))
    backup = None
    promoted = False
    discovery_created = False
    try:
        with zipfile.ZipFile(archive) as package:
            for name in manifest['files']:
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(package.read('researchhelper/' + name))
        (stage / 'manifest.json').write_bytes(raw)
        for name in extras:
            destination = stage / name
            destination.mkdir(parents=True, exist_ok=False)
            shutil.copystat(target / name, destination, follow_symlinks=False)
        assert verify(stage) == desired
        if digest(archive) != expected_sha256:
            raise RuntimeError('安装包在复制期间变化，未更新Skill')
        check_discovery(discovery, target)
        if before is not None:
            if verify(target) != before or empty_extra_directories(target,
                    manifest_record((target / 'manifest.json').read_bytes(), legacy=True)) != extras:
                raise RuntimeError('既有Skill在复制期间变化，未覆盖')
            recovery_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            backup = recovery_root / ('ResearchHelper-project-skill-' + stamp + '-' + before[:12])
            if backup.exists():
                raise RuntimeError('恢复位置已存在，拒绝覆盖')
            transfer_installation(target,backup,before,preserve_empty_directories=preserve_empty_directories)
        elif target.exists() or directory_link(target):
            raise RuntimeError('复制期间出现既有Skill，未覆盖')
        os.replace(stage, target)
        promoted = True
        discovery_created = publish_discovery(discovery, target)
        check_discovery(discovery, target)
        assert verify(target) == desired
        record['recovery'] = str(backup) if backup else None
        return record
    except BaseException:
        # Revert only exact bytes owned by this transaction. Concurrent user
        # modifications are preserved with the prior managed copy recoverable.
        if promoted:
            if verify(target) != desired:
                raise RuntimeError('新Skill在更新期间被修改，未覆盖；旧副本位于' + str(backup))
            os.replace(target, stage)
        if backup and backup.exists() and not target.exists() and not directory_link(target):
            transfer_installation(backup,target,before,preserve_empty_directories=preserve_empty_directories)
        if discovery_created and directory_link(discovery) and discovery.resolve() == target:
            if discovery.is_junction():
                discovery.rmdir()
            else:
                discovery.unlink()
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
