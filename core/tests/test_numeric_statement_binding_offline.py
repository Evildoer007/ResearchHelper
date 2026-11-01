"""Independent numeric binding contract tests; no models, files or suppliers."""

import copy
import unittest

from researchhelper.contracts import ContractError
from researchhelper.numeric_integrity import bind_numeric_statement, validate_numeric_statement


class NumericStatementBindingTests(unittest.TestCase):
    def setUp(self):
        self.task = {'id': 't1', 'version': 1, 'questions': [{'id': 'q1'}, {'id': 'q2'}],
                     'evidence': [], 'calculations': []}
        for ref, name, value in [('a', 'ETF-A', .2), ('b', 'ETF-B', .6)]:
            self.task['evidence'].append({'id': ref, 'object': name, 'value': value,
                'raw_value': value, 'unit': '%/年', 'raw_unit': '%/年',
                'text': f'{name}年费率为{value}%。', 'task_id': 't1',
                'research_version': 1, 'question_ids': ['q1']})
        self.task['calculations'] = [{'id': 'c', 'method': 'fee_difference_v1',
            'input_refs': ['a', 'b'], 'input_bindings': {'fee_a': 'a', 'fee_b': 'b'},
            'output': {'value': -.4, 'unit': '百分点/年'},
            'task_id': 't1', 'research_version': 1, 'question_ids': ['q1']}]

    def bind(self, text=None, **extra):
        proposal = {'question_ids': ['q1'], **extra}
        if text is not None:
            proposal['answer'] = text
        return bind_numeric_statement(self.task, proposal, ['answer'], ['a', 'b'], ['c'])

    def test_correct_legacy_direction_and_identity(self):
        for text in ('A比B低0.40个百分点。', 'ETF-A年费率比ETF-B低0.4个百分点。',
                     'B比A高0.4个百分点。', 'A年度费用低0.40个百分点。',
                     '差值为-0.4百分点/年。', '2026年10月5日代码510300的规则见第2条。'):
            with self.subTest(text=text):
                self.assertEqual(self.bind(text)['fields']['answer'], text)

    def test_wrong_magnitude_unit_direction_and_unknown_object_rejected(self):
        for text in ('A比B低40个百分点。', 'A比B高0.4个百分点。', 'A比B低0.4元。',
                     'A比B低-0.4个百分点。', 'B比A低0.4个百分点。',
                     '费用低0.4个百分点。', '差值为0.4个百分点。', 'A管理费为15%。',
                     'A比B低四十个百分点。', '管理费为百分之十五。'):
            with self.subTest(text=text), self.assertRaises(ContractError):
                self.bind(text)

    def test_confirmed_chinese_object_aliases(self):
        self.task['evidence'][0]['object'] = '材料示例ETF甲'
        self.task['evidence'][1]['object'] = '材料示例ETF乙'
        self.task['calculations'][0]['output']['value'] = -.05
        self.bind('甲年度费用低0.05个百分点。')
        with self.assertRaises(ContractError):
            self.bind('乙年度费用低0.05个百分点。')

    def test_raw_evidence_and_complete_verbatim_quantities(self):
        self.task['evidence'][0].update(value=.15, raw_value=.15, text='管理费0.15%，托管费0.05%。')
        self.bind('管理费0.15%，托管费0.05%。')
        with self.assertRaises(ContractError):
            self.bind('管理费15%。')
        with self.assertRaises(ContractError):
            self.bind('管理费0.15个百分点。')

    def test_segments_derive_signed_value_unit_and_comparison(self):
        part = {'source': 'calculation', 'ref': 'c', 'path': 'value', 'format': 'comparison', 'digits': 2}
        result = self.bind(numeric_segments={'answer': [part]})
        self.assertEqual(result['fields']['answer'], 'ETF-A比ETF-B低0.40个百分点/年')
        binding = result['record']['fields']['answer']['bindings'][0]
        self.assertEqual(binding['projection'], 'comparison_absolute')
        self.assertEqual(binding['canonical_value'], '-0.4')
        self.assertEqual(binding['unit'], '百分点/年')
        self.assertTrue(binding['source_hash'])
        self.assertEqual(binding['span'], [0, len(result['fields']['answer'])])

    def money(self,parts,answer=None):
        self.task['evidence'][0].update(value=4.1856,raw_value='4.1856',unit='元',raw_unit='元',text='单位净值4.1856元。')
        proposal=dict(question_ids=['q1'],numeric_segments=dict(answer=parts))
        if answer is not None:proposal['answer']=answer
        return bind_numeric_statement(self.task,proposal,['answer'],['a'],[])

    def test_repeated_unit_is_rendered_once_and_numeric_spans_stay_valid(self):
        part=dict(source='evidence',ref='a',path='value',format='signed',digits=4)
        parts=[dict(text='净值为'),part,dict(text='元，另一个读数为'),part,dict(text='元。')]
        original=copy.deepcopy(parts);result=self.money(parts)
        self.assertEqual(result['fields']['answer'],'净值为4.1856元，另一个读数为4.1856元。')
        self.assertEqual(parts,original)
        for binding in result['record']['fields']['answer']['bindings']:
            start,end=binding['span'];self.assertEqual(result['fields']['answer'][start:end],binding['display'])
        proposal=dict(question_ids=['q1'],answer=result['fields']['answer'],numeric_segments=dict(answer=parts),numeric_integrity=result['record'])
        self.assertEqual(validate_numeric_statement(self.task,proposal,['answer'],['a'],[])['record'],result['record'])

    def test_legacy_repeated_unit_text_is_accepted_but_changed_numbers_are_not(self):
        part=dict(source='evidence',ref='a',path='value',format='signed',digits=4);parts=[part,dict(text='元。')]
        self.assertEqual(self.money(parts,'4.1856元元。')['fields']['answer'],'4.1856元。')
        for text in ('4.1857元元。','-4.1856元元。','4.1856美元。'):
            with self.subTest(text=text),self.assertRaises(ContractError):self.money(parts,text)

    def test_normal_words_and_fragmented_words_are_not_truncated_as_units(self):
        part=dict(source='evidence',ref='a',path='value',format='signed',digits=4)
        for tail in ([dict(text='元旦期间再核对。')],[dict(text='元'),dict(text='旦期间再核对。')]):
            with self.subTest(tail=tail):self.assertEqual(self.money([part]+tail)['fields']['answer'],'4.1856元元旦期间再核对。')
        self.assertEqual(self.money([part,dict(text='元'),dict(text='，继续核对。')])['fields']['answer'],'4.1856元，继续核对。')

    def test_percent_unit_and_qualifier_are_joined_without_changing_the_quantity(self):
        self.task['evidence'][0].update(unit='%',raw_unit='%')
        part=dict(source='evidence',ref='a',path='value',format='signed',digits=2)
        result=bind_numeric_statement(self.task,dict(question_ids=['q1'],numeric_segments=dict(answer=[part,dict(text='%左右。')])),['answer'],['a'],[])
        self.assertEqual(result['fields']['answer'],'0.20%左右。')
        self.assertEqual(result['record']['fields']['answer']['bindings'][0]['canonical_value'],'0.2')

    def test_display_unit_join_never_edits_a_literal_quote(self):
        # Isolate presentation from the separate original-source validator.
        from unittest.mock import patch
        from researchhelper.numeric_integrity import _segments
        from researchhelper import argument_support
        task=dict(self.task,verbatim_evidence_segments_version=1,argument_support_contract=argument_support.VERSION)
        quote=dict(id='quote',task_id='t1',research_version=1,unit='不适用',
            source_check='通过',applicability_check='通过',artifact={'kind':'material'},text='原文保留元元及3.5%。')
        with patch('researchhelper.numeric_integrity.validate_evidence') as checked:
            text,bindings=_segments([dict(quote_ref='quote')],{('evidence','quote'):quote},task)
        checked.assert_called_once_with(quote,task)
        self.assertEqual(text,'“'+quote['text']+'”');self.assertEqual(bindings[0]['display'],text)

    def test_old_frozen_display_receipt_is_read_without_rewriting_its_text(self):
        self.task['evidence'][0].update(value=4.1856,raw_value='4.1856',unit='元',raw_unit='元',text='净值4.1856元。')
        part=dict(source='evidence',ref='a',path='value',format='signed',digits=4)
        proposal=dict(question_ids=['q1'],numeric_segments=dict(answer=[part,dict(text='元。')]))
        legacy=bind_numeric_statement(self.task,proposal,['answer'],['a'],[],unit_join_version=0)
        self.assertNotIn('unit_join_version',legacy['record']);self.assertEqual(legacy['fields']['answer'],'4.1856元元。')
        saved=dict(proposal,answer=legacy['fields']['answer'],numeric_integrity=legacy['record']);before=copy.deepcopy(saved)
        self.assertEqual(validate_numeric_statement(self.task,saved,['answer'],['a'],[])['record'],legacy['record'])
        self.assertEqual(saved,before)
        current=bind_numeric_statement(self.task,proposal,['answer'],['a'],[])
        self.assertEqual(current['fields']['answer'],'4.1856元。');self.assertEqual(current['record']['unit_join_version'],1)
        tampered=dict(proposal,answer=legacy['fields']['answer'],numeric_integrity=current['record'])
        with self.assertRaises(ContractError):validate_numeric_statement(self.task,tampered,['answer'],['a'],[])

    def test_segments_never_rewrite_model_prose(self):
        part = {'source': 'calculation', 'ref': 'c', 'path': 'value', 'format': 'comparison', 'digits': 2}
        with self.assertRaises(ContractError):
            self.bind('ETF-A比ETF-B高0.40百分点/年', numeric_segments={'answer': [part]})
        for changed in (dict(part, value=40), dict(part, unit='元'), dict(part, digits=True), dict(part, path='tolerance')):
            with self.subTest(changed=changed), self.assertRaises(ContractError):
                self.bind(numeric_segments={'answer': [changed]})
        with self.assertRaises(ContractError):
            self.bind(numeric_segments={'answer': [{'text': '管理费15%。'}]})

    def test_cross_task_version_question_and_uncited_inputs_rejected(self):
        for key, value in [('task_id', 'other'), ('research_version', 2), ('question_ids', ['q2'])]:
            original = copy.deepcopy(self.task)
            self.task['evidence'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.bind('管理费0.2%。')
            self.task = original
        with self.assertRaises(ContractError):
            bind_numeric_statement(self.task, {'id': 'q1', 'answer': '差值-0.4个百分点。'}, ['answer'], ['a'], ['c'])

    def test_receipt_rejects_text_change_deleted_receipt_and_source_change(self):
        proposal = {'question_ids': ['q1'], 'answer': 'A比B低0.4个百分点。'}
        result = self.bind(proposal['answer'])
        proposal['numeric_integrity'] = result['record']
        validate_numeric_statement(self.task, proposal, ['answer'], ['a', 'b'], ['c'])
        for changed in (dict(proposal, answer='A比B低0.40个百分点。'), dict(proposal, numeric_integrity=None)):
            with self.assertRaises(ContractError):
                validate_numeric_statement(self.task, changed, ['answer'], ['a', 'b'], ['c'])
        self.task['calculations'][0]['output']['value'] = -.40001
        with self.assertRaises(ContractError):
            validate_numeric_statement(self.task, proposal, ['answer'], ['a', 'b'], ['c'])

    def test_residual_registered_projection_and_rounding(self):
        calc = self.task['calculations'][0]
        calc.update(method='residual_present_value_v1', output={'value': 12.34567, 'unit': '元/股', 'premium': .234567, 'undiscounted': 14.2})
        self.bind('现值12.35元/股，溢价23.46%，未折现14.2元/股。')
        part = {'source': 'calculation', 'ref': 'c', 'path': 'premium', 'format': 'signed', 'digits': 2}
        result = self.bind(numeric_segments={'answer': [part]})
        self.assertEqual(result['fields']['answer'], '23.46%')
        self.assertEqual(result['record']['fields']['answer']['bindings'][0]['projection'], 'ratio_to_percent')
        with self.assertRaises(ContractError):
            self.bind('溢价0.234567%。')

    def test_nonfinite_boolean_and_inapplicable_unit_rejected(self):
        for value in (float('nan'), float('inf'), True):
            self.task['evidence'][0]['value'] = value
            with self.subTest(value=value), self.assertRaises(ContractError):
                self.bind('管理费0.2%。')
        self.task['evidence'][0].update(value=.2, unit='不适用')
        part = {'source': 'evidence', 'ref': 'a', 'path': 'value', 'format': 'signed', 'digits': 2}
        with self.assertRaises(ContractError):
            self.bind(numeric_segments={'answer': [part]})

    def test_nonnumeric_evidence_does_not_shadow_valid_sources(self):
        self.task['evidence'][0].update(value=None, raw_value=None, unit='不适用', raw_unit='不适用')
        self.bind('B年费率0.6%。')
        self.bind('A年费率0.2%。')  # The complete quantity remains in retained text.

    def test_user_threshold_is_bound_only_as_explicit_condition(self):
        self.task['question'] = '检验本案例10%现值溢价条件。'
        record = self.bind('本案例10%现值溢价条件尚待检验。')['record']
        self.assertEqual(record['fields']['answer']['bindings'][0]['source_kind'], 'user_condition')
        with self.assertRaises(ContractError):
            self.bind('现值溢价10%。')
        with self.assertRaises(ContractError):
            self.bind('现值溢价10%，条件充分。')

    def test_fractional_notation_and_unknown_unit_denominator(self):
        self.bind('B费率为.6%。')
        for text in ('B费率为－0.6%。', 'B费率为.6%/小时。', 'A比B高.4个百分点。'):
            with self.subTest(text=text), self.assertRaises(ContractError):
                self.bind(text)

    def test_rounding_cannot_erase_nonzero_value(self):
        for text in ('管理费0%。', '差值0个百分点。'):
            with self.subTest(text=text), self.assertRaises(ContractError):
                self.bind(text)
        for kind, ref, path in [('evidence', 'a', 'value'), ('calculation', 'c', 'value')]:
            part = {'source': kind, 'ref': ref, 'path': path, 'format': 'signed', 'digits': 0}
            with self.subTest(kind=kind), self.assertRaises(ContractError):
                self.bind(numeric_segments={'answer': [part]})

    def test_another_explicit_calculation_may_supply_correct_direction(self):
        other = copy.deepcopy(self.task['calculations'][0])
        other['id'] = 'd'
        other['output']['value'] = .4
        self.task['calculations'].insert(0, other)
        result = bind_numeric_statement(self.task, {'id': 'q1', 'answer': 'A比B低0.4个百分点。'},
                                        ['answer'], ['a', 'b'], ['d', 'c'])
        self.assertEqual(result['record']['fields']['answer']['bindings'][0]['ref'], 'c')

    def test_zero_comparison_and_multiple_text_fields(self):
        self.task['calculations'][0]['output']['value'] = 0
        part = {'source': 'calculation', 'ref': 'c', 'path': 'value', 'format': 'comparison', 'digits': 2}
        self.assertIn('相同', self.bind(numeric_segments={'answer': [part]})['fields']['answer'])
        proposal = {'question_ids': ['q1'], 'title': '费用核对', 'investment_meaning': '仅核对年费率。', 'limitation': '不推断收益。'}
        result = bind_numeric_statement(self.task, proposal, ['title', 'investment_meaning', 'limitation'], ['a', 'b'], ['c'])
        self.assertEqual(set(result['record']['fields']), {'title', 'investment_meaning', 'limitation'})

    def test_retained_fee_fixture_binds_without_model_or_provider(self):
        from tests import test_management_custody_fee_offline as fee_fixture
        fixture = fee_fixture.ManagementCustodyFeeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task, host = fixture.prepare()
        final = fixture.agent.start(task['id'], host)
        self.assertEqual(final['status'], '已完成', final.get('error'))
        question = final['questions'][0]
        result = bind_numeric_statement(final, question, ['answer'], question['refs'], question['calculation_refs'])
        self.assertTrue(result['record']['fields']['answer']['bindings'])

    def document_task(self):
        from tests.test_document_content_offline import DocumentContentTests
        fixture = DocumentContentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        text = '租金原件\n\n费用为3元/平方米，具体条件按原件执行。'
        task = fixture.material_task(text=text)
        spec = fixture.spec(task, text='费用为3元/平方米，具体条件按原件执行。')
        final = fixture.run_spec(task, spec)
        self.assertEqual(final['status'], '已完成', final.get('error'))
        return final, final['questions'][0]

    def test_document_only_binds_unknown_financial_unit_as_exact_original(self):
        task, question = self.document_task()
        result = bind_numeric_statement(task, question, ['answer'], question['refs'], [])
        self.assertIn('3元/平方米', result['fields']['answer'])
        self.assertEqual(result['record']['mode'], 'document_content')
        binding = result['record']['fields']['answer']['bindings'][0]
        self.assertEqual(binding['projection'], 'verbatim_original')
        self.assertNotIn('canonical_value', binding)
        proposal = dict(question, numeric_integrity=result['record'])
        validate_numeric_statement(task, proposal, ['answer'], question['refs'], [])

    def test_document_only_rejects_rewriting_segments_and_normal_research_reuse(self):
        task, question = self.document_task()
        for proposal in (dict(question, answer=question['answer'].replace('3元', '4元')),
                         dict(question, numeric_segments={'answer': [{'text': question['answer']}]})):
            with self.subTest(proposal=proposal), self.assertRaises(ContractError):
                bind_numeric_statement(task, proposal, ['answer'], question['refs'], [])
        with self.assertRaises(ContractError):
            bind_numeric_statement(dict(task, intent='综合研究'), question, ['answer'], question['refs'], [])

    def test_receipts_ignore_verification_wrappers_but_bind_substantive_sources(self):
        proposal = {'id': 'q1', 'answer': 'A费率为0.2%，A比B低0.4个百分点。'}
        result = bind_numeric_statement(self.task, proposal, ['answer'], ['a', 'b'], ['c'])
        proposal['numeric_integrity'] = result['record']
        for source in self.task['evidence'] + self.task['calculations']:
            source.update(verification={'renewed': True}, input_hash='renewed-wrapper')
        validate_numeric_statement(self.task, proposal, ['answer'], ['a', 'b'], ['c'])
        baseline = copy.deepcopy(self.task)
        mutations = [('evidence', 'value', .21), ('evidence', 'unit', '元'),
                     ('evidence', 'object', '另一基金'), ('evidence', 'content_version', 2),
                     ('calculations', 'inputs', {'fee_a': .3, 'fee_b': .6}),
                     ('calculations', 'output', {'value': -.40001, 'unit': '百分点/年'})]
        for collection, key, value in mutations:
            self.task = copy.deepcopy(baseline)
            self.task[collection][0][key] = value
            with self.subTest(collection=collection, key=key), self.assertRaises(ContractError):
                validate_numeric_statement(self.task, proposal, ['answer'], ['a', 'b'], ['c'])

    def test_segments_cannot_assemble_even_valid_quantities_from_text(self):
        self.task['evidence'][0].update(value=.15, raw_value=.15, text='管理费0.15%。')
        with self.assertRaises(ContractError):
            self.bind(numeric_segments={'answer': [{'text': '0.15'}, {'text': '%'}]})
