"""Check original-document attribution before a draft becomes a report."""
from copy import deepcopy
import socket
from unittest.mock import patch

from lxml import html
import pytest

from tests import ROOT
from tests.test_announcement_partial_delivery import ifind_query, ifind_row
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import fingerprint
from researchhelper.draft_sources import writing_context
from researchhelper.drafts import compose, normalize_report, source_binding
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import filing_record, snapshot_record, validate_filing_original_request
from researchhelper.report_designer import design


def seed_disclosures(task):
    """Two same-title catalogues with different original bodies."""
    rows = [('600519.SH', '甲股份', '19', '股价下跌主要源于渠道收缩'),
            ('000001.SZ', '乙股份', '7', '股价下跌主要源于需求回落')]
    originals = []
    for index, (code, name, number, quote) in enumerate(rows, 1):
        raw = ifind_row(code, name, reportTitle='季度经营披露', seq='filing'+str(index),
                        pdfURL=f'https://static.cninfo.com.cn/finalpage/2026-10-15/122500000{index}.PDF')
        catalogue = snapshot_record(task, 'ifind', 'report_query', ifind_query(code),
                                    {'errorcode':0, 'data':[raw]})
        task['source_records'].append(catalogue)
        directory = validate_filing_original_request(task,
            {'catalog_id':catalogue['id'],'source_id':raw['seq']})
        original = filing_record(task, directory, 'original.txt',
            ('合并报表2025年营业收入同比增长'+number+'%。\n\n'+quote+'。').encode())
        originals.append(original)
        task['source_records'].append(original)
    return originals


def disclosure_proposal(originals):
    a, b = (original['id'] for original in originals)
    return dict(title='公告出处核对', conclusion='两家公司披露分别核对。', logics=[
        dict(title='发行人甲', analysis='甲股份公告称“股价下跌主要源于渠道收缩”。'
             '甲股份合并报表2025年营业收入同比增长19%。', source_refs=[a]),
        dict(title='发行人乙', analysis='乙股份公告称“股价下跌主要源于渠道收缩”。'
             '乙股份合并报表2025年营业收入同比增长7%。', source_refs=[a,b]),
        dict(title='错用另一家公司读数', analysis='乙股份合并报表2025年营业收入同比增长19%。',
             source_refs=[a]),
    ])


@pytest.fixture
def scenario(tmp_path):
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
        agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True)
        try:
            task = agent.create('核对600519.SH和000001.SZ的同名公告', entry='事件',
                                objects=['600519.SH','000001.SZ'])
            originals = seed_disclosures(task)
            yield task, originals
        finally:
            agent.close_runtime()


def test_writing_context_preserves_issuer_and_distinguishes_directory_date(scenario):
    task, originals = scenario
    before = fingerprint(task)
    documents = writing_context(task, [])['documents']
    assert [row['source_ref'] for row in documents] == [item['id'] for item in originals]
    assert [row['disclosure']['issuer_code'] for row in documents] == ['600519.SH','000001.SZ']
    assert {row['disclosure']['title'] for row in documents} == {'季度经营披露'}
    assert {row['disclosure']['directory_date'] for row in documents} == {'2026-10-15'}
    assert all(row['published_at'] is None and 'provider_time_hint' not in row['disclosure'] for row in documents)
    assert fingerprint(task)==before


def check_disclosure_draft(task, report, originals):
    first, second, mismatched = report['logics']
    assert '甲股份公告称“股价下跌主要源于渠道收缩”' in first['analysis']
    assert '19%' in first['analysis']
    assert '渠道收缩' not in second['analysis']
    assert '引文仍待核对' in second['analysis'] and '7%' in second['analysis']
    assert '19%' not in mismatched['analysis'] and '缺少可核对的读数' in mismatched['analysis']
    for logic, original, passages in zip((first, second), originals,
            (['股价下跌主要源于渠道收缩', '19%'], ['7%']), strict=True):
        links = logic['quantity_links']['analysis']['links']
        assert [link['original'] for link in links] == passages
        for link in links:
            assert link['kind']=='document_excerpt'
            assert link['document']['source_ref']==original['id']
            assert link['document']['snapshot_hash']==original['hash']
    assert any(item['reason']=='draft_disclosure_issuer_unsubstantiated'
               for item in report['copy_adjustments'])


