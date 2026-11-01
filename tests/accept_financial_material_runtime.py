"""Retain previous contracts and evaluate real catalogue coverage and dates."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests import accept_filing_filter_runtime as acceptance
from tests.accept_reference_carrier_runtime import MODULES as PREVIOUS,TEST_COUNT as COUNT

MODULES=[*PREVIOUS,'test_material_publication_spacing','test_filing_choice_coverage','test_filing_selection_prompt_contract']
TEST_COUNT=COUNT+23


def run():
    acceptance.MODULES,acceptance.TEST_COUNT=MODULES,TEST_COUNT
    return acceptance.run()


if __name__=='__main__':run()
