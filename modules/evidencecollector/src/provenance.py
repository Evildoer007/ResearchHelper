"""Query lineage is a scoped reference, never a financial evidence item.

Project only the retained, budgeted request and business-response identity.
The current implementation accepts the kernel's current-acquisition receipt;
imported snapshots and successful HTTP flags alone cannot mint this reference.
"""

from __future__ import annotations

import copy

from .contracts import ContractError, DOCUMENT_CONTENT_INTENT, evidence_requirements, fingerprint
from .evidence import business_payload, current_query_reference, current_original_reference, safe_parameters

QUERY_SCOPE = "query_provenance"
REFERENCE_FIELDS = {"version", "artifact_id", "request_hash", "snapshot_hash", "projection_hash"}
LIMITATION = "仅核对本任务的取数留痕，不核验金融字段含义、单位、主体事实、首次公开时间或投资判断。"
QUERY_ANSWER = "本次取数的来源、操作、请求范围与业务响应身份见查询留痕。" + LIMITATION


def query_requirements(question):
    scopes = question.get("evidence_need_scopes", {})
    return [need for need in evidence_requirements(question) if scopes.get(need["text"]) == QUERY_SCOPE]


def query_only(question):
    return bool(query_requirements(question)) and len(query_requirements(question)) == len(question.get("evidence_needs", []))


def validate_query_only_proposal(question, proposal):
    if query_only(question) and (proposal.get("answer") != QUERY_ANSWER or proposal.get("refs", [])
            or proposal.get("calculation_refs", []) or "numeric_segments" in proposal):
        raise ContractError("独立查询核对只能采用实际留痕，不生成金融判断")


def query_projection(task, record):
    """Reject stale, fabricated or private source metadata before projection."""
    if not isinstance(record, dict) or task.get("intent") == DOCUMENT_CONTENT_INTENT:
        raise ContractError("查询留痕只用于普通研究的实际取数记录")
    if (type(record.get("research_version")) is not int or type(record.get("content_version")) is not int
            or not isinstance(record.get("acquisition"), dict)
            or type(record["acquisition"].get("version")) is not int):
        raise ContractError("查询留痕的版本必须为登记整数")
    retained = [r for r in task.get("source_records", []) if r.get("id") == record.get("id")]
    availability = (current_original_reference(task, record) if record.get('kind') == 'filing'
                    else current_query_reference(task, record))
    if (len(retained) != 1 or fingerprint(retained[0]) != fingerprint(record)
            or availability is None):
        raise ContractError("查询留痕缺少同任务同版的实际取数与响应绑定")
    if (safe_parameters(record["parameters"]) != record["parameters"] or
            business_payload(record["snapshot"]) != record["snapshot"]):
        raise ContractError("查询留痕不能包含凭据或未清理的私密响应")
    # This is metadata, not a row-value decoder. Parameters retain their actual
    # names and values; neither CPS=1 nor a response row count becomes a price.
    projection = {"version": 1, "usage_scope": QUERY_SCOPE,
        "task_id": task["id"], "research_version": task["version"],
        "artifact_id": record["id"], "source": record["source"], "operation": record["operation"],
        "parameters": copy.deepcopy(record["parameters"]), "request_hash": record["request_hash"],
        "snapshot_hash": record["hash"], "content_version": record["content_version"],
        "obtained_at": record["obtained_at"], "response_status": record["status"],
        "limitation": LIMITATION}
    payload = record["snapshot"]
    if record["source"] == "ifind":
        projection["response_fields"] = sorted({field for table in payload.get("tables", [])
            if isinstance(table, dict) and isinstance(table.get("table"), dict)
            for field in table["table"] if isinstance(field, str)})
    else:
        projection["response_fields"] = sorted({field for row in payload.get("data", [])
            if isinstance(row, dict) for field in row if isinstance(field, str)})
    return projection


def query_reference(task, record):
    projection = query_projection(task, record)
    return {"version": 1, "artifact_id": record["id"], "request_hash": record["request_hash"],
        "snapshot_hash": record["hash"], "projection_hash": fingerprint(projection)}


def query_candidates(task):
    result = []
    for record in task.get("source_records", []):
        try:
            result.append({"reference": query_reference(task, record), "record": query_projection(task, record)})
        except ContractError:
            continue  # Imported or unbound sources remain readable, not approved.
    return result


