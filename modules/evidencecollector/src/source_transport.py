"""Isolated iFinD HTTP, Tinyshare SDK and approved-original transport.

The ordinary EvidenceCollector owns budgets, caching, snapshots and validation.
Workers neither access an archive nor perform financial reasoning or fallback.
"""

import contextlib
import http.client
import importlib.util
import json
import math
import os
import socket
import ssl
import time
import urllib.error
import urllib.request

from .contracts import ContractError, DuplicateJSONField, charge, fingerprint, now, unique_json_object
from .https_transport import open_verified_https
from .evidence import EvidenceCollector, TINYSHARE_DOCS, _download_filing_pdf, filing_record
from .transport import TransportFailure, run_isolated

MAX_JSON_BYTES = 16 * 1024 * 1024


@contextlib.contextmanager
def _sdk_wire_scope(connection, deadline, record):
    """Meter the SDK's query, CDN and explicit retry sends, never its secrets.

    Each Session.send waits for a parent reservation. Disable the adapter's
    opaque retries so every additional send must cross this boundary again.
    """
    import requests
    from urllib3.util.retry import Retry
    original = requests.sessions.Session.send
    rows = []
    record.update(sdk_wire_metering=True, sdk_wire_requests_started=0, sdk_wire_results=rows)

    def blocked(code, message):
        # Some SDK branches catch transport exceptions and return a cached or
        # empty frame. The parent must still see a denied send as failure.
        record["sdk_blocked_request_code"] = code
        raise TransportFailure(code, message)

    def send(session, request, **options):
        if code := record.get("sdk_blocked_request_code"):
            raise TransportFailure(code, "SDK请求已经终止")
        index = len(rows) + 1
        role = "query" if request.method == "POST" else "download" if request.method == "GET" else None
        if role is None or index == 1 and role != "query":
            blocked("unregistered_sdk_request", "SDK请求类型未登记")
        if any(item["state"] == "连接或读取失败" for item in rows):
            blocked("sdk_unknown_retry_blocked", "SDK请求结果未知，禁止自动重发")
        if any(item.get("http_status") in (401, 403) for item in rows):
            blocked("sdk_auth_retry_blocked", "SDK认证或权限失败，禁止自动重发")
        if time.monotonic() >= deadline:
            blocked("response_deadline_exceeded", "SDK请求时间已耗尽")
        connection.send({"kind": "authorize", "index": index, "role": role})
        reply = connection.recv()
        if reply.get("kind") != "authorized" or reply.get("index") != index:
            blocked("invalid_transport_message", "SDK派发授权不匹配")
        if reply.get("allowed") is not True:
            blocked(reply.get("code", "wire_authorization_failed"), "SDK请求未获预算授权")
        if time.monotonic() >= deadline:
            blocked("response_deadline_exceeded", "SDK请求时间已耗尽，未发送")
        item = {"index": index, "role": role, "state": "已发送"}
        rows.append(item)
        record["sdk_wire_requests_started"] = len(rows)
        connection.send({"kind": "stage", "stage": "查询数据" if role == "query" else "下载数据",
            "network_request_started": True, "record": record})
        adapter = session.get_adapter(request.url)
        adapter.max_retries = Retry(total=0, connect=0, read=0, redirect=0, status=0)
        started = time.monotonic()
        try:
            response = original(session, request, **options)
            item["state"] = "响应已返回"
            item["http_status"] = response.status_code
            # Guard decoding on this response, including CDN JSON. The SDK
            # decides whether to read JSON, so other downloads stay untouched.
            decode_json = getattr(response, "json", None)
            if callable(decode_json):
                def strict_json(**kwargs):
                    try:
                        kwargs["object_pairs_hook"] = unique_json_object
                        return decode_json(**kwargs)
                    except DuplicateJSONField:
                        item["state"] = "返回结构异常"
                        blocked("duplicate_response_field", "SDK响应JSON包含重复字段")
                response.json = strict_json
            return response
        except Exception:
            item["state"] = "连接或读取失败"
            raise
        finally:
            item["elapsed_seconds"] = max(0, time.monotonic() - started)
            connection.send({"kind": "stage", "stage": "查询数据" if role == "query" else "下载数据", "record": record})

    requests.sessions.Session.send = send
    try:
        yield
    finally:
        requests.sessions.Session.send = original


