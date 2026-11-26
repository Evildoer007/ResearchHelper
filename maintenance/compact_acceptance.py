"""Compact one closed live QA workspace without changing its research result.

Customer reports and the original receipt stay readable. The exact task export
and private test Store are compressed and verified before redundant raw copies
are removed. Production research, current packages and unknown files are not
cleanup targets.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from .retire_closed_test_stores import referenced_cases

ROOT = Path(__file__).resolve().parents[1]
CLOSED = {'已完成', '部分完成', '失败', '已取消'}


def require_unreferenced_case(directory, root):
    if referenced_cases(root, (directory.name,)):
        raise ValueError('验收目录仍被回归引用，保留原路径')


def compact_closed_exports(directory, *, root=ROOT):
    """Compress verified closed JSON exports without touching a busy Store.

    A failed source-freeze check remains a failure. Byte-preserving export
    compression does not upgrade it, and never reads or removes the database.
    """
    root,directory=Path(root).resolve(),Path(directory)
    if (not directory.is_absolute() or directory.is_symlink() or directory.resolve()!=directory
            or directory.parent!=root/'result/acceptance'
            or not directory.name.startswith(('one-sentence-research-','autonomous-data-phase-'))):
        raise ValueError('仅压缩本项目已关闭验收的导出文件')
    require_unreferenced_case(directory, root)
    receipt=directory/'receipt.json'
    if receipt.is_symlink():raise ValueError('验收回执不是普通文件')
    record=json.loads(receipt.read_text())
    if record.get('production_unchanged') is not True or record.get('status') in ('running','preparing','executing'):
        raise ValueError('验收尚未关闭，未压缩导出')
    closed=(record.get('owned_runtime_closed') is True if directory.name.startswith('autonomous-data-phase-')
            else all(record.get(k) is True for k in ('service_stopped','browser_stopped',
                'temporary_credentials_cleared','temporary_execution_policy_disabled')))
    if not closed:raise ValueError('验收运行进程未确认回收')
    source=directory/'task.json'
    if not source.is_file() or source.is_symlink():raise ValueError('缺少普通研究导出')
    task=json.loads(source.read_text())
    if task.get('id')!=record.get('task_id') or task.get('status') not in CLOSED:
        raise ValueError('导出不是本次已关闭任务')
    del task
    summary_path=directory/'export-compaction.json'
    if summary_path.exists():raise ValueError('不覆盖既有导出压缩回执')
    files=[p for p in (source,directory/'report.json') if p.is_file()]
    def unused(path):
        opened=subprocess.run(['/usr/sbin/lsof','-nP','--',str(path)],capture_output=True,timeout=15)
        if opened.returncode not in (0,1) or opened.stdout.strip():
            raise ValueError('导出文件仍被进程使用，未清理')
    hashes={p.name:digest(p) for p in files}
    protected={p.name:digest(p) for p in (receipt,directory/'report.html') if p.is_file()}
    for p in files:
        if p.is_symlink() or (directory/(p.name+'.gz')).exists():
            raise ValueError('不覆盖压缩文件或处理软链接')
        unused(p)
    before=sum(p.stat().st_size for p in files)
    with tempfile.TemporaryDirectory(prefix='.compact-exports-',dir=directory) as temporary:
        for p in files:
            staged=Path(temporary)/(p.name+'.gz')
            with p.open('rb') as incoming,gzip.open(staged,'wb',compresslevel=6) as outgoing:
                shutil.copyfileobj(incoming,outgoing)
            if digest(staged,compressed=True)!=hashes[p.name] or digest(p)!=hashes[p.name]:
                raise ValueError('导出压缩与原值不一致，未清理')
        require_unreferenced_case(directory, root)
        for p in files:
            unused(p)
            if digest(p)!=hashes[p.name]:raise ValueError('导出在压缩时发生变化，保留原件')
            os.replace(Path(temporary)/(p.name+'.gz'),directory/(p.name+'.gz'))
            p.unlink()
    if any(digest(directory/name)!=value for name,value in protected.items()):
        raise ValueError('原回执或客户HTML发生变化')
    after=sum((directory/(p.name+'.gz')).stat().st_size for p in files)
    result=dict(status='closed_exports_compressed_verified',original_files_sha256=hashes,
        before_bytes=before,compressed_bytes=after,saved_bytes=before-after,
        store_touched=False,study_reopened=False,original_acceptance_status=record['status'],
        restoration='对应json.gz解压可逐字恢复原始JSON；数据库与原回执未修改。')
    summary_path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def require_unused_store(store, root):
    if not store.exists():
        return
    if not store.is_dir() or store.stat().st_dev != root.stat().st_dev:
        raise ValueError('测试库不是本卷普通目录，未清理')
    opened = subprocess.run(['/usr/sbin/lsof', '+D', str(store)],
        capture_output=True, timeout=15)
    if opened.returncode not in (0, 1) or opened.stdout.strip():
        raise ValueError('测试库仍被进程使用，未清理')


def digest(path, *, compressed=False):
    value = hashlib.sha256()
    opener = gzip.open if compressed else open
    with opener(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def compact_case(directory, *, root=ROOT):
    root, directory = Path(root).resolve(), Path(directory)
    if (not directory.is_absolute() or directory.is_symlink() or directory.resolve() != directory
            or directory.parent != root/'result/acceptance'
            or not directory.name.startswith('one-sentence-research-')):
        raise ValueError('仅压缩本项目独立验收任务，不处理生产库或任意目录')
    require_unreferenced_case(directory, root)
    if any(path.is_symlink() for path in directory.rglob('*')):
        raise ValueError('验收目录包含软链接，未清理')
    receipt = directory/'receipt.json'
    record = json.loads(receipt.read_text())
    required = ('service_stopped', 'browser_stopped', 'mount_detached',
        'temporary_credentials_cleared', 'temporary_execution_policy_disabled', 'production_unchanged')
    if (record.get('status') != 'collected_for_independent_review'
            or record.get('research_status') not in CLOSED
            or any(record.get(key) is not True for key in required)
            or 'temporary Store is empty' not in record.get('browser', {}).get('checks', [])):
        raise ValueError('仅处理已关闭、已回收且明确从空白测试库发起的验收')
    source = directory/'task.json'
    if not source.is_file():
        raise ValueError('尚无完整研究导出，未清理')
    task = json.loads(source.read_text())
    if task.get('id') != record.get('task_id') or task.get('status') != record['research_status']:
        raise ValueError('研究导出与验收任务不一致，未清理')
    if any((directory/'store').rglob('*.secret')):
        raise ValueError('临时凭据尚未清除，未归档')
    require_unused_store(directory/'store', root)
    archive, store_archive = directory/'task.json.gz', directory/'test-store.zip'
    report_source, report_archive = directory/'report.json', directory/'report.json.gz'
    if archive.exists() or store_archive.exists() or report_archive.exists() or (directory/'compaction.json').exists():
        raise ValueError('不覆盖既有压缩记录')
    original_hash = digest(source)
    before = source.stat().st_size
    kept = {name:digest(directory/name) for name in ('receipt.json','report.html','browser-receipt.json')
        if (directory/name).is_file()}
    report_hash = digest(report_source) if report_source.is_file() else None
    removed = []
    with tempfile.TemporaryDirectory(prefix='.compact-', dir=directory) as temporary:
        temporary = Path(temporary)
        packed_task = temporary/'task.json.gz'
        with source.open('rb') as incoming, gzip.open(packed_task, 'wb', compresslevel=6) as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        if digest(packed_task, compressed=True) != original_hash or digest(source) != original_hash:
            raise ValueError('压缩后原始导出不一致，未清理')
        if report_hash:
            packed_report = temporary/'report.json.gz'
            with report_source.open('rb') as incoming, gzip.open(packed_report, 'wb', compresslevel=6) as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if digest(packed_report, compressed=True) != report_hash or digest(report_source) != report_hash:
                raise ValueError('压缩后结果包不一致，未清理')
        store = directory/'store'
        files = sorted(path for path in store.rglob('*') if path.is_file()) if store.is_dir() else []
        hashes = {path.relative_to(store).as_posix():digest(path) for path in files}
        if files:
            packed_store = temporary/'test-store.zip'
            with zipfile.ZipFile(packed_store, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
                for path in files:
                    package.write(path, path.relative_to(store).as_posix())
            with zipfile.ZipFile(packed_store) as package:
                if package.testzip() is not None or set(package.namelist()) != set(hashes):
                    raise ValueError('临时库归档不完整，未清理')
                for name, value in hashes.items():
                    if hashlib.sha256(package.read(name)).hexdigest() != value or digest(store/name) != value:
                        raise ValueError('临时库发生变化，未清理')
        require_unreferenced_case(directory, root)
        if files:
            os.replace(packed_store, store_archive)
        os.replace(packed_task, archive)
        source.unlink()
        removed.append('task.json')
        if report_hash:
            os.replace(packed_report, report_archive)
            before += report_source.stat().st_size
            report_source.unlink()
            removed.append('report.json')
        if files:
            if (any(path.is_symlink() for path in store.rglob('*'))
                    or {path.relative_to(store).as_posix() for path in store.rglob('*') if path.is_file()} != set(hashes)
                    or any(digest(store/name) != value for name,value in hashes.items())):
                raise ValueError('压缩期间测试库发生变化，保留原库与已校验归档')
            require_unused_store(store, root)
            before += sum(path.stat().st_size for path in files)
            shutil.rmtree(store)
            removed.append('store')
        screenshot = directory/'reschat.png'
        if screenshot.is_file():
            before += screenshot.stat().st_size
            screenshot.unlink()
            removed.append('reschat.png')
    if any(digest(directory/name) != value for name,value in kept.items()):
        raise ValueError('保留的报告或原回执发生变化')
    after = archive.stat().st_size + (store_archive.stat().st_size if store_archive.exists() else 0)
    after += report_archive.stat().st_size if report_archive.exists() else 0
    summary = dict(status='compacted_verified', task_id=task['id'], research_status=task['status'],
        original_task_sha256=original_hash, preserved_files=kept, removed_raw_copies=removed,
        original_report_sha256=report_hash,
        before_bytes=before, compressed_bytes=after, saved_bytes=before-after,
        research_reopened=False, report_rewritten=False, financial_quality_accepted=False,
        restoration='task.json.gz、report.json.gz解压恢复完整导出与结果包；test-store.zip解压恢复本次测试库。')
    (directory/'compaction.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def compact_data_phase(directory, *, root=ROOT):
    """Archive a closed independent data-phase test, preserving its failures."""
    root,directory=Path(root).resolve(),Path(directory)
    if (not directory.is_absolute() or directory.is_symlink() or directory.resolve()!=directory
            or directory.parent!=root/'result/acceptance' or not directory.name.startswith('autonomous-data-phase-')):
        raise ValueError('仅处理本项目明确的独立取数验收')
    require_unreferenced_case(directory, root)
    if any(path.is_symlink() for path in directory.rglob('*')):
        raise ValueError('测试目录含链接，未清理')
    receipt=json.loads((directory/'receipt.json').read_text())
    task_path=directory/'task.json';task=json.loads(task_path.read_text())
    if (receipt.get('scope')!='autonomous_source_kernel_data_phase' or receipt.get('status')=='running'
            or any(receipt.get(key) is not True for key in ('owned_runtime_closed','production_unchanged','source_unchanged'))
            or receipt.get('supplied_plan') is not False or receipt.get('supplied_evidence') is not False
            or task.get('status') not in CLOSED or task.get('id')!=receipt.get('task_id')
            or task['status']!=receipt.get('task_status')):
        raise ValueError('不是已关闭并已回收的独立取数验收')
    store=directory/'store';archive=directory/'raw-qa.zip';summary_path=directory/'compaction.json'
    if archive.exists() or summary_path.exists() or any(store.rglob('*.secret')):
        raise ValueError('不覆盖归档，也不归档未清除的凭据')
    require_unused_store(store,root)
    files=[task_path]+sorted(path for path in store.rglob('*') if path.is_file())
    hashes={path.relative_to(directory).as_posix():digest(path) for path in files}
    before=sum(path.stat().st_size for path in files)
    protected={path:digest(path) for path in directory.glob('*.json') if path!=task_path}
    with tempfile.TemporaryDirectory(prefix='.compact-data-',dir=directory) as temporary:
        packed=Path(temporary)/'raw-qa.zip'
        with zipfile.ZipFile(packed,'w',zipfile.ZIP_LZMA) as package:
            for path in files:package.write(path,path.relative_to(directory).as_posix())
        with zipfile.ZipFile(packed) as package:
            if package.testzip() is not None or set(package.namelist())!=set(hashes):
                raise ValueError('取数验收归档不完整')
            if any(hashlib.sha256(package.read(name)).hexdigest()!=value for name,value in hashes.items()):
                raise ValueError('取数验收压缩前后不一致')
        if (any(path.is_symlink() for path in store.rglob('*'))
                or {'task.json'}|{path.relative_to(directory).as_posix() for path in store.rglob('*') if path.is_file()}!=set(hashes)
                or any(digest(directory/name)!=value for name,value in hashes.items())):
            raise ValueError('取数测试库发生变化，保留原件')
        require_unused_store(store,root)
        require_unreferenced_case(directory, root)
        os.replace(packed,archive)
        if store.exists():shutil.rmtree(store)
        task_path.unlink()
    if any(digest(path)!=value for path,value in protected.items()):raise ValueError('原验收摘要发生变化')
    summary=dict(status='compacted_verified',task_id=task['id'],task_status=task['status'],
        files_sha256=hashes,archive_sha256=digest(archive),before_bytes=before,
        compressed_bytes=archive.stat().st_size,saved_bytes=before-archive.stat().st_size,
        financial_approval=False,study_reopened=False,restoration='raw-qa.zip解压可恢复原始task.json与本次测试库。')
    summary_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary
