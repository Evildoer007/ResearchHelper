"""Read-only research evaluation, separate from execution and model inputs.

This checks functional invariants and independently specified expectations. It
does not grade financial reasoning semantically, approve evidence, change a
task, or treat a development case as independent quality validation.
"""

import copy
import hashlib
import math

from researchhelper.contracts import ContractError, ENTRIES, INTENTS, fingerprint
from researchhelper.reports import compose


VERSION = 1


def input_scope(task):
    return {key: copy.deepcopy(task[key])
            for key in ("question", "entry", "intent", "objects", "cutoff")}


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def evaluate(task, specification, *, saved_delivery=None):
    """Expected results stay outside the Agent and must bind its locked input."""
    try:
        before = fingerprint(task)
    except (TypeError, ValueError):
        raise ContractError("评估输入不是规范化研究记录，不能生成有效任务哈希") from None
    task = copy.deepcopy(task)
    spec = copy.deepcopy(specification)
    required = {"id", "split", "input_digest", "expected_status", "question_states"}
    if not isinstance(spec, dict) or set(spec) != required:
        raise ContractError("评估案例字段不完整或含未登记字段")
    if not isinstance(spec["id"], str) or not spec["id"] or spec["split"] not in ("development", "holdout"):
        raise ContractError("评估案例身份或分组无效")
    if spec["input_digest"] != fingerprint(input_scope(task)):
        raise ContractError("评估案例不对应本任务锁定输入")
    if not isinstance(spec["expected_status"], str) or not isinstance(spec["question_states"], dict):
        raise ContractError("评估期望状态格式无效")
    questions = task.get("questions", [])
    if set(spec["question_states"]) != {question["id"] for question in questions if question["required"]}:
        raise ContractError("评估期望必须覆盖全部必要问题，不能只统计成功分支")
    if any(state not in ("已回答", "未回答", "待回答") for state in spec["question_states"].values()):
        raise ContractError("评估问题期望状态无效")
    if spec["split"] == "holdout" and (task.get("synthetic") or task.get("fixture")):
        raise ContractError("合成开发案例不能登记为独立研究质量验证")

    violations = []
    if task.get("entry") not in ENTRIES or task.get("intent") not in INTENTS:
        violations.append("invalid_research_scope")
    ledger = task.get("ledger", {})
    costs = {}
    for kind in ("data_requests", "model_calls", "seconds"):
        used, cap = ledger.get("used", {}).get(kind), ledger.get("limits", {}).get(kind)
        valid = lambda value: not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
        counts_valid = kind == "seconds" or (type(used) is int and type(cap) is int)
        if not valid(used) or not valid(cap) or not counts_valid or cap <= 0 or used < 0 or used > cap + 1e-9:
            violations.append("invalid_budget_" + kind)
            costs[kind] = None
        else:
            costs[kind] = used
    # Shared content validation rechecks actual references, numbers, units,
    # methods and completion status. Passing is not financial semantic proof.
    try:
        content = compose(task)
    except (ContractError, KeyError, TypeError, ValueError):
        content = None
        violations.append("content_contract_failed")
    needed = [question for question in questions if question["required"]]
    answered = [question for question in needed if question.get("status") == "已回答"]
    if task.get("status") == "已完成" and len(answered) != len(needed):
        violations.append("wrong_completion_status")
    delivery = task.get("delivery") if isinstance(task.get("delivery"), dict) else {}
    if task.get("status") in ("已完成", "部分完成") and not delivery:
        violations.append("missing_saved_delivery")
    if delivery and delivery.get("version") != task.get("version"):
        violations.append("delivery_version_mismatch")
    saved_content_hash, saved_html_hash = None, None
    if task.get("status") in ("已完成", "部分完成"):
        if not isinstance(saved_delivery, dict):
            violations.append("saved_delivery_not_verified")
        else:
            try:
                stored = saved_delivery["content"]
                saved_content_hash = fingerprint(stored)
                html = saved_delivery["html"]
                if (saved_content_hash != saved_delivery["digest"] or
                        saved_content_hash != delivery.get("digest") or
                        not isinstance(html, str) or not html.strip()):
                    raise ContractError("冻结交付哈希或正文无效")
                identity = {"task_id": task["id"], "research_version": task["version"],
                            "input_hash": task["input_hash"]}
                if any(stored.get(key) != value for key, value in identity.items()):
                    raise ContractError("冻结交付身份不一致")
                for key in ("question", "entry", "intent", "objects", "cutoff", "questions", "evidence",
                            "calculations", "theses", "tracking", "knowledge"):
                    if fingerprint(stored[key]) != fingerprint(task[key]):
                        raise ContractError("冻结交付与任务依据不一致")
                saved_html_hash = hashlib.sha256(html.encode()).hexdigest()
            except (ContractError, KeyError, TypeError, ValueError):
                violations.append("saved_delivery_integrity_failed")
    matches = sum(question.get("status") == spec["question_states"][question["id"]] for question in needed)
    expectation_passed = task.get("status") == spec["expected_status"] and matches == len(needed)
    structured_statements = sum(question.get("status") == "已回答" for question in questions) + len(task.get("theses", []))
    record = {
        "evaluation_version": VERSION, "case_id": spec["id"], "split": spec["split"],
        "case_specification_hash": fingerprint(spec), "input_digest": spec["input_digest"],
        "task_id": task["id"], "research_version": task["version"], "task_hash": before,
        "entry": task["entry"], "intent": task["intent"], "run_mode": task.get("run_mode"),
        "functional_status": "passed" if expectation_passed and not violations else "failed",
        "expected_status_matched": task.get("status") == spec["expected_status"],
        "required_questions": len(needed), "answered_questions": len(answered),
        "question_completion_ratio": ratio(len(answered), len(needed)),
        "question_expectation_matches": matches,
        "critical_violations": sorted(set(violations)), "costs": costs,
        "validated_content_hash": fingerprint(content) if content is not None else None,
        "saved_content_hash": saved_content_hash, "saved_html_hash": saved_html_hash,
        "structured_answer_and_thesis_count": structured_statements,
        "important_statement_count": None,
        "semantic_citation_accuracy": None, "financial_reasoning_quality": None,
        "counterevidence_recall": None, "independent_semantic_review_status": "not_reviewed",
        "independent_research_quality_verified": False,
        "live_end_to_end_research_verified": False,
        "new_model_calls": 0, "new_supplier_requests": 0,
    }
    assert fingerprint(task) == before, "评估不得更改研究任务"
    return record


