"""Navigation input controls; no provider, model or production task is used."""
import copy
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical
from researchhelper.engine import ResearchAgent
from researchhelper.model_presentation import GUIDE, present, restore
from researchhelper.reasoning import model_object

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {'plan': {'questions', 'data_requests', 'calculations'}, 'acquire': {'data_requests', 'stop_reason'}}


class Captured(Exception):
    pass


class Observer:
    def supports_structured_analysis(self): return True

    def reason_structured_plan(self, task, messages):
        self.messages = messages
        raise Captured()

    def reason_structured_acquisition(self, task, messages, payload):
        self.messages = messages
        raise Captured()


def prepare_fixture():
    """Capture the real planning and acquisition boundary in a private task.

    The explicit plan is a local representation control, not model-generated
    reasoning or a replacement for ordinary Chat acceptance.
    """
    from researchhelper.acquisition import complete_initial_acquisition
    from researchhelper import engine as engine_module, acquisition as acquisition_module
    with tempfile.TemporaryDirectory(prefix='navigation-input-fixture-', dir=ROOT/'result/acceptance') as folder:
        agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
        question = '比较000300.SH最近一个月的区间变化，说明数据限制并生成报告'
        task = agent.create(question, entry='指数', research_mode='current')
        try:
            agent._adopt_method_knowledge(task)
            tasks, payloads = {}, {}
            def capture(model, current, phase, payload, fields, **kwargs):
                assert phase in FIELDS and fields == FIELDS[phase]
                tasks[phase] = copy.deepcopy(current)
                payloads[phase] = copy.deepcopy(payload)
                raise Captured()
            with (patch('socket.socket.connect', side_effect=AssertionError('No network in fixture')),
                  patch.object(agent, '_active', return_value=task),
                  patch.object(agent, '_reasoning_available', return_value=True),
                  patch.object(engine_module, 'model_object', side_effect=capture)):
                try: agent._prepare_model_plan(task['id'], task['batch'])
                except Captured: pass
            need = '000300.SH最近一个月起终实际交易日的收盘点位及日期'
            task = agent.revise_plan(task['id'], [dict(id='q1', kind='fact', required=True,
                question=question, dependencies=[], evidence_needs=[need], method_needs=[],
                evidence_need_constraints={need: dict(value_kind='number', unit_dimension='index',
                    minimum_count=2, period_kind='point')})],
                '离线核对取数导航输入，不执行研究', task['input_hash'])
            task['run_mode'] = 'model'
            agent.offline = False
            with (patch('socket.socket.connect', side_effect=AssertionError('No network in fixture')),
                  patch.object(agent, '_reasoning_available', return_value=True),
                  patch.object(agent, '_save_model_checkpoint', Mock()),
                  patch.object(acquisition_module, 'model_object', side_effect=capture)):
                try: complete_initial_acquisition(agent, task)
                except Captured: pass
            assert set(payloads) == set(FIELDS)
            assert task['ledger']['used']['model_calls'] == task['ledger']['used']['data_requests'] == 0
            return dict(tasks=tasks, payloads=payloads, explicit_private_control=True,
                        actual_model_quality_accepted=False, provider_calls=0, model_calls=0)
        finally:
            agent.close_runtime()


class NavigationInputSharingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_NAVIGATION_INPUT_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes())) if supplied else prepare_fixture()

    def test_candidate_representation_preserves_complete_navigation_inputs(self):
        # This measures an existing representation without enabling it for
        # planning or acquisition. Character counts are not billed tokens.
        for phase, payload in self.fixture['payloads'].items():
            observer = Observer()
            with self.assertRaises(Captured):
                model_object(observer, self.fixture['tasks'][phase], phase, payload, FIELDS[phase])
            body = json.loads(observer.messages[1]['content'])
            compact, changed = present(body)
            if changed:
                self.assertEqual(canonical(restore(compact)), canonical(body))
            print(phase, len(canonical(body)), '->', len(canonical(compact)), 'guide', len(GUIDE))


if __name__ == '__main__': unittest.main()
