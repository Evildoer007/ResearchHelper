"""The extraction scheduler explicitly authorizes same-question API reading."""
import unittest
from unittest.mock import patch
from researchhelper.engine import ResearchAgent

class MixedAtomicScopeTests(unittest.TestCase):
    def setUp(self):
        self.agent=ResearchAgent.__new__(ResearchAgent)
        self.task=dict(mixed_atomic_api_reading_version=1,questions=[dict(id='q1')])
        self.views=[dict(id='pdf',kind='filing')]
        self.current=self.views+[dict(id='api',kind='api'),dict(id='other',kind='api')]
        self.records=[dict(id='pdf',question_ids=['q1']),dict(id='api',question_ids=['q1'],data_need_ids=['new']),
            dict(id='other',question_ids=['q2'])]
        for name,value in (('original_sources',self.records),('extractable_source_views',self.current)):
            control=patch('researchhelper.engine.'+name,return_value=value);control.start();self.addCleanup(control.stop)

    def test_current_same_question_api_is_explicitly_authorized(self):
        self.assertEqual(self.agent._reading_artifact_ids(self.task,self.views),['pdf','api'])

    def test_supplement_does_not_borrow_unrequested_api(self):
        active=dict(affected_questions=['q1'],proposal=dict(artifact_ids=['pdf'],data_requests=[]))
        self.assertEqual(self.agent._reading_artifact_ids(self.task,self.views,active),['pdf'])
        active['proposal']['data_requests']=[dict(id='new')]
        self.assertEqual(self.agent._reading_artifact_ids(self.task,self.views,active),['pdf','api'])

    def test_old_scope_remains_unchanged(self):
        self.task.pop('mixed_atomic_api_reading_version')
        self.assertEqual(self.agent._reading_artifact_ids(self.task,self.views),['pdf'])
