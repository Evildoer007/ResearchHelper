"""Reader-4 counterpart for the retained reader-3 version-only assertion.

The original PDF-format fixture and its other four checks are inherited
unchanged. Package routing selects only the one revised assertion by full ID.
"""
from researchhelper.reports import render, READER_VERSION
from tests import test_pdf_quote_reflow_offline as previous


class PDFReaderRevision4Tests(previous.PDFQuoteReflowTests):
    def test_reader_revision_changes_no_frozen_content_template(self):
        paper = render(self.content, previous.ROOT)
        self.assertEqual(READER_VERSION, "rh-reader-4")
        self.assertIn("data-reader-version='rh-reader-4'", paper)
        self.assertIn("模板" + self.content["template_version"], paper)
