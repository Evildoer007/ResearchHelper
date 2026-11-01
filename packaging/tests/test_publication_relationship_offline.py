"""New negative expectations for unregistered cross-original publication links.

Old test files are preserved. Their fabricated data, unit assertions, loopback
auth and checkpoint assertions are reused, but never described as approved
historical availability. The changed expectations are explicit in the receipt.
"""

import inspect
import textwrap
import unittest

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, validate_evidence
from researchhelper.evidence import candidate_evidence
from tests import test_market_background_sources_offline as market
from tests import test_autonomous_supplement_interfaces_offline as interfaces


class PublicationSourceBoundaryTests(unittest.TestCase):
    setUp = market.MarketBackgroundSourceTests.setUp
    snapshot = market.MarketBackgroundSourceTests.snapshot
    spec = market.MarketBackgroundSourceTests.spec

    def approve(self, artifact, spec):
        """Check extraction/review but require final availability rejection."""
        candidate = candidate_evidence(self.task, artifact, spec)
        item = ResearchAnalyzer().assess(self.task, candidate, spec["review"], self.task["source_records"])
        with self.assertRaisesRegex(ContractError, "不对应本条证据原件"):
            validate_evidence(item, self.task)
        return item

    def test_registered_field_units_survive_but_borrowed_publication_is_rejected(self):
        market.MarketBackgroundSourceTests.test_all_registered_fields_retain_units_and_two_verification_receipts(self)

    def test_missing_pe_and_negative_pb_preserved_without_false_availability(self):
        market.MarketBackgroundSourceTests.test_missing_pe_is_never_promoted_to_zero_and_negative_pb_is_preserved(self)


class PublicationAppBoundaryTests(unittest.TestCase):
    setUp = interfaces.SupplementInterfacesTests.setUp
    stop = interfaces.SupplementInterfacesTests.stop
    request = interfaces.SupplementInterfacesTests.request
    login = interfaces.SupplementInterfacesTests.login

    def test_supplier_supplement_preserves_answer_but_cannot_borrow_its_publication(self):
        original = interfaces.SupplementInterfacesTests.test_authenticated_app_directed_supplier_round_preserves_independent_answer
        source = textwrap.dedent(inspect.getsource(original))
        before = 'self.assertEqual(final["status"], "已完成",'
        if source.count(before) != 1:
            raise AssertionError("Inspect changed baseline before adapting publication expectation")
        source = source.replace(before, 'self.assertEqual(final["status"], "部分完成",')
        # Rejected supplement evidence cannot trigger the final analysis call.
        for before, after in (
                ('self.assertEqual(len(calls), 6)', 'self.assertEqual(len(calls), 5)'),
                ('self.assertEqual(final["ledger"]["used"]["model_calls"], 6)', 'self.assertEqual(final["ledger"]["used"]["model_calls"], 5)')):
            if source.count(before) != 1:
                raise AssertionError("Inspect changed model ledger gate before adapting")
            source = source.replace(before, after)
        marker = 'self.assertIsNotNone(final["delivery"])'
        if source.count(marker) != 1:
            raise AssertionError("Inspect changed delivery gate before adapting")
        source = source.replace(marker, marker + '\n    self.assertEqual(final["questions"][1]["status"], "未回答")\n'
            '    self.assertEqual([item["id"] for item in final["evidence"]], ["publication"])\n'
            '    self.assertTrue(any("不对应本条证据原件" in item.get("rejection", "") for item in final["rejected_evidence"]))')
        namespace = dict(vars(interfaces))
        exec(compile(source, __file__, "exec"), namespace)
        namespace[original.__name__](self)


REPLACED_EXPECTATIONS = {
    "tests.test_market_background_sources_offline.MarketBackgroundSourceTests.test_all_registered_fields_retain_units_and_two_verification_receipts":
        "tests.test_publication_relationship_offline.PublicationSourceBoundaryTests.test_registered_field_units_survive_but_borrowed_publication_is_rejected",
    "tests.test_market_background_sources_offline.MarketBackgroundSourceTests.test_missing_pe_is_never_promoted_to_zero_and_negative_pb_is_preserved":
        "tests.test_publication_relationship_offline.PublicationSourceBoundaryTests.test_missing_pe_and_negative_pb_preserved_without_false_availability",
    "tests.test_autonomous_supplement_interfaces_offline.SupplementInterfacesTests.test_authenticated_app_directed_supplier_round_preserves_independent_answer":
        "tests.test_publication_relationship_offline.PublicationAppBoundaryTests.test_supplier_supplement_preserves_answer_but_cannot_borrow_its_publication",
}
