"""Test-only spawn bootstrap for code imported from an actual App archive.

The ordinary frozen App already bootstraps its worker processes. A CPython
unit-test parent loading that archive manually does not. Carry the same archive
loader into its isolated parser child, without using source or mocking parsing.
"""
import importlib.abc
import importlib.util
import os
from pathlib import Path
import sys


def frozen_pdf_worker(connection, deadline):
    from PyInstaller.archive.readers import CArchiveReader
    executable = Path(os.environ['RESEARCHHELPER_FROZEN_TEST_EXECUTABLE']).resolve(strict=True)
    assert executable == Path('/Applications/ResearchHelper.app/Contents/Resources/backend/researchhelper-server/researchhelper-server')
    archive = CArchiveReader(str(executable))
    pyz = archive.open_embedded_archive(next(name for name, entry in archive.toc.items() if entry[-1] == 'z'))

    class FrozenResearch(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, name, path=None, target=None):
            if name == 'researchhelper' or name.startswith('researchhelper.'):
                assert name in pyz.toc
                return importlib.util.spec_from_loader(name, self, is_package=bool(pyz.toc[name][0]))

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            relative = module.__name__.replace('.', '/')
            relative += '/__init__.pyc' if pyz.toc[module.__name__][0] else '.pyc'
            module.__file__ = str(executable.parent/'_internal'/relative)
            exec(pyz.extract(module.__name__), module.__dict__)

    sys.meta_path.insert(0, FrozenResearch())
    from researchhelper.material_parser import _parser_worker
    _parser_worker(connection, deadline)
