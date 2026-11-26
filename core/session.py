"""Host-Agent Python interface; no user-facing command line."""

import sys
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path

sys.dont_write_bytecode = True
INSTALLATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INSTALLATION))
if (INSTALLATION / 'core/src/researchhelper').is_dir():
    from core.bootstrap import configure_imports
    configure_imports()
from researchhelper.archive import project_store, project_workspace_path
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError, ExecutionTimeExhausted, canonical, context, fingerprint, recovery_time_block
from researchhelper.evidence import snapshot_record, data_request_contract
from researchhelper.source_catalogue import source_catalogue
from researchhelper.planning import plan_preservation_contract, question_contract, research_brief
from researchhelper.runtime import StoreLease
from researchhelper.reasoning import evidence_contract, analysis_contract, calculation_plan_contract, extractable_source_views
from researchhelper.settings import DataSettings
from researchhelper.source_reader import retained_reading
from researchhelper.argument_support import enabled as argument_review_required


def _api_selection_context(task, reading=None):
    """Bind the existing App codec to the exact host-visible source batch."""
    from researchhelper.output_schema import api_extraction_output_schema
    views = reading['views'] if reading is not None else extractable_source_views(task)
    schema = api_extraction_output_schema(task, views)
    if schema is None:
        return None
    aliases = {q['id']: [{'id': 'n' + str(i), 'text': text}
                        for i, text in enumerate(q['evidence_needs'], 1)] for q in task['questions']}
    binding = fingerprint(dict(context=context(task), schema=schema, source_views=views,
                               need_aliases=aliases, reading_hash=reading['reading_hash'] if reading else None))
    descriptor = dict(schema=schema, binding_hash=binding, decode='select_api_cells',
                      financial_approval=False, local_model_calls=False)
    if schema['properties']['version']['enum'][0] in (3, 4):
        descriptor['need_aliases'] = aliases
    return views, descriptor


