"""Bound binary-document extraction; retain bytes without inventing excerpts.

Only the parent archives a document. The parser receives bytes through private
IPC, performs no research, reads no settings and never returns exception text.
"""

import contextlib
from collections import OrderedDict
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time
import threading
import zipfile
import io

from .contracts import ContractError, fingerprint, now
from .evidence import extract_material, material_extract_version, material_extract_versions
from .transport import TransportFailure, run_isolated

MAX_BYTES = 25 * 1024 * 1024
MAX_TEXT_BYTES = 16 * 1024 * 1024
MAX_DOCX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_VERIFIED_EXTRACTIONS = 64
# Derived byte-to-text fingerprints only, never task identities, approvals,
# original bytes, credentials or archived research. Every caller must still
# validate its own task/version/snapshot and compare its retained text.
_verified_extractions = OrderedDict()
_verification_lock = threading.Lock()
_verification_context = ContextVar("researchhelper_material_verification", default=None)


def begin_verification(task, active, timeout):
    return _verification_context.set({"task": task, "active": active,
        "deadline": time.monotonic() + max(0, timeout)})


def end_verification(token):
    _verification_context.reset(token)


def check_verification_scope():
    """Keep local original processing inside the existing run, without I/O.

    No new timer or allowance is created. Readers outside a research run have
    no inherited task state; active runs retain cancellation and their original
    absolute deadline through every page and repeated helper call.
    """
    scope = _verification_context.get()
    if scope is not None:
        scope['active'](scope['task'])
        if time.monotonic() >= scope['deadline']:
            raise ContractError('原件复核时限已耗尽，未采用正文')


def _extraction_key(name, payload, version=None):
    return hashlib.sha256(payload).hexdigest(), Path(name).suffix.lower(), version or material_extract_version(name)


def _remember_extraction(name, payload, parsed):
    version = parsed.get('extract_version')
    if parsed.get("status") != "已解析" or version not in material_extract_versions(name):
        return
    key = _extraction_key(name, payload, version)
    if parsed.get("hash") != key[0]:
        return
    value = fingerprint(parsed["parts"])
    with _verification_lock:
        _verified_extractions[key] = value
        _verified_extractions.move_to_end(key)
        while len(_verified_extractions) > MAX_VERIFIED_EXTRACTIONS:
            _verified_extractions.popitem(last=False)


def verified_material(material, payload):
    """Reproduce text with a bounded parser or a private extraction fingerprint.

    Persisted parse diagnostics/receipts never populate this cache. Restarting
    the process reparses once; mutated text still fails even after a cache hit.
    An immutable extraction can be shared, not its research applicability.
    """
    scope = _verification_context.get()
    if scope:
        scope["active"](scope["task"])
        if time.monotonic() >= scope["deadline"]:
            raise ContractError("原件复核时限已耗尽，未采用正文")
    if not isinstance(material, dict) or not isinstance(material.get("name"), str) or len(payload) > MAX_BYTES:
        raise ContractError("原件复核身份或容量无效")
    key = _extraction_key(material["name"], payload, material.get('extract_version'))
    if material.get("hash") != key[0] or material.get("extract_version") != key[2]:
        raise ContractError("原件复核字节或提取版本不一致")
    with _verification_lock:
        expected = _verified_extractions.get(key)
        if expected is not None:
            _verified_extractions.move_to_end(key)
    if expected is None:
        timeout = scope["deadline"] - time.monotonic() if scope else None
        parsed = MaterialParser().parse(material["name"], payload,
            task=scope["task"] if scope else None, active=scope["active"] if scope else None, timeout=timeout,
            extract_version=key[2])
        if parsed.get("status") != "已解析":
            if parsed.get("parse_runtime", {}).get("failure_code") == "response_deadline_exceeded":
                raise ContractError("原件复核未取得完整正文，本地解析已超过本次时限")
            raise ContractError("原件复核未取得完整正文，原件保留，未采用摘录")
        expected = fingerprint(parsed["parts"])
    parts = material.get("parts")
    if not isinstance(parts, list) or fingerprint(parts) != expected:
        raise ContractError("留存正文与原件提取版本不一致，不能采用已修改摘录")
    if scope:
        scope["active"](scope["task"])
        if time.monotonic() >= scope["deadline"]:
            raise ContractError("原件复核时限已耗尽，未采用迟到正文")
    return {"parts": parts, "extract_version": key[2]}


