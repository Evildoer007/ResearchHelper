"""Versioned inflation frameworks reach App and Skill without extra inference."""
from copy import deepcopy
import json
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from tests import ROOT
from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,canonical,fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge,CATALOGUE_POLICY,GENERAL_DEFINITION_SCOPE
from researchhelper.draft_workflow import _write

NEW_IDS={'rh_k_cpi_reading','rh_k_inflation_transmission'}
QUESTION='分析中国CPI变化和投资含义，提出有条件的研究逻辑'


class InflationKnowledgeTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external I/O'))
        guard.start();self.addCleanup(guard.stop)
        self.knowledge=ResearchKnowledge()

    def agent(self,folder):
        engine=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True,draft_first=True)
        self.addCleanup(engine.close_runtime);return engine

    def package(self,revision=3,question=QUESTION):
        return self.knowledge.read('宏观',question,context={CATALOGUE_POLICY:revision})

    def test_old_catalogues_never_gain_new_entries_or_rewrite_their_packages(self):
        for revision in (1,2):
            package=self.package(revision);before=canonical(package)
            self.assertFalse(NEW_IDS & set(package['index']))
            self.knowledge.validate_package(package,revision=revision)
            self.assertFalse(NEW_IDS & {e['id'] for e in self.knowledge.catalog(revision)})
            self.assertEqual(canonical(package),before)

    def test_current_research_selects_frameworks_and_exact_dependencies(self):
        package=self.package()
        self.assertTrue(NEW_IDS <= package['index'].keys())
        self.assertEqual(package['index']['rh_k_rate_transmission'],1)
        self.assertEqual(package['index']['rh_k_fact'],1)
        self.knowledge.validate_package(package,revision=3)
        self.assertTrue(all(not e.get('implementation') for e in package['entries'] if e['id'] in NEW_IDS))

    def test_plain_definition_does_not_force_investment_channels(self):
        package=self.package(question='只解释CPI的同比与环比口径')
        self.assertIn('rh_k_cpi_reading',package['index'])
        self.assertNotIn('rh_k_inflation_transmission',package['index'])
        self.assertNotIn('rh_k_rate_transmission',package['index'])

    def test_unrelated_questions_do_not_acquire_inflation_paths(self):
        for entry,question in [('个股','核对营业收入'),('ETF','比较两只ETF年度管理费'),('宏观','说明PMI扩散指数')]:
            package=self.knowledge.read(entry,question,context={CATALOGUE_POLICY:3})
            self.assertFalse(NEW_IDS & package['index'].keys())

    def test_entry_has_official_source_locations_but_no_current_fact_or_foreign_target(self):
        entries={e['id']:e for e in self.knowledge.catalog(3)}
        for ident in NEW_IDS:
            e=entries[ident];self.assertIsNone(e['publication_date'])
            self.assertTrue(e['source_reference']['sections']);self.assertTrue(e['counterexample'])
            self.assertTrue(e['conditions']);self.assertTrue(e['verification_indicators'])
            self.assertTrue(e['source_reference']['url'].startswith(('https://www.stats.gov.cn/','https://www.ecb.europa.eu/')))
            self.assertNotIn('2026年9月CPI为',e['content']);self.assertNotIn('2%',e['content'])

    def test_new_task_pins_three_and_resuming_two_does_not_migrate(self):
        with TemporaryDirectory() as folder:
            engine=self.agent(folder);fresh=engine.create(QUESTION,entry='宏观',research_mode='current')
            self.assertEqual(fresh[CATALOGUE_POLICY],3);engine._adopt_method_knowledge(fresh)
            self.assertTrue(NEW_IDS <= fresh['knowledge']['index'].keys())
            old=engine.create(QUESTION,entry='宏观',research_mode='current');old[CATALOGUE_POLICY]=2
            old['knowledge']=self.package(2);before=deepcopy(old['knowledge']['entries'])
            engine._adopt_method_knowledge(old)
            self.assertEqual(old[CATALOGUE_POLICY],2);self.assertEqual(old['knowledge']['entries'],before)
            self.assertEqual(old['ledger']['used']['model_calls'],0)

    def test_new_entry_cannot_be_transplanted_into_old_catalogue(self):
        current=self.package();current[CATALOGUE_POLICY]=2
        with self.assertRaises(ContractError):self.knowledge.validate_package(current)

    def test_definition_contract_is_available_without_fabricating_evidence(self):
        question=dict(id='q1',question='解释CPI口径',kind='fact',required=True,dependencies=[],evidence_needs=[],method_needs=[],
            answer_scope=GENERAL_DEFINITION_SCOPE,knowledge_needs=['rh_k_cpi_reading'])
        with TemporaryDirectory() as folder:
            engine=self.agent(folder);task=engine.create(question['question'],entry='宏观',questions=[question],research_mode='current')
            engine._adopt_method_knowledge(task);contract=self.knowledge.definition_contract(task,task['questions'][0])
            self.assertTrue(contract['generation_ready']);self.assertFalse(contract['fact_approval'])
            self.assertEqual(contract['knowledge_refs'],[dict(id='rh_k_cpi_reading',version=1)])
            self.assertFalse(task['evidence'] or task['calculations'])

    def test_writer_receives_full_channels_and_counterexamples_with_one_dispatch(self):
        with TemporaryDirectory() as folder:
            engine=self.agent(folder);task=engine.create(QUESTION,entry='宏观',research_mode='current');engine._adopt_method_knowledge(task)
            calls=[]
            class Model:
                def reason(self,task,messages,**options):calls.append((messages,options));return '{}'
            class Writer:model=Model()
            before=canonical(task);_write(Writer(),task,[])
            self.assertEqual(len(calls),1);packet=json.loads(calls[0][0][1]['content'])['knowledge_context']
            entry=next(e for e in packet['entries'] if e['id']=='rh_k_inflation_transmission')
            authority=next(e for e in task['knowledge']['entries'] if e['id']==entry['id'])
            for field in ('content','conditions','counterexample','channels','verification_indicators'):
                self.assertEqual(entry[field],authority[field])
            self.assertEqual(calls[0][1]['phase'],'draft_report');self.assertEqual(canonical(task),before)
            self.assertLessEqual(len(canonical(packet)),3200)

    def test_context_remains_whole_bounded_and_defensively_copied(self):
        task=dict(id='context',version=1,input_hash='input',entry='宏观',knowledge=self.package(),synthetic=False)
        before=canonical(task);small=self.knowledge.draft_context(task,limit=1000)
        self.assertLessEqual(len(canonical(small)),1000);self.assertGreater(small['omitted_count'],0)
        originals={e['id']:e for e in task['knowledge']['entries']}
        for e in small['entries']:
            for field in ('content','conditions','counterexample','channels','verification_indicators'):
                if field in e:self.assertEqual(e[field],originals[e['id']][field])
        packet=self.knowledge.draft_context(task);packet['entries'][0]['channels'].append('changed copy')
        self.assertEqual(canonical(task),before)

    def test_skill_host_and_app_share_framework_without_automatic_citation(self):
        with TemporaryDirectory() as folder:
            session=Session(folder)
            try:
                task=session.create(QUESTION,entry='宏观');session.agent._adopt_method_knowledge(task);session.archive.save(task)
                first=session.tools(task['id'])['first_report'];expected=self.knowledge.draft_context(task)
                self.assertEqual(first['knowledge_context'],expected)
                self.assertTrue(NEW_IDS <= {e['id'] for e in expected['entries']})
                self.assertEqual(self.knowledge.draft_references(task,[]),([],[]))
                self.assertEqual(session.status(task['id'])['ledger']['used']['model_calls'],0)
            finally:session.agent.close_runtime()


if __name__=='__main__':unittest.main()
