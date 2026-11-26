"""Loopback-only App service. No legacy Qt/pipeline or shared SDK startup."""

from __future__ import annotations

import base64
import json
import math
import re
import os
import secrets
import shutil
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from sqlite3 import DatabaseError
from tempfile import TemporaryDirectory, TemporaryFile
from urllib.parse import parse_qs, urlparse

from . import __version__
from .workspace_views import workspace_task_summary
from .research_methods import catalogue as research_method_catalogue
from .archive import ArchiveRecordError, ResearchArchive, WorkspaceDraftConflict
from .backup_package import (BackupPackageRequired, FILE_INDEX_VERSION, PACKAGE_LIMIT, inspect_package,
                             limited_json, read_package_file, read_saved_package_file_index, write_package)
from .auth import AuthenticationRequired, LocalAccount, PermissionDenied
from .cases import catalog
from .contracts import (ContractError, DuplicateJSONField, ENTRIES, ExecutionTimeExhausted, FollowupParentChanged, INTENTS, LocalStateError,
                        browser_json_values, context, fingerprint, recovery_time_block, unique_json_object)
from .engine import ResearchAgent
from .evidence import active_source_capabilities, FIELD_REGISTRY, FILING_REQUEST_REGISTRY, identity_resolutions
from .source_catalogue import source_catalogue
from .financial_sources import FINANCIAL_REQUEST_REGISTRY
from .model import ModelService, IsolatedModelService, ModelSettings
from .runtime import ExecutionPolicy, StoreLease
from .settings import DataSettings
from .layout import default_resource_root, research_library_guides, resource_directory


POST_BODY_LIMIT = 36 * 1024 * 1024
POST_BODY_TIMEOUT = 15
# The same JSON file must fit the native save bridge and the import request.
TASK_BACKUP_LIMIT = 20_000_000
TASK_BACKUP_SIZE_ERROR = "任务备份超过20MB。请分别导出报告及留存原件。"


def _backup_title(raw):
    if isinstance(raw, dict):
        tasks = raw.get("tasks")
        first = tasks[0] if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict) else {}
        for title in (first.get("question"), raw.get("question"), raw.get("title")):
            if isinstance(title, str) and title.strip():
                return title.strip()[:20000].encode("utf-8", errors="replace").decode("utf-8")
    return "导入的历史任务材料"


def _finite_json_number(literal):
    value = float(literal)
    if not math.isfinite(value):
        raise ContractError("请求JSON包含无效数值，未执行操作")
    return value


class LocalTokenInvalid(ContractError):
    """A stale loopback guard, not an invalid account or password."""


class ResearchModelRequired(ContractError):
    """This study still needs reasoning and has not been dispatched."""


