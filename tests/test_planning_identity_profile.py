"""Current candidate and exact profiles preserve explicit identity/business gates."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (IDENTITY_EXACT_PROFILE, IDENTITY_CANDIDATE_PROFILE, IDENTITY_PARAMETERS_BY_MODE,
    identity_query_parameters, validate_ifind_identity_request)
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (planning_output_schema, planning_protocol,
    decode_planning_output, PLAN_REQUEST_PROTOCOL, PLAN_REQUEST_FUNCTION_NAME)
from researchhelper.planning import question_specs
from researchhelper.reasoning import validate_plan, model_object, PHASE_FIELDS

ROOT = Path(__file__).resolve().parents[1]


class PlanningIdentityProfileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='rh-identity-profile-')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(Path(folder.name)/'store'), ROOT,
            offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('研究贵州茅台2026年半年报的盈利质量。')
        self.schema = planning_output_schema(self.task)
        self.request = dict(id='identity', source='ifind', operation='get_thscode',
            parameters=dict(query_mode='secname', query_value='贵州茅台', profile_id=IDENTITY_CANDIDATE_PROFILE),
            question_ids=['q1'], selection_reason='解析用户原文中的证券名称，后续再选择财报。')
        questions = question_specs(self.task)
        self.wire = dict(version=3, main_question=questions[0], additional_questions=questions[1:],
                         data_requests=[self.request], calculations=[])
        settings = ModelSettings(Path(folder.name)/'settings')
        settings.save(dict(endpoint='https://api.deepseek.com/beta/chat/completions',
            model='deepseek-flash', api_key='FAKE_IDENTITY_PROFILE_KEY', reasoning_effort='none'))
        self.service = ModelService(settings, offline=False)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls'))
        guard.start(); self.addCleanup(guard.stop)

    def decode(self):
        return decode_planning_output(self.wire, self.schema)

    def test_new_formal_task_selects_explicit_versioned_protocol(self):
        self.assertEqual(planning_protocol(self.task), (PLAN_REQUEST_PROTOCOL, PLAN_REQUEST_FUNCTION_NAME))
        self.assertEqual(self.schema['properties']['version']['enum'], [3])

    def test_name_profile_expands_only_the_registered_vendor_constants(self):
        before = copy.deepcopy(self.wire)
        plan = self.decode()
        parameters = plan['data_requests'][0]['parameters']
        self.assertEqual(parameters, identity_query_parameters('secname', '贵州茅台', IDENTITY_CANDIDATE_PROFILE))
        self.assertEqual(validate_ifind_identity_request(self.task, parameters), ('贵州茅台', 'secname'))
        validate_plan(self.task, plan)
        self.assertEqual(self.wire, before)

    def test_numeric_query_stays_explicit_and_verbatim(self):
        self.task = self.agent.create('研究600519的盈利质量。')
        self.schema = planning_output_schema(self.task)
        self.wire['main_question'] = question_specs(self.task)[0]
        self.request['parameters'].update(query_mode='seccode', query_value='600519', profile_id=IDENTITY_EXACT_PROFILE)
        plan = self.decode()
        self.assertEqual(validate_ifind_identity_request(self.task, plan['data_requests'][0]['parameters']),
                         ('600519', 'seccode'))

    def test_other_entity_not_in_user_input_is_still_rejected(self):
        self.request['parameters']['query_value'] = '另一家公司'
        with self.assertRaises(ContractError):
            validate_plan(self.task, self.decode())

    def test_profile_does_not_approve_codes_or_expand_identity(self):
        self.decode()
        self.assertFalse(self.task.get('identity_resolutions'))
        self.assertEqual(self.task['evidence'], [])
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_current_name_lookup_does_not_silently_select_exact_profile(self):
        self.request['parameters']['profile_id'] = IDENTITY_EXACT_PROFILE
        with self.assertRaises(ContractError): self.decode()

    def test_unknown_profile_is_not_guessed(self):
        self.request['parameters']['profile_id'] = 'fuzzy_match'
        with self.assertRaises(ContractError): self.decode()

    def test_vendor_parameters_cannot_be_mixed_into_short_representation(self):
        self.request['parameters']['functionpara'] = {'isexact': '1'}
        with self.assertRaises(ContractError): self.decode()

    def test_mode_and_query_value_are_required_not_defaulted(self):
        for field in ('query_mode', 'query_value', 'profile_id'):
            with self.subTest(field=field):
                wire = copy.deepcopy(self.wire)
                wire['data_requests'][0]['parameters'].pop(field)
                with self.assertRaises(ContractError): decode_planning_output(wire, self.schema)

    def test_changed_profile_invalidates_the_original_schema_binding(self):
        with patch.dict(IDENTITY_PARAMETERS_BY_MODE, secname=dict(IDENTITY_PARAMETERS_BY_MODE['secname'], isexact='0')):
            with self.assertRaises(ContractError): self.decode()

    def test_legacy_tasks_keep_full_vendor_shape_and_version(self):
        old = copy.deepcopy(self.task)
        old.pop('planning_request_encoding')
        old.pop('planning_main_binding_version')
        old.pop('identity_candidate_lookup_version')
        wire = copy.deepcopy(self.wire)
        wire['questions'] = [wire.pop('main_question')] + wire.pop('additional_questions')
        wire['version'] = 1
        wire['data_requests'][0]['parameters'] = identity_query_parameters('secname', '贵州茅台')
        self.assertEqual(planning_protocol(old), ('strict_plan_v1', 'rh_submit_plan_v1'))
        self.assertEqual(decode_planning_output(wire, planning_output_schema(old))['data_requests'], wire['data_requests'])

    def test_retained_short_profile_task_keeps_v2_container(self):
        old = copy.deepcopy(self.task)
        old.pop('planning_main_binding_version')
        wire = copy.deepcopy(self.wire)
        wire.update(version=2, questions=[wire.pop('main_question')] + wire.pop('additional_questions'))
        schema = planning_output_schema(old)
        self.assertEqual(schema['properties']['version']['enum'], [2])
        self.assertEqual(decode_planning_output(wire, schema)['data_requests'][0]['parameters'],
                         identity_query_parameters('secname', '贵州茅台', IDENTITY_CANDIDATE_PROFILE))

    def test_new_task_does_not_silently_accept_legacy_encoding(self):
        self.wire['version'] = 1
        self.request['parameters'] = identity_query_parameters('secname', '贵州茅台')
        with self.assertRaises(ContractError): self.decode()

    def test_strict_transport_charges_once_and_returns_canonical_request(self):
        reply = dict(model='deepseek-flash', choices=[dict(index=0, finish_reason='tool_calls',
            message=dict(role='assistant', content=None, tool_calls=[dict(id='synthetic', type='function',
                function=dict(name=PLAN_REQUEST_FUNCTION_NAME, arguments=json.dumps(self.wire)))]))])
        with patch.object(self.service, '_request_bytes', return_value=json.dumps(reply).encode()) as transport:
            result = json.loads(self.service.reason_structured_plan(self.task, []))
        body = json.loads(transport.call_args.args[1].data)
        self.assertEqual(body['tools'][0]['function']['name'], PLAN_REQUEST_FUNCTION_NAME)
        self.assertEqual(result['data_requests'][0]['parameters'], identity_query_parameters('secname', '贵州茅台', IDENTITY_CANDIDATE_PROFILE))
        self.assertEqual(self.task['ledger']['used']['model_calls'], 1)
        self.assertEqual(self.task['ledger']['dispatches'][0]['output_protocol'], PLAN_REQUEST_PROTOCOL)

    def test_formal_prompt_describes_the_actual_short_container(self):
        with patch.object(self.service, 'reason_structured_plan', return_value=json.dumps(self.decode())) as submit:
            model_object(self.service, self.task, 'plan', {}, PHASE_FIELDS['plan'])
        prompt = json.dumps(submit.call_args.args[1], ensure_ascii=False)
        self.assertIn('rh_submit_plan_v2', prompt)
        self.assertIn('query_value', prompt)
        self.assertIn('不要提交functionpara', prompt)

    def test_explicit_responses_route_uses_the_same_profile_and_business_decoder(self):
        self.service.settings.save(dict(endpoint='https://api.deepseek.com/responses',
            model='deepseek-flash'))
        reply = dict(object='response', status='completed', model='deepseek-flash', error=None,
            incomplete_details=None, output=[dict(type='message', role='assistant', status='completed',
                content=[dict(type='output_text', text=json.dumps(self.wire))])])
        with patch.object(self.service, '_request_bytes', return_value=json.dumps(reply).encode()) as transport:
            result = json.loads(self.service.reason_structured_plan(self.task, []))
        body = json.loads(transport.call_args.args[1].data)
        self.assertEqual(body['text']['format']['name'], PLAN_REQUEST_FUNCTION_NAME)
        self.assertEqual(result['data_requests'][0]['parameters'], identity_query_parameters('secname', '贵州茅台', IDENTITY_CANDIDATE_PROFILE))
        self.assertEqual(self.task['ledger']['used']['model_calls'], 1)

    def test_new_task_rejects_a_legacy_protocol_before_dispatch(self):
        old = copy.deepcopy(self.task)
        old.pop('planning_request_encoding')
        with patch.object(self.service, '_request_bytes') as transport:
            with self.assertRaises(ContractError):
                self.service.reason(self.task, [], phase='plan', output_schema=planning_output_schema(old),
                    output_protocol='strict_plan_v1', planning_deadline=True)
        transport.assert_not_called()
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)


if __name__ == '__main__':
    unittest.main()
