"""Retain all source guards and test continuous client-paper references."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests import accept_filing_filter_runtime as acceptance
from tests.accept_filing_alias_runtime import MODULES as PREVIOUS,TEST_COUNT as COUNT
MODULES=[*PREVIOUS,'test_client_reference_carriers']
TEST_COUNT=COUNT+4


def run():
    acceptance.MODULES,acceptance.TEST_COUNT=MODULES,TEST_COUNT
    return acceptance.run()


if __name__=='__main__':run()
