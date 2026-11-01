"""PDF compatibility glyphs retain raw quotes and explicit calendar semantics."""
import copy
import unittest

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import original_date_is_nonobservational, validate_publication_basis
from researchhelper.financial_sources import (literal_interval, original_date_anchors,
    original_date_is_interval_boundary, original_interval_spans, original_period_anchors,
    _literal_financial_period)
from tests import test_material_publication_spacing as publication_fixture
from tests import test_current_original_availability as current_fixture


class LiteralDateGlyphTests(unittest.TestCase):
    def test_literal_date_anchor_preserves_the_exact_glyphs_and_spaces(self):
        for quote in ('2026 年6 ⽉30 ⽇', '2026年6月30日', '2026年6⽉30日', '2026-06-30'):
            view = dict(parts=[dict(locator='第1页', text='报告期末：' + quote)])
            before = copy.deepcopy(view)
            anchors = original_date_anchors(view)
            with self.subTest(quote=quote):
                self.assertEqual(anchors, [dict(date='2026-06-30', part='第1页', quote=quote)])
                self.assertEqual(view, before)
                self.assertEqual(_literal_financial_period(quote), '2026-06-30')

    def test_complete_interval_preserves_quote_without_inventing_quarter_boundaries(self):
        for quote in ('2026 年4 ⽉1 ⽇⾄2026 年6 ⽉30 ⽇',
                      '2026年4月1日至2026年6月30日', '2026-04-01到2026-06-30'):
            with self.subTest(quote=quote):
                self.assertEqual(literal_interval(quote), '2026-04-01/2026-06-30')
                view = dict(parts=[dict(locator='第1页', text=quote)])
                result = original_period_anchors(view, representation_version=2)
                self.assertEqual(result[0]['quote'], quote)
                self.assertEqual(result[0]['period'], '2026-04-01/2026-06-30')

    def test_shared_year_is_explicit_and_opt_in(self):
        quote = '2026 年4 ⽉1 ⽇⾄6 ⽉30 ⽇'
        with self.assertRaises(ContractError): literal_interval(quote)
        self.assertEqual(literal_interval(quote, allow_shared_year=True), '2026-04-01/2026-06-30')
        self.assertEqual(list(original_interval_spans(quote))[0]['quote'], quote)

    def test_interval_endpoints_do_not_become_independent_daily_observations(self):
        start, end = '2026 年4 ⽉1 ⽇', '2026 年6 ⽉30 ⽇'
        text = '报告期：' + start + '⾄' + end
        self.assertTrue(original_date_is_interval_boundary(text, start, '2026-04-01'))
        self.assertTrue(original_date_is_interval_boundary(text, end, '2026-06-30'))
        self.assertFalse(original_date_is_interval_boundary(text+'\n报告期末：'+end, end, '2026-06-30'))
        self.assertFalse(original_date_is_interval_boundary(text, '2026年6月30日', '2026-06-30'))

    def test_invalid_reversed_and_incomplete_dates_are_not_repaired(self):
        for quote in ('2026年4⽉1⽇⾄2⽉30⽇', '2026年12⽉1⽇⾄1⽉30⽇',
                      '2026年4⽉1⽇⾄30⽇', '2026年4⽉1⽇⾄2026年2⽉30⽇'):
            with self.subTest(quote=quote), self.assertRaises((ContractError, ValueError)):
                literal_interval(quote, allow_shared_year=True)
        self.assertEqual(original_date_anchors(dict(parts=[dict(locator='页1', text='2026年2⽉30⽇')])), [])

    def test_date_roles_stay_distinct_for_extracted_radicals(self):
        quote = '2026 年 7 ⽉21 ⽇'
        for label in ('送出⽇期', '发布⽇期', '公告⽇期', '披露⽇期', '签署⽇期', '⽣效⽇期', '编制⽇期'):
            with self.subTest(label=label):
                self.assertTrue(original_date_is_nonobservational(label+'：'+quote, quote, '2026-07-21'))
        self.assertFalse(original_date_is_nonobservational('报告期末：'+quote, quote, '2026-07-21'))


