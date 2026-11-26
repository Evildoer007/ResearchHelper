"""Replay conflicting wire fields without making supplier or model calls."""
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

from researchhelper.announcement_sources import issuer_from_response, parse_catalogue
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, DuplicateJSONField, charge
from researchhelper.economic_sources import search_response
from researchhelper.engine import ResearchAgent
from researchhelper.etf_sources import parse_snapshot
from researchhelper.evidence import EvidenceCollector
from researchhelper.financial_sources import metadata_response
from researchhelper.index_sources import csi_catalogue_contents
from researchhelper.source_transport import IsolatedEvidenceCollector, _source_worker
from researchhelper.transport import TransportFailure


TOKEN = 'SYNTHETIC_TEST_TOKEN'
FIELD = 'ths_revenue_stock'
POLICY = dict(qps_limit=dict(unit='requests_per_second', default_per_function=100, account_total=100),
              request_data_limit=dict(rules=[dict(data_types=['basicData'], max_per_request=1000)]))


class WorkerConnection:
    def __init__(self, spec):
        self.spec = spec
        self.messages = []

    def recv(self):
        if self.messages and self.messages[-1]['kind'] == 'authorize':
            return dict(kind='authorized', index=self.messages[-1]['index'], allowed=True)
        return self.spec

    def send(self, message):
        self.messages.append(message)

    def close(self):
        pass


def replay_worker(task, spec, started, timeout, record, target, **options):
    connection = WorkerConnection(spec)
    target(connection, started + timeout)
    message = connection.messages[-1]
    record.update(message['record'])
    record.update(transport_isolated=True, network_worker_reaped=True,
                  network_request_started=any(row.get('network_request_started') for row in connection.messages))
    if message['kind'] == 'failure':
        assert 'body' not in message, 'Rejected payload reached the parent'
        raise TransportFailure(message['code'], '数据响应包含重复字段，未采用该响应')
    return message['body']


class ReportModel:
    def reason(self, task, messages, *, phase, delivery=False, **options):
        charge(task['ledger'], 'model_calls', 1, delivery=delivery)
        if phase == 'draft_plan':
            return json.dumps(dict(data_requests=[
                dict(id='revenue_' + period, source='ifind', operation='basic_data_service',
                     parameters=dict(codes='002594.SZ', report_period=period, statement_type='1', indicators=[FIELD]),
                     question_ids=[task['questions'][0]['id']], selection_reason='核对营业收入。')
                for period in ('20260630', '20250630')]))
        return json.dumps(dict(title='比亚迪经营观察', conclusion='分期核对营业收入。',
            logics=[dict(title='收入比较', analysis='按同期口径比较两期数据。',
                         implication='持续关注收入变化。', risk='留意报表口径。')], risks=['关注后续披露。']))


class ProviderJSONIntegrityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='rh-provider-json-')
        self.addCleanup(folder.cleanup)
        fixture = Path.cwd() / 'provider-replay.json'
        self.config = json.loads(fixture.read_text()) if fixture.is_file() else {}
        resources = self.config.get('resources', str(Path(__file__).resolve().parents[1]))
        self.agent = ResearchAgent(ResearchArchive(self.config.get('store') or folder.name),
            resources, offline=False, model=ReportModel(), draft_first=True,
            require_argument_support=True, isolate_sources=True,
            source_credentials=dict(ifind=dict(access_token=TOKEN)))
        self.addCleanup(self.agent.close_runtime)
        for mocked in (patch('socket.socket.connect', side_effect=AssertionError('No external requests')),
                       patch('researchhelper.source_transport.run_isolated', side_effect=replay_worker)):
            mocked.start()
            self.addCleanup(mocked.stop)

    def task(self, question='研究比亚迪002594.SZ。'):
        return self.agent.create(question, research_mode='current')

    def assert_rejected(self, task, error):
        event = task['ledger']['dispatches'][-1]
        self.assertEqual(event['failure_code'], 'duplicate_response_field')
        self.assertEqual(event['status'], '返回结构异常')
        self.assertNotIn('business_code', event)
        self.assertNotIn('response_snapshot_hash', event)
        self.assertNotIn('PRIVATE_DUPLICATE_VALUE', str(task) + str(error))
        self.assertNotIn(TOKEN, str(task) + str(error))

    def test_http_duplicate_code_and_escaped_column_never_select_last_value(self):
        payloads = (
            b'{"errorcode":-4001,"errorcode":0,"tables":[{"table":{"close":[22]}}]}',
            br'{"errorcode":0,"tables":[{"table":{"close":["PRIVATE_DUPLICATE_VALUE"],"cl\u006fse":[22]}}]}',
        )
        params = dict(codes='002594.SZ', indicators='close', startdate='2026-07-20', enddate='2026-07-21',
                      functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        for cls, module in ((EvidenceCollector, 'evidence'), (IsolatedEvidenceCollector, 'source_transport')):
            for raw in payloads:
                with self.subTest(collector=cls.__name__, top_level=raw is payloads[0]):
                    task = self.task()
                    with patch(f'researchhelper.{module}.open_verified_https', return_value=io.BytesIO(raw)) as request:
                        with self.assertRaises(ContractError) as rejected:
                            cls(offline=False).external(task, 'ifind', 'cmd_history_quotation', params, dict(access_token=TOKEN))
                    request.assert_called_once()
                    self.assertEqual(task['ledger']['used']['data_requests'], 1)
                    self.assertEqual(len(task['ledger']['dispatches']), 1)
                    self.assertEqual(task['source_records'], [])
                    self.assert_rejected(task, rejected.exception)

    def test_auth_and_metadata_record_failure_without_caching_or_an_extra_send(self):
        for cls, module in ((EvidenceCollector, 'evidence'), (IsolatedEvidenceCollector, 'source_transport')):
            for refresh in (False, True):
                with self.subTest(collector=cls.__name__, refresh=refresh):
                    task = self.task()
                    collector = cls(offline=False)
                    raw = b'{"errorcode":0,"data":{"access_token":"PRIVATE_DUPLICATE_VALUE","access_token":"another"}}'
                    credentials = {'refresh_token' if refresh else 'access_token': TOKEN}
                    with patch(f'researchhelper.{module}.open_verified_https', return_value=io.BytesIO(raw)) as request:
                        with self.assertRaises(ContractError) as rejected:
                            collector.external(task, 'ifind', 'get_limit_policy', {}, credentials)
                    request.assert_called_once()
                    self.assertIsNone(collector._ifind_access_cache)
                    self.assertEqual(task['ledger']['used']['data_requests'], 1)
                    self.assert_rejected(task, rejected.exception)

    def test_nested_and_public_json_boundaries_preserve_ambiguity(self):
        date = '2026-07-22'
        issuer = b'[{"c":"00700","n":"fixture","i":123}]'
        params = dict(channel='hkex', code='00700.HK', start_date='2026-07-20', end_date='2026-07-21',
                      page='1', keyword='')
        cases = (
            (metadata_response, ('get_indicator_params', {}, dict(code=1, data='[{"name":"old","name":"new"}]'))),
            (search_response, (dict(text='fixture\n```json\n[{"unit":"元","unit":"亿元"}]\n```'),)),
            (issuer_from_response, (b'[{"c":"00700","i":1,"i":2}]', 'hkex', '00700.HK')),
            (parse_catalogue, (json.dumps(dict(result='[{"FILE_LINK":"old","FILE_LINK":"new"}]')).encode(),
                               issuer, params, date)),
            (parse_snapshot, (b'{"result":[{"NAV":"100","NAV":"200"}]}', '510300.SH', date)),
            (csi_catalogue_contents, (b'{"code":"500","code":"200","success":true,"data":{}}', '000300.SH')),
        )
        for parser, args in cases:
            with self.subTest(parser=parser.__name__), self.assertRaises(DuplicateJSONField):
                parser(*args)

    def test_embedded_search_never_creates_a_successful_metadata_snapshot(self):
        task = self.task('核对中国CPI实际指标。')
        collector = IsolatedEvidenceCollector(offline=False)
        with patch('researchhelper.source_transport.open_verified_https', return_value=io.BytesIO(json.dumps(POLICY).encode())):
            task['source_records'].append(collector.external(task, 'ifind', 'get_limit_policy', {}, dict(access_token=TOKEN)))
        raw = b'fixture\n```json\n[{"displayId":"M000000001","displayId":"M000000002"}]\n```'
        with patch('researchhelper.source_transport.open_verified_https', return_value=io.BytesIO(raw)) as request:
            with self.assertRaises(DuplicateJSONField) as rejected:
                collector.external(task, 'ifind', 'search_edb', dict(query='CPI'), dict(access_token=TOKEN))
        request.assert_called_once()
        self.assertEqual(task['ledger']['used']['data_requests'], 2)
        self.assertEqual([row['operation'] for row in task['source_records']], ['get_limit_policy'])
        self.assert_rejected(task, rejected.exception)

    def test_sdk_worker_does_not_accept_an_empty_frame_after_json_rejection(self):
        import requests

        def fake_query(**params):
            try:
                requests.post('https://sdk.invalid/query').json()
            except ContractError:
                return SimpleNamespace(to_dict=lambda **options: [])
            raise AssertionError('Ambiguous JSON was decoded by the SDK')

        response = requests.Response()
        response.status_code = 200
        response._content = b'{"code":0,"data":[{"close":[11],"close":[22]}]}'
        sdk = SimpleNamespace(pro_api=lambda *args, **kwargs: SimpleNamespace(fund_daily=fake_query))
        connection = WorkerConnection(dict(kind='tinyshare', operation='fund_daily', parameters={}, token=TOKEN))
        with (patch.dict('sys.modules', {'tinyshare': sdk}), patch.dict('os.environ', {}),
              patch.object(requests.Session, 'send', return_value=response) as send):
            _source_worker(connection, time.monotonic() + 2)
        send.assert_called_once()
        result = connection.messages[-1]
        self.assertEqual((result['kind'], result['code']), ('failure', 'duplicate_response_field'))
        self.assertNotIn('body', result)
        self.assertEqual(result['record']['sdk_wire_results'][0]['state'], '返回结构异常')

    def test_one_sentence_report_saves_only_the_independent_period(self):
        def wire(request, **options):
            operation = urlsplit(request.full_url).path.rsplit('/', 1)[-1]
            if operation == 'get_limit_policy':
                return io.BytesIO(json.dumps(POLICY).encode())
            self.assertEqual(operation, 'basic_data_service')
            params = json.loads(request.data)
            period = params['indipara'][0]['indiparams'][0]
            reflect = dict(function=FIELD, params=[dict(name='FDREPORT', value=period), dict(name='FL', value='1')])
            response = dict(errorcode=0, inputParams=dict(params=[reflect]),
                            tables=[dict(thscode=params['codes'], table={FIELD: [1234000000]})])
            raw = json.dumps(response).encode()
            if period == '20260630':
                needle = ('"' + FIELD + '": [1234000000]').encode()
                raw = raw.replace(needle, b'"PRIVATE_DUPLICATE_VALUE": 1, "PRIVATE_DUPLICATE_VALUE": 2, ' + needle)
            return io.BytesIO(raw)

        task_id = self.config.get('task_id')
        task = (self.agent.archive.get(task_id) if task_id else
                self.task('研究比亚迪002594.SZ，比较2026和2025年上半年营业收入并生成报告。'))
        with patch('researchhelper.source_transport.open_verified_https', side_effect=wire) as request:
            result = self.agent.start(task['id'])
        self.assertEqual(result['status'], '部分完成')
        self.assertEqual(request.call_count, 3)
        self.assertEqual(result['ledger']['used']['data_requests'], 3)
        self.assertEqual(result['ledger']['used']['model_calls'], 2)
        failed = [row for row in result['ledger']['dispatches'] if row.get('failure_code') == 'duplicate_response_field']
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]['status'], '返回结构异常')
        sources = [row for row in result['source_records'] if row['operation'] == 'basic_data_service']
        self.assertEqual([row['parameters']['indipara'][0]['indiparams'][0] for row in sources], ['20250630'])
        self.assertEqual({row['period'] for row in result['draft_report']['facts']}, {'20250630'})
        self.assertNotIn('PRIVATE_DUPLICATE_VALUE', str(result))
        report = self.agent.archive.report(task['id'], result['version'])
        self.assertIn('策略逻辑一', report['html'])
        self.assertIn('营业收入', report['html'])
        self.assertNotIn('PRIVATE_DUPLICATE_VALUE', report['html'])
        if self.config.get('store'):
            Path('replay-task.json').write_text(json.dumps(dict(task_id=task['id'], version=result['version'])))
