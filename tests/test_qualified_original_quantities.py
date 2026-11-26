"""Keep limits and approximations attached to the numbers in retained sources."""
from hashlib import sha256
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT
from researchhelper.draft_numeric_review import _draft_numbers, _measure_matches
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence
from researchhelper.numeric_integrity import ORIGINAL_QUANTITY_QUALIFIER_POLICY, ORIGINAL_QUANTITY_QUALIFIER_VERSION
from researchhelper.planning import input_fingerprint
from tests import ROOT


QUESTION = '核对600030.SH原件中的客户数量、归母净利润及营业收入'
ORIGINAL = (
    '公司个人客户超 1,800 万户 ； 企业与机构客户超 15 万家。\n\n'
    '2026年上半年归母净利润同比增长约30%。\n\n'
    '2026年上半年营业收入为36亿元。'
)


class QualifiedOriginalQuantityTests(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory(prefix='quantity-qualifiers-', dir=ROOT/'result/acceptance')
        self.addCleanup(work.cleanup)
        self.guard = patch('socket.socket.connect', side_effect=AssertionError('Offline acceptance'))
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.agent = ResearchAgent(ResearchArchive(work.name), ROOT, offline=True,
                                   require_argument_support=True, draft_first=True)
        self.addCleanup(self.agent.close_runtime)

    def material_task(self, text, *, document_only=False):
        kwargs = {'intent': DOCUMENT_CONTENT_INTENT} if document_only else {}
        task = self.agent.create(QUESTION, objects=['600030.SH'], **kwargs)
        return self.agent.add_material(task['id'], '披露摘录.txt', text.encode())

    def test_report_keeps_supported_limits_and_approximations_with_the_original_links(self):
        task = self.material_task(ORIGINAL)
        report = dict(title='客户和财务指标', conclusion='披露客户规模及财务变化。', logics=[
            dict(title='客户范围', analysis='公司个人客户超过1800万户。企业与机构客户15万家以上。',
                 source_refs=[task['materials'][0]['id']]),
            dict(title='报告期变化', analysis='2026年上半年归母净利润同比增长30%左右。2026年上半年营业收入为36亿元。',
                 source_refs=[task['materials'][0]['id']]),
            dict(title='不当精度', analysis='公司个人客户为1,800万户。企业与机构客户为15万家。'
                 '2026年上半年归母净利润同比增长30%。企业与机构客户少于15万家。',
                 source_refs=[task['materials'][0]['id']]),
        ])
        delivered = self.agent.start(task['id'], dict(draft_report=report, data_requests=[]))
        self.assertTrue(delivered.get('delivery'), delivered.get('error'))
        logics = delivered['draft_report']['logics']
        self.assertEqual([logic['analysis'] for logic in logics[:2]], [logic['analysis'] for logic in report['logics'][:2]])
        self.assertEqual([link['original'] for logic in logics[:2] for link in logic['quantity_links']['analysis']['links']],
                         ['超过1800万户', '15万家以上', '30%左右', '36亿元'])
        self.assertTrue(all(link['kind']=='document_excerpt' for logic in logics[:2]
                            for link in logic['quantity_links']['analysis']['links']))
        for unsupported in ('1,800万户', '15万家', '增长30%'):
            self.assertNotIn(unsupported, logics[2]['analysis'])
        self.assertEqual(delivered['ledger']['used']['data_requests'], 0)
        self.assertEqual(delivered['ledger']['used']['model_calls'], 0)
        frozen = self.agent.archive.report(task['id'], 1)
        self.assertEqual(frozen['content'][ORIGINAL_QUANTITY_QUALIFIER_POLICY], ORIGINAL_QUANTITY_QUALIFIER_VERSION)
        self.assertEqual(frozen['digest'], delivered['delivery']['digest'])

    def test_qualifiers_use_the_source_part_even_if_the_selection_omits_them(self):
        original = '累计超过人民币 20 亿元。\n\n账户总数 15 万户左右。\n\n预约 40 万户。'
        task = self.material_task(original, document_only=True)
        artifact = task['materials'][0]

        def spec(number, unit, text, part):
            return dict(id='original_quantity', artifact_id=artifact['id'], object='600030.SH',
                record_locator={'part': part}, text=text, period='原件所载期间', nature='已实现事实',
                unit=unit, raw_unit=unit, raw_token=str(number), raw_value=number, value=number,
                conversion_factor=1, question_ids=['q1'], usage_scope='document_content', published_at=None,
                publication=dict(at=None, basis_ref=None, precision='未知', timezone='未知',
                                 certainty='未知', basis='首次公开时间未核验'))

        for value, unit, quote, locator in ((20, '亿元', '20 亿元', '段落1'), (15, '万户', '15 万户', '段落2')):
            numeric = spec(value, unit, quote, locator)
            with self.subTest(quote=quote), self.assertRaisesRegex(ContractError, '保留完整文字摘录'):
                candidate_evidence(task, artifact, numeric)
        accepted = candidate_evidence(task, artifact, spec(40, '万户', '预约 40 万户', '段落3'))
        self.assertEqual(accepted['value'], 40)
        quote = spec(15, '万户', '账户总数 15 万户左右。', '段落2')
        for key in ('raw_token', 'raw_value', 'raw_unit', 'value', 'conversion_factor'):
            del quote[key]
        quote['unit'] = '不适用'
        text = candidate_evidence(task, artifact, quote)
        self.assertEqual(text['text'], '账户总数 15 万户左右。')
        self.assertIsNone(text.get('value'))
        self.assertEqual(text['source_check'], '通过')

    def test_structured_exact_value_compares_to_bounds_instead_of_rounding_to_them(self):
        for statement, kind, observed, expected in (
            ('客户数超过15万户', 'greater', 15, False),
            ('客户数超15万户', 'greater', 16, True),
            ('客户数不低于15万户', 'minimum', 15, True),
            ('客户数低于15万户', 'less', 15, False),
            ('客户数不超过15万户', 'maximum', 15, True),
            ('客户数未超过15万户', 'maximum', 16, False),
            ('客户数＞＝15万户', 'minimum', 15, True),
            ('客户数15万户以下', 'maximum', 16, False),
            ('客户数约15.0万户', 'approximate', 15.01, True),
            ('客户数约15万户', 'approximate', 14.7, True),
        ):
            number, = _draft_numbers(statement, check_qualifiers=True)
            with self.subTest(statement=statement, observed=observed):
                self.assertEqual([item.kind for item in number['qualifiers']], [kind])
                self.assertEqual(_measure_matches(observed, '万户', number, statement[:number['start']]), expected)
        for phrase in ('预约40万户', '最近30%'):
            number, = _draft_numbers(phrase, check_qualifiers=True)
            self.assertEqual(number['qualifiers'], ())
        self.assertEqual(_draft_numbers('核对两份公告和三条逻辑', check_qualifiers=True), [])
        compound, = _draft_numbers('客户数超过约15万户', check_qualifiers=True)
        self.assertEqual([item.kind for item in compound['qualifiers']], ['greater', 'approximate'])

    def test_new_revision_adopts_the_rule_without_rewriting_the_saved_edition(self):
        task = self.material_task(ORIGINAL)
        task.pop(ORIGINAL_QUANTITY_QUALIFIER_POLICY)
        task['input_hash'] = input_fingerprint(task)
        task['nodes'] = self.agent.plan(task)
        self.agent.archive.save(task)
        statement = '公司个人客户为1,800万户。'
        proposal = dict(title='历史研究', conclusion=statement, logics=[
            dict(title='历史客户数量', analysis=statement, source_refs=[task['materials'][0]['id']])])
        old = self.agent.start(task['id'], dict(draft_report=proposal, data_requests=[]))
        saved = self.agent.archive.report(task['id'], 1)
        previous_html = sha256(saved['html'].encode()).hexdigest()
        self.assertNotIn(ORIGINAL_QUANTITY_QUALIFIER_POLICY, saved['content'])
        self.assertIn(statement, old['draft_report']['conclusion'])
        new = self.agent._updated_task(old, '2090-01-01T00:00:00+08:00')
        self.assertEqual(new[ORIGINAL_QUANTITY_QUALIFIER_POLICY], ORIGINAL_QUANTITY_QUALIFIER_VERSION)
        self.assertEqual(new['input_hash'], input_fingerprint(new))
        self.assertEqual(new['update_from']['digest'], saved['digest'])
        self.assertEqual(sha256(self.agent.archive.report(task['id'], 1)['html'].encode()).hexdigest(), previous_html)
