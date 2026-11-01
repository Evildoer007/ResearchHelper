"""Independent regressions for partial data and planned discovery vocabulary.

Retained live failures are read-only fixtures. No network or replacement
model answers; schema eligibility is not evidence or financial approval.
"""
import copy
import gzip
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,validate_evidence
from researchhelper.data_requirements import api_cell_need_groups,pending
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence,expand_api_cell_selections
from researchhelper.object_discovery import discovery_request,request_guidance
from researchhelper.output_schema import api_extraction_output_schema,decode_api_extraction_output,_matches
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract,extractable_source_views


class FlexibleAPINeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path=ROOT/'result/acceptance/autonomous-data-phase-791d5w1u/task.json.gz'
        cls.original=hashlib.sha256(cls.path.read_bytes()).hexdigest()
        with gzip.open(cls.path,'rt',encoding='utf-8') as stream:cls.old=json.load(stream)

    def setUp(self):
        guard=patch('socket.socket.connect',side_effect=AssertionError('Offline fixtures only'))
        guard.start();self.addCleanup(guard.stop)
        self.task=copy.deepcopy(self.old)
        self.views=extractable_source_views(self.task)
        self.contract=evidence_contract(self.task,self.views)
        self.source=next(s for s in self.contract['sources'] if s.get('cell_choices'))
        self.cells=self.source['cell_choices']
        # Compare grammar on the same actual catalogue. Never rebind a prior
        # acquisition to a new input hash or call it a new accepted study.
        self.task['api_need_compatibility_version']=1
        with patch('researchhelper.reasoning.evidence_contract',return_value=self.contract):
            self.schema=api_extraction_output_schema(self.task,self.views)
        self.group=api_cell_need_groups(self.task,self.cells)[0]

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),self.original)

    def wire(self,ids=None):
        qids=list(self.group['need_choices'])
        return dict(version=2,evidence_specs=[dict(
            api_cell_selections=ids if ids is not None else self.group['candidate_ids'],
            question_ids=qids,review=dict(question_ids=qids,allowed_natures=['已实现事实'],
                conditions=['只核对实际返回的未复权价格，币种按原始请求，不证明完整交易日历。'],
                reason='缺少其他资料不影响这些实际返回的逐日观测。',
                evidence_need_texts=[dict(question_id=qid,texts=texts)
                    for qid,texts in self.group['need_choices'].items()]))])

    def test_all_actual_rows_remain_selectable_without_interval_calendar_link(self):
        self.assertEqual(len(self.cells),5)
        self.assertEqual(self.group['candidate_ids'],[c['candidate_id'] for c in self.cells])
        choices=self.group['need_choices']['q1']
        constraints=self.task['questions'][0]['evidence_need_constraints']
        self.assertTrue(any(constraints[t]['value_kind']=='number' for t in choices))
        self.assertTrue(all(constraints[t]['period_kind']!='interval' for t in choices))
        self.assertTrue(_matches(self.wire(),self.schema))

    def test_schema_rejects_the_old_all_needs_mapping_instead_of_silently_repairing(self):
        wire=self.wire()
        wire['evidence_specs'][0]['review']['evidence_need_texts'][0]['texts']=self.task['questions'][0]['evidence_needs']
        self.assertFalse(_matches(wire,self.schema))
        with self.assertRaises(ContractError):decode_api_extraction_output(wire,self.schema)
        old=copy.deepcopy(self.task);old.pop('api_need_compatibility_version')
        self.assertTrue(_matches(wire,api_extraction_output_schema(old,self.views)))

    def test_partial_observations_are_usable_but_do_not_claim_full_coverage(self):
        decoded=decode_api_extraction_output(self.wire(self.group['candidate_ids'][:3]),self.schema)
        task=copy.deepcopy(self.old)
        specifications=expand_api_cell_selections(task,decoded['evidence_specs'])
        sources={s['id']:s for s in task['source_records']}
        task['evidence']=[]
        for spec,cell in zip(specifications,self.cells):
            item=candidate_evidence(task,sources[spec['artifact_id']],spec)
            ResearchAnalyzer().assess(task,item,spec['review'],task['source_records'])
            validate_evidence(item,task)
            self.assertEqual((item['value'],item['unit'],item['period']),
                             (cell['value'],cell['unit'],cell['period']))
            task['evidence'].append(item)
        self.assertEqual(len(task['evidence']),3)
        self.assertTrue(pending(task))

    def test_other_source_candidate_cannot_enter_and_policy_is_fingerprinted(self):
        with self.assertRaises(ContractError):
            decode_api_extraction_output(self.wire(['foreign_cell']),self.schema)
        before=input_fingerprint(self.task)
        self.task.pop('api_need_compatibility_version')
        self.assertNotEqual(input_fingerprint(self.task),before)

    def test_short_links_restore_one_explicit_owner_without_copying_long_needs(self):
        task=copy.deepcopy(self.task);task['api_need_compatibility_version']=2
        task['input_hash']=input_fingerprint(task)
        with patch('researchhelper.reasoning.evidence_contract',return_value=self.contract):
            schema=api_extraction_output_schema(task,self.views)
        wire=self.wire();wire['version']=3
        item=wire['evidence_specs'][0];item.pop('question_ids');review=item['review'];review.pop('question_ids')
        pairs=review.pop('evidence_need_texts')
        question=task['questions'][0]
        review['need_links']=[dict(question_id=p['question_id'],
            need_ids=['n'+str(question['evidence_needs'].index(text)+1) for text in p['texts']]) for p in pairs]
        self.assertTrue(_matches(wire,schema))
        decoded=decode_api_extraction_output(wire,schema,context=(task,self.views))['evidence_specs'][0]
        self.assertEqual(decoded['review']['evidence_need_texts'],{p['question_id']:p['texts'] for p in pairs})
        self.assertEqual(decoded['question_ids'],['q1'])
        self.assertNotIn('need_links',decoded['review'])
        self.assertNotIn('value',decoded)
        with self.assertRaises(ContractError):decode_api_extraction_output(wire,schema)
        invalid=copy.deepcopy(wire);invalid['evidence_specs'][0]['review']['need_links'][0]['need_ids']=['n999']
        with self.assertRaises(ContractError):decode_api_extraction_output(invalid,schema,context=(task,self.views))


