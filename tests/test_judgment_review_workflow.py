"""Independent judgment-review workflow, using disclosed synthetic originals.

No real data or model calls, no production Store or old-test modification.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import researchhelper.engine as implementation
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper import judgment_review as review
from researchhelper.output_schema import analysis_output_schema, decode_analysis_output
from researchhelper.reasoning import analysis_contract, analysis_fields, model_object
from researchhelper.reports import compose

ROOT = Path(__file__).resolve().parents[1]
OLD_TEXT = '合成政策原件，非真实金融材料。\n\n发布日期：2026年7月1日。\n\n适用对象为符合条件的企业。'
NEW_TEXT = '合成政策原件，非真实金融材料。\n\n发布日期：2026年8月1日。\n\n适用对象为满足新增资格要求的企业。'
RULE = dict(indicator='资格条件', source='本任务政策原件', window='2026-12-01至2026-12-31',
            condition='新增条款支持资格，撤回则不再适用。', benchmark='原资格条款')


class JudgmentReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='judgment-review-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        origin = getattr(implementation, '__frozen_origin__', None)
        resources = Path(origin.split('!/PYZ.pyz/')[0]).parents[2] if origin else None
        self.agent = ResearchAgent(ResearchArchive(folder.name), resources, offline=True)
        self.addCleanup(self.agent.close_runtime)
        guard = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def specification(self, task, *, pending=False):
        materials = task.get('pending_materials', []) if pending else task['materials']
        material = materials[-1]
        parts = material['snapshot']['parts']
        date = '2026-08-01' if '8月1日' in parts[1]['text'] else '2026-07-01'
        return dict(id='ev_rule', artifact_id=material['id'], object='合成政策',
            record_locator={'part': '段落3'}, text=parts[2]['text'], period=date,
            nature='合同政策条款', unit='不适用', published_at=date,
            publication=dict(at=date, basis='本合成原件明确发布日期', precision='日', timezone='Asia/Shanghai',
                certainty='已确认', basis_ref=dict(kind='material', artifact_id=material['id'],
                    snapshot_hash=material['snapshot_hash'], part='段落2', quote=parts[1]['text'])),
            question_ids=['q1'], review=dict(question_ids=['q1'], allowed_natures=['合同政策条款'],
                conditions=['只解释原件资格条款，不代表经营结果或证券收益'], reason='合成条款的适用资格明确',
                evidence_need_texts={'q1': ['适用资格']}))

    def host(self, task, *, pending=False, title='资格条款仍需满足', judgment='支持'):
        thesis = dict(title=title, judgment=judgment, question_ids=['q1'], support=['ev_rule'], counter=[],
            calculation_refs=[], investment_meaning='只讨论满足资格要求的企业。',
            limitation='条款不证明经营兑现或市场价格表现。', tracking=copy.deepcopy(RULE))
        return dict(evidence_specs=[self.specification(task, pending=pending)],
            answers={'q1': dict(answer='条款限定适用资格，不能据此证明经营结果。', refs=['ev_rule'])}, theses=[thesis])

    def first(self):
        task = self.agent.create('政策适用资格如何影响企业？', entry='事件', objects=['合成政策'],
            cutoff='2026-07-20T18:00:00+08:00', questions=[dict(id='q1', question='政策适用资格如何影响企业？',
                kind='mechanism', required=True, evidence_needs=['适用资格'])])
        task = self.agent.add_material(task['id'], 'old.txt', OLD_TEXT.encode())
        done = self.agent.start(task['id'], self.host(task))
        self.assertEqual(done['status'], '已完成', done.get('error'))
        return done

    def prepare(self, *, changed=True):
        old = self.first()
        self.frozen = copy.deepcopy(self.agent.archive.report(old['id'], 1))
        if changed:
            self.agent.add_material(old['id'], 'new.txt', NEW_TEXT.encode())
        return self.agent.prepare_update(old['id'], '2026-08-20T18:00:00+08:00')

    def proposal(self, task, *, changed=True, assessment='维持'):
        anchor = review.contract(task)['anchors'][0]
        dimensions = dict(
            facts=dict(state='依据有变化' if changed else '留存依据未变', answer='引用条款已有修订。' if changed else '引用条款保持原样。',
                       refs=['ev_rule'], calculation_refs=[]),
            mechanism=dict(state='支持', answer='条款仍限定资格，不直接证明经营兑现。', refs=['ev_rule'], calculation_refs=[]),
            expectations=dict(state='不适用', answer='本问题不比较预测与实际结果。', refs=[], calculation_refs=[]),
            price=dict(state='不适用', answer='本问题没有研究价格表现。', refs=[], calculation_refs=[]))
        return dict(prior_thesis_id=anchor['id'], prior_thesis_hash=anchor['thesis_hash'], prior_version=1,
            conditions_hash=anchor['conditions_hash'], assessment=assessment,
            current_thesis_indices=[0], dimensions=dimensions)

    def second(self, *, changed=True, reviews=True, mutate=None):
        task = self.prepare(changed=changed)
        host = self.host(task, title='本版资格判断')
        if reviews:
            host['judgment_reviews'] = [self.proposal(task, changed=changed)]
            if mutate: mutate(host['judgment_reviews'][0])
        self.raw_result = copy.deepcopy(host)
        result = self.agent.start(task['id'], host)
        self.assertIn(result['status'], ('已完成', '部分完成'), result.get('error'))
        return result

    def test_new_fact_review_is_frozen_with_report_and_old_report_is_unchanged(self):
        task = self.second()
        self.assertEqual(task['status'], '已完成', task['gaps'])
        self.assertEqual(len(task['judgment_reviews']), 1)
        self.assertEqual(task['judgment_review_pending'], [])
        frozen = self.agent.archive.report(task['id'], 2)
        self.assertEqual(frozen['content']['judgment_reviews'], task['judgment_reviews'])
        self.assertEqual(self.agent.archive.report(task['id'], 1), self.frozen)
        self.assertEqual(task['ledger']['used']['model_calls'], 0)
        self.assertEqual(task['ledger']['used']['data_requests'], 0)

    def test_review_correspondence_does_not_use_reused_ordinal_ids(self):
        task = self.second()
        old = self.frozen['content']['theses'][0]
        current = task['theses'][0]
        self.assertEqual(old['id'], current['id'])
        self.assertNotEqual(old['title'], current['title'])
        result = task['judgment_reviews'][0]
        self.assertEqual(result['prior_thesis_hash'], fingerprint(old))
        self.assertEqual(result['current_theses'][0]['hash'], fingerprint(current))
        self.assertEqual(current['proposal_hash'], fingerprint(self.raw_result['theses'][0]))

    def test_missing_review_remains_pending_instead_of_approving_update(self):
        task = self.second(reviews=False)
        self.assertEqual(task['status'], '部分完成')
        self.assertFalse(task['judgment_reviews'])
        self.assertTrue(task['judgment_review_pending'])
        self.assertTrue(task['delivery'])

    def test_original_conditions_are_not_modified_by_new_review(self):
        task = self.second()
        anchor = task['judgment_review_baseline']['anchors'][0]
        self.assertEqual(anchor['conditions'], self.frozen['content']['tracking'])
        self.assertEqual(task['judgment_reviews'][0]['conditions_hash'], fingerprint(anchor['conditions']))
        self.assertEqual(self.agent.archive.report(task['id'], 1), self.frozen)

    def test_wrong_condition_hash_never_becomes_valid_review(self):
        task = self.second(mutate=lambda item: item.update(conditions_hash='0' * 64))
        self.assertEqual(task['status'], '部分完成')
        self.assertFalse(task['judgment_reviews'])

    def test_changed_fact_cannot_be_marked_unchanged(self):
        task = self.second(mutate=lambda item: item['dimensions']['facts'].update(state='留存依据未变'))
        self.assertFalse(task['judgment_reviews'])
        self.assertEqual(task['status'], '部分完成')

    def test_unchanged_retained_fact_can_be_identified_without_claiming_new_evidence(self):
        task = self.second(changed=False)
        self.assertEqual(task['judgment_reviews'][0]['dimensions']['facts']['state'], '留存依据未变')
        self.assertEqual(task['status'], '部分完成')
        self.assertIn('未取得新时点证据', ' '.join(task['gaps']))

    def test_price_state_does_not_inherit_mechanism_support(self):
        task = self.second(mutate=lambda item: item['dimensions']['price'].update(state='已观察', refs=['ev_rule']))
        self.assertFalse(task['judgment_reviews'])

    def test_expectation_cannot_use_two_policy_clauses_as_forecast_and_result(self):
        def mutate(item):
            item['dimensions']['expectations'].update(state='支持', comparison_kind='第三方预测',
                refs=['ev_rule'], expected_refs=['ev_rule'], observed_refs=['ev_rule'])
        self.assertFalse(self.second(mutate=mutate)['judgment_reviews'])

    def test_future_original_window_is_separate_from_evidence_insufficiency(self):
        task = self.second(mutate=lambda item: item['dimensions']['mechanism'].update(
            state='未到验证期', answer='尚未进入原发布观察窗口。', refs=[]))
        self.assertEqual(task['judgment_reviews'][0]['dimensions']['mechanism']['state'], '未到验证期')

    def test_unknown_or_already_started_window_is_not_called_not_due(self):
        task = self.second()
        for window in ('未来一段时间', '2026-07-01至2026-09-01', '2026-19-01'):
            altered = copy.deepcopy(task)
            altered['judgment_review_baseline']['anchors'][0]['conditions'][0]['window'] = window
            rule = altered['judgment_review_baseline']['anchors'][0]
            rule['conditions_hash'] = fingerprint(rule['conditions'])
            proposal = altered['judgment_review_proposals'][0]['reviews'][0]
            proposal['conditions_hash'] = rule['conditions_hash']
            proposal['dimensions']['mechanism'].update(state='未到验证期', refs=[])
            self.assertFalse(review.evaluate(altered)[0], window)

    def test_changed_input_or_evidence_invalidates_but_resume_keeps_unchanged_review(self):
        task = self.second()
        resumed = copy.deepcopy(task); resumed['batch'] += 1
        self.assertEqual(review.evaluate(resumed)[0], task['judgment_reviews'])
        for field, value in (('batch', 0), ('input_hash', 'changed')):
            altered = copy.deepcopy(task); altered[field] = value
            with self.subTest(field=field): self.assertFalse(review.evaluate(altered)[0])
        altered = copy.deepcopy(task); altered['evidence'][0]['text'] += 'changed'
        self.assertFalse(review.evaluate(altered)[0])

    def test_conflicting_reviews_do_not_retain_the_first_favorable_record(self):
        task = self.second()
        other = copy.deepcopy(task['judgment_review_proposals'][0])
        other['reviews'][0]['assessment'] = '调整'
        task['judgment_review_proposals'].append(other)
        records, pending = review.evaluate(task)
        self.assertEqual(records, [])
        self.assertIn('冲突', pending[0]['reason'])

    def test_invalid_later_review_rejects_the_complete_bundle(self):
        task = self.second()
        task['judgment_review_proposals'][0]['reviews'].append(dict(task['judgment_review_proposals'][0]['reviews'][0],
            prior_thesis_id='foreign'))
        self.assertFalse(review.evaluate(task)[0])

    def test_duplicate_proposal_hash_cannot_select_an_arbitrary_current_thesis(self):
        task = self.second()
        task['theses'].append(dict(task['theses'][0], id='another'))
        self.assertFalse(review.evaluate(task)[0])

    def test_frozen_review_and_baseline_changes_are_rejected(self):
        task = self.second()
        altered = copy.deepcopy(task); altered['judgment_reviews'][0]['assessment'] = '调整'
        with self.assertRaises(ContractError): compose(altered)
        altered = copy.deepcopy(task); altered['judgment_review_baseline']['anchors'][0]['title'] = 'changed'
        with self.assertRaises(ContractError): review.validate_saved(altered, self.frozen)

    def test_required_four_dimensions_and_explicit_field_shapes(self):
        task = self.prepare()
        proposal = self.proposal(task)
        variants = [None, [proposal, proposal], [dict(proposal, current_thesis_indices=[True])],
                    [dict(proposal, dimensions={'facts': proposal['dimensions']['facts']})]]
        for value in variants:
            with self.subTest(value=type(value).__name__), self.assertRaises(ContractError): review.validate_shape(value)

    def test_new_contract_does_not_change_initial_analysis_shape(self):
        task = self.first()
        self.assertEqual(analysis_fields(task), {'answers', 'theses'})
        updated = self.agent.prepare_update(task['id'], '2026-08-20T18:00:00+08:00')
        self.assertEqual(analysis_fields(updated), {'answers', 'theses', 'judgment_reviews'})
        self.assertEqual(analysis_contract(updated)['judgment_review']['prior_digest'], task['delivery']['digest'])

    def test_strict_and_ordinary_analysis_use_same_review_container(self):
        task = self.second()
        proposal = self.raw_result['judgment_reviews'][0]
        wire = dict(version=1, answers=[], theses=[], judgment_reviews=[copy.deepcopy(proposal)])
        for dimension in wire['judgment_reviews'][0]['dimensions'].values():
            dimension['segments'] = [{'text': dimension.pop('answer')}]
        decoded = decode_analysis_output(wire, analysis_output_schema(task))
        self.assertEqual(decoded['judgment_reviews'][0]['dimensions']['facts']['numeric_segments'],
                         {'answer': [{'text': proposal['dimensions']['facts']['answer']}]})
        class Model:
            def reason_phase(self, current, messages, phase):
                return json.dumps(dict(answers={}, theses=[], judgment_reviews=[proposal]), ensure_ascii=False)
        result, _ = model_object(Model(), task, 'analyze',
            dict(question=task['question'], intent=task['intent'], analysis_contract=analysis_contract(task)), analysis_fields(task))
        self.assertEqual(result['judgment_reviews'], [proposal])

    def test_invalid_host_update_has_no_new_version_or_budget_side_effect(self):
        task = self.first()
        before = copy.deepcopy(self.agent.archive.get(task['id']))
        with self.assertRaises(ContractError):
            self.agent.update(task['id'], '2026-08-20T18:00:00+08:00', host_result={'judgment_reviews': None})
        self.assertEqual(self.agent.archive.get(task['id']), before)


class JudgmentReviewDimensionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = JudgmentReviewWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent = self.fixture.agent

    def spec(self, task, material, ident, date, nature='已实现事实', part='段落3'):
        parts = {item['locator']: item['text'] for item in material['snapshot']['parts']}
        return dict(id=ident, artifact_id=material['id'], object='600000.SH', record_locator={'part': part},
            text=parts[part], period=date, nature=nature, unit='不适用', published_at=date,
            publication=dict(at=date, basis='合成原件明确发布日期', precision='日', timezone='Asia/Shanghai',
                certainty='已确认', basis_ref=dict(kind='material', artifact_id=material['id'],
                    snapshot_hash=material['snapshot_hash'], part='段落2', quote=parts['段落2'])),
            question_ids=['q1'], review=dict(question_ids=['q1'], allowed_natures=[nature],
                conditions=['合成材料，只用于契约验收，不证明真实证券或投资结果'], reason='核对本次原件明确的企业资格与假设',
                evidence_need_texts={'q1': ['适用资格']}))

    def scenario(self, *, forecast_date='2026年7月19日', forecast_nature='第三方估计'):
        task = self.agent.create('600000.SH合成资格假设和价格如何分别验证？', entry='个股', objects=['600000.SH'],
            cutoff='2026-07-20T18:00:00+08:00', questions=[dict(id='q1', kind='investment', required=True,
                question='600000.SH合成资格假设和价格如何分别验证？', evidence_needs=['适用资格'])])
        original = '合成原件，非真实证券信息。\n\n发布日期：2026年7月1日。\n\n600000.SH的适用资格和经营结果尚需验证。'
        task = self.agent.add_material(task['id'], 'baseline.txt', original.encode())
        host = self.fixture.host(task)
        host['evidence_specs'] = [self.spec(task, task['materials'][-1], 'ev_rule', '2026-07-01')]
        host['theses'][0]['judgment'] = '证据不足'
        first = self.agent.start(task['id'], host)
        self.assertEqual(first['status'], '已完成', first.get('error'))
        self.prior_report = self.agent.archive.report(first['id'], 1)
        sources = {
            'forecast.txt': '合成预测，非市场共识。\n\n发布日期：' + forecast_date + '。\n\n预计600000.SH满足新增资格条件并开展业务。',
            'actual.txt': '合成披露，非真实公司结果。\n\n发布日期：2026年8月10日。\n\n600000.SH已满足新增资格条件并开展业务。',
            'prices.txt': '合成价格，非真实证券行情。\n\n发布日期：2026年8月11日。\n\n600000.SH，人民币未复权日收盘价，窗口起点2026-07-21，终点2026-08-10，公司行为未核验。\n\n2026-07-21收盘价10元。\n\n2026-08-10收盘价12元。',
        }
        for name, text in sources.items(): self.agent.add_material(first['id'], name, text.encode())
        task = self.agent.prepare_update(first['id'], '2026-08-20T18:00:00+08:00')
        by_name = {item['name']: item for item in task['materials']}
        forecast_day = '2026-08-11' if '8月11日' in forecast_date else '2026-07-19'
        forecast = self.spec(task, by_name['forecast.txt'], 'forecast', forecast_day, forecast_nature)
        actual = self.spec(task, by_name['actual.txt'], 'actual', '2026-08-10')
        price_record = by_name['prices.txt']
        price_parts = {item['locator']: item['text'] for item in price_record['snapshot']['parts']}
        prices = []
        for ident, day, part, token in [('start', '2026-07-21', '段落4', '10'), ('end', '2026-08-10', '段落5', '12')]:
            spec = self.spec(task, price_record, ident, '2026-08-11', part=part)
            context = dict(security_id='600000.SH', currency='CNY', price_basis='unadjusted', price_kind='close',
                observation_date=day, window_start='2026-07-21', window_end='2026-08-10', corporate_action_status='unknown')
            basis = dict(kind='material', artifact_id=price_record['id'], snapshot_hash=price_record['snapshot_hash'],
                         part='段落3', quote=price_parts['段落3'])
            anchors = {key: copy.deepcopy(basis) for key in context}
            anchors['observation_date'] = dict(basis, part=part, quote=price_parts[part])
            anchors['corporate_action_status'] = {'kind': 'unverified'}
            spec.update(period=day, unit='元', raw_unit='元', raw_token=token, value=int(token), raw_value=int(token),
                        conversion_factor=1, calculation_context=context, calculation_context_basis=anchors)
            prices.append(spec)
        calculation = dict(id='price_return', method='observed_price_return_v1', inputs={'start_price': 'start', 'end_price': 'end'},
            parameters=dict(METHOD_REGISTRY['observed_price_return_v1']['parameters'], start_date='2026-07-21', end_date='2026-08-10'),
            assumptions={}, question_ids=['q1'])
        host = self.fixture.host(task, title='经营假设与价格分开复核')
        host['evidence_specs'] = [forecast, actual, *prices]
        host['calculations'] = [calculation]
        host['answers']['q1'].update(refs=['forecast', 'actual', 'start', 'end'], calculation_refs=['price_return'])
        host['theses'][0].update(support=['forecast', 'actual', 'start', 'end'], calculation_refs=['price_return'])
        proposed = self.fixture.proposal(task, assessment='调整')
        proposed['dimensions']['facts']['refs'] = ['actual']
        proposed['dimensions']['mechanism']['refs'] = ['actual']
        proposed['dimensions']['expectations'] = dict(state='支持', comparison_kind='第三方预测',
            answer='合成披露与此前第三方预测方向一致，不代表市场整体共识。',
            refs=['forecast', 'actual'], calculation_refs=[], expected_refs=['forecast'], observed_refs=['actual'])
        proposed['dimensions']['price'] = dict(state='已观察', refs=['start', 'end'], calculation_refs=['price_return'],
            numeric_segments={'answer': [{'text': '已观察的未复权价格变化为'},
                dict(source='calculation', ref='price_return', path='value', format='signed', digits=2),
                {'text': '，不等于总收益或事件因果。'}]})
        host['judgment_reviews'] = [proposed]
        result = self.agent.start(task['id'], host)
        self.assertIn(result['status'], ('已完成', '部分完成'), result.get('error'))
        return result

    def test_all_four_dimensions_have_independent_validated_inputs_and_saved_report(self):
        task = self.scenario()
        self.assertEqual(task['status'], '已完成', task['gaps'])
        record = task['judgment_reviews'][0]
        self.assertEqual(record['dimensions']['expectations']['comparison_kind'], '第三方预测')
        self.assertEqual(record['dimensions']['price']['state'], '已观察')
        self.assertIn('20.00%', record['dimensions']['price']['answer'])
        self.assertAlmostEqual(task['calculations'][0]['output']['value'], 20)
        self.assertEqual(self.agent.archive.report(task['id'], 2)['content']['judgment_reviews'], task['judgment_reviews'])
        self.assertEqual(self.agent.archive.report(task['id'], 1), self.prior_report)

    def test_post_outcome_forecast_cannot_appear_as_ex_ante_expectation(self):
        task = self.scenario(forecast_date='2026年8月11日')
        self.assertEqual(task['status'], '部分完成')
        self.assertFalse(task['judgment_reviews'])
        self.assertIn('事后预期', ' '.join(item['reason'] for item in task['judgment_review_pending']))

    def test_policy_nature_cannot_be_promoted_to_third_party_forecast(self):
        task = self.scenario(forecast_nature='合同政策条款')
        self.assertFalse(task['judgment_reviews'])

    def test_price_before_previous_cutoff_is_not_new_performance(self):
        task = self.scenario()
        task['judgment_review_baseline']['prior_cutoff'] = '2026-08-15T18:00:00+08:00'
        self.assertFalse(review.evaluate(task)[0])

    def test_price_review_requires_its_actual_inputs_not_an_unrelated_reference(self):
        task = self.scenario()
        bundle = task['judgment_review_proposals'][0]
        bundle['reviews'][0]['dimensions']['price']['refs'] = ['actual']
        self.assertFalse(review.evaluate(task)[0])

    def test_numeric_review_cannot_replace_observed_result_with_free_arithmetic(self):
        task = self.scenario()
        task['judgment_review_proposals'][0]['reviews'][0]['dimensions']['price']['answer'] = '价格上涨99%。'
        self.assertFalse(review.evaluate(task)[0])


if __name__ == '__main__': unittest.main()
