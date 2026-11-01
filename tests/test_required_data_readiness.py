"""Readiness cannot be inferred from file counts or a text-review verdict."""
import copy
import unittest

from tests.audit_required_data_readiness import requirement_inventory
from researchhelper.contracts import evidence_requirements


class RequiredDataReadinessTests(unittest.TestCase):
    def task(self):
        question = dict(id='q1', question='核对半年报金额', kind='fact', required=True,
            evidence_needs=['营业收入', '报表净利润', '报告原件及完整所属期'], method_needs=[],
            dependencies=[])
        task = dict(id='study', version=1, questions=[question], evidence=[], source_records=[],
            calculation_specs=[], host_result={}, materials=[])
        for index, need in enumerate(evidence_requirements(question)):
            task['evidence'].append(dict(id='e'+str(index), task_id='study', research_version=1,
                source_check='通过', applicability_check='通过', question_ids=['q1'],
                applicability_review=dict(evidence_need_ids=dict(q1=[need['id']]))))
        return task

    def test_complete_explicit_links_do_not_approve_calculation_or_strategy(self):
        task=self.task(); original=copy.deepcopy(task)
        result=requirement_inventory(task)
        self.assertTrue(result['required_data_obligations_settled'])
        self.assertFalse(result['strategy_accepted'])
        self.assertFalse(result['calculation_execution_accepted'])
        self.assertFalse(result['semantic_sufficiency_accepted'])
        self.assertEqual(task, original)

    def test_download_and_supported_review_do_not_fill_an_unlinked_need(self):
        task=self.task()
        task['evidence'].pop()
        task['source_records']=[dict(id='pdf', status='成功待核验')]
        task['argument_support_reviews']=[dict(result=dict(supported=True,answer_complete=True))]
        result=requirement_inventory(task)
        self.assertFalse(result['required_data_obligations_settled'])
        self.assertEqual(result['questions'][0]['unresolved_requirements'][0]['text'],'报告原件及完整所属期')
        self.assertFalse(result['unresolved_link_is_proof_of_missing_download'])

    def test_cross_task_version_failed_or_nonfinite_evidence_cannot_cover(self):
        for changes in (dict(task_id='another'),dict(research_version=2),dict(research_version=True),
                        dict(source_check='待核验'),dict(applicability_check='不通过'),
                        dict(value=float('nan')),dict(value=float('inf')),dict(value=True)):
            task=self.task(); task['evidence'][0].update(changes)
            with self.subTest(changes=changes):
                result=requirement_inventory(task)
                self.assertFalse(result['required_data_obligations_settled'])
                self.assertEqual(result['excluded_evidence_ids'],['e0'])

    def test_other_question_link_cannot_cover_this_question(self):
        task=self.task(); task['evidence'][0]['question_ids']=['q2']
        self.assertFalse(requirement_inventory(task)['required_data_obligations_settled'])

    def test_metadata_scope_cannot_be_covered_by_financial_excerpt(self):
        task=self.task()
        task['questions'][0]['evidence_need_scopes']={'营业收入':'query_provenance'}
        self.assertFalse(requirement_inventory(task)['required_data_obligations_settled'])

    def test_unknown_method_and_explicit_request_cannot_be_silently_skipped(self):
        for scope in ('question','request'):
            task=self.task()
            if scope=='question':task['questions'][0]['method_needs']=['unregistered']
            else:task['calculation_specs']=[dict(method='unregistered',question_ids=['q1'])]
            with self.subTest(scope=scope):
                result=requirement_inventory(task)
                self.assertFalse(result['required_data_obligations_settled'])
                self.assertEqual(result['questions'][0]['unregistered_methods'],['unregistered'])

    def test_units_without_compatible_metric_roles_stay_pending(self):
        task=self.task();task['questions'][0]['method_needs']=['net_profit_margin_v1']
        for item in task['evidence']:item.update(value=100,unit='元')
        result=requirement_inventory(task)
        self.assertFalse(result['required_data_obligations_settled'])
        self.assertTrue(result['questions'][0]['pending_method_inputs'])

    def test_optional_missing_data_does_not_block_independent_required_branch(self):
        task=self.task()
        task['questions'].append(dict(id='q2',kind='fact',required=False,
            evidence_needs=['可选行业汇总'],method_needs=[]))
        result=requirement_inventory(task)
        self.assertTrue(result['required_data_obligations_settled'])
        self.assertFalse(result['questions'][1]['registered_data_obligations_settled'])

    def test_empty_task_is_not_a_data_success(self):
        task=self.task();task['questions']=[]
        self.assertFalse(requirement_inventory(task)['required_data_obligations_settled'])
