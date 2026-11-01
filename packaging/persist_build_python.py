"""Persist the selected existing interpreter, not a newly created environment."""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def persist(root=ROOT, selected=None):
    selected = Path(selected or sys.executable)
    if not selected.is_absolute() or not selected.is_file():
        raise ValueError('构建解释器须为存在的绝对文件路径')
    destination = Path(root) / '.researchhelper/runtime/build-python-path'
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.build-python-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            stream.write(str(selected.resolve()) + '\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


if __name__ == '__main__':
    print(persist())
