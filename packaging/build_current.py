"""One-click local delivery: temporary build, verified archives, atomic downloads.

Uses OH's delivery pattern, not its business or release protocol. Updates only
the verified project Skill; never installs the App/global Skill, publishes
online, or synchronizes either retained presentation demo.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artifact_integrity import directory_record, verify_directory
from build_all import build_candidate, verify_release_binding, temporary_build_workspace
from source_snapshot import digest, source_files

ROOT = Path(__file__).resolve().parents[1]
# The double-click entry uses -I; development acceptance must resolve this checkout.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def write_record(path, value):
    """Replace a journal only after the whole UTF-8 JSON has reached disk."""
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.' + path.name + '-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def build_lock(root):
    """Kernel-owned lock: process death releases it without guessing stale PIDs."""
    path = Path(root) / '.researchhelper/runtime/build-current.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError('构建锁不能是符号链接')
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            acquire = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: (stream.seek(0), msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1))
        else:
            import fcntl
            acquire = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as error:
            raise RuntimeError('已有ResearchHelper一键构建正在运行，未开始第二次构建') from error
        try:
            yield
        finally:
            release()


def archive_file(file, root):
    """Retain exact delivery bytes by product, version and full content hash."""
    file, root = Path(file), Path(root).resolve()
    skill = re.fullmatch(r'ResearchHelper-Skill-(\d+\.\d+\.\d+)\.zip', file.name)
    app = re.fullmatch(r'ResearchHelper-(\d+\.\d+\.\d+)-(?:arm64\.dmg|windows-x64-setup\.exe)', file.name)
    match = skill or app
    if file.is_symlink() or not file.is_file():
        raise RuntimeError('交付物不是普通文件：' + str(file))
    value = digest(file)
    if match:
        folder = root / 'versions' / ('skill' if skill else 'app') / ('v' + match.group(1)) / value
    elif file.name == 'current-release.json':
        # Preserve the pre-alignment formal index without calling a new build formal.
        folder = root / 'versions/metadata' / value
    else:
        raise RuntimeError('交付目录含非交付文件，拒绝覆盖：' + file.name)
    if folder.is_symlink() or any(parent.is_symlink() for parent in folder.parents if parent != root):
        raise RuntimeError('版本归档路径不能是符号链接')
    target = folder / file.name
    if target.exists():
        if target.is_symlink() or digest(target) != value:
            raise RuntimeError('已有内容寻址归档不一致：' + str(target))
        return target
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=folder, prefix='.copy-', delete=False) as handle:
        temporary = Path(handle.name)
    try:
        shutil.copyfile(file, temporary)
        if digest(temporary) != value:
            raise RuntimeError('归档复制校验失败：' + file.name)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    write_record(folder / 'artifact.json', {'filename': file.name, 'sha256': value,
                 'version': match.group(1) if match else None,
                 'status': 'retained_bytes', 'formal_release_verified': False})
    return target


def is_finder_metadata(file):
    """Recognize only a regular macOS Finder file with its format header."""
    file = Path(file)
    if sys.platform != 'darwin' or file.name != '.DS_Store' or file.is_symlink() or not file.is_file():
        return False
    with file.open('rb') as stream:
        return stream.read(8) == b'\x00\x00\x00\x01Bud1'


def delivery_entries(delivery, project_root=None, *, recorded_copy=None):
    """Validate the entire destination without copying or deleting any bytes."""
    delivery = Path(delivery)
    installer_pattern = r'(?:arm64\.dmg|windows-x64-setup\.exe)'
    if project_root is not None:
        root = Path(project_root).resolve()
        lexical = delivery.absolute()
        normalized = Path(os.path.abspath(lexical))
        if not normalized.is_relative_to(root) or normalized == root:
            raise RuntimeError('交付目录超出本项目，未开始构建')
        # Check the original traversal before normalization. A linked/../dist
        # spelling must not hide a symlink and redirect an archive or swap.
        if any(parent.is_symlink() for parent in (lexical, *lexical.parents)
               if parent != root and parent.is_relative_to(root)):
            raise RuntimeError('交付目录及其项目内父目录不能是符号链接')
        delivery = normalized
        if delivery == root / 'dist':
            installer_pattern = r'arm64\.dmg'
        elif delivery == root / 'result/windows-candidate':
            installer_pattern = r'windows-x64-setup\.exe'
    if delivery.is_symlink():
        raise RuntimeError('交付目录不能是符号链接')
    if not delivery.exists():
        return []
    if not delivery.is_dir():
        raise RuntimeError('交付位置不是目录，未开始构建，原文件保持不变：' +
                           json.dumps(str(delivery), ensure_ascii=False))
    # Preflight the complete directory before writing any archive or replacing it.
    entries = sorted(delivery.iterdir())
    allowed = re.compile(r'(?:ResearchHelper-Skill-\d+\.\d+\.\d+\.zip|'
                         r'ResearchHelper-\d+\.\d+\.\d+-' + installer_pattern +
                         r'|current-release\.json)')
    installer = re.compile(r'ResearchHelper-\d+\.\d+\.\d+-(?:arm64\.dmg|windows-x64-setup\.exe)')
    issues = []
    artifacts = []
    for file in entries:
        if is_finder_metadata(file):
            continue  # Finder preferences are preserved, not published artifacts.
        if file.is_symlink():
            kind = '符号链接'
        elif file.is_dir():
            kind = '目录'
        elif not file.is_file():
            kind = '非普通文件'
        elif not allowed.fullmatch(file.name):
            if recorded_copy is not None and recorded_copy(file, allowed):
                continue
            kind = '其他平台安装包' if installer.fullmatch(file.name) else '未知文件'
        else:
            artifacts.append(file)
            continue
        # Quoted names keep tabs/newlines from masquerading as build progress.
        issues.append('  ' + kind + '：' + json.dumps(file.name, ensure_ascii=False))
    if issues:
        raise RuntimeError('交付目录含未知项目，原目录保持不变，未开始构建。\n目录：' +
                           json.dumps(str(delivery), ensure_ascii=False) + '\n' + '\n'.join(issues) +
                           '\n请先确认这些项目的用途，将需保留的项目移出交付目录后重试。未自动移动、归档或删除。')
    return artifacts


def archive_deliveries(delivery, root):
    return [archive_file(file, root) for file in delivery_entries(delivery, root)]


def _delivery_state(directory):
    files = {file.name: digest(file) for file in delivery_entries(directory)}
    metadata = directory / '.DS_Store'
    if is_finder_metadata(metadata):
        files[metadata.name] = digest(metadata)
    return files


def replace_delivery(staged, target, *, archived=None):
    """Sibling-directory swap with rollback; archives already retain the old bytes."""
    staged, target = Path(staged), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise RuntimeError('拒绝替换符号链接交付目录')
    previous = _delivery_state(target)
    if archived is not None and {name: value for name, value in previous.items() if name != '.DS_Store'} != archived:
        raise RuntimeError('交付目录与已归档内容不一致，未替换')
    with tempfile.TemporaryDirectory(prefix='.' + target.name + '-staging-', dir=target.parent) as folder:
        ready = Path(folder) / 'delivery'
        shutil.copytree(staged, ready)
        for file in staged.iterdir():
            if digest(file) != digest(ready / file.name):
                raise RuntimeError('交付复制校验失败')
        metadata = target / '.DS_Store'
        if metadata.exists() or metadata.is_symlink():
            if not is_finder_metadata(metadata) or (ready / '.DS_Store').is_symlink():
                raise RuntimeError('交付目录含未知Finder项目，原目录保持不变：.DS_Store')
            shutil.copyfile(metadata, ready / '.DS_Store')
            if previous.get('.DS_Store') != digest(ready / '.DS_Store'):
                raise RuntimeError('Finder元数据复制校验失败，原目录保持不变')
        backup = target.with_name('.' + target.name + '-backup-' + uuid.uuid4().hex)
        had_target = target.exists()
        if had_target:
            target.rename(backup)
        try:
            if (had_target and _delivery_state(backup) != previous) or (not had_target and previous):
                raise RuntimeError('交付目录在替换期间变化，保留原目录')
            if target.exists() or target.is_symlink():
                raise RuntimeError('交付位置在替换期间被占用，保留原目录')
            ready.rename(target)
        except BaseException:
            if had_target and backup.exists() and not target.exists() and not target.is_symlink():
                backup.rename(target)
            raise
        if had_target:
            try:
                if _delivery_state(backup) != previous:
                    raise RuntimeError('旧目录有新内容')
                for name, value in previous.items():
                    file = backup / name
                    if file.is_symlink() or not file.is_file() or digest(file) != value:
                        raise RuntimeError('旧文件已变化')
                    file.unlink()
                backup.rmdir()
            except (OSError, RuntimeError):
                # Never roll back a verified new delivery to a partially removed backup.
                print('新交付已就绪，旧目录保留待核对：' + str(backup), flush=True)


def current_sources_match(root, expected):
    actual = {file.relative_to(root).as_posix(): digest(file) for file in source_files(root)}
    if actual != expected:
        raise RuntimeError('源码或源清单已变化，拒绝把旧快照写入当前交付')


def materialize_candidate(root, built, temporary):
    """Keep only demo, product manifests and receipts; not a second bare App/runtime."""
    current_sources_match(root, built['source_hashes'])
    original = Path(built['payload'])
    release = json.loads((original / 'release-manifest.json').read_text())
    platform = built['platform']
    delivery = root / ('dist' if platform == 'macos' else 'result/windows-candidate')
    from reconcile_delivery import reconcile_downloads
    reconcile_downloads(root,delivery)
    previous = archive_deliveries(delivery, root)
    downloads = Path(temporary) / 'downloads'
    downloads.mkdir()
    artifacts = {}
    for name, item in release['artifacts'].items():
        source = Path(item['path'])
        archived = archive_file(source, root)
        shutil.copyfile(source, downloads / source.name)
        artifacts[name] = dict(item, path=str(archived))
    if len(artifacts) != 2:
        raise RuntimeError('交付只能包含Skill和本机安装包')
    record_id = Path(built['log']).parent.name
    output = root / 'result/build-candidates' / record_id
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(original / 'demo', output / 'demo')
    components = {}
    for name, item in release['component_manifests'].items():
        file = output / (name + '-manifest.json')
        shutil.copyfile(item['path'], file)
        if digest(file) != item['sha256']:
            raise RuntimeError('产品清单复制不一致：' + name)
        components[name] = {'path': str(file), 'sha256': item['sha256']}
    release.update(artifacts=artifacts, component_manifests=components,
                   directory_artifacts={'demo': directory_record(output / 'demo')})
    write_record(output / 'release-manifest.json', release)
    verify_release_binding(release, output, platform, source_root=root)
    verify_directory(release['directory_artifacts']['demo'], output)
    current_sources_match(root, built['source_hashes'])
    receipt = {**built, 'schema': 'researchhelper.current-build.v1', 'status': 'prepared_local_candidate',
               'payload': str(output), 'snapshot_retained': False, 'dist_updated': False,
               'manifest_sha256': digest(output / 'release-manifest.json'),
               'artifacts': artifacts, 'directory_artifacts': release['directory_artifacts'],
               'delivery': str(delivery), 'previous_artifacts': [str(path) for path in previous],
               'downloads': {key: str(delivery / Path(item['path']).name) for key, item in artifacts.items()}}
    record = output / 'candidate.json'
    write_record(record, receipt)
    try:
        replace_delivery(downloads, delivery, archived={file.name: file.parent.name for file in previous})
    except BaseException:
        receipt['status'] = 'failed_local_delivery'
        write_record(record, receipt)
        raise
    receipt.update(status='built_local_candidate', dist_updated=True)
    try:
        write_record(record, receipt)
    except OSError as error:
        raise RuntimeError('交付目录已更新，但最终回执保存失败；未安装或正式发布：' + str(record)) from error
    return receipt, record


def cache_native_renderer(candidate, root, candidate_path):
    """Refresh a derived developer component from the verified Skill ZIP."""
    if candidate.get('platform')!='macos':return {'status':'not_applicable'}
    import hashlib,zipfile
    root=Path(root).resolve();directory=root/'result/native-tools'
    worker=directory/'ResearchHelperHTMLPDF';metadata=directory/'renderer.json'
    if directory.is_symlink() or directory.resolve()!=directory or any(p.is_symlink() for p in (worker,metadata)):
        return {'status':'preserved_unmanaged_cache'}
    if worker.exists() or metadata.exists():
        try:
            old=json.loads(metadata.read_text())
            owned=worker.is_file() and old.get('sha256')==hashlib.sha256(worker.read_bytes()).hexdigest()
        except (OSError,ValueError,TypeError,AttributeError):owned=False
        if not owned:return {'status':'preserved_modified_cache'}
    artifact=candidate['artifacts']['skill'];archive=Path(artifact['path'])
    if digest(archive)!=artifact['sha256']:raise RuntimeError('Skill安装包在缓存更新前发生变化')
    with zipfile.ZipFile(archive) as package:
        manifest=json.loads(package.read('researchhelper/manifest.json'))
        data=package.read('researchhelper/app/tools/ResearchHelperHTMLPDF')
    sha=hashlib.sha256(data).hexdigest()
    if sha!=manifest['files']['app/tools/ResearchHelperHTMLPDF']:raise RuntimeError('原生组件与Skill清单不一致')
    source_sha=candidate['source_hashes']['modules/artifactexporter/assets/macos/HTMLPDF.swift']
    if digest(root/'modules/artifactexporter/assets/macos/HTMLPDF.swift')!=source_sha:
        raise RuntimeError('原生组件源码已变化，未更新缓存')
    directory.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory,delete=False) as out:
        out.write(data);out.flush();os.fsync(out.fileno());temporary=Path(out.name)
    try:
        temporary.chmod(0o755);os.replace(temporary,worker)
        write_record(metadata,dict(schema='researchhelper.native-pdf-cache',version=1,
            candidate=str(Path(candidate_path).resolve()),sha256=sha,source_sha256=source_sha))
    finally:temporary.unlink(missing_ok=True)
    return {'status':'updated','path':str(worker),'sha256':sha,'source_sha256':source_sha}


def update_project_skill(candidate_path, root=ROOT):
    """Refresh the managed project copy after commit, never an App or global Skill."""
    from skill.install_project_skill import install_project_skill
    root = Path(root).resolve()
    with build_lock(root):
        from reconcile_delivery import reconcile_downloads,reconcile_project_skill
        reconcile_downloads(root,root/('result/windows-candidate' if sys.platform=='win32' else 'dist'))
        candidate = verify_or_restore_downloads(candidate_path, root, 'project_skill')
        archive = candidate['artifacts']['skill']
        retired=reconcile_project_skill(root)
        installed=install_project_skill(Path(archive['path']), project_root=root,
                                     expected_sha256=archive['sha256'], preserve_empty_directories=True)
        installed['generated_copy_reconciliation']=retired
        installed['post_install_generated_copy_reconciliation']=reconcile_project_skill(
            root, expected_manifest=installed['manifest_sha256'])
        installed['native_renderer_cache']=cache_native_renderer(candidate,root,candidate_path)
        return installed


def project_skill_step(record,root,*,install_skill=True):
    """Permit research validation before changing the user's installed Skill."""
    if type(install_skill) is not bool:raise ValueError('Skill安装选项须为明确布尔值')
    if not install_skill:return {'status':'deferred_until_research_acceptance','installed':False}
    return update_project_skill(record,root)


