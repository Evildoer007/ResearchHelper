"""Forty synthetic host workflows, not forty independently judged studies.

Each entry uses different original statements. Intent selects the requested
output; unsupported buying claims remain opposed, not invented opportunities.
The existing positive valuation fixture remains a separate acceptance case.
"""
import copy
import hashlib
import inspect
import json
import subprocess
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import DOCUMENT_CONTENT_INTENT, ENTRIES, INTENTS, evidence_requirements
from researchhelper.engine import ResearchAgent
from researchhelper.planning import research_brief
from tests.skill_fixture_support import build_skill_fixture

ROOT = Path(__file__).resolve().parents[2]
CUTOFF = "2026-07-20T18:00:00+08:00"
PUBLICATION = "发布日期：2026年7月19日。"
SCENARIOS = {
    "个股": ("合成公司甲", "合成公司乙", "公司甲主营订单已进入交付，回款条件已确认。", "公司乙订单仍为意向，利润改善主要来自资产处置。", "订单与盈利兑现不能由意向或一次性收益替代"),
    "ETF": ("合成基金甲", "合成基金乙", "基金甲披露组合确有用户研究的目标业务暴露。", "基金乙仅名称相近，披露组合不包含目标业务暴露。", "基金主题名称不能替代实际暴露"),
    "指数": ("合成市值指数甲", "合成等权指数乙", "市值指数甲上涨集中在高权重成分，多数样本未改善。", "等权指数乙同期下跌，两者采用同一观察期及样本集合。", "权重集中与市场宽度必须分开判断"),
    "行业": ("合成行业样本甲", "合成行业样本乙", "行业样本甲需求改善，渠道和竞争条件保持稳定。", "行业样本乙受到渠道调整影响，份额和经营表现恶化。", "行业需求改善不能覆盖企业经营反证"),
    "产业链": ("合成资源环节甲", "合成加工环节乙", "资源环节甲受产品涨价支持，产量及成本条件未改变。", "加工环节乙原料随市价采购，加工费下降且产量不变。", "同一价格变化可能使不同环节受到相反影响"),
    "事件": ("合成资格群体甲", "合成资格群体乙", "已生效政策仅适用于资格群体甲，执行资格已核对。", "资格群体乙不满足政策条件，未披露其他直接受益渠道。", "政策存在不证明所有对象直接受益"),
    "宏观": ("合成浮息对象甲", "合成固定利率对象乙", "浮息对象甲合同按相关基准重定价，融资渠道可能改善。", "固定利率对象乙本期不重定价，盈利预期另受需求走弱影响。", "利率传导依赖敞口与期限，不能预设净收益方向"),
    "混合": ("合成政策对象甲", "合成费用对象乙", "政策对象甲资格满足，但材料没有价格及盈利兑现依据。", "费用对象乙仅披露合同费用，费用差异不代表实际持有收益。", "独立政策与费用问题不强造因果或重复机会"),
}


def specification(entry, intent):
    first, second, statement_a, statement_b, finding = SCENARIOS[entry]
    comparative = intent == "比较对象"
    text = {
        "综合研究": "研究材料中的现状与适用边界，不推断材料外的价格结论",
        "解释变化": "解释材料中对象表现或条件不同的渠道及限制",
        "比较对象": "比较材料中两个对象的已披露条件，不给买入建议",
        "验证观点": "验证两个对象可因相近名称或同一驱动被视为同样受益的观点",
        "发现投资机会": "现有材料是否足以支持直接买入判断，若不足说明限制，不补造价格依据",
    }[intent]
    questions = [
        dict(id="basis", kind="fact", group="basis", question="两个合成对象各自披露了什么条件",
             evidence_needs=["对象甲的原始陈述", "对象乙的原始陈述"]),
        dict(id="result", kind="comparison" if comparative else "investment" if intent == "发现投资机会" else "mechanism",
             group="result", question=text, dependencies=["basis"],
             evidence_needs=["对象甲的原始陈述", "对象乙的原始陈述"]),
        dict(id="extension", kind="fact", group="extension", question="可选的其他样本材料", required=False),
    ]
    answer = finding + "。仅限合成原件披露，未提供材料外价格、交易或净收益依据。"
    judgment = "反对" if intent in ("验证观点", "发现投资机会") else "支持"
    thesis = dict(title="不能据此形成直接买入判断" if intent == "发现投资机会" else finding,
                  judgment=judgment, question_ids=["result"], support=["original_a"], counter=["original_b"],
                  calculation_refs=[], investment_meaning=answer,
                  limitation="没有覆盖其他样本或材料外的价格条件，不推出目标收益。")
    return dict(entry=entry, intent=intent, question=text, objects=[first, second], questions=questions,
                statements=[statement_a, statement_b], finding=finding,
                answers={"basis": dict(answer=statement_a + statement_b, refs=["original_a", "original_b"]),
                         "result": dict(answer=answer, refs=["original_a", "original_b"])},
                theses=[] if comparative else [thesis])


