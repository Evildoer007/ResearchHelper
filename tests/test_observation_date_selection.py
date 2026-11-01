"""Date navigation excludes exactly what the unchanged adoption gate rejects."""
import copy
import unittest

from tests import test_current_original_availability as fixture
from tests.probe_etf_extraction_context import payload_for
from researchhelper.contracts import ContractError
from researchhelper.evidence import original_date_is_nonobservational, validate_current_snapshot
from researchhelper.output_schema import (original_span_output_schema, _original_span_choices,
    original_span_prompt, decode_original_spans, StructuredOutputError)
from researchhelper.planning import input_fingerprint


class ObservationDateSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        provider = fixture.CurrentOriginalAvailabilityTests()
        cls.addClassCleanup(provider.doCleanups)
        provider.setUp()
        cls.addClassCleanup(provider.tearDown)
        cls.legacy = copy.deepcopy(provider.task)
        cls.legacy['questions'][0]['evidence_needs'] = ['季度原件所载报告期间']
        # Compare the date-role policy alone. Duration and prompt compression
        # have independent suites; this synthetic source retains actual bytes.
        for key in ('original_observation_selection_version', 'original_duration_anchor_version',
                    'shared_original_dates_version'):
            cls.legacy.pop(key, None)
        cls.legacy['input_hash'] = input_fingerprint(cls.legacy)
        provider.task = cls.legacy
        bundle = provider.reading()
        cls.views = bundle['views']
        cls.legacy['reasoning_state'] = dict(reading=dict(bundle=bundle))
        cls.task = copy.deepcopy(cls.legacy)
        cls.task['original_observation_selection_version'] = 1
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.schema = original_span_output_schema(cls.task, cls.views)

    def wire(self):
        choice = _original_span_choices(self.task, self.views)[0]
        variant = next(v for v in self.schema['properties']['selections']['items']['anyOf']
            if v['properties']['source_ref']['enum'] == [choice['source_ref']]
            and v['properties']['kind']['enum'] == ['text'])
        props = variant['properties']
        link = props['review']['properties']['need_links']['items']['anyOf'][0]['properties']
        question = link['question_id']['enum'][0]
        period = next(p['ref'] for p in choice['period_anchors'] if p['period'].startswith('2026'))
        item = dict(
            id='ev_date', source_ref=choice['source_ref'], kind='text', object=props['object']['enum'][0],
            nature='合同政策条款',
            temporal_reference=dict(period_ref=period), review=dict(scope='current_original_descriptive_only',
                need_links=[dict(question_id=question, need_ids=[link['need_ids']['items']['enum'][0]])]))
        if 'text_reference' in props:
            item['text_reference'] = dict(line=0)
        else:
            item['text_span'] = dict(start=0,end=0)
        if 'question_ids' in props:
            item['question_ids'] = [question]
        return dict(version=12, binding_ref=self.schema['properties']['binding_ref']['enum'][0], selections=[item])

    def test_exact_existing_label_rule_not_general_date_approval(self):
        for label in ('送出日期', '发布日期', '公告日期', '披露日期', '签署日期', '生效日期', '编制日期'):
            with self.subTest(label=label):
                self.assertTrue(original_date_is_nonobservational(label+'：2026年8月28日', '2026年8月28日', '2026-08-28'))
        self.assertFalse(original_date_is_nonobservational('报告期末2026年6月30日', '2026年6月30日', '2026-06-30'))
        self.assertFalse(original_date_is_nonobservational('报告期末2026年6月30日', '2026年8月28日', '2026-08-28'))

    def test_actual_source_rejected_indices_not_offered_but_not_renumbered(self):
        choices = _original_span_choices(self.task, self.views)
        choice = choices[0]
        record = next(r for r in self.task['source_records'] if r['id'] == choice['view']['id'])
        parts = {part['locator']: part['text'] for part in record['snapshot']['material']['parts']}
        dates = choice['description']['dates']
        rejected = [i for i,date in enumerate(dates) if original_date_is_nonobservational(
            parts[date['part']], date['quote'], date['date'])]
        self.assertTrue(rejected)
        self.assertEqual(choice['observation_indices'], [i for i in range(len(dates)) if i not in rejected])
        variant = self.schema['properties']['selections']['items']['anyOf'][0]
        offered = variant['properties']['temporal_reference']['anyOf'][0]['properties']['observation_anchor']['enum']
        self.assertEqual(offered, choice['observation_indices'])

    def test_disclosure_selection_rejected_before_business_expansion(self):
        choice = _original_span_choices(self.task, self.views)[0]
        excluded = next(i for i in range(len(choice['description']['dates'])) if i not in choice['observation_indices'])
        wire = self.wire()
        wire['selections'][0]['temporal_reference'] = dict(observation_anchor=excluded)
        with self.assertRaises(StructuredOutputError):
            decode_original_spans(wire, self.schema, (self.task, self.views))

    def test_literal_reporting_period_still_passes_original_gate(self):
        spec = decode_original_spans(self.wire(), self.schema, (self.task, self.views))['evidence_specs'][0]
        self.assertEqual(spec['observation_date'], '2026-06-30')
        self.assertTrue(validate_current_snapshot(spec, self.task))
        self.assertNotIn('applicability_check', spec)

    def test_legacy_task_keeps_its_original_date_choices(self):
        legacy = original_span_output_schema(self.legacy, self.views)
        dates = _original_span_choices(self.legacy, self.views)[0]['description']['dates']
        offered = legacy['properties']['selections']['items']['anyOf'][0]['properties']['temporal_reference']['anyOf'][0]['properties']['observation_anchor']['enum']
        self.assertEqual(offered, list(range(len(dates))))

    def test_prompt_partitions_all_date_locations_without_renumbering_or_deletion(self):
        prompt = original_span_prompt(payload_for(self.task), self.task)
        for view, choice in zip(prompt['untrusted_sources'], _original_span_choices(self.task, self.views)):
            columns = view.get('observation_anchor_columns')
            def index(row):
                return row['index'] if isinstance(row, dict) else row[columns.index('index')]
            allowed = [index(row) for row in view['observation_anchors']]
            excluded = [index(row) for row in view.get('non_observation_dates', [])]
            self.assertEqual(allowed, choice['observation_indices'])
            self.assertEqual(sorted(allowed + excluded), list(range(len(choice['description']['dates']))))
            source = next(s for s in prompt['evidence_contract']['sources'] if s['artifact_id'] == choice['view']['id'])
            reference = source.get('original_date_anchors_ref')
            if excluded and reference:
                self.assertEqual(reference['tables'], ['observation_anchors','non_observation_dates'])

    def test_policy_bound_and_unknown_source_not_accepted(self):
        self.assertNotEqual(input_fingerprint(self.task), input_fingerprint(self.legacy))
        task = copy.deepcopy(self.task)
        task['source_records'] = []
        with self.assertRaises(ContractError):
            _original_span_choices(task, self.views)


if __name__ == '__main__':
    unittest.main()
