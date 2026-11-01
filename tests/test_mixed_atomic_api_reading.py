"""Mixed reading retains only complete, current, explicitly allowed API views."""

import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper import source_reader as reader


class MixedAtomicReadingTests(unittest.TestCase):
    def setUp(self):
        text = '营业收入' + '正文' * 45000
        self.task = dict(id='task', version=1, input_hash='input', questions=[dict(id='q')],
                         mixed_atomic_api_reading_version=1)
        self.records = []
        self.views = []
        for ident, kind, snapshot in (
                ('doc', 'material', dict(parts=[dict(locator='page1', text=text)])),
                ('api', 'api', dict(tables=[dict(value=[1, 2, 3])])),
                ('raw', 'api', dict(unregistered='raw only'))):
            digest = fingerprint(snapshot)
            record = dict(id=ident, kind=kind, source='fixture', content_version=1,
                          task_id='task', research_version=1, snapshot=snapshot,
                          snapshot_hash=digest, hash=digest, operation='fixture', status='已解析')
            self.records.append(record)
            view = dict(id=ident, kind=kind, source='fixture', content_version=1,
                        snapshot_hash=digest)
            view.update(dict(parts=copy.deepcopy(snapshot['parts'])) if kind == 'material'
                        else dict(raw_response=copy.deepcopy(snapshot)))
            self.views.append(view)
        self.task['source_records'] = self.records
        self.request = [dict(artifact_id='doc', snapshot_hash=self.records[0]['hash'],
                             queries=[], parts=[dict(part='page1', start=0, end=100)],
                             reason='核对', question_ids=['q'])]
        self.patches = [patch.object(reader, 'source_views', side_effect=lambda task: copy.deepcopy(self.views)),
                        patch('researchhelper.reasoning.extractable_source_views',
                              side_effect=lambda task: copy.deepcopy(self.views[:2])),
                        patch.object(reader, '_verify_original_text')]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def read(self, ids=None):
        return reader.read(self.task, self.request, reader.binding(self.task), ids)

    def test_selected_context_and_read_preserve_same_complete_api(self):
        context = reader.context_sources(self.task, ['doc', 'api', 'raw'])
        self.assertEqual(context['mode'], 'selected')
        bundle = self.read(['doc', 'api', 'raw'])
        atomic = [view for view in bundle['views'] if view['kind'] == 'api']
        self.assertEqual(atomic, context['views'])
        self.assertEqual(atomic, [self.views[1]])
        self.assertFalse(bundle['mixed_atomic_api_reading']['financial_approval'])
        self.assertEqual(next(row for row in bundle['coverage'] if row['artifact_id'] == 'raw')['mode'], 'not_read')
        reader.validate_reading(self.task, bundle)

    def test_allowlist_never_expands_to_other_api(self):
        bundle = self.read(['doc'])
        self.assertEqual([view['id'] for view in bundle['views']], ['doc'])
        self.assertEqual(bundle['mixed_atomic_api_reading']['atomic_artifacts'], [])
        reader.validate_reading(self.task, bundle)

    def test_foreign_task_or_changed_content_rejected(self):
        self.records[1]['task_id'] = 'foreign'
        with self.assertRaises(ContractError):
            self.read(['doc', 'api'])
        self.records[1]['task_id'] = 'task'
        self.records[1]['content_version'] = 2
        with self.assertRaises(ContractError):
            self.read(['doc', 'api'])

    def test_tampered_reading_fails_even_after_rehash(self):
        bundle = self.read(['doc', 'api'])
        bundle['views'][0]['raw_response']['tables'][0]['value'].pop()
        bundle['reading_hash'] = fingerprint({key: value for key, value in bundle.items() if key != 'reading_hash'})
        with self.assertRaises(ContractError):
            reader.validate_reading(self.task, bundle)

    def test_old_tasks_unchanged_and_version_strict(self):
        self.task.pop('mixed_atomic_api_reading_version')
        bundle = self.read(['doc', 'raw'])
        self.assertNotIn('mixed_atomic_api_reading', bundle)
        self.assertNotIn('mixed_atomic_api_reading_version', bundle['binding'])
        self.assertIn('raw', [view['id'] for view in bundle['views']])
        for invalid in (True, 1.0, 2):
            self.task['mixed_atomic_api_reading_version'] = invalid
            with self.assertRaises(ContractError):
                self.read(['doc', 'api'])

    def test_over_budget_api_is_omitted_whole(self):
        self.views[1]['raw_response']['large'] = 'x' * reader.CONTEXT_LIMIT
        bundle = self.read(['doc', 'api'])
        self.assertNotIn('api', [view['id'] for view in bundle['views']])
        self.assertFalse(next(row for row in bundle['coverage'] if row['artifact_id'] == 'api')['included'])
