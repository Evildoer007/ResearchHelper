"""Retain catalogue assertions while binding only the delivered implementation.

The two original test modules' sole repository implementation import is replaced
by the already loaded installed catalogue. Assertions are executed unchanged.
The source-copy isolation test uses the delivered code object for frozen builds,
whose __file__ is intentionally virtual rather than a readable source file.
"""

import ast
from pathlib import Path
import types
from unittest.mock import patch

from researchhelper import ifind_financial_catalog as catalog


def _retained_tests(filename):
    path = Path(__file__).with_name(filename)
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == 'modules.evidencecollector.src']
    if len(imports) != 1 or [(alias.name, alias.asname) for alias in imports[0].names] != [
            ('ifind_financial_catalog', 'catalog')]:
        raise AssertionError('Retained catalogue test import changed; review adaptation')
    tree.body.remove(imports[0])
    module = types.ModuleType('retained_' + path.stem)
    module.__dict__.update(catalog=catalog, __file__=str(path))
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


_generic = _retained_tests('test_fixed_financial_catalog.py')
_calibrated = _retained_tests('test_fixed_financial_calibrated_catalog.py')


class DeliveredFixedFinancialCatalogueTests(_generic.FixedFinancialCatalogueTests):
    def test_standalone_import_without_runtime_artifacts(self):
        origin = getattr(catalog, '__frozen_origin__', None)
        if origin:
            from PyInstaller.archive.readers import CArchiveReader
            executable, module_name = origin.split('!/PYZ.pyz/')
            self.assertEqual(module_name, 'researchhelper.ifind_financial_catalog')
            archive = CArchiveReader(executable)
            pyz = archive.open_embedded_archive(next(name for name, row in archive.toc.items() if row[-1] == 'z'))
            code = pyz.extract(module_name)
        else:
            code = catalog.__spec__.loader.get_code(catalog.__name__)
        self.assertIsNotNone(code)
        isolated = types.ModuleType('isolated_catalog')
        # No repo/runtime artifact is available to the independent code object.
        with patch('builtins.open', side_effect=AssertionError('Unexpected file read')):
            exec(code, isolated.__dict__)
            self.assertEqual(isolated.catalogue_digest(), catalog.catalogue_digest())
            self.assertEqual(isolated.catalogue(), catalog.catalogue())


class DeliveredCalibratedCatalogueTests(_calibrated.CalibratedCatalogueTests):
    pass
