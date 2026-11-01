"""Retain packaged coverage and exercise the shared planner in actual payloads.

The source-Skill import check is deliberately excluded: it does not establish
that a distributed Skill entry loads its own library. Independent ZIP/installed
entry acceptance covers that separate boundary before publication.
"""
from unittest.mock import patch

from tests import check_harness_candidate as retained
from tests.check_thesis_harness_candidate import CURRENT_CHECKS
from tests.check_report_reader_candidate import test_ids


def run():
    brief_checks = [identifier for identifier in test_ids([
        'tests.test_research_brief_offline.ResearchBriefTests'])
        if not identifier.endswith('.test_source_skill_and_app_share_brief_without_second_model')]
    assert len(brief_checks) == 9
    with patch.object(retained, 'CURRENT_MODULES',
                      retained.CURRENT_MODULES + CURRENT_CHECKS + tuple(brief_checks)):
        return retained.run()


if __name__ == '__main__':
    run()
