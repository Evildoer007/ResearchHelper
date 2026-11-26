"""Percentage-point semantics on retained actual macro observations, with no I/O."""
import copy,hashlib,json,socket,unittest
from unittest.mock import patch
from tests import ROOT
from researchhelper.draft_analysis import analyze,execute,formula_mathml
from researchhelper.draft_sources import observations
from researchhelper.draft_workflow import _write
from researchhelper.contracts import ContractError

SOURCE=ROOT/'result/acceptance/economic-discovery-live-20261023-v1/task.json'


class MacroDraftAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest=hashlib.sha256(SOURCE.read_bytes()).hexdigest();cls.original=json.loads(SOURCE.read_text())

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==cls.digest

    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No actual calls'))
        guard.start();self.addCleanup(guard.stop)
        self.task=copy.deepcopy(self.original);self.facts=observations(self.task)

    def test_actual_cpi_chronology_is_january_to_september(self):
        results=analyze(self.task,self.facts)['results']
        self.assertEqual(len(results),2)
        first=results[0]
        self.assertEqual((first['display'],first['unit']),('0.70','个百分点'))
        self.assertEqual(first['period'],'2026-01-31至2026-09-30')
        self.assertEqual(first['data_refs'],['D9','D1'])
        self.assertFalse(first['financial_approval']);self.assertFalse(first['conditional_equal_scale'])

    def test_latest_pair_is_point_one_points_not_relative_percent_or_cpi_month_on_month(self):
        latest=analyze(self.task,self.facts)['results'][1]
        self.assertEqual((latest['display'],latest['unit']),('0.10','个百分点'))
        self.assertEqual(latest['data_refs'],['D2','D1'])
        self.assertIn('最近两期读数',latest['label']);self.assertNotIn('环比',latest['label'])
        self.assertNotIn('涨幅',latest['label'])

    def test_missing_metadata_does_not_guess_percentage_unit(self):
        self.task['source_records']=[row for row in self.task['source_records'] if row['operation']!='search_edb']
        self.assertEqual(analyze(self.task,observations(self.task))['results'],[])

    def test_other_units_are_not_percentage_points(self):
        for row in self.facts:row['unit']='亿元'
        self.assertEqual(analyze(self.task,self.facts)['results'],[])

    def test_non_economic_field_named_value_is_not_adopted(self):
        for row in self.task['source_records']:
            if row['operation']=='edb_service':row['operation']='basic_data_service'
        self.assertEqual(analyze(self.task,self.facts)['results'],[])

    def test_missing_endpoints_do_not_shift_the_overall_window(self):
        self.facts[-1].update(value=None,raw_value=None)
        analysis=analyze(self.task,self.facts)
        self.assertEqual(len(analysis['results']),1)
        self.assertEqual(analysis['results'][0]['display'],'0.10')
        self.assertTrue(analysis['notes'])

    def test_conflicting_same_date_does_not_select_first_value(self):
        duplicate=dict(self.facts[0],id='conflicting',value=999,raw_value=999)
        self.assertEqual(analyze(self.task,self.facts+[duplicate])['results'],[])

    def test_two_periods_do_not_duplicate_the_same_calculation(self):
        result=analyze(self.task,[self.facts[-1],self.facts[0]])
        self.assertEqual(len(result['results']),1)

    def test_foreign_sources_do_not_enter_macro_arithmetic(self):
        for row in self.task['source_records']:row['task_id']='another-task'
        self.assertEqual(analyze(self.task,self.facts)['results'],[])

    def test_decimal_arithmetic_mathml_and_invalid_values(self):
        self.assertEqual(execute('raw_percentage_points_v1',dict(a='0.2',b='0.9'))['display'],'0.70')
        self.assertIn('<mo>−</mo>',formula_mathml('raw_percentage_points_v1'))
        for bad in (None,True,'NaN','Infinity'):
            with self.assertRaises(ContractError):execute('raw_percentage_points_v1',dict(a=bad,b=1))

    def test_writer_receives_actual_registered_point_changes(self):
        self.task['draft_analysis']=analyze(self.task,self.facts)
        class Captured:
            def reason(model,task,messages,**kwargs):
                model.payload=json.loads(messages[-1]['content']);return '{}'
        class Engine:pass
        engine=Engine();engine.model=Captured()
        _write(engine,self.task,self.facts)
        calculations=engine.model.payload['data']['calculations']
        self.assertEqual([row['display'] for row in calculations],['0.70','0.10'])
        self.assertEqual({row['unit'] for row in calculations},{'个百分点'})


# The new method catalogue intentionally invalidates the old cached envelope.
# Preserve the ordering assertions after a local, uncharged recomputation.
from tests import test_draft_observation_order as retained_order
from researchhelper.draft_analysis import current_analysis


class RegisteredMacroObservationOrderTests(retained_order.DraftObservationOrderTests):
    def test_provider_object_key_reordering_does_not_change_raw_references(self):
        task=copy.deepcopy(self.task)
        self.assertFalse(current_analysis(task,observations(task)))
        task['draft_analysis']=analyze(task,observations(task))
        retained_order.assert_calculation_meaning(self, task['draft_analysis']['results'], task['draft_report']['calculations'])
        expected = copy.deepcopy(task['draft_analysis']['results'])
        for record in task['source_records']:
            for table in record.get('snapshot',{}).get('tables',[]):
                if isinstance(table.get('table'),dict):table['table']=dict(reversed(list(table['table'].items())))
        self.assertEqual(observations(task),observations(self.task))
        self.assertTrue(current_analysis(task,observations(task)))
        self.assertEqual(current_analysis(task,observations(task))['results'], expected)
