"""Actual retained bytes, new parser projection, no old study resumed/written.

This exercises source/numeric gates offline; it is not an App/model/strategy
acceptance. Selection values are explicit test fixtures, not autonomous runs.
"""
import base64
import copy
import json
import socket
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, evidence_requirements, fingerprint
from researchhelper.evidence import candidate_evidence, _record_current_original
from researchhelper.financial_sources import (expand_financial_value_references,
    financial_table_candidates, validate_financial_metric_number)
from researchhelper.material_parser import MaterialParser, verified_material
from researchhelper.output_schema import (decode_financial_references,
    financial_reference_output_schema, financial_reference_prompt)
from researchhelper.reasoning import evidence_contract, source_views, validate_model_specs
from researchhelper.source_reader import binding, read, validate_read_specs

RETAINED = ROOT / 'result/acceptance/autonomous-data-phase-yd63l1kn/task.json'


class BorderlessFinancialCellsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = RETAINED.read_bytes()
        cls.task = json.loads(cls.original)
        cls.record = next(s for s in cls.task['source_records'] if s['operation'] == 'filing_original')
        cls.old_material = copy.deepcopy(cls.record['snapshot']['material'])
        cls.payload = base64.b64decode(cls.old_material['original_base64'], validate=True)
        cls.material = MaterialParser().parse(cls.old_material['name'], cls.payload)
        assert cls.material['status'] == '已解析', cls.material.get('parse_runtime')
        cls.material['original_base64'] = cls.old_material['original_base64']
        cls.record['snapshot']['material'] = cls.material
        digest = fingerprint(cls.record['snapshot'])
        cls.record['hash'] = cls.record['snapshot_hash'] = digest
        # A clearly synthetic acquisition fixture for the new parsed snapshot.
        # Never save it, resume this study or count it as a new live download.
        # Actual live acceptance must obtain a fresh source and receipt.
        event = cls.task['ledger']['dispatches'][cls.record['acquisition']['dispatch_index']]
        _record_current_original(cls.task, cls.record, event)
        request = dict(artifact_id=cls.record['id'], snapshot_hash=digest,
            queries=['营业收入','净利润','经营活动产生的现金流量净额','企业会计准则','合并范围'],
            parts=[], reason='新解析版本的独立离线位置检查', question_ids=[q['id'] for q in cls.task['questions']])
        cls.bundle = read(cls.task, [request], binding(cls.task), [cls.record['id']], reader_version=3)
        cls.views = cls.bundle['views']
        cls.contract = evidence_contract(cls.task, cls.views)
        cls.schema = financial_reference_output_schema(cls.task, cls.views)

    @classmethod
    def tearDownClass(cls):
        assert RETAINED.read_bytes() == cls.original

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def wire(self):
        self.assertIsNotNone(self.schema)
        presented = financial_reference_prompt(dict(untrusted_sources=self.views,
            evidence_contract=self.contract), self.task)
        source = self.contract['sources'][0]
        q = next(q for q in self.task['questions'] if 'net_profit_margin_v1' in q.get('method_needs', []))
        links = [dict(question_id=q['id'], need_ids=[n['id'] for n in evidence_requirements(q)])]
        anchor = next(a['index'] for a in presented['untrusted_sources'][0]['observation_anchors']
                      if a['date'] == '2026-06-30')
        shared = {field:source['financial_shared_context_choices'][field][0]['candidate_id']
                  for field in ('entity_id','accounting_standard')}
        cells = []
        for metric in ('revenue','net_profit','operating_cash_flow'):
            cell = next(c for c in source['financial_context_cells']
                if c['context']['metric'] == metric and c['context']['accounting_scope'] == 'consolidated')
            cells.append(dict(id='coordinate_'+metric, object=source['object_binding']['value'],
                nature='已实现事实', question_ids=[q['id']], observation_anchor=anchor,
                financial_context_reference=cell['candidate_id'], shared_context_references=shared,
                review=dict(scope='current_original_descriptive_only',need_links=links)))
        return dict(version=self.schema['properties']['version']['enum'][0],
            source_reference=presented['financial_reference_binding'],current_version_only=True,
            financial_cells=cells,text_quotes=[])

    def proposals(self):
        result = decode_financial_references(self.wire(), self.schema, (self.task,self.views))
        return expand_financial_value_references(self.task,result['evidence_specs'],self.views)

    def test_actual_full_pdf_has_coordinate_tables_and_keeps_every_prose_page(self):
        self.assertEqual(self.material['extract_version'],'pdf-tables-3')
        old_plain = [p for p in self.old_material['parts'] if 'pdf_table' not in p]
        new_plain = [p for p in self.material['parts'] if 'pdf_table' not in p]
        self.assertEqual(new_plain,old_plain)
        self.assertTrue(self.material['parse_runtime']['parser_worker_reaped'])
        self.assertTrue(any(p.get('pdf_table',{}).get('representation') == 'aligned_words'
                            for p in self.material['parts']))

    def test_three_current_consolidated_amounts_are_not_parent_amounts_or_note_numbers(self):
        expected = dict(revenue=49691932907.10,net_profit=23887522068.03,
                        operating_cash_flow=105619808843.44)
        specs = self.proposals()
        self.assertEqual({s['calculation_context']['metric']:s['value'] for s in specs},expected)
        self.assertTrue(all(s['calculation_context']['accounting_scope']=='consolidated' for s in specs))
        self.assertTrue(all(s['record_locator']['financial_column']['version']==3 for s in specs))

    def test_actual_source_read_and_context_gates_accept_explicit_fixture_selections(self):
        specs = self.proposals()
        validate_model_specs(self.task,specs)
        validate_read_specs(self.task,specs,self.bundle)
        for spec in specs:
            item = candidate_evidence(self.task,self.record,spec)
            reviewed = ResearchAnalyzer().assess(self.task,item,spec['review'],self.task['source_records'])
            self.assertEqual(reviewed['source_check'],'通过')
            self.assertEqual(reviewed['applicability_check'],'通过')
        self.assertTrue(all(s['publication']['at'] is None for s in specs))

    def test_comparative_year_cannot_borrow_the_current_year_duration(self):
        item = copy.deepcopy(self.proposals()[0])
        column = item['record_locator']['financial_column']
        column['column_index'] = 3
        item['raw_token'] = column['row_quote'].split('\t')[3]
        with self.assertRaises(ContractError):
            validate_financial_metric_number(item,self.record,
                dict(part=item['record_locator']['part'],quote=item['text']))

    def test_mutated_coordinates_cannot_pass_original_reproduction_after_rehash(self):
        forged = copy.deepcopy(self.material)
        part = next(p for p in forged['parts'] if p.get('pdf_table',{}).get('representation')=='aligned_words')
        cells = next(iter(part['pdf_table']['word_cells'].values()))
        next(w for cell in cells for w in cell)['x1'] += 2
        with self.assertRaises(ContractError):
            verified_material(forged,self.payload)

    def test_coordinates_stay_in_retained_source_not_the_model_context(self):
        for view in source_views(self.task):
            for part in view.get('parts',[]):
                if part.get('pdf_table',{}).get('representation')=='aligned_words':
                    self.assertNotIn('word_cells',part['pdf_table'])
        self.assertTrue(any('word_cells' in p.get('pdf_table',{}) for p in self.material['parts']))

    def test_previous_table_extraction_version_replays_all_original_parts(self):
        old = MaterialParser().parse(self.old_material['name'],self.payload,extract_version='pdf-tables-2')
        self.assertEqual(old['status'],'已解析')
        self.assertEqual(old['parts'],self.old_material['parts'])

    def test_aligned_words_cannot_be_declared_as_a_ruled_grid(self):
        item = copy.deepcopy(self.proposals()[0])
        item['record_locator']['financial_column']['version'] = 2
        with self.assertRaises(ContractError):
            validate_financial_metric_number(item,self.record,
                dict(part=item['record_locator']['part'],quote=item['text']))
