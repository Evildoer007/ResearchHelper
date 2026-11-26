"""Versioned contracts, identities, budgets and registered deterministic methods."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone

PROTOCOL = "researchhelper.module-request"
OPERATIONS = {
    "rh_evidence.fetch", "rh_analyzer.analyze", "rh_analyzer.calculate",
    "rh_thesis.build_thesis", "rh_report.compose", "rh_visual.render", "rh_design.design", "rh_export.export",
    "rh_knowledge.read", "rh_archive.save",
}
ENTRIES = ("个股", "ETF", "指数", "行业", "产业链", "事件", "宏观", "混合")
DOCUMENT_CONTENT_INTENT = "原件内容核对"
DOCUMENT_CONTENT_SCOPE = "document_content"
DOCUMENT_CONTENT_LIMITATION = "仅核对原件内容，不确认首次公开时间或历史可得性，不形成投资判断。"
INTENTS = ("综合研究", "解释变化", "比较对象", "验证观点", "发现投资机会", DOCUMENT_CONTENT_INTENT)
METHOD_REGISTRY = {
    "management_custody_fee_comparison_v1": {"implementation_version": 1,
        "knowledge_id": "rh_k_management_custody_fee", "title": "年度管理托管费率比较",
        "selection_scope": "原问题只要求比较管理费与托管费两项合计的差时，使用四项原值的本方法。需要列出两边合计时用management_custody_fee_summary_v1，不重复登记本方法；不把管理费单项差当作合计差。",
        "input_units": {"management_a": "%/年", "custody_a": "%/年", "management_b": "%/年", "custody_b": "%/年"},
        "input_unit_variants": {field: ["%/年", "%"] for field in ("management_a", "custody_a", "management_b", "custody_b")},
        "assumption_units": {}, "parameters": {"unit": "%/年"}, "context_parameters": ["period"],
        "output_unit": "百分点/年", "binding_context": ["fund_id", "share_class", "period", "fee_basis", "rate_period", "metric"],
        "conditions": "逐项核对两基金对应份额的管理和托管年费率、共同适用期间及净资产计费基数；原件单位为%时须保留%，年度频率由原件独立核验，不改写原始单位或年化；不将费率合计当作原件披露值。",
        "limitations": "仅比较管理费与托管费两项名义年费率合计，不是全部费用率、实际扣费金额或持有收益差。不计销售服务、交易及其他费用；减免和费用调整须使用同期间实际适用规则。"},
    "fee_difference_v1": {"implementation_version": 1, "knowledge_id": "rh_k_fee_difference", "title": "同口径年度费用差",
        "selection_scope": "原问题比较原件分别直接披露的两项同定义年度费用率时使用。分项费率相加不是本方法的原件输入；管理费单项差不能代替管理与托管合计差。独立要求单项费用差时才另登记。",
        "input_units": {"fee_a": "%/年", "fee_b": "%/年"}, "assumption_units": {},
        "parameters": {"unit": "%/年"}, "output_unit": "百分点/年",
        "conditions": "费用规则及期间可比，不解释为实际收益差"},
    "residual_present_value_v1": {"implementation_version": 1, "knowledge_id": "rh_k_residual", "title": "剩余现金流现值",
        "input_units": {"receipt": "百万元", "debt": "百万元", "cost": "百万元", "shares": "百万股", "price": "元/股"},
        "assumption_units": {"rate": "比例", "years": "年"},
        "parameters": {"amount_unit": "百万元", "shares_unit": "百万股"}, "output_unit": "元/股",
        "conditions": "收款、债务、费用、股本及分配已核验；回报率与期限为有依据的明确假设，不使用默认值"},
    "observed_price_return_v1": {"implementation_version": 1, "knowledge_id": "rh_k_price_change", "title": "已观测区间价格变化",
        "input_units": {"start_price": "元", "end_price": "元"}, "assumption_units": {},
        "parameters": {"unit": "元", "currency": "CNY", "return_kind": "price_change", "price_basis": "unadjusted"},
        "context_parameters": ["start_date", "end_date"], "output_unit": "%",
        "conditions": "同证券人民币未复权收盘价，明确起终交易日；公司行为状态记录为unknown、present或verified_none，不假定不存在公司行为。",
        "limitations": "仅未复权价格变化。公司行为存在或未知时尤其不能解释投资表现；任何状态均不是总收益、事件因果、超额收益或概率。",
        "output_label": "price_change_percent",
        "binding_context": ["security_id", "currency", "price_basis", "price_kind", "observation_date", "window_start", "window_end", "corporate_action_status"]},
    "observed_index_change_v1": {"implementation_version": 1, "knowledge_id": "rh_k_index_change", "title": "已观测指数区间变化",
        "input_units": {"start_level": "点", "end_level": "点"}, "assumption_units": {},
        "parameters": {"unit": "点", "return_kind": "index_level_change", "level_kind": "close"},
        "context_parameters": ["start_date", "end_date", "index_variant", "index_currency"], "output_unit": "%",
        "conditions": "同指数、同价格或全收益版本、同计价币种、同基期的明确起终日收盘点位；基期变化须先取得统一可比序列，不自行拼接或换算。",
        "limitations": "仅已观测同版指数点位变化。价格版本不含普通现金分红再投资；全收益与净全收益按原编制规则解释，不代表基金或用户实际收益、超额收益、事件因果或预测概率。",
        "output_label": "index_level_change_percent",
        "binding_context": ["index_id", "index_variant", "index_currency", "index_base", "level_kind", "observation_date", "window_start", "window_end"]},
    "etf_nav_premium_v1": {"implementation_version": 1, "knowledge_id": "rh_k_nav_premium", "title": "同估值时点ETF净值折溢价",
        "input_units": {"price": "元", "nav": "元"}, "assumption_units": {},
        "parameters": {"unit": "元", "currency": "CNY", "price_kind": "close", "nav_kind": "official"},
        "context_parameters": ["valuation_time"], "output_unit": "%",
        "conditions": "同基金份额类别、币种及估值时点的收盘价与已公布官方单位净值；不接受IOPV，不认定可实现套利或低估。",
        "limitations": "两个原值均对应同类的一份基金份额；单位元不代替份额类别、单位净值及估值时点的原件核验。仅有旧净值或估计净值时不计算。",
        "binding_context": ["fund_id", "share_class", "currency", "valuation_time", "price_kind或nav_kind"]},
    "etf_daily_nav_premium_v1": {"implementation_version": 1, "knowledge_id": "rh_k_daily_nav_premium",
        "title": "收盘价与同估值日净值偏离率", "input_units": {"price": "元", "nav": "元"}, "assumption_units": {},
        "selection_scope": "原问题要求日度收盘折溢价或收盘价与同估值日净值的事后比较时使用。明确要求同刻价差时仍用etf_nav_premium_v1并核验完整时点；不得替换已经登记的同刻义务，也不因缺输入删除任何计算需求。",
        "parameters": {"unit": "元", "currency": "CNY", "price_kind": "close", "nav_kind": "official"},
        "context_parameters": ["valuation_date"], "output_unit": "%",
        "conditions": "同基金份额类别、人民币及明确估值日的收盘价与官方单位净值；两者均按同一份基金份额核验。仅作估值日层面的事后比较，不认定同一时刻可成交。",
        "limitations": "公布日期与净值所属日分开。不得填补盘中时刻，不接受IOPV、旧净值、未知份额或跨币种；本比值不代表同刻价差、可实现套利、低估、投资收益或预测。",
        "binding_context": ["fund_id", "share_class", "currency", "valuation_date", "price_kind或nav_kind"]},
    "gross_margin_v1": {"implementation_version": 1, "knowledge_id": "rh_k_gross_margin", "title": "同口径营业毛利率",
        "input_units": {"revenue": "百万元", "cost": "百万元"}, "assumption_units": {},
        "parameters": {"amount_unit": "百万元", "currency": "CNY"},
        "context_parameters": ["period", "accounting_scope", "accounting_standard"], "output_unit": "%",
        "conditions": "同主体、同报告期间、同合并范围及会计准则的人民币营业收入与营业成本；收入须正，成本非负，不使用营业总成本。",
        "binding_context": ["entity_id", "currency", "period", "accounting_scope", "accounting_standard", "metric"]},
    "net_profit_margin_v1": {"implementation_version": 1, "knowledge_id": "rh_k_net_margin", "title": "同口径净利润率",
        "input_units": {"revenue": "百万元", "net_profit": "百万元"}, "assumption_units": {},
        "parameters": {"amount_unit": "百万元", "currency": "CNY"},
        "context_parameters": ["period", "accounting_scope", "accounting_standard"], "output_unit": "%",
        "conditions": "同主体、同期间、同合并范围和会计准则的营业收入与报表净利润，收入须正；不以归母或扣非净利润替代报表净利润。",
        "limitations": "保留亏损净利润率，不年化、不预测证券收益；须另核查一次性损益、收入确认、少数股东权益及行业可比性。",
        "binding_context": ["entity_id", "currency", "period", "accounting_scope", "accounting_standard", "metric"]},
    "operating_cash_profit_ratio_v1": {"implementation_version": 1, "knowledge_id": "rh_k_cash_profit_ratio", "title": "经营现金流净利润比",
        "input_units": {"operating_cash_flow": "百万元", "net_profit": "百万元"}, "assumption_units": {},
        "parameters": {"amount_unit": "百万元", "currency": "CNY"},
        "context_parameters": ["period", "accounting_scope", "accounting_standard"], "output_unit": "%",
        "conditions": "同主体、同期间、同合并范围和会计准则的经营活动产生的现金流量净额与报表净利润；净利润须正，不以归母净利润或经营现金流入小计替代。",
        "limitations": "仅描述已披露经营收付与利润的比例，负经营现金流不补零。零或负利润时不计算本比率；超过100%不自动证明盈利质量好，低于100%不自动证明财务问题。须检查营运资金、非现金项目、现金流分类和行业特点；不是自由现金流、可分配现金或投资结论。",
        "binding_context": ["entity_id", "currency", "period", "accounting_scope", "accounting_standard", "metric"]},
}

FINANCIAL_PERIOD_METHODS = ("gross_margin_v1", "net_profit_margin_v1", "operating_cash_profit_ratio_v1")
FINANCIAL_INPUT_METRICS = {
    "gross_margin_v1": {"revenue": "revenue", "cost": "operating_cost"},
    "net_profit_margin_v1": {"revenue": "revenue", "net_profit": "net_profit"},
    "operating_cash_profit_ratio_v1": {"operating_cash_flow": "operating_cash_flow", "net_profit": "net_profit"},
}
LEGACY_FINANCIAL_AMOUNT_UNITS = ("元", "万元", "百万元")
FINANCIAL_AMOUNT_UNITS = ("元", "千元", "万元", "百万元", "千万元", "亿元")
for _financial_method in FINANCIAL_PERIOD_METHODS:
    _definition = METHOD_REGISTRY[_financial_method]
    _definition.update(implementation_version=2,
        input_unit_variants={field: list(LEGACY_FINANCIAL_AMOUNT_UNITS) for field in _definition['input_units']},
        parameter_variants={'amount_unit': list(LEGACY_FINANCIAL_AMOUNT_UNITS)}, same_input_unit=True)
    _definition['conditions'] += '各金额输入使用同一已核验单位，并与显式amount_unit一致；不混算元、万元和百万元，不在计算中换算。'

# Arithmetic stays implementation 2. The maintained knowledge revision opts
# into more literal source scales; prior locked knowledge retains its exact
# definition hash and cannot acquire new units by resuming or exporting.
LEGACY_FINANCIAL_DEFINITIONS = {method:copy.deepcopy(METHOD_REGISTRY[method])
                                for method in FINANCIAL_PERIOD_METHODS}
for _financial_method in FINANCIAL_PERIOD_METHODS:
    _definition = METHOD_REGISTRY[_financial_method]
    _definition.update(
        input_unit_variants={field:list(FINANCIAL_AMOUNT_UNITS) for field in _definition['input_units']},
        parameter_variants={'amount_unit':list(FINANCIAL_AMOUNT_UNITS)})
    _definition['conditions'] = _definition['conditions'].replace(
        '不混算元、万元和百万元', '不混算元、千元、万元、百万元、千万元和亿元')


def method_definition_for_knowledge(method, entry):
    definition = METHOD_REGISTRY.get(method)
    if method in FINANCIAL_PERIOD_METHODS and entry.get('amount_unit_contract_version', 1) == 1:
        definition = LEGACY_FINANCIAL_DEFINITIONS[method]
    return copy.deepcopy(definition)

# A new registered method preserves the existing difference-only implementation
# and its output contract. Totals are executable results, not disclosed values.
METHOD_REGISTRY['management_custody_fee_summary_v1'] = copy.deepcopy(
    METHOD_REGISTRY['management_custody_fee_comparison_v1'])
METHOD_REGISTRY['management_custody_fee_summary_v1'].update(
    knowledge_id='rh_k_management_custody_fee_summary', title='年度管理托管费率合计及比较',
    selection_scope='原问题要求列出或比较两基金的管理与托管年费率合计时，使用四项原值的本方法，一次生成两项合计与差。不要再为相同交付重复登记差值方法；另有独立要求时保留对应方法。',
    additional_numeric_outputs={'total_a': {'unit': '%/年', 'unit_field': 'total_unit'},
                                'total_b': {'unit': '%/年', 'unit_field': 'total_unit'}})

# Source notation and annual frequency are separate evidence. Preserve the
# literal-only legacy method; the new contract also checks each fee's meaning.
METHOD_REGISTRY['fee_difference_v2'] = {
    'implementation_version': 2, 'knowledge_id': 'rh_k_annual_fee_difference',
    'title': '同口径单项年度费率差',
    'selection_scope': '仅在需要计算同类单项费率数值差时使用；仅列示条款或比较是否相同无需强造计算。管理费和托管费分别比较，不相加、不互相替代；合计差使用专门合计方法。',
    'input_units': {'fee_a': '%/年', 'fee_b': '%/年'},
    'input_unit_variants': {'fee_a': ['%/年', '%'], 'fee_b': ['%/年', '%']},
    'assumption_units': {}, 'parameters': {'unit': '%/年', 'fee_kind': 'management_fee'},
    'parameter_variants': {'fee_kind': ['management_fee', 'custody_fee']},
    'context_parameters': ['period'], 'output_unit': '百分点/年',
    'binding_context': ['fund_id', 'share_class', 'period', 'fee_basis', 'rate_period', 'metric'],
    'conditions': '两项同类费率须分别核验基金、份额、共同适用期间、净资产计费基数和年度频率；fee_kind显式选择两条原值实际一致的management_fee或custody_fee。原单位为%时仍保留%，不改写原值、不年化。',
    'limitations': '只比较同类单项名义年费率，不是全部费用率、实际扣费或持有收益差；不同类别、基数或期间不作差。',
}


METHOD_CATALOGUE_VERSION = 2
METHOD_CATALOGUE_ADDITIONS = {2: frozenset({'etf_daily_nav_premium_v1'})}


def methods_for_task(task=None):
    """Keep a task's planning vocabulary stable across catalogue additions.

    Missing version means the original catalogue. New tasks explicitly adopt
    the current version; decoding or resuming an old task never upgrades it.
    Method implementation and retained knowledge versions remain separate.
    """
    version = METHOD_CATALOGUE_VERSION if task is None else task.get('method_catalogue_version', 1)
    if type(version) is not int or version not in (1, METHOD_CATALOGUE_VERSION):
        raise ContractError('计算方法目录版本未登记，不自动升级旧任务')
    excluded = {method for introduced, methods in METHOD_CATALOGUE_ADDITIONS.items()
                if introduced > version for method in methods}
    fee_version = 2 if task is None else task.get('fee_method_catalogue_version', 1)
    if type(fee_version) is not int or fee_version not in (1, 2):
        raise ContractError('费用方法目录版本未登记，不自动升级旧任务')
    excluded.add('fee_difference_v1' if fee_version == 2 else 'fee_difference_v2')
    result = {name: copy.deepcopy(definition) for name, definition in METHOD_REGISTRY.items()
              if name not in excluded}
    if task is not None:
        entries = {entry['id']:entry for entry in (task.get('knowledge') or {}).get('entries', [])}
        for method, definition in result.items():
            if method in FINANCIAL_PERIOD_METHODS and definition['knowledge_id'] in entries:
                definition = method_definition_for_knowledge(method, entries[definition['knowledge_id']])
                result[method] = definition
            role_contract = calculation_context_contract(task, method)
            if role_contract:
                definition['input_context_contract'] = role_contract
                if method == 'etf_daily_nav_premium_v1':
                    definition['binding_context'] = ['fund_id', 'share_class', 'currency',
                        'observation_date或valuation_date', 'price_kind或nav_kind']
    return result


def calculation_quantity_paths(method=None):
    """Describe registered result fields, never approve a stored calculation."""
    if method is None:
        paths = ['value', 'premium', 'undiscounted']
        for definition in METHOD_REGISTRY.values():
            paths.extend(definition.get('additional_numeric_outputs', {}))
        return list(dict.fromkeys(paths))
    if method not in METHOD_REGISTRY:
        return []
    paths = ['value', 'premium', 'undiscounted'] if method == 'residual_present_value_v1' else ['value']
    return paths + list(METHOD_REGISTRY[method].get('additional_numeric_outputs', {}))


METHOD_REFERENCE_QUANTITIES = {
    'operating_cash_profit_ratio_v1': {
        'reference:parity': {
            'version': 1, 'value': 100, 'unit': '%', 'title': '现金流与净利润相等时的比例',
            'basis': '登记公式为经营现金流净额除以净利润再乘百分比尺度；分子等于分母时比例为100%。',
            'limitation': '仅数学比较基准，不是已观测数据、盈利质量合格线、预测或投资判断。',
        },
    },
}


def method_reference_quantities(method):
    """Registered formula constants, separate from observed/computed results."""
    return copy.deepcopy(METHOD_REFERENCE_QUANTITIES.get(method, {})) if method in METHOD_REGISTRY else {}


def analysis_quantity_paths(method=None):
    """Typed presentation paths; constants never become calculation outputs."""
    references = (path for values in METHOD_REFERENCE_QUANTITIES.values() for path in values) \
        if method is None else iter(method_reference_quantities(method))
    return list(dict.fromkeys(calculation_quantity_paths(method)+list(references)))


class ContractError(ValueError):
    pass


class FollowupParentChanged(ContractError):
    """The parent changed before the follow-up was saved."""


class ExecutionTimeExhausted(ContractError):
    """The existing version cannot continue within its cumulative time limit."""

    code = "execution_time_exhausted"


class LocalStateError(ContractError):
    """Local startup can report a fixed cause without forwarding exception text."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class DuplicateJSONField(ContractError):
    """An object contains two values for a field, with no implied ordering."""