class Session:
    def __init__(self, project, *, allow_network=False, source_credentials=None):
        if not isinstance(allow_network, bool):
            raise ContractError("Skill数据调用权限须由宿主明确指定")
        if source_credentials is not None and (not isinstance(source_credentials, dict) or not allow_network):
            raise ContractError("源凭据仅用于宿主明确授权的数据调用")
        if allow_network:
            if sys.flags.ignore_environment and not sys.flags.dont_write_bytecode:
                raise ContractError("隔离Python宿主须启用不写字节码后才能联网；安装目录保持只读，本次未派发")
        # Network and document-parser children import the installed engine
        # during spawn. Preserve read-only installation in offline runs too.
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        self.project = Path(project).resolve()
        self.archive = project_store(self.project, INSTALLATION)
        credentials = source_credentials
        if allow_network and credentials is None:
            credentials = DataSettings(self.archive.root).read(private=True)
        self.agent = ResearchAgent(self.archive, INSTALLATION, offline=not allow_network, source_credentials=credentials,
            isolate_sources=allow_network, isolate_materials=True, require_argument_support=True, draft_first=True)

    @contextmanager
    def _executing(self, task_id):
        """Only this task is exclusive, including recovery after a host exits."""
        if not isinstance(task_id, str) or not task_id:
            raise ContractError('研究任务标识无效')
        lease = StoreLease(self.archive.root, task_id=task_id)
        try:
            yield self.agent.recover_interrupted_task(task_id)
        finally:
            lease.close()

    def create(self, question, **options):
        options.setdefault("research_mode", "historical" if options.get("cutoff") else "current")
        return self.agent.create(question, **options)

    def follow_up(self, task_id, message, *, expected_revision=None):
        """Prepare a related question; the host still owns all reasoning."""
        return self.agent.follow_up(task_id, message, start=False, expected_revision=expected_revision)

    def plan(self, task_id, questions, reason, expected_input_hash=None):
        return self.agent.revise_plan(task_id, questions, reason, expected_input_hash)

    @staticmethod
    def _delivery_recovery(task):
        """Describe local work only after completed prose or content was retained."""
        if task['status'] not in ('失败','已取消'):
            return None
        from researchhelper.draft_workflow import has_composed_draft, retained_submission

        content = bool(task.get('pending_content'))
        composed = not content and has_composed_draft(task)
        submission = not (content or composed) and retained_submission(task) is not None
        if not (content or composed or submission):
            return None
        result = dict(action='retry_save(task_id)',retained_content=content or composed,
            retained_submission=submission,local_composition=submission,
            local_presentation_only=content,local_model_calls=False,data_requests=False)
        if composed:
            result['local_report_packaging'] = True
        return result

    def tools(self, task_id, *, reading_hash=None, scope='all'):
        """Read the same implemented request grammar used by the App planner."""
        if scope not in ('all', 'first_report', 'acquisition'):
            raise ContractError('工具范围须为all、acquisition或first_report')
        if scope != 'all' and reading_hash is not None:
            raise ContractError('原件选择绑定须使用完整工具')
        task = self.archive.get(task_id)
        if scope == 'acquisition':
            from researchhelper.draft_workflow import _include_conversation, _include_update, direct_readout
            from researchhelper.draft_sources import (acquisition_coverage,
                direct_financial_requests, stated_financial_supplements, stated_latest_requests,
                stated_window_requests)
            from researchhelper.knowledge import ResearchKnowledge
            from researchhelper.planning import draft_research_context, draft_research_scope

            first_report = task.get('draft_first_version') == 1
            result = dict(research_brief=research_brief(task),
                data_requests=data_request_contract(task, first_report=first_report),
                source_catalogue=source_catalogue(task), research_context=draft_research_context(task),
                knowledge_context=ResearchKnowledge().draft_context(task),
                acquisition_coverage=acquisition_coverage(task))
            if first_report:
                result['prepare'] = 'draft(task_id, defer_delivery=True, data_requests=[])'
                result['research_scope'] = draft_research_scope(task)
                from researchhelper.official_sources import (direct_fomc_rate_request, fomc_statement_requests,
                                                             research_fomc_rate_request)
                from researchhelper.economic_sources import research_economic_searches, research_economic_scopes

                if not self.agent.offline:
                    statement_scope = direct_fomc_rate_request(task) or research_fomc_rate_request(task)
                    if statement_scope is not None:
                        result['stated_official_requests'] = fomc_statement_requests(task, statement_scope)
                    if scopes := research_economic_scopes(task):
                        result['stated_economic_searches'] = research_economic_searches(task, scopes)
                from researchhelper.financial_sources import direct_financial_scope

                readout = None if task.get('host_checkpoint') else direct_readout(task)
                if not readout and not self.agent.offline and not task.get('host_checkpoint'):
                    fallback = direct_readout(task, policy_without_model=True)
                    if fallback and fallback[0] == 'valuation_research':
                        readout = fallback
                if readout is not None:
                    kind, plan = readout
                    result['direct_report'] = 'draft(task_id)'
                    if kind == 'compound' and plan['financial'] and (plan['points'] or plan['latest']):
                        result['direct_financial_market_report'] = 'draft(task_id)'
                if not self.agent.offline and (scope := direct_financial_scope(task)) is not None:
                    result['direct_financial_report'] = direct_financial_requests(task, result['acquisition_coverage'], scope)
                else:
                    requests = stated_financial_supplements(task, result['acquisition_coverage'])
                    if requests:
                        result['stated_financial_requests'] = requests
                requests = stated_window_requests(task, include_financial=False)
                if requests:
                    result['stated_window_requests'] = requests
                requests = stated_latest_requests(task, result['acquisition_coverage'])
                if requests:
                    result['stated_latest_requests'] = requests
            _include_update(task, result, include_prior_findings=False)
            rule = _include_conversation(task, result, include_prior_findings=False)
            if rule:
                result['conversation_rule'] = rule
            return result
        if scope == 'first_report' and task.get('draft_first_version') != 1:
            raise ContractError('当前任务没有首稿工具，请读取默认完整工具')
        reading = retained_reading(task, reading_hash) if reading_hash is not None else None
        result = {"questions": question_contract(task), "plan_preservation": plan_preservation_contract(task),
                "research_brief": research_brief(task),
                "data_requests": data_request_contract(task, first_report=scope == 'first_report'),
                "source_catalogue": source_catalogue(task),
                "calculations": calculation_plan_contract(task),
                "evidence": evidence_contract(task, reading['views'] if reading else None), "analysis": analysis_contract(task),
                "argument_support": {"required": argument_review_required(task),
                    "prepare": "run(defer_delivery=True)", "inputs": "argument_review_inputs",
                    "submit": "review_arguments", "resume": "resume(defer_delivery=True)",
                    "deliver": "deliver", "local_model_calls": False},
                "version_review": self.agent.review_baseline(task) if task.get("update_from") else None}
        if task["status"] == "待开始" and task["batch"] == 0:
            result['material_selection'] = dict(remove='remove_material(task_id, name, digest, expected_version=..., expected_revision=..., retained_files=...)',
                expected_version=task['version'], expected_revision=task['revision'], originals_retained=True, local_model_calls=False)
        recovery = self._delivery_recovery(task)
        blocked = recovery_time_block(task, save_only=recovery is not None)
        if blocked:
            result['recovery_block'] = dict(code=ExecutionTimeExhausted.code, reason=blocked)
        if recovery is not None:
            if blocked:
                recovery['action'] = None
            result['delivery_recovery'] = recovery
        elif not blocked and task['status'] in ('失败', '已取消'):
            from researchhelper.draft_workflow import has_retained_draft_data
            if has_retained_draft_data(task):
                result['delivery_recovery'] = dict(action='finish_draft(task_id)',
                    retained_data=True, local_model_calls=False, data_requests=False)
        if task.get('delivery'):
            result['report_corrections'] = dict(read='report_corrections(task_id, version)',
                history='report_history(task_id)',
                record='correct_report(task_id, reason, version, expected_digest=...)',
                frozen_report_unchanged=True, local_model_calls=False)
        if task.get('draft_first_version') == 1:
            result['first_report'] = dict(prepare='draft(task_id, defer_delivery=True, data_requests=[])',
                acquire='draft(task_id, defer_delivery=True, data_requests=...)',
                read='tools(task_id, scope="first_report").first_report.writing_context and sources(task_id)', write='draft(task_id, report=...)',
                report_fields=['title', 'conclusion', 'logics', 'risks'],
                logic_fields=['title', 'implication', 'risk'],
                logic_body_fields=['argument', 'analysis'],
                optional_logic_fields=['argument', 'data_refs', 'source_refs','calculation_refs','knowledge_refs','workpaper_notes','mechanism'],
                optional_report_fields=['workpaper_notes','reader_notes'],
                reference_scope='data_refs使用本任务draft_observations的id，source_refs使用本任务资料id；knowledge_refs以id和version引用实际使用的锁定知识。缺少引用不阻断初稿。',
                independent_review_required=False, local_model_calls=False)
            from researchhelper.publication_content import client_report_brief, report_writing_example
            result['first_report']['writing_brief']=client_report_brief(task)
            result['first_report']['report_example']=report_writing_example(task)
            if type(task.get('draft_tracking_version')) is int and task['draft_tracking_version'] == 1:
                from researchhelper.contracts import tracking_contract
                result['first_report']['tracking_contract'] = tracking_contract()
                result['first_report']['optional_logic_fields'].append('tracking')
            from researchhelper.draft_workflow import _include_conversation, _include_update
            _include_update(task, result['first_report'])
            conversation_rule = _include_conversation(task, result['first_report'])
            if conversation_rule:
                result['first_report']['conversation_rule'] = conversation_rule
            from researchhelper.planning import draft_research_scope, draft_research_context
            result['first_report']['research_context'] = draft_research_context(task)
            from researchhelper.draft_sources import acquisition_coverage
            result['first_report']['acquisition_coverage'] = acquisition_coverage(task, include_retained_economic=True)
            research_scope = draft_research_scope(task)
            if research_scope is not None:
                result['first_report']['research_scope'] = research_scope
                result['first_report']['optional_report_fields'].append('report_scope')
            from researchhelper.knowledge import ResearchKnowledge
            result['first_report']['knowledge_context']=ResearchKnowledge().draft_context(task)
            entries=result['first_report']['knowledge_context'].get('entries',[])
            if entries:
                result['first_report']['reference_examples']=dict(knowledge_refs=[dict(id=entries[0]['id'],version=entries[0]['version'])])
            from researchhelper.draft_analysis import registered_methods,interpretation_context
            result['first_report']['arithmetic_methods']=registered_methods()
            from researchhelper.draft_sources import observations,request_field_context,observation_context
            facts=observations(task)
            from researchhelper.draft_sources import client_writing_context
            result['first_report']['writing_context']=client_writing_context(task,facts)
            result['first_report']['interpretation_context']=interpretation_context(task,facts)
            observed_context=observation_context(task,facts)
            if observed_context:
                result['first_report']['observation_profiles']=observed_context['profiles']
                result['first_report']['observation_profile_rule']=observed_context['rule']
            request_context=request_field_context(task)
            if request_context:
                result['first_report']['request_context']=request_context
            result['first_report']['read_calculations']='writing_context.calculations；status().draft_analysis保留原始记录，不代替当前算术核对；原值算术不等于金融适用性通过。'
            result['first_report']['visuals']=dict(catalogue='visuals()',styled_export='export(visual_theme=...) or export(visual_profile=...)',source_data_unchanged=True,selection='按每条逻辑的实际引用独立配图',coverage='冻结呈现文档的figure_coverage',missing_data='有明确传导条件时可附mechanism.steps；不补造数值，不阻断正文',extra_model_calls=0)
        from researchhelper.planning import research_outline_contract
        outline = research_outline_contract(task)
        if outline is not None:
            result['research_outline'] = outline
        if task.get('planning_definition_scope_version') == 1:
            from researchhelper.knowledge import ResearchKnowledge
            result['planning_definitions'] = ResearchKnowledge().planning_definitions(task)
        if task.get('initial_acquisition_scope_version') == 1:
            from researchhelper.acquisition import initial_acquisition_coverage
            result['initial_source_readiness'] = initial_acquisition_coverage(task)
        if task.get('catalogue_search_navigation_version') == 1:
            from researchhelper.acquisition import negative_catalogue_searches
            result['negative_catalogue_searches'] = negative_catalogue_searches(task)
        api_selection = _api_selection_context(task, reading)
        if api_selection is not None:
            result['api_cells'] = api_selection[1]
        if reading is not None and task.get('original_span_selection_version') == 1:
            from researchhelper.output_schema import original_span_output_schema, original_span_prompt
            schema = original_span_output_schema(task, reading['views'])
            if schema is not None:
                result['original_spans'] = dict(schema=schema, prompt=original_span_prompt(
                    dict(untrusted_sources=reading['views'], evidence_contract=result['evidence']), task),
                    decode='select_original_spans', financial_approval=False, local_model_calls=False)
        if reading is not None and task.get('financial_amount_selection_version') == 1:
            from researchhelper.output_schema import original_batch_output_schema, original_batch_prompt
            schema = original_batch_output_schema(task, reading['views'])
            if schema is not None:
                result['financial_originals'] = dict(schema=schema, prompt=original_batch_prompt(
                    dict(untrusted_sources=reading['views'], evidence_contract=result['evidence']), task),
                    decode='select_financial_originals', financial_approval=False, local_model_calls=False)
        if scope == 'first_report':
            # This is a disclosure projection, not a smaller data capability.
            # Keep the exact shared request grammar, planning context and local
            # recovery actions. Formal evidence/review schemas remain available
            # through the unchanged default scope.
            fields = ('research_brief', 'data_requests', 'source_catalogue', 'first_report',
                      'plan_preservation', 'planning_definitions', 'initial_source_readiness',
                      'negative_catalogue_searches', 'research_outline', 'version_review',
                      'delivery_recovery', 'recovery_block', 'report_corrections', 'material_selection')
            return {key: value for key, value in result.items() if key in fields}
        return result

    def select_api_cells(self, task_id, binding_hash, submission, *, reading_hash=None):
        """Decode an explicit selection, without acquiring or approving facts."""
        from researchhelper.output_schema import decode_api_extraction_output
        task = self.archive.get(task_id)
        reading = retained_reading(task, reading_hash) if reading_hash is not None else None
        selected = _api_selection_context(task, reading)
        if selected is None:
            raise ContractError('本批次不适用接口选择，不删除来源强行套用')
        views, descriptor = selected
        if not isinstance(binding_hash, str) or binding_hash != descriptor['binding_hash']:
            raise ContractError('接口选择已过期或不属于本任务，请重新读取当前工具')
        return decode_api_extraction_output(submission, descriptor['schema'], (task, views))

    def select_original_spans(self, task_id, reading_hash, submission):
        """Host selection restores proposals; run still performs every evidence gate."""
        from researchhelper.output_schema import original_span_output_schema, decode_original_spans
        task = self.archive.get(task_id)
        views = retained_reading(task, reading_hash)['views']
        return decode_original_spans(submission, original_span_output_schema(task, views), (task, views))

    def select_financial_originals(self, task_id, reading_hash, submission):
        """Host-only selection; the ordinary run still owns both evidence gates."""
        from researchhelper.output_schema import original_batch_output_schema, decode_original_batch
        from researchhelper.financial_sources import financial_amount_selection_enabled
        task = self.archive.get(task_id)
        if not financial_amount_selection_enabled(task):
            raise ContractError('本任务未登记财务明细选择入口，不升级旧研究')
        views = retained_reading(task, reading_hash)['views']
        schema = original_batch_output_schema(task, views)
        if schema is None:
            raise ContractError('本轮实际读取不适用财务原件选择，不删除来源强行套用')
        return decode_original_batch(submission, schema, (task, views))

    def status(self, task_id, version=None):
        return self.archive.get(task_id, version)

    def draft(self, task_id, report=None, data_requests=None, *, defer_delivery=False):
        """First paper; host owns prose, shared engine owns data and delivery.

        Use defer_delivery=True to acquire data first, read draft_observations
        and sources, then submit the host's report in a second draft() call.
        This path never invokes the App model or requires argument approval.
        """
        with self._executing(task_id) as task:
            if (report is None and defer_delivery is False
                    and (data_requests is None or isinstance(data_requests,list) and not data_requests)):
                if self._delivery_recovery(task) is not None:
                    return self.agent.retry_save(task_id)
                if data_requests is None and not task.get('host_checkpoint'):
                    from researchhelper.draft_workflow import direct_readout

                    if direct_readout(task, policy_without_model=not self.agent.offline) is not None:
                        return self.agent.start(task_id)
            return self.agent.start(task_id, dict(draft_report=report,
                data_requests=[] if data_requests is None else data_requests), defer_delivery=defer_delivery)

    def report_history(self, task_id):
        """Read saved report versions with their original correction notes."""
        return self.archive.delivery_history(task_id)

    def report_corrections(self, task_id, version=None):
        """Read separate correction notes; a frozen report stays unchanged."""
        if version is None:
            current = self.archive.get(task_id)
            version = (current.get('delivery') or {}).get('version', current['version'])
        return self.archive.correction_records(task_id, version)

    def correct_report(self, task_id, reason, version=None, *, expected_digest=None):
        """Record a known error without a model call, refresh or overwrite."""
        if version is None:
            current = self.archive.get(task_id)
            version = (current.get('delivery') or {}).get('version', current['version'])
        return self.archive.correction(task_id, version, reason, expected_digest=expected_digest)

    def cancel(self, task_id):
        return self.agent.cancel(task_id)

    def resume(self, task_id, *, defer_delivery=False, **host_outputs):
        """Resume retained inputs; deferring delivery is not a new submission."""
        if not isinstance(defer_delivery, bool):
            raise ContractError("宿主研究检查点须明确指定，不能隐式延迟交付")
        if host_outputs:
            return self.run(task_id, defer_delivery=defer_delivery, **host_outputs)
        with self._executing(task_id) as task:
            if self._delivery_recovery(task) is not None:
                if defer_delivery and not recovery_time_block(task, save_only=True):
                    raise ContractError("报告正文已保留，请使用retry_save完成保存")
                return self.agent.retry_save(task_id)
            return self.agent.start(task_id, defer_delivery=defer_delivery)

    def retry_save(self, task_id):
        """Complete retained prose, HTML and saving without model or data dispatch."""
        with self._executing(task_id):
            return self.agent.retry_save(task_id)

    def finish_draft(self, task_id):
        """Finish an interrupted draft from this version's observations and public originals."""
        with self._executing(task_id):
            return self.agent.start(task_id, retained_data_only=True)

    def update(self, task_id, cutoff=None):
        """Prepare only: read this version, reason in the host, then call run."""
        return self.agent.prepare_update(task_id, cutoff)

    def material(self, task_id, name, payload):
        """Return the parsed material associated with this upload."""
        return self.agent.add_material(task_id, name, payload, return_record=True)

    def remove_material(self, task_id, name, digest, *, expected_version, expected_revision, retained_files=None):
        """Exclude an unstarted input; its original stays archived without being adopted."""
        return self.agent.remove_material(task_id, name, digest, expected_version=expected_version,
            expected_revision=expected_revision, retained_files=retained_files)

    def snapshot(self, task_id, source, operation, parameters, response):
        task = self.agent.add_snapshot(task_id, source, operation, parameters, response)
        records = task["source_records"] + task.get("pending_source_records", [])
        retained = snapshot_record(task, source, operation, parameters, response)["snapshot"]
        return next(x for x in reversed(records) if x.get("source") == source and x.get("operation") == operation and
                    x.get("parameters") == parameters and x["snapshot"] == retained)

    def filing(self, task_id, directory, name=None, payload=None):
        task = self.agent.add_filing(task_id, directory, name, payload)
        records = task["source_records"] + task.get("pending_source_records", [])
        return next(x for x in reversed(records) if x.get("directory") == directory and
                    (x["snapshot"].get("material", {}).get("hash") == hashlib.sha256(payload).hexdigest() if payload is not None else "material" not in x["snapshot"]))

    def sources(self, task_id, artifact_ids=None, *, offset=0, limit=24):
        return self.agent.source_catalog(task_id, artifact_ids, offset=offset, limit=limit)

    def analysis_inputs(self, task_id, question_ids=None):
        """Read the same fact/metadata projection used by App reasoning."""
        return self.agent.reasoning_inputs(task_id, question_ids)

    def argument_review_inputs(self, task_id):
        return self.agent.argument_review_inputs(task_id)

    def review_arguments(self, task_id, submission):
        return self.agent.submit_argument_review(task_id, submission)

    def retry_original(self, task_id, original_id):
        """Reparse a same-version retained original, without downloading again."""
        return self.agent.retry_original(task_id, original_id)

    def document_review(self, task_id, artifact_id, question, expected_version, expected_snapshot_hash, *, confirmed=False):
        """After explicit user selection, prepare a separate document-only task."""
        return self.agent.document_review(task_id, artifact_id, question, expected_version,
                                          expected_snapshot_hash, confirmed=confirmed)

    def read(self, task_id, requests, binding):
        return self.agent.read_materials(task_id, requests, binding)

    def run(self, task_id, evidence=None, answers=None, theses=None, calculations=None, evidence_specs=None, data_requests=None,
            *, defer_delivery=False, supplement=None, reading_hash=None, judgment_reviews=None):
        """Host reasoning enters the same validators as the native App."""
        if not isinstance(defer_delivery, bool):
            raise ContractError("宿主研究检查点须明确指定，不能隐式延迟交付")
        # Only an omitted argument receives a default. Preserve falsey values
        # so the shared engine can reject wrong containers without repair.
        payload = {"evidence": [] if evidence is None else evidence,
                   "answers": {} if answers is None else answers,
                   "theses": [] if theses is None else theses,
                   "calculations": [] if calculations is None else calculations,
                   "evidence_specs": [] if evidence_specs is None else evidence_specs,
                   "data_requests": [] if data_requests is None else data_requests}
        if reading_hash is not None:
            payload["reading_hash"] = reading_hash
        if judgment_reviews is not None:
            payload['judgment_reviews'] = judgment_reviews
        if supplement is not None:
            if not isinstance(supplement, dict):
                raise ContractError("定向补证须为登记提案，不接受自由指令")
            payload["supplement"] = supplement
            if data_requests is None:
                payload["data_requests"] = supplement.get("data_requests", [])
            if calculations is None:
                payload["calculations"] = supplement.get("calculations", [])
        with self._executing(task_id):
            return self.agent.start(task_id, payload, defer_delivery=defer_delivery)

    def deliver(self, task_id):
        """Freeze a saved host checkpoint without replacing its research outputs."""
        with self._executing(task_id) as task:
            if not task.get("host_checkpoint") or task.get("delivery"):
                raise ContractError("只有未交付的宿主研究检查点可以直接收束")
            return self.agent.start(task_id)

    def research_methods(self, task_id=None):
        """Read the public method library or the method references for one task."""
        from researchhelper.research_methods import catalogue, method_selection
        return catalogue() if task_id is None else method_selection(self.archive.get(task_id))

    def visuals(self, family=None):
        """Read presentation choices only when the host needs them; no model call."""
        from researchhelper.visual_catalog import catalogue
        result=catalogue()
        if family is not None:result['schemes']=[row for row in result['schemes'] if row['family']==family]
        return result

    def export(self, task_id, version=None, *, visual_theme=None, visual_profile=None, profile=None, format="html"):
        if version is not None and (type(version) is not int or version < 1):
            raise ContractError("报告版本无效")
        number = self.archive.get(task_id)["version"] if version is None else version
        report = self.agent.export(task_id, number)
        output = ('result', 'researchhelper', task_id)
        if profile is not None or format!='html' or visual_profile is not None:
            from researchhelper.artifact_exporter import export_report
            directory = project_workspace_path(self.project, INSTALLATION, *output, 'exports')
            receipt=export_report(report['content'],self.agent.resources,directory,
                profile='standard' if profile is None else profile,format=format,
                theme=visual_theme,visual_profile=visual_profile)
            return Path(receipt['path'])
        html=report['html']
        if visual_theme is not None:
            from researchhelper.visual_catalog import theme
            if theme(visual_theme) is None:raise ContractError('配色未登记，请从visuals()读取可用风格')
            from researchhelper.reports import render
            html=render(report['content'],self.agent.resources,visual_profile=dict(theme=visual_theme))
        from researchhelper.artifact_exporter import save_frozen_html
        directory = project_workspace_path(self.project, INSTALLATION, *output)
        filename = f"research-v{number}"+("-"+visual_theme if visual_theme else "")+".html"
        return save_frozen_html(report["content"],html,directory,filename)
