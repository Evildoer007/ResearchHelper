"""Explicit candidate selection and immutable identity for background acceptance."""
import json
import os
from pathlib import Path
import sys
import plistlib
import subprocess


def selected_candidate(root):
    value = os.environ.get('RESEARCHHELPER_CANDIDATE_RECEIPT')
    if not value:
        raise RuntimeError('必须明确指定候选回执，验收不按最新修改时间猜测')
    path = Path(value).resolve()
    root = Path(root).resolve()
    if not path.is_relative_to(root / 'result/build-staging') or path.name != 'candidate.json':
        raise RuntimeError('候选回执须位于ResearchHelper构建目录')
    return path


def verify_candidate(path, root):
    root, path = Path(root).resolve(), Path(path).resolve()
    sys.path.insert(0, str(root / 'packaging'))
    from source_snapshot import digest, source_files
    from artifact_integrity import verify_directory
    candidate = json.loads(path.read_text())
    if candidate.get('status') != 'built_not_published' or candidate.get('platform') != 'macos':
        raise RuntimeError('仅接受完整macOS候选用于本机验收')
    snapshot, payload = Path(candidate['snapshot']).resolve(), Path(candidate['payload']).resolve()
    if snapshot != path.parent / 'source' or not payload.is_relative_to(snapshot):
        raise RuntimeError('候选快照或载荷不属于该回执')
    inventory = candidate['source_hashes']
    if set(inventory) != {file.relative_to(root).as_posix() for file in source_files(root)}:
        raise RuntimeError('候选源码清单与当前明确源映射不一致')
    for name, expected in inventory.items():
        if digest(root / name) != expected or digest(snapshot / name) != expected:
            raise RuntimeError('候选源码身份不匹配：' + name)
    manifest = payload / 'release-manifest.json'
    if digest(manifest) != candidate['manifest_sha256']:
        raise RuntimeError('候选发布清单已经变化')
    release = json.loads(manifest.read_text())
    from build_all import verify_release_binding
    verify_release_binding(release, payload, 'macos')
    for key in ('artifacts', 'directory_artifacts'):
        if release.get(key) != candidate.get(key):
            raise RuntimeError('候选与发布清单的产物身份不一致')
    if not {'dmg', 'skill'} <= candidate['artifacts'].keys() or 'demo' not in candidate.get('directory_artifacts', {}):
        raise RuntimeError('候选缺少App、Skill或demo身份')
    for item in candidate['artifacts'].values():
        artifact = Path(item['path'])
        if not artifact.resolve().is_relative_to(payload) or digest(artifact) != item['sha256']:
            raise RuntimeError('候选产物已经变化或越界')
    for item in candidate['directory_artifacts'].values():
        verify_directory(item, payload)
    return candidate


def verify_mounted_bundle(bundle, candidate, mount_info=None):
    bundle = Path(bundle).resolve()
    if mount_info is None:
        probe = subprocess.run(['/usr/bin/hdiutil', 'info', '-plist'],
                               check=True, capture_output=True, timeout=15)
        mount_info = plistlib.loads(probe.stdout)
    image_path = Path(candidate['artifacts']['dmg']['path']).resolve()
    for image in mount_info.get('images', []):
        if Path(image.get('image-path', '')).resolve() != image_path:
            continue
        for entity in image.get('system-entities', []):
            point = entity.get('mount-point')
            if point and bundle == Path(point).resolve() / 'ResearchHelper.app':
                if not os.statvfs(bundle).f_flag & os.ST_RDONLY:
                    raise RuntimeError('报告验收的DMG挂载不是只读')
                return
    raise RuntimeError('包内后端不是来自该候选的只读DMG')