def unique_json_object(pairs):
    """Reject duplicate field names without storing or reporting their text."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONField("JSON包含重复字段，不能隐式覆盖")
        result[key] = value
    return result


class MethodNotApplicable(ContractError):
    """Valid retained inputs outside a registered method's economic domain."""


MAX_BROWSER_INTEGER = (1 << 53) - 1


def browser_json_values(value, *, omit=()):
    """Represent unsafe integers exactly in read-only browser data."""
    if type(value) is int and abs(value) > MAX_BROWSER_INTEGER:
        return str(value)
    if isinstance(value, dict):
        return {key: browser_json_values(item, omit=omit) for key, item in value.items() if key not in omit}
    if isinstance(value, (list, tuple)):
        return [browser_json_values(item, omit=omit) for item in value]
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def copy_json_value(value):
    """Detach JSON containers while sharing only immutable scalar values.

    For decoded archive values and canonical report objects only. No field is
    dropped, coerced or approved; generic runtime objects are not accepted.
    """
    kind = type(value)
    if kind is dict:
        return {key: copy_json_value(child) for key, child in value.items()}
    if kind is list:
        return [copy_json_value(child) for child in value]
    if kind in (str, int, float, bool, type(None)):
        return value
    raise ContractError('仅支持JSON数据复制，不能将运行时对象转换成研究数据')


