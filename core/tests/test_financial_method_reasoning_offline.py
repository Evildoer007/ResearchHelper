"""Financial reasoning contracts; synthetic local originals, no provider/model calls."""
import base64
import copy
import unittest

from researchhelper.contracts import ContractError, METHOD_REGISTRY, fingerprint
from researchhelper.evidence import extract_material
from researchhelper.reasoning import validate_model_specs, validate_plan


class FinancialMethodReasoningTests(unittest.TestCase):
    def setUp(self):
        self.task = {"question": "核对已披露材料中的价格变化、净值折溢价和营业毛利率", "intent": "综合研究",
                     "objects": ["本地合成主体"], "cutoff": "2026-07-20T18:00:00+08:00", "batch": 1,
                     "question_origin": "default", "questions": [{"id": "q1", "question": "核对已披露材料中的价格变化、净值折溢价和营业毛利率", "required": True}]}
        self.dynamic = {
            "observed_price_return_v1": {"start_date": "2026-07-01", "end_date": "2026-07-10"},
            "etf_nav_premium_v1": {"valuation_time": "2026-07-10T15:00:00+08:00"},
            "gross_margin_v1": {"period": "2026H1", "accounting_scope": "consolidated", "accounting_standard": "CAS"},
        }

    def plan(self, method):
        definition = METHOD_REGISTRY[method]
        spec = {"id": "calc_one", "method": method,
                "inputs": {key: "e_" + key for key in definition["input_units"]},
                "parameters": dict(definition["parameters"], **self.dynamic.get(method, {})),
                "assumptions": {}, "question_ids": ["q1"]}
        return {"questions": [{"id": "q1", "question": self.task["question"], "kind": "fact", "required": True,
                               "method_needs": [method]}], "data_requests": [], "calculations": [spec]}

    def source_spec(self):
        payload = "本地合成主体\n\n发布日期：2026年7月19日。\n\n交易日：2026年7月10日。\n\n人民币营业收入100百万元。".encode()
        snapshot = extract_material("statement.txt", payload)
        snapshot["original_base64"] = base64.b64encode(payload).decode()
        digest = fingerprint(snapshot)
        record = {"id": "original", "kind": "material", "snapshot": snapshot, "hash": digest, "snapshot_hash": digest}
        self.task["materials"] = [record]
        return {"id": "revenue", "artifact_id": "original", "object": "本地合成主体", "record_locator": {"part": "段落4"},
                "text": "人民币营业收入100百万元。", "published_at": "2026-07-19",
                "publication": {"basis_ref": {"artifact_id": "original", "snapshot_hash": digest, "part": "段落2", "quote": "发布日期：2026年7月19日。"}},
                "calculation_context": {"currency": "CNY"},
                "calculation_context_basis": {"currency": {"kind": "material", "artifact_id": "original", "snapshot_hash": digest,
                                                            "part": "段落4", "quote": "人民币营业收入100百万元。"}}}

    def test_three_methods_accept_registered_fixed_and_context_parameters(self):
        for method in self.dynamic:
            with self.subTest(method=method):
                plan = self.plan(method)
                before = copy.deepcopy(plan)
                self.assertEqual(validate_plan(self.task, plan)[0]["method_needs"], [method])
                self.assertEqual(plan, before, "Validation must not fill input values or approve evidence")

    def test_reject_missing_extra_and_changed_parameters(self):
        for method, dynamic in self.dynamic.items():
            for variant in ("missing", "extra", "fixed"):
                with self.subTest(method=method, variant=variant):
                    plan = self.plan(method)
                    parameters = plan["calculations"][0]["parameters"]
                    if variant == "missing":
                        parameters.pop(next(iter(dynamic)))
                    elif variant == "extra":
                        parameters["execute_code"] = "not permitted"
                    else:
                        parameters[next(iter(METHOD_REGISTRY[method]["parameters"]))] = "invented"
                    with self.assertRaises(ContractError):
                        validate_plan(self.task, plan)

    def test_reject_invalid_dynamic_contexts(self):
        cases = [("observed_price_return_v1", {"end_date": "2026-06-30"}),
                 ("observed_price_return_v1", {"start_date": "2026-02-30"}),
                 ("etf_nav_premium_v1", {"valuation_time": "2026-07-10"}),
                 ("gross_margin_v1", {"accounting_scope": "all_companies"}),
                 ("gross_margin_v1", {"accounting_standard": ""})]
        for method, changed in cases:
            with self.subTest(method=method, changed=changed):
                plan = self.plan(method)
                plan["calculations"][0]["parameters"].update(changed)
                with self.assertRaises(ContractError):
                    validate_plan(self.task, plan)

    def test_legacy_fee_and_residual_plans_remain_valid(self):
        validate_plan(self.task, self.plan("fee_difference_v1"))
        self.task["question"] = "按回报率10%和期限1年核算现值"
        self.task["questions"][0]["question"] = self.task["question"]
        plan = self.plan("residual_present_value_v1")
        plan["calculations"][0].update(assumptions={"rate": .1, "years": 1}, assumption_sources={
            "rate": {"source": "user_question", "quote": "回报率10%", "raw_token": "10", "raw_unit": "%", "factor": .01, "value": .1},
            "years": {"source": "user_question", "quote": "期限1年", "raw_token": "1", "raw_unit": "年", "factor": 1, "value": 1}})
        validate_plan(self.task, plan)

    def test_reject_numeric_input_or_self_signed_output(self):
        for extra in ("source_check", "output", "python_code"):
            plan = self.plan("gross_margin_v1")
            plan["calculations"][0][extra] = "通过"
            with self.assertRaises(ContractError):
                validate_plan(self.task, plan)
        plan = self.plan("gross_margin_v1")
        plan["calculations"][0]["inputs"]["revenue"] = 100
        with self.assertRaises(ContractError):
            validate_plan(self.task, plan)

    def test_context_specs_are_accepted_without_granting_verification(self):
        spec = self.source_spec()
        before = copy.deepcopy(spec)
        validate_model_specs(self.task, [spec])
        self.assertEqual(spec, before)
        self.assertNotIn("source_check", spec)
        self.assertNotIn("applicability_check", spec)
        spec.pop("calculation_context")
        spec.pop("calculation_context_basis")
        validate_model_specs(self.task, [spec])

    def test_invalid_context_schema_and_approval_fields_rejected(self):
        original = self.source_spec()
        variants = [{"calculation_context_basis": {}}, {"calculation_context": {"currency": True}},
                    {"calculation_context_basis": {"currency": "approved"}}, {"source_check": "通过"},
                    {"applicability_check": "通过"}, {"api_key": "fabricated"}]
        for update in variants:
            with self.subTest(update=update):
                spec = copy.deepcopy(original)
                spec.update(update)
                with self.assertRaises(ContractError):
                    validate_model_specs(self.task, [spec], schema_only=True)

    def test_context_does_not_bypass_original_publication_or_scope(self):
        original = self.source_spec()
        for variant in ("trade_date", "foreign_scope", "altered_original", "missing_basis"):
            with self.subTest(variant=variant):
                spec = copy.deepcopy(original)
                task = copy.deepcopy(self.task)
                if variant == "trade_date":
                    spec["published_at"] = "2026-07-10"
                    spec["publication"]["basis_ref"].update(part="段落3", quote="交易日：2026年7月10日。")
                elif variant == "foreign_scope":
                    spec["object"] = "另一个主体"
                elif variant == "altered_original":
                    task["materials"][0]["snapshot"]["name"] = "changed.txt"
                else:
                    spec["publication"] = {}
                with self.assertRaises(ContractError):
                    validate_model_specs(task, [spec])


if __name__ == "__main__":
    unittest.main()
