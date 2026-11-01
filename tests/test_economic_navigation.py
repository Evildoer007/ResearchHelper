"""Replay actual mixed-source premature completion without external calls."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from researchhelper.acquisition import (initial_acquisition_coverage,
    original_navigation_tools, navigation_question_projection)
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.economic_sources import with_economic_navigation_coverage
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import data_request_contract
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose, check_content

ROOT = Path(__file__).resolve().parents[1]
MIXED = ROOT/'result/acceptance/one-sentence-research-vhbnrx7_/task.json'
SERIES = ROOT/'result/acceptance/economic-discovery-live-20261023-v1/task.json'
FLAG = 'economic_navigation_version'


class EconomicNavigationTests(unittest.TestCase):
    def setUp(self):
        self.originals = {p:p.read_bytes() for p in (MIXED,SERIES)}
        self.task = json.loads(self.originals[MIXED]);self.task[FLAG]=1
        self.series = json.loads(self.originals[SERIES]);self.series[FLAG]=1
        guard=patch('socket.socket.connect',side_effect=AssertionError('Offline only'))
        guard.start();self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertTrue(all(p.read_bytes()==raw for p,raw in self.originals.items()))

    def test_actual_two_readable_originals_do_not_discharge_searched_series(self):
        old=copy.deepcopy(self.task);old.pop(FLAG)
        self.assertTrue(initial_acquisition_coverage(old)['ready'])
        actual=initial_acquisition_coverage(self.task)
        self.assertFalse(actual['ready'])
        self.assertEqual(actual['pending_economic_question_ids'],['q2'])
        self.assertFalse(actual['economic_numeric_approval'])
        self.assertEqual(self.task['ledger']['used']['model_calls'],8)

    def test_real_business_response_closes_route_without_approving_numbers(self):
        actual=with_economic_navigation_coverage(self.series,dict(ready=True))
        self.assertTrue(actual['ready'])
        self.assertEqual(actual['economic_data_route_attempted_question_ids'],['q1'])
        self.assertEqual(actual['pending_economic_question_ids'],[])
        self.assertFalse(actual['economic_numeric_approval'])

    def test_query_directory_only_question_adds_no_value_obligation(self):
        for q in self.task['questions']:
            q['evidence_need_scopes']={need:'query_provenance' for need in q['evidence_needs']}
        result=with_economic_navigation_coverage(self.task,dict(ready=True))
        self.assertTrue(result['ready'])
        self.assertEqual(result['pending_economic_question_ids'],[])

    def test_corrupted_business_snapshot_cannot_close_route(self):
        source=next(r for r in self.series['source_records'] if r['operation']=='edb_service')
        source['snapshot']['tables'][0]['value'][0]='999'
        actual=with_economic_navigation_coverage(self.series,dict(ready=True))
        self.assertFalse(actual['ready'])
        self.assertEqual(actual['pending_economic_question_ids'],['q1'])

    def test_wrong_question_business_response_cannot_close_route(self):
        source=next(r for r in self.series['source_records'] if r['operation']=='edb_service')
        source['question_ids']=[]
        self.assertEqual(with_economic_navigation_coverage(self.series,dict(ready=True))['pending_economic_question_ids'],['q1'])

    def test_original_navigation_filter_keeps_only_already_callable_edb(self):
        tools=data_request_contract(self.task)['tools']
        selected=original_navigation_tools(self.task,tools)
        self.assertTrue(any(t['operation']=='edb_service' for t in selected))
        no_edb=[t for t in tools if t['operation']!='edb_service']
        self.assertFalse(any(t['operation']=='edb_service' for t in original_navigation_tools(self.task,no_edb)))

    def test_navigation_projection_keeps_pending_numeric_question(self):
        coverage=initial_acquisition_coverage(self.task)
        contract,questions=navigation_question_projection(self.task,data_request_contract(self.task),coverage)
        self.assertEqual(contract['question_ids'],['q2'])
        self.assertEqual([q['id'] for q in questions],['q2'])

    def test_legacy_historical_document_and_invalid_versions(self):
        base=dict(ready=True)
        old=copy.deepcopy(self.task);old.pop(FLAG)
        self.assertIs(with_economic_navigation_coverage(old,base),base)
        for change in (dict(research_mode='historical'),dict(intent='原件内容核对')):
            self.assertIs(with_economic_navigation_coverage(dict(self.task,**change),base),base)
        for value in (True,'1',2,1.0):
            with self.assertRaises(ContractError):
                with_economic_navigation_coverage(dict(self.task,**{FLAG:value}),base)

    def test_readiness_never_bypasses_another_pending_source(self):
        result=with_economic_navigation_coverage(self.series,dict(ready=False,pending_original_question_ids=['other']))
        self.assertFalse(result['ready'])
        self.assertEqual(result['pending_original_question_ids'],['other'])

    def test_empty_search_does_not_invent_a_business_request(self):
        from researchhelper.financial_sources import _metadata_records
        sources=copy.deepcopy(_metadata_records(self.task,'search_edb'))
        for source in sources:source['parsed']=[]
        with patch('researchhelper.financial_sources._metadata_records',return_value=sources):
            result=with_economic_navigation_coverage(self.task,dict(ready=True))
        self.assertTrue(result['ready'])
        self.assertEqual(result['economic_directory_question_ids'],[])

    def test_later_search_cannot_retroactively_authorize_a_business_response(self):
        from researchhelper.financial_sources import _metadata_records
        sources=copy.deepcopy(_metadata_records(self.series,'search_edb'))
        for source in sources:source['record']['acquisition']['dispatch_index']=10000
        with patch('researchhelper.financial_sources._metadata_records',return_value=sources):
            result=with_economic_navigation_coverage(self.series,dict(ready=True))
        self.assertFalse(result['ready'])
        self.assertEqual(result['pending_economic_question_ids'],['q1'])

    def test_scheduler_continues_from_directory_to_business_despite_other_readable_material(self):
        from researchhelper import acquisition
        task=copy.deepcopy(self.series)
        series=next(r for r in task['source_records'] if r['operation']=='edb_service')
        task['source_records']=[r for r in task['source_records'] if r is not series]
        task['reasoning_state']={};task['supplement_history']=[]
        task['run_mode']='model'
        task['economic_observation_version']=1
        task['ledger']['limits'].update(data_requests=12,model_calls=8,seconds=600)
        task['ledger']['used']['model_calls']=1
        task['input_hash']=input_fingerprint(task)
        actual_views=acquisition.extractable_source_views
        engine=SimpleNamespace(offline=False,model=object(),
            source_credentials={'ifind':{'refresh_token':'SYNTHETIC_NO_NETWORK'}},
            _active_supplement=Mock(return_value=None),_reasoning_available=Mock(return_value=True),
            _save_model_checkpoint=Mock(),_active=Mock(),_validated_data_needs=Mock(),
            _gap=lambda current,text,questions:current['gaps'].append(text))
        def views(current):
            return [dict(id='synthetic-other-readable-material',kind='material')]+actual_views(current)
        def propose(model,current,phase,payload,fields):
            self.assertEqual(phase,'acquire')
            self.assertEqual(payload['initial_source_readiness']['pending_economic_question_ids'],['q1'])
            self.assertIn('edb_service',{t['operation'] for t in payload['data_request_contract']['tools']})
            current['ledger']['used']['model_calls']+=1
            return dict(data_requests=[dict(id='actual_retained_request',source='ifind',operation='edb_service',
                parameters=copy.deepcopy(series['parameters']),question_ids=['q1'],
                selection_reason='仅复用留存请求形状作离线调度模拟')],stop_reason=''),dict(acquisition_rules_version=3)
        def acquire(current,needs,**kwargs):
            self.assertEqual([r['operation'] for r in needs],['edb_service'])
            current['source_records'].append(copy.deepcopy(series))
        engine._acquire_sources=Mock(side_effect=acquire)
        with patch.object(acquisition,'extractable_source_views',side_effect=views), \
                patch.object(acquisition,'model_object',side_effect=propose) as model:
            acquisition.complete_initial_acquisition(engine,task)
        self.assertEqual(model.call_count,1)
        self.assertEqual(engine._acquire_sources.call_count,1)
        self.assertEqual(task['reasoning_state']['acquisition']['status'],'complete',task['reasoning_state']['acquisition'].get('stop_reason'))
        self.assertEqual(task['supplement_history'],[])

    def test_new_flag_is_bound_and_report_frozen(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(Path(folder)),ROOT,offline=True,require_argument_support=True)
            task=agent.create('查询月度CPI',entry='宏观',research_mode='current')
            self.assertEqual(task[FLAG],1)
            old=copy.deepcopy(task);old.pop(FLAG)
            self.assertNotEqual(input_fingerprint(task),input_fingerprint(old))
            task['knowledge']=ResearchKnowledge().read(task['entry'],task['question'],context=task)
            task['status']='部分完成';task['gaps']=['仅离线契约检查。']
            content=compose(task);check_content(content)
            self.assertEqual(content[FLAG],1)


if __name__=='__main__':
    unittest.main()