def calculation_dependency_hash(item):
    """Same numbers do not imply the same evidence, method or assumptions."""
    fields = ("method", "implementation_version", "method_binding", "input_versions", "input_bindings",
              "inputs", "parameters", "assumptions", "assumption_sources", "scenario_overrides")
    value = {key: item.get(key) for key in fields}
    if 'context_contract' in item:
        value['context_contract'] = item['context_contract']
    return fingerprint(value)


def calculation_dependency_questions(task, question_id):
    """Explicit upstream questions, not all questions sharing an object."""
    questions = {question['id']: question for question in task.get('questions', [])}
    if question_id not in questions:
        return set()
    ancestors, visiting = set(), set()
    def visit(ident):
        if ident in visiting or ident not in questions:
            raise ContractError('计算依赖问题包含循环或未登记对象')
        visiting.add(ident)
        for dependency in questions[ident].get('dependencies', []):
            if dependency not in ancestors:
                visit(dependency)
                ancestors.add(dependency)
        visiting.remove(ident)
    visit(question_id)
    return ancestors


def calculation_input_applies(task, item, question_id, method):
    """A planned calculation may explicitly select verified upstream numbers.

    This does not relabel evidence, cover the child's financial requirements,
    or authorize narrative/causal reuse. Unit, entity, period and method gates
    still run at execution and again when composing the report.
    """
    if question_id in item.get('question_ids', [question_id]):
        return True
    question = next((q for q in task.get('questions', []) if q['id'] == question_id), None)
    definition = METHOD_REGISTRY.get(method)
    if (not question or method not in question.get('method_needs', []) or not definition
            or definition.get('assumption_units') or item.get('task_id') != task['id']
            or item.get('research_version') != task['version']
            or item.get('source_check') != '通过' or item.get('applicability_check') != '通过'
            or item.get('usage_scope') == DOCUMENT_CONTENT_SCOPE
            or type(item.get('value')) not in (int, float) or not math.isfinite(item['value'])):
        return False
    return bool(calculation_dependency_questions(task, question_id).intersection(item.get('question_ids', [])))


def synthesis_dependency_questions(task, question_id):
    """Declared factual inputs of a synthesis, not inherited answers or approvals.

    A question with its own evidence or method obligations keeps the existing
    strict scope. Legacy tasks and historical research retain their policy.
    Status is deliberately absent: draft/review precede adoption, where every
    dependency must separately have an accepted answer.
    """
    version = task.get('dependency_synthesis_version')
    if version is None:
        return set()
    if type(version) is not int or version != 1:
        raise ContractError('依赖综合引用版本未登记')
    from .argument_support import enabled as review_enabled
    question = next((q for q in task.get('questions', []) if q['id'] == question_id), None)
    if (not question or task.get('research_mode') != 'current'
            or task.get('intent') == DOCUMENT_CONTENT_INTENT or task.get('fixture')
            or not review_enabled(task)
            or question.get('answer_scope') == 'general_definition'
            or question.get('evidence_needs') or question.get('method_needs')):
        return set()
    return calculation_dependency_questions(task, question_id)


def synthesis_evidence_applies(task, item, question_id):
    """Only source- and applicability-checked upstream facts, with original scope."""
    return (item.get('task_id') == task['id'] and item.get('research_version') == task['version']
        and item.get('source_check') == item.get('applicability_check') == '通过'
        and item.get('usage_scope') != DOCUMENT_CONTENT_SCOPE
        and item.get('nature') in ('已实现事实', '合同政策条款')
        and bool(synthesis_dependency_questions(task, question_id).intersection(item.get('question_ids', []))))


