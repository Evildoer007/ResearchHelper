"""Raw availability is not evidence adoption; all transports are offline controls."""
import copy
import json
import unittest

try:
    from . import test_sse_etf_price_context as fixtures
except ImportError:
    import test_sse_etf_price_context as fixtures
from researchhelper.source_inventory import inventory, _field, POLICY
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT


class SourceFieldInventoryTests(unittest.TestCase):
    def setUp(self):
        self.control = fixtures.SSEETFPcfPriceContextTests()
        self.control.setUp();self.addCleanup(self.control.doCleanups)
        self.task = copy.deepcopy(self.control.task)
        self.task[POLICY] = 1
        self.task.setdefault('knowledge',dict(entries=[]))
        for source in self.task['source_records']:source['question_ids']=['q1']

    def test_official_and_ifind_fields_keep_anchors_without_values_or_approval(self):
        before = copy.deepcopy(self.task)
        result = inventory(self.task,['q1'])
        self.assertEqual(len(result['records']),2)
        self.assertFalse(result['financial_approval']);self.assertFalse(result['absence_proved'])
        official = next(r for r in result['records'] if r['source']=='official')
        fields = {f['name']:f for g in official['groups'] for f in g['fields']}
        self.assertEqual(fields['CREATION_REDEMPTION_UNIT']['status'],'present_unverified')
        self.assertTrue(all(not f['financial_approval'] for f in fields.values()))
        self.assertNotIn('900000',json.dumps(result))
        quote = next(r for r in result['records'] if r['source']=='ifind')
        self.assertEqual(quote['groups'][0]['fields'][0]['name'],'close')
        self.assertEqual(official['reference']['snapshot_hash'],self.control.nav['hash'])
        from researchhelper.data_requirements import processing_state
        self.assertEqual(processing_state(self.task,['q1'])['downloaded_fields'],result)
        from researchhelper.reasoning import analysis_contract
        from researchhelper.argument_support import review_request
        author=analysis_contract(self.task,['q1'])['evidence_processing_state']['downloaded_fields']
        reviewer=review_request(self.task,dict(answers={'q1':dict(answer='本次尚未核验金融含义。',
            refs=[],calculation_refs=[])},theses=[]))['evidence_processing_state']['downloaded_fields']
        self.assertEqual(author,result);self.assertEqual(reviewer,result)
        self.assertEqual(self.task,before)

    def test_placeholder_and_zero_are_distinct_without_asserting_absence(self):
        self.assertEqual(_field('limit',[None,'-'])['status'],'empty_or_placeholder')
        self.assertEqual(_field('limit',[0])['status'],'present_unverified')
        self.assertNotIn('value',_field('limit',[0]))

    def test_old_historical_and_document_tasks_are_not_upgraded(self):
        task = copy.deepcopy(self.task);task.pop(POLICY)
        self.assertIsNone(inventory(task))
        task = copy.deepcopy(self.task);task['research_mode']='historical'
        self.assertIsNone(inventory(task))
        task = copy.deepcopy(self.task);task['intent']=DOCUMENT_CONTENT_INTENT
        self.assertIsNone(inventory(task))

    def test_invalid_version_is_not_truthy_approval(self):
        for version in (True,2,'1'):
            task = copy.deepcopy(self.task);task[POLICY]=version
            with self.assertRaises(ContractError):inventory(task)

    def test_other_question_fields_are_not_shared(self):
        self.assertEqual(inventory(self.task,['foreign'])['records'],[])
        from researchhelper.data_requirements import processing_state
        self.assertNotIn('downloaded_fields',processing_state(self.task,['foreign']))
        for source in self.task['source_records']:source['question_ids']=['foreign']
        self.assertEqual(inventory(self.task,['q1'])['records'],[])

    def test_changed_snapshot_and_foreign_task_cannot_inherit_availability(self):
        self.task['source_records'][0]['snapshot']['changed']=True
        result=inventory(self.task)
        self.assertFalse(any(r['source']=='official' for r in result['records']))
        self.task['id']='foreign'
        self.assertEqual(inventory(self.task)['records'],[])

    def test_imported_source_without_current_acquisition_is_not_listed(self):
        for source in self.task['source_records']:source.pop('acquisition',None)
        self.assertEqual(inventory(self.task)['records'],[])

    def test_duplicate_source_identity_is_rejected(self):
        self.task['source_records'].append(copy.deepcopy(self.task['source_records'][0]))
        with self.assertRaises(ContractError):inventory(self.task)
