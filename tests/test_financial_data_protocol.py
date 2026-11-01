"""Retained originals through the real reader, selector and financial gates.

Selections are explicit test inputs, not reconstructed model responses or a
resumed research task. No network, model call or production Store is used.
"""
import copy
import importlib
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import unittest
from unittest.mock import patch

SOURCE = importlib.import_module((__package__+'.' if __package__ else '')+'test_financial_data_module').SOURCE
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.financial_data import product_selection_enabled, duration_observation_applies
from researchhelper.financial_sources import financial_amount_tables
from researchhelper.source_reader import read, binding, validate_read_specs
from researchhelper.reasoning import (evidence_contract, validate_model_specs,
    expand_financial_value_references, expand_material_context_references,
    expand_material_publication_references)
from researchhelper.output_schema import (original_batch_output_schema, original_batch_prompt,
    decode_original_batch, financial_reference_output_schema, StructuredOutputError)
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent

ROOT = Path(__file__).resolve().parents[1]


class FinancialDataProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external I/O'))
        cls.guard.start(); cls.addClassCleanup(cls.guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser,'worker_target',staticmethod(frozen_pdf_worker))
            worker.start(); cls.addClassCleanup(worker.stop)
        from researchhelper.material_parser import MaterialParser
        original_parse = MaterialParser.parse
        def observed_parse(parser,*args,**kwargs):
            result = original_parse(parser,*args,**kwargs)
            if result.get('status') != '已解析':
                raise AssertionError(dict(parser_status=result.get('status'),
                    runtime=result.get('parse_runtime'),worker=MaterialParser.worker_target.__module__))
            return result
        observer = patch.object(MaterialParser,'parse',observed_parse)
        observer.start();cls.addClassCleanup(observer.stop)
        cls.raw = SOURCE.read_bytes(); cls.original = json.loads(cls.raw)
        cls.task = copy.deepcopy(cls.original)
        cls.task['financial_data_product_version'] = 1
        requests = copy.deepcopy(cls.task['reasoning_state']['reading']['bundle']['selection'])
        cls.bundle = read(cls.task, requests, binding(cls.task), reader_version=7)
        cls.views = cls.bundle['views']
        cls.contract = evidence_contract(cls.task, cls.views)
        cls.schema = original_batch_output_schema(cls.task, cls.views)
        cls.prompt = original_batch_prompt(dict(untrusted_sources=cls.views,
            evidence_contract=cls.contract), cls.task)
        probe = cls('test_all_numeric_routes_pass_actual_reading_source_and_applicability_gates')
        specs = decode_original_batch(probe.wire(),cls.schema,(cls.task,cls.views))['evidence_specs']
        specs = expand_financial_value_references(cls.task,specs,cls.views)
        specs = expand_material_publication_references(cls.task,specs,cls.views)
        cls.expanded_specs = expand_material_context_references(cls.task,specs,cls.views)

    @classmethod
    def tearDownClass(cls):
        assert SOURCE.read_bytes() == cls.raw

    def amount(self, source_index, field, period, needle, suffix):
        source = self.contract['sources'][source_index]
        choice = self.prompt['source_choices'][source_index]
        catalogue = source['financial_amount_tables']
        table, column, reference = next((table,column,reference)
            for table in catalogue['tables'] for column in table['columns']
            for reference in column['row_references']
            if table['rows'][reference]['cells'][column['reference']]['field'] == field
            and table['rows'][reference]['cells'][column['reference']]['period'] == period
            and needle in table['rows'][reference]['text'])
        need_token = dict(capital_expenditure='购建固定资产', cash_reconciliation='净利润调节至',
                          nonrecurring='非经常性损益项目及金额')[field]
        need = next(need for question in self.task['questions'] if question['id'] == 'q4'
                    for need in question['evidence_coverage']['items'] if need_token in need['text'])
        observations = (self.prompt['untrusted_sources'][source_index].get('observation_choices')
                        or choice['observation_choices'])
        observation = next(item['reference'] for item in observations if item['date'] == period[:4]+'-06-30')
        return dict(id='controlled_'+suffix, object=source['object_binding']['value'], nature='已实现事实',
            table_reference=table['reference'], column_reference=column['reference'], row_reference=reference,
            observation_reference=observation, review=dict(scope='current_original_descriptive_only',
                need_links={qid:([need['id']] if qid == 'q4' else None) for qid in self.contract['question_ids']}))

    def wire(self):
        sources = []
        first = self.contract['sources'][0]; choice = self.prompt['source_choices'][0]
        financial_cells = []
        labels = dict(revenue='合并利润表营业收入', operating_cost='合并利润表营业成本',
                      net_profit='合并利润表净利润', operating_cash_flow='合并现金流量表经营活动产生的现金流量净额')
        for index, candidate in enumerate(first['financial_context_cells']):
            need = next(need for question in self.task['questions'] if question['id'] == 'q2'
                for need in question['evidence_coverage']['items'] if labels[candidate['context']['metric']] in need['text'])
            year = re.search(r'\d{4}', candidate['context']['period'])[0]
            observation = next(item['reference'] for item in self.prompt['untrusted_sources'][0]['observation_choices']
                               if item['date'] == year+'-06-30')
            financial_cells.append(dict(id='controlled_core_'+str(index), object=first['object_binding']['value'],
                nature='已实现事实', financial_context_reference=candidate['candidate_id'],
                shared_context_references={field:first['financial_shared_context_choices'][field][0]['candidate_id']
                    for field in ('entity_id','accounting_standard')}, observation_reference=observation,
                review=dict(scope='current_original_descriptive_only', need_links={qid:
                    ([need['id']] if qid == 'q2' else None) for qid in self.contract['question_ids']})))
        amounts = [self.amount(0,field,period,needle,field+'_'+period)
            for field,needle in (('capital_expenditure','购建固定资产'), ('cash_reconciliation','存货的增加'))
            for period in ('20260630','20250630')]
        amounts.append(self.amount(0,'nonrecurring','20260630','合计','nonrecurring_20260630'))
        sources.append(dict(source_ref=choice['source_ref'], selection=dict(version=9,
            source_reference=choice['binding'], current_version_only=True, financial_cells=financial_cells,
            text_quotes=[], amount_cells=amounts)))
        second = self.prompt['source_choices'][1]
        sources.append(dict(source_ref=second['source_ref'], selection=dict(**second['binding'], text_quotes=[],
            amount_cells=[self.amount(1,'nonrecurring','20250630','合计','nonrecurring_20250630')])))
        return dict(version=1,binding_ref=self.prompt['batch_binding_ref'],sources=sources)

    def expanded(self):
        return copy.deepcopy(self.expanded_specs)

    def test_all_numeric_routes_pass_actual_reading_source_and_applicability_gates(self):
        before = fingerprint(self.task['ledger'])
        specs = self.expanded()
        self.assertEqual(len(specs), 14)
        validate_model_specs(self.task, specs)
        validate_read_specs(self.task, specs, self.bundle)
        analyzer = ResearchAnalyzer()
        adopted = []
        for spec in specs:
            record = next(record for record in self.task['source_records'] if record['id'] == spec['artifact_id'])
            item = candidate_evidence(self.task, record, spec)
            adopted.append(analyzer.assess(self.task,item,spec['review'],self.task['source_records']))
        self.assertEqual(len([item for item in adopted if item.get('calculation_context')]), 8)
        negative = {item['period']:item['value'] for item in adopted if '存货的增加' in item['text']}
        self.assertEqual(negative, {'截至2026年6月30日止6个月期间':-53231176,
                                    '截至2025年6月30日止6个月期间':-25987262})
        self.assertEqual(fingerprint(self.task['ledger']), before)
        self.assertEqual(self.task['evidence'], [])
        self.assertEqual(self.task['status'], self.original['status'])

    def test_no_empty_amount_collection_is_requested_when_no_cells_were_read(self):
        view = copy.deepcopy(self.views[0])
        view['parts'] = [part for part in view['parts'] if part['locator'] in
            ('第1页','第65页','第67页','第67页期间列式文本','第80页','第81页')]
        schema = financial_reference_output_schema(self.task,[view])
        self.assertNotIn('amount_cells', schema['properties'])
        old = dict(self.task);old.pop('financial_data_product_version')
        legacy = financial_reference_output_schema(old,[view])
        self.assertEqual(legacy['properties']['amount_cells']['maxItems'],0)

    def test_unread_period_and_continuation_are_not_offered_as_seen_values(self):
        view = self.original['reasoning_state']['reading']['bundle']['views'][1]
        record = next(record for record in self.task['source_records'] if record['id'] == view['id'])
        catalogue = financial_amount_tables(self.task,record,view)
        self.assertEqual(catalogue['tables'],[])
        self.assertTrue(any('第65页' in cell['required_parts'] for cell in catalogue['unread_supplement_cells']))
        self.assertEqual(sum(len(table['rows']) for table in self.contract['sources'][1]['financial_amount_tables']['tables']),8)

    def test_changed_sign_period_binding_and_calculation_authority_are_rejected(self):
        specs = self.expanded()
        original = next(spec for spec in specs if '存货的增加' in spec['text'] and '2026' in spec['period'])
        record = next(record for record in self.task['source_records'] if record['id'] == original['artifact_id'])
        for change in ('sign','period','binding','context'):
            spec = copy.deepcopy(original)
            if change == 'sign':spec['value'] = abs(spec['value'])
            elif change == 'period':spec['period'] = '2025年上半年'
            elif change == 'binding':spec['record_locator']['financial_amount_column']['cell_binding'] = 'foreign'
            else:spec['calculation_context'] = {'metric':'revenue'}
            with self.subTest(change=change),self.assertRaises(ContractError): candidate_evidence(self.task,record,spec)

    def test_collection_failure_records_registered_field_without_raw_values(self):
        wire = self.wire();wire['sources'][0]['selection']['amount_cells'] = None
        with self.assertRaises(StructuredOutputError) as caught:
            decode_original_batch(wire,self.schema,(self.task,self.views))
        self.assertEqual(caught.exception.diagnostic['selection_location'],'$.sources[0].selection.amount_cells')
        self.assertFalse(caught.exception.diagnostic['submitted_values_retained'])
        self.assertTrue(caught.exception.diagnostic['schema_issues'])

    def test_changed_policy_cannot_decode_the_new_cell_bindings(self):
        old = dict(self.task);old.pop('financial_data_product_version')
        with self.assertRaises(ContractError):decode_original_batch(self.wire(),self.schema,(old,self.views))
        for value in (True,2,'1',1.0):
            with self.assertRaises(ContractError):product_selection_enabled(dict(self.task,financial_data_product_version=value))

    def test_new_tasks_enable_the_product_without_upgrading_old_tasks(self):
        with tempfile.TemporaryDirectory(prefix='financial-product-new-task-') as folder:
            agent = ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            task = agent.create('研究比亚迪002594.SZ的经营表现')
            self.assertEqual(task['financial_data_product_version'],1)
        self.assertNotIn('financial_data_product_version',self.original)

    def test_reader_keeps_original_supplement_and_accounting_pages_in_its_shared_budget(self):
        filings = {view['id'] for view in self.views if view['kind'] == 'filing'}
        for coverage in self.bundle['coverage']:
            if coverage['artifact_id'] not in filings:
                continue
            groups = coverage['financial_data_reading']['groups']
            self.assertTrue(all(group['read_in_full'] for group in groups))
            self.assertTrue(any(group['field'] == 'nonrecurring' for group in groups))
            self.assertTrue(any(group['field'] == 'report_context' for group in groups))
            self.assertFalse(coverage['financial_data_reading']['financial_approval'])
        self.assertEqual(self.bundle['selection'],self.original['reasoning_state']['reading']['bundle']['selection'])

    def test_explicit_duration_is_not_a_single_day_or_a_publication_time(self):
        item = next(spec for spec in self.expanded_specs if spec.get('calculation_context'))
        record = next(record for record in self.task['source_records'] if record['id'] == item['artifact_id'])
        self.assertTrue(duration_observation_applies(item,record))
        for field,value in (('period',item['observation_date']),('observation_date','2026-01-01')):
            changed = dict(item,**{field:value})
            self.assertFalse(duration_observation_applies(changed,record))
        changed = copy.deepcopy(item)
        changed['calculation_context_basis']['period']['snapshot_hash'] = 'foreign'
        self.assertFalse(duration_observation_applies(changed,record))

    def test_prose_only_financial_source_keeps_a_literal_report_duration(self):
        wire = self.wire()
        choice = self.prompt['source_choices'][1]
        quote = next(reference for part in self.prompt['untrusted_sources'][1]['parts']
            for reference,text in part['quote_choices'] if '本财务报表符合' in ''.join(text.split()))
        observation = next(anchor['index'] for anchor in self.prompt['untrusted_sources'][1]['observation_anchors']
                           if anchor['date'] == '2025-06-30')
        period = next(period['reference'] for period in choice['reporting_period_choices'] if '2025' in period['period'])
        wire['sources'][1]['selection']['text_quotes'] = [dict(id='controlled_qualitative_duration',
            object=self.contract['sources'][1]['object_binding']['value'], nature='已实现事实',
            question_ids=['q4'], quote_reference=quote, observation_anchor=observation,
            period_reference=period, review=dict(scope='current_original_descriptive_only',need_links=[]))]
        specs = decode_original_batch(wire,self.schema,(self.task,self.views))['evidence_specs']
        spec = next(spec for spec in specs if spec['id'] == 'controlled_qualitative_duration')
        self.assertEqual(spec['period'],'截至2025年6月30日止6个月期间')
        self.assertNotIn('value',spec)
        self.assertNotIn('calculation_context',spec)
        validate_read_specs(self.task,[spec],self.bundle)


if __name__ == '__main__':
    unittest.main()
