"""Question-led plans shared by the App and host-Agent Skill.

Plans describe research obligations, not conclusions. They never choose a
provider field, enlarge an object universe, or approve a piece of evidence.
"""

from __future__ import annotations

import copy
import re

from .contracts import ContractError, DOCUMENT_CONTENT_INTENT, ENTRIES, INTENTS, METHOD_REGISTRY, canonical, fingerprint, methods_for_task

QUESTION_KINDS = ("fact", "comparison", "mechanism", "investment")
DIRECT_KINDS = {"fact", "comparison"}
REGISTERED_METHODS = set(METHOD_REGISTRY)
QUESTION_FIELDS = {"id", "question", "required", "kind", "group", "dependencies", "evidence_needs", "method_needs",
                   "answer_scope", "knowledge_needs", "evidence_need_scopes", "evidence_need_constraints", "evidence_period_targets"}
PRESERVED_SCALAR_FIELDS = ("question", "required", "kind", "group", "answer_scope", "evidence_need_scopes")
PRESERVED_LIST_FIELDS = ("dependencies", "evidence_needs", "method_needs", "knowledge_needs")
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
CONVERSATION_CONTEXT_LIMIT = 12000
RESEARCH_BRIEF_VERSION = 1
PLANNING_LIMITATION_GUIDANCE_VERSION = 3
CALCULATION_OBLIGATION_VERSION = 1
PLANNING_SCOPE_RULE = (
    "先按用户要求回答的内容分类，再安排呈现形式。研究报告、一页报告、图表或摘要只是交付形式，"
    "不构成投资论点、估值、盈利质量判断或因果解释的要求。"
    "明确仅核对财务原值并计算比率时，主问题及数值子问题用fact或comparison，"
    "即使入口为个股、意图为综合研究，也不因报告形式增加investment或mechanism问题、"
    "投资论点、估值和反证义务；requested_theses不得仅因这些呈现形式而为true。"
    "例如核对营业收入、报表净利润、经营现金流净额，计算净利润率及经营现金流对净利润覆盖，"
    "并生成带图表研究报告，仍是核对与计算，必须保留所需原值、主体、期间、合并口径及计算方法。"
    "用户实际要求投资判断、估值、盈利质量评价、原因或驱动解释，或开放式综合研究时，"
    "仍按实际内容保留investment或mechanism及必要依据，不能因同时要求计算或图表而降为数值摘录。"
    "仅在首次未采用计划中分类；不得改写原问题、自动批准证据、删改已采用义务或旧任务。"
)


def planning_scope_policy(task):
    """Versioned guidance, never a keyword classifier or a plan mutation."""
    if type(task.get('planning_scope_version')) is int and task['planning_scope_version'] == 1:
        return {'version': 1, 'rule': PLANNING_SCOPE_RULE,
                'automatic_plan_change': False, 'financial_approval': False}
    return None


CALCULATION_OBLIGATION_RULE = (
    "计算需求与可执行输入分开登记，不能因输入尚未取得而删除计算需求。"
    "用户明确要求实际计算且存在适用登记方法时，在对应问题method_needs保留精确方法id；"
    "仅选择原问题实际需要的方法，按methods中selection_scope区分原值比较和分项合计；"
    "相同或相近名称不是额外计算义务，不把方法清单全部登记为必需。独立交付确需多个方法时分别保留。"
    "尚未取得必要证据或口径时calculations可以为空，不填空参数、不猜证据id或默认口径。"
    "取证后可完善初始计算输入，再进入首次分析；实际需要新增原件、数据或解释时才进入定向补证。"
    "两者都只使用已登记问题和方法，必要输入齐备后才增加可执行计算。"
    "方法未登记或不适用时明确保留能力缺口，不自造实现，不套用其他资产方法。"
    "无需计算的文字核查保持空method_needs，不按入口、关键词或通用估值门槛自动选方法。"
)
PLANNING_LIMITATION_RULE = (
    "说明未知限制不等于核验未知事实。立题前按用户实际交付区分必需原件、计算口径与解释限制。"
    "只要求未复权价格变化并说明公司行为或总收益限制时，必需事实是适用价格与时点；"
    "公司行为状态在输入calculation_context中按原件记录，未核验可为unknown，依据为{kind:unverified}，"
    "这种未知记录不是必须新增取证的事实，不把unknown、present、verified_none三种状态清单登记为evidence_needs。"
    "这不表示无公司行为，也不证明总收益。明确要求核验分红、公司行为或持有人总回报时，"
    "对应原件与方法仍是必要义务，不能以unknown、用途说明或价格变化代替。"
    "明确的用户事实要求和已采用计划不得删除、降级或改为可选，不因缺数据改变原问题。"
)

