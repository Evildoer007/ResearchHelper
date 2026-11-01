"""Real extraction orchestration with a local model-transport double only.

Original response, wire decoding, source validation and suitability review use
the ordinary implementation. No prior study is resumed and no API is called.
"""
import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, validate_evidence
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.model import ModelService, ModelSettings
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import extractable_source_views, validate_model_specs

_prefix = __package__+'.' if __package__ else ''
fixture = importlib.import_module(_prefix+'test_economic_observations')
delivery = importlib.import_module(_prefix+'test_economic_observation_delivery')


class EconomicObservationEngineTests(unittest.TestCase):
    def setUp(self):
        helper = fixture.EconomicObservationTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.addCleanup(helper.tearDown)
        self.task = helper.task
        self.task.update(evidence=[], host_result=dict(evidence_specs=[]), reasoning_state={})
        # The raw-data diagnostic reserved its only call for delivery. This
        # disposable transport replay has one extraction slot plus that reserve.
        self.task['ledger']['limits']['model_calls'] = 2
        self.task['api_cell_selection_encoding'] = 1
        self.task['input_hash'] = input_fingerprint(self.task)
        self.source = helper.record
        self.folder = tempfile.TemporaryDirectory(prefix='economic-protocol-', dir=fixture.ROOT/'result/acceptance')
        self.addCleanup(self.folder.cleanup)
        settings = ModelSettings(Path(self.folder.name))
        settings.save(dict(endpoint='https://api.deepseek.com/beta/chat/completions', model='deepseek-flash',
            api_key='SYNTHETIC_ECONOMIC_PROTOCOL_ONLY', reasoning_effort='none'))
        self.model = ModelService(settings, offline=False)
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent.model = self.model
        self.agent.knowledge = ResearchKnowledge()
        self.agent._active = Mock()
        self.agent._save_model_checkpoint = Mock()

    def test_actual_model_container_and_engine_adopt_nine_real_observations(self):
        wire, _ = delivery.wire_selection(self.task, extractable_source_views(self.task))
        completion = dict(choices=[dict(index=0, finish_reason='tool_calls', message=dict(
            role='assistant', content=None, tool_calls=[dict(id='synthetic_edb', type='function', function=dict(
                name='rh_submit_api_evidence_v1', arguments=json.dumps(wire)))]))])
        with patch.object(self.model, '_request_bytes', return_value=json.dumps(completion).encode()) as transport:
            result = self.agent._extract_model_sources(self.task)
            self.assertEqual(transport.call_count, 1)
            self.assertEqual(len(result['evidence_specs']), 9)
            self.assertEqual(self.agent._extract_model_sources(self.task), result)
            self.assertEqual(transport.call_count, 1, 'Unchanged sources reuse the extraction checkpoint')
        self.assertNotIn('rejected_extraction', self.task['reasoning_state'])
        for spec in result['evidence_specs']:
            self.assertNotIn('field_definition', spec)
            item = candidate_evidence(self.task, self.source, spec)
            ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
            validate_evidence(item, self.task)
            self.assertEqual(item['applicability_check'], '通过')
        self.assertFalse(self.task['evidence'], 'Extraction never self-adopts a financial fact')

    def test_model_cannot_supply_internal_field_definition_or_approval(self):
        helper = fixture.EconomicObservationTests()
        helper.task, helper.record = self.task, self.source
        spec = helper.proposal()
        for field, value in (('field_definition', {}), ('source_check', '通过')):
            with self.assertRaises(ContractError):
                validate_model_specs(self.task, [dict(spec, **{field:value})], schema_only=True)
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, self.source, dict(spec, field_definition={}))

    def test_new_policy_changes_input_identity_without_upgrading_old_task(self):
        original = copy.deepcopy(self.task)
        current = input_fingerprint(self.task)
        self.task.pop('economic_observation_version')
        self.assertNotEqual(input_fingerprint(self.task), current)
        self.assertNotIn(self.source['id'], {v['id'] for v in extractable_source_views(self.task)})
        self.assertEqual(original['economic_observation_version'], 1)
