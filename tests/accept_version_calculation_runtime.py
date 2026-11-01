"""Version comparisons in the same source, Skill and frozen App cohort."""
from tests import accept_filing_filter_runtime as acceptance
from tests import accept_planning_effort_runtime as previous

MODULES=[*previous.MODULES, 'test_version_calculation_changes']
TEST_COUNT=previous.TEST_COUNT+22


def run():
    acceptance.MODULES, acceptance.TEST_COUNT=MODULES, TEST_COUNT
    return acceptance.run()


def run_frozen():
    original=previous.MODULES, previous.TEST_COUNT
    try:
        previous.MODULES, previous.TEST_COUNT=MODULES, TEST_COUNT
        previous.run_frozen()
        from tests.accept_version_calculation_view import run as view
        view()
    finally:
        previous.MODULES, previous.TEST_COUNT=original


if __name__=='__main__':run()
