"""Fee extraction selects actual original spans; it never approves a fee.

The retained failed ETF study supplies originals only. Every test uses copies,
and no real model, provider, Store mutation or previous-task restart is allowed.
"""
import copy
import hashlib
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
from researchhelper import output_schema as formats
from researchhelper.contracts import ContractError
from researchhelper.financial_sources import original_line_index

RETAINED=ROOT/'result/acceptance/one-sentence-research-3s5f7b02/task.json'


class FeeOriginalSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before=hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.original=json.loads(RETAINED.read_text())

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest()==cls.before

    def setUp(self):
        self.task=copy.deepcopy(self.original)
        self.task['fee_original_selection_version']=1
        self.views=self.task['reasoning_state']['reading']['bundle']['views']
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        guard.start();self.addCleanup(guard.stop)

    def schema(self):
        return formats.fee_original_output_schema(self.task,self.views)

    def prompt(self):
        return formats.fee_original_prompt(dict(untrusted_sources=self.views,
            evidence_contract={}),self.task)

    def selection(self):
        prompt=self.prompt();source=prompt['source_choices'][0]
        lines=original_line_index(self.views[0])
        start=next(item['index'] for item in lines if item['part']=='第30页' and '支付基金管理人' in item['text'])
        end=next(item['index'] for item in lines if item['index']>start and '日管理人报酬' in item['text'])
        span=dict(start=start,end=end)
        rate=dict(id='fee_rate_a',object='510300.SH',raw_token='0.15',raw_unit='%',
            span=span,observation_anchor=0,question_ids=['q1'],
            calculation_context=dict(fund_id='510300',share_class='510300',period='2026年',
                fee_basis='net_assets',rate_period='annual',metric='management_fee'),
            context_spans={key:copy.deepcopy(span) for key in
                ('fund_id','share_class','period','fee_basis','rate_period','metric')},
            need_links=[dict(question_id='q1',need_ids=[source['question_requirements']['q1'][0]['id']])])
        # Context locations above are deliberately unverified; decoding must
        # preserve them for the normal source/applicability gates to reject.
        return dict(version=1,binding_ref=prompt['fee_binding_ref'],
            sources=[dict(source_ref=source['source_ref'],rates=[rate])])

    def decode(self,wire=None):
        return formats.decode_fee_original_output(self.selection() if wire is None else wire,
            self.schema(),(self.task,self.views))

    def test_protocol_exists_for_two_actual_fund_originals(self):
        self.assertIsNotNone(self.schema())
        self.assertEqual(len(self.prompt()['source_choices']),2)

    def test_quote_is_selected_with_original_line_break_not_retyped(self):
        item=self.decode()['evidence_specs'][0]
        self.assertIn('逐\n日累计',item['text'])
        self.assertEqual(item['record_locator']['part'],'第30页')
        self.assertNotIn('source_check',item)
        self.assertEqual(item['raw_token'],'0.15')
        self.assertEqual(item['raw_unit'],'%')
        self.assertEqual(item['conversion_factor'],1)

    def test_all_six_context_fields_and_original_bases_are_retained_not_approved(self):
        item=self.decode()['evidence_specs'][0]
        self.assertEqual(set(item['calculation_context']),set(item['calculation_context_basis']))
        from researchhelper.evidence import validate_calculation_context_basis
        item.update(task_id=self.task['id'],research_version=self.task['version'])
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(item,self.task['source_records'])

    def test_no_legacy_document_or_other_method_is_silently_opted_in(self):
        for change in ('legacy','document','mixed_method'):
            with self.subTest(change=change):
                task=copy.deepcopy(self.task)
                if change=='legacy':task.pop('fee_original_selection_version')
                elif change=='document':task['intent']='原件内容核对'
                else:task['questions'][0]['method_needs'].append('net_profit_margin_v1')
                self.assertIsNone(formats.fee_original_output_schema(task,self.views))

    def test_missing_context_or_raw_quote_cannot_bypass_selection_format(self):
        for field in ('calculation_context','context_spans','raw_unit'):
            with self.subTest(field=field):
                wire=self.selection();del wire['sources'][0]['rates'][0][field]
                with self.assertRaises(ContractError):self.decode(wire)
        wire=self.selection();wire['sources'][0]['rates'][0]['text']='invented original'
        with self.assertRaises(ContractError):self.decode(wire)

    def test_cross_source_object_and_stale_bindings_are_rejected(self):
        wire=self.selection();wire['sources'][0]['rates'][0]['object']='510500.SH'
        with self.assertRaises(ContractError):self.decode(wire)
        wire=self.selection();wire['binding_ref']='stale'
        with self.assertRaises(ContractError):self.decode(wire)
        wire=self.selection();self.views[0]['parts'][0]['text']+='changed reading'
        with self.assertRaises(ContractError):self.decode(wire)

    def test_duplicate_ids_and_sources_are_rejected(self):
        for kind in ('id','source'):
            with self.subTest(kind=kind):
                wire=self.selection()
                if kind=='source':wire['sources']*=2
                else:wire['sources'][0]['rates']*=2
                with self.assertRaises(ContractError):self.decode(wire)

    def test_spans_cannot_merge_different_pages_or_fragments(self):
        wire=self.selection();wire['sources'][0]['rates'][0]['span']=dict(start=0,end=199)
        with self.assertRaises(ContractError):self.decode(wire)

    def test_need_links_are_nonempty_unique_and_question_scoped(self):
        for links in ([],[dict(question_id='q1',need_ids=[])],
                [dict(question_id='q1',need_ids=['unknown_need'])]):
            wire=self.selection();wire['sources'][0]['rates'][0]['need_links']=links
            with self.assertRaises(ContractError):self.decode(wire)

    def test_schema_binds_actual_readings_and_keeps_task_immutable(self):
        before=copy.deepcopy(self.task);first=self.schema();self.assertEqual(self.task,before)
        self.views[0]['parts'][0]['text']+='changed'
        with self.assertRaises(ContractError):self.schema()

    def test_exact_selected_period_basis_is_retained_without_fuzzy_quote_repair(self):
        item=self.decode()['evidence_specs'][0]
        self.assertEqual(item['reporting_period_basis'],item['calculation_context_basis']['period'])
        self.assertIn('逐\n日累计',item['text'])

    def test_native_model_transport_decodes_only_the_registered_fee_container(self):
        import tempfile
        from researchhelper.model import ModelSettings,ModelService
        temporary=tempfile.TemporaryDirectory(prefix='fee-protocol-model-',dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        settings=ModelSettings(temporary.name)
        settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
            api_key='FAKE_FEE_PROTOCOL_KEY',extraction_mode='direct',reasoning_effort='low'))
        service=ModelService(settings,offline=False)
        response=dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',
                content=[dict(type='output_text',text=json.dumps(self.selection()))])],
            usage=dict(input_tokens=10,output_tokens=10))
        initial=self.task['ledger']['used']['model_calls']
        with patch.object(service,'_request_bytes',return_value=json.dumps(response).encode()) as request:
            decoded=service.reason_structured_fee_original(self.task,[],self.views)
        self.assertEqual(json.loads(decoded),self.decode())
        body=json.loads(request.call_args.args[1].data)
        self.assertEqual(body['text']['format']['name'],formats.FEE_ORIGINAL_FUNCTION_NAME)
        self.assertEqual(self.task['ledger']['used']['model_calls'],initial+1)
        self.assertEqual(self.task['ledger']['dispatches'][-1]['output_protocol'],formats.FEE_ORIGINAL_PROTOCOL)

    def test_ordinary_extract_phase_uses_one_fee_call_and_no_freeform_quote_grammar(self):
        from researchhelper.model import ModelService
        from researchhelper.reasoning import model_object,evidence_contract
        class Selector:
            supports_structured_analysis=lambda self:True
            supports_financial_references=lambda self:True
            reason_structured_extraction=lambda *args:None
            def reason_structured_fee_original(inner,task,messages,views):
                inner.messages=messages
                return json.dumps(self.decode())
        model=Selector()
        payload=dict(untrusted_sources=self.views,evidence_contract=evidence_contract(self.task,self.views))
        value,_=model_object(model,self.task,'extract',payload,{'evidence_specs'})
        self.assertEqual(value,self.decode())
        self.assertIn('rh_select_fee_original_v1',model.messages[0]['content'])
        self.assertNotIn('只返回evidence_specs列表',model.messages[0]['content'])

    def test_marker_is_created_only_for_new_formal_tasks(self):
        import tempfile
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        temporary=tempfile.TemporaryDirectory(prefix='fee-protocol-new-task-',dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        agent=ResearchAgent(ResearchArchive(Path(temporary.name)/'store'),ROOT,offline=True,
            require_argument_support=True)
        self.assertEqual(agent.create('比较基金费用')['fee_original_selection_version'],1)
        self.assertNotIn('fee_original_selection_version',agent.create('核对原件',intent='原件内容核对'))
        self.assertNotIn('fee_original_selection_version',agent.create('固定例',fixture='etf'))
        for worker in agent.close_runtime():worker.join(timeout=5)

    def period_item(self):
        item=copy.deepcopy(self.task['reasoning_state']['rejected_extraction']['proposed_specs'][0])
        source=next(record for record in self.task['source_records'] if record['id']==item['artifact_id'])
        part=next(part for part in self.views[0]['parts'] if part['locator']=='第30页')
        item['text']=part['text']
        quote='2026年1月1日至2026年6\n月 30日'
        self.assertIn(quote,part['text'])
        period=dict(kind='material',artifact_id=source['id'],snapshot_hash=source['hash'],
            part='第30页',quote=quote)
        metric=dict(period,quote='基金管理费')
        item.update(task_id=self.task['id'],research_version=self.task['version'],
            period='2026年1月1日至2026年6月30日',
            calculation_context=dict(period='2026年1月1日至2026年6月30日',metric='management_fee'),
            calculation_context_basis=dict(period=period,metric=metric),
            reporting_period_basis=copy.deepcopy(period))
        return item

    def test_wrapped_literal_fee_period_has_its_own_same_original_proof(self):
        from researchhelper.evidence import validate_calculation_context_basis
        item=self.period_item();before=copy.deepcopy(item)
        validate_calculation_context_basis(item,self.task['source_records'])
        self.assertEqual(item,before)

    def test_fee_period_cannot_infer_an_interval_from_an_endpoint_or_wrong_year(self):
        from researchhelper.evidence import validate_calculation_context_basis
        for change in ('endpoint','year','cross_original','missing_proof'):
            with self.subTest(change=change):
                item=self.period_item()
                if change=='endpoint':
                    item['calculation_context_basis']['period']['quote']='2026年1月1日'
                    item['reporting_period_basis']=copy.deepcopy(item['calculation_context_basis']['period'])
                elif change=='year':item['period']=item['calculation_context']['period']='2025年1月1日至2025年6月30日'
                elif change=='cross_original':
                    item['calculation_context_basis']['period']['artifact_id']=self.views[1]['id']
                    item['reporting_period_basis']=copy.deepcopy(item['calculation_context_basis']['period'])
                else:item.pop('reporting_period_basis')
                with self.assertRaises(ContractError):
                    validate_calculation_context_basis(item,self.task['source_records'])


if __name__=='__main__':unittest.main()
