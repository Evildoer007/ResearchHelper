"""Exercise the saved-report reading flow with the maintained offline ETF case."""
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest

from lxml import html

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.layout import default_resource_root
from researchhelper.report_designer import design
from researchhelper.reports import render
from researchhelper import visual


class ReportTree(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.nodes, self.stack = [], []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = dict(tag=tag, attrs=dict(attrs), ancestors=tuple(self.stack))
        self.nodes.append(node)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append(node)

    def handle_endtag(self, tag):
        for position in range(len(self.stack)-1, -1, -1):
            if self.stack[position]['tag'] == tag:
                del self.stack[position:]
                break

    def with_class(self, name):
        return [node for node in self.nodes if name in node['attrs'].get('class','').split()]


class ReportArgumentFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        origin = getattr(visual, '__frozen_origin__', None)
        if origin:
            # The PYZ audit loader emulates a module file inside _internal;
            # the actual App keeps its verified resources in Contents/Resources.
            # Use that delivered directory, never the development CSS tree.
            executable = Path(origin.split('!/PYZ.pyz/', 1)[0])
            resources = executable.parents[2]
            if not (resources/'app/web/report.css').is_file():
                raise AssertionError('Actual frozen App report resources are missing')
        else:
            resources = default_resource_root()
        temporary = tempfile.TemporaryDirectory(prefix='researchhelper-report-argument-')
        cls.addClassCleanup(temporary.cleanup)
        cls.archive = ResearchArchive(temporary.name)
        agent = ResearchAgent(cls.archive, resources, offline=True)
        cls.addClassCleanup(agent.close_runtime)
        task = agent.start(agent.create_case('etf')['id'])
        cls.report_key = (task['id'], task['delivery']['version'])
        saved = cls.archive.report(*cls.report_key)
        cls.protected = fingerprint(saved)
        cls.content = saved['content']
        cls.before = fingerprint(cls.content)
        cls.tree = ReportTree(render(cls.content, resources))
        cls.detailed_tree = ReportTree(render(cls.content, resources, report_profile='detailed'))

    @classmethod
    def tearDownClass(cls):
        if fingerprint(cls.content) != cls.before:
            raise AssertionError('Renderer changed frozen research content')
        if fingerprint(cls.archive.report(*cls.report_key)) != cls.protected:
            raise AssertionError('Rendering changed the archived report')

    def test_all_related_question_figures_stay_near_their_complete_research(self):
        for thesis in self.content['theses']:
            required = {ref for question in self.content['questions'] if question['id'] in thesis['question_ids']
                        for ref in question.get('calculation_refs', [])}
            located = {node['attrs']['data-calculation-ref'] for node in self.tree.nodes
                if 'data-calculation-ref' in node['attrs'] and any(
                    parent['attrs'].get('data-thesis-ref') == thesis['id'] for parent in node['ancestors'])}
            self.assertTrue(required)
            self.assertTrue(required.issubset(located), required - located)

    def test_comparisons_use_one_full_width_reading_flow(self):
        for name, tree in (('standard', self.tree), ('detailed', self.detailed_tree)):
            with self.subTest(profile=name):
                comparison, = tree.with_class('argument-comparisons')
                evidence, = [node for node in tree.with_class('paper-wide') if comparison in node['ancestors']]
                figure, = [node for node in tree.nodes if node['tag'] == 'figure' and comparison in node['ancestors']]
                self.assertIn(evidence, figure['ancestors'])
                self.assertEqual(evidence['attrs']['data-columns'], '1')
                self.assertFalse(tree.with_class('logic-reading-grid'))

    def test_short_conclusion_precedes_complete_question_basis(self):
        for name, tree in (('standard', self.tree), ('detailed', self.detailed_tree)):
            with self.subTest(profile=name):
                introduction, = tree.with_class('argument-introduction')
                evidence, = tree.with_class('argument-visual')
                continuation, = tree.with_class('argument-continuation')
                self.assertLess(tree.nodes.index(introduction), tree.nodes.index(evidence))
                self.assertLess(tree.nodes.index(evidence), tree.nodes.index(continuation))
                bodies = [node for node in tree.nodes if 'data-main-question-ref' in node['attrs']]
                self.assertTrue(bodies)
                for body in bodies:
                    self.assertIn(continuation, body['ancestors'])

    def test_every_registered_figure_remains_present_exactly_once(self):
        references = [node['attrs']['data-calculation-ref'] for node in self.tree.nodes
                      if 'data-calculation-ref' in node['attrs']]
        self.assertEqual(len(references), len(set(references)))
        self.assertEqual(set(references), {item['id'] for item in self.content['calculations']})

    def test_complete_answer_is_not_collapsed_or_discarded(self):
        bodies = [node for node in self.tree.nodes if 'data-main-question-ref' in node['attrs']]
        self.assertEqual([node['attrs']['data-main-question-ref'] for node in bodies],
                         [question['id'] for question in self.content['questions']])
        self.assertFalse(any(parent['tag'] in ('details','summary') for node in bodies for parent in node['ancestors']))


class ReportValuationFigureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = default_resource_root()
        temporary = tempfile.TemporaryDirectory(prefix='researchhelper-report-valuation-')
        cls.addClassCleanup(temporary.cleanup)
        cls.archive = ResearchArchive(temporary.name)
        agent = ResearchAgent(cls.archive, cls.resources, offline=True)
        cls.addClassCleanup(agent.close_runtime)
        task = agent.start(agent.create_case('value')['id'])
        cls.report_key = (task['id'], task['delivery']['version'])
        cls.saved = cls.archive.report(*cls.report_key)
        cls.content = cls.saved['content']

    def test_both_reports_place_one_scoped_chart_with_exact_sources(self):
        before = fingerprint(self.saved)
        baseline, pressure = self.content['calculations']
        price = next(row for row in self.content['evidence'] if row['id'] == baseline['input_bindings']['price'])
        targets = ['#' + baseline['id'], '#' + pressure['id'], '#ev-' + price['id']]
        for profile in ('standard', 'detailed'):
            with self.subTest(profile=profile):
                report = design(self.content, self.resources, profile=profile)
                doc = report['document']
                chart, = (block['spec'] for block in doc['blocks'] if block['kind'] == 'chart')
                choice = chart['auto_visual']
                self.assertEqual(choice['reason'], 'registered_valuation_scenarios')
                self.assertEqual(choice['data']['labels'], ['基准现值', '压力现值', '参考价格'])
                self.assertEqual(choice['data']['values'], [baseline['output']['value'], pressure['output']['value'], price['value']])
                self.assertEqual(choice['data']['unit'], '元/股')
                self.assertEqual(chart['calculation_refs'], [baseline['id'], pressure['id']])
                self.assertEqual(doc['visual_sources'][chart['target']], chart)
                self.assertEqual(doc['content_hash'], self.saved['digest'])

                tree = html.document_fromstring(report['html'])
                plot = tree.get_element_by_id(chart['target'])
                self.assertTrue(plot.xpath('ancestor::article[@data-thesis-ref]'))
                detail = tree.get_element_by_id(plot.get('aria-details'))
                self.assertEqual(detail.xpath('.//tbody//a/@href'), targets)
                self.assertIn('收款减少20百万元', detail.text_content())
        self.assertEqual(fingerprint(self.archive.report(*self.report_key)), before)
        self.assertEqual(fingerprint(self.saved), before)

    def test_chart_requires_co_cited_results_with_matching_basis(self):
        calculations = {item['id']: item for item in self.content['calculations']}
        evidence = {item['id']: item for item in self.content['evidence']}
        figures = visual.research_figures(self.content)
        base, pressure = calculations
        self.assertFalse(visual.valuation_scenario_figures([base], calculations, evidence, figures))
        for field, value in (('research_version', 2), ('assumptions', {'rate': .2, 'years': 1}),
                             ('scenario_overrides', {'price': {'value': 8}})):
            with self.subTest(field=field):
                changed = deepcopy(calculations)
                changed[pressure][field] = value
                self.assertFalse(visual.valuation_scenario_figures([base, pressure], changed, evidence, figures))


if __name__ == '__main__':
    unittest.main()
