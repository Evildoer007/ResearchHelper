"""Literal reading completeness, not a live financial-research acceptance.

The retained real study is immutable. Geometry-only synthetic tests isolate
routing; separate real-PDF tests exercise actual extraction and replay.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper import source_reader as reader
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,canonical,charge,fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import extractable_source_views

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/default-app-investment-zl8a989j/task.json'


class RetainedTableReadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_sha=hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.task=json.loads(RETAINED.read_text())
        cls.old=cls.task['reasoning_state']['reading']['bundle']
        cls.new=reader.read(cls.task,cls.old['selection'],cls.old['binding'],cls.old['artifact_ids'])

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest()==cls.original_sha

    def test_old_reading_replays_byte_for_byte_instead_of_silent_upgrade(self):
        replay=reader.read(self.task,self.old['selection'],self.old['binding'],self.old['artifact_ids'],reader_version=1)
        self.assertEqual(replay,self.old);reader.validate_reading(self.task,self.old)

    def test_real_consolidated_statements_and_continuations_are_fully_exposed(self):
        parts={p['locator']:p for view in self.new['views'] for p in view['parts']}
        for locator in ('第30页表2','第31页表1','第32页表1','第33页表2','第34页表1'):
            self.assertIn(locator,parts)
            self.assertEqual((parts[locator]['reading_start'],parts[locator]['reading_end']),
                             (0,parts[locator]['original_characters']))
        self.assertIn('五、净利润',parts['第31页表1']['text'])
        self.assertIn('经营活动产生的现金流量净额',parts['第34页表1']['text'])

    def test_original_characters_and_approval_boundaries_are_unchanged(self):
        views={view['id']:view for view in extractable_source_views(self.task)}
        for view in self.new['views']:
            original={p['locator']:p for p in views[view['id']]['parts']}
            for part in view['parts']:
                self.assertEqual(part['text'],original[part['locator']]['text'][part['reading_start']:part['reading_end']])
        self.assertLessEqual(len(canonical(self.new['views'])),reader.CONTEXT_LIMIT)
        self.assertFalse(self.task['evidence']);self.assertFalse(self.task['calculations'])
        self.assertGreater(self.new['coverage'][0]['omitted_characters'],0)
        self.assertEqual(self.new['coverage'][0]['table_group_scope'],'geometry_only_not_financial_approval')

    def test_version_two_replay_and_term_coverage_are_deterministic(self):
        reader.validate_reading(self.task,self.new)
        self.assertEqual(self.new['reader_version'],2)
        coverage={row['term']:row for row in self.new['coverage'][0]['query_coverage']}
        self.assertGreater(coverage['营业收入']['provided_candidates'],0)
        self.assertGreater(coverage['经营活动产生的现金流量净额']['provided_candidates'],0)
        self.assertEqual(coverage['报表净利润']['hit_count'],0)

    def test_unknown_or_noninteger_reading_versions_are_rejected(self):
        for version in (3,True,'2',2.0):
            with self.assertRaises(ContractError):
                reader.read(self.task,self.old['selection'],self.old['binding'],self.old['artifact_ids'],reader_version=version)

    def test_rehashed_forged_reading_does_not_change_original_geometry(self):
        forged=copy.deepcopy(self.new)
        part=next(p for view in forged['views'] for p in view['parts'] if 'pdf_table' in p)
        part['pdf_table']['group']='forged-scope'
        forged['reading_hash']=fingerprint({k:v for k,v in forged.items() if k!='reading_hash'})
        with self.assertRaises(ContractError): reader.validate_reading(self.task,forged)


class SyntheticReadingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.task=dict(id='synthetic_reading',version=1,input_hash='synthetic',questions=[{'id':'q1'}])
        self.parts=[dict(locator='table-a',text='metric '+('a'*1200),pdf_table=dict(version=1,geometry_only=True,group='table-a')),
                    dict(locator='table-b',text='negative counterexample '+('b'*1200),pdf_table=dict(version=1,geometry_only=True,group='table-a'))]

    def read(self,queries=None,parts=None,**kwargs):
        record=dict(id='synthetic_original',kind='material',hash='synthetic',snapshot_hash='synthetic',
                    snapshot={'parts':self.parts})
        view=dict(id=record['id'],kind='material',snapshot_hash='synthetic',name='synthetic geometry only',parts=self.parts)
        request=dict(artifact_id=record['id'],snapshot_hash='synthetic',queries=['metric'] if queries is None else queries,
                     parts=[] if parts is None else parts,reason='synthetic routing only',question_ids=['q1'])
        with patch.object(reader,'_records',return_value=[record]),patch.object(reader,'source_views',return_value=[view]), \
                patch.object(reader,'_verify_original_text'):
            return reader.read(self.task,[request],reader.binding(self.task),**kwargs)

    def test_quotas_reuse_unused_capacity_without_suppressing_rare_terms(self):
        self.assertEqual(reader._fair_quotas([60,1,1],40),[38,1,1])
        self.assertEqual(reader._fair_quotas([3,1],80),[3,1])

    def test_sampling_does_not_alias_away_one_representation(self):
        channels=[[('table',i) for i in range(50)],[('prose',i) for i in range(50)]]
        sampled=reader._sample_channels(channels,12)
        self.assertEqual(len(sampled),12)
        for name in ('table','prose'):
            self.assertIn((name,0),sampled);self.assertIn((name,49),sampled)

    def test_queries_in_one_position_bucket_keep_rare_term_candidates(self):
        parts=[dict(locator='single',text='common '*600+'rare-marker'+' common'*600)]
        candidates,counts=reader._query_candidates(parts,['common','rare-marker'])
        self.assertGreater(counts['common'],1000)
        self.assertTrue(any(item[3]=='rare-marker' for item in candidates))
        self.assertLessEqual(len(candidates),reader.MAX_QUERY_CANDIDATES)

    def test_literal_search_never_executes_regex(self):
        candidates,counts=reader._query_candidates(self.parts,['.*'])
        self.assertEqual(candidates,[]);self.assertEqual(counts['.*'],0)

    def test_query_expands_the_complete_physical_group_including_counterexample(self):
        bundle=self.read();parts=bundle['views'][0]['parts']
        self.assertEqual({p['locator'] for p in parts},{'table-a','table-b'})
        self.assertTrue(all(p['reading_start']==0 and p['reading_end']==p['original_characters'] for p in parts))
        self.assertIn('negative counterexample',parts[1]['text'])

    def test_explicit_span_remains_exact_without_borrowing_unread_table_text(self):
        bundle=self.read(queries=[],parts=[dict(part='table-a',start=10,end=20)])
        parts=bundle['views'][0]['parts'];self.assertEqual(len(parts),1)
        self.assertEqual(parts[0]['text'],self.parts[0]['text'][10:20])

    def test_oversized_group_is_not_sliced_into_a_fake_complete_context(self):
        with patch.object(reader,'CONTEXT_LIMIT',2200): bundle=self.read()
        self.assertEqual(bundle['views'],[])
        self.assertEqual(bundle['coverage'][0]['unread_table_groups'],['table-a'])
        self.assertTrue(any('未截断表格' in item for item in bundle['limitations']))

    def test_distinct_groups_are_not_merged_by_column_similarity(self):
        self.parts[1]['pdf_table']['group']='other-group'
        bundle=self.read();self.assertEqual([p['locator'] for p in bundle['views'][0]['parts']],['table-a'])

    def test_unknown_geometry_does_not_gain_group_reading_authority(self):
        for part in self.parts: part['pdf_table']['version']=99
        bundle=self.read();self.assertEqual([p['locator'] for p in bundle['views'][0]['parts']],['table-a'])
        self.assertLess(len(bundle['views'][0]['parts'][0]['text']),len(self.parts[0]['text']))


class ReadingCheckpointVersionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='reader-checkpoint-',dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.agent=ResearchAgent(ResearchArchive(Path(temporary.name)/'store'),ROOT,model=object())

    def task(self,long=False):
        task=self.agent.create('核对目标条款和限制',objects=['合成材料'])
        body=('背景不是事实。'*14000 if long else '中性背景。')+'\n\n目标条款：仅限合成验收。'
        task=self.agent.add_material(task['id'],'fixture.txt',body.encode())
        task.update(status='运行中',batch=1,host_result={})
        self.agent.archive.save(task)
        return task

    def test_small_original_preserves_legacy_extraction_cache_key(self):
        task=self.task();views=extractable_source_views(task)
        digest=fingerprint(dict(input_hash=task['input_hash'],sources=views,reader_version=1))
        task['reasoning_state']={'extraction':{'input_digest':digest}}
        with patch('researchhelper.engine.model_object') as model:
            self.agent._extract_model_sources(task);model.assert_not_called()

    def test_old_large_checkpoint_cannot_skip_new_reading_and_new_cache_does_not_recall(self):
        task=self.task(long=True);views=extractable_source_views(task)
        old_digest=fingerprint(dict(input_hash=task['input_hash'],sources=views,reader_version=1))
        task['reasoning_state']={'extraction':{'input_digest':old_digest}}
        calls=[]
        def model(service,task,phase,payload,required):
            calls.append(phase);charge(task['ledger'],'model_calls')
            task['ledger']['dispatches'].append(dict(kind='model_calls',research_phase=phase,status='成功',batch=task['batch']))
            if phase=='read':
                source=task['materials'][0]
                return {'requests':[dict(artifact_id=source['id'],snapshot_hash=source['snapshot_hash'],
                    queries=['目标条款'],parts=[],reason='合成读取检查',question_ids=['q1'])]},{}
            self.assertEqual(phase,'extract');return {'evidence_specs':[]},{}
        with patch('researchhelper.engine.model_object',side_effect=model):
            self.agent._extract_model_sources(task);self.agent._extract_model_sources(task)
        self.assertEqual(calls,['read','extract'])
        self.assertEqual(task['reasoning_state']['reading']['bundle']['reader_version'],2)
        self.assertEqual(task['ledger']['used']['model_calls'],2)
        self.assertEqual(task['ledger']['used']['data_requests'],0)
        self.assertFalse(task['evidence'])


if __name__=='__main__': unittest.main()
