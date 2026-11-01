"""Read-only R160 proposal replay under an explicitly projected new policy.

No old study is changed or resumed, and no live acquisition/model is invoked.
The original manual proposals are reused verbatim in detached memory; only the
new policy flag is added to the detached task for this regression experiment.
"""

import copy
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from researchhelper.archive_codec import decode_body
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, validate_calculation_context, validate_evidence
from researchhelper.evidence import candidate_evidence


def retained_task():
    configured = os.environ.get('RESEARCHHELPER_FINANCIAL_CONTEXT_FIXTURE')
    folder = Path(__file__).resolve().parents[1] / 'result/acceptance/one-sentence-research-nwflnem4'
    path = Path(configured) if configured else folder / 'task.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    if configured:
        raise FileNotFoundError(path)
    database = folder / 'store/researchhelper.sqlite3'
    connection = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = connection.execute('SELECT body FROM tasks').fetchall()
        if len(rows) != 1:
            raise AssertionError('Expected one isolated R160 task')
        return decode_body(connection, rows[0][0])
    finally:
        connection.close()


class FixedFinancialContextInheritanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.retained = retained_task()

    def setUp(self):
        self.task = copy.deepcopy(self.retained)
        self.task['fixed_financial_context_version'] = 1
        self.record = next(row for row in self.task['source_records']
                           if row.get('operation') == 'basic_data_service')
        self.specs = [copy.deepcopy(spec) for spec in self.task['host_result']['evidence_specs']
                      if spec.get('artifact_id') == self.record['id']]
        self.assertEqual(len(self.specs), 3)
        self.assertTrue(all(not {'calculation_context', 'calculation_context_basis', 'api_context_reference'}
                            .intersection(spec) for spec in self.specs))
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline replay only'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_three_real_manual_proposals_receive_source_bound_context_and_pass_two_methods(self):
        before = copy.deepcopy((self.task, self.specs))
        items = {}
        for spec in self.specs:
            item = candidate_evidence(self.task, self.record, spec)
            self.assertEqual(item['applicability_check'], '待核验')
            context = item['calculation_context']
            self.assertEqual({key: context[key] for key in ('entity_id', 'currency', 'period',
                'accounting_scope', 'accounting_standard')}, dict(entity_id='600519.SH', currency='CNY',
                period='2026年1-6月', accounting_scope='consolidated', accounting_standard='CAS'))
            for key, basis in item['calculation_context_basis'].items():
                self.assertEqual(basis, dict(kind='api', field=key, artifact_id=self.record['id'],
                                            snapshot_hash=self.record['hash']))
            ResearchAnalyzer().assess(self.task, item, spec['review'], self.task['source_records'])
            validate_evidence(item, self.task)
            items[context['metric']] = item
        self.assertEqual(set(items), {'revenue', 'net_profit', 'operating_cash_flow'})
        parameters = dict(amount_unit='元', currency='CNY', period='2026年1-6月',
                          accounting_scope='consolidated', accounting_standard='CAS')
        validate_calculation_context('net_profit_margin_v1',
            {key: items[key] for key in ('revenue', 'net_profit')}, parameters)
        validate_calculation_context('operating_cash_profit_ratio_v1',
            {key: items[key] for key in ('operating_cash_flow', 'net_profit')}, parameters)
        self.assertEqual(before, (self.task, self.specs))

    def test_legacy_and_boolean_flags_do_not_inherit_context(self):
        for flag in (None, True):
            self.task['fixed_financial_context_version'] = flag
            item = candidate_evidence(self.task, self.record, self.specs[0])
            self.assertNotIn('calculation_context', item)
            self.assertNotIn('calculation_context_basis', item)

    def test_explicit_wrong_currency_is_not_replaced(self):
        valid = candidate_evidence(self.task, self.record, self.specs[0])
        wrong = copy.deepcopy(self.specs[0])
        wrong.update(calculation_context=valid['calculation_context'],
                     calculation_context_basis=valid['calculation_context_basis'])
        wrong['calculation_context']['currency'] = 'USD'
        with self.assertRaisesRegex(ContractError, '不能由当前API'):
            candidate_evidence(self.task, self.record, wrong)
        self.assertEqual(wrong['calculation_context']['currency'], 'USD')

    def test_explicit_null_or_incomplete_reference_is_not_repaired(self):
        for values in (dict(calculation_context=None), dict(calculation_context={}),
                       dict(api_context_reference={})):
            with self.subTest(values=values), self.assertRaises(ContractError):
                candidate_evidence(self.task, self.record, dict(self.specs[0], **values))
        item = candidate_evidence(self.task, self.record,
                                  dict(self.specs[0], calculation_context_basis={}))
        self.assertNotIn('calculation_context', item)

    def test_absent_definitions_do_not_supply_context_or_values(self):
        with patch('researchhelper.evidence._controlled_financial_definitions', return_value={}):
            with self.assertRaises(ContractError):
                candidate_evidence(self.task, self.record, self.specs[0])

    def test_cross_task_and_wrong_period_remain_rejected(self):
        foreign = copy.deepcopy(self.task)
        foreign['id'] = 'unrelated_task'
        with self.assertRaises(ContractError):
            candidate_evidence(foreign, self.record, self.specs[0])
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, self.record, dict(self.specs[0], period='2026年1-9月'))

    def test_ambiguous_or_invalid_locator_is_not_repaired(self):
        for locator in (dict(table=0, row=True, field='ths_np_stock'),
                        dict(table=0, row=1, field='ths_np_stock'),
                        dict(table=0, row=0, field='unknown')):
            with self.subTest(locator=locator), self.assertRaises(ContractError):
                candidate_evidence(self.task, self.record, dict(self.specs[0], record_locator=locator))
