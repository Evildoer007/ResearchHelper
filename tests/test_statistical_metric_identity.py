"""Validate the closed R129 selections locally; no task or API is resumed."""
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, canonical, fingerprint, validate_evidence
from researchhelper.evidence import candidate_evidence
from researchhelper.planning import input_fingerprint
from researchhelper.statistical_metrics import POLICY, enabled, metrics, validate_level

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT/'result/acceptance/one-sentence-research-m4v02ejg'
BAD = ('pmi_input_prices_sep', 'pmi_ex_factory_prices_sep', 'pmi_business_expectation_sep')
FIELDS = ('id', 'artifact_id', 'object', 'nature', 'question_ids', 'text', 'period', 'record_locator',
          'published_at', 'publication', 'availability', 'unit', 'observation_period',
          'raw_token', 'raw_value', 'raw_unit', 'value', 'conversion_factor')


def prepare_fixture():
    names = ('receipt.json', 'task.json', 'report.json', 'report.html')
    protected = {name: hashlib.sha256((STUDY/name).read_bytes()).hexdigest() for name in names}
    task = json.loads((STUDY/'task.json').read_text())
    assert protected == {name: hashlib.sha256((STUDY/name).read_bytes()).hexdigest() for name in names}
    return dict(task=task, protected=protected, synthetic_policy_overlay=True, original_research_reopened=False)


class StatisticalMetricIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_STATISTICAL_IDENTITY_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes())) if supplied else prepare_fixture()

    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline identity review only'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task = copy.deepcopy(self.fixture['task'])
        self.task[POLICY] = 1; self.task['input_hash'] = input_fingerprint(self.task)
        self.original = canonical(self.task)

    def tearDown(self):
        self.assertEqual(canonical(self.task), self.original)

    def proposal(self, ident):
        retained = next(item for item in self.task['evidence'] if item['id'] == ident)
        source = next(source for source in self.task['source_records'] if source['id'] == retained['artifact_id'])
        spec = {key: copy.deepcopy(retained[key]) for key in FIELDS if key in retained}
        review = {key: copy.deepcopy(value) for key, value in retained['applicability_review'].items()
                  if key != 'evidence_need_ids'}
        return source, spec, review

    def assess(self, ident):
        source, spec, review = self.proposal(ident)
        item = candidate_evidence(self.task, source, spec)
        ResearchAnalyzer().assess(self.task, item, review, self.task['source_records'])
        validate_evidence(item, self.task)
        return item

    def test_all_eight_real_level_observations_still_pass_without_changing_ledger_or_old_files(self):
        from researchhelper.reasoning import evidence_contract, extractable_source_views
        contract = evidence_contract(self.task, extractable_source_views(self.task))
        rules = [source['statistical_metric_identity_rule'] for source in contract['sources']
                 if 'statistical_metric_identity_rule' in source]
        self.assertEqual(len(rules), 2)
        self.assertTrue(all(rule['source_or_applicability_approved'] is False for rule in rules))
        good = [item['id'] for item in self.task['evidence'] if item['id'] not in BAD]
        self.assertEqual(len(good), 8)
        for ident in good:
            with self.subTest(ident=ident):
                item = self.assess(ident)
                self.assertIsNone(item['published_at'])
                self.assertEqual(item['publication']['certainty'], '未知')
        self.assertEqual(self.task['ledger']['used'], self.fixture['task']['ledger']['used'])

    def test_three_real_mislabeled_selections_fail_before_applicability_approval(self):
        for ident in BAD:
            source, spec, review = self.proposal(ident)
            item = candidate_evidence(self.task, source, spec)
            self.assertEqual(item['source_check'], '通过')
            with self.subTest(ident=ident), self.assertRaises(ContractError):
                ResearchAnalyzer().assess(self.task, item, review, self.task['source_records'])
            self.assertEqual(item['applicability_check'], '待核验')
            self.assertNotIn('applicability_receipt', item)

    def test_an_old_passed_receipt_does_not_bypass_a_new_tasks_identity_gate(self):
        for ident in BAD:
            item = copy.deepcopy(next(item for item in self.task['evidence'] if item['id'] == ident))
            with self.subTest(ident=ident), self.assertRaises(ContractError): validate_evidence(item, self.task)

    def test_true_production_value_cannot_cover_a_new_orders_need(self):
        item = copy.deepcopy(self.task['evidence'][1])
        need = self.task['questions'][0]['evidence_needs'][2]
        with self.assertRaises(ContractError): validate_level(item, self.task, {'q1': [fingerprint(need)]})

    def test_name_forgery_is_rejected_even_when_the_number_and_unit_are_real(self):
        item = copy.deepcopy(self.task['evidence'][0]); item['object'] = '制造业PMI出厂价格指数'
        with self.assertRaises(ContractError):
            validate_level(item, self.task, item['applicability_review']['evidence_need_ids'])

    def test_level_cannot_borrow_a_change_unit_or_convert_another_quantity(self):
        baseline = self.task['evidence'][1]
        for change in (dict(unit='个百分点'), dict(raw_unit='个百分点'), dict(conversion_factor=100),
                       dict(conversion_factor=True), dict(nature='本次推算'), dict(value=1.3),
                       dict(raw_token='1.3', raw_value=1.3, value=1.3)):
            item = dict(copy.deepcopy(baseline), **change)
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_level(item, self.task, item['applicability_review']['evidence_need_ids'])

    def test_previous_month_value_cannot_borrow_current_month_context(self):
        item = copy.deepcopy(self.task['evidence'][3])
        item.update(period='2026-09', observation_period=dict(frequency='month', value='2026-09'))
        with self.assertRaises(ContractError):
            validate_level(item, self.task, item['applicability_review']['evidence_need_ids'])

    def test_nonmanufacturing_section_cannot_supply_a_manufacturing_metric(self):
        item = copy.deepcopy(self.task['evidence'][7])
        item['record_locator']['part'] = '段落34'
        with self.assertRaises(ContractError):
            validate_level(item, self.task, item['applicability_review']['evidence_need_ids'])

    def test_one_scalar_does_not_cover_two_different_indices(self):
        item = copy.deepcopy(self.task['evidence'][1]); item['object'] = '国家统计局'
        needs = self.task['questions'][0]['evidence_needs'][1:3]
        with self.assertRaises(ContractError):
            validate_level(item, self.task, {'q1': [fingerprint(text) for text in needs]})

    def test_no_automatic_upgrade_of_closed_tasks(self):
        task = copy.deepcopy(self.fixture['task']); before = canonical(task)
        self.assertFalse(enabled(task))
        for ident in BAD:
            item = next(item for item in task['evidence'] if item['id'] == ident)
            self.assertIsNone(validate_level(item, task, item['applicability_review']['evidence_need_ids']))
        self.assertEqual(canonical(task), before)

    def test_unregistered_policy_values_do_not_enable_the_gate(self):
        for value in (True, 0, 2, '1'):
            task = copy.deepcopy(self.task); task[POLICY] = value
            with self.subTest(value=value), self.assertRaises(ContractError): enabled(task)

    def test_statistical_names_do_not_confuse_the_common_prefix_with_a_headline(self):
        self.assertEqual(metrics('国家统计局制造业PMI新订单指数'), {'new_orders'})
        self.assertEqual(metrics('国家统计局制造业PMI总体指数'), {'headline'})
        self.assertEqual(metrics('制造业PMI主要原材料购进价格指数'), {'input_prices'})

    def test_other_modes_and_sources_do_not_gain_this_adapters_permission(self):
        for key, value in (('research_mode', 'historical'), ('intent', '原件内容核对')):
            task = dict(self.task, **{key: value})
            self.assertFalse(enabled(task))
        item = copy.deepcopy(self.task['evidence'][0]); item['source'] = 'ifind'
        self.assertIsNone(validate_level(item, self.task, item['applicability_review']['evidence_need_ids']))

    def test_policy_is_bound_to_new_tasks_and_frozen_reports(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        from researchhelper.reports import compose
        folder = tempfile.TemporaryDirectory(prefix='metric-identity-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(agent.close_runtime)
        task = agent.create('解释指数水平与环比变化的区别', research_mode='current')
        self.assertEqual(task[POLICY], 1)
        agent._adopt_method_knowledge(task); task['status'] = '部分完成'
        agent._gap(task, '离线身份契约检查，不是实际研究', category='question_coverage')
        self.assertEqual(compose(task)[POLICY], 1)
        previous = copy.deepcopy(task); previous.pop(POLICY)
        self.assertNotEqual(input_fingerprint(previous), input_fingerprint(task))


if __name__ == '__main__': unittest.main()
