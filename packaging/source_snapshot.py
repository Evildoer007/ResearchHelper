"""Explicit source map for secret-free, reproducible App and Skill candidates."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_files(root):
    root = Path(root).resolve()
    record = json.loads((root / 'packaging/package-source-map.json').read_text())
    names = record['files']
    if not isinstance(names, list) or len(names) != len(set(names)):
        raise RuntimeError('Build source map must contain unique paths')
    files = []
    for name in names:
        if not isinstance(name, str) or '\\' in name or ':' in name:
            raise RuntimeError('Build source paths must use canonical relative POSIX notation')
        path = PurePosixPath(name)
        if path.as_posix() != name or path.is_absolute() or '..' in path.parts or any(part.startswith('.') for part in path.parts):
            raise RuntimeError('Unsafe build source path')
        candidate = root / name
        if not candidate.is_file() or not candidate.resolve().is_relative_to(root):
            raise RuntimeError('Build input is absent or leaves the project: ' + name)
        for ancestor in (candidate, *candidate.parents):
            if ancestor == root:
                break
            if ancestor.is_symlink():
                raise RuntimeError('Build input is a symlink: ' + name)
        if path.parts[0] in {'data', 'result', 'versions', 'dist', 'demo', 'maintenance'} or 'tests' in path.parts:
            # Only the explicitly identified public report area is releasable.
            if not name.startswith('products/app/data/'):
                raise RuntimeError('Private/development area is not a build input')
        files.append(candidate)
    from shared_payload import runtime_sources
    missing = set(runtime_sources(root)) - set(files)
    if missing:
        raise RuntimeError('研究内核源码未登记到打包白名单：' +
                           ', '.join(sorted(str(file.relative_to(root)) for file in missing)))
    return sorted(files)


def freeze_sources(root, target):
    root, target = Path(root).resolve(), Path(target)
    inventory = {}
    for source in source_files(root):
        relative = source.relative_to(root)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        inventory[relative.as_posix()] = digest(source)
        if digest(destination) != inventory[relative.as_posix()]:
            raise RuntimeError('Source changed during snapshot: ' + str(relative))
    return inventory