# Product-level planning prompts, not financial knowledge or task obligations.
# Send only the selected entry and intent, rather than an eight-way playbook.
ENTRY_FOCUS = {
    "个股": ("把公司经营与上市证券分开，按问题连接业务、财务、变化驱动和投资含义。",
             ["涉及财务比较时核对主体、报告期与合并口径", "涉及证券判断时核对上市地及证券身份", "现金、估值与交易条件只按原问题启用"]),
    "ETF": ("把产品规则、实际暴露、基金净值与交易价格分开，比较用户指定维度。",
            ["涉及持仓时保留披露时点和覆盖范围", "费用、跟踪、汇率、折溢价与流动性按维度启用", "底层判断不直接等于基金收益或买入建议"]),
    "指数": ("区分编制规则、成分权重与区间表现，识别指数变化和市场宽度的差异。",
             ["涉及贡献时使用区间适用的成分与权重", "指数版本、基期和收益口径不能混合", "涉及交易表达时另核验实际产品"]),
    "行业": ("连接供需、竞争与盈利分化，明确行业结论和个体企业结论的边界。",
             ["比较前登记样本覆盖和聚合口径", "行业改善须检查企业相反表现", "代表样本不推断全市场最优或全面受益"]),
    "产业链": ("识别技术、瓶颈与价值分配，分别检查上下游及替代路线。",
              ["明确对象在链条中的环节和实际暴露", "同一价格变化可能对不同环节有相反影响", "共同驱动不重复计为独立投资机会"]),
    "事件": ("先分清已发生事件、传闻和条件情景，再核查渠道、对象与影响窗口。",
             ["采用事件前区分公布、生效与执行状态", "资格、实际暴露与市场预期按问题核验", "同期表现不直接证明事件因果"]),
    "宏观": ("区分环境变化、传导渠道和对象差异，评估相反渠道及期限。",
             ["采用数据前核对公布版本和研究截止时点", "折现、盈利、汇率及融资渠道按相关性选择", "充分评估后仍不可识别净方向可以形成受限判断"]),
    "混合": ("一个主问题下组织相关分支，并列独立问题保留各自条件，不强造因果链。",
             ["对象与共同来源去重但保留不同用途", "只有真实前置关系才设置依赖", "共同驱动不重复归因，各分支保留完成状态"]),
}
INTENT_GOALS = {
    "综合研究": "按原范围解释现状、关键驱动、分歧与风险，形成可验证的研究含义，不自动改为找买点。",
    "解释变化": "核对变化事实，比较解释与竞争解释，说明影响对象、窗口和识别限制。",
    "比较对象": "围绕用户指定维度，在一致时点和口径下解释差异，不将比较结果自动变成买入结论。",
    "验证观点": "明确待验证命题，结合支持与反证判断支持、反对或证据不足，并保留可推翻条件。",
    "发现投资机会": "核查收益或风险变化的依据和适用对象，按机会类型选择方法；允许充分核验后没有机会。",
    DOCUMENT_CONTENT_INTENT: "仅核对归档原件实际陈述，回答绑定原文位置，不确认历史可得性、金融因果或投资判断。",
}


def research_brief(task):
    """Return bounded planning guidance for both model and host-Agent callers.

    It never amends a plan, forces all domain checks or authorizes acquisition.
    Protected questions, evidence and methods still use their own validators.
    Legacy stage tests without full questions may read a detached descriptor;
    full tasks must retain their actual registered input identity.
    """
    entry, intent = task.get("entry", "混合"), task.get("intent", "综合研究")
    if entry not in ENTRIES or intent not in INTENTS:
        raise ContractError("研究入口或意图未登记，不能生成规划依据")
    if "questions" in task and task["input_hash"] != input_fingerprint(task):
        raise ContractError("规划依据与登记研究范围不一致")
    document_only = intent == DOCUMENT_CONTENT_INTENT
    focus, checks = ENTRY_FOCUS[entry]
    result = {
        "version": RESEARCH_BRIEF_VERSION,
        "binding": {"task_id": task["id"], "research_version": task["version"], "input_hash": task["input_hash"]},
        "entry": entry, "intent": intent,
        "declared_scope": {"question": task.get("question"), "objects": copy.deepcopy(task.get("objects", [])),
                           "cutoff": task.get("cutoff"), "research_mode": task.get("research_mode")},
        "entry_focus": "仅使用文字原件核对用途，不启用对象投资研究流程。" if document_only else focus,
        "intent_goal": INTENT_GOALS[intent],
        "conditional_checks": [] if document_only else list(checks),
        "selection_rule": "仅将影响原必要答案的检查纳入计划，已有明确问题优先；无关扩展不升级为核心问题。",
        "branch_rule": "事实、比较、机制与投资判断按实际交付分类；核心条件不能因缺数据或反证删除，可选问题不阻断独立核心分支。",
        "evidence_purpose": "document_content" if document_only else "research",
        "required_method_ids": [], "required_thesis_count": None,
        "advisory_only": True, "automatic_plan_change": False,
        "external_acquisition_allowed_by_brief": False, "financial_fact_or_approval": False,
    }
    from .financial_sources import structured_source_preference
    scope = planning_scope_policy(task)
    if scope is not None:
        result['requested_content_policy'] = scope
        result['intent_goal'] += '具体义务以用户原问题为准，研究报告及图表不自动要求投资判断。'
    preference = structured_source_preference(task)
    if preference is not None:
        result['source_preference'] = preference
    if (not document_only and task.get('current_scope_planning_version') == 1
            and task.get('research_mode') == 'current'):
        result['current_version_evidence_policy'] = {
            'version':1, 'advisory_only':True, 'automatic_plan_change':False,
            'rule':'按用户实际交付登记事实、数值和金融口径；已有适用正式报告可提供所需原值，不预设必须另取单项公告。'
                   '当前取得版本允许首次公开时间未知，不能将查明首发或另取专门净值公告无条件新增为核心事实。'
                   '未知披露日时，在用户资料范围和单次查询限制内，可让目录窗口覆盖所属期后至研究截止，再按报告标题与实际所属期筛选；不机械截为观测日附近或固定十五日。'
                   '公布日、所属日及取得日分别保留，未知照实披露。用户明确要求公布日期、历史可得性、事件因果或指定原件时，仍严格登记相应依据。'
                   '这不批准当前原件、币种或份额；已采用计划不删除、不改用途。'}
    if not document_only and type(task.get('method_source_coverage_version')) is int and task['method_source_coverage_version'] == 1:
        result['method_source_policy'] = dict(version=1, financial_approval=False, automatic_plan_change=False,
            rule='登记必要计算方法时同时规划其原值及单位、主体、份额、会计或时点口径的来源。'
                 '日期序列、净值原始列和结构化财务查询目前标记为raw_only时，不能单独作为可计算输入。'
                 '优先实际登记且口径完整的接口；必要时提前查询真实公告目录和适用原件，不等分析后才寻找净值或财务来源。'
                 '纯行情不强制下载PDF；原件路径就绪也不批准事实或计算，已有问题和方法义务不能删除。')
    return result


