"""Recoverable removal of manifest-proven obsolete demo copies, not reports."""
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import tempfile

ROOT=Path(__file__).resolve().parents[1]
DEMO_ROOTS=(ROOT/'demo',Path('/Users/haoranxu/Desktop/demo/ResearchHelper demo'))
NUMBERED=re.compile(r'(.+) ([2-9]|[1-9]\d+)(\.[^.]+)\Z')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ordinary(path):
    return not path.is_symlink() and path.resolve()==path


def release_authorities(root):
    """An old filename alone never authorizes removal of different bytes."""
    known={}
    for candidate in sorted((root/'result/build-candidates').glob('*/candidate.json')):
        try:
            if not ordinary(candidate):continue
            record=json.loads(candidate.read_text());release=candidate.parent/'release-manifest.json'
            if (record.get('schema')!='researchhelper.current-build.v1'
                    or record.get('status')!='built_local_candidate' or not ordinary(release)
                    or digest(release)!=record['manifest_sha256']):continue
            binding=json.loads(release.read_text())['component_manifests']['demo']
            manifest=candidate.parent/'demo-manifest.json'
            if Path(binding['path'])!=manifest or not ordinary(manifest) or digest(manifest)!=binding['sha256']:continue
            files=json.loads(manifest.read_text())['files']
            for name,value in files.items():
                known.setdefault((name,value),[]).append(str(candidate))
            for name in ('manifest.json','researchhelper-release.json'):
                known.setdefault((name,binding['sha256']),[]).append(str(candidate))
        except (OSError,ValueError,KeyError,TypeError):
            continue
    return known


def controlled_files(root):
    manifest=root/'manifest.json'
    if not ordinary(root) or not root.is_dir() or not ordinary(manifest):
        raise RuntimeError('demo不是普通受控目录')
    files=json.loads(manifest.read_text())['files']
    for name,value in files.items():
        relative=PurePosixPath(name);file=root/name
        if (relative.is_absolute() or '..' in relative.parts or relative.as_posix()!=name
                or not ordinary(file) or not file.is_file() or digest(file)!=value):
            raise RuntimeError('当前demo文件清单已有改动，保留原件')
    return dict(manifest_sha256=digest(manifest),files=files)


def inventory(project=ROOT,roots=DEMO_ROOTS):
    project=Path(project).resolve();known=release_authorities(project)
    plan=dict(project=str(project),roots=[],targets=[],preserved=[])
    for index,root in enumerate(map(Path,roots)):
        current=controlled_files(root)
        plan['roots'].append(dict(path=str(root),**current))
        for file in sorted(root.rglob('*')):
            match=NUMBERED.fullmatch(file.name)
            if not match or not file.is_file():continue
            relative=file.relative_to(root);base=relative.with_name(match[1]+match[3]).as_posix()
            # Historical customer reports are explicitly retained, even when
            # they look like numbered duplicates or match an old release.
            if ('reports' in relative.parts or relative.as_posix() in current['files']
                    or not ordinary(file) or file.suffix not in {'.py','.js','.css','.html','.svg','.json'}):
                plan['preserved'].append(str(file));continue
            value=digest(file);authority=known.get((base,value))
            if not authority:
                plan['preserved'].append(str(file));continue
            info=file.stat()
            plan['targets'].append(dict(root=index,relative=relative.as_posix(),sha256=value,
                bytes=info.st_size,inode=info.st_ino,mtime_ns=info.st_mtime_ns,authority=authority[0]))
    return plan


def apply_inventory(plan,*,project=ROOT,roots=DEMO_ROOTS,recovery=Path('/Users/haoranxu/.Trash')):
    project=Path(project).resolve();roots=tuple(map(Path,roots));recovery=Path(recovery)
    if inventory(project,roots)!=plan:raise RuntimeError('清单、原件或历史依据变化，未移动')
    if (not ordinary(recovery) or not recovery.is_dir() or recovery.is_relative_to(project)
            or any(recovery.is_relative_to(root) for root in roots)):
        raise RuntimeError('恢复位置不安全，未移动')
    if not plan['targets']:return dict(status='nothing_to_retire',moved=0)
    destination=Path(tempfile.mkdtemp(prefix='ResearchHelper-numbered-demo-copies-',dir=recovery))
    output=Path(tempfile.mkdtemp(prefix='numbered-demo-copies-',dir=project/'result/maintenance'))
    record=dict(status='moving',plan=plan,recovery=str(destination),moves=[],permanent_deletion=False)
    try:
        for item in plan['targets']:
            source=roots[item['root']]/item['relative'];info=source.stat()
            if (not ordinary(source) or [info.st_ino,info.st_mtime_ns,info.st_size]!=[
                    item['inode'],item['mtime_ns'],item['bytes']] or digest(source)!=item['sha256']):
                raise RuntimeError('副本变化，停止移动')
            target=destination/('demo-'+str(item['root']))/item['relative']
            target.parent.mkdir(parents=True,exist_ok=True)
            source.rename(target)
            record['moves'].append(dict(source=str(source),destination=str(target),sha256=item['sha256']))
            if digest(target)!=item['sha256']:raise RuntimeError('恢复副本校验失败')
        for root,previous in zip(roots,plan['roots']):
            if controlled_files(root)!=dict(manifest_sha256=previous['manifest_sha256'],files=previous['files']):
                raise RuntimeError('当前demo内容发生变化')
        record.update(status='retired_recoverably',moved=len(record['moves']),
            bytes_moved=sum(item['bytes'] for item in plan['targets']),current_demos_unchanged=True)
    except BaseException:
        record['status']='requires_review'
        raise
    finally:
        (output/'receipt.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
    return dict(status=record['status'],moved=record['moved'],bytes_moved=record['bytes_moved'],
        receipt=str(output/'receipt.json'),recovery=str(destination))
