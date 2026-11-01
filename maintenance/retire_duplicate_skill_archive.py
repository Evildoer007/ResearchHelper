"""Recoverably retire an exact duplicate of a recorded superseded Skill build.

Unknown archives are not eligible. The standard delivery-directory gate remains
unchanged; this maintenance action requires byte equality with the old receipt.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

ROOT = Path('/Users/haoranxu/Desktop/ResearchHelper')


def retire_recorded_duplicate(*, previous_candidate=None, current_candidate=None):
    source = ROOT / 'dist/ResearchHelper-Skill-0.10.0 2.zip'
    previous = Path(previous_candidate) if previous_candidate is not None else ROOT / 'result/build-candidates/20261013-105644-112547/candidate.json'
    current = Path(current_candidate) if current_candidate is not None else ROOT / 'result/build-candidates/20261013-112113-010211/candidate.json'
    for receipt in (previous, current):
        if (not receipt.is_absolute() or receipt.resolve() != receipt or receipt.is_symlink() or
                not receipt.is_relative_to(ROOT / 'result/build-candidates') or
                receipt.name != 'candidate.json' or not receipt.is_file()):
            raise RuntimeError('Only exact recorded local candidates are eligible; nothing moved')
    if source.is_symlink() or not source.is_file() or source.resolve() != source:
        raise RuntimeError('Exact recorded duplicate is absent or linked; nothing moved')
    old = json.loads(previous.read_text())['artifacts']['skill']
    live = json.loads(current.read_text())['artifacts']['skill']
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if old['sha256'] == live['sha256'] or digest != old['sha256'] or source.stat().st_size != old['bytes']:
        raise RuntimeError('Archive is not byte-identical to the recorded superseded Skill')
    with zipfile.ZipFile(source) as package:
        if package.testzip() is not None:
            raise RuntimeError('Old archive is corrupt; retained for inspection')
        manifest = json.loads(package.read('researchhelper/manifest.json'))
        if set(package.namelist()) != {'researchhelper/' + name for name in manifest['files']} | {'researchhelper/manifest.json'}:
            raise RuntimeError('Archive contains unrecorded files; retained')
        for name, value in manifest['files'].items():
            if hashlib.sha256(package.read('researchhelper/' + name)).hexdigest() != value:
                raise RuntimeError('Recorded Skill file changed; retained')
    trash = Path('/Users/haoranxu/.Trash')
    if trash.is_symlink() or not trash.is_dir() or trash.stat().st_dev != source.stat().st_dev:
        raise RuntimeError('Same-volume recoverable Trash is unavailable')
    identifier = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output = ROOT / 'result/maintenance/duplicate-release-retirement' / identifier
    output.mkdir(parents=True, exist_ok=False)
    recovery = Path(tempfile.mkdtemp(prefix='ResearchHelper-old-skill-', dir=trash)) / source.name
    receipt = output / 'receipt.json'
    record = {'status': 'verified_before_move', 'source': str(source), 'recovery': str(recovery),
              'previous_candidate': str(previous), 'sha256': digest, 'bytes': old['bytes'],
              'permanent_deletion': False, 'current_artifacts_modified': False,
              'scope': 'byte_identical_duplicate_of_recorded_superseded_skill'}
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    source.rename(recovery)
    if hashlib.sha256(recovery.read_bytes()).hexdigest() != digest:
        raise RuntimeError('Recovery archive identity mismatch; review receipt')
    record['status'] = 'retired_recoverably'
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return {'status': record['status'], 'bytes': old['bytes'], 'receipt': str(receipt), 'recovery': str(recovery)}


def retire_recorded_dmg_duplicate(*, previous_candidate, current_candidate, trash=Path('/Users/haoranxu/.Trash')):
    """Retire only the exact numbered DMG from a superseded recorded build."""
    source = ROOT / 'dist/ResearchHelper-0.10.0-arm64 2.dmg'
    previous, current = Path(previous_candidate), Path(current_candidate)
    for receipt in (previous, current):
        if (not receipt.is_absolute() or receipt.resolve() != receipt or receipt.is_symlink()
                or not receipt.is_relative_to(ROOT / 'result/build-candidates')
                or receipt.name != 'candidate.json' or not receipt.is_file()):
            raise RuntimeError('必须提供明确的本项目候选，未移动文件')
        data = json.loads(receipt.read_text())
        release = receipt.parent / 'release-manifest.json'
        if (data.get('schema') != 'researchhelper.current-build.v1' or data.get('platform') != 'macos'
                or data.get('status') != 'built_local_candidate' or release.is_symlink()
                or hashlib.sha256(release.read_bytes()).hexdigest() != data.get('manifest_sha256')):
            raise RuntimeError('候选发布清单身份不符，保留文件')
    old = json.loads(previous.read_text())['artifacts']['dmg']
    live = json.loads(current.read_text())['artifacts']['dmg']
    canonical = ROOT / 'dist/ResearchHelper-0.10.0-arm64.dmg'
    if (source.is_symlink() or not source.is_file() or source.resolve() != source
            or canonical.is_symlink() or not canonical.is_file()):
        raise RuntimeError('明确的普通安装包不存在或路径异常，未移动')
    value = hashlib.sha256(source.read_bytes()).hexdigest()
    if (old['sha256'] == live['sha256'] or value != old['sha256']
            or source.stat().st_size != old['bytes']
            or hashlib.sha256(canonical.read_bytes()).hexdigest() != live['sha256']):
        raise RuntimeError('额外DMG不是记录中的旧副本或当前包已变化，保留文件')
    trash = Path(trash)
    if trash.is_symlink() or not trash.is_dir() or trash.stat().st_dev != source.stat().st_dev:
        raise RuntimeError('同卷可恢复废纸篓不可用，未移动')
    output = ROOT / 'result/maintenance/duplicate-release-retirement' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True, exist_ok=False)
    recovery = Path(tempfile.mkdtemp(prefix='ResearchHelper-old-dmg-', dir=trash)) / source.name
    target = output / 'receipt.json'
    record = dict(status='verified_before_move', source=str(source), recovery=str(recovery),
        previous_candidate=str(previous), current_candidate=str(current), sha256=value,
        bytes=old['bytes'], permanent_deletion=False, current_artifacts_modified=False)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    # Recheck exact targets immediately before moving; never follow a link.
    if (source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != value
            or canonical.is_symlink() or hashlib.sha256(canonical.read_bytes()).hexdigest() != live['sha256']):
        raise RuntimeError('移动前安装包已变化，未移动')
    source.rename(recovery)
    if hashlib.sha256(recovery.read_bytes()).hexdigest() != value:
        raise RuntimeError('恢复文件摘要不符，请核对移动回执')
    record['status'] = 'retired_recoverably'
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return dict(status=record['status'], bytes=old['bytes'], receipt=str(target), recovery=str(recovery))


def retire_recorded_installation_duplicates(*, installed_candidate=None, duplicate_manifest_candidate=None,
                                           trash=Path('/Users/haoranxu/.Trash')):
    """Only four exact generated copies; unrelated or edited files abort."""
    target = ROOT / '.skills/researchhelper'
    previous = Path(duplicate_manifest_candidate) if duplicate_manifest_candidate is not None else ROOT / 'result/build-candidates/20261013-112113-010211/candidate.json'
    installed = Path(installed_candidate) if installed_candidate is not None else ROOT / 'result/build-candidates/20261013-114646-588500/candidate.json'
    for receipt in (previous, installed):
        if (not receipt.is_absolute() or receipt.resolve() != receipt or receipt.is_symlink() or
                not receipt.is_relative_to(ROOT / 'result/build-candidates') or
                receipt.name != 'candidate.json' or not receipt.is_file()):
            raise RuntimeError('Duplicate authority must be an exact retained candidate receipt')
    if target.is_symlink() or not target.is_dir() or target.resolve() != target:
        raise RuntimeError('Project Skill is absent or linked; nothing moved')
    pairs = {'README 2.md': 'README.md', 'requirements 2.lock': 'requirements.lock',
             'SKILL 2.md': 'SKILL.md', 'manifest 2.json': None}
    old = json.loads(previous.read_text())['project_skill_installation']
    current = json.loads(installed.read_text())['project_skill_installation']
    digest = lambda file: hashlib.sha256(file.read_bytes()).hexdigest()
    raw = target / 'manifest.json'
    if digest(raw) != current['manifest_sha256']:
        raise RuntimeError('Installation no longer matches the recorded manifest; retained')
    manifest = json.loads(raw.read_text())
    expected = set(manifest['files']) | {'manifest.json'}
    files = list(target.rglob('*'))
    if any(file.is_symlink() for file in files):
        raise RuntimeError('Linked installation content is retained')
    actual = {file.relative_to(target).as_posix() for file in files if file.is_file()}
    if actual != expected | set(pairs):
        raise RuntimeError('Unrelated installation files are retained')
    if any(digest(target / name) != value for name, value in manifest['files'].items()):
        raise RuntimeError('Modified managed files are retained')
    # A restored old folder brings its old SKILL.md too. Compare that copy to
    # the recorded old manifest, not only to the newly installed instructions.
    old_raw = target / 'manifest 2.json'
    if digest(old_raw) != old['manifest_sha256']:
        raise RuntimeError('Duplicate manifest does not match its recorded installation; retained')
    old_manifest = json.loads(old_raw.read_text())
    items = []
    for name, original in pairs.items():
        source = target / name
        value = digest(source)
        approved = {old['manifest_sha256']: str(previous)} if original is None else {
            manifest['files'][original]: str(installed), old_manifest['files'][original]: str(previous)}
        if value not in approved:
            raise RuntimeError('Duplicate differs from its recorded generated bytes; retained')
        items.append({'source': str(source), 'sha256': value, 'bytes': source.stat().st_size,
                      'authority': approved[value]})
    trash = Path(trash)
    if trash.is_symlink() or not trash.is_dir() or trash.stat().st_dev != target.stat().st_dev:
        raise RuntimeError('Same-volume recoverable Trash is unavailable')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output = ROOT / 'result/maintenance/duplicate-skill-file-retirement' / stamp
    output.mkdir(parents=True, exist_ok=False)
    recovery = Path(tempfile.mkdtemp(prefix='ResearchHelper-generated-skill-copies-', dir=trash))
    for item in items:
        item['recovery'] = str(recovery / Path(item['source']).name)
    receipt = output / 'receipt.json'
    record = {'status': 'verified_before_move', 'scope': 'four_exact_recorded_generated_skill_copies',
              'files': items, 'permanent_deletion': False, 'managed_files_modified': False,
              'unknown_directories_preserved': True}
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    for item in items:
        source = Path(item['source'])
        if digest(source) != item['sha256']:
            raise RuntimeError('Duplicate changed before move; review recovery receipt')
        source.rename(item['recovery'])
        if digest(Path(item['recovery'])) != item['sha256']:
            raise RuntimeError('Recovery identity changed; review receipt')
    record['status'] = 'retired_recoverably'
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return {'status': record['status'], 'files': len(items), 'receipt': str(receipt),
            'bytes': sum(item['bytes'] for item in items), 'recovery': str(recovery)}
