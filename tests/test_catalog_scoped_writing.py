"""Writing scopes preserve actual retrieval and never approve completeness."""
import copy
import unittest
from unittest.mock import patch

from tests.fee_disclosure_fixture import fee_task
from tests.test_argument_support_workflow import assessed
from researchhelper.argument_support import make_record, review_request
from researchhelper.catalog_metadata import POLICY as METADATA_POLICY, QUERY_POLICY, scope_basis
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import analysis_contract

POLICY='catalog_scoped_writing_version'


class CatalogScopedWritingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.retained = fee_task(multiple_catalogue_rows=True)
        cls.retained.update({METADATA_POLICY:1, QUERY_POLICY:1})
        cls.retained.pop(POLICY, None)
        cls.retained['input_hash'] = input_fingerprint(cls.retained)
        cls.original_digest = fingerprint(cls.retained)

    @classmethod
    def tearDownClass(cls):
        assert fingerprint(cls.retained) == cls.original_digest

    def setUp(self):
        self.task=copy.deepcopy(self.retained)
        # Detached prompt projection, not reapproval of old financial evidence.
        self.task.update({POLICY:1,'evidence':[],'calculations':[]})
        self.task['input_hash']=input_fingerprint(self.task)
        network=patch('socket.socket.connect',side_effect=AssertionError('Offline projection'))
        network.start();self.addCleanup(network.stop)

    def test_writing_uses_full_actual_catalogue_shared_with_independent_review(self):
        before=canonical(self.task);contract=analysis_contract(self.task)
        self.assertFalse(contract['client_writing']['catalog_scope']['automatic_completion'])
        basis=contract['catalog_metadata_scope_basis']
        self.assertEqual(basis,scope_basis(self.task))
        qid=self.task['questions'][0]['id']
        review=review_request(self.task,dict(answers={qid:dict(answer='仅核对留存目录。',refs=[],calculation_refs=[])},theses=[]))
        self.assertEqual(review['scope']['catalog_metadata_scope_basis'],basis)
        for panel in basis['catalog_query_results']:
            parent=next(s for s in self.task['source_records'] if s['id']==panel['catalog_artifact_id'])
            self.assertEqual(len(panel['rows']),len(parent['catalog_entries']))
            self.assertEqual(panel['query_window'],parent['parameters'])
            self.assertEqual(panel['query_completeness'],'未证实')
        self.assertFalse(basis['latest_report_completeness_approved'])
        self.assertEqual(canonical(self.task),before)

    def test_no_question_method_or_data_obligation_is_replaced(self):
        old=copy.deepcopy(self.task);old.pop(POLICY);old['input_hash']=input_fingerprint(old)
        current=analysis_contract(self.task);legacy=analysis_contract(old)
        self.assertNotEqual(self.task['input_hash'],old['input_hash'])
        self.assertEqual(self.task['question'],self.retained['question'])
        for key in ('questions','data_need_specs','calculation_specs','ledger'):
            self.assertEqual(self.task[key],self.retained[key])
        self.assertEqual(current['by_question'],legacy['by_question'])
        self.assertEqual(current['catalog_metadata_scope_basis']['catalog_query_results'],
                         legacy['catalog_metadata_scope_basis']['catalog_query_results'])

    def test_legacy_boolean_or_unknown_policy_gets_no_new_writing_rule(self):
        for version in (None,True,False,0,2,1.0):
            task=copy.deepcopy(self.task);task.pop(POLICY)
            if version is not None:task[POLICY]=version
            task['input_hash']=input_fingerprint(task)
            self.assertNotIn('catalog_scope',analysis_contract(task)['client_writing'])

    def test_missing_or_historical_catalogue_cannot_gain_current_scope(self):
        for change in (dict(source_records=[]),dict(research_mode='historical')):
            task=copy.deepcopy(self.task);task.update(change);task['input_hash']=input_fingerprint(task)
            self.assertNotIn('catalog_scope',analysis_contract(task)['client_writing'])
        task=copy.deepcopy(self.task);task.pop('catalog_query_result_basis_version')
        task['input_hash']=input_fingerprint(task)
        self.assertNotIn('catalog_scope',analysis_contract(task)['client_writing'])

    def test_stale_binding_rejected_and_incomplete_review_not_upgraded(self):
        qid = self.task['questions'][0]['id']
        proposal = dict(answers={qid:dict(answer='已取得两家基金全部公告。', refs=[], calculation_refs=[])},
                        theses=[])
        request = review_request(self.task, proposal)
        targets = [target['id'] for target in request['targets']]
        _, result = assessed(self.task, proposal, rejected=targets, completion={qid:'requires_evidence'})
        record = make_record(self.task, proposal, result, actor='host')
        self.task['argument_support_reviews'] = [record]
        original = fingerprint(record)
        task=copy.deepcopy(self.task);task[POLICY]=2
        with self.assertRaises(ContractError):analysis_contract(task)
        self.assertIn('catalog_scope', analysis_contract(self.task)['client_writing'])
        self.assertFalse(result['supported']);self.assertFalse(result['answer_complete'])
        self.assertEqual(result['question_assessments'][0]['status'],'requires_evidence')
        self.assertEqual(fingerprint(self.task['argument_support_reviews'][0]), original)