def conversation_context(task):
    """Return immutable conversational data, never evidence or instructions."""
    value = task.get("conversation_context")
    if value is None:
        return None
    if not isinstance(value, dict) or len(canonical(value)) > CONVERSATION_CONTEXT_LIMIT:
        raise ContractError("追问上下文格式或总长度无效")
    if task.get("input_hash") != input_fingerprint(task):
        raise ContractError("追问上下文与登记输入不一致，请重新读取当前版本")
    parent = value.get("parent_binding", {})
    if (value.get("version") != 1 or value.get("usage") != "reference_resolution_only"
            or parent.get("task_id") != task.get("parent")
            or not isinstance(parent.get("research_version"), int)
            or not isinstance(parent.get("input_hash"), str)):
        raise ContractError("追问上下文父版本绑定无效")
    return copy.deepcopy(value)


def question_contract(task=None):
    """Expose the actual validator vocabulary, not another model-only registry."""
    from .data_requirements import GUIDANCE
    from .period_targets import enabled as period_targets_enabled, GUIDANCE as PERIOD_GUIDANCE, FIELD as PERIOD_FIELD
    return {"fields": sorted(QUESTION_FIELDS), "kinds": list(QUESTION_KINDS),
            **({'requested_content_policy': planning_scope_policy(task)}
               if task and planning_scope_policy(task) is not None else {}),
            "necessity": {
                "required": "直接支撑主问题及关键判断；保留用户要求、关键反证、身份和适用口径。",
                "optional": "仅增强分析的指标和背景独立为required=false子问题，不进入核心答案的前置依赖。",
                "granularity": "指标水平和变化分别登记，不用任意两个观测代替两种不同要求。",
                "no_automatic_demotion": "该区分在首次规划完成；不因缺数据自动删改已采用的必要需求。"},
            **({'typed_data_requirements':GUIDANCE}
                if task and task.get('typed_evidence_needs_version')==1 else {}),
            **({'source_period_targets': dict(field=PERIOD_FIELD, guidance=PERIOD_GUIDANCE,
                shape='可选对象，键为对应evidence_needs的完整原文；值含family、relation、period。'
                      '只声明实际必要期间，默认不登记；不要求用户填写。')}
                if task and period_targets_enabled(task) else {}),
            "registered_method_ids": sorted(methods_for_task(task)),
            "list_fields": ["dependencies", "evidence_needs", "method_needs"],
            "dependencies": "仅引用本计划问题id，不填自然语言或方法名",
            "evidence_needs": "仅登记必要事实或独立检查的原件与字段；逐条核验已覆盖的身份、时点和单位不另造重复门槛，未知解释限制不是新增事实要求",
            **({'market_calendar_coverage': {'field': 'evidence_need_scopes', 'kind': 'fact',
                'scope': 'market_calendar_coverage',
                'rule': '若原问题要求核对交易日覆盖，保留一条独立日历核对需求，并以该需求完整原文为键登记本用途；价格等金融需求仍保持普通用途。内核按本问题实际价格窗口获取独立日历并核对，不把日历标成价格证据，也不要求另下PDF。不能用此用途代替价格、收益、证券状态或历史可得性。'}}
                if task and type(task.get('market_coverage_basis_version')) is int and task['market_coverage_basis_version'] == 1 else {}),
            "query_provenance": {"field": "evidence_need_scopes", "kind": "fact",
                "guidance_version": 2,
                "shape": "可选对象，键为本问题evidence_needs的完整逐字文字，值只能为query_provenance，不要求模型计算哈希",
                "rule": "按每条依据需求分类，不按整道问题分类。混合事实问题若明确登记接口、请求参数、返回记录等来源元数据需求，只把这些需求标为query_provenance，价格等金融需求保持普通用途。仅要求报告附来源时可直接引用，不新增独立来源需求；若计划实际新增了来源记录需求，必须同时登记该用途。实际价格、净值、费率、财务、收益及指标原值不得用查询留痕代替。无元数据需求时省略本字段。",
                "preservation": "已采用计划的用途分类逐字保留，不因缺数据将事实需求改为查询留痕"},
            "method_needs": "仅填registered_method_ids中的精确计算方法id；无需计算时为[]",
            "general_definition": {"answer_scope": "general_definition", "kind": "fact",
                "knowledge_needs": "明确的已发布通用口径条目id；完整正文、条件、限制和来源由内核生成",
                "forbidden": "无evidence_needs、method_needs或dependencies，不取数、不计算、不形成论点；不是当前或历史主体事实",
                "preservation": "只能在未采用的计划中明确分类，不能把既有事实、机制或投资判断降级为口径说明"},
            "ordinary_question_fields": "普通问题省略answer_scope和knowledge_needs，不填null或空列表占位",
            "obligation_policy": {
                "version": 1,
                "limitation_guidance_version": PLANNING_LIMITATION_GUIDANCE_VERSION,
                "calculation_obligations": {
                    "version": CALCULATION_OBLIGATION_VERSION,
                    "registration": "用户要求实际计算且有适用登记方法时，在对应问题method_needs登记精确方法id，不等待取得全部输入。",
                    "missing_inputs": "缺输入时保留计算节点与依赖，calculations可以为空；不填空参数、不猜证据id，不补造指数版本、币种或基期。",
                    "completion": "同方法在另一问题执行成功不能代替本问题计算；必要计算未完成保留缺口，可选分支不阻断独立核心答案。",
                    "automatic_method_selection": False,
                    "adopted_plan": "已采用方法义务不得因缺输入删除或降级，不自动修改既有任务、计算或冻结报告。",
                },
                "limitation_boundary": {
                    "explain_only": "只要求说明未知限制时，不登记为必须取证的事实；把实际未知口径记录在计算输入，并在回答保留限制。",
                    "verify_requested": "明确要求核验公司行为、分红或总回报时，不能以unknown替代必要原件、真实收益口径或方法。",
                    "adopted_plan": "已采用的必要事实不得删除、改写或降级；本指南不批准证据，不修改旧任务或交付状态。",
                },
                "classification": "按用户要求的交付判断分类，不按个股、ETF或默认综合研究入口一律归为investment。首次默认主问题可细化类别，原问题逐字保留。",
                "necessary_evidence": "evidence_needs只列回答该问题实际必需的事实依据。用途限制、没有取得的数据和可选扩展不自动成为原件必须提供的内容。",
                "conditional_analysis": "用户只要求说明口径边界时，不强制计算另一口径。选择回答净值收益、折溢价、持有人总回报或投资机会时，必须另登记对应问题、原件和必要方法，不借口径说明省略其真实依据。",
                "independent_branches": "事实核查、登记计算和因果或投资判断可以分问题；只有真实依赖才设置dependencies。可选分析required=false，不阻断已经独立满足的核心问题。",
                "preservation": "已采用的必要依据不能因缺数据删除、降级或替换。此策略只指导尚未采用的计划，不放宽旧任务、冻结报告、证据核验或预算。",
                "examples_are_facts": False,
                "requested_outputs": {
                    "区间未复权价格变化": {"kind": "fact", "method": "observed_price_return_v1",
                        "necessary": "同证券两端价格、日期、币种与复权口径；公司行为状态在计算口径中记录，允许unknown，不是额外原件义务；未核验时只计算未复权价格变化",
                        "not_implied": "不默认计算净值收益、持有人总回报、事件因果或买卖结论"},
                    "ETF折溢价": {"kind": "comparison", "method": "etf_nav_premium_v1",
                        "necessary": "同基金份额、币种及同一估值时点的市场价格与官方净值",
                        "not_implied": "不将旧净值或IOPV替代，不推断可实现套利"},
                    "持有人总回报": {"kind": "comparison", "method": None,
                        "necessary": "实际适用的收益口径、公司行为、现金分配与再投资处理；方法未登记或依据缺失时保留缺口",
                        "not_implied": "不套用未复权价格变化方法，不自造实现"},
                    "事件影响机制": {"kind": "mechanism", "method": None,
                        "necessary": "事件原件、影响对象、传导依据、竞争解释与适用条件",
                        "not_implied": "不从同期价格变化直接推出因果，不默认扩大为买卖推荐"},
                    "投资机会判断": {"kind": "investment", "method": None,
                        "necessary": "按问题核查基本面、价格含义、风险、反证与验证条件，必要方法按实际判断选择",
                        "not_implied": "不降为事实摘录，不统一强加某个估值方法或固定论点数量"},
                },
            },
            "no_calculation_example": {"kind": "fact", "dependencies": [],
                                       "evidence_needs": ["原件正文与明确的公开时点依据"], "method_needs": []}}


