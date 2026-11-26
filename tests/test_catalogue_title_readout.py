"""Ordinary EDB reads with an explicit catalogue title or a plain industry name."""
from copy import deepcopy
import json
import socket
from unittest.mock import patch

import pytest

from core.session import Session
from researchhelper.draft_workflow import direct_readout
from tests.market_observation_fixtures import simulated_ifind_response
from tests.test_stated_economic_readout import CODE, CUTOFF, economic_replay_transport, retained_economic_inputs


@pytest.mark.parametrize('case', ['retained', 'industry', 'ambiguous', 'wrong_basis', 'wrong_frequency'])
def test_directory_title_read_through_session(tmp_path, case):
    retained = case == 'retained'
    title = 'CPI:当月同比' if retained else '中国汽车产量'
    question = ('查询2026年9月指标【CPI：当月同比】的读数。' if retained
                else '查询2026年9月中国汽车产量的月度值。')
    inputs = retained_economic_inputs()
    acquired = []
    calls = []
    replay = economic_replay_transport(inputs, calls)
    session = Session(tmp_path, allow_network=True, source_credentials={})

    def transport(task, source, operation, params, credentials):
        if operation == 'get_limit_policy':
            return replay(task, source, operation, params, credentials)
        if operation == 'search_edb':
            assert params['query'] == ('CPI：当月同比' if retained else title)
            if retained:
                # Different query text, original directory response bytes and series values.
                snapshot = deepcopy(inputs[0][operation]['snapshot'])
            else:
                row = dict(country='中国', dataSource='测试目录', displayId='M000123456', id='123456',
                           indexName='汽车:产量:当月值', freq='M', unit='万辆', endDate='20260930',
                           startDate='20200131', node='工业')
                if case == 'wrong_basis':
                    row['indexName'] = '汽车:产量:累计值'
                if case == 'wrong_frequency':
                    row['freq'] = 'Q'
                rows = [row, dict(row, displayId='M000123457')] if case == 'ambiguous' else [row]
                snapshot = dict(text='```json\n'+json.dumps(rows, ensure_ascii=False)+'\n```')
        else:
            assert operation == 'edb_service'
            acquired.append(params)
            if retained:
                return replay(task, source, operation, params, credentials)
            snapshot = dict(errorcode=0, inputParams=dict(zb_str='M000123456', sdate='20260901', edate='20260930'),
                            tables=[dict(id=['M000123456'], time=['2026-09-30'], value=['12.5'])])
        return simulated_ifind_response(task, operation, params, snapshot)

    try:
        with patch('researchhelper.engine.now', return_value=CUTOFF):
            task = session.agent.create(question, research_mode='current')
        assert direct_readout(task)[0] == 'economic'
        task['synthetic'] = True
        session.agent.archive.save(task)
        with (patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline only')),
              patch.object(session.agent.collector, 'external', transport)):
            done = session.draft(task['id'])
        assert done.get('delivery') and done['ledger']['used']['model_calls'] == 0
        facts = done['draft_report']['facts']
        if retained or case == 'industry':
            code = CODE if retained else 'M000123456'
            assert acquired == [dict(indicators=code, startdate='2026-09-01', enddate='2026-09-30')]
            assert [(row['code'], row['period'], row['raw_value'], row['unit']) for row in facts] == [
                (code, '2026-09-30', '0.9' if retained else '12.5', '%' if retained else '万辆')]
        else:
            assert acquired == [] and facts == []
    finally:
        session.agent.close_runtime()


def test_natural_industry_scope_in_an_open_question(tmp_path):
    from researchhelper.economic_sources import research_economic_searches

    session = Session(tmp_path, allow_network=True, source_credentials={})
    try:
        with patch('researchhelper.engine.now', return_value=CUTOFF):
            task = session.agent.create('查询2026年1月至9月中国汽车产量的月度值，并分析汽车行业的产能变化。',
                                        research_mode='current')
        assert direct_readout(task) is None
        searches = research_economic_searches(task)
        assert [row['parameters'] for row in searches] == [dict(query='中国汽车产量')]
    finally:
        session.agent.close_runtime()
