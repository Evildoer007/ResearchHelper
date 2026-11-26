"""Formal fact and comparison workpapers keep actual formulas with each answer.

The existing synthetic-original fixture traverses extraction, source checks,
calculation and saved delivery. No provider, model or production store is used.
"""
import copy
import unittest
from unittest.mock import patch
from lxml import html

from tests import ROOT
from tests import test_method_dependency_workflow_offline as workflow
from researchhelper.contracts import fingerprint
from researchhelper.reports import compose
from researchhelper.report_designer import design

METHODS = ("observed_price_return_v1", "etf_nav_premium_v1", "gross_margin_v1")


class FormalMethodWorkpaperTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workflow.MethodDependencyWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.agent.close_runtime)

    def complete(self, method):
        task, host, _ = self.fixture.prepare(method)
        task = self.fixture.agent.start(task["id"], host)
        self.assertEqual(task["status"], "已完成", task.get("error"))
        self.assertFalse(task["theses"])
        return task, compose(task)

    def test_each_factual_answer_expands_its_own_math_inputs_and_conditions(self):
        for method in METHODS:
            with self.subTest(method=method):
                task, content = self.complete(method)
                result = design(content, ROOT, profile="detailed")
                tree = html.document_fromstring(result["html"])
                question = content["questions"][0]
                key = "answer-" + question["id"]
                details = tree.xpath('//main//*[@data-logic-detail="' + key + '"]')
                self.assertEqual(len(details), 1)
                calculations = details[0].xpath('.//*[@data-calculation-ref]')
                self.assertEqual([node.get("data-calculation-ref") for node in calculations], question["calculation_refs"])
                for node in calculations:
                    self.assertEqual(len(node.xpath('.//math')), 1)
                    self.assertIn(task["calculations"][0]["limitations"], node.text_content())
                refs = set(details[0].xpath('.//tr/@data-observation-id'))
                self.assertEqual(refs, set(task["calculations"][0]["input_refs"]))
                self.assertFalse(tree.xpath('//main//details'))

    def test_client_paper_keeps_clickable_calculations_without_internal_blocks(self):
        task, content = self.complete("gross_margin_v1")
        result = design(content, ROOT)
        tree = html.document_fromstring(result["html"])
        self.assertFalse(tree.xpath('//main//*[@data-logic-detail]|//main//math|//main//details'))
        for calculation in task["calculations"]:
            ident = calculation["id"]
            self.assertTrue(tree.xpath('//main//a[@href="#' + ident + '"]'))
            record = tree.get_element_by_id(ident)
            self.assertTrue(record.xpath('.//math'))

    def test_reading_profiles_preserve_saved_content_dataset_and_usage(self):
        task, content = self.complete("gross_margin_v1")
        saved = copy.deepcopy(self.fixture.agent.export(task["id"]))
        before = fingerprint(content)
        ledger = copy.deepcopy(task["ledger"])
        with patch("researchhelper.reports.ResearchKnowledge.method_binding", side_effect=AssertionError("No method or knowledge upgrade")):
            client = design(content, ROOT)
            internal = design(content, ROOT, profile="detailed")
        self.assertEqual(before, fingerprint(content))
        self.assertEqual(client["document"]["dataset"], internal["document"]["dataset"])
        self.assertEqual(client["document"]["content_hash"], internal["document"]["content_hash"])
        self.assertEqual(self.fixture.agent.export(task["id"]), saved)
        self.assertEqual(self.fixture.agent.archive.get(task["id"])["ledger"], ledger)
        self.assertEqual(ledger["used"]["model_calls"], 0)
        self.assertEqual(ledger["used"]["data_requests"], 0)

    def test_question_workpapers_do_not_borrow_another_answers_calculation(self):
        _, content = self.complete("gross_margin_v1")
        # A sealed presentation-only variant, not another adopted research.
        question = copy.deepcopy(content["questions"][0])
        question.update(id="literal-revenue", question="原件列示的营业收入是多少？",
            answer="合成原件列示营业收入100百万元。", refs=["e_revenue"], calculation_refs=[], method_needs=[])
        content["questions"].append(question)
        content["content_hash"] = fingerprint({key: value for key, value in content.items() if key != "content_hash"})
        tree = html.document_fromstring(design(content, ROOT, profile="detailed")["html"])
        first = tree.xpath('//main//*[@data-logic-detail="answer-q1"]')[0]
        second = tree.xpath('//main//*[@data-logic-detail="answer-literal-revenue"]')[0]
        self.assertEqual(len(first.xpath('.//math')), 1)
        self.assertFalse(second.xpath('.//math|.//*[@data-calculation-ref]'))
        self.assertEqual(second.xpath('.//tr/@data-observation-id'), ["e_revenue"])

    def test_quoted_question_anchor_remains_a_single_literal_target(self):
        _, content = self.complete("gross_margin_v1")
        # Historical HTML must treat an anchor as data, never as XPath syntax.
        literal = "question'\"[literal]"
        content["questions"][0]["id"] = literal
        content["content_hash"] = fingerprint({key: value for key, value in content.items() if key != "content_hash"})
        tree = html.document_fromstring(design(content, ROOT, profile="detailed")["html"])
        answer = tree.get_element_by_id("answer-" + literal)
        details = answer.xpath('./*[@data-logic-detail=$target]', target="answer-" + literal)
        self.assertEqual(len(details), 1)
        self.assertEqual(len(details[0].xpath('.//math')), 1)
