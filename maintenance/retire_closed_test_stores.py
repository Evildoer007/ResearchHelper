"""Recoverably retire explicit, unreferenced, closed QA Stores only.

Keep task exports, client reports and receipts in place. Unknown stores,
referenced fixtures, production data, source and active processes are excluded.
"""
from datetime import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TRASH = Path('/Users/haoranxu/.Trash')
NAME = re.compile(r'one-sentence-research-[a-z0-9_]+\Z')
CLOSED = {'已完成', '部分完成', '失败', '已取消'}
FLAGS = ('service_stopped', 'browser_stopped', 'temporary_credentials_cleared',
    'temporary_execution_policy_disabled', 'production_unchanged')


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def referenced_cases(root, names):
    """Find exact QA directory names in the maintained test and product sources."""
    names = set(names)
    if not names:
        return set()
    alternatives = b'|'.join(re.escape(name.encode('utf-8')) for name in sorted(names))
    pattern = re.compile(rb'(?<![A-Za-z0-9_.-])(?:' + alternatives + rb')(?![A-Za-z0-9_.-])')
    suffixes = {'.py', '.js', '.cjs', '.mjs', '.ts', '.tsx', '.swift', '.cs', '.json'}
    excluded = {'node_modules', '__pycache__', '.pytest_cache', '.git'}
    found = set()
    for name in ('tests', 'core', 'modules', 'products', 'maintenance', 'packaging'):
        area = root / name
        if area.is_symlink():
            continue
        for directory, folders, files in os.walk(area):
            folders[:] = [name for name in folders if name not in excluded]
            for name in files:
                path = Path(directory) / name
                if path.suffix not in suffixes or path.is_symlink() or not path.is_file():
                    continue
                found.update(match.group().decode('utf-8') for match in pattern.finditer(path.read_bytes()))
                if found == names:
                    return found
    return found


def tree(path):
    result = {}
    for file in sorted(path.rglob('*')):
        if file.is_symlink():
            raise RuntimeError('临时测试库含软链接，保留原件')
        if file.is_file():
            if file.suffix == '.secret':
                raise RuntimeError('临时凭据未清除，保留测试库')
            result[file.relative_to(path).as_posix()] = dict(bytes=file.stat().st_size, sha256=digest(file))
    if not result or 'researchhelper.sqlite3' not in result:
        raise RuntimeError('目录不是完整ResearchHelper测试库，保留原件')
    return result


def unused(path):
    opened = subprocess.run(['/usr/sbin/lsof', '+D', str(path)], capture_output=True, timeout=15)
    if opened.returncode not in (0, 1) or opened.stdout.strip():
        raise RuntimeError('临时测试库仍被进程使用，保留原件')


def inventory(names, *, root=ROOT):
    root = Path(root).resolve()
    if (not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not NAME.fullmatch(name) for name in names)
            or len(names) != len(set(names))):
        raise ValueError('须指定不重复的明确验收目录')
    if referenced_cases(root, names):
        raise ValueError('验收目录仍被代码引用，保留测试库')
    items = []
    for name in names:
        case = root/'result/acceptance'/name
        store = case/'store'
        if case.is_symlink() or store.is_symlink() or not store.is_dir() or store.resolve() != store:
            raise ValueError('测试库须为本项目明确的普通子目录')
        receipt = case/'receipt.json'
        if receipt.is_symlink():
            raise ValueError('回执不是普通文件')
        record = json.loads(receipt.read_text())
        if (record.get('status') != 'collected_for_independent_review'
                or record.get('research_status') not in CLOSED or not all(record.get(key) is True for key in FLAGS)
                or 'temporary Store is empty' not in record.get('browser', {}).get('checks', [])):
            raise ValueError('验收未确认从空白临时库发起并完全关闭，不清理')
        task_path = case/'task.json'
        if not task_path.is_file():
            task_path = case/'task.json.gz'
        if task_path.is_symlink() or not task_path.is_file():
            raise ValueError('缺少独立研究导出，保留测试库')
        opener = gzip.open if task_path.suffix == '.gz' else open
        with opener(task_path, 'rt', encoding='utf-8') as stream:
            task = json.load(stream)
        if task.get('id') != record.get('task_id') or task.get('status') != record['research_status']:
            raise ValueError('研究导出与关闭回执不一致')
        unused(store)
        files = tree(store)
        kept = {file.name: digest(file) for file in case.iterdir()
            if file.is_file() and not file.is_symlink()}
        items.append(dict(case=name, source=str(store), files=files, preserved_files=kept,
            bytes=sum(file['bytes'] for file in files.values())))
    return dict(schema='researchhelper.closed-test-stores.v1', root=str(root), items=items)


def apply_inventory(plan, *, root=ROOT, recovery=TRASH):
    root, recovery = Path(root).resolve(), Path(recovery)
    if plan.get('root') != str(root) or plan.get('schema') != 'researchhelper.closed-test-stores.v1':
        raise ValueError('清理清单不属于本项目')
    if recovery.is_symlink() or not recovery.is_dir() or recovery.resolve() != recovery:
        raise ValueError('可恢复目录不可用，不清理')
    fresh = inventory([item['case'] for item in plan['items']], root=root)
    if fresh != plan:
        raise ValueError('清单后文件发生变化，保留全部原件')
    if recovery.stat().st_dev != root.stat().st_dev:
        raise ValueError('可恢复移动须在同一卷')
    output = root/'result/maintenance/closed-test-store-retirement'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True)
    destination = Path(tempfile.mkdtemp(prefix='ResearchHelper-closed-test-stores-', dir=recovery))
    record = dict(status='started', plan=plan, recovery=str(destination), moved=[])
    receipt = output/'receipt.json'
    try:
        for item in plan['items']:
            source = Path(item['source'])
            unused(source)
            if tree(source) != item['files']:
                raise ValueError('移动前文件发生变化，保留原件')
            target = destination/item['case']
            if target.exists():
                raise ValueError('恢复目标已存在，不覆盖')
            receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            os.rename(source, target)
            record['moved'].append(dict(source=str(source), recovery=str(target), bytes=item['bytes']))
            if tree(target) != item['files']:
                raise RuntimeError('移动后摘要不一致，请按回执检查恢复目录')
            case = source.parent
            if any(digest(case/name) != value for name, value in item['preserved_files'].items()):
                raise RuntimeError('保留的研究导出、报告或回执发生变化')
        record.update(status='retired_recoverably', retired_bytes=sum(item['bytes'] for item in plan['items']))
    finally:
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return receipt
