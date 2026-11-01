"""Independent offline visual profile and saved conversion contracts."""
import hashlib
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import ROOT
from tests.test_client_report_audience import fixture
from researchhelper.contracts import canonical, ContractError
from researchhelper.report_designer import design, style_document, compatible_schemes, styled_spec
from researchhelper.artifact_exporter import convert_saved_html, export_report

class VisualProfileCompletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden'))
        cls.guard.start()
        cls.task,cls.content=fixture()
        cls.publication=design(cls.content,ROOT)
    @classmethod
    def tearDownClass(cls): cls.guard.stop()
    def test_every_compatible_choice_preserves_canonical_data_and_refs(self):
        document=self.publication['document'];before=canonical(document)
        for target,spec in document['visual_sources'].items():
            for scheme in compatible_schemes(spec):
                with self.subTest(target=target,scheme=scheme):
                    result=style_document(document,{'theme':'research','schemes':{target:scheme}})
                    changed=next(b['spec'] for b in result['blocks'] if b['kind']=='chart' and b['spec']['target']==target)
                    self.assertEqual(changed['auto_visual']['scheme'],scheme)
                    for key in ('data_refs','refs','calculation_refs'):
                        self.assertEqual(changed.get(key),spec.get(key))
                    self.assertEqual(result['content_hash'],document['content_hash'])
                    self.assertEqual(result['dataset'],document['dataset'])
                    self.assertEqual(style_document(result,{})['blocks'],document['blocks'])
        self.assertEqual(canonical(document),before)
    def test_invalid_target_and_scheme_rejected(self):
        document=self.publication['document'];target=next(iter(document['visual_sources']))
        for schemes in ({'foreign':'line'},{target:'not-a-chart'}):
            with self.assertRaises(ContractError):style_document(document,{'schemes':schemes})
    def test_saved_styled_html_converts_pdf_docx_without_data_mutation(self):
        document=self.publication['document'];before=canonical(self.content)
        schemes={target:compatible_schemes(spec)[-1] for target,spec in document['visual_sources'].items()}
        profile={'theme':'research','schemes':schemes}
        with tempfile.TemporaryDirectory() as folder:
            receipt=export_report(self.content,ROOT,folder,visual_profile=profile)
            self.assertEqual(receipt['visual_profile'],profile)
            for format,prefix in [('pdf',b'%PDF-'),('docx',b'PK')]:
                raw,converted=convert_saved_html(receipt['source_html'],format,expected_sha256=receipt['source_html_sha256'])
                self.assertTrue(raw.startswith(prefix));self.assertEqual(converted['visual_profile'],profile)
                self.assertEqual(converted['dataset'],document['dataset'])
                self.assertEqual(converted['content_hash'],document['content_hash'])
        self.assertEqual(canonical(self.content),before)
    def test_grouped_bar_conversion_keeps_every_value(self):
        pairs={'auto_visual':{'scheme':'dumbbell','data':{'labels':['a','b'],'periods':['before','after'],'pairs':[[0,2],[-3,None]]}}}
        matrix={'auto_visual':{'scheme':'heatmap','data':{'labels':['2025-01','2025-02'],'rows':['a','b'],'matrix':[[0,None],[-3,2]]}}}
        self.assertEqual([s['values'] for s in styled_spec(pairs,'grouped_bar')['auto_visual']['data']['series']],[[0,-3],[2,None]])
        self.assertEqual([s['values'] for s in styled_spec(matrix,'grouped_bar')['auto_visual']['data']['series']],[[0,None],[-3,2]])
