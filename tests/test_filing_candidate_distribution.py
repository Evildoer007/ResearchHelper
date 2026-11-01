"""Bounded navigation must not hide one object behind another's notices."""
from collections import Counter
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import filing_candidate_choices, _filing_catalog_rows
from researchhelper.planning import input_fingerprint


class FilingCandidateDistributionTests(unittest.TestCase):
    def synthetic(self):
        # Queue-only fixture; the separate retained test uses the real gates.
        records=[]
        for ident, code, count in (('a','510300.SH',300),('b','159919.SZ',20),
                                  ('c','159919.SZ',1)):
            records.append(dict(id=ident,kind='api',operation='report_query',task_id='queue',
                research_version=1,parameters=dict(codes=code),catalog_entries=[
                    dict(source_id=ident+str(i),title=ident+str(i),
                         catalog_ref=dict(original_fields=dict(identity=ident+str(i)))) for i in range(count)]))
        return dict(id='queue',version=1,filing_candidate_distribution_version=1,source_records=records)

    def test_objects_and_later_queries_get_an_early_turn_in_provider_order(self):
        task=self.synthetic()
        pairs=list(_filing_catalog_rows(task,task['source_records']))
        self.assertEqual([(r['id'],e['source_id']) for r,e in pairs[:6]],
                         [('a','a0'),('b','b0'),('a','a1'),('c','c0'),('a','a2'),('b','b1')])
        self.assertEqual([e['source_id'] for r,e in pairs if r['id']=='b'],
                         ['b'+str(i) for i in range(20)])
        self.assertEqual(len(pairs),321)

    def test_unchanged_limits_and_no_automatic_download_or_approval(self):
        task=self.synthetic();before=copy.deepcopy(task)
        with patch('researchhelper.evidence.validate_filing_original_request'):
            result=filing_candidate_choices(task)
        self.assertEqual(result['coverage']['inspected_rows'],256)
        self.assertEqual(result['coverage']['offered_choices'],64)
        self.assertTrue(result['coverage']['truncated'])
        self.assertIn('c', {p['catalog_id'] for p in result['selections']})
        self.assertFalse(result['automatic_download'])
        self.assertFalse(result['automatic_selection'])
        self.assertTrue(all(not item['financial_approval'] for item in result['details']))
        self.assertEqual(task,before)

    def test_ordering_cannot_bypass_original_validation_or_cross_task_filter(self):
        task=self.synthetic();task['source_records'][1]['task_id']='other'
        with patch('researchhelper.evidence.validate_filing_original_request',side_effect=ContractError('拒绝')):
            result=filing_candidate_choices(task)
        self.assertEqual(result['selections'],[])

    def test_record_filter_and_legacy_order_remain_explicit(self):
        task=self.synthetic()
        with patch('researchhelper.evidence.validate_filing_original_request'):
            only=filing_candidate_choices(task,record_ids=['b'])
            task.pop('filing_candidate_distribution_version')
            legacy=filing_candidate_choices(task)
        self.assertEqual({p['catalog_id'] for p in only['selections']},{'b'})
        self.assertEqual({p['catalog_id'] for p in legacy['selections']},{'a'})

    def test_actual_retained_catalogues_both_represented_without_changing_old_task(self):
        path=ROOT/'result/acceptance/autonomous-data-phase-jv_z7eip/task.json'
        original=path.read_bytes();task=json.loads(original)
        byid={r['id']:r.get('parameters',{}).get('codes') for r in task['source_records']}
        old=filing_candidate_choices(task)
        self.assertEqual(Counter(byid[p['catalog_id']] for p in old['selections']),{'510300.SH':64})
        projection=copy.deepcopy(task)
        projection['filing_candidate_distribution_version']=1
        choices=filing_candidate_choices(projection)
        counts=Counter(byid[p['catalog_id']] for p in choices['selections'])
        self.assertTrue(counts['510300.SH'] and counts['159919.SZ'])
        targeted={r['id'] for r in task['source_records'] if r.get('parameters',{}).get('functionpara')
                  =={'keyWord':'2026年中期报告'}}
        self.assertTrue(targeted.intersection(p['catalog_id'] for p in choices['selections']))
        self.assertEqual(choices['coverage']['choice_limit'],64)
        self.assertEqual(choices['coverage']['inspection_limit'],256)
        self.assertEqual(task['status'],'失败')
        self.assertEqual(path.read_bytes(),original)

    def test_distribution_policy_is_bound_and_no_fixture_upgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task=agent.create('比较510300.SH和159919.SZ')
            self.assertEqual(task['filing_candidate_distribution_version'],1)
            old=copy.deepcopy(task);old.pop('filing_candidate_distribution_version')
            self.assertNotEqual(input_fingerprint(task),input_fingerprint(old))
            self.assertNotIn('filing_candidate_distribution_version',agent.create_case('mechanism'))
