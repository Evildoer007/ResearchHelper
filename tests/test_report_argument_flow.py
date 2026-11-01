"""Actual saved-report reading flow, without changing its frozen content."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest

from researchhelper.contracts import fingerprint
from researchhelper.layout import default_resource_root
from researchhelper.reports import render
from researchhelper import visual

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT/'result/acceptance/one-sentence-research-f6e_kjx1'


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
        cls.protected = {name:hashlib.sha256((STUDY/name).read_bytes()).hexdigest()
                         for name in ('receipt.json','report.html','report.json')}
        cls.content = json.loads((STUDY/'report.json').read_text())
        cls.before = fingerprint(cls.content)
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
        cls.tree = ReportTree(render(cls.content, resources))

    @classmethod
    def tearDownClass(cls):
        if fingerprint(cls.content) != cls.before:
            raise AssertionError('Renderer changed frozen research content')
        for name, value in cls.protected.items():
            if hashlib.sha256((STUDY/name).read_bytes()).hexdigest() != value:
                raise AssertionError('Saved report or original receipt changed')

    def test_all_related_question_figures_stay_near_their_complete_research(self):
        related = {qid for item in self.content['theses'] for qid in item['question_ids']}
        required = {ref for question in self.content['questions'] if question['id'] in related
                    for ref in question.get('calculation_refs', [])}
        located = {node['attrs']['data-calculation-ref'] for node in self.tree.nodes
            if 'data-calculation-ref' in node['attrs'] and any(
                'argument-reading' in parent['attrs'].get('class','').split() for parent in node['ancestors'])}
        self.assertTrue(required.issubset(located), required-located)

    def test_comparisons_use_one_full_width_reading_flow(self):
        self.assertEqual(len(self.tree.with_class('argument-comparisons')), 1)

    def test_short_conclusion_precedes_figures_and_complete_question_basis(self):
        bodies = [node for node in self.tree.nodes if 'data-main-question-ref' in node['attrs']]
        self.assertEqual(len(bodies), 1)
        self.assertTrue(any('argument-continuation' in parent['attrs'].get('class','').split()
                            for parent in bodies[0]['ancestors']))
        self.assertFalse(any('argument-introduction' in parent['attrs'].get('class','').split()
                             for parent in bodies[0]['ancestors']))

    def test_every_registered_figure_remains_present_exactly_once(self):
        references = [node['attrs']['data-calculation-ref'] for node in self.tree.nodes
                      if 'data-calculation-ref' in node['attrs']]
        self.assertEqual(len(references), len(set(references)))
        self.assertEqual(set(references), {item['id'] for item in self.content['calculations']})

    def test_complete_answer_is_not_collapsed_or_discarded(self):
        bodies = [node for node in self.tree.nodes if 'data-main-question-ref' in node['attrs']]
        self.assertEqual({node['attrs']['data-main-question-ref'] for node in bodies}, {'q1'})
        self.assertFalse(any(parent['tag'] in ('details','summary') for node in bodies for parent in node['ancestors']))


if __name__ == '__main__':
    unittest.main()
