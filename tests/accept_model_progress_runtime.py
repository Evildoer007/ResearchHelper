"""Same progress and prior financial assertions in source, Skill and frozen App."""
from tests import accept_filing_filter_runtime as acceptance
from tests import accept_financial_chart_runtime as previous

MODULES = [*previous.MODULES, 'test_model_progress_checkpoint']
TEST_COUNT = previous.TEST_COUNT + 9


def run():
    acceptance.MODULES, acceptance.TEST_COUNT = MODULES, TEST_COUNT
    return acceptance.run()


def run_frozen():
    original = previous.MODULES, previous.TEST_COUNT
    try:
        previous.MODULES, previous.TEST_COUNT = MODULES, TEST_COUNT
        previous.run_frozen()
        from tests.accept_model_progress_view import run as progress_view
        from tests.accept_reschat_submission import run as submission
        progress_view()
        submission()
    finally:
        previous.MODULES, previous.TEST_COUNT = original


if __name__ == '__main__':
    run()
