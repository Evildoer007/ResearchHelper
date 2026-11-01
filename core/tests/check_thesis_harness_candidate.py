"""Extend packaged-code acceptance with the new prerequisite and sample checks.

The retained acceptance runner and all previous test cases stay unchanged.
Interface installation and native compilation belong to their source gates;
these added checks must load each candidate's actual research engine.
"""
from unittest.mock import patch

from tests import check_harness_candidate as retained

CURRENT_CHECKS = (
    'tests.test_thesis_obligations_offline.ThesisObligationTests',
    'tests.test_thesis_obligation_interfaces_offline.ThesisInterfaceTests.test_completed_registered_method_requires_explicit_thesis_reference',
    'tests.test_report_full_sample_chart_offline.FullSampleChartTests',
)


def run():
    with patch.object(retained, 'CURRENT_MODULES', retained.CURRENT_MODULES + CURRENT_CHECKS):
        return retained.run()


if __name__ == '__main__':
    run()
