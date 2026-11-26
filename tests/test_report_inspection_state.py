"""Shared offline report inspector contract; records here are synthetic controls."""
import json
import os
from pathlib import Path
import unittest

from lxml import html
from researchhelper.layout import resource_directory
from researchhelper.visual import INSPECTION_SCRIPT, inspection_surface

RESOURCES=Path(os.environ.get('RESEARCHHELPER_TEST_RESOURCES',Path(__file__).resolve().parents[1]))


def fixture_html(resources=RESOURCES):
    frontend=resource_directory(resources,'frontend')
    assets={name:(frontend/name).read_text() for name in ('report.css','draft-figures.js',
        'visual-catalog-data.js','visual-library.js')}
    assets['echarts.min.js']=(resource_directory(resources,'vendor')/'echarts.min.js').read_text()
    records=[
        '<section id="record-a" data-inspection-title="观察依据"><h3>观察依据</h3>'
        '<table><tbody><tr data-observation-id="D1"><td>合成观察一</td><td>1</td></tr>'
        '<tr data-observation-id="D2"><td>合成观察二</td><td>2</td></tr></tbody></table>'
        '<a href="#record-b">查看计算依据</a></section>',
        '<section id="record-b" data-inspection-title="计算依据"><h3>计算依据</h3>'
        '<p>合成导航检查，不是投资研究。</p><a href="#record-c">查看原文</a></section>',
        '<section id="record-c" data-inspection-title="原文"><h3>原文</h3>'
        '<p>此处仅检查离线报告的依据导航。</p><a href="#record-a">返回观察依据</a></section>',
    ]
    spec=dict(target='inspection-chart',name='合成观察',type='bar',dates=['观察一','观察二'],
        values=[1,2],unit='点',data_refs=['D1','D2'],point_refs=[['D1'],['D2']],inspection_target='record-a')
    chart=assets['draft-figures.js'].replace('__RH_DRAFT_FIGURES__',json.dumps([spec],ensure_ascii=False))
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>合成报告导航检查</title><style>'+assets['report.css']+
        'main{max-width:720px;margin:auto;padding:20px}.chart{width:100%;height:240px}</style></head><body>'
        '<main><h1>合成报告导航检查</h1><p>仅检查界面状态，不是实际投资报告。</p>'
        '<div id="inspection-chart" class="chart" tabindex="0" role="img" aria-label="合成观察图"></div>'
        '<a id="opener-a" href="#record-a">查看完整观察依据</a>'
        '<a id="opener-b" href="#record-b">查看计算依据</a></main>'
        +inspection_surface(records)+'<script>'+INSPECTION_SCRIPT+'</script>'
        '<script>'+assets['echarts.min.js']+'</script><script>'+assets['visual-catalog-data.js']+
        '</script><script>'+assets['visual-library.js']+'</script><script>'+chart+'</script></body></html>')


class ReportInspectionStateTests(unittest.TestCase):
    def test_one_dialog_keeps_complete_records_unique_and_outside_client_paper(self):
        document=html.document_fromstring(fixture_html())
        self.assertEqual(len(document.xpath('//dialog')),1)
        ids=document.xpath('//*[@id]/@id');self.assertEqual(len(ids),len(set(ids)))
        records=document.get_element_by_id('report-records')
        self.assertIn('hidden',records.attrib)
        self.assertEqual([child.get('id') for child in records],['record-a','record-b','record-c'])
        self.assertFalse(document.xpath('//main//*[@data-observation-id]'))
        self.assertEqual(document.get_element_by_id('report-inspector').get('aria-labelledby'),'inspection-title')

    def test_no_script_fallback_and_offline_real_chart_assets_remain_present(self):
        text=fixture_html();document=html.document_fromstring(text)
        self.assertIn('#report-records[hidden]{display:block!important}',text)
        self.assertEqual(len(document.xpath('//noscript')),1)
        self.assertFalse(document.xpath('//script[@src]|//link[@href]'))
        self.assertNotIn('__RH_DRAFT_FIGURES__',text)
        self.assertIn('researchhelper:inspect',text)
        self.assertIn('data-chart-selected',text)


if __name__=='__main__':unittest.main()
