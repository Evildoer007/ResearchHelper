"""The first report actually receives and explicitly cites locked knowledge.

Synthetic observations only. Model dispatch is captured, external I/O denied;
no historical task, current App store or supplier is modified.
"""
from copy import deepcopy
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from lxml import html
from tests import ROOT
from tests.test_client_report_audience import fixture
from core.session import Session
from researchhelper.contracts import canonical, fingerprint
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.draft_workflow import _write
from researchhelper.drafts import normalize_report, compose, source_binding, logic_knowledge
from researchhelper.report_designer import design


class DraftKnowledgeTests(unittest.TestCase):
    def setUp(self):
        guard=patch('socket.socket.connect', side_effect=AssertionError('No external I/O'))
        guard.start();self.addCleanup(guard.stop)
        self.task,_=fixture();self.knowledge=ResearchKnowledge()
        self.task['knowledge']=self.knowledge.read('个股','营业收入、净利润与经营现金流')
        self.entry=next(row for row in self.task['knowledge']['entries'] if row['id']=='rh_k_cash_profit')
        self.reference={key:self.entry[key] for key in ('id','version')}

    def report(self, references):
        raw=deepcopy(self.task['draft_report'])
        raw['logics'][0]['knowledge_refs']=references
        result=normalize_report(raw,self.task,raw['facts'])
        self.task['draft_report']=dict(result,facts=raw['facts'],calculations=raw['calculations'],source_binding=source_binding(self.task))
        return compose(self.task)

    def test_app_writer_receives_whole_conditions_and_counterexamples_in_one_call(self):
        calls=[]
        class Model:
            def reason(self,task,messages,**options):calls.append((messages,options));return '{}'
        class Engine:model=Model()
        before=canonical(self.task)
        _write(Engine(),self.task,self.task['draft_report']['facts'])
        self.assertEqual(len(calls),1);messages,options=calls[0]
        packet=json.loads(messages[1]['content'])['knowledge_context']
        self.assertEqual(packet,self.knowledge.draft_context(self.task))
        selected=next(row for row in packet['entries'] if row['id']==self.entry['id'])
        for field in ('content','conditions','counterexample'):self.assertEqual(selected[field],self.entry[field])
        self.assertEqual(packet['binding']['knowledge_hash'],fingerprint(self.task['knowledge']))
        self.assertNotIn('entry_hash',selected)
        self.assertEqual(options['phase'],'draft_report');self.assertTrue(options['delivery'])
        self.assertEqual(canonical(self.task),before)

    def test_entry_scope_is_not_expanded_by_a_valid_but_unrelated_package(self):
        self.task['entry']='ETF'
        packet=self.knowledge.draft_context(self.task)
        self.assertNotIn('rh_k_cost_pass',[entry['id'] for entry in packet['entries']])

    def test_synthetic_only_rules_do_not_enter_a_real_draft_context(self):
        self.task['synthetic']=False
        packet=self.knowledge.draft_context(self.task)
        self.assertNotIn('rh_k_cost_pass',[entry['id'] for entry in packet['entries']])
        refs,_=self.knowledge.draft_references(self.task,[dict(id='rh_k_cost_pass',version=1)])
        self.assertEqual(refs,[])

    def test_bounded_context_omits_whole_entries_without_cutting_their_conditions(self):
        before=canonical(self.task)
        packet=self.knowledge.draft_context(self.task,limit=1000)
        self.assertLessEqual(len(canonical(packet)),1000)
        self.assertGreater(packet['omitted_count'],0)
        originals={entry['id']:entry for entry in self.task['knowledge']['entries']}
        for entry in packet['entries']:
            for field in ('content','conditions','counterexample'):self.assertEqual(entry[field],originals[entry['id']][field])
        self.assertEqual(canonical(self.task),before)

    def test_retrieval_is_not_an_explicit_theory_citation(self):
        content=self.report([])
        self.assertTrue(content['draft']['knowledge_context']['entries'])
        self.assertEqual(content['draft']['logics'][0]['knowledge_refs'],[])
        self.assertEqual(logic_knowledge(content,content['draft']['logics'][0]),[])

    def test_unknown_wrong_version_boolean_and_forged_hash_are_not_theory(self):
        proposals=[dict(id='foreign',version=1),dict(self.reference,version=self.entry['version']+1),
            dict(self.reference,version=True),dict(self.reference,entry_hash='forged'),
            self.reference,self.reference]
        content=self.report(proposals);logic=content['draft']['logics'][0]
        self.assertEqual(logic['knowledge_refs'],[dict(self.reference,entry_hash=fingerprint(self.entry))])
        self.assertTrue(logic['reference_notes'])
        self.assertEqual(logic['analysis'],'利润降幅大于收入，需关注盈利能力变化。')
        self.assertEqual(logic_knowledge(content,logic),[self.entry])

    def test_tampered_authority_does_not_block_the_draft_or_supply_theory(self):
        self.task['knowledge']['entries'][0]['content']='unregistered replacement'
        content=self.report([self.reference]);logic=content['draft']['logics'][0]
        self.assertEqual(logic['knowledge_refs'],[])
        self.assertFalse(content['draft']['knowledge_context']['entries'])
        self.assertIn('核心结论',design(content,ROOT)['html'])

    def test_theory_is_in_internal_expansion_and_standard_click_detail_only(self):
        content=self.report([self.reference]);before=canonical(content)
        client=html.document_fromstring(design(content,ROOT)['html'])
        detail=html.document_fromstring(design(content,ROOT,profile='detailed')['html'])
        self.assertNotIn(self.entry['title'],client.xpath('//main')[0].text_content())
        target=client.xpath('//section[@id="logic-1"]//h2/a/@href')[0].lstrip('#')
        self.assertIn(self.entry['content'],client.get_element_by_id(target).text_content())
        internal=detail.xpath('//main')[0].text_content()
        for field in ('content','conditions','counterexample','source'):self.assertIn(self.entry[field],internal)
        self.assertEqual(canonical(content),before)

    def test_theory_binding_cannot_be_transplanted_to_another_task_or_input(self):
        content=self.report([self.reference]);logic=content['draft']['logics'][0]
        for field,value in [('task_id','other'),('research_version',2),('input_hash','other')]:
            changed=dict(content,**{field:value})
            self.assertEqual(logic_knowledge(changed,logic),[])
        older=deepcopy(content);older['draft'].pop('knowledge_context')
        self.assertEqual(logic_knowledge(older,logic),[])

    def test_failed_retrieval_preserves_the_locked_package_and_phase(self):
        from researchhelper.draft_workflow import _refresh_knowledge
        from researchhelper.contracts import ContractError
        task=deepcopy(self.task);task['nodes']=[dict(id='identity',status='成功')]
        before=deepcopy(task['knowledge'])
        class Engine:
            def _adopt_method_knowledge(self,task,**options):
                task['knowledge']={};task['nodes'][0]['status']='待执行'
                raise ContractError('simulated incompatible dependency')
        _refresh_knowledge(Engine(),task,[dict(label='经营现金流')])
        self.assertEqual(task['knowledge'],before)
        self.assertEqual(task['nodes'][0]['status'],'成功')
        self.assertTrue(task['draft_notices'])

    def test_skill_host_receives_the_same_context_without_an_app_model(self):
        with TemporaryDirectory() as folder:
            session=Session(folder)
            try:
                task=session.create('解释收入、利润与经营现金流的差异',entry='个股')
                task['knowledge']=session.agent.knowledge.read(task['entry'],task['question'],
                    context={'knowledge_catalogue_revision':task['knowledge_catalogue_revision']})
                session.archive.save(task)
                tools=session.tools(task['id'])['first_report']
                self.assertIn('knowledge_refs',tools['optional_logic_fields'])
                self.assertEqual(tools['knowledge_context'],self.knowledge.draft_context(task))
                self.assertEqual(session.status(task['id'])['ledger']['used']['model_calls'],0)
            finally:session.agent.close_runtime()


