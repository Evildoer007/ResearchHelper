"""Statement scope/amount association in the source, Skill and frozen App."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests import accept_filing_filter_runtime as acceptance
from tests.accept_complete_table_runtime import MODULES as PREVIOUS,TEST_COUNT as COUNT

MODULES=[*PREVIOUS,'test_financial_statement_scope']
TEST_COUNT=COUNT+15


def run():
    acceptance.MODULES,acceptance.TEST_COUNT=MODULES,TEST_COUNT
    return acceptance.run()


def run_frozen():
    from tests import accept_packaged_filing_filters as packaged
    from tests.accept_packaged_need_driven_materials import frozen_material_runner
    packaged.MODULES,packaged.TEST_COUNT=MODULES,TEST_COUNT
    packaged.run_packaged_tests=frozen_material_runner()
    return packaged.run()


if __name__=='__main__': run()
