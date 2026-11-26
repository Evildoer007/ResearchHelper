"""Exercise the current public Skill workflow without live data or models."""
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from core.session import Session
from core.tests.test_research_workflow_matrix_offline import specification
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, ENTRIES, INTENTS, canonical


def matrix_cases():
    return [specification(entry, intent) for entry in ENTRIES for intent in INTENTS
            if intent != DOCUMENT_CONTENT_INTENT]


def run_host_first_report_matrix(session, cases):
    """Reuse this public-entry replay in the independent Skill installation."""
    from pathlib import Path
    import hashlib
    from lxml import html

    results = []
    for case in cases:
        task = session.create(case['question'], entry=case['entry'], intent=case['intent'],
                              objects=case['objects'], questions=case['questions'])
        task['synthetic'] = True
        session.archive.save(task)
        ident = task['id']
        refs = [session.material(ident, f'合成原件{index}.txt', statement.encode())['id']
                for index, statement in enumerate(case['statements'], 1)]
        checkpoint = session.draft(ident, data_requests=[], defer_delivery=True)
        assert checkpoint['status'] == '待开始' and checkpoint.get('host_checkpoint') and not checkpoint.get('delivery')
        tools = session.tools(ident, scope='first_report')
        first = tools['first_report']
        documents = {row['source_ref']: row for row in first['writing_context']['documents']}
        assert set(documents) == set(refs)
        for ref, statement in zip(refs, case['statements']):
            assert any(part['text'] == statement for part in documents[ref]['parts'])
        assert first['independent_review_required'] is False
        assert 'argument_support' not in tools
        original_questions = [(row['id'], row['kind'], row['required']) for row in checkpoint['questions']]
        statement = ''.join(f'原文写道“{value.rstrip("。")}”。' for value in case['statements'])
        report = dict(title=case['finding'], conclusion=case['finding'] + '。',
                      report_scope=first['research_scope']['allowed_report_scopes'][0],
                      logics=[dict(title='已披露条件', analysis=statement, source_refs=refs)],
                      risks=['结论限于合成原件披露的范围。'])
        saved = session.draft(ident, report=report)
        assert saved['status'] == '部分完成' and saved['delivery']['kind'] == 'research_draft'
        assert saved['error'] is None and not saved['evidence'] and not saved['theses']
        assert saved['draft_report']['logics'][0]['source_refs'] == refs
        assert [(row['id'], row['kind'], row['required']) for row in saved['questions']] == original_questions
        target = Path(session.export(ident))
        assert target.is_file() and target.is_relative_to(session.project / 'result/researchhelper')
        raw = target.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == saved['delivery']['html_sha256']
        text = html.document_fromstring(raw).find('.//main').text_content()
        assert case['finding'] in text and all(value.rstrip('。') in text for value in case['statements'])
        reread = session.status(ident)
        assert reread['delivery'] == saved['delivery'] and reread['ledger'] == saved['ledger']
        assert all(reread['ledger']['used'][key] == 0 for key in ('model_calls', 'data_requests'))
        assert reread['ledger']['supplements'] == 0
        results.append(dict(entry=case['entry'], intent=case['intent'], report_scope=report['report_scope'],
                            documents=len(documents), status=reread['status'], report_sha256=saved['delivery']['html_sha256']))
    return results


