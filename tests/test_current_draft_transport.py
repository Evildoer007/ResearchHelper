"""Current authenticated draft flow over local TLS and real spawned transports.

Only endpoint replies are fixtures. No production configuration, paid model,
provider request, PDF download or native window is used. Retained public data
exercises actual request compilation, extraction, arithmetic and report saving.
"""
import base64
import copy
import json
import hashlib
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request
import unittest
from unittest.mock import patch

from tests import test_authenticated_spawn_workflow_offline as transport
from tests import test_draft_first_workflow as financial
from researchhelper.contracts import context
from researchhelper.source_transport import IsolatedEvidenceCollector


QUESTION=('研究比亚迪002594.SZ，比较2026年与2025年上半年的营业收入、净利润和经营现金流，'
          '形成策略逻辑并生成报告。')


def local_model_worker(connection,deadline):
    import os
    with patch('researchhelper.https_transport.bundled_ca_file',return_value=os.environ['SSL_CERT_FILE']):
        transport.local_model_worker(connection,deadline)


def local_source_worker(connection,deadline):
    # Preserve the real GET allowlist and request validation. Route only the
    # test worker's network opener after those checks to the local TLS server.
    import os
    import urllib.request
    original=urllib.request.urlopen

    def local_open(request,*args,**kwargs):
        source=urlparse(request.full_url)
        allowed=('/api/v1/get_access_token','/ds_service_basic/api/v1/get_limit_policy',
                 '/api/v1/basic_data_service')
        if source.hostname!='quantapi.51ifind.com' or source.path not in allowed:
            raise AssertionError('Unexpected source request in the local fixture')
        routed=Request(os.environ['RH_WORKFLOW_FIXTURE_ENDPOINT']+source.path,
            data=request.data,headers=dict(request.headers),method=request.get_method())
        for name,value in request.unredirected_hdrs.items():
            routed.add_unredirected_header(name,value)
        return original(routed,*args,**kwargs)

    with patch('researchhelper.https_transport.bundled_ca_file',return_value=os.environ['SSL_CERT_FILE']), \
            patch('urllib.request.urlopen',side_effect=local_open):
        transport.local_source_worker(connection,deadline)


class LocalFinancialCollector(IsolatedEvidenceCollector):
    worker_target=staticmethod(local_source_worker)


class CurrentDraftTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        financial.DraftFirstWorkflowTests.setUpClass()
        cls.responses=financial.DraftFirstWorkflowTests.responses
        cls.original_hash=financial.DraftFirstWorkflowTests.original_hash
        cls.acceptance_results=[]

    @classmethod
    def tearDownClass(cls):
        financial.DraftFirstWorkflowTests.tearDownClass()

    def setUp(self):
        self.control=transport.AuthenticatedSpawnWorkflowTests()
        self.control.draft_first=True
        self.control.reply=self.reply
        self.addCleanup(self.control.doCleanups)
        self.control.setUp()
        handler=self.control.vendor.RequestHandlerClass
        handler.do_GET=handler.do_POST
        agent=self.control.server.agent
        agent.model.worker_target=local_model_worker
        agent.collector=LocalFinancialCollector(offline=agent.offline,
            journal=agent._journal,active=agent._check_run_control)
        agent.collector.original_keeper=agent.archive.retain_original
        self.fail_period=None
        self.writer_input=None

    def reply(self,route,payload,headers):
        if route=='/api/v1/get_access_token':
            self.assertEqual(headers.get('refresh_token'),transport.REFRESH)
            return 200,dict(errorcode=0,data=dict(access_token=transport.ACCESS))
        if route=='/ds_service_basic/api/v1/get_limit_policy':
            self.assertEqual(headers.get('access_token'),transport.ACCESS)
            return 200,copy.deepcopy(self.responses['get_limit_policy:'])
        if route=='/api/v1/basic_data_service':
            self.assertEqual(headers.get('access_token'),transport.ACCESS)
            period=financial.period(payload)
            if period==self.fail_period:
                return 200,dict(errorcode=-1,errmsg='Controlled unavailable period')
            return 200,copy.deepcopy(self.responses['basic_data_service:'+period])
        self.assertEqual(route,'/model')
        self.assertEqual(headers.get('Authorization'),'Bearer '+transport.MODEL_KEY)
        index=self.control.model_index;self.control.model_index+=1
        if index==0:
            planning=json.loads(payload['messages'][-1]['content'])
            result=dict(data_requests=[dict(id='financial_'+period,source='ifind',operation='basic_data_service',question_ids=self.question_ids,
                parameters=dict(codes='002594.SZ',report_period=period,statement_type='1',indicators=[
                    'ths_revenue_stock','ths_operating_cost_stock','ths_np_stock','ths_ncf_from_oa_stock']),
                selection_reason='核对问题指定的两期经营数据。') for period in ('20260630','20250630')])
            if 'binding_ref' in planning:
                result.update(version=1,binding_ref=planning['binding_ref'],stop_reason='')
        elif index==1:
            self.writer_input=json.loads(payload['messages'][-1]['content'])
            observations=self.writer_input['data']['observations']
            def refs(field):
                return [row[series['columns'].index('id')] for series in observations
                    if series.get('field')==field for row in series.get('rows',[])]
            result=dict(title='比亚迪经营观察',conclusion='收入、利润与现金回收分别核对，单期变化不代表持续趋势。',
                logics=[dict(title=title,analysis=analysis,implication=implication,risk=risk,data_refs=refs(field))
                    for field,title,analysis,implication,risk in (
                        ('ths_revenue_stock','收入与成本传导','收入变化需结合成本与业务结构解释。','经营规模变化不直接代表盈利改善。','单期变化与结构差异。'),
                        ('ths_np_stock','利润与盈利弹性','净利润变化需区分经营因素与非经常项目。','盈利持续性取决于后续经营兑现。','缺少分项贡献时不能确定原因。'),
                        ('ths_ncf_from_oa_stock','现金与经营兑现','经营现金流与利润应分别判断。','回款和结算节奏可能影响现金兑现。','无法仅凭合并金额确认回款原因。'))],
                risks=['本地流程测试使用保留数据及合成分析，不验证真实模型判断质量。'])
        else:
            raise AssertionError('Unexpected additional model request')
        return 200,dict(model='fabricated-model',choices=[dict(finish_reason='stop',message=dict(content=json.dumps(result,ensure_ascii=False)))])

    def run_current(self,*,cancel_after_first=False):
        client=self.control
        options=dict(question=QUESTION,research_mode='current')
        if cancel_after_first:
            options['limits']=dict(model_calls=2,data_requests=100,seconds=600)
        task=client.request('/api/tasks',options)[0]
        self.assertEqual(task.get('draft_first_version'),1)
        self.question_ids=[row['id'] for row in task['questions']]
        execution=client.request('/api/bootstrap')[0]['execution']
        route='/api/tasks/'+task['id']
        execution={key:execution[key] for key in ('mode','policy_version')}

        def wait():
            client.server.agent.workers[task['id']].join(45)
            self.assertFalse(client.server.agent.workers[task['id']].is_alive())

        if cancel_after_first:
            from researchhelper import draft_workflow
            checkpoint=draft_workflow._checkpoint
            stopped=[]

            def persist_then_cancel(engine,current):
                checkpoint(engine,current)
                if not stopped and any(row['operation']=='basic_data_service' for row in current['source_records']):
                    stopped.append(True)
                    client.request(route+'/cancel',dict(expected_task=context(current)))

            with patch.object(draft_workflow,'_checkpoint',side_effect=persist_then_cancel):
                client.request(route+'/start',dict(expected_task=context(task),expected_execution=execution))
                wait()
            cancelled=client.request(route)[0]
            self.assertTrue(stopped)
            self.assertEqual(cancelled['status'],'已取消')
            self.assertEqual(cancelled['ledger']['used']['model_calls'],1)
            client.request(route+'/resume',dict(expected_task=context(cancelled),expected_execution=execution))
            wait()
        else:
            client.request(route+'/start',dict(expected_task=context(task),expected_execution=execution))
            wait()
        final=client.request(route)[0]
        self.assertFalse(client.fixture_errors,client.fixture_errors)
        self.assertTrue(final.get('delivery'),final.get('gaps'))
        self.assertEqual(final['draft_report']['origin'],'model',dict(gaps=final.get('gaps'),mode=final.get('run_mode'),used=final['ledger']['used'],notices=final.get('draft_notices'),calls=[row['path'] for row in client.calls]))
        self.assertEqual(final['ledger']['used']['model_calls'],2)
        self.assertEqual(client.model_index,2)
        self.assertIsNotNone(self.writer_input)
        self.assertTrue(final['draft_report']['facts'],dict(notices=final.get('draft_notices'),attempts=final.get('draft_source_attempts'),calls=[row['path'] for row in client.calls]))
        self.assertEqual(len(final['draft_report']['logics']),3)
        self.assertTrue(all(call['path'] in ('/model','/api/v1/get_access_token',
            '/ds_service_basic/api/v1/get_limit_policy','/api/v1/basic_data_service') for call in client.calls))
        paths=[call['path'] for call in client.calls]
        policy='/ds_service_basic/api/v1/get_limit_policy'
        self.assertEqual(paths[0],'/model')
        self.assertEqual(paths.count(policy),1)
        self.assertLess(paths.index(policy),paths.index('/api/v1/basic_data_service'))
        self.assertEqual(sum(call['path']=='/api/v1/get_access_token' for call in client.calls),1)
        for event in final['ledger']['dispatches']:
            if event.get('network_request_started'):
                self.assertTrue(event['network_worker_reaped'])
        before=copy.deepcopy(final)
        saved_files=[]
        from researchhelper.artifact_exporter import export_report
        for profile in ('standard','detailed'):
            generated = []

            def read_conversion(content, resources, directory, **options):
                receipt = export_report(content, resources, directory, **options)
                path = Path(receipt['path'])
                self.assertEqual(json.loads(path.with_name('receipt.json').read_text()), receipt)
                self.assertTrue(receipt['converted_from_saved_html'])
                source = Path(receipt['source_html'])
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), receipt['source_html_sha256'])
                generated.append((Path(directory), receipt, path.read_bytes()))
                return receipt

            with patch('researchhelper.artifact_exporter.export_report', side_effect=read_conversion):
                exported=client.request('/api/export',dict(task_id=final['id'],profile=profile,format='html'))[0]
            self.assertEqual(len(generated), 1)
            directory, receipt, converted = generated[0]
            self.assertFalse(directory.exists())
            saved_bytes=base64.b64decode(exported['content_base64'],validate=True)
            self.assertEqual(saved_bytes, converted)
            self.assertEqual(receipt['profile'], profile)
            self.assertEqual(hashlib.sha256(saved_bytes).hexdigest(), receipt['sha256'])
            self.assertEqual(receipt['sha256'], exported['sha256'])
            saved_files.append(dict(profile=profile,bytes=len(saved_bytes),sha256=exported['sha256']))
            raw=saved_bytes.decode()
            self.assertIn('比亚迪经营观察',raw)
            self.assertTrue(raw.startswith('<!doctype html>'))
        self.assertEqual(client.request(route)[0],before)
        for secret in (transport.MODEL_KEY,transport.ACCESS,transport.REFRESH):
            self.assertNotIn(secret,json.dumps(final,ensure_ascii=False))
        self.acceptance_results.append(dict(case=self._testMethodName,cancelled_and_resumed=cancel_after_first,
            facts=len(final['draft_report']['facts']),logics=len(final['draft_report']['logics']),
            synthetic_model_calls=final['ledger']['used']['model_calls'],
            synthetic_data_requests=final['ledger']['used']['data_requests'],
            paths=[row['path'] for row in client.calls],saved_files=saved_files))
        return final

    def test_one_sentence_uses_spawned_data_and_model_workers_and_saves_two_profiles(self):
        final=self.run_current()
        self.assertEqual(len(final['draft_report']['facts']),8)
        self.assertEqual(sum(call['path']=='/api/v1/basic_data_service' for call in self.control.calls),2)
        paper=self.control.server.agent.archive.report(final['id'],final['version'])
        from researchhelper.draft_visuals import logic_figure_plan
        self.assertGreaterEqual(len(logic_figure_plan(paper['content'])),3)

    def test_missing_financial_period_keeps_received_values_and_still_saves_a_report(self):
        self.fail_period='20250630'
        final=self.run_current()
        facts=final['draft_report']['facts']
        self.assertEqual(len(facts),4)
        self.assertTrue(all('2025' not in row['period'] for row in facts))
        self.assertEqual(sum(call['path']=='/api/v1/basic_data_service' for call in self.control.calls),2)


    def test_cancel_and_resume_uses_the_paid_plan_and_finishes_the_second_period(self):
        final=self.run_current(cancel_after_first=True)
        self.assertEqual(len(final['draft_report']['facts']),8)
        self.assertEqual(sum(call['path']=='/api/v1/basic_data_service' for call in self.control.calls),2)
        self.assertEqual(final['batch'],2)