def _parser_worker(connection, deadline):
    def send(message):
        try:
            connection.send(message)
        except (OSError, EOFError):
            pass
    try:
        spec = connection.recv()
        def deny_network(*args, **kwargs):
            raise OSError("Parser networking is disabled")
        socket.socket.connect = deny_network
        socket.getaddrinfo = deny_network
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            if Path(spec["name"]).suffix.lower() == ".docx":
                with zipfile.ZipFile(io.BytesIO(spec["payload"])) as document:
                    if sum(part.file_size for part in document.infolist()) > MAX_DOCX_EXPANDED_BYTES:
                        raise TransportFailure("document_expansion_limit", "DOCX解压内容超过限制")
            parsed = extract_material(spec["name"], spec["payload"], extract_version=spec.get('extract_version'))
            if len(json.dumps(parsed, ensure_ascii=False).encode()) > MAX_TEXT_BYTES:
                raise TransportFailure("document_text_limit", "材料正文超过本次解析容量")
            if time.monotonic() >= deadline:
                raise TransportFailure("response_deadline_exceeded", "解析时间已耗尽")
        send({"kind": "response", "body": parsed})
    except TransportFailure as error:
        send({"kind": "failure", "code": error.code})
    except Exception:
        send({"kind": "failure", "code": "document_parse_failed"})
    finally:
        connection.close()


class MaterialParser:
    MAX_WAIT_SECONDS = 30
    MAX_PDF_WAIT_SECONDS = 90
    worker_target = staticmethod(_parser_worker)

    def parse(self, name, payload, *, task=None, active=None, timeout=None, extract_version=None):
        suffix = Path(name).suffix.lower()
        version = material_extract_version(name) if extract_version is None else extract_version
        if version not in material_extract_versions(name):
            raise ContractError('材料提取版本未登记，未启动解析')
        if len(payload) > MAX_BYTES:
            raise ContractError("材料超过25MB，请拆分材料")
        if suffix not in (".pdf", ".docx", ".html", ".htm"):
            parsed = extract_material(name, payload, extract_version=extract_version)
            _remember_extraction(name, payload, parsed)
            return parsed
        if sys.flags.ignore_environment and not sys.flags.dont_write_bytecode:
            raise ContractError("隔离Python宿主须启用不写字节码后才能解析PDF、DOCX或HTML；本次未启动解析")
        limit = self.MAX_PDF_WAIT_SECONDS if suffix == ".pdf" else self.MAX_WAIT_SECONDS
        diagnostics = {"operation": "material_parse", "timeout_seconds": min(timeout, limit) if timeout is not None else limit}
        started = time.monotonic()
        try:
            if diagnostics["timeout_seconds"] <= 0:
                raise TransportFailure("response_deadline_exceeded", "材料解析时间已耗尽")
            parsed = run_isolated(task, {"name": Path(name).name, "payload": payload,
                'extract_version': version}, started,
                diagnostics["timeout_seconds"], diagnostics, self.worker_target,
                active=active, label="材料解析", worker_kind="parser")
            parts = parsed.get("parts") if isinstance(parsed, dict) else None
            if (not isinstance(parsed, dict) or parsed.get("hash") != hashlib.sha256(payload).hexdigest() or parsed.get("name") != Path(name).name
                    or parsed.get("extract_version") != version
                    or parsed.get("status") not in ("已解析", "解析受限") or parsed.get("source_check") != "待核验"
                    or parsed.get("applicability_check") != "待核验" or not isinstance(parts, list)
                    or any(not isinstance(part, dict) or not isinstance(part.get("locator"), str) or not isinstance(part.get("text"), str) for part in parts)):
                raise TransportFailure("invalid_parser_response", "解析结果与原件不一致")
        except TransportFailure as error:
            messages = {"response_deadline_exceeded": "原件已保留，解析超过本次时限，未采用正文",
                "transport_cancelled": "原件已保留，解析已停止，未采用迟到正文",
                "worker_start_failed": "原件已保留，本地解析未启动",
                "document_expansion_limit": "原件已保留，DOCX解压内容超过100MB，请拆分材料",
                "document_text_limit": "原件已保留，正文超过本次解析容量，请拆分材料"}
            diagnostics["failure_code"] = error.code
            parsed = {"name": Path(name).name, "hash": hashlib.sha256(payload).hexdigest(), "extract_version": version,
                "parts": [], "status": "解析受限", "source_check": "待核验", "applicability_check": "待核验",
                "limitation": messages.get(error.code, "原件已保留，但无法完整解析，未生成正文"), "obtained_at": now()}
        finally:
            diagnostics["elapsed_seconds"] = max(0, time.monotonic() - started)
        parsed["parse_runtime"] = diagnostics
        _remember_extraction(name, payload, parsed)
        return parsed
