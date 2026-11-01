"""Add direct-observation cases without modifying the previous acceptance suite."""
from tests import accept_filing_filter_runtime as acceptance

MODULES = [*acceptance.MODULES, 'test_direct_code_observation']
TEST_COUNT = 64


def run():
    acceptance.MODULES = MODULES
    acceptance.TEST_COUNT = TEST_COUNT
    return acceptance.run()


if __name__ == '__main__':
    run()