class CurrentSnapshotContextTests(unittest.TestCase):
    """Explicit current-code references replace guessed identity periods."""
    def setUp(self):
        from tests import test_api_compact_context_offline as compact
        self.control=compact.AcquiredCompactAPITests()
        self.addCleanup(self.control.doCleanups)
        self.control.setUp()
        self.task,self.record=self.control.task,self.control.record

    def proposal(self):
        from researchhelper.evidence import direct_code_reference
        proposal=self.control.spec()
        self.assertIsNotNone(proposal['direct_code_reference'])
        self.assertEqual(proposal['direct_code_reference'],direct_code_reference(self.task,self.record))
        return proposal

    def approved(self,proposal):
        return self.control.fixture.approved(self.task,self.record,proposal)

    def test_current_cell_and_compact_context_use_the_same_unmodified_original(self):
        from researchhelper.evidence import api_cell_candidates,expand_api_context_reference
        from researchhelper.reasoning import validate_model_specs
        proposal=self.proposal()
        selected=api_cell_candidates(self.record)['cells'][0]['proposal_fields']
        proposal.update(selected)
        before=copy.deepcopy((proposal,self.task,self.record))
        validate_model_specs(self.task,[proposal],schema_only=True)
        full=expand_api_context_reference(self.task,self.record,proposal)
        compact_value=self.approved(proposal)
        self.assertEqual(compact_value,self.approved(full))
        self.assertEqual((proposal,self.task,self.record),before)
        self.assertEqual(compact_value['identity']['mode'],'direct_provider_observation')
        self.assertNotIn('valid_from',compact_value['identity'])
        self.assertEqual(compact_value['raw_value'],4.5)
        self.assertEqual(self.task['ledger']['used']['model_calls'],0)

    def test_current_binding_rejects_a_changed_artifact_or_observation(self):
        from researchhelper.contracts import ContractError
        for mutate in (
            lambda p:p['direct_code_reference'].update(artifact_id='another-artifact'),
            lambda p:p.update(raw_value=999),
            lambda p:p['api_context_reference'].update(artifact_id='another-artifact')):
            proposal=self.proposal();mutate(proposal)
            with self.subTest(mutate=mutate),self.assertRaises(ContractError):
                self.approved(proposal)

    def test_current_context_survives_delivery_and_archive_restart_without_calls(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        from researchhelper.contracts import validate_evidence
        from tests import ROOT
        proposal=self.proposal()
        agent=self.control.fixture.agent
        result=agent.start(self.task['id'],dict(evidence_specs=[proposal],answers=dict(q1=dict(
            refs=['close'],calculation_refs=[],numeric_segments=dict(answer=[
                dict(text='本次取得的收盘原值为'),
                dict(source='evidence',ref='close',path='raw_value',format='signed',digits=2),
                dict(text='元，不证明历史当时可得。')])))))
        self.assertTrue(result.get('delivery'),result.get('gaps'))
        self.assertEqual(len(result['evidence']),1,result.get('rejected_evidence'))
        item=result['evidence'][0]
        self.assertNotIn('api_context_reference',item)
        self.assertEqual(item['calculation_context_basis']['price_kind']['field'],'price_kind')
        self.assertEqual(result['ledger']['used']['model_calls'],0)
        self.assertEqual(result['ledger']['used']['data_requests'],2)
        saved=agent.export(self.task['id'])
        restarted=ResearchAgent(ResearchArchive(self.control.fixture.fixture.root/'store'),ROOT,offline=True)
        self.addCleanup(restarted.close_runtime)
        restored=restarted.archive.get(self.task['id'])
        validate_evidence(restored['evidence'][0],restored)
        self.assertEqual(restarted.export(self.task['id'])['digest'],saved['digest'])


if __name__=='__main__':
    unittest.main()