def workspace_task_view(task):
    """Opt-in presentation only; canonical storage and report content stay whole."""
    view = {key: value for key, value in task.items()
            if key not in ('pending_content', 'pending_html', 'pending_html_binding', 'previous_delivery',
                           'draft_composition_binding', 'recovery_block', 'can_finish_from_data',
                           'chat_submission_hash', 'legacy_backup', 'legacy_backup_text')}
    pending = bool(task.get('pending_content'))
    retained = False
    if not pending and task.get('status') in ('失败','已取消'):
        from .draft_workflow import has_composed_draft, retained_submission

        pending = has_composed_draft(task)
        retained = not pending and bool(task.get('draft_submissions')) and retained_submission(task) is not None
    view.update(workspace_view_version=1, has_retained_submission=retained,
                has_pending_content=pending)
    blocked = recovery_time_block(task, save_only=pending or retained)
    if blocked:
        view['recovery_block'] = dict(code=ExecutionTimeExhausted.code, reason=blocked)
    elif not pending and not retained:
        from .draft_workflow import has_retained_draft_data
        if has_retained_draft_data(task):
            view['can_finish_from_data'] = True
    if task['status'] == '已取消':
        # Older archived tasks can still carry a running node from the cancelled batch.
        view['nodes'] = [dict(node, status='已取消') if node.get('status') == '运行中' else node
                         for node in task.get('nodes', [])]
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
    # The workbench reads extracts and digests. Complete bytes stay in the
    # archive and travel only through explicit backup and file downloads.
    return browser_json_values(view, omit=("original_base64",))





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
        self.stop_requested = False
        self.runtime_connections = set()
        self.runtime_settled = threading.Event()
        self.runtime_lease = None
        super().__init__(*args, **kwargs)

    def server_bind(self):
        if os.name == 'nt':
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def process_request(self, request, client_address):
        with self.runtime_condition:
            if self.runtime_closing or self.stop_requested:
                self.shutdown_request(request)
                return
            self.runtime_connections.add(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_settled(request)
            raise

    def begin_operation(self):
        with self.runtime_condition:
            if self.runtime_closing or self.stop_requested:
                raise ContractError("本地服务正在退出，未执行请求")

    def _request_settled(self, request):
        with self.runtime_condition:
            self.runtime_connections.discard(request)
            self.runtime_condition.notify_all()

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_settled(request)

    def handle_error(self, request, client_address):
        if self.runtime_closing and isinstance(sys.exception(), ConnectionError):
            return
        super().handle_error(request, client_address)

    def server_close(self):
        with self.runtime_condition:
            if self.runtime_closing:
                return
            self.runtime_closing = True
            connections = tuple(self.runtime_connections)
        super().server_close()
        # Unblock socket reads and writes. Already-admitted operations still
        # own the Store until their handler and any research worker settle.
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        agent = getattr(self, 'agent', None)

        def stop_engine():
            return agent.close_runtime() if agent is not None else ()

        def release_after_settlement(workers):
            for worker in workers:
                if worker is not threading.current_thread():
                    worker.join()
            with self.runtime_condition:
                self.runtime_condition.wait_for(lambda: not self.runtime_connections)
                if self.runtime_lease is not None:
                    self.runtime_lease.close()
                self.runtime_settled.set()

        def settle_active_requests():
            release_after_settlement(stop_engine())

        if connections:
            # An admitted write may hold the agent lock. Let it finish without
            # blocking the caller that stopped accepting local connections.
            threading.Thread(target=settle_active_requests,
                name='ResearchHelper-service-settlement', daemon=True).start()
            return
        workers = stop_engine()
        if any(worker.is_alive() for worker in workers):
            threading.Thread(target=release_after_settlement, args=(workers,),
                name='ResearchHelper-service-settlement', daemon=True).start()
        else:
            release_after_settlement(workers)


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


def _startup_control(operation, failure_code):
    try:
        return operation()
    except LocalStateError:
        raise
    except (OSError, ContractError, DatabaseError) as error:
        raise LocalStateError(failure_code, str(error)) from None


def _make_server(store, resources, port, *, offline, source_credentials, authentication, runtime_configurable, preset_accounts):
    resources = Path(resources or default_resource_root()).resolve()
    try:
        library_guides = research_library_guides(resources)
    except (OSError, ValueError):
        library_guides = {}  # Archived bundles may predate the reading guides.
    execution = ExecutionPolicy(store, configurable=runtime_configurable)
    policy = execution.read()
    if runtime_configurable:
        offline = not policy["enabled"]
    source_settings = DataSettings(store)
    model_settings = ModelSettings(store, offline=offline)
    if runtime_configurable:
        # Only an updated native host migrates its own local legacy settings.
        # Locked demos and Skill hosts remain read-only on ordinary reads.
        _startup_control(model_settings.migrate_legacy, "model_configuration_unavailable")
        _startup_control(model_settings.read, "model_configuration_unavailable")
        _startup_control(source_settings.migrate_legacy, "source_configuration_unavailable")
    source_configuration_unavailable = False
    if source_credentials is None:
        _startup_control(source_settings.read, "source_configuration_unavailable")
        try:
            source_credentials = source_settings.read(private=True)
        except ContractError:
            # A missing/unreadable referenced secret must not prevent the RH
            # settings surface from opening. Do not persist blanks, change a
            # provider, enable execution or borrow another product's secrets.
            source_credentials = {source: {name: "" for name in names}
                                  for source, names in source_settings.fields.items()}
            source_configuration_unavailable = True
    account = LocalAccount(store) if authentication else None
    if preset_accounts:
        _startup_control(account.ensure_default_accounts, "account_unavailable")
    archive = _startup_control(lambda: ResearchArchive(store), "store_unavailable")
    agent = ResearchAgent(archive, resources, offline=offline, source_credentials=source_credentials,
        isolate_sources=runtime_configurable, isolate_materials=runtime_configurable, require_argument_support=True, draft_first=True)
    agent.source_configuration_unavailable = source_configuration_unavailable
    token = secrets.token_urlsafe(32)
    if runtime_configurable:
        agent.model = IsolatedModelService(model_settings, offline=offline, journal=agent._journal,
            active=agent._check_run_control)
    else:
        agent.model = ModelService(model_settings, offline=offline, journal=agent._journal)
    def execution_state():
        with agent.lock:
            persisted = execution.read()
            if runtime_configurable and persisted != policy:
                raise ContractError("运行策略文件已变化，请重新打开App确认；未派发研究")
            return {"mode": "offline" if agent.offline else "live", "configurable": runtime_configurable,
                    "policy_version": persisted["policy_version"]}

    def research_readiness():
        """Configuration completeness only; no connectivity or permission claim."""
        return {"model_configured": agent.model.configured(),
                "data_credentials_available": not agent.source_configuration_unavailable}

    def unreadable_summary(task):
        with agent.lock:
            return known_unreadable.get(task['id']) == task['revision']

    def require_idle():
        if (any(task.get("status") == "运行中" and not unreadable_summary(task)
                for task in agent.archive.list_workspace(unreadable={}))
                or agent.run_pending()):
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

    def require_runnable(task, *, incoming_attachments=False):
        if (runtime_configurable and not agent.offline and not research_readiness()["model_configured"]
                and not agent.can_run_without_model(task, incoming_attachments=incoming_attachments)):
            raise ResearchModelRequired("这个问题需要配置研究模型。")

    def chat_admission(payload):
        # Availability metadata is not part of the immutable submission ID.
        expectation = {}
        if "expected_execution" in payload:
            expectation["expected_execution"] = payload.pop("expected_execution")
        incoming = payload.pop("has_attachments", False)
        if type(incoming) is not bool:
            raise ContractError("待发送附件状态须为布尔值")

        def admit(task):
            if expectation:
                require_execution(expectation)
            require_runnable(task, incoming_attachments=incoming)

        return admit

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

        def send(self, value, status=200, mime="application/json; charset=utf-8", cookie=None, *,
                 file_size=None, report_identity=None):
            if self.server.runtime_closing:
                return
            if file_size is None:
                data = json.dumps(value, ensure_ascii=False).encode() if mime.startswith("application/json") else value.encode() if isinstance(value, str) else value
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data) if file_size is None else file_size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            if report_identity is not None:
                self.send_header("X-ResearchHelper-Report", report_identity)
            try:
                self.end_headers()
                if file_size is None:
                    self.wfile.write(data)
                else:
                    shutil.copyfileobj(value, self.wfile, length=128 * 1024)
            except (BrokenPipeError, ConnectionResetError):
                # Leaving a preview may cancel its read while the server is
                # responding. The disconnected client cannot read a retry.
                self.close_connection = True

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

        def owned(self, ident, *, saved_report=False):
            try:
                task = agent.archive.get(ident)
            except ArchiveRecordError:
                headers = agent.archive.list_workspace(task_id=ident, unreadable={})
                header = headers[0] if headers else None
                if account and (not header or header.get('owner_account') != self.authenticated()['account']):
                    raise PermissionDenied("任务不存在或当前账户无访问权限") from None
                if header:
                    with agent.lock:
                        known_unreadable[ident] = header['revision']
                    if saved_report:
                        return dict(header, archive_unreadable=True)
                raise
            if account and task.get("owner_account") != self.authenticated()["account"]:
                raise PermissionDenied("任务不存在或当前账户无访问权限")
            with agent.lock:
                known_unreadable.pop(ident, None)
            return task

        def read_saved_report(self, task, version):
            if account and task.get('archive_unreadable'):
                # A cached summary identifies the task. The saved version must
                # independently prove the same owner before its report is read.
                frozen = agent.archive.get(task['id'], version)
                if frozen.get('owner_account') != task.get('owner_account'):
                    raise PermissionDenied("报告不存在或当前账户无访问权限")
            return agent.export(task['id'], version)

        def check_report_profile(self, profile):
            if account and profile == 'detailed':
                account.require_admin(self.session())

        def saved_report_history(self, task):
            history = agent.archive.delivery_history(task['id'])
            if not task.get('archive_unreadable'):
                return history
            available = []
            for item in history:
                try:
                    self.read_saved_report(task, item['version'])
                except ContractError:
                    continue
                available.append(item)
            return available

        def task_view(self, task, *, batch_settling=False):
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
            if self.headers.get('X-ResearchHelper-View') not in ('workspace-v1', 'workspace-v2'):
                return value
            view = workspace_task_view(value)
            version = delivery.get('version')
            if type(version) is int and not delivery.get('title'):
                saved = agent.archive.delivery_history(task['id'], version=version)
                if saved and saved[0]['digest'] == delivery.get('digest') and saved[0].get('title'):
                    view['delivery'] = dict(view['delivery'], title=saved[0]['title'])
            files = agent.archive.original_files(task)
            if files:
                view['original_files'] = files
            if batch_settling:
                view['batch_settling'] = True
            prior = task.get('update_from')
            version = prior.get('version') if isinstance(prior, dict) else None
            if not delivery and type(version) is int and 0 < version < task['version']:
                history = agent.archive.delivery_history(task['id'], version=version)
                if history and history[0]['digest'] == prior.get('digest'):
                    view['previous_delivery'] = browser_json_values(history[0])
            return view

        def claim_new(self, task):
            if account:
                owner = self.authenticated()["account"]
                if task.get("owner_account") != owner:
                    task["owner_account"] = owner
                    agent.archive.save(task)
            return task

        def chat_submission(self, nonce, input_binding=None, *, current_task=None):
            if not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
                raise ContractError("发送标识无效")
            identity = self.authenticated()
            owner = identity["account"] if identity else None
            ident = "rh_" + fingerprint(["researchhelper.chat-submission.v1", owner, nonce])[:32]
            if current_task is not None and current_task["id"] != ident:
                raise ContractError("发送记录与任务不一致，未启动研究")
            task = current_task if current_task is not None else agent.archive.get(ident, allow_missing=True)
            if task is not None and (task.get("owner_account") != owner or not task.get("chat_submission_hash")):
                raise PermissionDenied("无法确认当前账户的发送记录")
            receipt = None
            if input_binding is not None:
                digest = fingerprint(input_binding)
                if task is not None and task["chat_submission_hash"] != digest:
                    raise ContractError("同一次发送的输入已改变，请作为新问题发送")
                receipt = {"task_id": ident, "input_hash": digest}
            return receipt, task

        def allowed(self, write=False):
            if self.headers.get('X-ResearchHelper-View') not in (None, 'workspace-v1', 'workspace-v2'):
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
                self.server.begin_operation()
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
                if route == "/api/workspace-draft":
                    identity = self.authenticated()
                    owner = identity["account"] if identity else None
                    self.send(dict(account=owner, **agent.archive.workspace_draft(owner or "")))
                    return
                if route == "/api/bootstrap":
                    historical = json.loads((resource_directory(resources, 'data') / 'report_catalog.json').read_text())
                    identity = self.authenticated()
                    summary_view = self.headers.get('X-ResearchHelper-View') == 'workspace-v2'
                    manager = not account or identity["role"] == "admin"
                    unreadable = {}
                    summaries = agent.archive.list_workspace(unreadable=unreadable)
                    visible_tasks = []
                    for summary in summaries:
                        if account and summary.get('owner_account') != identity['account']:
                            continue
                        if unreadable_summary(summary):
                            visible_tasks.append(dict(summary, archive_unreadable=True))
                        elif summary_view:
                            visible_tasks.append(summary)
                        else:
                            try:
                                visible_tasks.append(self.task_view(self.owned(summary['id'])))
                            except ArchiveRecordError:
                                visible_tasks.append(dict(summary, archive_unreadable=True))
                            except PermissionDenied:
                                continue  # ownership changed after the summary snapshot
                    archive_issue_count = sum(bool(t.get('archive_unreadable')) for t in visible_tasks)
                    if manager:
                        archive_issue_count += len(unreadable)
                    index = agent.archive.latest_delivery_versions() if summary_view else {}
                    saved_versions = {t['id']: index[t['id']] for t in visible_tasks
                                      if type(index.get(t['id'])) is int and type(t.get('version')) is int
                                      and 0 < index[t['id']] <= t['version']}
                    workspace = {}
                    if summary_view:
                        try:
                            workspace["workspace_draft"] = agent.archive.workspace_draft(identity["account"] if identity else "")
                        except ArchiveRecordError:
                            workspace["workspace_draft"] = None
                    self.send({"version": __version__, "offline": agent.offline, "token": token, "entries": ENTRIES, "intents": INTENTS,
                               "cases": catalog(), "tasks": visible_tasks, "task_presentations": agent.archive.presentations(visible_tasks), "history": historical["sources"], "capabilities": active_source_capabilities(), "source_catalogue": source_catalogue(),
                               "saved_report_versions": saved_versions,
                               "legacy_unassigned_count": sum(not t.get("owner_account") for t in summaries) if manager else 0,
                               "archive_issue_count": archive_issue_count,
                               "field_registry": FIELD_REGISTRY, "filing_operations": FILING_REQUEST_REGISTRY,
                               "financial_operations": FINANCIAL_REQUEST_REGISTRY,
                               "knowledge": agent.knowledge.catalog(), "research_methods": research_method_catalogue(),
                               "research_guides": library_guides,
                               "model_settings": model_settings.read() if manager else {},
                               "source_settings": source_settings.read() if manager else {}, "execution": execution_state(),
                               # Sales needs readiness, not the endpoint, provider or credential fields.
                               "research_readiness": research_readiness(),
                               "authentication": authentication, "account": identity["account"] if identity else None,
                               "role": identity["role"] if identity else None, "storage_location": str(agent.archive.root),
                               **workspace})
                elif route.startswith("/api/chat-submissions/"):
                    with agent.lock:
                        _, task = self.chat_submission(route.removeprefix("/api/chat-submissions/"))
                        settling = task is not None and task["status"] != "运行中" and agent.run_pending(task["id"])
                    self.send(self.task_view(task, batch_settling=settling) if task is not None else None)
                elif route.startswith("/api/tasks/"):
                    parts = route.strip("/").split("/")
                    ident = parts[2]
                    if len(parts) == 3:
                        # Pair the revision and run state before a worker can
                        # settle its ledger. A pre-settlement snapshot must
                        # never make the workbench stop watching.
                        with agent.lock:
                            task = self.owned(ident)
                            settling = task['status'] != '运行中' and agent.run_pending(ident)
                        self.send(self.task_view(task, batch_settling=settling))
                        return
                    action = parts[3]
                    if action == "backup" and len(parts) == 4:
                        with agent.lock:
                            task = self.owned(ident)
                            if task.get("backup_package"):
                                raise BackupPackageRequired("这份任务使用ZIP备份")
                            if task.get("parent") == "legacy-import":
                                wire = task.get("legacy_backup_text")
                                if wire is None:
                                    legacy = task.get("legacy_backup")
                                    if not isinstance(legacy, (dict, list)):
                                        raise ContractError("原备份内容缺失，无法下载")
                                    wire = json.dumps(legacy, ensure_ascii=True, separators=(",", ":"))
                            else:
                                backup = agent.archive.task_backup(task)
                        if task.get("parent") != "legacy-import":
                            wire = limited_json(backup, TASK_BACKUP_LIMIT)
                        elif len(wire.encode()) > TASK_BACKUP_LIMIT:
                            raise ContractError(TASK_BACKUP_SIZE_ERROR)
                        self.send(wire, mime="text/plain; charset=utf-8")
                        return
                    if action == "backup-package" and len(parts) == 4:
                        with agent.lock:
                            task = self.owned(ident)
                            expected = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                            if expected:
                                version, revision = expected.get("version", []), expected.get("revision", [])
                                if (set(expected) != {"version", "revision"} or version != [str(task["version"])]
                                        or len(revision) != 1 or not re.fullmatch(r"[1-9][0-9]{0,15}", revision[0])
                                        or int(revision[0]) > task["revision"]):
                                    raise ContractError("研究版本已变化，请重新备份当前任务")
                            imported = task.get("parent") == "legacy-import"
                            if imported and not task.get("backup_package"):
                                raise ContractError("这份历史档案使用JSON备份")
                            backup = None if imported else agent.archive.task_backup(task)
                        if imported:
                            self.send(agent.archive.backup_package(task), mime="application/zip")
                        else:
                            with TemporaryFile(prefix="rh-task-backup-") as file:
                                size = write_package(backup, file)
                                self.send(file, mime="application/zip", file_size=size)
                        return
                    if (action, len(parts)) in (("backup-files", 4), ("backup-file", 5)):
                        task = self.owned(ident)
                        receipt = task.get("backup_package")
                        if not isinstance(receipt, dict) or not isinstance(receipt.get("files"), list):
                            raise ContractError("备份文件不存在")
                        files = receipt["files"]
                        index = None
                        if action == "backup-file":
                            if not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})", parts[4]):
                                raise ContractError("备份文件不存在")
                            index = int(parts[4])
                        payload = agent.archive.backup_package(task)
                        if receipt.get("file_index_version") != FILE_INDEX_VERSION and (index is None or index >= len(files)):
                            files = read_saved_package_file_index(payload)
                        if index is None:
                            self.send({"files": files})
                        elif index >= len(files):
                            raise ContractError("备份文件不存在")
                        else:
                            self.send(read_package_file(payload, files[index]), mime="application/octet-stream")
                        return
                    if action == "original-file" and len(parts) == 6:
                        task = self.owned(ident)
                        version, identity = parts[4:]
                        if not re.fullmatch(r"[1-9][0-9]{0,15}", version) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity):
                            raise ContractError("留存原件标识无效")
                        payload = agent.archive.read_original_file(task, int(version), identity)
                        self.send(payload, mime="application/octet-stream")
                        return
                    task = self.owned(ident, saved_report=action in ('report', 'report-history', 'versions'))
                    if action == "report":
                        version = int(parts[4]) if len(parts) > 4 else task["version"]
                        selected_profile=parse_qs(urlparse(self.path).query).get('profile',[None])[0]
                        self.check_report_profile(selected_profile)
                        report=self.read_saved_report(task, version)
                        if not selected_profile and self.headers.get("X-ResearchHelper-Report-Preview") == "1":
                            from .reports import READER_VERSION
                            current_reader = f'data-reader-version="{READER_VERSION}"'
                            if (not report['html_integrity_verified'] or current_reader not in report['html']
                                    or 'id="rh-publication-document"' not in report['html']):
                                # Preview an older delivery in the current reader. Its
                                # original HTML remains available from the archive route.
                                selected_profile = 'standard'
                        if selected_profile:
                            from .report_designer import render_html
                            text=render_html(report['content'],resources,profile=selected_profile)
                        else:text=report['html']
                        self.send(text,mime="text/html; charset=utf-8",
                                  report_identity=f"{ident}:{version}:{report['digest']}")
                    elif action == "report-history" and len(parts) == 4:
                        self.send(dict(task_id=ident, reports=self.saved_report_history(task)))
                    elif action == "versions":
                        versions = ([item['version'] for item in reversed(self.saved_report_history(task))]
                                    if task.get('archive_unreadable') else agent.archive.delivery_versions(ident))
                        self.send(versions)
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
                self.send({"error": str(error), "failure_code": "authentication_required"}, 401)
            except PermissionDenied as error:
                self.send({"error": str(error)}, 403)
            except ArchiveRecordError as error:
                self.send({"error": str(error), "failure_code": "archive_record_unavailable"}, 400)
            except BackupPackageRequired as error:
                self.send({"error": str(error), "failure_code": error.code}, 400)
            except (ContractError, ValueError) as error:
                self.send({"error": str(error)}, 400)
            except Exception:
                self.send({"error": "读取失败，请检查本地档案"}, 500)

        def read_post_body(self, limit=POST_BODY_LIMIT, *, too_large="请求体积无效", timeout=POST_BODY_TIMEOUT):
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or self.headers.get("Transfer-Encoding") is not None:
                raise ContractError("请求正文长度声明无效，未执行操作")
            length = lengths[0]
            if not length.isascii() or not length.isdecimal():
                raise ContractError("请求正文长度声明无效，未执行操作")
            size = int(length)
            if size <= 0:
                raise ContractError("请求体积无效")
            if size > limit:
                raise ContractError(too_large)
            body = bytearray()
            deadline = time.monotonic() + timeout
            previous_timeout = self.connection.gettimeout()
            try:
                while len(body) < size:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    self.connection.settimeout(remaining)
                    chunk = self.rfile.read1(min(128 * 1024, size - len(body)))
                    if not chunk:
                        raise ContractError("请求正文不完整，未执行操作")
                    body.extend(chunk)
            except TimeoutError:
                raise ContractError("读取请求正文超时，未执行操作") from None
            finally:
                self.connection.settimeout(previous_timeout)
            return body

        def read_post_payload(self, *, backup_file=False):
            body = (self.read_post_body(TASK_BACKUP_LIMIT, too_large=TASK_BACKUP_SIZE_ERROR)
                    if backup_file else self.read_post_body())
            if backup_file:
                try:
                    wire = body.decode("utf-8")
                except UnicodeError:
                    raise ContractError("任务备份须为UTF-8 JSON") from None
            try:
                payload = json.loads(wire.removeprefix("\ufeff") if backup_file else body, object_pairs_hook=unique_json_object,
                                     parse_float=_finite_json_number, parse_constant=_finite_json_number)
            except DuplicateJSONField:
                raise ContractError("请求JSON包含重复字段，未执行操作") from None
            except ContractError:
                raise
            except (ValueError, RecursionError):
                raise ContractError("请求正文不是有效JSON，未执行操作") from None
            if not isinstance(payload, dict):
                if not backup_file or not isinstance(payload, list):
                    raise ContractError("请求必须为字段记录")
            if backup_file:
                return {"backup": payload, "wire": wire}
            return payload

        def do_POST(self):
            nonlocal policy
            try:
                self.allowed(write=True)
                location = urlparse(self.path)
                route = location.path
                if route == "/api/import" and location.query == "file=zip":
                    identity = self.authenticated()
                    package = bytes(self.read_post_body(PACKAGE_LIMIT, too_large="任务ZIP备份超过128MiB", timeout=45))
                    self.server.begin_operation()
                    receipt = inspect_package(package)
                    with agent.lock:
                        value = agent.archive_backup(receipt["title"], owner_account=identity["account"] if identity else None,
                                                     package=package, receipt=receipt)
                    self.send(workspace_task_view(value))
                    return
                backup_file = route == "/api/import" and location.query == "file=1"
                payload = self.read_post_payload(backup_file=backup_file)
                self.server.begin_operation()
                batch_settling = False
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
                identity = self.authenticated()
                if account and (route.startswith("/api/settings/") or route.startswith("/api/auth/users") or route == "/api/archive/claim-legacy"):
                    account.require_admin(self.session())
                if route == "/api/auth/logout" and account:
                    account.logout(self.session())
                    self.send({"logged_out": True}, cookie="researchhelper_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
                    return
                if route == "/api/workspace-draft":
                    owner = identity["account"] if identity else None
                    if set(payload) != {"account", "expected_revision", "write_id", "state"}:
                        raise ContractError("草稿请求字段无效")
                    if payload["account"] != owner:
                        raise PermissionDenied("登录账户已改变，未保存草稿")
                    value = agent.archive.save_workspace_draft(owner or "", payload["expected_revision"],
                                                               payload["write_id"], payload["state"])
                elif route == "/api/tasks":
                    if account and payload.get("parent"):
                        self.owned(payload["parent"])
                    with agent.lock:
                        receipt, existing, admission = None, None, None
                        if "submission_id" in payload:
                            nonce = payload.pop("submission_id")
                            admission = chat_admission(payload)
                            receipt, existing = self.chat_submission(nonce, ["root", payload])
                        if existing is not None:
                            batch_settling = existing["status"] != "运行中" and agent.run_pending(existing["id"])
                        value = existing if existing is not None else agent.create(**payload,
                            owner_account=identity["account"] if identity else None, submission=receipt, admission=admission)
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
                        for summary in agent.archive.list_workspace(unreadable={}):
                            if summary.get("owner_account"):
                                continue
                            try:
                                task = agent.archive.get(summary['id'])
                            except ArchiveRecordError:
                                continue
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
                        policy = execution.save(payload, expected=policy)
                        agent.offline = not policy["enabled"]
                        agent.collector.offline = agent.offline
                        agent.model.offline = agent.offline
                        model_settings.offline = agent.offline
                        value = execution_state()
                elif route == "/api/export":
                    if set(payload)-{'task_id','version','profile','format','theme','visual_profile'}:raise ContractError('导出字段无效')
                    task=self.owned(payload.get('task_id'),saved_report=True)
                    version=payload.get('version',task['version'])
                    if type(version) is not int or version<1:raise ContractError('报告版本无效')
                    profile=payload.get('profile','standard')
                    self.check_report_profile(profile)
                    report=self.read_saved_report(task, version)
                    from .artifact_exporter import export_report
                    # The desktop save panel or browser owns the destination.
                    # Keep the HTML-first conversion private and temporary.
                    with TemporaryDirectory(prefix='rh-app-export-') as directory:
                        receipt=export_report(report['content'],resources,directory,
                            profile=profile,format=payload.get('format','html'),
                            theme=payload.get('theme'),visual_profile=payload.get('visual_profile'))
                        encoded=base64.b64encode(Path(receipt['path']).read_bytes()).decode('ascii')
                    self.send(dict(task_id=task['id'],version=version,report_digest=receipt['content_hash'],
                        profile=receipt['profile'],visual_profile=receipt['visual_profile'],format=receipt['format'],filename=receipt['filename'],
                        mime=receipt['mime'],sha256=receipt['sha256'],content_base64=encoded))
                    return
                elif route == "/api/cases":
                    with agent.lock:
                        value = self.claim_new(agent.create_case(payload["id"]))
                elif route == "/api/import":
                    raw = payload.get("backup")
                    if set(payload) != ({"backup", "wire"} if backup_file else {"backup"}) or not isinstance(raw, (dict, list)):
                        raise ContractError("任务备份格式无效")
                    wire = payload["wire"] if backup_file else json.dumps(raw, ensure_ascii=True, separators=(",", ":"))
                    if len(wire.encode()) > TASK_BACKUP_LIMIT:
                        raise ContractError(TASK_BACKUP_SIZE_ERROR)
                    with agent.lock:
                        value = agent.archive_backup(_backup_title(raw), wire,
                                                     owner_account=identity["account"] if identity else None)
                    self.send(workspace_task_view(value))
                    return
                elif route.startswith("/api/tasks/"):
                    parts = route.strip("/").split("/")
                    if len(parts) != 4 or not parts[2]:
                        raise ContractError("任务操作路径无效")
                    ident, action = parts[2], parts[3]
                    task = self.owned(ident)
                    if task.get("parent") == "legacy-import" and action != "presentation":
                        raise ContractError("导入档案仅供查阅，请从原件新建研究")
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
                            task = self.owned(ident)
                            previously_started = False
                            if "submission_id" in payload:
                                if action != "start":
                                    raise ContractError("发送标识只用于首次启动")
                                self.chat_submission(payload["submission_id"], current_task=task)
                                previously_started = task["version"] != 1 or task["batch"] > 0 or task["status"] != "待开始"
                            if previously_started:
                                batch_settling = task["status"] != "运行中" and agent.run_pending(ident)
                                value = task
                            else:
                                require_task_context(payload, task)
                                require_execution(payload)
                                host_result = payload.get("host_result")
                                value = agent.start(ident, host_result=host_result, asynchronous=True,
                                                    admission=require_runnable if host_result is None else None)
                    elif action == "plan":
                        value = agent.revise_plan(ident, payload["questions"], payload["reason"], payload.get("expected_input_hash"))
                    elif action == "cancel":
                        with agent.lock:
                            require_task_context(payload, self.owned(ident))
                            value = agent.cancel(ident)
                            settling = agent.run_pending(ident)
                        self.send(self.task_view(value, batch_settling=settling))
                        return
                    elif action == "update":
                        with agent.lock:
                            require_task_context(payload, self.owned(ident))
                            require_execution(payload)
                            host_result = payload.get("host_result")
                            value = agent.update(ident, payload.get("cutoff"), asynchronous=True, host_result=host_result,
                                                 admission=require_runnable if host_result is None else None)
                    elif action == "retry-save":
                        value = agent.retry_save(ident, admission=lambda current:
                            require_task_context(payload, self.owned(current['id'])))
                    elif action == 'finish-draft':
                        if set(payload) != {'expected_task'}:
                            raise ContractError('现有资料出稿须指定当前任务版本及批次')
                        with agent.lock:
                            require_task_context(payload, self.owned(ident))
                            value = agent.start(ident, asynchronous=True, retained_data_only=True)
                    elif action == "material":
                        if "expected_version" in payload and (type(payload["expected_version"]) is not int or payload["expected_version"] < 1):
                            raise ContractError("材料保存需要有效的研究版本")
                        if "include_record" in payload and type(payload["include_record"]) is not bool:
                            raise ContractError("上传回执选项须为布尔值")
                        options = dict(expected_version=payload.get("expected_version"), expected_revision=payload.get("expected_revision"))
                        if payload.get("include_record"):
                            record = agent.add_material(ident, payload["name"], base64.b64decode(payload["base64"], validate=True), return_record=True, **options)
                            value = dict(task=self.task_view(self.owned(ident)),
                                material={key:record[key] for key in ("id", "task_id", "research_version", "name", "hash")})
                        else:
                            value = agent.add_material(ident, payload["name"], base64.b64decode(payload["base64"], validate=True), **options)
                    elif action == "remove-material":
                        if set(payload) != {"name", "sha256", "retained_files", "expected_revision", "expected_task"}:
                            raise ContractError("移除材料须指定文件、保留列表和当前任务状态")
                        with agent.lock:
                            current = self.owned(ident)
                            require_task_context(payload, current)
                            value = agent.remove_material(ident, payload["name"], payload["sha256"],
                                retained_files=payload["retained_files"], expected_version=current["version"],
                                expected_revision=payload["expected_revision"])
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
                        start = payload.get("start", True)
                        if not isinstance(start, bool):
                            raise ContractError("追问启动方式必须明确指定")
                        revision = payload.get('expected_revision')
                        if 'expected_revision' in payload and (type(revision) is not int or revision < 1):
                            raise ContractError('追问须提供有效的原研究修订号')
                        with agent.lock:
                            self.owned(ident)
                            receipt, existing = None, None
                            admission = require_runnable if start else None
                            if "submission_id" in payload:
                                if start:
                                    raise ContractError("带发送标识的追问须先保存，再启动研究")
                                admission = chat_admission(payload)
                                binding = ["follow-up", ident, payload["message"]]
                                if revision is not None:
                                    binding.append(revision)
                                receipt, existing = self.chat_submission(payload["submission_id"],
                                    binding)
                            if existing is not None:
                                batch_settling = existing["status"] != "运行中" and agent.run_pending(existing["id"])
                            if start:
                                require_execution(payload)
                            value = existing if existing is not None else self.claim_new(
                                agent.follow_up(ident, payload["message"], start=False, submission=receipt,
                                                expected_revision=revision, admission=admission))
                            if start:
                                value = agent.start(value["id"], asynchronous=True)
                    else:
                        raise ContractError("操作不存在")
                else:
                    raise ContractError("接口不存在")
                self.send(self.task_view(value, batch_settling=batch_settling)
                          if isinstance(value, dict) and "id" in value and "version" in value else value)
            except AuthenticationRequired as error:
                self.send({"error": str(error)}, 401)
            except PermissionDenied as error:
                self.send({"error": str(error)}, 403)
            except LocalTokenInvalid as error:
                self.send({"error": str(error), "failure_code": "local_token_invalid"}, 400)
            except ResearchModelRequired as error:
                self.send({"error": str(error), "failure_code": "research_model_required"}, 400)
            except FollowupParentChanged as error:
                self.send({"error": str(error), "failure_code": "followup_parent_changed"}, 409)
            except WorkspaceDraftConflict as error:
                self.send({"error": str(error)}, 409)
            except ExecutionTimeExhausted as error:
                self.send({"error": str(error), "failure_code": error.code}, 400)
            except (ContractError, ValueError, KeyError, TypeError) as error:
                self.send({"error": str(error)}, 400)
            except Exception:
                self.send({"error": "本地操作失败，检查点未被清理"}, 500)

    try:
        server = ResearchHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        agent.close_runtime()
        raise LocalStateError("local_listener_unavailable", "工作台本地端口暂不可用") from None
    server.agent = agent
    try:
        known_unreadable = agent.recover_interrupted()
    except BaseException:
        server.server_close()
        raise
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
    desktop = os.environ.get("RESEARCHHELPER_DESKTOP_LIFETIME") == "stdin"
    try:
        preferred_port = os.environ.get("RESEARCHHELPER_DESKTOP_PORT") if desktop else None
        port = 0
        if preferred_port is not None:
            if not re.fullmatch(r"[0-9]{1,5}", preferred_port) or not 1 <= int(preferred_port) <= 65535:
                raise LocalStateError("local_listener_unavailable", "工作台本地端口无效")
            port = int(preferred_port)
        server = make_server(app_store(), resources, port=port, authentication=True,
                             runtime_configurable=True, preset_accounts=True)
    except LocalStateError as error:
        print(json.dumps({"ready": False, "failure_code": error.code}), flush=True)
        raise SystemExit(1) from None
    url = f"http://127.0.0.1:{server.server_port}/"
    previous_handler = None

    def request_stop(_signum, _frame):
        # The signal can arrive while the main thread is registering a
        # connection. Leave locks and I/O to the regular shutdown path.
        server.stop_requested = True

    def watch_desktop_lifetime():
        # Only the native launcher owns the pipe's writer. EOF covers a
        # normal quit and owner death, without requiring a control request.
        # Use an unbuffered descriptor so signal-driven exit can still finish
        # while the reader is blocked and the owner has not closed its end.
        try:
            while os.read(0, 256):
                pass
        except OSError:
            pass
        request_stop(None, None)

    try:
        if threading.current_thread() is threading.main_thread():
            previous_handler = signal.signal(signal.SIGTERM, request_stop)
        if desktop:
            threading.Thread(target=watch_desktop_lifetime, name="ResearchHelper-desktop-lifetime",
                             daemon=True).start()
        print(json.dumps({"ready": True, "url": url, "pid": os.getpid(), "version": __version__}), flush=True)
        if open_browser:
            import webbrowser
            threading.Timer(.2, lambda: webbrowser.open(url)).start()
        server.timeout = .2
        while not server.stop_requested:
            server.handle_request()
    finally:
        server.server_close()
        server.runtime_settled.wait()
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)
