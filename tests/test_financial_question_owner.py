"""One explicit question map replaces contradictory financial owner lists."""
import copy
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.financial_sources import original_date_anchors
from researchhelper.output_schema import (financial_reference_output_schema, financial_reference_prompt,
    decode_financial_references, original_batch_output_schema, decode_original_batch)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract
from researchhelper.reports import compose

POLICY = 'financial_question_owner_version'
RETAINED = ROOT / 'result/acceptance/one-sentence-research-dyjfhyqh/task.json'


class FinancialQuestionOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.old = json.loads(RETAINED.read_text())
        cls.task = copy.deepcopy(cls.old)
        cls.task[POLICY] = 1
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.views = cls.task['reasoning_state']['reading']['bundle']['views']
        cls.contract = evidence_contract(cls.task, cls.views)
        cls.schema = financial_reference_output_schema(cls.task, cls.views)

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest() == cls.digest

    def selection(self, task=None):
        task = task or self.task
        contract = evidence_contract(task, self.views)
        source = contract['sources'][0]
        schema = financial_reference_output_schema(task, self.views)
        prompt = financial_reference_prompt(dict(untrusted_sources=self.views, evidence_contract=contract), task)
        dates = original_date_anchors(self.views[0])
        links = {qid:None for qid in contract['question_ids']}
        links['q1'] = [contract['question_requirements']['q1'][0]['id']]
        item = dict(id='fixture_owned_cell', object=source['object_binding']['value'], nature=contract['natures'][0],
            observation_anchor=next(index for index, date in enumerate(dates) if date['date']=='2026-06-30'),
            review=dict(scope='current_original_descriptive_only', need_links=links),
            financial_context_reference=source['financial_context_cells'][0]['candidate_id'],
            shared_context_references={field:source['financial_shared_context_choices'][field][0]['candidate_id']
                                       for field in ('entity_id','accounting_standard')})
        return schema, dict(version=schema['properties']['version']['enum'][0],
            source_reference=prompt['financial_reference_binding'], current_version_only=True,
            financial_cells=[item], text_quotes=[])

    def decode(self, wire=None):
        if wire is None: _, wire = self.selection()
        return decode_financial_references(wire, self.schema, (self.task, self.views))

    def test_question_map_is_the_only_declaration_of_each_evidence_owner(self):
        fields = self.schema['properties']['financial_cells']['items']['properties']
        self.assertNotIn('question_ids', fields)
        _, wire = self.selection()
        wire['financial_cells'][0]['review']['need_links']['q2'] = []
        item = self.decode(wire)['evidence_specs'][0]
        self.assertEqual(item['question_ids'], ['q2','q1'])
        self.assertEqual(set(item['review']['evidence_need_texts']), {'q1'})
        self.assertNotIn('source_check', item)
        self.assertNotIn('applicability_check', item)

    def test_old_duplicate_declarations_fail_but_new_encoding_has_no_drift_to_repair(self):
        old_schema = financial_reference_output_schema(self.old, self.views)
        source = evidence_contract(self.old, self.views)['sources'][0]
        _, wire = self.selection(self.old)
        item = wire['financial_cells'][0]
        item['question_ids'] = ['q2']
        item['review']['need_links'] = {qid:values or [] for qid,values in item['review']['need_links'].items()}
        self.assertEqual(source['artifact_id'], self.contract['sources'][0]['artifact_id'])
        with self.assertRaisesRegex(ContractError, '超出本摘录适用问题'):
            decode_financial_references(wire, old_schema, (self.old, self.views))
        self.assertEqual(self.decode()['evidence_specs'][0]['question_ids'], ['q1'])

    def test_redundant_unknown_or_missing_question_fields_are_not_silently_fixed(self):
        for issue in ('redundant', 'unknown', 'missing'):
            _, wire = self.selection(); item = wire['financial_cells'][0]
            if issue == 'redundant': item['question_ids'] = ['q2']
            elif issue == 'unknown': item['review']['need_links']['unknown'] = None
            else: item['review']['need_links'].pop('q2')
            with self.subTest(issue=issue), self.assertRaises(ContractError): self.decode(wire)

    def test_need_ids_still_cannot_cross_questions_or_repeat(self):
        for issue in ('cross_question', 'unknown', 'repeat'):
            _, wire = self.selection(); links = wire['financial_cells'][0]['review']['need_links']
            if issue == 'cross_question': links['q2'] = links['q1']
            elif issue == 'unknown': links['q1'] = ['foreign']
            else: links['q1'] *= 2
            with self.subTest(issue=issue), self.assertRaises(ContractError): self.decode(wire)

    def test_null_withholds_owner_while_empty_list_selects_owner_without_need_coverage(self):
        _, wire = self.selection(); links = wire['financial_cells'][0]['review']['need_links']
        links.update(dict.fromkeys(links, None))
        with self.assertRaisesRegex(ContractError, '适用问题'): self.decode(wire)
        links['q3'] = []
        item = self.decode(wire)['evidence_specs'][0]
        self.assertEqual(item['question_ids'], ['q3'])
        self.assertNotIn('evidence_need_texts', item['review'])

    def test_batch_keeps_the_same_explicit_owners_and_evidence(self):
        task = copy.deepcopy(self.task)
        task['financial_quote_fallback_version'] = 1
        task['input_hash'] = input_fingerprint(task)
        schema, selected = self.selection(task)
        batch_schema = original_batch_output_schema(task, self.views)
        wire = dict(version=1, binding_ref=batch_schema['properties']['binding_ref']['enum'][0],
                    sources=[dict(source_ref='source_1',selection=selected)])
        self.assertEqual(decode_original_batch(wire, batch_schema, (task,self.views)),
                         decode_financial_references(selected, schema, (task,self.views)))

    def test_old_or_invalid_policy_does_not_change_legacy_shape(self):
        for value in (None, True, False, 0, 2, 1.0):
            task = copy.deepcopy(self.old)
            if value is not None: task[POLICY] = value
            task['input_hash'] = input_fingerprint(task)
            schema = financial_reference_output_schema(task, self.views)
            self.assertIn('question_ids', schema['properties']['financial_cells']['items']['properties'])
        with self.assertRaises(ContractError):
            decode_financial_references(self.selection()[1], self.schema, (self.old,self.views))

    def test_instruction_describes_all_three_states_without_automatic_approval(self):
        shown = financial_reference_prompt(dict(untrusted_sources=self.views,
            evidence_contract=self.contract), self.task)
        rule = shown['financial_need_link_rule']
        self.assertIn('null', rule)
        self.assertIn('[]', rule)
        self.assertIn('不另填question_ids', rule)
        self.assertEqual(shown['evidence_contract']['question_requirements'], self.contract['question_requirements'])

    def test_formal_policy_is_input_bound_and_report_frozen(self):
        with tempfile.TemporaryDirectory(prefix='financial-owner-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF折溢价', entry='ETF', questions=[dict(id='q1',kind='fact',
                    question='解释ETF折溢价',answer_scope='general_definition',knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY], 1)
                old = copy.deepcopy(task); old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old), task['input_hash'])
                completed = agent.start(task['id'])
                self.assertEqual(compose(completed)[POLICY], 1)
                self.assertEqual(completed['ledger']['used']['model_calls'], 0)
                self.assertNotIn(POLICY, agent.create_case('mechanism'))
            finally: agent.close_runtime()
