"""Keep the historical package selector while checking both current profiles.

Content and saved exports stay frozen independently of the reader revision.
"""
import copy
from lxml import html
from researchhelper.reports import render, READER_VERSION
from tests import test_pdf_quote_reflow_offline as previous


class PDFReaderRevision4Tests(previous.PDFQuoteReflowTests):
    def test_reader_revision_changes_no_frozen_content_template(self):
        original = copy.deepcopy(self.content)
        for profile in ("standard", "detailed"):
            tree = html.document_fromstring(render(self.content, previous.ROOT, report_profile=profile))
            self.assertEqual(tree.get("data-reader-version"), READER_VERSION)
            self.assertIn("模板" + self.content["template_version"], tree.text_content())
            self.assertEqual(self.content, original)
        self.assertEqual(self.fixture.agent.export(self.done["id"]), self.original_export)
