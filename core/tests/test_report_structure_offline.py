"""Content-driven report structure with a reproducible current-original sample."""
import copy
from pathlib import Path
import unittest
from lxml import html as dom

from researchhelper.contracts import ContractError
from researchhelper.reports import render, compose
from tests import test_document_content_offline as originals
from tests import test_official_original_availability as official

ROOT = Path(__file__).resolve().parents[2]
ANSWER = '合成样本指标原件记录为12.5%。'


class _CurrentFactFixture(official.OfficialOriginalAvailabilityTests):
    __test__ = False

    def create_task(self):
        return super().create_task(questions=[dict(id='q1', question='核查合成样本指标', kind='fact')])

    def saved_report(self):
        spec = self.spec()
        spec['review'] = dict(question_ids=['q1'], allowed_natures=['已实现事实'],
            conditions=['合成资料仅供核对该指标。'], reason='合成测试原件', temporal_use='current_version')
        self.agent.archive.save(self.task)
        done = self.agent.start(self.task['id'], dict(evidence_specs=[spec],
            answers={'q1': dict(answer=ANSWER, refs=[spec['id']])}))
        self.assertEqual(done['status'], '已完成')
        self.assertEqual(done['questions'][0]['status'], '已回答')
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        return self.agent.export(done['id'])


class ReportStructureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = originals.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def current_fact(self):
        fixture = _CurrentFactFixture()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture, fixture.saved_report()

    def test_current_fact_answers_are_not_repeated_in_summary(self):
        _, saved = self.current_fact()
        for profile in ('standard', 'detailed'):
            main = dom.document_fromstring(render(saved['content'], ROOT, report_profile=profile)).find('.//main')
            self.assertEqual(main.text_content().count(ANSWER), 1)

    def test_facts_without_calculation_do_not_get_empty_methods_or_summary(self):
        _, saved = self.current_fact()
        tree = dom.document_fromstring(render(saved['content'], ROOT))
        self.assertEqual(tree.xpath('//main//*[@id="summary" or @id="methods"]'), [])
        answer = tree.get_element_by_id('answer-q1')
        self.assertTrue(answer.xpath('ancestor::main'))
        for target in ('facts', 'scope'):
            self.assertTrue(tree.get_element_by_id(target).xpath('ancestor::*[@id="report-records"]'))

    def test_all_rendered_chapters_are_continuously_numbered(self):
        for case in ('mechanism', 'etf', 'value'):
            with self.subTest(case=case):
                task = self.fixture.agent.create_case(case)
                content = compose(self.fixture.agent.start(task['id']))
                tree = dom.document_fromstring(render(content, ROOT))
                main = tree.find('.//main')
                links = tree.xpath('//*[@aria-label="报告目录"]//a')
                self.assertTrue(links)
                self.assertEqual([link.xpath('./span/text()')[0] for link in links],
                                 [str(index) + '.' for index in range(1, len(links) + 1)])
                for link in links:
                    self.assertEqual(len(main.xpath('.//*[@id=$ident]', ident=link.get('href')[1:])), 1)
                self.assertFalse(main.xpath('.//aside|.//nav'))

    def test_mechanism_judgment_counter_and_tracking_survive_without_empty_methods(self):
        task = self.fixture.agent.create_case('mechanism')
        done = self.fixture.agent.start(task['id'])
        content = compose(done)
        tree = dom.document_fromstring(render(content, ROOT))
        main = tree.find('.//main')
        self.assertFalse(tree.xpath('//*[@id="methods"]'))
        for thesis in content['theses']:
            for key in ('judgment', 'investment_meaning', 'limitation'):
                self.assertIn(thesis[key], main.text_content())
            for ref in thesis.get('counter', []):
                self.assertTrue(main.xpath('.//a[@href=$target and @aria-haspopup="dialog"]', target='#ev-' + ref))
                record = tree.get_element_by_id('ev-' + ref)
                expected = next(item['text'] for item in content['evidence'] if item['id'] == ref)
                self.assertIn(expected, record.text_content())
        for track in content['tracking']:
            self.assertIn(track['condition'], main.text_content())

    def test_real_calculations_keep_math_inputs_and_chart(self):
        task = self.fixture.agent.create_case('value')
        done = self.fixture.agent.start(task['id'])
        content = compose(done)
        before = copy.deepcopy(content)
        tree = dom.document_fromstring(render(content, ROOT))
        self.assertTrue(tree.xpath('//*[local-name()="math"]'))
        for calculation in content['calculations']:
            record = tree.xpath('//*[@id=$ident]', ident=calculation['id'])
            self.assertEqual(len(record), 1)
            self.assertIn(f"{calculation['output']['value']:.4f}", record[0].text_content())
            self.assertTrue(record[0].xpath('.//table'))
        main = tree.find('.//main')
        plot, = main.xpath('.//figure[@data-calculation-ref]//*[@role="img" and @aria-details]')
        table = tree.get_element_by_id(plot.get('aria-details')).find('.//table')
        price = content['calculations'][0]['input_bindings']['price']
        self.assertEqual(table.xpath('./tbody//a/@href'),
                         ['#' + item['id'] for item in content['calculations']] + ['#ev-' + price])
        self.assertEqual(content, before)

    def test_current_only_scope_publication_and_conditions_are_preserved(self):
        _, saved = self.current_fact()
        content = saved['content']
        tree = dom.document_fromstring(render(content, ROOT))
        self.assertIn(content['scope'], tree.get_element_by_id('scope').text_content())
        for evidence in content["evidence"]:
            visible = tree.get_element_by_id('ev-' + evidence['id']).text_content()
            self.assertIn('公开时间未核验', visible)
            self.assertIn(evidence['availability']['obtained_at'], visible)
            for condition in evidence["applicability_review"]["conditions"]:
                self.assertIn(condition, visible)

    def test_frozen_content_and_original_export_never_change(self):
        fixture, saved = self.current_fact()
        before = copy.deepcopy(saved)
        render(saved['content'], ROOT, report_profile='detailed')
        self.assertEqual(saved, before)
        self.assertEqual(fixture.agent.export(fixture.task['id']), before)

    def test_tampered_content_is_still_rejected(self):
        _, saved = self.current_fact()
        content = saved['content']
        content["questions"][0]["answer"] += "未核验新结论"
        with self.assertRaises(ContractError):
            render(content, ROOT)


if __name__ == "__main__":
    unittest.main()
