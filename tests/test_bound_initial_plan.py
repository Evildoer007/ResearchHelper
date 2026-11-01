"""An initial plan must carry the unchanged user question, not a paraphrase."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import planning_output_schema, decode_planning_output, planning_protocol
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose


POLICY = 'planning_main_binding_version'


class BoundInitialPlanTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='bound-initial-plan-'); self.addCleanup(folder.cleanup)
        self.folder = folder.name
        self.agent = ResearchAgent(ResearchArchive(self.folder), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('分析159915.SZ的收盘价与官方单位净值，计算偏离并生成报告。', entry='ETF')
        self.schema = planning_output_schema(self.task)
        main = self.task['questions'][0]
        self.wire = dict(version=3, main_question=dict(id=main['id'], question=main['question'], required=True,
            kind='comparison', group='q1', dependencies=[], evidence_needs=['同份额、币种和所属日的实际原值'],
            method_needs=['etf_daily_nav_premium_v1']), additional_questions=[], data_requests=[], calculations=[])

    def test_mandatory_main_fields_are_single_literal_values(self):
        self.assertEqual(self.schema['properties']['version']['enum'], [3])
        self.assertIn('main_question', self.schema['required'])
        for shape in self.schema['properties']['main_question']['anyOf']:
            self.assertEqual(shape['properties']['id']['enum'], [self.task['questions'][0]['id']])
            self.assertEqual(shape['properties']['question']['enum'], [self.task['question']])
            self.assertEqual(shape['properties']['required']['enum'], [True])

    def test_decode_only_repackages_the_explicit_unchanged_main_question(self):
        before = copy.deepcopy(self.wire)
        result = decode_planning_output(self.wire, self.schema)
        self.assertEqual(result['questions'], [self.wire['main_question']])
        self.assertEqual(self.wire, before)

    def test_rewrite_deletion_and_downgrade_are_rejected(self):
        for field, value in (('question', '仅解释净值'), ('required', False), ('id', 'q_other')):
            wire = copy.deepcopy(self.wire); wire['main_question'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError): decode_planning_output(wire, self.schema)
        wire = copy.deepcopy(self.wire); wire.pop('main_question')
        with self.assertRaises(ContractError): decode_planning_output(wire, self.schema)

    def test_duplicate_question_cannot_overwrite_the_main(self):
        wire = copy.deepcopy(self.wire); wire['additional_questions'] = [copy.deepcopy(wire['main_question'])]
        with self.assertRaisesRegex(ContractError, '重复'): decode_planning_output(wire, self.schema)

    def test_additional_questions_and_research_kind_remain_flexible(self):
        for kind in ('fact', 'comparison', 'mechanism', 'investment'):
            wire = copy.deepcopy(self.wire); wire['main_question']['kind'] = kind
            extra = dict(wire['main_question'], id='q2', group='other', question='独立补充问题', required=False)
            wire['additional_questions'] = [extra]
            result = decode_planning_output(wire, self.schema)
            self.assertEqual(len(result['questions']), 2)
            self.assertFalse(result['questions'][1]['required'])

    def test_explicit_and_retained_questions_do_not_upgrade(self):
        task = self.agent.create('解释ETF净值', questions=[dict(id='q1', question='解释ETF净值', kind='fact',
            answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
        self.assertEqual(planning_output_schema(task)['properties']['version']['enum'], [2])
        path = ROOT / 'result/acceptance/one-sentence-research-5ncnyvw1/task.json'
        before = path.read_bytes(); old = json.loads(before)
        self.assertEqual(fingerprint(planning_output_schema(old)), old['ledger']['dispatches'][0]['output_schema_hash'])
        self.assertEqual(path.read_bytes(), before)

    def test_new_policy_is_input_bound_and_frozen(self):
        self.assertEqual(self.task[POLICY], 1)
        old = dict(self.task); old.pop(POLICY)
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(self.task))
        task = self.agent.create('解释ETF净值', questions=[dict(id='q1', question='解释ETF净值', kind='fact',
            answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
        self.assertEqual(compose(self.agent.start(task['id']))[POLICY], 1)
        self.assertNotIn(POLICY, self.agent.create_case('mechanism'))

    def transport(self, responses):
        body = json.dumps(self.wire)
        if responses:
            reply = dict(object='response', status='completed', error=None, incomplete_details=None,
                output=[dict(type='message', role='assistant', status='completed', content=[dict(type='output_text', text=body)])], usage={})
        else:
            reply = dict(choices=[dict(finish_reason='tool_calls', message=dict(role='assistant', content=None,
                tool_calls=[dict(id='synthetic_bound_plan', type='function', function=dict(
                    name=planning_protocol(self.task)[1], arguments=body))]))], usage={})
        with tempfile.TemporaryDirectory(prefix='bound-plan-model-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses' if responses else
                'https://api.deepseek.com/beta/chat/completions', model='deepseek-flash', api_key='FAKE_PLAN_KEY'))
            model = ModelService(settings, offline=False)
            with patch.object(model, '_request_bytes', return_value=json.dumps(reply).encode()) as request:
                result = json.loads(model.reason_structured_plan(self.task, []))
            request.assert_called_once()
            self.assertEqual(result['questions'][0]['question'], self.task['question'])

    def test_actual_responses_transport_uses_the_bound_plan(self): self.transport(True)

    def test_actual_beta_transport_uses_the_bound_plan(self): self.transport(False)


if __name__ == '__main__':
    unittest.main()
