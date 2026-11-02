"""One ResearchAgent orchestrates local evidence, calculations and frozen delivery."""

from __future__ import annotations

from .market_calendar import covered_need_ids as calendar_need_ids, scope_basis as calendar_scope_basis

import copy
import re
import threading
import time
import uuid
from pathlib import Path

from .archive import ResearchArchive
from .analyzer import ResearchAnalyzer
from .cases import CUTOFF, get_case
from .contracts import (ContractError, MethodNotApplicable, DOCUMENT_CONTENT_INTENT, DOCUMENT_CONTENT_SCOPE, DOCUMENT_CONTENT_LIMITATION,
                        document_content_answer, missing_document_requirements, missing_evidence_requirements, evidence_requirements,
                        ENTRIES, INTENTS, METHOD_REGISTRY, FINANCIAL_PERIOD_METHODS, budget, calculate, calculation_dependency_hash, canonical,
                        charge, context, dependency_versions, fingerprint, module_result, now, request, supplement, timepoint,
                        tracking_record, validate_tracking, validate_evidence, validate_request, validate_result)
from .evidence import (EvidenceCollector, FILING_REQUEST_REGISTRY, business_payload, candidate_evidence, data_request_contract,
                       extract_material, filing_record, material_record, snapshot_record,
                       validate_filing_original_request, validate_ifind_report_request,
                       available_security_codes, identity_resolutions)
from .evidence import filing_candidate_choices, record_request_purpose
from .evidence import (FIELD_REGISTRY, reference_groups, current_snapshot_reference, api_calculation_context_contract,
                       expand_api_cell_selections, API_CELL_CHOICE_COVERAGE_VERSION, current_query_reference)
from .financial_sources import FINANCIAL_METRIC_BINDING_VERSION, expand_financial_value_references, IFIND_METADATA_REGISTRY
from .knowledge import ResearchKnowledge
from .numeric_integrity import bind_numeric_statement
from . import judgment_review
from . import argument_support
from .model import ModelCompletionError, analysis_completion_failure
from .planning import DIRECT_KINDS, build_plan, research_brief, validate_question_revision, validate_definition_actions, conversation_context, input_fingerprint, normalize_questions, plan_preservation_contract, question_specs, question_contract, thesis_obligations
from .reasoning import (model_object, validate_analysis_output, validate_analysis_execution, analysis_execution_contract, analysis_numeric_review, calculation_input_requirements, QueryReferenceFormatError, NumericSegmentFormatError, ModelJSONFormatError,
                        original_sources, planning_digest, source_views, extractable_source_views, evidence_contract, analysis_contract, calculation_plan_contract,
                        SUPPLEMENT_FIELDS, API_EXTRACTION_GUIDANCE_VERSION, CALCULATION_BINDING_PROMPT_VERSION,
                        supplement_request_blocked, supplement_reread_pairs, supplement_reread_contract,
                        record_extraction_validation, validate_model_specs, validate_plan, validate_supplement,
                        expand_api_evidence_groups, expand_material_publication_references, expand_material_context_references,
                        analysis_fields, phase_output_example)
from .source_reader import (binding as reading_binding, catalog as source_catalog, context_sources,
                            read as read_sources, validate_reading, validate_read_specs, retained_reading, ReadingSelectionError,
                            reader_version_for, registered_reading_requests)


from .layout import default_resource_root