def build_current(root=ROOT, *, builder=build_candidate, install_skill=True):
    if type(install_skill) is not bool:raise ValueError('Skill安装选项须为明确布尔值')
    root = Path(root).resolve()
    started = time.monotonic()
    # The host decides the native target. Check its current destination before
    # costly freezing; the same pure validator runs again before promotion.
    # Unknown user files remain untouched, and macOS/Windows stay isolated.
    target = root / ('result/windows-candidate' if sys.platform == 'win32' else 'dist')
    from reconcile_delivery import download_inventory, reconcile_downloads
    download_inventory(root, target)
    with build_lock(root):
        reconcile_downloads(root,target)
        delivery_entries(target, root)
        parent = root / 'result/build-work';parent.mkdir(parents=True, exist_ok=True)
        with temporary_build_workspace(root) as folder:
            built = builder(root=root, workspace=Path(folder))
            print('[7/7] 校验交付物，原子更新本地候选', flush=True)
            receipt, record = materialize_candidate(root, built, folder)
            # Scratch payloads are not installations.
            raw = Path(built['log']).parent / 'candidate.json'
            write_record(raw, {**built, 'status': 'promoted_local_candidate', 'final_receipt': str(record),
                               'snapshot_retained': False, 'temporary_payload_retained': False})
        receipt.update(temporary_workspace_removed=not Path(folder).exists(),
                       elapsed_seconds=round(time.monotonic() - started, 1))
        write_record(record, receipt)
        if receipt['platform'] == 'macos':
            marker = macos_candidate_marker(root)
            write_record(marker, {'candidate': str(record), 'manifest_sha256': receipt['manifest_sha256']})
    # Only after commit and scratch-workspace cleanup is it safe to retire old
    # delivery bytes. Failure cannot remove the current, verified candidate.
    from release_retention import retire_after_build
    try:
        receipt['release_retirement'] = retire_after_build(root, record)
    except Exception as error:
        receipt['release_retirement'] = {'status': 'requires_review', 'error': str(error)}
        print('旧交付物清理未完成，现版保留：' + str(error), flush=True)
    try:
        receipt['project_skill_installation'] = project_skill_step(record,root,install_skill=install_skill)
    except Exception as error:
        receipt['project_skill_installation'] = {'status': 'requires_review', 'error': str(error)}
        print('项目级Skill未更新，保留原安装：' + str(error), flush=True)
    receipt['elapsed_seconds'] = round(time.monotonic() - started, 1)
    write_record(record, receipt)
    print('[7/7] 本地候选完成，用时' + str(receipt['elapsed_seconds']) + '秒。未安装App、同步demo或正式发布。', flush=True)
    installed = receipt['project_skill_installation']
    if installed['status'] in {'installed', 'already_current'}:
        print('项目级Skill：' + installed['installation'] + '；已与本候选核对一致。', flush=True)
    for key, file in receipt['downloads'].items():
        print({'skill': 'Skill', 'dmg': 'macOS安装包', 'setup': 'Windows安装包'}[key] + '：' + file, flush=True)
    print('离线demo：' + receipt['directory_artifacts']['demo']['path'] + '\n回执：' + str(record) +
          '\n日志：' + receipt['log'] + '\n临时源码与编译目录已清理。', flush=True)
    retirement = receipt['release_retirement']
    if retirement['status'] == 'retired_permanently':
        print('已直接清理'+str(retirement['deleted'])+'项核验过的旧交付物；现版和研究记录保留。',flush=True)
    elif retirement['status'] == 'retired_recoverably':
        print('旧安装包与候选demo已移入废纸篓，可恢复；仓库仅保留现版交付。', flush=True)
    elif retirement['status'] == 'deferred_until_acceptance':
        print('旧交付物保留，离线验收通过后再直接清理。',flush=True)
    elif retirement['status'] == 'nothing_superseded_to_remove':
        print('仓库无待清理的旧安装包或候选demo。', flush=True)
    elif retirement['status'] == 'requires_review':
        print('旧交付物清理待核对：' + str(retirement.get('worker_receipt', retirement.get('error', '现版已保留'))), flush=True)
    return receipt


