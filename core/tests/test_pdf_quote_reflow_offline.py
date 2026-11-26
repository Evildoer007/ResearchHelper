"""Renderer-format checks; real PDF parsing is reviewed separately.

Use the existing explicitly fabricated document case to test format metadata,
not a machine-specific acceptance artifact or a claim of PDF source approval.
"""

import copy
import html
from lxml import html as dom
from pathlib import Path
import unittest

from researchhelper.contracts import fingerprint
from researchhelper.reports import compose, render, READER_VERSION
from tests import test_document_content_offline as documents

ROOT = Path(__file__).resolve().parents[2]


class PDFQuoteReflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = documents.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        task = self.fixture.material_task()
        self.done = self.fixture.run_spec(task, self.fixture.spec(task))
        self.original_export = self.fixture.agent.export(task["id"])
        self.content = compose(self.done)
        # Presentation metadata only: this fabricated source was not parsed
        # as a PDF and is never used to establish real-source validity.
        self.content["evidence"][0]["artifact"]["name"] = "format-example.pdf"
        self.content["content_hash"] = fingerprint({key: value for key, value in self.content.items() if key != "content_hash"})

    def test_pdf_format_quotes_gain_reflow_hint_without_text_change(self):
        rendered = render(self.content, ROOT)
        tree = dom.document_fromstring(rendered)
        quotes = tree.xpath('//main//p[@class="document-content-quote"]')
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].get("data-source-format"), "pdf")
        self.assertEqual(quotes[0].text_content(), self.content["evidence"][0]["text"])

    def test_rendering_never_changes_frozen_content_or_export(self):
        before = copy.deepcopy(self.content)
        render(self.content, ROOT)
        self.assertEqual(before, self.content)
        self.assertEqual(self.fixture.agent.export(self.done["id"]), self.original_export)

    def test_non_pdf_and_unknown_origins_keep_literal_linebreak_presentation(self):
        for name in ("original.txt", "original.md", "", None, "not-a-pdf.pdf.txt"):
            with self.subTest(name=name):
                content = copy.deepcopy(self.content)
                for item in content["evidence"]:
                    item["artifact"]["name"] = name
                content["content_hash"] = fingerprint({key: value for key, value in content.items() if key != "content_hash"})
                rendered = render(content, ROOT)
                tree = dom.document_fromstring(rendered)
                self.assertFalse(tree.xpath('//*[@data-source-format="pdf"]'))
                quotes = tree.xpath('//main//p[@class="document-content-quote"]')
                self.assertEqual(len(quotes), 1)
                self.assertEqual(quotes[0].text_content(), content["evidence"][0]["text"])

    def test_pdf_format_hint_is_case_insensitive_and_not_a_filename_guess_in_text(self):
        content = copy.deepcopy(self.content)
        for item in content["evidence"]:
            item["artifact"]["name"] = "original.PDF"
        content["content_hash"] = fingerprint({key: value for key, value in content.items() if key != "content_hash"})
        tree = dom.document_fromstring(render(content, ROOT))
        self.assertEqual(len(tree.xpath('//main//p[@data-source-format="pdf"]')), 1)

    def test_reader_revision_changes_no_frozen_content_template(self):
        paper = render(self.content, ROOT)
        self.assertRegex(READER_VERSION, r"^rh-reader-\d+$")
        self.assertEqual(dom.document_fromstring(paper).get("data-reader-version"), READER_VERSION)
        self.assertIn("模板" + self.content["template_version"], paper)


if __name__ == "__main__":
    unittest.main()
