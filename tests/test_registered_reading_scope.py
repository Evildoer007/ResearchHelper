"""A financial question must not choose reading for an unrelated sub-scope."""
import unittest
from researchhelper.source_reader import binding, registered_reading_requests


class RegisteredReadingScopeTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(id='isolated-scope', version=1, batch=1, input_hash='fixture',
            registered_source_reading_version=1, financial_input_reading_priority_version=1,
            questions=[dict(id='financial', method_needs=['net_profit_margin_v1']),
                dict(id='event', method_needs=[])], calculation_specs=[])
        self.catalogue = dict(binding=binding(self.task), offset=0, next_offset=None, total_artifacts=1,
            items=[dict(id='fixture-original', kind='filing', status='已解析', snapshot_hash='fixture-hash',
                request_binding=dict(artifact_id='fixture-original', snapshot_hash='fixture-hash'))])

    def test_unrelated_subset_keeps_model_selected_reading(self):
        self.assertIsNone(registered_reading_requests(self.task, self.catalogue, ['event']))

    def test_even_financial_subset_does_not_implicitly_cover_other_questions(self):
        self.assertIsNone(registered_reading_requests(self.task, self.catalogue, ['financial']))

    def test_full_initial_scope_only_proposes_locations_not_source_approval(self):
        requests = registered_reading_requests(self.task, self.catalogue, ['financial', 'event'])
        self.assertEqual(requests[0]['question_ids'], ['financial', 'event'])
        self.assertEqual(set(requests[0]), {'artifact_id', 'snapshot_hash', 'queries', 'parts', 'reason', 'question_ids'})
