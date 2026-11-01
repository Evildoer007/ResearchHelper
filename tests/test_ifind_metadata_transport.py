"""Metadata GET keeps the isolated worker and forbids redirects, without IO."""
import io
import json
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request
from unittest.mock import patch

from researchhelper.https_transport import _NoRedirect
from researchhelper.source_transport import IsolatedEvidenceCollector, _source_worker


class Connection:
    def __init__(self, spec):
        self.spec, self.messages = spec, []
    def recv(self):
        return self.spec
    def send(self, message):
        self.messages.append(message)
    def close(self):
        pass


class Response(io.BytesIO):
    def __init__(self):
        body = json.dumps(dict(indicators=[], datas=[])).encode()
        super().__init__(body)
        self.headers = {'Content-Length': str(len(body)), 'Content-Type': 'application/json'}
        self.status = 200


class IFindMetadataTransportTests(unittest.TestCase):
    def spec(self, url='https://quantapi.51ifind.com/ds_service_basic/api/v1/get_indicators?market=A%E8%82%A1'):
        return dict(kind='http_json', endpoint=url, method='GET', body=None, headers={},
            unredirected_headers={'access_token': 'synthetic_metadata_token'})

    def test_parent_serializes_get_method_without_a_body(self):
        collector = IsolatedEvidenceCollector(offline=False)
        request = Request(self.spec()['endpoint'], method='GET')
        request.add_unredirected_header('access_token', 'synthetic_metadata_token')
        with patch.object(collector, '_request', return_value={}) as send:
            collector._http_json({}, request, 1, {})
        spec = send.call_args.args[1]
        self.assertEqual(spec['method'], 'GET')
        self.assertIsNone(spec['body'])
        self.assertNotIn('access_token', spec['endpoint'])

    def test_existing_post_packets_keep_the_original_shape(self):
        collector = IsolatedEvidenceCollector(offline=False)
        request = Request('https://quantapi.51ifind.com/api/v1/basic_data_service', data=b'{}', method='POST')
        with patch.object(collector, '_request', return_value={}) as send:
            collector._http_json({}, request, 1, {})
        self.assertNotIn('method', send.call_args.args[1])

    def test_worker_reads_registered_json_without_following_a_redirect(self):
        connection = Connection(self.spec())
        with patch('researchhelper.source_transport.open_verified_https', return_value=Response()) as opening:
            _source_worker(connection, time.monotonic()+2)
        self.assertFalse(opening.call_args.kwargs['follow_redirects'])
        self.assertEqual(opening.call_args.args[0].get_method(), 'GET')
        self.assertTrue(any(row.get('kind') == 'response' for row in connection.messages), connection.messages)

    def test_worker_rejects_foreign_host_and_unregistered_get_paths(self):
        for url in ('https://example.com/ds_service_basic/api/v1/get_limit_policy',
                'https://quantapi.51ifind.com/api/v1/get_limit_policy',
                'https://quantapi.51ifind.com/ds_service_basic/api/v1/../api/v1/get_limit_policy'):
            connection = Connection(self.spec(url))
            with patch('researchhelper.source_transport.open_verified_https') as opening:
                _source_worker(connection, time.monotonic()+2)
            opening.assert_not_called()
            self.assertTrue(any(row.get('kind') == 'failure' for row in connection.messages))

    def test_worker_rejects_a_get_body_before_network_dispatch(self):
        spec = self.spec(); spec['body'] = b'{}'
        connection = Connection(spec)
        with patch('researchhelper.source_transport.open_verified_https') as opening:
            _source_worker(connection, time.monotonic()+2)
        opening.assert_not_called()

    def test_redirect_handler_refuses_the_new_destination(self):
        request = Request(self.spec()['endpoint'])
        with self.assertRaises(HTTPError):
            _NoRedirect().redirect_request(request, None, 302, '', {}, 'https://example.com/')
