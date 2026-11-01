"""Compact macro presentation preserves all actual cells and source references."""
import copy,hashlib,json,unittest
from html.parser import HTMLParser
from tests import ROOT
from researchhelper.draft_visuals import temporal_table,display_observation
from researchhelper.drafts import render

SOURCE=ROOT/'result/acceptance/one-sentence-research-lfu94vrd/report.json'


class MacroDraftLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest=hashlib.sha256(SOURCE.read_bytes()).hexdigest();cls.content=json.loads(SOURCE.read_text())

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==cls.digest

    def test_actual_twenty_seven_cells_are_a_nine_by_three_table(self):
        matrix=temporal_table(self.content)
        self.assertEqual((len(matrix['rows']),len(matrix['columns']),matrix['observations']),(9,3,27))
        ids=[row['id'] for period in matrix['rows'] for row in period['observations']]
        self.assertEqual(set(ids),{row['id'] for row in self.content['draft']['facts']})
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(matrix['rows'][0]['period'],'2026-01-31')

    def test_missing_date_never_gets_filled_or_drops_other_series(self):
        content=copy.deepcopy(self.content);content['draft']['facts'].pop()
        self.assertIsNone(temporal_table(content))

    def test_duplicate_dates_preserve_original_table_fallback(self):
        content=copy.deepcopy(self.content);content['draft']['facts'].append(dict(content['draft']['facts'][0],id='duplicate'))
        self.assertIsNone(temporal_table(content))

    def test_unit_or_ownership_conflicts_do_not_become_aligned_columns(self):
        for key,value in [('unit','万元'),('artifact_id','another-task-source')]:
            content=copy.deepcopy(self.content);content['draft']['facts'][0][key]=value
            self.assertIsNone(temporal_table(content))

    def test_display_rounding_preserves_raw_noise_and_nonzero_small_values(self):
        row=dict(value=1.0999999999999999,raw_value='1.0999999999999999',unit='%')
        self.assertEqual(display_observation(row),'1.10');self.assertEqual(row['raw_value'],'1.0999999999999999')
        self.assertNotEqual(float(display_observation(dict(value=1e-12,raw_value='1e-12',unit='%'))),0)
        self.assertEqual(display_observation(dict(value=1.0999999999999999,raw_value=row['raw_value'],unit='原始单位待核对')),row['raw_value'])
        self.assertEqual(display_observation(dict(value=None,raw_value=None,unit='%')),'未返回')

    def test_actual_render_keeps_all_cells_and_removes_provider_ids_from_chart_titles(self):
        html=render(self.content,ROOT)
        self.assertEqual(html.count('data-raw-value='),27)
        self.assertIn('temporal-comparison-table',html)
        self.assertNotIn('<figcaption>M002',html)
        self.assertIn('1.10</td>',html)
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(),self.digest)


    def test_unknown_units_do_not_imply_common_macro_scale(self):
        content=copy.deepcopy(self.content)
        for row in content['draft']['facts']:row['unit']='原始单位待核对'
        self.assertIsNone(temporal_table(content))
