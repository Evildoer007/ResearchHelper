"""Three full official originals through the wire contracts using offline fixtures."""
import copy
from functools import lru_cache
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector
from researchhelper.model import ModelSettings, ModelService
from researchhelper.official_sources import release_choices
from researchhelper.output_schema import (original_span_output_schema, original_span_prompt,
    decode_original_spans, _original_number_tokens)
from researchhelper.financial_sources import original_line_index
from researchhelper.planning import input_fingerprint, question_specs
from researchhelper.reasoning import (evidence_contract, extractable_source_views,
    model_object, PHASE_FIELDS, MAX_CONTEXT_CHARACTERS)

@lru_cache(maxsize=1)
def original_capacity_fixture():
    """Register complete, distinct HTML originals through the real adapter.

    Keep the older wire policies these regression tests exercise. The sample
    uses simulated transport and needs no past research or credentials.
    """
    question = '核对三份合成采购经理指数材料中的读数和变化'
    questions = [
        dict(id='q1', question='本期制造业PMI是什么', kind='fact',
             evidence_needs=['本期制造业PMI读数', '明确统计月份']),
        dict(id='q2', question='如何区分读数和变动', kind='mechanism',
             evidence_needs=['相较上期的变动', '分项读数']),
    ]
    publications = {}
    for index, category in enumerate(('制造业', '非制造业', '综合'), 1):
        url = f'https://www.stats.gov.cn/sj/zxfbhjd/202609/t20260930_{8000+index}.html'
        title = f'2026年9月份{category}合成统计原件'
        paragraphs = [f'<h1>{title}</h1>',
                      f'<p>合成测试读数：{category}采购经理指数为50.{index}%，比上月上升0.2个百分点。</p>']
        for row in range(88):
            digits = [f'{41+(column+row)%18}.{column%10}%' for column in range(12)]
            paragraphs.append(f'<p>合成序列样本{row+1}，分组读数依次为'+'、'.join(digits)+'。</p>')
        publications[url] = (title, ('<html><body>'+''.join(paragraphs)+'</body></html>').encode())
    catalogue = ''.join(f'<a href="{url}">{title}</a>' for url, (title, _) in publications.items()).encode()

    def response(task, url, started, timeout, event, **kwargs):
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
        return catalogue if url.endswith('/zxfbhjd/') else publications[url][1]

    with tempfile.TemporaryDirectory(prefix='multi-original-fixture-', dir=ROOT/'result/acceptance') as folder:
        agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
        try:
            task = agent.create(question, entry='宏观', intent='解释变化', objects=['中国制造业PMI'],
                                research_mode='current', questions=questions)
            policies = {'official_channels_version', 'statistical_period_version',
                        'original_span_selection_version', 'original_number_reference_version',
                        'general_original_selection_version', 'original_quantity_qualifier_version'}
            task = {key: value for key, value in task.items() if not key.endswith('_version') or key in policies}
            task['input_hash'] = input_fingerprint(task)
            collector = EvidenceCollector(offline=False)
            with patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline originals')), \
                 patch('researchhelper.source_transport.official_html', side_effect=response):
                task['source_records'].append(collector.external(task, 'official', 'release_catalog',
                    dict(channel='nbs_release', page='0'), {}))
                for choice in release_choices(task):
                    task['source_records'].append(collector.external(task, 'official', 'release_original',
                        {key: choice[key] for key in ('catalog_id', 'release_id')}, {}))
            return task
        finally:
            agent.close_runtime()


class MultiOriginalCapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protected = fingerprint(original_capacity_fixture())
        cls.task = copy.deepcopy(original_capacity_fixture())
        cls.task['general_original_selection_version'] = 2
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.views = extractable_source_views(cls.task)
        cls.payload = dict(question=cls.task['question'], intent=cls.task['intent'],
            objects=cls.task['objects'], cutoff=cls.task['cutoff'], questions=question_specs(cls.task),
            calculations=cls.task['calculation_specs'], untrusted_sources=cls.views,
            evidence_contract=evidence_contract(cls.task, cls.views), field_registry={}, source_reading=None)
        cls.payload['calculation_input_targets'] = copy.deepcopy(
            cls.payload['evidence_contract']['calculation_input_requirements']['proposal_requirements'])
        cls.schema = original_span_output_schema(cls.task, cls.views)
        cls.prompt = original_span_prompt(cls.payload, cls.task)

    @classmethod
    def tearDownClass(cls):
        if fingerprint(original_capacity_fixture()) != cls.protected:
            raise AssertionError('Original fixture changed')

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_all_three_originals_and_all_questions_fit_the_unchanged_limit(self):
        self.assertEqual(len(self.views), 3)
        self.assertIsNotNone(self.schema)
        self.assertLessEqual(len(canonical(self.prompt)) + len(canonical(self.schema)), MAX_CONTEXT_CHARACTERS)
        self.assertEqual(self.prompt['questions'], self.payload['questions'])
        self.assertEqual(self.prompt['calculations'], self.payload['calculations'])

    def test_every_character_line_token_and_extra_part_field_is_retained(self):
        for index, (view, encoded) in enumerate(zip(self.views, self.prompt['untrusted_sources']), 1):
            rows = [dict(zip(encoded['part_columns'], row)) for row in encoded['parts']]
            self.assertEqual(len(rows), len(view['parts']))
            restored = []
            for original, row in zip(view['parts'], rows):
                self.assertEqual(original['text'], ''.join(line[1] for line in row['indexed_lines']))
                for key in set(original) - {'text'}:
                    self.assertEqual(row[key], original[key])
                for line, _, tokens in row['indexed_lines']:
                    restored.extend((line, token) for _, token in tokens)
            expected = [(token['line'], token['token']) for token in
                        _original_number_tokens(original_line_index(view), index)]
            self.assertEqual(restored, expected)

    def test_each_short_requirement_reconstructs_the_original_text(self):
        before = self.payload['evidence_contract']['question_requirements']
        after = self.prompt['evidence_contract']['question_requirements']
        self.assertEqual(set(before), set(after))
        for qid, needs in after.items():
            self.assertEqual([need['text'] for need in needs], [need['text'] for need in before[qid]])
            self.assertEqual(len({need['id'] for need in needs}), len(needs))
            self.assertTrue(all(len(need['id']) < 20 for need in needs))

    def wire(self):
        view = self.prompt['untrusted_sources'][0]
        parts = [dict(zip(view['part_columns'], row)) for row in view['parts']]
        line, text, tokens = next(line for part in parts for line in part['indexed_lines']
            if '制造业采购经理指数' in line[1] and '50.1%' in line[1])
        reference = next(ref for ref, token in tokens if token == '50.1')
        anchor = next(row[0] for row in view['observation_anchors']
                      if row[1] == '2026-09' and row[2] == '标题1')
        qid = next(iter(self.prompt['evidence_contract']['question_requirements']))
        need = self.prompt['evidence_contract']['question_requirements'][qid][0]['id']
        item = dict(id='literal_pmi', source_ref='source_1', kind='number', object='中国制造业PMI',
            nature='已实现事实', question_ids=[qid], period='2026-09', observation_anchor=anchor,
            text_span=dict(start=line, end=line), number_ref=reference, unit='%',
            unit_span=dict(start=line, end=line), contexts=[],
            review=dict(scope='current_original_descriptive_only', need_links=[dict(question_id=qid, need_ids=[need])]))
        return dict(version=4, binding_ref=self.prompt['span_binding_ref'], selections=[item])

    def test_short_references_decode_exactly_without_approving_financial_use(self):
        result = decode_original_spans(self.wire(), self.schema, (self.task, self.views))['evidence_specs'][0]
        self.assertEqual(result['value'], 50.1)
        self.assertEqual(result['raw_token'], '50.1')
        self.assertEqual(result['observation_period'], dict(frequency='month', value='2026-09'))
        self.assertNotIn('observation_date', result)
        qid = result['question_ids'][0]
        self.assertEqual(result['review']['evidence_need_texts'][qid],
            [self.payload['evidence_contract']['question_requirements'][qid][0]['text']])
        self.assertNotIn('applicability_check', result)

    def test_foreign_source_number_or_missing_requirement_is_rejected(self):
        for field in ('number_ref', 'missing_number', 'need_ref', 'binding_ref'):
            wire = self.wire()
            if field == 'number_ref':
                wire['selections'][0]['number_ref'] = wire['selections'][0]['number_ref'].replace('n1_', 'n3_')
            elif field == 'missing_number':
                wire['selections'][0]['number_ref'] = 'n1_9999'
            elif field == 'need_ref':
                wire['selections'][0]['review']['need_links'][0]['need_ids'] = ['n99999']
            else:
                wire['binding_ref'] = 'foreign'
            with self.subTest(field=field), self.assertRaises(ContractError):
                decode_original_spans(wire, self.schema, (self.task, self.views))

    def test_version_one_keeps_its_original_schema_and_input_bytes(self):
        old = copy.deepcopy(self.task)
        old['general_original_selection_version'] = 1
        old['input_hash'] = input_fingerprint(old)
        before = fingerprint((old, self.payload))
        prompt = original_span_prompt(self.payload, old)
        self.assertGreater(len(canonical(prompt)) + len(canonical(original_span_output_schema(old, self.views))),
                           MAX_CONTEXT_CHARACTERS)
        self.assertNotIn('indexed_line_columns', prompt['untrusted_sources'][0])
        self.assertEqual(before, fingerprint((old, self.payload)))

    def test_single_dispatch_and_oversize_guard_keep_existing_budget(self):
        for oversize in (False, True):
            task, payload = copy.deepcopy(self.task), copy.deepcopy(self.payload)
            if oversize:
                payload['necessary_context'] = 'x' * MAX_CONTEXT_CHARACTERS
            with tempfile.TemporaryDirectory(prefix='multi-original-context-', dir=ROOT / 'result/acceptance') as folder:
                settings = ModelSettings(folder)
                settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash', api_key='OFFLINE_TEST'))
                model = ModelService(settings, offline=False)
                limits, used = copy.deepcopy(task['ledger']['limits']), copy.deepcopy(task['ledger']['used'])
                wire = dict(version=4, binding_ref=self.prompt['span_binding_ref'], selections=[])
                response = dict(object='response', status='completed', error=None, incomplete_details=None,
                    output=[dict(type='message', role='assistant', status='completed',
                        content=[dict(type='output_text', text=json.dumps(wire))])], usage={})
                with patch.object(model, '_request_bytes', return_value=json.dumps(response).encode()) as dispatch:
                    if oversize:
                        with self.assertRaises(ContractError):
                            model_object(model, task, 'extract', payload, PHASE_FIELDS['extract'])
                        dispatch.assert_not_called()
                    else:
                        model_object(model, task, 'extract', payload, PHASE_FIELDS['extract'])
                        dispatch.assert_called_once()
                self.assertEqual(task['ledger']['limits'], limits)
                self.assertEqual(task['ledger']['used']['data_requests'], used['data_requests'])
                self.assertEqual(task['ledger']['used']['model_calls'], used['model_calls'] + (not oversize))