def _source_worker(connection, deadline):
    record = {}
    operation_kind = None
    def send(value):
        try:
            connection.send(value)
        except (EOFError, OSError):
            pass
    try:
        spec = connection.recv()
        operation_kind = spec["kind"]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TransportFailure("response_deadline_exceeded", "请求时限已耗尽")
        if operation_kind == "http_json":
            method = spec.get('method', 'POST')
            if method not in ('POST', 'GET') or method == 'GET' and spec['body'] is not None:
                raise TransportFailure('unsupported_operation', 'JSON传输方法与请求体不符')
            request = urllib.request.Request(spec["endpoint"], data=spec["body"], headers=spec["headers"], method=method)
            for name, value in spec["unredirected_headers"].items():
                request.add_unredirected_header(name, value)
            send({"kind": "stage", "stage": "连接数据源", "network_request_started": True})
            options = dict(timeout=remaining, opener=urllib.request.urlopen)
            if method == 'GET':
                from .financial_sources import IFIND_METADATA_BASE, IFIND_METADATA_OPERATIONS
                from urllib.parse import urlsplit
                parsed = urlsplit(spec['endpoint'])
                if (parsed.scheme != 'https' or parsed.netloc != 'quantapi.51ifind.com'
                        or parsed.fragment or parsed.path.rsplit('/', 1)[-1] not in IFIND_METADATA_OPERATIONS
                        or parsed.path != urlsplit(IFIND_METADATA_BASE).path+parsed.path.rsplit('/', 1)[-1]):
                    raise TransportFailure('unsupported_operation', 'GET元数据地址未登记')
                options['follow_redirects'] = False
            with open_verified_https(request, **options) as response:
                send({"kind": "stage", "stage": "读取数据"})
                # Shared framing checks; no second model and no model.reason.
                from .model import ModelService
                reader = ModelService(None, offline=True)
                reader.MAX_RESPONSE_BYTES = MAX_JSON_BYTES
                started = time.monotonic()
                raw = reader._read_response(response, started, deadline - started, record)
                if method == 'GET' and parsed.path.rsplit('/', 1)[-1] == 'search_edb':
                    if len(raw) > 1000000:
                        raise TransportFailure('source_response_too_large', 'EDB搜索响应超过限额，不截断目录')
                    payload = {'text': raw.decode('utf-8') if isinstance(raw, bytes) else raw}
                else:
                    payload = json.loads(raw, object_pairs_hook=unique_json_object)
        elif operation_kind == 'official_html':
            from .official_sources import download_html
            send({'kind':'stage','stage':'获取官方资料','network_request_started':True})
            payload=download_html(spec['url'],remaining)
        elif operation_kind == 'official_announcement':
            from .announcement_sources import download
            send({'kind': 'stage', 'stage': '获取官方公告', 'network_request_started': True})
            payload = download(spec['parameters'], spec['phase'], remaining,
                issuer_raw=spec.get('issuer_raw'), selected=spec.get('selected'))
        elif operation_kind == 'official_etf':
            from .etf_sources import download
            send({'kind': 'stage', 'stage': '获取ETF清单', 'network_request_started': True})
            payload = download(spec['code'], remaining)
        elif operation_kind == 'official_etf_listing':
            from .etf_listing import download_listing
            send({'kind': 'stage', 'stage': '获取ETF证券名录', 'network_request_started': True})
            payload = download_listing(spec['market'], remaining)
        elif operation_kind == 'official_etf_trading':
            from .etf_trading import download
            send({'kind': 'stage', 'stage': '获取ETF交易资料', 'network_request_started': True})
            payload = download(spec['operation'], spec['code'], remaining)
        elif operation_kind == 'official_hsi_history':
            from .event_market import download_snapshot
            send({'kind': 'stage', 'stage': '获取港股指数历史', 'network_request_started': True})
            payload = download_snapshot(spec['codes'], remaining)
        elif operation_kind == 'official_mainland_index_history':
            from .event_market import download_mainland_snapshot
            send({'kind': 'stage', 'stage': '获取境内指数历史', 'network_request_started': True})
            payload = download_mainland_snapshot(spec['parameters'], remaining)
        elif operation_kind == 'official_h15_treasury_yields':
            from .treasury_rates import download_snapshot
            send({'kind': 'stage', 'stage': '获取美债固定期限收益率', 'network_request_started': True})
            payload = download_snapshot(spec['parameters'], remaining)
        elif operation_kind == 'official_lpr_history':
            from .lpr_rates import download_snapshot
            send({'kind': 'stage', 'stage': '获取LPR报价历史', 'network_request_started': True})
            payload = download_snapshot(spec['parameters'], remaining)
        elif operation_kind == 'official_nfra_bank_statistics':
            from .nfra_bank_statistics import download_snapshot
            send({'kind': 'stage', 'stage': '获取商业银行监管统计', 'network_request_started': True})
            payload = download_snapshot(spec['url'], remaining)
        elif operation_kind=='official_index':
            from .index_sources import download_csi_blob,csi_catalogue_contents,parse_csi_workbook
            import base64
            send({'kind':'stage','stage':'获取官方指数文件','network_request_started':True})
            payload=download_csi_blob(spec['url'],spec['index_code'],spec['role'],remaining)
            raw=base64.b64decode(payload['original_base64'],validate=True)
            if spec['role']=='catalog':
                contents=csi_catalogue_contents(raw,spec['index_code'])
                payload['entries']=contents['entries']
                if contents['issues']:payload['catalogue_issues']=contents['issues']
            else:payload['parsed']=parse_csi_workbook(raw,spec['index_code'],spec['role'],version=spec.get('parse_version',1))
        elif operation_kind == "tinyshare":
            from .financial_sources import FINANCIAL_REQUEST_REGISTRY
            if spec["operation"] not in TINYSHARE_DOCS and 'tinyshare.'+str(spec["operation"]) not in FINANCIAL_REQUEST_REGISTRY:
                raise TransportFailure("unsupported_operation", "接口未登记")
            # Suppress SDK diagnostics in this child only. A vendor exception or
            # import message can contain its key; never forward that text.
            with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                # Research snapshots and recovery belong to our own archive.
                # Never accept the SDK's shared on-disk historical cache as a
                # fresh provider response or create another persistent cache.
                os.environ["TINYSHARE_CACHE_MAX_ENTRIES"] = "0"
                record["sdk_history_cache_disabled"] = True
                send({"kind": "stage", "stage": "准备数据源", "record": record})
                import tinyshare as ts
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportFailure("response_deadline_exceeded", "请求时限已耗尽")
                try:
                    with _sdk_wire_scope(connection, deadline, record):
                        client = ts.pro_api(spec["token"], timeout=remaining)
                        result = getattr(client, spec["operation"])(**spec["parameters"])
                finally:
                    if blocked_code := record.get("sdk_blocked_request_code"):
                        raise TransportFailure(blocked_code, "SDK请求未完成，拒绝降级返回") from None
                sends = record["sdk_wire_results"]
                if sends and sends[-1]["state"] == "连接或读取失败":
                    raise TransportFailure("connection_interrupted", "SDK未取得完整响应")
                if sends and sends[-1].get("http_status", 200) >= 400:
                    status = sends[-1]["http_status"]
                    code = {401: "authentication_failed", 403: "permission_denied", 429: "rate_limited"}.get(status, "http_failure")
                    raise TransportFailure(code, "SDK返回失败响应，拒绝空表降级")
                if result is None or not hasattr(result, "to_dict"):
                    raise TransportFailure("invalid_response_shape", "返回结构异常")
                payload = result.to_dict(orient="records")
                if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
                    raise TransportFailure("invalid_response_shape", "返回结构异常")
                for row in payload:
                    for key, value in row.items():
                        if isinstance(value, float) and not math.isfinite(value):
                            row[key] = None
                if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) > MAX_JSON_BYTES:
                    raise TransportFailure("response_too_large", "返回数据超过大小限制")
        elif operation_kind == "filing_pdf":
            send({"kind": "stage", "stage": "获取公告原件", "network_request_started": True})
            payload = _download_filing_pdf(spec["url"], remaining, vendor_source_id=spec["source_id"]) if spec["source_id"] else _download_filing_pdf(spec["url"], remaining)
        else:
            raise TransportFailure("unsupported_operation", "传输操作未登记")
        send({"kind": "response", "body": payload, "record": record})
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        send({"kind": "failure", "code": {401: "authentication_failed", 403: "permission_denied", 429: "rate_limited"}.get(status, "http_failure"),
            "http_status": status, "record": record})
    except TransportFailure as error:
        send({"kind": "failure", "code": error.code, "record": record})
    except DuplicateJSONField:
        send({"kind": "failure", "code": "duplicate_response_field", "record": record})
    except ContractError:
        send({"kind": "failure", "code": record.get("failure_code", "original_download_failed"), "record": record})
    except (TimeoutError, socket.timeout):
        send({"kind": "failure", "code": "transport_timeout", "record": record})
    except urllib.error.URLError as error:
        code = "transport_timeout" if isinstance(error.reason, (TimeoutError, socket.timeout)) else "tls_failure" if isinstance(error.reason, ssl.SSLError) else "connection_failed"
        send({"kind": "failure", "code": code, "record": record})
    except http.client.IncompleteRead:
        send({"kind": "failure", "code": "response_incomplete", "record": record})
    except (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError, ssl.SSLEOFError):
        send({"kind": "failure", "code": "connection_interrupted", "record": record})
    except http.client.HTTPException:
        send({"kind": "failure", "code": "http_protocol_failure", "record": record})
    except UnicodeDecodeError:
        send({"kind":"failure","code":"invalid_response_encoding" if operation_kind=='official_html'
              else "invalid_response_json","record":record})
    except json.JSONDecodeError:
        send({"kind": "failure", "code": "invalid_response_json", "record": record})
    except Exception as error:
        code = "provider_failure"
        if operation_kind == "tinyshare":
            message = str(error).lower()
            if "过期" in message or "expired" in message:
                code = "credential_expired"
            elif type(error).__name__ == "TinySharePermissionError":
                code = "permission_denied"
        send({"kind": "failure", "code": code, "record": record})
    finally:
        connection.close()


