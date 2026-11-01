"""Observation time does not replace a financial flow's actual period."""
import copy
import unittest
import sys
from pathlib import Path
from researchhelper.contracts import ContractError
from researchhelper.evidence import evidence_binding
from researchhelper.output_schema import material_extraction_output_schema,decode_material_extraction_output
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
import test_current_original_availability as original


class CurrentOriginalFinancialPeriodTests(unittest.TestCase):
    def setUp(self):
        self.provider=original.CurrentOriginalAvailabilityTests()
        self.provider.setUp();self.addCleanup(self.provider.doCleanups)
        self.task=self.provider.task

    def tearDown(self):self.provider.tearDown()
    def spec(self):return self.provider.spec()
    def candidate(self,spec):return self.provider.candidate(spec)
    def reading(self):return self.provider.reading()
    def test_financial_period_is_separate_from_literal_observation_date(self):
        spec=self.spec(); spec['period']='2026年第2季度'; spec['observation_date']='2026-06-30'
        item=self.candidate(spec)
        self.assertEqual(item['period'],'2026年第2季度')
        self.assertEqual(item['observation_date'],'2026-06-30')

    def test_financial_period_label_without_explicit_observation_date_is_not_guessed(self):
        spec=self.spec();spec['period']='2026年第2季度'
        with self.assertRaises(ContractError):self.candidate(spec)

    def test_observation_date_must_still_equal_original_literal_date(self):
        spec=self.spec();spec.update(period='2026年第2季度',observation_date='2026-07-01')
        with self.assertRaises(ContractError):self.candidate(spec)

    def test_observation_date_cannot_rewrite_historical_cutoff(self):
        spec=self.spec();spec.update(period='2026年第2季度',observation_date='2099-06-30')
        with self.assertRaises(ContractError):self.candidate(spec)

    def test_observation_date_is_part_of_verified_dependency_binding(self):
        first=self.candidate(self.spec());second=copy.deepcopy(first)
        second['observation_date']='2026-06-30'
        self.assertNotEqual(evidence_binding(first),evidence_binding(second))

    def test_structured_model_wire_supports_period_and_explicit_observation_date(self):
        bundle=self.reading();schema=material_extraction_output_schema(self.task,bundle['views'])
        spec=self.spec();spec.update(period='2026年第2季度',observation_date='2026-06-30')
        spec.pop('published_at');spec.pop('publication');spec['publication_mode']='unknown_current_original'
        spec['review']['evidence_need_texts']=[]
        decoded=decode_material_extraction_output(dict(version=1,evidence_specs=[spec]),schema)
        self.assertEqual(decoded['evidence_specs'][0]['observation_date'],'2026-06-30')

    def test_period_label_cannot_be_fabricated_from_same_observation_date(self):
        spec=self.spec();spec.update(period='2027年全年',observation_date='2026-06-30')
        with self.assertRaises(ContractError):self.candidate(spec)

    def test_unchecked_context_quote_cannot_grant_a_fabricated_financial_period(self):
        spec=self.spec();spec.update(period='2027年全年',observation_date='2026-06-30')
        spec['calculation_context_basis']={'period':dict(kind='material',quote='2027年全年')}
        with self.assertRaises(ContractError):self.candidate(spec)


if __name__=='__main__':
    unittest.main()
