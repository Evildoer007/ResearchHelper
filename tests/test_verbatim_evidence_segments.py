"""Literal quotes over fresh synthetic originals, with both real evidence gates.

Only the public-source transport and semantic reviewer are simulated. The
scenario uses no retained result directory, supplier or real model call.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError, context, canonical, validate_evidence
from researchhelper.numeric_integrity import bind_numeric_statement, validate_numeric_statement
from researchhelper.output_schema import analysis_output_schema, decode_analysis_citations, _matches
from researchhelper.reasoning import analysis_contract, validate_analysis_output
from researchhelper.model import ModelService, ModelSettings
from researchhelper import argument_support


FLAG = 'verbatim_evidence_segments_version'


class VerbatimEvidenceSegmentTests(unittest.TestCase):
    def setUp(self):
        from tests.test_official_release_channel import CATALOG
        from tests.test_official_original_availability import BODY
        from researchhelper.evidence import EvidenceCollector, current_original_reference
        from researchhelper.official_sources import release_choices
        guard = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)
        folder = tempfile.TemporaryDirectory(prefix='rh-quote-fixture-')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        task = self.agent.create('核对合成样本指标发布原文，不作价格因果判断', entry='宏观', intent='解释变化',
            objects=['合成样本指标'], research_mode='current',
            questions=[dict(id='q1', question='原文记载了什么', kind='fact')])
        task['synthetic'] = True; task['messages'][0]['synthetic'] = True
        collector = EvidenceCollector(offline=False)
        def returned(task, url, started, timeout, event, **kwargs):
            # A simulated completed transport; never evidence of real access.
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return CATALOG if url.endswith('/zxfbhjd/') else BODY
        with patch('researchhelper.source_transport.official_html', side_effect=returned):
            catalogue = collector.external(task, 'official', 'release_catalog', dict(channel='nbs_release', page='0'), {})
            task['source_records'].append(catalogue)
            selected = release_choices(task)[0]
            original = collector.external(task, 'official', 'release_original',
                {key:selected[key] for key in ('catalog_id','release_id')}, {})
            task['source_records'].append(original)
        parts = original['snapshot']['parts']
        period = next(part for part in parts if '数据观测日期' in part['text'])
        quote = next(part for part in parts if '12.5%' in part['text'])
        spec = dict(id='sample_statement', artifact_id=original['id'], object='合成样本指标',
            record_locator=dict(part=quote['locator'], period_part=period['locator'], period_quote=period['text']),
            text=quote['text'], period='2026-09-30', nature='已实现事实', unit='不适用', published_at=None,
            availability=current_original_reference(task, original),
            publication=dict(at=None, basis_ref=None, precision='未知', timezone='未知', certainty='未知',
                basis='合成原件首发未知，只核对留存版本'), question_ids=['q1'],
            review=dict(question_ids=['q1'], allowed_natures=['已实现事实'], conditions=['只核对合成原件文字'],
                reason='流程测试，不批准金融判断', temporal_use='current_version'))
        self.agent.archive.save(task)
        self.task = self.agent.start(task['id'], dict(evidence_specs=[spec], answers={}))
        self.assertIsNotNone(self.task.get('delivery'), self.task.get('error'))
        self.assertEqual(len(self.task['evidence']), 1, self.task.get('rejected_evidence'))
        self.evidence = self.task['evidence'][0]
        validate_evidence(self.evidence, self.task)
        self.assertEqual(self.evidence['source_check'], '通过')
        self.assertEqual(self.evidence['applicability_check'], '通过')
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)
        self.question = self.task['questions'][0]['id']
        self.original = canonical(self.agent.archive.get(self.task['id']))

    def tearDown(self):
        self.assertEqual(canonical(self.agent.archive.get(self.task['id'])), self.original)

    def proposal(self):
        return dict(question_ids=[self.question], refs=[self.evidence['id']], calculation_refs=[],
            numeric_segments={'answer': [{'text': '声明原文：'}, {'quote_ref': self.evidence['id']},
                {'text': '。该段仅核对原文，不代表资产价格的实际因果。'}]})

    def wire(self):
        return dict(version=2, binding=context(self.task), answers=[dict(question_id=self.question,
            segments=[dict(text='声明原文：', refs=[], counter_refs=[]), {'quote_ref': self.evidence['id']}],
            knowledge_refs=[], provenance_refs=[])], theses=[])

    def test_new_formal_tasks_enable_quotes_without_upgrading_fixtures_or_legacy(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            self.assertEqual(agent.create('核对公开声明')[FLAG], 1)
            self.assertNotIn(FLAG, agent.create_case('mechanism'))
        task = copy.deepcopy(self.task)
        task.pop(FLAG)
        with self.assertRaises(ContractError):
            bind_numeric_statement(task, self.proposal(), ['answer'], [self.evidence['id']], [])

    def test_quote_is_generated_from_exact_adopted_evidence_not_model_text(self):
        result = bind_numeric_statement(self.task, self.proposal(), ['answer'], [self.evidence['id']], [])
        self.assertIn('“' + self.evidence['text'] + '”', result['fields']['answer'])
        binding = result['record']['fields']['answer']['bindings'][0]
        self.assertEqual(binding['projection'], 'verbatim_evidence')
        self.assertEqual(binding['ref'], self.evidence['id'])
        self.assertNotIn('canonical_value', binding)
        self.assertNotIn('value', self.evidence)
        proposal = dict(self.proposal(), numeric_integrity=result['record'])
        validate_numeric_statement(self.task, proposal, ['answer'], [self.evidence['id']], [])

    def test_quote_cannot_supply_free_text_new_number_or_format(self):
        for changed in ({'quote_ref': self.evidence['id'], 'text': '3.5%'},
                        {'quote_ref': self.evidence['id'], 'format': 'decimal'},
                        {'quote_ref': 'not_retained'}):
            proposal = self.proposal()
            proposal['numeric_segments']['answer'][1] = changed
            with self.assertRaises(ContractError):
                bind_numeric_statement(self.task, proposal, ['answer'], [self.evidence['id']], [])

    def test_free_numeric_prose_still_fails_even_beside_a_valid_quote(self):
        proposal = self.proposal()
        proposal['numeric_segments']['answer'].append({'text': '目标区间为3.5%至3.75%。'})
        with self.assertRaises(ContractError):
            bind_numeric_statement(self.task, proposal, ['answer'], [self.evidence['id']], [])

    def test_unlisted_cross_task_unapproved_or_rewritten_evidence_is_rejected(self):
        with self.assertRaises(ContractError):
            bind_numeric_statement(self.task, self.proposal(), ['answer'], [], [])
        for field, value in (('task_id', 'foreign'), ('research_version', 999),
                             ('source_check', '待核验'), ('text', 'A different policy at 9%.')):
            task = copy.deepcopy(self.task)
            next(item for item in task['evidence'] if item['id'] == self.evidence['id'])[field] = value
            with self.assertRaises(ContractError):
                bind_numeric_statement(task, self.proposal(), ['answer'], [self.evidence['id']], [])

    def test_strict_schema_and_decoder_preserve_explicit_source_without_numeric_path(self):
        schema = analysis_output_schema(self.task)
        self.assertTrue(_matches(self.wire(), schema))
        decoded = decode_analysis_citations(self.wire(), schema, self.task)
        answer = decoded['answers'][self.question]
        self.assertEqual(answer['refs'], [self.evidence['id']])
        self.assertEqual(answer['calculation_refs'], [])
        self.assertEqual(answer['numeric_segments']['answer'][1], {'quote_ref': self.evidence['id']})
        self.assertEqual(schema, analysis_output_schema(self.task, [self.question]))

    def test_legacy_schema_does_not_accept_quote_fields(self):
        self.task.pop(FLAG)
        self.assertFalse(_matches(self.wire(), analysis_output_schema(self.task)))

    def test_contract_offers_literal_quotation_separately_from_numeric_candidates(self):
        contract = analysis_contract(self.task)
        quote = contract['numeric_statement']['verbatim_evidence']
        self.assertTrue(quote['enabled'])
        self.assertIn(self.evidence['id'], quote['by_question'][self.question])
        self.assertFalse(quote['calculation_input_approval'])

    def test_quote_does_not_expose_a_calculation_value_or_gain_temporal_permission(self):
        wire = self.wire()
        wire['answers'][0]['segments'] = [dict(source='evidence', ref=self.evidence['id'],
            path='value', format='signed', digits=2)]
        self.assertFalse(_matches(wire, analysis_output_schema(self.task)))
        bind_numeric_statement(self.task, self.proposal(), ['answer'], [self.evidence['id']], [])
        self.assertIsNone(self.evidence['published_at'])
        self.assertFalse(self.evidence['availability']['event_causality_approved'])
        self.assertFalse(self.evidence['availability']['historical_availability_approved'])

    def test_unreviewed_quote_answer_is_not_adopted(self):
        task = copy.deepcopy(self.task)
        agent = ResearchAgent.__new__(ResearchAgent)
        proposal = dict(answers={self.question: self.proposal()}, theses=[])
        agent._answer_questions(task, [self.question], proposal, {})
        self.assertNotEqual(task['questions'][0]['status'], '已回答')

    def test_reviewed_quote_answer_renders_and_freezes_without_changing_source_words(self):
        from tests.test_argument_support_workflow import assessed
        from researchhelper.reports import compose, render
        from researchhelper.knowledge import ResearchKnowledge
        from unittest.mock import Mock
        task = copy.deepcopy(self.task)
        task['gaps'], task['gap_records'] = [], []
        refs = [item['id'] for item in task['evidence']]
        proposal = dict(answers={self.question: dict(refs=refs, calculation_refs=[],
            numeric_segments={'answer': [{'quote_ref': ref} for ref in refs]})}, theses=[])
        _, review = assessed(task, proposal)
        task['argument_support_reviews'] = [argument_support.make_record(task, proposal, review, actor='host')]
        agent = ResearchAgent.__new__(ResearchAgent)
        agent.knowledge = ResearchKnowledge()
        agent._save_model_checkpoint = Mock()
        agent._answer_questions(task, [self.question], proposal, {})
        self.assertEqual(task['questions'][0]['status'], '已回答', task['gaps'])
        content = compose(task)
        self.assertEqual(content[FLAG], 1)
        from html import unescape
        html = unescape(render(content, ROOT))
        self.assertIn(self.evidence['text'], html)
        self.assertIn('“' + self.evidence['text'] + '”', task['questions'][0]['answer'])
        self.assertEqual(canonical(self.agent.archive.get(self.task['id'])), self.original)

    def test_host_checkpoint_review_delivery_and_restart_preserve_literal_quote(self):
        from core.session import Session
        from tests.test_argument_support_workflow import assessed
        from researchhelper.reports import compose
        task = copy.deepcopy(self.task)
        for key in ('delivery','pending_content','pending_html','host_checkpoint'):
            task.pop(key, None)
        task.update(status='待开始', run_mode='host', revision=0)
        for node in task['nodes']:
            if node.get('kind') in ('answer','thesis') or node['id'] in ('compose','render'):
                node['status'] = '待执行'
        before = copy.deepcopy(task['ledger']['used'])
        with tempfile.TemporaryDirectory(prefix='rh-quote-project-') as project:
            session = Session(project)
            try:
                session.archive.save(task)
                proposal = dict(answers={self.question:self.proposal()}, theses=[])
                checkpoint = session.run(task['id'], evidence_specs=task['host_result']['evidence_specs'],
                    answers=proposal['answers'], defer_delivery=True)
                self.assertEqual(len(checkpoint['evidence']), 1, checkpoint.get('rejected_evidence'))
                self.assertIsNone(checkpoint.get('delivery'))
                self.assertEqual(checkpoint['status'], '待开始')
                descriptor = session.argument_review_inputs(task['id'])
                wire, _ = assessed(checkpoint, proposal)
                self.assertEqual(wire['review_hash'], descriptor['request']['review_hash'])
                session.review_arguments(task['id'], wire)
                delivered = session.deliver(task['id'])
                self.assertEqual(delivered['questions'][0]['status'], '已回答', delivered.get('gaps'))
                self.assertTrue(delivered.get('delivery'))
                self.assertEqual(delivered['status'], '已完成')
                self.assertEqual(delivered['gaps'], [])
                self.assertEqual(delivered['ledger']['used']['model_calls'], before['model_calls'])
                self.assertEqual(delivered['ledger']['used']['data_requests'], before['data_requests'])
                text = delivered['questions'][0]['answer']
                self.assertIn('“'+self.evidence['text']+'”', text)
                self.assertNotIn('value', delivered['evidence'][0])
                content = compose(delivered)
                digest = session.archive.report(task['id'], delivered['version'])['digest']
                self.assertEqual(content['evidence'][0]['text'], self.evidence['text'])
            finally:session.agent.close_runtime()
            restarted = Session(project)
            try:
                saved = restarted.status(task['id'])
                self.assertEqual(saved['questions'][0]['answer'], text)
                self.assertEqual(restarted.archive.report(task['id'], saved['version'])['digest'], digest)
                self.assertFalse(saved['evidence'][0]['availability']['historical_availability_approved'])
                self.assertFalse(saved['evidence'][0]['availability']['event_causality_approved'])
            finally:restarted.agent.close_runtime()

    def test_actual_model_transports_share_quote_schema_and_decoder(self):
        schema = analysis_output_schema(self.task)
        from researchhelper.output_schema import ANALYSIS_CITATION_FUNCTION
        for endpoint in ('https://api.deepseek.com/beta/chat/completions', 'https://api.deepseek.com/responses'):
            with self.subTest(endpoint=endpoint), tempfile.TemporaryDirectory() as folder:
                task = copy.deepcopy(self.task)
                settings = ModelSettings(Path(folder))
                settings.save(dict(endpoint=endpoint, model='deepseek-flash', api_key='FAKE_VERBATIM_KEY'))
                model = ModelService(settings, offline=False)
                if endpoint.endswith('responses'):
                    response = dict(object='response', status='completed', error=None, incomplete_details=None,
                        output=[dict(type='message', role='assistant', status='completed', content=[dict(
                            type='output_text', text=json.dumps(self.wire()))])], usage={})
                else:
                    response = dict(choices=[dict(finish_reason='tool_calls', message=dict(role='assistant', content=None,
                        tool_calls=[dict(id='quoted', type='function', function=dict(
                            name=ANALYSIS_CITATION_FUNCTION, arguments=json.dumps(self.wire())))]))], usage={})
                with patch.object(model, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                    decoded = json.loads(model.reason_structured_phase(task, [], 'analyze', schema))
                transport.assert_called_once()
                self.assertEqual(decoded['answers'][self.question]['refs'], [self.evidence['id']])
                validate_analysis_output(model, task, decoded, len(task['ledger']['dispatches']) - 1)

    def test_actual_prompt_uses_new_quote_grammar_without_conflicting_legacy_rule(self):
        from researchhelper.reasoning import model_object, PHASE_FIELDS, ANALYSIS_SEGMENT_SHAPE_RULE
        class Host:
            def reason(self, task, messages):
                self.messages = messages
                return '{"answers":{},"theses":[]}'
        host = Host()
        _, receipt = model_object(host, self.task, 'analyze',
            {'analysis_contract': analysis_contract(self.task)}, PHASE_FIELDS['analyze'])
        self.assertIn('quote_ref', host.messages[0]['content'])
        self.assertNotIn(ANALYSIS_SEGMENT_SHAPE_RULE, host.messages[0]['content'])
        self.assertEqual(receipt[FLAG], 1)
        task = copy.deepcopy(self.task)
        task.pop(FLAG)
        model_object(host, task, 'analyze', {'analysis_contract': analysis_contract(task)}, PHASE_FIELDS['analyze'])
        self.assertIn(ANALYSIS_SEGMENT_SHAPE_RULE, host.messages[0]['content'])
