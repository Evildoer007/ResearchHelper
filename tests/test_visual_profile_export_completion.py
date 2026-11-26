"""Independent offline visual profile and saved conversion contracts."""
import hashlib
from io import BytesIO
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import ROOT
from tests.test_client_report_audience import fixture
from researchhelper.contracts import canonical, ContractError
from researchhelper.report_designer import design, style_document, compatible_schemes, styled_spec, visual_profile_for
from researchhelper.visual_catalog import CATALOGUE_VERSION
from researchhelper.artifact_exporter import convert_saved_html, export_report

class VisualProfileCompletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden'))
        cls.guard.start()
        cls.task,cls.content=fixture()
        cls.publication=design(cls.content,ROOT)
    @classmethod
    def tearDownClass(cls): cls.guard.stop()
    def test_every_compatible_choice_preserves_canonical_data_and_refs(self):
        document=self.publication['document'];before=canonical(document)
        for target,spec in document['visual_sources'].items():
            for scheme in compatible_schemes(spec):
                with self.subTest(target=target,scheme=scheme):
                    result=style_document(document,{'theme':'research','schemes':{target:scheme}})
                    changed=next(b['spec'] for b in result['blocks'] if b['kind']=='chart' and b['spec']['target']==target)
                    self.assertEqual(changed['auto_visual']['scheme'],scheme)
                    for key in ('data_refs','refs','calculation_refs'):
                        self.assertEqual(changed.get(key),spec.get(key))
                    self.assertEqual(result['content_hash'],document['content_hash'])
                    self.assertEqual(result['dataset'],document['dataset'])
                    self.assertEqual(style_document(result,{})['blocks'],document['blocks'])
        self.assertEqual(canonical(document),before)
    def test_invalid_target_and_scheme_rejected(self):
        document=self.publication['document'];target=next(iter(document['visual_sources']))
        for schemes in ({'foreign':'line'},{target:'not-a-chart'}):
            with self.assertRaises(ContractError):style_document(document,{'schemes':schemes})
    def test_saved_styled_html_converts_pdf_docx_without_data_mutation(self):
        document=self.publication['document'];before=canonical(self.content)
        schemes={target:compatible_schemes(spec)[-1] for target,spec in document['visual_sources'].items()}
        profile={'theme':'research','schemes':schemes}
        with tempfile.TemporaryDirectory() as folder:
            receipt=export_report(self.content,ROOT,folder,visual_profile=profile)
            self.assertEqual(receipt['visual_profile'],profile)
            for format,prefix in [('pdf',b'%PDF-'),('docx',b'PK')]:
                raw,converted=convert_saved_html(receipt['source_html'],format,expected_sha256=receipt['source_html_sha256'])
                self.assertTrue(raw.startswith(prefix));self.assertEqual(converted['visual_profile'],profile)
                self.assertEqual(converted['dataset'],document['dataset'])
                self.assertEqual(converted['content_hash'],document['content_hash'])
        self.assertEqual(canonical(self.content),before)
    def test_grouped_bar_conversion_keeps_every_value(self):
        pairs={'auto_visual':{'scheme':'dumbbell','data':{'labels':['a','b'],'periods':['before','after'],'pairs':[[0,2],[-3,None]]}}}
        matrix={'auto_visual':{'scheme':'heatmap','data':{'labels':['2025-01','2025-02'],'rows':['a','b'],'matrix':[[0,None],[-3,2]]}}}
        self.assertEqual([s['values'] for s in styled_spec(pairs,'grouped_bar')['auto_visual']['data']['series']],[[0,-3],[2,None]])
        self.assertEqual([s['values'] for s in styled_spec(matrix,'grouped_bar')['auto_visual']['data']['series']],[[0,None],[-3,2]])

    def test_trend_switch_requires_an_ordered_period_axis(self):
        series = [dict(name='前期', values=[100, 20]), dict(name='本期', values=[98, None])]
        cases = [
            (['营收', '利润'], False),
            (['2025-01', '2025-02'], True),
            (['2024', '2025'], True),
            (['2024-02-29', '2024-03-01'], True),
            (['2025-02-30', '2025-03-01'], False),
            (['2025-02', '2025-01'], False),
            (['2025-01', '2025-01'], False),
            (['2024', '2025-01'], False),
        ]
        for labels, chronological in cases:
            with self.subTest(labels=labels):
                spec = dict(target='comparison', data_refs=['D1', 'D2', 'D3'],
                            auto_visual=dict(scheme='grouped_bar', data=dict(labels=labels, series=series)))
                choices = compatible_schemes(spec)
                self.assertIn('heatmap', choices)
                self.assertEqual('multi_line' in choices, chronological)
                matrix = styled_spec(spec, 'heatmap')
                self.assertEqual('multi_line' in compatible_schemes(matrix), chronological)
                self.assertEqual(matrix['data_refs'], spec['data_refs'])
                self.assertEqual(matrix['auto_visual']['data']['matrix'], [[100, 20], [98, None]])
                if not chronological:
                    with self.assertRaises(ContractError):
                        visual_profile_for({'schemes': {'comparison': 'multi_line'}}, {'comparison': spec})

    def test_saved_legacy_chart_uses_its_archived_choices(self):
        target = 'financial-inputs'
        spec = dict(target=target, auto_visual=dict(scheme='grouped_bar', data=dict(
            labels=['营收', '利润'], series=[dict(name='前期', values=[100, 21]),
                                           dict(name='本期', values=[105, 20])])))
        selection = dict(theme='research', schemes={target: 'multi_line'})
        document = dict(schema='researchhelper.presentation-document', version=1,
                        blocks=[dict(kind='chart', spec=spec)], visual_sources={target: spec})
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'report.html'
            for version in (None, CATALOGUE_VERSION):
                with self.subTest(catalogue_version=version):
                    if version is not None:
                        document['visual_catalogue_version'] = version
                    raw = ('<!doctype html><html><body><script id="rh-publication-document" type="application/json">'
                           + json.dumps(document) + '</script><script id="rh-visual-profile" type="application/json">'
                           + json.dumps(selection) + '</script></body></html>').encode('utf-8')
                    source.write_bytes(raw)
                    digest = hashlib.sha256(raw).hexdigest()
                    if version is None:
                        returned, resolved = convert_saved_html(source, 'html', expected_sha256=digest)
                        self.assertEqual(returned, raw)
                        self.assertEqual(resolved['blocks'][0]['spec']['auto_visual']['scheme'], 'multi_line')
                    else:
                        with self.assertRaises(ContractError):
                            convert_saved_html(source, 'html', expected_sha256=digest)

    def test_more_readings_share_the_frozen_data_and_render_offline(self):
        from pypdf import PdfReader
        from researchhelper.publication_graphics import chart_pdf

        raw = (ROOT/'products/app/frontend/visual-gallery-data.js').read_text()
        examples = {row['scheme']: row['data'] for row in json.loads(
            raw.split('window.RH_VISUAL_EXAMPLES=',1)[1].strip().removesuffix(';'))}
        for original, choice, expected in (('sample_range','ecdf','累计占比'),
                                           ('band','interval_dot','95%置信区间'),
                                           ('scenario_matrix','scenario_profile','收入增速'),
                                           ('scatter','scatter_rug','总项同比')):
            with self.subTest(choice=choice):
                data = examples[choice]
                spec = dict(target='selected', data_refs=['D1','D2'], auto_visual=dict(scheme=original,data=data))
                before = canonical(spec)
                self.assertEqual(compatible_schemes(spec), [original, choice])
                new = styled_spec(spec, choice)
                self.assertEqual(new['auto_visual']['data'], data)
                self.assertEqual(new['data_refs'], spec['data_refs'])
                for width in (252,510):
                    paper = PdfReader(BytesIO(chart_pdf(new,width=width,palette='red_blue')))
                    self.assertIn(expected, paper.pages[0].extract_text())
                self.assertEqual(canonical(spec), before)

    def test_new_options_require_their_actual_input_shape(self):
        cases = [
            ('beeswarm', dict(samples=[.2,.2,.3,.3,.3])),
            ('scatter', dict(points=[dict(x=1, y=index) for index in range(6)])),
            ('scatter', dict(points=[dict(x=index, y=index) for index in range(5)])),
            ('band', dict(labels=['甲','乙'],lower=[1,2],upper=[3,4])),
            ('scenario_matrix', dict(labels=['低','中','高'],rows=['结果'],matrix=[[1,2,3]],xValues=[0,3,3],xName='增速')),
        ]
        for original, data in cases:
            with self.subTest(original=original):
                spec = dict(target='present',auto_visual=dict(scheme=original,data=data))
                self.assertEqual(compatible_schemes(spec), [original])