def _strings(value, label):
    if not isinstance(value, list) or len(value) > 50 or not all(isinstance(x, str) and x.strip() for x in value):
        raise ContractError(label + "必须为非空文字列表，最多50项")
    return list(dict.fromkeys(x.strip() for x in value))


def validate_definition_actions(questions, *action_lists):
    """Knowledge-only questions cannot grant a source or calculation request."""
    scoped = {q['id'] for q in questions if q.get('answer_scope') == 'general_definition'}
    if not scoped:
        return
    for actions in action_lists:
        for action in actions:
            if not isinstance(action, dict):
                raise ContractError("口径说明的相关请求须为结构化记录")
            selected = action.get('question_ids', [q['id'] for q in questions])
            if not isinstance(selected, list) or not all(isinstance(ident, str) for ident in selected):
                raise ContractError("相关请求必须明确对应本任务问题")
            if scoped.intersection(selected):
                raise ContractError("通用口径说明不采用事实取证、金融计算或论点，具体研究须另列问题")


def ordered(items, dependencies):
    """Stable topological order; never infer a causal edge from display order."""
    pending, result = list(items), []
    while pending:
        ready = [key for key in pending if set(dependencies[key]).issubset(result)]
        if not ready:
            raise ContractError("研究依赖存在循环或未知节点")
        result.extend(ready)
        pending = [key for key in pending if key not in ready]
    return result


