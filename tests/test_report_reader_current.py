"""Check the active reader against retained precision and frozen content."""
from tests import test_report_reading_precision as legacy
from researchhelper.reports import READER_VERSION


class CurrentReportReaderTests(legacy.ReportReadingPrecisionTests):
    def test_new_reader_identity_and_offline_chart_data_retained(self):
        page = self.render()
        from lxml import html as dom
        self.assertRegex(READER_VERSION, r'^rh-reader-\d+$')
        self.assertEqual(dom.document_fromstring(page).get('data-reader-version'), READER_VERSION)
        from tests.argument_review_fixture import AMOUNTS
        for _label, original_token in AMOUNTS.values():
            self.assertIn(original_token, page)
        self.assertEqual(self.content['content_hash'], self.frozen['content_hash'])
