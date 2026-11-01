"""The reserved call can finalize a report, never expand the research budget."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from researchhelper.contracts import ContractError,budget
from researchhelper.model import ModelSettings,ModelService
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import model_object

ROOT=Path(__file__).resolve().parents[1]


class FinalAnalysisReserveTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='final-analysis-model-',dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        settings=ModelSettings(temporary.name)
        settings.save({'endpoint':'https://example.invalid/chat/completions','model':'synthetic_model',
            'api_key':'SYNTHETIC_NO_NETWORK'})
        self.service=ModelService(settings,offline=False)
        self.task={'id':'final_delivery_fixture','version':1,'batch':1,
            'ledger':budget({'data_requests':100,'model_calls':8,'seconds':600})}
        self.task['ledger']['used']['model_calls']=7
        response={'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'{}'}}]}
        self.request=patch.object(self.service,'_request_bytes',return_value=json.dumps(response).encode()).start()
        self.addCleanup(patch.stopall)
        patch('socket.socket.connect',side_effect=AssertionError('No real network')).start()

    def test_ordinary_research_cannot_consume_the_reserved_call(self):
        with self.assertRaises(ContractError):self.service.reason_phase(self.task,[],'analyze')
        self.request.assert_not_called()
        self.assertEqual(self.task['ledger']['used']['model_calls'],7)

    def test_final_answer_can_use_call_eight_but_never_call_nine(self):
        self.assertEqual(self.service.reason_final_analysis(self.task,[]),'{}')
        self.assertEqual(self.task['ledger']['used']['model_calls'],8)
        with self.assertRaises(ContractError):self.service.reason_final_analysis(self.task,[])
        self.request.assert_called_once()

    def test_delivery_flag_cannot_be_used_for_planning_or_extraction(self):
        for phase in ('plan','acquire','read','extract','bind_calculations','supplement'):
            with self.assertRaises(ContractError):self.service.reason(self.task,[],delivery=True,phase=phase)
        self.request.assert_not_called()

    def test_engine_requires_explicit_final_delivery_capability(self):
        fake=SimpleNamespace(model=Mock())
        self.assertFalse(ResearchAgent._final_analysis_available(fake,self.task))
        actual=SimpleNamespace(model=self.service)
        self.assertTrue(ResearchAgent._final_analysis_available(actual,self.task))

    def test_a_reserved_or_completed_final_attempt_is_not_reissued(self):
        actual=SimpleNamespace(model=self.service)
        for status in ('已派发','已提交','未采用'):
            task=copy.deepcopy(self.task);task['reasoning_state']={'final_delivery':{'status':status}}
            self.assertFalse(ResearchAgent._final_analysis_available(actual,task))

    def test_stage_adapter_rejects_reserve_use_outside_analysis(self):
        with self.assertRaises(ContractError):
            model_object(object(),{},'extract',{}, {'evidence_specs'},final_delivery=True)

    def test_final_strict_call_preserves_the_same_schema(self):
        schema={'type':'object','properties':{}}
        with patch.object(self.service,'supports_structured_analysis',return_value=True),\
             patch.object(self.service,'reason',return_value='{}') as reason:
            self.service.reason_final_analysis(self.task,[],schema)
        self.assertEqual(reason.call_args.kwargs,{'delivery':True,'phase':'analyze','output_schema':schema,'delivery_margin':5})

    def test_last_analysis_leaves_time_for_render_and_durable_save(self):
        self.task['ledger']['used']['seconds']=590
        self.service.reason_final_analysis(self.task,[])
        self.assertLessEqual(self.request.call_args.args[3],5)
        self.assertGreater(self.request.call_args.args[3],0)
