"""Do not spend a mandatory review's last call on unreviewable new prose.

The retained closed study supplies contract/evidence shapes only. All changes
are private copies; this test never resumes it or calls a model/provider.
"""
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper import argument_support
from researchhelper.contracts import ContractError
from researchhelper.model import ModelService, ModelSettings

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT/'result/acceptance/one-sentence-research-9eq092nf/task.json'
SOURCE = Path(os.environ.get('RESEARCHHELPER_REVIEW_RESERVE_INPUT',
    DEFAULT_SOURCE if DEFAULT_SOURCE.is_file() else DEFAULT_SOURCE.with_suffix('.json.gz')))
RESOURCES = Path(os.environ.get('RESEARCHHELPER_FIXTURE_RESOURCES', str(ROOT)))


class ReviewReserveAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        opener = gzip.open if SOURCE.suffix == '.gz' else open
        with opener(SOURCE, 'rt', encoding='utf-8') as incoming:
            cls.original = json.load(incoming)

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='review-reserve-', dir=Path(os.environ.get(
            'RESEARCHHELPER_REVIEW_RESERVE_OUTPUT', str(ROOT/'result/acceptance'))))
        self.addCleanup(folder.cleanup)
        self.model = Mock()
        self.model.supports_final_delivery.return_value = True
        self.agent = ResearchAgent(ResearchArchive(folder.name), RESOURCES, offline=False,
                                   model=self.model, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = copy.deepcopy(self.original)
        self.task.update(status='运行中', delivery=None, host_result={}, reasoning_state={}, revision=0)
        self.task['ledger']['used'].update(model_calls=7, seconds=100)
        self.task['argument_support_reviews'] = []
        for question in self.task['questions']:
            question.update(status='待回答', answer='', refs=[], calculation_refs=[])
        guard = patch('socket.socket.connect', side_effect=AssertionError('No external calls'))
        guard.start()
        self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start()
            self.addCleanup(worker.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), self.source_hash)

    def test_current_contract_never_spends_sole_remaining_call_on_new_analysis(self):
        self.assertTrue(argument_support.enabled(self.task))
        before = copy.deepcopy(self.task['ledger'])
        self.assertFalse(self.agent._reasoning_available(self.task))
        self.assertFalse(self.agent._final_analysis_available(self.task))
        self.assertEqual(self.task['ledger'], before)

    def test_actual_answer_node_keeps_evidence_and_budget_without_dispatch(self):
        before = copy.deepcopy((self.task['evidence'], self.task['ledger']))
        self.agent.archive.save(self.task)
        with patch.object(self.agent, '_reason_on_verified_material',
                          side_effect=AssertionError('Cannot pay for unreviewable prose')) as analyze:
            self.agent._step(self.task, 'answer', None)
        analyze.assert_not_called()
        self.assertEqual((self.task['evidence'], self.task['ledger']), before)
        self.assertTrue(self.task['gaps'])
        self.assertTrue(all(question['status'] != '已回答' for question in self.task['questions']))

    def test_two_remaining_calls_still_allow_ordinary_analysis_and_its_review(self):
        self.task['ledger']['used']['model_calls'] = 6
        self.assertTrue(self.agent._reasoning_available(self.task))
        self.assertFalse(self.agent._final_analysis_available(self.task))

    def test_legacy_nonreview_contract_retains_its_registered_final_delivery(self):
        self.task.pop('argument_support_contract')
        self.assertTrue(self.agent._final_analysis_available(self.task))
        self.task['ledger']['used']['model_calls'] = 8
        self.assertFalse(self.agent._final_analysis_available(self.task))

    def service(self):
        settings = ModelSettings(self.agent.archive.root)
        settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                           api_key='FAKE_RESERVE_TEST_KEY', reasoning_effort='none'))
        return ModelService(settings, offline=False)

    def test_model_boundary_also_rejects_direct_reserved_analysis_before_charge(self):
        service = self.service()
        before = copy.deepcopy(self.task['ledger'])
        with patch.object(service, '_request_bytes', side_effect=AssertionError('No dispatch')) as dispatch:
            for phase in (None, 'analyze'):
                with self.assertRaisesRegex(ContractError, '交付预留仅用于独立复核'):
                    service.reason(self.task, [], delivery=True, phase=phase)
            with self.assertRaisesRegex(ContractError, '交付预留仅用于独立复核'):
                service.reason_final_analysis(self.task, [])
        dispatch.assert_not_called()
        self.assertEqual(self.task['ledger'], before)

    def test_final_call_remains_available_for_independent_review_not_a_ninth_call(self):
        service = self.service()
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                content=[dict(type='output_text', text='{}')])], usage={})
        with patch.object(service, '_request_bytes', return_value=json.dumps(response).encode()) as dispatch:
            self.assertEqual(service.reason(self.task, [], delivery=True, phase='review_support'), '{}')
            self.assertEqual(self.task['ledger']['used']['model_calls'], 8)
            with self.assertRaises(ContractError):
                service.reason(self.task, [], delivery=True, phase='review_support')
        dispatch.assert_called_once()


if __name__ == '__main__':
    unittest.main()
