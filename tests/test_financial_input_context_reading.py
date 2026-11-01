"""Necessary financial context shares the ordinary bounded reading window."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tests import ROOT
from tests.test_asset_data_acceptance_review import load_retained_task
from researchhelper.contracts import fingerprint
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint
from researchhelper import source_reader
from researchhelper.financial_sources import financial_reporting_currency_choices


class FinancialInputContextReadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path=ROOT/'result/acceptance/autonomous-data-phase-l6vnrw6q'
        cls.task=load_retained_task(cls.path)
        cls.before=fingerprint(cls.task)
        cls.bundle=cls.task['reasoning_state']['reading']['bundle']

    @classmethod
    def tearDownClass(cls):
        assert fingerprint(load_retained_task(cls.path))==cls.before

    def test_legacy_reading_replays_without_adding_previously_unread_pages(self):
        source_reader.validate_reading(self.task,self.bundle)
        self.assertEqual(self.task['financial_input_reading_priority_version'],1)

    def test_new_navigation_keeps_literal_currency_with_amount_tables(self):
        # An isolated navigation comparison, not a resumed/adopted task.
        task=copy.deepcopy(self.task)
        task['financial_input_reading_priority_version']=2
        task['input_hash']=input_fingerprint(task)
        new=source_reader.read(task,self.bundle['selection'],source_reader.binding(task),
                               self.bundle['artifact_ids'],reader_version=self.bundle['reader_version'])
        record=task['source_records'][-1]
        view=next(v for v in new['views'] if v['id']==record['id'])
        self.assertTrue(financial_reporting_currency_choices(record,view))
        trace=new['coverage'][0]['registered_input_reading']
        self.assertEqual(trace['version'],2)
        self.assertFalse(trace['financial_approval'])
        self.assertTrue(any('第95页' in row['parts'] and row['read_in_full'] for row in trace['groups']))
        from researchhelper.contracts import canonical
        self.assertLessEqual(len(canonical(new['views'])),source_reader.CONTEXT_LIMIT)

    def test_new_formal_policy_is_input_bound(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as directory:
            agent=ResearchAgent(ResearchArchive(Path(directory)),ROOT,offline=True,require_argument_support=True)
            try:
                task=agent.create('核对合成公司报表',research_mode='current')
                self.assertEqual(task['financial_input_reading_priority_version'],2)
                old=copy.deepcopy(task);old['financial_input_reading_priority_version']=1
                self.assertNotEqual(input_fingerprint(old),task['input_hash'])
            finally:agent.close_runtime()
