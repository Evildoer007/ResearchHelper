"""Keep the existing cohort and add independent customer-paper boundaries."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from tests import accept_judgment_review_runtime as previous

MODULES = previous.MODULES + ['test_report_version_paper']
TEST_COUNT = previous.TEST_COUNT + 4


def run(*, frozen=False):
    original = previous.MODULES, previous.TEST_COUNT
    try:
        previous.MODULES, previous.TEST_COUNT = MODULES, TEST_COUNT
        return previous.run_frozen() if frozen else previous.run()
    finally:
        previous.MODULES, previous.TEST_COUNT = original


def run_frozen():
    return run(frozen=True)


if __name__ == '__main__': run()
