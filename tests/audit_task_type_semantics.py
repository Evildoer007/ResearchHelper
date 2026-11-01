"""Adversarial in-memory audit. Findings are not real research conclusions."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import data_request_contract
from researchhelper.knowledge import ResearchKnowledge

RETAINED=ROOT/'result/acceptance/one-sentence-research-jrfhac2u/task.json'
SCENARIOS={
    '个股':'研究600519.SH的盈利质量与估值，生成报告。',
    'ETF':'比较510300.SH与510500.SH的费率、净值与持仓，生成报告。',
    '指数':'研究000300.SH的估值、盈利及上涨来源，生成报告。',
    '行业':'研究券商行业的盈利驱动、竞争与估值，生成报告。',
    '产业链':'研究AI算力产业链的供需、利润分配与风险，生成报告。',
    '事件':'研究美联储降息对港股互联网的影响，核查支持与反证。',
    '宏观':'研究中国PMI、通胀和利率变化对权益市场的影响。',
    '混合':'研究降息对港股互联网的影响，并比较510300.SH与510500.SH。',
}


def run():
    output=Path(tempfile.mkdtemp(prefix='task-type-semantics-',dir=ROOT/'result/acceptance'))
    digest=hashlib.sha256(RETAINED.read_bytes()).hexdigest()
    record=dict(status='audit_complete',real_model_calls=0,real_supplier_calls=0,
        production_store_touched=False,app_installed=False,demo_synced=False,
        probes_are_in_memory_or_isolated_synthetic=True,financial_quality_accepted=False)
    with tempfile.TemporaryDirectory(prefix='isolated-',dir=output) as temporary:
        agent=ResearchAgent(ResearchArchive(Path(temporary)/'store'),ROOT,offline=True)
        probes=[]
        for entry,question in SCENARIOS.items():
            task=agent.create(question,entry=entry)
            contract=data_request_contract(task)
            probes.append(dict(entry=entry,question=question,declared_codes=contract['declared_codes'],
                tools=[tool['source']+'.'+tool['operation'] for tool in contract['tools']],
                series_fields=[field for tool in contract['tools'] if tool['operation']=='date_sequence'
                               for field in tool['raw_fields']]))
        record['entry_probes']=probes
        routing=[]
        for entry,code in (('个股','600519.SH'),('ETF','510300.SH'),('指数','000300.SH')):
            for selected in (entry,'混合'):
                task=agent.create('研究'+code+'的估值和净值',entry=selected,objects=[code])
                tools=data_request_contract(task)['tools']
                routing.append(dict(object_type=entry,entry=selected,code=code,
                    series_available=any(tool['operation']=='date_sequence' for tool in tools)))
        record['same_object_entry_routing']=routing
    task=json.loads(RETAINED.read_text())
    proposal=copy.deepcopy(task['host_result']['theses'][0])
    text={
        'title':'美联储降息已直接导致公司利润改善',
        'investment_meaning':'现有财务数字足以确认上述因果关系。',
        'limitation':'未取得政策事件原件或其他传导证据。',
    }
    proposal['numeric_segments']={key:[{'text':value}] for key,value in text.items()}
    proposal['judgment']='支持'
    task['theses']=[];task['tracking']=[]
    task['gaps']=[];task['gap_records']=[]
    agent=ResearchAgent.__new__(ResearchAgent)
    agent._accept_theses(task,proposal['question_ids'],{'theses':[proposal]},None)
    record['unrelated_causal_claim_probe']=dict(
        accepted_by_existing_gate=bool(task['theses']),real_model_generated=False,
        claim_is_intentionally_unsupported=True,persisted_to_research_store=False,
        actual_support_source='retained revenue, net profit and operating cash only',
        missing_support='no monetary-policy event original, transmission evidence or counterfactual',
        gaps=task['gaps'])
    record['knowledge_titles']=[dict(id=item['id'],type=item['type'],title=item['title'])
                                for item in ResearchKnowledge().catalog()]
    assert hashlib.sha256(RETAINED.read_bytes()).hexdigest()==digest
    record['retained_task_unchanged']=True
    target=output/'receipt.json';target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(dict(status=record['status'],receipt=str(target),
        semantic_probe=record['unrelated_causal_claim_probe'],routing=routing),ensure_ascii=False))
    return record


if __name__=='__main__':run()
