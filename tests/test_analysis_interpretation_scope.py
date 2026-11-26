"""New analysis guidance scopes comparisons without approving conclusions."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests.legacy_replay_fixtures import financial_task
from researchhelper.model import ModelService, ModelSettings
from researchhelper.reasoning import analysis_contract, model_object, PHASE_FIELDS


class AnalysisInterpretationScopeTests(unittest.TestCase):
    def setUp(self):
        self.task = financial_task(both_ratios=True)
        self.task.update(status='部分完成', analysis_reporting_version=2)

    def test_reference_remains_specific_to_its_actual_method(self):
        contract = analysis_contract(self.task)
        methods = {item['method']:item for item in contract['by_question']['financial']['calculations']}
        self.assertEqual(methods['net_profit_margin_v1']['registered_reference_quantities'], {})
        self.assertEqual(methods['operating_cash_profit_ratio_v1']['registered_reference_quantities']['reference:parity']['value'],100)
        self.assertIn('单项计算', contract['client_writing']['reference_scope'])

    def test_directional_interpretation_requires_more_than_the_ratio(self):
        rules = analysis_contract(self.task)['client_writing']
        self.assertEqual(rules['version'], 2)
        self.assertIn('具体来源分解', rules['interpretation'])
        self.assertIn('不能夹带', rules['interpretation'])

    def test_customer_copy_keeps_limits_but_does_not_require_internal_jargon(self):
        rules = analysis_contract(self.task)['client_writing']
        self.assertIn('必要的无法识别和资料限制仍明确保留', rules['customer_copy'])
        self.assertIn('ResDesk', rules['customer_copy'])

    def test_legacy_tasks_keep_their_original_writing_contract(self):
        self.task.pop('analysis_reporting_version')
        rules = analysis_contract(self.task)['client_writing']
        self.assertEqual(rules['version'], 1)
        self.assertNotIn('interpretation', rules)

    def test_contract_does_not_change_facts_calculations_or_rejected_study(self):
        before = copy.deepcopy(self.task)
        analysis_contract(self.task)
        self.assertEqual(self.task, before)
        self.assertEqual(self.task['status'], '部分完成')
        self.assertFalse(self.task['theses'])

    def test_actual_analysis_prompt_and_receipt_include_the_versioned_rules(self):
        with tempfile.TemporaryDirectory(prefix='rh-interpretation-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/beta/chat/completions',
                model='deepseek-flash', api_key='FAKE_INTERPRETATION_KEY'))
            model = ModelService(settings, offline=False)
            with patch.object(model, 'reason_structured_phase', return_value='{"answers":{},"theses":[]}') as submit:
                _, receipt = model_object(model, self.task, 'analyze',
                    {'analysis_contract':analysis_contract(self.task)}, PHASE_FIELDS['analyze'])
            prompt = json.dumps(submit.call_args.args[1], ensure_ascii=False)
            self.assertIn('净利润率与现金流净利润比不是同一个量', prompt)
            self.assertIn('不能仅凭覆盖比偏高', prompt)
            self.assertEqual(receipt['analysis_reporting_version'], 2)