class MissingDownloadCopies(RuntimeError):
    def __init__(self, present, missing):
        super().__init__('当前下载目录不属于本候选，缺少文件' + '、'.join(missing))
        self.present = present
        self.missing = missing


def candidate_downloads(candidate, root):
    target = root / ('dist' if candidate['platform'] == 'macos' else 'result/windows-candidate')
    if Path(candidate['delivery']) != target:
        raise RuntimeError('当前下载目录不属于本候选')
    return target, {Path(item['path']).name: item for item in candidate['artifacts'].values()}


def verify_current_build(record, root=ROOT, *, check_downloads=True):
    """Validate retained identity, not the scratch paths that were intentionally removed."""
    root, record = Path(root).resolve(), Path(record).resolve()
    if not record.is_relative_to(root / 'result/build-candidates') or record.name != 'candidate.json':
        raise RuntimeError('须明确指定本项目保留的候选回执')
    candidate = json.loads(record.read_text())
    if candidate.get('schema') != 'researchhelper.current-build.v1' or candidate.get('status') != 'built_local_candidate':
        raise RuntimeError('候选尚未完成本地交付')
    if not candidate.get('temporary_workspace_removed') or Path(candidate['snapshot']).exists():
        raise RuntimeError('候选临时源码尚未清理')
    output = record.parent
    if Path(candidate['payload']).resolve() != output:
        raise RuntimeError('候选载荷不属于本回执')
    manifest = output / 'release-manifest.json'
    if digest(manifest) != candidate['manifest_sha256']:
        raise RuntimeError('候选清单已变化')
    release = json.loads(manifest.read_text())
    for key in ('artifacts', 'directory_artifacts', 'source_hashes'):
        if release.get(key) != candidate.get(key):
            raise RuntimeError('候选与发行清单不一致：' + key)
    current_sources_match(root, candidate['source_hashes'])
    verify_release_binding(release, output, candidate['platform'], source_root=root)
    for item in candidate['artifacts'].values():
        file = Path(item['path'])
        if not file.resolve().is_relative_to(root / 'versions') or file.parent.name != item['sha256']:
            raise RuntimeError('候选归档未绑定内容哈希')
        if file.is_symlink() or digest(file) != item['sha256'] or file.stat().st_size != item['bytes']:
            raise RuntimeError('候选归档已变化')
    for item in candidate['directory_artifacts'].values():
        verify_directory(item, output)
    if check_downloads:
        from reconcile_delivery import download_inventory
        target, expected = candidate_downloads(candidate, root)
        actual = set(download_inventory(root, target)['files'])
        if actual - expected.keys():
            raise RuntimeError('当前下载目录不属于本候选或包含额外项目')
        present = {name: digest(target / name) for name in sorted(actual)}
        for name, value in present.items():
            if value != expected[name]['sha256']:
                raise RuntimeError('当前下载与保留候选不一致')
        if missing := sorted(expected.keys() - actual):
            raise MissingDownloadCopies(present, missing)
    return candidate