def check_disclosure_report(text):
    tree = html.document_fromstring(text)
    for index, issuer, amount in ((1,'甲股份 600519.SH','19%'), (2,'乙股份 000001.SZ','7%')):
        section = tree.get_element_by_id('logic-'+str(index))
        passages = [('股价下跌主要源于渠道收缩', 2), (amount, 1)] if index == 1 else [(amount, 1)]
        links = section.xpath('.//a[@class="quantity-link"]')
        assert len(links) == len(passages)
        for link, (reading, paragraph) in zip(links, passages, strict=True):
            assert link.text_content()==reading
            popup = tree.get_element_by_id(link.get('href')[1:])
            assert popup.xpath('./h3/text()')==['季度经营披露']
            assert popup.xpath('./p[@class="figure-source"]/text()')==[issuer+f'，目录日期2026-10-15，段落{paragraph}']
            assert reading in popup.text_content()
            assert '首发时间' not in popup.text_content()
    assert not tree.get_element_by_id('logic-3').xpath('.//a[@class="quantity-link"]')


def test_named_quote_and_amount_use_the_actual_original(scenario):
    task, originals = scenario
    before = fingerprint(task)
    report = normalize_report(disclosure_proposal(originals), task, [])
    check_disclosure_draft(task, report, originals)
    assert fingerprint(task)==before
    task['draft_report'] = dict(report, facts=[], calculations=[], source_binding=source_binding(task))
    content = compose(task)
    for profile in ('standard','detailed'):
        check_disclosure_report(design(content, ROOT, profile=profile)['html'])
    altered = deepcopy(content)
    original = next(row for row in altered['source_records'] if row['id']==originals[0]['id'])
    original['snapshot']['directory']['title'] = original['directory']['title'] = '替换的标题'
    original['hash'] = fingerprint(original['snapshot'])
    altered['content_hash'] = fingerprint({key:value for key,value in altered.items() if key!='content_hash'})
    tree = html.document_fromstring(design(altered, ROOT)['html'])
    assert not tree.get_element_by_id('logic-1').xpath('.//a[@class="quantity-link"]')


def test_changed_extraction_is_quarantined_locally(scenario):
    task, originals = scenario
    changed, good = originals
    changed['snapshot']['material']['parts'][0]['text'] = '合并报表2025年营业收入同比增长99%。'
    documents = writing_context(task, [])['documents']
    assert [row['source_ref'] for row in documents]==[good['id']]
    report = normalize_report(disclosure_proposal(originals), task, [])
    assert changed['id'] not in report['logics'][0]['source_refs']
    assert '7%' in report['logics'][1]['analysis']
    assert '99%' in changed['snapshot']['material']['parts'][0]['text']


def test_long_original_links_to_the_full_part_and_offset(tmp_path):
    claim = '甲股份合并报表2025年营业收入同比增长19%。'
    long_part = '历史条款仅供存档。' * 2600 + claim + '附录保留备查。' * 1200
    raw = '\n\n'.join(['甲股份报告目录', '无关附件。' * 5000, long_part])
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
        agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True)
        try:
            task = agent.create('核对600519.SH的2025年营业收入同比增长。', objects=['600519.SH'])
            task = agent.add_material(task['id'], '公司披露.txt', raw.encode())
            original, = task['materials']
            doc, = writing_context(task, [])['documents']
            assert doc['truncated'] and [part['locator'] for part in doc['parts']]==['段落1','段落3']
            assert claim in doc['parts'][1]['text'] and not doc['parts'][1]['text'].startswith('历史条款仅供存档。'*1000)
            value = dict(title='营业收入核对',conclusion=claim,logics=[dict(
                title='营业收入同比',analysis=claim,source_refs=[original['id']])])
            report = normalize_report(value, task, [])
            link, = report['quantity_links']['conclusion']['links']
            assert link['document']['part_index']==2
            assert link['document']['start']==long_part.index(claim)
            start, end = link['document']['start'], link['document']['end']
            assert original['snapshot']['parts'][2]['text'][start:end]==claim.rstrip('。')
            task['draft_report'] = dict(report,facts=[],calculations=[],source_binding=source_binding(task))
            content = compose(task)
            for profile in ('standard','detailed'):
                tree = html.document_fromstring(design(content,ROOT,profile=profile)['html'])
                anchor, = tree.get_element_by_id('summary').xpath('.//a[@class="quantity-link"]')
                dialog = tree.get_element_by_id(anchor.get('href')[1:])
                assert anchor.text_content()=='19%'
                assert '段落3' in dialog.text_content() and claim.rstrip('。') in dialog.text_content()
        finally:
            agent.close_runtime()


