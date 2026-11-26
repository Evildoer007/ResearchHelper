"""One retained API cell is one observation, even under two admitted aliases.

Sources are generated locally and go through the actual evidence gates. These
checks make no supplier/model calls and do not change retained studies.
"""
from copy import deepcopy
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('RESEARCHHELPER_TEST_RESOURCES', Path(__file__).resolve().parents[1]))
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import charge, fingerprint, missing_evidence_requirements, validate_evidence
from researchhelper.data_requirements import FIELD, count_satisfied, pending, processing_state
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint
from researchhelper.evidence import (api_cell_choices, candidate_evidence,
    expand_api_cell_selection, snapshot_record, _record_current_acquisition)

NEED = '核对所需的不同价格观测'


class ObservationCountIdentityTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls'))
        guard.start(); self.addCleanup(guard.stop)
        folder = TemporaryDirectory(prefix='rh-observation-count-')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True,
                                  draft_first=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)

    def fixture(self, *, days=2, fields=('close',), minimum=2):
        question = dict(id='q1', question='核对两条不同的价格观测', kind='fact', required=True,
            evidence_needs=[NEED], **{FIELD: {NEED: dict(value_kind='number',
                unit_dimension='price', minimum_count=minimum, period_kind='point')}})
        task = self.agent.create(question['question'], entry='ETF', objects=['510300.SH'],
                                 research_mode='current', questions=[question])
        # Explicit host plans can remain legacy-compatible; this fixture
        # deliberately opts into the typed data contract before any receipt.
        task['typed_evidence_needs_version'] = 1
        task['input_hash'] = input_fingerprint(task)
        return task, self.record(task, ['2026-09-21', '2026-09-30'][:days], fields)

    def record(self, task, dates, fields=('close',)):
        params = dict(codes='510300.SH', indicators=','.join(fields), startdate=dates[0],
            enddate=dates[-1], functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        response = dict(errorcode=0, tables=[dict(thscode='510300.SH', time=dates,
            table={field: [4] * len(dates) for field in fields})])
        record = snapshot_record(task, 'ifind', 'cmd_history_quotation', params, response)
        # This is a simulated dispatch receipt, not a supplier request.
        charge(task['ledger'], 'data_requests')
        event = dict(kind='data_requests', source='ifind', operation=record['operation'],
            fingerprint=record['request_hash'], batch=task['batch'], status='成功',
            transport_isolated=True, network_request_started=True, network_worker_reaped=True)
        task['ledger']['dispatches'].append(event)
        _record_current_acquisition(task, record, event)
        record['question_ids'] = ['q1']; task['source_records'].append(record)
        return record

    def admit(self, task, record, index, *, alias=False):
        choice = api_cell_choices(task, record)[index]
        review = dict(question_ids=['q1'], allowed_natures=['已实现事实'],
            conditions=['合成价格读数的软件检查'], reason='核对同源字段、单位和日期',
            evidence_need_ids={'q1': [fingerprint(NEED)]})
        spec = expand_api_cell_selection(task, dict(api_cell_selection=choice['candidate_id'],
            question_ids=['q1'], review=review))
        if alias:
            spec.update(id='same_cell_alias', text='同一价格读数的另一种文字说明。')
        item = ResearchAnalyzer().assess(task, candidate_evidence(task, record, spec),
                                        review, task['source_records'])
        validate_evidence(item, task)
        return item

    def test_two_separately_admitted_aliases_do_not_discharge_two_observations(self):
        task, record = self.fixture()
        first = self.admit(task, record, 0); alias = self.admit(task, record, 0, alias=True)
        self.assertNotEqual(first['text'], alias['text'])
        self.assertEqual(first['record_locator'], alias['record_locator'])
        self.assertFalse(count_satisfied(task['questions'][0], NEED, [first, alias]))
        self.assertTrue(missing_evidence_requirements(task['questions'][0], [first, alias]))
        self.assertEqual(task['ledger']['used']['model_calls'], 0)

    def test_equal_values_on_two_actual_dates_remain_two_observations(self):
        task, record = self.fixture()
        first = self.admit(task, record, 0); second = self.admit(task, record, 1)
        self.assertEqual(first['value'], second['value'])
        self.assertNotEqual(first['period'], second['period'])
        self.assertTrue(count_satisfied(task['questions'][0], NEED, [first, second]))
        self.assertEqual(missing_evidence_requirements(task['questions'][0], [first, second]), [])

    def test_same_price_in_two_request_windows_is_still_one_observation(self):
        task, record = self.fixture(days=1)
        expanded = self.record(task, ['2026-09-21', '2026-09-30'])
        first = self.admit(task, record, 0); repeated = self.admit(task, expanded, 0)
        second = self.admit(task, expanded, 1)
        self.assertNotEqual(first['artifact_id'], repeated['artifact_id'])
        self.assertNotEqual(first['artifact']['request_hash'], repeated['artifact']['request_hash'])
        self.assertEqual(first['period'], repeated['period'])
        self.assertFalse(count_satisfied(task['questions'][0], NEED, [first, repeated]))
        self.assertTrue(count_satisfied(task['questions'][0], NEED, [first, repeated, second]))

    def test_alias_order_and_repetitions_do_not_change_count(self):
        task, record = self.fixture()
        first = self.admit(task, record, 0); alias = self.admit(task, record, 0, alias=True)
        second = self.admit(task, record, 1)
        for items in ([first, alias] * 3, [alias, first], [alias] * 5):
            self.assertFalse(count_satisfied(task['questions'][0], NEED, items))
        self.assertTrue(count_satisfied(task['questions'][0], NEED, [first, alias, second]))

    def test_processing_and_pending_use_the_same_unique_cell_count(self):
        task, record = self.fixture()
        first = self.admit(task, record, 0); alias = self.admit(task, record, 0, alias=True)
        task['evidence'] = [first, alias]
        before = deepcopy(task)
        self.assertEqual(processing_state(task)['requirements'][0]['status'], 'adopted_partial')
        self.assertEqual(len(pending(task)), 1)
        self.assertEqual(task, before)
        task['evidence'].append(self.admit(task, record, 1))
        self.assertEqual(processing_state(task)['requirements'][0]['status'], 'adopted_sufficient')
        self.assertEqual(pending(task), [])

    def test_one_cell_can_still_satisfy_a_single_observation_requirement(self):
        task, record = self.fixture(minimum=1)
        item = self.admit(task, record, 0)
        self.assertTrue(count_satisfied(task['questions'][0], NEED, [item]))

    def test_counting_does_not_mutate_text_references_or_verification_receipts(self):
        task, record = self.fixture()
        items = [self.admit(task, record, 0), self.admit(task, record, 0, alias=True)]
        before = deepcopy(items)
        count_satisfied(task['questions'][0], NEED, items)
        self.assertEqual(items, before)
        for item in items: validate_evidence(item, task)

    def test_legacy_excerpts_without_an_api_address_keep_their_declared_identity(self):
        need = '公司营业收入'
        question = dict(id='q1', evidence_needs=[need], **{FIELD: {need: dict(value_kind='number',
            unit_dimension='amount', minimum_count=2, period_kind='interval')}})
        one = dict(id='excerpt', object='600030.SH', text='原文行', value=100, unit='元',
            period='2026-01-01/2026-06-30', record_locator=dict(row=0), question_ids=['q1'],
            applicability_review=dict(evidence_need_ids={'q1': [fingerprint(need)]}))
        two = dict(one, id='other_excerpt', text='另一原文行', record_locator=dict(row=1))
        self.assertTrue(count_satisfied(question, need, [one, two]))

    def test_missing_second_observation_does_not_block_the_first_draft(self):
        task, record = self.fixture(days=1)
        task['evidence'] = [self.admit(task, record, 0), self.admit(task, record, 0, alias=True)]
        self.agent.archive.save(task)
        done = self.agent.start(task['id'], dict(data_requests=[], draft_report=dict(
            title='单日价格观察', report_scope='fact', conclusion='本次仅观察已取得的单日读数。',
            logics=[dict(title='已取得读数', analysis='单日读数不代表区间走势。', data_refs=['D1'])])))
        self.assertTrue(done.get('delivery'), done.get('error'))
        self.assertEqual(done['status'], '部分完成')
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        frozen = self.agent.archive.report(done['id'], done['version'])
        self.assertIn('单日读数不代表区间走势', frozen['html'])
        pending(done)
        self.assertEqual(self.agent.archive.report(done['id'], done['version']), frozen)


if __name__ == '__main__':
    unittest.main()
