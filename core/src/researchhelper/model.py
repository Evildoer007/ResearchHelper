"""Independent model configuration and bounded, policy-controlled transport."""

import json
import math
import http.client
import multiprocessing
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .contracts import ContractError, DuplicateJSONField, canonical, charge, fingerprint, now, unique_json_object
from .credentials import LocalSecretStore, configuration_lock, owned_bytes, replace_references
from .https_transport import open_verified_https
from .evidence import business_payload
from .transport import TransportFailure, run_isolated

MODEL_OPTIONAL_FIELDS = frozenset({'extraction_mode', 'reasoning_effort', 'planning_effort'})
_JSON_QUOTE_OR_ESCAPE = re.compile(r'["\\]')


class ModelCompletionError(ContractError):
    """A dispatched response did not contain a usable final completion."""


class StructuredCompletionError(ModelCompletionError):
    """The model finished, but its structured submission was rejected."""


def _contains_private_completion(text, api_key, *, inspect_fields=False):
    if business_payload(text, (api_key,)) != text:
        return True
    if inspect_fields:
        # Draft replies are retained before the report renderer normalizes them.
        body = text.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        try:
            value = json.loads(body, object_pairs_hook=unique_json_object)
        except DuplicateJSONField:
            raise
        except (ValueError, RecursionError):
            pass
        else:
            if business_payload(value, (api_key,)) != value:
                return True
    if '\\' not in text:
        return False
    # A later JSON decode can expose a key or URL hidden by string escapes.
    # Advance one cursor so malformed quoted strings never rescan their tails.
    end = 0
    while (start := text.find('"', end)) != -1:
        end = start + 1
        escaped = False
        while True:
            boundary = _JSON_QUOTE_OR_ESCAPE.search(text, end)
            if boundary is None:
                return False
            end = boundary.end()
            if boundary[0] == '"':
                break
            end += 1
            escaped = True
        if not escaped:
            continue
        try:
            decoded = json.loads(text[start:end])
        except ValueError:
            continue
        if business_payload(decoded, (api_key,)) != decoded:
            return True
    return False


def analysis_completion_failure(task):
    """Distinguish provider completion from locally rejected selections."""
    result = dict(field='analysis.completion', reason='模型未正常完成本次分析')
    events = [row for row in task.get('ledger',{}).get('dispatches',[])
        if row.get('kind') == 'model_calls' and row.get('research_phase') == 'analyze'
        and row.get('batch') == task.get('batch')]
    event = events[-1] if events else {}
    if (event.get('failure_code') == 'private_content'
            or event.get('output_contract', {}).get('failure_code') == 'private_content'):
        return dict(field='analysis.private_content', reason='模型回复含敏感内容，已拦截')
    if (event.get('status') != '失败' or event.get('response_body_complete') is not True
            or event.get('provider_completion_status') != 'completed' or event.get('finish_reason') != 'stop'
            or event.get('failure_code') != 'structured_output_rejected'):
        return result
    result.update(field='analysis.output_selection',reason='模型分析已返回，但结构化选择未通过校验')
    issues = event.get('structured_output_diagnostic',{}).get('schema_issues',[])
    if issues and all(isinstance(issue,dict) and issue.get('code') == 'enum_mismatch'
        and isinstance(issue.get('path'),str) and issue['path'].startswith('$.answers')
        and issue['path'].endswith(('.refs[*]','.counter_refs[*]')) for issue in issues):
        result['reason'] = '分析引用不属于对应问题的允许范围'
    return result


def request_failure_category(payload):
    """Fixed HTTP400 diagnostics only; never retain an error body or message."""
    if not isinstance(payload, bytes) or len(payload) > 8192:
        return 'invalid_request_unspecified'
    try:
        value = json.loads(payload, object_pairs_hook=unique_json_object)
        error = value.get('error', {}) if isinstance(value, dict) else {}
        message = error.get('message', '') if isinstance(error, dict) else ''
    except (ValueError, UnicodeDecodeError):
        return 'invalid_request_unspecified'
    if not isinstance(message, str):
        return 'invalid_request_unspecified'
    message = message.casefold()
    if 'strict' in message and any(word in message for word in ('not support', 'unsupported', 'disabled')):
        return 'strict_output_unsupported'
    if 'tool_choice' in message and any(word in message for word in ('thinking', 'reasoning')):
        return 'tool_choice_thinking_conflict'
    if any(word in message for word in ('schema', 'additionalproperties', 'properties', 'anyof')):
        return 'output_schema_rejected'
    if 'max_tokens' in message:
        return 'output_limit_rejected'
    if 'messages' in message:
        return 'message_format_rejected'
    return 'invalid_request_unspecified'


