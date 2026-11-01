"""Monthly statistical observations on copies; no closed study is resumed."""
import copy
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError, canonical
from researchhelper.evidence import candidate_evidence, evidence_binding, validate_current_snapshot
from researchhelper.financial_sources import original_line_index
from researchhelper.model import ModelSettings, ModelService
from researchhelper.official_sources import statistical_period_anchors
from researchhelper.output_schema import original_span_output_schema, original_span_prompt, decode_original_spans
from researchhelper.planning import input_fingerprint, question_specs
from researchhelper.reasoning import evidence_contract, extractable_source_views, model_object, PHASE_FIELDS

RETAINED = ROOT / 'result/acceptance/one-sentence-research-j2jqn2i2/task.json'


class MonthlyOriginalPeriodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = RETAINED.read_bytes()
        cls.saved = json.loads(cls.original)

    @classmethod
    def tearDownClass(cls):
        if RETAINED.read_bytes() != cls.original:
            raise AssertionError('Original macro research changed')

    def setUp(self):
        self.task = copy.deepcopy(self.saved)
        self.task.update(general_original_selection_version=1, statistical_period_version=1)
        self.task['input_hash'] = input_fingerprint(self.task)
        self.views = extractable_source_views(self.task)
        self.record = next(record for record in self.task['source_records']
                           if record['id'] == self.views[0]['id'])
        self.schema = original_span_output_schema(self.task, self.views)
        self.payload = dict(question=self.task['question'], intent=self.task['intent'],
            objects=self.task['objects'], cutoff=self.task['cutoff'], questions=question_specs(self.task),
            calculations=[], field_registry={}, untrusted_sources=self.views,
            evidence_contract=evidence_contract(self.task, self.views))
        self.prompt = original_span_prompt(self.payload, self.task)
        view = self.prompt['untrusted_sources'][0]
        self.anchor = next(row[0] for row in view['observation_anchors']
            if row[1] == '2026-09' and row[2] == '标题1')
        lines = original_line_index(self.views[0])
        self.line = next(line for line in lines if '制造业采购经理指数' in line['text'] and '50.1%' in line['text'])
        parts = [dict(zip(view['part_columns'], row)) for row in view['parts']]
        self.number = next(row[0] for part in parts for row in part['number_tokens']
            if row[1] == self.line['index'] and row[2] == '50.1')
        need = self.payload['evidence_contract']['question_requirements']['q2'][0]['id']
        self.item = dict(id='pmi_literal_cell', source_ref='source_1', kind='number',
            object='中国制造业PMI', nature='已实现事实', question_ids=['q2'], period='2026-09',
            observation_anchor=self.anchor, text_span=dict(start=self.line['index'], end=self.line['index']),
            number_ref=self.number, unit='%', unit_span=dict(start=self.line['index'], end=self.line['index']),
            contexts=[], review=dict(scope='current_original_descriptive_only',
                need_links=[dict(question_id='q2', need_ids=[need])]))
        self.wire = dict(version=4, binding_ref=self.prompt['span_binding_ref'], selections=[self.item])
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls'))
        guard.start()
        self.addCleanup(guard.stop)

    def spec(self):
        return decode_original_spans(self.wire, self.schema, (self.task, self.views))['evidence_specs'][0]

    def test_month_remains_month_without_a_fabricated_day_or_publication(self):
        spec = self.spec()
        self.assertEqual(spec['observation_period'], dict(frequency='month', value='2026-09'))
        self.assertEqual(spec['period'], '2026-09')
        self.assertNotIn('observation_date', spec)
        self.assertIsNone(spec['published_at'])
        self.assertEqual(spec['raw_token'], '50.1')
        self.assertEqual(spec['value'], 50.1)
        evidence = candidate_evidence(self.task, self.record, spec)
        self.assertEqual(evidence['source_check'], '通过')
        self.assertEqual(evidence['applicability_check'], '待核验')
        self.assertTrue(validate_current_snapshot(evidence, self.task))

    def test_publication_day_and_current_or_future_month_do_not_become_periods(self):
        anchors = statistical_period_anchors(self.task, self.record)
        self.assertTrue(anchors)
        self.assertTrue(all(anchor['observation_period']['value'] < '2026-10' for anchor in anchors))
        self.assertFalse(any('/' in anchor['quote'] or '日' in anchor['quote'] for anchor in anchors))
        for value in ('2026-10', '2026-11'):
            spec = self.spec()
            spec.update(period=value, observation_period=dict(frequency='month', value=value))
            with self.subTest(month=value), self.assertRaises(ContractError):
                candidate_evidence(self.task, self.record, spec)

    def test_legacy_historical_or_non_nbs_sources_cannot_gain_monthly_approval(self):
        spec = self.spec()
        for changes in ({'statistical_period_version': None}, {'statistical_period_version': True},
                {'research_mode': 'historical'}, {'id': 'foreign'}, {'version': 2}):
            task = dict(self.task, **changes)
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                candidate_evidence(task, self.record, spec)
        record = copy.deepcopy(self.record)
        record['snapshot']['official_origin']['url'] = 'https://www.federalreserve.gov/newsevents/pressreleases/monetary20260930a.htm'
        self.assertEqual(statistical_period_anchors(self.task, record), [])

    def test_day_or_wrong_month_cannot_be_added_to_a_monthly_observation(self):
        for changes in ({'observation_date': '2026-09-30'}, {'period': '2026-09-30'},
                {'published_at': '2026-09-30'},
                {'observation_period': dict(frequency='month', value='2026-09', day='30')},
                {'observation_period': dict(frequency='day', value='2026-09')},
                {'observation_period': dict(frequency='month', value='2026-08')}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                candidate_evidence(self.task, self.record, dict(self.spec(), **changes))

    def test_observation_anchor_and_proposed_period_must_agree(self):
        wire = copy.deepcopy(self.wire)
        wire['selections'][0]['period'] = '2026-08'
        with self.assertRaises(ContractError):
            decode_original_spans(wire, self.schema, (self.task, self.views))

    def test_wrong_or_mixed_table_month_is_rejected_before_adoption(self):
        spec = self.spec()
        spec['text'] = '2026年8月50.1%'
        with self.assertRaisesRegex(ContractError, '其他或多个统计月份'):
            candidate_evidence(self.task, self.record, spec)
        spec['text'] = '2026年9月50.1%，2026年8月49.8%'
        with self.assertRaisesRegex(ContractError, '其他或多个统计月份'):
            candidate_evidence(self.task, self.record, spec)

    def test_period_reference_must_be_an_actual_retained_fragment(self):
        spec = self.spec()
        spec['record_locator']['period_quote'] = '2026年09月'
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, self.record, spec)
        self.assertEqual(statistical_period_anchors(self.task, self.record,
            [dict(locator='标题1', text='伪造的2026年9月')]), [])

    def test_monthly_precision_is_part_of_the_evidence_binding(self):
        spec = self.spec()
        changed = copy.deepcopy(spec)
        changed['observation_period']['frequency'] = 'day'
        self.assertNotEqual(evidence_binding(spec), evidence_binding(changed))

    def test_other_table_month_cannot_relabel_a_current_period_paragraph(self):
        anchor = next(anchor for anchor in statistical_period_anchors(self.task, self.record)
                      if anchor['observation_period']['value'] == '2026-08')
        spec = self.spec()
        spec.update(period='2026-08', observation_period=copy.deepcopy(anchor['observation_period']))
        spec['record_locator'].update(period_part=anchor['part'], period_quote=anchor['quote'])
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, self.record, spec)

    def test_short_month_label_cannot_contradict_the_selected_statistical_month(self):
        spec = self.spec()
        spec['text'] = '8月份，制造业采购经理指数为50.1%'
        with self.assertRaisesRegex(ContractError, '月份'):
            candidate_evidence(self.task, self.record, spec)

    def test_actual_strict_transport_decodes_once_without_data_or_budget_expansion(self):
        with tempfile.TemporaryDirectory(prefix='monthly-transport-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash', api_key='FAKE_OFFLINE_KEY'))
            model = ModelService(settings, offline=False)
            reply = dict(object='response', status='completed', error=None, incomplete_details=None,
                output=[dict(type='message', role='assistant', status='completed',
                    content=[dict(type='output_text', text=json.dumps(self.wire))])], usage={})
            limits, used = copy.deepcopy(self.task['ledger']['limits']), copy.deepcopy(self.task['ledger']['used'])
            with patch.object(model, '_request_bytes', return_value=json.dumps(reply).encode()) as request:
                result, _ = model_object(model, self.task, 'extract', self.payload, PHASE_FIELDS['extract'])
            request.assert_called_once()
            self.assertEqual(result['evidence_specs'][0]['observation_period']['value'], '2026-09')
            self.assertEqual(self.task['ledger']['limits'], limits)
            self.assertEqual(self.task['ledger']['used']['model_calls'], used['model_calls'] + 1)
            self.assertEqual(self.task['ledger']['used']['data_requests'], used['data_requests'])
