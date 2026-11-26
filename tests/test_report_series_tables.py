"""Source records survive dense report tables without invented or rounded cells."""
from copy import deepcopy
from datetime import date,timedelta
from html import escape
import math,unittest
from lxml import etree,html
from researchhelper.report_designer import _compact_series_tables,_blocks


def fixture(count=41,*,start=0,source='saved-source',unit='元',name=None,value=None):
    facts=[];rows=[]
    headers=['标的']+(['名称'] if name is not None else [])+['指标','期间','原值','单位']
    for offset in range(count):
        ident='D'+str(start+offset+1);period=(date(2026,1,1)+timedelta(days=offset)).isoformat()
        raw=value if value is not None else str(1000+offset)+'.1250'
        facts.append(dict(id=ident,code='600519.SH',field='close',unit=unit,series_basis='same-price-basis',
                          artifact_id=source,axis_role='time',period=period,raw_value=raw))
        cells=['600519.SH']+([name] if name is not None else [])+['收盘价',period,raw,unit]
        rows.append('<tr data-observation-id="'+ident+'">'+''.join('<td>'+escape(cell)+'</td>' for cell in cells)+'</tr>')
    return facts,headers,rows


def paper(facts,headers,rows):
    return html.fromstring('<main><section id="data"><h2>关键数据</h2><div aria-label="完整原始数据，可横向滚动"><table><thead><tr>'+
        ''.join('<th>'+name+'</th>' for name in headers)+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div></section></main>')


def visible_records(main,headers):
    result=[];period=headers.index('期间');value=headers.index('原值')
    for node in main.xpath('.//*[@data-observation-id]'):
        if node.tag=='tr':result.append((node.get('data-observation-id'),node[period].text_content(),node[value].text_content()))
        else:result.append((node.get('data-observation-id'),node.getprevious().text_content(),node.text_content()))
    return result


class ReportSeriesTablesTests(unittest.TestCase):
    def test_every_period_value_and_record_survives_once_in_source_order(self):
        facts,headers,rows=fixture();main=paper(facts,headers,rows)
        before=visible_records(main,headers);frozen=deepcopy(facts)
        _compact_series_tables(main,facts)
        self.assertEqual(visible_records(main,headers),before)
        self.assertEqual(facts,frozen)
        packed=main.xpath('.//table[@data-series-bands]');self.assertEqual(len(packed),1)
        bands=int(packed[0].get('data-series-bands'))
        self.assertGreaterEqual(bands,2);self.assertLessEqual(bands,4)
        self.assertEqual(len(packed[0].xpath('./tbody/tr')),math.ceil(len(facts)/bands))
        self.assertIn('600519.SH',packed[0].find('caption').text)
        self.assertIn('收盘价',packed[0].find('caption').text)
        self.assertIn('单位：元',packed[0].find('caption').text)
        self.assertEqual(len(packed[0].xpath('.//*[@data-observation-id]')),len(facts))

    def test_other_records_keep_their_complete_original_columns(self):
        short,headers,first=fixture(2)
        long,_,middle=fixture(41,start=2,source='long-source')
        final,_,last=fixture(1,start=43,source='final-source')
        facts=short+long+final;main=paper(facts,headers,first+middle+last)
        expected=visible_records(main,headers);_compact_series_tables(main,facts)
        self.assertEqual(visible_records(main,headers),expected)
        self.assertEqual([len(t.xpath('./tbody/tr')) for t in main.xpath('.//table')],[2,11,1])
        for node in main.xpath('.//tr[@data-observation-id]'):self.assertEqual(len(node),len(headers))

    def test_distinct_sources_and_units_never_merge(self):
        left,headers,first=fixture(28,source='first')
        right,_,last=fixture(28,start=28,source='second',unit='万元')
        main=paper(left+right,headers,first+last);expected=visible_records(main,headers)
        _compact_series_tables(main,left+right)
        self.assertEqual(visible_records(main,headers),expected)
        captions=main.xpath('.//table[@data-series-bands]/caption/text()')
        self.assertEqual(len(captions),2);self.assertIn('单位：元',captions[0]);self.assertIn('单位：万元',captions[1])

    def test_nonconsecutive_same_series_never_reorders_the_middle_source(self):
        facts=[];rows=[]
        for start,source in ((0,'first'),(28,'middle'),(56,'first')):
            block,headers,body=fixture(28,start=start,source=source);facts+=block;rows+=body
        main=paper(facts,headers,rows);expected=visible_records(main,headers)
        _compact_series_tables(main,facts)
        self.assertEqual(visible_records(main,headers),expected)
        self.assertEqual(len(main.xpath('.//table[@data-series-bands]')),3)

    def test_conflicting_or_repeated_periods_keep_the_original_table(self):
        facts,headers,rows=fixture();facts[1]['period']=facts[0]['period']
        rows[1]=rows[1].replace('2026-01-02','2026-01-01')
        main=paper(facts,headers,rows);before=etree.tostring(main)
        _compact_series_tables(main,facts)
        self.assertEqual(etree.tostring(main),before)

    def test_wide_raw_numbers_reduce_columns_without_losing_digits(self):
        token='12345678901234567890.123400'
        facts,headers,rows=fixture(value=token);main=paper(facts,headers,rows)
        _compact_series_tables(main,facts)
        self.assertEqual([row[2] for row in visible_records(main,headers)],[token]*len(facts))
        self.assertLess(int(main.xpath('.//table[@data-series-bands]')[0].get('data-series-bands')),4)

    def test_extremely_wide_values_fall_back_without_truncation(self):
        facts,headers,rows=fixture(value='9'*120+'.0000');main=paper(facts,headers,rows);before=etree.tostring(main)
        _compact_series_tables(main,facts)
        self.assertEqual(etree.tostring(main),before)

    def test_unconfirmed_unit_and_optional_name_remain_literal_caption_text(self):
        facts,headers,rows=fixture(unit='原始单位待核对',name='<b>原名称</b>');main=paper(facts,headers,rows)
        expected=visible_records(main,headers);_compact_series_tables(main,facts)
        self.assertEqual(visible_records(main,headers),expected)
        caption=main.xpath('.//table[@data-series-bands]/caption')[0]
        self.assertIn('<b>原名称</b>',caption.text_content());self.assertIn('原始单位待核对',caption.text_content())
        self.assertEqual(caption.xpath('.//b'),[])

    def test_short_unbound_or_named_tables_remain_unchanged(self):
        for mode in ('short','unbound','named','matrix'):
            with self.subTest(mode=mode):
                facts,headers,rows=fixture(10 if mode=='short' else 41)
                main=paper(facts,headers,rows)
                if mode=='unbound':main.xpath('.//tbody/tr')[0].attrib.pop('data-observation-id')
                if mode=='named':main.xpath('.//table')[0].set('id','original-table')
                if mode=='matrix':main.xpath('.//thead/tr/th')[0].text='其他原列'
                before=etree.tostring(main);_compact_series_tables(main,facts)
                self.assertEqual(etree.tostring(main),before)

    def test_table_caption_carries_identity_and_unit_to_export_blocks(self):
        node=html.fromstring('<main><table><caption>600519.SH收盘价，单位：元</caption><tr><th>期间</th><th>原值</th></tr><tr><td>2026-01-01</td><td>1439.1250</td></tr></table></main>')
        blocks=_blocks(node,{})
        self.assertEqual(blocks[0],dict(kind='caption',text='600519.SH收盘价，单位：元'))
        self.assertEqual(blocks[1]['rows'][1],['2026-01-01','1439.1250'])

    def test_existing_heading_captions_are_not_duplicated_as_plain_captions(self):
        node=html.fromstring('<main><table><caption class="publication-table-title"><h2>原始数据</h2></caption><tr><th>期间</th><th>原值</th></tr></table></main>')
        blocks=_blocks(node,{})
        self.assertEqual(blocks[0],dict(kind='heading',level=2,text='原始数据'))
        self.assertEqual(sum(row['kind']=='caption' for row in blocks),0)

    def test_a_trailing_note_survives_once_after_all_series_fragments(self):
        first,headers,left=fixture(2)
        middle,_,rows=fixture(41,start=2,source='series')
        final,_,right=fixture(1,start=43,source='tail')
        main=paper(first+middle+final,headers,left+rows+right)
        main.xpath('.//table')[0].tail='原始记录说明保持不变。'
        _compact_series_tables(main,first+middle+final)
        self.assertEqual(main.text_content().count('原始记录说明保持不变。'),1)
        self.assertTrue(main.text_content().endswith('原始记录说明保持不变。'))
