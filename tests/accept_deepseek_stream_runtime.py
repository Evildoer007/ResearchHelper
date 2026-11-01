"""The SSE wire codec in source, independent Skill and actual frozen App."""
from tests import accept_filing_filter_runtime as acceptance
from tests import accept_version_calculation_runtime as previous

MODULES=[*previous.MODULES,'test_deepseek_stream']
TEST_COUNT=previous.TEST_COUNT+29


def run():
    acceptance.MODULES,acceptance.TEST_COUNT=MODULES,TEST_COUNT
    return acceptance.run()


def run_frozen():
    original=previous.MODULES,previous.TEST_COUNT
    try:
        previous.MODULES,previous.TEST_COUNT=MODULES,TEST_COUNT
        previous.run_frozen()
    finally:
        previous.MODULES,previous.TEST_COUNT=original


if __name__=='__main__':run()