class SkillFirstReportToolTests(unittest.TestCase):
    def setUp(self):
        project = tempfile.TemporaryDirectory(prefix='researchhelper-first-report-tools-')
        self.addCleanup(project.cleanup)
        self.session = Session(project.name)
        self.addCleanup(self.session.agent.close_runtime)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls'))
        guard.start()
        self.addCleanup(guard.stop)

    def stock(self):
        return self.session.create('比较600519.SH的营业收入与经营现金流', objects=['600519.SH'])

    def test_projection_keeps_registered_data_access_and_reduces_host_payload(self):
        task = self.stock()
        full = self.session.tools(task['id'])
        projected = self.session.tools(task['id'], scope='first_report')
        for key in ('source_catalogue', 'first_report', 'research_brief', 'planning_definitions'):
            self.assertEqual(projected[key], full[key])
        # First-report tools can acquire account policy when needed. Keep the
        # complete original grammar and expose the three registered business
        # lookups without requiring a speculative policy request from the host.
        all_requests, first_requests = full['data_requests'], projected['data_requests']
        self.assertEqual({key: value for key, value in all_requests.items() if key != 'tools'},
                         {key: value for key, value in first_requests.items() if key != 'tools'})
        all_tools = {(tool['source'], tool['operation']): tool for tool in all_requests['tools']}
        first_tools = {(tool['source'], tool['operation']): tool for tool in first_requests['tools']}
        self.assertEqual(set(first_tools) - set(all_tools),
            {('ifind', operation) for operation in ('search_edb', 'get_indicators', 'basic_data_service')})
        self.assertTrue(set(all_tools).issubset(first_tools))
        for key, tool in all_tools.items():
            self.assertEqual({name: value for name, value in tool.items() if name != 'guidance'},
                {name: value for name, value in first_tools[key].items() if name != 'guidance'})
        self.assertNotIn('evidence', projected)
        self.assertTrue(full['argument_support']['required'])
        self.assertLess(len(canonical(projected).encode()), .6 * len(canonical(full).encode()))
        self.assertEqual(self.session.status(task['id']), task)

    def test_scope_typos_and_original_reading_bindings_do_not_fall_back(self):
        task = self.stock()
        with self.assertRaises(ContractError):
            self.session.tools(task['id'], scope='unknown')
        with self.assertRaises(ContractError):
            self.session.tools(task['id'], scope='first_report', reading_hash='foreign')
        with self.assertRaises(ContractError):
            self.session.tools(task['id'], scope='acquisition', reading_hash='foreign')
        self.assertEqual(self.session.status(task['id']), task)

    def test_first_report_tools_keep_the_bound_followup_context(self):
        for historical in (False, True):
            with self.subTest(historical=historical):
                options = {'cutoff': '2026-10-30'} if historical else {}
                parent = self.session.create('比较600519.SH与ETF510300.SH的表现', **options)
                report = dict(title='市场对照', conclusion='按各自范围核对。', logics=[dict(
                    title='市场差异', analysis='对照不同类型资产。', risk='原报告独有的反证。')])
                done = self.session.draft(parent['id'], report=report)
                self.assertTrue(done.get('delivery'))
                self.assertNotIn('conversation_context', self.session.tools(parent['id'], scope='first_report')['first_report'])
                child = self.session.follow_up(parent['id'], '只看后者，补充逻辑1的反证')
                self.assertEqual(child['objects'], ['510300.SH'])
                tools = self.session.tools(child['id'], scope='first_report')
                projected = tools['first_report']['conversation_context']
                full = self.session.tools(child['id'])
                acquisition = self.session.tools(child['id'], scope='acquisition')
                self.assertEqual(acquisition['data_requests'], tools['data_requests'])
                self.assertEqual(acquisition['conversation_context'],
                                 full['evidence']['research_scope']['conversation_context'])
                self.assertEqual(projected, full['analysis']['research_scope']['conversation_context'])
                self.assertNotIn('原报告独有的反证', canonical(acquisition))
                self.assertNotIn('first_report', acquisition)
                self.assertNotIn('analysis', acquisition)
                self.assertIn('不是本版事实', tools['first_report']['conversation_rule'])
                if historical:
                    self.assertEqual(projected['focus'], dict(status='historical_reference_only', logic_indices=[1]))
                    self.assertNotIn('原报告独有的反证', canonical(projected))
                else:
                    self.assertEqual(projected['focus']['logics'][0]['risk']['text'], '原报告独有的反证。')

    def test_document_only_task_keeps_its_original_reading_contract(self):
        task = self.session.create('核对原件写了什么', intent=DOCUMENT_CONTENT_INTENT)
        with self.assertRaises(ContractError):
            self.session.tools(task['id'], scope='first_report')
        full = self.session.tools(task['id'])
        self.assertIn('evidence', full)
        self.assertNotIn('first_report', full)
        acquisition = self.session.tools(task['id'], scope='acquisition')
        self.assertNotIn('prepare', acquisition)
        self.assertEqual(acquisition['data_requests'], full['data_requests'])

    def test_acquisition_exposes_explicit_window_without_dispatching_it(self):
        question = '核对510300.SH在2026-10-29至2026-10-30的单位净值涨跌幅。'
        task = self.session.create(question)
        self.session.draft(task['id'], data_requests=[], defer_delivery=True)
        tools = self.session.tools(task['id'], scope='acquisition')
        request, = tools['stated_window_requests']
        self.assertEqual((request['source'], request['operation']), ('ifind', 'date_sequence'))
        self.assertEqual((request['parameters']['startdate'], request['parameters']['enddate']),
                         ('2026-10-29', '2026-10-30'))
        self.assertEqual(self.session.status(task['id'])['ledger']['used']['data_requests'], 0)
        restricted = self.session.create(question+'只用本地材料。')
        self.assertNotIn('stated_window_requests', self.session.tools(restricted['id'], scope='acquisition'))

    def test_corrupt_raw_calculation_record_does_not_enter_writing_context(self):
        task = self.stock()
        prepared = self.session.draft(task['id'], defer_delivery=True)
        invalid = dict(id='cached-but-not-reproducible', display='999元', value=999)
        prepared['draft_analysis']['results'].append(invalid)
        self.session.archive.save(prepared)
        first = self.session.tools(task['id'], scope='first_report')['first_report']
        self.assertEqual(first['writing_context']['calculations'], [])
        self.assertIn(invalid, self.session.status(task['id'])['draft_analysis']['results'])

    def test_local_save_recovery_is_available_in_the_projected_tools(self):
        task = self.stock()
        report = dict(title='保存恢复检查', conclusion='已提交的正文在本地继续保存。')
        with patch('researchhelper.draft_workflow.normalize_report', side_effect=OSError('Controlled local error')):
            failed = self.session.draft(task['id'], report=report)
        self.assertEqual(failed['status'], '失败')
        recovery = self.session.tools(task['id'], scope='first_report')['delivery_recovery']
        self.assertTrue(recovery['retained_submission'])
        self.assertFalse(recovery['local_model_calls'] or recovery['data_requests'])
        saved = self.session.retry_save(task['id'])
        self.assertEqual(saved['draft_report']['conclusion'], report['conclusion'])
        self.assertEqual(saved['ledger']['used']['model_calls'], 0)
        self.assertEqual(saved['ledger']['used']['data_requests'], 0)

    def test_all_40_current_entry_intent_pairs_save_a_first_report(self):
        rows = run_host_first_report_matrix(self.session, matrix_cases())
        self.assertEqual(len(rows), 40)
        self.assertEqual(len({(row['entry'], row['intent']) for row in rows}), 40)


if __name__ == '__main__':
    unittest.main()
