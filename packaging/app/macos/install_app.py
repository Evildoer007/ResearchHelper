"""Install an explicit verified candidate without changing its research Store.

Building and installation remain separate. Existing Apps require a retained
candidate manifest; unknown modifications are preserved rather than overwritten.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'packaging'))
from build_current import build_lock, verify_current_build, write_record

DESTINATION = Path('/Applications/ResearchHelper.app')
LSREGISTER = Path('/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def recorded_manifest(selected, root=ROOT):
    """Old App identity survives retirement of its DMG and generated demo."""
    selected, root = Path(selected), Path(root)
    if (not selected.is_absolute() or selected.resolve() != selected or selected.is_symlink()
            or not selected.is_relative_to(root / 'result/build-candidates')
            or selected.name != 'candidate.json'):
        raise RuntimeError('安装身份须来自本项目明确保留的候选回执')
    candidate = json.loads(selected.read_text())
    release_path = selected.parent / 'release-manifest.json'
    if (candidate.get('schema') != 'researchhelper.current-build.v1'
            or candidate.get('platform') != 'macos'
            or candidate.get('status') != 'built_local_candidate'
            or digest(release_path) != candidate['manifest_sha256']):
        raise RuntimeError('候选或发布清单身份不匹配')
    release = json.loads(release_path.read_text())
    binding = release['component_manifests']['app']
    manifest_path = selected.parent / 'app-manifest.json'
    if (Path(binding['path']) != manifest_path or manifest_path.is_symlink()
            or digest(manifest_path) != binding['sha256']):
        raise RuntimeError('App文件清单未绑定所选候选')
    manifest = json.loads(manifest_path.read_text())
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise RuntimeError('App缺少完整文件清单')
    for name, expected in files.items():
        path = PurePosixPath(name)
        if (path.is_absolute() or path.as_posix() != name or '..' in path.parts
                or '\\' in name or ':' in name or not name.startswith('Contents/')
                or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected)):
            raise RuntimeError('App清单含越界路径或无效摘要')
    return manifest


def bundle_files(bundle):
    bundle = Path(bundle)
    if bundle.is_symlink() or not bundle.is_dir() or bundle.resolve() != bundle:
        raise RuntimeError('App须为明确的普通目录，保留既有路径')
    files = {}
    for file in bundle.rglob('*'):
        if file.is_symlink():
            if file.readlink().is_absolute() or not file.resolve().is_relative_to(bundle):
                raise RuntimeError('App链接越出包内范围')
        if file.is_file():
            files[file.relative_to(bundle).as_posix()] = digest(file)
        elif not file.is_dir():
            raise RuntimeError('App包含非普通文件或失效链接')
    return files


def verify_bundle(bundle, manifest):
    if bundle_files(bundle) != manifest['files']:
        raise RuntimeError('App文件缺失、额外增加或内容修改，未覆盖')
    info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
    if (info.get('CFBundleIdentifier') != 'io.researchhelper.app'
            or info.get('CFBundleName') != 'ResearchHelper'
            or info.get('CFBundleExecutable') != 'ResearchHelper'
            or info.get('CFBundleShortVersionString') != manifest['version']):
        raise RuntimeError('App身份或版本不匹配')
    return manifest['files']


def signature(bundle):
    result = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
        capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('App签名核验失败：' + result.stderr.strip())


def require_closed(destination=DESTINATION):
    processes = subprocess.check_output(['/bin/ps', '-axo', 'pid=,comm='], text=True)
    if any(str(destination) + '/' in line for line in processes.splitlines()):
        raise RuntimeError('ResearchHelper正在运行，安装未开始；请先关闭App')


def refresh_icon_registration(destination=DESTINATION):
    if destination != DESTINATION or destination.is_symlink():
        raise RuntimeError('图标刷新仅限ResearchHelper安装目录')
    os.utime(destination, None)
    subprocess.run([str(LSREGISTER), '-f', str(destination)], check=True,
        capture_output=True, timeout=30)
    return dict(forced_registration=True, shared_cache_reset=False,
        desktop_process_restarted=False)


def promote(stage, destination, manifest, previous, recovery, preflight):
    """Recheck both identities immediately before recoverable atomic promotion."""
    verify_bundle(stage, manifest)
    preflight()
    exists = destination.exists() or destination.is_symlink()
    if previous is None:
        if exists:
            raise RuntimeError('复制期间出现既有App，未覆盖')
    else:
        if not exists:
            raise RuntimeError('既有App在复制期间变化，未覆盖')
        verify_bundle(destination, previous)
    if recovery.exists() or recovery.is_symlink():
        raise RuntimeError('App恢复位置已占用，未覆盖')
    if previous is not None:
        os.replace(destination, recovery)
    try:
        os.replace(stage, destination)
        verify_bundle(destination, manifest)
    except BaseException:
        # Unknown concurrent changes stay untouched; exact old/new bytes remain
        # recoverable if the identity cannot be established.
        if destination.exists():
            if bundle_files(destination) != manifest['files']:
                raise RuntimeError('更新期间App内容变化；旧App保留在恢复区')
            os.replace(destination, stage)
        if previous is not None:
            verify_bundle(recovery, previous)
            os.replace(recovery, destination)
        raise


def install(candidate_path=None, *, previous_candidate=None):
    """Callable installation step; never infer a release from modification time."""
    selected = candidate_path or os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not selected:
        raise RuntimeError('请先选择明确的已核验候选，构建不会自动安装App')
    with build_lock(ROOT):
        from reconcile_delivery import reconcile_downloads
        reconcile_downloads(ROOT,ROOT/'dist')
        candidate = verify_current_build(selected, ROOT)
        selected = Path(selected).resolve()
        manifest = recorded_manifest(selected)
        require_closed()
        previous = None
        if DESTINATION.exists() or DESTINATION.is_symlink():
            if previous_candidate is None:
                raise RuntimeError('既有App须先核对其明确来源候选，未覆盖')
            previous = recorded_manifest(previous_candidate)
            verify_bundle(DESTINATION, previous)
            signature(DESTINATION)
        recovery_root = Path.home() / '.Trash'
        if recovery_root.is_symlink() or not recovery_root.is_dir():
            raise RuntimeError('可恢复废纸篓不可用，未安装')
        if ROOT.stat().st_dev != DESTINATION.parent.stat().st_dev or recovery_root.stat().st_dev != ROOT.stat().st_dev:
            raise RuntimeError('安装与恢复须位于同一卷，未安装')
        output = ROOT / 'result/maintenance/app-installation' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        output.mkdir(parents=True, exist_ok=False)
        receipt = output / 'receipt.json'
        record = dict(status='started', candidate=str(selected), version=manifest['version'],
            destination=str(DESTINATION), production_store_touched=False,
            foreground_opened=False, model_calls=0, provider_calls=0,
            candidate_receipt_sha256=digest(selected), mount_detached=False,
            previous_candidate=str(previous_candidate) if previous_candidate else None,
            image_sha256=candidate['artifacts']['dmg']['sha256'])
        mount = output / 'mounted'
        mount.mkdir()
        # Desktop file providers can attach FinderInfo while a bundle is copied.
        # Stage beside the installation, on the same volume, not in the synced
        # source checkout. Preserve the image's signature; never re-sign here.
        stage_folder = Path(tempfile.mkdtemp(prefix='.ResearchHelper-install-', dir=DESTINATION.parent))
        stage = stage_folder / 'ResearchHelper.app'
        recovery_folder = Path(tempfile.mkdtemp(prefix='ResearchHelper-installed-app-', dir=recovery_root))
        recovery = recovery_folder / 'ResearchHelper.app'
        record['recovery'] = str(recovery) if previous is not None else None
        record['temporary_stage'] = str(stage_folder)
        write_record(receipt, record)
        attached = False
        try:
            image = candidate['artifacts']['dmg']['path']
            subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse',
                '-mountpoint', str(mount), image], check=True, capture_output=True, timeout=30)
            attached = True
            if not os.statvfs(mount).f_flag & os.ST_RDONLY:
                raise RuntimeError('安装镜像不是只读挂载，未覆盖')
            source = mount / 'ResearchHelper.app'
            verify_bundle(source, manifest)
            signature(source)
            shutil.copytree(source, stage, symlinks=True, copy_function=shutil.copy)
            verify_bundle(stage, manifest)
            signature(stage)
            assert verify_current_build(selected, ROOT) == candidate
            record['status'] = 'verified_before_promotion'
            write_record(receipt, record)
            promote(stage, DESTINATION, manifest, previous, recovery, require_closed)
            record['status'] = 'installed_pending_acceptance'
            write_record(receipt, record)
            signature(DESTINATION)
            record['icon_registration'] = refresh_icon_registration()
            record['installed_files'] = len(manifest['files'])
            record['status'] = 'installed_verified'
        except BaseException as error:
            record.update(status='failed', error=str(error))
            raise
        finally:
            if stage.exists():
                preserved = recovery_folder / 'staged-or-rolled-back.app'
                os.replace(stage, preserved)
                record['preserved_stage'] = str(preserved)
            if stage_folder.exists() and not any(stage_folder.iterdir()):
                stage_folder.rmdir()
            record['temporary_stage_removed'] = not stage_folder.exists()
            if attached:
                try:
                    subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)],
                        check=True, capture_output=True, timeout=30)
                    record['mount_detached'] = True
                except BaseException as error:
                    record.update(status='failed', detach_error=str(error))
                    write_record(receipt, record)
                    raise
            write_record(receipt, record)
        return dict(record, receipt=str(receipt))