class ResearchAgent:
    def __init__(self, archive: ResearchArchive, resources=None, offline=True, model=None, source_credentials=None, isolate_sources=False, isolate_materials=False, require_argument_support=False, draft_first=False):
        if type(require_argument_support) is not bool:
            raise ContractError('论证复核策略必须由产品入口明确指定')
        # Bare engine callers retain the old contract. Both public products
        # enable this policy; neither exposes a user-controlled bypass.
        self.require_argument_support = require_argument_support
        if type(draft_first) is not bool:
            raise ContractError('首次报告策略须由产品入口指定')
        self.draft_first = draft_first
        if not isinstance(isolate_sources, bool):
            raise ContractError("取数传输模式必须明确指定")
        self.archive = archive
        self.resources = Path(resources) if resources is not None else default_resource_root()
        self.offline = offline
        self.model = model
        # In-memory, explicitly provided secrets only. Never serialize credentials
        # into a task, dispatch journal, Skill installation or raw response.
        self.source_credentials = source_credentials or {}
        self.collector = EvidenceCollector(offline, journal=self._journal)
        self.material_parser = None
        if not isinstance(isolate_materials, bool):
            raise ContractError("材料解析模式必须明确指定")
        if isolate_materials:
            from .material_parser import MaterialParser
            self.material_parser = MaterialParser()
        self.analyzer = ResearchAnalyzer()
        self.knowledge = ResearchKnowledge()
        self.lock = threading.RLock()
        self.workers = {}
        # Runtime-only stop signals, never serialized into research artifacts.
        # Transport polling must not acquire the archive or research lock.
        self.control_lock = threading.Lock()
        self.run_controls = {}
        self.runtime_closed = False
        if isolate_sources:
            from .source_transport import IsolatedEvidenceCollector
            self.collector = IsolatedEvidenceCollector(offline=offline, journal=self._journal, active=self._check_run_control)
        self.collector.original_keeper = self.archive.retain_original

    def _begin_run_control(self, ident, version, batch):
        with self.control_lock:
            for key in list(self.run_controls):
                if key[0] == ident:
                    self.run_controls.pop(key).set()
            self.run_controls[(ident, version, batch)] = threading.Event()

    def _finish_run_control(self, ident, version, batch):
        with self.control_lock:
            control = self.run_controls.pop((ident, version, batch), None)
            if control is not None:
                control.set()

    def _check_run_control(self, task):
        """Non-I/O transport guard; archive validation still gates adoption."""
        with self.control_lock:
            control = self.run_controls.get((task["id"], task["version"], task["batch"]))
            if control is None or control.is_set():
                raise ContractError("任务已停止或执行批次过期")

    def close_runtime(self):
        """Stop this service's dispatches without resetting or deleting tasks.

        Workers settle their original elapsed time and checkpoints through the
        normal failure path. The caller retains its Store lease until they exit.
        Skill sessions need not close the shared engine between host steps.
        """
        with self.lock:
            self.runtime_closed = True
            with self.control_lock:
                for signal in self.run_controls.values():
                    signal.set()
            return tuple(self.workers.values())

    def _journal(self, task):
        """Persist spent dispatches without overwriting a concurrent cancellation."""
        with self.lock:
            fresh = self._get(task["id"])
            if fresh["version"] != task["version"] or fresh["batch"] != task["batch"]:
                raise ContractError("请求所属执行批次已过期")
            limited = False
            events = task["ledger"]["dispatches"]
            persisted_count = len(fresh["ledger"]["dispatches"])
            # The collector journals a proposed dispatch before touching the
            # transport. Its local retry loop must not reset the per-request
            # limit when the user resumes a discarded, known-returned response.
            for index in range(len(events) - 1, persisted_count - 1, -1):
                event = events[index]
                digest = event.get("fingerprint")
                if event.get("kind") != "data_requests" or event.get("status") != "已派发" or digest not in task.get("source_attempts", {}):
                    continue
                prior_count = sum(e.get("kind") == "data_requests" and e.get("fingerprint") == digest for e in events[:index])
                if prior_count >= 3:
                    events.pop(index)
                    task["ledger"]["used"]["data_requests"] -= 1
                    limited = True  # undo only this uncommitted, never-sent reservation
            if fresh["status"] == "已取消":
                retained = {r.get("request_hash") for r in task.get("source_records", []) + fresh.get("source_records", [])}
                for event in events:
                    digest = event.get("fingerprint")
                    if event.get("kind") == "data_requests" and event.get("batch") == task["batch"] and event.get("status") in ("成功", "成功但为空") and digest not in retained:
                        attempt = task.get("source_attempts", {}).get(digest)
                        if attempt is not None:
                            attempt.update(status="响应已返回但取消丢弃", discarded_batch=task["batch"], discarded_at=now())
                            event["result_disposition"] = "取消后丢弃，未接受原件或研究结果"
            fresh["ledger"] = copy.deepcopy(task["ledger"])
            if "source_records" in task:
                records = {r["id"]: r for r in fresh.get("source_records", [])}
                records.update({r["id"]: copy.deepcopy(r) for r in task["source_records"]})
                fresh["source_records"] = list(records.values())
            if "source_attempts" in task:
                fresh["source_attempts"] = copy.deepcopy(task["source_attempts"])
            self.archive.save(fresh)
            task["revision"] = fresh["revision"]
            if limited:
                raise ContractError("同一请求跨恢复已达到三次派发上限，未发送新的请求")
            if fresh["status"] != "运行中":
                raise ContractError("任务已停止，保留已派发用量但拒绝结果")

    def create(self, question, entry="混合", intent="综合研究", objects=None, cutoff=None, limits=None, fixture=None, parent=None, questions=None, research_mode=None):
        task = self._prepare_task(question, entry, intent, objects, cutoff, limits, fixture, parent, questions, research_mode)
        return self.archive.save(task)

    def _prepare_task(self, question, entry="混合", intent="综合研究", objects=None, cutoff=None, limits=None, fixture=None, parent=None, questions=None, research_mode=None):
        """Build validated input without exposing an incomplete task in the archive."""
        if not isinstance(question, str) or not question.strip() or len(question) > 20000:
            raise ContractError("请输入有效研究问题，最长20000字")
        if entry not in ENTRIES or intent not in INTENTS:
            raise ContractError("研究入口或意图未登记")
        if research_mode not in (None, "current", "historical"):
            raise ContractError("研究时点模式未登记")
        if research_mode == "current" and cutoff is not None:
            raise ContractError("当前研究由内核登记启动时点；指定历史截止时点须使用historical模式")
        cutoff = cutoff or now()
        timepoint(cutoff)
        fixture_data = get_case(fixture) if fixture else None
        question_origin = "fixture" if fixture else "default" if questions is None else "explicit"
        if fixture_data:
            question, entry, intent, objects = (fixture_data[k] for k in ("question", "entry", "intent", "objects"))
        questions = normalize_questions(fixture_data["questions"] if fixture_data else questions, question.strip(), intent)
        if objects is not None and (not isinstance(objects, list) or not all(isinstance(obj, str) for obj in objects)):
            raise ContractError("对象范围必须为文字列表")
        adopted = {"question": question.strip(), "entry": entry, "intent": intent, "objects": list(objects or []), "cutoff": cutoff}
        if research_mode is not None:
            adopted["research_mode"] = research_mode
        task = dict(adopted, id="rh_" + uuid.uuid4().hex, version=1, batch=0, revision=0,
                    original_input=copy.deepcopy(adopted), input_hash=fingerprint(adopted), status="待开始",
                    created_at=now(), fixture=fixture, synthetic=bool(fixture), parent=parent,
                    ledger=budget(limits), questions=[dict(item, status="待回答") for item in questions],
                    evidence=[], rejected_evidence=[], calculations=[], theses=[], materials=[], source_records=[], gaps=[], tracking=[],
                    optional_findings=[], nodes=[], revisions=[], delivery=None, error=None, plan_version=1,
                    question_origin=question_origin,
                    calculation_specs=[fixture_data["calculation"]] if fixture_data and fixture_data.get("calculation") else [],
                    requested_theses=bool(fixture_data), data_need_specs=[])
        task["original_input"]["questions"] = copy.deepcopy(questions)
        if not fixture:
            from .contracts import METHOD_CATALOGUE_VERSION
            task['method_catalogue_version'] = METHOD_CATALOGUE_VERSION
            # An explicitly supplied legacy method remains an obligation.
            # Only an unplanned new study uses the source-aware fee catalogue.
            task['fee_method_catalogue_version'] = (1 if any(isinstance(item, dict)
                and 'fee_difference_v1' in item.get('method_needs', []) for item in (questions or [])) else 2)
            task['official_channels_version'] = 1
            task['official_etf_pcf_version'] = 2
            task['official_catalogue_pagination_version'] = 1
            task['official_policy_channels_version'] = 1
            task['official_temporal_contract_version'] = 1
            task['official_index_sources_version'] = 1
            task['official_selection_reference_version'] = 1
        if self.require_argument_support and not fixture and intent != DOCUMENT_CONTENT_INTENT:
            task['argument_support_contract'] = argument_support.VERSION
            task['argument_reference_format'] = 2
            task['passage_context_version'] = 2
            task['planning_request_encoding'] = 2
            task['planning_scope_version'] = 1
            if intent != DOCUMENT_CONTENT_INTENT:
                task['research_outline_version'] = 1
            task['security_declaration_encoding'] = 2
            task['analysis_reporting_version'] = 2
            task['analysis_citation_encoding'] = 2
            task['analysis_question_scope_version'] = 1
            task['review_basis_presentation_version'] = 1
            task['analysis_input_presentation_version'] = 2
            task['original_span_diagnostics_version'] = 1
            task['independent_original_selections_version'] = 1
            task['original_requirement_isolation_version'] = 1
            task['document_date_role_version'] = 1
            task['source_label_selection_version'] = 1
            task['original_requirement_feedback_version'] = 1
            task['source_period_targets_version'] = 1
            from .knowledge import CATALOGUE_REVISION
            task['knowledge_catalogue_revision'] = CATALOGUE_REVISION
            task['evidence_processing_state_version'] = 1
            task['source_field_inventory_version'] = 1
            task['official_index_profile_catalogue_version'] = 2
            task['ifind_metadata_sources_version'] = 1
            if research_mode == 'current':
                task['fixed_financial_catalog_version'] = 1
                if intent != DOCUMENT_CONTENT_INTENT:
                    task['fixed_financial_context_version'] = 1
                    task['economic_discovery_version'] = 1
                    task['economic_observation_version'] = 1
                    task['metadata_navigation_progress_version'] = 1
                    task['series_batch_version'] = 1
                    task['source_capability_roadmap_version'] = 1
                    task['economic_navigation_version'] = 1
                    task['financial_statement_unit_scope_version'] = 1
                    task['financial_response_navigation_version'] = 1
            task['partial_financial_response_version'] = 1
            task['original_scope_wire_version'] = 1
            task['api_context_wire_version'] = 1
            task['flat_api_selection_version'] = 1
            task['registered_etf_share_join_version'] = 1
            task['review_answer_choices_version'] = 1
            task['official_etf_cell_selection_version'] = 1
            task['series_parameter_selection_version'] = 1
            from .market_series import SERIES_ROUTING_VERSION, SERIES_FAMILIES
            task['series_parameter_registry'] = dict(version=SERIES_ROUTING_VERSION,
                fields=copy.deepcopy({name: values for family in SERIES_FAMILIES.values() for name, values in family.items()}))
            task['statistical_metric_identity_version'] = 1
            task['literal_quantity_reference_version'] = 5
            task['navigation_question_scope_version'] = 1
            task['argument_choice_recovery_version'] = 1
            task['analysis_structure_recovery_version'] = 1
            task['analysis_reference_choice_recovery_version'] = 1
            task['analysis_schema_sharing_version'] = 1
            task['analysis_feedback_projection_version'] = 1
            task['original_question_link_version'] = 1
            task['original_context_question_link_version'] = 1
            task['original_fee_context_mapping_version'] = 1
            task['initial_fee_input_completion_version'] = 1
            task['partial_fee_input_completion_version'] = 1
            task['original_interval_context_version'] = 1
            task['listed_report_unit_context_version'] = 1
            task['structured_schema_sharing_version'] = 1
            task['request_schema_sharing_version'] = 1
            task['method_excerpt_selection_version'] = 1
            task['method_literal_address_version'] = 1
            task['original_local_span_version'] = 1
            task['original_whole_part_selection_version'] = 1
            task['ifind_gateway_recovery_version'] = 1
            task['extraction_knowledge_guidance_version'] = 1
            task['original_concept_navigation_version'] = 1
            task['knowledge_reading_navigation_version'] = 1
            task['financial_input_reading_priority_version'] = 3
            task['financial_need_map_version'] = 1
            task['financial_question_owner_version'] = 1
            task['financial_observation_reference_version'] = 1
            task['financial_selector_projection_version'] = 1
            task['negative_claim_scope_version'] = 1
            task['research_synthesis_version'] = 1
            task['review_revision_readiness_version'] = 1
            task['mixed_question_revision_version'] = 1
            task['registered_source_reading_version'] = 1
            task['review_choice_delivery_version'] = 1
            task['identity_candidate_lookup_version'] = 2
            task['name_candidate_observation_version'] = 1
            task['catalog_query_result_basis_version'] = 1
            task['original_period_selection_version'] = 1
            task['original_observation_selection_version'] = 1
            task['index_constituent_source_version'] = 1
            task['candidate_code_observation_version'] = 1
            task['nonfinancial_count_grammar_version'] = 2
            task['catalog_scoped_writing_version'] = 1
            task['original_concept_reading_version'] = 1
            task['original_literal_choice_recovery_version'] = 1
            task['numeric_lookup_isolation_version'] = 1
            task['original_prompt_projection_version'] = 2
            task['original_number_catalogue_capacity_version'] = 1
            task['original_explicit_unit_reference_version'] = 1
            task['acquisition_reading_intent_version'] = 1
            task['financial_acquisition_route_version'] = 1
            task['original_duration_anchor_version'] = 1
            task['catalogue_alternative_version'] = 1
            if question_origin=='default':
                if research_mode == 'current' and intent != DOCUMENT_CONTENT_INTENT:
                    task['structured_source_priority_version'] = 1
                    task['fixed_financial_route_version'] = 1
                task['extraction_read_isolation_version'] = 2
                task['mixed_atomic_api_reading_version'] = 1
                task['typed_evidence_needs_version'] = 1
                task['knowledge_reading_navigation_version'] = 2
                task['typed_observation_contract_version'] = 2
                task['typed_numeric_selection_version'] = 3
                task['required_numeric_completion_version'] = 1
                task['api_need_compatibility_version'] = 2
                task['index_weight_evidence_version'] = 1
                task['discovery_planned_scope_version'] = 1
                task['financial_batch_query_version'] = 1
                task['semantic_query_encoding'] = 1
                task['financial_batch_followup_version'] = 1
                task['progressive_financial_acquisition_version'] = 1
                task['declared_reading_capacity_version'] = 1
                task['original_observation_reference_version'] = 1
                task['shared_original_dates_version'] = 1
                task['source_extraction_batches_version'] = 1
                task['source_extraction_id_choices_version'] = 1
            task['method_literal_quantity_version'] = 1
            task['analysis_question_map_version'] = 1
            task['analysis_prose_reference_version'] = 1
            task['filing_keyword_filter_version'] = 1
            task['filing_filter_profile_version'] = 1
            task['filing_candidate_distribution_version'] = 1
            task['object_discovery_version'] = 2
            task['acquisition_request_encoding'] = 1
            task['discovery_sample_scope_version'] = 1
            task['financial_quote_fallback_version'] = 1
            task['statistical_reading_version'] = 1
            task['argument_context_version'] = 2
            task['extraction_submission_recovery_version'] = 1
            task['extraction_value_recovery_version'] = 1
            task['dependency_synthesis_version'] = 1
            task['financial_text_period_version'] = 2
            task['financial_period_need_compatibility_version'] = 1
            task['independent_financial_selections_version'] = 1
            task['financial_address_isolation_version'] = 1
            task['financial_amount_selection_version'] = 1
            task['financial_data_product_version'] = 1
            task['source_request_fields_version'] = 1
            task['financial_amount_reference_version'] = 1
            task['financial_statement_details_version'] = 1
            task['table_geometry_projection_version'] = 1
            task['fee_original_selection_version'] = 1
            task['fee_candidate_encoding'] = 1
            task['api_cell_selection_encoding'] = 1
            task['definition_projection_version'] = 1
            task['planning_definition_scope_version'] = 1
            task['initial_acquisition_scope_version'] = 1
            task['acquisition_budget_navigation_version'] = 1
            task['acquisition_downstream_budget_version'] = 1
            task['registered_api_source_paths_version'] = 1
            task['model_context_budget_version'] = 1
            task['original_span_selection_version'] = 1
            task['general_original_selection_version'] = 2
            task['statistical_period_version'] = 1
            task['original_number_reference_version'] = 1
            task['evidence_gap_lifecycle_version'] = 1
            task['method_source_coverage_version'] = 1
            task['missing_api_context_selection_version'] = 1
            task['daily_nav_date_roles_version'] = 1
            task['nav_original_choice_version'] = 1
            task['nav_original_only_selection_version'] = 1
            task['planning_main_binding_version'] = 1
            task['market_calendar_coverage_version'] = 1
            task['market_coverage_basis_version'] = 1
            task['reviewed_writing_revision_version'] = 1
            task['review_delivery_call_version'] = 1
            task['observed_window_date_roles_version'] = 1
            task['economic_identifier_namespace_version'] = 2
            task['mixed_original_selection_version'] = 1
            task['original_context_selection_version'] = 1
            task['listed_etf_unit_context_version'] = 1
            task['current_scope_planning_version'] = 1
            task['catalog_metadata_basis_version'] = 1
            task['original_temporal_choice_recovery_version'] = 1
            task['catalogue_search_navigation_version'] = 1
            task['verbatim_evidence_segments_version'] = 1
            task['planning_submission_recovery_version'] = 1
            task['argument_submission_recovery_version'] = 1
        if self.draft_first and not fixture and intent != DOCUMENT_CONTENT_INTENT:
            self._apply_draft_policies(task)
        task["input_hash"] = input_fingerprint(task)
        task["nodes"] = self.plan(task)
        task["thread_id"] = task["id"]
        task["messages"] = [self._message("user", task["question"], task)]
        return task

    @staticmethod
    def _apply_draft_policies(task):
        """Adopt current readers only for a new task or an explicit new version.

        Opening, cancelling or resuming a frozen version never calls this.
        Formal research keeps its original workflow and approval contracts.
        """
        from .draft_sources import SCOPE_VERSION, VERSION
        task.update(draft_first_version=1, draft_quote_scope_version=SCOPE_VERSION, draft_source_reader_version=VERSION,
                    draft_direct_scope_version=1, draft_question_scope_version=2,
                    hk_code_alias_version=1)

    @staticmethod
    def _message(role, content, task):
        return {"id": "message_" + uuid.uuid4().hex, "role": role, "content": content,
                "task_id": task["id"], "version": task["version"], "synthetic": task["synthetic"]}

    @staticmethod
    def _draft_conversation_answer(previous, message):
        """Quote the displayed draft for reference resolution, never evidence."""
        paper = previous.get('draft_report')
        if not isinstance(paper, dict):
            return None
        logics = [item for item in paper.get('logics', []) if isinstance(item, dict)]
        lines = ['研究初稿，尚未独立复核：' + str(paper.get('title') or '研究报告')]
        match = re.search(r'(?:策略逻辑|逻辑|策略)\s*([0-9一二三四五六七八九十]+)(?![0-9一二三四五六七八九十百千万])', message)
        selected = None
        if match:
            number = match.group(1)
            digits = dict(zip('一二三四五六七八九', range(1, 10)))
            if number.isdecimal():
                ordinal = int(number)
            elif '十' in number and number.count('十') == 1:
                tens, units = number.split('十')
                ordinal = ((digits.get(tens, 1) if tens else 1) * 10 + digits.get(units, 0)
                           if (not tens or tens in digits) and (not units or units in digits) else 0)
            else:
                ordinal = digits.get(number, 0)
            if 1 <= ordinal <= len(logics):
                selected = ordinal - 1
        def quote(index):
            item = logics[index]
            return '\n'.join(['策略逻辑' + str(index + 1) + '：' + str(item.get('title') or ''),
                *[label + str(item[key]) for key, label in
                  [('analysis', '分析：'), ('implication', '判断：'), ('risk', '反证：')]
                  if isinstance(item.get(key), str) and item[key]]])
        if selected is not None:
            lines.append(quote(selected))
        if logics:
            lines.append('逻辑目录：' + '；'.join('逻辑' + str(i + 1) + '：' + str(item.get('title') or '')
                     for i, item in enumerate(logics)))
        if paper.get('conclusion'):
            lines.append('核心结论：' + str(paper['conclusion']))
        lines.extend(quote(i) for i in range(len(logics)) if i != selected)
        return '\n'.join(lines)

    def follow_up(self, ident, message, asynchronous=False, start=True):
        """A changed question is a related task, never an edit to a frozen result."""
        if not isinstance(start, bool):
            raise ContractError("追问启动方式必须明确指定")
        if not isinstance(message, str) or not message.strip() or len(message) > 20000:
            raise ContractError("请输入有效追问，最长20000字")
        with self.lock:
            previous = self._get(ident)
            conversation_context(previous)
            if previous["status"] == "运行中":
                raise ContractError("当前研究仍在运行，请先停止或等待完成")
            history = copy.deepcopy(previous.get("messages") or [self._message("user", previous["question"], previous)])
            if len(history) >= 99:
                raise ContractError("本对话已达到50轮，请新建研究；旧记录仍保留")
            conclusions = [f"{item.get('judgment', '证据不足')}：{item.get('statement', item.get('title', ''))}"
                           for item in previous["theses"]]
            if not conclusions:
                draft_answer = self._draft_conversation_answer(previous, message.strip())
                conclusions = ([draft_answer] if draft_answer else
                    [q["answer"] for q in previous["questions"] if q["status"] == "已回答" and q.get("answer")])
            gaps = ["未解决：" + item for item in previous["gaps"]]
            prior_answer = "\n".join(conclusions + gaps) or "本轮尚未形成研究结论。"
            snapshot = self._message("assistant", prior_answer, previous)
            snapshot.update(status=previous["status"], delivery=copy.deepcopy(previous.get("delivery")))
            history.append(snapshot)
            objects, conversation = self._follow_up_context(previous, message.strip(), prior_answer)
            related = self._prepare_task(message.strip(), entry=previous["entry"], intent=previous["intent"],
                                  objects=objects, limits=previous["ledger"]["limits"], parent=ident,
                                  cutoff=previous['cutoff'] if previous.get('research_mode') == 'historical' else None,
                                  research_mode=previous.get("research_mode"))
            related["conversation_context"] = conversation
            related["original_input"]["objects"] = copy.deepcopy(objects)
            related["original_input"]["conversation_context"] = copy.deepcopy(conversation)
            related["input_hash"] = input_fingerprint(related)
            related["nodes"] = self.plan(related)
            related.update(thread_id=previous.get("thread_id", ident),
                           conversation_parent={"task_id": ident, "version": previous["version"],
                                                "delivery": copy.deepcopy(previous.get("delivery"))},
                           messages=history + [self._message("user", message.strip(), related)])
            if 'owner_account' in previous:
                related['owner_account'] = previous['owner_account']
            self.archive.create_related(related, previous)
            # Prior conversation is readable context only, not verified evidence for this task.
            return self.start(related["id"], asynchronous=asynchronous) if start else related

    @staticmethod
    def _follow_up_context(previous, message, prior_answer):
        """Carry only a direct parent snapshot and user-declared object scope.

        A newly named code replaces inherited scope. An explicit change by name
        clears old objects until the normal identity workflow resolves the name.
        Assistant suggestions and arbitrary older messages never add objects.
        """
        code_pattern = r"(?<![A-Za-z0-9])(?:\d{6}\.(?:SH|SZ)|\d{4,5}\.HK)(?![A-Za-z0-9])"
        current_codes = list(dict.fromkeys(re.findall(code_pattern, message)))
        prior_objects = list(dict.fromkeys(previous["objects"] + re.findall(code_pattern, previous["question"])))
        prior_codes = {code for text in prior_objects for code in re.findall(code_pattern, text)}
        refers_back = bool(re.search(r"它|该(?:股|公司|基金|对象)|前者|后者|上述|之前|原来|这(?:只|些|两)", message))
        comparison = bool(current_codes and re.search(r"比较|对比|相比", message)
                          and (refers_back or re.search(r"加入|增加|再加", message)))
        # Changing an analytical angle or moving to risk analysis is still a
        # continuation. Only explicit object-switch wording drops old scope.
        named_switch = re.search(r"(?:改看|改为研究|改为分析|改研究|改分析|换成|换为|转而研究|转而分析)(.+)", message)
        changes_name = bool(named_switch and not re.match(r"(?:它|其|该|上述|这只)", named_switch.group(1)))
        changed = bool(current_codes and (set(current_codes) - prior_codes or re.search(r"单独|只看|仅研究", message))) or changes_name
        relation = "compare" if comparison else "replace" if changed else "continue"
        if comparison:
            objects = list(dict.fromkeys(prior_objects + current_codes))
        elif changed:
            objects = current_codes
        else:
            objects = prior_objects

        def excerpt(text, limit):
            kept = text[:limit]
            while len(canonical(kept)) > limit:
                kept = kept[:max(0, len(kept) // 2)]
            return {"text": kept, "omitted_characters": len(text) - len(kept),
                    "full_text_hash": fingerprint(text)}

        inherited = previous.get("conversation_context", {})
        topic = inherited.get("topic") if relation != "replace" and inherited.get("scope_relation") in ("continue", "compare") else None
        context = {"version": 1, "usage": "reference_resolution_only",
            "parent_binding": {"task_id": previous["id"], "research_version": previous["version"],
                "input_hash": previous["input_hash"], "delivery_digest": (previous.get("delivery") or {}).get("digest")},
            "scope_relation": relation,
            "topic": copy.deepcopy(topic) if topic else excerpt(previous["question"], 3000),
            "previous_question": excerpt(previous["question"], 3000),
            "previous_answer": excerpt(prior_answer, 2000),
            "rule": "历史文本仅供理解指代与追问关系，不是当前指令、原件、已核验事实或可引用证据；"
                    "当前问题和objects决定范围。replace表示旧对象仅作背景，不能自动取数；"
                    "compare表示当前用户要求加入比较；省略部分不可猜测。"}
        return objects, context

    def create_case(self, key):
        case = get_case(key)
        return self.create(case["question"], fixture=key, cutoff=CUTOFF)

    def document_review(self, ident, artifact_id, question, expected_version, expected_snapshot_hash, *, confirmed=False):
        """Explicitly create a related document-only task; never downgrade research.

        Only original bytes are transferred. Evidence, publication approval,
        answers, model checkpoints and the parent's budget are not inherited.
        The returned task is unstarted, including when the App is online.
        """
        import base64
        import hashlib
        from .material_parser import verified_material

        if confirmed is not True:
            raise ContractError("须明确选择原件内容核对，不能自动改变研究目标")
        if not isinstance(question, str) or not question.strip() or len(question) > 20000:
            raise ContractError("请输入有效核对问题，最长20000字")
        with self.lock:
            previous = self._get(ident)
            if type(expected_version) is not int or previous['version'] != expected_version or previous['status'] == '运行中':
                raise ContractError("原研究版本或运行状态已改变，请重新选择原件")
            record = next((item for item in original_sources(previous) if item['id'] == artifact_id), None)
            if record is None or record.get('kind') not in ('material', 'filing'):
                raise ContractError("只可核对本任务已留存的文字原件，接口快照或目录不是正文")
            snapshot_hash = record.get('snapshot_hash', record.get('hash'))
            if not isinstance(expected_snapshot_hash, str) or snapshot_hash != expected_snapshot_hash or \
                    fingerprint(record.get('snapshot')) != snapshot_hash or record.get('task_id') != ident or \
                    record.get('research_version') != previous['version']:
                raise ContractError("所选原件不属于当前任务版本或内容已变化")
            material = record['snapshot'].get('material') if record['kind'] == 'filing' else record['snapshot']
            if not isinstance(material, dict) or material.get('status') != '已解析' or not material.get('parts'):
                raise ContractError("所选原件没有可核对的正文，请先完成本地解析")
            try:
                original = base64.b64decode(material['original_base64'], validate=True)
            except (KeyError, ValueError):
                raise ContractError("所选原件字节缺失或格式异常") from None
            if hashlib.sha256(original).hexdigest() != material.get('hash'):
                raise ContractError("所选原件字节与内容指纹不一致")
            verified_material(material, original)
            # This new, explicit question has its own ledger. It cannot refund
            # the original research or present a retry as a new successful run.
            related = self._prepare_task(question.strip(), entry=previous['entry'], intent=DOCUMENT_CONTENT_INTENT,
                objects=previous['objects'], limits=previous['ledger']['limits'], parent=ident)
            # verified_material already reproduced the retained extraction. Do
            # not parse again after exposing a half-created child task.
            parsed = copy.deepcopy(material)
            parsed.pop('original_base64', None)
            parsed['obtained_at'] = now()
            adopted = material_record(related, parsed, original)
            adopted['source'] = record.get('source', '原研究留存原件')
            related['materials'] = [adopted]
            related['revisions'].append({'action': '补充原问题材料', 'hash': material['hash'], 'time': now(),
                'impact': '待核验，不自动改变已交付版本'})
            related['synthetic'] = previous['synthetic']
            related['messages'][0]['synthetic'] = related['synthetic']
            if 'owner_account' in previous:
                related['owner_account'] = previous['owner_account']
            related['document_review_origin'] = {'task_id': ident, 'research_version': previous['version'],
                'artifact_id': artifact_id, 'snapshot_hash': snapshot_hash, 'original_hash': material['hash'],
                'inherited_approval': False, 'parent_budget_reset': False}
            return self.archive.create_related(related, previous)

    def plan(self, task):
        return build_plan(task)

    def revise_plan(self, ident, questions, reason, expected_input_hash=None):
        """Revise obligations without changing the user's adopted scope or budget."""
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ContractError("计划修订须说明原因，最长2000字")
        with self.lock:
            task = self._get(ident)
            conversation_context(task)
            if task["status"] not in ("待开始", "失败", "已取消") or task.get("delivery") or task.get("pending_content"):
                raise ContractError("运行中或已冻结的研究不能改写计划，请停止后修订或建立关联研究")
            if ident in self.workers and self.workers[ident].is_alive():
                raise ContractError("旧批次仍在停止，不能修订计划")
            if expected_input_hash is not None and expected_input_hash != task["input_hash"]:
                raise ContractError("计划修订输入已过期，请重新读取")
            proposed = normalize_questions(questions, task["question"], task["intent"])
            prior = normalize_questions(question_specs(task), task["question"], task["intent"])
            validate_question_revision(task, proposed)
            if proposed == prior:
                return task
            old_hash = task["input_hash"]
            task["questions"] = [dict(q, status="待回答") for q in proposed]
            task["plan_version"] = task.get("plan_version", 1) + 1
            task["input_hash"] = input_fingerprint(task)
            task["nodes"] = self.plan(task)
            self._retain_thesis_sequence(task)
            task.update(theses=[], calculations=[], tracking=[], gaps=[], optional_findings=[])
            task["revisions"].append({"action": "计划修订", "reason": reason.strip(), "time": now(),
                "plan_version": task["plan_version"], "prior_input_hash": old_hash, "input_hash": task["input_hash"],
                "affected_nodes": [n["id"] for n in task["nodes"]], "budget_reset": False})
            return self.archive.save(task)

    def _get(self, ident):
        return self.archive.get(ident)

    def _adopt_method_knowledge(self, task, *, query=None):
        """Retrieve required-question context, retaining every already adopted version."""
        methods = [spec.get("method") for spec in task.get("calculation_specs", [])]
        # A declared obligation needs its definition and applicability limits
        # before inputs exist. Keyword ranking cannot replace that dependency.
        # Optional questions alone do not expand the adopted knowledge package;
        # their explicit calculation specs still follow the normal path above.
        methods.extend(method for question in task["questions"] if question["required"]
                       for method in question.get("method_needs", []))
        methods = list(dict.fromkeys(methods))
        from .knowledge import task_catalogue_revision, CATALOGUE_POLICY
        revision = task_catalogue_revision(task)
        adopted = self.knowledge.read(task["entry"], task["question"] if query is None else query, methods,
            context={"intent": task["intent"], "questions": question_specs(task), CATALOGUE_POLICY: revision})
        if not task.get("knowledge"):
            task["knowledge"] = adopted
        else:
            self.knowledge.validate_package(task["knowledge"], revision=revision)
            previous_hash = fingerprint(task["knowledge"])
            for item in adopted["entries"]:
                if item["id"] not in task["knowledge"]["index"]:
                    task["knowledge"]["entries"].append(item)
                    task["knowledge"]["index"][item["id"]] = item["version"]
            # Retrieval metadata describes the current questions, not a silent
            # replacement of definitions adopted by an earlier checkpoint.
            reasons = task["knowledge"].setdefault("selection_reasons", {})
            reasons.update(copy.deepcopy(adopted["selection_reasons"]))
            for key in ("query", "coverage", "unmatched", "unmatched_questions", "retrieval_context"):
                task["knowledge"][key] = copy.deepcopy(adopted[key])
            task["knowledge"]["excluded"] = [item for item in adopted["excluded"] if item["id"] not in task["knowledge"]["index"]]
            self.knowledge.reconcile_coverage(task["knowledge"])
            if previous_hash != fingerprint(task["knowledge"]):
                for node in task["nodes"]:
                    if node["id"] == "identity":
                        node["status"] = "待执行"
        self.knowledge.validate_package(task["knowledge"], revision=revision)
        for item in task.get("calculations", []):
            if item.get("method_binding") != self.knowledge.method_binding(task["knowledge"], item["method"]):
                raise ContractError("已有计算缺少锁定的方法版本，请主动更新，不静默迁移")

    @staticmethod
    def _validate_host_input(task, host_result):
        if not isinstance(host_result, dict):
            raise ContractError("宿主研究产物必须为结构化记录")
        if business_payload(host_result) != host_result:
            raise ContractError("宿主研究产物不能包含凭据或无关私密字段")
        host_result = copy.deepcopy(host_result)
        if task.get('draft_first_version') == 1 and 'draft_report' in host_result:
            # Per-request failures become draft workpaper notices. The host
            # does not have to satisfy the verified-analysis schema first.
            return host_result
        if 'evidence_specs' in host_result:
            host_result['evidence_specs'] = expand_api_evidence_groups(task, host_result['evidence_specs'])
            reading = retained_reading(task, host_result['reading_hash']) if 'reading_hash' in host_result else None
            host_result['evidence_specs'] = expand_financial_value_references(
                task, host_result['evidence_specs'], reading['views'] if reading else None)
            host_result['evidence_specs'] = expand_material_publication_references(
                task, host_result['evidence_specs'], reading['views'] if reading else None)
            host_result['evidence_specs'] = expand_material_context_references(
                task, host_result['evidence_specs'], reading['views'] if reading else None)
            host_result['evidence_specs'] = expand_api_cell_selections(task, host_result['evidence_specs'])
        for field in ("evidence", "evidence_specs", "calculations", "data_requests"):
            if field in host_result and not isinstance(host_result[field], list):
                raise ContractError(f"宿主研究产物的{field}须为数组，不能隐式转换")
        # Reject the complete request batch before changing run state, adopting
        # host revisions or consuming a supplement round. Credentials and
        # provider permissions remain separate execution-time checks.
        ResearchAgent._validated_data_needs(task, host_result.get("data_requests", []))
        validate_definition_actions(task['questions'], *(host_result.get(field, []) for field in
            ('data_requests', 'calculations', 'evidence_specs', 'evidence', 'theses')))
        analysis_output = {"answers": host_result.get("answers", {}), "theses": host_result.get("theses", [])}
        if judgment_review.enabled(task):
            host_result.setdefault('judgment_reviews', [])
            analysis_output['judgment_reviews'] = host_result['judgment_reviews']
            judgment_review.validate_shape(analysis_output['judgment_reviews'])
            known_reviews = {item['id'] for item in judgment_review.contract(task)['anchors']}
            if any(item['prior_thesis_id'] not in known_reviews for item in analysis_output['judgment_reviews']):
                raise ContractError('复核原判断不属于本任务冻结前版')
        elif host_result.get('judgment_reviews'):
            raise ContractError('本任务没有主动复核契约')
        validate_analysis_output(None, task, analysis_output,
            len(task["ledger"]["dispatches"]), actor="宿主")
        return host_result

    @staticmethod
    def _host_reading_scope(task):
        """Research meaning stays fixed while executable inputs are adopted."""
        return fingerprint(dict(
            scope={key: task.get(key) for key in ('id','version','question','entry','intent',
                'objects','cutoff','research_mode','conversation_context','requested_theses')},
            questions=question_specs(task)))

    @staticmethod
    def _adopt_host_reading(task, reading, prior_scope, submitted_specs):
        """Reproduce an already validated reading under the adopted plan.

        Host calculations/data requests change input_hash, not necessarily the
        material already read. Keep the old record immutable and create a new
        one only when scope, selected sources, text and coverage are identical.
        A stale or forged caller reading is rejected before this entry point.
        """
        if reading is None or reading['binding']==reading_binding(task):
            return
        if ResearchAgent._host_reading_scope(task)!=prior_scope:
            raise ContractError('研究问题或用途已改变，不能沿用旧阅读范围')
        replayed=read_sources(task,reading['selection'],reading_binding(task),reading['artifact_ids'],
                             reader_version=reading['reader_version'])
        excluded={'binding','reading_hash'}
        if ({key:value for key,value in replayed.items() if key not in excluded}
                != {key:value for key,value in reading.items() if key not in excluded}):
            raise ContractError('执行计划更新后原件、摘录或覆盖发生变化，须重新明确读取')
        validate_read_specs(task,submitted_specs,replayed)
        readings=task.setdefault('source_readings',[])
        existing=next((item for item in readings if item['reading_hash']==replayed['reading_hash']),None)
        if existing is not None and existing!=replayed:
            raise ContractError('本版阅读记录存在内容冲突，不能替换')
        if existing is None:
            if len(readings)>=24:
                raise ContractError('本版本已保存24份读取记录，不能追加执行计划阅读绑定')
            readings.append(replayed)
        task['host_result']['reading_hash']=replayed['reading_hash']
        task['revisions'].append(dict(action='执行计划采用后复核既有阅读',time=now(),
            previous_reading_hash=reading['reading_hash'],reading_hash=replayed['reading_hash'],
            reader_version=replayed['reader_version'],source_content_unchanged=True,
            automatic_source_selection=False,budget_reset=False))

    def start(self, ident, host_result=None, asynchronous=False, *, defer_delivery=False):
        with self.lock:
            if self.runtime_closed:
                raise ContractError('研究服务已停止，未派发任务；请重新打开App')
            task = self._get(ident)
            conversation_context(task)
            host_reading = None
            host_reading_specs = []
            reading_scope = self._host_reading_scope(task)
            if not isinstance(defer_delivery, bool):
                raise ContractError("宿主研究检查点选项必须为布尔值")
            if task["status"] not in ("待开始", "失败", "已取消"):
                raise ContractError("任务不能重复启动；已交付请主动更新")
            if ident in self.workers and self.workers[ident].is_alive():
                raise ContractError("旧批次仍在停止，请稍后恢复")
            if task.get("pending_content"):
                raise ContractError("仅保存失败，请重试保存，不重新研究")
            if (self.require_argument_support and not task.get('fixture')
                    and task['intent'] != DOCUMENT_CONTENT_INTENT
                    and not argument_support.enabled(task)):
                task['argument_support_contract'] = argument_support.VERSION
                task['theses'], task['tracking'] = [], []
                for question in task['questions']:
                    if question.get('answer_scope') != 'general_definition':
                        question['status'] = '待回答'
                        for field in ('answer','refs','calculation_refs','numeric_integrity','numeric_segments','research_coverage'):
                            question.pop(field, None)
                for node in task['nodes']:
                    if node.get('kind') in ('answer','thesis') or node['id'] in ('compose','render'):
                        node['status'] = '待执行'
            checkpoint = task.get("host_checkpoint")
            if checkpoint and host_result is None and checkpoint.get("source_digest") != fingerprint(original_sources(task)):
                raise ContractError("研究检查点后新增原件须显式登记补证，不能直接沿用旧交付依赖")
            if host_result is not None:
                host_result = self._validate_host_input(task, host_result)
            task["run_mode"] = "host" if host_result is not None or task.get("run_mode") == "host" else "model" if not task["fixture"] and self.model is not None and not self.offline else "offline"
            if defer_delivery and task["run_mode"] != "host":
                raise ContractError("仅显式宿主研究可在交付前暂停，不改变App自动交付")
            task["defer_delivery"] = defer_delivery
            task.pop("host_checkpoint", None)
            if host_result is not None:
                if task["intent"] == DOCUMENT_CONTENT_INTENT and any(host_result.get(key) for key in ("calculations", "theses", "data_requests")):
                    raise ContractError("原件内容核对不接受取数、金融计算或投资论点")
                host_result = copy.deepcopy(host_result)
                if "reading_hash" in host_result:
                    reading = retained_reading(task, host_result['reading_hash'])
                    validate_read_specs(task, host_result.get("evidence_specs", []), reading)
                    host_reading = reading
                    host_reading_specs = copy.deepcopy(host_result.get('evidence_specs',[]))
                proposal = host_result.pop("supplement", None)
                if proposal is not None:
                    if task["batch"] == 0 or self._active_supplement(task):
                        raise ContractError("补证须基于已有研究检查点，不能覆盖执行中补证轮")
                    if any(host_result.get(key, []) != proposal.get(key, []) for key in ("data_requests", "calculations") if key in host_result):
                        raise ContractError("宿主执行请求必须与已登记补证提案一致")
                    self._register_supplement(task, proposal, {"phase": "supplement", "source": "host"})
                    active = self._active_supplement(task)
                    if active:
                        active["evidence_specs"] = copy.deepcopy(host_result.get("evidence_specs", []))
                    merged = copy.deepcopy(task.get("host_result", {}))
                    merged.setdefault("answers", {}).update(host_result.get("answers", {}))
                    merged.setdefault("theses", []).extend(host_result.get("theses", []))
                    merged.setdefault("evidence_specs", []).extend(host_result.get("evidence_specs", []))
                    task["host_result"] = merged
                    host_result = merged
                previous_hash = fingerprint(task.get("host_result", {}))
                if previous_hash != fingerprint(host_result):
                    if task["batch"] > 0 and not (task.get('draft_first_version') == 1 and 'draft_report' in host_result):
                        supplement(task["ledger"])
                        task["revisions"].append({"action": "同问题补证", "round": task["ledger"]["supplements"], "time": now()})
                        task.setdefault("supplement_history", []).append({"round": task["ledger"]["supplements"], "status": "兼容宿主补证",
                            "gaps": copy.deepcopy(task["gaps"]), "expected_change": "宿主提交修订研究产物，按原问题重新核验",
                            "request_scope": {key: copy.deepcopy(host_result.get(key, [])) for key in ("data_requests", "calculations")},
                            "stop_reason": "旧宿主协议未声明影响范围，不能据此复用旧分支", "time": now()})
                    for node in task["nodes"]:
                        if node["id"] != "identity":
                            node.update(status="待执行", input_hash=task["input_hash"])
                task["host_result"] = copy.deepcopy(host_result)
            host_result = task.get("host_result")
            # Upgrade an unfinished old checkpoint in place; frozen reports are never migrated.
            specs = normalize_questions(question_specs(task), task["question"], task["intent"])
            prior_questions = {q["id"]: q for q in task["questions"]}
            task["questions"] = [dict(prior_questions[q["id"]], **q) for q in specs]
            draft_submission = task.get('draft_first_version') == 1 and isinstance(host_result, dict) and 'draft_report' in host_result
            if draft_submission:
                # Acquisition batches have their own request fingerprints.
                # They do not change the research question and must not expire
                # the name/code mappings obtained by the previous host stage.
                pass
            elif not task.get("fixture") and (task["run_mode"] != "model" or not task.get("reasoning_state", {}).get("plan")):
                task["calculation_specs"] = copy.deepcopy((host_result or {}).get("calculations", []))
                # Mechanism/investment questions already own thesis nodes.
                # This flag only adds an optional thesis to an all-direct plan;
                # toggling a redundant flag would expire unchanged readings.
                if all(question["kind"] in DIRECT_KINDS for question in task["questions"]):
                    task["requested_theses"] = bool((host_result or {}).get("theses"))
                task["data_need_specs"] = copy.deepcopy((host_result or {}).get("data_requests", []))
            elif "calculation_specs" not in task:
                case = get_case(task["fixture"])
                task["calculation_specs"] = [case["calculation"]] if case.get("calculation") else []
                task["requested_theses"] = True
            task["input_hash"] = input_fingerprint(task)
            self._adopt_method_knowledge(task)
            self._adopt_host_reading(task, host_reading, reading_scope, host_reading_specs)
            host_result = task.get("host_result")
            planned = self.plan(task)
            existing = {n["id"]: n for n in task["nodes"]}
            task["nodes"] = [existing[n["id"]] if n["id"] in existing and existing[n["id"]]["input_hash"] == task["input_hash"]
                             and existing[n["id"]]["dependencies"] == n["dependencies"] else n for n in planned]
            task["status"], task["error"] = "运行中", None
            task["batch"] += 1
            task["revisions"].append({"action": "启动" if task["batch"] == 1 else "恢复", "batch": task["batch"], "time": now()})
            self.archive.save(task)
            batch = task["batch"]
            self._begin_run_control(ident, task["version"], batch)
            if asynchronous:
                worker = threading.Thread(target=self._run, args=(ident, batch, host_result, task["version"]), daemon=True)
                self.workers[ident] = worker
                try:
                    worker.start()
                except Exception:
                    self._finish_run_control(ident, task["version"], batch)
                    self.workers.pop(ident, None)
                    task.update(status="失败", error="无法启动研究执行，检查点与用量已保留")
                    self.archive.save(task)
                    raise ContractError(task["error"]) from None
                return task
        return self._run(ident, batch, host_result, task["version"])

    def cancel(self, ident):
        with self.lock:
            task = self._get(ident)
            if task["status"] != "运行中":
                raise ContractError("只有运行中的任务可以取消")
            task["status"] = "已取消"
            task["revisions"].append({"action": "取消", "batch": task["batch"], "time": now()})
            saved = self.archive.save(task)
            self._finish_run_control(ident, task["version"], task["batch"])
            return saved

    def _active(self, ident, batch):
        if self.runtime_closed:
            raise ContractError('研究服务已停止，检查点与累计用量保留')
        task = self._get(ident)
        conversation_context(task)
        if task["status"] != "运行中" or task["batch"] != batch:
            raise ContractError("任务已取消或执行批次过期")
        return task

    def _run(self, ident, batch, host_result, version=None):
        started = time.monotonic()
        accounted_start = 0
        verification_token = None
        try:
            initial = self._get(ident)
            version = initial["version"] if version is None else version
            if initial["version"] != version or initial["batch"] != batch:
                raise ContractError("请求所属研究版本或执行批次已过期")
            accounted_start = initial["ledger"]["used"]["seconds"]
            if initial.get('draft_first_version') == 1 and (
                    initial.get('run_mode') == 'model' or isinstance(host_result, dict) and 'draft_report' in host_result):
                from .draft_workflow import run
                return run(self, ident, batch, host_result)
            from .material_parser import begin_verification
            verification_token = begin_verification(initial, self._check_run_control,
                initial["ledger"]["limits"]["seconds"] - accounted_start)
            if self._get(ident).get("run_mode") == "model":
                self._prepare_model_plan(ident, batch)
                # ModelService already settles the preparation's network time.
                spent = self._get(ident)["ledger"]["used"]["seconds"] - accounted_start
                with self.lock:
                    prepared = self._active(ident, batch)
                    charge(prepared["ledger"], "seconds", max(0, time.monotonic() - started - spent))
                    self.archive.save(prepared)
                started = time.monotonic()
            index = 0
            while index < len(self._get(ident)["nodes"]):
                step = self._get(ident)["nodes"][index]
                index += 1
                if step["id"] == "compose":
                    decision_started = time.monotonic()
                    decision_used = self._get(ident)["ledger"]["used"]["seconds"]
                    changed = self._maybe_supplement(ident, batch)
                    with self.lock:
                        decided = self._active(ident, batch)
                        settled = max(0, decided["ledger"]["used"]["seconds"] - decision_used)
                        charge(decided["ledger"], "seconds", max(0, time.monotonic() - decision_started - settled), delivery=not changed)
                        self.archive.save(decided)
                        accounted_start = decided["ledger"]["used"]["seconds"]
                    started = time.monotonic()
                    if changed:
                        index = 0
                        continue
                if step["id"] == "compose" and self._get(ident).get("defer_delivery"):
                    with self.lock:
                        checkpoint = self._active(ident, batch)
                        checkpoint["status"] = "待开始"
                        checkpoint["host_checkpoint"] = {"stage": "待交付或定向补证", "input_hash": checkpoint["input_hash"],
                                                         "round": checkpoint["ledger"]["supplements"], "time": now(),
                                                         "source_digest": fingerprint(original_sources(checkpoint))}
                        charge(checkpoint["ledger"], "seconds", max(0, time.monotonic() - started), delivery=True)
                        self.archive.save(checkpoint)
                        return checkpoint
                with self.lock:
                    task = self._active(ident, batch)
                    self.knowledge.validate_package(task["knowledge"])
                    node = next(n for n in task["nodes"] if n["id"] == step["id"])
                    if not self._binding_checkpoint_needs_upgrade(task,node) and node["status"] in ("成功", "部分成功") and node["input_hash"] == task["input_hash"] and \
                            node.get("result", {}).get("dependencies") == dependency_versions(task, node["id"]):
                        continue
                    if not self._active_supplement(task):
                        # Re-executing a node can clear shared outputs even
                        # when its eventual artifact hash is unchanged. Its
                        # dependent nodes must rebuild those outputs instead
                        # of trusting an old result with an identical hash.
                        # Scoped supplements have their own preserved-branch
                        # contract and invalidate only registered branches.
                        affected = {node["id"]}
                        while True:
                            descendants = {n["id"] for n in task["nodes"]
                                           if affected.intersection(n["dependencies"])}
                            if descendants.issubset(affected):
                                break
                            affected.update(descendants)
                        for dependent in task["nodes"]:
                            if dependent["id"] in affected and dependent["id"] != node["id"]:
                                dependent["status"] = "待执行"
                    charge(task["ledger"], "seconds", .001, delivery=node["id"] in ("compose", "render"))
                    accounted_start = task["ledger"]["used"]["seconds"]
                    node["status"] = "运行中"
                    node["request"] = request(task, node["operation"], node_id=node["id"])
                    validate_request(node["request"], task)
                    self.archive.save(task)
                # The deterministic operations have no unbounded provider calls.
                # Host inference is submitted explicitly; offline never contacts a model.
                outcome = self._step(copy.deepcopy(task), node["id"], host_result)
                with self.lock:
                    fresh = self._active(ident, batch)
                    if fresh["input_hash"] != task["input_hash"] or fingerprint(fresh["materials"]) != fingerprint(task["materials"]):
                        raise ContractError("输入已变化，迟到结果不能推进当前版本")
                    outcome["revision"] = fresh["revision"]
                    outcome["revisions"] = fresh["revisions"]
                    outcome["materials"] = fresh["materials"]
                    if fresh.get("pending_materials"):
                        outcome["pending_materials"] = fresh["pending_materials"]
                    result_node = next(n for n in outcome["nodes"] if n["id"] == node["id"])
                    artifacts = self._artifact_manifest(outcome, node["id"])
                    incomplete_calculation = (node["id"] == "calculation" and
                                              bool(self._missing_calculations(outcome, node["questions"])))
                    state = "部分成功" if incomplete_calculation or node["id"] == "evidence" and outcome["gaps"] or any(
                        q["id"] in node["questions"] and q["required"] and q["status"] == "未回答"
                        for q in outcome["questions"]) else "成功"
                    result_node["result"] = module_result(outcome, node["operation"], artifacts, state, node_id=node["id"])
                    validate_result(result_node["result"], outcome)
                    result_node.update(status=state, output_hash=fingerprint(artifacts))
                    self._reuse_unaffected_nodes(outcome)
                    already_settled = max(0, outcome["ledger"]["used"]["seconds"] - accounted_start)
                    elapsed = max(0, time.monotonic() - started - already_settled)
                    charge(outcome["ledger"], "seconds", elapsed, delivery=node["id"] in ("compose", "render"))
                    started = time.monotonic()
                    if node["id"] == "render":
                        content, html = outcome["pending_content"], outcome["pending_html"]
                        self.archive.save(outcome)  # durable save retry checkpoint before final transaction
                        result = self.archive.deliver(outcome, content, html)
                        return result
                    self.archive.save(outcome)
            return self._get(ident)
        except Exception as error:
            with self.lock:
                task = self._get(ident)
                if task["version"] == version and task["batch"] == batch and task["status"] in ("运行中", "已取消"):
                    # A failed or cancelled in-flight operation still consumed time.
                    # Do not refund it by only retaining the previous checkpoint.
                    already_settled = max(0, task["ledger"]["used"]["seconds"] - accounted_start)
                    task["ledger"]["used"]["seconds"] += max(0, time.monotonic() - started - already_settled)
                if task["version"] == version and task["status"] == "运行中" and task["batch"] == batch:
                    task["status"], task["error"] = "失败", str(error) if isinstance(error, ContractError) else "执行或保存失败，请检查本地档案"
                    for node in task["nodes"]:
                        if node["status"] == "运行中":
                            node.update(status="失败", error=task["error"])
                if task["version"] == version and task["batch"] == batch and task["status"] in ("失败", "已取消"):
                    try:
                        self.archive.save(task)
                    except Exception:
                        pass  # retain last durable checkpoint; startup recovery marks interruption
                return task
        finally:
            if verification_token is not None:
                from .material_parser import end_verification
                end_verification(verification_token)
            self._finish_run_control(ident, version, batch)

    def _dependency_state(self, task):
        return {key: task[key] for key in ("input_hash", "evidence", "calculations", "theses", "gaps")}

    @staticmethod
    def _active_supplement(task):
        return next((item for item in reversed(task.get("supplement_history", []))
                     if item.get("status") == "执行中"), None)

    @staticmethod
    def _question_closure(task, selected):
        affected = set(selected)
        while True:
            added = {q["id"] for q in task["questions"] if affected.intersection(q.get("dependencies", []))}
            if added.issubset(affected):
                return affected
            affected.update(added)

    @staticmethod
    def _branch_basis(task, node):
        """Shared evidence nodes do not make unrelated question facts dependent."""
        from .evidence import evidence_binding
        selected = set(node["questions"])
        return fingerprint({"questions": [q for q in task["questions"] if q["id"] in selected],
            "evidence": [{"binding": evidence_binding(e), "version": e["content_version"]} for e in task["evidence"] if selected.intersection(e.get("question_ids", []))],
            "calculations": [c for c in task["calculations"] if selected.intersection(c.get("question_ids", []))],
            "theses": [t for t in task["theses"] if selected.intersection(t.get("question_ids", []))]})

    def _reuse_unaffected_nodes(self, task):
        active = self._active_supplement(task)
        if not active:
            return
        for node in task["nodes"]:
            preserved = active.get("preserved_nodes", {}).get(node["id"])
            if not preserved or node["status"] not in ("成功", "部分成功"):
                continue
            if self._branch_basis(task, node) != preserved["basis"]:
                raise ContractError("补证触及未登记的独立分支，拒绝复用旧节点")
            node["input_hash"] = task["input_hash"]
            node["result"] = module_result(task, node["operation"], node["result"]["artifacts"], node["status"], node_id=node["id"])
            node["reuse"] = {"supplement_round": active["round"], "prior_input_hash": preserved["input_hash"],
                             "prior_output_hash": preserved["output_hash"], "prior_basis": preserved["basis"],
                             "current_basis": self._branch_basis(task, node), "reason": "本分支原值与产物未变化，仅更新共享节点依赖登记"}

    def _register_supplement(self, task, proposed, receipt=None):
        proposed = validate_supplement(task, proposed)
        if proposed["stop_reason"]:
            self._stop_supplement(task, proposed["stop_reason"], proposed)
            return False
        supplement(task["ledger"])
        affected = self._question_closure(task, proposed["question_ids"])
        for item in task["evidence"]:
            validate_evidence(item, task)
        preserved = {n["id"]: {"basis": self._branch_basis(task, n), "input_hash": task["input_hash"], "output_hash": n["output_hash"]}
                     for n in task["nodes"] if n.get("kind") in ("answer", "thesis")
                     and not affected.intersection(n["questions"]) and n["status"] in ("成功", "部分成功")}
        record = {"round": task["ledger"]["supplements"], "status": "执行中", "proposal": proposed,
                  "receipt": receipt, "gaps": copy.deepcopy(task["gaps"]), "expected_change": proposed["expected_change"],
                  "request_scope": {key: copy.deepcopy(proposed[key]) for key in ("data_requests", "calculations", "artifact_ids")},
                  "affected_questions": sorted(affected), "preserved_nodes": preserved,
                  "before": {"evidence": [e["id"] for e in task["evidence"]], "calculations": [c["id"] for c in task["calculations"]],
                             "answered": [q["id"] for q in task["questions"] if q["status"] == "已回答"],
                             "filing_candidates": self._supplement_filing_candidates(task, set(proposed["question_ids"]))},
                  "source_digest": fingerprint(original_sources(task)), "started_at": now(), "stop_reason": ""}
        task.setdefault("supplement_history", []).append(record)
        self._retain_thesis_sequence(task)
        task["theses"] = [t for t in task["theses"] if not affected.intersection(t.get("question_ids", []))]
        retained_theses = {t["id"] for t in task["theses"]}
        task["tracking"] = [t for t in task["tracking"] if t.get("thesis_id") in retained_theses]
        for question in task["questions"]:
            if question["id"] in affected:
                for key in ("answer", "refs", "calculation_refs", "numeric_integrity", "numeric_segments"):
                    question.pop(key, None)
                question["status"] = "待回答"
        old_hash = task["input_hash"]
        task["calculation_specs"].extend(copy.deepcopy(proposed["calculations"]))
        task["data_need_specs"].extend(copy.deepcopy(proposed["data_requests"]))
        host = task.setdefault("host_result", {})
        host["answers"] = {key: value for key, value in host.get("answers", {}).items() if key not in affected}
        host["theses"] = [t for t in host.get("theses", []) if not affected.intersection(t.get("question_ids", []))]
        host["calculations"] = copy.deepcopy(task["calculation_specs"])
        host["data_requests"] = copy.deepcopy(task["data_need_specs"])
        task["input_hash"] = input_fingerprint(task)
        # Changed plans require fresh provenance receipts, not a copied approval
        # with a rewritten hash. Re-extract and reassess the retained originals;
        # the substantive fact binding must remain byte-for-byte identical.
        from .evidence import evidence_binding
        records = original_sources(task)
        for index, original in enumerate(task["evidence"]):
            if original.get("source") == "合成验收原件":
                continue
            artifact = next(r for r in records if r["id"] == original["artifact"]["id"])
            checked = candidate_evidence(task, artifact, original)
            checked = self.analyzer.assess(task, checked, original["applicability_review"], records)
            checked["content_version"] = original["content_version"]
            if evidence_binding(checked) != evidence_binding(original):
                raise ContractError("补证复核改变原值依赖，不能静默替换已验证产物")
            validate_evidence(checked, task)
            task["evidence"][index] = checked
        self._adopt_method_knowledge(task)
        old_nodes = {n["id"]: n for n in task["nodes"]}
        planned = self.plan(task)
        rerun = {"compose", "render"}
        if proposed["data_requests"] or proposed["artifact_ids"]:
            rerun.add("evidence")
        if proposed["calculations"] or ("evidence" in rerun and task["calculation_specs"]):
            rerun.add("calculation")
        rerun.update(n["id"] for n in planned if n.get("kind") in ("answer", "thesis") and affected.intersection(n["questions"]))
        task["nodes"] = [n if n["id"] in rerun or n["id"] not in old_nodes else old_nodes[n["id"]] for n in planned]
        for node in task["nodes"]:
            node["input_hash"] = task["input_hash"]
            if node["id"] in ("identity", "evidence") and node["id"] not in rerun and node.get("result"):
                # Knowledge adoption and deterministic source re-verification
                # above already refreshed these shared dependencies. They are
                # not a reason to perform another global extraction pass.
                prior_output = node["output_hash"]
                node["status"] = node["result"]["status"]
                artifacts = self._artifact_manifest(task, node["id"])
                node["result"] = module_result(task, node["operation"], artifacts, node["status"], node_id=node["id"])
                node["output_hash"] = fingerprint(artifacts)
                node["dependency_refresh"] = {"supplement_round": record["round"], "prior_output_hash": prior_output,
                                              "reason": "登记知识与原件确定性复核完成，未重新请求或提取模型"}
        record["affected_nodes"] = [n["id"] for n in task["nodes"] if n["id"] in rerun]
        task.setdefault("reasoning_state", {}).get("plan", {})["adopted_input_hash"] = task["input_hash"]
        task["revisions"].append({"action": "定向补证", "round": record["round"], "time": now(),
            "prior_input_hash": old_hash, "input_hash": task["input_hash"], "affected_nodes": record["affected_nodes"], "budget_reset": False})
        self._reuse_unaffected_nodes(task)
        return True

    def _stop_supplement(self, task, reason, proposed=None):
        task["supplement_stop_reason"] = reason
        if task.get("supplement_history") and not task["supplement_history"][-1].get("stop_reason"):
            task["supplement_history"][-1]["stop_reason"] = reason
        task.setdefault("supplement_decisions", []).append({"status": "停止", "stop_reason": reason,
            "proposal": copy.deepcopy(proposed), "gaps": copy.deepcopy(task["gaps"]), "time": now()})
        unresolved = [q['id'] for q in task['questions'] if q['required'] and q['status']!='已回答']
        if unresolved:
            self._gap(task, '定向补证停止：' + reason, unresolved, category='supplement_stop')

    def _supplement_filing_candidates(self, task, selected):
        """Owned, verified download selections, not admitted research evidence."""
        candidates = []
        if self.offline:
            return candidates
        eligible_records = {}
        for record in task.get("source_records", []):
            if (record.get("kind") != "api" or record.get("operation") != "report_query"
                    or record.get("task_id") != task["id"] or record.get("research_version") != task["version"]):
                continue
            eligible = sorted(set(selected).intersection(record.get("question_ids", [])))
            if not eligible or supplement_request_blocked(task, "ifind", eligible):
                continue
            eligible_records[record['id']] = eligible
        index = filing_candidate_choices(task, eligible_records, exclude_attempted=True)
        candidates = []
        for detail in index['details']:
            params = detail['parameters']
            # Union only explicitly registered, currently eligible aliases.
            # This offers question choices; it does not approve their evidence.
            questions = set(eligible_records[params['catalog_id']])
            for alias in detail['catalog_aliases']:
                questions.update(eligible_records[alias['catalog_id']])
            candidates.append(dict(params,question_ids=sorted(questions)))
        return candidates

    def _supplement_options(self, task):
        """Expose existing executable capabilities, never invent provider tools."""
        missing = [q for q in task["questions"] if q["required"] and q["status"] != "已回答"]
        selected = {q["id"] for q in missing}
        attempted = {}
        for spec in task.get("host_result", {}).get("evidence_specs", []) + task["rejected_evidence"]:
            attempted.setdefault(spec.get("artifact_id"), set()).update(spec.get("question_ids", []))
        for entry in task.get("supplement_history", []):
            proposal = entry.get("proposal", {})
            for ident in proposal.get("artifact_ids", []):
                attempted.setdefault(ident, set()).update(proposal.get("question_ids", []))
        # A generic failure alone does not justify spending. Separately expose
        # a bounded reread only when an existing original provides an actual
        # alternative anchor or a source-checked question-binding discrepancy.
        partial_reading = any(item.get("omitted_characters", 0) for item in task.get("reasoning_state", {}).get("reading", {}).get("bundle", {}).get("coverage", []))
        artifacts = [r["id"] for r in extractable_source_views(task) if selected - attempted.get(r["id"], set()) and r.get("status") in ("已解析", "成功待核验")] if task["evidence"] or partial_reading else []
        extraction_repairs = self._supplement_extraction_repairs(task)
        artifacts = list(dict.fromkeys(artifacts + [item["artifact_id"] for item in extraction_repairs]))
        methods = []
        for question in missing:
            prior = {s["method"] for s in task["calculation_specs"] if question["id"] in s.get("question_ids", [])}
            for method in question.get("method_needs", []):
                from .contracts import calculation_input_applies
                available = [e['unit'] for e in task['evidence']
                    if e.get('value') is not None and calculation_input_applies(task,e,question['id'],method)]
                from .contracts import calculation_input_units_available
                if method not in prior and calculation_input_units_available(method, available):
                    methods.append(method)
        codes = available_security_codes(task)
        # Unresolved registered calculations also own source obligations.
        # Restricting this to price returns made a financial-only question
        # stop before collecting its missing statement original. Eligibility
        # exposes choices; it neither dispatches a request nor approves data.
        source_need_questions = set()
        for question in missing:
            completed_methods = {calculation["method"] for calculation in task["calculations"]
                if question["id"] in calculation.get("question_ids", [])}
            missing_input_method = any(method in METHOD_REGISTRY
                and METHOD_REGISTRY[method]["input_units"] and method not in completed_methods
                for method in question.get("method_needs", []))
            if question.get("evidence_needs") or missing_input_method or any(
                    question["id"] in need.get("question_ids", []) for need in task["data_need_specs"]):
                source_need_questions.add(question["id"])
        sources, source_questions = [], {}
        data_available = task["ledger"]["limits"]["data_requests"] - task["ledger"]["used"]["data_requests"] >= 1
        from .economic_sources import available_economic_indicators
        if data_available and not self.offline and task.get("intent") != DOCUMENT_CONTENT_INTENT and (codes or available_economic_indicators(task)) and source_need_questions:
            for source, keys in (("ifind", ("access_token", "refresh_token")), ("tinyshare", ("token",))):
                eligible = [q for q in sorted(source_need_questions) if not supplement_request_blocked(task, source, [q])]
                if eligible and (codes or source == "ifind") and any(self.source_credentials.get(source, {}).get(key) for key in keys):
                    sources.append(source)
                    source_questions[source] = eligible
        # Suppress unavailable actions without erasing retained directory
        # candidates used to account for the preceding round's progress.
        filing_candidates = self._supplement_filing_candidates(task, selected) if data_available else []
        return {"artifact_ids": artifacts, "methods": list(dict.fromkeys(methods)), "sources": sources,
                "source_question_ids": source_questions, "filing_candidates": filing_candidates,
                "extraction_repairs": extraction_repairs}

    def _supplement_extraction_repairs(self, task):
        """Read-only eligibility for one purposeful reread, not evidence repair.

        Transport failures, missing metadata and invented numbers are not
        executable increments. A proposal still consumes the usual supplement
        round and must be regenerated and independently verified afterwards.
        """
        selected = {q["id"] for q in task["questions"] if q["required"] and q["status"] != "已回答"}
        if not selected or not task.get("rejected_evidence"):
            return []
        reread = supplement_reread_pairs(task)
        records = {record["id"]: record for record in original_sources(task)
                   if record.get("task_id") == task["id"] and record.get("research_version") == task["version"]
                   and record.get("kind") in ("material", "filing")}
        proposals = {item.get("id"): item for item in task.get("host_result", {}).get("evidence_specs", [])
                     if isinstance(item, dict)}
        try:
            views = extractable_source_views(task)
            source_context = context_sources(task, [view["id"] for view in views])
            if source_context["mode"] == "selected":
                bundle = task.get("reasoning_state", {}).get("reading", {}).get("bundle")
                if not bundle:
                    return []
                validate_reading(task, bundle)
                views = bundle["views"]
            contract = evidence_contract(task, views)
        except ContractError:
            return []
        sources = {source["artifact_id"]: source for source in contract["sources"]}
        result, seen = [], set()
        from .evidence import validate_publication_basis
        for rejected in task["rejected_evidence"]:
            spec = proposals.get(rejected.get("id"))
            artifact = records.get(rejected.get("artifact_id"))
            if not spec or not artifact or spec.get("artifact_id") != artifact["id"] or artifact["id"] not in sources:
                continue
            review = spec.get("review", {})
            questions = selected.intersection(review.get("question_ids", []))
            questions = {question for question in questions if (artifact["id"], question) not in reread}
            if not questions or (artifact["id"], tuple(sorted(questions))) in seen:
                continue
            reason = rejected.get("rejection", "")
            eligible = (rejected.get("source_check") == "通过"
                        and reason == "材料适用性必须绑定本任务的实际研究问题"
                        and set(spec.get("question_ids", [])) != set(review.get("question_ids", [])))
            if eligible and task.get("intent") != DOCUMENT_CONTENT_INTENT:
                try:
                    if timepoint(spec.get("published_at")) > timepoint(task["cutoff"]):
                        continue
                    validate_publication_basis(spec, [artifact])
                except ContractError:
                    continue
            publication_locator_failure = (
                reason.startswith("公开时间未对应所引发布语句")
                or reason == "原文未明确定位发布或披露日期，所属期与生效日不能替代")
            if not eligible and publication_locator_failure:
                stamp = spec.get("published_at")
                try:
                    if not isinstance(stamp, str) or timepoint(stamp) > timepoint(task["cutoff"]):
                        continue
                    try:
                        validate_publication_basis(spec, [artifact])
                    except ContractError as error:
                        # Reproduce the locator failure against this exact
                        # proposal. A stale diagnostic must not turn another
                        # integrity or publication error into a spending path.
                        current_reason = str(error)
                        if not (current_reason.startswith("公开时间未对应所引发布语句")
                                or current_reason == "原文未明确定位发布或披露日期，所属期与生效日不能替代"):
                            continue
                    else:
                        continue  # A stale rejection is not a new reread action.
                    for candidate in sources[artifact["id"]]["publication_candidates"]:
                        probe = {"artifact_id": artifact["id"], "published_at": stamp,
                                 "publication": {"basis_ref": candidate["basis_ref"]}}
                        try:
                            validate_publication_basis(probe, [artifact])
                        except ContractError:
                            continue
                        eligible = True
                        break
                except ContractError:
                    continue
            if not eligible:
                continue
            seen.add((artifact["id"], tuple(sorted(questions))))
            result.append({"id": rejected["id"], "artifact_id": artifact["id"],
                "question_ids": sorted(questions), "rejection": reason,
                "purpose": "针对来源定位或问题绑定重读现有可读原件，不能重试取数或预设核验通过",
                "source_approved": False, "automatic_reference_repair": False})
            if len(result) >= 12:
                break
        return result

    def _supplement_context_constraints(self, task):
        """Diagnostic metadata is not an executable action or reason to spend."""
        selected = {q["id"] for q in task["questions"] if q["required"] and q["status"] != "已回答"}
        return [dict(api_calculation_context_contract(record),
                     question_ids=sorted(selected.intersection(record.get("question_ids", []))))
                for record in task.get("source_records", []) if record.get("kind") == "api"
                and selected.intersection(record.get("question_ids", []))][:24]

    def _maybe_supplement(self, ident, batch):
        task = self._active(ident, batch)
        active = self._active_supplement(task)
        if active:
            before = active["before"]
            self._resolve_current_gaps(task)
            active["results"] = {"evidence_added": [e["id"] for e in task["evidence"] if e["id"] not in before["evidence"]],
                "calculations_added": [c["id"] for c in task["calculations"] if c["id"] not in before["calculations"]],
                "questions_answered": [q["id"] for q in task["questions"] if q["status"] == "已回答" and q["id"] not in before["answered"]],
                "filing_candidates_added": [candidate for candidate in self._supplement_filing_candidates(task, set(active["proposal"]["question_ids"]))
                    if "filing_candidates" in before and candidate not in before["filing_candidates"]]}
            active.update(status="已完成", completed_at=now())
            if not any(active["results"].values()):
                active["stop_reason"] = "本轮未增加可用证据、有效计算、核心回答或可下载原件候选，不重复同一动作"
                self._stop_supplement(task, active["stop_reason"])
            elif all(q["status"] == "已回答" for q in task["questions"] if q["required"]):
                active["stop_reason"] = "核心问题已解决，不再补证"
            self._save_model_checkpoint(task)
        missing = [q for q in task["questions"] if q["required"] and q["status"] != "已回答"]
        if not missing or task.get("supplement_stop_reason") or task.get("run_mode") != "model":
            return False
        options = self._supplement_options(task)
        available_calls = task["ledger"]["limits"]["model_calls"] - task["ledger"]["used"]["model_calls"] - task["ledger"]["reserve"]["model_calls"]
        minimum_calls = 2 if options["methods"] else 3
        reason = ""
        if task["ledger"]["supplements"] >= 2:
            reason = "已达到同一版本两轮补证上限"
        elif task.get("supplement_decision", {}).get("status") == "已派发":
            reason = "上次补证决策未形成可复用检查点，不重复派发不确定的规划请求"
        elif not any(options[key] for key in ("artifact_ids", "methods", "sources", "filing_candidates")):
            reason = "缺口没有已登记且可执行的增量动作；身份、权限、原件或工具缺口需保留"
        elif not self._reasoning_available(task) or available_calls < minimum_calls:
            reason = "研究额度不足以完成完整补证轮，保留交付预留"
        if reason:
            self._stop_supplement(task, reason)
            self._save_model_checkpoint(task)
            return False
        source_context = context_sources(task)
        payload = {"question": task["question"], "objects": task["objects"], "cutoff": task["cutoff"],
            "questions": task["questions"], "gaps": task["gaps"], **self._reasoning_evidence_payload(task),
            "calculations": task["calculations"], "calculation_contract": calculation_plan_contract(task),
            "filing_operations": FILING_REQUEST_REGISTRY,
            "data_request_contract": data_request_contract(task),
            "untrusted_sources": source_context["views"], "source_catalog": source_context["catalog"], "executable_actions": options,
            "api_context_constraints": self._supplement_context_constraints(task),
            "extraction_repair_contract": supplement_reread_contract(task),
            "budget": task["ledger"], "previous_rounds": task.get("supplement_history", [])}
        task["supplement_decision"] = {"status": "已派发", "input_digest": fingerprint(payload), "time": now()}
        self._save_model_checkpoint(task)
        try:
            proposed, receipt = model_object(self.model, task, "supplement", payload, SUPPLEMENT_FIELDS)
            validate_supplement(task, proposed)
            required_calls = 2 if proposed["artifact_ids"] or proposed["data_requests"] else 1
            if proposed["artifact_ids"] and source_context["mode"] == "selected":
                required_calls += 1  # selected reading, extraction and interpretation
            remaining_calls = task["ledger"]["limits"]["model_calls"] - task["ledger"]["used"]["model_calls"] - task["ledger"]["reserve"]["model_calls"]
            if not proposed["stop_reason"] and (remaining_calls < required_calls or not self._reasoning_available(task)):
                raise ContractError("提案所需提取与解释额度不足，保留交付预留，不派发补证")
            # Validate before mutating history/specs, or deduplication would reject
            # the proposal against itself. Persist the charged round before tools.
            candidate = copy.deepcopy(task)
            changed = self._register_supplement(candidate, proposed, receipt)
            candidate["supplement_decision"].update(status="已校验", receipt=receipt)
            self._save_model_checkpoint(candidate)
            return changed
        except ContractError as error:
            self._active(ident, batch)
            self._stop_supplement(task, "补证提案未通过：" + str(error))
            self._save_model_checkpoint(task)
            return False

    def _artifact_manifest(self, task, node):
        planned = next(n for n in task["nodes"] if n["id"] == node)
        kind = planned.get("kind", node)
        fields = {"identity": ("identity_state", "knowledge"), "evidence": ("evidence", "rejected_evidence", "gaps"),
                  "calculation": ("calculations", "gaps"), "thesis": ("questions", "theses", "tracking", "gaps"),
                  "answer": ("questions", "gaps", "optional_findings"),
                  "compose": ("pending_content",), "render": ("pending_html",)}[kind]
        values = {key: task.get(key) for key in fields}
        if kind in ("answer", "thesis"):
            selected = set(planned["questions"])
            values["questions"] = [q for q in task["questions"] if q["id"] in selected]
            values["gaps"] = [g for g in task.get("gap_records", []) if selected.intersection(g["question_ids"])]
            if kind == "answer":
                values["optional_findings"] = [g for g in values["gaps"] if not g["required"]]
            else:
                values["theses"] = [t for t in task["theses"] if selected.intersection(t.get("question_ids", []))]
                ids = {t["id"] for t in values["theses"]}
                values["tracking"] = [t for t in task["tracking"] if t.get("thesis_id") in ids]
        return {key: {"digest": fingerprint(values[key]), "task_id": task["id"], "research_version": task["version"]} for key in fields}

    def _step(self, task, node, host_result):
        fixture = get_case(task["fixture"]) if task["fixture"] else {}
        host_result = task.get("host_result") if task.get("run_mode") == "model" else host_result if host_result is not None else task.get("host_result")
        planned = next(n for n in task["nodes"] if n["id"] == node)
        kind = planned.get("kind", node)
        active = self._active_supplement(task)
        selected = [q for q in planned["questions"] if not active or q in active["affected_questions"]]
        if node == "identity":
            self.knowledge.validate_package(task["knowledge"])
            task["identity_state"] = "合成身份已核验" if fixture else "待证据消歧；不按标的名称推定业务暴露"
        elif node == "evidence":
            candidates = fixture.get("evidence", []) if fixture else (host_result or {}).get("evidence", [])
            if not active:
                task["evidence"], task["rejected_evidence"], task["gaps"] = [], [], []
                self._retain_thesis_sequence(task)
                task.update(theses=[], calculations=[], tracking=[], optional_findings=[], gap_records=[])
                for q in task["questions"]:
                    for field in ("answer", "refs", "calculation_refs", "numeric_integrity", "numeric_segments"):
                        q.pop(field, None)
                    q["status"] = "待回答"
            else:
                candidates = []
            needs = active["proposal"]["data_requests"] if active else (host_result or {}).get("data_requests", []) if not fixture else []
            self._acquire_sources(task, needs)
            if task.get("run_mode") == "model":
                if not active:
                    from .acquisition import complete_initial_acquisition
                    complete_initial_acquisition(self, task)
                host_result = self._extract_model_sources(task)
            records = task.get("source_records", []) + [x for x in task["materials"] if x.get("kind") == "material"]
            specifications = active.get("evidence_specs", []) if active else (host_result or {}).get("evidence_specs", []) if not fixture else []
            if not isinstance(specifications, list) or len(specifications) > 1000:
                raise ContractError("取证规格必须为列表，最多1000项")
            def adopt(original):
                item = copy.deepcopy(original)
                if item.get("task_id", task["id"]) != task["id"] or item.get("research_version", task["version"]) != task["version"]:
                    raise ContractError("取证结果不能引用其他任务或研究版本")
                item.update(task_id=task["id"], research_version=task["version"], content_version=1)
                try:
                    if any(prior["id"] == item["id"] for prior in task["evidence"]):
                        raise ContractError("重复证据标识，不能覆盖另一来源")
                    if not item.get("snapshot") or fingerprint(item["snapshot"]) != item.get("snapshot_hash"):
                        raise ContractError("原始快照缺失或内容哈希不符")
                    validate_evidence(item, task)
                    task["evidence"].append(item)
                except ContractError as error:
                    item["rejection"] = str(error)
                    task["rejected_evidence"].append(item)

            # Source witnesses are adopted before their API dependents, in one
            # transaction-sized step. Input order never buys an extra model or
            # data call, and a failed witness cannot authorize an observation.
            for original in sorted(candidates, key=lambda item: item.get("artifact", {}).get("kind") == "api"):
                adopt(original)
            def api_spec(spec):
                return any(record.get("id") == spec.get("artifact_id") and record.get("kind") == "api"
                           for record in records) if isinstance(spec, dict) else False
            for spec in sorted(specifications, key=api_spec):
                artifact, item = None, None
                try:
                    if not isinstance(spec, dict):
                        raise ContractError("取证规格须为字段记录")
                    if task.get("run_mode") == "model":
                        validate_model_specs(task, [spec])
                    if active and (not isinstance(spec.get("question_ids"), list) or not set(spec["question_ids"]).issubset(active["affected_questions"])):
                        raise ContractError("补证取证规格越过已登记影响问题")
                    artifact = next((x for x in records if x["id"] == spec.get("artifact_id") or
                                     spec.get("request_id") and spec["request_id"] in x.get("data_need_ids", [])), None)
                    if artifact is None:
                        raise ContractError("取证原件或快照不存在")
                    if active and artifact["id"] not in active["proposal"]["artifact_ids"] and not set(artifact.get("data_need_ids", [])).intersection(n["id"] for n in needs):
                        raise ContractError("补证原件不在已登记请求范围")
                    item = candidate_evidence(task, artifact, spec)
                    adopt(self.analyzer.assess(task, item, spec.get("review"), records))
                except ContractError as error:
                    task["rejected_evidence"].append({"id": spec.get("id", "未登记") if isinstance(spec, dict) else "未登记",
                        "artifact_id": spec.get("artifact_id") if isinstance(spec, dict) else None,
                        "object": spec.get("object", "未确定") if isinstance(spec, dict) else "未确定",
                        "source": artifact.get("source", "待定位") if artifact else "待定位",
                        "question_ids": spec.get("question_ids", []) if isinstance(spec, dict) else [],
                        "rejection": str(error), "source_check": item.get("source_check", "不通过") if item else "不通过",
                        "applicability_check": "不通过" if item else "待核验"})
            from .provenance import query_requirements
            from .data_requirements import pending as pending_data_needs
            unmet=pending_data_needs(task)
            if unmet:
                self._gap(task,'所需数据尚未逐项取得：'+'；'.join(item['text'] for item in unmet),
                    list(dict.fromkeys(item['question_id'] for item in unmet)),category='question_coverage')
            evidence_questions = [q['id'] for q in task['questions'] if q.get('answer_scope') != 'general_definition'
                and not (query_requirements(q) and len(query_requirements(q)) == len(q['evidence_needs']))]
            if not task["evidence"] and evidence_questions:
                explanation = ("离线模式不自动联网，需宿主提供可核验原件。" if self.offline else
                               "原件或快照尚未形成可用依据，请核对来源定位、公开时点和研究口径。")
                self._gap(task, "尚无通过两类核验的必要证据；" + explanation,
                          active["affected_questions"] if active else evidence_questions,
                          category='evidence_absence' if task.get('evidence_gap_lifecycle_version') == 1 else None)
        elif node == "calculation":
            if task.get('run_mode') == 'model' and not fixture and (
                    not active or self._missing_calculations(task, selected)):
                self._bind_initial_calculations(task, selected if active else None)
            specs = task.get("calculation_specs", [])
            if not active:
                task["calculations"] = []
            else:
                existing = {c["id"] for c in task["calculations"]}
                specs = [s for s in specs if s.get("id") not in existing and set(s.get("question_ids", [])).intersection(selected)]
            for spec in specs:
                self._calculate_spec(task, spec, synthetic=bool(fixture))
            self._resolve_current_gaps(task)
            for question_id, methods in self._missing_calculations(task, selected).items():
                message = "必要计算尚未完成：" + question_id + "，" + "、".join(methods) + "。缺少已核验输入、口径或适用方法，未生成数值。"
                if not any(record["message"] == message and record["question_ids"] == [question_id]
                           for record in task.get("gap_records", [])):
                    self._gap(task, message, [question_id], category='calculation', method_ids=methods)
                next(q for q in task["questions"] if q["id"] == question_id)["status"] = "未回答"
        elif kind in ("answer", "thesis"):
            incremental = not active or any(e["id"] not in active["before"]["evidence"] for e in task["evidence"]) or any(c["id"] not in active["before"]["calculations"] for c in task["calculations"])
            from .provenance import query_candidates, query_requirements
            lineage_ready = any(query_requirements(q) for q in task["questions"]) and bool(query_candidates(task))
            if task["intent"] != DOCUMENT_CONTENT_INTENT and task.get("run_mode") == "model" and (task["evidence"] or lineage_ready) and incremental:
                contract = analysis_contract(task, active["affected_questions"] if active else None)
                digest = fingerprint({"questions": question_specs(task), "evidence": task["evidence"], "calculations": task["calculations"],
                                      "supplement": active["round"] if active else None,
                                      "execution_contract": contract["execution"],
                                      "numeric_contract": contract["numeric_statement"],
                                      **({'judgment_review': contract['judgment_review']} if 'judgment_review' in contract else {}),
                                      **({"query_contract": {key: value["query_provenance"] for key, value in contract["by_question"].items()
                                                         if "query_provenance" in value}} if lineage_ready else {}),
                                      "answer_structure": contract["answer_item_structure"]})
                saved = task.setdefault("reasoning_state", {}).get("analysis", {})
                reusable = (saved.get("input_digest") == digest and
                            saved.get("output_hash") == fingerprint(task.get("host_result", {})))
                if reusable:
                    try:
                        validate_analysis_output(None, task, task.get("host_result", {}),
                                                 len(task["ledger"]["dispatches"]))
                        validate_analysis_execution(None, task, task["host_result"],
                                                    len(task["ledger"]["dispatches"]))
                        reusable = analysis_numeric_review(task, task["host_result"])["accepted"]
                    except ContractError:
                        # Only an explicit task start/resume reaches this node.
                        # Retain accounting, but never replay an invalid legacy
                        # proposal solely because its input digest still matches.
                        reusable = False
                # Rejected proposals remain available to this batch's consumers
                # for partial delivery, but cannot become a reusable checkpoint.
                # Do not spend another call per node to repair the same response.
                same_attempt = (saved.get("attempt_digest") == digest and saved.get("attempt_batch") == task["batch"]
                                and saved.get("output_hash") == fingerprint(task.get("host_result", {})))
                if reusable or same_attempt:
                    host_result = task["host_result"]
                else:
                    output_rejected = False
                    rejection_field = "analysis.output_structure"
                    final_delivery=not self._reasoning_available(task) and self._final_analysis_available(task)
                    if self._reasoning_available(task) or final_delivery:
                        try:
                            if final_delivery:
                                task.setdefault('reasoning_state',{})['final_delivery']={
                                    'version':1,'status':'已派发','further_research_allowed':False}
                                self._save_model_checkpoint(task)
                                answer=self._reason_on_verified_material(task,
                                    active['affected_questions'] if active else None,final_delivery=True)
                            else:
                                answer = self._reason_on_verified_material(task, active["affected_questions"] if active else None)
                        except (QueryReferenceFormatError, NumericSegmentFormatError, ModelCompletionError, ModelJSONFormatError) as error:
                            # Do not coerce malformed references or fabricate a
                            # repaired answer. Other verified work can still be
                            # delivered with this explicit gap and its ledger.
                            output_rejected = True
                            answer = phase_output_example('analyze', task)
                            known_format=self._completed_analysis_format_issue(task,
                                active['affected_questions'] if active else None)
                            recovered=(self._recover_completed_analysis_format(task,
                                active['affected_questions'] if active else None)
                                if not final_delivery and argument_support.enabled(task) else None)
                            if recovered is not None:
                                answer=recovered
                                output_rejected=False
                            if known_format:
                                rejection_field='analysis.output_format'
                                gap='模型分析返回格式未通过'
                            elif isinstance(error, ModelCompletionError):
                                issue = analysis_completion_failure(task)
                                rejection_field, gap = issue['field'], issue['reason']
                            elif isinstance(error, ModelJSONFormatError):
                                rejection_field = "analysis.json"
                                gap = "模型分析正文不是有效JSON"
                            else:
                                gap = ("模型查询引用格式未通过" if isinstance(error, QueryReferenceFormatError)
                                       else "模型数字片段格式未通过")
                            if output_rejected:
                                self._gap(task, gap + "，未采用本次回答；保留已核验证据与计算，交付缺口报告。",
                                          active["affected_questions"] if active else selected)
                    else:
                        answer = phase_output_example('analyze', task)
                        task["gaps"].append("研究模型额度不足，保留已核验证据并使用交付预留收束。")
                    if judgment_review.enabled(task):
                        # Bind positions to the actual analysis submission,
                        # before unrelated retained theses are prepended below.
                        task['reasoning_state'].setdefault('analysis', {})['submitted_thesis_hashes'] = [
                            fingerprint(item) for item in answer['theses']]
                    prior = task.get("host_result", {})
                    if active:
                        retained_answers = {key: value for key, value in prior.get("answers", {}).items()
                            if not output_rejected or key not in active["affected_questions"]}
                        retained_theses = [item for item in prior.get("theses", []) if not output_rejected or
                            not set(item.get("question_ids", active["affected_questions"])).intersection(active["affected_questions"])]
                        answer["answers"] = dict(retained_answers, **{key: value for key, value in answer["answers"].items() if key in active["affected_questions"]})
                        answer["theses"] = retained_theses + answer["theses"]
                    host_result = dict(prior, **answer)
                    if not output_rejected:
                        revised = self._finalize_numeric_submission(task, host_result,
                            active['affected_questions'] if active else None)
                        if revised is not host_result:
                            host_result = dict(prior, **revised)
                            if judgment_review.enabled(task):
                                task['reasoning_state']['analysis']['submitted_thesis_hashes'] = [
                                    fingerprint(item) for item in revised['theses']]
                    task["host_result"] = host_result
                    review = analysis_numeric_review(task, host_result)
                    if output_rejected:
                        review = {"version": 1, "accepted": False, "invalid_fields": [rejection_field],
                                  "automatic_repair": False, "approval": False}
                    checkpoint = dict(task["reasoning_state"].get("analysis", {}),
                                      output_hash=fingerprint(host_result), numeric_review=review,
                                      attempt_digest=digest, attempt_batch=task["batch"])
                    checkpoint["output_rejected"] = output_rejected
                    checkpoint.pop("input_digest", None)
                    if review["accepted"]:
                        checkpoint["input_digest"] = digest
                    task["reasoning_state"]["analysis"] = checkpoint
                    self._save_model_checkpoint(task)
            self._prepare_argument_review(task, host_result, selected)
            host_result = self._revise_reviewed_writing(task, host_result, selected)
            self._answer_questions(task, selected, host_result, fixture)
            if kind == "thesis":
                self._accept_theses(task, selected, host_result, fixture)
                if judgment_review.enabled(task):
                    hashes = (task.get('reasoning_state', {}).get('analysis', {}).get('submitted_thesis_hashes')
                              if task.get('run_mode') == 'model' else None)
                    judgment_review.collect(task, host_result or {}, selected, filter_scope=True, thesis_hashes=hashes)
            self._resolve_current_gaps(task)
            task["gaps"] = list(dict.fromkeys(task["gaps"]))
        elif node == "compose":
            self._record_version_changes(task, fixture)
            from .reports import compose
            task["pending_content"] = compose(task)
        elif node == "render":
            from .reports import render
            task["pending_html"] = render(task["pending_content"], self.resources)
        return task

    def _save_model_checkpoint(self, task):
        """Save semantic decisions only while their actual inputs remain current."""
        with self.lock:
            fresh = self._active(task["id"], task["batch"])
            if fresh.get("conversation_context") != task.get("conversation_context"):
                raise ContractError("推理期间追问上下文已变化，拒绝迟到结果")
            if fingerprint(original_sources(fresh)) != fingerprint(original_sources(task)):
                raise ContractError("推理期间原始材料已变化，保留新增材料并从检查点恢复")
            task["revision"] = fresh["revision"]
            self.archive.save(task)

    def argument_review_inputs(self, ident):
        """Read the actual host proposal after evidence and calculations run."""
        with self.lock:
            task = self._get(ident)
            if not argument_support.enabled(task) or not task.get('host_result'):
                raise ContractError('本任务没有待复核的语言分析')
            request = argument_support.review_request(task, task['host_result'])
            return dict(request=request, schema=argument_support.review_schema(request))

    def submit_argument_review(self, ident, wire):
        """Host reasoning only: no App model, no fabricated model ledger."""
        with self.lock:
            task = self._get(ident)
            if (task.get('run_mode') != 'host' or task.get('delivery') or task.get('pending_content')
                    or task['status'] not in ('待开始','已取消','失败') or not argument_support.enabled(task)):
                raise ContractError('宿主只能复核已停止且尚未交付的研究检查点')
            if ident in self.workers and self.workers[ident].is_alive():
                raise ContractError('旧执行仍在停止，未采用复核结果')
            proposal = task.get('host_result', {})
            request = argument_support.review_request(task, proposal)
            result = argument_support.decode_review(wire, argument_support.review_schema(request), request)
            record = argument_support.make_record(task, proposal, result, actor='host')
            task.setdefault('argument_support_reviews', []).append(record)
            for node in task['nodes']:
                if node.get('kind') in ('answer','thesis') or node['id'] in ('compose','render'):
                    node['status'] = '待执行'
            self.archive.save(task)
            return copy.deepcopy(result)

    def _prepare_argument_review(self, task, proposal, selected):
        """One reviewed input per attempt; never retry an unknown dispatch."""
        if not argument_support.enabled(task) or not proposal:
            return
        definitions = {q['id'] for q in task['questions'] if q.get('answer_scope') == 'general_definition'}
        if not proposal.get('theses') and set(proposal.get('answers', {})).issubset(definitions):
            return
        try:
            request = argument_support.review_request(task, proposal)
            record = argument_support.find_record(task, proposal)
        except ContractError:
            self._gap(task, '分析尚未通过来源、数量或论证输入核验，未采用研究判断。', selected)
            return
        key = argument_support.review_key(request)
        if record is None and task.get('run_mode') == 'model':
            attempts = task.setdefault('reasoning_state', {}).setdefault('argument_support_attempts', {})
            attempt = attempts.get(key)
            analysis=task['reasoning_state'].get('analysis',{})
            analysis_input=analysis.get('input_digest',analysis.get('attempt_digest'))
            analysis_output=analysis.get('output_hash')
            submission=fingerprint(dict(analysis_input=analysis_input,analysis_output=analysis_output)) \
                if analysis_input and analysis_output else fingerprint(dict(
                    answers=proposal.get('answers',{}),theses=proposal.get('theses',[])))
            # Later execution nodes may change the review envelope without a
            # new completed analysis. Never redispatch that same submission
            # after failure/unknown completion merely under a different key.
            submission_attempted=any(saved.get('analysis_submission')==submission for saved in attempts.values())
            method = getattr(self.model, 'reason_argument_support', None)
            ledger = task['ledger']
            available = (callable(method) and ledger['limits']['model_calls'] > ledger['used']['model_calls']
                         and ledger['limits']['seconds'] - ledger['used']['seconds'] > 5
                         and task['reasoning_state'].get('final_delivery', {}).get('status')
                         not in ('已派发','已提交','未采用'))
            if attempt is None and not submission_attempted and available:
                delivery = not self._reasoning_available(task)
                attempt = dict(status='已派发', batch=task['batch'], review_hash=request['review_hash'],
                               analysis_submission=submission)
                attempts[key] = attempt
                if delivery:
                    task['reasoning_state']['final_delivery'] = dict(version=1,status='已派发',
                        further_research_allowed=False,reason='argument_support')
                self._save_model_checkpoint(task)
                dispatch_index = len(task['ledger']['dispatches'])
                try:
                    import json
                    result = json.loads(method(task, proposal, delivery=delivery))
                    record = argument_support.make_record(task, proposal, result, actor='model')
                    task.setdefault('argument_support_reviews', []).append(record)
                    attempt['status'] = '已复核'
                    if delivery:
                        task['reasoning_state']['final_delivery']['status'] = '已提交'
                except (ContractError, ValueError):
                    self._check_run_control(task)  # Cancellation still wins.
                    record = self._recover_completed_argument_format(task, proposal, attempt,
                        failed_dispatch_index=dispatch_index)
                    attempt['status'] = '已复核' if record is not None else '未采用'
                    if delivery or task['reasoning_state'].get('final_delivery', {}).get('reason') == 'argument_support':
                        task['reasoning_state']['final_delivery']['status'] = '已提交' if record is not None else '未采用'
                self._save_model_checkpoint(task)
        if record is None:
            self._gap(task, '论证复核未完成，未采用未经复核的研究回答与判断。', selected)
            return
        targets = {item['id']: item for item in request['targets']}
        by_question = {q['id']: q for q in task['questions']}
        from .review_answer_choices import effective_assessments
        affected = list(dict.fromkeys(ident for item in effective_assessments(request, record['result']) if not item['supported']
            for ident in targets[item['target_id']]['question_ids']
            if item['target_id'].startswith('answer:') or by_question[ident]['kind'] not in DIRECT_KINDS))
        if affected:
            self._gap(task, '部分陈述缺少足够依据，未采用对应回答或论点；已核验原值和计算保留。', affected)

    def _revise_reviewed_writing(self, task, proposal, selected):
        """One same-evidence revision, then a new independent content review.

        Only an explicitly completed review identifying a writing-ready issue
        can enter. Other questions may retain their genuine evidence gaps.
        This is not acquisition or an automatic prose repair.
        The journal survives cancellation/resume and the original budget holds.
        """
        state = task.get('reasoning_state', {})
        ledger = task['ledger']
        reserve_version = task.get('review_delivery_call_version')
        if reserve_version is not None and (type(reserve_version) is not int or reserve_version != 1):
            return proposal
        # The new explicit policy may use the delivery call for final review,
        # never for generating unreviewable prose. Keep the time reserve.
        held_calls = 0 if reserve_version == 1 else ledger['reserve'].get('model_calls', 0)
        if (type(task.get('reviewed_writing_revision_version')) is not int
                or task['reviewed_writing_revision_version'] != 1
                or task.get('run_mode') != 'model' or task.get('status') != '运行中'
                or not argument_support.enabled(task) or 'reviewed_writing_revision' in state
                or ledger['limits']['model_calls'] - ledger['used']['model_calls']
                    - held_calls < 2
                or not self._reasoning_available(task)):
            return proposal
        try:
            record = argument_support.find_record(task, proposal)
        except ContractError:
            return proposal
        if record is None:
            return proposal
        questions = record['result']['question_assessments']
        revision_ready = argument_support.revision_readiness_enabled(task)
        from .review_answer_choices import effective_assessments
        request = argument_support.review_request(task, proposal)
        if not (any(not item['supported'] for item in effective_assessments(request, record['result']))
                or revision_ready and any(item['status'] == 'requires_revision' for item in questions)):
            return proposal
        related = {ident for target in request['targets'] for ident in target['question_ids']}
        revision_questions = list(selected) if selected else sorted(related)
        if revision_ready and set(revision_questions) != related:
            active = self._active_supplement(task)
            original_scope = {q['id'] for q in task['questions']
                if q.get('answer_scope') != 'general_definition'
                and (not active or q['id'] in active['affected_questions'])}
            # Initial analysis serves several execution nodes. Repair that
            # same full proposal, not just the node currently consuming it.
            # Active supplements must still keep their exact limited scope.
            if related == original_scope and set(revision_questions).issubset(related):
                revision_questions = [q['id'] for q in task['questions'] if q['id'] in related]
        if related != set(revision_questions) or {item['question_id'] for item in questions} != related:
            return proposal
        try:
            readiness_scope = argument_support.mixed_revision_scope(task, record, request)
        except ContractError:
            return proposal
        if readiness_scope is not None:
            if not readiness_scope['revision_eligible']:
                return proposal
        elif any(item['status'] not in (('answered', 'unidentifiable', 'requires_revision')
                if revision_ready else ('answered', 'unidentifiable')) or item['missing_evidence']
                 for item in questions):
            return proposal
        fixed_inputs = fingerprint({key: task[key] for key in ('evidence', 'calculations', 'source_records')})
        revision = dict(version=1, status='已派发',
            original_proposal={key: copy.deepcopy(proposal.get(key, {} if key == 'answers' else []))
                for key in ('answers', 'theses')},
            review=copy.deepcopy(record['result']), review_key=record['review_key'],
            fixed_inputs_hash=fixed_inputs, question_ids=revision_questions,
            automatic_repair=False, further_acquisition_allowed=False)
        if readiness_scope is not None:
            revision['readiness_scope'] = readiness_scope
        state = task.setdefault('reasoning_state', {})
        state['reviewed_writing_revision'] = revision
        self._save_model_checkpoint(task)
        try:
            answer = self._reason_on_verified_material(task, revision_questions)
            self._check_run_control(task)
            if fingerprint({key: task[key] for key in ('evidence', 'calculations', 'source_records')}) != fixed_inputs:
                raise ContractError('同证据文字修订期间输入已变化，未采用迟到文本')
            candidate = dict(proposal, **answer)
            numeric = analysis_numeric_review(task, candidate)
            if not numeric['accepted']:
                revision['status'] = '未采用'
                self._save_model_checkpoint(task)
                return proposal
            if revision_ready and fingerprint({key: candidate.get(key, {} if key == 'answers' else [])
                    for key in ('answers', 'theses')}) == fingerprint(revision['original_proposal']):
                revision['status'] = '未改稿'
                self._save_model_checkpoint(task)
                return proposal
            revision['status'] = '已提交'
            task['host_result'] = candidate
            checkpoint = state.setdefault('analysis', {})
            checkpoint.update(output_hash=fingerprint(candidate), numeric_review=numeric)
            if judgment_review.enabled(task):
                checkpoint['submitted_thesis_hashes'] = [fingerprint(item) for item in candidate.get('theses', [])]
            self._save_model_checkpoint(task)
            self._prepare_argument_review(task, candidate, revision_questions)
            revision['status'] = '已复核' if argument_support.find_record(task, candidate) else '复核未完成'
            self._save_model_checkpoint(task)
            return candidate
        except (QueryReferenceFormatError, NumericSegmentFormatError, ModelCompletionError, ModelJSONFormatError, ContractError):
            self._check_run_control(task)
            revision['status'] = '未采用'
            self._save_model_checkpoint(task)
            return task.get('host_result', proposal)

    def _recover_completed_argument_format(self, task, proposal, attempt, *, failed_dispatch_index):
        """One journaled fresh review, not a repaired or automatically approved verdict."""
        state = task.setdefault('reasoning_state', {})
        ledger = task['ledger']
        if (type(task.get('argument_submission_recovery_version')) is not int
                or task['argument_submission_recovery_version'] != 1
                or not argument_support.enabled(task) or task.get('run_mode') != 'model'
                or task.get('status') != '运行中' or 'argument_submission_recovery' in state
                or type(failed_dispatch_index) is not int
                or failed_dispatch_index != len(ledger['dispatches'])-1
                or ledger['used']['model_calls'] >= ledger['limits']['model_calls']
                or ledger['limits']['seconds']-ledger['used']['seconds'] <= 5):
            return None
        issue = argument_support.completed_review_submission_issue(task, proposal)
        if issue is None or attempt.get('review_hash') != issue['review_hash']:
            return None
        final_choice_review = (type(task.get('review_choice_delivery_version')) is int
            and task['review_choice_delivery_version'] == 1)
        if (issue['category'] == 'completed_review_choice'
                and not final_choice_review and not self._reasoning_available(task)):
            return None  # Preserve delivery reserves for a new choice assessment.
        final = state.get('final_delivery', {})
        if final and not (final.get('reason') == 'argument_support' and final.get('status') == '已派发'):
            return None
        self._check_run_control(task)
        delivery = bool(final) or not self._reasoning_available(task)
        if delivery and not final:
            state['final_delivery'] = dict(version=1, status='已派发',
                further_research_allowed=False, reason='argument_support')
        recovery = dict(version=1, status='已派发', review_hash=issue['review_hash'],
            first_failure_dispatch=failed_dispatch_index, format_issue=issue,
            automatic_repair=False, further_acquisition_allowed=False)
        state['argument_submission_recovery'] = recovery
        self._save_model_checkpoint(task)
        try:
            import json
            result = json.loads(self.model.reason_argument_support(task, proposal,
                delivery=delivery, format_issue=issue))
            self._check_run_control(task)
            record = argument_support.make_record(task, proposal, result, actor='model')
            task.setdefault('argument_support_reviews', []).append(record)
            recovery['status'] = '已复核'
            return record
        except (ContractError, ValueError):
            self._check_run_control(task)
            recovery['status'] = '未采用'
            return None
        finally:
            self._save_model_checkpoint(task)

    @staticmethod
    def _reasoning_available(task):
        ledger = task["ledger"]
        return ledger["limits"]["model_calls"] - ledger["used"]["model_calls"] > ledger["reserve"]["model_calls"] and \
            ledger["limits"]["seconds"] - ledger["used"]["seconds"] > ledger["reserve"]["seconds"]

    def _final_analysis_available(self,task):
        # A mandatory independent review needs its own dispatch. Spending the
        # final reserved call on new prose cannot yield a reviewable answer.
        if argument_support.enabled(task):
            return False
        support=getattr(self.model,'supports_final_delivery',None)
        ledger=task['ledger']
        return (callable(support) and support() is True
            and callable(getattr(self.model,'reason_final_analysis',None))
            and ledger['reserve']['model_calls']>=1
            and ledger['limits']['model_calls']>ledger['used']['model_calls']
            and ledger['limits']['seconds']-ledger['used']['seconds']>5
            and task.get('reasoning_state',{}).get('final_delivery',{}).get('status') not in ('已派发','已提交','未采用'))

    def _finalize_numeric_submission(self, task, proposal, selected=None):
        """One final numeric-format resubmission, with unchanged research inputs.

        This is not another evidence round or an automatic text rewrite. The
        rejected proposal stays inspectable; the final proposal must pass all
        ordinary source, numeric and execution gates before it can be used.
        """
        review = analysis_numeric_review(task, proposal)
        if review['accepted']:
            return proposal
        return self._resubmit_analysis_once(task,proposal,review,selected)

    @staticmethod
    def _completed_model_format_issue(task, phase):
        """Only known, complete business-output format rejection is eligible.

        Never retry timeouts, unknown dispatches, truncation, refusals, access
        failures or incomplete bodies. The rejected text is not retained.
        """
        events=[event for event in task.get('ledger',{}).get('dispatches',[]) if event.get('kind')=='model_calls']
        if not events:return None
        event=events[-1]
        if (phase not in ('analyze', 'extract') or event.get('research_phase')!=phase or event.get('status')!='失败'
                or event.get('failure_code')!='structured_output_rejected'
                or event.get('provider_completion_status')!='completed'
                or event.get('response_body_complete') is not True or event.get('finish_reason')!='stop'):
            return None
        if phase == 'extract' and (event.get('batch') != task['batch']
                or event.get('structured_failure_category') != 'invalid_argument_json'):
            return None
        diagnostic=event.get('structured_output_diagnostic') or {}
        if not isinstance(diagnostic,dict):return None
        allowed={'expected_comma','expected_colon','expected_property_name','expected_value',
                 'invalid_escape','invalid_control_character','invalid_unicode_escape',
                 'unterminated_string','extra_data','syntax_error'}
        category = 'completed_json_syntax'
        if (phase == 'extract' and type(task.get('extraction_value_recovery_version')) is int
                and task['extraction_value_recovery_version'] == 1
                and diagnostic.get('json_error') in ('duplicate_key', 'nonfinite_constant')):
            allowed.update(('duplicate_key', 'nonfinite_constant'))
            category = 'completed_json_value'
        if diagnostic.get('json_error') not in allowed:return None
        return dict(version=1,category=category,json_error=diagnostic['json_error'],
                    original_text_retained=False,automatic_repair=False)

    @staticmethod
    def _completed_analysis_format_issue(task, selected=None):
        syntax = ResearchAgent._completed_model_format_issue(task, 'analyze')
        if syntax is not None:
            return syntax
        if type(task.get('analysis_structure_recovery_version')) is not int or task['analysis_structure_recovery_version'] != 1:
            return None
        events = task.get('ledger', {}).get('dispatches', [])
        event = events[-1] if events else {}
        from .output_schema import analysis_output_schema, analysis_protocol, registered_structure_issues
        if (event.get('kind') != 'model_calls' or event.get('research_phase') != 'analyze'
                or event.get('status') != '失败' or event.get('batch') != task['batch']
                or event.get('failure_code') != 'structured_output_rejected'
                or event.get('structured_failure_category') != 'schema_mismatch'
                or event.get('provider_completion_status') != 'completed'
                or event.get('response_body_complete') is not True or event.get('finish_reason') != 'stop'
                or event.get('output_protocol') != analysis_protocol(task)[0]):
            return None
        schema = analysis_output_schema(task, selected)
        if event.get('output_schema_hash') != fingerprint(schema):
            return None
        issues = registered_structure_issues(schema, event.get('structured_output_diagnostic'))
        category = 'completed_analysis_structure'
        if (issues is None and type(task.get('analysis_reference_choice_recovery_version')) is int
                and task['analysis_reference_choice_recovery_version'] == 1
                and event.get('network_request_started') is True and event.get('network_worker_reaped') is True):
            from .output_schema import registered_analysis_reference_choice_issues
            issues = registered_analysis_reference_choice_issues(schema, event.get('structured_output_diagnostic'))
            category = 'completed_analysis_reference_choice'
        if issues is None:
            return None
        return dict(version=1, category=category, schema_issues=issues,
                    original_text_retained=False, automatic_repair=False)

    @staticmethod
    def _completed_original_structure_issue(task, payload):
        """Only known field/type errors of this exact original-selection call.

        Enums are not structural corrections. Separately versioned locator
        policies can request one observation-index or literal-address selection
        after a known complete response. They share the existing correction
        slot and never repair financial values, units or source bindings.
        """
        if task.get('original_span_diagnostics_version') != 1:
            return None
        events = task.get('ledger', {}).get('dispatches', [])
        event = events[-1] if events else {}
        if (event.get('kind') != 'model_calls' or event.get('research_phase') != 'extract'
                or event.get('status') != '失败' or event.get('batch') != task['batch']
                or event.get('failure_code') != 'structured_output_rejected'
                or event.get('structured_failure_category') != 'invalid_original_spans'
                or event.get('provider_completion_status') != 'completed'
                or event.get('response_body_complete') is not True or event.get('finish_reason') != 'stop'
                or event.get('output_protocol') != 'strict_original_spans_v1'):
            return None
        from .output_schema import original_span_output_schema
        schema = original_span_output_schema(task, payload.get('untrusted_sources'))
        if schema is None or event.get('output_schema_hash') != fingerprint(schema):
            return None
        from .output_schema import registered_structure_issues
        issues = registered_structure_issues(schema, event.get('structured_output_diagnostic'))
        category = 'completed_original_structure'
        if (issues is None and type(task.get('original_temporal_choice_recovery_version')) is int
                and task['original_temporal_choice_recovery_version'] == 1
                and event.get('network_request_started') is True and event.get('network_worker_reaped') is True):
            from .output_schema import registered_temporal_choice_issues
            issues = registered_temporal_choice_issues(schema, event.get('structured_output_diagnostic'))
            category = 'completed_original_temporal_choice'
        if (issues is None and type(task.get('original_literal_choice_recovery_version')) is int
                and task['original_literal_choice_recovery_version'] == 1
                and event.get('network_request_started') is True and event.get('network_worker_reaped') is True):
            from .output_schema import registered_literal_choice_issues
            issues = registered_literal_choice_issues(schema, event.get('structured_output_diagnostic'))
            category = 'completed_original_literal_choice'
        if issues is None:
            return None
        return dict(version=1, category=category, schema_issues=copy.deepcopy(issues),
                    original_text_retained=False, automatic_repair=False)

    def _recover_completed_extraction_format(self, task, payload, input_digest, *, failed_dispatch_index):
        """One fresh bounded submission, never a network retry or JSON repair."""
        if (type(task.get('extraction_submission_recovery_version')) is not int
                or task['extraction_submission_recovery_version'] != 1 or not argument_support.enabled(task)):
            return None
        state = task.setdefault('reasoning_state', {})
        events = task['ledger'].get('dispatches', [])
        remaining = task['ledger']['limits']['model_calls'] - task['ledger']['used']['model_calls']
        if ('extraction_submission_recovery' in state or remaining < 3 or not self._reasoning_available(task)
                or type(failed_dispatch_index) is not int or failed_dispatch_index != len(events) - 1):
            return None
        issue = self._completed_model_format_issue(task, 'extract')
        if issue is None and remaining >= 4:
            issue = self._completed_original_structure_issue(task, payload)
        if issue is None:
            return None
        self._active(task['id'], task['batch'])
        recovery = dict(version=1, status='已派发', input_digest=input_digest,
            payload_digest=fingerprint(payload), first_failure_dispatch=failed_dispatch_index,
            format_issue=issue, automatic_repair=False, further_acquisition_allowed=False)
        state['extraction_submission_recovery'] = recovery
        self._save_model_checkpoint(task)
        try:
            result = model_object(self.model, task, 'extract', payload, {'evidence_specs'})
            self._retain_original_selection_diagnostics(task,result[1],input_digest)
            self._active(task['id'], task['batch'])
            recovery['status'] = '已提交'
            self._save_model_checkpoint(task)
            return result
        except ContractError:
            recovery['status'] = '未采用'
            events = task['ledger'].get('dispatches', [])
            latest_index = len(events) - 1
            latest = events[-1] if events else {}
            if (latest_index > failed_dispatch_index and latest.get('kind') == 'model_calls'
                    and latest.get('research_phase') == 'extract' and latest.get('batch') == task['batch']
                    and latest.get('status') == '失败'):
                recovery['last_failure'] = dict(dispatch_index=latest_index,
                    failure_code=latest.get('failure_code'),
                    structured_category=latest.get('structured_failure_category'),
                    diagnostic=copy.deepcopy(latest.get('structured_output_diagnostic') or {}))
            self._save_model_checkpoint(task)
            return None

    @staticmethod
    def _extraction_failure_message(task, input_digest, original_error):
        """Show the latest bounded attempt, not a stale first rejection."""
        recovery = task.get('reasoning_state', {}).get('extraction_submission_recovery', {})
        failure = recovery.get('last_failure', {})
        events = task.get('ledger', {}).get('dispatches', [])
        index = failure.get('dispatch_index')
        if (recovery.get('input_digest') != input_digest or recovery.get('status') != '未采用'
                or type(index) is not int or index != len(events)-1 or not 0<=index<len(events)
                or events[index].get('research_phase') != 'extract' or events[index].get('status') != '失败'
                or events[index].get('batch') != task['batch']):
            return str(original_error)
        event = events[index]
        if (failure.get('failure_code') != event.get('failure_code')
                or failure.get('structured_category') != event.get('structured_failure_category')
                or failure.get('diagnostic') != (event.get('structured_output_diagnostic') or {})):
            return str(original_error)
        diagnostic = failure.get('diagnostic', {})
        if failure.get('failure_code') == 'structured_output_rejected':
            if diagnostic.get('json_error') == 'duplicate_key':
                return '重新提交仍含重复JSON字段，未采用本批结果。'
            if diagnostic.get('json_error') == 'nonfinite_constant':
                return '重新提交仍含非有限数值，未采用本批结果。'
            return '重新提交未满足当前字段结构，未采用本批结果；原始资料与已派发用量保留。'
        return '本轮提取重新提交未完成，原始资料与已派发用量保留。'

    def _recover_completed_analysis_format(self,task,selected=None):
        issue=self._completed_analysis_format_issue(task, selected)
        if issue is None or not argument_support.enabled(task):return None
        proposal=phase_output_example('analyze',task)
        result=self._resubmit_analysis_once(task,proposal,
            dict(version=1,accepted=False,invalid_fields=['analysis.output_format'],automatic_repair=False),
            selected,format_issue=issue)
        return result if result is not proposal else None

    @staticmethod
    def _completed_planning_format_issue(task):
        """Known structural rejection only, with the actual current plan schema."""
        events=task.get('ledger',{}).get('dispatches',[])
        if not events:return None
        event=events[-1]
        if (event.get('kind')!='model_calls' or event.get('research_phase')!='plan'
                or event.get('status')!='失败' or event.get('batch')!=task['batch']
                or event.get('failure_code')!='structured_output_rejected'
                or event.get('structured_failure_category')!='schema_mismatch'
                or event.get('provider_completion_status')!='completed'
                or event.get('response_body_complete') is not True or event.get('finish_reason')!='stop'):
            return None
        from .output_schema import planning_output_schema,planning_protocol
        if (event.get('output_protocol')!=planning_protocol(task)[0]
                or event.get('output_schema_hash')!=fingerprint(planning_output_schema(task))):
            return None
        diagnostic=event.get('structured_output_diagnostic')
        if (not isinstance(diagnostic,dict) or diagnostic.get('version')!=1
                or diagnostic.get('submitted_values_retained') is not False
                or diagnostic.get('automatic_repair') is not False):return None
        issues=diagnostic.get('schema_issues')
        if not isinstance(issues,list) or not 1<=len(issues)<=8:return None
        allowed={'unknown_fields','missing_field','expected_object','expected_array','type_mismatch'}
        for issue in issues:
            if (not isinstance(issue,dict) or set(issue)!={'code','path'} or issue['code'] not in allowed
                    or not isinstance(issue['path'],str) or len(issue['path'])>256
                    or not re.fullmatch(r'\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\*\])*',issue['path'])):
                return None
        return dict(version=1,category='completed_plan_structure',schema_issues=copy.deepcopy(issues),
            original_text_retained=False,automatic_repair=False)

    def _recover_completed_planning_format(self,task,payload,input_digest,*,failed_dispatch_index):
        """One fresh plan submission, journaled before dispatch, on the same input."""
        if (type(task.get('planning_submission_recovery_version')) is not int
                or task['planning_submission_recovery_version']!=1 or not argument_support.enabled(task)
                or task.get('status')!='运行中'):return None
        state=task.setdefault('reasoning_state',{})
        remaining=task['ledger']['limits']['model_calls']-task['ledger']['used']['model_calls']
        if ('planning_submission_recovery' in state or remaining<3 or not self._reasoning_available(task)
                or input_digest!=planning_digest(task) or type(failed_dispatch_index) is not int
                or failed_dispatch_index!=len(task['ledger']['dispatches'])-1):return None
        issue=self._completed_planning_format_issue(task)
        if issue is None:return None
        self._active(task['id'],task['batch'])
        recovery=dict(version=1,status='已派发',input_digest=input_digest,payload_digest=fingerprint(payload),
            context=context(task),first_failure_dispatch=failed_dispatch_index,format_issue=issue,
            automatic_repair=False,further_acquisition_allowed=False)
        state['planning_submission_recovery']=recovery
        self._save_model_checkpoint(task)
        try:
            result=model_object(self.model,task,'plan',payload,{'questions','data_requests','calculations'})
            self._active(task['id'],task['batch'])
            recovery['status']='已提交'
            self._save_model_checkpoint(task)
            return result
        except ContractError:
            recovery['status']='未采用'
            self._save_model_checkpoint(task)
            raise

    def _resubmit_analysis_once(self,task,proposal,review,selected=None,*,format_issue=None):
        """One shared correction slot for numerical or known JSON rejection."""
        if not self._final_analysis_available(task):return proposal
        state = task.setdefault('reasoning_state', {})
        reserved = not argument_support.enabled(task)
        if not reserved:
            # Reviewed research needs one further call after the correction.
            # Never spend that last call on prose which then cannot be reviewed.
            remaining = task['ledger']['limits']['model_calls'] - task['ledger']['used']['model_calls']
            if remaining < 2 or not self._reasoning_available(task) or state.get('numeric_resubmission'):
                return proposal
        state.setdefault('rejected_analysis_submissions', []).append({
            'task_id':task['id'], 'research_version':task['version'], 'batch':task['batch'],
            'input_hash':task['input_hash'], 'proposal':copy.deepcopy(proposal),
            'numeric_review':copy.deepcopy(review), 'evidence_approved':False, 'automatic_repair':False})
        state_key = 'final_delivery' if reserved else 'numeric_resubmission'
        state[state_key] = {'version':1, 'status':'已派发', 'further_research_allowed':False,
            'reason':'analysis_output_format' if format_issue else 'numeric_submission_rejected',
            'invalid_fields':copy.deepcopy(review['invalid_fields'])}
        if task.get('analysis_question_scope_version') == 1 and 'question_failures' in review:
            state[state_key]['question_failures'] = copy.deepcopy(review['question_failures'])
        if task.get('analysis_feedback_projection_version') == 1 and 'thesis_failures' in review:
            state[state_key]['thesis_failures'] = copy.deepcopy(review['thesis_failures'])
        if format_issue:state[state_key]['format_issue']=copy.deepcopy(format_issue)
        self._save_model_checkpoint(task)
        try:
            result = self._reason_on_verified_material(task, selected, final_delivery=reserved)
            state[state_key]['status'] = '已提交'
            return result
        except (QueryReferenceFormatError, NumericSegmentFormatError, ModelCompletionError, ModelJSONFormatError):
            state[state_key]['status'] = '未采用'
            self._save_model_checkpoint(task)
            return proposal

    def _prepare_model_plan(self, ident, batch):
        task = self._active(ident, batch)
        state = task.setdefault("reasoning_state", {})
        if state.get("plan", {}).get("adopted_input_hash") == task["input_hash"] or state.get("plan", {}).get("input_digest") == planning_digest(task):
            return
        if 'planning_submission_recovery' in state:
            raise ContractError('规划重新提交已使用或结果未知，保留检查点与累计用量，不重复派发')
        knowledge = copy.deepcopy(task["knowledge"])
        from .evidence import FIELD_REGISTRY
        source_context = context_sources(task)
        payload = {"question": task["question"], "entry": task["entry"], "intent": task["intent"],
                   "objects": task["objects"], "cutoff": task["cutoff"], "questions": question_specs(task),
                   "question_origin": task.get("question_origin", "explicit"), "budget": task["ledger"],
                   "question_contract": question_contract(task),
                   "research_brief": research_brief(task),
                   "plan_preservation_contract": plan_preservation_contract(task),
                   "data_request_contract": data_request_contract(task),
                   "knowledge": knowledge, "field_registry": FIELD_REGISTRY, "filing_operations": FILING_REQUEST_REGISTRY,
                   "calculation_contract": calculation_plan_contract(task), "untrusted_sources": source_context["views"],
                   "source_catalog": source_context["catalog"]}
        if task.get('planning_definition_scope_version') == 1:
            payload['planning_definition_contract'] = self.knowledge.planning_definitions(task)
        if self._reasoning_available(task):
            before_plan=len(task['ledger']['dispatches'])
            input_digest=planning_digest(task)
            try:
                proposed, receipt = model_object(self.model, task, "plan", payload, {"questions", "data_requests", "calculations"})
            except (ModelCompletionError,ModelJSONFormatError):
                recovered=self._recover_completed_planning_format(task,payload,input_digest,
                    failed_dispatch_index=before_plan)
                if recovered is None:raise
                proposed,receipt=recovered
        else:
            proposed = {"questions": question_specs(task), "data_requests": [], "calculations": []}
            receipt = {"phase": "plan", "status": "研究额度不足，未调用模型；保留原问题与交付预留"}
        try:
            questions = validate_plan(task, proposed)
        except ContractError as error:
            # Retain the exact sanitized proposal for diagnosis, but never adopt
            # it or dispatch a tool. Otherwise a generic failure loses the
            # evidence needed to correct the model/tool contract.
            state["rejected_plan"] = {"receipt": receipt, "reason": str(error),
                                      "proposed": copy.deepcopy(proposed), "adopted": False}
            self._save_model_checkpoint(task)
            raise
        old_hash = task["input_hash"]
        task["questions"] = [dict(q, status="待回答") for q in questions]
        task["calculation_specs"] = copy.deepcopy(proposed["calculations"])
        task["data_need_specs"] = copy.deepcopy(proposed["data_requests"])
        task["requested_theses"] = any(q["kind"] not in DIRECT_KINDS for q in questions)
        task["plan_version"] += 1
        task["input_hash"] = input_fingerprint(task)
        task["nodes"] = self.plan(task)
        task["host_result"] = {"data_requests": proposed["data_requests"], "calculations": proposed["calculations"]}
        self._adopt_method_knowledge(task)
        state.pop("extraction", None)
        state.pop("analysis", None)
        state["plan"] = {"input_digest": planning_digest(task), "adopted_input_hash": task["input_hash"], "receipt": receipt, "proposed": proposed}
        task["revisions"].append({"action": "按原问题自主立题", "time": now(), "plan_version": task["plan_version"],
                                  "prior_input_hash": old_hash, "input_hash": task["input_hash"], "budget_reset": False})
        self._save_model_checkpoint(task)

    def _reading_artifact_ids(self, task, views, active=None):
        """Authorize current same-question atomic APIs before choosing text spans.

        A text-reading selection must not narrow an already acquired numeric
        source to PDF-only. The reader still owns exact scope, hashes and size.
        Supplementary rounds cannot import unrelated earlier task sources.
        """
        ids = [view['id'] for view in views]
        if task.get('mixed_atomic_api_reading_version') != 1:
            return ids
        questions = set(active['affected_questions'] if active else [q['id'] for q in task['questions']])
        records = {record['id']:record for record in original_sources(task)}
        permitted = None
        if active:
            requested = {need['id'] for need in active['proposal']['data_requests']}
            permitted = set(active['proposal']['artifact_ids']) | {record['id'] for record in records.values()
                if requested.intersection(record.get('data_need_ids',[]))}
        for view in extractable_source_views(task):
            if (view['kind']=='api' and view['id'] not in ids
                    and questions.intersection(records[view['id']].get('question_ids',[]))
                    and (permitted is None or view['id'] in permitted)):
                ids.append(view['id'])
        return ids

    def _select_model_reading(self, task, digest, reading_payload, artifact_ids):
        """One bounded re-selection for an explicitly rejected source locator.

        No ID substitution, new source, network retry, budget refund or change
        to evidence approval. A correction slot remains spent across resume.
        """
        state = task.setdefault('reasoning_state', {})
        rejections = state.setdefault('reading_rejections', [])
        correction_used = any(item.get('input_digest') == digest and item.get('correction_reserved')
                              for item in rejections)
        if correction_used:
            raise ReadingSelectionError('selection_correction_exhausted',
                '同一读取输入的重新选择额度已用完，保留原件、拒绝记录与原预算', 0)
        selected_questions = {q['id'] for q in reading_payload['questions']}
        feedback = None
        for attempt in range(2):
            self._active(task['id'], task['batch'])
            payload = copy.deepcopy(reading_payload)
            payload['budget'] = copy.deepcopy(task['ledger'])
            if feedback is not None:
                payload['selection_feedback'] = feedback
            before_dispatch = len(task['ledger']['dispatches'])
            proposed, receipt = model_object(self.model, task, 'read', payload, {'requests'})
            requests = proposed['requests']
            if not isinstance(requests, list) or any(not isinstance(r, dict) or
                    not isinstance(r.get('question_ids'), list) or
                    any(q not in selected_questions for q in r['question_ids']) for r in requests):
                raise ContractError('读取选择越过本轮核验问题范围')
            try:
                bundle = read_sources(task, requests, reading_binding(task), artifact_ids)
            except ReadingSelectionError as error:
                rejection = {'input_digest': digest, 'output_hash': fingerprint(proposed), 'receipt': receipt,
                    'issue': copy.deepcopy(error.diagnostic), 'correction_reserved': False,
                    'automatic_identifier_repair': False}
                rejections.append(rejection)
                events = task['ledger']['dispatches'][before_dispatch:]
                if len(events) == 1 and events[0].get('research_phase') == 'read':
                    events[0]['source_selection_review'] = copy.deepcopy(error.diagnostic)
                self._save_model_checkpoint(task)
                ledger = task['ledger']
                remaining = ledger['limits']['model_calls']-ledger['used']['model_calls']-ledger['reserve']['model_calls']
                seconds = ledger['limits']['seconds']-ledger['used']['seconds']-ledger['reserve']['seconds']
                if attempt or correction_used or remaining < 2 or seconds <= 0:
                    raise
                self._active(task['id'], task['batch'])
                rejection['correction_reserved'] = True
                self._save_model_checkpoint(task)
                feedback = {'version': 1, 'issue': copy.deepcopy(error.diagnostic),
                    'rule': '仅重新选择本次source_catalog内的读取位置。绑定原样复制request_binding，同原件合并成一项。'
                            '不改问题、来源、截止时点、核验门槛或预算，不提取事实，不把错误提案当依据。',
                    'remaining_corrections': 0}
                correction_used = True
                continue
            events = task['ledger']['dispatches'][before_dispatch:]
            if len(events) == 1 and events[0].get('research_phase') == 'read':
                events[0]['source_selection_review'] = {'version': 1, 'selection_accepted': True,
                    'financial_approval': False}
            state['reading'] = {'input_digest': digest, 'bundle': bundle, 'receipt': receipt,
                                'selection_correction_used': feedback is not None}
            self._save_model_checkpoint(task)
            return bundle

    def _extract_model_sources(self, task):
        state = task.setdefault("reasoning_state", {})
        active = self._active_supplement(task)
        # Identity conversion and announcement directories are navigation
        # inputs, not body evidence. The planner still receives them, but an
        # extraction call cannot make their titles into research facts.
        views = extractable_source_views(task)
        if active:
            requested = {n["id"] for n in active["proposal"]["data_requests"]}
            permitted = set(active["proposal"]["artifact_ids"]) | {r["id"] for r in original_sources(task) if requested.intersection(r.get("data_need_ids", []))}
            views = [v for v in views if v["id"] in permitted]
        feedback = [{key: copy.deepcopy(item[key]) for key in
                     ("id", "artifact_id", "question_ids", "rejection", "source_check", "applicability_check") if key in item}
                    for item in task.get("rejected_evidence", [])
                    if active and item.get("artifact_id") in {view["id"] for view in views}
                    and set(item.get("question_ids", [])).intersection(active["affected_questions"])][:12]
        source_context = context_sources(task, [v['id'] for v in views])
        selected_originals = source_context['mode'] == 'selected' and any(v['kind'] in ('material', 'filing') for v in views)
        extraction_input = {"input_hash": task["input_hash"], "sources": views,
                            "reader_version": reader_version_for(task) if selected_originals else 1}
        if task.get("calculation_specs") or any(question.get("method_needs") for question in task["questions"]):
            extraction_input["calculation_input_contract"] = calculation_input_requirements(task)
        # Re-read unfinished financial extractions once under the new literal
        # amount policy. Do not expire unrelated API/non-financial checkpoints
        # or rewrite a frozen report. The original ledger is still enforced.
        planned_methods = {method for question in task["questions"] for method in question.get("method_needs", [])}
        planned_methods.update(item["method"] for item in task.get("calculation_specs", []))
        if planned_methods.intersection(FINANCIAL_PERIOD_METHODS) and any(view["kind"] in ("material", "filing") for view in views):
            extraction_input["financial_metric_binding_version"] = FINANCIAL_METRIC_BINDING_VERSION
        if task.get("intent") != DOCUMENT_CONTENT_INTENT and any(
                view.get("kind") == "api" and str(view.get("source")) + "." + str(view.get("operation")) in FIELD_REGISTRY
                for view in views):
            extraction_input["api_candidate_version"] = API_EXTRACTION_GUIDANCE_VERSION
            if task.get('api_cell_selection_encoding') == 1:
                extraction_input['api_cell_selection_encoding'] = 1
                extraction_input['api_cell_choice_coverage_version'] = API_CELL_CHOICE_COVERAGE_VERSION
            # An advanced field definition changes the admissible units and
            # per-table context. Reuse neither its old extraction checkpoint
            # nor an old numeric proposal; unrelated version-1 sources keep
            # their existing cache key. Frozen deliveries are not rewritten.
            changed_definitions = {
                operation: FIELD_REGISTRY[operation]["version"]
                for operation in {str(view.get("source")) + "." + str(view.get("operation"))
                                  for view in views if view.get("kind") == "api"}
                if operation in FIELD_REGISTRY and FIELD_REGISTRY[operation]["version"] > 1
            }
            if changed_definitions:
                extraction_input["field_definition_versions"] = changed_definitions
        if active:
            extraction_input["supplement_binding"] = {"round": active["round"],
                "affected_questions": active["affected_questions"], "proposal_digest": fingerprint(active["proposal"])}
            extraction_input["review_feedback"] = feedback
        if task.get('financial_period_need_compatibility_version') == 1:
            extraction_input['financial_period_need_compatibility_version'] = 1
        if task.get('independent_financial_selections_version') == 1:
            extraction_input['independent_financial_selections_version'] = 1
        if task.get('financial_address_isolation_version') == 1:
            extraction_input['financial_address_isolation_version'] = 1
        if task.get('financial_amount_selection_version') == 1:
            extraction_input['financial_amount_selection_version'] = 1
        if 'financial_data_product_version' in task:
            extraction_input['financial_data_product_version'] = task['financial_data_product_version']
        if 'financial_amount_reference_version' in task:
            extraction_input['financial_amount_reference_version'] = task['financial_amount_reference_version']
        if 'financial_statement_details_version' in task:
            extraction_input['financial_statement_details_version'] = task['financial_statement_details_version']
        if 'table_geometry_projection_version' in task:
            extraction_input['table_geometry_projection_version'] = task['table_geometry_projection_version']
        digest = fingerprint(extraction_input)
        cached_extraction = state.get('extraction', {}).get('input_digest') == digest
        if cached_extraction and not (self._initial_fee_completion_available(task, task['host_result'])
                or self._required_numeric_completion_available(task,task['host_result'])):
            return task["host_result"]
        recovery = state.get('extraction_submission_recovery', {})
        if (recovery.get('input_digest') == digest and recovery.get('status') in ('已派发', '未采用')):
            self._gap(task, '本轮提取重新提交未形成可采用结果，保留原件和已派发用量，不重复提交。',
                      [q['id'] for q in task['questions']], category='evidence_extraction')
            return dict(task['host_result'], evidence_specs=[])
        if not views:
            return task["host_result"]
        if not self._reasoning_available(task):
            task["gaps"].append("原件提取模型额度不足，不能占用交付预留或自签核验。")
            return dict(task["host_result"], evidence_specs=[])
        bundle = None
        if source_context["mode"] == "selected":
            previous = state.get("reading", {})
            if previous.get("input_digest") == digest:
                bundle = previous["bundle"]
                validate_reading(task, bundle)
            else:
                if not any(v["kind"] in ("material", "filing") for v in views):
                    task["gaps"].append("接口快照完整结构超过上下文预算，不能截断列、移位或按文字原件读取；需更小的明确查询范围。")
                    return dict(task["host_result"], evidence_specs=[])
                reading_questions = [q['id'] for q in task['questions']
                    if not active or q['id'] in active['affected_questions']]
                from .source_reader import acquisition_reading_requests
                navigation = (acquisition_reading_requests(task, source_context['catalog'], reading_questions,
                    artifact_ids=[view['id'] for view in views])
                    if not active else None)
                reading_execution='declared_acquisition_reading'
                if navigation is None:
                    navigation=(registered_reading_requests(task, source_context['catalog'], reading_questions)
                        if not active else None)
                    reading_execution='registered_financial_navigation'
                remaining = task["ledger"]["limits"]["model_calls"] - task["ledger"]["used"]["model_calls"] - task["ledger"]["reserve"]["model_calls"]
                if remaining < (1 if navigation is not None else 2):
                    task["gaps"].append("长原件读取与提取额度不足，原件完整保留，不占交付预留。")
                    return dict(task["host_result"], evidence_specs=[])
                reading_payload = {"question": task["question"], "intent": task["intent"], "objects": task["objects"], "cutoff": task["cutoff"],
                    "questions": [q for q in question_specs(task) if not active or q["id"] in active["affected_questions"]],
                    "source_catalog": source_context["catalog"], "budget": task["ledger"]}
                if navigation is None:
                    bundle = self._select_model_reading(task, digest, reading_payload, self._reading_artifact_ids(task,views,active))
                else:
                    bundle = read_sources(task, navigation, reading_binding(task), self._reading_artifact_ids(task,views,active))
                    state['reading'] = dict(input_digest=digest, bundle=bundle, selection_correction_used=False,
                        receipt=dict(phase='read', execution=reading_execution,
                            version=1, model_calls=0, source_approval=False, financial_approval=False))
                    self._save_model_checkpoint(task)
            views = bundle["views"]
            for limitation in bundle["limitations"]:
                if limitation not in task["optional_findings"]:
                    task["optional_findings"].append(limitation)
            if not views:
                task["gaps"].append("本轮长原件读取未得到可提取片段，原件保留，不以未命中声称事实不存在。")
                self._save_model_checkpoint(task)
                return dict(task["host_result"], evidence_specs=[])
        if task.get('mixed_atomic_api_reading_version') == 1:
            # Recheck after the reading selection as well. A prior text-only
            # checkpoint must not hide a now-readable same-question API.
            allowed = self._reading_artifact_ids(task, views, active)
            if set(allowed) - {view['id'] for view in views}:
                if bundle:
                    bundle = read_sources(task,bundle['selection'],reading_binding(task),allowed,
                        reader_version=bundle['reader_version'])
                    validate_reading(task,bundle)
                    state['reading']['bundle'] = bundle
                    state['reading']['atomic_api_scope_rechecked'] = True
                    views = bundle['views']
                    self._save_model_checkpoint(task)
                else:
                    refreshed = context_sources(task,allowed)
                    if refreshed['mode'] != 'full':
                        self._gap(task,'完整API快照超出本轮上下文预算，未截断后继续提取。',
                            [q['id'] for q in task['questions']],category='evidence_extraction')
                        return dict(task['host_result'],evidence_specs=[])
                    views = refreshed['views']
        # Extraction interprets acquired originals, not the tool catalogue.
        # Keep only their registered field descriptions. This does not change
        # validation or invalidate accepted evidence/checkpoints: the complete
        # registry still governs acquisition and independent evidence review.
        acquired_operations = {str(view.get("source")) + "." + str(view.get("operation"))
                               for view in views if view.get("kind") == "api"}
        extraction_registry = {name: copy.deepcopy(FIELD_REGISTRY[name])
                               for name in sorted(acquired_operations) if name in FIELD_REGISTRY}
        if 'ifind.basic_data_service' in acquired_operations:
            from .evidence import api_calculation_context_contract
            actual = [api_calculation_context_contract(record) for record in task['source_records']
                if record['id'] in {view['id'] for view in views}
                and record.get('source') == 'ifind' and record.get('operation') == 'basic_data_service']
            extraction_registry['ifind.basic_data_service'] = dict(version=1,
                fields={field: {'unit': info['unit'], 'metric': info['metric']}
                    for contract in actual for field, info in contract.get('fields', {}).items()},
                limitation='仅限本次原件的已登记单元。使用sources中的cell_choices并明确适用性，不扩大主体、期间或历史用途。')
        payload = {"question": task["question"], "intent": task["intent"], "objects": task["objects"], "cutoff": task["cutoff"],
                   "questions": [q for q in question_specs(task) if not active or q["id"] in active["affected_questions"]],
                   "calculations": task["calculation_specs"],
                   "evidence_contract": evidence_contract(task, views),
                   "field_registry": extraction_registry, "untrusted_sources": views,
                   "source_reading": {"reading_hash": bundle["reading_hash"], "coverage": bundle["coverage"],
                                      "limitations": bundle["limitations"]} if bundle else None}
        # Expose the small conditional submission contract at the phase entry.
        # The complete shared grammar stays available to App and host Skill;
        # no values, source approvals or input identities are filled here.
        payload["calculation_input_targets"] = copy.deepcopy(
            payload["evidence_contract"]["calculation_input_requirements"]["proposal_requirements"])
        if task.get('initial_fee_input_completion_version') == 1:
            from .output_schema import fee_context_mapping_enabled
            if fee_context_mapping_enabled(task):
                payload['unbound_calculation_input_requirements'] = copy.deepcopy(
                    payload['evidence_contract']['calculation_input_requirements']['unbound_requirements'])
        if cached_extraction:
            return self._complete_initial_fee_inputs(task, payload, digest, bundle)
        if active:
            payload["review_feedback"] = feedback
            payload["review_feedback_contract"] = {"version": 1,
                "rule": "反馈属于上次拒绝原因，不是正确答案。仅重新阅读本轮原件与登记问题，生成新的提案；不得复制批准标签、伪造时间、扩大问题用途或覆盖已通过证据。"}
        proposed = receipt = None
        before_extract = len(task["ledger"].get("dispatches", []))
        try:
            from .reasoning import ModelContextCapacityError
            try:
                proposed, receipt = model_object(self.model, task, "extract", payload, {"evidence_specs"})
            except ModelContextCapacityError:
                proposed,receipt=self._extract_source_batches(task,payload,digest,bundle)
        except ContractError as error:
            self._active(task["id"], task["batch"])
            recovered = self._recover_completed_extraction_format(task, payload, digest,
                failed_dispatch_index=before_extract)
            if recovered is None:
                self._gap(task, '材料分析未完成：' + self._extraction_failure_message(task, digest, error),
                          [q['id'] for q in payload['questions']],
                          category='evidence_extraction')
                if hasattr(error, 'rejected_extraction_specs'):
                    state['rejected_extraction'] = {'input_digest':digest,
                        'specification_accepted':False,'automatic_repair':False,
                        'issue':copy.deepcopy(error.diagnostic),
                        'proposed_specs':copy.deepcopy(error.rejected_extraction_specs)}
                    self._save_model_checkpoint(task)
                return dict(task["host_result"], evidence_specs=[])
            proposed, receipt = recovered
            before_extract = len(task['ledger']['dispatches']) - 1
        from .reasoning import ExtractionFormatError
        if receipt.get('financial_reference_rejections'):
            state.setdefault('financial_reference_rejections', []).append({
                'input_digest':digest, 'task_id':task['id'], 'research_version':task['version'],
                'batch':task['batch'], 'input_hash':task['input_hash'], 'evidence_approved':False,
                'items':copy.deepcopy(receipt['financial_reference_rejections'])})
            self._save_model_checkpoint(task)
        self._retain_original_selection_diagnostics(task,receipt,digest)
        if (type(task.get('extraction_read_isolation_version')) is int
                and task['extraction_read_isolation_version'] == 2):
            submitted = proposed.get('evidence_specs')
            if not isinstance(submitted, list) or len(submitted) > 1000:
                raise ContractError('模型提取规格须为列表，最多1000项')
            from collections import Counter
            # Count before validation: no duplicate can win by arriving first.
            # Compact selections use the same final candidate IDs as expanded
            # specifications, including explicit groups of API selections.
            def proposal_ids(spec):
                if not isinstance(spec, dict):
                    return []
                values = [spec.get('id'), spec.get('api_cell_selection')]
                if isinstance(spec.get('api_cell_selections'), list):
                    values += spec['api_cell_selections']
                return [value for value in values if isinstance(value, str) and value]
            counts = Counter(ident for spec in submitted for ident in proposal_ids(spec))
            existing_ids = {item['id'] for item in task.get('evidence', []) if isinstance(item, dict) and isinstance(item.get('id'), str)}
            retained, rejected = [], []
            for index, spec in enumerate(submitted):
                try:
                    identifiers = proposal_ids(spec)
                    if not identifiers:
                        raise ContractError('提案缺少明确证据或单元选择编号')
                    if any(counts[ident] > 1 or ident in existing_ids for ident in identifiers):
                        raise ContractError('重复证据编号全部拒绝，不覆盖已核验证据或采用首项')
                    validate_model_specs(task, [spec], schema_only=True)
                    if bundle:
                        validate_read_specs(task, [spec], bundle)
                except ContractError as error:
                    rejected.append(dict(index=index, spec=copy.deepcopy(spec), spec_hash=fingerprint(spec),
                        reason=str(error), issue=copy.deepcopy(getattr(error, 'diagnostic', {
                            'version':1, 'code':'proposal_constraints_rejected', 'evidence_approved':False}))))
                else:
                    retained.append(spec)
            if rejected:
                self._active(task['id'], task['batch'])
                original_hash = fingerprint(proposed)
                state['rejected_extraction'] = dict(input_digest=digest, receipt=copy.deepcopy(receipt),
                    output_hash=original_hash, specification_accepted=False, automatic_repair=False,
                    isolation_version=2, proposed_specs=copy.deepcopy(submitted), rejected_items=rejected,
                    retained_spec_hashes=[fingerprint(spec) for spec in retained], evidence_approved=False)
                questions = {q['id'] for q in payload['questions']}
                for rejection in rejected:
                    raw = rejection['spec']
                    linked = raw.get('question_ids') if isinstance(raw, dict) else None
                    affected = ([ident for ident in linked if isinstance(ident, str) and ident in questions]
                                if isinstance(linked, list) else [])
                    self._gap(task, '材料分析提案未通过核验，未采用该项证据：' + rejection['reason'],
                              affected or sorted(questions), category='evidence_extraction')
                proposed = dict(proposed, evidence_specs=retained)
                receipt = dict(receipt, extraction_read_isolation=dict(version=2,
                    original_output_hash=original_hash, retained_output_hash=fingerprint(proposed),
                    rejected_count=len(rejected), retained_count=len(retained), evidence_approved=False))
                if not retained:
                    record_extraction_validation(self.model, task, before_extract, receipt, False,
                                                 'proposal_constraints_rejected')
                    self._save_model_checkpoint(task)
                    return dict(task['host_result'], evidence_specs=[])
                self._save_model_checkpoint(task)
        try:
            validate_model_specs(task, proposed["evidence_specs"], schema_only=True)
            if bundle:
                validate_read_specs(task, proposed["evidence_specs"], bundle)
        except ExtractionFormatError as error:
            # A spent call or exhausted research budget must not steal delivery
            # reserve. No source is approved on this restricted path.
            self._active(task["id"], task["batch"])
            record_extraction_validation(self.model, task, before_extract, receipt, False,
                                         "calculation_context_shape")
            self._gap(task, '材料分析未完成：' + str(error), [q['id'] for q in payload['questions']],
                      category='evidence_extraction')
            # Only explicit shape errors may become delivery gaps. Unknown
            # approval fields and unread-source references remain hard errors.
            state["rejected_extraction"] = {"input_digest": digest,
                "receipt": receipt, "output_hash": fingerprint(proposed),
                "reason": str(error), "specification_accepted": False,
                "automatic_repair": False}
            self._save_model_checkpoint(task)
            return dict(task["host_result"], evidence_specs=[])
        except ContractError as error:
            self._active(task["id"], task["batch"])
            record_extraction_validation(self.model, task, before_extract, receipt, False,
                                         "proposal_constraints_rejected")
            state['rejected_extraction'] = {'input_digest': digest, 'receipt': receipt,
                'output_hash': fingerprint(proposed), 'specification_accepted': False,
                'automatic_repair': False, 'issue': copy.deepcopy(getattr(error,'diagnostic',{
                    'version':1,'code':'proposal_constraints_rejected','evidence_approved':False})),
                'reason':str(error),'proposed_specs':copy.deepcopy(proposed['evidence_specs'])}
            if (type(task.get('extraction_read_isolation_version')) is int
                    and task['extraction_read_isolation_version'] == 1):
                # Reject the whole untrusted proposal batch. Existing verified
                # evidence remains intact; no date, source or value is repaired.
                # Only contract failures from the two validators above reach
                # this handler. Storage/system failures still propagate.
                self._gap(task, '材料分析提案未通过核验，未采用本批证据：' + str(error),
                          [q['id'] for q in payload['questions']], category='evidence_extraction')
                self._save_model_checkpoint(task)
                return dict(task['host_result'], evidence_specs=[])
            self._save_model_checkpoint(task)
            raise
        record_extraction_validation(self.model, task, before_extract, receipt, True)
        if active:
            active["evidence_specs"] = copy.deepcopy(proposed["evidence_specs"])
            task["host_result"].setdefault("evidence_specs", []).extend(copy.deepcopy(proposed["evidence_specs"]))
        else:
            task["host_result"].update(proposed)
        state["extraction"] = {"input_digest": digest, "receipt": receipt}
        if task.get('initial_fee_input_completion_version') == 1:
            state['extraction']['evidence_specs_hash'] = fingerprint(proposed['evidence_specs'])
        if task.get('evidence_gap_lifecycle_version') == 1:
            state['extraction'].update(accepted_dispatch=before_extract,
                                      question_ids=[q['id'] for q in payload['questions']])
        self._save_model_checkpoint(task)
        return self._complete_initial_fee_inputs(task, payload, digest, bundle)

    def _retain_original_selection_diagnostics(self,task,receipt,digest):
        diagnostic=receipt.get('original_selection_rejections')
        if diagnostic is None:return
        from .output_schema import independent_original_selections, independent_financial_selections, original_requirement_isolation
        protocol = receipt.get('output_protocol')
        enabled = ((protocol == 'strict_original_spans_v1' and independent_original_selections(task))
                   or (protocol == 'strict_original_batch_v1' and independent_financial_selections(task)))
        if diagnostic.get('version') == 2 and not original_requirement_isolation(task):
            raise ContractError('本任务未登记需求容器隔离，不能接收新版本诊断')
        if (diagnostic.get('version') == 3 and
                (protocol != 'strict_original_batch_v1'
                 or type(task.get('financial_address_isolation_version')) is not int
                 or task['financial_address_isolation_version'] != 1)):
            raise ContractError('详细选择诊断未登记到当前财务地址隔离契约')
        if (not isinstance(diagnostic,dict) or not enabled or receipt.get('phase')!='extract'
                or any(receipt.get(key)!=task[field] or type(receipt.get(key)) is not type(task[field]) for key,field in
                    [('task_id','id'),('research_version','version'),('batch','batch'),('input_hash','input_hash')])
                or diagnostic.get('schema_hash')!=receipt.get('output_schema_hash')):
            raise ContractError('原文逐项诊断不属于当前任务、版本或提取契约')
        state=task.setdefault('reasoning_state',{})
        record=dict(input_digest=digest,task_id=task['id'],research_version=task['version'],
            batch=task['batch'],input_hash=task['input_hash'],output_hash=receipt['output_hash'],
            diagnostic=copy.deepcopy(diagnostic))
        records=state.setdefault('original_selection_rejections',[])
        if record in records:return
        records.append(record)
        # A failed redundant selector is not a new business obligation. The
        # existing evidence-coverage gate still decides which answers are ready.
        self._gap(task,'本批部分原文选择未通过，已单独记录；独立有效项继续核验，缺项不代表资料不存在。',
                  [],category='selection_rejection')
        self._save_model_checkpoint(task)

    def _extract_source_batches(self,task,payload,digest,bundle):
        """Process all acquired originals inside the same budget and reading.

        Only a known pre-dispatch capacity rejection enters this path. Each
        full source view is retained, and completed outputs have persistent
        checkpoints. An unknown/failed dispatch is not retried on resume.
        """
        from .reasoning import ModelContextCapacityError,RULES_VERSION
        views=payload.get('untrusted_sources',[])
        if (task.get('source_extraction_batches_version')!=1 or len(views)<2
                or any(v.get('kind') not in ('filing','material') for v in views)):
            raise ModelContextCapacityError('必要材料无法在当前提取路径完整处理，保留全部原件和缺口')
        state=task.setdefault('reasoning_state',{})
        batches=state.get('source_extraction_batches')
        if batches is None or batches.get('input_digest')!=digest:
            batches=dict(version=1,input_digest=digest,view_ids=[v['id'] for v in views],items={},active=None)
            state['source_extraction_batches']=batches
        if batches.get('view_ids')!=[v['id'] for v in views]:
            raise ContractError('提取批次的原件顺序或范围已变化，不沿用旧产物')
        outstanding=sum(v['id'] not in batches['items'] for v in views)
        remaining=task['ledger']['limits']['model_calls']-task['ledger']['used']['model_calls']-task['ledger']['reserve']['model_calls']
        if remaining<outstanding+2:
            raise ContractError('完整原件分批提取及后续分析复核额度不足，未新增派发')
        combined=[];receipts=[];ids=set()
        for index,view in enumerate(views,1):
            self._active(task['id'],task['batch'])
            entry=batches['items'].get(view['id'])
            if entry is not None and entry.get('status')!='已提交':
                self._gap(task,'第'+str(index)+'份原件已有失败或未确认提取，保留检查点和用量，不自动重试。',
                    [q['id'] for q in payload['questions']],category='evidence_extraction')
                continue
            if entry is None:
                entry=dict(status='已派发',id_prefix='batch'+str(index)+'_',view_hash=fingerprint(view),
                    task_id=task['id'],research_version=task['version'],input_hash=task['input_hash'])
                batches['items'][view['id']]=entry;batches['active']=dict(id_prefix=entry['id_prefix'])
                self._save_model_checkpoint(task)
                single=copy.deepcopy(payload);single['untrusted_sources']=[view]
                single['evidence_contract']=evidence_contract(task,[view])
                single['calculation_input_targets']=copy.deepcopy(single['evidence_contract']['calculation_input_requirements']['proposal_requirements'])
                before=len(task['ledger']['dispatches'])
                try:
                    proposed,receipt=model_object(self.model,task,'extract',single,{'evidence_specs'})
                    self._retain_original_selection_diagnostics(task,receipt,fingerprint(single))
                    validate_model_specs(task,proposed['evidence_specs'],schema_only=True)
                    if bundle:validate_read_specs(task,proposed['evidence_specs'],bundle)
                    if any(not e['id'].startswith(entry['id_prefix']) for e in proposed['evidence_specs']):
                        raise ContractError('分批证据标识未使用本批空间，不自动重命名')
                    record_extraction_validation(self.model,task,before,receipt,True)
                    entry.update(status='已提交',evidence_specs=copy.deepcopy(proposed['evidence_specs']),
                        output_hash=fingerprint(proposed['evidence_specs']),receipt=receipt)
                except ContractError:
                    entry['status']='未采用'
                    self._active(task['id'],task['batch'])
                    self._gap(task,'第'+str(index)+'份原件未形成有效提取；保留原件、失败与用量，不自动重试。',
                        [q['id'] for q in payload['questions']],category='evidence_extraction')
                finally:
                    batches['active']=None;self._save_model_checkpoint(task)
            if entry.get('status')!='已提交':continue
            if (entry['view_hash']!=fingerprint(view) or entry['task_id']!=task['id']
                    or entry['research_version']!=task['version'] or entry['input_hash']!=task['input_hash']
                    or entry['output_hash']!=fingerprint(entry['evidence_specs'])):
                raise ContractError('原件提取检查点不属于当前输入，不继承或补造')
            for spec in entry['evidence_specs']:
                if spec['id'] in ids:raise ContractError('分批证据标识重复，未覆盖或自动改名')
                ids.add(spec['id']);combined.append(copy.deepcopy(spec))
            receipts.append(copy.deepcopy(entry['receipt']))
        result=dict(evidence_specs=combined)
        return result,dict(phase='extract',rules_version=RULES_VERSION,task_id=task['id'],research_version=task['version'],
            batch=task['batch'],input_hash=task['input_hash'],context_hash=fingerprint(payload),output_hash=fingerprint(result),
            execution='source_extraction_batches',source_receipts=receipts,
            all_source_views_processed=all(e.get('status')=='已提交' for e in batches['items'].values())
                and len(batches['items'])==len(views))

    @staticmethod
    def _unpaired_fee_input_groups(specs):
        """Describe incomplete comparable proposals, never approve or fill them.

        A fee method needs two distinct fund/share units under one actual
        metric, period and basis. Counting every number together would mistake
        a complete custody pair for the missing management-fee counterpart.
        """
        groups = {}
        for spec in specs:
            if not isinstance(spec, dict) or spec.get('raw_value') is None:
                continue
            values = spec.get('calculation_context', {})
            fields = ('metric', 'period', 'fee_basis', 'rate_period', 'fund_id', 'share_class')
            if (not isinstance(values, dict)
                    or any(not isinstance(values.get(field), str) or not values[field] for field in fields)
                    or values['metric'] not in ('management_fee', 'custody_fee')):
                continue
            key = tuple(values[field] for field in fields[:4])
            group = groups.setdefault(key, dict(context={field: values[field] for field in fields[:4]},
                                                observed_input_ids=[], units=set()))
            group['observed_input_ids'].append(spec['id'])
            group['units'].add((values['fund_id'], values['share_class']))
        return [dict(context=group['context'], observed_input_ids=group['observed_input_ids'])
                for group in groups.values() if len(group['units']) < 2]

    @staticmethod
    def _initial_fee_completion_available(task, proposed):
        """One initial numeric pass, never reopening or repairing evidence.

        Four remaining calls cover this pass, deferred binding, analysis and
        independent delivery review. Existing numbers/explicit plans retain
        their normal path; no source or financial judgement is approved here.
        """
        from .output_schema import fee_context_mapping_enabled
        state = task.get('reasoning_state', {})
        specs = proposed.get('evidence_specs', []) if isinstance(proposed, dict) else []
        valid_specs = isinstance(specs, list) and bool(specs) and all(isinstance(item, dict) for item in specs)
        text_only = valid_specs and all(item.get('value') is None and item.get('raw_value') is None for item in specs)
        partial = (valid_specs and type(task.get('partial_fee_input_completion_version')) is int
                   and task['partial_fee_input_completion_version'] == 1
                   and bool(ResearchAgent._unpaired_fee_input_groups(specs)))
        return (type(task.get('initial_fee_input_completion_version')) is int
            and task['initial_fee_input_completion_version'] == 1
            and fee_context_mapping_enabled(task) and argument_support.enabled(task)
            and task.get('run_mode') == 'model' and task.get('status') == '运行中'
            and not task.get('delivery') and not task.get('pending_content')
            and not task.get('calculation_specs') and 'fee_input_completion' not in state
            and 'extraction_submission_recovery' not in state
            and not task.get('active_supplement')
            and (text_only or partial)
            and state.get('extraction', {}).get('evidence_specs_hash') == fingerprint(specs)
            and task['ledger']['limits']['model_calls'] - task['ledger']['used']['model_calls'] >= 4
            and ResearchAgent._reasoning_available(task))

    @staticmethod
    def _required_numeric_completion_available(task,proposed,*,views=None):
        from .data_requirements import unproposed_numeric
        state=task.get('reasoning_state',{})
        specs=proposed.get('evidence_specs',[]) if isinstance(proposed,dict) else []
        available = (type(task.get('required_numeric_completion_version')) is int
            and task['required_numeric_completion_version']==1 and argument_support.enabled(task)
            and task.get('run_mode')=='model' and task.get('status')=='运行中'
            and not task.get('delivery') and not task.get('pending_content')
            and not task.get('calculation_specs') and not any(q.get('method_needs') for q in task['questions'])
            and not task.get('active_supplement') and not any(k in state for k in
                ('required_numeric_completion','fee_input_completion','extraction_submission_recovery'))
            and all(e.get('status')=='已提交' for e in state.get('source_extraction_batches',{}).get('items',{}).values())
            and isinstance(specs,list) and all(isinstance(s,dict) for s in specs)
            and state.get('extraction',{}).get('evidence_specs_hash')==fingerprint(specs)
            and task['ledger']['limits']['model_calls']-task['ledger']['used']['model_calls']>=4
            and ResearchAgent._reasoning_available(task))
        if not available: return False
        if views is None: return bool(unproposed_numeric(task, specs))
        from .extraction_feedback import completion_scope
        return bool(completion_scope(task, specs, views)['eligible'])

    def _complete_initial_fee_inputs(self, task, payload, digest, bundle):
        """Compatibility entry; one shared numeric-input completion owner."""
        return self._complete_initial_numeric_inputs(task,payload,digest,bundle)

    def _complete_initial_numeric_inputs(self, task, payload, digest, bundle):
        """Prepare missing numeric inputs from the same already-read originals.

        This stays inside initial extraction before evidence-node adoption.
        It neither acquires materials nor selects/executes calculations. A
        journaled unknown or failed attempt cannot be replayed after resume.
        """
        first = task['host_result']
        views = payload.get('untrusted_sources', [])
        legacy=self._initial_fee_completion_available(task,first)
        required=not legacy and self._required_numeric_completion_available(task,first,views=views)
        if not (legacy or required) or self._active_supplement(task):
            return first
        support = getattr(self.model, 'supports_structured_analysis', None)
        if (not callable(support) or support() is not True
                or not callable(getattr(self.model, 'reason_structured_original_spans', None))):
            return first
        if not views or any(view.get('kind') not in ('filing', 'material') for view in views):
            return first
        original_specs = copy.deepcopy(first['evidence_specs'])
        first_hash = fingerprint(original_specs)
        prefix = 'input_' + first_hash[:10] + '_'
        if any(str(spec.get('id', '')).startswith(prefix) for spec in original_specs):
            return first
        completion = dict(version=1, status='已派发', binding=context(task), input_digest=digest,
            views_hash=fingerprint(views), first_specs_hash=first_hash, id_prefix=prefix,
            automatic_repair=False, further_acquisition_allowed=False, financial_approval=False)
        if required:
            from .extraction_feedback import completion_scope
            scope = completion_scope(task, original_specs, views)
            completion.update(scope='required_numeric', missing_numeric_needs=scope['eligible'])
            if scope['feedback_hash'] is not None:
                completion.update(extraction_feedback_hash=scope['feedback_hash'],
                    all_missing_numeric_needs=scope['all_missing'], deferred_numeric_needs=scope['deferred'])
        task['reasoning_state']['required_numeric_completion' if required else 'fee_input_completion'] = completion
        self._save_model_checkpoint(task)
        before = len(task['ledger'].get('dispatches', []))
        proposed = receipt = None
        try:
            from .output_schema import original_span_output_schema
            numeric_schema = original_span_output_schema(task, views)
            if (numeric_schema is None or any(variant['properties']['kind']['enum'] != ['number']
                    for variant in numeric_schema['properties']['selections']['items']['anyOf'])):
                raise ContractError('当前原件没有本次数值输入专用选择契约，未派发模型')
            request_payload = copy.deepcopy(payload)
            request_payload['input_completion'] = dict(version=1, id_prefix=prefix,
                first_specs_hash=first_hash, scope=('仅从同批已读原件补齐下列已登记必要数字，不复抄文字，不计算，不取数。'
                    if required else '仅从本次原件补齐已登记单项费率方法的数值输入，不复抄文字，不计算，不取数。'))
            if required:request_payload['input_completion']['missing_numeric_needs']=copy.deepcopy(completion['missing_numeric_needs'])
            if not required:
                request_payload['input_completion']['unbound_input_requirements'] = copy.deepcopy(
                    payload.get('evidence_contract', {}).get('calculation_input_requirements', {}).get('unbound_requirements', []))
            if not required and type(task.get('partial_fee_input_completion_version')) is int and task['partial_fee_input_completion_version'] == 1:
                request_payload['input_completion']['observed_numeric_inputs'] = [
                    {field: copy.deepcopy(spec.get(field)) for field in
                     ('id', 'artifact_id', 'raw_value', 'raw_unit', 'calculation_context')}
                    for spec in original_specs if spec.get('raw_value') is not None]
                request_payload['input_completion']['unpaired_context_groups'] = self._unpaired_fee_input_groups(original_specs)
            proposed, receipt = model_object(self.model, task, 'extract', request_payload, {'evidence_specs'})
            self._retain_original_selection_diagnostics(task,receipt,fingerprint(request_payload))
            self._active(task['id'], task['batch'])
            if fingerprint(task['host_result']['evidence_specs']) != first_hash:
                raise ContractError('数值输入提取期间初始提案已变化，未合并迟到结果')
            validate_model_specs(task, proposed['evidence_specs'], schema_only=True)
            if bundle:
                validate_read_specs(task, proposed['evidence_specs'], bundle)
            ids = {item['id'] for item in original_specs}
            for spec in proposed['evidence_specs']:
                if (not str(spec.get('id', '')).startswith(prefix) or spec['id'] in ids
                        or spec.get('raw_value') is None):
                    raise ContractError('数值输入须使用新的标识及实际原值，不覆盖初始文字提案')
                ids.add(spec['id'])
            record_extraction_validation(self.model, task, before, receipt, True)
            merged = original_specs + copy.deepcopy(proposed['evidence_specs'])
            task['host_result']['evidence_specs'] = merged
            completion.update(status='已提交' if proposed['evidence_specs'] else '未形成输入', receipt=receipt,
                numeric_specs_hash=fingerprint(proposed['evidence_specs']), combined_specs_hash=fingerprint(merged),
                added_ids=[item['id'] for item in proposed['evidence_specs']])
        except ContractError as error:
            self._active(task['id'], task['batch'])
            completion.update(status='未采用', reason=str(error))
            if receipt is not None:
                record_extraction_validation(self.model, task, before, receipt, False,
                                             'input_completion_constraints_rejected')
            if proposed is not None:
                completion.update(rejected_proposal=copy.deepcopy(proposed), evidence_specs_accepted=False)
            self._gap(task, '初次数值输入提取未完成，原文字与计算义务保留。',
                      [question['id'] for question in payload['questions']], category='evidence_extraction')
        self._save_model_checkpoint(task)
        return task['host_result']

    @staticmethod
    def _validated_data_needs(task, needs):
        """Validate detached batch syntax and currently known source authority.

        A malformed later entry must not consume an earlier valid request.
        This shared boundary applies to model acquisition and host input.
        Pending identity dependencies are checked again before execution;
        preflight never approves permissions, returned facts or calculations.
        """
        if not isinstance(needs, list) or len(needs) > 50:
            raise ContractError("数据需求必须为列表，最多50项")
        questions = {q["id"] for q in task["questions"]}
        seen = set()
        selected_originals = set()
        validated = []
        identity_pending = False
        for need in needs:
            if not isinstance(need, dict):
                raise ContractError("数据需求须为字段记录")
            if set(need) - {"id", "source", "operation", "parameters", "question_ids", "selection_reason", "reading_queries", "catalogue_alternative_ref"}:
                raise ContractError("数据需求含未登记字段；凭据不能进入任务")
            from .source_reader import validate_acquisition_reading_queries
            validate_acquisition_reading_queries(task,need)
            ident, selected = need.get("id"), need.get("question_ids")
            from .planning import IDENTIFIER
            if not isinstance(ident, str) or not IDENTIFIER.fullmatch(ident) or ident in seen or not isinstance(selected, list) or not selected or not all(isinstance(x, str) and x in questions for x in selected):
                raise ContractError("数据需求标识或对应研究问题无效")
            seen.add(ident)
            source = need.get("source", "ifind")
            if source != "ifind" and (not isinstance(need.get("selection_reason"), str) or not need["selection_reason"].strip()):
                raise ContractError("采用iFinD以外的来源必须记录原因，不静默换源")
            operation, params = need.get("operation"), need.get("parameters")
            from .evidence import validate_source_request
            # Identity is a runtime dependency, not a fabricated preflight fact.
            # Only a validated earlier lookup allows syntax-only inspection of
            # dependent requests; normal requests retain the full scope check.
            params = validate_source_request(task, source, operation, params,
                                             require_identity=not identity_pending)
            from .financial_sources import validate_financial_acquisition_role
            validate_financial_acquisition_role(task,need)
            from .source_requirements import validate_choice
            validate_choice(task,need)
            if source == 'ifind' and operation == 'filing_original':
                from .evidence import filing_selection_identity, validate_filing_selection_reason
                validate_filing_selection_reason(need)
                original = filing_selection_identity(task, params)
                if original in selected_originals:
                    raise ContractError('同一原件须合并对应问题，不能通过不同目录编号重复下载')
                selected_originals.add(original)
            if source == 'official' and operation == 'release_original':
                from .official_sources import validate_request
                original=validate_request(task,operation,params)['url']
                if original in selected_originals:
                    raise ContractError('同一官方原文须合并对应问题，不能重复下载')
                selected_originals.add(original)
            validated.append((copy.deepcopy(need), source, operation, copy.deepcopy(params)))
            if source == 'ifind' and operation == 'get_thscode':
                identity_pending = True
        return validated

    def _acquire_sources(self, task, needs, *, metadata_dependency=False, yield_on_readable_financial=False):
        """Execute a preflighted batch after checking each resolved dependency."""
        if yield_on_readable_financial:
            from .financial_sources import progressive_financial_source_ready
            # Validate the task policy before any provider I/O. This option is
            # scheduling only: it does not approve an original or its numbers.
            progressive_financial_source_ready(task, {})
        def issue(message, selected):
            if metadata_dependency:
                # Coverage is not a universal prerequisite for endpoint-only
                # questions. Keep failures in the ledger and bottom papers;
                # current coverage, not stale failure prose, guides analysis.
                task.setdefault('calendar_dependency_failures', []).append(dict(
                    message=message, question_ids=list(selected), batch=task['batch'], time=now()))
            else:
                self._gap(task, message, selected)

        prepared = self._validated_data_needs(task, needs)
        from .source_requirements import register
        if register(task,[need for need,source,operation,params in prepared]):
            self._save_model_checkpoint(task)
        processed_count = 0
        deferred_request_ids = []
        for index, (need, source, operation, params) in enumerate(prepared):
            if (yield_on_readable_financial and index > 0
                    and prepared[index - 1][2] == 'filing_original'):
                from .acquisition import initial_acquisition_coverage
                if progressive_financial_source_ready(task, initial_acquisition_coverage(task)):
                    # Keep every registered requirement. The caller records the
                    # unattempted requests and processes the actual retained
                    # material before deciding whether more collection helps.
                    deferred_request_ids = [item[0]['id'] for item in prepared[index:]]
                    break
            processed_count = index + 1
            ident, selected = need["id"], need["question_ids"]
            from .evidence import validate_source_request
            try:
                # Recheck actual retained mappings before cache aliases,
                # checkpoints, budget journal entries or provider I/O.
                params = validate_source_request(task, source, operation, params)
            except ContractError as error:
                issue(f"数据需求{ident}：请求依赖尚未核验，未派发。{error}", selected)
                continue
            expected = fingerprint({"source": source, "operation": operation, "parameters": params})
            records = task.setdefault("source_records", [])
            if source == 'ifind' and operation == 'filing_original':
                from .evidence import filing_selection_identity
                identity = filing_selection_identity(task, params)
                cached_original = next((x for x in records if x.get('kind') == 'filing'
                    and x.get('operation') == 'filing_original' and x.get('task_id') == task['id']
                    and x.get('research_version') == task['version'] and x.get('status') == '成功待核验'
                    and fingerprint(x.get('snapshot')) == x.get('hash')
                    and filing_selection_identity(task, x['parameters']) == identity), None)
                if cached_original is not None:
                    cached_original['data_need_ids'] = list(dict.fromkeys(cached_original.get('data_need_ids', []) + [ident]))
                    cached_original['question_ids'] = list(dict.fromkeys(cached_original.get('question_ids', []) + selected))
                    self._journal(task)
                    continue
            if source == 'official' and operation == 'release_original':
                from .official_sources import validate_request, validate_original
                selected_release=validate_request(task,operation,params)
                cached_official=next((x for x in records if x.get('source')=='official'
                    and x.get('operation')=='release_original' and x.get('task_id')==task['id']
                    and x.get('research_version')==task['version'] and x.get('status')=='成功待核验'
                    and x.get('snapshot',{}).get('official_origin',{}).get('url')==selected_release['url']
                    and fingerprint(x.get('snapshot'))==x.get('snapshot_hash')),None)
                if cached_official is not None:
                    validate_original(task,cached_official)
                    cached_official['data_need_ids']=list(dict.fromkeys(cached_official.get('data_need_ids',[])+[ident]))
                    cached_official['question_ids']=list(dict.fromkeys(cached_official.get('question_ids',[])+selected))
                    self._journal(task)
                    continue
            cached = next((x for x in records if x.get("kind") in ("api", "filing") and x.get("request_hash") == expected and
                           x.get("task_id") == task["id"] and x.get("research_version") == task["version"] and
                           x.get("status") == "成功待核验" and fingerprint(x.get("snapshot")) == x.get("hash") and
                           (task.get("research_mode") != "current" or x.get("kind") != "api" or
                            x.get("source", "") + "." + x.get("operation", "") not in FIELD_REGISTRY or
                            current_snapshot_reference(task, x) is not None)
                           and (x.get('source', '')+'.'+x.get('operation', '') not in IFIND_METADATA_REGISTRY
                                or current_query_reference(task, x) is not None)), None)
            if cached:
                # Aliases are explicit references, not repeated independent evidence.
                record_request_purpose(cached, need, selected)
                cached["data_need_ids"] = list(dict.fromkeys(cached.get("data_need_ids", []) + [ident]))
                cached["question_ids"] = list(dict.fromkeys(cached.get("question_ids", []) + selected))
                self._journal(task)
                continue
            if supplement_request_blocked(task, source, selected):
                self._gap(task, f"数据需求{ident}：来源或对应问题已遇到授权权限失败；保留已有证据与用量，不继续派发或换源规避。", selected)
                continue
            attempted = task.setdefault("source_attempts", {})
            if source == 'ifind' and operation == 'filing_original' and any(
                    digest != expected and attempt.get('original_identity') == identity
                    for digest, attempt in attempted.items()):
                self._gap(task, f"数据需求{ident}：同一原件已通过另一目录尝试，保留失败与用量，不改编号重试。", selected)
                continue
            dispatches = [d for d in task["ledger"]["dispatches"] if d.get("kind") == "data_requests" and d.get("fingerprint") == expected]
            previous = attempted.get(expected, {})
            retry_discarded = (previous.get("status") == "响应已返回但取消丢弃" and previous.get("discarded_batch") != task["batch"]
                               and len(dispatches) < 3)
            if (expected in attempted or dispatches) and not retry_discarded:
                issue(f"数据需求{ident}：本版本已经尝试，未获可复用响应；保留用量，不重复派发。", selected)
                continue
            try:
                attempted[expected] = {"id": ident, "source": source, "operation": operation, "status": "待响应", "time": now(),
                    "batch": task["batch"], "prior_dispatches": len(dispatches),
                    "recovery_reason": "用户恢复已取消任务；前次响应已返回但被丢弃，在同一请求三次总上限内重新取数" if retry_discarded else "首次登记"}
                if source == 'ifind' and operation == 'filing_original':
                    attempted[expected]['original_identity'] = identity
                self._save_model_checkpoint(task)
                raw = self.collector.external(task, source, operation, params, self.source_credentials.get(source, {}))
                raw.update(data_need_ids=[ident], question_ids=list(selected), selection_reason=need.get("selection_reason", "优先iFinD"))
                record_request_purpose(raw, need, selected)
                existing = next((item for item in records if item["id"] == raw["id"]), None)
                if existing is None:
                    records.append(raw)
                else:
                    existing.update(raw)
                attempted[expected]["status"] = "已留存"
                if operation == "get_thscode":
                    mappings = identity_resolutions(task)
                    task["identity_state"] = "；".join(
                        str(item.get("query", "输入")) + "：" + item["status"] +
                        ("→" + ",".join(item["codes"]) if item["status"] == "代码映射已核验" else "") for item in mappings)
                    if any(item["status"] == "代码映射已核验" for item in mappings):
                        task["identity_state"] += "；证券类型、业务暴露及历史身份仍需另行核验"
                if raw["status"] != "成功待核验":
                    issue(f"数据需求{ident}：{raw['status']}，不自动扩大时点或换源。", selected)
                self._journal(task)  # response checkpoint precedes any interpretation
            except ContractError as error:
                attempted[expected]["status"] = "失败或中断"
                issue(f"数据需求{ident}：{error}", selected)
                # Cancellation and stale batches stop the whole run. Missing or
                # unavailable independent data leaves other questions executable.
                self._active(task["id"], task["batch"])
                self._save_model_checkpoint(task)

        # A separate, budgeted calendar is a data dependency, not another
        # model research. Recursion terminates because calendar needs do not
        # contain a price operation. Normal cache/cancel/resume gates apply.
        from .market_calendar import dependency_requests
        calendars = dependency_requests(task, prepared[:processed_count])
        for offset in range(0, len(calendars), 50):
            self._acquire_sources(task, calendars[offset:offset + 50], metadata_dependency=True)
        return dict(deferred_request_ids=deferred_request_ids)

    @staticmethod
    def _missing_calculations(task, question_ids):
        """Check per-question obligations, never infer inputs or approve results."""
        outputs = task.get("calculations", [])
        completed_ids = {item["id"] for item in outputs}
        missing = {}
        for question in task["questions"]:
            ident = question["id"]
            if ident not in question_ids:
                continue
            completed_methods = {item["method"] for item in outputs if ident in item.get("question_ids", [])}
            methods = set(question.get("method_needs", [])) - completed_methods
            # Explicit executable requests also need their own result, even
            # when a host omitted method_needs or another same-method run exists.
            methods.update(spec["method"] for spec in task.get("calculation_specs", [])
                           if ident in spec.get("question_ids", [q["id"] for q in task["questions"]])
                           and (spec["id"] not in completed_ids if spec.get("id")
                                else spec["method"] not in completed_methods))
            if methods:
                missing[ident] = sorted(methods)
        return missing

    @staticmethod
    def _gap(task, message, question_ids=None, *, category=None, method_ids=None):
        blocking = question_ids is None or any(q["required"] and q["id"] in question_ids for q in task["questions"])
        lifecycle = type(task.get('evidence_gap_lifecycle_version')) is int and task['evidence_gap_lifecycle_version'] == 1
        if lifecycle and category == 'question_coverage':
            selected = set(question_ids or [q['id'] for q in task['questions']])
            retained, resolved = [], []
            for previous in task.get('gap_records', []):
                if (previous.get('category') == category and previous.get('task_id') == task['id']
                        and type(previous.get('research_version')) is int and previous['research_version'] == task['version']
                        and set(previous.get('question_ids', [])) == selected and previous['message'] != message):
                    resolved.append(dict(copy.deepcopy(previous), resolution=dict(task_id=task['id'],
                        research_version=task['version'], batch=task['batch'], input_hash=task['input_hash'],
                        reason='同一问题已重新核对覆盖范围，当前缺口另行保留')))
                else:
                    retained.append(previous)
            if resolved:
                ResearchAgent._archive_gap_resolutions(task, retained, resolved)
        task.setdefault("gaps" if blocking else "optional_findings", []).append(message)
        record = {"message": message, "question_ids": list(question_ids) if question_ids is not None else [q["id"] for q in task["questions"]], "required": blocking}
        if category is not None:
            record.update(category=category, task_id=task['id'], research_version=task['version'],
                          method_ids=list(method_ids or []))
        if lifecycle and category == 'evidence_extraction':
            record['failure_dispatch'] = len(task.get('ledger', {}).get('dispatches', [])) - 1
        if record not in task.setdefault("gap_records", []):
            task["gap_records"].append(record)

    @staticmethod
    def _archive_gap_resolutions(task, retained, resolved):
        removed = {g['message'] for g in resolved} - {g['message'] for g in retained}
        task['gaps'] = list(dict.fromkeys(message for message in task.get('gaps', []) if message not in removed))
        task['optional_findings'] = list(dict.fromkeys(message for message in task.get('optional_findings', []) if message not in removed))
        task['gap_records'] = retained
        task.setdefault('resolved_gap_records', []).extend(resolved)

    def _resolve_current_gaps(self, task):
        """Current unresolved state is not an append-only history of failures.

        Registered stage-state gaps may resolve before the core question does.
        Unknown text is never matched or hidden by keywords.
        Original messages and their resolution remain in the same-version log.
        Frozen deliveries are not touched by this lifecycle operation.
        """
        questions = {q['id']:q for q in task['questions']}
        missing = self._missing_calculations(task, list(questions))
        lifecycle = type(task.get('evidence_gap_lifecycle_version')) is int and task['evidence_gap_lifecycle_version'] == 1
        evidence_questions = set()
        if lifecycle and any(g.get('category') == 'evidence_absence' for g in task.get('gap_records', [])):
            from .contracts import validate_evidence
            for item in task.get('evidence', []):
                if (item.get('task_id') != task['id'] or item.get('research_version') != task['version']
                        or item.get('source_check') != '通过' or item.get('applicability_check') != '通过'):
                    continue
                try:
                    validate_evidence(item, task)
                except (ContractError, ValueError, KeyError, TypeError):
                    continue
                evidence_questions.update(item.get('question_ids', []))
        extraction = task.get('reasoning_state', {}).get('extraction', {})
        receipt = extraction.get('receipt', {})
        accepted_dispatch = extraction.get('accepted_dispatch')
        events = task.get('ledger', {}).get('dispatches', [])
        accepted_extraction = (lifecycle and type(accepted_dispatch) is int and 0 <= accepted_dispatch < len(events)
            and events[accepted_dispatch].get('research_phase') == 'extract'
            and events[accepted_dispatch].get('status') == '成功'
            and type(receipt.get('research_version')) is int and type(receipt.get('batch')) is int
            and events[accepted_dispatch].get('batch') == task['batch']
            and events[accepted_dispatch].get('output_contract', {}).get('shape_accepted') is True
            and all(receipt.get(key) == value for key, value in dict(task_id=task['id'],
                research_version=task['version'], batch=task['batch'], input_hash=task['input_hash'], phase='extract').items()))
        retained, resolved = [], []
        for record in task.get('gap_records', []):
            selected = record.get('question_ids', [])
            owned = ('category' not in record or record.get('task_id')==task['id']
                and type(record.get('research_version')) is int and record['research_version']==task['version'])
            answered = owned and bool(selected) and all(q in questions and questions[q]['status']=='已回答' for q in selected)
            calculated = (record.get('category')=='calculation' and record.get('task_id')==task['id']
                and type(record.get('research_version')) is int and record['research_version']==task['version']
                and bool(record.get('method_ids')) and bool(selected)
                and all(q in questions and not missing.get(q) and all(any(c['method']==method
                    and q in c.get('question_ids', []) and c.get('task_id')==task['id']
                    and type(c.get('research_version')) is int and c['research_version']==task['version']
                    for c in task['calculations']) for method in record['method_ids']) for q in selected))
            evidence_exists = (lifecycle and owned and record.get('category') == 'evidence_absence'
                               and bool(set(selected).intersection(evidence_questions)))
            extraction_completed = (accepted_extraction and owned and record.get('category') == 'evidence_extraction'
                and type(record.get('failure_dispatch')) is int and record['failure_dispatch'] < accepted_dispatch
                and bool(selected) and set(selected).issubset(extraction.get('question_ids', [])))
            if not answered and not calculated and not evidence_exists and not extraction_completed:
                retained.append(record)
                continue
            resolved.append(dict(copy.deepcopy(record), resolution={'task_id':task['id'],
                'research_version':task['version'], 'batch':task['batch'], 'input_hash':task['input_hash'],
                'reason':('必要计算已完成' if calculated else '关联问题已通过回答核验' if answered
                    else '已取得相关核验证据，问题充分性仍独立检查' if evidence_exists
                    else '后续提取格式已通过，来源和金融核验仍独立检查'),
                'calculation_ids':[c['id'] for c in task['calculations'] if set(selected).intersection(c.get('question_ids', []))],
                'answered_question_ids':[q for q in selected if questions[q]['status']=='已回答']}))
        ResearchAgent._archive_gap_resolutions(task, retained, resolved)

    def _answer_questions(self, task, selected, host_result, fixture):
        analysis_proposal = host_result
        proposed_answers = (host_result or {}).get('answers', {})
        host_result = argument_support.adoptable(task, host_result)
        task["numeric_integrity_contract"] = 1
        good_ids = {item["id"] for item in task["evidence"]}
        evidence_by_id = {item["id"]: item for item in task["evidence"]}
        answers = (host_result or {}).get("answers", {})
        if not isinstance(answers, dict):
            raise ContractError("问题回答必须按问题标识登记")
        by_id = {q["id"]: q for q in task["questions"]}
        if set(answers) - set(by_id):
            raise ContractError("回答引用未登记的问题")
        for q in task["questions"]:
            if q["id"] not in selected or (q["status"] == "已回答" and not argument_support.enabled(task)):
                continue
            if argument_support.enabled(task) and q.get('answer_scope') != 'general_definition':
                for field in ('answer','refs','calculation_refs','numeric_integrity','numeric_segments','research_coverage',
                              'provenance_refs','provenance_records','provenance_integrity'):
                    q.pop(field, None)
                if q['id'] in proposed_answers and q['id'] not in answers:
                    q['status'] = '未回答'
                    self._gap(task, '本问题的回答未通过论证复核，未采用该陈述；原始资料与登记计算保留。', [q['id']])
                    continue
            answer = answers.get(q["id"], {})
            if q.get('answer_scope') == 'general_definition':
                try:
                    bound = self.knowledge.definition_answer(task, q, answer if answer else None)
                except ContractError as error:
                    q['status'] = '未回答'
                    self._gap(task, '通用口径说明未通过：' + str(error), [q['id']])
                    continue
                q.update(status='已回答', **bound)
                continue
            if (not answer and task.get('run_mode') == 'model' and
                    task.get('reasoning_state', {}).get('analysis', {}).get('output_rejected') is True):
                q['status'] = '未回答'
                self._gap(task, '分析未完成，本问题尚无可采用回答；已取得的原件、证据与计算保留。',
                          [q['id']], category='analysis')
                continue
            missing_needs = []
            if task["intent"] == DOCUMENT_CONTENT_INTENT:
                items = [item for item in task["evidence"] if q["id"] in item.get("question_ids", []) and item.get("usage_scope") == DOCUMENT_CONTENT_SCOPE]
                expected = {"answer": document_content_answer(items), "refs": [item["id"] for item in items], "calculation_refs": []} if items else {}
                if answer and answer != expected:
                    raise ContractError("原件内容核对答案只能使用对应已核验原文，不能增加推断或投资建议")
                answer = expected
                missing_needs = missing_document_requirements(q, items)
            if fixture:
                answer = fixture.get("answers", {}).get(q["id"], {}) if not task["rejected_evidence"] else {}
            if not isinstance(answer, dict):
                raise ContractError("问题回答格式无效")
            if answer.get("task_id", task["id"]) != task["id"] or answer.get("research_version", task["version"]) != task["version"]:
                raise ContractError("问题回答不能引用其他任务或研究版本")
            refs, text = answer.get("refs", []), answer.get("answer")
            from .provenance import bind_query_references, query_requirements, QUERY_ANSWER
            try:
                lineage = bind_query_references(task, q, answer.get("provenance_refs", []))
            except ContractError as error:
                q["status"] = "未回答"
                self._gap(task, "查询引用未通过：" + str(error), [q["id"]])
                continue
            query_only = bool(query_requirements(q)) and len(query_requirements(q)) == len(q["evidence_needs"])
            calc_refs = answer.get("calculation_refs", [])
            calculations = {c["id"]: c for c in task["calculations"]}
            from .contracts import evidence_applies_to_answer, calculation_applies_to_answer
            valid_refs = isinstance(refs, list) and refs and all(isinstance(ref, str) and ref in good_ids and
                evidence_applies_to_answer(task, evidence_by_id[ref], q['id'], calc_refs) for ref in refs)
            if query_only and refs == [] and lineage["refs"] and not q.get("method_needs"):
                valid_refs = True
                expected = QUERY_ANSWER
                if text != expected or calc_refs or "numeric_segments" in answer:
                    q["status"] = "未回答"
                    self._gap(task, "独立查询核对只能采用实际留痕，不生成金融判断。", [q["id"]])
                    continue
            valid_calcs = isinstance(calc_refs, list) and all(isinstance(ref, str) and ref in calculations for ref in calc_refs)
            if valid_calcs and valid_refs:
                valid_calcs = all(calculation_applies_to_answer(task, calculations[ref], q['id']) and
                                  set(calculations[ref]["input_refs"]).issubset(refs) for ref in calc_refs)
            if task["intent"] != DOCUMENT_CONTENT_INTENT:
                missing_needs = missing_evidence_requirements(q, [evidence_by_id[ref] for ref in refs] if valid_refs else [],
                    lineage["covered_need_ids"], market_need_ids=calendar_need_ids(task, q))
            if q.get("evidence_needs"):
                cited = [evidence_by_id[ref] for ref in refs] if valid_refs else []
                q["evidence_coverage"] = {"version": 1, "task_id": task["id"], "research_version": task["version"],
                    "input_hash": task["input_hash"], "question_id": q["id"], "items": [
                        dict(need, refs=[item["id"] for item in cited if {need["id"], q["id"] + ":" + need["id"]}.intersection(
                            item.get("applicability_review", {}).get("evidence_need_ids", {}).get(q["id"], []))])
                        for need in evidence_requirements(q)]}
                if lineage["refs"]:
                    for item in q["evidence_coverage"]["items"]:
                        item["provenance_ids"] = [ref["artifact_id"] for ref in lineage["refs"]
                                                  if item["id"] in ref["evidence_need_ids"]]
                calendar_basis = calendar_scope_basis(task)
                if calendar_basis:
                    need_ids = calendar_basis['covered_need_ids'].get(q['id'], [])
                    source_ids = sorted({panel['calendar_ref']['artifact_id'] for panel in calendar_basis['panels']
                        if q['id'] in panel['question_ids'] and panel['calendar_source']})
                    for item in q['evidence_coverage']['items']:
                        if item['id'] in need_ids:
                            item['calendar_source_ids'] = source_ids
            methods = {calculations[ref]["method"] for ref in calc_refs} if valid_calcs else set()
            missing_methods = set(q.get("method_needs", [])) - methods
            missing_dependencies = [dep for dep in q.get("dependencies", []) if by_id[dep]["status"] != "已回答"]
            ready = valid_refs and valid_calcs and not missing_methods and not missing_dependencies and not missing_needs
            completeness = (argument_support.question_assessment(task, analysis_proposal, q['id'])
                if argument_support.enabled(task) and answer else None)
            # Coverage describes completion, not whether every supported
            # sentence must disappear. Only a separately reviewed, supported
            # answer can survive missing obligations. A supported
            # statement is not erased merely because the reviewer sees no
            # further gap while the stricter internal coverage still does.
            # References, quantities and completion criteria stay unchanged.
            reviewed_partial = (not ready and valid_refs and valid_calcs
                and task['intent'] != DOCUMENT_CONTENT_INTENT
                and completeness is not None and (
                    completeness['status'] in ('answered','unidentifiable')
                    or completeness['status'] == 'requires_evidence'
                    and bool(completeness.get('missing_evidence'))))
            if (ready or reviewed_partial) and (isinstance(text, str) and text.strip() or "numeric_segments" in answer):
                proposal = dict(answer, question_ids=[q["id"]])
                if isinstance(text, str):
                    # Exact original-content quotations include their literal
                    # boundary whitespace. Only ordinary prose is normalized;
                    # changing a checked quote would invalidate its binding.
                    proposal["answer"] = text if task["intent"] == DOCUMENT_CONTENT_INTENT else text.strip()
                try:
                    bound = bind_numeric_statement(task, proposal, ["answer"], refs, calc_refs)
                except ContractError as error:
                    q["status"] = "未回答"
                    self._gap(task, "回答数字与引用依据不一致，未接受该回答：" + str(error), [q["id"]])
                    continue
                q.update(status="已回答" if ready else "未回答", answer=bound["fields"]["answer"], refs=list(dict.fromkeys(refs)),
                         calculation_refs=list(dict.fromkeys(calc_refs)), numeric_integrity=bound["record"])
                if argument_support.enabled(task):
                    q['research_coverage'] = copy.deepcopy(completeness)
                    if completeness['status'] in ('requires_evidence', 'requires_revision'):
                        q['status'] = '未回答'
                        detail = ('必要研究问题尚需按已有依据重新分析，保留原稿与已核验计算。'
                            if completeness['status'] == 'requires_revision'
                            else '必要研究问题仍缺少关键依据，保留受限回答与已核验计算。')
                        self._gap(task, detail, [q['id']])
                if reviewed_partial:
                    missing = ([need['text'] for need in missing_needs]
                        + ['必要计算：' + method for method in sorted(missing_methods)]
                        + ['前置问题：' + ident for ident in missing_dependencies])
                    self._gap(task, '已保留通过复核的部分回答；本问题仍未完成：' + '、'.join(missing),
                        [q['id']], category='question_coverage'
                        if task.get('evidence_gap_lifecycle_version') == 1 else None)
                if lineage["refs"]:
                    q.update(provenance_refs=lineage["refs"], provenance_records=lineage["records"],
                             provenance_integrity=lineage["receipt"])
                if "numeric_segments" in answer:
                    q["numeric_segments"] = copy.deepcopy(answer["numeric_segments"])
                else:
                    q.pop("numeric_segments", None)
            else:
                q["status"] = "未回答"
                detail = "；未覆盖：" + "、".join(n["text"] for n in missing_needs) if missing_needs else ""
                self._gap(task, ("核心" if q["required"] else "可选") + "问题未回答：" + q["question"] + detail, [q["id"]],
                          category='question_coverage' if task.get('evidence_gap_lifecycle_version') == 1 else None)
        task["tracking_reason"] = DOCUMENT_CONTENT_LIMITATION if task["intent"] == DOCUMENT_CONTENT_INTENT else fixture.get("tracking_reason", "静态事实与比较仅在依据变化时主动复核，不制造收益目标。")

    @staticmethod
    def _retain_thesis_sequence(task):
        """Keep the same-version high watermark even when a branch is removed.

        Older checkpoints have no counter, so reserve their existing identifiers
        before discarding any outputs. A new research version resets the counter
        explicitly; every thesis reference still includes its task and version.
        """
        next_id = task.get("next_thesis_id", 1)
        if type(next_id) is not int or next_id < 1:
            raise ContractError("研究论点标识序号无效")
        for thesis in task["theses"]:
            match = re.fullmatch(r"rh_thesis_([1-9][0-9]*)", thesis.get("id", ""))
            if match:
                next_id = max(next_id, int(match.group(1)) + 1)
        task["next_thesis_id"] = next_id
        return next_id

    def _accept_theses(self, task, selected, host_result, fixture):
        host_result = argument_support.adoptable(task, host_result)
        question_contexts=argument_support.thesis_question_contexts(task,selected)
        if argument_support.enabled(task):
            task['theses'] = [item for item in task['theses'] if not set(selected).intersection(item['question_ids'])]
            remaining = {item['id'] for item in task['theses']}
            task['tracking'] = [item for item in task['tracking'] if item.get('thesis_id') in remaining]
        task["numeric_integrity_contract"] = 1
        if task["intent"] == DOCUMENT_CONTENT_INTENT:
            raise ContractError("原件内容核对不能生成投资论点或判断追踪")
        proposals = fixture.get("claims", []) if fixture else (host_result or {}).get("theses", [])
        if not isinstance(proposals, list):
            raise ContractError("研究论点必须为列表")
        good_ids = {item["id"] for item in task["evidence"]}
        by_question = {q["id"]: q for q in task["questions"]}
        calculations = {c["id"]: c for c in task["calculations"]}
        multiple_groups = sum(n.get("kind") == "thesis" for n in task["nodes"]) > 1
        for proposal in proposals:
            if not isinstance(proposal, dict):
                raise ContractError("研究论点格式无效")
            if 'proposal_hash' in proposal:
                raise ContractError('论点提案身份由内核生成，不能自行填写')
            if 'question_context_receipt' in proposal:
                raise ContractError('问题研究上下文回执由内核生成，不能自行填写')
            proposal_hash = fingerprint(proposal)
            if proposal.get("task_id", task["id"]) != task["id"] or proposal.get("research_version", task["version"]) != task["version"]:
                raise ContractError("论点不能引用其他任务或研究版本")
            if multiple_groups and "question_ids" not in proposal and not fixture:
                raise ContractError("独立问题组的论点须明确对应问题，不能重复归因")
            related = proposal.get("question_ids", selected)
            if not isinstance(related, list) or not related or any(not isinstance(q, str) or q not in by_question for q in related):
                raise ContractError("论点对应问题不存在")
            if not set(related).intersection(selected):
                continue
            if not set(related).issubset(selected):
                raise ContractError("跨问题组论点须由同一综合节点处理，不能隐式合并独立问题")
            affected = related if fixture else [q for q in related if by_question[q]["kind"] not in DIRECT_KINDS]
            if not all(isinstance(proposal.get(key, []), list) and all(isinstance(ref, str) for ref in proposal.get(key, [])) for key in ("support", "counter")):
                raise ContractError("论点证据引用必须为标识列表")
            refs = proposal.get("support", []) + proposal.get("counter", [])
            evidence_by_id = {e["id"]: e for e in task["evidence"]}
            from .contracts import evidence_applies_to_answer
            if not refs or any(ref not in good_ids or not any(evidence_applies_to_answer(
                    task, evidence_by_id[ref], ident, proposal.get('calculation_refs', [])) for ident in related) for ref in refs):
                self._gap(task, "一项判断引用不可用，未接受该判断。", affected)
                continue
            if proposal.get("judgment") not in ("支持", "反对", "证据不足"):
                raise ContractError("研究判断状态不合法")
            fixture_refs = [c["id"] for c in task["calculations"] if set(c["input_refs"]).issubset(refs)] if fixture else []
            calc_refs = proposal.get("calculation_refs", fixture_refs)
            valid_calcs = isinstance(calc_refs, list) and all(isinstance(ref, str) and ref in calculations for ref in calc_refs)
            if valid_calcs:
                from .contracts import calculation_applies_to_answer
                valid_calcs = all(set(calculations[ref]["input_refs"]).issubset(refs) and
                    any(calculation_applies_to_answer(task, calculations[ref], ident) for ident in related)
                    for ref in calc_refs)
            if not valid_calcs:
                self._gap(task, "计算引用不可用或不属于本判断，未接受依赖数字的论点。", affected)
                continue
            try:
                bound = bind_numeric_statement(task, dict(proposal, question_ids=related),
                                               ["title", "investment_meaning", "limitation"], refs, calc_refs)
            except ContractError as error:
                self._gap(task, "论点数字与引用依据不一致，未接受该判断：" + str(error), affected)
                continue
            proposal = dict(proposal, **bound["fields"], numeric_integrity=bound["record"])
            if proposal.get("judgment") in ("支持", "反对"):
                context={ident:question_contexts[ident] for ident in related if ident in question_contexts}
                obligation_refs=list(dict.fromkeys(calc_refs+[
                    ref for value in context.values() for ref in value['calculation_refs']]))
                obligations = thesis_obligations(task, related, obligation_refs)
                if obligations:
                    for missing in obligations:
                        detail = []
                        if missing['missing_dependencies']:
                            detail.append('前置问题未回答：' + '、'.join(missing['missing_dependencies']))
                        if missing['missing_methods']:
                            detail.append('必要计算未完成或未引用：' + '、'.join(missing['missing_methods']))
                        self._gap(task, '确定性判断缺少登记条件；' + '；'.join(detail) + '；未接受该判断。',
                                  [missing['question_id']])
                    continue
                missing = [need["text"] for q in related if q not in context for need in missing_evidence_requirements(
                    by_question[q], [evidence_by_id[ref] for ref in refs],
                    market_need_ids=calendar_need_ids(task, by_question[q]))]
                if missing:
                    self._gap(task, "判断依据未覆盖：" + "、".join(dict.fromkeys(missing)) + "；未接受确定性判断。", affected)
                    continue
            try:
                validate_tracking(proposal.get("tracking"))
            except ContractError as error:
                self._gap(task, "验证条件无效，未接受该判断：" + str(error), affected)
                continue
            sequence = self._retain_thesis_sequence(task)
            thesis = dict(proposal, id=f"rh_thesis_{sequence}", task_id=task["id"], research_version=task["version"],
                          question_ids=related, calculation_refs=list(dict.fromkeys(calc_refs)))
            context={ident:question_contexts[ident] for ident in related if ident in question_contexts}
            if context:thesis['question_context_receipt']=copy.deepcopy(context)
            if judgment_review.enabled(task):
                thesis['proposal_hash'] = proposal_hash
            task["next_thesis_id"] = sequence + 1
            thesis["reference_groups_contract"] = 1
            for side in ("support", "counter"):
                for field, groups in reference_groups(evidence_by_id, proposal.get(side, [])).items():
                    thesis[side + "_" + field] = groups
            task["theses"].append(thesis)
            task["tracking_contract_version"] = 1
            rule = tracking_record(task, thesis)
            if rule is not None:
                task["tracking"].append(rule)
        for q in task["questions"]:
            if q["id"] in selected and q.get("kind") not in DIRECT_KINDS and not any(q["id"] in t["question_ids"] for t in task["theses"]):
                q["status"] = "未回答"
                self._gap(task, "必要研究判断未形成可核验论点：" + q["question"], [q["id"]])

    def _record_version_changes(self, task, fixture):
        if not task.get("update_from"):
            return
        if judgment_review.enabled(task):
            task['judgment_reviews'], task['judgment_review_pending'] = judgment_review.evaluate(task)
            if task['judgment_review_pending']:
                task['gaps'].append('原判断尚未完成事实、机制、预期差与价格的分项复核。')
        from .reports import build_version_changes
        task["changes"] = build_version_changes(task, self.archive.report(task["id"], task["update_from"]["version"]))
        task["version_changes_contract"] = task["changes"]["contract_version"]
        if fixture or not task["changes"]["added"] and not task["changes"]["revised"]:
            task["gaps"].append("主动更新未取得新时点证据；记录变化不代表机制或价格判断已兑现。")

    @staticmethod
    def _reasoning_evidence(task, selected=None):
        from .contracts import evidence_applies_to_answer
        fields = ("id", "object", "source", "locator", "record_locator", "published_at", "publication", "period", "observation_date", "observation_period", "nature", "unit",
                  "raw_unit", "raw_value", "value", "text", "date_role", "source_label", "question_ids", "applicability_review", "event_group",
                  "calculation_context", "calculation_context_basis", "reporting_period_basis", "artifact_id", "snapshot_hash",
                  "availability", "obtained_at", "identity", "field_definition")
        calculations = [calc['id'] for calc in task.get('calculations', [])]
        return [{key: copy.deepcopy(item[key]) for key in fields if key in item} for item in task["evidence"]
                if selected is None or any(evidence_applies_to_answer(task,item,ident,calculations) for ident in selected)]

    @staticmethod
    def _reasoning_evidence_payload(task, selected=None):
        """Send business facts once; retain mechanical proofs in the archive.

        Shared metadata is deduplicated by exact value, never by presumed
        equivalence. Values, dates, units, reviews and limitations remain
        available to reasoning; no row or research obligation is dropped.
        Validators continue reading the complete archived evidence, not this
        model-only representation. This version also binds failed-attempt
        cache identity, so a genuine format change is not an identical retry.
        """
        records = ResearchAgent._reasoning_evidence(task, selected)
        if argument_support.enabled(task):
            from .financial_sources import evidence_passage_context
            for record in records:
                passage = evidence_passage_context(task,record,include_text=True)
                if passage is not None:
                    record['passage_context'] = passage
        shared, candidates = {}, {}
        fields = ('identity', 'availability', 'field_definition', 'publication', 'applicability_review')
        for field in fields:
            counts = {}
            for record in records:
                if field in record and isinstance(record[field], (dict, list)):
                    key = fingerprint(record[field])
                    counts[key] = counts.get(key, 0) + 1
            candidates[field] = {key for key, count in counts.items() if count > 1}
        originals = {item['id']: item for item in task['evidence']}
        for record in records:
            if 'calculation_context_basis' in record:
                # These anchors prove parameter/unit lineage in the kernel;
                # they are not additional facts for the analyst to interpret.
                record.pop('calculation_context_basis')
                original = originals[record['id']]
                record['calculation_basis_archive_ref'] = {
                    'evidence_id': record['id'], 'artifact_id': original['artifact']['id'],
                    'snapshot_hash': original['snapshot_hash']}
            for field in fields:
                if field not in record or not isinstance(record[field], (dict, list)):
                    continue
                key = fingerprint(record[field])
                if key not in candidates[field]:
                    continue
                shared.setdefault(field, {})
                if key not in shared[field]:
                    shared[field][key] = {'ref': field + '_' + str(len(shared[field]) + 1),
                                          'value': record[field]}
                record[field + '_ref'] = shared[field][key]['ref']
                record.pop(field)
        return {'evidence': records,
                'evidence_shared_metadata': {field: list(entries.values()) for field, entries in shared.items()},
                'evidence_projection': {'version': 1, 'same_task': task['id'], 'research_version': task['version'],
                    'binding': context(task),
                    'rule': '字段_ref指向evidence_shared_metadata中对应字段的完整值；重复只按逐字相同值合并。',
                    'calculation_basis': '原件参数与单位定位保留在同任务同版归档证据；只用已有calculation_context选择输入，不制造或修改底稿。',
                    'financial_approval': False, 'automatic_input_selection': False}}

    def reasoning_inputs(self, ident, question_ids=None):
        """Same bounded, read-only analyst input for App and host Skill."""
        with self.lock:
            task = self._get(ident)
            known = {question['id'] for question in task['questions']}
            if question_ids is not None and (not isinstance(question_ids, list) or not question_ids
                    or any(not isinstance(q, str) or q not in known for q in question_ids)
                    or len(set(question_ids)) != len(question_ids)):
                raise ContractError('分析输入范围须为本任务明确问题列表')
            return self._reasoning_evidence_payload(task, question_ids)

    def _reason_on_verified_material(self, task, selected=None, *, final_delivery=False):
        """App reasoning shares host-result validation; models cannot approve sources."""
        payload = {"question": task["question"], "intent": task["intent"], "questions": [q for q in task["questions"] if selected is None or q["id"] in selected], "cutoff": task["cutoff"],
                   **self._reasoning_evidence_payload(task, selected),
                   "calculations": [c for c in task["calculations"] if selected is None or set(selected).intersection(c.get("question_ids", []))],
                   "gaps": task["gaps"], "knowledge": task.get("knowledge", {})}
        if task.get("update_from"):
            payload["version_review"] = self.review_baseline(task)
        payload["analysis_contract"] = analysis_contract(task, selected)
        revision = task.get('reasoning_state', {}).get('reviewed_writing_revision', {})
        if revision.get('status') == '已派发':
            payload['completed_review_feedback'] = dict(version=1,
                original_proposal=copy.deepcopy(revision['original_proposal']),
                review=copy.deepcopy(revision['review']), automatic_repair=False,
                rule='各题现有依据已足够，但正文有未获支持的陈述。只依据相同原值、计算及核对记录重新形成答案和论点；修正复核指出的问题，保留所有必答内容和适用限制。不新增事实、计算、假设或取数，不弱化原问题。只写研究内容，不写后续将如何制作、列示、导出或交付的承诺。新正文仍须数量核验和全新独立复核，不得自填通过。')
            if 'readiness_scope' in revision:
                payload['completed_review_feedback']['readiness_scope'] = copy.deepcopy(revision['readiness_scope'])
                payload['completed_review_feedback']['rule'] = (
                    '按readiness_scope区分各题：ready_question_ids可用同一批依据修正文句与分析，'
                    'evidence_limited_question_ids仍确实缺资料，保留其未解决需求和限制，不写成已经补齐。'
                    '完整提交本轮原提案范围，保留所有必答内容，不删除问题或改变必答性。'
                    '只用相同原值、计算及核对记录，不新增事实、计算、假设或取数，不弱化原问题。'
                    '只写研究内容，不写制作或交付承诺；新正文仍须数量核验和全新独立复核，不得自填通过。')
        final_state = task.get('reasoning_state', {}).get('final_delivery', {})
        correction = (final_state if final_delivery else
                      task.get('reasoning_state', {}).get('numeric_resubmission', {}))
        if correction.get('reason') == 'numeric_submission_rejected' and correction.get('status') == '已派发':
            payload['submission_feedback'] = {
                'version':1, 'invalid_fields':copy.deepcopy(correction['invalid_fields']),
                'rejected_proposal':copy.deepcopy(task['reasoning_state']['rejected_analysis_submissions'][-1]['proposal']),
                'rule':'上次提案未通过数字核验，不是有效结论。只重新提交当前已核验证据和已完成计算的分析，'
                       '不得再取数、改问题、改引用原值或添加假设。所有金融数字和比率都用numeric_segments数量引用，'
                       '包括标题、风险、阈值和比较中的数量；普通text不能夹带百分比、倍数或金融数字。'
                       '不能引用的阈值不用数字表达，改为有依据的定性说明。保留来源限制和反证，不把缺口写成已证实。',
                'remaining_resubmissions':0, 'automatic_repair':False}
            if task.get('analysis_question_scope_version') == 1:
                payload['submission_feedback']['question_failures'] = copy.deepcopy(correction.get('question_failures', []))
            if type(task.get('analysis_feedback_projection_version')) is int and task['analysis_feedback_projection_version'] == 1:
                rejected = task['reasoning_state']['rejected_analysis_submissions'][-1]
                payload['submission_feedback'].pop('rejected_proposal')
                payload['submission_feedback'].update(rejected_proposal_available=False,
                    rejected_proposal_archive_ref=dict(task_id=task['id'], research_version=task['version'],
                        batch=task['batch'], input_hash=task['input_hash'],
                        record_index=len(task['reasoning_state']['rejected_analysis_submissions']) - 1,
                        proposal_hash=fingerprint(rejected['proposal'])),
                    projection_version=1, thesis_failures=copy.deepcopy(correction.get('thesis_failures', [])))
                payload['submission_feedback']['rule'] += (
                    '失败草稿完整保存在本任务归档，不是有效证据。本次从完整核验证据、原问题及错误诊断重新分析，'
                    '不猜测或复用未展示的旧草稿，不改变引用、数值、计算及问题要求。'
                    'unbound_numeric_prose表示普通text中写了金融数量，须用本次实际数量引用或合格quote_ref表达，不能手填数字或删除必要数字；'
                    '其他代码分别核对所选原文、当前陈述引用、展示文本或登记比较方法，不自动补事实。')
        elif correction.get('reason')=='analysis_output_format' and correction.get('status')=='已派发':
            payload['submission_feedback']=dict(version=1,issue=copy.deepcopy(correction['format_issue']),
                rejected_proposal_available=False,remaining_resubmissions=0,automatic_repair=False,
                rule='上次服务完整返回，但最终JSON语法无效，未采用任何研究判断。仅按原问题、当前核验证据和登记计算重新提交。'
                     '不要取数、扩大范围、添加假设、猜测旧响应或改写原值。完整保留必要回答、实际引用和限制；'
                     '只输出指定JSON容器，字符串引号、反斜杠与换行正确转义，对象和数组项目以逗号分隔。'
                     '所有金融数量仍使用登记numeric_segments，结果仍须通过原数字、来源及独立论证核验。')
            if correction['format_issue'].get('category') == 'completed_analysis_structure':
                payload['submission_feedback']['rule'] = (
                    '上次响应已完整返回，但登记字段或类型不合规，未采用任何判断。按issue.schema_issues核对本轮Schema并重新提交完整分析。'
                    '不得取数、改问题、补引用、改变原值或添加假设；格式修正不批准证据、数量或论证，仍须独立复核。')
            elif correction['format_issue'].get('category') == 'completed_analysis_reference_choice':
                payload['submission_feedback']['rule'] = (
                    '上次响应完整返回，但对应回答选择了另一问题的证据，未采用任何判断。'
                    '逐题按本轮Schema的refs与counter_refs实际允许集合重新选择，并核对该证据确实支持本句；不能只改编号来保留无依据的断言。'
                    '没有适用依据则明确保留未回答或限制，不伪造引用、不删除必要问题。仅允许本次重新提交；'
                    '不得取数、改题、改变数字、添加假设或扩大引用范围，原数字核验和全新独立论证复核继续执行。')
        before = len(task.get("ledger", {}).get("dispatches", []))
        if final_delivery:
            result,receipt=model_object(self.model,task,'analyze',payload,analysis_fields(task),final_delivery=True)
        else:
            result, receipt = model_object(self.model, task, "analyze", payload, analysis_fields(task))
        validate_analysis_output(self.model, task, result, before)
        validate_analysis_execution(self.model, task, result, before, selected)
        if final_delivery:task['reasoning_state']['final_delivery']['status']='已提交'
        task.setdefault("reasoning_state", {}).setdefault("analysis", {})["receipt"] = receipt
        return result

    @staticmethod
    def _binding_checkpoint_needs_upgrade(task,node):
        saved = task.get('reasoning_state',{}).get('initial_calculation_binding',{})
        version = saved.get('binding_prompt_version',1 if saved.get('version')==1 else None)
        return (node.get('id')=='calculation' and task.get('run_mode')=='model'
            and saved.get('rejected') is True and type(version) is int
            and version < CALCULATION_BINDING_PROMPT_VERSION)

    def _bind_initial_calculations(self, task, question_ids=None):
        """Refine the initial plan after evidence IDs exist, before analysis.

        Unique registered financial bindings need no language decision. Any
        ambiguity retains ordinary model binding; no source is approved here.
        The finite ledger, locked methods and supplement rules still apply.
        """
        from .output_schema import deferred_calculation_output_schema, decode_calculation_binding_output
        allowed = {q['id'] for q in task['questions']}
        if question_ids is not None:
            if (not isinstance(question_ids,list) or not question_ids or len(set(question_ids))!=len(question_ids)
                    or any(not isinstance(q,str) or q not in allowed for q in question_ids)):
                raise ContractError('计算输入绑定不能越过本轮明确研究问题')
            allowed = set(question_ids)
        schema = deferred_calculation_output_schema(task)
        if schema is None:
            return
        from .contracts import calculation_dependency_questions
        inputs_scope = set(allowed)
        for ident in allowed:
            inputs_scope.update(calculation_dependency_questions(task, ident))
        payload = {'questions': [q for q in question_specs(task) if q['id'] in allowed],
            **self._reasoning_evidence_payload(task,sorted(inputs_scope)), 'binding_question_ids':sorted(allowed),
            'binding_scope_rule':'只提交binding_question_ids内原计划必要方法的输入绑定，不涉及其他问题。',
            'upstream_input_rule':'可选择明确依赖问题已核验的数值作为原计划方法输入；不修改原证据问题关联，'
                                  '不覆盖本问题的额外金融需求，不批准因果或投资判断。单位、主体、期间及方法仍独立核验。',
            'binding_prompt_version': CALCULATION_BINDING_PROMPT_VERSION,
            'input_requirements': calculation_input_requirements(task),
            'calculation_plan_contract': calculation_plan_contract(task),
            'binding_schema': schema, 'existing_calculations': copy.deepcopy(task.get('calculation_specs', []))}
        stable_context = {key: task[key] for key in ('id', 'version', 'cutoff', 'input_hash')}
        digest_payload = copy.deepcopy(payload)
        digest_payload['calculation_plan_contract']['binding'].pop('batch', None)
        digest_payload['evidence_projection']['binding'].pop('batch', None)
        digest = fingerprint({'task': stable_context, 'payload': digest_payload})
        state = task.setdefault('reasoning_state', {})
        previous = state.get('initial_calculation_binding', {})
        from .contracts import registered_financial_bindings
        registered = (registered_financial_bindings(task, allowed)
                      if argument_support.enabled(task) else None)
        if previous.get('input_digest') == digest or registered is None and not self._reasoning_available(task):
            return
        base_specs=copy.deepcopy(task.get('calculation_specs',[]))
        if (previous.get('derived_plan_version')==1 and previous.get('status')=='已登记'
                and previous.get('registered_plan_hash')==fingerprint(base_specs)
                and previous.get('input_base_hash')==task['input_hash']
                and input_fingerprint(task)==task['input_hash']):
            base_specs=copy.deepcopy(previous['input_calculation_specs'])
        state['initial_calculation_binding'] = {'version': 1, 'input_digest': digest,
            'derived_plan_version':1,'input_base_hash':task['input_hash'],
            'input_calculation_specs':base_specs,
            'binding_prompt_version': CALCULATION_BINDING_PROMPT_VERSION,
            'status': '已派发', 'automatic_input_selection': registered is not None,
            'selection_policy': 'registered_unique_financial_v1' if registered is not None else 'analyst',
            'supplement_round': False}
        self._save_model_checkpoint(task)
        try:
            if registered is None:
                proposal, receipt = model_object(self.model, task, 'bind_calculations', payload,
                                                  {'calculations', 'reason'})
            else:
                proposal = registered
                receipt = dict(actor='ResearchAgent', policy='registered_unique_financial_v1',
                    model_dispatched=False, source_approved=False, input_digest=digest)
            proposal = decode_calculation_binding_output(dict(proposal, version=1), schema)
            if any(not set(spec['question_ids']).issubset(allowed) for spec in proposal['calculations']):
                raise ContractError('计算提案越过本轮绑定问题范围')
            if proposal['calculations']:
                selected = sorted({q for spec in proposal['calculations'] for q in spec['question_ids']})
                validated = validate_supplement(task, {'question_ids': selected, 'reason': proposal['reason'],
                    'expected_change': '在首次分析前执行原计划的必要登记计算',
                    'data_requests': [], 'calculations': proposal['calculations'], 'artifact_ids': [], 'stop_reason': ''})
                task['calculation_specs'].extend(copy.deepcopy(validated['calculations']))
                task.setdefault('host_result', {})['calculations'] = copy.deepcopy(task['calculation_specs'])
            state['initial_calculation_binding'].update(status='已登记', receipt=receipt,
                registered_plan_hash=fingerprint(task.get('calculation_specs',[])),
                calculation_ids=[item['id'] for item in proposal['calculations']], reason=proposal['reason'])
        except ContractError:
            state['initial_calculation_binding'].update(status='未采用', rejected=True)
            for question in task['questions']:
                if question['id'] in allowed and question.get('method_needs'):
                    self._gap(task, '初始计算输入绑定未通过，保留已核验证据与方法义务，不自动重试。', [question['id']],
                              category='calculation', method_ids=question['method_needs'])
        self._save_model_checkpoint(task)

    def _calculate_spec(self, task, spec, synthetic=False):
        by_id = {item["id"]: item for item in task["evidence"]}
        from .contracts import methods_for_task
        units = {name: definition["input_units"] for name, definition in methods_for_task(task).items()}
        method = spec.get("method")
        def failure(message):
            self._gap(task, message, spec.get('question_ids'), category='calculation', method_ids=[method])
        if method not in units or set(spec.get("inputs", {})) != set(units[method]):
            raise ContractError("方法或输入字段未登记")
        if any(ref not in by_id for ref in spec["inputs"].values()):
            failure("计算输入缺失，停止依赖该计算的判断。")
            return
        from .contracts import calculation_input_applies
        if any(not all(calculation_input_applies(task, by_id[ref], ident, method)
                for ident in spec.get('question_ids', [q['id'] for q in task['questions']]))
                for ref in spec['inputs'].values()):
            failure("计算输入未针对对应问题完成适用性核验。")
            return
        from .contracts import valid_calculation_input_unit
        if any(not valid_calculation_input_unit(method, key, by_id[ref]["unit"]) for key, ref in spec["inputs"].items()):
            # A registered calculation may be inapplicable to these verified
            # observations. Refuse the result, just as for a missing input or
            # invalid context, without discarding independent owned research.
            failure("证据单位与方法输入约束不一致，未执行该计算。")
            return
        from .contracts import validate_calculation_context, calculation_context_contract
        from .evidence import evidence_binding
        bindings = {field: by_id[ref] for field, ref in spec["inputs"].items()}
        try:
            date_contract = calculation_context_contract(task, method)
            validate_calculation_context(method, bindings, spec["parameters"], context_contract=date_contract)
            if method in ("observed_price_return_v1", "observed_index_change_v1"):
                from .contracts import market_date
                if spec["parameters"]["end_date"] > market_date(task["cutoff"]) or any(
                        item.get("nature") != "已实现事实" for item in bindings.values()):
                    raise ContractError("已观测区间变化不能使用未来窗口或预测点位、预测价格")
            method_binding = self.knowledge.method_binding(task.get("knowledge"), method)
            from .contracts import validate_financial_method_scope
            validate_financial_method_scope(method, spec['parameters'], method_binding)
        except ContractError as error:
            failure("计算口径或方法依赖未通过：" + str(error))
            return
        values = {key: by_id[ref]["value"] for key, ref in spec["inputs"].items()}
        assumptions = spec.get("assumptions", {})
        if set(assumptions) & set(values):
            raise ContractError("假设不能覆盖披露的事实输入")
        values.update(assumptions)
        try:
            answer = calculate(method, values, spec["parameters"])
        except MethodNotApplicable as error:
            failure("登记方法不适用：" + str(error))
            return
        ident = spec.get("id", f"rh_calc_{len(task['calculations']) + 1}")
        from .planning import IDENTIFIER
        if not isinstance(ident, str) or not IDENTIFIER.fullmatch(ident) or any(c["id"] == ident for c in task["calculations"]):
            raise ContractError("计算标识无效或重复")
        calc = {"id": ident, "method": method, "implementation_version": METHOD_REGISTRY[method]["implementation_version"],
                "input_refs": list(spec["inputs"].values()), "inputs": values, "parameters": spec["parameters"],
                "input_bindings": spec["inputs"], "scenario_overrides": {},
                "assumptions": assumptions, "output": answer, "input_hash": fingerprint(values),
                "question_ids": spec.get("question_ids", [q["id"] for q in task["questions"]]),
                "task_id": task["id"], "research_version": task["version"],
                "method_binding": method_binding, "method_title": METHOD_REGISTRY[method]["title"],
                "limitations": METHOD_REGISTRY[method].get("limitations", METHOD_REGISTRY[method]["conditions"]),
                "input_versions": {ref: {"content_version": by_id[ref]["content_version"],
                    "snapshot_hash": by_id[ref]["snapshot_hash"], "evidence_hash": evidence_binding(by_id[ref])}
                    for ref in spec["inputs"].values()}}
        if date_contract is not None:
            calc['context_contract'] = date_contract
        if "assumption_sources" in spec:
            calc["assumption_sources"] = copy.deepcopy(spec["assumption_sources"])
        calc["dependency_hash"] = calculation_dependency_hash(calc)
        task["calculations"].append(calc)
        if synthetic and method == "residual_present_value_v1":
            stressed = dict(values, receipt=values["receipt"] - 20)
            stress_calc = dict(calc, id="rh_calc_stress", inputs=stressed, input_hash=fingerprint(stressed),
                scenario_overrides={"receipt": {"value": stressed["receipt"], "base_ref": spec["inputs"]["receipt"],
                    "reason": "合成压力假设收款减少20百万元，不是原件修订"}},
                output=calculate(method, stressed, spec["parameters"]), scenario="合成压力情景，收款减少20百万元")
            stress_calc["dependency_hash"] = calculation_dependency_hash(stress_calc)
            task["calculations"].append(stress_calc)

    def retry_save(self, ident):
        with self.lock:
            task = self._get(ident)
            if task["status"] != "失败" or not task.get("pending_content") or not task.get("pending_html"):
                raise ContractError("无可重试的保存检查点")
            self.knowledge.validate_package(task["knowledge"])
            for item in task["calculations"]:
                if item.get("method_binding") != self.knowledge.method_binding(task["knowledge"], item["method"]):
                    raise ContractError("保存依赖中的方法版本已失效，请建立关联研究")
            from .reports import compose
            if fingerprint(compose(task)) != fingerprint(task["pending_content"]):
                raise ContractError("待保存报告与当前核验依赖不一致，不能交付旧检查点")
            charge(task["ledger"], "seconds", .001, delivery=True)
            # Freeze only a durable checkpoint. Preserve the save attempt's
            # accounting even if the delivery transaction fails again.
            self.archive.save(task)
            return self.archive.deliver(task, task["pending_content"], task["pending_html"])

    def review_baseline(self, task):
        """Frozen prior judgments are context, never current evidence approvals."""
        if not task.get("update_from"):
            return None
        from .reports import build_version_changes
        prior = build_version_changes(task, self.archive.report(task["id"], task["update_from"]["version"]))["prior_basis"]
        fields = ("id", "title", "judgment", "question_ids", "investment_meaning", "limitation")
        return {"task_id": task["id"], "prior_version": prior["research_version"], "research_version": task["version"],
                "prior_cutoff": prior["cutoff"], "prior_digest": task["update_from"]["digest"],
                "prior_theses": [{key: t[key] for key in fields if key in t} for t in prior["theses"]],
                "prior_calculations": [{key: copy.deepcopy(value) for key, value in item.items()
                    if key != 'input_evidence'} for item in prior.get('calculations', [])],
                "prior_calculations_retained": prior.get('calculations_retained', False),
                "original_conditions": prior["conditions"],
                "rule": "原判断仅供复核，不能按编号自动匹配或作本版证据。事实、机制与价格兑现分别核验；只用当前analysis_contract中的证据作答，缺口写入limitation，不事后改写原条件。"}

    def _updated_task(self, old, cutoff):
        """Build detached context so all host input is checked before saving."""
        conversation_context(old)
        if not old.get('delivery') or old['status'] not in ('已完成', '部分完成'):
            raise ContractError('只有已保存交付可以主动更新')
        explicitly_dated = cutoff is not None
        cutoff = cutoff or now()
        if timepoint(cutoff) <= timepoint(old['cutoff']):
            raise ContractError('更新时间必须晚于原截止时点')
        new = copy.deepcopy(old)
        new.update(version=old['version'] + 1, batch=0, cutoff=cutoff, status='待开始', delivery=None,
            error=None, ledger=budget(old['ledger']['limits']), evidence=[], calculations=[], theses=[],
            gaps=[], tracking=[], optional_findings=[], next_thesis_id=1)
        if 'research_mode' in new:
            new['research_mode'] = 'historical' if explicitly_dated else old['research_mode']
        new['materials'] += new.pop('pending_materials', [])
        new.setdefault('source_records', []).extend(new.pop('pending_source_records', []))
        for item in new['materials'] + new['source_records']:
            if item.get('kind') in ('material', 'api', 'filing'):
                item['research_version'] = new['version']
        for key in ('pending_content', 'pending_html', 'host_result', 'knowledge', 'supplement_history',
                    'supplement_decisions', 'supplement_decision', 'supplement_stop_reason', 'source_attempts',
                    'gap_records', 'reasoning_state', 'source_readings', 'host_checkpoint', 'defer_delivery',
                    'changes', 'version_changes_contract', 'resolved_gap_records', 'judgment_review_contract',
                    'judgment_review_baseline', 'judgment_review_proposals', 'judgment_reviews', 'judgment_review_pending',
                    'catalogue_alternative_requests', 'draft_report', 'draft_observations', 'draft_notices', 'draft_submissions'):
            new.pop(key, None)
        new.pop('argument_support_reviews', None)
        new['questions'] = [dict(question, status='待回答') for question in normalize_questions(
            question_specs(new), new['question'], new['intent'])]
        if new.get('draft_first_version') == 1:
            self._apply_draft_policies(new)
        new['input_hash'] = input_fingerprint(new)
        new['nodes'] = self.plan(new)
        new['update_from'] = dict(version=old['version'], digest=old['delivery']['digest'],
            conditions=copy.deepcopy(old['tracking']), knowledge_versions=old.get('knowledge', {}).get('index', {}),
            reason='用户主动复核新时点，历史交付不覆盖；新研究重新采用适用知识')
        original = judgment_review.baseline(self.archive.report(old['id'], old['version']))
        if original['anchors']:
            new.update(judgment_review_contract=judgment_review.VERSION, judgment_review_baseline=original,
                       judgment_review_proposals=[], judgment_reviews=[], judgment_review_pending=[])
        new['revisions'].append(dict(action='主动更新', version=new['version'], time=now()))
        return new

    def prepare_update(self, ident, cutoff=None):
        """Start a fresh version without dispatch; host reasoning can read first."""
        with self.lock:
            new = self._updated_task(self._get(ident), cutoff)
            self.archive.save(new)
            return new

    def update(self, ident, cutoff=None, asynchronous=False, host_result=None):
        with self.lock:
            new = self._updated_task(self._get(ident), cutoff)
            if host_result is not None:
                host_result = self._validate_host_input(new, host_result)
            self.archive.save(new)
            return self.start(ident, host_result, asynchronous)

    def source_catalog(self, ident, artifact_ids=None, *, offset=0, limit=24):
        with self.lock:
            task = self._get(ident)
            result = source_catalog(task, artifact_ids, offset=offset, limit=limit)
            result["retained_originals"] = self.archive.retained_originals(task, pending_only=True)
            return result

    def read_materials(self, ident, requests, expected_binding):
        """Shared host/App reader, with no model, provider or approval side effect."""
        with self.lock:
            task = self._get(ident)
            bundle = read_sources(task, requests, expected_binding)
            if task["status"] != "运行中" and not task.get("delivery") and not task.get("pending_content"):
                readings = task.setdefault("source_readings", [])
                if not any(r["reading_hash"] == bundle["reading_hash"] for r in readings):
                    if len(readings) >= 24:
                        raise ContractError("本版本已保存24份读取记录，请复用既有范围，不无限累计重读")
                    readings.append(bundle)
                    task["revisions"].append({"action": "原件定向读取", "reading_hash": bundle["reading_hash"], "time": now(),
                                              "impact": "读取不批准事实，不改变预算、研究计划或冻结交付"})
                    self.archive.save(task)
            return bundle

    def add_material(self, ident, name, payload, *, expected_version=None):
        if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
            raise ContractError("材料保存需要有效的研究版本")
        with self.lock:
            original_task = self._get(ident)
            if expected_version is not None and (original_task["version"] != expected_version or original_task["status"] == "运行中"):
                raise ContractError("任务版本或运行状态已改变，未重新解析原件")
            self.archive.retain_original(original_task, "material", name, payload)
        parsed = self.material_parser.parse(name, payload) if self.material_parser else extract_material(name, payload)
        with self.lock:
            task = self._get(ident)
            if task["version"] != original_task["version"]:
                raise ContractError("解析期间研究版本已改变，原件留存于原版本，未采用正文")
            if expected_version is not None and task["status"] == "运行中":
                raise ContractError("解析期间任务已开始运行，原件保留但未改写运行依赖")
            # Uploaded materials remain unverified; no hidden approval is implied.
            record = material_record(task, parsed, payload)
            if any(x.get("id") == record["id"] for x in task["materials"] + task.get("pending_materials", [])):
                return task
            task["materials"].append(record)
            task["revisions"].append({"action": "补充原问题材料", "hash": parsed["hash"], "time": now(),
                                      "impact": "待核验，不自动改变已交付版本"})
            if task["delivery"]:
                # Keep additions outside the immutable version snapshot.
                task.setdefault("pending_materials", []).append(task["materials"].pop())
            elif task["status"] != "运行中":
                self._invalidate_sources(task)
            return self.archive.save(task)

    def add_snapshot(self, ident, source, operation, parameters, response):
        """Import a retained raw response, not a vendor credential or approval."""
        with self.lock:
            task = self._get(ident)
            return self._save_source(task, snapshot_record(task, source, operation, parameters, response))

    def add_filing(self, ident, directory, name=None, payload=None, *, expected_version=None):
        with self.lock:
            original_task = self._get(ident)
            if expected_version is not None and original_task["version"] != expected_version:
                raise ContractError("任务版本已改变，未重新解析原件")
            if original_task["status"] == "运行中":
                raise ContractError("取证输入须在执行前或任务停止后补充，不能改写运行中的依赖")
            related = original_task.get("source_records", []) + original_task.get("pending_source_records", [])
            validated = filing_record(original_task, directory, related=related)
            if payload is not None:
                self.archive.retain_original(original_task, "filing", name or "original.pdf", payload, validated["directory"])
        parsed = self.material_parser.parse(name or "original.pdf", payload) if self.material_parser and payload is not None else None
        with self.lock:
            task = self._get(ident)
            if task["version"] != original_task["version"]:
                raise ContractError("解析期间研究版本已改变，原件留存于原版本，未采用正文")
            records = task.get("source_records", []) + task.get("pending_source_records", [])
            return self._save_source(task, filing_record(task, directory, name, payload, records, _parsed=parsed))

    def retry_original(self, ident, original_id):
        """Explicit local retry, never a new download, model call or approval."""
        with self.lock:
            task = self._get(ident)
            if task["status"] == "运行中":
                raise ContractError("研究仍在运行，请停止后重新解析原件")
            original, payload = self.archive.original(task, original_id)
        # Parsing must not hold the task lock: cancellation and task reads remain
        # available. Recheck the captured version before and after local parsing.
        if original["kind"] == "material":
            return self.add_material(ident, original["name"], payload, expected_version=task["version"])
        return self.add_filing(ident, original["directory"], original["name"], payload, expected_version=task["version"])

    def _save_source(self, task, record):
        if task["status"] == "运行中":
            raise ContractError("取证输入须在执行前或任务停止后补充，不能改写运行中的依赖")
        field = "pending_source_records" if task.get("delivery") else "source_records"
        records = task.setdefault(field, [])
        if any(x["id"] == record["id"] for x in records):
            return task
        records.append(record)
        if not task.get("delivery"):
            self._invalidate_sources(task)
        task["revisions"].append({"action": "补充原始来源", "artifact_id": record["id"], "hash": record.get("snapshot_hash", record["hash"]),
                                  "time": now(), "impact": "待两类核验，不改写冻结交付"})
        return self.archive.save(task)

    @staticmethod
    def _invalidate_sources(task):
        if task.get("host_checkpoint"):
            # A host may upload the next original before declaring its targeted
            # supplement. Keep completed branches until that scope is validated;
            # start() blocks final delivery with unregistered new originals.
            return
        for node in task["nodes"]:
            if node["id"] != "identity":
                node["status"] = "待执行"

    def recover_interrupted(self):
        for task in self.archive.list():
            if task["status"] == "运行中":
                task.update(status="失败", error="上次运行中断，检查点已保留；恢复继承原时点和用量")
                self.archive.save(task)

    def export(self, ident, version=None):
        task = self._get(ident)
        return self.archive.report(ident, version or task["version"])

    def submit(self, ident, module_request, result):
        """Shared App/Skill binding check for externally produced module artifacts."""
        with self.lock:
            task = self._get(ident)
            validate_request(module_request, task)
            if task.get("delivery"):
                raise ContractError("冻结研究不能补写模块结果")
            if "schema" in result:
                validate_result(result, task)
                if result["operation"] != module_request["operation"]:
                    raise ContractError("模块请求与结果操作不匹配")
                if result.get("node_id") != module_request.get("node_id"):
                    raise ContractError("模块请求与结果计划节点不匹配")
            elif result.get("input_hash") != task["input_hash"]:
                raise ContractError("模块结果依赖已过期")
            return {"accepted": True, "context": context(task), "result_hash": fingerprint(result)}
