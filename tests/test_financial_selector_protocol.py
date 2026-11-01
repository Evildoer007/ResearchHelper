"""Source selectors preserve literal material without model retranscription."""
import copy
import importlib
import unittest

from researchhelper.contracts import ContractError
from researchhelper.financial_sources import (original_line_index,original_date_anchors,
    expand_financial_value_references,selected_original_lines)
from researchhelper.output_schema import (financial_extraction_v5_schema as financial_extraction_output_schema,
    decode_financial_extraction_output,financial_selector_prompt)
from researchhelper.reasoning import validate_model_specs,evidence_contract

Fixture=importlib.import_module('tests.test_financial_value_reference' if __package__
                              else 'test_financial_value_reference').FinancialValueReferenceTests


class FinancialSelectorProtocolTests(unittest.TestCase):
    setUpClass=classmethod(Fixture.setUpClass.__func__)
    tearDownClass=classmethod(Fixture.tearDownClass.__func__)
    _set_up=Fixture.setUp
    candidates_for=Fixture.candidates_for
    compact=Fixture.compact

    def setUp(self):
        self._set_up();self.lines=original_line_index(self.view)
        self.dates=original_date_anchors(self.view)
        self.schema=financial_extraction_output_schema(self.task,[self.view])
        self.wire={'version':5,'source_reference':{'artifact_id':self.record['id'],
            'snapshot_hash':self.view['snapshot_hash']},'current_version_only':True,
            'financial_cells':[],'text_quotes':[]}
        for spec in self.compact():
            needed={key:spec[key] for key in ('id','object','nature','question_ids','review',
                'financial_value_reference','calculation_context')}
            needed=copy.deepcopy(needed)
            needs=needed['review']['evidence_need_texts']
            contract=evidence_contract(self.task,[self.view])
            needed['review']={'scope':'current_original_descriptive_only','need_links':[
                {'question_id':key,'need_ids':[need['id'] for need in contract['question_requirements'][key]
                    if need['text'] in texts]} for key,texts in needs.items()]}
            needed['observation_anchor']=next(i for i,date in enumerate(self.dates)
                if date['date']==spec['observation_date'] and date['part']==spec['record_locator']['period_part'])
            needed['context_spans']={key:self.locate(basis['part'],basis['quote'])
                for key,basis in spec['calculation_context_basis'].items()}
            self.wire['financial_cells'].append(needed)

    def locate(self,part,quote):
        lines=[line for line in self.lines if line['part']==part]
        text=''.join(line['text'] for line in lines);start=text.index(quote);end=start+len(quote)
        position=0;result=[]
        for line in lines:
            stop=position+len(line['text'])
            if stop>start and position<end:result.append(line['index'])
            position=stop
        return {'start':result[0],'end':result[-1]}

    def decode(self):
        return decode_financial_extraction_output(self.wire,self.schema,(self.task,[self.view]))

    def test_selected_cells_and_context_pass_original_validators(self):
        specs=self.decode()['evidence_specs']
        specs=expand_financial_value_references(self.task,specs,[self.view])
        validate_model_specs(self.task,specs)
        for actual,expected in zip(specs,self.original_specs):
            for key in ('value','unit','period','calculation_context','observation_date','record_locator'):
                self.assertEqual(actual[key],expected[key])

    def test_prompt_preserves_every_source_character_and_does_not_change_canonical_views(self):
        payload={'untrusted_sources':[self.view],'evidence_contract':evidence_contract(self.task,[self.view])}
        before=copy.deepcopy(payload);presented=financial_selector_prompt(payload)
        for actual,expected in zip(presented['untrusted_sources'][0]['parts'],self.view['parts']):
            self.assertEqual(''.join(line[1] for line in actual['indexed_lines']),expected['text'])
            self.assertNotIn('text',actual)
        self.assertEqual(payload,before)

    def test_download_date_cannot_be_submitted_as_a_free_observation_date(self):
        self.wire['financial_cells'][0]['observation_date']='2026-10-15'
        with self.assertRaises(ContractError):self.decode()

    def test_out_of_range_line_is_rejected_not_clipped(self):
        self.wire['financial_cells'][0]['context_spans']['currency']={'start':len(self.lines),'end':len(self.lines)}
        with self.assertRaises(ContractError):self.decode()

    def test_cross_part_or_noncontiguous_quotes_are_not_spliced(self):
        same=[line for line in self.lines if line['part']==self.lines[0]['part']]
        with self.assertRaises(ContractError):selected_original_lines(self.lines,[same[0]['index'],same[2]['index']])
        other=next(line for line in self.lines if line['part']!=self.lines[0]['part'])
        with self.assertRaises(ContractError):selected_original_lines(self.lines,[0,other['index']])

    def test_context_binding_changes_reject_even_valid_local_line_numbers(self):
        task=copy.deepcopy(self.task);task['input_hash']='different_input'
        with self.assertRaises(ContractError):
            decode_financial_extraction_output(self.wire,self.schema,(task,[self.view]))

    def test_no_context_cannot_decode_source_addresses(self):
        with self.assertRaises(ContractError):decode_financial_extraction_output(self.wire,self.schema)

    def test_source_hash_cannot_be_replaced_by_another_original(self):
        self.wire['source_reference']['snapshot_hash']='foreign'
        with self.assertRaises(ContractError):self.decode()

    def test_retyped_quote_is_not_accepted_by_selector_schema(self):
        self.wire['financial_cells'][0]['text']='fabricated quote'
        with self.assertRaises(ContractError):self.decode()


del Fixture
