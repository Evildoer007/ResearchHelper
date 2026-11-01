"""Portable CA verification and shared call paths, without external traffic."""
import io
import os
import ssl
import time
import unittest
import urllib.request
from unittest.mock import Mock, patch

from researchhelper.https_transport import bundled_ca_file, open_verified_https, verified_ssl_context
from researchhelper.model import ModelService, _model_http_worker
from researchhelper.evidence import EvidenceCollector, _PublicFilingConnection
from researchhelper.source_transport import _source_worker


class PortableHTTPS(unittest.TestCase):
    def test_context_uses_the_explicit_bundled_ca(self):
        with patch('researchhelper.https_transport.ssl.create_default_context') as create:
            verified_ssl_context()
        create.assert_called_once_with(cafile=bundled_ca_file())

    def test_real_context_requires_certificate_and_hostname(self):
        context = verified_ssl_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(context.cert_store_stats()['x509_ca'], 0)

    def test_environment_certificate_paths_do_not_replace_bundle(self):
        with patch.dict(os.environ, {'SSL_CERT_FILE': '/no/developer/certificate.pem', 'SSL_CERT_DIR': '/no/developer/ca'}):
            context = verified_ssl_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreater(context.cert_store_stats()['x509_ca'], 0)

    def test_real_opener_receives_verified_context(self):
        opener = Mock(return_value='response')
        context = Mock()
        with patch('researchhelper.https_transport.urlopen', opener), patch('researchhelper.https_transport.verified_ssl_context', return_value=context):
            self.assertEqual(open_verified_https('https://example.invalid', timeout=3, opener=opener), 'response')
        opener.assert_called_once_with('https://example.invalid', timeout=3, context=context)

    def test_injected_host_or_test_opener_keeps_its_interface(self):
        opener = Mock(return_value='host-response')
        with patch('researchhelper.https_transport.verified_ssl_context') as context:
            self.assertEqual(open_verified_https('request', timeout=3, opener=opener), 'host-response')
        opener.assert_called_once_with('request', timeout=3)
        context.assert_not_called()

    def test_invalid_bundle_fails_closed_without_fallback(self):
        opener = Mock()
        with patch('researchhelper.https_transport.urlopen', opener), patch('researchhelper.https_transport.verified_ssl_context', side_effect=ssl.SSLError('fabricated trust failure')):
            with self.assertRaises(ssl.SSLError):
                open_verified_https('request', timeout=3, opener=opener)
        opener.assert_not_called()

    def test_direct_model_transport_uses_shared_trust(self):
        request = urllib.request.Request('https://example.invalid')
        with patch('researchhelper.model.open_verified_https', return_value=io.BytesIO(b'{}')) as opener:
            body = ModelService(None, offline=False)._request_bytes({}, request, time.monotonic(), 3, {})
        self.assertEqual(body, b'{}')
        opener.assert_called_once_with(request, timeout=3, opener=urllib.request.urlopen)

    def test_isolated_model_worker_uses_shared_trust(self):
        connection = Mock()
        connection.recv.return_value = {'endpoint': 'https://example.invalid', 'body': b'{}', 'headers': {}}
        with patch('researchhelper.model.open_verified_https', return_value=io.BytesIO(b'{}')) as opener:
            _model_http_worker(connection, time.monotonic() + 3)
        self.assertEqual(opener.call_count, 1)
        self.assertIs(opener.call_args.kwargs['opener'], urllib.request.urlopen)
        self.assertTrue(any(call.args[0].get('kind') == 'response' for call in connection.send.call_args_list))

    def test_direct_supplier_http_uses_shared_trust(self):
        request = urllib.request.Request('https://example.invalid')
        with patch('researchhelper.evidence.open_verified_https', return_value=io.BytesIO(b'{"errorcode":0}')) as opener:
            value = EvidenceCollector(offline=False)._http_json({}, request, 3, {})
        self.assertEqual(value, {'errorcode': 0})
        opener.assert_called_once_with(request, timeout=3, opener=urllib.request.urlopen)

    def test_filing_connection_keeps_verified_hostname_context(self):
        connection = _PublicFilingConnection('example.invalid', timeout=3)
        self.addCleanup(connection.close)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(connection._context.check_hostname)

    def test_isolated_supplier_worker_uses_shared_trust(self):
        connection = Mock()
        connection.recv.return_value = {'kind': 'http_json', 'endpoint': 'https://example.invalid',
            'body': b'{}', 'headers': {}, 'unredirected_headers': {}}
        with patch('researchhelper.source_transport.open_verified_https', return_value=io.BytesIO(b'{"errorcode":0}')) as opener:
            _source_worker(connection, time.monotonic() + 3)
        self.assertEqual(opener.call_count, 1)
        self.assertIs(opener.call_args.kwargs['opener'], urllib.request.urlopen)
        self.assertTrue(any(call.args[0].get('kind') == 'response' for call in connection.send.call_args_list))


if __name__ == '__main__':
    unittest.main()
