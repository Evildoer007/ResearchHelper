"""Stable raw references through provider key order and Store serialization."""
import copy
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

    def test_actual_saved_app_arithmetic_reconstructs_the_same_eleven_results(self):
        self.assertEqual(analyze(self.task,observations(self.task))['results'],self.task['draft_report']['calculations'])

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
