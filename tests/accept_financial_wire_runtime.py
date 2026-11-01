"""Retain the complete503 checks and add12 navigation/wire regressions."""
from tests import accept_initial_acquisition_runtime as previous
from tests.accept_financial_original_runtime import MODULES as ORIGINAL_MODULES, TEST_COUNT as ORIGINAL_COUNT

MODULES=ORIGINAL_MODULES+['test_acquisition_request_allocation','test_material_wire_contract']
TEST_COUNT=ORIGINAL_COUNT+12


def run(*,frozen=False):
    original=previous.MODULES,previous.TEST_COUNT
    try:
        previous.MODULES,previous.TEST_COUNT=MODULES,TEST_COUNT
        return previous.run_frozen() if frozen else previous.run()
    finally:
        previous.MODULES,previous.TEST_COUNT=original


def run_frozen():
    return run(frozen=True)


if __name__=='__main__':run()