def test_long_single_part_keeps_two_distant_requested_findings(tmp_path):
    from core.session import Session

    income = '甲股份合并报表2025年营业收入同比增长19%。'
    cashflow = '甲股份合并报表2025年经营活动产生的现金流量净额为25亿元。'
    raw = '报告说明。' + '历史条款仅供存档。'*1800 + income + '一般事项留存备查。'*2400 + cashflow + '附录。'*1500
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
        session = Session(tmp_path)
        try:
            task = session.create('核对所附公司文件中2025年营业收入同比和经营活动产生的现金流量净额。')
            record = session.material(task['id'], '年度披露.txt', raw.encode())
            task = session.archive.get(task['id'])
            original, = task['materials'][0]['snapshot']['parts']
            assert original['text']==raw
            doc, = session.tools(task['id'], scope='first_report')['first_report']['writing_context']['documents']
            assert doc['truncated']
            assert sum(len(part['text']) for part in doc['parts']) <= 24_000
            for finding in (income, cashflow):
                part, = [part for part in doc['parts'] if finding in part['text']]
                offset = part['source_offset']
                assert part['source_part_index']==0 and offset>0
                assert raw[offset:offset+len(part['text'])]==part['text']
            done = session.draft(task['id'], data_requests=[], report=dict(title='年度财务披露核对',
                conclusion=income+cashflow, logics=[dict(title='两项财务读数', analysis=income+cashflow,
                    source_refs=[record['id']])]))
            assert done.get('delivery'), done.get('error')
            links = done['draft_report']['quantity_links']['conclusion']['links']
            assert [link['original'] for link in links]==['19%', '25亿元']
            assert [link['document']['start'] for link in links]==[raw.index(income), raw.index(cashflow)]
            for profile in ('standard','detailed'):
                page = html.parse(str(session.export(task['id'], profile=profile)))
                anchors = page.xpath('//*[@id="summary"]//a[@class="quantity-link"]')
                assert [anchor.text_content() for anchor in anchors]==['19%', '25亿元']
                for anchor, finding in zip(anchors, (income,cashflow)):
                    excerpt, = page.xpath('//*[@id=$id]', id=anchor.get('href')[1:])
                    assert finding.rstrip('。') in excerpt.text_content()
        finally:
            session.agent.close_runtime()


@pytest.mark.parametrize('suffix,locator,claim', [
    ('txt', '段落2', '甲股份合并报表2025年营业收入同比增长19%。'),
    ('pdf', '第2页', 'Revenue growth was 19%.'),
])
def test_empty_opening_part_keeps_the_next_part_and_delivers(tmp_path, suffix, locator, claim):
    from core.session import Session
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from tests.test_filing_acquisition_offline import synthetic_pdf

    if suffix == 'txt':
        payload = ('\n\n'+claim+'\n\n').encode()
    else:
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        writer.append(PdfReader(BytesIO(synthetic_pdf(statement=claim))))
        writer.add_blank_page(width=300, height=300)
        output = BytesIO()
        writer.write(output)
        payload = output.getvalue()

    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
        session = Session(tmp_path)
        try:
            task = session.create('只核对原件的营业收入增幅。')
            record = session.material(task['id'], '材料.'+suffix, payload)
            saved = session.archive.get(task['id'])
            original_parts = saved['materials'][0]['snapshot']['parts']
            assert [part['text'] for part in original_parts]==['', claim, '']
            context = session.tools(task['id'], scope='first_report')['first_report']['writing_context']
            document, = context['documents']
            part, = document['parts']
            assert document['source_ref']==record['id'] and not document['truncated']
            assert part==dict(locator=locator, text=claim, source_part_index=1, source_offset=0)

            done = session.draft(task['id'], data_requests=[], report=dict(
                title='原件核对', conclusion=claim,
                logics=[dict(title='营业收入增幅', analysis=claim, source_refs=[record['id']])]))
            assert done.get('delivery'), done.get('error')
            assert done['ledger']['used']['model_calls']==done['ledger']['used']['data_requests']==0
            for profile in ('standard','detailed'):
                paper = html.parse(str(session.export(task['id'], profile=profile)))
                assert claim in paper.find('.//main').text_content()
        finally:
            session.agent.close_runtime()


@pytest.mark.parametrize('suffix', ['docx', 'md'])
@pytest.mark.parametrize('include_amount', [True, False])
def test_local_attachment_retains_separate_requested_passages(tmp_path, suffix, include_amount):
    from core.session import Session
    from docx import Document
    from io import BytesIO

    effective = '本政策生效日期为2026年12月1日。'
    amount = '本政策费用上限为100百万元。'
    paragraphs = ['合成办公园区维护政策，仅供验收。', effective, amount,
                  effective, '与本次核对无关的联系人信息另行告知。']
    if suffix == 'docx':
        document = Document()
        for text in paragraphs:
            document.add_paragraph(text)
        stream = BytesIO()
        document.save(stream)
        payload = stream.getvalue()
    else:
        payload = '\n\n'.join(paragraphs).encode()

    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
        session = Session(tmp_path)
        try:
            requested = '生效日期和费用上限' if include_amount else '生效日期'
            task = session.create(f'本轮不联网，仅核对附件政策的{requested}，生成报告。')
            record = session.material(task['id'], '政策.'+suffix, payload)
            frozen = session.archive.get(task['id'])['materials']
            done = session.resume(task['id'])
            assert done.get('delivery'), done.get('error')
            assert done['ledger']['used']['model_calls']==done['ledger']['used']['data_requests']==0
            assert done['materials']==frozen
            expected = [(effective,'段落2')] + ([(amount,'段落3')] if include_amount else [])
            for profile in ('standard','detailed'):
                page = html.parse(str(session.export(task['id'], profile=profile)))
                quotes = page.xpath('//main//q')
                assert [quote.text_content() for quote in quotes]==[text for text,_ in expected]
                for quote, (text,locator) in zip(quotes,expected,strict=True):
                    link, = quote.getparent().getnext().xpath('.//a')
                    target = page.getroot().get_element_by_id(link.get('href')[1:])
                    assert text in target.text_content() and locator in target.text_content()
            assert record['id']==done['materials'][0]['id']
        finally:
            session.agent.close_runtime()


