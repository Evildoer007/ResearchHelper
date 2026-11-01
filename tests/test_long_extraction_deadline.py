"""Long extraction may use the task's time, never change modes or retry calls."""
import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import budget
from researchhelper.model import ModelService


class FakeSettings:
    def __init__(self):
        self.value = dict(endpoint='https://example.invalid', model='offline-fixture',
                          api_key='OFFLINE_FAKE_NEVER_REAL')

    def read(self, private=False):
        return copy.deepcopy(self.value)


class LongExtractionDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.settings = FakeSettings()
        self.service = ModelService(self.settings, offline=False)
        self.task = dict(id='deadline_fixture', batch=1, ledger=budget())

    def execute(self, phase='extract', content=None):
        messages = [dict(role='user', content='研'*32_000 if content is None else content)]
        original = copy.deepcopy(messages), copy.deepcopy(self.settings.value)
        response = json.dumps(dict(choices=[dict(finish_reason='stop', message=dict(content='{}'))])).encode()
        with patch.object(self.service, '_request_bytes', return_value=response) as request:
            self.assertEqual(self.service.reason_phase(self.task, messages, phase), '{}')
        self.assertEqual((messages, self.settings.value), original)
        return self.task['ledger']['dispatches'][-1], json.loads(request.call_args.args[1].data)

    def test_long_extract_uses_a_bounded_180_seconds_without_mode_change(self):
        record, body = self.execute()
        self.assertEqual(record['timeout_seconds'], 180)
        self.assertEqual(record['wait_policy'], 'bounded_long_extraction_v1')
        self.assertNotIn('thinking', body)
        self.assertEqual(body['max_tokens'], self.service.output_limit(self.settings.value))

    def test_short_extraction_keeps_the_ordinary_wait(self):
        record, _ = self.execute(content='核对已提供原文')
        self.assertEqual(record['timeout_seconds'], 90)
        self.assertNotIn('wait_policy', record)

    def test_untrusted_extract_words_cannot_change_another_phase(self):
        record, _ = self.execute(phase='plan', content='extract'*32_000)
        self.assertEqual(record['timeout_seconds'], 90)

    def test_delivery_reserve_is_not_consumed_by_a_long_extract(self):
        self.task['ledger']['limits']['seconds'] = 140
        self.task['ledger']['used']['seconds'] = 50
        record, _ = self.execute()
        self.assertEqual(record['timeout_seconds'], 30)

    def test_stricter_transport_cap_still_wins(self):
        self.service.MAX_WAIT_SECONDS = 0.4
        record, _ = self.execute()
        self.assertEqual(record['timeout_seconds'], 0.4)

    def test_existing_model_ledger_is_charged_once_and_never_reset(self):
        self.task['ledger']['used']['model_calls'] = 5
        self.execute()
        self.assertEqual(self.task['ledger']['used']['model_calls'], 6)
        self.assertEqual(len(self.task['ledger']['dispatches']), 1)


if __name__ == '__main__':
    unittest.main()
