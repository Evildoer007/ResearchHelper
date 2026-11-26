"""ReportComposer freezes verified content; VisualRenderer never invents facts."""

from __future__ import annotations

from .market_calendar import covered_need_ids as calendar_need_ids, scope_basis as calendar_scope_basis

import copy
from hashlib import sha256
import html
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import (ContractError, DOCUMENT_CONTENT_INTENT, DOCUMENT_CONTENT_LIMITATION, document_content_answer, missing_document_requirements,
                        missing_evidence_requirements,
                        METHOD_REGISTRY, calculate, calculation_dependency_hash,
                        fingerprint, tracking_record, validate_calculation_context, validate_evidence, copy_json_value)
from .contracts import evidence_applies_to_answer, calculation_applies_to_answer
from .evidence import evidence_binding, reference_groups
from .knowledge import ResearchKnowledge
from .numeric_integrity import bind_numeric_statement, validate_numeric_statement
from .planning import thesis_obligations
from .layout import resource_directory

TEMPLATE_VERSION = "rh-paper-3"
READER_VERSION = "rh-reader-34"
VERSION_CHANGES_CONTRACT = 2
DRAFT_VERSION_CHANGES_CONTRACT = 3
CALCULATION_COMPARISON_FIELDS = (
    'id', 'method', 'method_title', 'method_binding', 'inputs', 'input_refs',
    'parameters', 'assumptions', 'output', 'question_ids', 'implementation_version',
    'input_bindings', 'scenario_overrides', 'assumption_sources', 'limitations',
)
CALCULATION_CHANGE_LABELS = dict(method='计算方法', method_title='方法名称',
    method_binding='知识与方法版本', implementation_version='实现版本', inputs='输入值',
    input_refs='输入引用', input_evidence='输入证据', parameters='计算口径', assumptions='显式假设',
    output='计算结果', question_ids='关联问题', input_bindings='字段与证据关联',
    scenario_overrides='情景覆盖', assumption_sources='假设来源', limitations='适用限制')
COMPARISON_VALUE_LABELS = dict(revenue='营业收入', net_profit='报表净利润',
    operating_cash_flow='经营现金流', amount='金额', fee_a='费用A', fee_b='费用B',
    currency='币种', amount_unit='金额单位', period='所属期间', value='数值',
    implementation_version='实现版本', knowledge_id='知识条目', knowledge_version='知识版本',
    accounting_scope='报表范围', accounting_standard='会计准则',
    start_price='期初价格', end_price='期末价格', observation_date='观测日期')


def _changes_contract(task):
    version = task.get('version_changes_contract', VERSION_CHANGES_CONTRACT)
    if (type(version) is not int or version not in (1, VERSION_CHANGES_CONTRACT, DRAFT_VERSION_CHANGES_CONTRACT)
            or version == DRAFT_VERSION_CHANGES_CONTRACT and not isinstance(task.get('draft_report'), dict)):
        raise ContractError('版本差异契约未登记，不能跳过复核后交付')
    return version