def test_range_that_cannot_fit_leaves_room_for_the_next_part(tmp_path):
    note = '说明。'
    payload = ('0.00%至\n4.00%。\n\n'+note).encode()
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
        agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True)
        try:
            task = agent.create('核对原件。')
            task = agent.add_material(task['id'], '原件.txt', payload)
            document, = writing_context(task, [], text_limit=9)['documents']
            assert document['truncated']
            assert document['parts']==[dict(locator='段落2', text=note, source_part_index=1, source_offset=0)]
        finally:
            agent.close_runtime()


def test_excerpt_edges_cannot_turn_a_conditional_or_wrapped_range_into_a_point(tmp_path):
    conditional = '公司预计2025年营业收入同比增长93%。'
    observed = '公司2025年营业收入同比增长19%。'
    range_text = '短期借款利率区间为0.00%至\n4.00%。'
    # The opening slice would stop before 4.00%, and the long statement
    # cannot be copied in full by a single selected window.
    raw = ('附录。'*229 + range_text + '附录。'*3000
           + '若' + '约束'*6800 + conditional + '说明。'*1900 + observed)
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
        agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True)
        try:
            task = agent.create('核对所附公司2025年营业收入同比和短期借款利率区间。')
            record = agent.add_material(task['id'], '年度披露.txt', raw.encode(), return_record=True)
            task = agent.archive.get(task['id'])
            document, = writing_context(task, [])['documents']
            assert any(range_text in part['text'] for part in document['parts'])
            assert all(('0.00%' in part['text']) == ('4.00%' in part['text']) for part in document['parts'])
            assert any(part.get('partial_start') and conditional in part['text'] for part in document['parts'])
            proposal = dict(title='报告原文核对', conclusion=observed,
                logics=[dict(title='观察与条件',analysis='公司2025年营业收入同比增长93%。'+observed+range_text,
                             source_refs=[record['id']])])
            result = normalize_report(proposal, task, [])
            assert '增长93%' not in result['logics'][0]['analysis']
            assert observed in result['logics'][0]['analysis']
            assert range_text in result['logics'][0]['analysis']
        finally:
            agent.close_runtime()


def test_official_disclosure_keeps_listing_time_as_a_separate_field(tmp_path):
    from tests.test_filing_acquisition_offline import synthetic_pdf
    from tests.test_independent_official_sources import HK_ISSUER, hk_raw, params
    from researchhelper.evidence import EvidenceCollector
    from researchhelper.official_sources import selection_references

    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
        agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True)
        try:
            task = agent.create('核对0700.HK披露易原件', entry='事件', objects=['0700.HK'])
            collector = EvidenceCollector(offline=False)
            with patch('researchhelper.source_transport.official_announcement',
                       side_effect=[HK_ISSUER, hk_raw()]):
                catalogue = collector.external(task,'official','release_catalog',params('hkex'),{})
            task['source_records'].append(catalogue)
            selection, = selection_references(task)
            with patch('researchhelper.source_transport.official_announcement', return_value=synthetic_pdf()):
                original = collector.external(task,'official','release_original',
                    dict(selection_ref=selection['selection_ref']),{})
            task['source_records'].append(original)
            document, = writing_context(task, [])['documents']
            assert document['disclosure']['issuer_code']=='0700.HK'
            assert document['disclosure']['listed_at']=='2026-10-15T12:03:00+08:00'
            assert document['disclosure']['directory_date']=='2026-10-15'
            assert document['published_at'] is None
            changed = deepcopy(original)
            changed['snapshot']['official_origin']['title']='被替换的目录标题'
            changed['snapshot_hash']=fingerprint(changed['snapshot'])
            task['source_records'][-1]=changed
            assert writing_context(task, [])['documents']==[]
        finally:
            agent.close_runtime()
