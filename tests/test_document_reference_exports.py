"""Question-to-original navigation survives actual HTML-to-Office/PDF export."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from lxml import etree, html
from pypdf import PdfReader
from researchhelper.artifact_exporter import _docx, _pdf, convert_saved_html, export_report
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.report_designer import design, document_reference_blocks
from tests import test_document_content_offline as fixtures

ROOT=Path(__file__).resolve().parents[1]
NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


def word_tree(raw):
    with zipfile.ZipFile(BytesIO(raw)) as package:
        assert package.testzip() is None
        return etree.fromstring(package.read('word/document.xml'))


class DocumentReferenceExportTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DocumentContentTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        task=self.fixture.material_task()
        done=self.fixture.run_spec(task,self.fixture.spec(task))
        self.content=self.fixture.agent.export(done['id'])['content']
        self.frozen=self.fixture.agent.archive.report(done['id'],done['version'])
        self.task_id=done['id']
        folder=tempfile.TemporaryDirectory(prefix='document-reference-exports-',dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup);self.output=Path(folder.name)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def assert_word_links(self,raw,expected):
        tree=word_tree(raw)
        targets=tree.xpath('//w:hyperlink/@w:anchor',namespaces=NS)
        bookmarks=tree.xpath('//w:bookmarkStart/@w:name',namespaces=NS)
        self.assertEqual(len(targets),expected)
        self.assertEqual(len(bookmarks),len(set(bookmarks)))
        self.assertTrue(set(targets)<=set(bookmarks))
        for target in targets:self.assertRegex(target,r'^rh_[0-9a-f]{32}$')
        self.assertFalse(tree.xpath('//w:hyperlink/@*[local-name()="id"]',namespaces=NS))
        return tree

    def test_both_profiles_save_html_then_convert_clickable_word_references(self):
        original=canonical(self.content)
        for profile in ('standard','detailed'):
            with self.subTest(profile=profile):
                receipt=export_report(self.content,ROOT,self.output,profile=profile,format='docx')
                raw=Path(receipt['path']).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(),receipt['sha256'])
                source=Path(receipt['source_html']).read_bytes()
                self.assertEqual(hashlib.sha256(source).hexdigest(),receipt['source_html_sha256'])
                self.assertTrue(receipt['converted_from_saved_html'])
                tree=self.assert_word_links(raw,1)
                text=''.join(tree.xpath('//w:t/text()',namespaces=NS))
                self.assertIn('2.1 文件写了什么？',text)
                self.assertIn('证据1',text)
                source_tree=html.document_fromstring(source)
                source_main=source_tree.find('.//main')
                quote=self.content['evidence'][0]['text']
                # Detailed HTML also retains its research-basis table. The
                # conversion must preserve that table, not silently deduplicate
                # the frozen presentation to satisfy a compact-profile rule.
                self.assertEqual(text.count(quote),source_main.text_content().count(quote))
                self.assertEqual(sum(block.get('kind')=='paragraph' and block.get('text')==quote
                    for block in json.loads(source_tree.get_element_by_id('rh-publication-document').text)['blocks']),1)
                self.assertEqual(canonical(self.content),original)
        self.assertEqual(self.fixture.agent.archive.report(self.task_id,1),self.frozen)

    def test_legacy_html_recovers_links_without_rewriting_saved_bytes_or_data(self):
        publication=design(self.content,ROOT)
        tree=html.document_fromstring(publication['html'])
        node=tree.get_element_by_id('rh-publication-document')
        legacy=json.loads(node.text)
        legacy['blocks']=[{key:value for key,value in block.items() if key!='anchor'}
            for block in legacy['blocks'] if not block.get('inline_links')]
        node.text=json.dumps(legacy,ensure_ascii=False).replace('<','\\u003c')
        raw=html.tostring(tree,encoding='utf-8');source=self.output/'legacy.html';source.write_bytes(raw)
        digest=hashlib.sha256(raw).hexdigest()
        converted,document=convert_saved_html(source,'docx',expected_sha256=digest)
        self.assert_word_links(converted,1)
        self.assertEqual(source.read_bytes(),raw)
        self.assertEqual(document['dataset'],legacy['dataset'])
        self.assertEqual(document['content_hash'],legacy['content_hash'])
        self.assertEqual(convert_saved_html(source,'html',expected_sha256=digest)[0],raw)

    def test_two_questions_share_one_excerpt_and_one_destination(self):
        content=deepcopy(self.content)
        second=deepcopy(content['questions'][0]);second.update(id='q2',question='原件载明什么生效时间？')
        content['questions'].append(second)
        content['content_hash']=fingerprint({key:value for key,value in content.items() if key!='content_hash'})
        document=design(content,ROOT)['document']
        tree=self.assert_word_links(_docx(document),2)
        self.assertEqual(len(tree.xpath('//w:bookmarkStart',namespaces=NS)),1)
        self.assertEqual(''.join(tree.xpath('//w:t/text()',namespaces=NS)).count(content['evidence'][0]['text']),1)

    def test_reference_projection_is_idempotent_and_does_not_mutate_input(self):
        publication=design(self.content,ROOT)
        blocks=publication['document']['blocks'];before=deepcopy(blocks)
        main=html.fromstring(publication['html']).find('.//main')
        self.assertEqual(document_reference_blocks(blocks,main),blocks)
        self.assertEqual(blocks,before)
        self.assertEqual(sum(bool(row.get('inline_links')) for row in blocks),1)

    def test_fallback_pdf_has_a_real_internal_link_and_the_original_excerpt(self):
        document=design(self.content,ROOT)['document'];raw=_pdf(document);reader=PdfReader(BytesIO(raw))
        links=[item.get_object() for page in reader.pages for item in page.get('/Annots',[])]
        self.assertEqual(len(links),1)
        self.assertEqual(links[0]['/Subtype'],'/Link')
        self.assertIn('/Dest',links[0])
        self.assertNotIn('/A',links[0])
        self.assertIn(self.content['evidence'][0]['text'],''.join(page.extract_text() for page in reader.pages))

    def test_missing_or_duplicate_destinations_preserve_text_without_bad_links(self):
        publication=design(self.content,ROOT);document=publication['document']
        for kind in ('missing','duplicate'):
            altered=deepcopy(document)
            original=next(block for block in altered['blocks'] if block.get('anchor'))
            if kind=='missing':del original['anchor']
            else:altered['blocks'].append(deepcopy(original))
            with self.subTest(kind=kind):
                tree=self.assert_word_links(_docx(altered),0)
                self.assertIn('证据1',''.join(tree.xpath('//w:t/text()',namespaces=NS)))

    def test_unknown_or_ambiguous_html_target_does_not_manufacture_a_link(self):
        publication=design(self.content,ROOT)
        tree=html.fromstring(publication['html']);main=tree.find('.//main')
        blocks=[{key:value for key,value in block.items() if key!='anchor'}
            for block in publication['document']['blocks'] if not block.get('inline_links')]
        link=main.xpath('.//article[@class="document-answer-index"]//a')[0]
        link.set('href','#ev-missing')
        result=document_reference_blocks(blocks,main)
        self.assertFalse(any(block.get('inline_links') for block in result))
        self.assertEqual(result,blocks)

    def test_changed_html_is_rejected_before_conversion(self):
        source=self.output/'changed.html';source.write_text(design(self.content,ROOT)['html'])
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        with source.open('ab') as stream:stream.write(b'<!-- changed -->')
        with self.assertRaises(ContractError):convert_saved_html(source,'docx',expected_sha256=digest)