def review_fixed_case(agent, task, case):
    """Explicit host verdicts for the declared synthetic statements only."""
    request = agent.argument_review_inputs(task['id'])['request']
    expected = {'answer:' + key + '.answer': answer['answer'] for key, answer in case['answers'].items()}
    for index, thesis in enumerate(case['theses']):
        for field in ('title', 'investment_meaning', 'limitation'):
            expected['thesis:' + str(index) + '.' + field] = thesis[field]
    assert {target['id']: target['text'] for target in request['targets']} == expected
    assert {item['text'] for item in task['evidence']} == set(case['statements'])
    assert all(item['source_receipt']['component'] == 'EvidenceCollector' and
               item['applicability_receipt']['component'] == 'ResearchAnalyzer' for item in task['evidence'])
    reviews = []
    for target in request['targets']:
        assert set(target['evidence_refs']) == {'original_a', 'original_b'}
        reviews.append(dict(target_id=target['id'], segments=[dict(text=target['text'],
            verdict='supported', basis_refs=['evidence:original_a', 'evidence:original_b'],
            explanation='固定合成开发样本明确列示两项条件及相反渠道；只核对已声明的测试判断，不代表真实金融质量。')]))
    submission = dict(version=request['version'], review_hash=request['review_hash'], reviews=reviews,
        questions=[dict(question_id=ident, status='answered', missing_evidence=[],
            reason='只回答两份固定合成原件及其明示限制，不给真实证券推荐。') for ident in case['answers']])
    agent.submit_argument_review(task['id'], submission)


def execute(agent, case):
    """Run through normal materials and two-class verification, no raw approvals."""
    task = agent.create(case["question"], entry=case["entry"], intent=case["intent"], objects=case["objects"],
                        cutoff=CUTOFF, questions=case["questions"])
    task["synthetic"] = True
    agent.archive.save(task)
    specs = []
    for index, text in enumerate(case["statements"]):
        task = agent.add_material(task["id"], "synthetic-original-" + str(index) + ".txt",
                                  (PUBLICATION + "\n\n" + text).encode())
        artifact = task["materials"][-1]
        need_ids = {question["id"]: [evidence_requirements(question)[index]["id"]]
                    for question in task["questions"] if question["id"] in ("basis", "result")}
        specs.append(dict(id="original_a" if index == 0 else "original_b", artifact_id=artifact["id"],
                          object=case["objects"][index], text=text, record_locator={"part": "段落2"},
                          period="合成材料期", nature="合同政策条款" if case["entry"] == "宏观" else "已实现事实", unit="不适用", question_ids=["basis", "result"],
                          published_at="2026-07-19", publication=dict(at="2026-07-19", precision="日", timezone="Asia/Shanghai",
                              certainty="已确认", basis="合成原件明确发布日期", basis_ref=dict(kind="material", artifact_id=artifact["id"],
                                  snapshot_hash=artifact["snapshot_hash"], part="段落1", quote=PUBLICATION)),
                          review=dict(question_ids=["basis", "result"], allowed_natures=["合同政策条款", "已实现事实"],
                                      conditions=["仅用于合成材料的声明、差异和适用限制"],
                                      reason="逐字原件与公开时点匹配，开发样本不对应真实证券", evidence_need_ids=need_ids)))
    brief = research_brief(task)
    staged = agent.start(task["id"], dict(evidence_specs=specs, answers=case["answers"], theses=case["theses"]), defer_delivery=True)
    assert staged['status'] == '待开始' and not staged.get('delivery'), staged.get('gaps')
    review_fixed_case(agent, staged, case)
    return agent.start(task['id']), brief


def semantic_projection(task):
    return dict(entry=task["entry"], intent=task["intent"], question=task["question"], objects=task["objects"], status=task["status"],
                answers=[{key: q.get(key) for key in ("id", "kind", "required", "status", "answer")} for q in task["questions"]],
                theses=[{key: thesis.get(key) for key in ("title", "judgment", "question_ids", "investment_meaning", "limitation")}
                        for thesis in task["theses"]])


class ResearchWorkflowMatrixTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="research-matrix-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.agent = ResearchAgent(ResearchArchive(self.directory / "app"), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        denied = patch.object(socket.socket, "connect", side_effect=AssertionError("No external calls"))
        denied.start()
        self.addCleanup(denied.stop)

    def test_forty_actual_archived_host_workflows_and_source_skill_parity(self):
        from lxml import html
        cases = [specification(entry, intent) for entry in ENTRIES for intent in INTENTS if intent != DOCUMENT_CONTENT_INTENT]
        self.assertEqual(len(cases), 40)
        installation = self.directory / 'installation'
        build_skill_fixture(installation)
        def hashes():
            return {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in installation.rglob('*') if p.is_file()}
        before = hashes()
        program = '\n'.join([
            'import copy,importlib.util,json,sys',
            'from pathlib import Path',
            'from lxml import html',
            'installation,project=map(Path,sys.argv[1:]);sys.path.insert(0,str(installation))',
            "spec=importlib.util.spec_from_file_location('matrix_skill',installation/'core/session.py')",
            'module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)',
            'from researchhelper.contracts import evidence_requirements,fingerprint',
            'from researchhelper.planning import research_brief',
            'from researchhelper.archive import ResearchArchive',
            'import researchhelper.engine',
            'assert Path(researchhelper.engine.__file__).resolve().is_relative_to(installation)',
            'CUTOFF='+repr(CUTOFF), 'PUBLICATION='+repr(PUBLICATION),
            inspect.getsource(review_fixed_case), inspect.getsource(execute), inspect.getsource(semantic_projection),
            "session=module.Session(project);records=[];assert session.agent.model is None",
            'try:',
            ' for case in json.loads(sys.stdin.read()):',
            '  untouched=copy.deepcopy(case);task,brief=execute(session.agent,case)',
            "  assert task['status']=='已完成',task.get('gaps')",
            '  assert case==untouched',
            "  assert task['ledger']['used']['model_calls']==task['ledger']['used']['data_requests']==0",
            "  assert len(task['argument_support_reviews'])==1",
            "  saved=copy.deepcopy(session.agent.export(task['id']))",
            "  target=session.export(task['id']);assert target.is_file() and target.with_suffix('.json').is_file()",
            "  paper=html.document_fromstring(target.read_text()).find('.//main').text_content()",
            "  assert case['finding'] in paper and all(statement in paper for statement in case['statements'])",
            "  assert session.agent.export(task['id'])==saved",
            "  assert semantic_projection(ResearchArchive(session.archive.root).get(task['id']))==semantic_projection(task)",
            "  records.append(dict(projection=semantic_projection(task),focus=brief['entry_focus'],content_hash=fingerprint(saved['content'])))",
            ' print(json.dumps(records,ensure_ascii=False))',
            'finally:session.agent.close_runtime()'])
        script = self.directory / 'check-skill.py';script.write_text(program)
        policy='(version 1)(allow default)(deny network*)'
        for path in (ROOT, ROOT.parent/'OptionHelper', ROOT.parent/'DataFetcher', Path.home()/'Library/Application Support/ResearchHelper'):
            policy+='(deny file-read* file-write* (subpath '+json.dumps(str(path))+'))'
        policy+='(deny file-write* (subpath '+json.dumps(str(installation))+'))'
        process = subprocess.run(['/usr/bin/sandbox-exec','-p',policy,sys.executable,'-I','-B',str(script),
            str(installation),str(self.directory/'skill-project')], input=json.dumps(cases,ensure_ascii=False),
            cwd=self.directory,text=True,capture_output=True,timeout=120)
        self.assertEqual(process.returncode,0,process.stderr[-2500:])
        hosted=json.loads(process.stdout);self.assertEqual(len(hosted),40);self.assertEqual(hashes(),before)
        combinations=[];proof=[]
        for case,remote in zip(cases,hosted):
            with self.subTest(entry=case['entry'],intent=case['intent']):
                untouched=copy.deepcopy(case);actual,brief=execute(self.agent,case)
                self.assertEqual(actual['status'],'已完成',actual.get('gaps'))
                self.assertEqual(case,untouched)
                self.assertEqual(semantic_projection(actual),remote['projection'])
                self.assertEqual(brief['entry_focus'],remote['focus'])
                self.assertEqual(next(q for q in actual['questions'] if q['id']=='extension')['status'],'未回答')
                self.assertEqual(len(actual['theses']),0 if case['intent']=='比较对象' else 1)
                self.assertEqual(len(actual['argument_support_reviews']),1)
                self.assertTrue(actual['synthetic'])
                self.assertEqual(actual['ledger']['used']['model_calls'],0)
                self.assertEqual(actual['ledger']['used']['data_requests'],0)
                report=self.agent.export(actual['id']);paper=html.document_fromstring(report['html']).find('.//main').text_content()
                self.assertIn(case['finding'],paper)
                for statement in case['statements']:self.assertIn(statement,paper)
                recovered=ResearchAgent(ResearchArchive(self.agent.archive.root),ROOT,offline=True)
                try:self.assertEqual(semantic_projection(recovered.archive.get(actual['id'])),semantic_projection(actual))
                finally:recovered.close_runtime()
                combinations.append((case['entry'],case['intent']))
                proof.append(dict(entry=case['entry'],intent=case['intent'],source=semantic_projection(actual),
                    skill=remote['projection'],skill_content_hash=remote['content_hash']))
        self.assertEqual(len(combinations),40);self.assertEqual(len(set(combinations)),40)
        self.matrix_receipt=dict(status='passed',cases=proof,skill_installation_unchanged=True,
            source_repository_denied_in_skill=True,model_calls=0,real_supplier_calls=0,financial_quality_verified=False)



if __name__ == "__main__":
    unittest.main()
