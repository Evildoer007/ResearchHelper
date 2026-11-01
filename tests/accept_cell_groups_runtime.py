"""Run all prior guards plus the new grouped short-reference cases."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests import accept_filing_filter_runtime as acceptance
from tests.accept_source_requirement_runtime import MODULES as PREVIOUS, TEST_COUNT as COUNT

MODULES=[*PREVIOUS,'test_api_cell_selection_groups']
TEST_COUNT=COUNT+11


def run():
    acceptance.MODULES,acceptance.TEST_COUNT=MODULES,TEST_COUNT
    return acceptance.run()


if __name__=='__main__':
    run()
