"""Loopback-only App service. No legacy Qt/pipeline or shared SDK startup."""

from __future__ import annotations

import base64
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .archive import ResearchArchive
from .auth import AuthenticationRequired, LocalAccount, PermissionDenied
from .cases import catalog
from .contracts import ContractError, ENTRIES, INTENTS, context
from .engine import ResearchAgent
from .evidence import active_source_capabilities, FIELD_REGISTRY, FILING_REQUEST_REGISTRY, identity_resolutions
from .financial_sources import FINANCIAL_REQUEST_REGISTRY
from .model import ModelService, IsolatedModelService, ModelSettings
from .runtime import ExecutionPolicy, StoreLease
from .settings import DataSettings
from .layout import default_resource_root, resource_directory


class LocalTokenInvalid(ContractError):
    """A stale loopback guard, not an invalid account or password."""


def workspace_task_view(task):
    """Opt-in presentation only; canonical storage and report content stay whole."""
    view = {key: value for key, value in task.items() if key != 'pending_content'}
    pending = task.get('pending_content')
    view.update(workspace_view_version=1,
                has_pending_content=isinstance(pending, (dict, list)) or bool(pending))
    originals, duplicates = {}, set()
    for record in task.get('materials', []) + task.get('source_records', []):
        ident = record.get('id')
        if not isinstance(ident, str):
            continue
        if ident in originals:
            duplicates.add(ident)
        originals[ident] = record
    evidence = []
    for item in task.get('evidence', []):
        projected = dict(item)
        ident, artifact = item.get('artifact_id'), item.get('artifact')
        record = originals.get(ident) if isinstance(ident, str) and ident not in duplicates else None
        if record is not None and isinstance(artifact, dict) and artifact.get('id') == ident:
            snapshot = record.get('snapshot')
            if snapshot is not None:
                if item.get('snapshot') == snapshot:
                    projected.pop('snapshot', None)
                if artifact.get('snapshot') == snapshot:
                    projected['artifact'] = {key: value for key, value in artifact.items() if key != 'snapshot'}
        evidence.append(projected)
    view['evidence'] = evidence
    return view


def _validate_launch_policy(offline, authentication, runtime_configurable, preset_accounts):
    # The launcher chooses whether a real admin may manage execution consent.
    # Offline demo/test launchers never gain that authority from a request body.
    if not isinstance(offline, bool):
        raise ContractError("执行权限须由本地启动策略明确指定")
    if not isinstance(authentication, bool) or not isinstance(runtime_configurable, bool):
        raise ContractError("登录和运行策略管理权限须由本地启动器指定")
    if runtime_configurable and not authentication:
        raise ContractError("可管理运行策略的App须启用独立账户认证")
    # The standard App provisions preset identities. Embedding hosts may retain
    # explicit account provisioning; the browser never initializes credentials.
    if not isinstance(preset_accounts, bool) or preset_accounts and not authentication:
        raise ContractError("预置账户须由启用认证的本地启动器明确指定")


class ResearchHTTPServer(ThreadingHTTPServer):
    """Release ownership only after requests and research workers have settled."""
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.runtime_condition = threading.Condition()
        self.runtime_closing = False
        self.runtime_requests = 0
        self.runtime_lease = None
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        with self.runtime_condition:
            if self.runtime_closing:
                self.shutdown_request(request)
                return
            self.runtime_requests += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_settled()
            raise

    def _request_settled(self):
        with self.runtime_condition:
            self.runtime_requests -= 1
            self.runtime_condition.notify_all()

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_settled()

    def server_close(self):
        with self.runtime_condition:
            if self.runtime_closing:
                return
            self.runtime_closing = True
        super().server_close()
        agent = getattr(self, 'agent', None)
        workers = agent.close_runtime() if agent is not None else ()

        def release_after_settlement():
            for worker in workers:
                if worker is not threading.current_thread():
                    worker.join()
            with self.runtime_condition:
                self.runtime_condition.wait_for(lambda: self.runtime_requests == 0)
                if self.runtime_lease is not None:
                    self.runtime_lease.close()

        if any(worker.is_alive() for worker in workers) or self.runtime_requests:
            threading.Thread(target=release_after_settlement,
                name='ResearchHelper-service-settlement', daemon=True).start()
        else:
            release_after_settlement()


