"""Acquisition only proposes existing source tools; it never approves facts."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.contracts import budget, ContractError
from researchhelper.model import ModelSettings, ModelService
from researchhelper.reasoning import PHASE_FIELDS, PHASE_RULES, phase_output_example
from researchhelper.evidence import data_request_contract, validate_ifind_identity_request
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent


class AcquisitionPhaseContractTests(unittest.TestCase):
    def test_fields_and_example_are_navigation_only(self):
        self.assertEqual(PHASE_FIELDS['acquire'], {'data_requests', 'stop_reason'})
        self.assertEqual(phase_output_example('acquire'), {'data_requests': [], 'stop_reason': ''})
        self.assertIn('不要求callable_codes非空', PHASE_RULES['acquire'])
        self.assertIn('不改问题', PHASE_RULES['acquire'])

    def test_trusted_navigation_inherits_planning_not_analysis_effort(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = ModelSettings(Path(temporary))
            settings.save({'endpoint': 'https://api.deepseek.com', 'model': 'deepseek-flash',
                'api_key': 'SYNTHETIC_ACQUISITION_KEY', 'planning_effort': 'none', 'reasoning_effort': 'low'})
            service = ModelService(settings, offline=False)
            task = {'id': 'rh_synthetic_acquisition', 'version': 1, 'batch': 1, 'ledger': budget()}
            bodies = []
            def respond(current, request, started, timeout, record):
                bodies.append(json.loads(request.data))
                return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}).encode()
            with patch.object(service, '_request_bytes', side_effect=respond):
                service.reason_phase(task, [], 'acquire')
                service.reason_phase(task, [], 'analyze')
            self.assertEqual(bodies[0]['thinking'], {'type': 'disabled'})
            self.assertEqual(bodies[1]['reasoning_effort'], 'low')
            self.assertEqual(task['ledger']['used']['model_calls'], 2)
            self.assertNotIn('SYNTHETIC_ACQUISITION_KEY', json.dumps(task))

    def test_offline_navigation_never_dispatches(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = ModelService(ModelSettings(Path(temporary)), offline=True)
            with patch.object(service, '_request_bytes') as request, self.assertRaises(ContractError):
                service.reason_phase({'ledger': budget()}, [], 'acquire')
            request.assert_not_called()

    def test_identity_shapes_have_exact_registered_nesting(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = ResearchAgent(ResearchArchive(Path(temporary)), offline=True).create('研究贵州茅台600519半年报')
            tool = next(item for item in data_request_contract(task)['tools'] if item['operation'] == 'get_thscode')
            for mode, query in (('secname', '贵州茅台'), ('seccode', '600519')):
                parameters = dict(tool['parameter_shapes'][mode], **{mode: query})
                self.assertEqual(set(parameters), {mode, 'functionpara'})
                self.assertEqual(validate_ifind_identity_request(task, parameters), (query, mode))
