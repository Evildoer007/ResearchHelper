"""Curated definitions and processing-state boundaries; no paid research."""
import base64
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import (ResearchKnowledge, ENTRIES, CATALOGUE_POLICY,
    CATALOGUE_REVISION, catalogue_revision, task_catalogue_revision)
from researchhelper.planning import input_fingerprint
from researchhelper.data_requirements import processing_state
from researchhelper.reasoning import analysis_contract
from researchhelper.argument_support import review_request

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT/'result/acceptance/one-sentence-research-6nm7u50s'
IDENT = 'rh_k_pmi_diffusion'
STATE_POLICY = 'evidence_processing_state_version'
LATEST_NEEDS = ('最新一期制造业PMI总体指数（%）及所属月份', '最新一期制造业生产指数（%）及所属月份',
                '最新一期制造业新订单指数（%）及所属月份')
PREVIOUS_NEEDS = ('上一期制造业PMI总体指数（%）', '上一期制造业生产指数（%）', '上一期制造业新订单指数（%）')


def prepare_fixture():
    from tests.test_extraction_feedback import prepare_fixture as source_fixture
    from researchhelper.evidence import candidate_evidence, current_original_reference
    from researchhelper.analyzer import ResearchAnalyzer
    from researchhelper.contracts import validate_evidence
    from researchhelper.planning import question_specs
    from researchhelper.period_targets import FAMILY, FIELD
    names = ('receipt.json', 'task.json', 'report.json', 'report.html')
    protected = {name: hashlib.sha256((STUDY/name).read_bytes()).hexdigest() for name in names}
    task = json.loads((STUDY/'task.json').read_text())
    fresh = source_fixture()['task']
    # New controls bind reviewed business labels explicitly. Dictionary order
    # is not a current/previous classifier and must never decide these roles.
    with tempfile.TemporaryDirectory(prefix='curated-source-plan-') as folder:
        agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
        try:
            inserted = copy.deepcopy(fresh); inserted['revision'] = 0
            agent.archive.save(inserted)
            questions = question_specs(fresh)
            numeric = {text for text, spec in questions[0]['evidence_need_constraints'].items() if spec['value_kind'] == 'number'}
            assert numeric == set(LATEST_NEEDS + PREVIOUS_NEEDS)
            questions[0][FIELD] = {text: dict(family=FAMILY, relation=relation, period=None)
                for relation, needs in (('latest', LATEST_NEEDS), ('previous', PREVIOUS_NEEDS)) for text in needs}
            fresh = agent.revise_plan(fresh['id'], questions, '离线逐字登记已审阅的本期与上期目标', fresh['input_hash'])
            agent._adopt_method_knowledge(fresh)
        finally: agent.close_runtime()
    derived = {'applicability_check', 'applicability_receipt', 'applicability_review', 'artifact', 'content_version',
        'locator', 'obtained_at', 'research_version', 'snapshot', 'snapshot_hash', 'source', 'source_check',
        'source_receipt', 'task_id'}
    for prior in task['evidence']:
        origin = prior['artifact']['snapshot']['official_origin']['url']
        source = next(record for record in fresh['source_records']
                      if record.get('snapshot', {}).get('official_origin', {}).get('url') == origin)
        spec = {key: copy.deepcopy(value) for key, value in prior.items() if key not in derived}
        spec.update(artifact_id=source['id'], availability=current_original_reference(fresh, source))
        review = {key: copy.deepcopy(value) for key, value in prior['applicability_review'].items() if key != 'evidence_need_ids'}
        item = candidate_evidence(fresh, source, spec)
        ResearchAnalyzer().assess(fresh, item, review, fresh['source_records'])
        validate_evidence(item, fresh); fresh['evidence'].append(item)
    # Reuse only a private proposal for input wiring. It is not adopted or
    # independently approved, and no old task/store/checkpoint is resumed.
    fresh['host_result'] = copy.deepcopy(task['host_result'])
    fresh['status'] = '部分完成'
    ResearchAgent._gap(fresh, '离线知识与处理状态控制，不是模型质量验收', category='question_coverage')
    assert protected == {name: hashlib.sha256((STUDY/name).read_bytes()).hexdigest() for name in names}
    return dict(task=task, fresh=fresh, protected=protected, real_model_calls=0, real_data_calls=0,
        closed_study_reopened=False, authored_controls=True, reverified_business_proposals=True, real_quality_accepted=False)


class CuratedMacroKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_MACRO_KNOWLEDGE_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes())) if supplied else prepare_fixture()

    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('No paid or public requests'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task = copy.deepcopy(self.fixture['task'])
        self.knowledge = ResearchKnowledge()
        self.entry = next(item for item in ENTRIES if item['id'] == IDENT)
        self.resources = Path(os.environ.get('RESEARCHHELPER_MACRO_KNOWLEDGE_RESOURCES', str(ROOT)))

    def package(self, revision=2):
        return self.knowledge.read('宏观', '比较制造业PMI的生产和新订单',
            context={CATALOGUE_POLICY: revision, 'intent': self.task['intent'], 'questions': self.task['questions']})

    def state_task(self):
        self.task = copy.deepcopy(self.fixture['fresh'])
        self.assertEqual(self.task[STATE_POLICY], 1)
        return self.task

    def agent(self, folder):
        agent = ResearchAgent(ResearchArchive(folder), self.resources, offline=True, require_argument_support=True)
        self.addCleanup(agent.close_runtime)
        return agent

    def test_definition_is_backed_by_retained_official_bytes_and_method_sections(self):
        from researchhelper.official_sources import validate_original
        from researchhelper.material_parser import verified_material
        reference = self.entry['source_reference']
        record = next(record for record in self.task['source_records']
                      if record.get('snapshot', {}).get('official_origin', {}).get('url') == reference['url'])
        validate_original(self.task, record)
        raw = base64.b64decode(record['snapshot']['original_base64'], validate=True)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), reference['snapshot_sha256'])
        parts = verified_material(record['snapshot'], raw)['parts']
        self.assertTrue(any('正向回答的企业个数百分比加上回答不变的百分比的一半' in part['text'] for part in parts))
        self.assertTrue(any('供应商配送时间指数为逆指数' in part['text'] for part in parts))
        self.assertFalse(self.entry.get('implementation'))

    def test_period_controls_bind_actual_need_meanings_not_dictionary_order(self):
        task = self.state_task(); goals = task['questions'][0]['evidence_period_targets']
        self.assertTrue(all(goals[text]['relation'] == 'latest' for text in LATEST_NEEDS))
        self.assertTrue(all(goals[text]['relation'] == 'previous' for text in PREVIOUS_NEEDS))
        from researchhelper.period_targets import resolve
        periods = {row['text']: row['resolved_period'] for row in resolve(task)}
        self.assertEqual({periods[text] for text in LATEST_NEEDS}, {'2026-09'})
        self.assertEqual({periods[text] for text in PREVIOUS_NEEDS}, {'2026-08'})

    def test_new_catalogue_adopts_relevant_definition_without_market_facts(self):
        package = self.package()
        self.assertEqual(package[CATALOGUE_POLICY], 2)
        self.assertIn(IDENT, package['index']); self.knowledge.validate_package(package, revision=2)
        self.assertEqual(package['index'][IDENT], 1)
        self.assertNotIn('2026年9月制造业PMI为', self.entry['content'])

    def test_old_catalogue_and_unbound_legacy_read_do_not_adopt_new_entry(self):
        self.assertNotIn(IDENT, self.package(1)['index'])
        self.assertNotIn(IDENT, self.knowledge.read('宏观', '制造业PMI')['index'])
        self.assertNotIn(IDENT, {row['id'] for row in self.knowledge.catalog(1)})
        self.assertIn(IDENT, {row['id'] for row in self.knowledge.catalog()})

    def test_later_entry_revision_does_not_erase_old_catalogue_body(self):
        from researchhelper import knowledge
        newer = copy.deepcopy(self.entry)
        newer.update(version=2, catalogue_introduced=3, content='合成的后续版本，用于目录隔离检查')
        entries = [newer if item['id'] == IDENT else item for item in ENTRIES]
        retained = dict(knowledge.RETAINED_VERSIONS); retained[(IDENT, 2)] = newer
        with (patch.object(knowledge, 'ENTRIES', entries), patch.object(knowledge, 'RETAINED_VERSIONS', retained),
              patch.object(knowledge, 'CATALOGUE_REVISION', 3)):
            old = next(item for item in self.knowledge.catalog(2) if item['id'] == IDENT)
            current = next(item for item in self.knowledge.catalog(3) if item['id'] == IDENT)
            self.assertEqual(old, self.entry); self.assertEqual(current, newer)
            package = self.package(2); self.assertEqual(package['index'][IDENT], 1)
            self.knowledge.validate_package(package, revision=2)

    def test_old_task_does_not_offer_new_planning_definition(self):
        old = self.knowledge.planning_definitions(self.task)
        self.assertNotIn(IDENT, {row['id'] for row in old['candidates']})
        self.task[CATALOGUE_POLICY] = 2
        current = self.knowledge.planning_definitions(self.task)
        self.assertIn(IDENT, {row['id'] for row in current['candidates']})

    def test_unknown_catalogue_revision_is_not_treated_as_latest(self):
        for value in (True, False, None, 0, 3, '2', 2.0):
            with self.assertRaises(ContractError): catalogue_revision(value)
            with self.assertRaises(ContractError): self.package(value)

    def test_new_entry_cannot_be_spliced_into_an_old_package(self):
        package = self.package(1)
        package['entries'].append(copy.deepcopy(self.entry)); package['index'][IDENT] = 1
        with self.assertRaises(ContractError): self.knowledge.validate_package(package)
        current = self.package()
        with self.assertRaises(ContractError): self.knowledge.validate_package(current, revision=1)

    def test_legacy_published_entry_bodies_are_not_replaced(self):
        old = self.task['knowledge']; self.knowledge.validate_package(old, revision=1)
        current = self.package(); indexed = {entry['id']: entry for entry in current['entries']}
        for entry in old['entries']:
            if entry['id'] in indexed: self.assertEqual(fingerprint(entry), fingerprint(indexed[entry['id']]))
        self.assertEqual(task_catalogue_revision(self.task), 1)

    def test_unrelated_equity_and_fee_questions_do_not_force_pmi_knowledge(self):
        for entry, question in (('个股', '比较企业收入和利润'), ('ETF', '比较管理费'), ('混合', '比较ETF管理费')):
            package = self.knowledge.read(entry, question, context={CATALOGUE_POLICY: 2})
            self.assertNotIn(IDENT, package['index'])

    def test_fresh_actual_resource_task_pins_catalogue_and_adopts_without_model(self):
        with tempfile.TemporaryDirectory(prefix='curated-policy-') as folder:
            agent = self.agent(folder)
            task = agent.create('解释制造业PMI的计量含义', entry='宏观', research_mode='current')
            self.assertEqual(task[CATALOGUE_POLICY], CATALOGUE_REVISION)
            agent._adopt_method_knowledge(task)
            self.assertEqual(task['knowledge'][CATALOGUE_POLICY], 2)
            self.assertIn(IDENT, task['knowledge']['index'])
            self.assertFalse(task['evidence']); self.assertIsNone(agent.model)
            self.assertEqual(task['ledger']['used']['model_calls'], 0)

    def test_resuming_old_task_does_not_expand_its_catalogue(self):
        with tempfile.TemporaryDirectory(prefix='curated-legacy-') as folder:
            agent = self.agent(folder); old = self.task; original = copy.deepcopy(old['knowledge']['entries'])
            agent._adopt_method_knowledge(old)
            self.assertNotIn(IDENT, old['knowledge']['index'])
            by_id = {entry['id']: entry for entry in old['knowledge']['entries']}
            self.assertTrue(all(by_id[entry['id']] == entry for entry in original))
            self.assertNotIn(CATALOGUE_POLICY, old)

    def test_catalogue_mismatch_is_rejected_without_mutation_or_paid_retry(self):
        self.task['knowledge'] = self.package()
        before = canonical(self.task)
        with tempfile.TemporaryDirectory(prefix='curated-mismatch-') as folder:
            agent = self.agent(folder)
            with self.assertRaises(ContractError): agent._adopt_method_knowledge(self.task)
        self.assertEqual(canonical(self.task), before)

    def test_explicit_definition_is_generated_from_locked_entry_without_new_facts(self):
        with tempfile.TemporaryDirectory(prefix='curated-definition-') as folder:
            agent = self.agent(folder)
            question = dict(id='q1', question='解释PMI的计量含义', kind='fact', required=True,
                dependencies=[], evidence_needs=[], method_needs=[], answer_scope='general_definition', knowledge_needs=[IDENT])
            task = agent.create(question['question'], entry='宏观', questions=[question], research_mode='current')
            agent._adopt_method_knowledge(task)
            contract = self.knowledge.definition_contract(task, task['questions'][0])
            self.assertTrue(contract['generation_ready']); self.assertFalse(contract['fact_approval'])
            self.assertEqual(contract['knowledge_refs'], [dict(id=IDENT, version=1)])
            self.assertFalse(task['evidence']); self.assertFalse(task['calculations'])
            old = copy.deepcopy(task); old.pop(CATALOGUE_POLICY)
            with self.assertRaises(ContractError): self.knowledge.definition_contract(old, old['questions'][0])

    def test_processing_state_is_absent_for_old_tasks_and_rejects_unknown_versions(self):
        self.assertIsNone(processing_state(self.task))
        for value in (True, 0, 2, '1'):
            self.task[STATE_POLICY] = value
            with self.assertRaises(ContractError): processing_state(self.task)

    def test_processing_state_distinguishes_adopted_from_missing_without_absence_claim(self):
        state = processing_state(self.state_task())
        self.assertEqual([row['status'] for row in state['requirements']].count('adopted_sufficient'), 4)
        self.assertEqual([row['status'] for row in state['requirements']].count('not_adopted'), 3)
        self.assertTrue(all(row['source_absence_proved'] is False for row in state['requirements']))
        self.assertFalse(state['financial_approval']); self.assertFalse(state['automatic_completion'])

    def test_partial_observations_are_not_counted_as_fully_satisfied(self):
        task = self.state_task(); question = task['questions'][0]
        first = next(row['need'] for row in processing_state(task)['requirements']
                     if row['status'] == 'adopted_sufficient'
                     and question['evidence_need_constraints'][row['need']]['value_kind'] == 'number')
        question['evidence_need_constraints'][first]['minimum_count'] = 2
        row = next(row for row in processing_state(task)['requirements'] if row['need'] == first)
        self.assertEqual(row['status'], 'adopted_partial')

    def test_cross_task_or_unapproved_record_cannot_satisfy_processing_state(self):
        task = self.state_task()
        for item in task['evidence']: item['task_id'] = 'another_task'
        self.assertTrue(all(row['status'] == 'not_adopted' for row in processing_state(task)['requirements']))
        task = copy.deepcopy(self.fixture['task']); task[STATE_POLICY] = 1
        for item in task['evidence']: item['applicability_check'] = '未核验'
        self.assertTrue(all(row['status'] == 'not_adopted' for row in processing_state(task)['requirements']))

    def test_gap_text_and_not_found_reason_do_not_become_source_absence(self):
        task = self.state_task(); original = processing_state(task)
        task['gaps'].append('合成错误缺失断言：原件没有提供其他信息')
        task.setdefault('reasoning_state', {})['untrusted_missing_note'] = '未找到，所以未披露'
        self.assertEqual(processing_state(task), original)

    def test_author_and_reviewer_receive_same_state_without_rewriting_proposal(self):
        task = self.state_task(); before = canonical(task)
        contract = analysis_contract(task)
        request = review_request(task, task['host_result'])
        self.assertEqual(request['evidence_processing_state'], contract['evidence_processing_state'])
        self.assertEqual(request['proposal']['answers'], task['host_result']['answers'])
        self.assertEqual(canonical(task), before)

    def test_review_hash_binds_processing_projection_not_just_existing_evidence(self):
        task = self.state_task()
        # Omit the projection only in this synthetic old-payload control.
        # Do not change a live scope or reuse invalidated evidence receipts.
        with patch('researchhelper.data_requirements.processing_state', return_value=None):
            old = review_request(task, task['host_result'])
        current = review_request(task, task['host_result'])
        self.assertNotEqual(old['review_hash'], current['review_hash'])
        self.assertNotIn('evidence_processing_state', old)

    def test_report_and_archive_preserve_catalogue_and_processing_policy(self):
        from researchhelper.reports import compose
        task = self.state_task(); task[CATALOGUE_POLICY] = 2; task['knowledge'] = self.package()
        task['input_hash'] = input_fingerprint(task)
        content = compose(task)
        self.assertEqual(content[CATALOGUE_POLICY], 2); self.assertEqual(content[STATE_POLICY], 1)
        self.assertIn(IDENT, content['knowledge']['index'])
        with tempfile.TemporaryDirectory(prefix='curated-archive-') as folder:
            archive = ResearchArchive(folder); private = copy.deepcopy(task); private['revision'] = 0
            archive.save(private); restored = archive.get(private['id'])
            self.assertEqual(restored['knowledge'], task['knowledge'])
            self.assertEqual(processing_state(restored), processing_state(task))


if __name__ == '__main__': unittest.main()
