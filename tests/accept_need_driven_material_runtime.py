"""Source and standalone Skill acceptance, without real external calls."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import accept_filing_filter_runtime as acceptance
from tests.accept_financial_material_runtime import MODULES as PREVIOUS, TEST_COUNT as COUNT

MODULES = [*PREVIOUS, 'test_need_driven_acquisition', 'test_current_original_availability', 'test_current_analysis_shape_examples']
TEST_COUNT = COUNT + 38


def run():
    acceptance.MODULES, acceptance.TEST_COUNT = MODULES, TEST_COUNT
    return acceptance.run()


if __name__ == '__main__': run()
