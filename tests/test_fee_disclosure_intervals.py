"""Annual fee comparison preserves literal reporting dates from both PDFs."""
import unittest

from tests.fee_disclosure_fixture import FUNDS, METHOD, PERIOD, fee_task, read_fee_task
from researchhelper.contracts import calculate, fingerprint, validate_calculation_context
from researchhelper.evidence import candidate_evidence
from researchhelper.output_schema import decode_fee_original_output, fee_original_output_schema, fee_original_prompt
from researchhelper.source_reader import validate_read_specs


def fee_proposal(task, bundle):
    views = bundle['views']
    schema = fee_original_output_schema(task, views)
    if schema['properties']['version']['enum'] != [2]:
        raise AssertionError('A wrapped literal period lost the fee-reference protocol')
    prompt = fee_original_prompt(dict(untrusted_sources=views), task)
    sources = []
    for source in prompt['source_choices']:
        candidates = source['fee_candidates']
        period, = candidates['periods']
        fund, = candidates['funds']
        share = next(row for row in candidates['shares'] if row['role']=='listed_share_code')
        need, = source['question_requirements']['q1']
        rates = [dict(id=source['source_ref']+'_'+row['metric'], object=source['object_binding']['value'],
            rate_reference=row['reference'],period_reference=period['reference'],fund_reference=fund['reference'],
            share_reference=share['reference'],question_ids=['q1'],
            need_links=[dict(question_id='q1',need_ids=[need['id']])]) for row in candidates['rates']]
        sources.append(dict(source_ref=source['source_ref'],rates=rates))
    wire = dict(version=2,binding_ref=prompt['fee_binding_ref'],sources=sources)
    return decode_fee_original_output(wire,schema,(task,views))['evidence_specs']


class FeeDisclosureIntervalTests(unittest.TestCase):
    def setUp(self):
        self.task = fee_task()
        self.bundle = read_fee_task(self.task)

    def test_wrapped_complete_dates_remain_selectable_in_the_current_fee_protocol(self):
        views = self.bundle['views']
        self.assertEqual(len(views),2)
        self.assertTrue(any('日起\n至' in part['text'] for part in views[1]['parts']))
        schema = fee_original_output_schema(self.task,views)
        self.assertEqual(schema['properties']['version']['enum'],[2])
        legacy = dict(self.task)
        legacy.pop('original_duration_anchor_version')
        self.assertEqual(fee_original_output_schema(legacy,views)['properties']['version']['enum'],[1])

    def test_four_rates_keep_source_units_and_compare_the_same_complete_interval(self):
        before = fingerprint(self.task)
        proposals = fee_proposal(self.task,self.bundle)
        self.assertEqual(len(proposals),4)
        validate_read_specs(self.task,proposals,self.bundle)
        originals = {row['id']:row for row in self.task['source_records']}
        rates = {}
        for proposal in proposals:
            evidence = candidate_evidence(self.task,originals[proposal['artifact_id']],proposal)
            self.assertEqual((evidence['raw_unit'],evidence['unit'],evidence['conversion_factor']),('%','%',1))
            self.assertEqual(evidence['source_check'],'通过')
            self.assertEqual(evidence['period'],PERIOD)
            self.assertEqual(evidence['reporting_period_basis']['version'],3)
            self.assertIsNone(evidence['published_at'])
            side = 'a' if evidence['object']==FUNDS[0] else 'b'
            role = evidence['calculation_context']['metric'].removesuffix('_fee')
            rates[role+'_'+side] = evidence
        parameters = dict(unit='%/年',period=PERIOD)
        validate_calculation_context(METHOD,rates,parameters)
        result = calculate(METHOD,{key:row['value'] for key,row in rates.items()},parameters)
        self.assertAlmostEqual(result['value'],-0.05)
        self.assertEqual(result['unit'],'百分点/年')
        self.assertEqual(fingerprint(self.task),before)