def research_date_label(value, *, compact=False):
    """Format a known instant locally, never invent a zone for a naive date.

    Header presentation uses minute precision; the time element and scope
    retain the exact boundary. Detailed labels keep all declared precision.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return str(value)
    if 'T' not in value and ' ' not in value:
        return parsed.strftime('%Y年%m月%d日')
    beijing = compact and parsed.utcoffset() is not None
    if beijing:
        parsed = parsed.astimezone(timezone(timedelta(hours=8)))
    clock = parsed.strftime('%H:%M')
    if not compact and (parsed.second or parsed.microsecond):
        clock += parsed.strftime(':%S')
        if parsed.microsecond:
            clock += '.' + str(parsed.microsecond).zfill(6).rstrip('0')
    zone = parsed.strftime('%z')
    if zone:
        zone = 'UTC' + zone[:3] + ':' + zone[3:]
    return ('北京时间' if beijing else '') + parsed.strftime('%Y年%m月%d日') + clock + ('' if beijing else zone)


def version_basis(value, version, *, contract_version=1):
    """Readable comparison snapshots, not approvals or cross-version citations.

    Thesis IDs are local to each version. No correspondence is inferred from
    their ID, title or order. Evidence comparisons include the extracted fact,
    not just the underlying file hash, and exclude fresh verification receipts.
    """
    evidence_fields = ("object", "source", "locator", "record_locator", "snapshot_hash", "published_at",
                       "publication", "period", "nature", "unit", "raw_unit", "raw_token", "raw_value",
                       "value", "text", "question_ids", "calculation_context", "calculation_context_basis", "availability")
    thesis_fields = ("id", "title", "judgment", "question_ids", "support", "counter", "calculation_refs",
                     "investment_meaning", "limitation")
    if contract_version >= 2:
        evidence_fields += ('observation_date', 'observation_period', 'field_definition', 'identity', 'usage_scope')
    basis = {"task_id": value.get("task_id", value.get("id")), "research_version": version,
            "cutoff": value["cutoff"],
            "evidence": [{"id": item["id"], **{key: copy.deepcopy(item[key]) for key in evidence_fields if key in item}}
                         for item in value["evidence"]],
            "theses": [{key: copy.deepcopy(item[key]) for key in thesis_fields if key in item} for item in value["theses"]],
            "conditions": copy.deepcopy(value["tracking"]),
            "knowledge_versions": copy.deepcopy(value.get("knowledge", {}).get("index", {}))}
    if contract_version >= 2:
        records = value.get('calculations', [])
        if (not isinstance(records, list) or any(not isinstance(item, dict)
                or not isinstance(item.get('id'), str) or not item['id'] for item in records)
                or len({item['id'] for item in records}) != len(records)):
            raise ContractError('版本计算缺少唯一标识，不能比较')
        evidence = {item['id']: item for item in basis['evidence']}
        calculations = []
        for item in records:
            projected = {key: copy.deepcopy(item[key]) for key in CALCULATION_COMPARISON_FIELDS if key in item}
            references = item.get('input_refs', [])
            if not isinstance(references, list) or any(not isinstance(ref, str) for ref in references):
                raise ContractError('版本计算输入引用无效，不能比较')
            # Keep each full evidence basis once, not once per dependent calculation.
            projected['input_evidence'] = [dict(id=ref, digest=fingerprint(evidence.get(ref))) for ref in references]
            calculations.append(projected)
        basis.update(basis_contract_version=2, calculations=calculations,
                     calculations_retained='calculations' in value)
    return basis


def _calculation_changes(prior, current):
    """Compare retained computations, never infer thesis correspondence or payoff."""
    if prior.get('calculations_retained') is not True:
        return dict(baseline_available=False, added=[], revised=[], missing=[], unchanged=[])
    before = {item['id']: item for item in prior['calculations']}
    after = {item['id']: item for item in current['calculations']}
    revised, unchanged = [], []
    for ident in after.keys() & before.keys():
        fields = sorted(key for key in before[ident].keys() | after[ident].keys()
                        if (key in before[ident]) != (key in after[ident])
                        or before[ident].get(key) != after[ident].get(key))
        if fields:
            revised.append(dict(id=ident, fields=fields))
        else:
            unchanged.append(ident)
    return dict(baseline_available=True, added=sorted(after.keys() - before.keys()),
                revised=sorted(revised, key=lambda item: item['id']),
                missing=sorted(before.keys() - after.keys()), unchanged=sorted(unchanged))


def _comparison_text(value, field=None, evidence=None):
    if value is None:
        return '未留存'
    if field == 'input_evidence':
        return '；'.join((
            '，'.join(str(part) for part in (
                (evidence.get(item['id']) or {}).get('object'),
                (evidence.get(item['id']) or {}).get('source'),
                (evidence.get(item['id']) or {}).get('period'),
                (evidence.get(item['id']) or {}).get('observation_date'),
                (evidence.get(item['id']) or {}).get('text')) if part is not None)
            or item['id']) for item in value) or '无'
    if isinstance(value, dict):
        return '；'.join(COMPARISON_VALUE_LABELS.get(key, key) + '：' + _comparison_text(item)
                        for key, item in value.items()) or '无'
    if isinstance(value, list):
        return '、'.join(_comparison_text(item) for item in value) or '无'
    return str(value)


def calculation_change_rows(changes):
    """Shared presentation data; consumers must escape text, not calculate anew."""
    delta = changes.get('calculation_changes')
    if not delta or not delta.get('baseline_available'):
        return []
    before = {item['id']: item for item in changes['prior_basis']['calculations']}
    after = {item['id']: item for item in changes['current_basis']['calculations']}
    old_evidence = {item['id']: item for item in changes['prior_basis']['evidence']}
    new_evidence = {item['id']: item for item in changes['current_basis']['evidence']}
    rows = []
    for status, entries in (('新增', delta['added']), ('已变更', delta['revised']), ('本版未取得', delta['missing'])):
        for entry in entries:
            ident = entry['id'] if isinstance(entry, dict) else entry
            old, new = before.get(ident), after.get(ident)
            fields = entry['fields'] if isinstance(entry, dict) else [
                key for key in ('method_title', 'inputs', 'parameters', 'output') if key in (new or old)]
            summary = lambda item: {key: item[key] for key in ('method', 'method_title') if key in item} if item else None
            rows.append(dict(id=ident, status=status, before=summary(old), after=summary(new),
                fields=fields, details=[dict(field=key, label=CALCULATION_CHANGE_LABELS.get(key, key),
                    before_text=_comparison_text(old.get(key) if old else None, key, old_evidence),
                    after_text=_comparison_text(new.get(key) if new else None, key, new_evidence)) for key in fields]))
    return rows


def _version_changes(task, prior, digest):
    contract_version = _changes_contract(task)
    parent = task.get("update_from") or {}
    if (prior.get("task_id") != task["id"] or type(parent.get("version")) is not int
            or parent["version"] != task["version"] - 1 or prior.get("research_version") != parent["version"]
            or parent.get("digest") != digest or parent.get("conditions") != prior.get("conditions")
            or parent.get("knowledge_versions") != prior.get("knowledge_versions")):
        raise ContractError("主动更新的原版本、交付或验证条件不一致")
    current = version_basis(task, task["version"], contract_version=contract_version)
    if contract_version >= 2 and (prior.get('basis_contract_version') != 2
            or type(prior.get('calculations_retained')) is not bool or not isinstance(prior.get('calculations'), list)):
        raise ContractError('原版本缺少计算比较依据，不能伪造版本差异')
    before = {e["id"]: e for e in prior["evidence"]}
    after = {e["id"]: e for e in current["evidence"]}
    old_knowledge, new_knowledge = prior["knowledge_versions"], current["knowledge_versions"]
    changes = {"contract_version": contract_version, "task_id": task["id"], "prior_version": parent["version"],
            "research_version": task["version"], "prior_digest": digest,
            "prior_basis": copy.deepcopy(prior), "current_basis": current,
            "added": [key for key in after if key not in before],
            "revised": [key for key in after if key in before and fingerprint(after[key]) != fingerprint(before[key])],
            "missing": [key for key in before if key not in after],
            "prior_judgments": [t["judgment"] for t in prior["theses"]],
            "current_judgments": [t["judgment"] for t in current["theses"]],
            "original_conditions": copy.deepcopy(prior["conditions"]),
            "knowledge_changes": [{"id": key, "prior_version": old_knowledge.get(key), "current_version": new_knowledge.get(key)}
                                  for key in sorted(set(old_knowledge) | set(new_knowledge)) if old_knowledge.get(key) != new_knowledge.get(key)]}
    if contract_version >= 2:
        changes['calculation_changes'] = _calculation_changes(prior, current)
        changes['calculation_rows'] = calculation_change_rows(changes)
    return changes


def build_version_changes(task, prior_report):
    content = prior_report["content"]
    if fingerprint(content) != prior_report["digest"]:
        raise ContractError("原交付内容与冻结哈希不一致，不能主动复核")
    version = _changes_contract(task)
    changes = _version_changes(task, version_basis(content, content["research_version"],
        contract_version=version), prior_report["digest"])
    if version == DRAFT_VERSION_CHANGES_CONTRACT:
        from .draft_version import compare_observations
        changes['draft_observations'] = compare_observations(content, task)
        if type(task.get('draft_tracking_version')) is int and task['draft_tracking_version'] == 1:
            from .drafts import draft_conditions
            original = draft_conditions(content)
            if task['update_from'].get('draft_conditions') != original:
                raise ContractError('原报告条件与冻结版本不一致，不能覆盖后复核')
            changes['draft_conditions'] = dict(version=1, prior=original, current=draft_conditions(task))
        from .draft_version import comparison_enabled, compare_reports
        if comparison_enabled(task):
            changes['draft_report_comparison'] = compare_reports(content, task)
    return changes


def validate_version_changes(task, prior_report=None):
    if task.get("version_changes_contract") is None:
        return  # Frozen legacy reports remain readable without rewriting them.
    if _changes_contract(task) == DRAFT_VERSION_CHANGES_CONTRACT and prior_report is None:
        raise ContractError('草稿观测差异须与原交付重新核对')
    changes = task.get("changes")
    if not isinstance(changes, dict) or not isinstance(changes.get("prior_basis"), dict):
        raise ContractError("主动更新缺少同版差异记录")
    expected = (build_version_changes(task, prior_report) if prior_report is not None
                else _version_changes(task, changes["prior_basis"], changes.get("prior_digest")))
    if changes != expected:
        raise ContractError("版本差异与原交付或本版研究不一致，不能保存")

# Rendering uses frozen calculation metadata; these are mathematical notation,
# not a lookup of the latest knowledge revision for an old published report.
METHOD_NOTATION = {
    "residual_present_value_v1": ("情景残值现值", "<mi>V</mi><mo>=</mo><mfrac><mrow><mi>C</mi><mo>−</mo><mi>D</mi><mo>−</mo><mi>F</mi></mrow><mrow><mi>N</mi><mo>×</mo><msup><mrow><mo>(</mo><mn>1</mn><mo>+</mo><mi>r</mi><mo>)</mo></mrow><mi>t</mi></msup></mrow></mfrac>", "C为收款，D为债务，F为费用，N为股数，r为必要回报率，t为年数。金额与股本使用相同百万量级。"),
    "etf_daily_nav_premium_v1": ("收盘价与同估值日净值偏离率", "<mi>Q</mi><mo>=</mo><mo>(</mo><mfrac><mi>P</mi><mi>NAV</mi></mfrac><mo>−</mo><mn>1</mn><mo>)</mo><mo>×</mo><mn>100</mn><mo>%</mo>", "P为收盘价，NAV为同一基金份额、人民币及估值日的官方单位净值，均为元。仅作日度事后比较，不表示同一时刻可成交或可实现套利；公布日不代替净值所属日。"),
    "management_custody_fee_summary_v1": ("年度管理托管费率合计及比较", "<msub><mi>T</mi><mi>A</mi></msub><mo>=</mo><msub><mi>M</mi><mi>A</mi></msub><mo>+</mo><msub><mi>C</mi><mi>A</mi></msub><mo>;</mo><msub><mi>T</mi><mi>B</mi></msub><mo>=</mo><msub><mi>M</mi><mi>B</mi></msub><mo>+</mo><msub><mi>C</mi><mi>B</mi></msub><mo>;</mo><mi>D</mi><mo>=</mo><msub><mi>T</mi><mi>A</mi></msub><mo>−</mo><msub><mi>T</mi><mi>B</mi></msub>", "M、C分别为管理费和托管费年费率，T为登记计算的两项合计，单位%/年；D为两基金合计之差，单位百分点/年。合计不是原件直接披露值，不是全部费用率或实际持有收益差。"),
    "observed_index_change_v1": ("已观测指数区间变化", "<mi>R</mi><mo>=</mo><mo>(</mo><mfrac><msub><mi>L</mi><mn>1</mn></msub><msub><mi>L</mi><mn>0</mn></msub></mfrac><mo>−</mo><mn>1</mn><mo>)</mo><mo>×</mo><mn>100</mn><mo>%</mo>", "L₀、L₁为同指数、同收益版本、同计价币种及基期的起终收盘点位，单位点。R为该版本已观测变化，不是基金或用户实际收益、事件因果或预测概率。"),
    "management_custody_fee_comparison_v1": ("年度管理托管费率比较", "<mi>D</mi><mo>=</mo><mo>(</mo><msub><mi>M</mi><mi>A</mi></msub><mo>+</mo><msub><mi>C</mi><mi>A</mi></msub><mo>)</mo><mo>−</mo><mo>(</mo><msub><mi>M</mi><mi>B</mi></msub><mo>+</mo><msub><mi>C</mi><mi>B</mi></msub><mo>)</mo>", "M、C分别为管理费和托管费年费率，单位%/年；A、B对应两基金的已核验份额。D为两项合计之差，单位百分点/年，不是全部费用率或持有收益差。"),
    "observed_price_return_v1": ("已观测区间价格变化", "<mi>R</mi><mo>=</mo><mo>(</mo><mfrac><msub><mi>P</mi><mn>1</mn></msub><msub><mi>P</mi><mn>0</mn></msub></mfrac><mo>−</mo><mn>1</mn><mo>)</mo><mo>×</mo><mn>100</mn><mo>%</mo>", "P₀、P₁分别为起点和终点未复权收盘价，单位元；R为价格变化，不是总收益或事件因果。"),
    "etf_nav_premium_v1": ("同估值时点ETF净值折溢价", "<mi>Q</mi><mo>=</mo><mo>(</mo><mfrac><mi>P</mi><mi>NAV</mi></mfrac><mo>−</mo><mn>1</mn><mo>)</mo><mo>×</mo><mn>100</mn><mo>%</mo>", "P为收盘价，NAV为同类一份ETF同估值时点的官方单位净值，均为元；Q正值为溢价，负值为折价。"),
    "gross_margin_v1": ("同口径营业毛利率", "<mi>G</mi><mo>=</mo><mfrac><mrow><mi>R</mi><mo>−</mo><mi>C</mi></mrow><mi>R</mi></mfrac><mo>×</mo><mn>100</mn><mo>%</mo>", "R为营业收入，C为营业成本，均为百万元，属于同主体、期间、会计准则和合并范围；G为营业毛利率。"),
    "net_profit_margin_v1": ("同口径净利润率", "<mi>M</mi><mo>=</mo><mfrac><mi>NP</mi><mi>R</mi></mfrac><mo>×</mo><mn>100</mn><mo>%</mo>", "NP为报表净利润，R为正营业收入，均为同主体、期间、合并范围及准则的百万元数据；不以归母或扣非净利润替代。"),
    "operating_cash_profit_ratio_v1": ("经营现金流净利润比", "<mi>K</mi><mo>=</mo><mfrac><mi>CFO</mi><mi>NP</mi></mfrac><mo>×</mo><mn>100</mn><mo>%</mo>", "CFO为经营活动产生的现金流量净额，NP为同口径正净利润，均为百万元；不含自由现金流或可分配现金含义，不以比率高低直接判定盈利质量。"),
    "fee_difference_v1": ("同口径年度费用差", "<mi>D</mi><mo>=</mo><msub><mi>f</mi><mi>A</mi></msub><mo>−</mo><msub><mi>f</mi><mi>B</mi></msub>", "fₐ、fᵦ为同口径年度费用率，单位%/年；D为费用差，单位百分点/年，不是实际收益差。"),
    "fee_difference_v2": ("同口径单项年度费率差", "<mi>D</mi><mo>=</mo><msub><mi>f</mi><mi>A</mi></msub><mo>−</mo><msub><mi>f</mi><mi>B</mi></msub>", "fₐ、fᵦ为同类单项年费率；原文单位与独立年度频率均保留。D单位为百分点/年，不是合计费用、实际扣费或持有收益差。"),
}


def calculation_notation(calculation):
    """Present a frozen method and its input unit without refreshing knowledge."""
    method = calculation["method"]
    title, notation, variables = METHOD_NOTATION.get(method,
        (calculation.get("method_title", method), "", ""))
    if METHOD_REGISTRY.get(method, {}).get("same_input_unit"):
        variables = variables.replace("百万元", calculation["parameters"]["amount_unit"])
    formula = ('<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">'
        + notation + '</math>') if notation else ""
    return title, formula, variables


def compose(task):
    if task.get('draft_first_version') == 1 and task.get('draft_report') is not None:
        from .drafts import compose as compose_draft
        return compose_draft(task)
    from .argument_support import validate_adopted
    validate_adopted(task)
    document_only = task.get("intent") == DOCUMENT_CONTENT_INTENT
    if document_only and (task["theses"] or task["calculations"] or task["tracking"]):
        raise ContractError("原件内容核对报告不能包含金融计算、投资论点或判断追踪")
    from .knowledge import task_catalogue_revision
    ResearchKnowledge().validate_package(task.get("knowledge"), revision=task_catalogue_revision(task))
    reading = task.get("reasoning_state", {}).get("reading", {}).get("bundle")
    if reading:
        from .source_reader import validate_reading
        validate_reading(task, reading)
    for item in task["evidence"]:
        validate_evidence(item, task)
        if fingerprint(item.get("snapshot")) != item["snapshot_hash"]:
            raise ContractError("报告原件内容与快照哈希不一致")
        if item["task_id"] != task["id"] or item["research_version"] != task["version"]:
            raise ContractError("报告拒绝跨任务或跨版本证据")
    ids = {item["id"] for item in task["evidence"]}
    evidence = {item["id"]: item for item in task["evidence"]}
    calc_ids = {item["id"] for item in task["calculations"]}
    question_ids = {q["id"] for q in task["questions"]}
    by_question = {q["id"]: q for q in task["questions"]}
    by_calculation = {c["id"]: c for c in task["calculations"]}
    for question in task["questions"]:
        if question["status"] != "已回答":
            continue
        if question.get('answer_scope') == 'general_definition':
            if document_only:
                raise ContractError("原件核对不能混入知识说明")
            ResearchKnowledge().validate_definition_answer(task, question)
            continue
        from .provenance import validate_query_answer, query_requirements, QUERY_ANSWER
        lineage = validate_query_answer(task, question)
        query_only = bool(query_requirements(question)) and len(query_requirements(question)) == len(question["evidence_needs"])
        if not isinstance(question.get("answer"), str) or not question["answer"].strip() or (
                not question.get("refs") and not (query_only and lineage["refs"])) or not set(question["refs"]).issubset(ids):
            raise ContractError("问题回答缺少同版证据或正文")
        if query_only and not question["refs"] and (question.get("calculation_refs") or
                question["answer"] != QUERY_ANSWER):
            raise ContractError("查询留痕报告不能生成金融判断")
        if any(not evidence_applies_to_answer(task,evidence[ref],question['id'],question.get('calculation_refs', []))
                for ref in question['refs']):
            raise ContractError("问题回答引用的材料未完成对应适用性核验")
        if document_only and (question.get("kind") != "fact" or question.get("method_needs") or
                question["answer"] != document_content_answer([evidence[ref] for ref in question["refs"]])):
            raise ContractError("原件内容核对报告只接受定位原文，不接受投资推断")
        if document_only and missing_document_requirements(question, [evidence[ref] for ref in question["refs"]]):
            raise ContractError("原件内容核对回答未覆盖计划登记的全部依据需求")
        if not document_only and missing_evidence_requirements(question, [evidence[ref] for ref in question["refs"]],
                lineage["covered_need_ids"], market_need_ids=calendar_need_ids(task, question)):
            raise ContractError("研究回答未覆盖计划登记的全部依据需求")
        if not set(question.get("calculation_refs", [])).issubset(calc_ids):
            raise ContractError("问题回答引用的计算不存在")
        if task.get("numeric_integrity_contract") == 1:
            validate_numeric_statement(task, dict(question, question_ids=[question["id"]]), ["answer"],
                                       question["refs"], question.get("calculation_refs", []))
        else:
            # Retained reports remain frozen. Re-composing a legacy checkpoint
            # does not grant its prose an exemption from current quantity checks.
            bind_numeric_statement(task, dict(question, question_ids=[question["id"]]), ["answer"],
                                   question["refs"], question.get("calculation_refs", []))
        for ref in question.get("calculation_refs", []):
            calculation = by_calculation[ref]
            if not calculation_applies_to_answer(task, calculation, question['id']) or not set(calculation["input_refs"]).issubset(question["refs"]):
                raise ContractError("问题回答计算与原值引用不对应")
        methods = {by_calculation[ref]["method"] for ref in question.get("calculation_refs", [])}
        if not set(question.get("method_needs", [])).issubset(methods):
            raise ContractError("问题回答缺少已登记的必要计算")
        if any(dep not in by_question or by_question[dep]["status"] != "已回答" for dep in question.get("dependencies", [])):
            raise ContractError("问题回答依赖的必要答案未完成")
        if question.get("kind") in ("mechanism", "investment") and not any(question["id"] in t.get("question_ids", []) for t in task["theses"]):
            raise ContractError("研究判断缺少对应论点")
    for item in task["calculations"]:
        if "assumption_sources" in item:
            from .reasoning import validate_assumptions
            validate_assumptions(task["question"], item)
        if item.get("task_id") != task["id"] or item.get("research_version") != task["version"]:
            raise ContractError("报告拒绝跨任务或跨版本计算")
        if not set(item["input_refs"]).issubset(ids) or fingerprint(item["inputs"]) != item["input_hash"]:
            raise ContractError("计算输入依赖缺失或已变化")
        from .contracts import calculation_input_applies
        if any(not all(calculation_input_applies(task,evidence[ref],ident,item['method'])
                for ident in item.get('question_ids',question_ids)) for ref in item['input_refs']):
            raise ContractError("计算引用的材料未完成对应问题的适用性核验")
        if set(item.get("input_bindings", {}).values()) != set(item["input_refs"]):
            raise ContractError("计算缺少字段到原值的绑定")
        binding = ResearchKnowledge().method_binding(task.get("knowledge"), item["method"])
        from .contracts import validate_financial_method_scope
        validate_financial_method_scope(item['method'], item['parameters'], binding)
        definition = METHOD_REGISTRY[item["method"]]
        if fingerprint(item.get("method_binding")) != fingerprint(binding):
            raise ContractError("计算方法知识与任务锁定版本不一致")
        if type(item.get("implementation_version")) is not int or item["implementation_version"] != binding["implementation_version"]:
            raise ContractError("计算实现版本不一致")
        versions = {ref: {"content_version": evidence[ref]["content_version"],
                          "snapshot_hash": evidence[ref]["snapshot_hash"],
                          "evidence_hash": evidence_binding(evidence[ref])} for ref in item["input_refs"]}
        if fingerprint(item.get("input_versions")) != fingerprint(versions):
            raise ContractError("计算原值版本或证据依赖已变化")
        if item.get("dependency_hash") != calculation_dependency_hash(item):
            raise ContractError("计算方法、参数或原值依赖哈希不一致")
        if item.get("method_title") != definition["title"] or item.get("limitations") != definition.get("limitations", definition["conditions"]):
            raise ContractError("计算方法名称或适用限制与登记定义不一致")
        from .contracts import calculation_context_contract
        date_contract = calculation_context_contract(task, item['method'])
        if item.get('context_contract') != date_contract:
            raise ContractError('计算口径契约与本研究版本不一致')
        validate_calculation_context(item["method"], {field: evidence[ref] for field, ref in item["input_bindings"].items()},
                                     item["parameters"], context_contract=date_contract)
        if not set(item.get("scenario_overrides", {})).issubset(item["input_bindings"]):
            raise ContractError("情景覆盖字段未绑定原值")
        for field, ref in item["input_bindings"].items():
            scenario = item.get("scenario_overrides", {}).get(field)
            original = evidence[ref]["value"]
            if scenario:
                if not scenario.get("reason") or scenario.get("base_ref") != ref or scenario.get("value") != item["inputs"][field]:
                    raise ContractError("压力输入缺少原值与情景假设关系")
            elif item["inputs"][field] != original:
                raise ContractError("计算输入不等于被引用的原件事实")
        expected = calculate(item["method"], item["inputs"], item["parameters"])
        if set(item["output"]) != set(expected):
            raise ContractError("计算输出字段不匹配")
        for key, value in expected.items():
            actual = item["output"][key]
            if isinstance(value, (int, float)):
                if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual) or abs(value - actual) > expected["tolerance"]:
                    raise ContractError("计算复核不一致")
            elif actual != value:
                raise ContractError("计算输出口径不一致")
    for thesis in task["theses"]:
        if thesis.get("task_id") != task["id"] or thesis.get("research_version") != task["version"]:
            raise ContractError("报告拒绝跨任务或跨版本论点")
        references = thesis.get("support", []) + thesis.get("counter", [])
        if not references or not set(references).issubset(ids):
            raise ContractError("报告判断缺少有效证据")
        if any(not any(evidence_applies_to_answer(task,evidence[ref],ident,thesis.get('calculation_refs', []))
                for ident in thesis.get('question_ids', [])) for ref in references):
            raise ContractError("论点引用了未经对应问题核验的材料")
        context={}
        if thesis.get('question_context_receipt'):
            from .argument_support import thesis_question_contexts
            context=thesis_question_contexts(task,thesis.get('question_ids',[]))
            if thesis['question_context_receipt']!=context:
                raise ContractError('论点的问题研究上下文回执已失效或不属于当前完整回答')
        if thesis.get("judgment") in ("支持", "反对") and any(missing_evidence_requirements(
                by_question[q], [evidence[ref] for ref in references],
                market_need_ids=calendar_need_ids(task, by_question[q])) for q in thesis.get("question_ids", [])
                if q in by_question and q not in context):
            raise ContractError("确定性判断未覆盖计划登记的全部依据需求")
        if not set(thesis.get("calculation_refs", [])).issubset(calc_ids):
            raise ContractError("报告计算引用不存在")
        if any(not any(calculation_applies_to_answer(task, by_calculation[ref], ident)
                for ident in thesis.get('question_ids', [])) for ref in thesis.get('calculation_refs', [])):
            raise ContractError('报告论点计算不属于本问题或登记依赖')
        if not thesis.get("question_ids") or not set(thesis["question_ids"]).issubset(question_ids):
            raise ContractError("论点缺少对应研究问题")
        if thesis.get('judgment') in ('支持', '反对') and thesis_obligations(
                task, thesis['question_ids'], list(dict.fromkeys(thesis.get('calculation_refs', [])+[
                    ref for value in context.values() for ref in value['calculation_refs']]))):
            raise ContractError('确定性判断缺少已回答前置问题或本问题已登记必要计算')
        if any(not set(by_calculation[ref]["input_refs"]).issubset(references) for ref in thesis.get("calculation_refs", [])):
            raise ContractError("论点计算与原值引用不对应")
        if task.get("numeric_integrity_contract") == 1:
            validate_numeric_statement(task, thesis, ["title", "investment_meaning", "limitation"],
                                       references, thesis.get("calculation_refs", []))
        else:
            bind_numeric_statement(task, thesis, ["title", "investment_meaning", "limitation"],
                                   references, thesis.get("calculation_refs", []))
        if thesis.get("reference_groups_contract") == 1:
            for side in ("support", "counter"):
                for field, groups in reference_groups(evidence, thesis.get(side, [])).items():
                    if thesis.get(side + "_" + field) != groups:
                        raise ContractError("论点来源或事件分组与核验证据不一致")
    if any(q["required"] and q["status"] != "已回答" for q in task["questions"]) and not task["gaps"]:
        raise ContractError("必要问题未回答但报告缺口为空")
    expected_tracking = [rule for thesis in task["theses"] if (rule := tracking_record(task, thesis)) is not None]
    if not task.get("tracking_contract_version"):
        # Retained checkpoints predate the binding contract. Their original
        # four-field rules remain readable; new runs use full bindings.
        expected_tracking = [{key: value for key, value in rule.items()
            if key not in ("task_id", "research_version", "thesis_hash", "condition_hash")}
            for rule in expected_tracking]
    if task["tracking"] != expected_tracking:
        raise ContractError("验证条件与同版论点不一致，不能改写、遗漏或跨任务引用后交付")
    validate_version_changes(task)
    from .judgment_review import enabled as review_enabled, validate_saved as validate_review
    validate_review(task)
    content = {
        "schema": "researchhelper.report-content", "schema_version": 1, "task_id": task["id"],
        "research_version": task["version"], "cutoff": task["cutoff"], "input_hash": task["input_hash"],
        "title": task["question"] if not task["fixture"] else __import__("researchhelper.cases", fromlist=["get_case"]).get_case(task["fixture"])["title"],
        "question": task["question"],
        "entry": task["entry"], "intent": task["intent"], "objects": task["objects"], "synthetic": task["synthetic"],
        "scope": (DOCUMENT_CONTENT_LIMITATION if document_only else "通用口径说明，不核验当前或历史主体事实，不构成投资判断。" if all(q.get('answer_scope') == 'general_definition' for q in task['questions'])
                  else "合成验收，非真实证券研究" if task["synthetic"] or any(e.get("source") == "合成验收原件" for e in task["evidence"])
                  else "基于留存材料与接口响应逐项核验；响应留存不代表供应商全部权限或研究结论已获验证" if task.get("source_records")
                  else "用户提供材料研究，未联网核验"),
        "questions": task["questions"], "theses": task["theses"], "evidence": task["evidence"], "calculations": task["calculations"],
        "limitations": task["gaps"], "tracking": task["tracking"], "tracking_reason": task.get("tracking_reason", ""),
        "resolved_gap_records": copy.deepcopy(task.get("resolved_gap_records", [])),
        "optional_findings": task.get("optional_findings", []), "plan_version": task.get("plan_version", 1),
        "source_records": task.get("source_records", []), "data_need_specs": task.get("data_need_specs", []),
        "reasoning_state": task.get("reasoning_state", {}), "run_mode": task.get("run_mode", "host"),
        "reading_coverage": copy.deepcopy(task.get("reasoning_state", {}).get("reading", {}).get("bundle", {}).get("coverage", [])),
        "reading_diagnostics": copy.deepcopy(task.get('reasoning_state', {}).get('reading', {}).get('bundle', {}).get('limitations', [])),
        "changes": task.get("changes"), "update_from": task.get("update_from"),
        "knowledge": task.get("knowledge", {}), "template_version": TEMPLATE_VERSION,
        "numeric_integrity_contract": task.get("numeric_integrity_contract"),
    }
    if "research_mode" in task:
        content["research_mode"] = task["research_mode"]
    from .provenance import collection_coverage
    coverage = collection_coverage(task)
    if coverage:
        content['collection_coverage'] = coverage
    if 'dependency_synthesis_version' in task:
        content['dependency_synthesis_version'] = task['dependency_synthesis_version']
    if 'definition_projection_version' in task:
        content['definition_projection_version'] = task['definition_projection_version']
    if 'fee_original_selection_version' in task:
        content['fee_original_selection_version'] = task['fee_original_selection_version']
    if 'fee_candidate_encoding' in task:
        content['fee_candidate_encoding'] = task['fee_candidate_encoding']
    if 'planning_submission_recovery_version' in task:
        content['planning_submission_recovery_version'] = task['planning_submission_recovery_version']
    if 'argument_submission_recovery_version' in task:
        content['argument_submission_recovery_version'] = task['argument_submission_recovery_version']
    if 'official_channels_version' in task:
        content['official_channels_version'] = task['official_channels_version']
    if 'official_catalogue_pagination_version' in task:
        content['official_catalogue_pagination_version'] = task['official_catalogue_pagination_version']
    if 'independent_original_selections_version' in task:
        content['independent_original_selections_version'] = task['independent_original_selections_version']
    if 'official_policy_channels_version' in task:
        content['official_policy_channels_version'] = task['official_policy_channels_version']
    if 'official_temporal_contract_version' in task:
        content['official_temporal_contract_version'] = task['official_temporal_contract_version']
    if 'official_selection_reference_version' in task:
        content['official_selection_reference_version'] = task['official_selection_reference_version']
    if 'verbatim_evidence_segments_version' in task:
        content['verbatim_evidence_segments_version'] = task['verbatim_evidence_segments_version']
    if 'planning_definition_scope_version' in task:
        content['planning_definition_scope_version'] = task['planning_definition_scope_version']
    if 'initial_acquisition_scope_version' in task:
        content['initial_acquisition_scope_version'] = task['initial_acquisition_scope_version']
    for policy in ('review_revision_readiness_version', 'registered_source_reading_version',
                   'api_need_compatibility_version', 'discovery_planned_scope_version', 'financial_batch_query_version', 'semantic_query_encoding', 'financial_batch_followup_version',
                   'progressive_financial_acquisition_version', 'declared_reading_capacity_version',
                   'index_weight_evidence_version', 'analysis_input_presentation_version',
                   'review_choice_delivery_version', 'identity_candidate_lookup_version',
                   'name_candidate_observation_version', 'original_number_catalogue_capacity_version',
                   'original_explicit_unit_reference_version', 'original_quantity_qualifier_version', 'acquisition_reading_intent_version',
                   'financial_acquisition_route_version', 'original_duration_anchor_version', 'catalogue_alternative_version', 'typed_evidence_needs_version', 'typed_observation_contract_version', 'typed_numeric_selection_version', 'required_numeric_completion_version', 'original_observation_reference_version', 'shared_original_dates_version', 'source_extraction_batches_version', 'source_extraction_id_choices_version', 'official_index_sources_version'):
        if policy in task:
            content[policy] = task[policy]
    from .source_requirements import frozen_requirements
    if 'flat_api_selection_version' in task:
        content['flat_api_selection_version'] = task['flat_api_selection_version']
    if 'registered_etf_share_join_version' in task:
        content['registered_etf_share_join_version'] = task['registered_etf_share_join_version']
    if 'review_answer_choices_version' in task:
        content['review_answer_choices_version'] = task['review_answer_choices_version']
    if 'source_field_inventory_version' in task:
        content['source_field_inventory_version'] = task['source_field_inventory_version']
    if 'official_index_profile_catalogue_version' in task:
        content['official_index_profile_catalogue_version'] = task['official_index_profile_catalogue_version']
    if 'ifind_metadata_sources_version' in task:
        content['ifind_metadata_sources_version'] = task['ifind_metadata_sources_version']
    if 'partial_financial_response_version' in task:
        content['partial_financial_response_version'] = task['partial_financial_response_version']
    if 'structured_source_priority_version' in task:
        content['structured_source_priority_version'] = task['structured_source_priority_version']
    if 'fixed_financial_catalog_version' in task:
        content['fixed_financial_catalog_version'] = task['fixed_financial_catalog_version']
    for policy in ('table_geometry_projection_version', 'financial_statement_details_version', 'financial_amount_reference_version', 'source_request_fields_version', 'financial_data_product_version', 'research_outline_version', 'financial_response_navigation_version', 'financial_statement_unit_scope_version', 'economic_navigation_version', 'source_capability_roadmap_version', 'series_batch_version', 'planning_scope_version', 'economic_discovery_version', 'fixed_financial_route_version', 'fixed_financial_context_version', 'extraction_read_isolation_version', 'mixed_atomic_api_reading_version', 'economic_observation_version', 'metadata_navigation_progress_version'):
        if policy in task:
            content[policy] = task[policy]
    requirements=frozen_requirements(task)
    if requirements is not None:content['source_requirements']=requirements
    from .period_targets import enabled as period_targets_enabled, resolve as resolve_period_targets
    if period_targets_enabled(task):
        content['source_period_resolution'] = dict(version=1, targets=resolve_period_targets(task),
            financial_approval=False, catalogue_completeness_verified=False)
    if 'original_span_selection_version' in task:
        content['original_span_selection_version'] = task['original_span_selection_version']
    if 'mixed_original_selection_version' in task:
        content['mixed_original_selection_version'] = task['mixed_original_selection_version']
    if 'original_context_selection_version' in task:
        content['original_context_selection_version'] = task['original_context_selection_version']
    if 'method_catalogue_version' in task:
        content['method_catalogue_version'] = task['method_catalogue_version']
    for policy in ('filing_candidate_distribution_version', 'filing_filter_profile_version', 'security_declaration_encoding', 'analysis_prose_reference_version', 'financial_quote_fallback_version', 'discovery_sample_scope_version', 'acquisition_request_encoding', 'object_discovery_version', 'filing_keyword_filter_version', 'analysis_question_map_version', 'original_question_link_version', 'original_context_question_link_version', 'analysis_feedback_projection_version', 'analysis_structure_recovery_version', 'argument_choice_recovery_version', 'navigation_question_scope_version', 'literal_quantity_reference_version', 'original_span_diagnostics_version', 'review_basis_presentation_version', 'analysis_question_scope_version', 'statistical_reading_version',
                   'research_synthesis_version', 'financial_selector_projection_version', 'financial_observation_reference_version', 'financial_question_owner_version', 'negative_claim_scope_version', 'financial_need_map_version', 'financial_input_reading_priority_version', 'knowledge_reading_navigation_version', 'analysis_schema_sharing_version', 'analysis_reference_choice_recovery_version', 'partial_fee_input_completion_version', 'initial_fee_input_completion_version', 'review_delivery_call_version', 'original_fee_context_mapping_version', 'original_literal_choice_recovery_version', 'original_concept_reading_version', 'catalog_scoped_writing_version', 'general_original_selection_version', 'statistical_period_version',
                   'listed_etf_unit_context_version', 'current_scope_planning_version', 'catalog_metadata_basis_version', 'original_temporal_choice_recovery_version', 'ifind_gateway_recovery_version', 'extraction_knowledge_guidance_version', 'original_concept_navigation_version', 'catalog_query_result_basis_version', 'original_interval_context_version', 'listed_report_unit_context_version', 'structured_schema_sharing_version', 'method_excerpt_selection_version', 'method_literal_address_version', 'original_local_span_version', 'original_whole_part_selection_version',
                   'catalogue_search_navigation_version', 'extraction_value_recovery_version',
                   'original_requirement_isolation_version', 'original_requirement_feedback_version', 'source_period_targets_version', 'knowledge_catalogue_revision', 'evidence_processing_state_version', 'original_scope_wire_version', 'series_parameter_selection_version', 'series_parameter_registry', 'document_date_role_version', 'source_label_selection_version', 'statistical_metric_identity_version', 'mixed_question_revision_version',
                   'original_number_reference_version', 'evidence_gap_lifecycle_version', 'method_source_coverage_version', 'acquisition_budget_navigation_version',
                   'acquisition_downstream_budget_version', 'registered_api_source_paths_version', 'model_context_budget_version',
                   'missing_api_context_selection_version', 'daily_nav_date_roles_version', 'observed_window_date_roles_version', 'market_coverage_basis_version', 'reviewed_writing_revision_version', 'nav_original_choice_version',
                   'nav_original_only_selection_version', 'official_etf_pcf_version', 'api_context_wire_version',
                   'official_etf_cell_selection_version',
                   'market_calendar_coverage_version', 'economic_identifier_namespace_version',
                   'planning_main_binding_version', 'original_period_selection_version', 'original_prompt_projection_version', 'method_literal_quantity_version', 'original_observation_selection_version', 'index_constituent_source_version', 'candidate_code_observation_version', 'fee_method_catalogue_version', 'nonfinancial_count_grammar_version', 'numeric_lookup_isolation_version'):
        if policy in task:
            content[policy] = copy.deepcopy(task[policy])
    if review_enabled(task):
        for key in ('judgment_review_contract', 'judgment_review_baseline', 'judgment_review_proposals',
                    'judgment_reviews', 'judgment_review_pending'):
            content[key] = copy.deepcopy(task[key])
        content['template_version'] = 'rh-paper-4'
    host_reading = next((r for r in task.get("source_readings", []) if r["reading_hash"] == task.get("host_result", {}).get("reading_hash")), None)
    if host_reading:
        from .source_reader import validate_reading
        validate_reading(task, host_reading)
        content["reading_coverage"] = copy.deepcopy(host_reading["coverage"])
        content["optional_findings"] = list(dict.fromkeys(content["optional_findings"] + host_reading["limitations"]))
        content['reading_diagnostics'] = list(dict.fromkeys(content['reading_diagnostics'] + host_reading['limitations']))
    from .market_calendar import coverage as market_coverage
    calendar_coverage = market_coverage(task)
    if calendar_coverage is not None:
        content['market_coverage'] = calendar_coverage
    basis = calendar_scope_basis(task)
    if basis is not None:
        content['market_coverage_scope_basis'] = basis
    from .catalog_metadata import scope_basis as catalog_scope_basis
    catalog_basis = catalog_scope_basis(task)
    if catalog_basis is not None:
        content['catalog_metadata_scope_basis'] = catalog_basis
    content = copy_json_value(content)
    content["content_hash"] = fingerprint(content)
    return content


def check_content(content):
    if content.get("schema") != "researchhelper.report-content" or content.get("schema_version") != 1:
        raise ContractError("冻结报告协议不匹配")
    if fingerprint({k: v for k, v in content.items() if k != "content_hash"}) != content.get("content_hash"):
        raise ContractError("冻结报告内容已变化，需重新核验")


def source_publisher_label(item):
    """Name a retained publisher, never trust host substrings or model prose."""
    source = item.get('source')
    if source == 'official':
        origin = item.get('artifact', {}).get('snapshot', {}).get('official_origin', {})
        try:
            address = urlsplit(origin.get('url', ''))
            if address.scheme == 'https':
                return {'www.federalreserve.gov': '美联储', 'www.stats.gov.cn': '国家统计局'}.get(
                    address.netloc, '官方原文')
        except ValueError:
            pass
        return '官方原文'
    return {'ifind': 'iFinD', 'tinyshare': 'Tinyshare', 'material': '用户提供材料'}.get(source, source or '未登记来源')


def _official_document_heading(content):
    """Use only one adopted, checked original's directory title as a label."""
    from .official_sources import article_url
    refs = {ref for question in content.get('questions', []) for ref in question.get('refs', [])}
    refs.update(ref for thesis in content.get('theses', [])
                for key in ('support', 'counter') for ref in thesis.get(key, []))
    selected = [item for item in content.get('evidence', []) if item['id'] in refs]
    if not selected or {item['id'] for item in selected} != refs:
        return None
    identities = set()
    for item in selected:
        artifact = item.get('artifact', {})
        origin = artifact.get('snapshot', {}).get('official_origin', {})
        if (item.get('source') != 'official'
                or item.get('source_check') != item.get('applicability_check') or item.get('source_check') != '通过'
                or artifact.get('source') != 'official' or artifact.get('operation') != 'release_original'
                or artifact.get('kind') != 'material' or item.get('artifact_id') != artifact.get('id')
                or not item.get('snapshot_hash') or item['snapshot_hash'] != artifact.get('snapshot_hash')):
            return None
        title, url = origin.get('title'), origin.get('url')
        if (not isinstance(title, str) or not title or len(title) > 36
                or any(character in title for character in '\n\r。；;！？!?<>')):
            return None
        try:
            channel = 'fed_fomc' if urlsplit(url).netloc == 'www.federalreserve.gov' else 'nbs_release'
            article_url(url, channel=channel)
        except (ContractError, TypeError, ValueError):
            return None
        identities.add((artifact['id'], item['snapshot_hash'], title, url))
    return next(iter(identities))[2] if len(identities) == 1 else None


