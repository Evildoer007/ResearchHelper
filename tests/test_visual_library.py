"""Versioned visual choices never mutate research or fabricate chart inputs."""
import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from tests import ROOT
from researchhelper.visual_catalog import catalogue,browser_catalogue,theme
from researchhelper.draft_visuals import catalogue_figures,ohlc_figures
from researchhelper.drafts import render,draft_figures
from core.session import Session
from researchhelper.contracts import ContractError,canonical

MACRO=ROOT/'result/acceptance/one-sentence-research-lfu94vrd/report.json'
HK=ROOT/'result/acceptance/one-sentence-research-_kw2k9ap/report.json'

class VisualLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.macro=json.loads(MACRO.read_text());cls.hk=json.loads(HK.read_text())
        cls.hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (MACRO,HK)}

    @classmethod
    def tearDownClass(cls):
        assert all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in cls.hashes.items())

    def test_single_catalogue_matches_browser_derived_file(self):
        self.assertEqual(browser_catalogue(),(ROOT/'products/app/frontend/visual-catalog-data.js').read_text())
        c=catalogue();self.assertEqual((len(c['schemes']),len(c['themes'])),(40,10))
        self.assertEqual(len({r['id'] for r in c['schemes']}),40)
        self.assertTrue(all(r['requires'] and r['purpose'] and r['renderer']=='echarts_svg' for r in c['schemes']))

    def test_catalogue_is_defensive_and_theme_unknown_is_not_substituted(self):
        c=catalogue();c['schemes'].clear();self.assertEqual(len(catalogue()['schemes']),40)
        self.assertIsNone(theme('unknown'));p=theme('research');p['colors'].clear()
        self.assertGreater(len(theme('research')['colors']),0)

    def test_macro_comparison_and_heatmap_preserve_all_actual_cells(self):
        content=copy.deepcopy(self.macro);before=copy.deepcopy(content)
        figures=catalogue_figures(content,draft_figures(content['draft']['facts']))
        self.assertEqual([r['scheme'] for r in figures],['multi_line','heatmap'])
        self.assertEqual(len(figures[0]['data']['series']),3)
        self.assertEqual(sum(len(r['values']) for r in figures[0]['data']['series']),27)
        self.assertEqual(figures[1]['data']['matrix'],[r['values'] for r in figures[0]['data']['series']])
        self.assertEqual(set(figures[1]['data_refs']),{r['id'] for r in content['draft']['facts']})
        self.assertEqual(content,before)

    def test_partial_or_conflicting_macro_does_not_fabricate_matrix(self):
        for change in ('missing','unit','foreign'):
            content=copy.deepcopy(self.macro)
            if change=='missing':content['draft']['facts'].pop()
            elif change=='unit':content['draft']['facts'][0]['unit']='万元'
            else:content['draft']['facts'][0]['artifact_id']='foreign'
            original=[dict(name='保留原图',data_refs=[])]
            self.assertEqual(catalogue_figures(content,original),original)

    def candles(self):
        content=copy.deepcopy(self.hk);facts=[]
        for i,row in enumerate(content['draft']['facts']):
            for field,delta in [('open',-.5),('close',0),('low',-1),('high',1)]:
                facts.append(dict(row,id=f'C{i}-{field}',field=field,value=row['value']+delta))
        content['draft']['facts']=facts
        return content

    def test_complete_native_ohlc_uses_candles_and_every_owned_ref(self):
        content=self.candles();before=copy.deepcopy(content);figures=ohlc_figures(content,[])
        self.assertEqual(figures[0]['scheme'],'candlestick');self.assertEqual(len(figures[0]['data']['values']),5)
        self.assertEqual(set(figures[0]['data_refs']),{r['id'] for r in content['draft']['facts']})
        self.assertEqual(content,before)

    def test_incomplete_or_duplicate_ohlc_preserves_raw_fallback(self):
        for change in ('missing','duplicate','bounds','nan','date','foreign','basis'):
            content=self.candles();rows=content['draft']['facts']
            if change=='missing':rows.pop()
            elif change=='duplicate':rows.append(copy.deepcopy(rows[0]))
            elif change=='bounds':rows[2]['value']=999
            elif change=='nan':rows[2]['value']=float('nan')
            elif change=='date':rows[0]['period']='invalid'
            elif change=='foreign':rows[0]['artifact_id']='foreign'
            else:rows[0]['series_basis']='other'
            fallback=[dict(name='原始价格')]
            self.assertEqual(ohlc_figures(content,fallback),fallback,change)

    def test_actual_close_only_report_never_becomes_ohlc(self):
        original=draft_figures(self.hk['draft']['facts'])
        self.assertEqual(ohlc_figures(self.hk,original),original)

    def test_profile_only_changes_presentation_and_content_stays_frozen(self):
        from lxml import html
        content=copy.deepcopy(self.macro);before=canonical(content)
        original=html.document_fromstring(render(content,ROOT))
        styled=render(content,ROOT,visual_profile={'theme':'porcelain'})
        tree=html.document_fromstring(styled)
        self.assertEqual(json.loads(tree.get_element_by_id('rh-visual-profile').text),{'theme':'porcelain'})
        # A theme changes presentation only. Per-logic figures need not use
        # the independent catalogue's global macro heatmap.
        self.assertEqual(tree.xpath('//main')[0].text_content(),original.xpath('//main')[0].text_content())
        self.assertEqual(tree.xpath('//*[@data-raw-value]/text()'),original.xpath('//*[@data-raw-value]/text()'))
        self.assertEqual(len(tree.xpath('//*[@data-raw-value]')),27)
        self.assertEqual(canonical(content),before)

    def test_skill_style_export_keeps_archived_body_and_default_export(self):
        from core.session import Session
        session=object.__new__(Session);session.archive=Mock();session.archive.get.return_value={'version':1}
        original=render(self.macro,ROOT);session.agent=SimpleNamespace(resources=ROOT,export=Mock(return_value={'html':original,'content':self.macro}))
        with tempfile.TemporaryDirectory() as folder:
            session.project=Path(folder)
            default=session.export('task');before=default.read_bytes()
            styled=session.export('task',visual_theme='midnight')
            self.assertEqual(default.read_bytes(),before);self.assertNotEqual(styled.read_bytes(),before)
            self.assertEqual(json.loads(styled.with_suffix('.json').read_text()),self.macro)
            self.assertEqual(styled.name,'research-v1-midnight.html')
            with self.assertRaises(ContractError):session.export('task',visual_theme='unregistered')
        self.assertEqual(len(session.visuals()['schemes']),40)
        self.assertTrue(all(row['family']=='趋势' for row in session.visuals('趋势')['schemes']))
