"""Rebuild a financial review checkpoint from synthetic originals, offline."""
from functools import lru_cache
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import METHOD_REGISTRY
from researchhelper.engine import ResearchAgent


ENTITY = '合成财务验收主体'
PERIOD = '2026年1月1日至2026年6月30日'
QUESTION = '合成财务验收主体本期盈利与经营现金流的关系如何，能否据此判断回款质量？'
METHODS = {'profit_margin': 'net_profit_margin_v1', 'cash_conversion': 'operating_cash_profit_ratio_v1'}
AMOUNTS = {'revenue': ('合并利润表营业收入', '84000000000.4800'),
           'net_profit': ('合并利润表净利润', '42630000000.78'),
           'operating_cash_flow': ('合并现金流量表经营活动产生的现金流量净额', '65462628001.20')}


def _financial_checkpoint(agent):
    task = agent.create(QUESTION, entry='个股', intent='解释变化', objects=[ENTITY],
        cutoff='2026-10-02T18:00:00+08:00', questions=[dict(id='q1', question=QUESTION,
            kind='mechanism', method_needs=list(METHODS.values()))])
    task['synthetic'] = True
    agent.archive.save(task)

    def retain(ident, paragraph, *, appendix=''):
        text = f'{ENTITY}，{PERIOD}。{paragraph}。发布于2026-10-01。'
        material = agent.add_material(task['id'], ident + '.txt', (text + appendix).encode())['materials'][-1]
        anchor = dict(kind='material', artifact_id=material['id'], snapshot_hash=material['snapshot_hash'],
                      part='段落1', quote=text)
        return dict(id=ident, artifact_id=material['id'], object=ENTITY, record_locator={'part': '段落1'},
            text=text, period=PERIOD, nature='已实现事实', unit='不适用', question_ids=['q1'],
            published_at='2026-10-01', publication=dict(at='2026-10-01', precision='日', timezone='Asia/Shanghai',
                certainty='已确认', basis='合成原件所载发布日期', basis_ref=anchor),
            review=dict(question_ids=['q1'], allowed_natures=['已实现事实'],
                        conditions=['仅适用于合成原件的主体和期间'], reason='已定位原文和日期'))

    specifications = []
    for metric, (label, amount) in AMOUNTS.items():
        spec = retain(metric, f'人民币，企业会计准则，{label}{amount}元')
        context = dict(entity_id=ENTITY, currency='CNY', period=PERIOD, accounting_scope='consolidated',
                       accounting_standard='CAS', metric=metric)
        spec.update(raw_value=float(amount), value=float(amount), raw_token=amount, raw_unit='元', unit='元',
            conversion_factor=1, calculation_context=context,
            calculation_context_basis={field: spec['publication']['basis_ref'] for field in context})
        specifications.append(spec)
    specifications.append(retain('receivables', '附注披露部分应收账款逾期，未说明后续回款情况',
        appendix='\n\n附录仅为合成原件阅读测试说明。'))

    inputs = {'profit_margin': ('revenue', 'net_profit'), 'cash_conversion': ('operating_cash_flow', 'net_profit')}
    calculations = [dict(id=ident, method=method, question_ids=['q1'], inputs={key: key for key in inputs[ident]},
        parameters=dict(METHOD_REGISTRY[method]['parameters'], amount_unit='元', period=PERIOD,
                        accounting_scope='consolidated', accounting_standard='CAS'))
        for ident, method in METHODS.items()]

    def number(source, ref, digits):
        return dict(source=source, ref=ref, path='value', format='signed', digits=digits)

    answer = dict(refs=list(AMOUNTS), calculation_refs=list(METHODS), numeric_segments={'answer': [
        {'text': f'报告期间为{PERIOD}。营业收入为'}, number('evidence', 'revenue', 4),
        {'text': '，报表净利润为'}, number('evidence', 'net_profit', 4),
        {'text': '，经营现金流净额为'}, number('evidence', 'operating_cash_flow', 4),
        {'text': '。净利润率为'}, number('calculation', 'profit_margin', 2),
        {'text': '，经营现金流净利润比为'}, number('calculation', 'cash_conversion', 2),
        {'text': '。两项比率描述本期数据，尚不能判断应收账款是否收回。'}]})
    thesis = dict(question_ids=['q1'], judgment='支持', support=list(AMOUNTS), counter=['receivables'],
        calculation_refs=list(METHODS), numeric_segments={
            'title': [{'text': '本期经营现金流净额高于报表净利润'}],
            'investment_meaning': [{'text': '本期数据支持现金流覆盖报表净利润的判断。回款仍需单独核对。'}],
            'limitation': [{'text': '现金流分类、账龄及后续回款需要结合原件验证。'}]})
    catalog = agent.source_catalog(task['id'])
    retained = agent.archive.get(task['id'])['materials']
    requests = [dict(artifact_id=item['id'], snapshot_hash=item['snapshot_hash'], queries=[],
        parts=[dict(part='段落1', start=0, end=len(item['snapshot']['parts'][0]['text']))],
        reason='核对合成财务原文', question_ids=['q1']) for item in retained]
    reading = agent.read_materials(task['id'], requests, catalog['binding'])
    final = agent.start(task['id'], dict(evidence_specs=specifications, calculations=calculations,
        answers={'q1': answer}, theses=[thesis], reading_hash=reading['reading_hash']))
    assert final['status'] == '已完成', (final['error'], final['gaps'], final['rejected_evidence'])
    assert len(final['calculations']) == 2 and len(final['theses']) == 1
    assert final['ledger']['used']['data_requests'] == final['ledger']['used']['model_calls'] == 0
    return final, agent.export(final['id'])['content']


@lru_cache(maxsize=1)
def _frozen_fixture():
    directory = ROOT / 'result/acceptance'
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='argument-review-fixture-', dir=directory) as store, \
            patch('socket.socket.connect', side_effect=AssertionError('External network forbidden')):
        agent = ResearchAgent(ResearchArchive(store), ROOT, offline=True)
        try:
            return json.dumps(_financial_checkpoint(agent), ensure_ascii=False)
        finally:
            agent.close_runtime()


def financial_review_fixture():
    """Return independent copies of the task checkpoint and frozen report."""
    return json.loads(_frozen_fixture())