class ModelSettings:
    @staticmethod
    def completion_endpoint(value):
        """Accept OH-style base URLs without rewriting legacy custom routes."""
        parsed = urlparse(value)
        trimmed = value.rstrip("/")
        path = parsed.path.rstrip("/")
        if path == "" or path.endswith("/v1"):
            return trimmed + "/chat/completions"
        if path.endswith("/chat/completions"):
            return trimmed
        return value  # explicit full/proxy endpoints remain backward-compatible

    @staticmethod
    def credential_origin(value):
        parsed = urlparse(value)
        return parsed.scheme, parsed.hostname, parsed.port or 443

    @staticmethod
    def supports_deepseek_controls(value):
        endpoint = urlparse(value.get("endpoint", ""))
        try:
            return (endpoint.scheme == "https" and endpoint.hostname == "api.deepseek.com"
                    and endpoint.port in (None, 443)
                    and value.get("model") in ("deepseek-flash", "deepseek-v4-pro"))
        except ValueError:
            return False

    @staticmethod
    def supports_direct_extraction(value):
        return ModelSettings.supports_deepseek_controls(value)

    @staticmethod
    def supports_structured_analysis(value):
        """Explicit official strict routes; never rewrite a configured URL."""
        return (ModelSettings.supports_deepseek_controls(value)
                and urlparse(value.get('endpoint', '')).path in ('/beta/chat/completions', '/responses'))

    @staticmethod
    def uses_responses(value):
        return (ModelSettings.supports_deepseek_controls(value)
                and urlparse(value.get('endpoint', '')).path == '/responses')

    def __init__(self, store, offline=True):
        self.path = Path(store) / "model-settings.json"
        self.offline = offline
        self.lock = configuration_lock(self.path)
        self.secrets = LocalSecretStore(store)

    @staticmethod
    def validate(value):
        required = {"endpoint", "model", "api_key"}
        if not isinstance(value, dict) or not required <= set(value) or set(value) - required - MODEL_OPTIONAL_FIELDS:
            raise ContractError("模型配置字段无效")
        limits = {"endpoint": 2000, "model": 200, "api_key": 16384}
        for field, limit in limits.items():
            item = value[field]
            if not isinstance(item, str) or len(item) > limit or any(ord(char) < 32 or ord(char) == 127 for char in item):
                raise ContractError("模型配置须为长度适当且不含控制字符的文字")
        try:
            parsed = urlparse(value["endpoint"])
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError()
        except ValueError:
            raise ContractError("模型服务地址或端口无效，请检查HTTPS地址") from None
        if value["endpoint"] and (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query):
            raise ContractError("模型地址须为HTTPS，不允许地址内嵌凭据")
        mode = value.get("extraction_mode", "default")
        if mode not in ("default", "direct"):
            raise ContractError("请选择有效的证据提取模式")
        if mode == "direct" and not ModelSettings.supports_direct_extraction(value):
            raise ContractError("直接提取仅支持DeepSeek官方接口的deepseek-flash或deepseek-v4-pro；其他接口请选择保持模型默认")
        effort = value.get("reasoning_effort", "default")
        if effort not in ("default", "none", "low", "high", "max"):
            raise ContractError("请选择有效的推理强度")
        if effort != "default" and not ModelSettings.supports_deepseek_controls(value):
            raise ContractError("推理强度仅支持DeepSeek官方接口的deepseek-flash或deepseek-v4-pro；其他接口请选择服务默认")
        planning = value.get('planning_effort', 'inherit')
        if planning not in ('inherit', 'none', 'low', 'high', 'max'):
            raise ContractError('请选择有效的规划推理强度')
        if planning != 'inherit' and not ModelSettings.supports_deepseek_controls(value):
            raise ContractError('独立规划强度仅支持DeepSeek官方接口的deepseek-flash或deepseek-v4-pro；其他接口请选择沿用默认强度')
        return value

    def read(self, private=False):
        with self.lock:
            try:
                saved = self._configuration()
                reference = saved.get("secret_ref")
                value = self.validate({key: item for key, item in saved.items() if key != "secret_ref"}
                    | {"api_key": self.secrets.resolve(reference, "model-service") if private and reference else saved.get("api_key", "")})
            except (OSError, ValueError, TypeError, ContractError):
                raise ContractError("ResearchHelper模型配置无法读取，请恢复原配置；不会覆盖") from None
            if private:
                return value
            public = {"endpoint": value["endpoint"], "model": value["model"], "has_key": bool(reference or value["api_key"]), "offline": self.offline}
            if value.get("extraction_mode", "default") != "default":
                public["extraction_mode"] = value["extraction_mode"]
            if value.get("reasoning_effort", "default") != "default":
                public["reasoning_effort"] = value["reasoning_effort"]
            if value.get('planning_effort', 'inherit') != 'inherit':
                public['planning_effort'] = value['planning_effort']
            return public

    def _configuration(self):
        raw = owned_bytes(self.path)
        if raw is None:
            return {"endpoint": "", "model": "", "api_key": ""}
        try:
            saved = json.loads(raw, object_pairs_hook=unique_json_object)
        except (ValueError, RecursionError):
            raise ContractError("模型配置JSON损坏或含重复字段，原件保留") from None
        if isinstance(saved, dict) and "secret_ref" in saved:
            if not {"endpoint", "model", "secret_ref"} <= set(saved) or set(saved) - {"endpoint", "model", "secret_ref"} - MODEL_OPTIONAL_FIELDS:
                raise ContractError("模型配置字段无效")
            if saved["secret_ref"] is not None:
                self.secrets.validate(saved["secret_ref"], "model-service")
            self.validate({key: value for key, value in saved.items() if key != "secret_ref"} | {"api_key": ""})
            return saved
        return self.validate(saved)  # legacy content is read-only until save/migration

    def migrate_legacy(self):
        """Only the updated RH host opts into migration; no foreign vault access."""
        with self.lock:
            if not self.path.exists() or "secret_ref" in self._configuration():
                return False
            legacy = self.read(private=True)
            self.save(legacy)
            return True

    def save(self, value):
        if not isinstance(value, dict) or set(value) - {"endpoint", "model", "api_key", "clear_key"} - MODEL_OPTIONAL_FIELDS or any(not isinstance(value.get(k, ""), str) for k in ("endpoint", "model", "api_key")):
            raise ContractError("模型配置必须为文字字段")
        if not isinstance(value.get("clear_key", False), bool):
            raise ContractError("清除密钥须使用明确布尔选项")
        incoming = self.validate({field: value.get(field, "") for field in ("endpoint", "model", "api_key")})
        with self.lock:
            try:
                expected = owned_bytes(self.path)
                persisted = self._configuration()
            except (OSError, ValueError, TypeError, ContractError):
                raise ContractError("ResearchHelper模型配置无法读取，请恢复原配置；不会覆盖") from None
            old = self.read()
            saved = {field: incoming[field].strip() for field in incoming}
            if value.get("clear_key") is True:
                saved["api_key"] = ""
            elif not saved["api_key"]:
                if old.get("has_key") and self.credential_origin(saved["endpoint"]) != self.credential_origin(old["endpoint"]):
                    raise ContractError("服务地址已更换，请输入新的API密钥或明确清除原密钥；未保存")
                saved["api_key"] = self.read(private=True)["api_key"]
            mode = value.get("extraction_mode", old.get("extraction_mode", "default"))
            if mode != "default":
                saved["extraction_mode"] = mode
            effort = value.get("reasoning_effort", old.get("reasoning_effort", "default"))
            if effort != "default":
                saved["reasoning_effort"] = effort
            planning = value.get('planning_effort', old.get('planning_effort', 'inherit'))
            if planning != 'inherit':
                saved['planning_effort'] = planning
            self.validate(saved)  # validate the merged settings before any write
            public = {key: item for key, item in saved.items() if key != "api_key"}
            replace_references(self.path, self.secrets,
                lambda refs: dict(public, secret_ref=refs["model-service"]),
                {"model-service": saved["api_key"]},
                {"model-service": persisted.get("secret_ref")}, expected)
        return self.read()


