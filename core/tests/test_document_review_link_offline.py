"""Independent original-to-document workflow and account-boundary checks."""

import base64
import copy
import unittest

from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint
from tests import test_document_content_offline as documents
from tests import test_auth_roles_offline as roles


class DocumentReviewLinkTests(unittest.TestCase):
    def setUp(self):
        self.fixture = documents.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent = self.fixture.agent
        self.parent = self.fixture.material_task(intent='综合研究')
        self.parent = self.agent.start(self.parent['id'])
        self.record = self.parent['materials'][0]

    def review(self, **changes):
        arguments = {'ident': self.parent['id'], 'artifact_id': self.record['id'],
            'question': '文件中的政策生效条款是什么？', 'expected_version': self.parent['version'],
            'expected_snapshot_hash': self.record['snapshot_hash'], 'confirmed': True}
        arguments.update(changes)
        return self.agent.document_review(**arguments)

    def test_explicit_selection_required_before_any_new_task(self):
        before = self.agent.archive.list()
        for confirmed in (False, None, 1, 'true'):
            with self.subTest(confirmed=confirmed), self.assertRaises(ContractError):
                self.review(confirmed=confirmed)
        self.assertEqual(self.agent.archive.list(), before)

    def test_only_original_bytes_transferred_and_parent_is_unchanged(self):
        frozen = self.agent.export(self.parent['id'])
        task = self.review()
        self.assertEqual(task['status'], '待开始')
        self.assertEqual(task['intent'], DOCUMENT_CONTENT_INTENT)
        self.assertEqual(task['parent'], self.parent['id'])
        self.assertNotEqual(task['id'], self.parent['id'])
        self.assertEqual(task['materials'][0]['hash'], self.record['hash'])
        self.assertEqual(task['materials'][0]['task_id'], task['id'])
        self.assertEqual(task['materials'][0]['source'], self.record['source'])
        self.assertEqual(task['ledger']['used'], {'data_requests': 0, 'model_calls': 0, 'seconds': 0})
        for key in ('evidence', 'rejected_evidence', 'theses', 'calculations', 'source_records', 'tracking'):
            self.assertEqual(task[key], [], key)
        self.assertIsNone(task['delivery'])
        self.assertEqual(self.agent.archive.get(self.parent['id']), self.parent)
        self.assertEqual(self.agent.export(self.parent['id']), frozen)
        self.assertFalse(task['document_review_origin']['inherited_approval'])

    def test_document_completion_does_not_complete_original_research(self):
        task = self.review()
        final = self.agent.start(task['id'], {'evidence_specs': [self.fixture.spec(task)]})
        self.assertEqual(final['status'], '已完成', final.get('error'))
        self.assertIsNone(final['evidence'][0]['published_at'])
        self.assertEqual(final['evidence'][0]['usage_scope'], 'document_content')
        self.assertEqual(self.agent.archive.get(self.parent['id']), self.parent)
        self.assertEqual(self.parent['status'], '部分完成')
        self.assertEqual(final['ledger']['used']['data_requests'], 0)

    def test_stale_version_hash_and_cross_task_source_are_rejected(self):
        other = self.agent.add_material(self.agent.create('其他问题')['id'], 'other.txt', b'Other material')
        for arguments in ({'expected_version': 2}, {'expected_version': True},
                          {'expected_snapshot_hash': '0' * 64}, {'artifact_id': other['materials'][0]['id']}):
            before = len(self.agent.archive.list())
            with self.subTest(arguments=arguments), self.assertRaises(ContractError):
                self.review(**arguments)
            self.assertEqual(len(self.agent.archive.list()), before)

    def test_running_parent_and_invalid_question_rejected_before_creation(self):
        for question in ('', ' ', '字' * 20001, None):
            with self.subTest(question=question), self.assertRaises(ContractError):
                self.review(question=question)
        task = copy.deepcopy(self.parent);task['status'] = '运行中';self.agent.archive.save(task)
        with self.assertRaises(ContractError):
            self.review()

    def test_interface_snapshot_and_catalog_are_not_document_originals(self):
        task = copy.deepcopy(self.parent)
        task['source_records'].append({'id': 'api-only', 'kind': 'api', 'snapshot': {'text': 'Looks like prose'},
                                      'task_id': task['id'], 'research_version': 1})
        self.agent.archive.save(task)
        with self.assertRaises(ContractError):
            self.review(artifact_id='api-only')

    def test_rehashed_forged_excerpt_cannot_transfer(self):
        task = copy.deepcopy(self.parent)
        material = task['materials'][0]
        material['snapshot']['parts'][0]['text'] = 'Forged disclosure and numbers'
        material['snapshot_hash'] = fingerprint(material['snapshot'])
        self.agent.archive.save(task)
        before = len(self.agent.archive.list())
        with self.assertRaises(ContractError):
            self.review(expected_snapshot_hash=material['snapshot_hash'])
        self.assertEqual(len(self.agent.archive.list()), before)

    def test_unparsed_and_corrupt_originals_are_rejected(self):
        task = copy.deepcopy(self.parent)
        material = task['materials'][0]
        material['snapshot']['original_base64'] = base64.b64encode(b'Wrong bytes').decode()
        material['snapshot_hash'] = fingerprint(material['snapshot'])
        self.agent.archive.save(task)
        with self.assertRaises(ContractError):
            self.review(expected_snapshot_hash=material['snapshot_hash'])
        material['snapshot']['status'] = '未解析'
        material['snapshot_hash'] = fingerprint(material['snapshot'])
        self.agent.archive.save(task)
        with self.assertRaises(ContractError):
            self.review(expected_snapshot_hash=material['snapshot_hash'])