def normalize_questions(questions, question, intent):
    if questions is None:
        questions = [question]
    if not isinstance(questions, list) or not 1 <= len(questions) <= 50:
        raise ContractError("研究问题必须为非空列表，最多50项")
    default_kind = "fact" if intent == DOCUMENT_CONTENT_INTENT else "comparison" if intent == "比较对象" else "mechanism" if intent == "解释变化" else "investment"
    result = []
    for index, original in enumerate(questions):
        item = {"question": original} if isinstance(original, str) else copy.deepcopy(original)
        if not isinstance(item, dict) or set(item) - QUESTION_FIELDS:
            raise ContractError("研究问题字段未登记")
        text = item.get("question")
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise ContractError("研究问题必须为非空文字，最长20000字")
        ident, group = item.get("id", f"q{index+1}"), item.get("group", "main")
        if not isinstance(ident, str) or not IDENTIFIER.fullmatch(ident) or not isinstance(group, str) or not IDENTIFIER.fullmatch(group):
            raise ContractError("问题与问题组标识无效")
        kind, required = item.get("kind", default_kind), item.get("required", True)
        if kind not in QUESTION_KINDS or not isinstance(required, bool):
            raise ContractError("问题类别或必要性无效")
        normalized = {"id": ident, "question": text.strip(), "kind": kind, "required": required, "group": group}
        for field in ("dependencies", "evidence_needs", "method_needs"):
            normalized[field] = _strings(item.get(field, []), field)
        if 'evidence_need_constraints' in item:
            from .data_requirements import normalized as need_constraints
            normalized['evidence_need_constraints']=need_constraints(item)
        if 'evidence_period_targets' in item:
            from .period_targets import normalized as period_targets
            normalized['evidence_period_targets'] = period_targets(item)
        if "evidence_need_scopes" in item:
            scopes = item["evidence_need_scopes"]
            allowed = set(normalized["evidence_needs"])
            if (kind != "fact" or intent == DOCUMENT_CONTENT_INTENT or "answer_scope" in item
                    or not isinstance(scopes, dict) or not scopes
                    or any(not isinstance(key, str) or key not in allowed or value not in ("query_provenance", 'market_calendar_coverage')
                           for key, value in scopes.items())):
                raise ContractError("查询留痕需求须在普通事实计划中明确绑定，不能替代金融事实或判断")
            normalized["evidence_need_scopes"] = copy.deepcopy(scopes)
            if set(scopes) == allowed and normalized["method_needs"]:
                raise ContractError("纯查询留痕不能要求金融计算，实际金融分析须另列必要依据")
        if "answer_scope" in item or "knowledge_needs" in item:
            if (item.get("answer_scope") != "general_definition" or kind != "fact" or
                    any(normalized[field] for field in ("dependencies", "evidence_needs", "method_needs")) or
                    intent == DOCUMENT_CONTENT_INTENT):
                raise ContractError("通用口径说明须独立标明范围，不能承载事实取证、计算、依赖或投资判断")
            needs = _strings(item.get("knowledge_needs", []), "knowledge_needs")
            if not needs or any(not IDENTIFIER.fullmatch(ident) or not ident.startswith("rh_k_") for ident in needs):
                raise ContractError("通用口径说明须明确选择知识条目")
            normalized.update(answer_scope="general_definition", knowledge_needs=needs)
        if set(normalized["method_needs"]) - REGISTERED_METHODS:
            raise ContractError("问题引用未登记的计算方法")
        if intent == DOCUMENT_CONTENT_INTENT and (kind != "fact" or normalized["method_needs"]):
            raise ContractError("原件内容核对仅支持原文事实问题，不生成金融计算或投资判断")
        result.append(normalized)
    ids = [q["id"] for q in result]
    if len(set(ids)) != len(ids) or not any(q["required"] for q in result):
        raise ContractError("问题标识不能重复，且至少保留一项核心问题")
    if any(set(q["dependencies"]) - set(ids) or q["id"] in q["dependencies"] for q in result):
        raise ContractError("问题依赖不存在或引用自身")
    sequence = ordered(ids, {q["id"]: q["dependencies"] for q in result})
    return sorted(result, key=lambda q: sequence.index(q["id"]))


def question_specs(task):
    return [{key: copy.deepcopy(q[key]) for key in QUESTION_FIELDS if key in q} for q in task["questions"]]


def research_outline_contract(task):
    """Shared writing guidance, never additional evidence or approval."""
    version = task.get('research_outline_version')
    if version is None:
        return None
    if type(version) is not int or version != 1:
        raise ContractError('研究论证结构版本未登记')
    if task.get('intent') == DOCUMENT_CONTENT_INTENT:
        return None
    main = [question['id'] for question in task.get('questions', [])
            if question.get('required') and question.get('question') == task.get('question')]
    return dict(version=1, main_question_ids=main if len(main) == 1 else [],
        core_conclusion='主问题的answer直接回答研究问题，综合判断、适用对象、成立条件和主要不确定性；不只列计算结果或指向子问题。',
        research_logic='按具体问题提出待验证解释、相反解释和取证需求。theses各自形成可独立评估的逻辑，说明驱动或机制、实际证据、投资含义与边界；不用数据复述替代论证。',
        linkage='每条论点显式关联其实际question_ids、support、counter和calculation_refs。不要把主问题的全部计算自动当作每条论点的支持；没有完整序列就用适用表格，不补造图表数据。',
        scope='事实、直接比较或原件核对不强制投资论点；逻辑条数不限，不强制看多或发现机会。证据不足只限制依赖它的判断，不删除其他已成立内容。',
        output='只填写既有answer与theses字段，不另写Markdown整份报告或新增结论字段；核心结论、策略逻辑编号和图表排布由呈现层生成。',
        automatic_approval=False, requires_extra_model_call=False)



def _direct_question_kind(task):
    required = [question for question in task.get('questions', []) if question.get('required', True)]
    if not required or any(question.get('kind') not in DIRECT_KINDS for question in required):
        return None
    return 'comparison' if any(question['kind'] == 'comparison' for question in required) else 'fact'


def draft_direct_scope(task):
    version = task.get('draft_direct_scope_version')
    if version is None:
        return None
    if type(version) is not int or version != 1:
        raise ContractError('备用内容组织规则未登记')
    return _direct_question_kind(task)


def draft_research_scope(task):
    """Scope guidance does not rewrite obligations or grant tool permissions."""
    version = task.get('draft_question_scope_version')
    if version is None:
        return None
    if type(version) is not int or version not in (1, 2):
        raise ContractError('研究问题范围规则未登记')
    required = [question for question in task.get('questions', []) if question.get('required', True)]
    mode = _direct_question_kind(task) or 'research'
    if version == 2 and any(question.get('kind') == 'mechanism' for question in required) and not any(
            question.get('kind') == 'investment' for question in required):
        mode = 'mechanism'
    initial_default = (task.get('question_origin') == 'default' and task.get('plan_version', 1) == 1
        and task.get('intent') == '综合研究' and len(required) == 1
        and required[0].get('question') == task.get('question') and required[0].get('kind') == 'investment')
    if initial_default:
        mode = 'by_question'
    questions = []
    for question in task.get('questions', []):
        row = {key: question[key] for key in ('id', 'kind', 'required') if key in question}
        if question.get('question') == task.get('question'):
            row['question_ref'] = 'main'
        else:
            row['question'] = question.get('question', '')
        for key in ('evidence_needs', 'method_needs'):
            if question.get(key):
                row[key] = list(question[key])
        questions.append(row)
    allowed = ['fact', 'comparison'] + (['mechanism'] if version == 2 else []) + ['research']
    rule = ('questions中的investment只是初始默认分类，按原问题决定核对、比较、机制解释或研究，不默认扩展为投资建议。'
            if initial_default else '按照必要问题的实际类型取证与写作，不用可选问题扩大主问题范围。')
    if version == 1 and initial_default:
        rule = 'questions中的investment只是初始默认分类，按原问题决定核对、比较或研究，不默认扩展为投资建议。'
    if mode == 'mechanism':
        rule += '本次回答变化的可能机制、传导条件与竞争解释；同期变动不是因果证据，不自动增加投资机会或买卖判断。'
    return dict(version=version, mode=mode, questions=questions,
        question_scope_is_default=initial_default,
        allowed_report_scopes=allowed if initial_default else [mode],
        changes_question_obligations=False, extra_model_calls=0,
        rule=rule)


