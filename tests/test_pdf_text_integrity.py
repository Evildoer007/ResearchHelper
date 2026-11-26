"""Exercise the actual font decoder and the original reader on mixed PDFs."""
import copy
import io
import tempfile
import unittest
from unittest.mock import patch

from pdfminer.cmapdb import CMapDB
from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject,
                           NumberObject, TextStringObject)

from tests import ROOT
from core.session import Session
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.draft_sources import source_context, writing_context
from researchhelper.draft_workflow import _submission_binding, retained_submission
from researchhelper.evidence import extract_material, material_record
from researchhelper.pdf_material import EXTRACT_VERSION, legacy_pdf_text_damaged, pdf_reparse_replacements
from researchhelper.reasoning import extractable_source_views
from researchhelper.source_reader import validate_reading


def pdf_fixture(*pages):
    writer = PdfWriter()
    latin = writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'), NameObject('/Encoding'): NameObject('/WinAnsiEncoding'),
    }))
    mapped = CMapDB.get_unicode_map('Adobe-CNS1').cid2unichr
    cids = ''.join(f'{next(cid for cid,value in mapped.items() if value==char):04x}' for char in '優先票據')
    for style in pages:
        page = writer.add_blank_page(width=400,height=220)
        fonts = DictionaryObject({NameObject('/F1'):latin})
        if style == 'clean':
            body = 'BT /F1 12 Tf 30 180 Td (Local filing contents) Tj ET'
        else:
            collection = 'CNS1' if style == 'mapped' else 'Unavailable'
            descriptor = DictionaryObject({
                NameObject('/Type'):NameObject('/FontDescriptor'),NameObject('/FontName'):NameObject('/FixtureCJK'),
                NameObject('/FontBBox'):ArrayObject([NumberObject(n) for n in (0,-200,1000,900)]),
                NameObject('/Flags'):NumberObject(4),NameObject('/Ascent'):NumberObject(900),
                NameObject('/Descent'):NumberObject(-200),NameObject('/CapHeight'):NumberObject(700),
                NameObject('/ItalicAngle'):NumberObject(0),NameObject('/StemV'):NumberObject(80),
            })
            descendant = DictionaryObject({
                NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/CIDFontType0'),
                NameObject('/BaseFont'): NameObject('/FixtureCJK'), NameObject('/DW'): NumberObject(1000),
                NameObject('/FontDescriptor'):writer._add_object(descriptor),
                NameObject('/CIDSystemInfo'): DictionaryObject({
                    NameObject('/Registry'):TextStringObject('Adobe'),
                    NameObject('/Ordering'):TextStringObject(collection),
                    NameObject('/Supplement'):NumberObject(0),
                }),
            })
            composite = DictionaryObject({
                NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type0'),
                NameObject('/BaseFont'):NameObject('/FixtureCJK'),NameObject('/Encoding'):NameObject('/Identity-H'),
                NameObject('/DescendantFonts'):ArrayObject([writer._add_object(descendant)]),
            })
            fonts[NameObject('/F2')] = writer._add_object(composite)
            glyphs = cids if style == 'mapped' else '009800990100'
            body = 'BT /F2 12 Tf 30 180 Td <'+glyphs+'> Tj /F1 12 Tf ( 2.40%) Tj ET'
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):fonts})
        stream = DecodedStreamObject()
        stream.set_data(body.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class PDFTextIntegrityTests(unittest.TestCase):
    def test_bundled_cid_map_recovers_glyphs_and_preserves_existing_extractors(self):
        raw = pdf_fixture('mapped','clean')
        original = extract_material('fixture.pdf',raw,extract_version='pdf-tables-6')
        current = extract_material('fixture.pdf',raw)
        self.assertEqual(current['hash'],original['hash'])
        self.assertEqual(current['extract_version'],EXTRACT_VERSION)
        self.assertEqual(current['status'],'已解析')
        self.assertNotIn('優先票據',original['parts'][0]['text'])
        self.assertIn('優先票據',current['parts'][0]['text'])
        self.assertIn('2.40%',current['parts'][0]['text'])
        self.assertTrue(current['parts'][0]['pdf_text_recovered'])
        self.assertEqual(current['parts'][1],original['parts'][1])
        self.assertEqual(extract_material('fixture.pdf',raw,extract_version='pdf-tables-6')['parts'],original['parts'])

    def test_unreadable_page_cannot_supply_an_orphan_number(self):
        raw = pdf_fixture('clean','unmapped','mapped')
        old = extract_material('fixture.pdf',raw,extract_version='pdf-tables-6')
        self.assertTrue(legacy_pdf_text_damaged(old))
        self.assertIn('2.40%',old['parts'][1]['text'])
        result = extract_material('fixture.pdf',raw)
        self.assertEqual(result['status'],'已解析')
        self.assertIn('1页文字编码无法可靠读取',result['limitation'])
        self.assertEqual(result['parts'][1],dict(locator='第2页',text='',pdf_text_limited=True))
        self.assertIn('Local filing contents',result['parts'][0]['text'])
        self.assertIn('優先票據',result['parts'][2]['text'])

    def test_all_unreadable_original_stays_in_the_catalogue_without_a_quote(self):
        with tempfile.TemporaryDirectory() as work:
            session = Session(work)
            try:
                task = session.create('核对原件写了什么',intent='原件内容核对')
                record = session.material(task['id'],'fixture.pdf',pdf_fixture('unmapped'))
                self.assertEqual(record['status'],'解析受限')
                catalog = session.sources(task['id'])
                original, = catalog['items']
                self.assertEqual(original['headings'][0]['part'],'第1页')
                self.assertEqual(original['reading_spans'],[])
                self.assertIn('文字编码无法可靠读取',original['parse_limitation'])
                request = dict(original['request_binding'],queries=['2.40%'],parts=[],
                    reason='检查数字与文字能否对应原件',question_ids=[task['questions'][0]['id']])
                readback = session.read(task['id'],[request],catalog['binding'])
                self.assertTrue(all(not view['parts'] for view in readback['views']))
                self.assertEqual(session.status(task['id'])['ledger']['used']['model_calls'],0)
            finally:
                session.agent.close_runtime()

    def test_successful_reparse_replaces_the_reading_projection_and_keeps_receipts(self):
        with tempfile.TemporaryDirectory() as work:
            session = Session(work)
            self.addCleanup(session.agent.close_runtime)
            task = session.create('核对原件写了什么', intent='原件内容核对')
            payload = pdf_fixture('mapped', 'unmapped', 'clean')
            name = 'fixture.pdf'
            old = material_record(task, extract_material(name, payload, extract_version='pdf-tables-6'), payload)
            task['materials'].append(old)
            task = session.archive.save(task)
            original_id = session.archive.retain_original(task, 'material', name, payload)
            question_ids = [task['questions'][0]['id']]

            def request(item):
                return dict(item['request_binding'], queries=['2.40%'], parts=item['reading_spans'],
                            reason='核对PDF文字和数值', question_ids=question_ids)

            before = session.sources(task['id'])
            old_request = request(before['items'][0])
            archived_read = session.read(task['id'], [old_request], before['binding'])
            self.assertEqual(archived_read['views'][0]['id'], old['id'])

            session.retry_original(task['id'], original_id)
            task = session.status(task['id'])
            retained, current = task['materials']
            self.assertEqual(retained, old)
            self.assertIn(archived_read, task['source_readings'])
            validate_reading(task, archived_read)
            with self.assertRaisesRegex(ContractError, '请按原件目录读取新版'):
                session.tools(task['id'], reading_hash=archived_read['reading_hash'])
            self.assertEqual(pdf_reparse_replacements(task), {old['id']: current['id']})
            self.assertEqual([view['id'] for view in extractable_source_views(task)], [current['id']])
            self.assertNotIn(old['id'], {source['artifact_id'] for source in session.tools(task['id'])['evidence']['sources']})
            self.assertEqual(source_context(task)[0]['superseded_by'], [current['id']])
            self.assertEqual(source_context(task)[0]['content'], '')
            document, = writing_context(task, [])['documents']
            self.assertEqual(document['source_ref'], current['id'])
            self.assertIn('優先票據', document['parts'][0]['text'])
            self.assertEqual(document['parts'][1]['text'], '')

            latest = session.sources(task['id'])
            retired, available = latest['items']
            self.assertEqual(retired['superseded_by'], current['id'])
            self.assertEqual(retired['reading_spans'], [])
            self.assertNotIn('request_binding', retired)
            readback = session.read(task['id'], [old_request, request(available)], latest['binding'])
            validate_reading(task, readback)
            self.assertEqual([view['id'] for view in readback['views']], [current['id']])
            self.assertEqual(readback['coverage'][0]['superseded_by'], current['id'])
            self.assertEqual(readback['coverage'][0]['mode'], 'not_read')
            self.assertEqual(task['ledger']['used']['model_calls'], 0)
            self.assertEqual(task['ledger']['used']['data_requests'], 0)

    def test_reparse_selection_requires_a_unique_readable_and_bound_original(self):
        task = dict(id='fixture-task', version=1)
        name = 'fixture.pdf'
        payload = pdf_fixture('mapped')
        old = material_record(task, extract_material(name, payload, extract_version='pdf-tables-6'), payload)
        parsed = extract_material(name, payload)
        new = material_record(task, parsed, payload)
        self.assertEqual(pdf_reparse_replacements(dict(task, materials=[new, old])), {old['id']: new['id']})
        failed = material_record(task, dict(parsed, parts=[], status='解析受限'), payload)
        altered = material_record(task, dict(parsed, parts=[dict(locator='第1页', text='不同摘录')]), payload)
        wrong_bytes = material_record(task, parsed, pdf_fixture('unmapped'))
        corrupt = copy.deepcopy(new)
        corrupt['snapshot']['parts'][0]['text'] = '留存哈希失效'
        clean_payload = pdf_fixture('clean')
        clean_old = material_record(task, extract_material(name, clean_payload, extract_version='pdf-tables-6'), clean_payload)
        clean_current = material_record(task, extract_material(name, clean_payload), clean_payload)
        for label, records in (
                ('failed', [old, failed]), ('ambiguous', [old, new, altered]),
                ('different source', [old, dict(new, source='不同来源')]),
                ('different request', [old, dict(new, parameters={'scope': '不同范围'})]),
                ('different version', [old, dict(new, research_version=2)]),
                ('different task', [old, dict(new, task_id='different-task')]),
                ('wrong bytes', [old, wrong_bytes]), ('snapshot changed', [old, corrupt]),
                ('healthy older text', [clean_old, clean_current])):
            with self.subTest(label=label):
                self.assertEqual(pdf_reparse_replacements(dict(task, materials=records)), {})
        self.assertEqual(pdf_reparse_replacements(dict(task, materials=[old], pending_materials=[new])), {})

    def test_completed_host_reply_recovers_locally_with_current_pdf_citations(self):
        with tempfile.TemporaryDirectory() as work:
            session = Session(work)
            self.addCleanup(session.agent.close_runtime)
            task = session.create('整理票据条款与风险', objects=['600519.SH'])
            payload = pdf_fixture('mapped')
            name = 'fixture.pdf'
            old = material_record(task, extract_material(name, payload, extract_version='pdf-tables-6'), payload)
            new = material_record(task, extract_material(name, payload), payload)
            task['materials'] = [old, new]
            session.archive.save(task)
            report = dict(title='票据条款', conclusion='留存原件列明票据条款。',
                          logics=[dict(title='票据条款', analysis='原件载有优先票据条款。',
                                       source_refs=[old['id'], new['id']])])
            with patch('researchhelper.draft_workflow.normalize_report', side_effect=OSError('test save recovery')):
                failed = session.draft(task['id'], report=report)
            submission, = failed['draft_submissions']
            previous_hash = _submission_binding(failed, [], 'host', prefer_current_pdf=False)
            self.assertNotEqual(previous_hash, submission['writing_hash'])
            submission['writing_hash'] = previous_hash
            submission['id'] = fingerprint({key: value for key, value in submission.items()
                                            if key not in ('id', 'received_at')})
            failed = session.archive.save(failed)
            self.assertEqual(retained_submission(failed)['payload'], report)

            delivered = session.draft(task['id'])
            self.assertTrue(delivered.get('delivery'), delivered.get('error'))
            self.assertEqual(delivered['draft_report']['logics'][0]['source_refs'], [new['id']])
            self.assertEqual(delivered['ledger']['used']['model_calls'], 0)
            self.assertEqual(delivered['ledger']['used']['data_requests'], 0)
