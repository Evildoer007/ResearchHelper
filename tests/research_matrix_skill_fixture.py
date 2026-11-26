"""Fixed SYNTHETIC branch expectations, not a financial-quality evaluation."""
import importlib.util
import json
import math
from pathlib import Path
import sys

CASE_EXPECTATIONS = {
    'mechanism': ('产业链', '解释变化', ['支持'], 4),
    'etf': ('ETF', '比较对象', ['支持'], 2),
    'value': ('个股', '发现投资机会', ['支持'], 5),
    'index': ('指数', '综合研究', ['支持'], 2),
    'sector': ('行业', '综合研究', ['支持'], 2),
    'event': ('事件', '验证观点', ['反对'], 2),
    'macro': ('宏观', '解释变化', ['证据不足'], 2),
    # Fee questions remain direct comparisons; only the separate policy
    # mechanism contributes a thesis. Two inputs must not force two logics.
    'mixed': ('混合', '比较对象', ['反对'], 4),
}


def projection(content):
    fields = {'questions': ('question', 'kind', 'required', 'status', 'answer', 'refs', 'calculation_refs'),
              'theses': ('title', 'judgment', 'support', 'counter', 'investment_meaning', 'limitation', 'calculation_refs'),
              'calculations': ('method', 'inputs', 'parameters', 'output', 'limitations'),
              'evidence': ('id', 'object', 'text', 'value', 'unit', 'nature', 'source'),
              'tracking': ('indicator', 'source', 'window', 'condition', 'benchmark')}
    return {'entry': content['entry'], 'intent': content['intent'], 'synthetic': content['synthetic'],
            'limitations': content['limitations'],
            **{group: [{key: item[key] for key in selected if key in item} for item in content[group]]
               for group, selected in fields.items()}}


def verify_case(key, task, content, paper):
    entry, intent, judgments, evidence_count = CASE_EXPECTATIONS[key]
    assert task['status'] == '已完成' and task['synthetic'] is True
    assert task['ledger']['used']['model_calls'] == task['ledger']['used']['data_requests'] == 0
    assert content['entry'] == entry and content['intent'] == intent and content['synthetic'] is True
    assert all(question['status'] == '已回答' for question in content['questions'] if question['required'])
    assert [thesis['judgment'] for thesis in content['theses']] == judgments, key + ' judgment routing'
    assert len(content['evidence']) == evidence_count
    from html.parser import HTMLParser
    from researchhelper.reports import READER_VERSION

    class ReaderIdentity(HTMLParser):
        def __init__(self):
            super().__init__()
            self.versions = []

        def handle_starttag(self, tag, attrs):
            if tag == 'html':
                self.versions.extend(value for name, value in attrs if name == 'data-reader-version')

    identity = ReaderIdentity()
    identity.feed(paper)
    assert '合成验收' in paper and identity.versions == [READER_VERSION]
    result = projection(content)
    if key in ('etf', 'mixed'):
        fees = [item for item in content['calculations'] if item['method'] == 'fee_difference_v1']
        assert len(fees) == 1 and math.isclose(fees[0]['output']['value'], .20 - .60, abs_tol=1e-10)
        assert fees[0]['output']['unit'] == '百分点/年'
        assert '实际收益差' in paper and ('不是实际收益差' in paper or '不解释为实际收益差' in paper)
    if key == 'value':
        from lxml import html

        calculations = [item for item in content['calculations'] if item['method'] == 'residual_present_value_v1']
        values = [item['output']['value'] for item in calculations]
        expected = [(120 - 20 - 5) / (10 * 1.1), (100 - 20 - 5) / (10 * 1.1)]
        assert len(values) == 2 and all(math.isclose(value, correct, abs_tol=1e-10)
                                       for value, correct in zip(values, expected))
        tree = html.document_fromstring(paper)
        document = json.loads(tree.get_element_by_id('rh-publication-document').text)
        figure, = (block['spec'] for block in document['blocks'] if block['kind'] == 'chart')
        data = figure['auto_visual']['data']
        price_id = calculations[0]['input_bindings']['price']
        price = next(row['value'] for row in content['evidence'] if row['id'] == price_id)
        assert data['labels'] == ['基准现值', '压力现值', '参考价格']
        assert data['unit'] == '元/股' and data['values'] == [*values, price]
        assert figure['calculation_refs'] == [item['id'] for item in calculations]
        result['valuation_figure'] = data
    if key == 'macro':
        assert '不可识别' in paper and '竞争解释' in paper
    if key == 'event':
        assert '做空' in paper and '不满足资格' in paper
    return result


def run():
    installation, project = (Path(value).resolve() for value in sys.argv[1:])
    spec = importlib.util.spec_from_file_location('installed_skill', installation / 'core/session.py')
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    session = host.Session(project)
    from researchhelper import engine, reports
    from researchhelper.contracts import fingerprint
    from researchhelper.report_designer import read_presentation_document
    assert Path(engine.__file__).is_relative_to(installation)
    assert Path(reports.__file__).is_relative_to(installation)
    assert session.agent.model is None
    results = {}
    reopened = host.Session(project)
    try:
        for key in CASE_EXPECTATIONS:
            task = session.agent.create_case(key)
            done = session.agent.start(task['id'])
            report = session.agent.export(task['id'])
            result = verify_case(key, done, report['content'], report['html'])
            assert reopened.agent.export(task['id']) == report
            profiles = {}
            for profile in ('standard', 'detailed'):
                target = session.export(task['id'], profile=profile)
                tree, document = read_presentation_document(target.read_text(encoding='utf-8'))
                assert tree.find('body').get('data-report-variant') == document['profile'] == profile
                assert document['content_hash'] == report['digest']
                assert reopened.export(task['id'], profile=profile) == target
                profiles[profile] = fingerprint(document['dataset'])
            results[key] = {'projection': result, 'profiles': profiles, 'status': done['status']}
    finally:
        session.agent.close_runtime()
        reopened.agent.close_runtime()
    print(json.dumps({'status': 'passed', 'cases': results, 'installed_engine': True,
                      'model_calls': 0, 'data_requests': 0}, ensure_ascii=False))


if __name__ == '__main__':
    run()
