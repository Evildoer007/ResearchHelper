"""Explicit scalar bindings cannot borrow or be poisoned by another source."""
import copy
import unittest

from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.numeric_integrity import _segments, _legacy_bindings


class ExplicitQuantityLookupTests(unittest.TestCase):
    def setUp(self):
        self.task=dict(numeric_lookup_isolation_version=1)
        self.scalar=dict(id='scalar',task_id='software_fixture',research_version=1,
            value=0.15,unit='%',raw_value=0.15,raw_unit='%',text='软件夹具原值，不是金融批准。')
        self.other=dict(id='other',task_id='software_fixture',research_version=1,
            value=None,unit='不适用',text='其他原文示例1%/365，不是本片段所选来源。')
        self.sources={('evidence','other'):self.other,('evidence','scalar'):self.scalar}
        self.parts=[dict(text='披露费率为'),dict(source='evidence',ref='scalar',path='value',format='signed',digits=2)]

    def test_explicit_actual_reference_is_checked_without_scanning_another_quote(self):
        text,bindings=_segments(self.parts,self.sources,self.task)
        self.assertEqual(text,'披露费率为0.15%')
        self.assertEqual(bindings[0]['ref'],'scalar')
        self.assertEqual(bindings[0]['projection'],'identity')

    def test_old_policy_does_not_silently_change(self):
        for task in ({},dict(numeric_lookup_isolation_version=True)):
            with self.assertRaises(ContractError):
                _segments(self.parts,self.sources,task)

    def test_scalar_hash_change_cannot_borrow_an_equal_value(self):
        text,bindings=_segments(self.parts,self.sources,self.task)
        altered=copy.deepcopy(self.sources)
        altered['evidence','scalar']['text']='已修改来源'
        with self.assertRaises(ContractError):
            _legacy_bindings(text,altered,self.task,reference_bindings=bindings)

    def test_missing_reference_and_invalid_units_do_not_become_plain_text(self):
        for changes in (dict(ref='unknown'),dict(path='text')):
            parts=copy.deepcopy(self.parts)
            parts[1].update(changes)
            with self.assertRaises(ContractError):
                _segments(parts,self.sources,self.task)
        with self.assertRaises(ContractError):
            _segments(self.parts+[dict(text='/365')],self.sources,self.task)

    def test_evidence_scalar_does_not_license_comparison_direction(self):
        parts=copy.deepcopy(self.parts)
        parts[0]['text']='基金甲比基金乙低'
        with self.assertRaises(ContractError):
            _segments(parts,self.sources,self.task)

    def test_unbound_numeric_prose_is_still_rejected(self):
        with self.assertRaises(ContractError):
            _segments([dict(text='披露费率为0.15%')],self.sources,self.task)
