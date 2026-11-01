"""Unknown catalogue times require a publication anchor in actual PDF bytes."""
import copy
import io
import unittest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import candidate_evidence
from tests import test_filing_acquisition_offline as fixtures

TEXT = 'Synthetic filing only. Published on 2026-09-29.'


def publication_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=180)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(('BT /F1 12 Tf 20 120 Td ('+TEXT+') Tj ET').encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class FilingOriginalPublicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FilingAcquisitionTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def original(self, ctime):
        fixture = self.fixture
        catalog = fixture.catalog({'errorcode': 0, 'tables': [dict(fixture.row, ctime=ctime)]})
        self.assertIsNone(catalog['catalog_entries'][0]['published_at'])
        self.assertTrue(catalog['catalog_entries'][0]['downloadable'])
        _, connection = fixture.transport(payload=publication_pdf())
        filing = fixture.collector.external(fixture.task, 'ifind', 'filing_original', fixture.selection(catalog), {})
        fixture.task['source_records'].append(filing)
        connection.close.assert_called_once()
        self.assertIn(TEXT, filing['snapshot']['material']['parts'][0]['text'])
        specification = {'id': 'e1', 'object': '600000.SH', 'period': fixture.row['reportDate'],
            'nature': '已实现事实', 'unit': '不适用', 'record_locator': {'part': '第1页'},
            'text': TEXT, 'published_at': '2026-09-29', 'publication': {
                'at': '2026-09-29', 'precision': '日', 'timezone': 'Asia/Shanghai',
                'certainty': '已确认', 'basis': '实际PDF正文明确披露日期',
                'basis_ref': {'kind': 'material', 'artifact_id': filing['id'],
                    'snapshot_hash': filing['hash'], 'part': '第1页', 'quote': TEXT}}}
        return filing, specification

    def test_unknown_directory_time_can_use_exact_retained_pdf_anchor(self):
        for ctime in (None, '', '2026-09-29 17:00:00'):
            with self.subTest(ctime=ctime):
                filing, spec = self.original(ctime)
                original = copy.deepcopy(filing)
                candidate = candidate_evidence(self.fixture.task, filing, spec)
                self.assertEqual(candidate['source_check'], '通过')
                self.assertEqual(candidate['applicability_check'], '待核验')
                self.assertEqual(candidate['published_at'], '2026-09-29')
                self.assertEqual(filing, original)
                self.assertNotIn('applicability_receipt', candidate)

    def test_directory_without_time_and_wrong_pdf_quote_are_not_approval(self):
        filing, spec = self.original(None)
        original = copy.deepcopy(filing)
        for change in ('missing', 'directory', 'quote'):
            bad = copy.deepcopy(spec)
            if change == 'missing':
                bad.pop('publication')
            elif change == 'directory':
                bad['publication']['basis_ref'] = {'kind': 'directory', 'artifact_id': filing['id'],
                    'snapshot_hash': filing['hash'], 'field': 'published_at'}
            else:
                bad['publication']['basis_ref']['quote'] = 'Published on 2026-09-28.'
            with self.subTest(change=change), self.assertRaises(ContractError):
                candidate_evidence(self.fixture.task, filing, bad)
        self.assertEqual(filing, original)

    def test_rewriting_parsed_parts_and_rehashing_does_not_replace_original_pdf(self):
        filing, spec = self.original(None)
        changed = copy.deepcopy(filing)
        text = TEXT.replace('2026-09-29', '2026-09-28')
        changed['snapshot']['material']['parts'][0]['text'] = text
        changed['hash'] = fingerprint(changed['snapshot'])
        bad = copy.deepcopy(spec)
        bad.update(text=text, published_at='2026-09-28')
        bad['publication'].update(at='2026-09-28')
        bad['publication']['basis_ref'].update(snapshot_hash=changed['hash'], quote=text)
        with self.assertRaises(ContractError):
            candidate_evidence(self.fixture.task, changed, bad)
