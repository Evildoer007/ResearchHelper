"""Related-task creation must not expose partial tasks or orphan originals."""

import sqlite3
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from tests import test_document_review_link_offline as review_tests


class RelatedTaskCommitTests(unittest.TestCase):
    def setUp(self):
        self.fixture = review_tests.DocumentReviewLinkTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent = self.fixture.agent
        self.archive = self.agent.archive

    def counts(self):
        with self.archive.connect() as db:
            return {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                    for table in ('tasks', 'versions', 'originals', 'deliveries')}

    def test_original_insert_failure_rolls_back_task_and_version(self):
        before = self.counts()
        parent = self.archive.get(self.fixture.parent['id'])
        with self.archive.connect() as db:
            db.execute("CREATE TRIGGER fail_related_original BEFORE INSERT ON originals "
                       "BEGIN SELECT RAISE(ABORT, 'injected original failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.fixture.review()
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.archive.get(parent['id']), parent)

    def test_material_binding_failure_creates_no_task(self):
        before = self.counts()
        with patch('researchhelper.engine.material_record', side_effect=ContractError('injected binding failure')):
            with self.assertRaises(ContractError):
                self.fixture.review()
        self.assertEqual(self.counts(), before)

    def test_final_commit_contains_owner_origin_and_original(self):
        parent = self.archive.get(self.fixture.parent['id'])
        parent['owner_account'] = 'admin'
        self.archive.save(parent)
        child = self.fixture.review()
        self.assertEqual(child.get('owner_account'), 'admin')
        self.assertEqual(child['revision'], 1)
        self.assertIn('document_review_origin', child)
        self.assertEqual(self.archive.get(child['id']), child)
        originals = self.archive.retained_originals(child)
        self.assertEqual(len(originals), 1)
        self.assertEqual(originals[0]['artifact_id'], child['materials'][0]['id'])
        self.assertEqual(self.archive.get(parent['id']), parent)

    def test_parent_changed_during_preparation_prevents_creation(self):
        before = self.counts()
        from researchhelper.engine import material_record

        def change_parent(*args, **kwargs):
            record = material_record(*args, **kwargs)
            parent = self.archive.get(self.fixture.parent['id'])
            parent['status'] = '待开始'
            self.archive.save(parent)
            return record

        with patch('researchhelper.engine.material_record', side_effect=change_parent):
            with self.assertRaises(ContractError):
                self.fixture.review()
        self.assertEqual(self.counts(), before)

    def test_version_insert_failure_rolls_back_task(self):
        before = self.counts()
        with self.archive.connect() as db:
            db.execute("CREATE TRIGGER fail_related_version BEFORE INSERT ON versions "
                       "WHEN NEW.task_id != '" + self.fixture.parent['id'] + "' "
                       "BEGIN SELECT RAISE(ABORT, 'injected version failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.fixture.review()
        self.assertEqual(self.counts(), before)

    def test_success_does_not_reparse_or_expose_intermediate_task(self):
        from researchhelper.engine import material_record
        before = self.counts()

        def check_unpublished(*args, **kwargs):
            self.assertEqual(self.counts(), before)
            return material_record(*args, **kwargs)

        with patch('researchhelper.engine.material_record', side_effect=check_unpublished), \
                patch('researchhelper.engine.extract_material', side_effect=AssertionError('duplicate parse')):
            child = self.fixture.review()
        self.assertEqual(child['status'], '待开始')
        self.assertEqual(child['ledger']['dispatches'], [])

    def test_follow_up_commits_complete_conversation_with_owner(self):
        parent = self.archive.get(self.fixture.parent['id'])
        parent['owner_account'] = 'sales'
        self.archive.save(parent)
        child = self.agent.follow_up(parent['id'], '继续研究政策影响', start=False)
        self.assertEqual(child['owner_account'], 'sales')
        self.assertEqual(child['revision'], 1)
        self.assertEqual(child['conversation_parent']['task_id'], parent['id'])
        self.assertEqual(child['thread_id'], parent['thread_id'])
        self.assertEqual(len(child['messages']), 3)
        self.assertEqual(self.archive.get(parent['id']), parent)

    def test_follow_up_failure_leaves_no_task_or_history_fragment(self):
        before = self.counts()
        with patch.object(self.agent, '_message', side_effect=[{'role': 'assistant'}, RuntimeError('injected history failure')]):
            with self.assertRaises(RuntimeError):
                self.agent.follow_up(self.fixture.parent['id'], '继续研究', start=False)
        self.assertEqual(self.counts(), before)

    def test_archive_rejects_owner_mismatch(self):
        before = self.counts()
        parent = self.archive.get(self.fixture.parent['id'])
        child = self.agent._prepare_task('继续研究', parent=parent['id'])
        child['owner_account'] = 'sales'
        with self.assertRaises(ContractError):
            self.archive.create_related(child, parent)
        self.assertEqual(self.counts(), before)


if __name__ == '__main__':
    unittest.main()
