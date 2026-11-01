"""Diagnostic inputs and inspection do not approve financial interpretations."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.accept_multimarket_fixed_api_r165 import create_diagnostic_task, inspect_financial, remaining_requests, FIELDS
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.evidence import validate_source_request


class MultimarketDiagnosticTests(unittest.TestCase):
    def test_factory_explicitly_selects_current_research_before_any_external_call(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch('socket.socket.connect', side_effect=AssertionError('No network')):
            agent, task = create_diagnostic_task(ResearchArchive(Path(folder)))
            self.addCleanup(agent.close_runtime)
            self.assertEqual(task['research_mode'], 'current')
            self.assertEqual(task['fixed_financial_catalog_version'], 1)
            self.assertEqual(task['ledger']['used']['data_requests'], 0)
            self.assertEqual(task['ledger']['used']['model_calls'], 0)

    def example(self):
        return dict(snapshot=dict(tables=[dict(thscode=code,
            table={field: [123.45] for field in FIELDS}) for code in ('600519.SH', '300750.SZ')])), \
            dict(codes='600519.SH,300750.SZ')

    def test_remaining_shapes_validate_without_reopening_retained_task(self):
        path = ROOT/'result/acceptance/multimarket-fixed-api-6qk1u24x/task.json.gz'
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with gzip.open(path, 'rt') as stream:
            task = json.load(stream)
        with patch('socket.socket.connect', side_effect=AssertionError('No network')):
            requests = remaining_requests(task)
            self.assertEqual(len(requests), 3)
            self.assertEqual([r[2]['codes'] for r in requests[:2]], ['510300.SH', '159919.SZ'])
            for label, operation, parameters, inspect in requests:
                validate_source_request(task, 'ifind', operation, parameters)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_valid_cells_remain_raw_without_currency_or_unit_approval(self):
        source, params = self.example()
        result = inspect_financial(source, params)
        self.assertTrue(result['usable'])
        self.assertFalse(result['unit_approved'])
        self.assertFalse(result['financial_context_approved'])

    def test_missing_foreign_duplicate_boolean_or_nonfinite_cells_fail(self):
        source, params = self.example()
        for issue in ('missing', 'foreign', 'duplicate', 'boolean', 'nonfinite'):
            value = copy.deepcopy(source)
            table = value['snapshot']['tables'][0]
            if issue == 'missing': table['table'][FIELDS[0]] = []
            elif issue == 'foreign': table['thscode'] = 'FOREIGN'
            elif issue == 'duplicate': value['snapshot']['tables'][1]['thscode'] = table['thscode']
            elif issue == 'boolean': table['table'][FIELDS[0]] = [True]
            else: table['table'][FIELDS[0]] = [float('nan')]
            self.assertFalse(inspect_financial(value, params)['usable'], issue)


if __name__ == '__main__':
    unittest.main()
