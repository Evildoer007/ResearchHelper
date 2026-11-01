"""Current parser regression using the retained, independently acquired ruled PDF.

The earlier default-App fixture was removed before this run. Its fourteen
checks are not represented as rerun or passed. Reuse the available eight-test
physical-rule contract without modifying it, updating only the expected current
parser version. No closed research task is changed or resumed.
"""
import copy
import importlib

from researchhelper.contracts import ContractError
from researchhelper.evidence import extract_material
from researchhelper.material_parser import verified_material
from researchhelper.pdf_material import EXTRACT_VERSION

prior = importlib.import_module('tests.test_pdf_physical_rules')


class CurrentPhysicalRuleRegressionTests(prior.PdfPhysicalRuleTests):
    def test_new_parser_preserves_every_plain_page_and_original_byte_hash(self):
        self.assertEqual(self.material['extract_version'], EXTRACT_VERSION)
        self.assertEqual(self.material['hash'], self.old['hash'])
        self.assertEqual([part for part in self.material['parts'] if 'pdf_table' not in part],
                         [part for part in self.old['parts'] if 'pdf_table' not in part])

    def test_mutated_geometry_cannot_reproduce_original_bytes(self):
        changed = copy.deepcopy(self.material)
        table = next(part for part in changed['parts'] if 'pdf_table' in part)
        table['pdf_table']['column_bounds'][0] += 10
        with self.assertRaises(ContractError):
            verified_material(changed, self.payload)

    def test_unregistered_parser_version_is_rejected(self):
        with self.assertRaises(ContractError):
            extract_material(self.old['name'], self.payload, extract_version='pdf-unknown')