def make_server(store, resources=None, port=0, *, offline=True, source_credentials=None, authentication=False, runtime_configurable=False, preset_accounts=False):
    _validate_launch_policy(offline, authentication, runtime_configurable, preset_accounts)
    lease = StoreLease(store)
    try:
        server = _make_server(store, resources, port, offline=offline,
            source_credentials=source_credentials, authentication=authentication,
            runtime_configurable=runtime_configurable, preset_accounts=preset_accounts)
        server.runtime_lease = lease
        return server
    except BaseException:
        lease.close()
        raise


def _make_server(store, resources, port, *, offline, source_credentials, authentication, runtime_configurable, preset_accounts):
    resources = Path(resources or default_resource_root()).resolve()
    execution = ExecutionPolicy(store, configurable=runtime_configurable)
    policy = execution.read()
    if runtime_configurable:
        offline = not policy["enabled"]
    source_settings = DataSettings(store)
    model_settings = ModelSettings(store, offline=offline)
    if runtime_configurable:
        # Only an updated native host migrates its own local legacy settings.
        # Locked demos and Skill hosts remain read-only on ordinary reads.
        model_settings.migrate_legacy()
        source_settings.migrate_legacy()
    source_configuration_unavailable = False
    if source_credentials is None:
        source_settings.read()  # corrupt configuration still fails closed
        try:
            source_credentials = source_settings.read(private=True)
        except ContractError:
            # A missing/unreadable referenced secret must not prevent the RH
            # settings surface from opening. Do not persist blanks, change a
            # provider, enable execution or borrow another product's secrets.
            source_credentials = {source: {name: "" for name in names}
                                  for source, names in source_settings.fields.items()}
            source_configuration_unavailable = True
    agent = ResearchAgent(ResearchArchive(store), resources, offline=offline, source_credentials=source_credentials,
        isolate_sources=runtime_configurable, isolate_materials=runtime_configurable, require_argument_support=True, draft_first=True)
    agent.recover_interrupted()
    agent.source_configuration_unavailable = source_configuration_unavailable
    token = secrets.token_urlsafe(32)
    if runtime_configurable:
        agent.model = IsolatedModelService(model_settings, offline=offline, journal=agent._journal,
            active=agent._check_run_control)
    else:
        agent.model = ModelService(model_settings, offline=offline, journal=agent._journal)
    account = LocalAccount(store) if authentication else None
    if preset_accounts:
        account.ensure_default_accounts()

    def execution_state():
        return {"mode": "offline" if agent.offline else "live", "configurable": runtime_configurable,
                "policy_version": execution.read()["policy_version"]}

    def research_readiness():
        """Configuration completeness only; no connectivity or permission claim."""
        config = model_settings.read()
        return {"model_configured": all(config.get(field) for field in ("endpoint", "model", "has_key")),
                "data_credentials_available": not agent.source_configuration_unavailable}

    def require_idle():
        if (any(task["status"] == "运行中" for task in agent.archive.list())
                or any(worker.is_alive() for worker in agent.workers.values())):
            raise ContractError("研究批次尚未停止，请等待结束后再修改运行配置；当前预算保留")

    def require_execution(payload):
        if not runtime_configurable:
            return  # trusted offline/fabricated launchers retain their contract
        expected = payload.get("expected_execution")
        current = execution_state()
        if (not isinstance(expected, dict) or set(expected) != {"mode", "policy_version"}
                or type(expected.get("policy_version")) is not int
                or expected.get("mode") != current["mode"] or expected["policy_version"] != current["policy_version"]):
            raise ContractError("运行模式已变化或尚未确认，请刷新状态后重新发送；未派发研究")

    def require_task_context(payload, task):
        # Old trusted callers retain their interface. A supplied expectation
        # is always strict; new browser actions send it before awaiting I/O.
        if "expected_task" not in payload:
            return
        expected = payload["expected_task"]
        if (not isinstance(expected, dict) or set(expected) != set(context(task))
                or any(type(expected.get(key)) is not int for key in ("version", "batch"))
                or any(not isinstance(expected.get(key), str) for key in ("id", "cutoff", "input_hash"))
                or expected != context(task)):
            raise ContractError("研究版本、批次或输入已变化，请重新读取任务后确认操作；未执行")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # no bodies, tokens, user questions or credential logs

        def send(self, value, status=200, mime="application/json; charset=utf-8", cookie=None):
            data = json.dumps(value, ensure_ascii=False).encode() if mime.startswith("application/json") else value.encode() if isinstance(value, str) else value
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(data)

        def session(self):
            try:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                value = cookie.get("researchhelper_session")
                return value.value if value else None
            except Exception:
                return None

        def authenticated(self):
            if account:
                return account.require(self.session())

        def owned(self, ident):
            task = agent.archive.get(ident)
            if account and task.get("owner_account") != self.authenticated()["account"]:
                raise PermissionDenied("任务不存在或当前账户无访问权限")
            return task

        def task_view(self, task):
            originals = agent.archive.retained_originals(task, pending_only=True)
            mappings = identity_resolutions(task)
            extra = {}
            if originals:
                extra["retained_originals"] = originals
            if mappings:
                extra["identity_resolutions"] = mappings
            delivery = task.get('delivery') or {}
            if type(delivery.get('version')) is int:
                corrections = agent.archive.correction_records(task['id'], delivery['version'])
                if corrections:
                    extra['report_corrections'] = corrections
            value = dict(task, **extra) if extra else task
            return workspace_task_view(value) if self.headers.get('X-ResearchHelper-View') == 'workspace-v1' else value

        def claim_new(self, task):
            if account:
                owner = self.authenticated()["account"]
                if task.get("owner_account") != owner:
                    task["owner_account"] = owner
                    agent.archive.save(task)
            return task

        def allowed(self, write=False):
            if self.headers.get('X-ResearchHelper-View') not in (None, 'workspace-v1'):
                raise ContractError('工作区读取视图版本未登记')
            expected = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected:
                raise ContractError("仅允许本地研究服务")
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + expected:
                raise ContractError("拒绝跨域请求")
            if write and self.headers.get("X-ResearchHelper-Token") != token:
                raise LocalTokenInvalid("本地操作令牌无效")

        def do_GET(self):
            try:
                self.allowed()
                route = urlparse(self.path).path
                if route == "/api/auth/status":
                    self.send({**(account.status(self.session()) if account else {"configured": False, "authenticated": True, "local_only": True}),
                               "token": token, "authentication_required": authentication})
                    return
                if route.startswith("/api/") or route.startswith("/reports/"):
                    self.authenticated()
                if route in ("/", "/index.html") and account and not account.current(self.session()):
                    query = urlparse(self.path).query
                    self.send_response(302)
                    self.send_header("Location", "/login/index.html" + ("?" + query if query else ""))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if route == "/api/bootstrap":
                    historical = json.loads((resource_directory(resources, 'data') / 'report_catalog.json').read_text())
                    identity = self.authenticated()
                    tasks = agent.archive.list()
                    visible_tasks = [t for t in tasks if not account or t.get('owner_account') == identity['account']]
                    manager = not account or identity["role"] == "admin"
                    self.send({"version": __version__, "offline": agent.offline, "token": token, "entries": ENTRIES, "intents": INTENTS,
                               "cases": catalog(), "tasks": [self.task_view(t) for t in visible_tasks], "task_presentations": agent.archive.presentations(visible_tasks), "history": historical["sources"], "capabilities": active_source_capabilities(),
                               "legacy_unassigned_count": sum(not t.get("owner_account") for t in tasks) if manager else 0,
                               "field_registry": FIELD_REGISTRY, "filing_operations": FILING_REQUEST_REGISTRY,
                               "financial_operations": FINANCIAL_REQUEST_REGISTRY,
                               "knowledge": agent.knowledge.catalog(), "model_settings": model_settings.read() if manager else {},
                               "source_settings": source_settings.read() if manager else {}, "execution": execution_state(),
                               # Sales needs readiness, not the endpoint, provider or credential fields.
                               "research_readiness": research_readiness(),
                               "authentication": authentication, "account": identity["account"] if identity else None,
                               "role": identity["role"] if identity else None, "storage_location": str(agent.archive.root)})
                elif route.startswith("/api/tasks/"):
                    parts = route.strip("/").split("/")
                    ident = parts[2]
                    task = self.owned(ident)
                    if len(parts) == 3:
                        # Return exactly the verified ownership snapshot, not a
                        # second potentially changed and expensive archive read.
                        self.send(self.task_view(task))
                    elif parts[3] == "report":
                        version = int(parts[4]) if len(parts) > 4 else agent.archive.get(ident)["version"]
                        report=agent.export(ident,version)
                        from urllib.parse import parse_qs
                        selected_profile=parse_qs(urlparse(self.path).query).get('profile',[None])[0]
                        if selected_profile:
                            from .report_designer import render_html
                            text=render_html(report['content'],resources,profile=selected_profile)
                        else:text=report['html']
                        self.send(text,mime="text/html; charset=utf-8")
                    elif parts[3] == "report-history" and len(parts) == 4:
                        self.send(dict(task_id=ident, reports=agent.archive.delivery_history(ident)))
                    elif parts[3] == "versions":
                        self.send(agent.archive.delivery_versions(ident))
                    elif parts[3] == "presentation":
                        self.send(agent.archive.presentations([task]).get(task.get('thread_id') or ident, {'title':'','pinned':False,'archived':False,'revision':0}))
                    else:
                        raise ContractError("操作不存在")
                else:
                    frontend = resource_directory(resources, 'frontend')
                    data = resource_directory(resources, 'data')
                    path = frontend / ('index.html' if route == '/' else route.lstrip('/'))
                    if route.startswith("/reports/"):
                        path = data / route.lstrip('/')
                    path = path.resolve()
                    permitted = [frontend, data / 'reports']
                    if not any(root == path or root in path.parents for root in permitted) or not path.is_file():
                        self.send({"error": "文件不存在"}, 404)
                        return
                    suffix_mime = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".json": "application/json"}
                    if route.startswith('/reports/') and path.suffix == '.html':
                        from .report_examples import render_example
                        self.send(render_example(path.stem, resources), mime=suffix_mime['.html'])
                        return
                    self.send(path.read_bytes(), mime=suffix_mime.get(path.suffix, "application/octet-stream"))
            except AuthenticationRequired as error:
                self.send({"error": str(error)}, 401)
            except PermissionDenied as error:
                self.send({"error": str(error)}, 403)
            except (ContractError, ValueError) as error:
                self.send({"error": str(error)}, 400)
            except Exception:
                self.send({"error": "读取失败，请检查本地档案"}, 500)

        def do_POST(self):
            try:
                self.allowed(write=True)
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 36 * 1024 * 1024:
                    raise ContractError("请求体积无效")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ContractError("请求必须为字段记录")
                route = urlparse(self.path).path
                if route == "/api/auth/setup" and account:
                    if set(payload) != {"admin_password", "sales_password"}:
                        raise ContractError("初始化字段无效")
                    self.send(account.setup(payload["admin_password"], payload["sales_password"]))
                    return
                if route == "/api/auth/login" and account:
                    if set(payload) - {"account", "password", "remember"}:
                        raise ContractError("登录字段无效")
                    cookie, value = account.login(payload.get("account"), payload.get("password"), payload.get("remember", False))
                    header = "researchhelper_session=" + cookie + "; Path=/; HttpOnly; SameSite=Strict"
                    if value["remember"]:
                        header += "; Max-Age=2592000"
                    self.send(value, cookie=header)
                    return
                self.authenticated()
                if account and (route.startswith("/api/settings/") or route.startswith("/api/auth/users") or route == "/api/archive/claim-legacy"):
                    account.require_admin(self.session())
                if route == "/api/auth/logout" and account:
                    account.logout(self.session())
                    self.send({"logged_out": True}, cookie="researchhelper_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
                    return
                if route == "/api/tasks":
                    if account and payload.get("parent"):
                        self.owned(payload["parent"])
                    with agent.lock:
                        value = self.claim_new(agent.create(**payload))
                elif route == "/api/auth/users" and account:
                    value = account.create_sales(self.session(), payload.get("account"), payload.get("password"))
                elif route == "/api/auth/users/sales/password" and account:
                    if set(payload) != {"password"}:
                        raise ContractError("密码设置字段无效")
                    value = account.reset_sales(self.session(), payload["password"])
                elif route == "/api/archive/claim-legacy" and account:
                    if payload != {"confirm": True}:
                        raise ContractError("认领旧档案须明确确认")
                    claimed = []
                    with agent.lock:
                        for task in agent.archive.list():
                            if not task.get("owner_account"):
                                task.setdefault("ownership_audit", []).append({"action": "explicit_legacy_claim", "previous_owner": None, "account": "admin"})
                                self.claim_new(task)
                                claimed.append(task["id"])
                    value = {"claimed": len(claimed)}
                elif route == "/api/settings/model":
                    with agent.lock:
                        require_idle()
                        value = model_settings.save(payload)
                elif route == "/api/settings/data":
                    with agent.lock:
                        require_idle()
                        value = source_settings.save(payload)
                        agent.source_credentials = source_settings.read(private=True)
                        agent.source_configuration_unavailable = False
                elif route == "/api/settings/execution":
                    with agent.lock:
                        require_idle()
                        if not runtime_configurable:
                            raise ContractError("此运行环境锁定离线验收，不允许开启真实调用")
                        if payload.get("enabled") is True:
                            config = model_settings.read(private=True)
                            if not all(config.get(field) for field in ("endpoint", "model", "api_key")):
                                raise ContractError("请先保存完整模型配置，再确认开启真实研究")
                        saved = execution.save(payload)
                        agent.offline = not saved["enabled"]
                        agent.collector.offline = agent.offline
                        agent.model.offline = agent.offline
                        model_settings.offline = agent.offline
                        value = execution_state()
                elif route == "/api/export":
                    if set(payload)-{'task_id','version','profile','format','theme','visual_profile'}:raise ContractError('导出字段无效')
                    task=self.owned(payload.get('task_id'))
                    version=payload.get('version',task['version'])
                    if type(version) is not int or version<1:raise ContractError('报告版本无效')
                    report=agent.export(task['id'],version)
                    from .artifact_exporter import export_report
                    receipt=export_report(report['content'],resources,agent.archive.root/'exports'/task['id'],
                        profile=payload.get('profile','standard'),format=payload.get('format','html'),theme=payload.get('theme','research'),visual_profile=payload.get('visual_profile'))
                    self.send(dict(task_id=task['id'],version=version,profile=receipt['profile'],visual_profile=receipt['visual_profile'],filename=receipt['filename'],mime=receipt['mime'],sha256=receipt['sha256'],
                        content_base64=base64.b64encode(Path(receipt['path']).read_bytes()).decode()))
                    return
                elif route == "/api/cases":
                    with agent.lock:
                        value = self.claim_new(agent.create_case(payload["id"]))
                elif route == "/api/import":
                    # Archive an old backup without executing old state, HTML or approvals.
                    raw = payload.get("backup")
                    serialized = json.dumps(raw, ensure_ascii=False)
                    if len(serialized) > 2_000_000 or not isinstance(raw, (dict, list)):
                        raise ContractError("旧备份格式或体积不适用")
                    with agent.lock:
                        value = self.claim_new(agent.create("导入的历史任务材料", parent="legacy-import"))
                    value["legacy_backup"] = raw
                    value["gaps"] = ["旧状态仅留档，不等于新引擎核验通过；请从原材料重新研究。"]
                    agent.archive.save(value)
                elif route.startswith("/api/tasks/"):
                    parts = route.strip("/").split("/")
                    if len(parts) != 4 or not parts[2]:
                        raise ContractError("任务操作路径无效")
                    ident, action = parts[2], parts[3]
                    self.owned(ident)
                    if action == 'presentation':
                        value = agent.archive.update_presentation(ident, payload)
                    elif action == 'report-correction':
                        if set(payload) != {'version', 'reason', 'expected_digest'}:
                            raise ContractError('更正须指定报告版本、说明和原报告标识')
                        if not isinstance(payload['expected_digest'], str) or not payload['expected_digest']:
                            raise ContractError('缺少所选报告标识，请重新打开报告')
                        with agent.lock:
                            self.owned(ident)
                            notes = agent.archive.correction(ident, payload['version'], payload['reason'],
                                expected_digest=payload['expected_digest'])
                        value = dict(task_id=ident, version=payload['version'], corrections=notes)
                    elif action in ("start", "resume"):
                        with agent.lock:
                            require_task_context(payload, self.owned(ident))
                            require_execution(payload)
                            value = agent.start(ident, host_result=payload.get("host_result"), asynchronous=True)
                    elif action == "plan":
                        value = agent.revise_plan(ident, payload["questions"], payload["reason"], payload.get("expected_input_hash"))
                    elif action == "cancel":
                        with agent.lock:
                            require_task_context(payload, self.owned(ident))
                            value = agent.cancel(ident)
                    elif action == "update":
                        with agent.lock:
                            require_task_context(payload, self.owned(ident))
                            require_execution(payload)
                            value = agent.update(ident, payload.get("cutoff"), asynchronous=True, host_result=payload.get("host_result"))
                    elif action == "retry-save":
                        with agent.lock:
                            require_task_context(payload, self.owned(ident))
                            value = agent.retry_save(ident)
                    elif action == "material":
                        if "expected_version" in payload and (type(payload["expected_version"]) is not int or payload["expected_version"] < 1):
                            raise ContractError("材料保存需要有效的研究版本")
                        value = agent.add_material(ident, payload["name"], base64.b64decode(payload["base64"], validate=True),
                                                   expected_version=payload.get("expected_version"))
                    elif action == "snapshot":
                        value = agent.add_snapshot(ident, payload["source"], payload["operation"], payload["parameters"], payload["response"])
                    elif action == "filing":
                        content = base64.b64decode(payload["base64"], validate=True) if payload.get("base64") is not None else None
                        value = agent.add_filing(ident, payload["directory"], payload.get("name"), content)
                    elif action == "source-catalog":
                        value = agent.source_catalog(ident, payload.get("artifact_ids"), offset=payload.get("offset", 0), limit=payload.get("limit", 24))
                    elif action == "retry-original":
                        value = agent.retry_original(ident, payload["original_id"])
                    elif action == "read-materials":
                        value = agent.read_materials(ident, payload["requests"], payload["binding"])
                    elif action == "document-review":
                        if set(payload) != {"artifact_id", "question", "expected_version", "expected_snapshot_hash", "confirmed"}:
                            raise ContractError("原件内容核对只接受所选原件、问题与明确用途，不接受研究产物或自动启动")
                        with agent.lock:
                            value = self.claim_new(agent.document_review(ident, **payload))
                    elif action == "messages":
                        if not isinstance(payload.get("start", True), bool):
                            raise ContractError("追问启动方式必须明确指定")
                        with agent.lock:
                            if payload.get("start", True):
                                require_execution(payload)
                            value = self.claim_new(agent.follow_up(ident, payload["message"], start=False))
                            if payload.get("start", True):
                                value = agent.start(value["id"], asynchronous=True)
                    else:
                        raise ContractError("操作不存在")
                else:
                    raise ContractError("接口不存在")
                self.send(self.task_view(value) if isinstance(value, dict) and "id" in value and "version" in value else value)
            except AuthenticationRequired as error:
                self.send({"error": str(error)}, 401)
            except PermissionDenied as error:
                self.send({"error": str(error)}, 403)
            except LocalTokenInvalid as error:
                self.send({"error": str(error), "failure_code": "local_token_invalid"}, 400)
            except (ContractError, ValueError, KeyError, TypeError) as error:
                self.send({"error": str(error)}, 400)
            except Exception:
                self.send({"error": "本地操作失败，检查点未被清理"}, 500)

    server = ResearchHTTPServer(("127.0.0.1", port), Handler)
    server.agent = agent
    return server


def app_store():
    configured = os.environ.get("RESEARCHHELPER_STORE")
    # Never add the new schema to the legacy application's database.
    if configured:
        return Path(configured)
    if os.name == 'nt':
        local = os.environ.get('LOCALAPPDATA')
        return (Path(local) if local else Path.home() / 'AppData/Local') / 'ResearchHelper/research-v1'
    return Path.home() / "Library" / "Application Support" / "ResearchHelper" / "research-v1"


def serve(resources=None, open_browser=False):
    server = make_server(app_store(), resources, authentication=True, runtime_configurable=True, preset_accounts=True)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(json.dumps({"ready": True, "url": url, "pid": os.getpid(), "version": __version__}), flush=True)
    if open_browser:
        import webbrowser
        threading.Timer(.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()
