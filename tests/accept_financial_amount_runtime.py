"""Verify coupled financial units and original periods in source and Skill."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests import accept_filing_filter_runtime as acceptance
from tests.accept_need_driven_material_runtime import MODULES as PREVIOUS,TEST_COUNT as COUNT

MODULES=[*PREVIOUS,'test_financial_amount_representations','test_current_original_financial_period','test_financial_presentation_units']
TEST_COUNT=COUNT+25


def run():
    acceptance.MODULES,acceptance.TEST_COUNT=MODULES,TEST_COUNT
    return acceptance.run()


if __name__=='__main__':run()
