"""One development import root; installed packages remain self-contained."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def configure_imports():
    source = PROJECT_ROOT / 'core/src'
    if not (source / 'researchhelper/__init__.py').is_file():
        raise RuntimeError('ResearchHelper development source is missing')
    location = str(source)
    if location not in sys.path:
        sys.path.insert(0, location)
    return PROJECT_ROOT
