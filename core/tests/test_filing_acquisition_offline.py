"""Announcement acquisition contracts. All responses and PDFs are synthetic."""

import copy
import io
import json
import socket
import unittest
from unittest.mock import MagicMock, patch

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from researchhelper.contracts import ContractError, budget, fingerprint
from researchhelper.evidence import (
    CAPABILITIES, FILING_OPERATIONS, FILING_REQUEST_REGISTRY, IFIND_REPORT_FIELDS,
    EvidenceCollector, _PublicFilingConnection, _download_filing_pdf, candidate_evidence,
    snapshot_record, validate_filing_catalog_binding, validate_filing_original_request,
    validate_ifind_report_request, validate_provenance,
)


def synthetic_pdf(text=True, *, statement="Synthetic filing only."):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    if text:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 20 250 Td (' + statement.encode('ascii') + b') Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class FakeResponse(io.BytesIO):
    def __init__(self, payload, status=200, headers=None):
        super().__init__(payload)
        self.status, self.headers = status, headers or {}

    def getheader(self, key):
        return self.headers.get(key)


class FilingAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "rh_filing_fixture", "version": 1, "batch": 1, "input_hash": "synthetic",
                     "cutoff": "2026-10-02T18:00:00+08:00", "objects": ["600000.SH"], "ledger": budget(),
                     "source_records": [], "materials": [], "questions": [{"id": "q1", "required": True}]}
        self.params = {"codes": "600000.SH", "functionpara": {"reportType": "901"}, "beginrDate": "2026-09-01",
                       "endrDate": "2026-10-02", "outputpara": ','.join(field + ':Y' for field in IFIND_REPORT_FIELDS)}
        self.row = {"reportDate": "2026-09-30", "thscode": "600000.SH", "secName": "合成主体",
                    "ctime": "2026-09-29T17:00:00+08:00", "reportTitle": "合成公告，不是真实数据",
                    "pdfURL": "https://static.cninfo.com.cn/finalpage/fixture.pdf", "seq": "fixture_1"}
        self.collector = EvidenceCollector(offline=False)
        self.addCleanup(patch.stopall)
        patch('socket.socket.connect', side_effect=AssertionError('No real networking')).start()
        patch('socket.getaddrinfo', side_effect=AssertionError('No real DNS')).start()

    def catalog(self, payload=None):
        record = snapshot_record(self.task, 'ifind', 'report_query', self.params,
                                 payload if payload is not None else {"errorcode": 0, "tables": [copy.deepcopy(self.row)]})
        self.task['source_records'].append(record)
        return record

    def selection(self, record):
        return {"catalog_id": record['id'], "source_id": 'fixture_1'}

    def transport(self, payload=None, status=200, headers=None):
        connection = MagicMock()
        connection.getresponse.return_value = FakeResponse(synthetic_pdf() if payload is None else payload, status, headers)
        factory = patch('researchhelper.evidence._PublicFilingConnection', return_value=connection).start()
        return factory, connection

    def test_registered_internal_operation_is_not_a_vendor_endpoint(self):
        self.assertEqual(FILING_OPERATIONS, {'report_query', 'filing_original'})
        self.assertNotIn('filing_original', CAPABILITIES['ifind']['endpoints'])
        self.assertEqual(set(FILING_REQUEST_REGISTRY), {'ifind.report_query', 'ifind.filing_original'})
        self.assertEqual(FILING_REQUEST_REGISTRY['ifind.report_query']['permission'], '未验收')
        self.assertEqual(validate_ifind_report_request(self.task, self.params), ['2026-09-01', '2026-10-02'])

    def test_request_rejects_undeclared_scope_fields_dates_before_auth_and_charge(self):
        cases = [dict(self.params, codes=code) for code in ('', '600000.SH,000001.SZ', '600000.BJ', '000001.SZ')]
        cases += [dict(self.params, **changes) for changes in (
            {'beginrDate': '20260901'}, {'beginrDate': '2026-09-31'}, {'endrDate': '2026-10-03'},
            {'beginrDate': '2026-10-03'}, {'outputpara': 'reportDate:Y'}, {'functionpara': {'reportType': '902'}},
            {'functionpara': {'reportType': 901}}, {'functionpara': {'reportType': '901', 'mode': 'allAStock'}},
            {'url': self.row['pdfURL']})]
        with patch('urllib.request.urlopen') as dispatch:
            for params in cases:
                with self.subTest(params=params), self.assertRaises(ContractError):
                    self.collector.external(self.task, 'ifind', 'report_query', params, {'refresh_token': 'synthetic'})
            dispatch.assert_not_called()
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_ifind_dispatch_retains_original_named_rows_and_query_identity(self):
        raw = {"errorcode": 0, "tables": [copy.deepcopy(self.row)]}
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(raw).encode())) as dispatch:
            record = self.collector.external(self.task, 'ifind', 'report_query', self.params, {'access_token': 'synthetic'})
        self.assertEqual(json.loads(dispatch.call_args.args[0].data), self.params)
        self.assertEqual(record['snapshot'], raw)
        self.assertEqual(record['catalog_coverage']['returned_rows'], 1)
        self.assertEqual(record['catalog_coverage']['completeness'], '未证实')
        entry = record['catalog_entries'][0]
        self.assertEqual(entry['report_date'], '2026-09-30')
        self.assertEqual(entry['published_at'], '2026-09-29T17:00:00+08:00')
        self.assertEqual(entry['catalog_ref']['field_mapping']['ctime'], ['tables', 0, 'ctime'])
        self.assertEqual(entry['source_check'], '待核验')
        self.assertEqual(self.task['ledger']['used']['data_requests'], 1)

    def test_named_rows_and_column_tables_no_positional_guess(self):
        columns = {key: [value] for key, value in self.row.items()}
        payloads = [{"errorcode": 0, "data": [self.row]}, {"errorcode": 0, "tables": [{"table": columns}]},
                    {"errorcode": 0, "tables": [{"thscode": self.row['thscode'], 'table': {k: v for k, v in columns.items() if k != 'thscode'}}]}]
        for payload in payloads:
            with self.subTest(payload=payload):
                record = self.catalog(payload)
                self.assertEqual(record['catalog_status'], '成功待核验')
                self.assertEqual(record['catalog_entries'][0]['catalog_ref']['original_fields'], self.row)

    def test_unknown_incomplete_and_business_error_preserve_raw_not_empty(self):
        cases = [{"errorcode": 0}, {"errorcode": 0, "tables": [list(self.row.values())]},
                 {"errorcode": 0, "tables": [{'seq': 'fixture_1'}]},
                 {"errorcode": 0, "tables": [{"table": {k: [v] for k, v in self.row.items() if k != 'ctime'}}]},
                 {"errorcode": 0, "tables": [self.row], 'data': []},
                 {"errorcode": 0, "tables": [self.row, self.row]}, {"errorcode": -1, "tables": []}]
        for payload in cases:
            with self.subTest(payload=payload):
                record = self.catalog(payload)
                self.assertEqual(record['snapshot'], payload)
                self.assertNotEqual(record['catalog_status'], '成功但为空')
                self.assertNotEqual(record['status'], '成功但为空')
        empty = self.catalog({"errorcode": 0, "tables": []})
        self.assertEqual(empty['catalog_status'], '成功但为空')

    def test_future_time_and_scope_mismatch_remain_visible_gaps(self):
        for changes in ({'ctime': '2026-10-02T19:00:00+08:00'},
                        {'reportDate': '2026-10-03'}, {'thscode': '000001.SZ'}, {'pdfURL': None}):
            with self.subTest(changes=changes):
                record = self.catalog({'errorcode': 0, 'tables': [dict(self.row, **changes)]})
                self.assertTrue(record['catalog_entries'][0]['gaps'])
                with self.assertRaises(ContractError):
                    validate_filing_original_request(self.task, self.selection(record))
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_unknown_publication_time_allows_original_but_requires_pdf_publication_anchor(self):
        for ctime in (None, '', '2026-09-29 17:00:00'):
            with self.subTest(ctime=ctime):
                record = self.catalog({'errorcode': 0, 'tables': [dict(self.row, ctime=ctime)]})
                self.assertTrue(record['catalog_entries'][0]['gaps'])
                self.assertTrue(record['catalog_entries'][0]['downloadable'])
                self.assertIsNone(record['catalog_entries'][0]['published_at'])
                anchor = 'Published on 2026-09-29.'
                self.transport(payload=synthetic_pdf(statement=anchor))
                filing = self.collector.external(self.task, 'ifind', 'filing_original', self.selection(record), {})
                self.task['source_records'].append(filing)
                spec = {'id': 'e1', 'object': '600000.SH', 'published_at': '2026-09-30', 'period': self.row['reportDate'],
                        'nature': '已实现事实', 'unit': '不适用', 'record_locator': {'part': '第1页'}, 'text': anchor, 'value': None}
                with self.assertRaises(ContractError):
                    candidate_evidence(self.task, filing, spec)
                spec['publication'] = {'basis_ref': {'artifact_id': filing['id'], 'snapshot_hash': filing['hash'],
                                                    'kind': 'directory', 'field': 'published_at'}}
                with self.assertRaises(ContractError):
                    candidate_evidence(self.task, filing, spec)
                spec['publication'] = {'basis_ref': {'artifact_id': filing['id'], 'snapshot_hash': filing['hash'],
                    'kind': 'material', 'part': '第1页', 'quote': anchor}}
                with self.assertRaises(ContractError):
                    candidate_evidence(self.task, filing, spec)
                spec['published_at'] = '2026-09-29'
                candidate = candidate_evidence(self.task, filing, spec)
                self.assertEqual(candidate['source_check'], '通过')
                self.assertEqual(candidate['applicability_check'], '待核验')

    def test_explicit_code_in_user_question_is_allowed_without_name_mapping(self):
        task = dict(self.task, objects=['合成主体'], question='请核对600000.SH的公告')
        self.assertEqual(validate_ifind_report_request(task, self.params), ['2026-09-01', '2026-10-02'])

    def test_directory_tamper_cross_task_and_one_sided_version_are_rejected(self):
        record = self.catalog()
        original = copy.deepcopy(record)
        for field, value in (('title', '篡改'), ('issuer', '000001.SZ'), ('original_url', 'https://www.sse.com.cn/changed.pdf')):
            record['catalog_entries'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_filing_original_request(self.task, self.selection(record))
            record.clear(); record.update(copy.deepcopy(original))
        record['snapshot']['tables'][0]['reportTitle'] = '改原响应再算hash'
        record['hash'] = fingerprint(record['snapshot'])
        with self.assertRaises(ContractError):
            validate_filing_original_request(self.task, self.selection(record))
        record.clear(); record.update(original)
        for task in (dict(self.task, id='other'), dict(self.task, version=2)):
            with self.assertRaises(ContractError):
                validate_filing_original_request(task, self.selection(record))
        for params in (dict(self.selection(record), url=self.row['pdfURL']), {'catalog_id': record['id'], 'source_id': 'absent'}):
            with self.assertRaises(ContractError):
                validate_filing_original_request(self.task, params)

    def test_updated_current_records_retain_original_acquisition_identity(self):
        record = self.catalog()
        entry = copy.deepcopy(record['catalog_entries'][0])
        original_id = record['id']
        self.task['version'] = record['research_version'] = 2
        self.assertEqual(validate_filing_original_request(self.task, self.selection(record)), entry)
        self.assertEqual(record['id'], original_id)
        self.assertEqual(entry['catalog_ref']['research_version'], 1)
        self.transport()
        filing = self.collector.external(self.task, 'ifind', 'filing_original', self.selection(record), {})
        self.assertEqual(filing['research_version'], 2)
        self.assertEqual(filing['directory']['catalog_ref']['research_version'], 1)

    def test_pdf_original_is_bound_but_not_automatically_approved(self):
        record = self.catalog()
        factory, connection = self.transport()
        filing = self.collector.external(self.task, 'ifind', 'filing_original', self.selection(record), {})
        self.task['source_records'].append(filing)
        self.assertEqual(filing['status'], '成功待核验')
        self.assertEqual(filing['stages']['source_check'], '待核验')
        self.assertEqual(filing['stages']['applicability_check'], '待核验')
        self.assertEqual(filing['parameters'], self.selection(record))
        self.assertEqual(filing['request_hash'], fingerprint({'source': 'ifind', 'operation': 'filing_original', 'parameters': self.selection(record)}))
        self.assertIn('original_base64', filing['snapshot']['material'])
        connection.close.assert_called_once()
        self.assertLessEqual(factory.call_args.kwargs['timeout'], 30)
        spec = {'id': 'e1', 'object': '600000.SH', 'published_at': self.row['ctime'], 'period': self.row['reportDate'],
                'nature': '已实现事实', 'unit': '不适用', 'record_locator': {'part': '第1页'}, 'text': 'Synthetic filing only.', 'value': None}
        with self.assertRaisesRegex(ContractError, '首次公开时间缺少原始来源定位'):
            candidate_evidence(self.task, filing, spec)
        spec['publication'] = {'basis_ref': {'artifact_id': filing['id'], 'snapshot_hash': filing['hash'],
                                             'kind': 'directory', 'field': 'published_at'}}
        candidate = candidate_evidence(self.task, filing, spec)
        self.assertEqual(candidate['source_check'], '通过')
        self.assertEqual(candidate['applicability_check'], '待核验')
        with self.assertRaises(ContractError):
            validate_provenance(candidate, self.task)
        record['catalog_entries'][0]['title'] = '篡改标题'
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, filing, spec)

    def test_offline_and_budget_exhaustion_never_download(self):
        record = self.catalog()
        factory, _ = self.transport()
        with self.assertRaises(ContractError):
            EvidenceCollector().external(self.task, 'ifind', 'filing_original', self.selection(record), {})
        for kind in ('data_requests', 'seconds'):
            self.task['ledger'] = budget()
            self.task['ledger']['used'][kind] = self.task['ledger']['limits'][kind] - self.task['ledger']['reserve'].get(kind, 0)
            with self.subTest(kind=kind), self.assertRaises(ContractError):
                self.collector.external(self.task, 'ifind', 'filing_original', self.selection(record), {})
        factory.assert_not_called()

    def test_url_scope_rejected_before_dispatch(self):
        for url in ('http://static.cninfo.com.cn/a.pdf', 'https://127.0.0.1/a.pdf', 'https://static.cninfo.com.cn.evil.com/a.pdf',
                    'https://user:pass@static.cninfo.com.cn/a.pdf', 'https://static.cninfo.com.cn:444/a.pdf',
                    'https://static.cninfo.com.cn/a.pdf?secret=x', 'https://static.cninfo.com.cn/a.pdf#fragment',
                    'https://static.cninfo.com.cn/a.pdf\n', 'https://unknown.example/a.pdf'):
            with self.subTest(url=url):
                record = self.catalog({'errorcode': 0, 'tables': [dict(self.row, pdfURL=url)]})
                with self.assertRaises(ContractError):
                    self.collector.external(self.task, 'ifind', 'filing_original', self.selection(record), {})
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_redirect_nonpdf_size_and_missing_download_preserve_catalog_and_spending(self):
        record = self.catalog()
        original = copy.deepcopy(record)
        cases = [(b'', 302, {'Location': 'https://static.cninfo.com.cn/other.pdf'}), (b'<html>error</html>', 200, {}),
                 (b'', 404, {}), (b'%PDF-fake', 200, {'Content-Length': str(25 * 1024 * 1024 + 1)}),
                 (b'%PDF-fake', 200, {'Content-Length': '100'}), (b'%PDF-fake', 200, {'Content-Encoding': 'gzip'})]
        for index, (body, status, headers) in enumerate(cases):
            factory, connection = self.transport(body, status, headers)
            with self.subTest(status=status, headers=headers), self.assertRaises(ContractError):
                self.collector.external(self.task, 'ifind', 'filing_original', self.selection(record), {})
            self.assertEqual(record, original)
            self.assertEqual(self.task['ledger']['used']['data_requests'], index + 1)
            connection.request.assert_called_once()
            connection.close.assert_called_once()
        self.transport(b'%PDF-' + b'x' * 100)
        with patch('researchhelper.evidence.FILING_MAX_BYTES', 50), self.assertRaises(ContractError):
            self.collector.external(self.task, 'ifind', 'filing_original', self.selection(record), {})

    def test_scanned_and_broken_pdf_keep_bytes_with_parse_limitation(self):
        record = self.catalog()
        for payload in (synthetic_pdf(False), b'%PDF-1.4\nnot a valid PDF'):
            with self.subTest(payload=payload[:20]):
                self.transport(payload)
                filing = self.collector.external(self.task, 'ifind', 'filing_original', self.selection(record), {})
                self.assertEqual(filing['status'], '解析受限')
                self.assertEqual(filing['stages']['download'], '已获取')
                self.assertEqual(filing['stages']['parse'], '解析受限')
                self.assertIn('original_base64', filing['snapshot']['material'])

    def test_dns_rejects_private_and_pins_public_address_with_original_tls_host(self):
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1'):
            with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]), self.assertRaises(ContractError):
                _PublicFilingConnection('static.cninfo.com.cn', timeout=1).connect()
        raw, secured, context = MagicMock(), MagicMock(), MagicMock()
        context.wrap_socket.return_value = secured
        address = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))
        with patch('socket.getaddrinfo', return_value=[address]) as dns, patch('socket.socket', return_value=raw):
            connection = _PublicFilingConnection('static.cninfo.com.cn', timeout=1)
            connection._context = context
            connection.connect()
            raw.connect.assert_called_once_with(('8.8.8.8', 443))
            context.wrap_socket.assert_called_once_with(raw, server_hostname='static.cninfo.com.cn')
            dns.assert_called_once()
            connection.close()

    def test_zero_transport_deadline_is_rejected_and_closed(self):
        _, connection = self.transport()
        with self.assertRaises(ContractError):
            _download_filing_pdf(self.row['pdfURL'], 0)
        connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
