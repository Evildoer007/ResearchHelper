"""Preserve515 existing checks and add9 financial submission regressions."""
from tests import accept_initial_acquisition_runtime as previous
from tests.accept_financial_wire_runtime import MODULES as PRIOR_MODULES,TEST_COUNT as PRIOR_COUNT

MODULES=PRIOR_MODULES+['test_financial_reference_diagnostics','test_strict_financial_extraction']
TEST_COUNT=PRIOR_COUNT+9


def run(*,frozen=False):
    original=previous.MODULES,previous.TEST_COUNT
    try:
        previous.MODULES,previous.TEST_COUNT=MODULES,TEST_COUNT
        return previous.run_frozen() if frozen else previous.run()
    finally:
        previous.MODULES,previous.TEST_COUNT=original


def run_frozen():return run(frozen=True)


if __name__=='__main__':run()
