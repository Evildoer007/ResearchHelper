"""Keep the original research assertions with current TLS and Skill fixtures.

The test-only CA trusts the generated localhost certificate, not a vendor.
Production trust, hostname verification and network authority are unchanged.
Original tests are retained; only stale fixture locations and version checks
are adapted here.
"""
import importlib.util
import importlib
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from unittest.mock import patch

from researchhelper.contracts import context
from researchhelper.reasoning import (CALCULATION_PLAN_VERSION, PHASE_FIELDS, RULES_VERSION,
                                     calculation_plan_contract, model_object,
                                     phase_output_example)
from tests import test_question_scope_workflow_offline as workflow
from tests import test_calculation_shared_contract_offline as shared
from tests.skill_fixture_support import build_skill_fixture


def model_fixture_worker(connection, deadline):
    with patch('researchhelper.https_transport.bundled_ca_file', return_value=os.environ['SSL_CERT_FILE']):
        workflow.transport.local_model_worker(connection, deadline)


def source_fixture_worker(connection, deadline):
    with patch('researchhelper.https_transport.bundled_ca_file', return_value=os.environ['SSL_CERT_FILE']):
        workflow.transport.local_source_worker(connection, deadline)


def prepare_skill_fixture(installation):
    Path(installation).parent.mkdir(parents=True, exist_ok=True)
    return build_skill_fixture(installation)


class CurrentPriceWorkflow(workflow.QuestionScopeWorkflowTests):
    def setUp(self):
        super().setUp()
        self.server.agent.model.worker_target = model_fixture_worker
        self.server.agent.collector.worker_target = source_fixture_worker

    def test_independent_skill_matches_app_without_a_second_model(self):
        done, _ = self.workflow()
        workflow.BASE.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix='current-parity-', dir=workflow.BASE)
        self.addCleanup(folder.cleanup)
        output = Path(folder.name)
        installation = output / 'installation/researchhelper'
        prepare_skill_fixture(installation)
        installed_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in installation.rglob('*') if path.is_file()}
        for name in ('planning', 'engine', 'reasoning', 'numeric_integrity'):
            actual_source = Path(importlib.import_module('researchhelper.' + name).__file__)
            self.assertEqual(hashlib.sha256((installation / 'researchhelper' / (name + '.py')).read_bytes()).hexdigest(),
                             hashlib.sha256(actual_source.read_bytes()).hexdigest())
        (output / 'fixed-input.json').write_text(json.dumps({
            'question': done['question'], 'plan': done['reasoning_state']['plan']['proposed'],
            'expected_answer': done['questions'][0]['answer']}, ensure_ascii=False))
        child = subprocess.run([sys.executable, '-I', '-B',
            str(workflow.ROOT / 'tests/current_price_skill_fixture.py'),
            str(workflow.ROOT), str(installation), str(output)],
            cwd=output, capture_output=True, text=True, timeout=40)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertTrue(all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == value
                            for path, value in installed_hashes.items()))
        skill = json.loads((output / 'skill-result.json').read_text())
        self.assertEqual(skill['answer'], done['questions'][0]['answer'])
        self.assertEqual(skill['calculation'], done['calculations'][0]['output'])
        self.assertTrue(skill['model_is_none'] and skill['restarted_report_unchanged'])
        self.assertEqual(skill['data_fixture_calls'], 3)
        self.assertEqual(skill['model_fixture_calls'], 0)
        self.assertFalse(self.fixture_errors, self.fixture_errors)
        (output / 'receipt.json').write_text(json.dumps({
            'status': 'passed', 'same_fixed_input': True, 'numeric_outputs_equal': True,
            'answers_equal': True, 'skill_has_no_second_model': True,
            'real_model_calls': 0, 'real_supplier_calls': 0, 'candidate_only': True,
            'skill_result': str(output / 'skill-result.json')}, ensure_ascii=False, indent=2))


class CurrentSharedCalculation(shared.CalculationSharedContractTests):
    def skill_session(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        installation = Path(folder.name) / 'installation/researchhelper'
        installation.parent.mkdir()
        build_skill_fixture(installation)
        spec = importlib.util.spec_from_file_location('current_calculation_skill', installation / 'core/session.py')
        module = importlib.util.module_from_spec(spec)
        old_path, old_bytecode = list(sys.path), sys.dont_write_bytecode
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path[:] = old_path
            sys.dont_write_bytecode = old_bytecode
        session = module.Session(Path(folder.name) / 'project')
        self.addCleanup(session.agent.close_runtime)
        return session

    def test_optional_empty_objects_and_required_fields_match_validator(self):
        contract = calculation_plan_contract(self.task)
        self.assertEqual(contract['binding'], context(self.task))
        self.assertEqual(contract['empty_assumptions'], {})
        self.assertEqual(set(contract['required_fields']), {'id', 'method', 'inputs', 'parameters', 'question_ids'})
        for field in ('inputs', 'parameters', 'assumptions', 'assumption_sources'):
            self.assertEqual(contract['field_types'][field], 'object')
        self.assertEqual(contract['field_types']['question_ids'], 'array<string>')

        class Host:
            def reason(self, task, messages):
                return json.dumps(self.output)

        host = Host()
        for phase in ('plan', 'supplement', 'extract', 'analyze'):
            host.output = phase_output_example(phase)
            _, receipt = model_object(host, self.task, phase, {}, PHASE_FIELDS[phase])
            self.assertEqual(receipt['rules_version'], RULES_VERSION)
            if phase in ('plan', 'supplement'):
                self.assertEqual(receipt['calculation_contract_version'], CALCULATION_PLAN_VERSION)
            else:
                self.assertNotIn('calculation_contract_version', receipt)