def _economic_observation_heading(content):
    """Name a single observed series using its adopted literal metadata.

    This is a display label, not a generated claim or replacement for the
    frozen question. Mixed topics, unverified sources and analytical tasks
    retain the ordinary heading rules.
    """
    required = [q for q in content.get('questions', []) if q.get('required')]
    if (not required or {q.get('kind') for q in required} != {'fact'}
            or content.get('theses') or content.get('calculations')):
        return None
    refs = {ref for question in required for ref in question.get('refs', [])}
    selected = [item for item in content.get('evidence', []) if item.get('id') in refs]
    if not refs or {item.get('id') for item in selected} != refs:
        return None
    identities = set()
    for item in selected:
        metadata = item.get('economic_context') or {}
        if (item.get('source') != 'ifind'
                or item.get('source_check') != '通过' or item.get('applicability_check') != '通过'
                or (item.get('identity') or {}).get('mode') != 'economic_indicator_observation'
                or not metadata.get('indicator')
                or (metadata.get('metadata_reference') or {}).get('display_id') != metadata['indicator']):
            return None
        fields = tuple(metadata.get(key) for key in ('indicator', 'name', 'country', 'frequency', 'unit', 'data_source'))
        if not all(isinstance(value, str) for value in fields):
            return None
        identities.add(fields)
    if len(identities) != 1:
        return None
    _, name, country, _, _, _ = next(iter(identities))
    label = (country if country and country not in name else '') + name.replace(':', '').replace('：', '') + '观察'
    if not name or len(label) > 36 or any(character in label for character in '\n\r。；;！？!?<>'):
        return None
    return label


