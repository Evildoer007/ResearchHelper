"""Independent scalar recomputation and evidence-context rejection tests."""

import copy
from decimal import Decimal
import math
import unittest

from researchhelper.contracts import (
    ContractError, METHOD_REGISTRY, calculate, validate_calculation_context,
    validate_calculation_parameters,
)


def method_case(method):
    parameters = dict(METHOD_REGISTRY[method]["parameters"])
    if method == "observed_price_return_v1":
        parameters.update(start_date="2026-09-01", end_date="2026-09-02")
        common = {"security_id": "600000.SH", "currency": "CNY", "price_basis": "unadjusted",
                  "price_kind": "close", "window_start": parameters["start_date"],
                  "window_end": parameters["end_date"], "corporate_action_status": "unknown"}
        records = {key: {"object": "600000.SH", "unit": "元", "period": day,
                         "calculation_context": dict(common, observation_date=day)}
                   for key, day in (("start_price", parameters["start_date"]), ("end_price", parameters["end_date"]))}
        return {"start_price": 8, "end_price": 9}, records, parameters
    if method == "etf_nav_premium_v1":
        parameters.update(valuation_time="2026-09-01T15:00:00+08:00")
        common = {"fund_id": "510000.SH", "share_class": "listed-CNY", "currency": "CNY",
                  "valuation_time": parameters["valuation_time"]}
        records = {key: {"object": "510000.SH", "unit": "元", "period": "2026-09-01",
                         "calculation_context": dict(common, **specific)}
                   for key, specific in (("price", {"price_kind": "close"}), ("nav", {"nav_kind": "official"}))}
        return {"price": 1.02, "nav": 1}, records, parameters
    parameters.update(period="2026H1", accounting_scope="consolidated", accounting_standard="CAS")
    common = {"entity_id": "company-a", "currency": "CNY", "period": "2026H1",
              "accounting_scope": "consolidated", "accounting_standard": "CAS"}
    records = {key: {"object": "company-a", "unit": "百万元", "period": "2026H1",
                     "calculation_context": dict(common, metric=metric)}
               for key, metric in (("revenue", "revenue"), ("cost", "operating_cost"))}
    return {"revenue": 125, "cost": 80}, records, parameters