def official_html(task,url,started,timeout,event,*,active=None,on_stage=None):
    return run_isolated(task,dict(kind='official_html',url=url),started,timeout,event,_source_worker,
        active=active,label='官方资料',allowed_stages={'获取官方资料'},on_stage=on_stage)


def official_announcement(task, parameters, phase, started, timeout, event, *,
                          issuer_raw=None, selected=None, active=None, on_stage=None):
    return run_isolated(task, dict(kind='official_announcement', parameters=parameters, phase=phase,
        issuer_raw=issuer_raw, selected=selected), started, timeout, event, _source_worker,
        active=active, label='官方公告', allowed_stages={'获取官方公告'}, on_stage=on_stage)


def official_etf(task, code, started, timeout, event, *, active=None, on_stage=None):
    return run_isolated(task, dict(kind='official_etf', code=code), started, timeout, event, _source_worker,
        active=active, label='ETF清单', allowed_stages={'获取ETF清单'}, on_stage=on_stage)


def official_etf_listing(task, market, started, timeout, event, *, active=None, on_stage=None):
    return run_isolated(task, dict(kind='official_etf_listing', market=market), started, timeout, event,
        _source_worker, active=active, label='ETF证券名录', allowed_stages={'获取ETF证券名录'}, on_stage=on_stage)