def thesis_obligations(task, question_ids, calculation_refs):
    """Find unsatisfied prerequisites of a determinate research judgment.

    An execution node having run is not evidence that its questions were
    answered. Nor may a calculation for a sibling satisfy this question's
    declared method. The engine and save gate use this same rule. It does not
    approve prose or prevent an explicitly insufficient, conditional finding.
    """
    questions = {question['id']: question for question in task['questions']}
    calculations = {item['id']: item for item in task['calculations']}
    if any(ident not in questions for ident in question_ids) or any(ref not in calculations for ref in calculation_refs):
        raise ContractError('论点前置条件引用未登记问题或计算')
    missing = []
    for ident in question_ids:
        question = questions[ident]
        dependencies = [dep for dep in question.get('dependencies', [])
                        if dep not in questions or questions[dep]['status'] != '已回答']
        methods = {calculations[ref]['method'] for ref in calculation_refs
                   if ident in calculations[ref].get('question_ids', question_ids)}
        required = [method for method in question.get('method_needs', []) if method not in methods]
        if dependencies or required:
            missing.append(dict(question_id=ident, missing_dependencies=dependencies, missing_methods=required))
    return missing


def initial_decomposition(task):
    # A rejected first proposal never adopted a research plan. A retry batch
    # must still be able to classify the unchanged default question; accepted
    # plans and new research versions remain protected.
    return task.get("question_origin") == "default" and task.get("version", 1) == 1 and task.get("plan_version", 1) == 1


def plan_preservation_contract(task):
    """Expose the same exact invariants enforced before tool dispatch."""
    prior = normalize_questions(question_specs(task), task["question"], task["intent"])
    initial = initial_decomposition(task)
    protected = []
    for question in prior[:1] if initial else [q for q in prior if q["required"]]:
        fields = ("question", "required") if initial else PRESERVED_SCALAR_FIELDS
        protected.append({"id": question["id"], "exact_fields": {key: copy.deepcopy(question[key]) for key in fields if key in question},
                          "retained_list_items": {} if initial else {
                              key: copy.deepcopy(question[key]) for key in PRESERVED_LIST_FIELDS if key in question}})
    return {"version": 1, "binding": {"task_id": task["id"], "research_version": task["version"],
            "input_hash": task["input_hash"]}, "initial_decomposition": initial,
            "protected_questions": protected, "list_changes": "可增加必要事项，但必须逐字保留已登记项；不以同义解释替换",
            "format_is_approval": False}


