"""Frozen backend entry, launched by the native App rather than user commands."""

import sys
import multiprocessing
from pathlib import Path

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if not getattr(sys, 'frozen', False):
        project = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(project))
        from core.bootstrap import configure_imports
        configure_imports()
    from researchhelper.service import serve

    resources = Path(sys.executable).resolve().parents[2] if getattr(sys, "frozen", False) else project
    serve(resources)
