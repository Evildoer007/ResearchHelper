"""A missing industry classification must not mask financial-source work."""
import copy
import hashlib
import json
import unittest

from tests import ROOT
from researchhelper.acquisition import initial_acquisition_coverage, navigation_question_projection
from researchhelper.evidence import data_request_contract
from researchhelper.financial_sources import with_financial_batch_coverage
from researchhelper.contracts import ContractError

FIXTURE = ROOT / 'result/acceptance/autonomous-data-phase-pcvl0iok/task.json'


class IndependentBatchNavigationTests(unittest.TestCase):
    def setUp(self):
        raw = FIXTURE.read_bytes(); self.original = hashlib.sha256(raw).hexdigest()
        self.legacy = json.loads(raw)
        self.task = dict(copy.deepcopy(self.legacy), financial_batch_followup_version=1)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), self.original)

    def test_original_failure_offered_only_classification_but_new_scope_keeps_both(self):
        coverage = initial_acquisition_coverage(self.legacy)
        contract, _ = navigation_question_projection(self.legacy, data_request_contract(self.legacy), coverage)
        self.assertEqual(contract['question_ids'], ['q2'])
        coverage = initial_acquisition_coverage(self.task)
        contract, _ = navigation_question_projection(self.task, data_request_contract(self.task), coverage)
        self.assertEqual(set(contract['question_ids']), {'q1', 'q2'})
        self.assertEqual(coverage['pending_financial_batch_question_ids'], ['q1'])
        self.assertEqual(self.task['evidence'], [])
        self.assertFalse(coverage['ready'])

    def test_actual_readable_source_clears_only_its_own_financial_followup(self):
        self.task['materials'].append(dict(id='synthetic_readable', kind='material', question_ids=['q1'],
            task_id=self.task['id'], research_version=self.task['version']))
        result = with_financial_batch_coverage(self.task, [dict(id='synthetic_readable')], dict(ready=False))
        self.assertEqual(result['pending_financial_batch_question_ids'], [])
        self.assertFalse(result['ready'])  # An unrelated classification gap is not completed.

    def test_imported_or_modified_raw_sources_cannot_open_financial_followup(self):
        for record in self.task['source_records']:
            record.pop('acquisition', None)
        result = with_financial_batch_coverage(self.task, [], dict(ready=False))
        self.assertEqual(result['pending_financial_batch_question_ids'], [])

    def test_explicit_query_metadata_needs_do_not_require_financial_originals(self):
        question = next(q for q in self.task['questions'] if q['id'] == 'q1')
        question['evidence_need_scopes'] = {need:'query_provenance' for need in question['evidence_needs']}
        result = with_financial_batch_coverage(self.task, [], dict(ready=False))
        self.assertEqual(result['pending_financial_batch_question_ids'], [])

    def test_unknown_or_boolean_policy_is_not_a_navigation_permission(self):
        for version in (True, '1', 2):
            with self.subTest(version=version), self.assertRaises(ContractError):
                with_financial_batch_coverage(dict(self.task, financial_batch_followup_version=version), [], dict(ready=False))
