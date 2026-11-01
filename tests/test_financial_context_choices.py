"""Grounded context choices cross the real gates, not a mocked approval path."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from researchhelper.contracts import ContractError
from researchhelper.reasoning import evidence_contract,validate_model_specs
from researchhelper.financial_sources import expand_financial_value_references,financial_context_cells
from researchhelper.output_schema import financial_extraction_output_schema,decode_financial_extraction_output,financial_selector_prompt
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-5mp4kywz/task.json'


class FinancialContextChoicesTests(unittest.TestCase):
    def setUp(self):
        self.digest=hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        self.task=json.loads(RETAINED.read_text());self.views=self.task['reasoning_state']['reading']['bundle']['views']
        self.record=next(r for r in self.task['source_records'] if r['kind']=='filing')
        self.contract=evidence_contract(self.task,self.views);self.source=self.contract['sources'][0]
        self.schema=financial_extraction_output_schema(self.task,self.views)
        self.wire={'version':7,'source_reference':{'artifact_id':self.record['id'],'snapshot_hash':self.source['snapshot_hash']},
            'current_version_only':True,'financial_cells':[],'text_quotes':[]}
        from researchhelper.financial_sources import original_date_anchors
        dates=original_date_anchors(self.views[0])
        for spec in self.task['host_result']['evidence_specs']:
            if 'calculation_context' not in spec:continue
            context=spec['calculation_context']
            cell=next(c for c in self.source['financial_context_cells'] if all(c['context'][key]==context[key]
                for key in ('metric','period','currency','accounting_scope')))
            links=[{'question_id':qid,'need_ids':[need['id'] for need in self.contract['question_requirements'][qid]
                if need['text'] in texts]} for qid,texts in spec['review']['evidence_need_texts'].items()]
            self.wire['financial_cells'].append({'id':spec['id'],'object':self.source['object_binding']['value'],
                'nature':spec['nature'],'question_ids':spec['question_ids'],
                'review':{'scope':'current_original_descriptive_only','need_links':links},
                'observation_anchor':next(i for i,date in enumerate(dates) if date['date']=='2026-06-30'),
                'financial_context_reference':cell['candidate_id'],
                'shared_context_references':{field:self.source['financial_shared_context_choices'][field][0]['candidate_id']
                    for field in ('entity_id','accounting_standard')}})

    def tearDown(self):
        self.assertEqual(hashlib.sha256(RETAINED.read_bytes()).hexdigest(),self.digest)

    def decode(self):
        return decode_financial_extraction_output(self.wire,self.schema,(self.task,self.views))

    def test_actual_original_contexts_pass_source_and_applicability_without_mocking(self):
        self.assertEqual(self.schema['properties']['version']['enum'],[7])
        specs=expand_financial_value_references(self.task,self.decode()['evidence_specs'],self.views)
        validate_model_specs(self.task,specs)
        self.assertEqual(len(specs),3)
        for spec in specs:
            evidence=candidate_evidence(self.task,self.record,spec)
            self.assertEqual(evidence['source_check'],'通过')
            assessed=ResearchAnalyzer().assess(self.task,evidence,spec['review'],self.task['source_records'])
            self.assertEqual(assessed['applicability_check'],'通过',assessed.get('rejection'))
        self.assertFalse(self.task['evidence']);self.assertFalse(self.task['calculations'])

    def test_old_mislocated_entity_text_is_not_silently_repaired(self):
        spec=copy.deepcopy(self.task['host_result']['evidence_specs'][0]);spec['object']=self.source['object_binding']['value']
        with self.assertRaisesRegex(ContractError,'计算口径'):candidate_evidence(self.task,self.record,spec)

    def test_current_choices_cannot_cross_task_binding(self):
        task=copy.deepcopy(self.task);task['input_hash']='different_input'
        with self.assertRaises(ContractError):decode_financial_extraction_output(self.wire,self.schema,(task,self.views))

    def test_model_cannot_override_a_selected_raw_value_or_currency(self):
        for field,value in (('value',1),('calculation_context',{'currency':'USD'})):
            wire=copy.deepcopy(self.wire);wire['financial_cells'][0][field]=value
            with self.assertRaises(ContractError):
                decode_financial_extraction_output(wire,self.schema,(self.task,self.views))

    def test_missing_explicit_currency_does_not_default_to_cny(self):
        view=copy.deepcopy(self.views[0])
        for part in view['parts']:part['text']=part['text'].replace('币种：人民币','币种：[缺失]')
        # Altered readings cannot be accepted merely to make candidates exist.
        with self.assertRaises(ContractError):financial_context_cells(self.task,self.record,view)

    def test_unknown_entity_and_standard_choices_are_rejected(self):
        for field in ('entity_id','accounting_standard'):
            wire=copy.deepcopy(self.wire);wire['financial_cells'][0]['shared_context_references'][field]='foreign'
            with self.assertRaises(ContractError):
                decode_financial_extraction_output(wire,self.schema,(self.task,self.views))

    def test_all_bases_remain_literal_positions_in_actual_read_parts(self):
        parts={p['locator']:p['text'] for p in self.views[0]['parts']}
        for spec in self.decode()['evidence_specs']:
            for basis in spec['calculation_context_basis'].values():
                self.assertIn(basis['quote'],parts[basis['part']])
                self.assertEqual(basis['snapshot_hash'],self.source['snapshot_hash'])

    def test_quote_partition_preserves_every_read_character(self):
        from researchhelper.financial_sources import financial_quote_choices
        choices=financial_quote_choices(self.task,self.views[0])
        self.assertLessEqual(len(choices),512)
        for part in self.views[0]['parts']:
            self.assertEqual(''.join(choice['quote'] for choice in choices if choice['part']==part['locator']),part['text'])

    def test_actual_prompt_preserves_all_text_without_duplicate_line_copy(self):
        payload={'untrusted_sources':self.views,'evidence_contract':self.contract};before=copy.deepcopy(payload)
        prompt=financial_selector_prompt(payload,task=self.task)
        self.assertNotIn('financial_quote_choices',prompt['evidence_contract']['sources'][0])
        self.assertNotIn('financial_table_cells',prompt['evidence_contract']['sources'][0])
        for actual,original in zip(prompt['untrusted_sources'][0]['parts'],self.views[0]['parts']):
            self.assertEqual(''.join(item[1] for item in actual['quote_choices']),original['text'])
            self.assertNotIn('text',actual);self.assertNotIn('indexed_lines',actual)
        self.assertEqual(payload,before)

    def test_quoted_block_passes_source_without_any_cross_part_splice(self):
        from researchhelper.financial_sources import financial_quote_choices
        choice=next(choice for choice in financial_quote_choices(self.task,self.views[0])
            if '企业会计准则' in choice['quote'])
        item=copy.deepcopy(self.wire['financial_cells'][0])
        for key in ('financial_context_reference','shared_context_references'):item.pop(key)
        item.update(id='quoted_standard',quote_reference=choice['candidate_id'],period='2026-06-30')
        self.wire['text_quotes']=[item]
        specs=expand_financial_value_references(self.task,self.decode()['evidence_specs'],self.views)
        quote=specs[-1]
        self.assertEqual(quote['text'],choice['quote'])
        self.assertEqual(quote['record_locator']['part'],choice['part'])
        self.assertEqual(candidate_evidence(self.task,self.record,quote)['source_check'],'通过')

    def test_text_selector_rejects_foreign_quote_and_handmade_span(self):
        item=copy.deepcopy(self.wire['financial_cells'][0])
        for key in ('financial_context_reference','shared_context_references'):item.pop(key)
        item.update(id='quoted_standard',quote_reference='foreign',period='2026-06-30')
        self.wire['text_quotes']=[item]
        with self.assertRaises(ContractError):self.decode()
