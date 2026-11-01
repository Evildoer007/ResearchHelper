"""One authorized connection check, using the shared bounded App transport.

The credential enters through a non-echoing terminal, never arguments, source,
receipts or test stores. A successful check may save the independently supplied
RH configuration, but never enables production execution or starts research.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from bootstrap import configure_imports
configure_imports()
from researchhelper.contracts import ContractError, budget
from researchhelper.model import IsolatedModelService, ModelSettings
from researchhelper.runtime import ExecutionPolicy

PRODUCTION = Path("/Users/haoranxu/Library/Application Support/ResearchHelper")
CANDIDATE = ROOT / "result/build-candidates/20261013-042719-465008/candidate.json"
ENDPOINT = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"


class MemoryProbeSettings:
    def __init__(self, credential):
        self.value = ModelSettings.validate({"endpoint": ENDPOINT, "model": MODEL,
            "api_key": credential, "extraction_mode": "direct"})

    def read(self, private=False):
        if private:
            return dict(self.value)
        return {"endpoint": ENDPOINT, "model": MODEL, "has_key": True,
                "extraction_mode": "direct", "offline": False}


class ConnectionProbe(IsolatedModelService):
    # Probe-only output cap, not a change to research production limits.
    MAX_WAIT_SECONDS = 25

    @staticmethod
    def output_limit(config):
        return 128


def file_identity(path):
    if path.is_symlink():
        raise RuntimeError("私有配置路径不能是软链接")
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def run(credential):
    sys.path.insert(0, str(ROOT / "packaging"))
    from build_current import verify_current_build
    verify_current_build(CANDIDATE, ROOT)
    settings = ModelSettings(PRODUCTION)
    original = settings.read()
    if any(original.get(k) for k in ("endpoint", "model", "has_key")):
        raise RuntimeError("RH已有模型配置，不覆盖正在使用的服务")
    identity = file_identity(settings.path)
    policy = ExecutionPolicy(PRODUCTION).read()
    policy_identity = file_identity(PRODUCTION / "execution-policy.json")
    folder = Path(tempfile.mkdtemp(prefix="authorized-deepseek-connection-",
                                 dir=ROOT / "result/acceptance"))
    task = {"id": "rh_connection_check", "version": 1, "batch": 1,
            "ledger": budget({"data_requests": 1, "model_calls": 1, "seconds": 25})}
    receipt = {"status": "executing", "scope": "authorized_model_connection_only",
        "candidate": str(CANDIDATE), "provider": "DeepSeek", "model": MODEL,
        "production_config_saved": False, "production_research_started": False,
        "production_execution_changed": False, "other_product_credentials_read": False,
        "real_model_requests": 0, "data_requests": 0, "automatic_retry": False,
        "max_output_tokens": 128, "timeout_seconds": 25,
        "research_quality_verified": False, "full_app_research_verified": False}
    target = folder / "receipt.json"

    def persist(_task=None):
        receipt["real_model_requests"] = task["ledger"]["used"]["model_calls"]
        receipt["ledger"] = task["ledger"]
        target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))

    persist()
    memory = None
    try:
        memory = MemoryProbeSettings(credential)
        service = ConnectionProbe(memory, offline=False, journal=persist)
        reply = service.reason(task, [
            {"role": "system", "content": "Connection check only. Output exactly the JSON object {\"ok\":true}."},
            {"role": "user", "content": "Return the JSON connection check. Do not use tools."},
        ], delivery=True, phase="extract")
        if json.loads(reply) != {"ok": True}:
            raise ContractError("连接响应未满足固定检查契约")
        if file_identity(settings.path) != identity or settings.read() != original:
            raise ContractError("连接检查期间本机配置已改变，不覆盖")
        public = settings.save({"endpoint": ENDPOINT, "model": MODEL, "api_key": credential})
        if settings.path.stat().st_mode & 0o777 != 0o600:
            raise ContractError("私有模型配置权限核验失败")
        receipt.update(status="scoped_passed", production_config_saved=True,
                       public_configuration=public, private_configuration_mode="0600")
    except ContractError:
        receipt["status"] = "failed"
        dispatched = task["ledger"]["dispatches"]
        receipt["failure_code"] = dispatched[-1].get("failure_code", "probe_contract_failed") if dispatched else "probe_contract_failed"
    except Exception:
        # Never serialize arbitrary exceptions or provider bodies.
        receipt.update(status="failed", failure_code="probe_local_failure")
    finally:
        if memory is not None:
            memory.value["api_key"] = ""
        credential = ""
        receipt["production_execution_unchanged"] = (
            ExecutionPolicy(PRODUCTION).read() == policy and
            file_identity(PRODUCTION / "execution-policy.json") == policy_identity)
        if not receipt["production_execution_unchanged"]:
            receipt.update(status="failed", failure_code="execution_policy_changed")
        persist()
    print(json.dumps({k: receipt.get(k) for k in (
        "status", "failure_code", "model", "real_model_requests", "production_config_saved",
        "production_execution_unchanged")}, ensure_ascii=False))
    print("receipt: " + str(target))
    return receipt


if __name__ == "__main__":
    if not sys.stdin.isatty():
        raise SystemExit("需要不回显的私有输入，不接受命令参数或日志输入")
    supplied = getpass.getpass("Private credential input (echo disabled): ")
    try:
        run(supplied)
    finally:
        supplied = ""
