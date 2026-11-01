"""Extend retained packaged checks without changing the earlier test runner."""
from unittest.mock import patch

from tests import check_report_reader_candidate as retained
from tests.review_single_page_release import REPLACEMENTS

CURRENT_MODULES = ('tests.test_single_page_report_offline',
                   'tests.test_single_page_retained_contracts_offline',
                   'tests.test_fee_retained_versions_offline',
                   'tests.test_report_examples_offline',
                   'tests.test_report_argument_figures_offline')


def run():
    original_ids = retained.test_ids

    def current_ids(names):
        # Existing receipt groups retain exactly their recorded execution count.
        # Only the new packaged regression group adds current report contracts.
        if 'tests.test_report_visual_selection_offline' in names:
            names = list(names)+list(CURRENT_MODULES)
        return original_ids(names)

    replacements = dict(retained.REPLACEMENTS)
    replacements.update(REPLACEMENTS)
    with patch.object(retained, 'REPLACEMENTS', replacements), \
            patch.object(retained, 'test_ids', current_ids):
        return retained.run()


if __name__ == '__main__':
    run()
