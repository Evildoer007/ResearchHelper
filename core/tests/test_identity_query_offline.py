"""Independent exact identity query guards, no vendor requests."""

import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_ifind_identity_request


class IdentityQueryTests(unittest.TestCase):
    def parameters(self, mode='secname', query='腾讯控股'):
        return {mode: query, 'functionpara': {'mode': mode, 'sectype': '', 'market': '', 'tradestatus': '0', 'isexact': '1'}}

    def test_exact_user_name_or_code_is_allowed(self):
        task = {'question': '分析腾讯控股及300033', 'objects': []}
        self.assertEqual(validate_ifind_identity_request(task, self.parameters()), ('腾讯控股', 'secname'))
        self.assertEqual(validate_ifind_identity_request(task, self.parameters('seccode', '300033')), ('300033', 'seccode'))

    def test_unstated_object_or_partial_code_is_rejected(self):
        task = {'question': '分析腾讯控股及300033', 'objects': []}
        for mode, query in [('secname', '其他公司'), ('seccode', '0033'), ('seccode', '600000'), ('secname', '腾讯控股,其他公司')]:
            with self.subTest(query=query), self.assertRaises(ContractError):
                validate_ifind_identity_request(task, self.parameters(mode, query))

    def test_scan_and_filters_and_unknown_fields_are_rejected(self):
        task = {'question': '腾讯控股'}
        for changes in [{'isexact': '0'}, {'market': '212001'}, {'tradestatus': '1'}, {'mode': 'unknown'}, {'extra': 'bad'}]:
            parameters = self.parameters()
            parameters['functionpara'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                validate_ifind_identity_request(task, parameters)

    def test_business_failure_retains_numeric_code_without_private_error_text_or_retry(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix='identity-diagnostic-', dir=root / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), root, offline=False)
            self.addCleanup(agent.close_runtime)
            agent.collector.journal = None
            task = agent.create('分析腾讯控股', objects=['腾讯控股'])
            for code in [-4102, '-4102', 'PRIVATE_TOKEN', True]:
                current = agent.archive.get(task['id'])
                with patch.object(agent.collector, '_http_json', return_value={'errorcode': code, 'errmsg': 'PRIVATE_TOKEN'}) as request:
                    with self.assertRaises(ContractError):
                        agent.collector.external(current, 'ifind', 'get_thscode', self.parameters(), {'access_token': 'FAKE_ACCESS'})
                self.assertEqual(request.call_count, 1)
                event = current['ledger']['dispatches'][-1]
                self.assertEqual(event['status'], '业务失败')
                self.assertNotIn('PRIVATE_TOKEN', str(event))
                if type(code) is int or code == '-4102':
                    self.assertEqual(event['business_code'], code)
                else:
                    self.assertNotIn('business_code', event)


if __name__ == '__main__':
    unittest.main()
