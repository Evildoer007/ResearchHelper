"""A rejected proposal is quarantined, not an approval or a lost valid batch."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from researchhelper.contracts import ContractError
from researchhelper.financial_sources import expand_financial_value_references
from researchhelper.reasoning import validate_model_specs
from researchhelper.source_reader import validate_read_specs
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT/'result/acceptance/one-sentence-research-g4bku34l/task.json'


class FinancialReferenceItemReviewTests(unittest.TestCase):
    def setUp(self):
        self.digest = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        self.task = json.loads(RETAINED.read_text())
        self.specs = self.task['reasoning_state']['rejected_extraction']['proposed_specs']
        self.bundle = self.task['reasoning_state']['reading']['bundle']
        self.views = self.bundle['views']

    def tearDown(self):
        self.assertEqual(hashlib.sha256(RETAINED.read_bytes()).hexdigest(), self.digest)

    def review(self, specs=None):
        rejected = []
        result = expand_financial_value_references(self.task, self.specs if specs is None else specs,
            self.views, rejections=rejected)
        return result, rejected

    def test_real_duplicate_is_recorded_and_three_amounts_cross_both_gates(self):
        specs, rejected = self.review()
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]['proposed_spec']['id'], 'fc_share_code_2026h1')
        self.assertFalse(rejected[0]['evidence_approved'])
        validate_model_specs(self.task, specs)
        validate_read_specs(self.task, specs, self.bundle)
        record = next(r for r in self.task['source_records'] if r['kind'] == 'filing')
        numbers = [spec for spec in specs if 'calculation_context' in spec]
        self.assertEqual(len(numbers), 3)
        for spec in numbers:
            item = candidate_evidence(self.task, record, spec)
            self.assertEqual(item['source_check'], '通过')
            assessed = ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
            self.assertEqual(assessed['applicability_check'], '通过', assessed.get('rejection'))
        self.assertFalse(self.task['evidence'])

    def test_default_host_path_still_rejects_the_whole_invalid_submission(self):
        with self.assertRaises(ContractError):
            expand_financial_value_references(self.task, self.specs, self.views)

    def test_bad_first_item_does_not_poison_later_valid_cell(self):
        bad = copy.deepcopy(self.specs[0]); bad['financial_value_reference'] = 'not-offered'
        specs, rejected = self.review([bad, self.specs[0]])
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]['id'], self.specs[0]['id'])
        self.assertEqual(len(rejected), 1)
        self.assertFalse(rejected[0]['automatic_repair'])

    def test_conflicting_scope_is_not_merged_or_overwritten(self):
        bad = copy.deepcopy(self.specs[0]); bad['id'] = 'conflict'
        bad['calculation_context']['currency'] = 'USD'
        specs, rejected = self.review([self.specs[0], bad])
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]['calculation_context']['currency'], 'CNY')
        self.assertEqual(rejected[0]['proposed_spec']['calculation_context']['currency'], 'USD')

    def test_all_invalid_means_no_accepted_proposals(self):
        bad = copy.deepcopy(self.specs[0]); bad['financial_value_reference'] = 'not-offered'
        specs, rejected = self.review([bad])
        self.assertEqual(specs, [])
        self.assertEqual(len(rejected), 1)
        self.assertFalse(self.task['evidence'])

    def test_review_never_mutates_task_wire_or_prior_quarantine(self):
        before = copy.deepcopy((self.task, self.specs))
        self.review()
        self.assertEqual((self.task, self.specs), before)
        with self.assertRaises(ContractError):
            expand_financial_value_references(self.task, self.specs, self.views, rejections=[{}])