def make_ui_fixture(output):
    """Seed one explicit synthetic paper for offline browser presentation only."""
    from researchhelper.engine import ResearchAgent
    from researchhelper.archive import ResearchArchive
    from researchhelper.draft_analysis import analyze
    source,_=fixture()
    with TemporaryDirectory() as scratch:
        agent=ResearchAgent(ResearchArchive(scratch),ROOT,offline=True,draft_first=True)
        try:
            task=agent.create('比较收入、利润与经营现金流，解释盈利质量和短期定价',entry='个股',research_mode='current')
            task['synthetic']=True;task['source_records']=deepcopy(source['source_records'])
            for record in task['source_records']:
                record.update(task_id=task['id'],research_version=task['version'])
            task['knowledge']=ResearchKnowledge().read(task['entry'],task['question'],
                context={'knowledge_catalogue_revision':task.get('knowledge_catalogue_revision',1)})
            raw=deepcopy(source['draft_report']);facts=raw['facts']
            task['draft_analysis']=analyze(task,facts)
            entry=next(row for row in task['knowledge']['entries'] if row['id']=='rh_k_cash_profit')
            raw['logics'][0]['knowledge_refs']=[dict(id=entry['id'],version=entry['version'])]
            draft=normalize_report(raw,task,facts)
            draft['logics'][2]['reference_notes']=['有一项引用待补充']
            task['draft_report']=dict(draft,facts=facts,calculations=task['draft_analysis']['results'],
                source_binding=source_binding(task),origin='synthetic_knowledge_fixture')
            task['status']='部分完成';task=agent.archive.save(task);content=compose(task)
            standard=design(content,ROOT)['html'];detailed=design(content,ROOT,profile='detailed')['html']
            task['pending_html']=standard;task=agent.archive.save(task)
            task=agent.archive.deliver(task,content,standard)
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(dict(task=task,entry=entry,standard=standard,detailed=detailed),ensure_ascii=False))
        finally:agent.close_runtime()


if __name__=='__main__':unittest.main()
