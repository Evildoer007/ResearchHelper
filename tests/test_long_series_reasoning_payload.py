"""Large synthetic price series; check wire projections without external I/O."""
import copy
from datetime import date, timedelta
from tempfile import TemporaryDirectory
import unittest
import threading
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical, charge, fingerprint, now
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (
    _record_current_acquisition, candidate_evidence, direct_code_reference, snapshot_record,
)
from researchhelper.reasoning import (
    analysis_contract, calculation_plan_contract, evidence_contract, MAX_CONTEXT_CHARACTERS,
)


def synthetic_price_task(agent, *, days=None, query_window=None):
    code = '600000.SH'
    task = agent.create('核对合成收盘序列', entry='个股', objects=[code], research_mode='current',
        questions=[dict(id='q2', question='收盘序列如何变化？', kind='fact',
                        evidence_needs=['逐日收盘原值'], method_needs=['observed_price_return_v1']),
                   dict(id='q3', question='公开材料说明了什么？', kind='fact',
                        evidence_needs=['材料原文'], method_needs=[])])
    task['synthetic'] = True
    if days is None:
        calendar = [date(2026, 7, 8) + timedelta(days=offset) for offset in range(90)]
        days = [day.isoformat() for day in calendar if day.weekday() < 5][:61]
    values = [10 + index / 100 for index in range(len(days))]
    first, last = query_window or (days[0], days[-1])
    params = dict(codes=code, indicators='close', startdate=first, enddate=last,
                  functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
    payload = dict(errorcode=0, tables=[dict(thscode=code, time=days, table=dict(close=values))])
    record = snapshot_record(task, 'ifind', 'cmd_history_quotation', params, payload)
    charge(task['ledger'], 'data_requests', 1)
    event = dict(kind='data_requests', source='ifind', operation=record['operation'], batch=task['batch'],
                 fingerprint=record['request_hash'], status='成功', time=now(), transport_isolated=True,
                 network_request_started=True, network_worker_reaped=True, synthetic_test_transport=True)
    task['ledger']['dispatches'].append(event)
    _record_current_acquisition(task, record, event)
    record['question_ids'] = ['q2']
    task['source_records'] = [record]
    agent._adopt_method_knowledge(task)
    contract = evidence_contract(task)
    source, = contract['sources']
    identity = direct_code_reference(task, record)
    review = dict(question_ids=['q2'], allowed_natures=['已实现事实'],
                  conditions=['合成行情，仅检查长序列传输'], reason='逐项核对原值、代码和日期。',
                  evidence_need_ids={'q2': [row['id'] for row in contract['question_requirements']['q2']]})
    for row, (day, value) in enumerate(zip(days, values)):
        spec = dict(id=f'price_{row + 1}', artifact_id=record['id'], object=code,
                    record_locator=dict(table=0, row=row, field='close'), period=day,
                    nature='已实现事实', unit='元', published_at=None,
                    publication=contract['current_publication_template'], availability=source['current_availability_reference'],
                    raw_value=value, raw_unit='元', value=value, conversion_factor=1, question_ids=['q2'],
                    direct_code_reference=identity, api_context_reference=source['compact_context_reference'],
                    calculation_context=dict(security_id=code, currency='CNY', price_basis='unadjusted',
                        price_kind='close', observation_date=day, window_start=first,
                        window_end=last, corporate_action_status='unknown'))
        item = candidate_evidence(task, record, spec)
        task['evidence'].append(agent.analyzer.assess(task, item, review, [record]))
    return task


class LongSeriesReasoningPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with TemporaryDirectory() as folder, patch('socket.socket.connect', side_effect=AssertionError('No network')):
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True)
            try:
                cls.original = synthetic_price_task(agent)
            finally:
                agent.close_runtime()

    def setUp(self):
        self.digest = fingerprint(self.original)
        self.task = copy.deepcopy(self.original)

    def tearDown(self):
        self.assertEqual(fingerprint(self.original), self.digest)

    def restored(self,payload):
        lookup={field:{entry['ref']:entry['value'] for entry in entries}
                for field,entries in payload['evidence_shared_metadata'].items()}
        records=copy.deepcopy(payload['evidence'])
        for record in records:
            for field,entries in lookup.items():
                if field+'_ref' in record:
                    record[field]=entries[record.pop(field+'_ref')]
        return records

    def test_complete_business_values_and_metadata_reconstruct_exactly(self):
        before=copy.deepcopy(self.task)
        ordinary=ResearchAgent._reasoning_evidence(self.task)
        projected=ResearchAgent._reasoning_evidence_payload(self.task)
        for left,right in zip(ordinary,self.restored(projected)):
            if 'calculation_context_basis' in left:
                left.pop('calculation_context_basis')
                reference=right.pop('calculation_basis_archive_ref')
                self.assertEqual(reference['evidence_id'],left['id'])
                self.assertEqual(reference['snapshot_hash'],left['snapshot_hash'])
            self.assertEqual(left,right)
        self.assertEqual(len(projected['evidence']),61)
        self.assertEqual(self.task,before)

    def test_full_analysis_and_binding_fit_without_increasing_context_limit(self):
        ordinary=ResearchAgent._reasoning_evidence(self.task)
        self.assertGreater(len(canonical(ordinary)),MAX_CONTEXT_CHARACTERS)
        projected=ResearchAgent._reasoning_evidence_payload(self.task)
        self.assertLess(len(canonical(dict(projected,analysis_contract=analysis_contract(self.task),
            questions=self.task['questions'],knowledge=self.task['knowledge']))),MAX_CONTEXT_CHARACTERS)
        self.assertLess(len(canonical(dict(projected,calculation_plan_contract=calculation_plan_contract(self.task),
            questions=self.task['questions']))),MAX_CONTEXT_CHARACTERS)

    def test_unknown_publication_and_action_limits_remain_available(self):
        records=self.restored(ResearchAgent._reasoning_evidence_payload(self.task))
        for record in records:
            self.assertEqual(record['publication']['certainty'],'未知')
            self.assertIsNone(record['published_at'])
            self.assertEqual(record['calculation_context']['corporate_action_status'],'unknown')

    def test_equal_looking_different_metadata_is_not_merged(self):
        self.task['evidence'][0]['publication']['basis']='不同的原件说明'
        projected=ResearchAgent._reasoning_evidence_payload(self.task)
        self.assertEqual(self.restored(projected)[0]['publication']['basis'],'不同的原件说明')
        self.assertEqual(projected['evidence'][0]['publication']['basis'],'不同的原件说明')

    def test_selection_does_not_leak_evidence_from_other_questions(self):
        projected=ResearchAgent._reasoning_evidence_payload(self.task,['q3'])
        self.assertEqual(projected['evidence'],[])
        self.assertEqual(projected['evidence_shared_metadata'],{})

    def test_projection_does_not_choose_inputs_approve_facts_or_change_ledger(self):
        before=copy.deepcopy(self.task['ledger'])
        result=ResearchAgent._reasoning_evidence_payload(self.task)
        self.assertFalse(result['evidence_projection']['financial_approval'])
        self.assertFalse(result['evidence_projection']['automatic_input_selection'])
        self.assertEqual(self.task['ledger'],before)

    def test_public_read_rejects_foreign_empty_and_duplicate_question_scope(self):
        agent=ResearchAgent.__new__(ResearchAgent);agent.lock=threading.RLock()
        agent._get=lambda ident:self.task
        for selected in ([],['foreign'],['q2','q2'],True):
            with self.subTest(selected=selected),self.assertRaises(ContractError):
                agent.reasoning_inputs(self.task['id'],selected)
        before=copy.deepcopy(self.task)
        self.assertEqual(agent.reasoning_inputs(self.task['id'],['q2']),
            ResearchAgent._reasoning_evidence_payload(self.task,['q2']))
        self.assertEqual(self.task,before)

    def test_projection_binds_task_version_batch_and_cutoff_without_reset(self):
        payload=ResearchAgent._reasoning_evidence_payload(self.task)
        binding=payload['evidence_projection']['binding']
        self.assertEqual(binding['id'],self.task['id'])
        self.assertEqual(binding['version'],self.task['version'])
        self.assertEqual(binding['batch'],self.task['batch'])
        self.assertEqual(binding['cutoff'],self.task['cutoff'])


if __name__=='__main__':
    unittest.main()
