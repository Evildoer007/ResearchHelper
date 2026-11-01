"""Observation rules, same-version dependencies and frozen history; no network."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint, tracking_contract, tracking_record, validate_tracking
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import analysis_contract
from researchhelper.reports import compose
from tests import test_research_coverage_offline as coverage_fixture

ROOT = Path(__file__).resolve().parents[2]
RULE = {'indicator': '政策适用资格', 'source': '政策公布机关原文', 'window': '下一次主动更新',
        'condition': '适用资格正式调整时重新核对原判断', 'benchmark': '本版已核验的适用条款'}


class TrackingContractTests(unittest.TestCase):
    def setUp(self):
        out = ROOT / 'result/acceptance/tracking-contract'
        out.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix='isolated-', dir=out)
        self.addCleanup(folder.cleanup)
        self.fixture = coverage_fixture.ResearchCoverageTests.__new__(coverage_fixture.ResearchCoverageTests)
        self.fixture.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)

    def complete(self, rule=RULE):
        task = self.fixture.task(kind='mechanism')
        thesis = self.fixture.thesis()
        thesis['tracking'] = copy.deepcopy(rule)
        return self.fixture.result(task, self.fixture.specs(task), theses=[thesis])

    def test_valid_rule_freezes_before_delivery_and_does_not_monitor(self):
        done = self.complete()
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(done['tracking'], [tracking_record(done, done['theses'][0])])
        self.assertEqual(done['tracking'][0]['condition_hash'], fingerprint(RULE))
        self.assertEqual(done['tracking'][0]['status'], '待主动复核')
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)

    def test_non_dictionary_rule_is_rejected_without_losing_verified_evidence(self):
        for rule in ('明天上涨', ['跟踪'], 0, False):
            with self.subTest(rule=rule):
                done = self.complete(rule)
                self.assertEqual(done['status'], '部分完成', done.get('error'))
                self.assertEqual(done['tracking'], [])
                self.assertEqual(done['theses'], [])
                self.assertEqual(len(done['evidence']), 2)
                self.assertIn('验证条件无效', ' '.join(done['gaps']))

    def test_missing_empty_and_non_string_fields_are_rejected(self):
        for field in RULE:
            for invalid in ('', '  ', [], 3, None):
                rule = dict(RULE, **{field: invalid})
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ContractError):
                    validate_tracking(rule)
        for field in tracking_contract()['required_fields']:
            rule = dict(RULE)
            rule.pop(field)
            with self.assertRaises(ContractError):
                validate_tracking(rule)
        with self.assertRaises(ContractError):
            validate_tracking({})

    def test_host_cannot_supply_status_identity_or_monitoring_state(self):
        for field in ('task_id', 'research_version', 'thesis_id', 'status', 'probability', 'watcher'):
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_tracking(dict(RULE, **{field: '伪造'}))

    def test_no_benchmark_is_not_replaced_by_a_fabricated_baseline(self):
        rule = dict(RULE)
        rule.pop('benchmark')
        done = self.complete(rule)
        self.assertEqual(done['status'], '已完成')
        self.assertNotIn('benchmark', done['tracking'][0])

    def test_non_applicable_mechanism_and_static_comparison_do_not_create_rules(self):
        done = self.complete(None)
        self.assertEqual(done['status'], '已完成')
        self.assertEqual(done['tracking'], [])
        case = self.fixture.agent.create_case('etf')
        direct = self.fixture.agent.start(case['id'])
        self.assertEqual(direct['status'], '已完成')
        self.assertEqual(direct['tracking'], [])

    def test_report_rechecks_changed_omitted_and_unattached_rules(self):
        done = self.complete()
        for corruption in ('condition', 'benchmark', 'missing', 'extra'):
            task = copy.deepcopy(done)
            if corruption == 'missing':
                task['tracking'] = []
            elif corruption == 'extra':
                task['tracking'].append(copy.deepcopy(task['tracking'][0]))
            else:
                task['tracking'][0][corruption] = '事后改写'
            with self.subTest(corruption=corruption), self.assertRaises(ContractError):
                compose(task)

    def test_report_rejects_foreign_task_version_thesis_and_stale_hash(self):
        done = self.complete()
        for field in ('task_id', 'research_version', 'thesis_id', 'thesis_hash', 'condition_hash', 'status'):
            task = copy.deepcopy(done)
            task['tracking'][0][field] = '不属于本次交付'
            with self.subTest(field=field), self.assertRaises(ContractError):
                compose(task)

    def test_report_contains_actual_baseline_and_escapes_markup(self):
        done = self.complete(dict(RULE, benchmark='<script>不执行</script>'))
        exported = self.fixture.agent.export(done['id'])
        self.assertIn('比较基准：&lt;script&gt;不执行&lt;/script&gt;', exported['html'])
        self.assertEqual(self.fixture.agent.archive.report(done['id'], 1)['content']['tracking'], done['tracking'])

    def test_existing_checkpoint_four_field_rules_remain_composable(self):
        done = self.complete()
        legacy = copy.deepcopy(done)
        legacy.pop('tracking_contract_version')
        for rule in legacy['tracking']:
            for field in ('task_id', 'research_version', 'thesis_hash', 'condition_hash'):
                rule.pop(field)
        self.assertEqual(compose(legacy)['tracking'], legacy['tracking'])

    def test_update_preserves_original_conditions_and_frozen_report(self):
        done = self.complete()
        original = copy.deepcopy(self.fixture.agent.archive.report(done['id'], 1))
        updated = self.fixture.agent.update(done['id'], cutoff='2026-08-20T18:00:00+08:00')
        self.assertEqual(updated['update_from']['conditions'], done['tracking'])
        self.assertEqual(self.fixture.agent.archive.report(done['id'], 1), original)
        self.assertEqual(done['tracking'][0]['research_version'], 1)

    def test_app_and_skill_get_the_same_optional_tracking_contract(self):
        done = self.complete()
        contract = analysis_contract(done)
        self.assertEqual(contract['tracking'], tracking_contract())
        self.assertEqual(contract['optional_thesis_fields'], ['tracking'])
        spec = importlib.util.spec_from_file_location('tracking_skill', ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session.__new__(module.Session)
        session.archive = self.fixture.agent.archive
        self.assertEqual(session.tools(done['id'])['analysis'], contract)
        class Host:
            def reason(self, task, messages):
                self.messages = messages
                return '{"answers":{},"theses":[]}'
        host = Host()
        self.fixture.agent.model = host
        self.fixture.agent._reason_on_verified_material(done)
        self.assertEqual(json.loads(host.messages[1]['content'])['analysis_contract']['tracking'], contract['tracking'])


if __name__ == '__main__':
    unittest.main()
