"""Isolated adversarial checks of claim support, not model research results.

All proposals use the same retained company evidence. Cross-domain proposals
are deliberately invalid; accepting one exposes a gate weakness, not proof
that the corresponding research task has run successfully.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent


RETAINED = ROOT / 'result/acceptance/one-sentence-research-jrfhac2u/task.json'
REGRESSION_MODULES = (
    'test_object_series_routing', 'test_dependent_calculation_inputs',
    'test_raw_query_provenance', 'test_financial_reference_item_review',
    'test_responses_schema_transport',
)
CASES = (
    ('descriptive_control', '本期利润与经营现金流可以用于计算对应比率',
     '仅陈述已有核验数值和登记计算的用途。',
     '未据此判断未来回报或解释因果。', True),
    ('unrelated_event_causation', '美联储降息已经直接导致公司利润改善',
     '公司收入、利润和现金流足以确认上述政策传导。',
     '没有取得政策事件原件、传导证据或竞争解释。', False),
    ('cash_flow_composition', '经营现金流改善完全来自主营业务回款改善',
     '可以排除营运资金变化、金融子公司存款和其他现金流因素。',
     '没有取得现金流拆解、营运资金明细或可比期间数据。', False),
    ('single_company_to_industry', '公司收入足以证明整个白酒行业需求全面复苏',
     '整个行业的需求和盈利均已得到验证。',
     '没有取得行业样本、权重、价格、销量或渠道库存。', False),
    ('unrelated_etf_ranking', '现有公司报表足以确认沪深证券ETF未来收益更高',
     '该基金可以优于其他同类产品。',
     '没有取得基金身份、持仓、费用、净值或总回报。', False),
    ('valuation_and_buy_signal', '公司估值足够便宜并已形成明确买入机会',
     '现有本期财务比率足以支持估值与买入判断。',
     '没有取得价格、估值、增长预期、可比样本或折现假设。', False),
)


def probe(original, title, meaning, limitation, *, override=None):
    task = copy.deepcopy(original)
    proposal = copy.deepcopy(task['host_result']['theses'][0])
    proposal['numeric_segments'] = {
        key: [{'text': text}]
        for key, text in (('title', title), ('investment_meaning', meaning),
                          ('limitation', limitation))
    }
    proposal['judgment'] = '支持'
    if override:
        proposal.update(override)
    task.update(theses=[], tracking=[], gaps=[], gap_records=[])
    agent = ResearchAgent.__new__(ResearchAgent)
    error = None
    try:
        agent._accept_theses(task, proposal['question_ids'], {'theses': [proposal]}, None)
    except ContractError as exception:
        error = str(exception)
    return dict(accepted=bool(task['theses']), gaps=task['gaps'], contract_error=error)


def run():
    folder = Path(tempfile.mkdtemp(prefix='current-claim-support-', dir=ROOT / 'result/acceptance'))
    original_bytes = RETAINED.read_bytes()
    original = json.loads(original_bytes)
    results = []
    with patch('socket.socket.connect', side_effect=AssertionError('Audit forbids networking')):
        regression_log = io.StringIO()
        regression = unittest.TextTestRunner(stream=regression_log, verbosity=1).run(
            unittest.defaultTestLoader.loadTestsFromNames(
                ['tests.' + name for name in REGRESSION_MODULES]))
        (folder / 'extraction-regressions.log').write_text(regression_log.getvalue())
        for name, title, meaning, limitation, expected in CASES:
            result = probe(original, title, meaning, limitation)
            results.append(dict(case=name, title=title, expected_acceptance=expected,
                                expectation_met=result['accepted'] is expected, **result))
        control = CASES[0]
        identity_controls = []
        for name, override in (
            ('cross_task', {'task_id': 'rh_foreign_audit_only'}),
            ('cross_version', {'research_version': original['version'] + 1}),
            ('missing_support', {'support': ['not_an_existing_evidence'], 'counter': []}),
            ('missing_calculation', {'calculation_refs': ['not_an_existing_calculation']}),
        ):
            result = probe(original, control[1], control[2], control[3], override=override)
            identity_controls.append(dict(case=name, expected_acceptance=False,
                                          expectation_met=not result['accepted'], **result))
    assert RETAINED.read_bytes() == original_bytes
    record = dict(
        status='audit_complete', semantic_gate_passed=all(item['expectation_met'] for item in results),
        identity_controls_passed=all(item['expectation_met'] for item in identity_controls),
        extraction_regressions=dict(passed=regression.wasSuccessful(), run=regression.testsRun,
                                    failures=len(regression.failures), errors=len(regression.errors),
                                    skipped=len(regression.skipped), modules=list(REGRESSION_MODULES)),
        cases=results, identity_controls=identity_controls,
        findings=[item['case'] for item in results if not item['expectation_met']],
        real_model_calls=0, real_supplier_calls=0, customer_report_created=False,
        real_research_store_written=False, original_task_unchanged=True,
        retained_task_sha256=hashlib.sha256(original_bytes).hexdigest(),
        scope='Claim gate replay on one retained financial task; not eight end-to-end studies',
    )
    target = folder / 'receipt.json'
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(receipt=str(target), semantic_gate_passed=record['semantic_gate_passed'],
                          identity_controls_passed=record['identity_controls_passed'],
                          extraction_regressions=record['extraction_regressions'],
                          findings=record['findings']), ensure_ascii=False))
    return record


if __name__ == '__main__':
    run()
