"""The ordinary isolated transport preserves EDB's actual text wire format."""
import importlib
import io
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from researchhelper.source_transport import _source_worker

_prefix = __package__+'.' if __package__ else ''
base = importlib.import_module(_prefix+'test_ifind_metadata_transport')


class EconomicSearchTransportTests(unittest.TestCase):
    def execute(self, raw):
        response = io.BytesIO(raw)
        response.headers = {'Content-Length': str(len(raw)), 'Content-Type': 'text/plain; charset=utf-8'}
        response.status = 200
        request = base.IFindMetadataTransportTests().spec('https://quantapi.51ifind.com/ds_service_basic/api/v1/search_edb?query=CPI')
        connection = base.Connection(request)
        with patch('researchhelper.source_transport.open_verified_https', return_value=response) as opening:
            _source_worker(connection, time.monotonic()+3)
        self.assertFalse(opening.call_args.kwargs['follow_redirects'])
        return connection.messages

    def test_actual_text_is_retained_without_json_repair(self):
        text = (Path(__file__).resolve().parents[1]/'result/acceptance/ifind-economic-discovery-20261023-v1/3-search_edb.txt').read_text()
        messages = self.execute(text.encode())
        returned = [row for row in messages if row.get('kind') == 'response']
        self.assertEqual(len(returned), 1)
        self.assertEqual(returned[0]['body'], {'text': text})

    def test_large_text_is_rejected_not_truncated_to_a_partial_directory(self):
        messages = self.execute(b'x'*1000001)
        self.assertFalse(any(row.get('kind') == 'response' for row in messages))
        self.assertTrue(any(row.get('kind') == 'failure' for row in messages))

    def test_invalid_utf8_is_not_silently_replaced(self):
        messages = self.execute(b'\xff\xfe')
        self.assertFalse(any(row.get('kind') == 'response' for row in messages))
        self.assertTrue(any(row.get('kind') == 'failure' for row in messages))
