"""Actual spawned model transport against local fixture TLS, not a provider."""
import json
from researchhelper.contracts import ContractError

from tests import test_portable_transport_fixtures_offline as portable


class ModelProgressSpawnTests(portable.PortableModelTransport):
    def test_pending_checkpoints_precede_the_complete_response(self):
        self.server('normal')
        task = self.task()
        self.assertEqual(self.service.reason_research_plan(task, []), 'fixture answer')
        pending = [item['ledger']['dispatches'][-1] for item in self.journal
                   if item['ledger']['dispatches'][-1]['status'] == '已派发']
        self.assertTrue(any(item.get('progress_version') == 1 and item['stage'] == '连接服务' for item in pending))
        self.assertTrue(any(item.get('progress_version') == 1 and item['stage'] == '读取响应' for item in pending))
        final = self.checked(task)
        self.assertEqual(final['status'], '成功')
        self.assertEqual(len(self.request_headers), 1)
        self.assertNotIn('PRIVATE_NEVER_ARCHIVE', json.dumps(self.journal))

    def test_pending_read_is_preserved_on_actual_body_timeout(self):
        self.server('body')
        self.service.MAX_WAIT_SECONDS = .65
        task = self.task()
        with self.assertRaises(ContractError):
            self.service.reason_research_plan(task, [])
        pending = [item['ledger']['dispatches'][-1] for item in self.journal
                   if item['ledger']['dispatches'][-1]['status'] == '已派发']
        self.assertTrue(any(item.get('progress_version') == 1 and item['stage'] == '读取响应' for item in pending))
        final = self.checked(task)
        self.assertEqual(final['status'], '失败')
        self.assertTrue(final['network_worker_reaped'])
        self.assertEqual(len(self.request_headers), 1)
        self.assertEqual(task['ledger']['used']['model_calls'], 1)
        self.assertEqual(task['ledger']['reserve']['seconds'], 60)