def macos_candidate_marker(root):
    path = root / '.researchhelper/runtime/current-macos-candidate.json'
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise RuntimeError('macOS候选记录不能使用链接路径')
    return path


def active_macos_candidate(root):
    marker = macos_candidate_marker(root)
    if not marker.is_file():
        return None
    try:
        state = json.loads(marker.read_text())
        selected = Path(state['candidate'])
        if (selected.name != 'candidate.json' or selected.parent.parent != root / 'result/build-candidates'
                or selected.resolve() != selected):
            return None
        candidate = json.loads(selected.read_text())
        if (candidate['platform'] == 'macos' and candidate['status'] == 'built_local_candidate'
                and candidate['manifest_sha256'] == state['manifest_sha256']):
            return selected, candidate
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None


def verify_or_restore_downloads(selected, root, phase):
    """Under the build lock, replace only missing copies of the active Mac candidate."""
    selected = Path(selected).resolve()
    try:
        return verify_current_build(selected, root)
    except MissingDownloadCopies as missing:
        active = active_macos_candidate(root)
        if not active or active[0] != selected:
            raise
        candidate = verify_current_build(selected, root, check_downloads=False)
        if candidate['manifest_sha256'] != active[1]['manifest_sha256']:
            raise RuntimeError('恢复前候选身份已变化')
        target, expected = candidate_downloads(candidate, root)
        with tempfile.TemporaryDirectory(prefix='.download-recovery-', dir=target.parent) as directory:
            staged = Path(directory)
            for name, item in expected.items():
                file = staged / name
                shutil.copyfile(item['path'], file)
                if digest(file) != item['sha256']:
                    raise RuntimeError('恢复文件与候选归档不一致：' + name)
            replace_delivery(staged, target, archived=missing.present)
        candidate = verify_current_build(selected, root)
        candidate.setdefault('download_repairs', []).append({'phase': phase, 'files': missing.missing})
        write_record(selected, candidate)
        print('已从同一候选恢复下载文件：' + '、'.join(missing.missing), flush=True)
        return candidate