def aggregate(records):
    """Do not hide a critical failure inside an average or an empty denominator."""
    if not records or len({record["case_id"] for record in records}) != len(records):
        raise ContractError("评估清单为空或案例重复")
    if any(record.get("evaluation_version") != VERSION for record in records):
        raise ContractError("评估记录版本不一致")
    groups = {}
    for record in records:
        groups.setdefault(record["input_digest"], set()).add(record["split"])
    if any(len(splits) > 1 for splits in groups.values()):
        raise ContractError("同一输入不能同时计为开发集与独立评审集")
    rows = []
    for axis, labels in (("entry", ENTRIES), ("intent", INTENTS)):
        for label in labels:
            group = [record for record in records if record[axis] == label]
            rows.append({"axis": axis, "label": label, "case_count": len(group),
                         "unique_input_count": len({record["input_digest"] for record in group}),
                         "functional_passed": sum(record["functional_status"] == "passed" for record in group),
                         "functional_pass_rate": ratio(sum(record["functional_status"] == "passed" for record in group), len(group)),
                         "holdout_case_count": sum(record["split"] == "holdout" for record in group),
                         "independent_reviewed_count": 0,
                         "research_quality_pass_rate": None})
    failed = [record["case_id"] for record in records if record["critical_violations"]]
    costs = {kind: sum(record["costs"][kind] for record in records)
             if all(record["costs"][kind] is not None for record in records) else None
             for kind in ("data_requests", "model_calls", "seconds")}
    return {"evaluation_version": VERSION, "case_count": len(records),
            "unique_input_count": len(groups), "rate_basis": "recorded_runs_not_independent_problems",
            "case_ids": [record["case_id"] for record in records],
            "functional_status": "failed" if failed or any(record["functional_status"] != "passed" for record in records) else "passed",
            "critical_failure_cases": failed, "coverage": rows, "recorded_costs": costs,
            "quality_status": "independent_review_missing", "research_quality_pass_rate": None,
            "independent_research_quality_verified": False, "full_harness_complete": False}
