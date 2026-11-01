"""Task-bound uploads preserve ownership/version and never dispatch inference."""

import base64
import unittest
from urllib.error import HTTPError

from tests import test_autonomous_supplement_interfaces_offline as fixtures
from tests import test_management_custody_fee_offline as fee_fixtures


class TaskMaterialBindingTests(unittest.TestCase):
    setUp = fixtures.SupplementInterfacesTests.setUp
    stop = fixtures.SupplementInterfacesTests.stop
    request = fixtures.SupplementInterfacesTests.request
    login = fixtures.SupplementInterfacesTests.login

    def test_bound_upload_preserves_task_ledger_and_remains_unverified(self):
        task = self.request('/api/tasks', {'question': '核对本任务材料'})[0]
        result = self.request('/api/tasks/' + task['id'] + '/material', {
            'name': 'policy.txt', 'base64': base64.b64encode('离线合成原件，未核验公开时间。'.encode()).decode(),
            'expected_version': task['version']})[0]
        self.assertEqual(result['id'], task['id'])
        self.assertEqual(result['version'], task['version'])
        self.assertEqual(result['ledger'], task['ledger'])
        self.assertEqual(result['status'], '待开始')
        self.assertFalse(result['evidence'])
        self.assertEqual(len(result['materials']), 1)

    def test_stale_invalid_and_foreign_uploads_do_not_change_materials(self):
        task = self.request('/api/tasks', {'question': '保留任务版本'})[0]
        route = '/api/tasks/' + task['id'] + '/material'
        payload = {'name': 'policy.txt', 'base64': base64.b64encode(b'offline original').decode()}
        for version in (task['version'] + 1, True, 0, '1', None):
            with self.subTest(version=version), self.assertRaises(HTTPError) as error:
                self.request(route, dict(payload, expected_version=version))
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            self.assertEqual(self.request('/api/tasks/' + task['id'])[0], task)
        self.login('sales')
        with self.assertRaises(HTTPError) as error:
            self.request(route, dict(payload, expected_version=task['version']))
        self.assertIn(error.exception.code, (403, 404))
        error.exception.close()


class DeliveredMaterialBindingTests(unittest.TestCase):
    def test_new_material_is_pending_without_changing_frozen_report(self):
        fixture = fee_fixtures.ManagementCustodyFeeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task, host = fixture.prepare()
        done = fixture.agent.start(task['id'], host)
        self.assertEqual(done['status'], '已完成', done.get('gaps'))
        report = fixture.agent.archive.report(done['id'], done['version'])
        updated = fixture.agent.add_material(done['id'], 'extra.txt', '合成新增原件，尚未核验。'.encode(),
                                             expected_version=done['version'])
        self.assertEqual(updated['id'], done['id'])
        self.assertEqual(updated['version'], done['version'])
        self.assertEqual(updated['ledger'], done['ledger'])
        self.assertEqual(updated['delivery'], done['delivery'])
        self.assertEqual(updated['materials'], done['materials'])
        self.assertEqual(updated['evidence'], done['evidence'])
        self.assertEqual(len(updated['pending_materials']), 1)
        self.assertEqual(fixture.agent.archive.report(done['id'], done['version']), report)


if __name__ == '__main__':
    unittest.main()
