"""Retained package scope plus current independent workflow/report checks."""
from unittest.mock import patch

from tests import check_report_reader_candidate as retained
from tests import check_single_page_candidate as reader
from tests.harness_release_gate import current_gate
from tests.harness_report_lineage import REPORT_POLICY

CURRENT_MODULES = reader.CURRENT_MODULES + (
    'tests.test_harness_current_inventory_offline',
    'tests.test_harness_retained_workflows_offline',
    'tests.test_report_measurement_accessibility_offline',
    'tests.test_report_editorial_figures_offline',
    'tests.test_reference_report_figures_offline',
    'tests.test_reference_harness_policy_offline',
    'tests.test_filing_publication_original_offline')


def run():
    original_ids = retained.test_ids
    def current_ids(names):
        identifiers = [REPORT_POLICY.get(identifier, identifier) for identifier in original_ids(names)]
        # The additional group names the independently checked transitions too.
        # Run those once per context, not once for each obsolete predecessor.
        return list(dict.fromkeys(identifiers)) if 'tests.test_reference_harness_policy_offline' in names else identifiers
    with patch.object(retained, 'current_gate', current_gate), \
            patch.object(retained, 'test_ids', current_ids), \
            patch.object(reader, 'CURRENT_MODULES', CURRENT_MODULES):
        return reader.run()


if __name__ == '__main__':
    run()
