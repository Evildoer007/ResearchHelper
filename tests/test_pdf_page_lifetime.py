"""Release completed-page caches without changing text or geometry outputs."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.pdf_material import extract_parts


class PDFPageLifetimeTests(unittest.TestCase):
    def test_completed_page_is_closed_before_the_next_page_is_processed(self):
        pages = []
        class Page:
            def __init__(self, index):
                self.index, self.closed = index, False
            def find_tables(self, settings):
                if self.index:
                    self_test.assertTrue(pages[self.index - 1].closed)
                return []
            def close(self):
                self.closed = True
        class Document:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        self_test = self
        pages.extend(Page(index) for index in range(2))
        document = Document()
        document.pages = pages
        plain = [SimpleNamespace(extract_text=lambda: '首个合成页面'),
                 SimpleNamespace(extract_text=lambda: '第二个合成页面')]
        with patch('pypdf.PdfReader', return_value=SimpleNamespace(pages=plain)), \
                patch('pdfplumber.open', return_value=document):
            parts = extract_parts(b'private fake input, not a financial document')
        self.assertEqual(parts, [dict(locator='第1页', text='首个合成页面'), dict(locator='第2页', text='第二个合成页面')])
        self.assertTrue(all(page.closed for page in pages))