class DocumentReviewAccountTests(unittest.TestCase):
    def setUp(self):
        self.fixture = roles.RoleIsolationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.admin, self.sales = self.fixture.setup_accounts()
        _, self.task = self.fixture.call('/api/tasks', {'question': '原研究'}, self.admin)[:2]
        _, self.task = self.fixture.call('/api/tasks/' + self.task['id'] + '/material',
            {'name': 'original.txt', 'base64': base64.b64encode(documents.TEXT.encode()).decode()}, self.admin)[:2]
        record = self.task['materials'][0]
        self.payload = {'artifact_id': record['id'], 'question': '该文件写了什么？', 'expected_version': 1,
                        'expected_snapshot_hash': record['snapshot_hash'], 'confirmed': True}
        self.route = '/api/tasks/' + self.task['id'] + '/document-review'

    def test_new_task_inherits_owner_not_approval_and_does_not_start(self):
        status, task = self.fixture.call(self.route, self.payload, self.admin)[:2]
        self.assertEqual(status, 200)
        self.assertEqual(task['owner_account'], 'admin')
        self.assertEqual(task['status'], '待开始')
        self.assertEqual(task['ledger']['dispatches'], [])
        self.assertEqual(task['intent'], DOCUMENT_CONTENT_INTENT)
        self.assertEqual(self.fixture.call('/api/tasks/' + task['id'], cookie=self.sales)[0], 403)

    def test_other_account_cannot_copy_an_original(self):
        self.assertEqual(self.fixture.call(self.route, self.payload, self.sales)[0], 403)
        self.assertEqual(self.fixture.call(self.route, self.payload)[0], 401)

    def test_endpoint_rejects_automatic_start_and_scope_overrides(self):
        for addition in ({'start': True}, {'intent': '综合研究'}, {'host_result': {}}):
            self.assertEqual(self.fixture.call(self.route, dict(self.payload, **addition), self.admin)[0], 400)
        status, task = self.fixture.call('/api/tasks/' + self.task['id'], cookie=self.admin)[:2]
        self.assertEqual(status, 200)
        self.assertEqual(task['intent'], '综合研究')
        self.assertIsNone(task['delivery'])
