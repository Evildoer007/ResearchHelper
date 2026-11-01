"""Document report presentation without changing evidence or frozen exports."""
import copy
import html
import unittest

from researchhelper.contracts import fingerprint
from researchhelper.reports import compose, render, READER_VERSION, TEMPLATE_VERSION
from tests import test_document_content_offline as documents


class DocumentReportIndexTests(unittest.TestCase):
    def setUp(self):
        self.fixture = documents.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        task = self.fixture.material_task()
        done = self.fixture.run_spec(task, self.fixture.spec(task))
        self.content = compose(done)
        self.frozen = self.fixture.agent.export(done["id"])
        self.task_id = done["id"]

    def paper(self, content=None):
        content = copy.deepcopy(self.content if content is None else content)
        content["content_hash"] = fingerprint({key: value for key, value in content.items() if key != "content_hash"})
        return render(content, documents.ROOT)

    def test_exact_generated_answer_is_a_compact_index_not_duplicate_text(self):
        paper = self.paper()
        self.assertEqual(paper.count("class='document-answer-index'"), 1)
        self.assertIn("<h3>2.1 文件写了什么？</h3>", paper)
        self.assertEqual(paper.count(html.escape(self.content["evidence"][0]["text"])), 1)
        self.assertIn("href='#ev-clause'", paper)

    def test_excerpt_heading_comes_from_its_explicit_question_reference(self):
        paper = self.paper()
        self.assertIn("<h3>3.1 文件写了什么？</h3>", paper)
        self.assertNotIn("<h3>3.1 材料示例政策</h3>", paper)

    def test_shared_excerpt_retains_every_distinct_linked_question(self):
        content = copy.deepcopy(self.content)
        second = copy.deepcopy(content["questions"][0])
        second.update(id="q2", question="原件载明什么生效时间？")
        content["questions"].append(second)
        paper = self.paper(content)
        self.assertEqual(paper.count("class='document-answer-index'"), 2)
        self.assertIn("<h3>3.1 文件写了什么？；原件载明什么生效时间？</h3>", paper)
        self.assertEqual(paper.count("class='document-content-quote'"), 1)

    def test_unreferenced_excerpt_retains_original_object_heading(self):
        content = copy.deepcopy(self.content)
        content["questions"] = []
        self.assertIn("<h3>3.1 材料示例政策</h3>", self.paper(content))

    def test_distinct_legacy_answer_is_never_elided_or_compacted(self):
        content = copy.deepcopy(self.content)
        content["questions"][0]["answer"] = "历史交付另有解释，不能因为摘录相近而删除。"
        paper = self.paper(content)
        self.assertNotIn("class='document-answer-index'", paper)
        self.assertIn(html.escape(content["questions"][0]["answer"]), paper)
        self.assertIn(html.escape(content["evidence"][0]["text"]), paper)

    def test_missing_reference_does_not_hide_the_saved_answer(self):
        content = copy.deepcopy(self.content)
        content["questions"][0]["refs"].append("missing-original")
        paper = self.paper(content)
        self.assertNotIn("class='document-answer-index'", paper)
        self.assertIn(html.escape(content["questions"][0]["answer"]), paper)

    def test_question_and_identity_are_escaped_in_both_locations(self):
        content = copy.deepcopy(self.content)
        content["questions"][0]["question"] = "<script>原件&核对</script>"
        content["evidence"][0]["id"] = "quote'unsafe"
        content["questions"][0]["refs"] = ["quote'unsafe"]
        paper = self.paper(content)
        self.assertIn("<h3>3.1 &lt;script&gt;原件&amp;核对&lt;/script&gt;</h3>", paper)
        self.assertIn("id='ev-quote&#x27;unsafe'", paper)
        self.assertNotIn("<script>原件&核对</script>", paper)

    def test_normal_research_does_not_adopt_document_index_or_question_titles(self):
        done = self.fixture.agent.start(self.fixture.agent.create_case("mechanism")["id"])
        content = compose(done)
        paper = self.paper(content)
        self.assertNotIn("class='document-answer-index'", paper)
        self.assertNotIn("class='document-content-quote'", paper)
        for index, item in enumerate(content["evidence"], start=1):
            self.assertIn(f"<h3>3.{index} {html.escape(item['object'])}</h3>", paper)

    def test_new_presentation_keeps_content_template_hash_and_saved_export(self):
        before = copy.deepcopy(self.content)
        paper = render(self.content, documents.ROOT)
        self.assertEqual(READER_VERSION, "rh-reader-4")
        self.assertEqual(TEMPLATE_VERSION, "rh-paper-3")
        self.assertIn("data-reader-version='rh-reader-4'", paper)
        self.assertEqual(self.content, before)
        self.assertEqual(self.fixture.agent.export(self.task_id), self.frozen)


if __name__ == "__main__":
    unittest.main()