def input_fingerprint(task):
    adopted = {key: task[key] for key in ("question", "entry", "intent", "objects", "cutoff")}
    if "research_mode" in task:
        adopted["research_mode"] = task["research_mode"]
    if 'series_batch_version' in task:
        adopted['series_batch_version'] = task['series_batch_version']
    if 'source_capability_roadmap_version' in task:
        adopted['source_capability_roadmap_version'] = task['source_capability_roadmap_version']
    if 'economic_navigation_version' in task:
        adopted['economic_navigation_version'] = task['economic_navigation_version']
    if 'financial_statement_unit_scope_version' in task:
        adopted['financial_statement_unit_scope_version'] = task['financial_statement_unit_scope_version']
    if 'financial_response_navigation_version' in task:
        adopted['financial_response_navigation_version'] = task['financial_response_navigation_version']
    if 'research_outline_version' in task:
        adopted['research_outline_version'] = task['research_outline_version']
    if 'hk_code_alias_version' in task:
        adopted['hk_code_alias_version'] = task['hk_code_alias_version']
    if 'draft_quote_scope_version' in task:
        adopted['draft_quote_scope_version'] = task['draft_quote_scope_version']
    if 'draft_source_reader_version' in task:
        adopted['draft_source_reader_version'] = task['draft_source_reader_version']
    if 'draft_direct_scope_version' in task:
        adopted['draft_direct_scope_version'] = task['draft_direct_scope_version']
    if 'draft_question_scope_version' in task:
        adopted['draft_question_scope_version'] = task['draft_question_scope_version']
    # Preserve hashes for older tasks without conversational context.
    if "conversation_context" in task:
        adopted["conversation_context"] = task["conversation_context"]
    # Address rejection is an extraction-only policy, already included in
    # the extraction digest. It must not invalidate research/source identity.
    for policy in ('request_schema_sharing_version', 'review_revision_readiness_version', 'registered_source_reading_version',
                   'api_need_compatibility_version', 'discovery_planned_scope_version', 'financial_batch_query_version', 'semantic_query_encoding', 'financial_batch_followup_version',
                   'progressive_financial_acquisition_version', 'declared_reading_capacity_version', 'official_catalogue_pagination_version', 'independent_original_selections_version',
                   'index_weight_evidence_version', 'analysis_input_presentation_version',
                   'review_choice_delivery_version', 'identity_candidate_lookup_version',
                   'name_candidate_observation_version', 'original_number_catalogue_capacity_version',
                   'original_explicit_unit_reference_version', 'acquisition_reading_intent_version',
                   'financial_acquisition_route_version', 'original_duration_anchor_version', 'catalogue_alternative_version', 'typed_evidence_needs_version', 'typed_observation_contract_version', 'typed_numeric_selection_version', 'required_numeric_completion_version', 'original_observation_reference_version', 'shared_original_dates_version', 'source_extraction_batches_version', 'source_extraction_id_choices_version', 'official_index_sources_version'):
        if policy in task:
            adopted[policy] = task[policy]
    for policy in ('filing_candidate_distribution_version', 'filing_filter_profile_version', 'security_declaration_encoding', 'analysis_prose_reference_version', 'financial_quote_fallback_version', 'discovery_sample_scope_version', 'acquisition_request_encoding', 'object_discovery_version', 'filing_keyword_filter_version', 'analysis_question_map_version', 'original_question_link_version', 'original_context_question_link_version', 'analysis_feedback_projection_version', 'analysis_structure_recovery_version', 'argument_choice_recovery_version', 'navigation_question_scope_version', 'literal_quantity_reference_version', 'original_span_diagnostics_version', 'review_basis_presentation_version', 'analysis_question_scope_version', 'statistical_reading_version',
                   'research_synthesis_version', 'financial_selector_projection_version', 'financial_observation_reference_version', 'financial_question_owner_version', 'negative_claim_scope_version', 'financial_need_map_version', 'financial_input_reading_priority_version', 'knowledge_reading_navigation_version', 'analysis_schema_sharing_version', 'analysis_reference_choice_recovery_version', 'partial_fee_input_completion_version', 'initial_fee_input_completion_version', 'review_delivery_call_version', 'original_fee_context_mapping_version', 'original_literal_choice_recovery_version', 'original_concept_reading_version', 'catalog_scoped_writing_version', 'general_original_selection_version', 'statistical_period_version',
                   'official_selection_reference_version', 'current_scope_planning_version', 'catalog_metadata_basis_version', 'original_temporal_choice_recovery_version', 'ifind_gateway_recovery_version', 'extraction_knowledge_guidance_version', 'original_concept_navigation_version', 'catalog_query_result_basis_version', 'original_interval_context_version', 'listed_report_unit_context_version', 'structured_schema_sharing_version', 'method_excerpt_selection_version', 'method_literal_address_version', 'original_local_span_version', 'original_whole_part_selection_version', 'catalogue_search_navigation_version',
                   'original_requirement_isolation_version', 'original_requirement_feedback_version', 'source_period_targets_version', 'knowledge_catalogue_revision', 'evidence_processing_state_version', 'original_scope_wire_version', 'series_parameter_selection_version', 'series_parameter_registry', 'document_date_role_version', 'source_label_selection_version', 'statistical_metric_identity_version', 'mixed_question_revision_version',
                   'extraction_value_recovery_version', 'original_number_reference_version',
                   'evidence_gap_lifecycle_version', 'method_source_coverage_version', 'acquisition_budget_navigation_version',
                   'acquisition_downstream_budget_version', 'registered_api_source_paths_version', 'model_context_budget_version',
                   'missing_api_context_selection_version', 'daily_nav_date_roles_version', 'observed_window_date_roles_version', 'market_coverage_basis_version', 'reviewed_writing_revision_version', 'nav_original_choice_version',
                   'nav_original_only_selection_version', 'official_etf_pcf_version', 'api_context_wire_version', 'flat_api_selection_version',
                   'official_etf_cell_selection_version', 'registered_etf_share_join_version', 'review_answer_choices_version', 'source_field_inventory_version', 'official_index_profile_catalogue_version', 'ifind_metadata_sources_version', 'partial_financial_response_version', 'structured_source_priority_version', 'fixed_financial_catalog_version',
                   'market_calendar_coverage_version', 'economic_identifier_namespace_version', 'economic_discovery_version', 'economic_observation_version', 'metadata_navigation_progress_version', 'fixed_financial_route_version', 'fixed_financial_context_version', 'extraction_read_isolation_version', 'mixed_atomic_api_reading_version',
                   'planning_scope_version', 'planning_main_binding_version', 'original_period_selection_version', 'original_prompt_projection_version', 'method_literal_quantity_version', 'original_observation_selection_version', 'index_constituent_source_version', 'candidate_code_observation_version', 'fee_method_catalogue_version', 'nonfinancial_count_grammar_version', 'numeric_lookup_isolation_version'):
        if policy in task:
            adopted[policy] = task[policy]
    def digest(calculations):
        return fingerprint(dict(adopted,questions=question_specs(task),calculation_specs=calculations,
            requested_theses=task.get('requested_theses',False),data_need_specs=task.get('data_need_specs',[])))
    calculations=task.get('calculation_specs',[])
    binding=task.get('reasoning_state',{}).get('initial_calculation_binding',{})
    if (binding.get('derived_plan_version')==1 and binding.get('status')=='已登记'
            and isinstance(binding.get('input_calculation_specs'),list)
            and binding.get('registered_plan_hash')==fingerprint(calculations)):
        base=digest(binding['input_calculation_specs'])
        if base==binding.get('input_base_hash'):
            # Late bindings are executable products of this immutable input,
            # not a new research scope. Exact plan and base hashes are required;
            # edits or forged/stale metadata fall back to the ordinary digest.
            return base
    return digest(calculations)


def check_revision(previous, proposed):
    """Missing data cannot erase or weaken an existing necessary question."""
    by_id = {q["id"]: q for q in proposed}
    for old in previous:
        if not old["required"]:
            continue
        new = by_id.get(old["id"])
        if not new or any(new.get(key) != old.get(key) for key in PRESERVED_SCALAR_FIELDS):
            raise ContractError("计划修订不能删除、改写或降级既有核心问题")
        for field in PRESERVED_LIST_FIELDS:
            if not set(old.get(field, [])).issubset(new.get(field, [])):
                raise ContractError("计划修订不能删除核心问题的已登记依据或依赖")


