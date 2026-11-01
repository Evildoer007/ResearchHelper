"""Method-driven reading repairs no facts and preserves prior reader versions."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from researchhelper.source_reader import read,validate_reading,financial_reader_terms,reader_version_for
from researchhelper.reasoning import evidence_contract
from researchhelper.financial_sources import financial_table_candidates
from researchhelper.contracts import canonical
from researchhelper.output_schema import financial_extraction_output_schema,financial_selector_prompt
from researchhelper.planning import question_specs

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-o0fz0x18/task.json'


class RegisteredFinancialReadingTests(unittest.TestCase):
    def setUp(self):
        self.digest=hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        self.task=json.loads(RETAINED.read_text())
        self.old=self.task['reasoning_state']['reading']['bundle']
        self.new=read(self.task,self.old['selection'],self.old['binding'],self.old['artifact_ids'])

    def tearDown(self):
        self.assertEqual(hashlib.sha256(RETAINED.read_bytes()).hexdigest(),self.digest)

    def test_registered_methods_locate_original_cells_when_all_model_queries_miss(self):
        self.assertTrue(all(item['hit_count']==0 for item in self.old['coverage'][0]['query_coverage']))
        contract=evidence_contract(self.task,self.new['views'])
        cells=contract['sources'][0]['financial_table_cells']['cells']
        self.assertTrue({'revenue','net_profit','operating_cash_flow'}.issubset({cell['metric'] for cell in cells}))
        self.assertEqual(self.new['reader_version'],3)
        self.assertLessEqual(self.new['coverage'][0]['read_characters'],80000)

    def test_registered_search_is_recorded_not_misreported_as_model_selection_or_approval(self):
        self.assertEqual(self.new['selection'],self.old['selection'])
        trace=self.new['coverage'][0]['method_queries']
        self.assertEqual(trace['terms'],financial_reader_terms(self.task))
        self.assertFalse(trace['financial_approval'])
        self.assertEqual(trace['shared_query_candidate_limit'],80)
        validate_reading(self.task,self.new)

    def test_old_saved_reading_still_replays_exactly_without_new_terms(self):
        replay=read(self.task,self.old['selection'],self.old['binding'],self.old['artifact_ids'],reader_version=2)
        self.assertEqual(replay,self.old)

    def test_unrelated_tasks_do_not_gain_financial_search_or_new_reader_version(self):
        task=copy.deepcopy(self.task)
        for question in task['questions']:question['method_needs']=[]
        task['calculation_specs']=[]
        self.assertEqual(financial_reader_terms(task),[])
        self.assertEqual(reader_version_for(task),2)

    def test_method_reading_and_source_selector_fit_without_truncating_the_original(self):
        views=self.new['views']
        payload={'question':self.task['question'],'questions':question_specs(self.task),
            'evidence_contract':evidence_contract(self.task,views),'untrusted_sources':views}
        schema=financial_extraction_output_schema(self.task,views)
        self.assertIsNotNone(schema)
        self.assertLess(len(canonical(financial_selector_prompt(payload,task=self.task)))+len(canonical(schema)),155000)
