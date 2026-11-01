"""Keep the495 existing checks and add financial-routing/anchor diagnostics."""
from tests import accept_initial_acquisition_runtime as previous

MODULES = previous.MODULES + ['test_reading_anchor_diagnostics','test_financial_acquisition_focus']
TEST_COUNT = previous.TEST_COUNT + 8


def run(*, frozen=False):
    original = previous.MODULES, previous.TEST_COUNT
    try:
        previous.MODULES, previous.TEST_COUNT = MODULES, TEST_COUNT
        return previous.run_frozen() if frozen else previous.run()
    finally:
        previous.MODULES, previous.TEST_COUNT = original


def run_frozen():
    return run(frozen=True)


if __name__ == '__main__':
    run()
