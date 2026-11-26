"""Integration test namespace spanning the core, product and packaging suites."""
import gzip
from pathlib import Path
from core.bootstrap import configure_imports

ROOT = configure_imports()
__path__ += [str(ROOT / folder) for folder in ('core/tests', 'products/app/tests', 'packaging/tests')]


def read_retained_export(path: Path):
    """Return the original bytes of a closed acceptance export."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        with gzip.open(path.with_suffix(path.suffix + '.gz'), 'rb') as stream:
            return stream.read()
