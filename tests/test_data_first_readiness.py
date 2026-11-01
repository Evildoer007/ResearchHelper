"""Decision-boundary fixtures only; no supplier or financial acceptance."""
import copy
import unittest
from unittest.mock import patch

from tests import accept_data_first_readiness as audit
from researchhelper.contracts import ContractError


class DataFirstReadinessTests(unittest.TestCase):
    def inspect(self, *, source_valid=True, links=True, required=True,
                review=None, review_error=False, provenance_error=False):
        task = dict(id='fixture', version=1, status='已完成', source_records=[{}],
                    evidence=[dict(id='e1')], host_result=dict(answers=dict(q1={})))
        inventory = dict(required_data_obligations_settled=links,
                         questions=[dict(question_id='q1', required=required)])
        before = copy.deepcopy(task)
        with patch.object(audit, 'inspect_source', return_value=dict(snapshot_hash_valid=source_valid)), \
             patch.object(audit, 'requirement_inventory', return_value=inventory), \
             patch.object(audit, 'validate_provenance', side_effect=ContractError('fixture') if provenance_error else None), \
             patch.object(audit, 'find_record', side_effect=ContractError('fixture') if review_error else None,
                          return_value=review):
            result = audit.inspect_task(task)
        self.assertEqual(task, before)
        self.assertFalse(result['strategy_accepted'])
        self.assertFalse(result['report_accepted'])
        self.assertFalse(result['all_research_data_sufficient_accepted'])
        return result

    def test_download_and_links_never_approve_semantic_completeness(self):
        result = self.inspect()
        self.assertTrue(result['registered_input_checks_passed'])
        self.assertEqual(result['status'], 'registered_inputs_revalidated_semantics_not_accepted')
        self.assertEqual(result['independent_review_status'], 'not_available_for_current_proposal')

    def test_compound_need_missing_date_is_not_hidden_by_all_links(self):
        review = dict(result=dict(question_assessments=[dict(question_id='q1',
            status='requires_evidence', missing_evidence=['最近报告的披露日期'], reason='日期未核验')]))
        result = self.inspect(review=review)
        self.assertTrue(result['registered_input_checks_passed'])
        self.assertEqual(result['status'], 'data_gaps_remain')
        self.assertEqual(len(result['independently_identified_data_gaps']), 1)

    def test_bad_hash_or_missing_link_or_provenance_blocks_registered_inputs(self):
        for changes in (dict(source_valid=False), dict(links=False), dict(provenance_error=True)):
            with self.subTest(changes=changes):
                result = self.inspect(**changes)
                self.assertFalse(result['registered_input_checks_passed'])
                self.assertEqual(result['status'], 'data_gaps_remain')

    def test_stale_review_never_clears_completeness(self):
        self.assertEqual(self.inspect(review_error=True)['independent_review_status'], 'invalid_or_stale_review')

    def test_optional_only_task_is_not_a_required_data_pass(self):
        self.assertFalse(self.inspect(required=False)['registered_input_checks_passed'])

    def test_optional_gap_cannot_block_independent_required_branch(self):
        review = dict(result=dict(question_assessments=[dict(question_id='optional',
            status='requires_evidence', missing_evidence=['补充'], reason='可选')]))
        result = self.inspect(review=review)
        self.assertEqual(result['independently_identified_data_gaps'], [])

    def test_complete_text_review_never_becomes_source_or_strategy_approval(self):
        review = dict(result=dict(question_assessments=[dict(question_id='q1', status='answered')]))
        result = self.inspect(review=review)
        self.assertEqual(result['independent_review_status'], 'bound_review_revalidated')
        self.assertEqual(result['status'], 'registered_inputs_revalidated_semantics_not_accepted')