def validate_question_revision(task, proposed):
    """Use one preservation gate for first model plans and host revisions."""
    from .data_requirements import validate_plan as validate_need_plan
    validate_need_plan(task,proposed)
    from .period_targets import validate_plan as validate_period_plan
    validate_period_plan(task, proposed)
    methods = methods_for_task(task)
    if any(set(question.get('method_needs', [])) - methods.keys() for question in proposed):
        raise ContractError('计算方法不属于本任务已采用的目录版本，不自动升级')
    prior = normalize_questions(question_specs(task), task["question"], task["intent"])
    if initial_decomposition(task):
        main = next((question for question in proposed if question["id"] == prior[0]["id"]), None)
        if not main or main["question"] != prior[0]["question"] or not main["required"]:
            raise ContractError("首次拆解不能删除、改写或降级用户主问题")
    else:
        check_revision(prior, proposed)


def build_plan(task):
    questions = task["questions"]
    from .period_targets import validate_plan as validate_period_plan
    validate_period_plan(task, questions)
    methods = methods_for_task(task)
    if any(set(question.get('method_needs', [])) - methods.keys() for question in questions):
        raise ContractError('研究问题包含本任务方法目录之外的计算')
    if task.get("intent") == DOCUMENT_CONTENT_INTENT and (task.get("calculation_specs") or task.get("requested_theses") or task.get("data_need_specs")):
        raise ContractError("原件内容核对只读取留存原件，不派发取数、计算或投资论点")
    ids = [q["id"] for q in questions]
    # A method obligation exists before its executable inputs are available.
    # Keep that dependency visible without manufacturing a calculation spec.
    calculation_ids = {q["id"] for q in questions if q.get("method_needs")}
    for spec in task.get("calculation_specs", []):
        if not isinstance(spec, dict) or spec.get("method") not in methods:
            raise ContractError("计划计算方法未登记")
        related = _strings(spec.get("question_ids", ids), "计算对应问题")
        if not related or set(related) - set(ids):
            raise ContractError("计算对应问题不存在")
        calculation_ids.update(related)
    groups = {}
    for q in questions:
        route = "answer" if q["kind"] in DIRECT_KINDS else "thesis"
        # A display group is not an execution barrier. Only siblings with the
        # same prerequisites may share a node: contracting an intervening
        # answer -> thesis -> answer path would otherwise create a cycle.
        # Keep calculation-free questions independent of calculated siblings.
        key = (route, q.get("group", "main"), tuple(sorted(q.get("dependencies", []))),
               q["id"] in calculation_ids)
        groups.setdefault(key, []).append(q)
    if task.get("requested_theses") and not any(key[0] == "thesis" for key in groups):
        groups[("thesis", "main", (), False)] = questions
    nodes = []

    def node(ident, title, operation, deps, selected, kind, completion, **extra):
        return dict(id=ident, title=title, operation=operation, dependencies=list(dict.fromkeys(deps)),
                    status="待执行", questions=[q["id"] for q in selected], kind=kind,
                    evidence_needs=list(dict.fromkeys(x for q in selected for x in q.get("evidence_needs", []))),
                    method_needs=list(dict.fromkeys(x for q in selected for x in q.get("method_needs", []))),
                    completion=completion, termination="只限制依赖缺失依据的问题，保留独立分支结果",
                    resource_limits=copy.deepcopy(task["ledger"]["limits"]), input_hash=task["input_hash"], **extra)

    nodes.append(node("identity", "核验对象与必要背景", "rh_evidence.fetch", [], questions, "identity", "对象身份、研究时点及适用知识留档"))
    nodes.append(node("evidence", "定向取证与两类核验", "rh_analyzer.analyze", ["identity"], questions, "evidence", "保留原件定位，分别记录来源与适用性核验"))
    if calculation_ids:
        nodes.append(node("calculation", "按问题执行登记计算", "rh_analyzer.calculate", ["evidence"],
                          [q for q in questions if q["id"] in calculation_ids], "calculation", "原值、单位、方法版本及复算结果一致"))
    # Reserve every original group name before allocating split-node suffixes,
    # so a user group such as main_2 cannot collide with a split of main.
    bases, counts = {}, {"answer": 0, "thesis": 0}
    for route, group, _, _ in groups:
        if (route, group) not in bases:
            counts[route] += 1
            bases[(route, group)] = route if counts[route] == 1 else f"{route}_{group}"
    identifiers, used = {}, set()
    reserved = set(bases.values())
    owners = {}
    for key, selected in groups.items():
        route, group, _, _ = key
        base = bases[(route, group)]
        ident, suffix = base, 2
        while ident in used or (ident != base and ident in reserved):
            ident = f"{base}_{suffix}"
            suffix += 1
        used.add(ident)
        identifiers[key] = ident
        for q in selected:
            if (q["kind"] in DIRECT_KINDS) == (route == "answer"):
                owners[q["id"]] = ident
    for key, selected in groups.items():
        route = key[0]
        ident = identifiers[key]
        deps = ["evidence"]
        if any(q["id"] in calculation_ids for q in selected):
            deps.append("calculation")
        deps += [owners[dep] for q in selected for dep in q.get("dependencies", []) if owners[dep] != ident]
        nodes.append(node(ident, "核查事实与直接比较" if route == "answer" else "处理反证与形成研究论点",
                          "rh_analyzer.analyze" if route == "answer" else "rh_thesis.build_thesis", deps, selected, route,
                          "每个核心问题有可定位回答；机制与投资判断保留适用论点"))
    branches = list(identifiers.values())
    nodes.append(node("compose", "冻结同版报告内容", "rh_report.compose", branches, questions, "compose", "回答、引用、缺口与交付状态一致"))
    nodes.append(node("render", "离线呈现与保存", "rh_visual.render", ["compose"], questions, "render", "同版正文、图表及来源完整保存"))
    sequence = ordered([n["id"] for n in nodes], {n["id"]: n["dependencies"] for n in nodes})
    return sorted(nodes, key=lambda n: sequence.index(n["id"]))
