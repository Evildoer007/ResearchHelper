"""Size the actual extraction gateway without dispatching a model request."""
import json
import unittest

try:
    from . import test_csi_cross_index_sources as controls
except ImportError:
    import test_csi_cross_index_sources as controls

from researchhelper.contracts import canonical
from researchhelper.model import ModelService
from researchhelper.planning import question_specs
from researchhelper.reasoning import (ModelContextCapacityError, evidence_contract,
    extractable_source_views, model_object)


class GatewayCaptured(Exception):
    pass


class ExtractionObserver:
    def supports_structured_analysis(self):
        return True

    def input_budget(self):
        return ModelService.documented_input_budget(dict(
            endpoint='https://api.deepseek.com/responses', model='deepseek-flash'))

    def reason_structured_extraction(self, task, messages, views):
        self.messages = messages
        self.views = views
        raise GatewayCaptured()


class CSICrossIndexModelInputTests(unittest.TestCase):
    def setUp(self):
        self.control = controls.CSICrossIndexSourceTests()
        self.control.setUpClass()
        self.control.setUp()
        self.addCleanup(self.control.doCleanups)

    def payload(self, code):
        self.control.reset(code)
        self.control.acquire()
        task = self.control.task
        views = extractable_source_views(task)
        return dict(question=task['question'], intent=task['intent'], objects=task['objects'],
            cutoff=task['cutoff'], questions=question_specs(task),
            calculations=task['calculation_specs'], evidence_contract=evidence_contract(task, views),
            field_registry={}, untrusted_sources=views, source_reading=None)

    def test_actual_gateway_retains_both_complete_tables_without_a_paid_dispatch(self):
        for code in controls.CODES:
            payload = self.payload(code)
            task = self.control.task
            before = canonical(task), canonical(payload)
            observer = ExtractionObserver()
            with self.subTest(code=code), self.assertRaises(GatewayCaptured):
                model_object(observer, task, 'extract', payload, {'evidence_specs'})
            self.assertEqual(observer.views, payload['untrusted_sources'])
            body = json.loads(observer.messages[-1]['content'])
            self.assertEqual(canonical(body['untrusted_sources']), canonical(payload['untrusted_sources']))
            self.assertLess(sum(len(item['content'].encode()) for item in observer.messages),
                observer.input_budget()['input_byte_limit'])
            self.assertEqual((canonical(task), canonical(payload)), before)
            self.assertEqual(task['ledger']['used']['model_calls'], 0)

    def test_oversized_wire_is_rejected_before_the_observer_and_without_mutation(self):
        payload = self.payload('000852')
        payload['untrusted_padding_control'] = '中' * 300000
        task = self.control.task
        before = canonical(task), canonical(payload)
        observer = ExtractionObserver()
        with self.assertRaises(ModelContextCapacityError):
            model_object(observer, task, 'extract', payload, {'evidence_specs'})
        self.assertFalse(hasattr(observer, 'messages'))
        self.assertEqual((canonical(task), canonical(payload)), before)
        self.assertEqual(task['ledger']['used']['model_calls'], 0)
