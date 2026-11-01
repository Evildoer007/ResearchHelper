"""Missing data narrows conclusions instead of manufacturing obligations.

Offline policy regression. Existing partial-answer delivery tests separately
exercise preservation, citation checks and rejection of unsupported prose.
"""
import unittest

from tests import ROOT
from researchhelper.reasoning import COMMON_RULE, MISSING_DATA_RULE


class MissingDataResearchPolicyTests(unittest.TestCase):
    def test_policy_is_shared_by_research_phases(self):
        self.assertIn(MISSING_DATA_RULE, COMMON_RULE)

    def test_enrichment_is_not_an_automatic_required_obligation(self):
        self.assertIn('不因后者缺失阻断已有依据的研究', MISSING_DATA_RULE)
        self.assertIn('只限制直接依赖缺口', MISSING_DATA_RULE)

    def test_sample_analysis_does_not_claim_explicit_full_universe_completion(self):
        self.assertIn('明确实际样本与覆盖范围', MISSING_DATA_RULE)
        self.assertIn('不能用小样本或定性解释冒充完成', MISSING_DATA_RULE)

    def test_stopping_keeps_results_without_filling_or_substituting_silently(self):
        self.assertIn('交付已有结果并说明受影响的判断', MISSING_DATA_RULE)
        self.assertIn('缺失值不填零、不猜单位、不编曲线', MISSING_DATA_RULE)
        self.assertIn('不能静默冒充原指标', MISSING_DATA_RULE)


if __name__ == '__main__':
    unittest.main()
