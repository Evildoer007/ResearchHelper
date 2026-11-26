"""Response-local names cannot hide earlier missing fields or change originals."""
from copy import deepcopy
import json,socket,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests import ROOT
from core.session import Session
from researchhelper.contracts import canonical
from researchhelper.draft_workflow import _acquire
from researchhelper.draft_sources import acquisition_coverage
from researchhelper.evidence import snapshot_record,record_request_purpose


class DraftRequestIdentityTests(unittest.TestCase):
    def setUp(self):
        blocked=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        blocked.start();self.addCleanup(blocked.stop)
        folder=tempfile.TemporaryDirectory(prefix='rh-request-identities-');self.addCleanup(folder.cleanup)
        self.session=Session(folder.name);self.addCleanup(self.session.agent.close_runtime)
        self.task=self.session.create('比较600519.SH的开盘价与收盘价',objects=['600519.SH'],research_mode='current')
        self.sent=[]
        def acquire(task,requests,*,first_report):
            self.assertTrue(first_report)
            for request in requests:
                self.session.agent._validated_data_needs(task,[request])
                response=dict(errorcode=0,tables=[dict(thscode='600519.SH',time=['2026-10-29'],table=dict(close=[110]))])
                record=snapshot_record(task,'ifind','cmd_history_quotation',request['parameters'],response)
                record.update(data_need_ids=[request['id']],question_ids=request['question_ids'])
                record_request_purpose(record,request,request['question_ids'])
                task['source_records'].append(record);self.sent.append(deepcopy(request))
        self.engine=SimpleNamespace(_check_run_control=lambda _:None,_acquire_sources=acquire)

    def request(self,fields,ident='prices'):
        return dict(id=ident,source='ifind',operation='cmd_history_quotation',
            parameters=dict(codes='600519.SH',indicators=fields,startdate='2026-10-29',enddate='2026-10-29',
                functionpara=dict(Currency='RMB',CPS='1',Fill='Omit')),
            question_ids=[self.task['questions'][0]['id']],selection_reason='比较指定日期的开盘与收盘。')

    def acquire(self,requests):
        with patch('researchhelper.draft_workflow._checkpoint'):_acquire(self.engine,self.task,requests)

    def test_reused_name_does_not_erase_a_previous_missing_field(self):
        first=self.request('close,open');second=self.request('close');original=canonical([first,second])
        self.acquire([first]);self.acquire([second])
        coverage=acquisition_coverage(self.task)
        self.assertEqual(coverage['evaluated_requests'],2)
        self.assertEqual(coverage['unresolved'][0]['missing'],[dict(code='600519.SH',field='open')])
        self.assertNotEqual(self.sent[0]['id'],self.sent[1]['id'])
        self.assertNotIn(self.sent[0]['id'],coverage['complete_request_ids'])
        self.assertIn(self.sent[1]['id'],coverage['complete_request_ids'])
        self.assertEqual(canonical([first,second]),original)
        self.assertFalse(coverage['blocks_report'])

    def test_two_local_names_in_one_batch_stay_distinct(self):
        self.acquire([self.request('close,open'),self.request('close')])
        self.assertEqual(len({row['id'] for row in self.sent}),2)
        self.assertEqual(acquisition_coverage(self.task)['evaluated_requests'],2)

    def test_same_proposal_reuses_its_compiled_identity(self):
        self.acquire([self.request('close,open'),self.request('close'),self.request('close')])
        self.assertEqual(self.sent[1]['id'],self.sent[2]['id'])
        self.assertNotEqual(self.sent[0]['id'],self.sent[1]['id'])
        self.assertEqual(acquisition_coverage(self.task)['evaluated_requests'],2)

    def test_existing_explicit_request_keeps_its_name_and_parameters(self):
        value=self.request('close');self.task['data_need_specs']=[deepcopy(value)]
        self.acquire([value]);self.assertEqual(self.sent,[value])
        self.assertEqual(self.task['data_need_specs'],[value])


    def test_foreign_attempt_cannot_supply_a_local_alias(self):
        value=self.request('close')
        self.task['draft_source_attempts']=[dict(proposed=deepcopy(value),encoded=dict(value,id='foreign_alias'),
            binding=dict(task_id='foreign',research_version=self.task['version'],input_hash=self.task['input_hash']))]
        self.acquire([value]);self.assertEqual(self.sent[0],value)

    def test_resume_retains_alias_and_does_not_reset_the_ledger(self):
        self.acquire([self.request('close,open'),self.request('close')])
        used=deepcopy(self.task['ledger']);self.task['batch']+=1
        self.acquire([self.request('close')])
        self.assertEqual(self.sent[1]['id'],self.sent[2]['id'])
        self.assertEqual(self.task['ledger'],used)


class SharedDraftIdentityWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_draft_first_workflow as financial
        cls.financial=financial;financial.DraftFirstWorkflowTests.setUpClass()
    @classmethod
    def tearDownClass(cls):cls.financial.DraftFirstWorkflowTests.tearDownClass()
    def setUp(self):
        self.control=self.financial.DraftFirstWorkflowTests()
        self.addCleanup(self.control.doCleanups);self.control.setUp()

    def test_app_and_incremental_skill_keep_both_financial_periods_and_deliver(self):
        model=self.control.model;base=model.reason
        requests=[]
        def local_names(task,messages,**options):
            result=base(task,messages,**options)
            if options['phase']=='draft_plan':
                payload=json.loads(result)
                for request in payload['data_requests']:request['id']='financial'
                requests.extend(deepcopy(payload['data_requests']))
                return json.dumps(payload,ensure_ascii=False)
            return result
        model.reason=local_names
        app=self.control.agent.create(self.financial.QUESTION,research_mode='current')
        app=self.control.agent.start(app['id'])
        self.assertTrue(app.get('delivery'),app.get('error'))
        self.assertEqual(model.calls,['draft_plan','draft_report'])
        self.assertEqual(app['ledger']['used']['data_requests'],3)
        self.assertEqual(app['draft_acquisition_coverage']['unresolved'],[])
        self.assertEqual(app['draft_acquisition_coverage']['evaluated_requests'],2)
        self.assertEqual(sum(row['operation']=='get_limit_policy' for row in app['source_records']),1)
        raw=canonical(requests)
        with tempfile.TemporaryDirectory(prefix='rh-skill-identities-') as folder:
            host=Session(folder,allow_network=True);self.addCleanup(host.agent.close_runtime)
            host.agent.collector.external=self.control.transport
            task=host.create(self.financial.QUESTION,research_mode='current')
            question=task['questions'][0]['id']
            for request in requests:
                item=deepcopy(request);item['question_ids']=[question]
                prepared=host.draft(task['id'],data_requests=[item],defer_delivery=True)
                self.assertEqual(prepared['ledger']['used']['model_calls'],0)
            # Reopening the same host selection uses its retained alias and cache.
            repeated=host.draft(task['id'],data_requests=[item],defer_delivery=True)
            self.assertEqual(repeated['ledger']['used']['data_requests'],3)
            self.assertEqual(repeated['draft_source_attempts'][-1]['encoded']['id'],
                             repeated['draft_source_attempts'][-2]['encoded']['id'])
            delivered=host.draft(task['id'],report=self.financial.REPORT)
            self.assertTrue(delivered.get('delivery'),delivered.get('error'))
            self.assertEqual(delivered['ledger']['used']['data_requests'],3)
            self.assertEqual(sum(row['operation']=='get_limit_policy' for row in delivered['source_records']),1)
            coverage=host.tools(task['id'])['first_report']['acquisition_coverage']
            self.assertEqual(coverage['unresolved'],[])
            self.assertEqual(coverage['evaluated_requests'],2)
            self.assertEqual(coverage,acquisition_coverage(delivered))
            values=lambda task:sorted((row.get('code'),row.get('field'),row.get('period'),str(row.get('value')))
                for row in task['draft_report']['facts'])
            self.assertEqual(values(delivered),values(app))
            for field in ('title','conclusion','logics','risks'):
                self.assertEqual(delivered['draft_report'][field],app['draft_report'][field])
            original=host.agent.archive.report(task['id'],delivered['version'])
            restored=host.status(task['id'])
            self.assertEqual(restored['delivery'],delivered['delivery'])
            self.assertEqual(host.agent.archive.report(task['id'],delivered['version']),original)
        self.assertEqual(canonical(requests),raw)


if __name__=='__main__':unittest.main()
