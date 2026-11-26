"""Complete category names must stay visible in narrow Word and static figures."""
from copy import deepcopy
from datetime import date, timedelta
from io import BytesIO
import unittest
from unittest.mock import Mock
from zipfile import ZipFile
from lxml import etree
from pypdf import PdfReader
from tests import ROOT
from researchhelper.publication_graphics import EvidenceChart,FONT,chart_pdf
from researchhelper.visual_catalog import theme
from reportlab.lib.colors import toColor
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

    def test_zero_endpoint_is_at_the_correct_edge_in_single_sign_pairs(self):
        for pairs, period, side in (([[0, .25]], 0, 'left'), ([[-.25, 0]], 1, 'right')):
            with self.subTest(side=side):
                spec = dict(scheme='dumbbell',data=dict(labels=['负债率'],pairs=pairs,periods=['前期','本期'],unit='%'))
                chart, canvas = self.draw(spec,width=220)
                zero = canvas.circle.call_args_list[period].args[0]
                self.assertEqual(zero, chart.categories['left'] if side == 'left' else chart.width-24)
                self.assert_inside(chart,canvas)

    def test_slope_preserves_periods_series_names_and_endpoint_geometry(self):
        labels = ['经营活动产生的现金流量净额同比原始观测值', '归属母公司股东的净利润同比原始观测值']
        spec = self.specimen(labels)
        spec.update(scheme='slope')
        spec['data'].update(pairs=[[0, .9], [-.7, -1.7]], compactDates=True)
        before = deepcopy(spec)
        chart, canvas = self.draw(spec, width=220)
        names = [call.args[2] for call in canvas.drawString.call_args_list]
        self.assertEqual(''.join(names), ''.join(labels))
        dates = [call.args[2] for call in canvas.drawCentredString.call_args_list]
        self.assertEqual(dates, spec['data']['periods'])
        first, second, third, fourth = [call.args[:2] for call in canvas.circle.call_args_list]
        self.assertEqual((first[0], second[0]), (third[0], fourth[0]))
        self.assertLess(first[0], second[0])
        self.assertGreater(second[1], first[1])
        self.assertLess(fourth[1], third[1])
        self.assertTrue(all((*start, *end) in [call.args for call in canvas.line.call_args_list]
                            for start, end in ((first, second), (third, fourth))))
        self.assert_inside(chart, canvas)
        self.assertEqual(spec, before)

    def test_grouped_bar_labels_stay_inside_narrow_panel_with_both_signs(self):
        spec = dict(scheme='grouped_bar', data=dict(
            labels=['前值', '后值'], unit='未经单位换算的接口原值', numberDigits=7,
            series=[dict(name='第一期', values=[-99.8765432, 100.1234567]),
                    dict(name='第二期', values=[0, None])]))
        before = deepcopy(spec)
        chart, canvas = self.draw(spec, width=220)
        numbers = [call.args[2] for call in canvas.drawString.call_args_list]
        self.assertTrue({'-99.8765432', '100.1234567', '0'} <= set(numbers))
        self.assertIn(spec['data']['unit'], [call.args[2] for call in canvas.drawRightString.call_args_list])
        self.assertEqual(canvas.rect.call_count, 3)
        self.assert_inside(chart, canvas)
        self.assertEqual(spec, before)

    def test_numeric_axis_keeps_distinct_positions_with_coarse_value_labels(self):
        spec = dict(scheme='line', data=dict(labels=['期初', '期末'], values=[20.21, 20.34], numberDigits=0))
        chart, canvas = self.draw(spec)
        ticks = [call.args[2] for call in canvas.drawRightString.call_args_list]
        self.assertEqual(ticks, ['20.2', '20.25', '20.3', '20.35'])
        self.assert_inside(chart, canvas)

    def test_heatmap_uses_a_zero_centered_scale_and_distinguishes_missing_cells(self):
        dates = ['2026-01-31', '2026-02-28', '2026-03-31', '2026-04-30']
        spec = dict(scheme='heatmap', data=dict(labels=dates,
            rows=['CPI:食品:当月同比', 'CPI:消费品:当月同比'],
            matrix=[[-4, None, 0, 2], [4, .2, -2, .9]], unit='%'))
        before = deepcopy(spec)
        chart, canvas = self.draw(spec, width=225)
        cells = []
        for call in canvas.method_calls:
            if call[0] == 'setFillColor':
                fill = toColor(call.args[0])
            if call[0] == 'rect':
                cells.append((fill.rgb(), call.kwargs['stroke']))
        palette = theme('research')
        negative, paper, positive = (toColor(palette[key]).rgb() for key in ('negative', 'paper', 'positive'))
        self.assertEqual(cells[0], (negative, 0))
        self.assertEqual(cells[1:3], [(paper, 1), (paper, 0)])
        self.assertEqual(cells[4], (positive, 0))
        texts = [call.args[2] for call in canvas.drawCentredString.call_args_list]
        self.assertTrue({dates[0], dates[-1], '-', '0', '-4', '4'} <= set(texts))
        self.assert_inside(chart, canvas)
        self.assertEqual(spec, before)

    def test_scatter_preserves_axis_names_and_both_periods_for_coincident_points(self):
        periods = ['20241231', '20251231']
        labels = ['2024-12-31', '2025-12-31']
        data = dict(points=[dict(name=period, x=12e9, y=38e9) for period in periods],
            comparison_periods=periods, dateLabels=dict(zip(periods, labels)), unit='人民币元',
            xName='净利润', yName='经营现金流')
        spec = dict(scheme='scatter', data=data)
        before = deepcopy(spec)
        chart, canvas = self.draw(spec, width=225)
        text = [call.args[2] for call in canvas.drawString.call_args_list]
        self.assertTrue({*labels, '经营现金流', '9'} <= set(text))
        self.assertIn('净利润', [call.args[2] for call in canvas.drawCentredString.call_args_list])
        first, second = [call.args[:3] for call in canvas.circle.call_args_list[:2]]
        self.assertEqual(first[:2], second[:2])
        self.assertGreater(first[2], second[2])
        self.assert_inside(chart, canvas)
        self.assertEqual(spec, before)

    def test_multi_line_legend_wraps_complete_names_and_date_ticks_do_not_collide(self):
        names = ['按统一观测口径记录的全部消费价格指数当月同比',
                 '按统一观测口径记录的全部消费价格指数食品分项当月同比']
        labels = [f'2026-01-{day:02}' for day in range(1,10)]
        data = dict(labels=labels, series=[dict(name=name, values=list(range(9))) for name in names])
        chart, canvas = self.draw(dict(scheme='multi_line', data=data), width=220)
        columns = {}
        for call in canvas.drawString.call_args_list:
            x, y, label = call.args
            columns.setdefault(x, []).append(label)
        self.assertEqual([''.join(parts) for parts in columns.values()], names)
        ticks = [call.args for call in canvas.drawCentredString.call_args_list]
        self.assertEqual([ticks[0][2], ticks[-1][2]], [labels[0], labels[-1]])
        for (x1, _, a), (x2, _, b) in zip(ticks,ticks[1:]):
            self.assertGreaterEqual(x2-x1, (pdfmetrics.stringWidth(a,FONT,8.25)
                                           + pdfmetrics.stringWidth(b,FONT,8.25))/2+3)
        self.assert_inside(chart, canvas)

    def test_candle_units_cover_all_four_values_and_date_ticks_keep_endpoints(self):
        raw = [f'202601{day:02}' for day in range(1,10)]
        dates = [f'2026-01-{day:02}' for day in range(1,10)]
        data = dict(labels=raw, dateLabels=dict(zip(raw, dates)), unit='元',
                    values=[[12e6, 14e6, 10e6, 15e6]]*len(dates))
        chart, canvas = self.draw(dict(scheme='candlestick',data=data), width=225)
        self.assertEqual(canvas.rect.call_count, len(dates))
        self.assertIn('元×10', [call.args[2] for call in canvas.drawRightString.call_args_list])
        self.assertIn('6', [call.args[2] for call in canvas.drawString.call_args_list])
        ticks = [call.args[2] for call in canvas.drawCentredString.call_args_list]
        self.assertEqual([ticks[0], ticks[-1]], [dates[0], dates[-1]])
        self.assert_inside(chart, canvas)

    def test_dense_dated_lollipop_draws_every_real_point_in_order_and_fits_one_panel(self):
        labels = [(date(2025, 12, 1)+timedelta(days=i)).isoformat() for i in range(64)]
        values = [i-23 for i in range(64)]
        values[22] = None
        spec = dict(scheme='lollipop', data=dict(labels=labels, values=values, unit='元', compactDates=True))
        before = deepcopy(spec)
        chart, canvas = self.draw(spec, width=240)
        self.assertEqual(chart.height, 115)
        points = [call.args for call in canvas.circle.call_args_list]
        self.assertEqual(len(points), 63)
        self.assertTrue(all(a[0] < b[0] for a,b in zip(points,points[1:])))
        self.assertGreater(points[22][0]-points[21][0], points[21][0]-points[20][0])
        self.assertTrue(all(a[1] < b[1] for a,b in zip(points,points[1:])))
        dates = [call.args[2] for call in canvas.drawCentredString.call_args_list]
        self.assertEqual([dates[0],dates[-1]], [labels[0],labels[-1]])
        self.assert_inside(chart, canvas)
        self.assertEqual(spec, before)

    def test_long_categorical_pages_preserve_every_label_and_use_the_same_numeric_axis(self):
        labels = [f'资产类别{i:02}' for i in range(55)]
        values = [5]*len(labels)
        values[-1] = -140
        spec = dict(scheme='lollipop', data=dict(labels=labels, values=values, unit='元'))
        chart = EvidenceChart(spec,width=240,height=115)
        pages = list(chart.pages(400))
        seen, repeated_x = [], []
        for page in pages:
            canvas=Mock()
            page.canv=canvas
            page.draw()
            seen.extend(call.args[2] for call in canvas.drawRightString.call_args_list if call.args[2] in labels)
            repeated_x.append(canvas.circle.call_args_list[0].args[0])
            self.assertLessEqual(page.height,400)
            self.assert_inside(page,canvas)
        self.assertGreater(len(pages),1)
        self.assertEqual(seen,labels)
        self.assertEqual(len(set(repeated_x)),1)

    def test_oversized_chart_group_exports_real_paginated_pdf_and_bounded_word_images(self):
        from hashlib import sha256
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from researchhelper.artifact_exporter import _docx,convert_saved_html

        labels = [f'资产类别{i:02}' for i in range(61)]
        spec = dict(target='portfolio-chart',scheme='lollipop',data=dict(labels=labels, values=list(range(61)), unit='元'))
        group = dict(figure_group='portfolio', figure_index=0, figure_columns=1)
        document = dict(schema='researchhelper.presentation-document',title='分类比较',theme='research',blocks=[
            dict(kind='caption',text='分类比较',**group),dict(kind='chart',spec=spec,**group),
            dict(kind='paragraph',text='后续结论仍完整')])
        before = deepcopy(document)
        with TemporaryDirectory() as folder:
            source = Path(folder)/'report.html'
            raw = ('<!doctype html><main></main><script id="rh-publication-document">'+
                   json.dumps(document)+'</script>').encode()
            source.write_bytes(raw)
            output,converted = convert_saved_html(source,'pdf',expected_sha256=sha256(raw).hexdigest())
            self.assertEqual(converted['conversion_engine'],'reportlab')
            self.assertEqual(source.read_bytes(),raw)
        pdf = PdfReader(BytesIO(output))
        full = '\n'.join(page.extract_text() for page in pdf.pages)
        self.assertGreaterEqual(len(pdf.pages),3)
        self.assertTrue(all(full.count(label)==1 for label in labels))
        self.assertIn('后续结论仍完整',full)
        with ZipFile(BytesIO(_docx(document))) as word:
            tree = etree.fromstring(word.read('word/document.xml'))
        extents = tree.xpath('//*[local-name()="inline"]/*[local-name()="extent"]')
        self.assertGreaterEqual(len(extents),3)
        self.assertTrue(all(int(extent.get('cy'))/12700 < 735 for extent in extents))
        self.assertIn('后续结论仍完整', ''.join(tree.itertext()))
        self.assertEqual(document,before)

    def test_pdf_keeps_compact_period_value_pairs_together_in_both_column_widths(self):
        from researchhelper.artifact_exporter import _pdf

        pairs = [(f'2026-01-{i:02}', f'{i}.125') for i in range(1,13)]
        rows = [['期间','原值']*4] + [[cell for pair in pairs[i:i+4] for cell in pair] for i in range(0,12,4)]
        table = dict(kind='table',rows=rows)
        for two_columns in (False, True):
            with self.subTest(two_columns=two_columns):
                blocks = [table]
                if two_columns:
                    blocks = [dict(table,figure_group='series',figure_index=0,figure_columns=2),
                              dict(kind='paragraph',text='同版附注',figure_group='series',figure_index=1,figure_columns=2)]
                pdf = PdfReader(BytesIO(_pdf(dict(title='观测原值',theme='research',blocks=blocks))))
                words = '\n'.join(page.extract_text() for page in pdf.pages).split()
                self.assertEqual([(a,b) for a,b in zip(words,words[1:]) if a.startswith('2026-01-')],pairs)

if __name__=='__main__':unittest.main()