class ModelService:
    # Header/DNS connection setup still uses urllib's socket-wait cap. Once
    # headers arrive, every single-read wait shares the original deadline;
    # slow trickles cannot obtain a new full timeout for each response chunk.
    MAX_WAIT_SECONDS = 180
    ORDINARY_WAIT_SECONDS = 90
    LONG_EXTRACTION_CHARACTERS = 32_000
    MAX_RESPONSE_BYTES = 4 * 1024 * 1024
    READ_CHUNK_BYTES = 64 * 1024

    def configured(self):
        settings = self.settings.read() if self.settings is not None else {}
        return all(settings.get(field) for field in ('endpoint', 'model', 'has_key'))

    @staticmethod
    def _response_socket(response):
        """Locate CPython HTTPResponse's live socket, without global overrides."""
        stream = getattr(response, "fp", None)
        raw = getattr(stream, "raw", None)
        return getattr(raw, "_sock", None)

    def _read_provider_response(self,response,started,timeout,record,on_progress=None):
        protocol=record.get('response_protocol')
        if protocol is None:
            return self._read_response(response,started,timeout,record)
        from .model_stream import DeepSeekStream,PROTOCOL,MAX_WIRE_BYTES
        if protocol!=PROTOCOL:
            record['failure_code']='unsupported_response_protocol'
            raise ContractError('模型响应协议未登记，不采用响应')
        headers=getattr(response,'headers',{})
        if headers.get('Content-Type','').split(';',1)[0].strip().lower()!='text/event-stream':
            record['failure_code']='unexpected_response_content_type'
            raise ModelCompletionError('模型未返回已请求的流式协议，不自动切换协议或重试')
        consumer=DeepSeekStream(record,on_progress)
        return self._read_response(response,started,timeout,record,consumer=consumer,byte_limit=MAX_WIRE_BYTES)

    def _read_response(self, response, started, timeout, record, *, consumer=None,byte_limit=None):
        """Use one underlying read per iteration and reduce its remaining wait.

        HTTPResponse.read(n) can loop over arbitrarily many slow arrivals. Its
        read1(n) performs at most one underlying read; the socket timeout is
        recomputed before that operation. In-memory host transports need no
        socket. Unknown network readers fail explicitly instead of silently
        reverting to an unbounded read or changing process-wide timeouts.
        """
        limit=self.MAX_RESPONSE_BYTES if byte_limit is None else byte_limit
        reader = getattr(response, "read1", None)
        if not callable(reader):
            record["failure_code"] = "unsupported_response_reader"
            raise ContractError("模型响应读取器不支持有界读取，不采用该响应")
        connection = self._response_socket(response)
        # A real urllib response has a BufferedReader with a SocketIO beneath
        # it. BytesIO fixture/host transports expose no fp and cannot block on
        # that socket; an opaque network wrapper must not claim this guarantee.
        if getattr(response, "fp", None) is not None and connection is None:
            record["failure_code"] = "unsupported_response_reader"
            raise ContractError("模型响应无法设置剩余等待时限，不采用该响应")
        deadline = started + timeout
        chunks, size = [], 0
        record["response_read_deadline"] = True
        expected = getattr(response, "length", None)
        if isinstance(expected, int) and not isinstance(expected, bool) and expected >= 0:
            record["declared_response_bytes"] = expected
            if expected > limit:
                record["failure_code"] = "response_too_large"
                raise ModelCompletionError("模型响应超出大小限制，不作为有效研究产物")
        else:
            expected = None
        record["received_response_bytes"] = 0

        def complete():
            # read1 does not raise IncompleteRead for short Content-Length
            # responses. Even a parseable JSON prefix cannot establish that
            # the provider's declared body was completely received.
            if expected is not None and size != expected:
                record["failure_code"] = "response_incomplete"
                raise TransportFailure("response_incomplete", "模型响应正文未完整接收，不作为有效研究产物")
            record["response_body_complete"] = True
            return consumer.complete() if consumer is not None else b"".join(chunks)

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                record["failure_code"] = "response_deadline_exceeded"
                raise TransportFailure("response_deadline_exceeded", "模型响应超过本次时限，不作为有效研究产物")
            if connection is not None:
                connection.settimeout(remaining)
            chunk = reader(min(self.READ_CHUNK_BYTES, limit + 1 - size))
            if time.monotonic() >= deadline:
                record["failure_code"] = "response_deadline_exceeded"
                raise TransportFailure("response_deadline_exceeded", "模型响应超过本次时限，不作为有效研究产物")
            if not chunk:
                return complete()
            if not isinstance(chunk, bytes):
                record["failure_code"] = "invalid_response_encoding"
                raise ModelCompletionError("模型响应字节格式无效，不采用该响应")
            size += len(chunk)
            record["received_response_bytes"] = size
            if size > limit:
                record["failure_code"] = "response_too_large"
                raise ModelCompletionError("模型响应超出大小限制，不作为有效研究产物")
            if consumer is not None:
                consumer.feed(chunk)
            else:
                chunks.append(chunk)
            # read1 closes HTTPResponse's fp as soon as Content-Length reaches
            # zero. Its captured socket is then closed as well; do not set a
            # timeout on that dead socket merely to perform an extra EOF read.
            closed = getattr(response, "isclosed", None)
            if connection is not None and callable(closed) and closed():
                return complete()

    @staticmethod
    def output_limit(config, phase=None):
        """Allow space for provider reasoning without changing model or effort."""
        endpoint = urlparse(config["endpoint"])
        if endpoint.hostname == "api.deepseek.com" and config["model"] in ("deepseek-flash", "deepseek-v4-pro"):
            if phase in ('analyze','review_support') and config.get('reasoning_effort','default') != 'none':
                # The provider counts thought and visible text together. Its
                # documented thinking default is64K;16K can end just as the
                # answer begins. This is a ceiling, never a requested length.
                return 65536
            return 16384
        return 8192

    @staticmethod
    def documented_input_budget(config):
        """Conservative byte upper bound for verified official 1M models.

        A nonempty tokenizer piece consumes at least one input byte. Counting
        the whole serialized request is deliberately conservative; output and
        a separate framing/safety allowance remain inside the documented window.
        Unknown providers, model names and routes do not inherit this profile.
        """
        if not ModelSettings.supports_deepseek_controls(config):
            return None
        endpoint=urlparse(ModelSettings.completion_endpoint(config['endpoint']))
        if (endpoint.path not in ('/responses','/chat/completions','/v1/chat/completions','/beta/chat/completions')
                or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password):
            return None
        return dict(version=1,profile='deepseek_1m_20261021',context_tokens=1000000,
            output_token_reserve=65536,safety_token_reserve=65536,input_byte_limit=868928,
            unit='utf8_bytes_upper_bound',documentation='https://api-docs.deepseek.com/quick_start/pricing/')

    def input_budget(self):
        return self.documented_input_budget(self.settings.read())

    @staticmethod
    def validate_input_budget(profile):
        expected=ModelService.documented_input_budget(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash'))
        numeric=('version','context_tokens','output_token_reserve','safety_token_reserve','input_byte_limit')
        if (not isinstance(profile,dict) or profile!=expected
                or any(type(profile.get(key)) is not int for key in numeric)):
            raise ContractError('模型输入容量依据未登记，未提高输入上限')

    @staticmethod
    def truncated_response_message(config, phase, record):
        """Explain a verified reasoning-only exhaustion without changing mode.

        Provider usage is diagnostic, not reasoning content or valid evidence.
        A direct-extraction recommendation is limited to supported providers,
        the extraction phase, and an unused opt-in setting.
        """
        default = "模型正文被截断，不作为有效研究产物"
        if (phase != "extract" or config.get("extraction_mode") == "direct"
                or not ModelSettings.supports_direct_extraction(config)):
            return default
        usage = record.get("usage")
        if not isinstance(usage, dict):
            return default
        details = usage.get("completion_tokens_details")
        if not isinstance(details, dict):
            return default
        limit = record.get("max_output_tokens")
        reasoning = details.get("reasoning_tokens")
        completion = usage.get("completion_tokens")
        if (any(type(value) is not int for value in (limit, reasoning, completion))
                or limit <= 0 or completion != limit or not 0 <= reasoning <= completion):
            return default
        if reasoning == limit:
            return "证据提取的输出额度全部用于模型思考，未取得有效正文。可在模型设置选择直接提取后决定是否继续；本次不会自动重试。"
        # Only a confirmed length finish supports diagnosing a near-total
        # reasoning share. Partial usage alone does not prove exhaustion.
        if record.get("finish_reason") == "length" and reasoning * 10 >= limit * 9:
            return "证据提取正文被截断，大部分输出额度用于模型思考。可在模型设置选择直接提取后决定是否继续；本次不会自动重试。"
        return default

    def __init__(self, settings, offline=True, journal=None):
        self.settings, self.offline, self.journal = settings, offline, journal

    def _request_bytes(self, task, request, started, timeout, record):
        """In-process transport retained for explicit host/fabricated launchers."""
        with open_verified_https(request, timeout=timeout, opener=urllib.request.urlopen) as response:
            record["stage"] = "读取响应"
            return self._read_provider_response(response, started, timeout, record)

    def reason_phase(self, task, messages, phase):
        """Trusted engine stage, separate from the untrusted material text."""
        if phase not in ("plan", "acquire", "read", "extract", "bind_calculations", "analyze", "supplement"):
            raise ContractError("模型调用阶段无效，未派发请求")
        return self.reason(task, messages, phase=phase)

    def reason_research_plan(self, task, messages):
        """Formal engine planning uses remaining task time, not material hints."""
        return self.reason(task, messages, phase='plan', planning_deadline=True)

    @staticmethod
    def supports_final_delivery():
        return True

    def reason_final_analysis(self,task,messages,schema=None):
        """Use the reserved call for a final answer, never further research."""
        if schema is not None and not self.supports_structured_analysis():
            raise ContractError('最终分析的结构化服务不可用，未派发请求')
        from .output_schema import analysis_protocol
        protocol = ({'output_protocol': analysis_protocol(task)[0]}
                    if schema is not None and task.get('analysis_citation_encoding') == 2 else {})
        return self.reason(task,messages,delivery=True,phase='analyze',output_schema=schema,
                           delivery_margin=5,**protocol)

    def reason_argument_support(self, task, proposal, *, delivery=False, format_issue=None):
        """Review submitted wording, not another research or extraction round."""
        from .argument_support import (PROTOCOL, review_request, review_schema, review_messages,
            enabled, reference_review_schema, REFERENCE_PROTOCOL)
        if type(delivery) is not bool or not self.supports_structured_analysis():
            raise ContractError('论证复核需要明确的结构化服务及预算用途，未派发请求')
        description = review_request(task, proposal)
        if format_issue is not None:
            from .argument_support import completed_review_submission_issue
            if format_issue != completed_review_submission_issue(task, proposal):
                raise ContractError('复核格式失败不是当前已完成派发，未重新提交')
        references = enabled(task)
        schema = reference_review_schema(description) if references else review_schema(description)
        return self.reason(task, review_messages(description,use_reference_units=references,format_issue=format_issue), delivery=delivery, phase='review_support',
                           output_schema=schema, output_protocol=REFERENCE_PROTOCOL if references else PROTOCOL,
                           schema_context=proposal, delivery_margin=5 if delivery else 0)

    def supports_structured_analysis(self):
        return ModelSettings.supports_structured_analysis(self.settings.read())

    def reason_structured_phase(self, task, messages, phase, schema):
        if phase != 'analyze' or not self.supports_structured_analysis():
            raise ContractError('严格结构化输出仅用于显式官方Beta地址的分析阶段，未派发请求')
        from .output_schema import analysis_protocol
        protocol = ({'output_protocol': analysis_protocol(task)[0]}
                    if task.get('analysis_citation_encoding') == 2 else {})
        return self.reason(task, messages, phase=phase, output_schema=schema,
                           **protocol)

    def reason_structured_supplement(self, task, messages, actions):
        from .output_schema import material_supplement_output_schema, SUPPLEMENT_PROTOCOL
        schema = material_supplement_output_schema(task, actions)
        if schema is None or not self.supports_structured_analysis():
            raise ContractError('当前补证不适用登记的严格容器，未派发请求')
        return self.reason(task, messages, phase='supplement', output_schema=schema,
                           output_protocol=SUPPLEMENT_PROTOCOL, schema_context=actions)

    def reason_structured_plan(self, task, messages):
        from .output_schema import planning_output_schema, planning_protocol
        if not self.supports_structured_analysis():
            raise ContractError('严格规划仅用于显式官方Beta配置，未派发请求')
        return self.reason(task, messages, phase='plan', output_schema=planning_output_schema(task),
                           output_protocol=planning_protocol(task)[0], planning_deadline=True)

    def reason_structured_acquisition(self, task, messages, description, *, draft_completion=None):
        from .output_schema import acquisition_output_schema, ACQUISITION_PROTOCOL
        if not self.supports_structured_analysis():
            raise ContractError('严格导航仅用于显式支持配置，未派发请求')
        checkpoint = {'draft_completion': draft_completion} if draft_completion is not None else {}
        return self.reason(task, messages, phase='acquire',
            output_schema=acquisition_output_schema(task, description),
            output_protocol=ACQUISITION_PROTOCOL, schema_context=description, **checkpoint)

    def reason_structured_reading(self, task, messages, description):
        from .output_schema import reading_output_schema, READING_PROTOCOL
        if not self.supports_structured_analysis():
            raise ContractError('严格读取选择仅用于显式官方Beta配置，未派发请求')
        return self.reason(task, messages, phase='read', output_schema=reading_output_schema(task, description),
                           output_protocol=READING_PROTOCOL, schema_context=description)

    def supports_reading_references(self):
        # A custom adapter overriding the old wire protocol is not silently
        # opted into a different submission format.
        return type(self).reason_structured_reading is ModelService.reason_structured_reading

    def reason_structured_reading_references(self, task, messages, description):
        from .output_schema import reading_reference_output_schema, READING_REFERENCE_PROTOCOL
        if not self.supports_structured_analysis():
            raise ContractError('读取短引用需要明确的结构化服务，未派发请求')
        return self.reason(task,messages,phase='read',output_schema=reading_reference_output_schema(task,description),
                           output_protocol=READING_REFERENCE_PROTOCOL,schema_context=description)

    def reason_structured_extraction(self, task, messages, views):
        from .output_schema import (material_extraction_output_schema, MATERIAL_EXTRACTION_PROTOCOL,
            financial_extraction_output_schema, FINANCIAL_EXTRACTION_PROTOCOL,
            api_extraction_output_schema, API_EXTRACTION_PROTOCOL)
        schema = financial_extraction_output_schema(task, views)
        protocol = FINANCIAL_EXTRACTION_PROTOCOL
        if schema is None:
            schema = material_extraction_output_schema(task, views)
            protocol = MATERIAL_EXTRACTION_PROTOCOL
        if schema is None:
            schema = api_extraction_output_schema(task, views)
            protocol = API_EXTRACTION_PROTOCOL
        if schema is None or not self.supports_structured_analysis():
            raise ContractError('当前提取不适用登记的严格容器，未派发请求')
        return self.reason(task, messages, phase='extract', output_schema=schema,
                           output_protocol=protocol, schema_context=views)

    def reason_structured_original_spans(self, task, messages, views):
        from .output_schema import original_span_output_schema, ORIGINAL_SPAN_PROTOCOL
        schema = original_span_output_schema(task, views)
        if schema is None or not self.supports_structured_analysis():
            raise ContractError('当前原件不适用登记的行范围选择，未派发请求')
        return self.reason(task, messages, phase='extract', output_schema=schema,
                           output_protocol=ORIGINAL_SPAN_PROTOCOL, schema_context=views)

    def reason_structured_calculation_binding(self, task, messages):
        from .output_schema import deferred_calculation_output_schema, CALCULATION_BINDING_PROTOCOL
        schema = deferred_calculation_output_schema(task)
        if schema is None or not self.supports_structured_analysis():
            raise ContractError('当前方法没有适用的实际输入绑定容器，未派发请求')
        return self.reason(task, messages, phase='bind_calculations', output_schema=schema,
                           output_protocol=CALCULATION_BINDING_PROTOCOL)

    def supports_financial_references(self):
        return type(self).reason_structured_extraction is ModelService.reason_structured_extraction

    def reason_structured_financial_references(self, task, messages, views):
        from .output_schema import financial_reference_output_schema, FINANCIAL_REFERENCE_PROTOCOL
        schema = financial_reference_output_schema(task, views)
        if schema is None or not self.supports_structured_analysis():
            raise ContractError('本次财务原件不适用短引用结构，未派发请求')
        return self.reason(task, messages, phase='extract', output_schema=schema,
                           output_protocol=FINANCIAL_REFERENCE_PROTOCOL, schema_context=views)

    def reason_structured_original_batch(self, task, messages, views):
        from .output_schema import original_batch_output_schema, ORIGINAL_BATCH_PROTOCOL
        schema = original_batch_output_schema(task, views)
        if schema is None or not self.supports_structured_analysis():
            raise ContractError('本次多原件不适用登记选择格式，未派发请求')
        return self.reason(task, messages, phase='extract', output_schema=schema,
                           output_protocol=ORIGINAL_BATCH_PROTOCOL, schema_context=views)

    def reason_structured_fee_original(self, task, messages, views):
        from .output_schema import fee_original_output_schema, FEE_ORIGINAL_PROTOCOL
        schema=fee_original_output_schema(task,views)
        if schema is None or not self.supports_structured_analysis():
            raise ContractError('费用原件选择需要完整登记口径与明确结构化服务，未派发请求')
        return self.reason(task,messages,phase='extract',output_schema=schema,
                           output_protocol=FEE_ORIGINAL_PROTOCOL,schema_context=views)

    @staticmethod
    def _responses_message(result, record):
        """Confirm the explicitly requested Responses envelope, without tools.

        Private reasoning is neither returned nor journaled. Incomplete or
        failed responses never contribute even a parseable JSON prefix.
        """
        status = result.get('status') if isinstance(result, dict) else None
        if status not in ('completed', 'incomplete', 'failed'):
            record.update(failure_code='completion_not_confirmed', completion_metadata='responses_status')
            raise ModelCompletionError('模型未提供有效结束状态，不作为有效研究产物')
        record.update(provider_completion_status=status, completion_metadata='responses_status')
        if status == 'incomplete':
            record.update(failure_code='content_truncated', finish_reason='length')
            raise ModelCompletionError('模型正文被截断，不作为有效研究产物')
        if status == 'failed':
            record.update(failure_code='generation_interrupted', finish_reason='aborted')
            raise ModelCompletionError('模型生成中断，未采用正文；已派发用量保留，不自动重试')
        output = result.get('output')
        if (result.get('object') != 'response' or result.get('error') is not None
                or result.get('incomplete_details') is not None or not isinstance(output, list)
                or any(not isinstance(item, dict) or item.get('type') not in ('reasoning', 'message')
                       for item in output)):
            record['failure_code'] = 'invalid_response_shape'
            raise ModelCompletionError('模型响应结构不完整，不作为有效研究产物')
        messages = [item for item in output if item['type'] == 'message']
        if len(messages) != 1:
            record['failure_code'] = 'invalid_response_shape'
            raise ModelCompletionError('模型没有单一明确结果，不作为有效研究产物')
        message = messages[0]
        content = message.get('content')
        if (message.get('role') != 'assistant' or message.get('status') != 'completed'
                or not isinstance(content, list) or len(content) != 1
                or not isinstance(content[0], dict) or content[0].get('type') != 'output_text'
                or not isinstance(content[0].get('text'), str) or not content[0]['text'].strip()):
            record['failure_code'] = 'invalid_completion_message'
            raise ModelCompletionError('模型正文角色、拒绝或结束状态无效，不采用该响应')
        # Internal message representation only. No tool envelope is synthesized.
        return {'role': 'assistant', 'content': content[0]['text']}, 'stop'

    def reason(self, task, messages, delivery=False, *, phase=None, output_schema=None,
               output_protocol=None, schema_context=None, planning_deadline=False,delivery_margin=0,
               draft_completion=None):
        if draft_completion is not None:
            if (task.get('draft_first_version') != 1 or phase not in ('draft_plan', 'acquire', 'draft_report')
                    or not isinstance(draft_completion, tuple) or len(draft_completion) != 2
                    or not isinstance(draft_completion[0], str)
                    or re.fullmatch(r'[a-f0-9]{64}', draft_completion[0]) is None
                    or not callable(draft_completion[1])):
                raise ContractError('草稿回复保存上下文无效，未派发请求')
        if self.offline:
            raise ContractError("离线验收阻断真实模型调用，保存配置不改变执行权限")
        from .argument_support import enabled as requires_argument_support
        if delivery and phase in (None, 'analyze') and requires_argument_support(task):
            raise ContractError('本任务的交付预留仅用于独立复核，不能生成没有复核额度的新分析')
        if delivery and phase not in (None,'analyze','review_support') and not (
                phase == 'draft_report' and task.get('draft_first_version') == 1):
            raise ContractError('交付预留仅用于最终分析或独立复核，不得继续规划或取证')
        if (type(delivery_margin) not in (int,float) or not math.isfinite(delivery_margin)
                or not 0<=delivery_margin<=60 or delivery_margin and not delivery):
            raise ContractError('交付保存余量只能用于最终生成，不改变研究预算')
        if type(planning_deadline) is not bool or planning_deadline and (phase != 'plan' or delivery):
            raise ContractError('规划等待策略只能由正式研究规划阶段使用，不能占用交付预留')
        config = self.settings.read(private=True)
        ModelSettings.validate(config)
        if output_schema is not None:
            from .output_schema import (analysis_output_schema, analysis_selected_questions, analysis_protocol, material_supplement_output_schema,
                planning_output_schema, FUNCTION_NAME, SUPPLEMENT_FUNCTION_NAME, SUPPLEMENT_PROTOCOL,
                PLAN_FUNCTION_NAME, PLAN_PROTOCOL, PLAN_REQUEST_PROTOCOL, planning_protocol, material_extraction_output_schema,
                MATERIAL_EXTRACTION_FUNCTION_NAME, MATERIAL_EXTRACTION_PROTOCOL,
                financial_extraction_output_schema, FINANCIAL_EXTRACTION_FUNCTION_NAME, FINANCIAL_EXTRACTION_PROTOCOL,
                api_extraction_output_schema, API_EXTRACTION_FUNCTION_NAME, API_EXTRACTION_PROTOCOL,
                reading_output_schema, READING_PROTOCOL, READING_FUNCTION_NAME)
            if (not ModelSettings.supports_structured_analysis(config)
                    or not isinstance(output_schema, dict)):
                raise ContractError('结构化输出服务或阶段已变化，未派发请求')
            output_protocol = output_protocol or 'strict_analysis_v1'
            if output_protocol == analysis_protocol(task)[0] and phase == 'analyze' and schema_context is None:
                try:
                    selected = analysis_selected_questions(output_schema)
                except (KeyError, TypeError, ValueError):
                    raise ContractError('结构化输出契约无效，未派发请求') from None
                expected_schema = analysis_output_schema(task, selected)
                function_name = analysis_protocol(task)[1]
                function_description = '提交本轮有来源的研究分析，不执行外部操作。正文按完整文字与引用数量片段提交；没有依据保留缺口。'
            elif output_protocol == READING_PROTOCOL and phase == 'read':
                expected_schema = reading_output_schema(task, schema_context)
                function_name = READING_FUNCTION_NAME
                function_description = '选择本任务原件的关键词和实际读取范围。不提取事实，不批准证据。'
            elif output_protocol == 'strict_reading_reference_v1' and phase == 'read':
                from .output_schema import reading_reference_output_schema, READING_REFERENCE_FUNCTION_NAME
                expected_schema = reading_reference_output_schema(task,schema_context)
                function_name = READING_REFERENCE_FUNCTION_NAME
                function_description = '只选择本次原件与页段短编号及关键词，内核恢复真实绑定；不批准事实或补造位置。'
            elif output_protocol == SUPPLEMENT_PROTOCOL and phase == 'supplement':
                expected_schema = material_supplement_output_schema(task, schema_context)
                function_name = SUPPLEMENT_FUNCTION_NAME
                function_description = '提交本轮材料补证决策，不执行工具。继续时提议已有原件读取或已登记计算；停止时只提交原因。'
            elif output_protocol == 'strict_acquisition_v1' and phase == 'acquire':
                from .output_schema import acquisition_output_schema, ACQUISITION_FUNCTION_NAME
                expected_schema = acquisition_output_schema(task, schema_context)
                function_name = ACQUISITION_FUNCTION_NAME
                function_description = '选择本轮已登记来源请求或明确停止，不执行工具、不批准证据，不扩大问题或预算。'
            elif output_protocol == planning_protocol(task)[0] and phase == 'plan' and schema_context is None:
                expected_schema = planning_output_schema(task)
                function_name = planning_protocol(task)[1]
                function_description = '提交问题驱动的研究计划，不执行工具。计算输入只引用稳定证据id，缺输入保留方法义务，不填数字或默认口径。'
            elif output_protocol == MATERIAL_EXTRACTION_PROTOCOL and phase == 'extract':
                expected_schema = material_extraction_output_schema(task, schema_context)
                function_name = MATERIAL_EXTRACTION_FUNCTION_NAME
                function_description = '提交实际读取原件的摘录提案，不执行工具、不批准时点、事实或适用性。选择登记引用，不混合不同格式。'
            elif output_protocol == FINANCIAL_EXTRACTION_PROTOCOL and phase == 'extract':
                expected_schema = financial_extraction_output_schema(task, schema_context)
                function_name = FINANCIAL_EXTRACTION_FUNCTION_NAME
                function_description = '选择实际读取财务表格的原值位置，并提交明确口径及逐字依据。不执行工具，不批准事实或财务适用性。'
            elif output_protocol == 'strict_financial_reference_v1' and phase == 'extract':
                from .output_schema import financial_reference_output_schema, FINANCIAL_REFERENCE_FUNCTION_NAME
                expected_schema = financial_reference_output_schema(task, schema_context)
                function_name = FINANCIAL_REFERENCE_FUNCTION_NAME
                function_description = '按本次原件的短编号选择摘录，不执行工具，不批准事实或金融用途。'
            elif output_protocol == 'strict_original_batch_v1' and phase == 'extract':
                from .output_schema import original_batch_output_schema, ORIGINAL_BATCH_FUNCTION_NAME
                expected_schema = original_batch_output_schema(task, schema_context)
                function_name = ORIGINAL_BATCH_FUNCTION_NAME
                function_description = '分别选择本次实际读取的财务单元和原文摘录，不跨原件借用日期、口径或核验结果。'
            elif output_protocol == 'strict_original_spans_v1' and phase == 'extract':
                from .output_schema import original_span_output_schema, ORIGINAL_SPAN_FUNCTION_NAME
                expected_schema = original_span_output_schema(task, schema_context)
                function_name = ORIGINAL_SPAN_FUNCTION_NAME
                function_description = '选择已读原件的明确行范围和数字位置，程序还原原文；不批准事实、日期口径或计算用途。'
            elif output_protocol == 'strict_fee_original_v1' and phase == 'extract':
                from .output_schema import fee_original_output_schema, FEE_ORIGINAL_FUNCTION_NAME
                expected_schema=fee_original_output_schema(task,schema_context)
                function_name=FEE_ORIGINAL_FUNCTION_NAME
                function_description='选择实际费用原文范围、数字及完整计费依据，不执行计算，不批准来源、费率或适用性。'
            elif output_protocol == API_EXTRACTION_PROTOCOL and phase == 'extract':
                expected_schema = api_extraction_output_schema(task, schema_context)
                function_name = API_EXTRACTION_FUNCTION_NAME
                function_description = '提交实际接口单元格的平铺提案，不执行工具、不批准身份、事实、公开时间或计算口径。'
            elif output_protocol == 'strict_calculation_binding_v1' and phase == 'bind_calculations' and schema_context is None:
                from .output_schema import deferred_calculation_output_schema, CALCULATION_BINDING_FUNCTION_NAME
                expected_schema = deferred_calculation_output_schema(task)
                function_name = CALCULATION_BINDING_FUNCTION_NAME
                function_description = '为已登记必要方法绑定当前核验证据id，不取数、不批准计算、不填写新数值或假设。'
            elif output_protocol == 'strict_argument_support_v2' and phase == 'review_support':
                from .argument_support import review_request, review_schema, FUNCTION_NAME as REVIEW_FUNCTION_NAME
                expected_schema = review_schema(review_request(task, schema_context))
                function_name = REVIEW_FUNCTION_NAME
                function_description = '独立核对完整正文与已有依据的支持关系，不取数、不改写提案、不批准金融事实。'
            elif output_protocol == 'strict_argument_reference_v1' and phase == 'review_support':
                from .argument_support import review_request, reference_review_schema, REFERENCE_FUNCTION_NAME
                expected_schema = reference_review_schema(review_request(task,schema_context))
                function_name = REFERENCE_FUNCTION_NAME
                function_description = '按原文单元编号审阅完整提案及必要问题，不重抄正文、不批准来源、不修改判断。'
            else:
                raise ContractError('结构化输出协议或阶段未登记，未派发请求')
            if expected_schema is None or output_schema != expected_schema:
                raise ContractError('结构化输出契约与本任务范围不符，未派发请求')
        elif output_protocol is not None or schema_context is not None:
            raise ContractError('结构化输出缺少本任务契约，未派发请求')
        if not all(config.get(key) for key in ("endpoint", "model", "api_key")):
            raise ContractError("独立模型服务配置不完整")
        reserve = delivery_margin if delivery else task["ledger"]["reserve"]["seconds"]
        long_extraction = (phase == 'extract' and
            sum(len(str(message.get('content', ''))) for message in messages) >= self.LONG_EXTRACTION_CHARACTERS)
        output_limit = self.output_limit(config, phase if requires_argument_support(task) else None)
        reasoned_delivery = output_limit > self.output_limit(config)
        wait_cap = min(self.MAX_WAIT_SECONDS, 180 if long_extraction or planning_deadline or reasoned_delivery
                       else self.ORDINARY_WAIT_SECONDS)
        if phase=='review_support' and requires_argument_support(task) and reasoned_delivery:
            wait_cap=300  # Still bounded below by this task's remaining time and delivery reserve.
        timeout = min(wait_cap, task["ledger"]["limits"]["seconds"] - task["ledger"]["used"]["seconds"] - reserve)
        if timeout <= 0:
            raise ContractError("执行时间预算不足")
        body = {"model": config["model"], "messages": messages,
                "response_format": {"type": "json_object"}, "max_tokens": output_limit}
        responses = ModelSettings.uses_responses(config)
        draft_schema = responses and phase == 'draft_report' and task.get('draft_first_version') == 1
        stream=ModelSettings.supports_deepseek_controls(config) and not responses
        if stream:
            body.update(stream=True,stream_options={'include_usage':True})
        direct_extraction = phase == "extract" and config.get("extraction_mode") == "direct"
        requested_effort = config.get("reasoning_effort", "default")
        planning_effort = config.get('planning_effort', 'inherit') if phase in ('plan', 'acquire') else 'inherit'
        effective_effort = ('none' if direct_extraction else requested_effort
                            if planning_effort == 'inherit' else planning_effort)
        if effective_effort != "default":
            body["thinking"] = {"type": "disabled" if effective_effort == "none" else "enabled"}
            if effective_effort != "none":
                body["reasoning_effort"] = effective_effort
        if output_schema is not None:
            from .output_schema import provider_request_schema
            wire_schema = provider_request_schema(task,output_schema,output_protocol)
            body.pop('response_format')
            body['tools'] = [{'type': 'function', 'function': {'name': function_name, 'strict': True,
                'description': function_description,
                'parameters': wire_schema}}]
            # Named choice is unsupported in thinking mode. Preserve the user's
            # effort and reject a missing submission, rather than disabling it.
            body['tool_choice'] = ({'type': 'function', 'function': {'name': function_name}}
                                   if effective_effort == 'none' else 'auto')
        if responses:
            # A declared transport, never an endpoint rewrite or an automatic
            # retry after a missing optional tool. Retain the selected effort.
            body = {'model': config['model'], 'input': messages, 'stream': False, 'store': False,
                    'max_output_tokens': output_limit,
                    'text': {'format': {'type': 'json_object'}}}
            if effective_effort != 'default':
                body['reasoning'] = {'effort': effective_effort}
            if output_schema is not None:
                body['text']['format'] = {'type': 'json_schema', 'name': function_name,
                    'strict': True, 'schema': wire_schema}
            elif draft_schema:
                from .output_schema import DRAFT_REPORT_SCHEMA_NAME, draft_report_output_schema

                # Ask the provider for the right container. A complete usable
                # draft is still normalized locally even if an optional field is absent.
                body['text']['format'] = {'type': 'json_schema', 'name': DRAFT_REPORT_SCHEMA_NAME,
                                         'strict': True, 'schema': draft_report_output_schema(task)}
        serialized=json.dumps(body).encode()
        input_budget=None
        if phase=='extract' and task.get('model_context_budget_version')==1:
            input_budget=self.documented_input_budget(config)
            if input_budget is not None:
                if output_limit>input_budget['output_token_reserve'] or len(serialized)>input_budget['input_byte_limit']:
                    raise ContractError('完整模型请求超过已核实上下文及输出预留，未派发请求')
            else:
                from .reasoning import MAX_CONTEXT_CHARACTERS
                measured=sum(len(str(message.get('content',''))) for message in messages)
                if output_schema is not None:measured+=len(canonical(wire_schema))
                if measured>MAX_CONTEXT_CHARACTERS:
                    raise ContractError('未知模型的完整输入超过保守上限，未派发请求')
        request = urllib.request.Request(ModelSettings.completion_endpoint(config["endpoint"]), method="POST",
            data=serialized,
            headers={"Content-Type": "application/json"})
        request.add_unredirected_header("Authorization", "Bearer " + config["api_key"])
        charge(task["ledger"], "model_calls", delivery=delivery)
        record = {"kind": "model_calls", "batch": task["batch"], "status": "已派发", "requested_model": config["model"], "time": now(),
                  "timeout_seconds": timeout, "max_output_tokens": output_limit, "stage": "连接服务"}
        if input_budget is not None:
            record['input_budget']=dict(input_budget,request_bytes=len(serialized))
        if stream:
            record.update(response_protocol='deepseek_sse_v1',stream_progress_is_lower_bound=True)
        if responses:
            record.update(completion_api='deepseek_responses_v1',
                          submission_transport='responses_json_schema_v1' if output_schema is not None or draft_schema
                          else 'responses_json_object_v1')
        if long_extraction:
            record.update(wait_policy='bounded_long_extraction_v1', wait_cap_seconds=wait_cap)
        elif planning_deadline:
            record.update(wait_policy='bounded_research_plan_v1', wait_cap_seconds=wait_cap)
        elif reasoned_delivery:
            record.update(wait_policy='bounded_reasoned_delivery_v1', wait_cap_seconds=wait_cap,
                          output_limit_policy='reasoning_and_visible_output_v1')
        if phase is not None:
            record["research_phase"] = phase
        if draft_completion is not None:
            record['draft_call_binding'] = draft_completion[0]
        if output_schema is not None:
            record.update(output_protocol=output_protocol, output_schema_hash=fingerprint(output_schema))
            if (wire_schema.get('$defs')
                    or (type(task.get('structured_schema_sharing_version')) is int and task['structured_schema_sharing_version'] == 1)
                    or (type(task.get('request_schema_sharing_version')) is int and task['request_schema_sharing_version'] == 1)):
                record.update(provider_output_schema_hash=fingerprint(wire_schema),
                              schema_sharing_version=1, schema_definitions_shared=bool(wire_schema.get('$defs')))
        if direct_extraction:
            record["extraction_mode"] = "direct"
        if requested_effort != "default" or planning_effort != 'inherit':
            record.update(requested_reasoning_effort=requested_effort,
                          effective_reasoning_effort=effective_effort)
        if planning_effort != 'inherit':
            record['requested_planning_effort'] = planning_effort
        task["ledger"]["dispatches"].append(record)
        if self.journal:
            self.journal(task)  # spent request is durable before network dispatch
        started = time.monotonic()
        try:
            payload = self._request_bytes(task, request, started, timeout, record)
            record["stage"] = "检查响应"
            if time.monotonic() - started > timeout:
                record["failure_code"] = "response_deadline_exceeded"
                raise TransportFailure("response_deadline_exceeded", "模型响应超过本次时限，不作为有效研究产物")
            if len(payload) > self.MAX_RESPONSE_BYTES:
                record["failure_code"] = "response_too_large"
                raise ModelCompletionError("模型响应超出大小限制，不作为有效研究产物")
            try:
                result = json.loads(payload, object_pairs_hook=unique_json_object)
            except DuplicateJSONField:
                raise
            except (ValueError, RecursionError):
                record["failure_code"] = "invalid_response_json"
                raise ModelCompletionError("模型响应不是有效JSON；已派发用量保留，不自动重试或换模型") from None
            if isinstance(result, dict):
                # Provider usage is meaningful even when its answer is rejected.
                # Never retain private reasoning_content or the raw response.
                try:
                    record.update(effective_model=business_payload(result.get("model"), [config["api_key"]]),
                                  usage=business_payload(result.get("usage", {}), [config["api_key"]]))
                except (ContractError, RecursionError):
                    record["failure_code"] = "invalid_response_shape"
                    raise ModelCompletionError("模型响应的用量或服务标识无法核对，不采用该响应") from None
            if responses:
                response_message, response_finish = self._responses_message(result, record)
                choices = [dict(message=response_message, finish_reason=response_finish)]
            else:
                choices = result.get("choices") if isinstance(result, dict) else None
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict) or not isinstance(choices[0].get("message"), dict):
                record["failure_code"] = "invalid_response_shape"
                raise ModelCompletionError("模型响应结构不完整，不作为有效研究产物")
            choice = choices[0]
            message = choice["message"]
            if "index" in choice and (type(choice["index"]) is not int or choice["index"] != 0):
                record["failure_code"] = "invalid_response_shape"
                raise ModelCompletionError("模型返回了非本次请求的候选，不作为有效研究产物")
            finish = choice.get("finish_reason")
            known_finishes = ("stop", "end_turn", "length", "content_filter", "tool_calls",
                              "function_call", "insufficient_system_resource", "aborted")
            # Old compatible hosts omitted this metadata entirely. Preserve
            # that contract, but do not conflate it with an explicit stop.
            # A present null belongs to an unfinished streaming chunk, not a
            # complete non-streaming answer. Unknown provider text is never
            # echoed or retained as a diagnostic.
            if "finish_reason" not in choice:
                record["finish_reason"] = None
                record["completion_metadata"] = "legacy_missing"
            elif not isinstance(finish, str) or finish not in known_finishes:
                record.update(finish_reason="unrecognized", failure_code="completion_not_confirmed")
                raise ModelCompletionError("模型未提供有效结束状态，不作为有效研究产物")
            else:
                record["finish_reason"] = finish
            if finish == "length":
                record["failure_code"] = "content_truncated"
                raise ModelCompletionError(self.truncated_response_message(config, phase, record))
            if finish in ("insufficient_system_resource", "aborted"):
                record["failure_code"] = "generation_interrupted"
                raise ModelCompletionError("模型生成中断，未采用正文；已派发用量保留，不自动重试")
            if output_schema is not None:
                from .output_schema import StructuredOutputError, structured_completion, structured_json_completion
                try:
                    context = ((task,schema_context) if output_protocol in
                               ('strict_financial_original_v7','strict_financial_reference_v1',
                                'strict_original_batch_v1','strict_original_spans_v1','strict_fee_original_v1','strict_argument_support_v2','strict_argument_reference_v1',
                                'strict_reading_reference_v1','strict_api_extraction_v1') else None)
                    if output_protocol == 'strict_analysis_citations_v2':
                        context = task
                    if responses:
                        text = structured_json_completion(message['content'], output_schema,
                            protocol=output_protocol, context=context)
                    else:
                        text = structured_completion(message, finish, output_schema, protocol=output_protocol,
                            context=context)
                except ContractError as error:
                    record['failure_code'] = 'structured_output_rejected'
                    if isinstance(error, StructuredOutputError):
                        record['structured_failure_category'] = error.category
                        if error.diagnostic:
                            record['structured_output_diagnostic'] = error.diagnostic
                        if finish in ('stop', 'tool_calls') and not message.get('refusal'):
                            raise StructuredCompletionError(str(error)) from None
                    raise ModelCompletionError(str(error)) from None
                if _contains_private_completion(text, config['api_key']):
                    record['failure_code'] = 'private_content'
                    raise ModelCompletionError('结构化分析包含敏感内容，未采用正文')
                record.update(status='成功', stage='已完成')
                return text
            if finish in ("content_filter", "tool_calls", "function_call"):
                record["failure_code"] = "content_not_available"
                raise ModelCompletionError("模型未完成研究正文，不作为有效研究产物")
            if (("role" in message and message["role"] != "assistant")
                    or message.get("tool_calls") not in (None, [])
                    or message.get("function_call") is not None):
                # This JSON-only channel executes no model tool calls. Even a
                # claimed stop cannot turn an unexecuted call into evidence.
                record["failure_code"] = "invalid_completion_message"
                raise ModelCompletionError("模型正文角色或工具调用与本次研究请求不符，未采用正文")
            text = message.get("content")
            if not isinstance(text, str) or not text.strip():
                record["failure_code"] = "empty_content"
                raise ModelCompletionError("模型未返回可处理的正文")
            # Business-response redaction operates on string values, not JSON
            # serialization. Applying it to the entire model text may remove
            # escape characters adjacent to a private URL and corrupt JSON.
            # Reject such content without rewriting it, retaining no raw text.
            if _contains_private_completion(text, config["api_key"], inspect_fields=phase == 'draft_report'):
                record["failure_code"] = "private_content"
                raise ModelCompletionError("模型正文包含敏感内容，未改写或采用；已派发用量保留")
            record.update(status="成功", stage="已完成")
            return text
        except urllib.error.HTTPError as error:
            classifications = {401: ("authentication_failed", "模型认证未通过"),
                               402: ("insufficient_balance", "模型服务余额不足"),
                               403: ("permission_denied", "模型调用权限未通过"),
                               429: ("rate_limited", "模型服务限流")}
            code, message = classifications.get(error.code, ("http_failure", "模型服务HTTP调用失败"))
            record.update(status="失败", failure_code=code, http_status=error.code)
            if error.code == 400 and 'request_failure_category' not in record:
                try:
                    record['request_failure_category'] = request_failure_category(error.read(8193))
                except Exception:
                    record['request_failure_category'] = 'invalid_request_unspecified'
            error.close()
            raise TransportFailure(code, message + "；已派发用量保留，不自动重试或换模型", error.code) from None
        except DuplicateJSONField:
            record.update(status="失败", failure_code="duplicate_response_field")
            raise ModelCompletionError("模型响应包含重复字段，未采用正文") from None
        except ContractError:
            record["status"] = "失败"
            raise
        except (TimeoutError, socket.timeout):
            record.update(status="失败", failure_code="transport_timeout")
            raise TransportFailure("transport_timeout", "模型连接或读取超时；已派发用量保留，不自动重试或换模型") from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                code, message = "transport_timeout", "模型连接或读取超时"
            elif isinstance(error.reason, http.client.IncompleteRead):
                code, message = "response_incomplete", "模型响应正文未完整接收"
            elif isinstance(error.reason, (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError, ssl.SSLEOFError)):
                code, message = "connection_interrupted", "模型连接在响应完成前中断"
            elif isinstance(error.reason, http.client.HTTPException):
                code, message = "http_protocol_failure", "模型HTTP响应协议不完整"
            elif isinstance(error.reason, ssl.SSLError):
                code, message = "tls_failure", "模型服务安全连接未通过"
            else:
                code, message = "connection_failed", "无法连接模型服务"
            record.update(status="失败", failure_code=code)
            raise TransportFailure(code, message + "；已派发用量保留，不自动重试或换模型") from None
        except http.client.IncompleteRead:
            record.update(status="失败", failure_code="response_incomplete")
            raise TransportFailure("response_incomplete", "模型响应正文未完整接收；已派发用量保留，不自动重试或换模型") from None
        except (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError, ssl.SSLEOFError):
            record.update(status="失败", failure_code="connection_interrupted")
            raise TransportFailure("connection_interrupted", "模型连接在响应完成前中断；已派发用量保留，不自动重试或换模型") from None
        except http.client.HTTPException:
            record.update(status="失败", failure_code="http_protocol_failure")
            raise TransportFailure("http_protocol_failure", "模型HTTP响应协议不完整；已派发用量保留，不自动重试或换模型") from None
        except Exception as error:
            record.update(status="失败", failure_code="unexpected_transport_failure")
            # Fixed categories only. Exception messages/args may contain a
            # private URL, provider response or credential and are never kept.
            record["exception_category"] = next((label for kind, label in (
                (OSError, "os_error"), (ValueError, "value_error"), (TypeError, "type_error"),
                (AttributeError, "attribute_error"), (RuntimeError, "runtime_error"))
                if isinstance(error, kind)), "unclassified")
            if isinstance(error, OSError) and type(error.errno) is int and 0 <= error.errno <= 4096:
                record["os_error_number"] = error.errno
            raise ContractError("模型调用失败，已派发用量保留；不自动换模型或恢复预算") from None
        finally:
            elapsed = max(0, time.monotonic() - started)
            record["elapsed_seconds"] = elapsed
            task["ledger"]["used"]["seconds"] += elapsed
            try:
                if draft_completion is not None and record['status'] == '成功':
                    # Persist the accepted visible reply with its charged usage
                    # before returning to an interruptible orchestration step.
                    draft_completion[1](text)
            finally:
                if self.journal:
                    self.journal(task)


