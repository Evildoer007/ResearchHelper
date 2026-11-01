"""Retain original interface assertions with the current resource location.

Only the obsolete report.css fixture lookup is adapted. Do not recreate a
legacy app directory in the development or production tree.
"""
from pathlib import Path
from unittest.mock import patch

from tests import test_financial_method_interfaces_offline as original


class CurrentFinancialMethodInterfacesTests(original.FinancialMethodInterfacesTests):
    def setUp(self):
        copyfile = original.shutil.copyfile
        legacy = original.ROOT / 'app/web/report.css'
        current = original.ROOT / 'products/app/frontend/report.css'

        def copy_current_resource(source, target, *args, **kwargs):
            return copyfile(current if Path(source) == legacy else source, target, *args, **kwargs)

        with patch.object(original.shutil, 'copyfile', side_effect=copy_current_resource):
            super().setUp()
