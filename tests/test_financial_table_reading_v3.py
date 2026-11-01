"""Keep all prior geometry gates, updating only the new registered version."""
import importlib
from researchhelper.contracts import ContractError
from researchhelper import source_reader as reader

original=importlib.import_module('tests.test_complete_table_reading' if __package__ else 'test_complete_table_reading')


class RetainedTableReadingTests(original.RetainedTableReadingTests):
    def test_version_two_replay_and_term_coverage_are_deterministic(self):
        reader.validate_reading(self.task,self.new)
        self.assertEqual(self.new['reader_version'],3)
        coverage={row['term']:row for row in self.new['coverage'][0]['query_coverage']}
        self.assertGreater(coverage['营业收入']['provided_candidates'],0)
        self.assertGreater(coverage['经营活动产生的现金流量净额']['provided_candidates'],0)
        self.assertEqual(coverage['报表净利润']['hit_count'],0)
        old=reader.read(self.task,self.old['selection'],self.old['binding'],self.old['artifact_ids'],reader_version=2)
        self.assertEqual(old['reader_version'],2)
        reader.validate_reading(self.task,old)

    def test_unknown_or_noninteger_reading_versions_are_rejected(self):
        for version in (4,True,'3',3.0):
            with self.assertRaises(ContractError):
                reader.read(self.task,self.old['selection'],self.old['binding'],self.old['artifact_ids'],reader_version=version)


SyntheticReadingPolicyTests=original.SyntheticReadingPolicyTests
ReadingCheckpointVersionTests=original.ReadingCheckpointVersionTests
