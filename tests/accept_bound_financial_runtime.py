"""Keep555 checks and add11 real-source context/quote regressions."""
from tests import accept_initial_acquisition_runtime as previous
from tests.accept_strict_financial_container_runtime import MODULES as PRIOR_MODULES,TEST_COUNT as PRIOR_COUNT
MODULES=PRIOR_MODULES+['test_financial_context_choices']
TEST_COUNT=PRIOR_COUNT+11


def run(*,frozen=False):
    original=previous.MODULES,previous.TEST_COUNT
    try:
        previous.MODULES,previous.TEST_COUNT=MODULES,TEST_COUNT
        return previous.run_frozen() if frozen else previous.run()
    finally:previous.MODULES,previous.TEST_COUNT=original


def run_frozen():return run(frozen=True)


if __name__=='__main__':run()