def collection_coverage(task):
    """Describe receipt coverage, never approve the returned financial values.

    Recompute counts from a same-task, transport-bound original response.
    No amount, inferred unit, company identity or universe approval is emitted.
    Imported or modified snapshots cannot acquire collection proof here.
    """
    if type(task.get('financial_batch_query_version')) is not int or task['financial_batch_query_version'] != 1:
        return []
    from .draft_sources import _number
    from .financial_sources import financial_batch_response
    from .subject_scope import subject_allowed
    result = []
    for source in task.get('source_records', []):
        if source.get('source') != 'ifind' or source.get('operation') != 'smart_stock_picking':
            continue
        try:
            batch = financial_batch_response(task, source['parameters'], source['snapshot'])
            reference = query_reference(task, source)
        except (ContractError, KeyError, TypeError, ValueError):
            continue
        rows, period = [], batch['period']
        columns = {field: f'{field}[{period}]' for field in batch['fields']}
        for location in batch['row_locations']:
            table = source['snapshot']['tables'][location['table']]['table']
            row = location['row']
            code, name = table['股票代码'][row], table['股票简称'][row]
            if not subject_allowed(task, code) or not subject_allowed(task, name or ''):
                continue
            received = {field: key in table and _number(table[key][row]) is not None
                        for field, key in columns.items()}
            rows.append(dict(code=code, name=name,
                classification=table.get('所属同花顺行业', [None] * len(table['股票代码']))[row],
                fields=received))
        result.append(dict(version=1, reference=reference, source=source['source'],
            topic=batch['topic'], discovery_kind=batch['discovery_kind'],
            obtained_at=source['obtained_at'], requested_period=batch['period'],
            row_count=len(rows), returned_unique_codes=len({row['code'] for row in rows}), rows=rows,
            fields=[dict(name=field, received=sum(row['fields'][field] for row in rows),
                missing=sum(not row['fields'][field] for row in rows)) for field in batch['fields']],
            financial_values_approved=False, full_universe_verified=False,
            limitation='仅统计本次接口返回记录。原始金额的单位、币种及报表口径尚未核验，不参与金额比较、计算或投资判断；返回记录不证明完整行业范围。'))
    return result


def bind_query_references(task, question, submitted):
    """Explicitly assign immutable metadata to explicitly typed query needs.

    Selection remains the Agent's responsibility. The kernel never infers a
    need type from words or allows metadata to cover untyped financial needs.
    """
    if not isinstance(submitted, list) or len(submitted) > 50:
        raise ContractError("查询引用必须为限定长度的记录列表")
    if not submitted:
        return {"refs": [], "records": [], "covered_need_ids": []}
    if (question.get("kind") != "fact" or question.get("answer_scope") is not None
            or task.get("intent") == DOCUMENT_CONTENT_INTENT):
        raise ContractError("查询引用不能用于口径说明、机制、投资判断或原件核对")
    allowed = {need["id"] for need in query_requirements(question)}
    if not allowed:
        raise ContractError("问题未明确登记查询留痕需求，不能用元数据替代金融事实")
    records, refs, covered, seen = [], [], set(), set()
    for proposal in submitted:
        if (not isinstance(proposal, dict) or set(proposal) != REFERENCE_FIELDS | {"evidence_need_ids"}
                or type(proposal.get("version")) is not int or proposal["version"] != 1):
            raise ContractError("查询引用字段或版本无效")
        needs = proposal["evidence_need_ids"]
        if (not isinstance(needs, list) or not needs or any(not isinstance(n, str) or n not in allowed for n in needs)
                or len(set(needs)) != len(needs)):
            raise ContractError("查询留痕只能覆盖本问题已登记的查询需求")
        source = next((r for r in task.get("source_records", []) if r.get("id") == proposal["artifact_id"]), None)
        if isinstance(source, dict) and "question_ids" in source and question["id"] not in source["question_ids"]:
            raise ContractError("查询记录不属于所选问题的数据需求")
        expected = query_reference(task, source)
        if fingerprint({key: proposal[key] for key in REFERENCE_FIELDS}) != fingerprint(expected):
            raise ContractError("查询引用的请求、响应或元数据版本已变化")
        if proposal["artifact_id"] in seen:
            raise ContractError("同一查询引用不能重复计为不同来源")
        seen.add(proposal["artifact_id"])
        records.append(query_projection(task, source))
        refs.append(copy.deepcopy(proposal))
        covered.update(needs)
    result = {"refs": refs, "records": records, "covered_need_ids": sorted(covered)}
    result["receipt"] = {"version": 1, "usage_scope": QUERY_SCOPE,
        "task_id": task["id"], "research_version": task["version"], "input_hash": task["input_hash"],
        "question_id": question["id"], "question_hash": fingerprint({key: question[key] for key in
            ("id", "question", "kind", "evidence_needs", "evidence_need_scopes")}),
        "references_hash": fingerprint(refs), "records_hash": fingerprint(records),
        "covered_need_ids": sorted(covered), "financial_approval": False}
    return result


def validate_query_answer(task, question):
    bound = bind_query_references(task, question, question.get("provenance_refs", []))
    validate_query_only_proposal(question, question)
    if bound["refs"]:
        if (fingerprint(question.get("provenance_records")) != fingerprint(bound["records"]) or
                fingerprint(question.get("provenance_integrity")) != fingerprint(bound["receipt"])):
            raise ContractError("回答的查询留痕内容或核对记录已变化")
    elif "provenance_records" in question or "provenance_integrity" in question:
        raise ContractError("回答不能保留无引用的查询批准记录")
    return bound
