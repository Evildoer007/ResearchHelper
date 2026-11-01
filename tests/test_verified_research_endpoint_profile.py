"""Maintenance configuration validation using only an isolated synthetic Store."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from maintenance import apply_verified_model_profile as profile
from researchhelper.model import ModelSettings

ROOT = Path(__file__).resolve().parents[1]


class VerifiedResearchEndpointProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='verified-profile-', dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); (self.root/'result/acceptance').mkdir(parents=True)
        self.settings = ModelSettings(self.root/'store')
        self.settings.save(dict(endpoint='https://api.deepseek.com/chat/completions', model='deepseek-flash',
            api_key='SYNTHETIC_LOCAL_ONLY', extraction_mode='direct', planning_effort='low', reasoning_effort='low'))
        self.study = dict(backend_mode='readonly_dmg', ordinary_chat_report_completed=True,
            financial_research_completed=True, production_unchanged=True,
            temporary_model_controls=dict(extraction_mode='direct',planning_effort='low',reasoning_effort='low'),
            temporary_endpoint_override=dict(endpoint='https://api.deepseek.com/beta/chat/completions',
                production_endpoint_changed=False), task_id='synthetic_task',
            ledger=dict(dispatches=[dict(kind='model_calls',requested_model='deepseek-flash')]))
        self.visual = dict(status='scoped_passed', report_accepted=True, task_id='synthetic_task')
        self.paths = [self.root/'result/acceptance'/name for name in ('study.json','review.json')]
        original = ModelSettings; store = self.root/'store'
        class IsolatedSettings(original):
            def __init__(self, ignored): super().__init__(store)
        self.addCleanup(patch.stopall)
        patch.object(profile,'ROOT',self.root).start()
        patch('researchhelper.model.ModelSettings',IsolatedSettings).start()
        patch('socket.socket.connect',side_effect=AssertionError('No external calls')).start()

    def apply(self):
        for path, value in zip(self.paths,(self.study,self.visual)):
            path.write_text(json.dumps(value))
        return profile.apply_research_endpoint(*self.paths)

    def test_verified_profile_changes_only_endpoint_and_retains_key_model(self):
        before = self.settings.read(private=True)
        result = self.apply(); after = self.settings.read(private=True)
        self.assertEqual(result['status'],'applied_verified_research_endpoint')
        self.assertEqual(after,dict(before,endpoint='https://api.deepseek.com/beta/chat/completions'))
        self.assertNotIn('SYNTHETIC_LOCAL_ONLY',Path(result['receipt']).read_text())

    def test_source_only_or_partial_study_cannot_change_settings(self):
        before = self.settings.read(private=True)
        for field, value in (('backend_mode','development_source'),('financial_research_completed',False),
                             ('production_unchanged',False)):
            original = copy.deepcopy(self.study); self.study[field] = value
            with self.assertRaises(RuntimeError): self.apply()
            self.study = original
        self.assertEqual(self.settings.read(private=True),before)

    def test_failed_or_other_task_report_is_not_sufficient(self):
        for field,value in (('status','failed'),('task_id','another_task'),('report_accepted',False)):
            original = copy.deepcopy(self.visual); self.visual[field] = value
            with self.assertRaises(RuntimeError): self.apply()
            self.visual = original

    def test_injected_study_or_changed_model_is_rejected(self):
        for field in ('supplied_answers','supplied_evidence','supplied_plan','task_retry'):
            self.study[field] = True
            with self.assertRaises(RuntimeError): self.apply()
            self.study.pop(field)
        self.study['ledger']['dispatches'][0]['requested_model']='different'
        with self.assertRaises(RuntimeError): self.apply()

    def test_other_provider_cannot_be_silently_replaced(self):
        config = self.settings.read(private=True); config['endpoint']='https://example.invalid/chat/completions'
        config.update(extraction_mode='default',planning_effort='inherit',reasoning_effort='default')
        self.settings.save(config)
        before = self.settings.read(private=True)
        with self.assertRaises(RuntimeError): self.apply()
        self.assertEqual(self.settings.read(private=True),before)

    def test_missing_or_foreign_receipt_path_is_rejected(self):
        with self.assertRaises(RuntimeError): profile.apply_research_endpoint('/not-present/study.json',self.paths[1])
