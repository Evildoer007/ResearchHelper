"""Complete category names must stay visible in narrow Word and static figures."""
from copy import deepcopy
from io import BytesIO
import unittest
from unittest.mock import Mock
from pypdf import PdfReader
from tests import ROOT
from researchhelper.publication_graphics import EvidenceChart,FONT,chart_pdf
from reportlab.pdfbase import pdfmetrics

class StaticCategoryGeometry(unittest.TestCase):
    def specimen(self,labels=None):
        return dict(scheme='dumbbell',data=dict(labels=labels or ['CPI:当月同比','CPI:食品:当月同比','CPI:消费品:当月同比'],
            pairs=[[.2,.9],[-.7,-1.7],[.3,1.0]],periods=['2026-01-31','2026-09-30'],unit='%'))
    def draw(self,spec,width=241,height=115):
        canvas=Mock();chart=EvidenceChart(spec,width=width,height=height);chart.canv=canvas;chart.draw()
        return chart,canvas
    def assert_inside(self,chart,canvas):
        for method,alignment in [('drawString','left'),('drawRightString','right'),('drawCentredString','center')]:
            for call in getattr(canvas,method).call_args_list:
                x,y,text=call.args;length=pdfmetrics.stringWidth(text,FONT,8.25)
                left=x if alignment=='left' else x-length if alignment=='right' else x-length/2
                right=left+length
                self.assertGreaterEqual(left,3,(text,left));self.assertLessEqual(right,chart.width-3,(text,right))
                self.assertGreaterEqual(y,3,(text,y));self.assertLessEqual(y+8.25,chart.height-2,(text,y))
    def test_real_cpi_names_and_both_dates_fit_the_word_column(self):
        spec=self.specimen();before=deepcopy(spec);chart,canvas=self.draw(spec)
        names=[call.args[2] for call in canvas.drawRightString.call_args_list]
        for label in spec['data']['labels']:self.assertIn(label,names)
        dates=[call.args[2] for method in (canvas.drawString,canvas.drawRightString) for call in method.call_args_list]
        for date in spec['data']['periods']:self.assertIn(date,dates)
        self.assert_inside(chart,canvas);self.assertEqual(spec,before)
    def test_long_names_wrap_without_losing_characters_or_rows(self):
        labels=['中国居民消费价格指数食品分项当月同比原始观测值','消费品价格指数当月同比变化的观测口径','全部居民消费价格指数当月同比读数']
        spec=self.specimen(labels);chart,canvas=self.draw(spec,width=220,height=96)
        drawn=[call.args[2] for call in canvas.drawRightString.call_args_list if call.args[2] not in spec['data']['periods']+['%']]
        self.assertEqual(''.join(drawn),''.join(labels));self.assertGreater(chart.height,96)
        self.assert_inside(chart,canvas)
        baselines=[call.args[1] for call in canvas.drawRightString.call_args_list if call.args[2] in drawn]
        self.assertTrue(all(abs(a-b)>=9.9 for a,b in zip(baselines,baselines[1:])))
    def test_lollipop_uses_the_same_complete_category_geometry(self):
        label='经营活动产生的现金流量净额与归母净利润对照口径'
        spec=dict(scheme='lollipop',data=dict(labels=[label],values=[-33.46],unit='%'))
        chart,canvas=self.draw(spec,width=241,height=96)
        self.assertEqual(''.join(call.args[2] for call in canvas.drawRightString.call_args_list if call.args[2]!='%'),label)
        self.assert_inside(chart,canvas)
    def test_small_static_pdf_retains_original_numbers_and_all_names(self):
        spec=self.specimen();before=deepcopy(spec);raw=chart_pdf(spec,width=241,height=115)
        text=PdfReader(BytesIO(raw)).pages[0].extract_text()
        for label in spec['data']['labels']:self.assertIn(label,text)
        for value in ['0.2%','0.9%','-0.7%','-1.7%','0.3%','1%']:self.assertIn(value,text)
        self.assertEqual(spec,before)

if __name__=='__main__':unittest.main()
