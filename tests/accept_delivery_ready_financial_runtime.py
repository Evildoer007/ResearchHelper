"""Shared source, Skill and frozen-App research-contract regressions."""
from tests import accept_initial_acquisition_runtime as previous
from tests.accept_bound_financial_runtime import MODULES as PRIOR_MODULES,TEST_COUNT as PRIOR_COUNT
MODULES=PRIOR_MODULES+['test_derived_calculation_input_binding','test_final_analysis_reserve','test_financial_reading_fragments',
                     'test_bounded_reading_submission','test_financial_date_selector',
                     'test_financial_reference_item_review','test_final_numeric_submission','test_object_series_routing',
                     'test_responses_schema_transport','test_raw_query_provenance','test_dependent_calculation_inputs',
                     'test_report_reading_precision','test_argument_support_review']
TEST_COUNT=PRIOR_COUNT+124


def run(*,frozen=False):
    original=previous.MODULES,previous.TEST_COUNT
    try:
        previous.MODULES,previous.TEST_COUNT=MODULES,TEST_COUNT
        return previous.run_frozen() if frozen else previous.run()
    finally:previous.MODULES,previous.TEST_COUNT=original


def run_frozen():return run(frozen=True)


if __name__=='__main__':run()
