"""Stable raw references through provider key order and Store serialization."""
import copy
from decimal import Context, Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
import socket
import unittest
from unittest.mock import patch
from tests import ROOT
from researchhelper.contracts import canonical,fingerprint
from researchhelper.draft_sources import observations
from researchhelper.draft_analysis import analyze,current_analysis

ORIGINAL=ROOT/'result/acceptance/one-sentence-research-vgjkyxq9/task.json'
HK=ROOT/'result/acceptance/one-sentence-research-_kw2k9ap/task.json'


def assert_calculation_meaning(self, results, frozen):
    self.assertEqual(len(results), 11)
    self.assertEqual(len(results), len(frozen))
    for current, original in zip(results, frozen, strict=True):
        with self.subTest(calculation=current['id']):
            # Inputs, references, periods and financial meaning remain fixed.
            reading = {'value', 'exact', 'display'}
            self.assertEqual({key:value for key,value in current.items() if key not in reading},
                             {key:value for key,value in original.items() if key not in reading})
            a, b = (Decimal(str(current['inputs'][key])) for key in ('a', 'b'))
            with localcontext(Context(prec=60, rounding=ROUND_HALF_UP)):
                method = current['method']
                if method == 'raw_change_v1':
                    expected = (b-a)/a*100
                elif method in ('raw_difference_v1', 'raw_percentage_points_v1'):
                    expected = b-a
                elif method == 'raw_percent_v1':
                    expected = a/b*100
                elif method == 'raw_multiple_v1':
                    expected = a/b
                elif method == 'raw_margin_v1':
                    expected = (a-b)/a*100
                else:
                    self.fail('Unexpected method in this retained calculation sample: '+method)
                tolerance = Decimal('1e-36')*max(Decimal(1), abs(expected))
                self.assertLessEqual(abs(Decimal(current['exact'])-expected), tolerance)
                self.assertLessEqual(abs(Decimal(str(current['value']))-expected),
                                     Decimal('1e-14')*max(Decimal(1), abs(expected)))
                if not expected or abs(expected) >= Decimal('0.005'):
                    self.assertEqual(Decimal(current['display']),
                                     expected.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
                else:
                    self.assertNotEqual(Decimal(current['display']), 0)
                    self.assertEqual(Decimal(current['display']).is_signed(), expected.is_signed())


class DraftObservationOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_hash=hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
        cls.task=json.loads(ORIGINAL.read_text());cls.hk=json.loads(HK.read_text())

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()==cls.original_hash

    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Offline ordering test'))
        guard.start();self.addCleanup(guard.stop)

    def test_actual_saved_report_reconstructs_the_same_ten_fact_identifiers(self):
        self.assertEqual(observations(self.task),self.task['draft_report']['facts'])

    def test_actual_saved_inputs_keep_eleven_calculations_and_the_frozen_report(self):
        before = canonical(self.task)
        results = analyze(self.task, observations(self.task))['results']
        assert_calculation_meaning(self, results, self.task['draft_report']['calculations'])
        self.assertEqual(canonical(self.task), before)

    def test_store_canonical_serialization_preserves_fact_references(self):
        self.assertEqual(observations(self.task),observations(json.loads(canonical(self.task))))

    def test_provider_object_key_reordering_does_not_change_raw_references(self):
        task=copy.deepcopy(self.task)
        task['draft_analysis']=analyze(task,observations(task))
        for record in task['source_records']:
            for table in record.get('snapshot',{}).get('tables',[]):
                if isinstance(table.get('table'),dict):table['table']=dict(reversed(list(table['table'].items())))
        self.assertEqual(observations(task),observations(self.task))
        self.assertTrue(current_analysis(task,observations(task)))

    def test_quotation_request_order_is_stable_across_storage(self):
        task=copy.deepcopy(self.hk)
        record=next(row for row in task['source_records'] if row['operation']=='cmd_history_quotation')
        record['parameters']['indicators']='close,open'
        table=record['snapshot']['tables'][0]['table'];table['open']=list(table['close'])
        table=dict(reversed(list(table.items())));record['snapshot']['tables'][0]['table']=table
        # A deliberately different response/request is a new unapproved fixture.
        record['hash']=fingerprint(record['snapshot'])
        record['request_hash']=fingerprint({key:record[key] for key in ('source','operation','parameters')})
        self.assertEqual(observations(task),observations(json.loads(canonical(task))))
        self.assertEqual([row['field'] for row in observations(task)][:5],['close']*5)

    def test_unrequested_fields_have_a_deterministic_fallback_without_loss(self):
        task=copy.deepcopy(self.task)
        for row in task['source_records']:
            if row.get('operation')=='basic_data_service':
                row['parameters'].pop('indipara',None)
                row['request_hash']=fingerprint({key:row[key] for key in ('source','operation','parameters')})
        before=observations(task);after=observations(json.loads(canonical(task)))
        self.assertEqual(before,after);self.assertEqual(len(before),10)


if __name__=='__main__':unittest.main()
