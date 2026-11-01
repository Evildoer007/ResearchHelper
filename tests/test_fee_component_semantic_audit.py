"""Adversarial annual-fee ownership checks, using synthetic local originals.

These assertions require the number to belong to its claimed fee component.
Failures expose a source gate issue, not a completed model or supplier study.
"""
import copy
import unittest

from tests import test_management_custody_fee_offline as fixture
from researchhelper.contracts import ContractError
from researchhelper.evidence import candidate_evidence


class FeeComponentSemanticAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.ManagementCustodyFeeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        task,host=self.fixture.prepare()
        self.text=('合成验收原件，非真实基金规则。ETF-A，ETF-A份额，2026年度规则。'
            '管理费按基金资产净值计提，年费率为0.15%。'
            '托管费按基金资产净值计提，年费率为0.05%。发布于2026-07-19。')
        self.task=self.fixture.agent.add_material(task['id'],'same-page-fee-audit.txt',self.text.encode())
        self.original=self.task['materials'][-1]
        self.spec=copy.deepcopy(host['evidence_specs'][0])
        self.spec.update(artifact_id=self.original['id'],text=self.text,raw_unit='%',unit='%')
        for anchor in [self.spec['publication']['basis_ref'],*self.spec['calculation_context_basis'].values()]:
            anchor.update(artifact_id=self.original['id'],snapshot_hash=self.original['snapshot_hash'],quote=self.text)

    def check(self,metric,token):
        item=copy.deepcopy(self.spec)
        item['calculation_context']['metric']=metric
        item.update(raw_token=token,raw_value=float(token),value=float(token))
        return candidate_evidence(self.task,self.original,item)

    def test_management_rate_control(self):
        self.assertEqual(self.check('management_fee','0.15')['value'],0.15)

    def test_custody_rate_control(self):
        self.assertEqual(self.check('custody_fee','0.05')['value'],0.05)

    def test_custody_cannot_borrow_management_rate_from_same_page(self):
        with self.assertRaises(ContractError):self.check('custody_fee','0.15')

    def test_management_cannot_borrow_custody_rate_from_same_page(self):
        with self.assertRaises(ContractError):self.check('management_fee','0.05')

    def test_monthly_custody_cannot_borrow_management_annual_frequency(self):
        text=self.text.replace('托管费按基金资产净值计提，年费率','托管费按基金资产净值计提，月费率')
        self.task=self.fixture.agent.add_material(self.task['id'],'mixed-frequency.txt',text.encode())
        self.original=self.task['materials'][-1]
        self.spec.update(artifact_id=self.original['id'],text=text)
        for anchor in [self.spec['publication']['basis_ref'],*self.spec['calculation_context_basis'].values()]:
            anchor.update(artifact_id=self.original['id'],snapshot_hash=self.original['snapshot_hash'],quote=text)
        with self.assertRaises(ContractError):self.check('custody_fee','0.05')
