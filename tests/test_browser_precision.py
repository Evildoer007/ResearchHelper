"""Exact observations through the shared report designer and browser view."""
import json

from lxml import html

from researchhelper.contracts import fingerprint
from researchhelper.drafts import compose, source_binding
from researchhelper.report_designer import design
from tests import ROOT
from tests.test_client_report_audience import fixture

VALUES = (10_000_000_000_000_003, 10_000_000_000_000_004, 10_000_000_000_000_005)


def precision_content(values=VALUES):
    task, _ = fixture()
    draft = task['draft_report']
    facts = [
        dict(row, label='成交量', field='volume', series_basis='volume', unit='股',
             value=value, raw_value=value, source='ifind', source_operation='cmd_history_quotation',
             locator={'table': 0, 'row': index, 'field': 'volume'})
        for index, (row, value) in enumerate(zip(draft['facts'][6::2], values))
    ]
    dates = [row['period'] for row in facts]
    snapshot = {'errorcode': 0, 'tables': [
        {'thscode': '600519.SH', 'time': dates, 'table': {'volume': list(values)}}]}
    task['source_records'][1].update(kind='api', operation='cmd_history_quotation', status='成功待核验',
        snapshot=snapshot, response_hash=fingerprint(snapshot),
        parameters={'codes': '600519.SH', 'indicators': 'volume', 'startdate': dates[0],
                    'enddate': dates[-1], 'functionpara': {'Currency': 'RMB', 'CPS': '1', 'Fill': 'Omit'}})
    draft.update(facts=facts, calculations=[], logics=[dict(draft['logics'][0], title='成交量观察',
        analysis='按原始日期核对成交量。', data_refs=[row['id'] for row in facts],
        calculation_refs=[], implication='', risk='')], source_binding=source_binding(task))
    return compose(task)


def check_exact_report(text):
    tree = html.document_fromstring(text)
    document = json.loads(tree.get_element_by_id('rh-publication-document').text)
    assert [row[3] for row in document['dataset']['rows']] == list(VALUES)
    assert not [block for block in document['blocks'] if block['kind'] == 'chart']
    tables = tree.xpath('//main//table')
    assert any(all(str(value) in table.text_content().replace(',', '') for value in VALUES)
               for table in tables)
    assert document['figure_coverage']['logic-1']['figures'] == 0


def test_unsafe_absolute_plot_falls_back_to_exact_data_in_both_reports():
    content = precision_content()
    for profile in ('standard', 'detailed'):
        check_exact_report(design(content, ROOT, profile=profile)['html'])


def test_same_series_at_safe_precision_still_plots():
    content = precision_content((100, 101, 102))
    for profile in ('standard', 'detailed'):
        blocks = design(content, ROOT, profile=profile)['document']['blocks']
        assert any(block['kind'] == 'chart' and block['spec']['field'] == 'volume' for block in blocks)
