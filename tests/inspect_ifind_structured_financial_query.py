"""Inspect one exact-security iFinD structured query, never approve its numbers.

This is a separate, bounded acquisition check. It neither changes model/source
settings nor resumes an existing study, logs in an SDK, or renews any token.
The official HTTP manual documents the operation and its two parameters; its
semantic result still needs independent subject, period, unit and scope review.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.bootstrap import configure_imports
configure_imports()
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge, fingerprint, now
from researchhelper.engine import ResearchAgent
from researchhelper.settings import DataSettings

CASE = "ifind-exact-security-structured-financial-query-20261015-v1"
STORE = Path.home() / "Library/Application Support/ResearchHelper/research-v1"
OPERATION = "smart_stock_picking"
PARAMETERS = {
    "searchstring": "贵州茅台600519.SH2026年半年报营业收入、净利润、经营活动产生的现金流量净额",
    "searchtype": "stock",
}
DOCUMENTATION = "https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html"


def configuration_hashes():
    files = [STORE / name for name in
             ("model-settings.json", "source-settings.json", "execution-policy.json")]
    files.extend((STORE / "credentials").glob("*.secret"))
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files if path.is_file()}


def inspect_response(payload, private_values):
    """Return only safe structural diagnostics, not a source-approval label."""
    encoded = json.dumps(payload, ensure_ascii=False)
    if any(secret and secret in encoded for secret in private_values):
        raise ContractError("供应商返回含授权内容，不留存该响应")
    if not isinstance(payload, dict):
        raise ContractError("结构化查询未返回对象，不视作空结果")
    code = payload.get("errorcode")
    summary = {"business_code": code if type(code) in (int, str) else None,
               "top_level_fields": sorted(payload), "financial_evidence_approved": False}
    if type(code) is bool or code not in (0, "0"):
        summary["outcome"] = "supplier_rejected_query"
        return summary
    tables = payload.get("tables")
    summary.update(outcome="retained_response_for_review",
                   tables_type=type(tables).__name__, tables=[])
    if isinstance(tables, list):
        for table in tables[:10]:
            if not isinstance(table, dict):
                summary["tables"].append({"type": type(table).__name__})
                continue
            data = table.get("table")
            summary["tables"].append({
                "metadata_fields": sorted(key for key in table if key != "table"),
                "columns": sorted(data) if isinstance(data, dict) else [],
                "table_type": type(data).__name__,
            })
    return summary


def run(*, registered=False):
    acceptance = ROOT / "result/acceptance"
    case = CASE + ("-registered-shape" if registered else "")
    parameters = dict(PARAMETERS)
    if registered:
        parameters["searchstring"] = "600519.SH2026年半年报营业收入、净利润、经营活动产生的现金流量净额"
    for target in acceptance.glob("ifind-structured-financial-query-*/receipt.json"):
        if json.loads(target.read_text()).get("case") == case:
            raise RuntimeError("This exact query already ran; no automatic retry")
    folder = Path(tempfile.mkdtemp(prefix="ifind-structured-financial-query-", dir=acceptance))
    before = configuration_hashes()
    agent = ResearchAgent(ResearchArchive(folder / "store"), ROOT, offline=False)
    task = agent.create(
        "检查iFinD对600519.SH2026年半年度三项财务指标的结构化查询原始响应，不批准数字或投资判断。",
        entry="个股", objects=["600519.SH"], research_mode="current",
        limits={"data_requests": 2, "model_calls": 2, "seconds": 180})
    task.update(status="运行中", batch=1)
    agent.archive.save(task)
    record = {"case": case, "status": "failed", "documentation": DOCUMENTATION,
              "registered_request_shape": registered,
              "model_calls": 0, "sdk_login": False, "forced_refresh": False,
              "production_store_written": False, "financial_evidence_approved": False}
    refresh = token = ""
    try:
        if registered:
            from researchhelper.evidence import validate_source_request
            validate_source_request(task, "ifind", OPERATION, parameters)
        refresh = DataSettings(STORE).read(private=True)["ifind"]["refresh_token"]
        token = agent.collector._resolve_ifind_access(task, refresh)
        charge(task["ledger"], "data_requests")
        event = {"kind": "data_requests", "source": "ifind", "operation": OPERATION,
                 "fingerprint": fingerprint({"operation": OPERATION, "parameters": parameters}),
                 "status": "已派发", "batch": task["batch"], "time": now(),
                 "purpose": "独立结构化响应检查，不能作为已核验事实"}
        task["ledger"]["dispatches"].append(event)
        agent.archive.save(task)
        request = urllib.request.Request(
            "https://quantapi.51ifind.com/api/v1/" + OPERATION,
            data=json.dumps(parameters, ensure_ascii=False).encode(), method="POST",
            headers={"Content-Type": "application/json", "ifindlang": "cn"})
        request.add_unredirected_header("access_token", token)
        start = time.monotonic()
        try:
            payload = agent.collector._http_json(task, request, 30, event)
            summary = inspect_response(payload, (refresh, token))
            if registered and summary["outcome"] == "retained_response_for_review":
                from researchhelper.evidence import snapshot_record
                retained = snapshot_record(task, "ifind", OPERATION, parameters, payload, (refresh, token))
                record["registered_response_status"] = retained["status"]
                record["financial_query_contract"] = retained["financial_query_contract"]
            event["status"] = "成功" if summary["outcome"] == "retained_response_for_review" else "业务失败"
            record.update(status=summary["outcome"], response_summary=summary)
            if summary["outcome"] == "retained_response_for_review":
                target = folder / "response.json"
                target.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
                record.update(response=str(target),
                              response_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        finally:
            task["ledger"]["used"]["seconds"] += time.monotonic() - start
    except Exception as error:
        record.update(failure_type=type(error).__name__, http_status=getattr(error, "code", None))
    finally:
        refresh = token = ""
        agent.collector._ifind_access_cache = None
        task["status"] = "已取消"
        agent.archive.save(task)
        agent.close_runtime()
        record.update(task_id=task["id"], ledger=task["ledger"],
                      configuration_unchanged=before == configuration_hashes())
        target = folder / "receipt.json"
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    assert record["configuration_unchanged"]
    assert task["ledger"]["used"]["model_calls"] == 0
    assert task["ledger"]["used"]["data_requests"] <= 2
    print(json.dumps({"status": record["status"], "receipt": str(target),
                      "response_summary": record.get("response_summary"),
                      "failure_type": record.get("failure_type")}, ensure_ascii=False))
    return record


if __name__ == "__main__":
    run()