def _model_http_worker(connection, deadline):
    """One HTTP request only; no archive, model reasoning or credential files.

    Input and response travel through a private pipe, not command arguments,
    stdout or temporary files. Only fixed error categories cross the boundary.
    """
    record = {}
    def send(value):
        # Cancellation may close the controller pipe at any stage. Never let
        # error reporting create another error or expose a private traceback.
        try:
            connection.send(value)
        except (EOFError, OSError):
            pass
    try:
        spec = connection.recv()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            send({"kind": "failure", "code": "response_deadline_exceeded"})
            return
        request = urllib.request.Request(spec["endpoint"], data=spec["body"], headers=spec["headers"], method="POST")
        for name, value in spec.get("unredirected_headers", {}).items():
            request.add_unredirected_header(name, value)
        send({"kind": "stage", "stage": "连接服务", "network_request_started": True})
        with open_verified_https(request, timeout=remaining, opener=urllib.request.urlopen) as response:
            send({"kind": "stage", "stage": "读取响应"})
            reader = ModelService(None, offline=False)
            body_started = time.monotonic()
            record['response_protocol']=spec.get('response_protocol')
            def progress(metrics):
                send({'kind':'stage','stage':'读取响应','diagnostics':metrics})
            payload = reader._read_provider_response(response, body_started, deadline - body_started, record,progress)
        send({"kind": "response", "body": payload, "record": record})
    except urllib.error.HTTPError as error:
        status = error.code
        if status == 400:
            try:
                # The parent owns the absolute deadline and kills a blocked
                # reader. Only a fixed classification crosses the private pipe.
                diagnostic = {}
                body_started = time.monotonic()
                payload = ModelService(None, offline=False)._read_response(
                    error, body_started, max(0, deadline - body_started), diagnostic)
                record['request_failure_category'] = request_failure_category(payload)
            except Exception:
                record['request_failure_category'] = 'invalid_request_unspecified'
        error.close()
        send({"kind": "failure", "code": "http_failure", "http_status": status, "record": record})
    except ContractError:
        send({"kind": "failure", "code": record.get("failure_code", "connection_failed"), "record": record})
    except (TimeoutError, socket.timeout):
        send({"kind": "failure", "code": "transport_timeout"})
    except urllib.error.URLError as error:
        code = "transport_timeout" if isinstance(error.reason, (TimeoutError, socket.timeout)) else "tls_failure" if isinstance(error.reason, ssl.SSLError) else "connection_failed"
        send({"kind": "failure", "code": code})
    except http.client.IncompleteRead:
        send({"kind": "failure", "code": "response_incomplete", "record": record})
    except (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError, ssl.SSLEOFError):
        send({"kind": "failure", "code": "connection_interrupted"})
    except http.client.HTTPException:
        send({"kind": "failure", "code": "http_protocol_failure"})
    except Exception:
        # Exception text or arguments can contain the request or private URL.
        send({"kind": "failure", "code": "unexpected_transport_failure"})
    finally:
        connection.close()


