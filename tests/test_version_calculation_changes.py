"""Independent update comparisons; no provider, model or production Store."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.reports import build_version_changes, validate_version_changes


def prior_content():
    return dict(task_id='rh_comparison', research_version=1, cutoff='2026-07-31',
        evidence=[dict(id='e1', object='合成样本', source='合成原件', period='本期',
            observation_date='2026-06-30', nature='事实', unit='元', value=10,
            field_definition={'meaning': '合成金额'}, text='仅用于离线检查')],
        theses=[], tracking=[], knowledge={'index': {}}, calculations=[dict(
            id='c1', method='synthetic_method', method_title='合成方法',
            method_binding={'implementation_version': 1}, inputs={'amount': 10},
            parameters={'basis': 'current'}, input_refs=['e1'], question_ids=['q1'],
            assumptions={}, output={'value': 10})])


def updated_task(content):
    task=copy.deepcopy(content)
    task.pop('task_id');task.pop('research_version')
    task.update(id=content['task_id'], version=2, cutoff='2026-08-31',
        version_changes_contract=2, update_from=dict(version=1, digest=fingerprint(content),
            conditions=copy.deepcopy(content['tracking']), knowledge_versions={}))
    return task


class VersionCalculationChangesTests(unittest.TestCase):
    def setUp(self):
        self.before=prior_content()
        self.report=dict(content=self.before, digest=fingerprint(self.before))
        self.task=updated_task(self.before)

    def changes(self):
        return build_version_changes(self.task, self.report)

    def test_new_comparison_has_calculation_baselines(self):
        change=self.changes()
        self.assertEqual(change['contract_version'], 2)
        self.assertEqual(change['prior_basis']['calculations'][0]['inputs'], {'amount': 10})
        self.assertEqual(change['calculation_changes']['unchanged'], ['c1'])

    def test_input_value_change_is_explained(self):
        self.task['calculations'][0]['inputs']['amount']=11
        self.assertEqual(self.changes()['calculation_changes']['revised'],
            [{'id': 'c1', 'fields': ['inputs']}])

    def test_method_implementation_change_is_explained(self):
        self.task['calculations'][0]['method_binding']['implementation_version']=2
        self.assertEqual(self.changes()['calculation_changes']['revised'],
            [{'id': 'c1', 'fields': ['method_binding']}])

    def test_parameters_and_output_are_separate_changes(self):
        self.task['calculations'][0].update(parameters={'basis': 'other'}, output={'value': 9})
        self.assertEqual(self.changes()['calculation_changes']['revised'],
            [{'id': 'c1', 'fields': ['output', 'parameters']}])

    def test_changed_input_evidence_is_not_hidden_by_equal_numbers(self):
        self.task['evidence'][0]['observation_date']='2026-07-31'
        change=self.changes()
        self.assertEqual(change['revised'], ['e1'])
        self.assertEqual(change['calculation_changes']['revised'],
            [{'id': 'c1', 'fields': ['input_evidence']}])

    def test_changed_field_definition_is_retained(self):
        self.task['evidence'][0]['field_definition']['meaning']='另一合成口径'
        change=self.changes()
        self.assertEqual(change['revised'], ['e1'])
        self.assertIn('input_evidence', change['calculation_changes']['revised'][0]['fields'])

    def test_changed_input_references_are_retained(self):
        self.task['calculations'][0]['input_refs']=[]
        self.assertEqual(self.changes()['calculation_changes']['revised'],
            [{'id': 'c1', 'fields': ['input_evidence', 'input_refs']}])

    def test_added_and_missing_calculations_are_distinct(self):
        self.task['calculations'][0]['id']='c2'
        change=self.changes()['calculation_changes']
        self.assertEqual(change['added'], ['c2']);self.assertEqual(change['missing'], ['c1'])
        self.assertEqual(change['revised'], [])

    def test_new_receipts_are_not_calculation_changes(self):
        self.task['calculations'][0].update(research_version=2, batch=5,
            task_id='rh_comparison', dependency_hash='new-binding', input_hash='new-receipt')
        self.assertEqual(self.changes()['calculation_changes']['unchanged'], ['c1'])

    def test_missing_legacy_calculation_baseline_is_unknown_not_added(self):
        self.before.pop('calculations')
        self.report['digest']=fingerprint(self.before)
        self.task['update_from']['digest']=self.report['digest']
        change=self.changes()['calculation_changes']
        self.assertFalse(change['baseline_available'])
        self.assertEqual(change['added'], []);self.assertEqual(change['revised'], [])

    def test_old_contract_keeps_old_projection_and_validation(self):
        self.task['version_changes_contract']=1
        self.task['changes']=self.changes()
        self.assertNotIn('calculation_changes', self.task['changes'])
        self.assertNotIn('calculations', self.task['changes']['prior_basis'])
        self.assertEqual(self.task['changes']['contract_version'], 1)
        validate_version_changes(self.task, self.report)

    def test_unknown_and_boolean_contract_versions_are_rejected(self):
        for version in (0, 3, True, False, '2'):
            with self.subTest(version=version), self.assertRaises(ContractError):
                task=dict(self.task, version_changes_contract=version)
                validate_version_changes(task, self.report)

    def test_tampered_calculation_change_is_rejected(self):
        self.task['changes']=self.changes()
        self.task['changes']['calculation_changes']['unchanged']=[]
        with self.assertRaises(ContractError):validate_version_changes(self.task, self.report)

    def test_tampered_calculation_baseline_is_rejected(self):
        self.task['changes']=self.changes()
        self.task['changes']['prior_basis']['calculations'][0]['inputs']['amount']=99
        with self.assertRaises(ContractError):validate_version_changes(self.task, self.report)

    def test_report_digest_and_original_are_immutable(self):
        before=copy.deepcopy(self.report)
        self.changes()
        self.assertEqual(before, self.report)
        self.report['content']['calculations'][0]['output']['value']=99
        with self.assertRaises(ContractError):self.changes()

    def test_same_ordinal_theses_are_not_matched(self):
        self.before['theses']=[dict(id='t1', title='原判断', judgment='支持')]
        self.report['digest']=fingerprint(self.before)
        self.task['update_from']['digest']=self.report['digest']
        self.task['theses']=[dict(id='t1', title='另一判断', judgment='反对')]
        change=self.changes()
        self.assertNotIn('matches', change)
        self.assertNotEqual(change['prior_basis']['theses'], change['current_basis']['theses'])

    def test_duplicate_calculation_identity_is_rejected(self):
        self.task['calculations'].append(copy.deepcopy(self.task['calculations'][0]))
        with self.assertRaises(ContractError):self.changes()

    def test_presentation_rows_retain_both_versions_without_calculating(self):
        from researchhelper.reports import calculation_change_rows
        self.task['calculations'][0].update(method_title='<script>bad()</script>', inputs={'amount': 11})
        rows=calculation_change_rows(self.changes())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], '已变更')
        inputs=next(item for item in rows[0]['details'] if item['field']=='inputs')
        self.assertEqual(inputs['before_text'], '金额：10')
        self.assertEqual(inputs['after_text'], '金额：11')
        self.assertEqual(rows[0]['fields'], ['inputs', 'method_title'])


class VersionCalculationWorkflowTests(unittest.TestCase):
    def setUp(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        import researchhelper.engine as implementation
        folder=tempfile.TemporaryDirectory(prefix='version-calculation-fixture-',
            dir=Path(__file__).resolve().parents[1]/'result/acceptance')
        self.addCleanup(folder.cleanup)
        origin=getattr(implementation, '__frozen_origin__', None)
        resources=Path(origin.split('!/PYZ.pyz/')[0]).parents[2] if origin else None
        self.agent=ResearchAgent(ResearchArchive(folder.name), resources, offline=True)
        self.addCleanup(self.agent.close_runtime)
        guard=patch('socket.socket.connect', side_effect=AssertionError('No external connection'))
        guard.start();self.addCleanup(guard.stop)

    def first(self):
        task=self.agent.create_case('etf')
        done=self.agent.start(task['id'])
        self.assertTrue(done['delivery'], done.get('error'))
        return done

    def test_engine_report_and_frozen_history_share_v2(self):
        first=self.first()
        frozen=copy.deepcopy(self.agent.archive.report(first['id'], 1))
        after=self.agent.update(first['id'], cutoff='2026-10-16T18:00:00+08:00')
        self.assertTrue(after['delivery'], after.get('error'))
        self.assertEqual(after['version_changes_contract'], 2)
        saved=self.agent.archive.report(after['id'], 2)
        self.assertEqual(saved['content']['changes'], after['changes'])
        self.assertEqual(self.agent.archive.report(first['id'], 1), frozen)
        self.assertEqual(after['ledger']['used']['model_calls'], 0)
        self.assertEqual(after['ledger']['used']['data_requests'], 0)

    def test_update_context_retains_prior_calculations_without_current_approval(self):
        first=self.first()
        updated=self.agent.prepare_update(first['id'], cutoff='2026-10-16T18:00:00+08:00')
        context=self.agent.review_baseline(updated)
        self.assertTrue(context['prior_calculations_retained'])
        self.assertEqual(context['prior_calculations'][0]['inputs'], first['calculations'][0]['inputs'])
        self.assertEqual(updated['calculations'], [])

    def test_save_failure_recovers_same_diff_without_new_research(self):
        first=self.first()
        with patch.object(self.agent.archive, 'deliver', side_effect=OSError('合成保存失败')):
            after=self.agent.update(first['id'], cutoff='2026-10-16T18:00:00+08:00')
        self.assertEqual(after['status'], '失败')
        changes=copy.deepcopy(after['changes'])
        used=copy.deepcopy(after['ledger']['used'])
        delivery=self.agent.retry_save(first['id'])
        self.assertEqual(delivery['version'], 2)
        saved=self.agent.archive.get(first['id'])
        self.assertEqual(saved['changes'], changes)
        self.assertEqual(saved['ledger']['used']['model_calls'], used['model_calls'])
        self.assertEqual(saved['ledger']['used']['data_requests'], used['data_requests'])

    def test_presentation_escapes_comparison_content_and_does_not_alter_history(self):
        from researchhelper.visual import render
        first=self.first()
        after=self.agent.update(first['id'], cutoff='2026-10-16T18:00:00+08:00')
        frozen=self.agent.archive.report(first['id'], 2)
        preview=copy.deepcopy(frozen['content'])
        preview['changes']['calculation_rows']=[dict(status='已变更',
            before=None, after=dict(method='synthetic', method_title='<script>bad()</script>'),
            details=[dict(label='输入值', before_text='<img src=x onerror=bad()>', after_text='金额：11')])]
        # A separate, explicitly synthetic presentation fixture, never a saved report.
        preview['content_hash']=fingerprint({k:v for k,v in preview.items() if k!='content_hash'})
        page=render(preview, self.agent.resources)
        self.assertIn('&lt;script&gt;bad()&lt;/script&gt;', page)
        self.assertNotIn('<script>bad()</script>', page)
        self.assertIn('&lt;img src=x onerror=bad()&gt;', page)
        self.assertIn('金额：11', page)
        self.assertEqual(self.agent.archive.report(first['id'], 2), frozen)


if __name__=='__main__':unittest.main()
