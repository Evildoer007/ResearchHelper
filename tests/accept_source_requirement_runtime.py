"""Extend the prior source/independent Skill checks without overwriting them."""
from tests import accept_filing_filter_runtime as acceptance
from tests.accept_direct_code_runtime import MODULES as PRIOR_MODULES, TEST_COUNT as PRIOR_COUNT

MODULES = [*PRIOR_MODULES, 'test_source_requirement_lifecycle']
TEST_COUNT = PRIOR_COUNT + 21


def run():
    acceptance.MODULES, acceptance.TEST_COUNT = MODULES, TEST_COUNT
    acceptance.run()


if __name__ == '__main__':
    run()
