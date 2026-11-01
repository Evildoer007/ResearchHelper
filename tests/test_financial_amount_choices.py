"""Single-cell selections reuse the actual source and applicability gates."""
import copy
import importlib
import unittest

fixtures=importlib.import_module((__package__+'.' if __package__ else '')+'test_financial_data_protocol')
from researchhelper.output_schema import (original_batch_output_schema, original_batch_prompt,
    decode_original_batch, StructuredOutputError, ORIGINAL_SELECTION_REJECTIONS,
    _financial_amount_choices, _financial_amount_reference_enabled)
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.reasoning import (expand_financial_value_references,
    expand_material_publication_references, expand_material_context_references)


class FinancialAmountChoiceTests(unittest.TestCase):
    amount=fixtures.FinancialDataProtocolTests.amount
    expanded=fixtures.FinancialDataProtocolTests.expanded
    tearDownClass=classmethod(fixtures.FinancialDataProtocolTests.tearDownClass.__func__)
    test_all_numeric_routes_pass_actual_reading_source_and_applicability_gates=fixtures.FinancialDataProtocolTests.test_all_numeric_routes_pass_actual_reading_source_and_applicability_gates

    @classmethod
    def setUpClass(cls):
        fixtures.FinancialDataProtocolTests.setUpClass.__func__(cls)
        cls.task['financial_amount_reference_version']=1
        cls.schema=original_batch_output_schema(cls.task,cls.views)
        cls.prompt=original_batch_prompt(dict(untrusted_sources=cls.views,evidence_contract=cls.contract),cls.task)
        probe=cls('test_all_numeric_routes_pass_actual_reading_source_and_applicability_gates')
        specs=decode_original_batch(probe.wire(),cls.schema,(cls.task,cls.views))['evidence_specs']
        specs=expand_financial_value_references(cls.task,specs,cls.views)
        specs=expand_material_publication_references(cls.task,specs,cls.views)
        cls.expanded_specs=expand_material_context_references(cls.task,specs,cls.views)

    def wire(self):
        wire=fixtures.FinancialDataProtocolTests.wire(self)
        if not self.task.get('financial_amount_reference_version'):
            return wire
        for index,entry in enumerate(wire['sources']):
            choices=_financial_amount_choices(self.task,self.contract['sources'][index]['financial_amount_tables'])
            for item in entry['selection']['amount_cells']:
                address={key:item.pop(key) for key in ('table_reference','column_reference','row_reference')}
                item['amount_reference']=next(ref for ref,choice in choices.items() if choice['address']==address)
        return wire

    def test_prompt_offers_one_exact_source_cell_without_row_assembly(self):
        for source in self.prompt['source_choices']:
            choices=source['financial_amount_choices']
            self.assertTrue(choices)
            self.assertNotIn('financial_amount_tables',source)
            self.assertEqual(len(choices),len({row['reference'] for row in choices}))
            self.assertTrue(all(row['reference'].startswith('amount_') and row['text'] and row['period'] for row in choices))
        for entry in self.wire()['sources']:
            for item in entry['selection']['amount_cells']:
                self.assertIn('amount_reference',item)
                self.assertTrue(set(item).isdisjoint({'row_reference','column_reference','table_reference','value'}))

    def test_single_choice_decodes_the_same_fourteen_original_records(self):
        legacy=copy.copy(self);legacy.task=copy.deepcopy(self.task)
        legacy.task.pop('financial_amount_reference_version')
        legacy.schema=original_batch_output_schema(legacy.task,self.views)
        legacy.prompt=original_batch_prompt(dict(untrusted_sources=self.views,evidence_contract=self.contract),legacy.task)
        old=decode_original_batch(legacy.wire(),legacy.schema,(legacy.task,self.views))
        new=decode_original_batch(self.wire(),self.schema,(self.task,self.views))
        self.assertEqual(len(new['evidence_specs']),14)
        self.assertEqual(new,old)

    def test_wrong_local_choice_is_discarded_without_guessing_or_erasing_other_facts(self):
        wire=self.wire()
        amounts=[item for entry in wire['sources'] for item in entry['selection']['amount_cells']]
        invalid=('amount_foreign',None,7,True,[1],{'row':1})
        self.assertEqual(len(amounts),len(invalid))
        for item,value in zip(amounts,invalid):item['amount_reference']=value
        before=copy.deepcopy(wire)
        result=decode_original_batch(wire,self.schema,(self.task,self.views))
        self.assertEqual(len(result['evidence_specs']),8)
        self.assertEqual(result[ORIGINAL_SELECTION_REJECTIONS]['accepted_items'],8)
        self.assertEqual(len(result[ORIGINAL_SELECTION_REJECTIONS]['rejected_items']),6)
        self.assertFalse(result[ORIGINAL_SELECTION_REJECTIONS]['automatic_repair'])
        self.assertEqual(wire,before)
        self.assertFalse(self.task['evidence'] or self.task['calculations'])

    def test_choice_cannot_change_task_source_period_or_financial_authority(self):
        before=fingerprint(self.task)
        wire=self.wire()
        changed=copy.deepcopy(self.task);changed['version']+=1
        with self.assertRaises(ContractError):
            fresh=original_batch_output_schema(changed,self.views)
            decode_original_batch(wire,fresh,(changed,self.views))
        for name,value in (('object','600519.SH'),('financial_approval',True),('row_reference','line_1')):
            altered=copy.deepcopy(wire);altered['sources'][0]['selection']['amount_cells'][0][name]=value
            altered['sources'][0]['selection']['amount_cells'][0]['amount_reference']={'row':1}
            with self.subTest(name=name),self.assertRaises(StructuredOutputError):
                decode_original_batch(altered,self.schema,(self.task,self.views))
        self.assertEqual(fingerprint(self.task),before)

    def test_policy_is_strict_and_old_tasks_do_not_acquire_new_cells(self):
        old=copy.deepcopy(self.task);old.pop('financial_amount_reference_version')
        self.assertFalse(_financial_amount_reference_enabled(old))
        old_schema=original_batch_output_schema(old,self.views)
        with self.assertRaises(StructuredOutputError):decode_original_batch(self.wire(),old_schema,(old,self.views))
        for value in (True,2,'1',1.0):
            with self.subTest(value=value),self.assertRaises(ContractError):
                _financial_amount_reference_enabled(dict(old,financial_amount_reference_version=value))


if __name__=='__main__':unittest.main()
