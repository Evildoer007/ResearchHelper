"""Recoverable App upgrade when the old release receipt is unavailable.

The existing signed ResearchHelper bundle is observed, not retroactively
declared a verified release. Preserve its exact bytes in Trash before replacing
it with the explicitly verified candidate. No production Store or UI is opened.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'packaging/app/macos'))
import install_app as native


def observe_bundle(bundle):
    bundle=Path(bundle)
    metadata=plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
    if (metadata.get('CFBundleIdentifier')!='io.researchhelper.app'
            or metadata.get('CFBundleName')!='ResearchHelper'
            or metadata.get('CFBundleExecutable')!='ResearchHelper'
            or not isinstance(metadata.get('CFBundleShortVersionString'),str)
            or not metadata['CFBundleShortVersionString']):
        raise RuntimeError('既有App不是ResearchHelper，未替换')
    native.signature(bundle)
    files=native.bundle_files(bundle)
    return dict(schema='researchhelper.observed-installed-bundle.v1',
        release_origin_verified=False,signature_verified=True,
        bundle_identifier=metadata['CFBundleIdentifier'],version=metadata['CFBundleShortVersionString'],files=files,
        inventory_sha256=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest())


def require_observed(bundle,expected_inventory_sha256):
    observed=observe_bundle(bundle)
    if observed['inventory_sha256']!=expected_inventory_sha256:
        raise RuntimeError('既有App与刚核对的完整文件清单不一致，未替换')
    return observed


def install(candidate_path,*,expected_existing_inventory_sha256):
    """Only the fixed ResearchHelper destination; not a generic installer."""
    selected=Path(candidate_path).resolve()
    with native.build_lock(ROOT):
        candidate=native.verify_current_build(selected,ROOT)
        manifest=native.recorded_manifest(selected)
        native.require_closed()
        previous=require_observed(native.DESTINATION,expected_existing_inventory_sha256)
        trash=Path('/Users/haoranxu/.Trash')
        if trash.is_symlink() or not trash.is_dir():raise RuntimeError('废纸篓不可用于恢复，未安装')
        if any(p.stat().st_dev!=ROOT.stat().st_dev for p in (trash,native.DESTINATION.parent)):
            raise RuntimeError('安装与恢复位置不在同卷，未安装')
        stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        output=ROOT/'result/maintenance/observed-app-installation'/stamp
        output.mkdir(parents=True,exist_ok=False)
        snapshot=output/'existing-bundle-snapshot.json'
        native.write_record(snapshot,previous)
        stage_folder=Path(tempfile.mkdtemp(prefix='.ResearchHelper-install-',dir=native.DESTINATION.parent))
        stage=stage_folder/'ResearchHelper.app'
        recovery_folder=Path(tempfile.mkdtemp(prefix='ResearchHelper-observed-app-',dir=trash))
        recovery=recovery_folder/'ResearchHelper.app'
        mount=output/'mounted';mount.mkdir()
        receipt=output/'receipt.json'
        record=dict(status='preparing',candidate=str(selected),destination=str(native.DESTINATION),
            previous_origin_verified=False,previous_snapshot=str(snapshot),
            previous_inventory_sha256=previous['inventory_sha256'],recovery=str(recovery),
            production_store_touched=False,foreground_opened=False,model_calls=0,provider_calls=0,
            image_sha256=candidate['artifacts']['dmg']['sha256'],mount_detached=False)
        attached=False
        try:
            native.write_record(receipt,record)
            subprocess.run(['/usr/bin/hdiutil','attach',candidate['artifacts']['dmg']['path'],
                '-readonly','-nobrowse','-mountpoint',str(mount)],check=True,capture_output=True,timeout=30)
            attached=True
            if not os.statvfs(mount).f_flag&os.ST_RDONLY:raise RuntimeError('安装镜像不是只读挂载')
            source=mount/'ResearchHelper.app'
            native.verify_bundle(source,manifest);native.signature(source)
            shutil.copytree(source,stage,symlinks=True,copy_function=shutil.copy)
            native.verify_bundle(stage,manifest);native.signature(stage)
            if native.verify_current_build(selected,ROOT)!=candidate:raise RuntimeError('候选在复制期间变化')
            require_observed(native.DESTINATION,expected_existing_inventory_sha256)
            native.promote(stage,native.DESTINATION,manifest,previous,recovery,native.require_closed)
            native.signature(native.DESTINATION)
            native.verify_bundle(recovery,previous)
            record.update(status='installed_verified',installed_files=len(manifest['files']),
                previous_bytes_preserved=True,icon_registration=native.refresh_icon_registration())
        except BaseException as error:
            record.update(status='failed',error_type=type(error).__name__)
            raise
        finally:
            if stage.exists():
                preserved=recovery_folder/'staged-or-rolled-back.app'
                os.replace(stage,preserved);record['preserved_stage']=str(preserved)
            if stage_folder.exists() and not any(stage_folder.iterdir()):stage_folder.rmdir()
            if attached:
                try:
                    subprocess.run(['/usr/bin/hdiutil','detach',str(mount)],check=True,capture_output=True,timeout=30)
                    record['mount_detached']=True
                except BaseException:
                    record['status']='failed';native.write_record(receipt,record);raise
            native.write_record(receipt,record)
        return dict(record,receipt=str(receipt))