def calculation_applies_to_answer(task, item, question_id):
    """Expose executed upstream results without satisfying new method obligations."""
    if question_id in item.get('question_ids', [question_id]):
        return True
    definition = METHOD_REGISTRY.get(item.get('method'))
    dependencies = synthesis_dependency_questions(task, question_id)
    if (not dependencies.intersection(item.get('question_ids', [])) or not definition
            or definition.get('assumption_units') or item.get('task_id') != task['id']
            or item.get('research_version') != task['version'] or not item.get('input_refs')):
        return False
    evidence = {value['id']: value for value in task.get('evidence', [])}
    return all(ref in evidence and synthesis_evidence_applies(task, evidence[ref], question_id)
               for ref in item['input_refs'])


def evidence_applies_to_answer(task, item, question_id, calculation_refs=()):
    """Original scopes plus explicitly versioned dependency facts and method inputs."""
    if question_id in item.get('question_ids', [question_id]):
        return True
    if synthesis_evidence_applies(task, item, question_id):
        return True
    if not isinstance(calculation_refs, (list, tuple)):
        return False
    return any(calc.get('id') in calculation_refs and calc.get('task_id') == task['id']
        and calc.get('research_version') == task['version']
        and question_id in calc.get('question_ids', []) and item.get('id') in calc.get('input_refs', [])
        and calculation_input_applies(task, item, question_id, calc.get('method'))
        for calc in task.get('calculations', []))


def now():
    return datetime.now(timezone.utc).isoformat()


def timepoint(value):
    """Date-only publication is conservatively known at end of local day."""
    if not isinstance(value, str) or not value:
        raise ContractError("公开时间未知，不能作为关键事实")
    if len(value) == 10:
        value += "T23:59:59.999999+08:00"
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ContractError("公开时间格式无效，请使用有效日期或带时区时间") from None
    if stamp.tzinfo is None:
        raise ContractError("时间必须包含时区")
    return stamp


def market_date(value):
    """Calendar date for registered mainland/Hong Kong source requests.

    Keep the original cutoff instant and offset in task provenance. Converting
    here governs date-only query windows, not intraday publication approval.
    """
    return timepoint(value).astimezone(timezone(timedelta(hours=8))).date().isoformat()


def context(task):
    return {key: task[key] for key in ("id", "version", "batch", "cutoff", "input_hash")}


def request(task, operation, refs=(), node_id=None):
    value = {"schema": PROTOCOL, "schema_version": 1, "operation": operation,
             "context": context(task), "refs": list(refs), "dependencies": dependency_versions(task, node_id)}
    if node_id is not None:
        value["node_id"] = node_id
    return value


def dependency_versions(task, node_id=None):
    selected = None
    if node_id is not None:
        node = next((n for n in task.get("nodes", []) if n["id"] == node_id), None)
        if node is None:
            raise ContractError("模块对应计划节点不存在")
        selected = set(node["dependencies"])
    return {node["id"]: node["output_hash"] for node in task.get("nodes", [])
            if node.get("output_hash") and node.get("status") in ("成功", "部分成功")
            and (selected is None or node["id"] in selected)}


def module_result(task, operation, artifacts, status="成功", node_id=None):
    if operation not in OPERATIONS or status not in ("成功", "部分成功", "失败", "不支持", "取消"):
        raise ContractError("模块操作或执行状态未登记")
    value = {"schema": "researchhelper.module-result", "schema_version": 1, "operation": operation,
             "context": context(task), "dependencies": dependency_versions(task, node_id), "status": status,
             "artifacts": artifacts, "artifact_hash": fingerprint(artifacts)}
    if node_id is not None:
        value["node_id"] = node_id
    return value


def validate_result(value, task):
    if value.get("schema") != "researchhelper.module-result" or value.get("schema_version") != 1:
        raise ContractError("模块结果协议或版本不匹配")
    if value.get("operation") not in OPERATIONS or value.get("context") != context(task):
        raise ContractError("模块结果任务绑定或操作不匹配")
    current = dependency_versions(task, value.get("node_id"))
    if value.get("node_id") is not None:
        node = next(n for n in task["nodes"] if n["id"] == value["node_id"])
        if value["operation"] != node["operation"]:
            raise ContractError("模块结果操作与计划节点不匹配")
    if value.get("dependencies") != current:
        raise ContractError("模块结果依赖版本已变化")
    if value.get("artifact_hash") != fingerprint(value.get("artifacts")):
        raise ContractError("模块结果内容哈希不一致")
    if value.get("status") not in ("成功", "部分成功", "失败", "不支持", "取消"):
        raise ContractError("模块结果执行状态无效")


def validate_request(value, task):
    if value.get("schema") != PROTOCOL or value.get("schema_version") != 1:
        raise ContractError("协议或版本不匹配")
    if value.get("operation") not in OPERATIONS:
        raise ContractError("未登记的ResearchHelper操作")
    if value.get("context") != context(task):
        raise ContractError("任务、批次、截止时点或输入版本不匹配")
    if value.get("dependencies") != dependency_versions(task, value.get("node_id")):
        raise ContractError("请求依赖版本已变化")
    if value.get("node_id") is not None:
        node = next(n for n in task["nodes"] if n["id"] == value["node_id"])
        if value["operation"] != node["operation"]:
            raise ContractError("模块操作与计划节点不匹配")
    for ref in value.get("refs", []):
        if ref.get("task_id") != task["id"] or ref.get("research_version") != task["version"]:
            raise ContractError("拒绝跨任务或跨版本引用")


def budget(limits=None):
    caps = {"data_requests": 100, "model_calls": 8, "seconds": 600.0}
    caps.update(limits or {})
    if set(caps) != {"data_requests", "model_calls", "seconds"}:
        raise ContractError("未知预算字段")
    for key, value in caps.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ContractError("预算必须为有限正数")
        if key != "seconds" and (not isinstance(value, int) or value > 100000):
            raise ContractError("请求预算必须为有限整数")
    return {"limits": caps, "used": {"data_requests": 0, "model_calls": 0, "seconds": 0.0},
            "reserve": {"model_calls": min(1, caps["model_calls"]), "seconds": min(60.0, caps["seconds"])},
            "supplements": 0, "dispatches": []}


MINIMUM_SAVE_SECONDS = .001
_BUDGET_EPSILON = 1e-9


def recovery_time_block(task, *, save_only=False):
    """Read the total execution allowance, including time reserved for delivery."""
    status = task.get("status")
    if status not in ("失败", "已取消") and not (status == "待开始" and task.get("batch", 0) > 0):
        return None
    try:
        remaining = task["ledger"]["limits"]["seconds"] - task["ledger"]["used"]["seconds"]
    except (KeyError, TypeError):
        return None
    if save_only:
        if remaining + _BUDGET_EPSILON < MINIMUM_SAVE_SECONDS:
            return "剩余执行时间不足，待保存正文已保留。"
    elif remaining <= 0:
        return "执行时间已用尽，已取得的研究资料仍可查看。"
    return None


def charge(ledger, kind, amount=1, delivery=False):
    remaining = ledger["limits"][kind] - ledger["used"][kind]
    reserve = 0 if delivery else ledger["reserve"].get(kind, 0)
    if amount < 0 or not math.isfinite(amount) or amount > remaining - reserve + _BUDGET_EPSILON:
        raise ContractError("预算不足，不能占用交付预留")
    ledger["used"][kind] += amount


def supplement(ledger):
    if ledger["supplements"] >= 2:
        raise ContractError("已达到两轮补证上限")
    ledger["supplements"] += 1


def tracking_contract():
    """A future observation rule is not a forecast or an observed outcome."""
    return {"required_fields": ["indicator", "source", "window", "condition"],
            "optional_fields": ["benchmark"], "not_applicable": None,
            "rule": "需要持续验证时记录明确观察规则；比较基准有依据才填写；不适用时省略或填null",
            "automatic_monitoring": False, "condition_is_observed_result": False}


