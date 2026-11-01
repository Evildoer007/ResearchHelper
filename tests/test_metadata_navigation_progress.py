"""Replay the actual R163 failure boundary; originals and paid guards stay closed."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from researchhelper import acquisition
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import extractable_source_views

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT/'result/acceptance/one-sentence-research-h1pttlvb/task.json'


class MetadataNavigationProgressTests(unittest.TestCase):
    def setUp(self):
        self.original = ORIGINAL.read_bytes()
        self.task = json.loads(self.original)
        self.task['metadata_navigation_progress_version'] = 1
        self.records = copy.deepcopy([r for r in self.task['source_records'] if r.get('kind') != 'filing'])
        self.policy = next(r for r in self.records if r['operation'] == 'get_limit_policy')
        self.basic = next(r for r in self.records if r['operation'] == 'basic_data_service')
        self.task['source_records'] = [r for r in self.records if r['operation'] == 'report_query']
        guard = patch('socket.socket.connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(ORIGINAL.read_bytes(),self.original)

    def test_actual_policy_is_progress_before_numeric_evidence_exists(self):
        before = acquisition.navigation_progress(self.task)
        self.task['source_records'].append(self.policy)
        after = acquisition.navigation_progress(self.task)
        self.assertEqual(len(after[-1]-before[-1]),1)
        self.assertEqual(extractable_source_views(self.task),[])
        self.assertFalse(acquisition.initial_acquisition_coverage(self.task)['financial_approval'])

    def test_identical_received_policy_does_not_keep_navigation_alive(self):
        self.task['source_records'].append(self.policy)
        before = acquisition.navigation_progress(self.task)
        self.assertEqual(acquisition.navigation_progress(self.task),before)
        self.assertFalse(any(new-old for old,new in zip(before,acquisition.navigation_progress(self.task))))

    def test_changed_bytes_or_foreign_policy_never_count_as_progress(self):
        for mutation in ('bytes','task','version','ledger'):
            record = copy.deepcopy(self.policy)
            if mutation == 'bytes': record['snapshot']['qps_limit']['account_total'] = 999
            elif mutation == 'task': record['task_id'] = 'foreign'
            elif mutation == 'version': record['research_version'] += 1
            else: record['acquisition']['dispatch_index'] = 99999
            self.task['source_records'] = [record]
            self.assertEqual(acquisition.navigation_progress(self.task)[-1],set(),mutation)

    def test_policy_without_ledger_binding_is_not_progress(self):
        record = copy.deepcopy(self.policy);record.pop('acquisition')
        self.task['source_records'] = [record]
        self.assertEqual(acquisition.navigation_progress(self.task)[-1],set())

    def test_legacy_tasks_keep_their_original_progress_dimensions(self):
        self.task['source_records'].append(self.policy)
        self.task.pop('metadata_navigation_progress_version')
        before = acquisition.navigation_progress(self.task)
        self.task['metadata_navigation_progress_version'] = 1
        self.assertEqual(acquisition.navigation_progress(self.task)[:-1],before)

    def test_unknown_or_boolean_version_is_rejected(self):
        for value in (True,2,'1'):
            self.task['metadata_navigation_progress_version'] = value
            with self.assertRaises(ContractError): acquisition.navigation_progress(self.task)

    def test_policy_then_fixed_api_reaches_extraction_without_pdf_or_supplement(self):
        task = self.task
        task['reasoning_state'] = {}
        task['supplement_history'] = []
        # Disposable scheduling simulation, not a claim to replay paid calls.
        task['ledger']['used']['model_calls'] = 1
        task['input_hash'] = input_fingerprint(task)
        operations = []
        engine = SimpleNamespace(offline=False,model=object(),
            source_credentials={'ifind':{'refresh_token':'SYNTHETIC_NO_NETWORK'}},
            _active_supplement=Mock(return_value=None),_reasoning_available=Mock(return_value=True),
            _save_model_checkpoint=Mock(),_active=Mock(),_validated_data_needs=Mock(),
            _gap=lambda task,text,questions: task['gaps'].append(text))
        def propose(model,current,phase,payload,fields):
            operation = ('get_limit_policy','basic_data_service')[len(operations)]
            self.assertIn(operation,{tool['operation'] for tool in payload['data_request_contract']['tools']})
            source = self.policy if operation == 'get_limit_policy' else self.basic
            current['ledger']['used']['model_calls'] += 1
            return dict(data_requests=[dict(id='request_'+operation,source='ifind',operation=operation,
                parameters=copy.deepcopy(source['parameters']),question_ids=['q1'],
                selection_reason='仅复用实际留存的请求形状作离线调度模拟')],stop_reason=''),dict(acquisition_rules_version=3)
        def acquire(current,needs,**kwargs):
            operation = needs[0]['operation'];operations.append(operation)
            current['source_records'].append(copy.deepcopy(self.policy if operation == 'get_limit_policy' else self.basic))
        engine._acquire_sources = Mock(side_effect=acquire)
        with patch.object(acquisition,'model_object',side_effect=propose) as model:
            acquisition.complete_initial_acquisition(engine,task)
        self.assertEqual(operations,['get_limit_policy','basic_data_service'])
        self.assertEqual(model.call_count,2)
        self.assertEqual(task['reasoning_state']['acquisition']['status'],'complete')
        self.assertTrue(acquisition.initial_acquisition_coverage(task)['ready'])
        self.assertEqual(task['supplement_history'],[])
        self.assertEqual(task['reasoning_state']['acquisition']['decisions'][0]['progress'][-1],1)

    def test_policy_flag_changes_input_identity(self):
        before = input_fingerprint(self.task)
        self.task.pop('metadata_navigation_progress_version')
        self.assertNotEqual(input_fingerprint(self.task),before)
