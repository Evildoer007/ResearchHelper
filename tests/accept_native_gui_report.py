"""Review only the HTML files saved through the installed App's native Save dialog.

This acceptance check never starts research, contacts a provider, or calls a model.
The Accessibility action log and screenshots establish how the files were created.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unittest
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'result/acceptance/native-gui-current'
DOCUMENT_PATTERN = re.compile(
    r'<script id="rh-publication-document" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def load_export(path: Path) -> tuple[str, dict]:
    html = path.read_text(encoding='utf-8')
    match = DOCUMENT_PATTERN.search(html)
    if match is None:
        raise ValueError(f'Missing frozen publication data: {path.name}')
    return html, json.loads(match.group(1))


class NativeGuiReportAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = json.loads((EVIDENCE / 'session.json').read_text())
        cls.standard_html, cls.standard = load_export(Path(cls.session['report_path']))
        cls.detailed_html, cls.detailed = load_export(Path(cls.session['detailed_report_path']))
        cls.rows = cls.standard['dataset']['rows']

    def test_research_was_sent_once_through_app_ui(self):
        self.assertFalse(self.session['research_started_by_backend'])
        sends = [a for a in self.session['gui_actions']
                 if a.get('target') == '发送研究问题']
        self.assertEqual(len(sends), 1)
        self.assertEqual(self.session['app'], '/Applications/ResearchHelper.app')
        saves = [a for a in self.session['gui_actions']
                 if a.get('action') == 'native_save_completed']
        self.assertEqual(len(saves), 2)

    def test_native_exports_match_recorded_file_hashes(self):
        for path_key, hash_key in [
            ('report_path', 'report_sha256'),
            ('detailed_report_path', 'detailed_report_sha256'),
        ]:
            digest = hashlib.sha256(Path(self.session[path_key]).read_bytes()).hexdigest()
            self.assertEqual(digest, self.session[hash_key])

    def test_profiles_share_the_same_research_dataset(self):
        self.assertEqual(self.standard['template']['id'], 'report-standard')
        self.assertEqual(self.detailed['template']['id'], 'report-detailed')
        self.assertEqual(self.standard['dataset'], self.detailed['dataset'])
        self.assertGreater(len(self.detailed['blocks']), len(self.standard['blocks']))

    def test_requested_fields_and_dates_are_present(self):
        grouped = defaultdict(list)
        for code, field, date, value, unit, provider in self.rows:
            self.assertEqual(code, '600519.SH')
            self.assertEqual(provider, 'iFinD')
            self.assertTrue(math.isfinite(float(value)))
            grouped[field].append((date, value, unit))
        self.assertEqual(len(self.rows), 20)
        for field in ['营业收入', '归属于母公司所有者的净利润',
                      '经营活动产生的现金流量净额']:
            self.assertEqual({r[0] for r in grouped[field]}, {'20241231', '20251231'})
        for field in ['收盘价', 'PE(TTM)']:
            self.assertEqual(len(grouped[field]), 7)
            self.assertEqual(max(r[0] for r in grouped[field]), '2026-10-28')

    def test_exported_financial_values_match_visible_chart_evidence(self):
        visible = json.loads((EVIDENCE / '25-chart-evidence-ui.json').read_text())
        texts = {str(c.get('title', '')) for c in visible['controls']}
        for _, field, _, value, _, _ in self.rows:
            if field not in {'收盘价', 'PE(TTM)'}:
                self.assertIn(str(value), texts)

    def test_registered_changes_recompute_from_raw_values(self):
        grouped = defaultdict(list)
        for _, field, date, value, _, _ in self.rows:
            grouped[field].append((date, Decimal(str(value))))
        by_label = {c['label']: c for c in self.standard['dataset']['calculations']}
        for field in ['收盘价', '营业收入', '归属于母公司所有者的净利润',
                      '经营活动产生的现金流量净额']:
            rows = sorted(grouped[field])
            expected = (rows[-1][1] / rows[0][1] - 1) * 100
            label = '收盘价变化' if field == '收盘价' else f'{field}原值同比变化'
            self.assertAlmostEqual(float(expected), by_label[label]['value'], places=9)

    def test_logic_and_offline_charts_are_in_the_actual_exports(self):
        for html, document in [(self.standard_html, self.standard),
                               (self.detailed_html, self.detailed)]:
            headings = [b.get('text', '') for b in document['blocks']
                        if b['kind'] == 'heading']
            self.assertEqual(sum(h.startswith('策略逻辑') for h in headings), 3)
            self.assertEqual(sum(b['kind'] == 'chart' for b in document['blocks']), 4)
            self.assertIn('echarts', html)
            self.assertIsNone(re.search(r'<script\b[^>]*\bsrc=["\']https?://', html))
            self.assertIn('600519.SH', html)


def write_receipt(result: unittest.TestResult):
    session = NativeGuiReportAcceptance.session
    document = NativeGuiReportAcceptance.standard
    usage = session['gui_observed_usage']
    receipt = {
        'schema': 'researchhelper.native-gui-acceptance',
        'version': 1,
        'candidate': session['candidate'],
        'app': session['app'],
        'installation_receipt': session['installation_receipt'],
        'research_initiation': 'native_app_gui_send_button',
        'export_method': 'native_macos_save_dialog',
        'gui_workflow_passed': result.wasSuccessful(),
        'checks_run': result.testsRun,
        'failures': len(result.failures),
        'errors': len(result.errors),
        'question': session['question'],
        'usage_observed_in_app': usage,
        'data_rows': len(document['dataset']['rows']),
        'charts': sum(b['kind'] == 'chart' for b in document['blocks']),
        'report_paths': [session['report_path'], session['detailed_report_path']],
        'screenshots': [str(EVIDENCE / name) for name in [
            '06-question-ready.png', '07-running.png', '21-budget-sources.png',
            '24-detailed.png', '25-chart-evidence.png', '27-final-report.png',
        ]],
        'additional_model_calls_for_export_or_review': 0,
        'client_ready_analysis_accepted': False,
        'findings': [
            'Financial amounts and PE still display an unverified source unit.',
            'The overall task remains partial although all four execution nodes succeeded and both reports were saved.',
            'The cash-flow logic still needs the financial-subsidiary explanation and operating-item decomposition.',
            'Seven recent price/PE observations do not establish fair valuation or a causal investment thesis.',
            'The request asked for the latest quote, but the App fetched seven trading days.',
            'The native Save dialog defaults to a generic version filename instead of the named report profile.',
        ],
        'all_prd_accepted': False,
    }
    (EVIDENCE / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: receipt[k] for k in [
        'gui_workflow_passed', 'checks_run', 'data_rows', 'charts',
        'usage_observed_in_app', 'client_ready_analysis_accepted',
    ]}, ensure_ascii=False))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeGuiReportAcceptance)
    outcome = unittest.TextTestRunner(verbosity=1).run(suite)
    write_receipt(outcome)
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
