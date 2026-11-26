"""The current fixed-case verifier checks reader identity without quote assumptions."""
from copy import deepcopy
import unittest
from researchhelper.reports import READER_VERSION
from tests.research_matrix_skill_fixture import verify_case


def fixture():
    task = dict(status='已完成',synthetic=True,ledger=dict(used=dict(model_calls=0,data_requests=0)))
    content = dict(entry='指数',intent='综合研究',synthetic=True,questions=[],
        theses=[dict(judgment='支持')],evidence=[dict(id='synthetic-a'),dict(id='synthetic-b')],
        calculations=[],tracking=[],limitations=[])
    return task,content


class MatrixReaderTests(unittest.TestCase):
    def test_current_reader_identity_accepts_either_html_quote_style(self):
        task,content=fixture();before=deepcopy((task,content))
        for quote in ('"',"'"):
            with self.subTest(quote=quote):
                paper='<html data-reader-version='+quote+READER_VERSION+quote+'><body>合成验收</body></html>'
                self.assertTrue(verify_case('index',task,content,paper))
        self.assertEqual((task,content),before)

    def test_missing_old_and_duplicate_reader_markers_do_not_pass(self):
        task,content=fixture()
        for attributes in ('', 'data-reader-version="rh-reader-7"',
                           'data-reader-version="'+READER_VERSION+'" data-reader-version="'+READER_VERSION+'"'):
            with self.subTest(attributes=attributes),self.assertRaises(AssertionError):
                verify_case('index',task,content,'<html '+attributes+'><body>合成验收</body></html>')

    def test_a_reader_marker_outside_the_document_root_does_not_pass(self):
        task,content=fixture()
        with self.assertRaises(AssertionError):
            verify_case('index',task,content,'<html><body><div data-reader-version="'+READER_VERSION+'">合成验收</div></body></html>')
