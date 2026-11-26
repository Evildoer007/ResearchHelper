"""Interruptible request transport shared by models and data adapters.

Only the parent owns the research ledger, archive and cancellation controls.
The child receives one private request through IPC and emits fixed diagnostics.
"""

import multiprocessing
import queue
import threading
import time

from .contracts import ContractError


class TransportFailure(ContractError):
    def __init__(self, code, message, http_status=None):
        super().__init__(message)
        self.code, self.http_status = code, http_status


def run_isolated(task, spec, started, timeout, record, worker_target, *, active=None,
                 label="数据", poll_seconds=.1, cleanup_seconds=.5, authorize=None, worker_kind="network",
                 allowed_stages=None, on_stage=None, allowed_stage_diagnostics=None):
    if worker_kind not in ("network", "parser"):
        raise ContractError("本地执行类型未登记")
    diagnostics=allowed_stage_diagnostics or {}
    if not isinstance(diagnostics,dict) or any(not isinstance(key,str) or type(limit) is not int
            or limit<0 for key,limit in diagnostics.items()):
        raise ContractError('本地传输诊断契约未登记')
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    worker = context.Process(target=worker_target, args=(child, started + timeout), daemon=True)
    sender = receiver = None
    authorizers, authorization_pending = [], False
    received, send_errors = queue.Queue(), []
    outgoing, decisions = queue.Queue(), queue.Queue()
    outgoing.put(spec)
    record.update(wall_clock_deadline=True, transport_isolated=True, network_request_started=False)
    try:
        try:
            worker.start()
        except (OSError, RuntimeError):
            record["failure_code"] = "worker_start_failed"
            raise TransportFailure("worker_start_failed", "本地" + label + "传输进程无法启动；未自动重试或换源，已登记用量保留") from None
        child.close()
        record[worker_kind + "_worker_pid"] = worker.pid
        def send():
            try:
                while True:
                    message = outgoing.get()
                    if message is None:
                        return
                    parent.send(message)
            except (OSError, EOFError):
                send_errors.append(True)
        sender = threading.Thread(target=send, daemon=True)
        sender.start()
        def receive():
            try:
                while True:
                    value = parent.recv()
                    if not isinstance(value, dict) or value.get("kind") not in ("stage", "authorize", "response", "failure"):
                        received.put({"kind": "failure", "code": "invalid_transport_message"})
                        return
                    received.put(value)
                    if value.get("kind") not in ("stage", "authorize"):
                        return
            except (EOFError, OSError):
                received.put({"kind": "failure", "code": "connection_interrupted"})
        receiver = threading.Thread(target=receive, daemon=True)
        receiver.start()
        while True:
            if active is not None:
                try:
                    active(task)
                except ContractError:
                    record["failure_code"] = "transport_cancelled"
                    raise TransportFailure("transport_cancelled", "研究已停止，终止本地" + label + "传输；已派发用量保留") from None
            remaining = started + timeout - time.monotonic()
            if remaining <= 0:
                record["failure_code"] = "response_deadline_exceeded"
                raise TransportFailure("response_deadline_exceeded", label + "响应超过本次等待时限，终止本地传输；已派发用量保留")
            try:
                decision = decisions.get_nowait()
            except queue.Empty:
                pass
            else:
                authorization_pending = False
                outgoing.put(decision)
            try:
                result = received.get(timeout=min(poll_seconds, remaining))
            except queue.Empty:
                if send_errors:
                    record["failure_code"] = "connection_interrupted"
                    raise TransportFailure("connection_interrupted", label + "传输已退出，未收到完整响应；已派发用量保留")
                continue
            if result["kind"] == "authorize":
                if authorize is None or authorization_pending:
                    raise TransportFailure("invalid_transport_message", "本地请求授权顺序异常，未放行新增请求")
                authorization_pending = True
                def decide(message=result):
                    try:
                        authorize(message)
                        decision = {"kind": "authorized", "index": message.get("index"), "allowed": True}
                    except TransportFailure as error:
                        decision = {"kind": "authorized", "index": message.get("index"), "allowed": False, "code": error.code}
                    except Exception:
                        decision = {"kind": "authorized", "index": message.get("index"), "allowed": False, "code": "wire_authorization_failed"}
                    decisions.put(decision)
                authorizer = threading.Thread(target=decide, daemon=True)
                authorizers.append(authorizer)
                authorizer.start()
                continue
            if result["kind"] == "stage":
                # Model progress carries fixed controller diagnostics only,
                # never provider text, credentials or arbitrary record fields.
                # Source workers retain their separate SDK metadata contract.
                if allowed_stages is not None and (
                    set(result) - ({"kind", "stage", "network_request_started"}
                        | ({'diagnostics'} if diagnostics else set()))
                    or not isinstance(result.get("stage"), str)
                    or result["stage"] not in allowed_stages
                    or "network_request_started" in result and type(result["network_request_started"]) is not bool
                ):
                    record["failure_code"] = "invalid_transport_message"
                    raise TransportFailure("invalid_transport_message", "本地传输阶段记录无效，未采用进度或响应")
                if 'diagnostics' in result:
                    metrics=result['diagnostics']
                    if (not diagnostics or not isinstance(metrics,dict)
                            or set(metrics)-diagnostics.keys()
                            or any(type(value) is not int or not 0<=value<=diagnostics[key]
                                   for key,value in metrics.items())
                            or any(value<record.get(key,0) for key,value in metrics.items())):
                        record['failure_code']='invalid_transport_message'
                        raise TransportFailure('invalid_transport_message','本地传输诊断无效，未采用进度或响应')
                    record.update(metrics)
                record["stage"] = result["stage"]
                if isinstance(result.get("record"), dict):
                    record.update(result["record"])
                if result.get("network_request_started"):
                    record["network_request_started"] = True
                if on_stage is not None:
                    on_stage()
                continue
            if time.monotonic() >= started + timeout:
                record["failure_code"] = "response_deadline_exceeded"
                raise TransportFailure("response_deadline_exceeded", label + "响应超过本次等待时限，不采用迟到结果")
            record.update(result.get("record", {}))
            if result["kind"] == "response":
                return result["body"]
            code = result.get("code", "unexpected_transport_failure")
            record["failure_code"] = code
            status = result.get("http_status")
            if code == "duplicate_response_field":
                raise TransportFailure(code, label + "响应包含重复字段，未采用该响应；已派发用量保留")
            raise TransportFailure(code, label + "连接或响应未完成；已派发用量保留，不自动重试或换源",
                status if type(status) is int else None)
    finally:
        if worker.pid is not None:
            if worker.is_alive():
                worker.terminate()
            worker.join(cleanup_seconds)
            if worker.is_alive():
                worker.kill()
                worker.join(cleanup_seconds)
            record[worker_kind + "_worker_reaped"] = not worker.is_alive()
            if record[worker_kind + "_worker_reaped"]:
                worker.close()
        else:
            record[worker_kind + "_worker_reaped"] = True
            worker.close()
        child.close()
        # Reap networking first. A durable reservation may still wait for an
        # archive transaction; settle it before returning the mutable ledger.
        # No decision is forwarded after the transport has stopped.
        if authorizers:
            for authorizer in authorizers:
                authorizer.join()
            record["transport_authorizer_stopped"] = True
        outgoing.put(None)
        if sender is not None:
            sender.join(cleanup_seconds)
            record["transport_sender_stopped"] = not sender.is_alive()
        else:
            record["transport_sender_stopped"] = True
        if receiver is not None:
            receiver.join(cleanup_seconds)
            record["transport_receiver_stopped"] = not receiver.is_alive()
        else:
            record["transport_receiver_stopped"] = True
        parent.close()
