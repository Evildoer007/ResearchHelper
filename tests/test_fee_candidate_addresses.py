"""Finite fee addresses over retained originals, with all normal gates intact.

Selections below are explicit test inputs, never injected into a live study.
The retained partial task and its ledger/report remain byte-for-byte unchanged.
"""
import copy
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
from researchhelper import output_schema as formats
from researchhelper.contracts import ContractError,calculate,validate_calculation_context,context,canonical
from researchhelper.evidence import candidate_evidence,validate_calculation_context_basis
from researchhelper.fee_sources import annual_fee_mentions,original_fee_choices
from researchhelper.reasoning import evidence_contract,model_object
from researchhelper.source_reader import read,binding,reader_version_for,validate_read_specs

RETAINED=ROOT/'result/acceptance/one-sentence-research-iohya1u7/task.json'


def prepared():
    task=json.loads(RETAINED.read_text());task['fee_candidate_encoding']=1
    old=task['reasoning_state']['reading']['bundle']
    bundle=read(task,old['selection'],binding(task),old['artifact_ids'])
    task['reasoning_state']['reading']['bundle']=bundle
    return task,bundle


def probe():
    task,bundle=prepared()
    prompt=formats.fee_original_prompt(dict(untrusted_sources=bundle['views']),task)
    print('reader',bundle['reader_version'],'wire',formats.fee_original_output_schema(task,bundle['views'])['properties']['version'])
    for source in prompt['source_choices']:
        view=next(item for item in bundle['views'] if item['id']==next(
            item['artifact_id'] for item in evidence_contract(task,bundle['views'])['sources']
            if item.get('object_binding')==source['object_binding']))
        c=original_fee_choices(view,object_code=source['object_binding']['value'].split('.')[0])
        print(source['object_binding']['value'],{key:len(c.get(key,[])) for key in ('rates','funds','shares','periods')},
            [(r['metric'],r['raw_token']) for r in c.get('rates',[])])
        print('fee_reading_parts',[(part['locator'],[term for term in ('基金主代码','交易代码','年费率')
            if term in part['text']]) for part in view['parts'] if any(term in part['text'] for term in ('基金主代码','交易代码','年费率'))])


class FeeCandidateAddressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before=hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.retained=json.loads(RETAINED.read_text())
        with patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls')):
            cls.prepared_task,cls.bundle=prepared()

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest()==cls.before

    def setUp(self):
        self.task=copy.deepcopy(self.prepared_task)
        self.views=self.task['reasoning_state']['reading']['bundle']['views']
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def schema(self):return formats.fee_original_output_schema(self.task,self.views)

    def prompt(self):
        return formats.fee_original_prompt(dict(untrusted_sources=self.views,evidence_contract={}),self.task)

    def wire(self):
        prompt=self.prompt();sources=[]
        for source in prompt['source_choices']:
            options=source['fee_candidates'];rates=[]
            period=next(item for item in options['periods'] if item['value']=='2026年1月1日至2026年6月30日')
            for metric in ('management_fee','custody_fee'):
                rate=next(item for item in options['rates'] if item['metric']==metric)
                ident=source['source_ref']+'_'+metric
                needs=source['question_requirements']['q1']
                rates.append(dict(id=ident,object=source['object_binding']['value'],
                    rate_reference=rate['reference'],fund_reference=options['funds'][0]['reference'],
                    share_reference=options['shares'][0]['reference'],period_reference=period['reference'],
                    question_ids=['q1'],need_links=[dict(question_id='q1',need_ids=[needs[0]['id']])]))
            sources.append(dict(source_ref=source['source_ref'],rates=rates))
        return dict(version=2,binding_ref=prompt['fee_binding_ref'],sources=sources)

    def decode(self,wire=None):
        return formats.decode_fee_original_output(self.wire() if wire is None else wire,
            self.schema(),(self.task,self.views))

    def test_method_reading_contains_both_fees_and_explicit_codes(self):
        self.assertEqual(self.bundle['reader_version'],4)
        self.assertEqual(self.schema()['properties']['version']['enum'],[2])
        for source in self.prompt()['source_choices']:
            c=source['fee_candidates']
            self.assertTrue(c['funds'] and c['shares'] and c['periods'])
            self.assertEqual({item['metric'] for item in c['rates']},{'management_fee','custody_fee'})

    def test_four_selected_original_rates_pass_unchanged_source_and_reading_gates(self):
        specs=self.decode()['evidence_specs'];self.assertEqual(len(specs),4)
        validate_read_specs(self.task,specs,self.task['reasoning_state']['reading']['bundle'])
        for item in specs:
            original=next(source for source in self.task['source_records'] if source['id']==item['artifact_id'])
            candidate=candidate_evidence(self.task,original,item)
            self.assertEqual(candidate['source_check'],'通过')
            self.assertEqual(candidate['applicability_check'],'待核验')
            self.assertNotIn('source_check',item)

    def test_explicit_dates_and_units_are_not_publication_or_annualization(self):
        for item in self.decode()['evidence_specs']:
            self.assertEqual(item['observation_date'],'2026-06-30')
            self.assertEqual(item['period'],'2026年1月1日至2026年6月30日')
            self.assertEqual(item['raw_unit'],item['unit']);self.assertEqual(item['raw_unit'],'%')
            self.assertEqual(item['conversion_factor'],1)
            self.assertIsNone(item['published_at']);self.assertEqual(item['publication']['certainty'],'未知')

    def test_literal_codes_and_fund_scope_never_invent_a_share_class(self):
        for item in self.decode()['evidence_specs']:
            self.assertEqual(item['calculation_context']['fund_id'],item['object'].split('.')[0])
            self.assertIn('基金主代码',item['calculation_context_basis']['fund_id']['quote'])
            if '交易代码' not in item['calculation_context_basis']['share_class']['quote']:
                self.assertIn('基金简称',item['calculation_context_basis']['share_class']['quote'])
                self.assertTrue(any('不证明A/C' in condition for condition in item['review']['conditions']))
            self.assertNotEqual(item['calculation_context_basis']['fund_id'],item['calculation_context_basis']['share_class'])

    def test_all_six_context_fields_and_exact_original_lines_survive(self):
        for item in self.decode()['evidence_specs']:
            self.assertEqual(set(item['calculation_context']),set(item['calculation_context_basis']))
            self.assertEqual(item['reporting_period_basis'],item['calculation_context_basis']['period'])
            self.assertIn('基金资产净值',item['calculation_context_basis']['fee_basis']['quote'])
            self.assertIn(item['raw_token'],item['text'])

    def test_original_rates_are_calculable_but_not_approved_answers(self):
        specs=self.decode()['evidence_specs']
        names=('management_a','custody_a','management_b','custody_b')
        bindings=dict(zip(names,specs));period=specs[0]['period']
        validate_calculation_context('management_custody_fee_summary_v1',bindings,dict(unit='%/年',period=period))
        out=calculate('management_custody_fee_comparison_v1',
            {name:item['value'] for name,item in bindings.items()},dict(unit='%/年',period=period))
        self.assertEqual(out['value'],0)
        self.assertFalse(self.task['calculations']);self.assertFalse(self.task['evidence'])

    def test_unknown_cross_family_and_cross_source_refs_are_rejected(self):
        for field,bad in (('rate_reference','missing'),('fund_reference','rates_1'),
                ('period_reference','periods_9999'),('share_reference','funds_1')):
            wire=self.wire();wire['sources'][0]['rates'][0][field]=bad
            with self.assertRaises(ContractError):self.decode(wire)
        wire=self.wire();wire['sources'][0]['rates'][0]['object']='510500.SH'
        with self.assertRaises(ContractError):self.decode(wire)

    def test_free_values_contexts_dates_and_spans_are_not_accepted(self):
        for field,value in (('raw_token','0.01'),('span',dict(start=0,end=1)),
                ('calculation_context',{}),('observation_date','2026-08-27')):
            wire=self.wire();wire['sources'][0]['rates'][0][field]=value
            with self.assertRaises(ContractError):self.decode(wire)

    def test_stale_binding_task_and_changed_reading_are_rejected(self):
        wire=self.wire();wire['binding_ref']='old'
        with self.assertRaises(ContractError):self.decode(wire)
        wire=self.wire();schema=self.schema();task=copy.deepcopy(self.task);task['batch']+=1
        with self.assertRaises(ContractError):formats.decode_fee_original_output(wire,schema,(task,self.views))
        self.views[0]['parts'][0]['text']+='altered'
        with self.assertRaises(ContractError):self.schema()

    def test_duplicated_sources_ids_and_empty_need_mapping_are_rejected(self):
        for kind in ('source','id','needs'):
            wire=self.wire()
            if kind=='source':wire['sources']*=2
            elif kind=='id':wire['sources'][0]['rates']*=2
            else:wire['sources'][0]['rates'][0]['need_links']=[]
            with self.assertRaises(ContractError):self.decode(wire)

    def test_legacy_and_unrecognized_sources_keep_full_v1_obligations(self):
        self.task.pop('fee_candidate_encoding')
        self.assertEqual(self.schema()['properties']['version']['enum'],[1])
        task=copy.deepcopy(self.prepared_task)
        views=copy.deepcopy(self.retained['reasoning_state']['reading']['bundle']['views'])
        task['reasoning_state']['reading']['bundle']=copy.deepcopy(self.retained['reasoning_state']['reading']['bundle'])
        self.assertEqual(formats.fee_original_output_schema(task,views)['properties']['version']['enum'],[1])

    def test_explicit_legacy_reader_is_byte_identical(self):
        task=copy.deepcopy(self.retained);old=task['reasoning_state']['reading']['bundle']
        again=read(task,old['selection'],binding(task),old['artifact_ids'],reader_version=old['reader_version'])
        self.assertEqual(canonical(again),canonical(old))

    def test_reader_does_not_select_fee_protocol_for_unrelated_methods(self):
        task=copy.deepcopy(self.task);task['calculation_specs']=[]
        for q in task['questions']:q['method_needs']=[]
        self.assertEqual(reader_version_for(task),2)
        old=self.retained['reasoning_state']['reading']['bundle']
        with self.assertRaises(ContractError):
            read(task,old['selection'],binding(task),old['artifact_ids'],reader_version=4)

    def test_parser_does_not_invent_rates_from_formula_monthly_or_signed_values(self):
        for text in ('基金管理费按基金资产净值计提，月费率为0.15%。',
                '日管理人报酬=前一日基金资产净值×0.15%/当年天数。',
                '管理费按基金资产净值计提，年费率为-0.15%。',
                '管理费按基金资产净值计提，年费率为− 0.15%。'):
            self.assertFalse(annual_fee_mentions(text),text)

    def test_annual_label_variants_keep_specific_component(self):
        for text,metric in (('年管理费率为0.15%。','management_fee'),
                ('托管费按净资产的0.05%的年费率计提。','custody_fee'),
                ('管理人报酬按基金资产净值的0.15%的年费率计提。','management_fee')):
            self.assertEqual(annual_fee_mentions(text)[0]['metric'],metric)

    def test_fee_aliases_do_not_expire_unrelated_financial_checkpoints(self):
        from researchhelper.evidence import material_calculation_context_contract
        financial=material_calculation_context_contract(['entity_id','currency','period',
            'accounting_scope','accounting_standard','metric'])
        fee=material_calculation_context_contract(['fund_id','share_class','period','fee_basis','rate_period','metric'])
        self.assertEqual(financial['canonical_values']['metric']['management_fee'],('管理费',))
        self.assertEqual(fee['canonical_values']['metric']['management_fee'],('管理费','管理人报酬'))

    def test_model_prompt_uses_refs_instead_of_free_context_and_line_numbers(self):
        class Selector:
            supports_structured_analysis=lambda self:True
            supports_financial_references=lambda self:True
            def reason_structured_fee_original(inner,task,messages,views):
                inner.messages=messages
                return json.dumps(self.decode())
        model=Selector()
        value,_=model_object(model,self.task,'extract',dict(untrusted_sources=self.views,
            evidence_contract=evidence_contract(self.task,self.views)),{'evidence_specs'})
        self.assertEqual(len(value['evidence_specs']),4)
        self.assertIn('提交version=2',model.messages[0]['content'])
        self.assertNotIn('rates每项含id、object、raw_token',model.messages[0]['content'])

    def test_actual_model_transport_keeps_v2_schema_and_original_budget(self):
        from researchhelper.model import ModelSettings,ModelService
        with tempfile.TemporaryDirectory(prefix='fee-candidates-model-',dir=ROOT/'result/acceptance') as temporary:
            settings=ModelSettings(temporary)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
                api_key='SYNTHETIC_FEE_CANDIDATES_ONLY',extraction_mode='direct',reasoning_effort='low'))
            model=ModelService(settings,offline=False)
            response=dict(object='response',status='completed',error=None,incomplete_details=None,
                output=[dict(type='message',role='assistant',status='completed',content=[
                    dict(type='output_text',text=json.dumps(self.wire()))])],
                usage=dict(input_tokens=10,output_tokens=10))
            initial=self.task['ledger']['used']['model_calls']
            with patch.object(model,'_request_bytes',return_value=json.dumps(response).encode()) as request:
                value=model.reason_structured_fee_original(self.task,[],self.views)
            self.assertEqual(json.loads(value),self.decode())
            body=json.loads(request.call_args.args[1].data)
            self.assertEqual(body['text']['format']['schema']['properties']['version']['enum'],[2])
            self.assertEqual(self.task['ledger']['used']['model_calls'],initial+1)

    def test_prompt_and_decode_do_not_mutate_task_or_ledger(self):
        before=copy.deepcopy(self.task)
        self.prompt();self.decode()
        self.assertEqual(self.task,before)
