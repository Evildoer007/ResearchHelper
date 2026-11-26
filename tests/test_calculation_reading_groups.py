"""A displayed calculation retains its formula, conditions and result as one unit."""
from copy import deepcopy
from io import BytesIO
import socket,unittest
from unittest.mock import patch
from lxml import html
from docx import Document
from tests import ROOT
from tests.test_client_report_audience import fixture
from researchhelper.artifact_exporter import _layout_segments,_docx
from researchhelper.contracts import canonical,fingerprint
from researchhelper.publication_content import project_content
from researchhelper.report_designer import design


def pdf_calculation_pages(path,document):
    """Check ordered titles and results, including repeated calculations by logic."""
    from pypdf import PdfReader
    from tests.accept_native_hidden_print import pdf_text_key
    pages=[pdf_text_key(page.extract_text() or '') for page in PdfReader(path).pages]
    text=''.join(pages);ends=[];offset=0
    for page in pages:offset+=len(page);ends.append(offset)
    cursor=0;checks=[]
    for group in _layout_segments(document['blocks']):
        if group['kind']!='calculation':continue
        title=pdf_text_key(group['blocks'][0]['text']);result=pdf_text_key(group['blocks'][-1]['text'])
        start=text.find(title,cursor);assert start>=0,('Missing calculation title',title)
        finish=text.find(result,start+len(title));assert finish>=0,('Missing calculation result',result)
        title_page=next(index for index,end in enumerate(ends) if start<end)
        result_page=next(index for index,end in enumerate(ends) if finish+len(result)<=end)
        checks.append(dict(title=title,title_page=title_page+1,result_page=result_page+1,together=title_page==result_page))
        cursor=finish+len(result)
    assert checks,'No calculation groups were checked'
    return checks


def word_calculation_groups(raw,document):
    word=Document(BytesIO(raw));paragraphs=word.paragraphs;cursor=0;checks=[]
    for group in _layout_segments(document['blocks']):
        if group['kind']!='calculation':continue
        title=group['blocks'][0]['text']
        start=next(index for index in range(cursor,len(paragraphs)) if paragraphs[index].text==title)
        rows=paragraphs[start:start+len(group['blocks'])]
        assert rows[-1].text==group['blocks'][-1]['text']
        assert all(row.paragraph_format.keep_together for row in rows)
        assert [row.paragraph_format.keep_with_next for row in rows]==[True]*(len(rows)-1)+[False]
        assert any(row._p.xpath('.//m:oMath') for row in rows)
        checks.append(dict(title=title,paragraphs=len(rows),kept_as_group=True));cursor=start+len(rows)
    assert checks,'No Word calculation groups were checked'
    return checks


class CalculationReadingGroupsTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Offline only'))
        guard.start();self.addCleanup(guard.stop)
        self.task,self.content=fixture()
    def test_every_selected_calculation_has_one_complete_group_per_logic(self):
        before=canonical(self.content);projection=project_content(self.content,'detailed')
        paper=design(self.content,ROOT,profile='detailed');tree=html.fromstring(paper['html'])
        expected=[]
        for logic,blocks in projection['logic_blocks'].items():
            refs=list(dict.fromkeys(block['calculation_ref'] for block in blocks if block.get('calculation_ref')))
            for ref in refs:
                expected.append(logic+':'+ref)
                nodes=tree.xpath('//*[@data-logic-detail="'+logic+'"]//*[@class="calculation-reading publication-detail-unit"][@data-calculation-ref="'+ref+'"]')
                self.assertEqual(len(nodes),1)
                self.assertIn('结果：',nodes[0].xpath('./p')[-1].text_content())
                self.assertTrue(nodes[0].xpath('.//*[local-name()="math"]'))
        self.assertTrue(expected)
        actual=list(dict.fromkeys(block['calculation_group'] for block in paper['document']['blocks'] if block.get('calculation_group')))
        self.assertEqual(actual,expected);self.assertEqual(canonical(self.content),before)
    def test_no_formula_or_number_is_lost_when_groups_are_segmented(self):
        document=design(self.content,ROOT,profile='detailed')['document']
        groups=[row for row in _layout_segments(document['blocks']) if row['kind']=='calculation']
        actual=[{key:value for key,value in row.items() if key not in ('calculation_group','calculation_ref')}
                for group in groups for row in group['blocks']]
        expected=[{key:value for key,value in row.items() if key not in ('calculation_group','calculation_ref')}
                  for row in document['blocks'] if row.get('calculation_group')]
        self.assertEqual(actual,expected)
        self.assertEqual(document['dataset'],design(self.content,ROOT)['document']['dataset'])
    def test_calculation_heading_stays_with_only_the_first_calculation(self):
        paper=design(self.content,ROOT,profile='detailed')
        tree=html.fromstring(paper['html'])
        headings=tree.xpath('//main//h3[text()="计算依据"]')
        self.assertTrue(headings)
        for heading in headings:
            first=heading.getparent()
            self.assertEqual(first.get('class'),'calculation-reading publication-detail-unit')
            self.assertTrue(first.get('data-calculation-ref'))
            self.assertTrue(first.xpath('.//*[local-name()="math"]'))
            self.assertIn('结果：',first[-1].text_content())
        groups=[group['blocks'] for group in _layout_segments(paper['document']['blocks']) if group['kind']=='calculation']
        self.assertEqual(sum(group[0].get('text')=='计算依据' for group in groups),len(headings))
    def test_word_keeps_each_calculation_together_without_linking_the_next_one(self):
        document=design(self.content,ROOT,profile='detailed')['document']
        checks=word_calculation_groups(_docx(document),document);self.assertTrue(checks)
    def test_a_container_without_a_calculation_id_keeps_its_original_text(self):
        from researchhelper.report_designer import _blocks
        node=html.fromstring('<main><div class="calculation-reading"><p>仅作方法说明。</p></div></main>')
        self.assertEqual(_blocks(node,{}),[dict(kind='paragraph',text='仅作方法说明。')])

    def test_long_conditions_remain_complete_and_standard_has_no_internal_groups(self):
        content=deepcopy(self.content);condition='该结果仍取决于相同观察期间、输入尺度和报告口径。'*100
        content['draft']['calculations'][0]['condition']=condition
        content.pop('content_hash',None);content['content_hash']=fingerprint(content)
        detail=design(content,ROOT,profile='detailed');tree=html.fromstring(detail['html'])
        self.assertIn(condition,tree.xpath('//main')[0].text_content())
        standard=design(self.content,ROOT);self.assertNotIn('calculation-reading',standard['html'])
        self.assertFalse(any(row.get('calculation_group') for row in standard['document']['blocks']))


if __name__=='__main__':unittest.main()
