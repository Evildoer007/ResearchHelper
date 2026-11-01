"""Integration test namespace spanning the core, product and packaging suites."""
from pathlib import Path
from core.bootstrap import configure_imports

ROOT = configure_imports()
__path__ += [str(ROOT / folder) for folder in ('core/tests', 'products/app/tests', 'packaging/tests')]