def report_heading(content):
    """A display label, not a summary, evidence claim or rewritten question."""
    if content.get("intent") == DOCUMENT_CONTENT_INTENT:
        return DOCUMENT_CONTENT_INTENT
    from .knowledge import definition_title
    title = definition_title(content)
    if title:
        return title
    original = content["title"]
    if len(original) <= 36 and not any(character in original for character in "\n\r。；;！？!?<>"):
        return original
    subjects = content.get("objects", [])
    calculations = content.get('calculations', [])
    methods = {item['method'] for item in calculations}
    if not subjects:
        # A natural-language task may have no adopted code list. Use only
        # frozen, checked calculation inputs, never parse a company from prose.
        used = {ref for item in calculations for ref in item.get('input_refs', [])}
        inputs = [item for item in content.get('evidence', []) if item['id'] in used
                  and item.get('source_check') == item.get('applicability_check') == '通过']
        subjects = list(dict.fromkeys(item['object'] for item in inputs))
        entities = {(item.get('calculation_context') or {}).get('entity_id') for item in inputs}
        if len(subjects) == 1 and len(entities) == 1 and next(iter(entities), None):
            subjects = list(entities)
    if subjects and methods == {'net_profit_margin_v1', 'operating_cash_profit_ratio_v1'}:
        label = '、'.join(subjects) + '盈利与现金流'
        if len(label) <= 36:
            return label
    if not subjects:
        economic_title = _economic_observation_heading(content)
        if economic_title:
            return economic_title
        official_title = _official_document_heading(content)
        if official_title:
            return official_title
        coverage = content.get('collection_coverage', [])
        themes = list(dict.fromkeys(row['topic'] + row['discovery_kind'] for row in coverage))
        if len(themes) == 1 and not content.get('evidence') and not content.get('theses'):
            return themes[0] + '资料核对'
    suffix = {"解释变化": "机制分析", "比较对象": "比较研究", "验证观点": "观点核验"}.get(content.get("intent"), "研究报告")
    # Intent is an entry hint. The adopted required questions define what the
    # report actually studies; a price fact must not promise causal analysis.
    required = [question for question in content.get("questions", []) if question.get("required")]
    kinds = {question.get("kind") for question in required}
    if kinds == {"fact"} and not content.get("theses"):
        suffix = "事实研究"
        methods = {item['method'] for item in content.get('calculations', [])}
        if len(required) == 1 and methods == {'observed_price_return_v1'}:
            suffix = "区间价格研究"
        elif len(required) == 1 and methods == {'observed_index_change_v1'}:
            suffix = "指数区间观察"
    elif kinds == {"comparison"}:
        suffix = "比较研究"
    elif kinds == {"investment"}:
        suffix = "观点核验" if content.get("intent") == "验证观点" else "投资研究"
    elif required and kinds != {"mechanism"}:
        suffix = "研究报告"
    if subjects:
        label = "、".join(subjects) + suffix
        if len(label) <= 36 and not any(character in label for character in "\n\r。；;！？!?<>"):
            return label
    entry = content.get("entry")
    return {"个股": "个股研究报告", "ETF": "ETF研究报告", "指数": "指数研究报告", "行业": "行业研究报告",
            "产业链": "产业链研究报告", "事件": "事件研究报告", "宏观": "宏观研究报告", "混合": "综合研究报告"}.get(entry, "研究报告")


