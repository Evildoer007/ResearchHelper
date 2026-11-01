"""Unread anchors still fail closed; diagnostics disclose no proposed content."""
import copy
import unittest
from unittest.mock import patch

from researchhelper.source_reader import ReadingAnchorError, validate_read_specs
from researchhelper.reasoning import extractable_source_views, source_views


class ReadingAnchorDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.task = {'id':'rh_fixture','version':1,'input_hash':'fixture','intent':'综合研究'}
        self.bundle = {'views':[{'id':'original','kind':'material','parts':[
            {'locator':'p1','text':'营业收入100元。2026年半年度。人民币。'}]}]}
        self.spec = {'artifact_id':'original','text':'营业收入100元。',
            'record_locator':{'part':'p1'},'publication':{'basis_ref':None}}
        for name, target in (('reading','researchhelper.source_reader.validate_reading'),
                             ('usage','researchhelper.contracts.validate_evidence_usage'),
                             ('snapshot','researchhelper.evidence.validate_current_snapshot')):
            guard=patch(target, return_value=name=='snapshot')
            guard.start();self.addCleanup(guard.stop)

    def validate(self):
        validate_read_specs(self.task,[self.spec],self.bundle)

    def test_valid_literal_anchor_passes_without_mutation(self):
        before=copy.deepcopy((self.task,self.spec,self.bundle));self.validate()
        self.assertEqual((self.task,self.spec,self.bundle),before)

    def test_unseen_quote_is_rejected_without_recording_its_value(self):
        self.spec['text']='PRIVATE_UNSEEN_QUOTE'
        with self.assertRaises(ReadingAnchorError) as caught:self.validate()
        self.assertEqual(caught.exception.diagnostic['field'],'text')
        self.assertNotIn('PRIVATE_UNSEEN_QUOTE',str(caught.exception.diagnostic))
        self.assertFalse(caught.exception.diagnostic['evidence_approved'])

    def test_wrong_header_has_its_own_field_path(self):
        self.spec['record_locator']['financial_column']={'row_quote':self.spec['text'],
            'header_part':'p1','header_quote':'not exposed'}
        with self.assertRaises(ReadingAnchorError) as caught:self.validate()
        self.assertEqual(caught.exception.diagnostic['field'],'financial_column.header_quote')

    def test_current_original_period_still_requires_a_read_anchor(self):
        self.spec['availability']={'kind':'current_original'}
        self.spec['record_locator'].update(period_part='p1',period_quote='not exposed')
        with self.assertRaises(ReadingAnchorError) as caught:self.validate()
        self.assertEqual(caught.exception.diagnostic['field'],'period_quote')

    def test_context_field_diagnostic_keeps_known_name_only(self):
        self.spec['calculation_context_basis']={'currency':{'kind':'material','artifact_id':'original',
            'part':'p1','quote':'not exposed'}}
        with self.assertRaises(ReadingAnchorError) as caught:self.validate()
        self.assertEqual(caught.exception.diagnostic['field'],'calculation_context_basis.currency')


class RawFinanceNavigationTests(unittest.TestCase):
    def test_raw_financial_and_economic_records_are_not_body_extraction(self):
        views=[{'id':'raw-finance','kind':'api','source':'ifind','operation':'smart_stock_picking'},
               {'id':'raw-cashflow','kind':'api','source':'tinyshare','operation':'cashflow'},
               {'id':'price','kind':'api','source':'ifind','operation':'cmd_history_quotation'}]
        with patch('researchhelper.reasoning.source_views',return_value=views):
            self.assertEqual(extractable_source_views({}),[views[-1]])