def official_etf_trading(task, operation, code, started, timeout, event, *, active=None, on_stage=None):
    return run_isolated(task, dict(kind='official_etf_trading', operation=operation, code=code),
        started, timeout, event, _source_worker, active=active, label='ETF交易资料',
        allowed_stages={'获取ETF交易资料'}, on_stage=on_stage)


def official_hsi_history(task, codes, started, timeout, event, *, active=None, on_stage=None):
    return run_isolated(task, dict(kind='official_hsi_history', codes=codes), started, timeout, event,
        _source_worker, active=active, label='港股指数历史', allowed_stages={'获取港股指数历史'}, on_stage=on_stage)


def official_mainland_index_history(task, parameters, started, timeout, event, *, active=None, on_stage=None):
    return run_isolated(task, dict(kind='official_mainland_index_history', parameters=parameters), started, timeout,
        event, _source_worker, active=active, label='境内指数历史', allowed_stages={'获取境内指数历史'}, on_stage=on_stage)


def official_h15_treasury_yields(task, parameters, started, timeout, event, *, active=None, on_stage=None):
    return run_isolated(task, dict(kind='official_h15_treasury_yields', parameters=parameters), started, timeout,
        event, _source_worker, active=active, label='美债固定期限收益率', allowed_stages={'获取美债固定期限收益率'},
        on_stage=on_stage)


