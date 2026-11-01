"""Original horizontal date spacing, not publication/timezone inference."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_publication_basis
from researchhelper.reasoning import evidence_contract,expand_material_publication_references

ROOT=Path(__file__).resolve().parents[1]


class MaterialPublicationSpacingTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='rh-publication-spacing-',dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.agent=ResearchAgent(ResearchArchive(folder.name),ROOT,offline=True)
        guard=patch('socket.socket.connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def material(self,date='2026 年 7 月 19 日',prefix='发布日期：',cutoff='2026-07-20T18:00:00+08:00'):
        task=self.agent.create('这份政策何时生效？',objects=['政策'],cutoff=cutoff,
            questions=[dict(id='q1',question='这份政策何时生效？',kind='fact')])
        text='政策原件\n\n'+prefix+date+'。\n\n政策自2026年7月21日起生效。'
        return self.agent.add_material(task['id'],'policy.txt',text.encode())

    def spec(self,task,quote,published='2026-07-19'):
        original=task['materials'][0]
        return dict(id='policy_date',artifact_id=original['id'],object='政策',
            record_locator={'part':'段落3'},text='政策自2026年7月21日起生效。',
            period='政策条款',nature='合同政策条款',unit='不适用',question_ids=['q1'],published_at=published,
            publication=dict(at=published,precision='日',timezone='Asia/Shanghai',certainty='已确认',
                basis='原件所载发布日期',basis_ref=dict(kind='material',artifact_id=original['id'],
                    snapshot_hash=original['snapshot_hash'],part='段落2',quote=quote)),
            review=dict(question_ids=['q1'],allowed_natures=['合同政策条款'],
                conditions=['仅核对明确条款，不作收益判断'],reason='发布日期和生效日分开核验'))

    def test_original_horizontal_spacing_accepts_same_date_not_a_rewritten_quote(self):
        for date in ('2026 年 7 月 19 日','2026年 07月 19日','2026\t年\t7\t月\t19\t日',
                     '2026\u00a0年\u00a07\u00a0月\u00a019\u00a0日','2026\u3000年\u30007\u3000月\u300019\u3000日'):
            with self.subTest(date=date):
                task=self.material(date)
                quote='发布日期：'+date+'。'
                before=fingerprint(task)
                validate_publication_basis(self.spec(task,quote),task['materials'])
                self.assertEqual(fingerprint(task),before)
                self.assertIn(quote,task['materials'][0]['snapshot']['parts'][1]['text'])

    def test_short_candidates_preserve_exact_original_spaces_and_reading_range(self):
        task=self.material()
        source=evidence_contract(task)['sources'][0]
        candidate=source['publication_candidates'][0]
        raw=candidate['basis_ref']['quote']
        self.assertIn('2026 年 7 月 19 日',raw)
        self.assertNotIn('publication_approved',source['material_publication_references'][0])
        self.assertFalse(evidence_contract(task)['compact_material_publication']['reference_is_approval'])
        spec=self.spec(task,raw)
        spec['publication'].pop('basis_ref')
        spec['material_publication_candidate_id']=source['material_publication_references'][0]['candidate_id']
        result=expand_material_publication_references(task,[spec])[0]
        self.assertEqual(result['publication']['basis_ref']['quote'],raw)
        validate_publication_basis(result,task['materials'])

    def test_full_host_workflow_can_deliver_literal_policy_with_spaced_release_date(self):
        task=self.material()
        spec=self.spec(task,'发布日期：2026 年 7 月 19 日。')
        final=self.agent.start(task['id'],dict(evidence_specs=[spec],answers={'q1':{
            'answer':'原件记载政策自2026年7月21日起生效。','refs':['policy_date'],'calculation_refs':[]}},theses=[]))
        self.assertEqual(final['status'],'已完成',final.get('error'))
        self.assertEqual(final['evidence'][0]['published_at'],'2026-07-19')
        self.assertEqual(final['ledger']['used']['model_calls'],0)
        self.assertEqual(final['ledger']['used']['data_requests'],0)
        self.assertTrue(self.agent.export(task['id'])['html'])

    def test_removing_original_spaces_from_quote_is_not_accepted(self):
        task=self.material()
        with self.assertRaises(ContractError):
            validate_publication_basis(self.spec(task,'发布日期：2026年7月19日。'),task['materials'])

    def test_changed_date_not_repaired(self):
        task=self.material()
        with self.assertRaises(ContractError):
            validate_publication_basis(self.spec(task,'发布日期：2026 年 7 月 19 日。','2026-07-18'),task['materials'])

    def test_planned_and_negative_qualifiers_cannot_be_clipped(self):
        for prefix in ('计划发布日期：','尚未发布，预计发布日期：','未发布日期：'):
            with self.subTest(prefix=prefix):
                task=self.material(prefix=prefix)
                with self.assertRaises(ContractError):
                    validate_publication_basis(self.spec(task,'发布日期：2026 年 7 月 19 日。'),task['materials'])

    def test_effective_reporting_and_signed_dates_do_not_become_release_dates(self):
        for prefix in ('生效日期：','报告期截至：','落款日期：','报告送出日期：'):
            with self.subTest(prefix=prefix):
                task=self.material(prefix=prefix)
                self.assertEqual(evidence_contract(task)['sources'][0]['publication_candidates'],[])
                with self.assertRaises(ContractError):
                    validate_publication_basis(self.spec(task,prefix+'2026 年 7 月 19 日。'),task['materials'])

    def test_full_quote_with_other_date_cannot_borrow_release_cue(self):
        task=self.material()
        original=task['materials'][0]
        spec=self.spec(task,'发布日期：2026 年 7 月 19 日。','2026-07-21')
        with self.assertRaises(ContractError):validate_publication_basis(spec,[original])

    def test_newlines_do_not_concatenate_separate_date_fragments(self):
        task=self.material('2026\n年 7 月 19 日')
        self.assertEqual(evidence_contract(task)['sources'][0]['publication_candidates'],[])

    def test_day_precision_does_not_claim_intraday_availability(self):
        task=self.material(cutoff='2026-07-19T12:00:00+08:00')
        final=self.agent.start(task['id'],dict(evidence_specs=[self.spec(task,'发布日期：2026 年 7 月 19 日。')],
            answers={},theses=[]))
        self.assertEqual(final['status'],'部分完成')
        self.assertFalse(final['evidence'])

    def test_invalid_calendar_date_cannot_pass(self):
        task=self.material('2026 年 2 月 30 日')
        with self.assertRaises(ContractError):
            validate_publication_basis(self.spec(task,'发布日期：2026 年 2 月 30 日。','2026-02-28'),task['materials'])


if __name__=='__main__':unittest.main()