# Public report API remains stable across App and Skill.
from .visual import source_locator_label, research_figures

def render(content, resources, visual_profile=None, report_profile='standard'):
    from .report_designer import render_html
    return render_html(content, resources, profile=report_profile, visual_profile=visual_profile)


def stage_pending_html(task, page):
    """Bind the exact standard report page before saving its checkpoint."""
    from .report_designer import read_presentation_document

    if not isinstance(page, str) or not page.strip():
        raise ContractError('报告页面尚未生成，请重试保存')
    digest = fingerprint(task['pending_content'])
    tree, document = read_presentation_document(page)
    body = tree.find('body')
    if (type(document.get('version')) is not int or document['version'] != 1
            or document.get('output_type') != 'report' or document.get('profile') != 'standard'
            or document.get('content_hash') != digest or body is None
            or body.get('data-report-variant') != 'standard' or body.find('.//main') is None):
        raise ContractError('报告页面与待保存的正文或详略不一致')
    task['pending_html_binding'] = dict(content_digest=digest, html_sha256=sha256(page.encode()).hexdigest())
    title = document.get('title')
    if isinstance(title, str) and title.strip():
        task['pending_html_binding']['title'] = ' '.join(title.split())
    task['pending_html'] = page


def pending_html_sha256(task, content_digest):
    """Verify retained bytes against the receipt made at rendering time."""
    page, binding = task.get('pending_html'), task.get('pending_html_binding')
    if (not isinstance(page, str) or not page.strip() or not isinstance(binding, dict)
            or binding.get('content_digest') != content_digest):
        return None
    try:
        digest = sha256(page.encode()).hexdigest()
    except UnicodeEncodeError:
        return None
    return digest if digest == binding.get('html_sha256') else None


def clear_pending_html(task):
    task.pop('pending_html', None)
    task.pop('pending_html_binding', None)
