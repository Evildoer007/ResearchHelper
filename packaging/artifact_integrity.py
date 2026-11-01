"""Bind directory deliveries as strictly as installer files."""
import hashlib
import json
from pathlib import Path
from source_snapshot import digest


def directory_record(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError('Directory delivery is absent or is a symlink')
    files = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise RuntimeError('Directory delivery cannot contain symlinks')
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = digest(path)
    if not files or 'manifest.json' not in files:
        raise RuntimeError('Directory delivery needs its own manifest')
    return {'path': str(directory), 'files': files,
            'sha256': hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def verify_directory(record, payload):
    path = Path(record['path'])
    payload = Path(payload).resolve()
    if not path.resolve().is_relative_to(payload) or path.resolve() == payload:
        raise RuntimeError('Directory delivery leaves candidate payload')
    actual = directory_record(path)
    if actual['files'] != record['files'] or actual['sha256'] != record['sha256']:
        raise RuntimeError('Directory delivery does not match its complete inventory')
    return actual