def validate_tracking(value):
    if value is None:
        return
    contract = tracking_contract()
    required, optional = set(contract["required_fields"]), set(contract["optional_fields"])
    if not isinstance(value, dict) or not required.issubset(value) or set(value) - required - optional:
        raise ContractError("验证条件须含指标、来源、观察窗口和判断条件，不能提交身份或状态字段")
    if any(not isinstance(text, str) or not text.strip() for text in value.values()):
        raise ContractError("验证条件与比较基准必须为非空文字，不接受未核验的结构或数值")


def tracking_record(task, thesis):
    """Bind the rule to its accepted thesis; never accept a host-supplied status."""
    value = thesis.get("tracking")
    validate_tracking(value)
    if value is None:
        return None
    return dict(value, task_id=task["id"], research_version=task["version"],
                thesis_id=thesis["id"], thesis_hash=fingerprint(thesis),
                condition_hash=fingerprint(value), status="待主动复核")


def validate_evidence_usage(item, task, artifact=None):
    """Document reading is a separate, non-financial evidence scope.

    Its source bytes still require full verification. It cannot acquire temporal
    validity by using the research cutoff, download date or a successful receipt.
    """
    document_only = task.get("intent") == DOCUMENT_CONTENT_INTENT
    scope = item.get("usage_scope")
    if not document_only:
        if scope is not None:
            raise ContractError("原件内容核对摘录不能作为普通研究证据")
        return False
    if scope != DOCUMENT_CONTENT_SCOPE:
        raise ContractError("原件内容核对须明确绑定document_content用途")
    if artifact is None:
        ident = item.get("artifact_id") or (item.get("artifact") or {}).get("id")
        artifact = next((r for r in task.get("source_records", []) + task.get("materials", []) if r.get("id") == ident), None)
    if not isinstance(artifact, dict) or artifact.get("kind") not in ("material", "filing"):
        raise ContractError("原件内容核对只接受实际文字原件，不接受行情或目录接口")
    if task.get("objects") and item.get("object") not in task["objects"]:
        raise ContractError("原件内容核对对象超出用户声明的范围")
    if any(q.get("kind") != "fact" or q.get("method_needs") for q in task["questions"]):
        raise ContractError("原件内容核对不接受机制、比较、投资判断或计算问题")
    if "calculation_context" in item or "calculation_context_basis" in item:
        raise ContractError("原件内容核对不能登记金融计算口径")
    publication = item.get("publication")
    if item.get("published_at") is not None or not isinstance(publication, dict) or any(
            publication.get(key) != expected for key, expected in
            (("at", None), ("basis_ref", None), ("certainty", "未知"), ("precision", "未知"), ("timezone", "未知"))):
        raise ContractError("原件内容核对不核验公开时点，须明确保留未知，不能声明历史可得")
    if not isinstance(publication.get("basis"), str) or not publication["basis"].strip():
        raise ContractError("原件内容核对须说明公开时点未核验")
    return True


def document_content_answer(items):
    """Return exact checked quotations, never model-inferred financial meaning."""
    return "原件载明：\n" + "\n\n".join(item["text"] for item in items)


def evidence_requirements(question):
    """Stable obligations from the saved plan, never inferred from an answer."""
    return [{"id": fingerprint(text), "text": text,
             **({'constraints':copy.deepcopy(question['evidence_need_constraints'][text])} if 'evidence_need_constraints' in question else {})}
            for text in question.get("evidence_needs", [])]


def document_content_requirements(question):
    """Compatibility name for the same versioned question obligations."""
    return evidence_requirements(question)


def validate_question_coverage(task, selected, review):
    """Check declared excerpt-to-obligation links; semantic reading stays with the Agent.

    Absence is allowed for retained useful excerpts, but contributes no coverage.
    The analyzer receipt binds this mapping and the complete task input version.
    """
    mapping = review.get("evidence_need_ids", {})
    texts = review.get("evidence_need_texts")
    if (not isinstance(mapping, dict) or set(mapping) - set(selected) or
            'evidence_need_texts' in review and (not isinstance(texts, dict) or set(texts) - set(selected))):
        raise ContractError("依据覆盖必须绑定本条摘录对应的问题")
    questions = {q["id"]: q for q in task["questions"]}
    result = {}
    for ident, needs in mapping.items():
        requirements = evidence_requirements(questions[ident])
        # v1 stored question prefixes inside ids even though the enclosing key
        # already binds the question. Keep exact registered v1 ids readable;
        # neither infer ids from words nor rewrite the submitted review.
        allowed = {alias for n in requirements for alias in (n["id"], ident + ":" + n["id"])}
        if not isinstance(needs, list) or not needs or not all(isinstance(n, str) and n in allowed for n in needs) or len(set(needs)) != len(needs):
            raise ContractError("依据覆盖引用了未登记、重复或其他问题的依据需求")
        result[ident] = list(needs)
    if texts is not None:
        resolved = {}
        for ident, needs in texts.items():
            registered = {need['text']: need['id'] for need in evidence_requirements(questions[ident])}
            if (not isinstance(needs, list) or not needs or
                    any(not isinstance(need, str) or need not in registered for need in needs) or
                    len(set(needs)) != len(needs)):
                raise ContractError("依据需求原文须逐字对应本问题的已登记需求，不接受近似、重复或新增文字")
            resolved[ident] = [registered[need] for need in needs]
        if 'evidence_need_ids' in review:
            # Both forms are retained in a text-submitted review. Revalidation
            # must check their equivalence, not let either override the other.
            if set(mapping) != set(resolved):
                raise ContractError("依据需求ID与原文登记关系不一致")
            for ident, needs in mapping.items():
                canonical_ids = {alias: need['id'] for need in evidence_requirements(questions[ident])
                    for alias in (need['id'], ident + ':' + need['id'])}
                ids = [canonical_ids[need] for need in needs]
                if len(ids) != len(resolved[ident]) or set(ids) != set(resolved[ident]):
                    raise ContractError("依据需求ID与原文登记关系不一致")
        result = resolved
    return result


def validate_document_coverage(task, selected, review):
    return validate_question_coverage(task, selected, review)


def missing_evidence_requirements(question, items, provenance_need_ids=(), *, market_need_ids=()):
    covered = {need for item in items for need in
               item.get("applicability_review", {}).get("evidence_need_ids", {}).get(question["id"], [])
               if question["id"] in item.get("question_ids", [question["id"]])}
    scopes = question.get("evidence_need_scopes", {})
    # Financial excerpts cannot claim to be a request receipt. Conversely,
    # validated lineage cannot cover ordinary financial evidence requirements.
    query_needs = {fingerprint(text) for text, scope in scopes.items() if scope == "query_provenance"}
    covered.difference_update(query_needs | {question["id"] + ":" + ident for ident in query_needs})
    covered.update(query_needs.intersection(provenance_need_ids))
    calendar_needs = {fingerprint(text) for text, scope in scopes.items() if scope == 'market_calendar_coverage'}
    covered.difference_update(calendar_needs | {question['id'] + ':' + ident for ident in calendar_needs})
    covered.update(calendar_needs.intersection(market_need_ids))
    from .data_requirements import count_satisfied
    return [need for need in evidence_requirements(question)
            if not {need["id"], question["id"] + ":" + need["id"]}.intersection(covered)
            or need['id'] not in query_needs|calendar_needs and not count_satisfied(question,need['text'],items)]


def missing_document_requirements(question, items):
    return missing_evidence_requirements(question, items)


