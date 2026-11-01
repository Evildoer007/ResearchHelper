"""Content-driven report structure; previous presentation tests stay intact."""
import copy
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest

from researchhelper.contracts import ContractError
from researchhelper.reports import render, compose
from tests import test_document_content_offline as originals

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "result/acceptance/live-current-research/research-n4s6r1c5"


class Chapters(HTMLParser):
    def __init__(self, paper):
        super().__init__()
        self.headings, self.targets = [], []
        self.heading = None
        self.feed(paper)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h2":
            self.heading = ""
        if tag == "section":
            self.targets.append(attrs["id"])

    def handle_data(self, data):
        if self.heading is not None:
            self.heading += data

    def handle_endtag(self, tag):
        if tag == "h2":
            self.headings.append(self.heading)
            self.heading = None


class ReportStructureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = originals.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def live_content(self):
        report = Path(json.loads((LIVE / "receipt.json").read_text())["report"])
        return json.loads(report.with_suffix(".json").read_text())

    def test_current_fact_answers_are_not_repeated_in_summary(self):
        content = self.live_content()
        paper = render(content, ROOT)
        for question in content["questions"]:
            self.assertEqual(paper.count(html.escape(question["answer"], quote=True)), 1)

    def test_facts_without_calculation_do_not_get_empty_methods_or_summary(self):
        chapters = Chapters(render(self.live_content(), ROOT))
        self.assertEqual(chapters.targets, ["theses", "facts", "risk", "scope"])
        self.assertEqual(chapters.headings, ["1.问题回答", "2.事实与证据", "3.限制与验证条件", "4.研究范围与知识依据"])

    def test_all_rendered_chapters_are_continuously_numbered(self):
        for case in ("mechanism", "etf", "value"):
            with self.subTest(case=case):
                task = self.fixture.agent.create_case(case)
                content = compose(self.fixture.agent.start(task["id"]))
                chapters = Chapters(render(content, ROOT))
                self.assertEqual([int(title.split(".", 1)[0]) for title in chapters.headings],
                                 list(range(1, len(chapters.headings) + 1)))
                for target in chapters.targets:
                    self.assertIn(f"href='#{target}'", render(content, ROOT))

    def test_mechanism_judgment_counter_and_tracking_survive_without_empty_methods(self):
        task = self.fixture.agent.create_case("mechanism")
        done = self.fixture.agent.start(task["id"])
        content = compose(done)
        paper = render(content, ROOT)
        self.assertNotIn("id='methods'", paper)
        for thesis in content["theses"]:
            for key in ("judgment", "investment_meaning", "limitation"):
                self.assertIn(html.escape(thesis[key], quote=True), paper)
            for ref in thesis.get("counter", []):
                self.assertIn(f"href='#ev-{ref}'", paper)
        for track in content["tracking"]:
            self.assertIn(html.escape(track["condition"], quote=True), paper)

    def test_real_calculations_keep_math_inputs_and_chart(self):
        task = self.fixture.agent.create_case("value")
        content = compose(self.fixture.agent.start(task["id"]))
        paper = render(content, ROOT)
        self.assertIn("id='methods'", paper)
        self.assertIn("<math ", paper)
        self.assertIn("echarts.init", paper)
        for calculation in content["calculations"]:
            self.assertIn(f"{calculation['output']['value']:.4f}", paper)

    def test_current_only_scope_publication_and_conditions_are_preserved(self):
        content = self.live_content()
        paper = render(content, ROOT)
        self.assertIn(html.escape(content["scope"], quote=True), paper)
        for evidence in content["evidence"]:
            self.assertIn("未核验", paper)
            self.assertIn(evidence["availability"]["obtained_at"], paper)
            for condition in evidence["applicability_review"]["conditions"]:
                self.assertIn(html.escape(condition, quote=True), paper)

    def test_frozen_content_and_original_export_never_change(self):
        content = self.live_content()
        before = copy.deepcopy(content)
        report = Path(json.loads((LIVE / "receipt.json").read_text())["report"])
        original = hashlib.sha256(report.read_bytes()).hexdigest()
        render(content, ROOT)
        self.assertEqual(content, before)
        self.assertEqual(hashlib.sha256(report.read_bytes()).hexdigest(), original)

    def test_tampered_content_is_still_rejected(self):
        content = self.live_content()
        content["questions"][0]["answer"] += "未核验新结论"
        with self.assertRaises(ContractError):
            render(content, ROOT)


if __name__ == "__main__":
    unittest.main()