class FinancialMethodTests(unittest.TestCase):
    methods = ("observed_price_return_v1", "etf_nav_premium_v1", "gross_margin_v1")

    def test_independent_decimal_recomputation(self):
        expected = {
            self.methods[0]: (Decimal("9") / Decimal("8") - 1) * 100,
            self.methods[1]: (Decimal("1.02") / Decimal("1") - 1) * 100,
            self.methods[2]: (Decimal("125") - Decimal("80")) / Decimal("125") * 100,
        }
        for method in self.methods:
            with self.subTest(method=method):
                values, records, parameters = method_case(method)
                validate_calculation_context(method, records, parameters)
                result = calculate(method, values, parameters)
                self.assertAlmostEqual(result["value"], float(expected[method]), delta=result["tolerance"])
                self.assertEqual(result["unit"], "%")
                self.assertEqual(set(result), {"value", "unit", "tolerance"})

    def test_negative_changes_and_margin_are_not_suppressed(self):
        cases = ((self.methods[0], {"start_price": 10, "end_price": 8}, -20),
                 (self.methods[1], {"price": 0.98, "nav": 1}, -2),
                 (self.methods[2], {"revenue": 100, "cost": 120}, -20))
        for method, values, expected in cases:
            with self.subTest(method=method):
                result = calculate(method, values, method_case(method)[2])
                self.assertAlmostEqual(result["value"], expected, delta=result["tolerance"])

    def test_numeric_boundaries_and_no_implicit_inputs(self):
        for method in self.methods:
            values, _, parameters = method_case(method)
            for key in values:
                for invalid in (True, "1", None, math.nan, math.inf, -math.inf):
                    with self.subTest(method=method, key=key, invalid=invalid), self.assertRaises(ContractError):
                        calculate(method, {**values, key: invalid}, parameters)
                missing = dict(values)
                missing.pop(key)
                with self.assertRaises(ContractError):
                    calculate(method, missing, parameters)
            with self.assertRaises(ContractError):
                calculate(method, {**values, "probability": 0.5}, parameters)
            with self.assertRaises(ContractError):
                calculate(method, values)
        for method, values in ((self.methods[0], {"start_price": 0, "end_price": 1}),
                               (self.methods[0], {"start_price": 1, "end_price": 0}),
                               (self.methods[1], {"price": 1, "nav": 0}),
                               (self.methods[1], {"price": -1, "nav": 1}),
                               (self.methods[2], {"revenue": 0, "cost": 1}),
                               (self.methods[2], {"revenue": 1, "cost": -1})):
            with self.subTest(method=method, values=values), self.assertRaises(ContractError):
                calculate(method, values, method_case(method)[2])
        with self.assertRaises(ContractError):
            calculate(self.methods[0], {"start_price": 1e-308, "end_price": 1e308}, method_case(self.methods[0])[2])

    def test_parameters_require_explicit_registered_values(self):
        for method in self.methods:
            _, _, parameters = method_case(method)
            for key in parameters:
                changed = dict(parameters)
                changed.pop(key)
                with self.subTest(method=method, missing=key), self.assertRaises(ContractError):
                    validate_calculation_parameters(method, changed)
            for key, value in (("currency", "HKD"), ("causal_effect", True), ("probability", 0.8)):
                with self.subTest(method=method, key=key), self.assertRaises(ContractError):
                    validate_calculation_parameters(method, {**parameters, key: value})

    def test_object_unit_currency_and_missing_context_are_rejected(self):
        for method in self.methods:
            _, records, parameters = method_case(method)
            first = next(iter(records))
            for field, value in (("object", "other-object"), ("unit", "港元/股"),
                                 ("period", ""), ("calculation_context", None)):
                changed = copy.deepcopy(records)
                changed[first][field] = value
                with self.subTest(method=method, field=field), self.assertRaises(ContractError):
                    validate_calculation_context(method, changed, parameters)
            changed = copy.deepcopy(records)
            changed[first]["calculation_context"]["currency"] = "HKD"
            with self.assertRaises(ContractError):
                validate_calculation_context(method, changed, parameters)
            changed = copy.deepcopy(records)
            changed[first]["calculation_context"] = {"reason": "模型认为两个输入可比"}
            with self.assertRaises(ContractError):
                validate_calculation_context(method, changed, parameters)

    def test_price_window_and_corporate_actions_are_explicit(self):
        method = self.methods[0]
        _, records, parameters = method_case(method)
        for field, value in (("security_id", "another-security"), ("price_basis", "adjusted"),
                             ("price_kind", "intraday"), ("observation_date", "2026-09-03"),
                             ("window_start", "2026-08-31"), ("window_end", "2026-09-03"),
                             ("corporate_action_status", "absent"), ("corporate_action_status", True)):
            changed = copy.deepcopy(records)
            changed["end_price"]["calculation_context"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ContractError):
                validate_calculation_context(method, changed, parameters)
        for end in ("2026-09-01", "2026-08-31", "2026-02-30", "20260902"):
            with self.subTest(end=end), self.assertRaises(ContractError):
                validate_calculation_parameters(method, {**parameters, "end_date": end})
        with self.assertRaises(ContractError):
            validate_calculation_parameters(method, {**parameters, "return_kind": "total_return"})
        for state in ("unknown", "present", "verified_none"):
            changed = copy.deepcopy(records)
            for record in changed.values():
                record["calculation_context"]["corporate_action_status"] = state
            validate_calculation_context(method, changed, parameters)
            result = calculate(method, {"start_price": 8, "end_price": 9}, parameters)
            self.assertEqual(result["value"], 12.5)

    def test_etf_requires_official_nav_and_synchronous_share_class(self):
        method = self.methods[1]
        _, records, parameters = method_case(method)
        for field, value in (("fund_id", "another-fund"), ("share_class", "another-class"),
                             ("valuation_time", "2026-09-01T14:59:00+08:00"),
                             ("nav_kind", "IOPV"), ("nav_kind", "estimated")):
            changed = copy.deepcopy(records)
            changed["nav"]["calculation_context"][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_calculation_context(method, changed, parameters)
        for value in ("2026-09-01", "2026-09-01T15:00:00", "2026-09-01T15:00:00+00:00"):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_calculation_parameters(method, {**parameters, "valuation_time": value})

    def test_margin_requires_same_statement_scope_and_cost_definition(self):
        method = self.methods[2]
        _, records, parameters = method_case(method)
        for field, value in (("entity_id", "other-company"), ("period", "2025H1"),
                             ("accounting_scope", "parent"), ("accounting_standard", "IFRS"),
                             ("metric", "total_operating_expenses")):
            changed = copy.deepcopy(records)
            changed["cost"]["calculation_context"][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_calculation_context(method, changed, parameters)
        for field, value in (("period", ""), ("accounting_scope", "unknown"), ("accounting_standard", "unknown")):
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_calculation_parameters(method, {**parameters, field: value})

    def test_existing_methods_keep_outputs_and_fee_period_mismatch_is_rejected(self):
        result = calculate("fee_difference_v1", {"fee_a": .15, "fee_b": .25}, {"unit": "%/年"})
        self.assertEqual(result, {"value": -.1, "unit": "百分点/年", "tolerance": 1e-10})
        records = {"fee_a": {"period": "2025年度", "unit": "%/年"},
                   "fee_b": {"period": "2026年度", "unit": "%/年"}}
        with self.assertRaises(ContractError):
            validate_calculation_context("fee_difference_v1", records, {"unit": "%/年"})
        records["fee_a"]["period"] = "2026年度"
        validate_calculation_context("fee_difference_v1", records, {"unit": "%/年"})
        values = {"receipt": 120, "debt": 20, "cost": 5, "shares": 10, "price": 7.5, "rate": .1, "years": 1}
        result = calculate("residual_present_value_v1", values, {"amount_unit": "百万元", "shares_unit": "百万股"})
        self.assertAlmostEqual(result["value"], float(Decimal("95") / Decimal("10") / Decimal("1.1")), delta=1e-8)
        self.assertEqual(set(result), {"value", "unit", "premium", "undiscounted", "tolerance"})

    def test_registered_knowledge_identity_is_explicit(self):
        expected = {"fee_difference_v1": "rh_k_fee_difference", "residual_present_value_v1": "rh_k_residual",
                    "observed_price_return_v1": "rh_k_price_change", "etf_nav_premium_v1": "rh_k_nav_premium",
                    "gross_margin_v1": "rh_k_gross_margin"}
        for method, ident in expected.items():
            self.assertEqual(METHOD_REGISTRY[method]["knowledge_id"], ident)
            self.assertEqual(METHOD_REGISTRY[method]["implementation_version"], 1)


if __name__ == "__main__":
    unittest.main()