def official_lpr_history(task, parameters, started, timeout, event, *, active=None, on_stage=None):
    return run_isolated(task, dict(kind='official_lpr_history', parameters=parameters), started, timeout,
        event, _source_worker, active=active, label='LPR报价历史', allowed_stages={'获取LPR报价历史'},
        on_stage=on_stage)


def official_nfra_bank_statistics(task, url, started, timeout, event, *, active=None, on_stage=None):
    return run_isolated(task, dict(kind='official_nfra_bank_statistics', url=url), started, timeout,
        event, _source_worker, active=active, label='商业银行监管统计', allowed_stages={'获取商业银行监管统计'},
        on_stage=on_stage)


def official_index(task,selection,started,timeout,event,*,active=None,on_stage=None):
    return run_isolated(task,dict(kind='official_index',
        parse_version=2 if task.get('index_weight_evidence_version')==1 else 1,
        **{key:selection[key] for key in ('url','index_code','role')}),
        started,timeout,event,_source_worker,active=active,label='官方指数文件',
        allowed_stages={'获取官方指数文件'},on_stage=on_stage)


class IsolatedEvidenceCollector(EvidenceCollector):
    MAX_WAIT_SECONDS = 30
    worker_target = staticmethod(_source_worker)

    def __init__(self, offline=True, journal=None, active=None):
        super().__init__(offline=offline, journal=journal)
        self.active = active

    def _request(self, task, spec, timeout, event):
        timeout = min(timeout, self.MAX_WAIT_SECONDS)
        event["timeout_seconds"] = timeout
        diagnostics = {}
        wires = []
        def authorize(message):
            index, role = message.get("index"), message.get("role")
            if type(index) is not int or index != len(wires) + 1 or role not in ("query", "download") or index == 1 and role != "query":
                raise TransportFailure("invalid_transport_message", "SDK请求顺序异常")
            if self.active is not None:
                self.active(task)
            if index == 1:
                wire = event  # the SDK invocation already reserved this send
            else:
                digest = event["fingerprint"] if role == "query" else fingerprint({"sdk_parent": event["fingerprint"], "role": role})
                prior = sum(item.get("fingerprint") == digest for item in task["ledger"]["dispatches"])
                if prior >= 3:
                    raise TransportFailure("sdk_retry_limit", "同一SDK请求已达到三次派发上限")
                try:
                    charge(task["ledger"], "data_requests")
                except ContractError:
                    raise TransportFailure("source_budget_exceeded", "SDK追加请求预算不足") from None
                wire = {"kind": "data_requests", "fingerprint": digest, "sdk_parent": event["fingerprint"],
                    "attempt": prior + 1, "batch": task["batch"], "status": "已派发", "time": now()}
                task["ledger"]["dispatches"].append(wire)
            wire.update(sdk_request_index=index, sdk_request_role=role, sdk_request_state="已登记")
            wires.append(wire)
            if index > 1 and self.journal:
                self.journal(task)
            if self.active is not None:
                self.active(task)
        try:
            return run_isolated(task, spec, time.monotonic(), timeout, diagnostics, self.worker_target, active=self.active,
                authorize=authorize if spec["kind"] == "tinyshare" else None)
        except TransportFailure as error:
            event.update(failure_code=error.code, status={"transport_cancelled": "已取消",
                "response_deadline_exceeded": "数据调用超过时限", "transport_timeout": "数据调用超时",
                "credential_expired": "授权已过期", "permission_denied": "权限未通过",
                "authentication_failed": "认证未通过", "invalid_response_json": "返回结构异常",
                "duplicate_response_field": "返回结构异常",
                "invalid_response_shape": "返回结构异常", "response_too_large": "响应超出大小限制",
                "response_incomplete": "响应不完整", "tls_failure": "安全连接未通过",
                "worker_start_failed": "本地传输未启动", "invalid_transport_message": "本地传输响应异常",
                "source_budget_exceeded": "数据请求预算不足", "sdk_retry_limit": "SDK请求次数达到上限",
                "wire_authorization_failed": "数据请求登记未完成",
                "provider_failure": "调用失败待核查"}.get(error.code, "传输失败"))
            raise
        finally:
            event.update(diagnostics)
            returned = {item["index"]: item for item in diagnostics.get("sdk_wire_results", [])}
            for wire in wires:
                details = returned.get(wire["sdk_request_index"], {})
                state = details.get("state", "已登记但未发送")
                wire["sdk_request_state"] = state
                if "elapsed_seconds" in details:
                    wire["sdk_request_elapsed_seconds"] = details["elapsed_seconds"]
                if wire is not event:
                    wire["status"] = state

    def _http_json(self, task, request, timeout, event):
        try:
            spec = {"kind": "http_json", "endpoint": request.full_url, "body": request.data,
                "headers": dict(request.headers), "unredirected_headers": dict(request.unredirected_hdrs)}
            if request.get_method() != 'POST':
                spec['method'] = request.get_method()
            return self._request(task, spec, timeout, event)
        except TransportFailure as error:
            if error.http_status is not None:
                raise urllib.error.HTTPError(request.full_url, error.http_status, "", {}, None) from None
            raise

    def _download_original(self, task, url, timeout, event, source_id=None):
        return self._request(task, {"kind": "filing_pdf", "url": url, "source_id": source_id}, timeout, event)

    def _original_record(self, task, directory, payload, timeout, event):
        from .material_parser import MaterialParser
        parsed = MaterialParser().parse("original.pdf", payload, task=task, active=self.active, timeout=timeout)
        event["material_parse"] = parsed["parse_runtime"]
        return filing_record(task, directory, "original.pdf", payload, task.get("source_records", []), _parsed=parsed)

    def _parse_official_material(self, task, name, payload, timeout, event, *, extract_version=None):
        if not name.endswith('.pdf'):
            return super()._parse_official_material(task, name, payload, timeout, event, extract_version=extract_version)
        from .material_parser import MaterialParser
        parsed = MaterialParser().parse(name, payload, task=task, active=self.active, timeout=timeout)
        event['material_parse'] = parsed['parse_runtime']
        return parsed

    def _checkpoint_original(self, task, record):
        # Store the unapproved original, including limited/cancelled extraction,
        # before cancellation or a downstream journal can discard the return.
        if self.journal:
            records = task.setdefault("source_records", [])
            if not any(item["id"] == record["id"] for item in records):
                records.append(record)
            self.journal(task)

    def _require_tinyshare(self):
        if importlib.util.find_spec("tinyshare") is None:
            raise ContractError("Tinyshare运行依赖尚未安装，本次未派发请求")
        return None

    def _tinyshare_rows(self, task, operation, params, token, timeout, event, provider):
        try:
            return self._request(task, {"kind": "tinyshare", "operation": operation, "parameters": params, "token": token}, timeout, event)
        except TransportFailure as error:
            if error.code == "credential_expired":
                raise ContractError("Tinyshare授权码已过期，请更新ResearchHelper独立配置；已派发用量保留，不自动换源") from None
            if error.code == "permission_denied":
                raise ContractError("Tinyshare授权或接口权限未通过，请核对独立配置；已派发用量保留，不自动换源") from None
            messages = {"source_budget_exceeded": "Tinyshare追加请求预算不足，新增请求未发送",
                "sdk_retry_limit": "Tinyshare同一请求达到三次派发上限，新增请求未发送",
                "wire_authorization_failed": "Tinyshare请求登记未完成，新增请求未发送",
                "sdk_unknown_retry_blocked": "Tinyshare上次请求结果未知，禁止自动重发",
                "sdk_auth_retry_blocked": "Tinyshare认证或权限未通过，禁止自动重发"}
            if error.code in messages:
                raise ContractError(messages[error.code] + "；原账本保留") from None
            raise
