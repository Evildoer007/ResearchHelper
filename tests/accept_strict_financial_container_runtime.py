"""Retain524 prior assertions and add20 container/date/selector regressions."""
from tests import accept_initial_acquisition_runtime as previous
from tests.accept_strict_financial_runtime import MODULES as PRIOR_MODULES,TEST_COUNT as PRIOR_COUNT
MODULES=['test_financial_table_reading_v3' if name=='test_complete_table_reading' else name for name in PRIOR_MODULES]+[
                       'test_provider_container_schema','test_financial_cell_reuse','test_financial_selector_protocol',
                       'test_registered_financial_reading','test_filing_object_binding']
TEST_COUNT=PRIOR_COUNT+31


def run(*,frozen=False):
    original=previous.MODULES,previous.TEST_COUNT
    try:
        previous.MODULES,previous.TEST_COUNT=MODULES,TEST_COUNT
        return previous.run_frozen() if frozen else previous.run()
    finally:previous.MODULES,previous.TEST_COUNT=original


def run_frozen():return run(frozen=True)


if __name__=='__main__':run()
