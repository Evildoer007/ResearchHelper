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

    def test_app_and_skill_share_the_versioned_method_guidance(self):
        from researchhelper.research_methods import catalogue, method_selection

        with TemporaryDirectory() as folder:
            session = Session(folder)
            self.addCleanup(session.agent.close_runtime)
            task = session.create('对照600519.SH营收、利润和经营现金流', entry='个股', cutoff='2026-11-21')
            methods = session.research_methods(task['id'])
            self.assertEqual(methods, method_selection(task))
            self.assertEqual([item['id'] for item in methods['methods']], ['stock.financial'])
            packet = self.knowledge.draft_context(task)
            self.assertEqual([item['id'] for item in packet['research_methods']], ['stock.financial'])
            self.assertNotIn('knowledge_refs', methods)
            self.assertEqual({row['subject'] for row in catalogue()['methods']},
                             {'个股', 'ETF', '指数', '行业', '事件', '宏观', '跨市场'})
            locked = dict(task, research_methods=methods)
            self.assertEqual(method_selection(dict(locked, question='市盈率估值')), methods)
            changed = method_selection(dict(locked, input_hash='changed', question='市盈率估值'))
            self.assertEqual([item['id'] for item in changed['methods']], ['stock.valuation'])

    def test_industry_addendum_and_historical_cutoff_are_specific(self):
        from researchhelper.research_methods import select_methods

        task = dict(entry='行业', question='研究券商行业景气与供需', cutoff='2026-11-21')
        self.assertEqual([item['id'] for item in select_methods(task)], ['industry.cycle', 'industry.broker'])
        task['cutoff'] = '2025-11-21'
        self.assertEqual([item['id'] for item in select_methods(task)], ['industry.cycle'])
        task.update(entry='ETF', question='比较沪深300ETF管理费与跟踪误差')
        self.assertEqual([item['id'] for item in select_methods(task)], ['etf.tracking'])

    def test_general_financial_method_works_for_an_earlier_research_cutoff(self):
        from researchhelper.research_methods import method_guidance

        task = dict(entry='个股', question='复盘贵州茅台600519.SH年报利润和经营现金流',
                    cutoff='2024-12-31')
        guidance, = method_guidance(task)
        self.assertEqual(guidance['id'], 'stock.financial')
        self.assertNotIn('年度报告准则', canonical(guidance))

    def test_direct_reads_do_not_inherit_unrequested_methods(self):
        from researchhelper.research_methods import select_methods

        for question in ('查询600519.SH的open和close，不分析财务',
                         '只查510300.SH最新单位净值', '核对600519.SH历史open'):
            with self.subTest(question=question):
                self.assertEqual(select_methods(dict(entry='混合', question=question, cutoff='2026-11-21')), [])
        task = dict(entry='个股', question='核对600519.SH业绩预告，不做估值', cutoff='2026-11-21')
        self.assertEqual([row['id'] for row in select_methods(task)], ['stock.disclosure'])

    def test_method_selection_distinguishes_research_scope_and_task(self):
        from researchhelper.research_methods import select_methods

        cases = (
            ('混合', '分析中国CPI同比的走势和食品价格变化，不研究利率', ['macro.inflation']),
            ('宏观', '解读PMI与实际GDP是否说明需求修复', ['macro.activity']),
            ('事件', '解释FOMC降息与短端利率变化', ['macro.finance']),
            ('宏观', '比较专项债发行与财政预算执行', ['macro.fiscal']),
            ('宏观', '分析国际收支、外需与贸易差额', ['macro.external']),
            ('个股', '对照600519.SH和0700.HK在同一交易窗口的涨跌和币种口径', ['cross.returns']),
            ('混合', '比较A股与H股同一发行人的A/H溢价和估值', ['cross.valuation']),
            ('事件', '研究美股与港股的跨市场传导和观察先后', ['cross.transmission']),
            ('行业', '按券商行业样本覆盖率解释财务分化', ['industry.sample', 'industry.broker']),
            ('混合', '获取证券行业半年报营业收入，用完整公司名单核对覆盖情况。', ['industry.sample', 'industry.broker']),
            ('行业', '比较行业估值分位与可比公司市盈率', ['industry.valuation']),
            ('产业链', '梳理产业链成本向下游利润的传导', ['industry.earnings']),
            ('行业', '研究港股创新药行业监管事件，美国政策作为背景', ['industry.policy', 'industry.drug']),
            ('宏观', '只查询最新一期CPI同比读数', []),
            ('指数', '比较两个指数的价格走势与全收益', ['index.performance']),
            ('指数', '分析成分股的权重贡献以及上涨家数', ['index.breadth']),
            ('混合', '沪深300指数是少数权重股带动还是普涨？', ['index.breadth']),
            ('指数', '查询沪深300最新交易日涨跌幅', []),
            ('事件', '研究公告发布后的交易窗口与价格反应', ['event.timeline']),
            ('混合', '分析IPO事件窗口与同期市场走势', ['event.timeline']),
            ('事件', '列出公告的发布时间', []),
        )
        for entry, question, expected in cases:
            with self.subTest(entry=entry, question=question):
                task = dict(entry=entry, question=question, cutoff='2026-11-21')
                self.assertEqual([row['id'] for row in select_methods(task)], expected)

    def test_method_guidance_survives_the_draft_context_budget(self):
        with TemporaryDirectory() as folder:
            session = Session(folder)
            self.addCleanup(session.agent.close_runtime)
            task = session.create('比较A股和港股同一交易窗口的涨跌及币种', entry='混合', cutoff='2026-11-21')
            guidance = self.knowledge.draft_context(task)
            stored = session.research_methods(task['id'])
            self.assertEqual([row['id'] for row in guidance['research_methods']], ['cross.returns'])
            self.assertEqual(stored['binding']['input_hash'], task['input_hash'])
            self.assertEqual([(row['id'], row['version']) for row in guidance['research_methods']],
                             [(row['id'], row['version']) for row in stored['methods']])
            self.assertLessEqual(len(canonical(guidance)), 3200)

    def test_malformed_saved_method_reference_does_not_break_the_prompt(self):
        from researchhelper.research_methods import method_guidance

        binding = dict(id='study', version=1, input_hash='bound')
        task = dict(binding, entry='ETF', question='比较管理费', cutoff='2026-11-21')
        for reference in ({'binding': binding}, {'binding': binding, 'methods': [{'id': [], 'version': 1}]}):
            task['research_methods'] = reference
            self.assertEqual([row['id'] for row in method_guidance(task)], ['etf.tracking'])

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
            from researchhelper.reports import stage_pending_html
            task['pending_content']=content;stage_pending_html(task,standard);task=agent.archive.save(task)
            task=agent.archive.deliver(task,content,standard)
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(dict(task=task,entry=entry,standard=standard,detailed=detailed),ensure_ascii=False))
        finally:agent.close_runtime()


if __name__=='__main__':unittest.main()