def resumable_macos_candidate(root):
    from release_retention import _accepted_current_candidate, permanent_cleanup_enabled
    active = active_macos_candidate(root)
    if not active:
        return None
    selected, candidate = active
    try:
        current_sources_match(root, candidate['source_hashes'])
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return None
    target, expected = candidate_downloads(candidate, root)
    installation = candidate.get('project_skill_installation', {}).get('status')
    if installation not in ('installed', 'already_current') or not _accepted_current_candidate(root, selected):
        return selected
    if (permanent_cleanup_enabled(root) and
            candidate.get('release_retirement', {}).get('status') == 'deferred_until_acceptance'):
        return selected
    if any(not (target / name).is_file() for name in expected):
        return selected
    return None


def build_and_accept(root=ROOT, *, builder=build_candidate):
    """Accept the active macOS candidate, then verify delivery and update the Skill."""
    from accept_current import accept_current
    from reconcile_delivery import reconcile_downloads
    from release_retention import _accepted_current_candidate, permanent_cleanup_enabled, retire_after_build
    root = Path(root).resolve()
    with build_lock(root):
        selected = resumable_macos_candidate(root)
    if selected is None:
        built = build_current(root, builder=builder, install_skill=False)
        selected = Path(built['payload']) / 'candidate.json'
    else:
        print('继续验收同一候选：' + selected.parent.name, flush=True)
    with build_lock(root):
        reconcile_downloads(root,root/'dist')
        if _accepted_current_candidate(root, selected):
            candidate = verify_current_build(selected, root, check_downloads=False)
        else:
            accepted = accept_current(selected, root)
            if accepted.get('status') != 'scoped_passed':
                raise RuntimeError('包后验收未通过，旧交付物保留。')
            candidate = verify_current_build(selected, root, check_downloads=False)
            candidate['postbuild_acceptance'] = {'status': accepted['status'], 'path': accepted['receipt'],
                                                 'sha256': digest(accepted['receipt'])}
            write_record(selected, candidate)
        verify_or_restore_downloads(selected, root, 'after_acceptance')
    installation = update_project_skill(selected, root)
    if installation.get('status') not in ('installed','already_current'):
        raise RuntimeError('项目级Skill尚未更新，旧交付物保留。')
    with build_lock(root):
        reconcile_downloads(root,root/'dist')
        current = verify_or_restore_downloads(selected, root, 'finalize')
        current.update(project_skill_installation=installation,
                       postbuild_acceptance=candidate['postbuild_acceptance'])
        write_record(selected, current)
    # The worker takes its own lock only after package acceptance and Skill
    # installation. Failed acceptance leaves the prior delivery bytes intact.
    if permanent_cleanup_enabled(root):
        try:
            retirement=retire_after_build(root,selected)
        except Exception as error:
            retirement=dict(status='requires_review',error=str(error))
        with build_lock(root):
            reconcile_downloads(root,root/'dist')
            current=verify_or_restore_downloads(selected,root,'after_retirement')
            current['release_retirement']=retirement;write_record(selected,current)
        if retirement['status']=='retired_permanently':
            print('已直接清理'+str(retirement['deleted'])+'项核验过的旧交付物；现版和研究记录保留。',flush=True)
        elif retirement['status']=='requires_review':
            print('旧交付物清理未完成，已保留现版；详见当次回执。',flush=True)
    print('打包及离线验收完成：实际DMG和独立Skill的三类研究、两版报告、五种导出均已通过。', flush=True)
    print('验收回执：' + current['postbuild_acceptance']['path'] + '\n未自动安装App、同步demo或正式发布。', flush=True)
    return current


if __name__ == '__main__':
    try:
        if sys.platform == 'darwin':
            build_and_accept()
        else:
            result = build_current()
            if result['project_skill_installation']['status'] not in ('installed', 'already_current'):
                raise RuntimeError('安装包候选已生成，项目级Skill尚未更新，请核对候选回执')
    except KeyboardInterrupt:
        print('构建已取消。请查看当次回执；未安装或正式发布。', flush=True)
        sys.exit(130)
    except Exception as error:
        print('本地交付未完成：' + str(error) + '\n未安装、同步demo或正式发布。', flush=True)
        sys.exit(1)