def validate_evidence(item, task):
    document_only = validate_evidence_usage(item, task)
    from .evidence import validate_current_snapshot
    current_only = validate_current_snapshot(item, task)
    required = ("id", "object", "source", "locator", "snapshot_hash", "published_at",
                "obtained_at", "period", "nature", "unit", "source_check", "applicability_check")
    missing = [key for key in required if key not in item or item[key] is None and not ((document_only or current_only) and key == "published_at")]
    if missing:
        raise ContractError("证据字段缺失：" + "、".join(missing))
    if item["source_check"] != "通过" or item["applicability_check"] != "通过":
        raise ContractError("证据未通过来源与适用性两类核验")
    from .data_requirements import validate_links
    coverage = validate_question_coverage(task,item.get('question_ids',[]),item.get('applicability_review',{}))
    validate_links(task,item,coverage)
    from .statistical_metrics import validate_level
    validate_level(item, task, coverage)
    if not document_only and not current_only and timepoint(item["published_at"]) > timepoint(task["cutoff"]):
        raise ContractError("证据公开时间晚于研究截止时点")
    if isinstance(item.get("value"), float) and not math.isfinite(item["value"]):
        raise ContractError("证据数字不是有限值")
    from .evidence import validate_provenance
    validate_provenance(item, task)


