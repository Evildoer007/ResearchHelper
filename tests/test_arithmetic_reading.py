"""Arithmetic readings stay reproducible and usable in a draft report.

The table fixtures are explicitly synthetic. They test calculation and writing
contracts, not supplier permissions or independently approved research.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_EVEN, Inexact, Rounded, localcontext
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.draft_analysis import analyze, current_analysis, execute, writing_calculations
from researchhelper.draft_sources import observations
from researchhelper.drafts import compose, normalize_report, source_binding
from researchhelper.publication_content import prepare_draft_client_copy
from researchhelper.report_designer import design

RESOURCES = Path(os.environ.get('RESEARCHHELPER_TEST_RESOURCES', Path(__file__).resolve().parents[1]))


def scenario(prior=800, current=809):
    task = dict(id='synthetic-arithmetic-reading', version=1, input_hash='synthetic-reading',
        synthetic=True, cutoff='2026-10-31T23:00:00+08:00', source_records=[], materials=[],
        objects=['600000.SH'], entry='个股', intent='综合研究', question='比较两年财务原值',
        draft_source_reader_version=8, draft_client_copy_version=1,
        questions=[], evidence=[], calculations=[], knowledge={})
    fields = ('ths_revenue_stock', 'ths_np_stock', 'ths_ncf_from_oa_stock')
    for period, values in (('20241231', (prior, 10, 20)), ('20251231', (current, 12, 18))):
        parameters = dict(codes='600000.SH', indipara=[
            dict(indicator=field, indiparams=[period, '1']) for field in fields])
        snapshot = dict(errorcode=0, tables=[dict(thscode='600000.SH', time=['2026-10-31'],
            table={field:[value] for field, value in zip(fields, values, strict=True)})])
        record = dict(id='synthetic-source-'+period, kind='api', source='ifind',
            operation='basic_data_service', task_id=task['id'], research_version=1,
            status='成功待核验', parameters=parameters, snapshot=snapshot, hash=fingerprint(snapshot))
        record['request_hash'] = fingerprint({key:record[key] for key in ('source', 'operation', 'parameters')})
        task['source_records'].append(record)
    facts = observations(task)
    task['draft_analysis'] = analyze(task, facts)
    return task, facts


class ArithmeticReadingTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_registered_calculations_follow_one_half_up_reading_rule(self):
        cases = [
            ('raw_change_v1', dict(a=800, b=809)),
            ('raw_difference_v1', dict(a=0, b='1.125')),
            ('raw_percentage_points_v1', dict(a=0, b='1.125')),
            ('raw_percent_v1', dict(a=9, b=800)),
            ('raw_multiple_v1', dict(a=9, b=8)),
            ('raw_margin_v1', dict(a=800, b=791)),
        ]
        for method, values in cases:
            with self.subTest(method=method):
                before = deepcopy(values)
                result = execute(method, values)
                self.assertEqual(result['display'], '1.13')
                self.assertEqual(Decimal(result['exact']), Decimal('1.125'))
                self.assertEqual(result['value'], 1.125)
                self.assertEqual(values, before)
        self.assertEqual(execute('raw_difference_v1', dict(a=0, b='-1.125'))['display'], '-1.13')

    def test_nonzero_results_keep_their_sign_instead_of_rounding_to_zero(self):
        for value in ('0.004', '-0.0001', '0.000001', '-0.000000123456', '1e-100'):
            with self.subTest(value=value):
                result = execute('raw_difference_v1', dict(a=0, b=value))
                shown = Decimal(result['display'])
                self.assertNotEqual(shown, 0)
                self.assertEqual(shown.is_signed(), Decimal(value).is_signed())
                self.assertEqual(Decimal(result['exact']), Decimal(value))

    def test_true_zero_has_one_unsigned_display(self):
        for rounding in (ROUND_DOWN, ROUND_UP, ROUND_HALF_EVEN):
            with localcontext() as context:
                context.rounding = rounding
                self.assertEqual(execute('raw_difference_v1', dict(a='1.125', b='1.125'))['display'], '0.00')
                self.assertEqual(execute('raw_percent_v1', dict(a='-0.000', b=10))['display'], '0.00')

    def test_the_host_decimal_context_does_not_change_a_saved_result(self):
        expected = execute('raw_multiple_v1', dict(a=1, b=24))
        for precision in (6, 28, 50):
            for rounding in (ROUND_DOWN, ROUND_UP, ROUND_HALF_EVEN):
                with self.subTest(precision=precision, rounding=rounding), localcontext() as context:
                    context.prec = precision
                    context.rounding = rounding
                    before = context.copy()
                    self.assertEqual(execute('raw_multiple_v1', dict(a=1, b=24)), expected)
                    self.assertEqual((context.prec, context.rounding), (before.prec, before.rounding))

    def test_host_traps_and_exponent_limits_do_not_leak_into_the_calculator(self):
        expected = execute('raw_multiple_v1', dict(a=1, b=24))
        with localcontext() as context:
            context.prec = 6
            context.Emax = 5
            context.Emin = -5
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            self.assertEqual(execute('raw_multiple_v1', dict(a=1, b=24)), expected)
            self.assertEqual(Decimal(execute('raw_difference_v1', dict(a=0, b='1e100'))['display']), Decimal('1e100'))
            self.assertTrue(context.traps[Inexact] and context.traps[Rounded])
            self.assertEqual((context.prec, context.Emax, context.Emin), (6, 5, -5))

    def test_nearly_equal_inputs_keep_their_small_relative_change(self):
        current = '1.'+'0'*49+'1'
        result = execute('raw_change_v1', dict(a='1', b=current))
        self.assertEqual(Decimal(result['exact']), Decimal('1e-48'))
        self.assertEqual(Decimal(result['display']), Decimal('1e-48'))
        self.assertNotEqual(result['value'], 0)

    def test_decimal_overflow_and_malformed_inputs_raise_the_local_contract_error(self):
        for values in (None, [], 1, dict(a='1e999999', b='1e-999999')):
            with self.subTest(values=values), self.assertRaises(ContractError):
                execute('raw_multiple_v1', values)

    def test_ordinary_readings_and_raw_ranges_keep_their_existing_meaning(self):
        self.assertEqual(execute('raw_change_v1', dict(a=100, b=120))['display'], '20.00')
        self.assertEqual(execute('raw_difference_v1', dict(a=0, b='-5.84'))['display'], '-5.84')
        self.assertEqual(execute('raw_multiple_v1', dict(a=303, b=100))['display'], '3.03')
        self.assertEqual(execute('raw_range_v1', dict(a='1.125', b='2.000')),
            dict(minimum='1.125', maximum='2.000', display='1.125至2.000'))

    def test_finite_large_results_do_not_fail_only_because_of_display_precision(self):
        result = execute('raw_difference_v1', dict(a=0, b='1e100'))
        self.assertEqual(Decimal(result['display']), Decimal('1e100'))
        self.assertEqual(result['value'], 1e100)

    def test_unrepresentable_nonzero_float_cannot_be_published_as_zero(self):
        with self.assertRaises(ContractError):
            execute('raw_multiple_v1', dict(a='1e-300', b='1e300'))

    def test_invalid_inputs_and_inapplicable_ratios_remain_local_failures(self):
        for method, values in [
            ('raw_change_v1', dict(a=0, b=10)),
            ('raw_percent_v1', dict(a=1, b=0)),
            ('raw_multiple_v1', dict(a=True, b=10)),
            ('raw_difference_v1', dict(a='NaN', b=1)),
            ('raw_margin_v1', dict(a=10, b=-1)),
            ('raw_range_v1', dict(a=10, b=1)),
        ]:
            with self.subTest(method=method, values=values), self.assertRaises(ContractError):
                execute(method, values)

    def test_the_writer_and_client_copy_use_the_same_rounding_as_the_calculation(self):
        task, facts = scenario()
        before = canonical((task, facts))
        writing = writing_calculations(task, facts)
        self.assertEqual(writing[0]['display'], '1.13')
        value = dict(title='合成读数核对', conclusion='营业收入由800元升至809元。', logics=[])
        result, _ = prepare_draft_client_copy(task, value, facts)
        self.assertEqual(result['conclusion'], '营业收入同比1.13%。')
        self.assertEqual(canonical((task, facts)), before)
        self.assertFalse(task['draft_analysis']['financial_approval'])

    def test_small_change_survives_normalization_and_both_html_profiles(self):
        task, facts = scenario(1000000, 999999)
        proposal = dict(title='合成微小变化', conclusion='营业收入由1000000元降至999999元。',
            logics=[dict(title='原值变化', analysis='营业收入同比-0.0001%。', calculation_refs=['A1'])])
        report = normalize_report(proposal, task, facts)
        self.assertIn('-0.0001%', report['conclusion'])
        self.assertIn('-0.0001%', report['logics'][0]['analysis'])
        report.update(facts=facts, calculations=current_analysis(task, facts)['results'], source_binding=source_binding(task))
        task['draft_report'] = report
        content = compose(task)
        before = canonical(content)
        for profile in ('standard', 'detailed'):
            with self.subTest(profile=profile):
                result = design(content, RESOURCES, profile=profile)
                self.assertIn('-0.0001%', result['html'])
                self.assertNotIn('-0.00%', result['html'])
                self.assertEqual(canonical(content), before)

    def test_old_rounded_cache_is_not_rewritten_or_treated_as_a_current_result(self):
        task, facts = scenario()
        first = task['draft_analysis']['results'][0]
        first['display'] = '1.12'
        before = canonical(task)
        current = current_analysis(task, facts)
        self.assertNotIn(first['id'], {row['id'] for row in current['results']})
        self.assertTrue(current['results'])
        self.assertEqual(canonical(task), before)

    def test_an_unrepresentable_ratio_does_not_remove_other_results(self):
        task, facts = scenario()
        for row in facts:
            if row['period'] == '20251231' and row['field'] == 'ths_np_stock':
                row.update(raw_value='1e300', value=1e300)
            if row['period'] == '20251231' and row['field'] == 'ths_ncf_from_oa_stock':
                row.update(raw_value='1e-300', value=1e-300)
        result = analyze(task, facts)
        self.assertFalse(any(row['method'] == 'raw_multiple_v1' and row['period'] == '20251231'
                             for row in result['results']))
        self.assertTrue(result['results'])
        self.assertTrue(result['notes'])
