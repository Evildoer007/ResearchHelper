"""Retained-source, authored-proposal delivery checks; never model quality.

No old study is resumed. These fresh private tasks exercise the current codec,
two evidence gates, host checkpoint, explicit review, save and report reader.
"""
import copy
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import test_original_review_wire as fixtures
except ModuleNotFoundError:
    from tests import test_original_review_wire as fixtures

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import canonical, validate_evidence
from researchhelper.output_schema import original_span_output_schema, structured_json_completion, ORIGINAL_SPAN_PROTOCOL
from researchhelper.reports import compose, render
from researchhelper.visual import pmi_observation_figures, adopted_numeric_evidence
from researchhelper import argument_support

ROOT = Path(__file__).resolve().parents[1]


class OriginalDeliveryChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_SCOPE_WIRE_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes())) if supplied else fixtures.prepare_fixture()

    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('No API or public requests'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        directory = tempfile.TemporaryDirectory(prefix='rh-original-delivery-')
        self.addCleanup(directory.cleanup)
        self.resources = Path(os.environ.get('RESEARCHHELPER_SCOPE_WIRE_RESOURCES', str(ROOT)))
        self.archive = ResearchArchive(directory.name)
        self.agent = ResearchAgent(self.archive, self.resources, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = copy.deepcopy(self.fixture['task']); self.task['revision'] = 0
        self.archive.save(self.task)
        self.views = copy.deepcopy(self.fixture['views'])
        schema = original_span_output_schema(self.task, self.views)
        wire = fixtures.canonical_wire(self.task, self.views, schema)
        wire['version'] = 13; wire.pop('selections')
        for requirement in wire['requirements'].values():
            for item in requirement['items']: item.pop('review')
        self.specs = json.loads(structured_json_completion(json.dumps(wire), schema,
            protocol=ORIGINAL_SPAN_PROTOCOL, context=(self.task, self.views)))['evidence_specs']
        self.assertFalse(self.task['synthetic']); self.assertIsNone(self.task['fixture'])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def content(self):
        task = copy.deepcopy(self.task)
        for spec in self.specs:
            source = next(record for record in task['source_records'] if record['id'] == spec['artifact_id'])
            item = candidate_evidence(task, source, spec)
            ResearchAnalyzer().assess(task, item, spec['review'], task['source_records'])
            validate_evidence(item, task)
            task['evidence'].append(item)
        task['status'] = '部分完成'
        self.agent._gap(task, '离线受控提案，尚未提交语言分析，不是自主模型研究', category='question_coverage')
        return compose(task)

    def proposal(self):
        segments = [dict(text='留存原件的受控链路检查。本期与上期的制造业PMI、生产指数及新订单指数原值依次为：')]
        for index, spec in enumerate(self.specs):
            segments.extend([dict(source='evidence', ref=spec['id'], path='value', format='signed', digits=1),
                             dict(text='；' if index < len(self.specs)-1 else '。')])
        segments.append(dict(text='生产与新订单的变化并不同步。这里仅比较已披露指数，不推定企业利润、资产收益或事件因果，不代表实际模型研究质量已验收。'))
        refs = [spec['id'] for spec in self.specs]
        thesis = dict(title='生产与需求变化不同步', judgment='支持', question_ids=['q1'],
            support=[refs[index] for index in (0, 1, 3, 4)], counter=[refs[index] for index in (2, 5)],
            calculation_refs=[], investment_meaning='生产改善不能单独证明需求同步增强或资产回报改善。',
            limitation='仅比较已披露指数，不识别事件因果，不预测后续走势。')
        return dict(evidence=[], evidence_specs=copy.deepcopy(self.specs), data_requests=[], calculations=[],
            answers=dict(q1=dict(numeric_segments=dict(answer=segments), refs=[spec['id'] for spec in self.specs],
                                 calculation_refs=[])), theses=[thesis], judgment_reviews=[])

    def checkpoint(self):
        result = self.agent.start(self.task['id'], self.proposal(), defer_delivery=True)
        self.assertTrue(result.get('host_checkpoint'))
        self.assertEqual(len(result['evidence']), 6)
        self.assertFalse(result.get('delivery'))
        return result

    def review(self, *, supported=True):
        request = self.agent.argument_review_inputs(self.task['id'])['request']
        refs = ['evidence:' + spec['id'] for spec in self.specs]
        wire = dict(version=argument_support.VERSION, review_hash=request['review_hash'],
            questions=[dict(question_id='q1', status='answered' if supported else 'requires_evidence',
                missing_evidence=[] if supported else ['测试控制：分析依据不足'],
                reason='显式宿主复核控制，不是新的模型质量批准')],
            reviews=[dict(target_id=target['id'], segments=[dict(text=target['text'],
                verdict='supported' if supported else 'insufficient', basis_refs=refs if supported else [],
                explanation='检查保存与核验衔接，不执行第二模型或补造事实')]) for target in request['targets']])
        self.agent.submit_argument_review(self.task['id'], wire)

    def test_two_separate_releases_can_render_one_paired_chart(self):
        content = self.content(); before = canonical(content)
        self.assertEqual(len(adopted_numeric_evidence(content)), 6)
        self.assertEqual({item['object'] for item in content['evidence']}, {'中国制造业'})
        self.assertEqual(len({item['artifact_id'] for item in content['evidence']}), 2)
        figures = pmi_observation_figures(content)
        self.assertEqual(len(figures), 1)
        self.assertEqual(figures[0]['periods'], ['2026-08', '2026-09'])
        self.assertEqual([row['value'] for row in figures[0]['rows']], [49.8, 50.4, 50.6, 50.1, 51.7, 50.5])
        self.assertEqual(canonical(content), before)

    def test_uncited_or_missing_metric_is_not_filled(self):
        content = self.content()
        for selected in ({item['id'] for item in content['evidence'] if item['period'] == '2026-09'},
                         {item['id'] for item in content['evidence'][1:]}):
            self.assertEqual(pmi_observation_figures(content, selected), [])

    def test_changed_values_or_snapshots_do_not_inherit_adoption(self):
        for field in ('value', 'snapshot'):
            content = self.content()
            for item in content['evidence']:
                if field == 'value': item['value'] += 1
                else: item['artifact']['snapshot']['changed'] = True
            self.assertEqual(pmi_observation_figures(content), [])

    def test_reordering_adopted_evidence_does_not_change_period_or_metric_order(self):
        content = self.content(); expected = pmi_observation_figures(content)
        content['evidence'].reverse()
        self.assertEqual(pmi_observation_figures(content), expected)

    def test_swapped_need_links_cannot_relabel_a_source_value(self):
        content = self.content(); first, second = content['evidence'][1], content['evidence'][2]
        first['applicability_review']['evidence_need_ids'], second['applicability_review']['evidence_need_ids'] = (
            second['applicability_review']['evidence_need_ids'], first['applicability_review']['evidence_need_ids'])
        self.assertEqual(pmi_observation_figures(content), [])

    def test_unregistered_policy_or_missing_need_links_does_not_guess_generic_objects(self):
        for change in ('policy', 'coverage'):
            content = self.content()
            if change == 'policy': content.pop('statistical_metric_identity_version')
            else:
                for item in content['evidence']: item['applicability_review'].pop('evidence_need_ids', None)
            self.assertEqual(pmi_observation_figures(content), [])

    def test_partial_paper_shows_chart_without_promoting_research_completion(self):
        content = self.content(); page = render(content, self.resources)
        self.assertTrue('rh-pmi-comparison-1' in page, 'Paired PMI chart missing')
        self.assertTrue('研究分析尚未完成' in page, 'Partial research warning missing')
        self.assertFalse('<details' in page, 'Client report must not contain accordions')
        self.assertFalse(any(question.get('answer') for question in content['questions']))

    def test_checkpoint_explicit_review_save_restart_and_export(self):
        self.checkpoint(); self.review()
        reviewed = self.archive.get(self.task['id'])
        expected = argument_support.review_request(reviewed, reviewed['host_result'])
        done = self.agent.start(self.task['id'])
        current = argument_support.review_request(done, done['host_result'])
        changed = [key for key in expected if key not in ('review_hash', 'scope') and expected.get(key) != current.get(key)]
        self.assertEqual(done['status'], '已完成', dict(gaps=done['gaps'], changed_review_inputs=changed))
        report = self.agent.export(done['id'])
        self.assertTrue('rh-pmi-comparison-1' in report['html'], 'Saved paired PMI chart missing')
        self.assertEqual(report['html'].count("class='chart chart-grouped'"), 1)
        self.assertFalse('<details' in report['html'], 'Client report must not contain accordions')
        self.assertEqual(len(report['content']['evidence']), 6)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertIsNone(self.agent.model)
        reopened = ResearchAgent(self.archive, self.resources, offline=True, require_argument_support=True)
        try: self.assertEqual(reopened.export(done['id']), report)
        finally: reopened.close_runtime()
        destination = os.environ.get('RESEARCHHELPER_DELIVERY_CHAIN_EXPORT')
        if destination:
            Path(destination).write_text(report['html'], encoding='utf-8')
            Path(destination).with_suffix('.json').write_text(canonical(report['content']), encoding='utf-8')

    def test_absent_review_cannot_approve_the_authored_answer(self):
        self.checkpoint(); done = self.agent.start(self.task['id'])
        self.assertNotEqual(done['status'], '已完成')
        self.assertFalse(done['questions'][0].get('answer'))
        self.assertEqual(len(done['evidence']), 6)

    def test_insufficient_review_keeps_facts_without_approving_analysis(self):
        self.checkpoint(); self.review(supported=False)
        done = self.agent.start(self.task['id'])
        self.assertNotEqual(done['status'], '已完成')
        self.assertFalse(done['questions'][0].get('answer'))
        self.assertTrue('rh-pmi-comparison-1' in self.agent.export(done['id'])['html'], 'Adopted data chart erased')

