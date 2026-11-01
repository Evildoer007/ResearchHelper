"""Versioned identity navigation on actual R121 originals, not identity approval."""
import copy
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.source_reader import (read, reader_version_for, validate_reading, CONTEXT_LIMIT,
    _identity_context_groups, _try_read_expansion)
from researchhelper.financial_sources import financial_table_candidates, financial_amount_tables
from researchhelper.output_schema import financial_reference_output_schema, financial_reference_prompt, decode_financial_references
from researchhelper.reasoning import evidence_contract, validate_model_specs
from researchhelper.source_reader import validate_read_specs
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.data_requirements import pending

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('RESEARCHHELPER_AMOUNT_NEEDS_INPUT', str(
    ROOT/'result/acceptance/one-sentence-research-itcqfraw/task.json')))


class FinancialIdentityContextReadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SOURCE.read_bytes(); cls.original = json.loads(cls.raw)

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Retained original only'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task = copy.deepcopy(self.original)
        self.old = self.task['reasoning_state']['reading']['bundle']

    def tearDown(self):
        self.assertEqual(SOURCE.read_bytes(), self.raw)

    def current(self):
        # Explicit forensic read: do not change the closed plan/policy/ledger.
        return read(self.task, self.old['selection'], self.old['binding'], self.old['artifact_ids'], reader_version=7)

    def test_current_read_contains_actual_identity_fields_within_the_shared_limit(self):
        before = fingerprint(self.task)
        bundle = self.current()
        text = '\n'.join(part['text'] for view in bundle['views'] for part in view['parts'])
        for term in ('300750', '宁德时代', '深圳证券交易所'):
            self.assertIn(term, text)
        self.assertLessEqual(len(canonical(bundle['views'])), CONTEXT_LIMIT)
        self.assertEqual(fingerprint(self.task), before)
        trace = bundle['coverage'][0]['identity_context_reading']
        self.assertFalse(trace['financial_approval'] or trace['identity_approval'] or trace['absence_is_proof'])
        self.assertTrue(any(group['read_in_full'] for group in trace['groups']))
        validate_reading(self.task, bundle)

    def test_financial_inputs_and_cashflow_adjustments_survive_identity_navigation(self):
        bundle = self.current(); view = bundle['views'][0]
        source = next(record for record in self.task['source_records'] if record['id'] == view['id'])
        typed = financial_table_candidates(self.task, source, view)['cells']
        self.assertTrue({'revenue', 'net_profit', 'operating_cash_flow'}.issubset({cell['metric'] for cell in typed}))
        raw = financial_amount_tables(self.task, source, view)
        rows = [row['text'] for table in raw['tables'] for row in table['rows'].values()]
        for term in ('固定资产折旧', '存货的减少', '经营性应收', '经营性应付'):
            self.assertTrue(any(term in row for row in rows), term)
        self.assertFalse(self.task['evidence'] or self.task['calculations'] or self.task['theses'])

    def test_actual_identity_rows_pass_normal_evidence_gates_without_approving_listing_or_answer(self):
        ledger = copy.deepcopy(self.task['ledger'])
        identities = copy.deepcopy(self.task['identity_resolutions'])
        bundle = self.current(); views = bundle['views']
        contract = evidence_contract(self.task, views)
        prompt = financial_reference_prompt(dict(untrusted_sources=views, evidence_contract=contract), self.task)
        schema = financial_reference_output_schema(self.task, views)
        quotes = [choice for part in prompt['untrusted_sources'][0]['parts'] for choice in part['quote_choices']]
        selected = [next(choice for choice in quotes if term in choice[1]) for term in ('300750', '深圳证券交易所')]
        observation = next(item['reference'] for item in prompt['untrusted_sources'][0]['observation_choices']
                           if item['date'] == '2026-06-30')
        question = next(question for question in self.task['questions'] if question['id'] == 'q2')
        need = question['evidence_needs'][0]
        wire = dict(version=9, source_reference=prompt['financial_reference_binding'], current_version_only=True,
            financial_cells=[], amount_cells=[], text_quotes=[dict(id='identity_control_'+str(index),
                object=contract['sources'][0]['object_binding']['value'], nature='已实现事实',
                quote_reference=choice[0], observation_reference=observation, period_reference='observation_date',
                review=dict(scope='current_original_descriptive_only', need_links={qid:
                    ([fingerprint(need)] if qid == 'q2' else None) for qid in contract['question_ids']}))
                for index, choice in enumerate(selected)])
        specs = decode_financial_references(wire, schema, (self.task, views))['evidence_specs']
        validate_model_specs(self.task, specs); validate_read_specs(self.task, specs, bundle)
        analyzer = ResearchAnalyzer()
        source = next(record for record in self.task['source_records'] if record['id'] == views[0]['id'])
        for spec in specs:
            item = analyzer.assess(self.task, candidate_evidence(self.task, source, spec), spec['review'], self.task['source_records'])
            self.assertEqual((item['source_check'], item['applicability_check']), ('通过', '通过'))
            self.task['evidence'].append(item)
        self.assertFalse(any(item['question_id'] == 'q2' for item in pending(self.task)))
        self.assertEqual(self.task['ledger'], ledger)
        self.assertEqual(self.task['identity_resolutions'], identities)
        self.assertNotEqual(question['status'], '已回答')
        self.assertEqual(self.task['status'], '部分完成')

    def test_legacy_reading_is_still_exact_and_only_new_financial_policy_defaults_to_seven(self):
        self.assertEqual(reader_version_for(self.task), self.old['reader_version'])
        replay = read(self.task, self.old['selection'], self.old['binding'], self.old['artifact_ids'])
        self.assertEqual(replay, self.old)
        proposed = copy.deepcopy(self.task); proposed['financial_input_reading_priority_version'] = 3
        self.assertEqual(reader_version_for(proposed), 7)
        unrelated = dict(questions=[], calculation_specs=[], financial_input_reading_priority_version=3)
        self.assertNotEqual(reader_version_for(unrelated), 7)

    def test_identity_reading_is_not_a_general_or_unversioned_upgrade(self):
        task = copy.deepcopy(self.task)
        for question in task['questions']: question['method_needs'] = []
        task['calculation_specs'] = []
        with self.assertRaises(ContractError):
            read(task, self.old['selection'], self.old['binding'], self.old['artifact_ids'], reader_version=7)
        with self.assertRaises(ContractError):
            read(self.task, self.old['selection'], self.old['binding'], self.old['artifact_ids'], reader_version=8)

    def test_literal_spacing_and_repeated_page_representations_keep_two_exact_bounded_contexts(self):
        text = '前文'*4000+'股 票 代 码\t300750'+'后文'*4000
        parts = [dict(locator='第1页', text=text), dict(locator='第1页表1', text='股票代码\t300750'),
                 dict(locator='第2页', text='股票简称\t原文名称'), dict(locator='第3页', text='基金代码\t510300')]
        lookup = {part['locator']: part['text'] for part in parts}
        groups = _identity_context_groups(parts, lookup, {}, {})
        self.assertEqual(len(groups), 2)
        self.assertEqual([list(group['spans']) for group in groups], [['第1页'], ['第2页']])
        start, end = groups[0]['spans']['第1页'][0]
        self.assertLessEqual(end-start, 1200)
        self.assertIn('股 票 代 码\t300750', text[start:end])

    def test_overlarge_identity_expansion_never_removes_existing_financial_spans(self):
        original_parts = [dict(locator='financial', text='123')]
        item = dict(lookup=dict(financial='123', large='股票代码'+('x'*CONTEXT_LIMIT)),
                    intervals=dict(financial=[(0, 3)]), view=dict(parts=copy.deepcopy(original_parts)), table_metadata={})
        self.assertFalse(_try_read_expansion(item, {'large': [(0, len(item['lookup']['large']))]}, [item], []))
        self.assertEqual(item['view']['parts'], original_parts)
        self.assertEqual(item['intervals']['financial'], [(0, 3)])


if __name__ == '__main__': unittest.main()
