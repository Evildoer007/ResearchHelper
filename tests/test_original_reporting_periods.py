"""Complete literal periods replace free-form intervals, not source gates."""
import copy
import hashlib
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_current_snapshot, validate_reporting_period_basis
from researchhelper.financial_sources import original_date_anchors, original_period_anchors
from researchhelper.financial_sources import original_line_index
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (original_span_output_schema, original_span_prompt, decode_original_spans,
    StructuredOutputError, _original_span_choices)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract, model_object, MAX_CONTEXT_CHARACTERS
from researchhelper.source_reader import read, binding, catalog

RETAINED=ROOT/'result/acceptance/autonomous-data-phase-jv_z7eip/task.json'


class LiteralReportingPeriodTests(unittest.TestCase):
    def test_full_same_part_dates_only_no_inferred_year_or_end(self):
        texts=['报告期自2026年1月1日起至2026年6 月30日。',
               '期间2026-01-01至2026-06-30。',
               '2026年上半年','2026年1月1日至6月30日',
               '2026年6月30日至2026年1月1日','2026年2月30日至2026年6月30日']
        for index,text in enumerate(texts):
            periods=original_period_anchors(dict(parts=[dict(locator='页1',text=text)]))
            self.assertEqual(bool(periods),index<2,text)
        self.assertFalse(original_period_anchors(dict(parts=[dict(locator='页1',text='2026年1月1日至'),
                                                            dict(locator='页2',text='2026年6月30日')])))

    def test_literal_endpoint_not_unrelated_signature_or_transaction_date(self):
        view=dict(parts=[dict(locator='页1',text='报告期自2026年1月1日至2026年6月30日。交易日2026年6月26日。')])
        dates=original_date_anchors(view);period=original_period_anchors(view,dates)[0]
        self.assertEqual(dates[period['end_anchor']]['date'],'2026-06-30')
        self.assertEqual(period['quote'],'2026年1月1日至2026年6月30日')


class OriginalReportingPeriodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=RETAINED.read_bytes()
        cls.old=json.loads(cls.original)
        cls.task=copy.deepcopy(cls.old)
        cls.task['original_period_selection_version']=1
        cls.task['input_hash']=input_fingerprint(cls.task)
        cls.record=next(item for item in cls.task['source_records'] if item['operation']=='filing_original')
        parts={part['locator']:part for part in cls.record['snapshot']['material']['parts']}
        requested=['第2页','第5页','第30页','第65页']
        selection=[dict(artifact_id=cls.record['id'],snapshot_hash=cls.record['hash'],queries=[],
            parts=[dict(part=part,start=0,end=len(parts[part]['text'])) for part in requested],
            reason='内存只读夹具核对报告期、基金概况、费用原文及无关交易日期，不采用研究产物。',question_ids=['q1'])]
        cls.bundle=read(cls.task,selection,binding(cls.task),[cls.record['id']])
        cls.views=cls.bundle['views']
        cls.schema=original_span_output_schema(cls.task,cls.views)
        cls.payload=dict(question=cls.task['question'],intent=cls.task['intent'],objects=cls.task['objects'],
            cutoff=cls.task['cutoff'],questions=cls.task['questions'],calculations=cls.task['calculation_specs'],
            evidence_contract=evidence_contract(cls.task,cls.views),field_registry={},untrusted_sources=cls.views,
            source_reading=dict(reading_hash=cls.bundle['reading_hash'],coverage=cls.bundle['coverage']))
        cls.prompt=original_span_prompt(cls.payload,cls.task)

    @classmethod
    def tearDownClass(cls):
        if RETAINED.read_bytes()!=cls.original:raise AssertionError('Closed research changed')

    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Offline period test'))
        guard.start();self.addCleanup(guard.stop)

    def wire(self):
        variant=next(v for v in self.schema['properties']['selections']['items']['anyOf']
                     if v['properties']['kind']['enum']==['text'])
        p=variant['properties'];link=p['review']['properties']['need_links']['items']['anyOf'][0]['properties']
        lines=[line['index'] for line in original_line_index(self.views[0]) if line['part']=='第30页']
        item=dict(id='ev_rule',
            source_ref=p['source_ref']['enum'][0],kind='text',object=p['object']['enum'][0],
            nature='合同政策条款',text_span=dict(start=lines[0],end=lines[-1]),temporal_reference=dict(
                period_ref=self.prompt['untrusted_sources'][0]['reporting_period_choices'][0][0]),
            review=dict(scope='current_original_descriptive_only',need_links=[dict(
                question_id=link['question_id']['enum'][0],need_ids=[link['need_ids']['items']['enum'][0]])]))
        item['question_ids']=[link['question_id']['enum'][0]]
        return dict(version=12,binding_ref=self.prompt['span_binding_ref'],selections=[item])

    def decode(self,wire=None):
        return decode_original_spans(wire or self.wire(),self.schema,(self.task,self.views))

    def test_literal_period_and_exact_endpoint_pass_existing_checks_without_approval(self):
        spec=self.decode()['evidence_specs'][0]
        self.assertEqual(spec['period'],'2026年1月1日起至2026年6月30日')
        self.assertEqual(spec['observation_date'],'2026-06-30')
        self.assertEqual(spec['reporting_period_basis']['part'],'第2页')
        validate_reporting_period_basis(spec,self.record)
        self.assertTrue(validate_current_snapshot(spec,self.task))
        self.assertNotIn('source_check',spec);self.assertNotIn('applicability_check',spec)
        self.assertEqual(spec['text'],next(part['text'] for part in self.views[0]['parts'] if part['locator']=='第30页'))

    def test_cannot_mix_freely_typed_period_or_unrelated_date_with_period_reference(self):
        for extra in (dict(period='2026-01-01至2026-06-30'),dict(observation_anchor=0)):
            wire=self.wire();wire['selections'][0].update(extra)
            with self.subTest(extra=extra),self.assertRaises(StructuredOutputError):self.decode(wire)
        wire=self.wire();wire['selections'][0]['temporal_reference']['observation_anchor']=0
        with self.assertRaises(StructuredOutputError):self.decode(wire)

    def test_unknown_reference_and_other_source_binding_are_not_repaired(self):
        for field,value in (('temporal_reference',dict(period_ref='p999999')),('source_ref','source_2')):
            wire=self.wire();wire['selections'][0][field]=value
            with self.subTest(field=field),self.assertRaises(StructuredOutputError):self.decode(wire)

    def test_date_only_retains_a_date_and_never_mints_a_report_period(self):
        dates=original_date_anchors(self.views[0])
        index=next(index for index,date in enumerate(dates) if date['date']=='2026-06-26')
        wire=self.wire();wire['selections'][0]['temporal_reference']=dict(observation_anchor=index)
        spec=self.decode(wire)['evidence_specs'][0]
        self.assertEqual(spec['period'],'2026-06-26')
        self.assertNotIn('reporting_period_basis',spec)

    def test_real_fee_number_keeps_exact_raw_unit_and_has_no_invented_annual_context(self):
        choice=_original_span_choices(self.task,self.views)[0]
        token=next(token for token in choice['number_tokens'] if token['token']=='0.15'
                   and choice['lines'][token['line']]['part']=='第30页')
        wire=self.wire();item=wire['selections'][0]
        item.update(kind='number',number_ref=token['ref'],unit='%',contexts=[],
                    text_span=dict(start=token['line'],end=token['line']),
                    unit_span=dict(start=token['line'],end=token['line']))
        spec=self.decode(wire)['evidence_specs'][0]
        self.assertEqual((spec['raw_token'],spec['raw_value'],spec['unit'],spec['conversion_factor']),('0.15',0.15,'%',1))
        self.assertNotIn('calculation_context',spec)
        self.assertEqual(spec['observation_date'],'2026-06-30')
        validate_reporting_period_basis(spec,self.record)

    def test_forged_other_date_or_other_original_still_fails_existing_validation(self):
        spec=self.decode()['evidence_specs'][0]
        for changes in (dict(period='2025年1月1日至2025年6月30日'),dict(reporting_period_basis=dict(
                spec['reporting_period_basis'],artifact_id='other'))):
            with self.subTest(changes=changes),self.assertRaises(ContractError):
                validate_reporting_period_basis(dict(spec,**changes),self.record)

    def test_catalogue_locations_are_not_substituted_for_actual_reading(self):
        source=catalog(self.task,[self.record['id']])['items'][0]
        locations=source['literal_period_locations']
        self.assertTrue(any('第2页' in row['parts'] and '2026年6月30日' in row['period'] for row in locations))
        shortened=copy.deepcopy(self.views)
        shortened[0]['parts']=[p for p in shortened[0]['parts'] if p['locator']=='第65页']
        with self.assertRaises(StructuredOutputError):
            decode_original_spans(self.wire(),self.schema,(self.task,shortened))

    def test_old_task_and_schema_keep_free_period_grammar_without_upgrading_failure(self):
        old=copy.deepcopy(self.task);old.pop('original_period_selection_version')
        previous=original_span_output_schema(old,self.views)
        self.assertEqual(previous['properties']['version']['enum'],[4])
        self.assertIn('period',previous['properties']['selections']['items']['anyOf'][0]['properties'])
        self.assertEqual(self.old['status'],'失败')
        self.assertEqual(RETAINED.read_bytes(),self.original)

    def test_full_payload_is_preserved_and_fits_the_existing_cap(self):
        self.assertLessEqual(len(canonical(self.prompt))+len(canonical(self.schema)),MAX_CONTEXT_CHARACTERS)
        self.assertEqual(self.prompt['questions'],self.payload['questions'])
        for original,view in zip(self.views,self.prompt['untrusted_sources']):
            self.assertEqual([p['text'] for p in original['parts']],
                [''.join(line[1] for line in part['indexed_lines']) for part in view['parts']])

    def test_real_model_adapter_selects_period_once_without_additional_research(self):
        reply=dict(object='response',status='completed',error=None,incomplete_details=None,output=[dict(type='message',role='assistant',
            status='completed',content=[dict(type='output_text',text=json.dumps(self.wire()))])],usage={})
        task=copy.deepcopy(self.task);before=task['ledger']['used']['model_calls']
        with tempfile.TemporaryDirectory(prefix='period-selector-',dir=ROOT/'result/acceptance') as folder:
            settings=ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
                               api_key='SYNTHETIC_PERIOD_KEY',extraction_mode='direct'))
            with patch.object(ModelService,'_request_bytes',return_value=json.dumps(reply).encode()) as transport:
                result,receipt=model_object(ModelService(settings,offline=False),task,'extract',self.payload,{'evidence_specs'})
        self.assertEqual(result,self.decode())
        self.assertEqual(transport.call_count,1)
        self.assertEqual(task['ledger']['used']['model_calls'],before+1)
        self.assertEqual(receipt['original_period_selection_version'],1)
        self.assertNotIn('literal_quantity_reference_version',receipt)
        body=transport.call_args.args[1].data.decode()
        self.assertIn('提交version=12',json.dumps(json.loads(body),ensure_ascii=False))

    def test_formal_policy_is_bound_and_not_installed_into_old_or_document_tasks(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task=agent.create('核对510300.SH半年报的费用')
            old=copy.deepcopy(task);old.pop('original_period_selection_version')
            self.assertNotEqual(input_fingerprint(task),input_fingerprint(old))
            self.assertNotIn('original_period_selection_version',agent.create_case('mechanism'))
            self.assertNotIn('original_period_selection_version',agent.create('核对文件',intent='原件内容核对'))
