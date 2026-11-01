"""Official M/G namespaces are explicit identifiers, not guessed mappings."""
import unittest
from researchhelper.economic_sources import declared_economic_indicators, validate_economic_request
from researchhelper.contracts import ContractError


class EconomicNamespaceTests(unittest.TestCase):
    def task(self, version=2):
        return dict(question='核查G009035746与M001620253的原始序列', objects=[],
            cutoff='2026-10-17T08:00:00+08:00', economic_identifier_namespace_version=version)

    def test_current_namespace_accepts_only_explicit_m_and_g_identifiers(self):
        task = self.task()
        self.assertEqual(declared_economic_indicators(task), ['G009035746', 'M001620253'])
        validate_economic_request(task, 'edb_service',
            dict(indicators='G009035746', startdate='2026-01-01', enddate='2026-09-30'))

    def test_legacy_and_boolean_versions_do_not_change_scope(self):
        for version in (1, True, False, 3, None):
            self.assertEqual(declared_economic_indicators(self.task(version)), ['M001620253'])

    def test_topic_or_unrequested_identifier_cannot_expand_scope(self):
        for question in ('研究GDP、PMI和通胀', 'XG009035746', 'g009035746', 'G００９０３５７４６'):
            task = dict(self.task(), question=question)
            self.assertEqual(declared_economic_indicators(task), [])
            with self.assertRaises(ContractError):
                validate_economic_request(task, 'edb_service',
                    dict(indicators='G009035746', startdate='2026-01-01', enddate='2026-09-30'))


if __name__ == '__main__':
    unittest.main()
