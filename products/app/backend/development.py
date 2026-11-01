"""Development workspace entry; the native App uses sibling entry.py."""

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from core.bootstrap import configure_imports

configure_imports()
from researchhelper.service import serve


def main():
    serve(open_browser=True)


if __name__ == "__main__":
    main()
