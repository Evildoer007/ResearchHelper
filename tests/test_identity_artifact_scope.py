"""Source identity, dates and numeric records must resolve to one original."""
from copy import deepcopy

if __package__:
    from .test_identity_period_provenance import IdentityPeriodFixture
else:
    from test_identity_period_provenance import IdentityPeriodFixture
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError
from researchhelper.evidence import (candidate_evidence, extract_material, identity_period_basis_contract,
                                    material_record)


class IdentityArtifactScopeTests(IdentityPeriodFixture):
    def test_conflicting_original_with_the_same_id_cannot_authorize_a_quote(self):
        self.witness()
        duplicate = deepcopy(self.task['materials'][-1])
        duplicate['snapshot']['parts'][-1]['text'] += '另一个原件'
        self.task['materials'].append(duplicate)
        with self.assertRaisesRegex(ContractError, '原件事实'):
            self.candidate()
        self.assertEqual(identity_period_basis_contract(self.task)['admissible_bases'], [])

    def test_later_original_cannot_borrow_an_earlier_originals_publication(self):
        self.witness()
        body = ('发布日期：2026年10月2日。\n\n'+self.statement).encode()
        later = material_record(self.task, extract_material('later-identity.txt', body), body)
        self.task['materials'].append(later)
        # The statement is identical, but the cited publication belongs to
        # the other original. A historical decision must not adopt this one.
        with self.assertRaisesRegex(ContractError, '原件编号与实际原件不一致'):
            item = candidate_evidence(self.task, later, self.witness_spec)
            ResearchAnalyzer().assess(self.task, item, self.witness_spec['review'], self.task['materials'])

    def test_conflicting_quote_record_cannot_use_the_first_matching_id(self):
        self.witness()
        duplicate = deepcopy(self.artifact)
        duplicate['snapshot']['tables'][0]['table']['close'][0] = 9.9
        self.task['source_records'].append(duplicate)
        with self.assertRaisesRegex(ContractError, '当前版本可得依据'):
            self.candidate()
