"""Recoverably retire only exact, migration-recorded obsolete icon/Skill copies.

Current sources, releases, installed Skill and both demos remain protected.
The retained historical demo and historical JSON evidence are not targets.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'packaging'))
from build_current import build_lock,verify_current_build,write_record

AUTHORITY=Path('result/maintenance/development-layout-20261010')
AUTHORITY_HASHES={
    'moves.json':'379f090677a120ec6a240111eae97219b265d2195dcb25c52caf82ad4f9d4428',
    'relocation.json':'a78f13ea8cbec05c59112a8256a6a8e035b4a67811f96c36211767f5a55e9651',
    'acceptance.json':'33df0173d5288b5d2edbd2fc78806753c4153369141fc95b94f9dee6e704cce2',
}
TARGETS={
    'versions/app/v0.10.0/ResearchHelper.iconset':'dist/0.10.0/ResearchHelper.iconset',
    'versions/app/v0.10.0/ResearchHelper.icns':'dist/0.10.0/ResearchHelper.icns',
    'versions/skill/v0.10.0/researchhelper':'dist/0.10.0/researchhelper',
}


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_root(root):
    root=Path(root)
    if not root.is_absolute() or root!=root.resolve() or not root.is_dir() or (
            root!=ROOT and not root.is_relative_to(ROOT/'result/acceptance')):
        raise RuntimeError('只允许本ResearchHelper或其独立验收目录')
    return root


def authority(root):
    records={}
    for name,expected in AUTHORITY_HASHES.items():
        path=root/AUTHORITY/name
        if path.is_symlink() or path.resolve()!=path or digest(path)!=expected:
            raise RuntimeError('历史迁移依据发生变化，未清理')
        records[name]=json.loads(path.read_text())
    moves=records['moves.json']
    relocation,accepted=records['relocation.json'],records['acceptance.json']
    if (not isinstance(moves,list) or relocation.get('moves')!=len(moves) or
            relocation.get('status')!='relocated_not_yet_accepted' or
            relocation.get('optionHelperModified') is not False or accepted.get('status')!='scoped_passed'):
        raise RuntimeError('历史迁移未具备完整验收依据')
    result={}
    for target,source in TARGETS.items():
        matches=[item for item in moves if item.get('target')==target and item.get('source')==source]
        if len(matches)!=1 or not isinstance(matches[0].get('files'),dict) or not matches[0]['files']:
            raise RuntimeError('旧交付物缺少唯一迁移清单')
        result[target]=matches[0]['files']
    return result


def snapshot(path,expected):
    if path.is_symlink() or path.resolve()!=path:
        raise RuntimeError('旧交付物含链接，保留原件')
    directory='' not in expected
    if directory!=path.is_dir(): raise RuntimeError('旧交付物类型已变化')
    paths=sorted(path.rglob('*')) if directory else [path]
    files,identities,directories={},{},set()
    for item in paths:
        info=item.lstat()
        name=item.relative_to(path).as_posix() if directory else ''
        if stat.S_ISDIR(info.st_mode): directories.add(name)
        elif stat.S_ISREG(info.st_mode):
            files[name]=digest(item)
            identities[name]=[info.st_ino,info.st_size,info.st_mtime_ns]
        else: raise RuntimeError('旧交付物包含链接或非普通文件')
    parents={parent.as_posix() for name in expected for parent in PurePosixPath(name).parents
             if parent.as_posix()!='.'} if directory else set()
    if files!=expected or directories!=parents:
        raise RuntimeError('旧交付物存在改动、缺失或额外文件，保留原件')
    info=path.lstat()
    return dict(files=files,identities=identities,directories=sorted(directories),
                inode=info.st_ino,mtime_ns=info.st_mtime_ns,bytes=sum(row[1] for row in identities.values()))


def inventory(candidate,root=ROOT):
    root=checked_root(root)
    current=verify_current_build(candidate,root)
    known=authority(root)
    protected=[Path(item['path']) for item in current['artifacts'].values()]
    protected += [Path(item['path']) for item in current['directory_artifacts'].values()]
    tracked=subprocess.run(['git','ls-files','-z','--',*TARGETS],cwd=root,
        check=True,capture_output=True).stdout.decode().split('\0')
    targets,skipped=[],[]
    for name,expected in known.items():
        path=root/name
        if not path.exists() and not path.is_symlink(): continue
        if any(path==keep or path.is_relative_to(keep) or keep.is_relative_to(path) for keep in protected):
            raise RuntimeError('旧路径仍被当前交付使用，未清理')
        if any(item==name or item.startswith(name+'/') for item in tracked):
            skipped.append(dict(source=str(path),reason='受Git跟踪的文件保持原位'));continue
        try: stamp=snapshot(path,expected)
        except (OSError,RuntimeError) as error:
            skipped.append(dict(source=str(path),reason=str(error)));continue
        targets.append(dict(source=str(path),relative=name,snapshot=stamp))
    return dict(root=str(root),candidate=str(Path(candidate).resolve()),authority_hashes=AUTHORITY_HASHES,
                targets=targets,skipped=skipped)


def ensure_idle(path):
    command=['/usr/sbin/lsof','-t',*(['+D',str(path)] if path.is_dir() else ['--',str(path)])]
    result=subprocess.run(command,capture_output=True,text=True,timeout=20)
    if result.returncode not in (0,1) or result.stdout.strip():
        raise RuntimeError('旧交付物仍被进程打开，保留原位')


def retire(candidate,root=ROOT,recovery=None):
    root=checked_root(root)
    with build_lock(root):
        plan=inventory(candidate,root)
        if not plan['targets']: return dict(status='nothing_to_retire',skipped=plan['skipped'])
        trash=Path('/Users/haoranxu/.Trash') if recovery is None else Path(recovery)
        if trash.is_symlink() or trash.resolve()!=trash or not trash.is_dir() or trash.stat().st_dev!=root.stat().st_dev:
            raise RuntimeError('需要可恢复的同卷普通废纸篓目录')
        for item in plan['targets']: ensure_idle(Path(item['source']))
        output=root/'result/maintenance/legacy-deliverable-retirement'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')/'receipt.json'
        output.parent.mkdir(parents=True,exist_ok=False)
        recovery=Path(tempfile.mkdtemp(prefix='ResearchHelper-legacy-deliverables-',dir=trash))
        record=dict(plan,status='moving',recovery=str(recovery),moves=[],permanent_deletion=False,
                    production_store_touched=False,current_release_rebuilt=False)
        write_record(output,record)
        try:
            for item in plan['targets']:
                path=Path(item['source'])
                known=authority(root)
                if snapshot(path,known[item['relative']])!=item['snapshot']:
                    raise RuntimeError('旧交付物在核对后发生变化，停止清理')
                ensure_idle(path)
                if snapshot(path,known[item['relative']])!=item['snapshot']:
                    raise RuntimeError('旧交付物在占用检查期间发生变化，停止清理')
                destination=recovery/item['relative']
                destination.parent.mkdir(parents=True,exist_ok=True)
                move=dict(source=str(path),destination=str(destination),moved=False)
                record['moves'].append(move);write_record(output,record)
                path.rename(destination);move['moved']=True;write_record(output,record)
                if snapshot(destination,known[item['relative']])['files']!=item['snapshot']['files']:
                    raise RuntimeError('恢复目录内容不匹配，需人工核对')
                move['content_verified']=True;write_record(output,record)
            verify_current_build(candidate,root)
            record.update(status='retired_recoverably',moved=len(record['moves']),
                files_moved=sum(len(item['snapshot']['files']) for item in plan['targets']),
                bytes_moved=sum(item['snapshot']['bytes'] for item in plan['targets']),current_candidate_verified=True)
        except BaseException:
            record['status']='partial_retirement_requires_review';raise
        finally: write_record(output,record)
        return dict(status=record['status'],moved=record['moved'],files=record['files_moved'],
                    bytes=record['bytes_moved'],skipped=record['skipped'],receipt=str(output))