class IsolatedModelService(ModelService):
    """Production HTTP transport with an interruptible DNS/header/body deadline.

    All planning, budgets, output parsing and research verification still use
    ModelService/ResearchAgent. The child executes exactly one HTTP operation.
    """
    POLL_SECONDS = .1
    CLEANUP_SECONDS = .5
    worker_target = staticmethod(_model_http_worker)

    def __init__(self, settings, offline=True, journal=None, active=None):
        super().__init__(settings, offline=offline, journal=journal)
        self.active = active

    def _request_bytes(self, task, request, started, timeout, record):
        spec = {"endpoint": request.full_url, "body": request.data,
                "headers": dict(request.headers), "unredirected_headers": dict(request.unredirected_hdrs),
                "response_protocol": record.get('response_protocol')}
        last_stage = None

        def checkpoint_stage():
            nonlocal last_stage
            stage = (record["stage"], record.get("network_request_started", False))
            if stage == last_stage:
                return
            last_stage = stage
            record.update(progress_version=1, stage_updated_at=now())
            if self.journal is not None:
                # The engine's existing journal preserves concurrent cancel
                # and version boundaries. Save failures stop the same worker;
                # they do not trigger a new call or reset its original budget.
                self.journal(task)

        try:
            from .model_stream import DIAGNOSTIC_LIMITS
            return run_isolated(task, spec, started, timeout, record, self.worker_target,
                active=self.active, label="模型", poll_seconds=self.POLL_SECONDS,
                cleanup_seconds=self.CLEANUP_SECONDS,
                allowed_stages=frozenset({"连接服务", "读取响应"}), on_stage=checkpoint_stage,
                allowed_stage_diagnostics=DIAGNOSTIC_LIMITS)
        except TransportFailure as error:
            if error.http_status is not None:
                raise urllib.error.HTTPError(request.full_url, error.http_status, "", {}, None) from None
            raise