class PublicationGlyphTests(unittest.TestCase):
    def setUp(self):
        self.provider = publication_fixture.MaterialPublicationSpacingTests()
        self.provider.setUp()
        self.addCleanup(self.provider.doCleanups)
        self.addCleanup(self.provider.agent.close_runtime)

    def test_explicit_publication_preserves_original_date_glyphs(self):
        for prefix in ('发布⽇期：', '公告⽇期：'):
            with self.subTest(prefix=prefix):
                date = '2026 年 7 ⽉19 ⽇'
                task = self.provider.material(date, prefix=prefix)
                before = fingerprint(task)
                validate_publication_basis(self.provider.spec(task, prefix+date+'。'), task['materials'])
                self.assertEqual(fingerprint(task), before)
                with self.assertRaises(ContractError):
                    validate_publication_basis(self.provider.spec(task, prefix+'2026 年 7 月19 日。'), task['materials'])

    def test_other_date_cannot_borrow_an_earlier_publication_cue(self):
        date = '2026 年 7 ⽉19 ⽇'
        for label in ('⽣效⽇期', '交易⽇', '观测⽇', '实施⽇期', '编制⽇期', '落款⽇期', '截⾄'):
            with self.subTest(label=label):
                prefix = '已发布，'+label+'：'
                task = self.provider.material(date, prefix=prefix)
                with self.assertRaises(ContractError):
                    validate_publication_basis(self.provider.spec(task, prefix+date+'。'), task['materials'])

    def test_future_planned_invalid_and_intraday_dates_still_fail(self):
        for date, prefix, published, cutoff in (
            ('2026 年 7 ⽉19 ⽇', '计划发布⽇期：', '2026-07-19', '2026-07-20T18:00:00+08:00'),
            ('2026 年 2 ⽉30 ⽇', '发布⽇期：', '2026-02-28', '2026-07-20T18:00:00+08:00'),
            ('2026 年 7 ⽉21 ⽇', '发布⽇期：', '2026-07-21', '2026-07-20T18:00:00+08:00'),
            ('2026 年 7 ⽉19 ⽇', '发布⽇期：', '2026-07-19', '2026-07-19T12:00:00+08:00')):
            with self.subTest(date=date, prefix=prefix, cutoff=cutoff):
                task = self.provider.material(date, prefix=prefix, cutoff=cutoff)
                spec = self.provider.spec(task, prefix+date+'。', published)
                final = self.provider.agent.start(task['id'], dict(evidence_specs=[spec], answers={}, theses=[]))
                self.assertNotEqual(final['status'], '已完成')
                self.assertFalse(final['evidence'])


class ParsedOriginalGlyphTests(unittest.TestCase):
    def setUp(self):
        self.provider = current_fixture.CurrentOriginalAvailabilityTests()
        self.provider.setUp()
        self.addCleanup(self.provider.doCleanups)

    def tearDown(self): self.provider.tearDown()

    def test_current_text_only_original_builds_schema_without_a_numeric_union(self):
        from researchhelper.output_schema import original_span_output_schema
        from researchhelper.planning import input_fingerprint
        task = copy.deepcopy(self.provider.task)
        task['questions'][0]['evidence_needs'] = ['季度原件所载报告期间']
        task['input_hash'] = input_fingerprint(task)
        schema = original_span_output_schema(task, self.provider.reading()['views'])
        self.assertIsNotNone(schema)
        variants = schema['properties']['selections']['items']['anyOf']
        self.assertTrue(any(v['properties'].get('kind', {}).get('enum') == ['text'] for v in variants))

    def test_actual_pdf_has_distinct_report_interval_and_period_end(self):
        record, task = self.provider.record, self.provider.task
        before = fingerprint(task)
        part = record['snapshot']['material']['parts'][0]
        self.assertIn(current_fixture.OBSERVATION_QUOTE, part['text'])
        self.assertIn('⾄', part['text'])
        spans = list(original_interval_spans(part['text']))
        self.assertEqual([span['period'] for span in spans], ['2026-04-01/2026-06-30'])
        self.assertTrue(original_date_is_interval_boundary(part['text'], '2026 年4 ⽉1 ⽇', '2026-04-01'))
        self.assertFalse(original_date_is_interval_boundary(part['text'], current_fixture.OBSERVATION_QUOTE, '2026-06-30'))
        item = self.provider.candidate()
        self.assertEqual(item['source_check'], '通过')
        self.assertEqual(item['applicability_check'], '待核验')
        self.assertIsNone(item['published_at'])
        self.assertEqual(fingerprint(task), before)


if __name__ == '__main__': unittest.main()
