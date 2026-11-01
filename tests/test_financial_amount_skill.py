"""The host Skill exposes the App's selector without a second model or writes."""
import copy
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
try:
    import test_financial_amount_selection as fixtures
except ModuleNotFoundError:
    from tests import test_financial_amount_selection as fixtures
from researchhelper.contracts import ContractError, canonical
from researchhelper.output_schema import original_batch_output_schema

ROOT = Path(__file__).resolve().parents[1]


class FinancialAmountSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.FinancialAmountSelectionTests.setUpClass()
        cls.raw, cls.original = fixtures.FinancialAmountSelectionTests.raw, fixtures.FinancialAmountSelectionTests.original

    setUp = fixtures.FinancialAmountSelectionTests.setUp
    tearDown = fixtures.FinancialAmountSelectionTests.tearDown
    wire = fixtures.FinancialAmountSelectionTests.wire
    selection = fixtures.FinancialAmountSelectionTests.selection

    def session(self):
        module = importlib.import_module('core.session')
        expected = os.environ.get('RESEARCHHELPER_SKILL_INTERFACE_PATH')
        if expected:
            self.assertEqual(Path(module.__file__).resolve(), Path(expected).resolve())
        folder = tempfile.TemporaryDirectory(prefix='financial-amount-host-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        location = Path(folder.name)
        if module.INSTALLATION == ROOT:
            # Source-only test: use a sibling installation anchor to keep all
            # files in RH while preserving the real read-only project guard.
            anchor = location/'source-resource-anchor'; anchor.mkdir()
            guard = patch.object(module, 'INSTALLATION', anchor)
            guard.start(); self.addCleanup(guard.stop)
        session = module.Session(location/'project')
        self.addCleanup(session.agent.close_runtime)
        task = copy.deepcopy(self.task)
        task['revision'] = 0  # Fresh private archive, not the live storage revision.
        task['source_readings'] = [copy.deepcopy(self.bundle)]
        session.archive.save(task)
        return session

    def test_host_and_app_share_exact_schema_decoding_and_no_local_model_call(self):
        session = self.session()
        before = canonical(session.status(self.task['id']))
        ledger = copy.deepcopy(self.task['ledger'])
        tools = session.tools(self.task['id'], reading_hash=self.bundle['reading_hash'])
        selector = tools['financial_originals']
        self.assertEqual(selector['schema'], original_batch_output_schema(self.task, self.views))
        self.assertEqual(selector['decode'], 'select_financial_originals')
        self.assertFalse(selector['financial_approval'] or selector['local_model_calls'])
        wire = dict(version=1, binding_ref=selector['prompt']['batch_binding_ref'],
            sources=[dict(source_ref=selector['prompt']['source_choices'][0]['source_ref'], selection=self.wire())])
        result = session.select_financial_originals(self.task['id'], self.bundle['reading_hash'], wire)
        self.assertEqual([item['value'] for item in result['evidence_specs']], [14282273, -37859085, -33026925, 71690439])
        self.assertEqual(canonical(session.status(self.task['id'])), before)
        self.assertEqual(session.status(self.task['id'])['ledger'], ledger)
        self.assertIsNone(session.agent.model)

    def test_stale_reading_or_legacy_task_does_not_get_new_selection_authority(self):
        session = self.session()
        with self.assertRaises(ContractError):
            session.select_financial_originals(self.task['id'], 'foreign_reading', {})
        old = session.status(self.task['id']); old.pop('financial_amount_selection_version')
        session.archive.save(old)
        tools = session.tools(self.task['id'], reading_hash=self.bundle['reading_hash'])
        self.assertNotIn('financial_originals', tools)
        with self.assertRaises(ContractError):
            session.select_financial_originals(self.task['id'], self.bundle['reading_hash'], {})


if __name__ == '__main__':
    unittest.main()