class PlannedDiscoveryScopeTests(unittest.TestCase):
    def task(self):
        return dict(question='研究证券行业',objects=[],research_mode='current',
            object_discovery_version=2,discovery_planned_scope_version=1,
            questions=[dict(id='q1',question='核对证券公司盈利',
                evidence_needs=['明确分类下的完整A股证券公司名单'])])

    def test_research_plan_can_refine_retrieval_words_without_changing_the_user_question(self):
        task=self.task();before=copy.deepcopy(task)
        parameters=dict(searchstring='A股证券行业股票',searchtype='stock')
        self.assertEqual(discovery_request(task,parameters)['topic'],'A股证券')
        self.assertEqual(task,before)
        task.pop('discovery_planned_scope_version')
        with self.assertRaises(ContractError):discovery_request(task,parameters)

    def test_unrelated_supplier_text_and_generic_market_scans_remain_outside_scope(self):
        task=self.task();task['materials']=[dict(text='半导体')]
        for topic in ('半导体','全市场'):
            with self.subTest(topic=topic),self.assertRaises(ContractError):
                discovery_request(task,dict(searchstring=topic+'行业股票',searchtype='stock'))
        task['research_mode']='historical'
        self.assertIsNone(discovery_request(task,dict(searchstring='证券行业股票',searchtype='stock')))

    def test_new_ordinary_tasks_share_the_versioned_rules(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as directory:
            agent=ResearchAgent(ResearchArchive(directory),ROOT,offline=True,require_argument_support=True)
            try:
                task=agent.create('研究证券行业',research_mode='current')
                self.assertEqual(task['api_need_compatibility_version'],2)
                self.assertEqual(task['discovery_planned_scope_version'],1)
                self.assertIn('研究子问题',request_guidance(task,include_results=False)['topic_rule'])
                before=input_fingerprint(task);task.pop('discovery_planned_scope_version')
                self.assertNotEqual(before,input_fingerprint(task))
            finally:agent.close_runtime()


if __name__=='__main__':unittest.main()
