"""Reader12 keeps prior financial precision assertions; legacy pins remain.

Only the version assertion is renewed in this independent test module. The
original reader11 test still fails its old literal pin and is not modified.
"""
from tests import test_report_reading_precision as legacy
from researchhelper.reports import READER_VERSION


class CurrentReportReaderTests(legacy.ReportReadingPrecisionTests):
    def test_new_reader_identity_and_offline_chart_data_retained(self):
        page = self.render()
        self.assertEqual(READER_VERSION, 'rh-reader-12')
        self.assertIn("data-reader-version='rh-reader-12'", page)
        for value in ('90703260964.48', '46033330566.78', '70690750119.06'):
            self.assertIn(value, page)
        self.assertEqual(self.content['content_hash'], self.frozen['content_hash'])