def _calendar_date(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ContractError("方法窗口须为明确的YYYY-MM-DD日期")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ContractError("方法窗口日期无效") from None
    if parsed.isoformat() != value:
        raise ContractError("方法窗口日期格式无效")
    return parsed


def validate_calculation_parameters(method, parameters):
    """Validate constants and explicit contextual parameters; never fill defaults."""
    definition = METHOD_REGISTRY.get(method)
    if definition is None or not isinstance(parameters, dict):
        raise ContractError("方法或参数未登记")
    fixed = definition["parameters"]
    dynamic = definition.get("context_parameters", [])
    allowed = set(fixed) | set(dynamic)
    missing = sorted(allowed - set(parameters))
    extra = set(parameters) - allowed
    variants = definition.get('parameter_variants', {})
    mismatched = sorted(key for key, value in fixed.items() if key in parameters and (
        parameters[key] not in variants[key] if key in variants else parameters[key] != value))
    if missing or extra or mismatched:
        # Show registered field names, never arbitrary model keys or values.
        # Source metadata and credentials must not leak through an error path.
        parts = []
        if missing:
            parts.append("缺少字段：" + "、".join(missing))
        if extra:
            context_fields = sorted(extra & set(definition.get("binding_context", [])))
            if context_fields:
                parts.append("证据calculation_context字段误放入parameters：" + "、".join(context_fields))
            unknown_count = len(extra - set(context_fields))
            if unknown_count:
                parts.append("未登记字段数量：" + str(unknown_count))
        if mismatched:
            parts.append("固定口径不匹配：" + "、".join(mismatched))
        raise ContractError("方法参数缺失、额外或口径不匹配；" + "；".join(parts))
    for key in dynamic:
        if not isinstance(parameters[key], str) or not parameters[key].strip():
            raise ContractError("方法上下文参数须有明确依据，不能留空")
    if method in ("observed_price_return_v1", "observed_index_change_v1"):
        if _calendar_date(parameters["start_date"]) >= _calendar_date(parameters["end_date"]):
            raise ContractError("价格变化窗口终点须晚于起点")
        if method == "observed_index_change_v1" and parameters["index_variant"] not in ("price", "gross_total_return", "net_total_return"):
            raise ContractError("指数收益版本须明确为价格、全收益或净全收益，不猜测未知版本")
    elif method == "etf_nav_premium_v1":
        stamp = parameters["valuation_time"]
        if "T" not in stamp or len(stamp) < 25 or timepoint(stamp).utcoffset().total_seconds() != 28800:
            raise ContractError("净值比较须指定完整北京时间估值时点")
        _calendar_date(stamp[:10])
    elif method == "etf_daily_nav_premium_v1":
        _calendar_date(parameters['valuation_date'])
    elif method in FINANCIAL_PERIOD_METHODS:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', parameters['period']):
            raise ContractError('财务流量比率须保留单季度、累计或年度等实际期间，不能仅用共同期末日')
        if parameters["accounting_scope"] not in ("consolidated", "parent") or parameters["accounting_standard"] not in ("CAS", "IFRS", "US_GAAP"):
            raise ContractError("财报合并范围或会计准则未登记")


def valid_calculation_input_unit(method, field, unit):
    """Accept only explicitly registered source representations, never convert."""
    definition = METHOD_REGISTRY.get(method)
    if definition is None or field not in definition['input_units'] or not isinstance(unit, str):
        return False
    variants = definition.get('input_unit_variants', {}).get(field)
    return unit in variants if variants is not None else unit == definition['input_units'][field]


def calculation_input_units_available(method, available):
    """Candidate availability only; distinct records, no financial approval."""
    fields = list(METHOD_REGISTRY[method]['input_units'])
    if METHOD_REGISTRY[method].get('same_input_unit'):
        return any(sum(unit == candidate for candidate in available) >= len(fields)
                   for unit in FINANCIAL_AMOUNT_UNITS)
    matched = {}

    def assign(field, seen):
        for index, unit in enumerate(available):
            if index in seen or not valid_calculation_input_unit(method, field, unit):
                continue
            seen.add(index)
            if index not in matched or assign(matched[index], seen):
                matched[index] = field
                return True
        return False

    return all(assign(field, set()) for field in fields)


def calculation_context_contract(task, method):
    """Version context semantics independently from unchanged arithmetic.

    Legacy daily comparisons keep their shared valuation-date requirement.
    New tasks explicitly compare a price observation with a NAV valuation;
    neither date is inferred from publication, retrieval or the parameter.
    """
    if (method in ('observed_price_return_v1', 'observed_index_change_v1')
            and type(task.get('observed_window_date_roles_version')) is int
            and task['observed_window_date_roles_version'] == 1):
        fields = METHOD_REGISTRY[method]['binding_context']
        return dict(version=2, method=method,
            input_fields={name: list(fields) for name in METHOD_REGISTRY[method]['input_units']},
            rule='window_start与window_end是同一原始查询窗口，须覆盖方法实际起终观测日；start_date与end_date须分别等于输入原值自己的observation_date及所属日，不由查询边界补价格。仅比较这两个实际观测，不自动称为自然月收益、完整区间收益或含分红回报；覆盖完整性由独立日历及价格另行核对。')
    if (method == 'etf_daily_nav_premium_v1'
            and type(task.get('daily_nav_date_roles_version')) is int
            and task['daily_nav_date_roles_version'] == 1):
        return dict(version=2, method=method, input_fields={
            'price': ['fund_id', 'share_class', 'currency', 'observation_date', 'price_kind'],
            'nav': ['fund_id', 'share_class', 'currency', 'valuation_date', 'nav_kind']},
            rule='价格观测日与官方净值估值日分别有原始依据，均等于valuation_date参数及各自所属日；不补盘中时刻或历史可得性。')
    return None


def validate_calculation_context(method, bindings, parameters, *, context_contract=None):
    """Check already verified evidence records jointly, not a model's freeform reason.

    Binding records carry object, period, unit and a source-verified
    calculation_context. Evidence provenance and publication checks remain with
    EvidenceCollector/ResearchAnalyzer; this function never grants verification.
    """
    validate_calculation_parameters(method, parameters)
    if context_contract is not None and context_contract != calculation_context_contract(
            {'daily_nav_date_roles_version': 1, 'observed_window_date_roles_version': 1}, method):
        raise ContractError('计算口径契约未登记或不属于本方法')
    units = METHOD_REGISTRY[method]["input_units"]
    if not isinstance(bindings, dict) or set(bindings) != set(units):
        raise ContractError("计算证据绑定与方法字段不匹配")
    for key, unit in units.items():
        item = bindings[key]
        if isinstance(item, dict) and item.get("usage_scope") is not None:
            raise ContractError("原件内容核对摘录不能进入金融计算")
        if not isinstance(item, dict) or not valid_calculation_input_unit(method, key, item.get("unit")):
            raise ContractError("计算证据单位不匹配")
        if not isinstance(item.get("period"), str) or not item["period"].strip():
            raise ContractError("计算证据缺少所属期")
    if method == "fee_difference_v1":
        if bindings["fee_a"]["period"] != bindings["fee_b"]["period"]:
            raise ContractError("费用比较须使用共同适用期间")
        return
    if method == 'fee_difference_v2':
        required = set(METHOD_REGISTRY[method]['binding_context'])
        for item in bindings.values():
            values = item.get('calculation_context')
            if (not isinstance(values, dict) or set(values) != required
                    or any(not isinstance(values[key], str) or not values[key].strip()
                        for key in ('fund_id', 'share_class'))
                    or not isinstance(item.get('object'), str) or not item['object'].strip()
                    or item['period'] != parameters['period'] or values['period'] != parameters['period']
                    or values['fee_basis'] != 'net_assets' or values['rate_period'] != 'annual'
                    or values['metric'] != parameters['fee_kind']):
                raise ContractError('单项费率差须核验同类费用、份额、共同期间、净资产基数和年度频率')
        return
    if method in ("management_custody_fee_comparison_v1", "management_custody_fee_summary_v1"):
        required = {"fund_id", "share_class", "period", "fee_basis", "rate_period", "metric"}
        for side in ("a", "b"):
            pair = [bindings[component + '_' + side] for component in ("management", "custody")]
            contexts = [item.get('calculation_context') for item in pair]
            if any(not isinstance(c, dict) or set(c) != required for c in contexts):
                raise ContractError('管理托管费率缺少完整的份额、期间及计费口径')
            if any(not isinstance(item.get('object'), str) or not item['object'].strip() for item in pair) or pair[0]['object'] != pair[1]['object']:
                raise ContractError('同一基金的管理费与托管费不能混用不同对象')
            for field in ('fund_id', 'share_class'):
                if any(not isinstance(c[field], str) or not c[field].strip() for c in contexts) or contexts[0][field] != contexts[1][field]:
                    raise ContractError('管理费与托管费的基金或份额类别不一致')
            if any(item['period'] != parameters['period'] or c['period'] != parameters['period']
                    or c['fee_basis'] != 'net_assets' or c['rate_period'] != 'annual' for item, c in zip(pair, contexts)):
                raise ContractError('费率比较须使用共同适用期间、净资产基数及年费率')
            if contexts[0]['metric'] != 'management_fee' or contexts[1]['metric'] != 'custody_fee':
                raise ContractError('管理费与托管费字段不可互换或替换其他费用')
        return
    if method == "residual_present_value_v1":
        return  # Existing valuation assumptions retain their separately verified contract.
    records = list(bindings.values())
    if any(not isinstance(item.get("object"), str) or not item["object"].strip() for item in records) or len({item["object"] for item in records}) != 1:
        raise ContractError("计算输入须属于同一明确对象")
    contexts = {key: item.get("calculation_context") for key, item in bindings.items()}
    missing = [key for key, item in contexts.items() if not isinstance(item, dict)]
    if missing:
        labels = {"start_price": "起点价格", "end_price": "终点价格", "start_level": "起点点位",
                  "end_level": "终点点位", "price": "市场价格", "nav": "基金净值"}
        raise ContractError("计算缺少结构化且已核验的口径依据：" +
                            "、".join(labels.get(key, key) for key in missing))

    def shared(field, expected=None):
        values = [item.get(field) for item in contexts.values()]
        if any(not isinstance(value, str) or not value.strip() for value in values) or len(set(values)) != 1:
            raise ContractError("计算输入的" + field + "缺失或不一致")
        if expected is not None and values[0] != expected:
            raise ContractError("计算输入的" + field + "与方法参数不一致")

    def observed_window():
        if context_contract is None:
            shared('window_start', parameters['start_date'])
            shared('window_end', parameters['end_date'])
            return
        shared('window_start')
        shared('window_end')
        first = next(iter(contexts.values()))
        start, end = (_calendar_date(first[key]) for key in ('window_start', 'window_end'))
        if not start <= _calendar_date(parameters['start_date']) < _calendar_date(parameters['end_date']) <= end:
            raise ContractError('实际起终观测日未被同一原始查询窗口覆盖')

    if method == "observed_index_change_v1":
        shared("index_id")
        shared("index_base")
        shared("index_variant", parameters["index_variant"])
        shared("index_currency", parameters["index_currency"])
        shared("level_kind", parameters["level_kind"])
        observed_window()
        for key, date_key in (("start_level", "start_date"), ("end_level", "end_date")):
            if contexts[key].get("observation_date") != parameters[date_key] or bindings[key]["period"] != parameters[date_key]:
                raise ContractError("指数点位观测日与指定窗口不符")
        return
    shared("currency", parameters["currency"])
    if method == "observed_price_return_v1":
        shared("security_id")
        shared("price_basis", parameters["price_basis"])
        shared("price_kind", "close")
        observed_window()
        shared("corporate_action_status")
        if contexts["start_price"]["corporate_action_status"] not in ("unknown", "present", "verified_none"):
            raise ContractError("公司行为状态须明确记录为未知、存在或已核验不存在")
        for key, date_key in (("start_price", "start_date"), ("end_price", "end_date")):
            if contexts[key].get("observation_date") != parameters[date_key] or bindings[key]["period"] != parameters[date_key]:
                raise ContractError("价格证据观测日与指定窗口不符")
    elif method == "etf_nav_premium_v1":
        shared("fund_id")
        shared("share_class")
        shared("valuation_time", parameters["valuation_time"])
        if any(item["period"] != parameters["valuation_time"][:10] for item in records):
            raise ContractError("净值与价格所属日不一致")
        if contexts["price"].get("price_kind") != "close" or contexts["nav"].get("nav_kind") != "official":
            raise ContractError("仅允许同估值时点收盘价与官方单位净值，不接受IOPV")
    elif method == "etf_daily_nav_premium_v1":
        bound_fund = records[0]['object']
        if context_contract is not None and re.fullmatch(r'[0-9]{6}\.(?:SH|SZ)', bound_fund):
            # Both records already belong to this exchange-qualified object.
            # A filing may print only its six-digit code, while the quote
            # includes the exchange. Preserve the source values and bind
            # both to the verified object for comparison.
            aliases = (bound_fund, bound_fund[:6])
            if any(item.get('fund_id') not in aliases for item in contexts.values()):
                raise ContractError('日度折溢价输入的基金代码与已核验对象不一致')
        else:
            shared('fund_id')
        shared('share_class')
        if any(contexts['price'][field].casefold() in ('unknown', 'unverified', '未知', '未核验', '未披露', '不适用')
               for field in ('fund_id', 'share_class')):
            raise ContractError('基金及份额类别须明确识别，未知状态不能作为相同份额的依据')
        if context_contract is None:
            shared('valuation_date', parameters['valuation_date'])
        else:
            for role, field in (('price', 'observation_date'), ('nav', 'valuation_date')):
                value = contexts[role].get(field)
                if not isinstance(value, str) or value != parameters['valuation_date']:
                    raise ContractError('日度比较的' + role + '.' + field + '缺失或与目标日不一致')
                _calendar_date(value)
        if any(item['period'] != parameters['valuation_date'] for item in records):
            raise ContractError('收盘价与官方净值须属于同一明确估值日，不沿用昨日或最近净值')
        if contexts['price'].get('price_kind') != 'close' or contexts['nav'].get('nav_kind') != 'official':
            raise ContractError('日度偏离率仅采用收盘价与官方单位净值，不接受IOPV或盘中价格')
    elif method in FINANCIAL_PERIOD_METHODS:
        if any(item['unit'] != parameters['amount_unit'] for item in records):
            raise ContractError('财务比率各输入须使用同一已核验金额单位，并与声明amount_unit一致，不静默换算')
        shared("entity_id")
        for field in ("period", "accounting_scope", "accounting_standard"):
            shared(field, parameters[field])
        if any(item["period"] != parameters["period"] for item in records):
            raise ContractError("财务输入须属于同一报告期间")
        metrics = FINANCIAL_INPUT_METRICS[method]
        if any(contexts[key].get("metric") != metric for key, metric in metrics.items()):
            raise ContractError("财务计算字段与登记指标不一致，不替换归母、扣非或现金流口径")
    if (method in ('etf_nav_premium_v1', 'etf_daily_nav_premium_v1')
            and bindings['nav'].get('artifact', {}).get('kind') == 'api'
            and bindings['nav'].get('record_locator', {}).get('field') in ('open', 'high', 'low', 'close')):
        raise ContractError('行情价格字段不能改标为官方单位净值，不用另一个原件的净值标签替代原值来源')


def validate_financial_method_scope(method, parameters, binding):
    """A new implementation does not broaden a task's locked knowledge scope."""
    if method in FINANCIAL_PERIOD_METHODS and parameters.get('amount_unit') not in binding.get('amount_units', ['百万元']):
        raise ContractError('该任务锁定的方法知识未登记此金额表示，不自动升级旧知识或改写冻结结果')


def registered_financial_bindings(task, question_ids):
    """Bind only uniquely determined inputs for already selected methods.

    This is not method selection or evidence approval. Every missing required
    method must belong to this finite registry, with one eligible realized
    fact per metric and identical verified context. Any ambiguity leaves the
    entire proposal to the ordinary analyst. No period, unit, currency,
    financial scope or numeric value is inferred from question prose.
    """
    known = {question['id'] for question in task['questions']}
    if not question_ids or not set(question_ids).issubset(known):
        raise ContractError('登记计算绑定须限定本任务实际问题')
    specs, pending = [], []
    for question in task['questions']:
        ident = question['id']
        if ident not in question_ids or not question.get('required', True):
            continue
        for method in question.get('method_needs', []):
            if any(spec.get('method') == method and ident in spec.get('question_ids', [])
                   for spec in task.get('calculation_specs', [])):
                continue
            if method not in FINANCIAL_INPUT_METRICS:
                return None
            pending.append((ident, method))
    if not pending:
        return None
    for ident, method in pending:
        definition = METHOD_REGISTRY[method]
        if definition['assumption_units']:
            return None
        bindings = {}
        for field, metric in FINANCIAL_INPUT_METRICS[method].items():
            candidates = [item for item in task['evidence']
                if item.get('nature') == '已实现事实'
                and item.get('source_check') == item.get('applicability_check') == '通过'
                and item.get('task_id') == task['id'] and item.get('research_version') == task['version']
                and calculation_input_applies(task, item, ident, method)
                and (item.get('calculation_context') or {}).get('metric') == metric]
            if len(candidates) != 1:
                return None
            bindings[field] = candidates[0]
        first = next(iter(bindings.values()))
        supplied = first['calculation_context']
        try:
            for item in bindings.values():
                validate_evidence(item, task)
            parameters = {key: supplied[key] for key in definition['context_parameters']}
            parameters.update(amount_unit=first['unit'], currency=supplied['currency'])
            validate_calculation_context(method, bindings, parameters)
        except (ContractError, KeyError):
            return None
        inputs = {field: item['id'] for field, item in bindings.items()}
        identity = dict(task_id=task['id'], research_version=task['version'],
            question_id=ident, method=method, inputs=inputs, parameters=parameters)
        specs.append(dict(id='calc_' + method + '_' + fingerprint(identity)[:12],
            method=method, inputs=inputs, parameters=parameters,
            question_ids=[ident]))
    return dict(calculations=specs,
        reason='按原计划登记方法绑定唯一的已核验指标与共同口径；不新增方法、假设、证据或研究判断。')


def calculate(method, values, parameters=None):
    """Registered methods may only publish finite, reproducible numeric results."""
    if not isinstance(values, dict) or parameters is not None and not isinstance(parameters, dict):
        raise ContractError("计算输入与参数必须为字段记录")
    try:
        result = _calculate_registered(method, values, parameters)
    except (OverflowError, ZeroDivisionError) as error:
        raise ContractError("计算超出有限数值范围，请检查输入与单位") from error
    if any(isinstance(value, (int, float)) and not math.isfinite(value) for value in result.values()):
        raise ContractError("计算超出有限数值范围，请检查输入与单位")
    return result


def _calculate_registered(method, values, parameters=None):
    """No eval/exec/model code. Methods enforce their own units and conditions."""
    parameters = parameters or {}
    for val in values.values():
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            raise ContractError("计算输入必须为有限数字")
    if method in ('management_custody_fee_comparison_v1', 'management_custody_fee_summary_v1'):
        validate_calculation_parameters(method, parameters)
        if set(values) != set(METHOD_REGISTRY[method]['input_units']) or min(values.values()) < 0:
            raise ContractError('管理托管费率须为四项完整非负年费率')
        total_a = values['management_a'] + values['custody_a']
        total_b = values['management_b'] + values['custody_b']
        output = {'value': total_a - total_b, 'unit': '百分点/年', 'tolerance': 1e-10}
        if method == 'management_custody_fee_summary_v1':
            output.update(total_a=total_a, total_b=total_b, total_unit='%/年')
        return output
    if method in ("observed_price_return_v1", "observed_index_change_v1", "etf_nav_premium_v1", "etf_daily_nav_premium_v1", *FINANCIAL_PERIOD_METHODS):
        validate_calculation_parameters(method, parameters)
        if set(values) != set(METHOD_REGISTRY[method]["input_units"]):
            raise ContractError("计算输入缺失或有未登记字段")
        if method == "observed_index_change_v1":
            if values["start_level"] <= 0 or values["end_level"] < 0:
                raise MethodNotApplicable("指数起点须为正且终点不能为负；保留原点位，不补零或替换分母")
            result = (values["end_level"] / values["start_level"] - 1) * 100
        elif method == "observed_price_return_v1":
            if values["start_price"] <= 0 or values["end_price"] <= 0:
                raise ContractError("已观测收盘价格须为正数")
            result = (values["end_price"] / values["start_price"] - 1) * 100
        elif method in ("etf_nav_premium_v1", "etf_daily_nav_premium_v1"):
            if values["price"] <= 0 or values["nav"] <= 0:
                raise ContractError("ETF价格和官方单位净值须为正数")
            result = (values["price"] / values["nav"] - 1) * 100
        elif method == "gross_margin_v1":
            if values["revenue"] <= 0 or values["cost"] < 0:
                raise ContractError("营业收入须为正且营业成本不得为负")
            result = (values["revenue"] - values["cost"]) / values["revenue"] * 100
        elif method == "net_profit_margin_v1":
            if values["revenue"] <= 0:
                raise MethodNotApplicable("净利润率的营业收入须为正数，保留收入与利润原值分别分析")
            result = values["net_profit"] / values["revenue"] * 100
        else:
            if values["net_profit"] <= 0:
                raise MethodNotApplicable("零或负净利润不计算经营现金流净利润比，保留两项原值分别分析")
            result = values["operating_cash_flow"] / values["net_profit"] * 100
        return {"value": result, "unit": "%", "tolerance": 1e-10}
    if method in ('fee_difference_v1', 'fee_difference_v2'):
        if method == 'fee_difference_v2':
            validate_calculation_parameters(method, parameters)
        if set(values) != {"fee_a", "fee_b"} or parameters.get("unit") != "%/年":
            raise ContractError("费用比较输入或单位不匹配")
        if min(values.values()) < 0:
            raise ContractError("费用不能为负")
        return {"value": values["fee_a"] - values["fee_b"], "unit": "百分点/年", "tolerance": 1e-10}
    if method == "residual_present_value_v1":
        if set(values) != {"receipt", "debt", "cost", "shares", "rate", "years", "price"}:
            raise ContractError("剩余价值输入不完整")
        if parameters.get("amount_unit") != "百万元" or parameters.get("shares_unit") != "百万股":
            raise ContractError("金额或股本单位不匹配")
        if values["shares"] <= 0 or values["rate"] <= -1 or values["years"] <= 0 or values["price"] <= 0:
            raise ContractError("股本、期限、价格或回报率无效")
        cash = values["receipt"] - values["debt"] - values["cost"]
        present = cash / values["shares"] / (1 + values["rate"]) ** values["years"]
        return {"value": present, "unit": "元/股", "premium": present / values["price"] - 1,
                "undiscounted": cash / values["shares"], "tolerance": 1e-8}
    raise ContractError("未登记的可执行计算方法")
