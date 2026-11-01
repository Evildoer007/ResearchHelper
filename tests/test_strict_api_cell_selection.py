"""Explicit price-cell selection retains context, never financial approval.

The retained failed study is read-only. Synthetic model returns exercise the
wire protocol; they are not a new live research acceptance.
"""
import copy
from decimal import Decimal
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError, fingerprint, validate_evidence
from researchhelper.evidence import api_cell_choices, candidate_evidence, expand_api_cell_selections
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import api_extraction_output_schema, decode_api_extraction_output
from researchhelper.reasoning import evidence_contract, extractable_source_views, model_object


RETAINED = ROOT / 'result/acceptance/one-sentence-research-bc8mz68b/task.json'


class StrictAPICellSelectionTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)
        self.task['api_cell_selection_encoding'] = 1
        self.views = extractable_source_views(self.task)
        self.source = self.task['source_records'][0]
        self.cells = api_cell_choices(self.task, self.source)
        self.assertEqual(len(self.cells), 4)
        self.review = dict(question_ids=['q1'], allowed_natures=['已实现事实'],
            conditions=['只核对当前取得的未复权价格，公司行为仍未知'],
            reason='选择原始位置并保留全部原件口径，不推断总收益',
            evidence_need_texts=[dict(question_id='q1', texts=[self.task['questions'][0]['evidence_needs'][0]])])
        self.wire = dict(version=2, evidence_specs=[dict(
            api_cell_selections=[cell['candidate_id'] for cell in self.cells],
            question_ids=['q1'], review=self.review)])
        guard = patch('socket.socket.connect', side_effect=AssertionError('No external calls'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def schema(self):
        return api_extraction_output_schema(self.task, self.views)

    def test_strict_container_uses_explicit_cells_without_retyping_context(self):
        schema = self.schema()
        self.assertEqual(schema['properties']['version']['enum'], [2])
        output = decode_api_extraction_output(self.wire, schema)
        self.assertEqual(output['evidence_specs'][0]['api_cell_selections'], self.wire['evidence_specs'][0]['api_cell_selections'])
        self.assertNotIn('calculation_context', output['evidence_specs'][0])

    def test_selected_cells_keep_every_registered_context_field_and_exact_original(self):
        before = fingerprint(self.task)
        output = decode_api_extraction_output(self.wire, self.schema())
        specs = expand_api_cell_selections(self.task, output['evidence_specs'])
        for cell, spec in zip(self.cells, specs):
            item = candidate_evidence(self.task, self.source, spec)
            self.assertEqual(item['value'], cell['value'])
            self.assertEqual(item['period'], cell['period'])
            self.assertEqual(item['calculation_context'], cell['calculation_context'])
            self.assertEqual(item['source_check'], '通过')
            self.assertEqual(item['applicability_check'], '待核验')
            self.assertEqual(item['calculation_context']['corporate_action_status'], 'unknown')
            ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
            validate_evidence(item, self.task)
        self.assertEqual(fingerprint(self.task), before)

    def test_legacy_tasks_retain_the_existing_flat_container(self):
        self.task.pop('api_cell_selection_encoding')
        self.assertEqual(self.schema()['properties']['version']['enum'], [1])

    def test_empty_group_cannot_become_successful_extraction(self):
        self.wire['evidence_specs'][0]['api_cell_selections'] = []
        with self.assertRaises(ContractError):
            decode_api_extraction_output(self.wire, self.schema())

    def test_normal_registered_calculation_accepts_selected_and_reviewed_endpoints(self):
        decoded = decode_api_extraction_output(self.wire, self.schema())
        specs = expand_api_cell_selections(self.task, decoded['evidence_specs'])
        self.task['evidence'] = []
        self.task['calculations'] = []
        for spec in specs:
            item = candidate_evidence(self.task, self.source, spec)
            ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
            validate_evidence(item, self.task)
            self.task['evidence'].append(item)
        calculation = copy.deepcopy(self.task['calculation_specs'][0])
        calculation['inputs'] = dict(start_price=self.cells[0]['candidate_id'], end_price=self.cells[-1]['candidate_id'])
        with tempfile.TemporaryDirectory(prefix='selected-price-calculation-', dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True)
            try:
                agent._calculate_spec(self.task, calculation)
            finally:
                agent.close_runtime()
        self.assertEqual(len(self.task['calculations']), 1)
        actual = self.task['calculations'][0]
        expected = (Decimal(str(self.cells[-1]['value'])) / Decimal(str(self.cells[0]['value'])) - 1) * 100
        self.assertLessEqual(abs(Decimal(str(actual['output']['value'])) - expected), Decimal('1e-10'))
        self.assertEqual(actual['input_bindings'], calculation['inputs'])

    def test_foreign_cell_and_numeric_or_approval_injection_are_rejected(self):
        for field, value in (('api_cell_selections', ['rh_cell_foreign']),
                             ('value', 999), ('source_check', '通过'),
                             ('calculation_context', {'currency': 'CNY'})):
            wire = copy.deepcopy(self.wire)
            wire['evidence_specs'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                decode_api_extraction_output(wire, self.schema())

    def test_duplicate_cells_still_fail_normal_selection_validation(self):
        wire = copy.deepcopy(self.wire)
        wire['evidence_specs'][0]['api_cell_selections'][1] = wire['evidence_specs'][0]['api_cell_selections'][0]
        with self.assertRaises(ContractError):
            decoded = decode_api_extraction_output(wire, self.schema())
            expand_api_cell_selections(self.task, decoded['evidence_specs'])

    def test_changed_task_or_source_cannot_reuse_previously_selected_cells(self):
        decoded = decode_api_extraction_output(self.wire, self.schema())
        for mutation in ('task', 'version', 'value'):
            task = copy.deepcopy(self.task)
            if mutation == 'task': task['id'] = 'foreign_task'
            elif mutation == 'version': task['version'] += 1
            else: task['source_records'][0]['snapshot']['tables'][0]['table']['close'][0] += 1
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                expand_api_cell_selections(task, decoded['evidence_specs'])

    def test_incomplete_cell_catalogue_never_silently_drops_observations(self):
        contract = evidence_contract(self.task, self.views)
        contract['sources'][0]['cell_choices'].pop()
        with patch('researchhelper.reasoning.evidence_contract', return_value=contract):
            self.assertEqual(self.schema()['properties']['version']['enum'], [1])

    def test_historical_or_text_batches_do_not_enter_current_price_selection(self):
        self.task['research_mode'] = 'historical'
        self.assertIsNone(self.schema())
        self.task['research_mode'] = 'current'
        self.assertIsNone(api_extraction_output_schema(self.task, self.views + [dict(kind='material')]))

    def test_actual_model_container_selects_cells_and_preserves_normal_validators(self):
        with tempfile.TemporaryDirectory(prefix='strict-price-cells-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(Path(folder))
            settings.save(dict(endpoint='https://api.deepseek.com/beta/chat/completions',
                model='deepseek-flash', api_key='SYNTHETIC_PRICE_CELL_KEY', reasoning_effort='none'))
            model = ModelService(settings, offline=False)
            completion = dict(choices=[dict(index=0, finish_reason='tool_calls', message=dict(
                role='assistant', content=None, tool_calls=[dict(id='test_api', type='function', function=dict(
                    name='rh_submit_api_evidence_v1', arguments=json.dumps(self.wire)))]))])
            with patch.object(model, '_request_bytes', return_value=json.dumps(completion).encode()) as wire:
                output, receipt = model_object(model, self.task, 'extract', dict(
                    untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views)), {'evidence_specs'})
            request = json.loads(wire.call_args.args[1].data)
            self.assertEqual(request['tools'][0]['function']['parameters']['properties']['version']['enum'], [2])
            self.assertEqual(receipt['cell_selection_version'], 1)
            self.assertEqual(len(output['evidence_specs']), 4)
            self.assertTrue(all(spec['calculation_context'] for spec in output['evidence_specs']))
            self.assertNotIn('source_check', output['evidence_specs'][0])

