"""Source acquisition and extraction; interpretation is owned by the analyzer."""

from __future__ import annotations

import hashlib
import base64
import copy
import io
import http.client
import ipaddress
import json
from itertools import islice, repeat, zip_longest
import math
import re
import socket
import time
import urllib.error
import urllib.request
from .https_transport import open_verified_https, verified_ssl_context
from datetime import timedelta, timezone
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit, urlencode

from .contracts import (ContractError, DOCUMENT_CONTENT_INTENT, DuplicateJSONField, charge, fingerprint,
                        market_date, now, timepoint, unique_json_object, validate_evidence_usage)
from .market_calendar import (REGISTRY as CALENDAR_REQUEST_REGISTRY, request_shapes as calendar_request_shapes,
    validate_request as validate_calendar_request, sessions as calendar_sessions)
from .intraday_quotes import REGISTRY as INTRADAY_REQUEST_REGISTRY
from .financial_sources import (FINANCIAL_REQUEST_REGISTRY, financial_request_shapes, financial_evidence_path,
                                validate_financial_request, validate_financial_snapshot, validate_financial_metric_number,
                                validate_financial_statement_scope, FINANCIAL_METRIC_LABELS,
                                FINANCIAL_QUERY_REGISTRY, financial_query_shapes,
                                validate_financial_query, financial_query_response,
                                IFIND_METADATA_REGISTRY, IFIND_METADATA_OPERATIONS, IFIND_METADATA_BASE,
                                validate_metadata_request, metadata_response, metadata_request_shapes,
                                metadata_wait_seconds, partial_financial_enabled)
from .economic_sources import (ECONOMIC_REQUEST_REGISTRY, economic_request_shapes,
                              validate_economic_request, economic_record_contract)
from .market_series import (SERIES_REQUEST_REGISTRY, series_request_shapes,
                            validate_series_request, series_record_contract)
from .official_sources import REGISTRY as OFFICIAL_REQUEST_REGISTRY
from .etf_sources import REGISTRY as ETF_REQUEST_REGISTRY
from .etf_listing import OPERATION as ETF_LISTING, REGISTRY as ETF_LISTING_REGISTRY
from .etf_trading import REGISTRY as ETF_TRADING_REGISTRY
from .index_sources import (INDEX_REQUEST_REGISTRY, index_request_shapes,
                            validate_index_request, index_response, callable_member_codes)
from .subject_scope import excluded_subjects, scope_question, subject_allowed, subject_key

CAPABILITIES = {
    "ifind": {"state": "真实权限未验收", "transport": "HTTPS", "fields": ["行情", "证券身份", "公告目录与原件"],
              "documented_only": [{"operation": "basic_data_service", "kind": "基础数据"}]},
    "tinyshare": {"state": "真实权限未验收", "transport": "tinyshare", "fields": []},
    # Legacy identifier retained for archive compatibility, not an active adapter.
    "wind": {"state": "本版本不接入", "transport": "SQL", "fields": [], "excluded": True},
    "material": {"state": "本地解析可用", "formats": ["PDF", "TXT", "MD", "DOCX", "HTML"]},
    "official": {"state": "固定官方渠道，逐次核验", "transport": "HTTPS",
                 "fields": ["官方发布及公告目录", "静态HTML原文", "目录绑定的公告PDF"]},
}

IFIND_MANUAL = "https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html"
IFIND_HTTP_REJECTIONS = {401: "authentication_failed", 403: "permission_denied", 429: "rate_limited"}
# HTTP API user manual, error table. These codes describe account-wide usage,
# not an empty response, a missing security or a bad request parameter.
IFIND_QUOTA_ERRORS = {
    "-4301": "本周基础数据额度已用尽",
    "-4302": "本周行情额度已用尽",
    "-4303": "本周宏观数据额度已用尽",
    "-4317": "本周数据额度已用尽",
    "-4318": "本月数据额度已用尽",
}
# Shared literal reading grammar. A cue is not evidence approval: the retained
# original, date binding, negation, temporal precision and cutoff still apply.
# PDF text layers can use Kangxi radicals for these printed calendar markers.
# Match their literal glyphs in place; never normalize stored text or quotes.
LITERAL_MONTH_MARKER = r"[月⽉]"
LITERAL_DAY_MARKER = r"[日⽇]"
LITERAL_RANGE_MARKER = r"[至⾄到]"
PUBLICATION_DATE_PATTERN = (r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|"
    r"\d{4}[ \t\u00a0\u3000]*年[ \t\u00a0\u3000]*\d{1,2}[ \t\u00a0\u3000]*" + LITERAL_MONTH_MARKER +
    r"[ \t\u00a0\u3000]*\d{1,2}[ \t\u00a0\u3000]*" + LITERAL_DAY_MARKER + r")(?!\d)")
PUBLICATION_CUE_RE = re.compile(
    r"发布|公布|披露|公告[日⽇]期|\b(?:published|released|disclosed|"
    r"(?:publication|release|disclosure)\s+date|date\s+of\s+(?:publication|release|disclosure))\b",
    re.IGNORECASE,
)
PUBLICATION_OTHER_DATE_RE = re.compile(
    r"报告期|所属期|统计期|交易[日⽇]|观测[日⽇]|[生⽣]效[日⽇]期|[生⽣]效[日⽇]|实施[日⽇]期|编制[日⽇]期|落款[日⽇]期|截[至⾄]|截止|"
    r"\b(?:report(?:ing)?\s+period|period\s+(?:ending|ended)|as\s+of|effective(?:\s+date)?|"
    r"observation\s+(?:date|day)|trade\s+date|trading\s+day|compiled\s+on|signed\s+on)\b",
    re.IGNORECASE,
)
PUBLICATION_UNCERTAIN_RE = re.compile(
    r"(?:计划|拟|预计|将于|尚未|未曾|并未|未予|未|不得|暂无|不早于|不晚于)|"
    r"\b(?:scheduled|expected|planned|intended|will|may|might|not|never|no)\b",
    re.IGNORECASE,
)
IFIND_IDENTITY_VERSION = 1
IDENTITY_REQUEST_REGISTRY = {
    "ifind.get_thscode": {"version": IFIND_IDENTITY_VERSION, "kind": "单对象精确代码转换",
        "query_modes": ["seccode", "secname"],
        "documentation": "https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/download.html",
        "limitations": "仅核验本任务当前版本的代码映射；不确认证券类型、行业暴露、历史存在或截止时点可得性"},
}
IDENTITY_EXACT_PROFILE = 'rh_ifind_exact_identity_v1'
IDENTITY_CANDIDATE_PROFILE = 'rh_ifind_name_candidates_v1'
IDENTITY_PARAMETERS_BY_MODE = {
    mode: {"mode": mode, "sectype": "", "market": "", "tradestatus": "0", "isexact": "1"}
    for mode in IDENTITY_REQUEST_REGISTRY['ifind.get_thscode']['query_modes']
}


def identity_query_parameters(mode, value, profile=IDENTITY_EXACT_PROFILE):
    """Encode a selected registered profile, never repair a vendor request."""
    if mode not in IDENTITY_PARAMETERS_BY_MODE or not isinstance(value, str):
        raise ContractError('身份查询方式或原始查询值无效')
    if profile not in (IDENTITY_EXACT_PROFILE, IDENTITY_CANDIDATE_PROFILE) or profile == IDENTITY_CANDIDATE_PROFILE and mode != 'secname':
        raise ContractError('身份候选只查询用户原名称，不作模糊代码扫描')
    settings = copy.deepcopy(IDENTITY_PARAMETERS_BY_MODE[mode])
    if profile == IDENTITY_CANDIDATE_PROFILE:
        settings['isexact'] = '0'
    return {mode: value, 'functionpara': settings}


FILING_OPERATIONS = {"report_query", "filing_original"}
IFIND_REPORT_FIELDS = ("reportDate", "thscode", "secName", "ctime", "reportTitle", "pdfURL", "seq")
FILING_ORIGINAL_HOSTS = frozenset({"static.cninfo.com.cn", "www.sse.com.cn", "disc.static.szse.cn"})
FILING_MAX_BYTES = 25 * 1024 * 1024
MATERIAL_EXTRACT_VERSION = "1"


def material_extract_version(name):
    if Path(name).suffix.lower() == '.pdf':
        from .pdf_material import EXTRACT_VERSION
        return EXTRACT_VERSION
    if Path(name).suffix.lower() in (".html", ".htm"):
        from .html_material import EXTRACT_VERSION
        return EXTRACT_VERSION
    return MATERIAL_EXTRACT_VERSION


def material_extract_versions(name):
    if Path(name).suffix.lower() == '.pdf':
        from .pdf_material import TABLE_EXTRACT_VERSIONS
        return ('1', *TABLE_EXTRACT_VERSIONS)
    if Path(name).suffix.lower() in ('.html', '.htm'):
        from .html_material import EXTRACT_VERSIONS
        return EXTRACT_VERSIONS
    return (material_extract_version(name),)
IFIND_RESOURCE_HOST = "ft.10jqka.com.cn"
IFIND_RESOURCE_PATH = "/standardgwapi/api/news_service/pubnote/download/resource"
FILING_REQUEST_REGISTRY = {
    "ifind.report_query": {"version": 3, "kind": "公告目录查询", "markets": ["A股", "港股"], "parameters": {
        "codes": "本任务明确的单只六位SH/SZ或四至五位HK代码", "functionpara": {},
        "beginrDate": "YYYY-MM-DD", "endrDate": "YYYY-MM-DD，不超过截止日期",
        "outputpara": ",".join(field + ":Y" for field in IFIND_REPORT_FIELDS)},
        "functionpara_options": [{}, {"reportType": "901"}],
        "filter_rule": "显式选择不限定类型的空对象，或指定901；不默认强加901，不设置板块扫描参数，不因空结果自动重派。",
        "documentation": IFIND_MANUAL, "permission": "未验收",
        "limitations": "官方手册包含港股公告及缺省按代码查询；本内核仅单证券、两种明确类型过滤和七项明确列名。"
                        "文档支持不证明当前权限或完整覆盖；目录不等于正文，公告日期不等于发布时间"},
    "ifind.filing_original": {"version": 1, "kind": "内部原件获取，不是供应商HTTP端点",
        "parameters": {"catalog_id": "本任务当前版本已留存report_query记录id", "source_id": "候选原字段seq的字符串值"},
        "documentation": IFIND_MANUAL, "permission": "未验收",
        "limitations": "只下载明确选中的一个原件；公共HTTPS或留存目录绑定的iFinD临时授权资源，无重定向，25MB上限；临时授权仅在内存使用，下载和解析不等于两类核验通过"},
}
TINYSHARE_DOCS = {"daily": "https://tushare.pro/document/2?doc_id=27",
                  "fund_daily": "https://tushare.pro/document/2?doc_id=127",
                  "daily_basic": "https://tushare.pro/document/2?doc_id=32"}
# Definitions are documentation evidence, not permission or live-sample proof.
# iFinD volume units are intentionally absent until the actual field definition
# is registered. A familiar-looking field name is not a unit specification.
FIELD_REGISTRY = {
    "ifind.cmd_history_quotation": {"version": 2, "markets": ["A股", "港股"], "frequency": "日",
        "fields": {key: {"raw_unit": "元", "unit": "元", "factor": 1,
                           "definition": "人民币、未复权、缺失不填充的" + label}
                   for key, label in (("open", "开盘价"), ("high", "最高价"), ("low", "最低价"), ("close", "收盘价"))},
        "documentation": IFIND_MANUAL, "state": "文档支持但未核验", "permission": "未验收",
        "reviewed_on": "2026-10-13", "live_samples": [],
        "limitations": "按登记代码命名空间逐表区分货币价格与指数点位；不是证券身份、上市有效期、指数版本或账号权限证明。"
                       "字段权限、代码映射和首次可得时间须逐次核验，未登记命名空间只保留原响应。"},
}
# OH uses per-asset conventions rather than one unit for an entire query.
# Copy that separation, not its runtime, account state or pricing assumptions.
# A namespace constrains quote units; it does not approve a security identity.
IFIND_QUOTE_FUNCTION = {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}
FIELD_REGISTRY["ifind.cmd_history_quotation"].update(
    raw_observation_fields={"volume": "成交量", "amount": "成交额"},
    raw_observation_rule="保留接口原值；成交量单位及成交额币种、倍率未核验，不换算或作为计算输入。",
    instrument_namespaces={
        "index_level": r"(?:000\d{3}\.SH|399\d{3}\.SZ)",
        "price": r"(?:(?:600|601|603|605|688|510|511|512|513|515|516|517|518|520|530|560|561|562|563|565|566|567|588|589)\d{3}\.SH|(?:000|001|002|003|300|301|158|159)\d{3}\.SZ|\d{4,5}\.HK)",
    },
    namespace_documents=[
        "https://www.sse.com.cn/market/sseindex/indexlist/index.shtml?classid=010202",
        "https://www.szse.cn/marketServices/technicalservice/notice/t20230825_602945.html",
        "https://www.sse.com.cn/lawandrules/guide/stock/jyglywznylc/zn/c/c_20260831_10830435.shtml",
        "https://etf.sse.com.cn/fundlist/",
    ],
    field_families={
        "price": copy.deepcopy(FIELD_REGISTRY["ifind.cmd_history_quotation"]["fields"]),
        "index_level": {key: {"raw_unit": "点", "unit": "点", "factor": 1,
                              "definition": "指数" + label + "点位，缺失不填充；指数版本、计价币种和基期须另有原件依据"}
                        for key, label in (("open", "开盘"), ("high", "最高"), ("low", "最低"), ("close", "收盘"))},
    },
)
for _operation in ("daily", "fund_daily"):
    FIELD_REGISTRY["tinyshare." + _operation] = {"version": 1, "markets": ["A股"], "frequency": "日",
        "fields": {**{key: {"raw_unit": "元", "unit": "元", "factor": 1, "definition": label}
                    for key, label in (("open", "开盘价"), ("high", "最高价"), ("low", "最低价"), ("close", "收盘价"))},
                   "pct_chg": {"raw_unit": "%", "unit": "%", "factor": 1, "definition": "接口披露涨跌幅，不重算为其他口径"},
                   "vol": {"raw_unit": "手", "unit": "手", "factor": 1,
                           "definition": "接口披露成交量，保留原单位，不推定每手证券数量"},
                   "amount": {"raw_unit": "千元", "unit": "元", "factor": 1000, "definition": "成交额"}},
        "documentation": TINYSHARE_DOCS[_operation], "state": "文档支持但未核验", "permission": "未验收",
        "reviewed_on": "2026-10-04", "live_samples": [], "limitations": "兼容API文档，不证明Tinyshare服务权限或历史版本可恢复"}
FIELD_REGISTRY["tinyshare.daily_basic"] = {
    "version": 1, "markets": ["A股"], "frequency": "日",
    "fields": {
        "pe": {"raw_unit": "倍", "unit": "倍", "factor": 1, "definition": "市盈率，总市值除以净利润；亏损时为空，缺失不能填零"},
        "pe_ttm": {"raw_unit": "倍", "unit": "倍", "factor": 1, "definition": "滚动市盈率，总市值除以滚动净利润；亏损时为空，缺失不能填零"},
        "pb": {"raw_unit": "倍", "unit": "倍", "factor": 1, "definition": "市净率，总市值除以净资产减其他权益工具后的余额"},
        "ps": {"raw_unit": "倍", "unit": "倍", "factor": 1, "definition": "市销率，保留接口披露口径，不反推营业收入"},
        "ps_ttm": {"raw_unit": "倍", "unit": "倍", "factor": 1, "definition": "滚动市销率，保留接口披露口径，不反推营业收入"},
        "volume_ratio": {"raw_unit": "倍", "unit": "倍", "factor": 1, "definition": "量比，接口披露的相对成交量指标，不代替成交量原值"},
        **{key: {"raw_unit": "%", "unit": "%", "factor": 1, "definition": label}
           for key, label in (("turnover_rate", "换手率"), ("turnover_rate_f", "自由流通股换手率"),
                              ("dv_ratio", "股息率"), ("dv_ttm", "滚动股息率"))},
        **{key: {"raw_unit": "万股", "unit": "股", "factor": 10000, "definition": label}
           for key, label in (("total_share", "总股本"), ("float_share", "流通股本"), ("free_share", "自由流通股本"))},
        **{key: {"raw_unit": "万元", "unit": "元", "factor": 10000, "definition": label}
           for key, label in (("total_mv", "总市值"), ("circ_mv", "流通市值"))},
    },
    "documentation": TINYSHARE_DOCS["daily_basic"], "state": "文档支持但未核验", "permission": "未验收",
    "reviewed_on": "2026-10-04", "live_samples": [],
    "limitations": "仅作指定证券和观测日的市场背景；兼容API文档不证明Tinyshare权限或历史版本可恢复。"
                   "估值倍数及股息率不是买入信号；亏损、负净资产和缺失值须保留限制，不反推财报或补零。"
                   "交易日不是首次公开时间，历史财务修订与当时可得性须另核验。",
}
CAPABILITIES["tinyshare"]["fields"] = ["A股日线", "ETF日线", "A股每日市场背景指标"]
CAPABILITIES["tinyshare"]["fields"].append("A股财报原始记录，单位与历史版本待核验")
CAPABILITIES["ifind"]["fields"].append("宏观与行业原始序列，指标与单位待核验")
CAPABILITIES["ifind"]["fields"].append("OH同源估值与ETF原始日期序列，单位与历史版本待核验")
CAPABILITIES["ifind"]["fields"].append("单证券财务原始语义查询，逐项核对实际返回字段，不直接批准数字")
FIELD_REGISTRY['official.etf_pcf'] = copy.deepcopy(ETF_REQUEST_REGISTRY['official.etf_pcf'])
CAPABILITIES['official']['fields'].append('目录绑定的完整指数样本XLS，日期及权重单位另列')
from .index_sources import WEIGHT_FIELD
FIELD_REGISTRY['official.index_file']=dict(version=1,fields={'weight':WEIGHT_FIELD},frequency='日',
    kind='官方指数成分权重',documentation='https://www.csindex.com.cn/#/indices/family/detail?indexCode=000300',
    limitations='仅登记当前官方XLS的百分比权重，仍须实际取数、目录、原件与逐行核验；不是价格或基金持仓。')
from .event_market import (INDEX_CODE_PATTERN as HK_EVENT_INDEX_PATTERN, MAINLAND_OPERATION as MAINLAND_EVENT_HISTORY,
                           OPERATION as HK_EVENT_HISTORY)
from .treasury_rates import FIELD as H15_YIELD_FIELD, METHOD_SOURCE as H15_METHOD_SOURCE, OPERATION as H15_TREASURY_YIELDS
from .lpr_rates import FIELD as LPR_FIELD, HISTORY_PAGE as LPR_PAGE, OPERATION as LPR_HISTORY
from .nfra_bank_statistics import (CATALOGUE_PAGE as NFRA_BANK_PAGE, FIELD as NFRA_BANK_FIELD,
                                   OPERATION as NFRA_BANK_STATISTICS)
FIELD_REGISTRY['official.'+HK_EVENT_HISTORY] = dict(version=1, frequency='日', markets=['港股'],
    fields=dict(close=dict(raw_unit='点', unit='点', factor=1, definition='价格指数日收盘点位')),
    kind='恒生指数收盘历史', documentation='https://www.hsi.com.hk/en-hk/indexes/all-indexes/index-details/?seriesCode=hstech',
    limitations='限已定位政策声明的港股价格指数窗口。仅用于事后对照，未核验因果或历史首次可得时点。')
FIELD_REGISTRY['official.'+MAINLAND_EVENT_HISTORY] = dict(version=1, frequency='日', markets=['A股'],
    fields=dict(close=dict(raw_unit='点', unit='点', factor=1, definition='价格指数日收盘点位')),
    kind='沪深交易所事件窗口指数行情', documentation='https://www.sse.com.cn/market/price/trends/',
    limitations='限已核对政策声明的境内价格指数窗口，按各指数原交易日保留收盘值。')
FIELD_REGISTRY['official.'+H15_TREASURY_YIELDS] = dict(version=1, frequency='日', markets=['美国国债'],
    fields={H15_YIELD_FIELD: dict(raw_unit='%', unit='%', factor=1, definition='名义固定期限国债收益率')},
    kind='H.15美债固定期限收益率', documentation=H15_METHOD_SOURCE,
    limitations='以已核对的美东政策日期与所需期限为限，比较日度名义收益率。')
FIELD_REGISTRY['official.'+LPR_HISTORY] = dict(version=1, frequency='月', markets=['中国'],
    fields={LPR_FIELD: dict(raw_unit='%', unit='%', factor=1, definition='1年期及5年期以上LPR报价')},
    kind='全国银行间同业拆借中心LPR历史', documentation=LPR_PAGE,
    limitations='发布日及两期限官方报价，个别合同和银行执行利率须另有依据。')
FIELD_REGISTRY['official.'+NFRA_BANK_STATISTICS] = dict(version=1, frequency='季', markets=['中国'],
    fields={NFRA_BANK_FIELD: dict(raw_unit='小数', unit='%', factor=100, definition='商业银行合计净息差')},
    kind='商业银行主要监管指标表', documentation=NFRA_BANK_PAGE,
    limitations='核对当期披露的商业银行合计季报表，区分公告日与统计期。')

# One public, secret-free inventory for App, Skill and generated demo.
# A documented provider tool is not callable until its request contract is
# implemented. Registered fields do not approve any particular source value.
for _capability in CAPABILITIES.values():
    _capability["registry_version"] = 1
    _capability["registered_operations"] = []
for _registry, _category in ((FIELD_REGISTRY, "字段"), (IDENTITY_REQUEST_REGISTRY, "身份"),
                              (FILING_REQUEST_REGISTRY, "原件"), (FINANCIAL_REQUEST_REGISTRY, "财报"),
                              (ECONOMIC_REQUEST_REGISTRY, "宏观"), (SERIES_REQUEST_REGISTRY, "研究序列"),
                              (INDEX_REQUEST_REGISTRY, "指数成分原始名单"),
                              (FINANCIAL_QUERY_REGISTRY, "财报语义原始记录"),
                              (IFIND_METADATA_REGISTRY, "字段与参数目录及财务原始截面"),
                              (CALENDAR_REQUEST_REGISTRY, "独立交易日历"),
                              (INTRADAY_REQUEST_REGISTRY, "盘中行情"),
                              (OFFICIAL_REQUEST_REGISTRY, "固定官方原文"),
                              (ETF_LISTING_REGISTRY, "ETF证券名录"),
                              (ETF_TRADING_REGISTRY, "ETF二级市场与单位净值")):
    for _qualified, _definition in _registry.items():
        if _qualified=='official.index_file' and _registry is INDEX_REQUEST_REGISTRY:
            continue  # Retrieval contract and numeric field share one inventory entry.
        _source, _operation = _qualified.split(".", 1)
        _kind = _definition.get("kind") or ("财报原始记录" if _category == "财报" else
            "市场背景指标" if _operation == "daily_basic" else
            "ETF日行情" if _operation == "fund_daily" else "日行情")
        CAPABILITIES[_source]["registered_operations"].append({"operation": _operation, "kind": _kind,
            "version": _definition["version"], "raw_only": bool(_definition.get("raw_only", False)),
            "internal": _source == "ifind" and _operation == "filing_original",
            "fields": sorted(_definition.get("fields", {}))})


# Executable endpoint names and documentation-only labels derive from the same
# registered operations. This describes adapter support, never live permission.
for _capability in CAPABILITIES.values():
    _operations = _capability['registered_operations']
    if _operations:
        _capability['endpoints'] = [item['operation'] for item in _operations if not item['internal']]
    if 'documented_only' in _capability:
        _implemented = {item['operation'] for item in _operations}
        _capability['documented_only'] = [item for item in _capability['documented_only']
                                        if item['operation'] not in _implemented]


def active_source_capabilities():
    """Return the public inventory without providers excluded from this version."""
    return copy.deepcopy({source: capability for source, capability in CAPABILITIES.items()
                          if not capability.get("excluded", False)})


def acquisition_channels(task=None):
    """Fixed implemented routes, not a promise of supplier permissions.

    Numerical sources remain iFinD-first. Independent official directories
    are task-versioned; an allowlisted download host alone is not a search adapter.
    """
    policy = {"version": 2, "default_source": "ifind", "automatic_fallback": False,
        "channels": [
            {"id": "ifind", "kind": "structured_data", "enabled_by_default": True,
             "operations": [tool["operation"] for tool in CAPABILITIES["ifind"]["registered_operations"]],
             "permission": "按独立配置和每次实际响应核验"},
            {"id": "disclosure_original", "kind": "original_document", "enabled_by_default": True,
             "source": "ifind", "operation": "filing_original", "requires_retained_catalogue": True,
             "public_hosts": sorted(FILING_ORIGINAL_HOSTS), "vendor_resource_host": IFIND_RESOURCE_HOST,
             "arbitrary_url_allowed": False, "redirect_allowed": False},
            {"id": "user_material", "kind": "local_original", "enabled_by_default": True,
             "network": False, "formats": copy.deepcopy(CAPABILITIES["material"]["formats"])},
            {"id": "official", "kind": "fixed_official_original", "enabled_by_default": True,
             "registered_host": "www.stats.gov.cn", "selection_reason_required": True,
             "credential_required": False, "automatic_fallback": False},
            {"id": "tinyshare", "kind": "optional_structured_data", "enabled_by_default": False,
             "operations": [tool["operation"] for tool in CAPABILITIES["tinyshare"]["registered_operations"]],
             "selection_reason_required": True, "permission": "独立权限，不因iFinD失败自动启用"}],
        "download_policy": {"question_binding_required": True, "selection_reason_required": True,
            "whole_catalogue_download_by_default": False, "same_original_reused": True,
            "unknown_publication_is_download_blocker": False, "download_is_evidence_approval": False},
        "unregistered_channels": "未登记抓取适配的官方站点只能作为待接入渠道或用户提供原件，不声称已自动接通"}
    if task is not None and task.get('official_channels_version')!=1:
        policy['channels']=[channel for channel in policy['channels'] if channel['id']!='official']
        policy['version']=1
    elif task is None or task.get('official_policy_channels_version')==1:
        channel=next(item for item in policy['channels'] if item['id']=='official')
        channel['registered_hosts']=['www.stats.gov.cn','www.federalreserve.gov']
        channel['registered_channels']=['nbs_release','fed_fomc']
    if (task is None or task.get('official_source_expansion_version') == 1) and any(
            item['id']=='official' for item in policy['channels']):
        from .official_sources import POLICY_CHANNELS
        from .miit_releases import CHANNELS as MIIT_CHANNELS, POLICY as MIIT_POLICY
        from .announcement_sources import CHANNELS as ANNOUNCEMENT_CHANNELS
        channel=next(item for item in policy['channels'] if item['id']=='official')
        channel.setdefault('registered_channels', ['nbs_release']).extend([*POLICY_CHANNELS, *ANNOUNCEMENT_CHANNELS])
        channel['registered_hosts']=sorted(set(channel.get('registered_hosts', ['www.stats.gov.cn'])+[
            'www.pbc.gov.cn', '*.mof.gov.cn', 'www.cninfo.com.cn', 'static.cninfo.com.cn',
            'query.sse.com.cn', 'www.sse.com.cn', 'www1.hkexnews.hk']))
        if task is None or task.get(MIIT_POLICY) == 1:
            channel['registered_channels'].extend(MIIT_CHANNELS)
            channel['registered_hosts'].append('www.miit.gov.cn')
        channel['announcement_query_fields']=['code','start_date','end_date','page','keyword']
        channel['directory_is_body']=False
    return policy


def security_code_mentions(text, *, encoding=1):
    """Literal complete codes in user order, using the task's token grammar.

    Token recognition grants neither provider identity nor instrument type.
    Reuse this lexer in conversation scope so an ETF label or following year
    cannot make a follow-up inherit a different security.
    """
    return [match[1] for match in security_code_matches(text, encoding=encoding)]


def security_code_matches(text, *, encoding=1):
    """Keep token spans for mixed security and indicator references."""
    if type(encoding) is not int or encoding not in (1, 2):
        raise ContractError('用户证券代码识别版本未登记')
    if not isinstance(text, str):
        return ()
    if encoding == 1:
        return re.finditer(r'(?<![A-Za-z0-9])(\d{4,6}\.(?:SH|SZ|HK))(?![A-Za-z0-9])', text)
    code = r'(?:\d{6}\.(?:SH|SZ)|\d{4,5}\.HK)'
    # Both are lexical boundaries, not normalization or identity approval.
    left = r'(?:(?<![A-Za-z0-9_])|(?<![A-Za-z_])(?i:ETF))'
    right = r'(?:(?![A-Za-z0-9_])|(?=\d{4}年))'
    return re.finditer(left + '(' + code + ')' + right, text)


def declared_security_codes(task, *, encoding=None):
    """Read complete codes and exact names admitted by the registered market rule.

    Archived tasks retain their own token version; callers requesting a new
    message's scope explicitly select that new task's version.
    """
    from .market_calendar import named_index_point_subjects

    question = scope_question(task)
    registered = named_index_point_subjects(task, question) if encoding is None else {}
    encoding = task.get('security_declaration_encoding', 1) if encoding is None else encoding
    # Validate even a task with no readable text, as before.
    security_code_mentions('', encoding=encoding)
    return sorted(set(registered) | {code for text in [question, *task.get('objects', [])]
                  for code in security_code_mentions(text, encoding=encoding) if subject_allowed(task, code)})


def identity_binding(task):
    return {"task_id": task["id"], "research_version": task["version"],
            "input_hash": task["input_hash"], "cutoff": task["cutoff"]}


def identity_scope(task):
    """Stable user scope, independent of supplement-plan revisions."""
    scope = {key: task[key] for key in ("question", "entry", "intent", "objects", "cutoff")}
    if 'subject_scope' in task:
        scope['subject_scope'] = task['subject_scope']
    return {"task_id": task["id"], "research_version": task["version"],
            "scope_hash": fingerprint(scope)}


def identity_candidate_lookup_enabled(task):
    return type(task.get('identity_candidate_lookup_version')) is int and task['identity_candidate_lookup_version'] in (1,2)


def _identity_name_in_scope(task, query):
    """Match continued names literally and allow names in the current question."""
    context = task.get('conversation_context') or {}
    inherited = context.get('security_name_queries', ())
    query = query.casefold()
    if not subject_allowed(task, query):
        return False
    if query in scope_question(task).casefold():
        return True
    return any(query == text.casefold() if text in inherited else query in text.casefold()
               for text in task.get('objects', ()))


def identity_candidates(task):
    """Retained names authorize bounded lookup, never financial identity approval.

    Comma-separated codes are an observed vendor cell format, not a market
    choice. Keep every returned market, including unsupported ones; never turn
    a single supported code into proof that the original name is unambiguous.
    """
    if not identity_candidate_lookup_enabled(task):
        return []
    from .planning import input_fingerprint
    if task.get('input_hash') != input_fingerprint(task):
        return []
    result = []
    for record in task.get('source_records', []):
        if record.get('source') != 'ifind' or record.get('operation') != 'get_thscode':
            continue
        params = record.get('parameters', {})
        query = params.get('secname')
        if (not isinstance(query, str) or not 2 <= len(query) <= 80
                or query != query.strip() or any(char in query for char in ',;\n\r')
                or params != identity_query_parameters('secname', query, IDENTITY_CANDIDATE_PROFILE)
                or not _identity_name_in_scope(task, query)):
            continue
        origin = record.get('identity_origin', {})
        expected_id = 'rh_source_' + fingerprint([task['id'],task['version'],record.get('request_hash'),record.get('hash')])[:24]
        if (not isinstance(origin, dict) or any(origin.get(key) != value for key,value in identity_scope(task).items())
                or origin.get('input_hash') != task['input_hash'] or record.get('id') != expected_id
                or current_query_reference(task, record) is None):
            continue
        payload = record['snapshot']
        inputs, rows = payload.get('inputParams'), payload.get('tables')
        if (type(payload.get('errorcode')) is bool or payload.get('errorcode') not in (0,'0')
                or not isinstance(inputs, dict) or inputs.get('mode') != 'secname'
                or 'isexact' in inputs and (type(inputs['isexact']) is not int or inputs['isexact'] != 0)
                or not isinstance(rows, list) or not 1 <= len(rows) <= 50):
            continue
        codes, valid = [], True
        for row in rows:
            values = row.get('table', {}).get('thscode') if isinstance(row, dict) and isinstance(row.get('table'), dict) else None
            if not isinstance(row, dict) or row.get('secname') != query or not isinstance(values, list) or len(values) > 100:
                valid = False; break
            for value in values:
                if not isinstance(value, str):
                    valid = False; break
                tokens = value.split(',')
                if any(re.fullmatch(r'[A-Z0-9][A-Z0-9._-]{1,39}', code) is None for code in tokens):
                    valid = False; break
                codes.extend(tokens)
                if len(codes) > 100:
                    valid = False; break
            if not valid: break
        if not valid:
            continue
        all_codes = sorted(set(codes))
        supported = [code for code in all_codes if re.fullmatch(r'(?:\d{6}\.(?:SH|SZ)|\d{4,5}\.HK)',code)]
        result.append(dict(artifact_id=record['id'],query=query,snapshot_hash=record['hash'],
            codes=all_codes,exact_lookup_codes=supported,unsupported_codes=[code for code in all_codes if code not in supported],
            identity_approved=False,market_selected=False,business_lookup_authorized=task['identity_candidate_lookup_version']==2,
            limitation=('可显式选择实际完整代码取得后续原始资料；不自动选择A/H股，不批准主体、上市有效期、行业或金融适用性。'
                if task['identity_candidate_lookup_version']==2 else
                '只供显式选择实际候选的原号码精确查询；不改用户代码、不自动选择A/H股，不批准身份或行业。')))
    return result


def identity_resolutions(task):
    """Derive mappings from retained exact responses, never persisted approval flags.

    Only the explicit row/column schema observed in both real exact queries is
    registered. One code does not establish security type or historical identity.
    A stale, ambiguous or conflicting response never widens the callable scope.
    """
    results = []
    candidates = {item['artifact_id']:item for item in identity_candidates(task)}
    for record in task.get("source_records", []):
        if record.get("source") != "ifind" or record.get("operation") != "get_thscode":
            continue
        result = {"artifact_id": record.get("id"), "status": "待核验", "codes": []}
        if record.get('parameters', {}).get('functionpara', {}).get('isexact') == '0':
            candidate = candidates.get(record.get('id'))
            if candidate:
                result.update(status='候选待确定',query=candidate['query'],mode='secname',
                    candidate_codes=candidate['codes'],reason=candidate['limitation'])
            else:
                result['reason']='候选查询尚未满足当前任务、响应及实际请求绑定，不能选取代码'
            results.append(result)
            continue
        try:
            query, mode = validate_ifind_identity_request(task, record.get("parameters"))
            result.update(query=query, mode=mode)
            digest = fingerprint(record.get("snapshot"))
            request_hash = fingerprint({"source": "ifind", "operation": "get_thscode", "parameters": record["parameters"]})
            expected_id = "rh_source_" + fingerprint([task["id"], task["version"], request_hash, digest])[:24]
            from .planning import input_fingerprint
            origin = record.get("identity_origin", {})
            if (not isinstance(origin, dict) or record.get("kind") != "api"
                    or {key: origin.get(key) for key in identity_scope(task)} != identity_scope(task)
                    or not isinstance(origin.get("input_hash"), str) or not re.fullmatch(r"[a-f0-9]{64}", origin["input_hash"])
                    or task.get("input_hash") != input_fingerprint(task)
                    or record.get("task_id") != task["id"] or record.get("research_version") != task["version"]
                    or record.get("hash") != digest or record.get("request_hash") != request_hash
                    or record.get("id") != expected_id):
                raise ContractError("代码转换原始响应或任务版本绑定不一致")
            payload = record["snapshot"]
            if not isinstance(payload, dict) or isinstance(payload.get("errorcode"), bool) or payload.get("errorcode") not in (0, "0"):
                raise ContractError("代码转换业务未成功")
            inputs = payload.get("inputParams")
            if (not isinstance(inputs, dict) or inputs.get("mode") != mode
                    or type(inputs.get("isexact")) is not int or inputs["isexact"] != 1):
                raise ContractError("代码转换响应未确认本次精确查询模式")
            rows = payload.get("tables")
            if not isinstance(rows, list) or len(rows) > 50:
                raise ContractError("代码转换响应结构未登记或超过单对象限额")
            codes = []
            for row in rows:
                if not isinstance(row, dict) or row.get(mode) != query or not isinstance(row.get("table"), dict):
                    raise ContractError("代码转换响应对象与查询不一致")
                values = row["table"].get("thscode")
                if not isinstance(values, list) or len(values) > 100:
                    raise ContractError("代码转换缺少明确thscode列，不按位置或别名猜测")
                for code in values:
                    if not isinstance(code, str) or not re.fullmatch(r"(?:\d{6}\.(?:SH|SZ)|\d{4,5}\.HK)", code):
                        raise ContractError("代码转换包含未登记市场或非明确代码")
                    hk_padding_alias = (type(task.get('hk_code_alias_version')) is int
                        and task['hk_code_alias_version'] == 1 and code.endswith('.HK')
                        and re.fullmatch(r'\d{4,5}', query) is not None
                        and int(code.split('.')[0]) == int(query)
                        and any(declared.endswith('.HK') and int(declared.split('.')[0]) == int(query)
                                for declared in declared_security_codes(task)))
                    if mode == "seccode" and code.split(".")[0] != query and not hk_padding_alias:
                        raise ContractError("代码转换返回的证券号码与查询不一致")
                    codes.append(code)
            codes = sorted(set(codes))
            if len(codes) != 1:
                result.update(status="多结果待消歧" if codes else "无匹配", codes=codes,
                              reason="不自动选取候选证券或猜测映射")
            else:
                result.update(status="代码映射已核验", codes=codes, snapshot_hash=digest,
                              binding=identity_binding(task), version=IFIND_IDENTITY_VERSION,
                              acquired_binding=copy.deepcopy(origin),
                              historical_identity_verified=False, security_type_verified=False,
                              limitation=IDENTITY_REQUEST_REGISTRY["ifind.get_thscode"]["limitations"])
        except (ContractError, KeyError, TypeError, ValueError) as error:
            result["reason"] = str(error) if isinstance(error, ContractError) else "代码转换记录结构无效"
        results.append(result)
    # Separate requests for the same input must agree; a fresh conflicting
    # response cannot silently supersede the earlier retained mapping.
    conflicts = {(r.get("mode"), r.get("query")) for r in results if any(
        (peer.get("mode"), peer.get("query")) == (r.get("mode"), r.get("query"))
        and peer["status"] in ("多结果待消歧", "代码映射已核验")
        and r["status"] in ("多结果待消歧", "代码映射已核验") and peer["codes"] != r["codes"] for peer in results)}
    for item in results:
        if item["status"] == "代码映射已核验" and (item.get("mode"), item.get("query")) in conflicts:
            item.update(status="映射冲突待核验", reason="同一输入的留存响应不一致，不采用任一映射")
    return results


def retained_identity_queries(task):
    """Current query responses, including no-match results, are reusable once.

    Raw responses inherited from another research version stay readable but
    cannot suppress this version's lookup. A reusable query is not a uniquely
    resolved security or permission to use it in a financial judgment.
    """
    reusable={'代码映射已核验','候选待确定','无匹配','多结果待消歧','映射冲突待核验'}
    return [row for row in identity_resolutions(task) if row['status'] in reusable]


IDENTITY_PERIOD_BASIS_VERSION = 1
_IDENTITY_STATEMENT = re.compile(
    r"(?:供应商|provider)\s*[:：]\s*((?i:ifind|tinyshare))\s*[;；]\s*"
    r"(?:供应商代码|provider_code)\s*[:：]\s*(\d{4,6}\.(?:SH|SZ|HK))\s*[;；]\s*"
    r"(?:研究对象|object)\s*[:：]\s*([^;；\n]+?)\s*[;；]\s*"
    r"(?:映射有效期起点|valid_from)\s*[:：]\s*(\d{4}-\d{2}-\d{2})\s*[;；]\s*"
    r"(?:映射有效期终点|valid_to)\s*[:：]\s*(\d{4}-\d{2}-\d{2})[。.]?"
)


def identity_period_statement(text):
    """Registered explicit daily mapping statement, not dates in prose.

    Start/end roles are mandatory. Listing dates, observation dates, partial
    quotes, future estimates and general code-conversion responses do not
    become effective periods. Unsupported wording stays unverified.
    """
    match = _IDENTITY_STATEMENT.fullmatch(text.strip()) if isinstance(text, str) else None
    if match is None:
        raise ContractError("身份依据不是已登记的完整代码映射及有效期声明")
    provider, code, subject, start, end = match.groups()
    timepoint(start)
    timepoint(end)
    if start > end:
        raise ContractError("身份映射有效期起点晚于终点")
    return {"source": provider.lower(), "provider_code": code, "object": subject,
            "valid_from": start, "valid_to": end}


def validate_identity_period_basis(item, task):
    """A model declaration cannot mint an identity-period source receipt."""
    mapping = item.get("identity", {})
    reference = mapping.get("basis_ref") if isinstance(mapping, dict) else None
    if (not isinstance(reference, dict) or set(reference) != {"version", "evidence_id", "evidence_hash", "content_version"}
            or type(reference.get("version")) is not int or reference["version"] != IDENTITY_PERIOD_BASIS_VERSION):
        raise ContractError("证券身份有效期缺少经核验的同版来源引用，不能填入猜测日期")
    proofs = [proof for proof in task.get("evidence", []) if proof.get("id") == reference["evidence_id"]]
    if len(proofs) != 1:
        raise ContractError("身份有效期依据未在本任务唯一登记")
    proof = proofs[0]
    original = _unique_artifact(task.get("materials", []) + task.get("source_records", []),
                                _evidence_artifact_id(proof))
    if (proof.get("task_id") != task["id"] or proof.get("research_version") != task["version"]
            or proof.get("source_check") != "通过" or proof.get("applicability_check") != "通过"
            or proof.get("nature") != "已实现事实"
            or proof.get("artifact", {}).get("kind") not in ("material", "filing")
            or not original or original.get("kind") != proof["artifact"]["kind"]):
        raise ContractError("身份有效期须引用同任务同版且两类核验通过的原件事实")
    if (type(reference.get("content_version")) is not int
            or reference["content_version"] != proof.get("content_version")
            or reference["evidence_hash"] != evidence_binding(proof)):
        raise ContractError("身份有效期依据内容已改变")
    # Material-only witnesses make recursion bounded: an API observation is
    # never its own mapping-period proof, even if its code happens to match.
    validate_provenance(proof, task)
    material = original["snapshot"].get("material") if original["kind"] == "filing" else original["snapshot"]
    text = next((part["text"] for part in material.get("parts", [])
                 if part.get("locator") == proof.get("record_locator", {}).get("part")), "")
    if text.strip() != str(proof.get("text", "")).strip():
        raise ContractError("身份映射须为所定位的完整独立页段，不能裁剪否定或限定语句")
    values = identity_period_statement(proof.get("text"))
    if (mapping.get("basis") != proof.get("text") or values["source"] != item.get("source")
            or proof.get("object") != values["object"]
            or any(mapping.get(field) != values[field] for field in ("provider_code", "object", "valid_from", "valid_to"))):
        raise ContractError("证券身份、供应商或有效期与所引用原文不一致")
    return proof


def identity_period_basis_contract(task):
    """Copy exact admissible witnesses; never ask the model to invent hashes."""
    candidates = []
    for proof in task.get("evidence", []):
        try:
            values = identity_period_statement(proof.get("text"))
            mapping = {key: values[key] for key in ("provider_code", "object", "valid_from", "valid_to")}
            mapping.update(basis=proof["text"], basis_ref={"version": IDENTITY_PERIOD_BASIS_VERSION,
                "evidence_id": proof["id"], "evidence_hash": evidence_binding(proof), "content_version": proof["content_version"]})
            validate_identity_period_basis({"identity": mapping, "source": values["source"]}, task)
            candidates.append({"source": values["source"], "identity": mapping})
        except (ContractError, KeyError, TypeError):
            continue
    return {"version": IDENTITY_PERIOD_BASIS_VERSION, "admissible_bases": candidates,
        "basis_ref_fields": ["version", "evidence_id", "evidence_hash", "content_version"],
        "statement_format": "完整独立页段：供应商：来源；供应商代码：完整代码；研究对象：对象；映射有效期起点：YYYY-MM-DD；映射有效期终点：YYYY-MM-DD",
        "rule": "仅复制经两类核验的原件声明及完整引用；哈希由内核提供。先核验映射原件，再提取依赖它的API事实。",
        "no_basis": "有效期映射无适用引用时保留缺口；请求窗口、行情日期、上市日和下载时间均不是映射有效期。用户明确完整代码的当前行情可另选直接观察引用，不证明历史身份。"}


def expand_identity_basis_reference(task, spec):
    """Explicit selection can reference a witness adopted earlier in this batch.

    This is not repair: free identity claims and reference selection cannot be
    mixed. Only the registered complete statement supplies fields and hashes.
    """
    if "identity_basis_reference" not in spec:
        return spec
    reference = spec["identity_basis_reference"]
    if (not isinstance(reference, dict) or set(reference) != {"version", "evidence_id"}
            or type(reference.get("version")) is not int or reference["version"] != IDENTITY_PERIOD_BASIS_VERSION
            or not isinstance(reference.get("evidence_id"), str) or not reference["evidence_id"] or "identity" in spec):
        raise ContractError("身份依据选择须为明确引用，不能与手填identity混用")
    choices = identity_period_basis_contract(task)["admissible_bases"]
    selected = [choice for choice in choices if choice["identity"]["basis_ref"]["evidence_id"] == reference["evidence_id"]]
    if len(selected) != 1:
        raise ContractError("所选身份有效期依据尚未通过核验或不能唯一定位")
    result = copy.deepcopy(spec)
    result.pop("identity_basis_reference")
    result["identity"] = copy.deepcopy(selected[0]["identity"])
    return result


def available_security_codes(task):
    from .object_discovery import callable_discovery_codes
    from .etf_comparison import comparison_scope

    name_codes = {code for item in identity_candidates(task) if item['business_lookup_authorized']
                  for code in item['exact_lookup_codes']}
    if any(record.get('operation') == ETF_LISTING for record in task.get('source_records', ())):
        scope = comparison_scope(task)
        if scope:
            name_codes.update(item['code'] for item in scope.get('resolved', ()))
    candidates = name_codes | set(declared_security_codes(task)) | set(callable_discovery_codes(task)) | set(callable_member_codes(task)) | {
        code for item in identity_resolutions(task) if item["status"] == "代码映射已核验" for code in item["codes"]}
    return sorted(code for code in candidates if subject_allowed(task, code))


def _identity_query_allowed(task, mode, query):
    excluded = excluded_subjects(task)
    if not excluded:
        return True
    if mode == 'secname':
        return subject_allowed(task, query)
    # Exact-number lookup has no market parameter. An excluded full code
    # cannot supply authority through its number or a Hong Kong padding alias.
    declared = declared_security_codes(task)
    if any(code.partition('.')[0] == query for code in declared):
        return True
    keys = {query, str(int(query))} if query.isascii() and query.isdecimal() else {query}
    return not any(subject_key(value).partition('.')[0] in keys for value in excluded)


def filing_filter_profiles(task, codes):
    """Registered request profiles, not security classification or permission.

    901 has not been established as a usable fund-report filter. The ETF
    namespace therefore offers an untyped catalogue, optionally title-filtered.
    A fresh explicit selection is required; failed requests are never rewritten.
    """
    if task.get('filing_filter_profile_version') is None:
        return None
    if (type(task['filing_filter_profile_version']) is not int
            or task['filing_filter_profile_version'] != 1):
        raise ContractError('公告过滤配置版本未登记')
    from .market_series import series_family
    result = []
    for fund_namespace in (True, False):
        selected = [code for code in codes if (series_family(code) == 'ETF') == fund_namespace]
        if selected:
            result.append(dict(available_codes=selected,
                functionpara_options=[{}] if fund_namespace else [{}, {'reportType': '901'}],
                routing_only=True, financial_approval=False,
                rule=('该基金代码命名空间未登记901为可用基金报告类型过滤；显式选择不限定类型或标题过滤，'
                      '再按实际返回标题、主体、所属期及原文核对。' if fund_namespace else
                      '显式选择已登记类型或不限定类型，不把901或目录当作原件内容。')))
    return result


FILING_CHOICE_LIMIT = 64
FILING_INSPECTION_LIMIT = 256


def _interleave_catalogs(streams):
    for group in zip_longest(*streams):
        for item in group:
            if item is not None:
                yield item


def _filing_catalog_rows(task, records):
    if task.get('filing_candidate_distribution_version') is None:
        return ((record, entry) for record in records for entry in record.get('catalog_entries', []))
    if (type(task['filing_candidate_distribution_version']) is not int
            or task['filing_candidate_distribution_version'] != 1):
        raise ContractError('公告候选分配版本未登记')
    by_code = {}
    for record in records:
        code = record.get('parameters', {}).get('codes')
        by_code.setdefault(code, []).append(zip(repeat(record), record.get('catalog_entries', [])))
    # Balance objects first, then each object's distinct retained queries.
    # Every individual catalogue keeps its provider order. Neither stage
    # ranks a report, claims completeness, or selects anything to download.
    return _interleave_catalogs([_interleave_catalogs(streams) for streams in by_code.values()])


def filing_candidate_choices(task, record_ids=None, *, exclude_attempted=False):
    """Expose bounded real choices without selecting or approving a document.

    The old head-only list omitted periodic filings behind recent service
    notices. Use one shared index for App and Skill; retain each catalogue's
    provider order and mark incomplete coverage rather than claiming completeness.
    An exact duplicate's alternate catalogue ids stay visible as aliases.
    """
    records = [record for record in task.get('source_records', [])
        if record.get('kind') == 'api' and record.get('operation') == 'report_query'
        and record.get('task_id') == task['id'] and record.get('research_version') == task['version']
        and (record_ids is None or record['id'] in record_ids)]
    inspected, rows = 0, []
    total = sum(len(record.get('catalog_entries', [])) for record in records)
    for record, entry in islice(_filing_catalog_rows(task, records), FILING_INSPECTION_LIMIT):
        inspected += 1
        params = {'catalog_id':record['id'], 'source_id':entry.get('source_id')}
        try:
            validate_filing_original_request(task, params)
        except ContractError:
            continue
        identity = (params['source_id'], fingerprint(entry.get('catalog_ref', {}).get('original_fields')))
        request_hash = fingerprint({'source':'ifind', 'operation':'filing_original', 'parameters':params})
        rows.append((entry,params,identity,request_hash))
    attempted = {identity for _,_,identity,request_hash in rows
        if exclude_attempted and request_hash in task.get('source_attempts', {})}
    choices, details, positions, omitted = [], [], {}, False
    for entry,params,identity,_ in rows:
        if identity in attempted:
            continue
        if identity in positions:
            position = positions[identity]
            details[position]['catalog_aliases'].append(params)
            continue
        if len(choices) >= FILING_CHOICE_LIMIT:
            omitted = True
            continue
        positions[identity] = len(choices)
        choices.append(params)
        details.append(dict(parameters=copy.deepcopy(params),title=entry['title'],
            issuer_code=entry['issuer'],issuer_name=entry['security_name'],
            report_date=entry.get('report_date'),published_at=entry.get('published_at'),
            limitations=copy.deepcopy(entry.get('gaps', [])),catalog_aliases=[],
            title_is_body_evidence=False,financial_approval=False))
    coverage = dict(returned_catalog_rows=total, inspected_rows=inspected, offered_choices=len(choices),
        inspection_limit=FILING_INSPECTION_LIMIT, choice_limit=FILING_CHOICE_LIMIT,
        truncated=inspected < total or omitted, catalog_completeness='未证实')
    invalid_count = sum(record.get('catalog_coverage', {}).get('invalid_location_count', 0) for record in records)
    if invalid_count:
        coverage['invalid_catalogue_locations'] = invalid_count
    return dict(selections=choices, details=details, coverage=coverage,
                automatic_selection=False, automatic_download=False)


def data_request_contract(task, *, first_report=False):
    """Describe implemented request shapes, never broader supplier capabilities.

    Examples demonstrate serialization only: their date is the eligible cutoff,
    not an inferred trading day or proposed retrieval window. No source call,
    name-to-code inference or future catalogue identifier is created here.
    """
    declared = declared_security_codes(task)
    callable_codes = available_security_codes(task)
    mainland = [code for code in callable_codes if re.fullmatch(r"\d{6}\.(?:SH|SZ)", code)]
    from .draft_sources import pdf_download_disabled
    from .market_series import daily_market_as_of, observation_selection, OBSERVATION_OPERATIONS
    html_only = pdf_download_disabled(task)
    date = market_date(task["cutoff"])
    market_day = daily_market_as_of(task)
    question_ids = [item["id"] for item in task["questions"]]
    tools = []
    for qualified, definition in FIELD_REGISTRY.items():
        if qualified in ('official.index_file', 'official.etf_pcf', 'official.'+HK_EVENT_HISTORY,
                         'official.'+MAINLAND_EVENT_HISTORY, 'official.'+H15_TREASURY_YIELDS,
                         'official.'+LPR_HISTORY, 'official.'+NFRA_BANK_STATISTICS):
            continue  # These sources own their task-bound request shapes, not quotation parameters.
        source, operation = qualified.split(".", 1)
        if source == "ifind":
            keys = ["codes", "indicators", "startdate", "enddate", "functionpara"]
            fixed = {"functionpara": copy.deepcopy(IFIND_QUOTE_FUNCTION)}
            code, fields, date_format = next(iter(callable_codes), None), "close", "YYYY-MM-DD"
            params = {"codes": code, "indicators": fields, "startdate": market_day, "enddate": market_day, **fixed}
        else:
            keys = ["ts_code", "fields", "start_date", "end_date"]
            fixed = {}
            code, date_format = next(iter(mainland), None), "YYYYMMDD"
            fields = "ts_code,trade_date," + ("pe_ttm" if operation == "daily_basic" else "close")
            compact_day = market_day.replace("-", "")
            params = {"ts_code": code, "fields": fields, "start_date": compact_day, "end_date": compact_day}
        item = {"qualified_id_for_lookup_only": qualified, "source": source, "operation": operation,
                "parameter_keys": keys, "fixed_parameters": fixed,
                "numeric_fields": sorted(definition["fields"]), "date_format": date_format,
                "required_record_fields": [] if source == "ifind" else ["ts_code", "trade_date"],
                "example": None}
        if code and question_ids:
            item["example"] = {"id": "shape_example", "source": source, "operation": operation,
                               "parameters": params, "question_ids": question_ids[:1],
                               "selection_reason": "仅接口形状示例，不是研究取数建议"}
        tools.append(item)
    tools.extend(financial_request_shapes(task))
    tools.extend(financial_query_shapes(task))
    tools.extend(metadata_request_shapes(task, first_report=first_report))
    tools.extend(economic_request_shapes(task))
    tools.extend(series_request_shapes(task))
    tools.extend(index_request_shapes(task))
    tools.extend(calendar_request_shapes(task))
    from .intraday_quotes import question_request as intraday_question_request

    if (intraday := intraday_question_request(task)) is not None:
        operation = intraday['operation']
        tools.append(dict(qualified_id_for_lookup_only='ifind.'+operation, source='ifind', operation=operation,
            parameter_keys=list(intraday['parameters']), fixed_parameters=copy.deepcopy(intraday['parameters']),
            raw_only=True, numeric_fields=[], example=intraday))
    for qualified, definition in FILING_REQUEST_REGISTRY.items():
        source, operation = qualified.split(".", 1)
        if html_only and operation == 'filing_original':
            continue
        item = {"qualified_id_for_lookup_only": qualified, "source": source, "operation": operation,
                "parameter_keys": list(definition["parameters"]), "example": None}
        if operation == "report_query":
            item.update(date_format="YYYY-MM-DD", fixed_parameters={
                "outputpara": definition["parameters"]["outputpara"]},
                parameter_options={"functionpara": copy.deepcopy(definition["functionpara_options"])},
                filter_rule=definition["filter_rule"],
                date_semantics=dict(version=1, fields=['beginrDate', 'endrDate'], role='公告披露日期窗口，不是指标观测日或财务所属期',
                    current_mode='当前版本研究可按需要查找观测期后、研究截止日前披露的原件；不能把披露检索固定在观测日前后几天，导致漏掉定期报告。',
                    historical_mode='不得查询晚于研究截止时点的披露来补历史信息集；没有当时可得原件则保留缺口。',
                    original_priority='按问题和实际缺失计算输入选择原件；仅有估值方法、规则调整或服务公告不能代替官方净值、财务金额或持仓记录。',
                    automatic_window=False, financial_approval=False))
            profiles = filing_filter_profiles(task, callable_codes)
            if profiles is not None:
                item['filing_filter_profiles'] = profiles
                # Generic options are the union; each code's actual choices
                # remain scoped by its profile in both Schema and validator.
                options = []
                for profile in profiles:
                    for option in profile['functionpara_options']:
                        if option not in options:
                            options.append(copy.deepcopy(option))
                item['parameter_options']['functionpara'] = options
            if type(task.get('filing_keyword_filter_version')) is int and task['filing_keyword_filter_version'] == 1:
                item['optional_title_filter'] = dict(parameter='keyWord', minLength=1, maxLength=40,
                    rule='可在两种既有functionpara上显式增加标题关键词，缩小同证券同日期窗口；不自动加词、分词、换词、扩大窗口或重派。'
                         '查询返回不保证标题精确命中、目录完整或原件满足研究需求，仍逐项读取和核验。')
            if callable_codes and question_ids:
                item["example"] = {"id": "shape_example", "source": source, "operation": operation,
                    "parameters": {"codes": callable_codes[0], "functionpara": {}, "beginrDate": date, "endrDate": date,
                                   **copy.deepcopy(item["fixed_parameters"])}, "question_ids": question_ids[:1],
                    "selection_reason": "仅接口形状示例，不是研究取数建议"}
        else:
            index = filing_candidate_choices(task)
            item.update(fixed_parameters={}, available_selections=index['selections'],
                        selection_details=index['details'],candidate_coverage=index['coverage'],
                        selection_limit=FILING_CHOICE_LIMIT, inspected_candidates=index['coverage']['inspected_rows'],
                        prerequisite="必须先留存本任务当前版本目录；不得猜id或使用URL；此列表为有界候选，不声称目录穷尽")
        tools.append(item)
    for qualified, definition in IDENTITY_REQUEST_REGISTRY.items():
        tools.append({"qualified_id_for_lookup_only": qualified, "source": "ifind", "operation": "get_thscode",
            "parameter_keys": ["seccode或secname二选一", "functionpara"], "example": None,
            "query_modes": definition["query_modes"], "query_rule": "单个查询值须逐字来自用户输入；seccode为4至6位数字，secname为名称；不得猜代码",
            "fixed_parameters_by_mode": copy.deepcopy(IDENTITY_PARAMETERS_BY_MODE),
            "parameter_shape_version": 1,
            "parameter_shapes": {mode: {mode: "逐字取自用户输入的单个名称或代码",
                "functionpara": {"mode": mode, "sectype": "", "market": "", "tradestatus": "0", "isexact": "1"}}
                for mode in definition["query_modes"]},
            "serialization_rule": "parameters顶层只含secname或seccode二选一，以及functionpara。mode、sectype、market、tradestatus、isexact全部放在functionpara内部，不得平铺到parameters顶层。",
            "limitations": definition["limitations"]})
        from .object_discovery import enabled as discovery_enabled
        if discovery_enabled(task):
            tools[-1]['query_rule'] += '；也可精确选择本任务实际object_discovery候选的完整股票简称或代码中原有六位数字，不截取简称、不把候选当行业归属证明。'
        if identity_candidate_lookup_enabled(task):
            tools[-1]['query_modes'] = ['seccode']
            tools[-1]['query_rule'] = ('名称先显式选择candidate_profile查询；已返回完整市场代码时按用途选择后续原始业务查询。'
                '号码与市场均保留原值，不猜前导零或默认份额；候选本身不批准金融事实。'
                if task['identity_candidate_lookup_version']==2 else
                '名称先显式选择candidate_profile查询；精确配置只选用户原号码或本任务实际候选的原号码，不补零、不选市场。')
            tools[-1]['parameter_shapes'].pop('secname',None)
            original_numbers = {match.group() for text in [scope_question(task),*task['objects']]
                for match in re.finditer(r'(?<!\d)\d{4,6}(?!\d)',
                    re.sub(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{4}年',' ',text))
                if _identity_query_allowed(task, 'seccode', match.group())}
            actual_numbers = {code.split('.')[0] for item in identity_candidates(task) for code in item['exact_lookup_codes']
                              if subject_allowed(task, code)}
            from .object_discovery import identity_query_candidates
            actual_numbers.update(item['code'].split('.')[0] for item in identity_query_candidates(task)
                                  if subject_allowed(task, item['code']))
            tools[-1]['query_value_options'] = sorted(original_numbers | (actual_numbers
                if task['identity_candidate_lookup_version']==1 else set()))
            tools[-1]['candidate_profile'] = dict(profile_id=IDENTITY_CANDIDATE_PROFILE,query_modes=['secname'],
                parameters=identity_query_parameters('secname','用户原名称',IDENTITY_CANDIDATE_PROFILE),
                retained_candidates=identity_candidates(task),identity_approved=False,
                rule=('显式查询用户原名称，保留全部返回市场；按问题选择实际完整代码取得原始资料，不再把完整代码改成数字重新转换。'
                     '候选只限定后续查询范围，不是证券身份或金融批准，不裁掉海外结果冒充唯一；市场未明确时保留歧义。'
                     if task['identity_candidate_lookup_version']==2 else
                     '同名多市场时显式查询名称候选，再从实际返回代码的原号码选择精确seccode查询。'
                     '不直接用候选取业务数据、不裁掉海外结果冒充唯一、不猜或补证券号码；市场未明确时保留歧义。'))
            inherited = (task.get('conversation_context') or {}).get('security_name_queries') or []
            if any(name in task['objects'] for name in inherited):
                tools[-1]['candidate_profile']['rule'] += '追问沿用的名称逐字使用本轮objects中的原名。'
    from .official_sources import request_shapes
    tools.extend(request_shapes(task, html_only=html_only))
    from .etf_sources import request_shapes as etf_request_shapes
    tools.extend(etf_request_shapes(task))
    from .event_market import mainland_request_shape, request_shape as event_market_request_shape
    tools.append(event_market_request_shape(task))
    mainland_event = mainland_request_shape(task)
    if mainland_event['available_parameter_sets']:
        tools.append(mainland_event)
    from .treasury_rates import request_shape as h15_request_shape, study_scope as h15_scope

    if scope := h15_scope(task):
        tools.append(h15_request_shape(scope))
    from .lpr_rates import request_shape as lpr_request_shape, study_scope as lpr_scope

    if scope := lpr_scope(task):
        tools.append(lpr_request_shape(scope))
        from .nfra_bank_statistics import request_options

        if options := request_options(task):
            parameters = [value['parameters'] for value in options.values()]
            tools.append(dict(qualified_id_for_lookup_only='official.'+NFRA_BANK_STATISTICS, source='official',
                operation=NFRA_BANK_STATISTICS, parameter_keys=list(dict.fromkeys(key for row in parameters for key in row)),
                available_parameter_sets=parameters, numeric_fields=[NFRA_BANK_FIELD], credential_required=False,
                selection_rule='银行息差研究使用金融监管总局当期合计表，先定位目录及附件，再读取正式表格。'))
    from .draft_sources import historical_structured_issue, historical_structured_scope
    if historical_structured_scope(task):
        tools = [item for item in tools if not historical_structured_issue(task, item['source'], item['operation'])]
    observation = observation_selection(task)
    for item in tools:
        item.setdefault("example", None)
        if observation is not None and item['source'] == 'ifind' and item['operation'] in OBSERVATION_OPERATIONS:
            item['observation_selection'] = copy.deepcopy(observation)
    result = {"version": 2, "binding": {"task_id": task["id"], "research_version": task["version"],
            "input_hash": task["input_hash"], "cutoff": task["cutoff"]},
            "declared_codes": declared, "callable_codes": callable_codes,
            "identity_resolutions": identity_resolutions(task), "question_ids": question_ids,
            "tools": tools, "empty_requests_allowed": True, "acquisition_channels": acquisition_channels(task),
            "financial_evidence_path": financial_evidence_path(task),
            "example_scope": "仅展示字段形状，日期不是已确认交易日，不要复制为默认取数动作；按原问题另行选择窗口",
            "operation_rule": "source与operation分别填写，operation不得加ifind.或tinyshare.前缀；只使用tools中的精确名称",
            "unsupported_rule": "未登记的业务接口不规划、不换名规避；已有原件足够时data_requests可为[]"}
    from .object_discovery import request_guidance
    discovery = request_guidance(task)
    if discovery:
        # Already-retained choices must survive narrowing executable tools.
        # Do not nest the only copy under a discovery tool that can be removed
        # once the current question needs identity or an original instead.
        result['object_discovery'] = discovery
    from .source_requirements import alternatives
    if task.get('catalogue_alternative_version') is not None and not html_only:
        result['catalogue_alternatives']=alternatives(task)
    from .financial_sources import source_request_fields_enabled
    if source_request_fields_enabled(task):
        result['source_request_fields_version'] = 1
        result['financial_request_rule'] = ('基础财务按代码、报告期、报表类型及不重复字段选择；'
            '由内核生成D/C顺序参数，不由模型拼indiparams数组。不同报告期分别请求。'
            '没有适用目录备选项时不填写catalogue_alternative_ref。格式选择不批准数据或权限。')
    from .financial_sources import structured_source_preference
    preference = structured_source_preference(task)
    if preference is not None:
        result['source_preference'] = preference
    if historical_structured_scope(task):
        result['historical_structured_rule'] = ('历史时点按已登记的日频日期上限和适用原件取证。'
            '当前财务、宏观修订、指数成分和ETF清单只保存在底稿，缺少当时版本时保留缺项。')
    if first_report:
        from .draft_sources import ORIGINAL_DOWNLOAD_OPERATIONS, historical_originals_local_only, local_sources_only
        if local_sources_only(task):
            result['tools'] = []
            result.pop('catalogue_alternatives', None)
            result['operation_rule'] = '本次仅使用留存资料或附件，data_requests须为空。'
            result['acquisition_channels']['download_policy']['scope'] = 'retained_only'
        elif historical_originals_local_only(task):
            result['tools'] = [item for item in tools
                               if (item['source'], item['operation']) not in ORIGINAL_DOWNLOAD_OPERATIONS]
            result.pop('catalogue_alternatives', None)
            result['acquisition_channels']['download_policy']['historical_originals'] = 'retained_only'
            result['historical_original_rule'] = (
                '截止时点已过。首稿复用已留存且可作历史依据的原件，优先取仍可使用的结构化数据。'
                '仅在问题要求定位或列举时查询公告目录，不把目录当作正文，不因目录命中再规划原件下载。')
    if excluded := excluded_subjects(task):
        result['excluded_subjects'] = list(excluded)
        result['subject_rule'] = '用户排除的对象不得加入本轮取数或结论。'
    return result

_PRIVATE_KEYS = re.compile(r"^(?:access_?token|refresh_?token|token|password|passwd|secret|authorization|credentials?|cookie|api_?key|client_?name|customer|contact|phone|email)$", re.I)
_SAFE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
NATURES = {"已实现事实", "合同政策条款", "管理层展望", "第三方估计", "本次推算"}
_PRIVATE_URL_KEYS = frozenset({"userid", "user_id", "ip", "signature", "sig", "credential", "auth"})
_RESPONSE_URL = re.compile(r"https?://[^\s<>\"']+", re.I)


def _public_response_url(match):
    """Authentication in a URL is private even when its containing key is public."""
    value = match.group(0)
    try:
        parsed = urlsplit(value)
        names = {key.lower() for key, _ in parse_qsl(parsed.query + "&" + parsed.fragment, keep_blank_values=True)}
        private = parsed.username is not None or parsed.password is not None or any(
            _PRIVATE_KEYS.search(key) or key in _PRIVATE_URL_KEYS or key.startswith(("x-amz-", "x-goog-"))
            for key in names)
        if not private:
            return value
        # Drop the complete authorization query/fragment, not just the token:
        # other parameters can encode account, IP, signature or token fragments.
        authority = parsed.netloc.rsplit("@", 1)[-1]
        return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))
    except ValueError:
        # Malformed authentication URLs must not become a diagnostic leak.
        return "[不可公开的来源地址]"


def business_payload(value, private_values=()):
    """Remove authentication/private fields before hashing or archiving responses."""
    if isinstance(value, dict):
        return {key: business_payload(item, private_values) for key, item in value.items()
                if isinstance(key, str) and not _PRIVATE_KEYS.search(key)}
    if isinstance(value, list):
        return [business_payload(item, private_values) for item in value]
    if isinstance(value, str):
        for secret in private_values:
            if isinstance(secret, str) and secret:
                value = value.replace(secret, "[已移除敏感值]")
        return _RESPONSE_URL.sub(_public_response_url, value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ContractError("业务响应包含非有限值或不可归档类型")


def response_url_redactions(value, path=()):
    """Record only field paths, never the original authorization strings."""
    if isinstance(value, dict):
        return [found for key, item in value.items() if isinstance(key, str) and not _PRIVATE_KEYS.search(key)
                for found in response_url_redactions(item, (*path, key))]
    if isinstance(value, list):
        return [found for index, item in enumerate(value) for found in response_url_redactions(item, (*path, index))]
    if isinstance(value, str) and _RESPONSE_URL.sub(_public_response_url, value) != value:
        return [list(path)]
    return []


def _ifind_resource_locator(value):
    """An observed provider resource path, without its ephemeral authorization."""
    if not isinstance(value, str) or any(ord(char) <= 32 or ord(char) >= 127 for char in value) or "\\" in value:
        return None
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        return None
    if (url.scheme not in ("http", "https") or url.hostname != IFIND_RESOURCE_HOST or url.username or url.password
            or port is not None or url.path != IFIND_RESOURCE_PATH or url.query or url.fragment):
        return None
    # This is only a non-authorized locator. Actual downloading uses TLS and
    # the provider's returned query in memory; never fall back to clear HTTP.
    return urlunsplit(("https", IFIND_RESOURCE_HOST, IFIND_RESOURCE_PATH, "", ""))


def _ifind_authorized_resource(value, source_id):
    """Accept only a selected seq's exact observed provider resource route."""
    if not isinstance(value, str) or len(value) > 16384 or any(ord(char) <= 32 or ord(char) >= 127 for char in value) or "\\" in value:
        raise ContractError("iFinD原件临时授权地址无效")
    try:
        url = urlsplit(value)
        fields = parse_qsl(url.query, keep_blank_values=True)
        port = url.port
    except ValueError:
        raise ContractError("iFinD原件临时授权地址无效") from None
    if (url.scheme not in ("http", "https") or url.hostname != IFIND_RESOURCE_HOST or port is not None
            or url.username or url.password or url.fragment or url.path != IFIND_RESOURCE_PATH):
        raise ContractError("iFinD原件地址不在已登记供应商资源路径")
    if len(fields) != 4 or {key for key, _ in fields} != {"seq", "token", "userid", "ip"}:
        raise ContractError("iFinD原件授权参数缺失、重复或未登记")
    params = dict(fields)
    if params["seq"] != source_id or not re.fullmatch(r"\d+", source_id) or not all(params.values()):
        raise ContractError("iFinD原件授权与已选择的seq不匹配")
    # Preserve the returned query byte-for-byte. It never enters a snapshot,
    # log or error message and is never sent over the returned HTTP scheme.
    return urlunsplit(("https", IFIND_RESOURCE_HOST, IFIND_RESOURCE_PATH, url.query, ""))


def safe_parameters(params):
    if not isinstance(params, dict) or business_payload(params) != params:
        raise ContractError("查询参数不能含凭据、认证头或无关私密字段")
    return copy.deepcopy(params)


def _require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(label + "缺失")
    return value.strip()


def _bound(record, task):
    if record.get("task_id") != task["id"] or record.get("research_version") != task["version"]:
        raise ContractError("原始材料属于其他任务或研究版本")


def _evidence_artifact_id(item):
    artifact = item.get("artifact")
    embedded = artifact.get("id") if isinstance(artifact, dict) else None
    declared = item.get("artifact_id")
    if embedded is not None and declared is not None and embedded != declared:
        raise ContractError("证据的原件编号与实际原件不一致")
    return embedded if embedded is not None else declared


def _unique_artifact(records, ident):
    if not isinstance(ident, str) or not ident:
        return None
    matches = [record for record in records if isinstance(record, dict) and record.get("id") == ident]
    return matches[0] if len(matches) == 1 else None


def validate_tinyshare_request(task, operation, params):
    """One explicit security/window; documentation is not a permission receipt."""
    if "tinyshare." + str(operation) in FINANCIAL_REQUEST_REGISTRY:
        return validate_financial_request(task, operation, params)
    definition = FIELD_REGISTRY.get("tinyshare." + str(operation))
    if not definition or operation not in TINYSHARE_DOCS:
        raise ContractError("Tinyshare接口未登记")
    if not isinstance(params, dict) or set(params) != {"ts_code", "start_date", "end_date", "fields"} or not all(isinstance(value, str) and value for value in params.values()):
        raise ContractError("Tinyshare仅接受明确对象、日期窗口和登记字段")
    if not re.fullmatch(r"\d{6}\.(?:SH|SZ)", params["ts_code"]):
        raise ContractError("Tinyshare仅接受明确单只A股或基金代码，不扫描全市场或猜测身份")
    fields = params["fields"].split(",")
    selected = set(fields)
    if len(fields) != len(selected) or not {"ts_code", "trade_date"}.issubset(selected) or not selected - {"ts_code", "trade_date"} or selected - set(definition["fields"]) - {"ts_code", "trade_date"}:
        raise ContractError("Tinyshare字段或记录键未登记")
    dates = []
    for key in ("start_date", "end_date"):
        value = params[key]
        if not re.fullmatch(r"\d{8}", value):
            raise ContractError("Tinyshare查询日期须为官方YYYYMMDD格式，不改写实际请求")
        value = value[:4] + "-" + value[4:6] + "-" + value[6:]
        timepoint(value)
        dates.append(value)
    cutoff = task.get("cutoff")
    if operation == "daily_basic" and not cutoff:
        raise ContractError("市场背景查询必须绑定研究截止时点")
    if dates[0] > dates[1] or cutoff is not None and dates[1] > market_date(cutoff):
        raise ContractError("Tinyshare查询窗口倒置或晚于研究截止时点")
    return dates


def validate_market_request(task, source, operation, params, *, require_identity=True):
    """One outgoing market-data authority for model, host and provider calls.

    Retained/imported raw responses are not dispatched here and remain readable.
    Validating a request does not approve a response, identity or numeric fact.
    """
    definition = FIELD_REGISTRY.get(str(source) + "." + str(operation))
    if definition is None:
        raise ContractError("行情接口未登记")
    if not task.get("cutoff"):
        raise ContractError("行情请求必须绑定研究截止时点")
    declared = set(available_security_codes(task))
    if source == "tinyshare":
        dates = validate_tinyshare_request(task, operation, params)
        if require_identity and params["ts_code"] not in declared:
            raise ContractError("行情请求对象未由本任务明确声明或核验映射，不能扩大研究范围")
        return dates
    if source != "ifind" or operation != "cmd_history_quotation":
        raise ContractError("行情请求路径未登记")
    keys = {"codes", "indicators", "startdate", "enddate", "functionpara"}
    if not isinstance(params, dict) or set(params) != keys:
        raise ContractError("iFinD行情请求须明确对象、字段、日期窗口及登记口径")
    codes, fields = params["codes"], params["indicators"]
    if not isinstance(codes, str) or not codes or not isinstance(fields, str) or not fields:
        raise ContractError("iFinD行情代码及字段须为非空文字")
    selected_codes, selected_fields = codes.split(","), fields.split(",")
    hk_event = any(re.fullmatch(HK_EVENT_INDEX_PATTERN, code) for code in selected_codes)
    if hk_event:
        from .event_market import ifind_parameters, market_scope
        scope = market_scope(task)
        if not scope or ifind_parameters(scope) != params:
            raise ContractError('港股指数行情请求须匹配当前政策事件时点和登记对象')
    elif (len(set(selected_codes)) != len(selected_codes) or any(not re.fullmatch(
            r"(?:\d{6}\.(?:SH|SZ)|\d{4,5}\.HK)", code) for code in selected_codes)
            or require_identity and not set(selected_codes).issubset(declared)):
        from .event_market import ifind_parameters, market_scopes

        scope = market_scopes(task).get('mainland')
        if not scope or ifind_parameters(scope) != params:
            raise ContractError("行情请求对象未由本任务明确声明或核验映射，不能重复代码或扩大研究范围")
    requestable = definition["fields"].keys() | definition["raw_observation_fields"].keys()
    if len(set(selected_fields)) != len(selected_fields) or not set(selected_fields).issubset(requestable):
        raise ContractError("iFinD行情字段未登记或重复")
    if params["functionpara"] != IFIND_QUOTE_FUNCTION:
        raise ContractError("行情请求不能更改登记币种、复权和缺失填充口径")
    dates = []
    for key in ("startdate", "enddate"):
        value = params[key]
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ContractError("iFinD行情查询日期须为YYYY-MM-DD格式，不改写实际请求")
        timepoint(value)
        dates.append(value)
    if dates[0] > dates[1] or dates[1] > market_date(task["cutoff"]):
        raise ContractError("行情查询窗口倒置或晚于研究截止时点")
    return dates


def validate_source_request(task, source, operation, params, *, require_identity=True, defer_account_policy=False):
    """Validate outgoing authority before cache reuse, checkpoints or credentials.

    Raw imports remain readable through snapshot_record. Neither validation nor
    reuse approves the returned facts, units, identity or historical vintage.
    Batch preflight can check parameter syntax before an earlier identity query
    resolves. Every actual send and cache reuse still requires verified identity.
    """
    params = safe_parameters(params)
    from .draft_sources import historical_structured_issue
    if issue := historical_structured_issue(task, source, operation):
        raise ContractError(issue)
    qualified = str(source) + "." + str(operation)
    if qualified in ETF_REQUEST_REGISTRY:
        from .etf_sources import validate_request as validate_etf_request
        validate_etf_request(task, params, require_identity=require_identity)
    elif qualified in ETF_LISTING_REGISTRY:
        from .etf_listing import validate_request as validate_etf_listing
        validate_etf_listing(task, params)
    elif qualified in ETF_TRADING_REGISTRY:
        from .etf_trading import validate_request as validate_etf_trading
        validate_etf_trading(task, operation, params)
    elif qualified in OFFICIAL_REQUEST_REGISTRY:
        from .official_sources import canonical_request_parameters
        params = canonical_request_parameters(task, operation, params)
    elif qualified=='official.index_file':
        from .index_sources import validate_official_index_request
        validate_official_index_request(task,operation,params)
    elif qualified == 'official.'+HK_EVENT_HISTORY:
        from .event_market import validate_request as validate_history
        validate_history(task, params)
    elif qualified == 'official.'+MAINLAND_EVENT_HISTORY:
        from .event_market import validate_mainland_request
        validate_mainland_request(task, params)
    elif qualified == 'official.'+H15_TREASURY_YIELDS:
        from .treasury_rates import validate_request as validate_h15_request
        validate_h15_request(task, params)
    elif qualified == 'official.'+LPR_HISTORY:
        from .lpr_rates import validate_request as validate_lpr_request
        validate_lpr_request(task, params)
    elif qualified == 'official.'+NFRA_BANK_STATISTICS:
        from .nfra_bank_statistics import validated_request as validate_nfra_request
        validate_nfra_request(task, params)
    elif qualified in IFIND_METADATA_REGISTRY:
        from .financial_sources import financial_request_parameters
        params = financial_request_parameters(task, operation, params)
        validate_metadata_request(task, operation, params, require_identity=require_identity,
                                  defer_account_policy=defer_account_policy)
    elif qualified in INTRADAY_REQUEST_REGISTRY:
        from .intraday_quotes import validate_request as validate_intraday_request

        validate_intraday_request(task, operation, params)
    elif qualified in FIELD_REGISTRY:
        validate_market_request(task, source, operation, params, require_identity=require_identity)
    elif qualified in IDENTITY_REQUEST_REGISTRY:
        validate_ifind_identity_request(task, params)
    elif qualified in ECONOMIC_REQUEST_REGISTRY:
        validate_economic_request(task, operation, params)
    elif qualified in SERIES_REQUEST_REGISTRY:
        from .market_series import valuation_exclusion_scope

        validate_series_request(task, operation, params, require_identity=require_identity)
        _, excluded = valuation_exclusion_scope(task)
        if any(item['indicator'] in excluded for item in params['indipara']):
            raise ContractError('本轮已排除对应估值字段，未派发请求。')
    elif qualified in INDEX_REQUEST_REGISTRY:
        if source=='official':
            from .index_sources import validate_official_index_request
            validate_official_index_request(task,operation,params)
        else:validate_index_request(task, params, require_identity=require_identity)
    elif qualified in CALENDAR_REQUEST_REGISTRY:
        validate_calendar_request(task, params)
    elif qualified in FINANCIAL_REQUEST_REGISTRY:
        validate_financial_request(task, operation, params)
    elif qualified in FINANCIAL_QUERY_REGISTRY:
        validate_financial_query(task, params, require_identity=require_identity)
    elif source == "ifind" and operation == "report_query":
        validate_ifind_report_request(task, params, require_identity=require_identity)
        if require_identity and params["codes"] not in set(available_security_codes(task)):
            raise ContractError("公告查询代码须由用户明确声明或核验映射")
    elif source == "ifind" and operation == "filing_original":
        validate_filing_original_request(task, params)
    elif source == "ifind":
        raise ContractError("iFinD接口未登记")
    elif source == "tinyshare":
        raise ContractError("Tinyshare接口未登记")
    elif source == "wind":
        raise ContractError("Wind查询与字段口径未登记，本轮不能派发")
    else:
        raise ContractError("未知数据源")
    from .market_series import bound_daily_market_window
    return bound_daily_market_window(task, source, operation, params)


def record_request_purpose(record, need, question_ids):
    """Keep each reuse reason beside the exact request and questions.

    The source snapshot and hash stay unchanged. Older aggregate metadata is
    retained without guessing a per-request mapping when aliases already exist.
    """
    from .market_series import record_observation_scope
    record_observation_scope(record, need, question_ids)
    purpose = need.get('selection_reason')
    if not isinstance(purpose, str) or not purpose.strip():
        return
    entries = record.get('request_purposes')
    if not isinstance(entries, list):
        entries = []
        prior = record.get('selection_reason')
        identifiers = record.get('data_need_ids', [])
        if isinstance(prior, str) and prior.strip() and isinstance(identifiers, list) and identifiers:
            entries.append(dict(data_need_id=identifiers[0] if len(identifiers) == 1 else None,
                question_ids=copy.deepcopy(record.get('question_ids', [])), selection_reason=prior.strip()))
    selected = dict(data_need_id=need['id'], question_ids=list(question_ids), selection_reason=purpose.strip())
    if selected not in entries:
        entries.append(selected)
    record['request_purposes'] = entries


def snapshot_record(task, source, operation, params, payload, private_values=()):
    """Persist an actual business response, without calling or approving a provider."""
    if source not in ("ifind", "tinyshare", "wind"):
        raise ContractError("结构化来源未登记")
    if operation not in CAPABILITIES[source].get("endpoints", []):
        raise ContractError("来源接口未登记，不能猜测Wind表或供应商字段")
    params = safe_parameters(params)
    if f'{source}.{operation}' in INTRADAY_REQUEST_REGISTRY:
        from .intraday_quotes import validate_request as validate_intraday_request

        validate_intraday_request(task, operation, params)
    metadata = source == 'ifind' and 'ifind.' + str(operation) in IFIND_METADATA_REGISTRY
    if metadata:
        validate_metadata_request(task, operation, params)
    if source == "ifind" and operation == "get_thscode":
        validate_ifind_identity_request(task, params)
    economic = source == "ifind" and "ifind." + str(operation) in ECONOMIC_REQUEST_REGISTRY
    if economic:
        validate_economic_request(task, operation, params)
    series = source == "ifind" and "ifind." + str(operation) in SERIES_REQUEST_REGISTRY
    if series:
        validate_series_request(task, operation, params)
    index_pool = source == 'ifind' and 'ifind.' + str(operation) in INDEX_REQUEST_REGISTRY
    if index_pool:
        validate_index_request(task, params)
    calendar = source == 'ifind' and 'ifind.' + str(operation) in CALENDAR_REQUEST_REGISTRY
    if calendar:
        validate_calendar_request(task, params)
    financial = source == "tinyshare" and "tinyshare." + str(operation) in FINANCIAL_REQUEST_REGISTRY
    if financial:
        validate_financial_request(task, operation, params)
    financial_query = source == "ifind" and "ifind." + str(operation) in FINANCIAL_QUERY_REGISTRY
    if financial_query:
        query_scope = validate_financial_query(task, params)
    public_payload = business_payload(payload, private_values)
    redacted = public_payload != payload
    url_paths = response_url_redactions(payload)
    if url_paths and isinstance(public_payload, dict):
        public_payload["_researchhelper_privacy"] = {"version": 1, "redacted_url_paths": url_paths}
    payload = public_payload
    if not isinstance(payload, dict):
        raise ContractError("业务响应必须保留原始字段结构")
    if metadata:
        try:
            metadata_contract = metadata_response(operation, params, payload,
                allow_partial=partial_financial_enabled(task))
            metadata_valid = True
        except DuplicateJSONField:
            raise
        except (ContractError, TypeError, ValueError, KeyError):
            metadata_contract, metadata_valid = None, False
    request_hash = fingerprint({"source": source, "operation": operation, "parameters": params})
    digest = fingerprint(payload)
    code = payload.get("errorcode") if source == "ifind" else payload.get("code", 0)
    if metadata and operation in IFIND_METADATA_OPERATIONS:
        code = payload.get('code', payload.get('errorcode'))
        status = '成功待核验' if metadata_valid else '返回结构异常'
    elif isinstance(code, bool) or code is None:
        status = "返回结构异常"
    elif code not in (0, "0"):
        # Do not guess meanings for undocumented supplier error numbers.
        status = "业务失败待按官方错误定义核查"
    else:
        rows = payload.get("tables") if source == "ifind" else payload.get("data")
        status = "返回结构异常" if not isinstance(rows, list) else "成功待核验" if rows else "成功但为空"
    record = {"id": "rh_source_" + fingerprint([task["id"], task["version"], request_hash, digest])[:24],
            "kind": "api", "source": source, "operation": operation, "parameters": params,
            "request_hash": request_hash, "snapshot": payload, "hash": digest, "content_version": 1,
            "task_id": task["id"], "research_version": task["version"], "obtained_at": now(),
            "status": status, "business_code": code, "source_check": "待核验", "applicability_check": "待核验"}
    if source == "ifind" and operation == "cmd_history_quotation" and status == "成功待核验":
        indicators = params.get("indicators")
        requested = indicators.split(",") if isinstance(indicators, str) else []
        raw_fields = FIELD_REGISTRY["ifind.cmd_history_quotation"]["raw_observation_fields"]
        if raw_fields.keys() & requested:
            # Presence is structural only. Identity, dates and usable cells are
            # independently bound by the observation reader for each table.
            missing_by_table = []
            for index, table in enumerate(payload["tables"]):
                columns = table.get("table") if isinstance(table, dict) else None
                missing = [field for field in requested if not isinstance(columns, dict) or field not in columns]
                if missing:
                    missing_by_table.append({"table": index, "missing_fields": missing})
            if missing_by_table:
                record["missing_quote_columns"] = missing_by_table
    if metadata:
        if not metadata_valid:
            record['status'] = '返回结构异常'
        record['metadata_contract'] = dict(version=1, raw_only=True, numeric_evidence_approved=False,
            response_kind=operation, shape_validated=metadata_valid, parsed=metadata_contract)
        if not metadata_valid and operation=='basic_data_service':
            from .financial_sources import draft_financial_response
            try:
                projection=draft_financial_response(task,params,payload)
            except (ContractError,TypeError,ValueError,KeyError,IndexError):
                projection=None
            if projection is not None:
                record['status']='成功待核验'
                record['draft_response_contract']=dict(projection,request_hash=request_hash,snapshot_hash=digest)
    if redacted:
        record["snapshot_privacy"] = {"policy_version": 1, "redacted": True,
            "basis": "已移除认证字段及来源地址中的临时授权参数；哈希绑定保留的业务快照，不冒充完整传输响应",
            "business_snapshot_hash": digest}
    if economic:
        record["economic_contract"] = economic_record_contract(task)
    if calendar:
        try:
            record['calendar_contract'] = dict(version=1, raw_only=True,
                financial_approval=False, **calendar_sessions(params, payload))
            record['status'] = '成功待核验'  # An explicit empty date column is meaningful here only.
        except ContractError as error:
            record.update(status='返回结构异常', calendar_contract=dict(version=1, raw_only=True,
                financial_approval=False, response_issue=str(error)))
    if series:
        try:
            record["series_contract"] = series_record_contract(task, params, payload)
        except ContractError as error:
            record.update(status='返回结构异常', series_contract=dict(version=3, raw_only=True,
                financial_approval=False, response_issue=str(error)))
        from .draft_sources import draft_date_series_scope, partial_date_series_enabled
        if partial_date_series_enabled(task):
            scope = draft_date_series_scope(task, record)
            contract = record['series_contract']
            if contract.get('response_issue') or scope['rejected_tables'] or scope['rejected_cells']:
                contract['draft_response_coverage'] = dict(
                    numeric_cells=scope['numeric_cells'], rejected_tables=scope['rejected_tables'],
                    rejected_cells=scope['rejected_cells'], raw_only=True, financial_approval=False)
                record['status'] = '成功待核验' if scope['numeric_cells'] else '返回结构异常'
    if index_pool:
        try:
            record['index_contract'] = index_response(task, params, payload)
        except ContractError as error:
            record.update(status='返回结构异常', index_contract=dict(version=1,raw_only=True,
                numeric_evidence_registered=False,response_issue=str(error)))
    if financial_query:
        try:
            record["financial_query_contract"] = financial_query_response(task, params, payload)
            if not record["financial_query_contract"]["row_count"]:
                record["status"] = "成功但为空"
        except ContractError as error:
            record["financial_query_contract"] = {
                "version": 1, "raw_only": True, "numeric_evidence_registered": False,
                "response_issue": str(error)}
            if query_scope.get('query_kind') == 'object_discovery':
                record['financial_query_contract']['query_kind'] = 'object_discovery'
            if record["status"] == "成功待核验":
                record["status"] = "返回结构异常"
    if source == "ifind" and operation == "report_query":
        validate_ifind_report_request(task, params)
        record["catalog_origin"] = {"task_id": task["id"], "research_version": task["version"]}
        record["catalog_read_version"] = 1
        record.update(_filing_catalog(task, record))
    if source == "ifind" and operation == "get_thscode":
        record["identity_origin"] = dict(identity_scope(task), input_hash=task["input_hash"])
    if financial:
        definition = FINANCIAL_REQUEST_REGISTRY["tinyshare." + operation]
        record["financial_source"] = {
            "version": 1, "raw_only": True, "numeric_evidence_registered": False,
            "task_id": task["id"], "research_version": task["version"],
            "request_hash": request_hash, "snapshot_hash": digest,
            "report_period": params["period"], "announcement_window": {
                "start": params["start_date"], "end": params["end_date"]},
            "documentation": definition["documentation"],
            "unit_verified": False, "historical_vintage_verified": False,
            "permission_verified": False,
            "limitation": "公告日期窗口不等于财务所属期；当前报表不证明历史当时版本。"
                          "金额单位、币种、会计准则及版本可得性未核验，不作为金融数字或计算依据。"}
        try:
            validate_financial_snapshot(task, operation, params, payload)
        except ContractError as error:
            record.update(status="返回结构异常")
            record["financial_source"]["response_issue"] = str(error)
    return record


CURRENT_SNAPSHOT_LIMITATION = "仅采用本次取得的当前数据版本，不证明历史当时可得、历史投资机会或策略有效性。"
CURRENT_ORIGINAL_LIMITATION = ("仅采用本任务实际取得的当前原件版本及明确观测日期；首次公开时间未知，"
    "不证明历史当时可得或事件因果，披露时点的持仓不代表实时完整持仓。")


def _original_download_profile(record):
    """Registered original shapes, distinct from imported or API snapshots."""
    if not isinstance(record, dict) or not isinstance(record.get('snapshot'), dict):
        return None
    identity = (record.get('source'), record.get('operation'), record.get('kind'))
    if identity == ('ifind', 'filing_original', 'filing'):
        material = record['snapshot'].get('material')
        version, snapshot_hash = 2, record.get('hash')
    elif identity == ('official', 'release_original', 'material'):
        material = record['snapshot']
        version, snapshot_hash = 3, record.get('snapshot_hash')
    else:
        return None
    if not isinstance(material, dict) or not isinstance(snapshot_hash, str):
        return None
    if version == 3 and record.get('hash') != material.get('hash'):
        return None
    return dict(version=version, source=identity[0], operation=identity[1],
                material=material, snapshot_hash=snapshot_hash)


def _validated_original_download(task, receipt):
    """Validate the saved download independently of a later parser outcome."""
    keys = {'version', 'task_id', 'research_version', 'batch', 'dispatch_index', 'source', 'operation',
            'parameters', 'request_hash', 'original_id', 'original_hash', 'byte_count', 'kind', 'name',
            'obtained_at', 'status'}
    if (not isinstance(receipt, dict) or set(receipt) != keys
            or type(receipt.get('version')) is not int or receipt['version'] != 1
            or receipt.get('task_id') != task['id']
            or type(receipt.get('research_version')) is not int or receipt['research_version'] != task['version']
            or type(receipt.get('batch')) is not int or receipt['batch'] < 0
            or type(receipt.get('dispatch_index')) is not int
            or type(receipt.get('byte_count')) is not int or receipt['byte_count'] <= 0
            or receipt.get('status') != '已留存'
            or not isinstance(receipt.get('source'), str) or not isinstance(receipt.get('operation'), str)
            or not isinstance(receipt.get('parameters'), dict)
            or not isinstance(receipt.get('original_id'), str) or not receipt['original_id'].startswith('rh_original_')
            or not isinstance(receipt.get('original_hash'), str) or re.fullmatch(r'[a-f0-9]{64}', receipt['original_hash']) is None
            or not isinstance(receipt.get('name'), str) or Path(receipt['name']).name != receipt['name']):
        return None
    expected_kind = {('ifind', 'filing_original'): 'filing', ('official', 'release_original'): 'material'}
    if expected_kind.get((receipt.get('source'), receipt.get('operation'))) != receipt['kind']:
        return None
    events = task.get('ledger', {}).get('dispatches', [])
    index = receipt['dispatch_index']
    if not 0 <= index < len(events):
        return None
    event = events[index]
    try:
        if (event.get('download_receipt') != receipt or event.get('kind') != 'data_requests'
                or event.get('batch') != receipt['batch'] or event.get('source') != receipt['source']
                or event.get('operation') != receipt['operation'] or event.get('fingerprint') != receipt['request_hash']
                or receipt['request_hash'] != fingerprint(dict(source=receipt['source'], operation=receipt['operation'],
                                                              parameters=receipt['parameters']))
                or not all(event.get(key) is True for key in
                           ('transport_isolated', 'network_request_started', 'network_worker_reaped'))
                or not timepoint(task['created_at']) <= timepoint(event['time']) <= timepoint(receipt['obtained_at']) <= timepoint(now())):
            return None
        if receipt['source'] == 'ifind':
            validate_filing_original_request(task, receipt['parameters'])
        else:
            from .official_sources import validate_request as validate_official_request
            validate_official_request(task, 'release_original', receipt['parameters'])
    except (ContractError, KeyError, TypeError, ValueError):
        return None
    return receipt


def original_download_recovery(task, original):
    """Locate an actual saved download for these retained bytes, never re-send.

    Repeated identical downloads retain distinct ledger entries. The earliest
    matching retained download supplies the original time; later sends are not
    erased or substituted for the source's first obtained time.
    """
    for event in task.get('ledger', {}).get('dispatches', []):
        receipt = _validated_original_download(task, event.get('download_receipt'))
        if (receipt is None or any(receipt[key] != original.get(key) for key in ('kind', 'name'))
                or receipt['original_id'] != original.get('id')
                or receipt['original_hash'] != original.get('sha256')
                or receipt['byte_count'] != original.get('byte_count')):
            continue
        if receipt['source'] == 'ifind':
            directory = validate_filing_original_request(task, receipt['parameters'])
            if directory != original.get('directory'):
                continue
            return dict(receipt=copy.deepcopy(receipt), directory=directory)
        from .official_sources import validate_request as validate_official_request
        origin = validate_official_request(task, 'release_original', receipt['parameters'])
        return dict(receipt=copy.deepcopy(receipt), origin=origin)
    return None


def _download_record_reference(task, record):
    """A completed local parse may use its own committed download receipt."""
    profile = _original_download_profile(record)
    receipt = _validated_original_download(task, record.get('download_receipt'))
    if (profile is None or receipt is None or task.get('research_mode') != 'current'
            or task.get('intent') == DOCUMENT_CONTENT_INTENT
            or record.get('task_id') != task['id'] or record.get('research_version') != task['version']
            or profile['version'] == 3 and task.get('official_channels_version') != 1
            or record.get('status') != '成功待核验'
            or any(record.get(key) != receipt[key] for key in ('kind', 'source', 'operation', 'parameters', 'request_hash', 'obtained_at'))
            or fingerprint(record['snapshot']) != profile['snapshot_hash']
            or profile['material'].get('name') != receipt['name']
            or profile['material'].get('status') != '已解析' or not profile['material'].get('parts')):
        return None
    try:
        material = profile['material']
        payload = base64.b64decode(material['original_base64'], validate=True)
        if (len(payload) != receipt['byte_count'] or hashlib.sha256(payload).hexdigest() != receipt['original_hash']
                or material.get('hash') != receipt['original_hash']):
            return None
        if profile['version'] == 2:
            directory = validate_filing_original_request(task, record['parameters'])
            if directory != record.get('directory') or directory != record['snapshot'].get('directory'):
                return None
        else:
            from .official_sources import validate_original
            validate_original(task, record)
        link = record.get('local_reparse')
        if link is not None:
            if (not isinstance(link, dict) or set(link) != {'version', 'original_id', 'reparsed_at'}
                    or type(link.get('version')) is not int or link['version'] != 2
                    or link.get('original_id') != receipt['original_id']
                    or not timepoint(receipt['obtained_at']) <= timepoint(link['reparsed_at']) <= timepoint(now())):
                return None
    except (ContractError, KeyError, ValueError, TypeError):
        return None
    return dict(kind='current_original', artifact_id=record['id'], snapshot_hash=profile['snapshot_hash'],
        original_hash=receipt['original_hash'], obtained_at=receipt['obtained_at'], research_version=task['version'],
        historical_availability_approved=False, event_causality_approved=False)


def _record_current_original(task, record, event):
    """Mint only after the actual isolated download and local parsing finish."""
    profile = _original_download_profile(record)
    if profile is None:
        return record
    material = profile['material']
    if (task.get("research_mode") != "current" or record.get("status") != "成功待核验"
            or profile['version'] == 3 and task.get('official_channels_version') != 1
            or event.get('kind') != 'data_requests' or event.get('source') != profile['source']
            or event.get('operation') != profile['operation'] or event.get('fingerprint') != record.get('request_hash')
            or material.get("status") != "已解析" or event.get("status") != "成功"
            or not all(event.get(key) is True for key in
                ("transport_isolated", "network_request_started", "network_worker_reaped"))):
        return record
    event.update(response_snapshot_hash=profile['snapshot_hash'], response_obtained_at=record["obtained_at"],
                 response_original_hash=material["hash"])
    record["acquisition"] = {"version": profile['version'], "task_id": task["id"], "research_version": task["version"],
        "batch": task["batch"], "dispatch_index": task["ledger"]["dispatches"].index(event),
        "request_hash": record["request_hash"], "snapshot_hash": profile['snapshot_hash'],
        "original_hash": material["hash"], "obtained_at": record["obtained_at"]}
    if profile['version'] == 3:
        record['acquisition'].update(source=profile['source'], operation=profile['operation'])
    return record


def _reparsed_original_reference(task, record):
    """Reuse a verified download's time for an exact local parse of its bytes.

    Parsing creates a new snapshot, never a network receipt or a financial
    approval. Every retained link must preserve source, task, version and bytes.
    """
    records = task.get('source_records', []) + task.get('pending_source_records', [])
    current = record
    seen = set()
    while 'local_reparse' in current:
        if current.get('id') in seen:
            return None
        seen.add(current.get('id'))
        matches = [item for item in records if item.get('id') == current.get('id')]
        link = current.get('local_reparse')
        if (len(matches) != 1 or matches[0] != current or not isinstance(link, dict)
                or set(link) != {'version', 'original_id', 'previous_artifact_id', 'previous_snapshot_hash', 'reparsed_at'}
                or type(link.get('version')) is not int or link['version'] != 1
                or not isinstance(link.get('original_id'), str) or not link['original_id']):
            return None
        parents = [item for item in records if item.get('id') == link['previous_artifact_id']]
        if len(parents) != 1:
            return None
        parent = parents[0]
        new_profile, old_profile = _original_download_profile(current), _original_download_profile(parent)
        if (new_profile is None or old_profile is None
                or current.get('task_id') != task['id'] or parent.get('task_id') != task['id']
                or current.get('research_version') != task['version'] or parent.get('research_version') != task['version']
                or new_profile['version'] != old_profile['version']
                or link['previous_snapshot_hash'] != old_profile['snapshot_hash']
                or current.get('status') != '成功待核验'
                or any(current.get(key) != parent.get(key) for key in
                       ('kind', 'source', 'operation', 'parameters', 'request_hash', 'obtained_at', 'directory'))
                or fingerprint(current['snapshot']) != new_profile['snapshot_hash']
                or new_profile['material'].get('status') != '已解析'
                or not new_profile['material'].get('parts')
                or new_profile['material'].get('hash') != old_profile['material'].get('hash')):
            return None
        try:
            payload = base64.b64decode(new_profile['material']['original_base64'], validate=True)
            if (hashlib.sha256(payload).hexdigest() != new_profile['material']['hash']
                    or payload != base64.b64decode(old_profile['material']['original_base64'], validate=True)
                    or not timepoint(current['obtained_at']) <= timepoint(link['reparsed_at']) <= timepoint(now())):
                return None
            if new_profile['version'] == 2:
                directory = validate_filing_original_request(task, current['parameters'])
                if directory != current['directory'] or directory != current['snapshot'].get('directory'):
                    return None
            else:
                from .official_sources import validate_original
                validate_original(task, current)
        except (ContractError, KeyError, ValueError, TypeError):
            return None
        current = parent
    reference = current_original_reference(task, current)
    if reference is None:
        return None
    profile = _original_download_profile(record)
    return dict(reference, artifact_id=record['id'], snapshot_hash=profile['snapshot_hash'])


def current_original_reference(task, record):
    """An import or old original cannot acquire a live download receipt."""
    if isinstance(record, dict) and 'download_receipt' in record:
        return _download_record_reference(task, record)
    if isinstance(record, dict) and 'local_reparse' in record:
        return _reparsed_original_reference(task, record)
    profile = _original_download_profile(record)
    if (profile is None or task.get("research_mode") != "current" or task.get("intent") == DOCUMENT_CONTENT_INTENT
            or profile['version'] == 3 and task.get('official_channels_version') != 1
            or record.get("status") != "成功待核验"
            or record.get("task_id") != task["id"] or record.get("research_version") != task["version"]):
        return None
    receipt = record.get("acquisition")
    keys = {"version", "task_id", "research_version", "batch", "dispatch_index", "request_hash",
            "snapshot_hash", "original_hash", "obtained_at"}
    if profile['version'] == 3:
        keys.update(('source', 'operation'))
    if not isinstance(receipt, dict) or set(receipt) != keys:
        return None
    index = receipt.get("dispatch_index")
    events = task.get("ledger", {}).get("dispatches", [])
    if type(index) is not int or not 0 <= index < len(events):
        return None
    event = events[index]
    material = profile['material']
    expected = dict(version=profile['version'], task_id=task["id"], research_version=task["version"], batch=event.get("batch"),
        dispatch_index=index, request_hash=record.get("request_hash"), snapshot_hash=profile['snapshot_hash'],
        original_hash=material.get("hash"), obtained_at=record.get("obtained_at"))
    if profile['version'] == 3:
        expected.update(source=profile['source'], operation=profile['operation'])
    if (receipt != expected or material.get("status") != "已解析" or not material.get("parts")
            or fingerprint(record.get("snapshot")) != profile['snapshot_hash']
            or fingerprint({"source": profile['source'], "operation": profile['operation'], "parameters": record.get("parameters")}) != record.get("request_hash")
            or event.get("kind") != "data_requests" or event.get("fingerprint") != record["request_hash"]
            or event.get("source") != profile['source'] or event.get("operation") != profile['operation']
            or event.get("status") != "成功" or event.get("response_snapshot_hash") != profile['snapshot_hash']
            or event.get("response_original_hash") != material.get("hash")
            or event.get("response_obtained_at") != record.get("obtained_at")
            or not all(event.get(key) is True for key in
                ("transport_isolated", "network_request_started", "network_worker_reaped"))):
        return None
    try:
        if profile['version'] == 2:
            directory = validate_filing_original_request(task, record["parameters"])
            if directory != record.get("directory") or directory != record["snapshot"].get("directory"):
                return None
        else:
            from .official_sources import validate_original
            validate_original(task, record)
        original = base64.b64decode(material["original_base64"], validate=True)
        if hashlib.sha256(original).hexdigest() != material["hash"]:
            return None
        if not timepoint(task["created_at"]) <= timepoint(record["obtained_at"]) <= timepoint(now()):
            return None
    except (ContractError, KeyError, ValueError, TypeError):
        return None
    return {"kind": "current_original", "artifact_id": record["id"], "snapshot_hash": profile['snapshot_hash'],
        "original_hash": material["hash"], "obtained_at": record["obtained_at"], "research_version": task["version"],
        "historical_availability_approved": False, "event_causality_approved": False}


def _query_request_operations():
    """Registered request lineage, deliberately broader than numeric fields."""
    return (set(FIELD_REGISTRY) | set(FINANCIAL_REQUEST_REGISTRY) | set(FINANCIAL_QUERY_REGISTRY) | set(IFIND_METADATA_REGISTRY)
            | set(ECONOMIC_REQUEST_REGISTRY) | set(SERIES_REQUEST_REGISTRY) | set(CALENDAR_REQUEST_REGISTRY) | set(INDEX_REQUEST_REGISTRY)
            | set(INTRADAY_REQUEST_REGISTRY) | {'ifind.get_thscode', 'ifind.report_query', 'official.release_catalog'}
            | set(ETF_LISTING_REGISTRY) | set(ETF_TRADING_REGISTRY))


def _query_response_received(record):
    if (record.get('source'), record.get('operation')) == ('official', 'release_catalog'):
        from .announcement_sources import CHANNELS
        parameters = record.get('parameters')
        if not isinstance(parameters, dict) or parameters.get('channel') not in CHANNELS:
            return False
    return (record.get('status') == '成功待核验'
            or (record.get('status') == record.get('catalog_status') == '成功但为空'
                and (record.get('source'), record.get('operation')) == ('ifind', 'report_query')))


def _record_current_acquisition(task, record, event):
    """Bind a completed isolated transport to its archived response."""
    operation = record["source"] + "." + record["operation"]
    mode = task.get("research_mode")
    allowed = _query_request_operations() if mode == "current" else (
        CALENDAR_REQUEST_REGISTRY if mode == "historical" else ())
    if not _query_response_received(record) or operation not in allowed:
        return record
    if not all(event.get(key) is True for key in ("transport_isolated", "network_request_started", "network_worker_reaped")):
        return record
    if event.get("status") != "成功":
        return record
    if record["source"] == "tinyshare" and not event.get("sdk_wire_requests_started", 0):
        return record
    event.update(response_snapshot_hash=record["hash"], response_obtained_at=record["obtained_at"])
    record["acquisition"] = {"version": 1, "task_id": task["id"], "research_version": task["version"],
        "batch": task["batch"], "dispatch_index": task["ledger"]["dispatches"].index(event),
        "request_hash": record["request_hash"], "snapshot_hash": record["hash"], "obtained_at": record["obtained_at"]}
    return record


def current_snapshot_reference(task, record):
    """Current availability is not first publication and cannot survive rebinding."""
    from .economic_observations import is_series, contract as economic_observations
    if is_series(record):
        observations = economic_observations(task, record)
        return observations.get('availability') if observations['status'] == 'registered_observations' else None
    if _is_basic_financial(record):
        if (type(task.get('fixed_financial_catalog_version')) is not int
                or task['fixed_financial_catalog_version'] != 1
                or _basic_financial_contract(record)['status'] != 'registered_constraints'
                or current_query_reference(task, record) is None):
            return None
        return _current_api_reference(task, record, {'ifind.basic_data_service'}, 'current_snapshot')
    if isinstance(record, dict) and record.get('source') == 'official' and record.get('operation') == 'etf_pcf':
        from .etf_sources import enabled
        if not enabled(task):
            return None
    if isinstance(record,dict) and record.get('source')=='official' and record.get('operation')=='index_file':
        from .index_sources import weight_evidence_enabled
        if not weight_evidence_enabled(task,record):return None
    return _current_api_reference(task, record, set(FIELD_REGISTRY), 'current_snapshot')


def current_query_reference(task, record):
    """Actual query identity only; never authorize raw-only numeric evidence."""
    reference = _current_api_reference(task, record, _query_request_operations(), 'query_request')
    if reference and record.get('source') == 'ifind' and 'ifind.'+str(record.get('operation')) in IFIND_METADATA_REGISTRY:
        if (type(record.get('research_version')) is not int or type(record.get('acquisition', {}).get('version')) is not int
                or type(record.get('acquisition', {}).get('research_version')) is not int):
            return None
        try:
            validate_metadata_request(task, record['operation'], record['parameters'], before=record)
            metadata_response(record['operation'], record['parameters'], record['snapshot'],
                allow_partial=partial_financial_enabled(task))
        except (ContractError, KeyError, ValueError, TypeError):
            return None
    return reference


def _current_api_reference(task, record, operations, kind):
    if task.get("research_mode") != "current":
        return None
    return _api_acquisition_reference(task, record, operations, kind)


def calendar_query_reference(task, record):
    """Prove the technical calendar query, including a retrospective query."""
    if task.get("research_mode") not in ("current", "historical"):
        return None
    reference = _api_acquisition_reference(task, record, CALENDAR_REQUEST_REGISTRY, "calendar_query")
    if reference is not None:
        receipt = record["acquisition"]
        if type(receipt["version"]) is not int or type(receipt["research_version"]) is not int:
            return None
        event = task["ledger"]["dispatches"][receipt["dispatch_index"]]
        if (event.get("source"), event.get("operation")) != (record["source"], record["operation"]):
            return None
    return reference


def _api_acquisition_reference(task, record, operations, kind):
    if task.get("intent") == DOCUMENT_CONTENT_INTENT or \
            not isinstance(record, dict) or record.get("kind") != "api" or not _query_response_received(record) or \
            str(record.get("source")) + "." + str(record.get("operation")) not in operations:
        return None
    receipt = record.get("acquisition")
    if not isinstance(receipt, dict) or set(receipt) != {"version", "task_id", "research_version", "batch",
            "dispatch_index", "request_hash", "snapshot_hash", "obtained_at"}:
        return None
    index = receipt.get("dispatch_index")
    events = task.get("ledger", {}).get("dispatches", [])
    if type(index) is not int or not 0 <= index < len(events):
        return None
    event = events[index]
    expected = {"version": 1, "task_id": task["id"], "research_version": task["version"],
        "batch": event.get("batch"), "dispatch_index": index, "request_hash": record.get("request_hash"),
        "snapshot_hash": record.get("hash"), "obtained_at": record.get("obtained_at")}
    if receipt != expected or record.get("task_id") != task["id"] or record.get("research_version") != task["version"] or \
            fingerprint(record.get("snapshot")) != record.get("hash") or \
            fingerprint({"source": record.get("source"), "operation": record.get("operation"),
                         "parameters": record.get("parameters")}) != record.get("request_hash"):
        return None
    if event.get("kind") != "data_requests" or event.get("fingerprint") != record["request_hash"] or \
            event.get("status") != "成功" or event.get("response_snapshot_hash") != record["hash"] or \
            event.get("response_obtained_at") != record["obtained_at"] or not all(event.get(key) is True for key in
                ("transport_isolated", "network_request_started", "network_worker_reaped")):
        return None
    if record["source"] == "tinyshare" and not event.get("sdk_wire_requests_started", 0):
        return None
    try:
        if not timepoint(task["created_at"]) <= timepoint(record["obtained_at"]) <= timepoint(now()):
            return None
    except (ContractError, KeyError):
        return None
    return {"kind": kind, "artifact_id": record["id"], "snapshot_hash": record["hash"],
            "obtained_at": record["obtained_at"], "research_version": task["version"]}


def validate_reporting_period_basis(item, record):
    """Validate an explicitly selected literal duration, never infer a period."""
    if 'reporting_period_basis' not in item:
        return
    basis = item['reporting_period_basis']
    fields = {'kind', 'artifact_id', 'snapshot_hash', 'part', 'quote'}
    interval = isinstance(basis, dict) and 'version' in basis
    if interval:
        if type(basis['version']) is not int or basis['version'] not in (2,3,4):
            raise ContractError('报告期间依据版本未登记')
        fields = fields | {'version'}
    if (not isinstance(basis, dict) or set(basis) != fields or basis['kind'] != 'material'
            or basis['artifact_id'] != record['id'] or basis['snapshot_hash'] != record.get('snapshot_hash', record['hash'])
            or not isinstance(basis['part'], str) or not isinstance(basis['quote'], str)
            or not basis['quote'].strip()):
        raise ContractError('报告期间依据须绑定同一原件、快照及实际原文定位')
    material=record['snapshot'].get('material') if record.get('kind')=='filing' else record['snapshot']
    text = next((part['text'] for part in material['parts']
                 if part['locator'] == basis['part']), '')
    if interval:
        if basis['version']==4:
            from .financial_sources import _literal_financial_period
            if (basis['quote'] not in text or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(item.get('period','')))
                    or _literal_financial_period(basis['quote'])!=item['period']):
                raise ContractError('余额日期须由同原件单个明确日期逐字规范，不推断期间')
            return
        from .financial_sources import literal_interval
        if basis['quote'] not in text or item.get('period') != literal_interval(
                basis['quote'],allow_shared_year=basis['version']==3):
            raise ContractError('规范报告区间须逐项对应同原件完整起止日期，不推断期间')
        return
    period = re.sub(r'\s+', '', str(item.get('period', '')))
    if (basis['quote'] not in text or not period
            or period not in re.sub(r'\s+', '', basis['quote'])):
        raise ContractError('报告期间名称须逐字定位同原件，不推断季度、累计或财政年度')


def original_date_is_nonobservational(text, quote, observed, matches=None):
    """Share the existing rejection rule with date selection; never approve dates."""
    if not isinstance(quote, str) or not quote or quote not in text:
        return False
    if matches is None:
        matches = [match for match in re.finditer(PUBLICATION_DATE_PATTERN, quote)
                   if _publication_date_value(match.group()) == observed]
    if not matches:
        return False
    offset = text.index(quote)
    labels = re.compile(r'(?:送出|发布|公告|披露|签署|[生⽣]效|编制)[日⽇]期')
    return all(labels.search(text[:offset + match.start()].split('\n')[-1]) for match in matches)


def validate_current_snapshot(item, task, records=None):
    if 'source_label' in item and item.get('date_role') != 'document_source_label':
        raise ContractError('来源标注字段须使用登记的来源标注角色')
    reference = item.get("availability")
    if reference is None:
        if 'date_role' in item:
            raise ContractError('原页标注日期须绑定本任务实际取得的当前原件')
        if 'reporting_period_basis' in item:
            raise ContractError('独立报告期间依据仅用于实际取得的当前原件')
        return False
    registered = task.get("source_records", []) if records is None else records
    record = _unique_artifact(registered, _evidence_artifact_id(item))
    expected = (current_original_reference(task, record) if reference.get("kind") == "current_original"
                else current_snapshot_reference(task, record)) if isinstance(reference, dict) else None
    if expected is None or reference != expected or \
            "snapshot_hash" in item and item["snapshot_hash"] != expected["snapshot_hash"] or \
            "artifact" in item and item["artifact"] != record:
        raise ContractError("当前版本可得依据未绑定本任务实际取数记录，导入快照或历史版本不能代替")
    if 'observation_date' in item and expected['kind'] != 'current_original':
        raise ContractError('独立观测日期仅用于本次实际取得的文字原件，不能覆盖接口原始日期')
    if 'reporting_period_basis' in item:
        if expected['kind'] != 'current_original':
            raise ContractError('独立报告期间依据不能覆盖接口日期或历史可得性')
        if (item['reporting_period_basis'].get('version')==3
                and task.get('original_duration_anchor_version')!=1):
            raise ContractError('本任务未登记共享年份报告区间')
        if item['reporting_period_basis'].get('version')==4:
            from .financial_data import statement_details_enabled
            if not statement_details_enabled(task):
                raise ContractError('本任务未登记财务余额的明确日期')
        validate_reporting_period_basis(item, record)
    publication = item.get("publication")
    if item.get("published_at") is not None or not isinstance(publication, dict) or any(
            publication.get(key) != value for key, value in
            (("at", None), ("basis_ref", None), ("precision", "未知"), ("timezone", "未知"), ("certainty", "未知"))) or \
            not isinstance(publication.get("basis"), str) or not publication["basis"].strip():
        raise ContractError("当前版本可得时间不能冒充首次公开时间，首次公开未知须继续保留未知")
    if 'date_role' in item:
        if expected['kind'] != 'current_original':
            raise ContractError('原页标注日期不能覆盖接口日期或历史信息集')
        from .source_labels import ROLE as SOURCE_LABEL_ROLE, validate as validate_source_label
        if item['date_role'] == SOURCE_LABEL_ROLE:
            validate_source_label(item, task, record)
        else:
            if 'source_label' in item: raise ContractError('来源标注不能借其他日期角色绕过核验')
            from .document_dates import validate
            validate(item, task, record)
        return True
    if 'observation_period' in item:
        if expected['kind'] != 'current_original':
            raise ContractError('统计所属月不能覆盖接口原始日期或历史可得性')
        from .official_sources import validate_statistical_period
        validate_statistical_period(task, record, item)
        return True
    market_zone = timezone(timedelta(hours=8))
    observed = item.get('observation_date', item.get('period')) if expected['kind'] == 'current_original' else item.get('period')
    if _is_basic_financial(record):
        cell = _basic_financial_cell(record, item.get('record_locator', {}))
        if item.get('period') != cell['period']:
            raise ContractError('财务累计期间与原始D参数及字段定义不一致')
        observed = cell['report_end']
    from .economic_observations import is_series, cell as economic_cell
    if is_series(record):
        selected = economic_cell(task, record, item.get('record_locator'))
        if item.get('period') != selected['period']:
            raise ContractError('宏观统计期与原始日期及频率定义不一致')
        observed = selected['economic_context']['observation_date']
    if timepoint(observed).astimezone(market_zone).date() > timepoint(task["cutoff"]).astimezone(market_zone).date():
        raise ContractError("当前快照不能采用研究观测窗口后的数据")
    if expected["kind"] == "current_original":
        locator = item.get("record_locator", {})
        quote, part = locator.get("period_quote"), locator.get("period_part")
        parts = _original_download_profile(record)['material']['parts']
        text = next((p["text"] for p in parts if p["locator"] == part), "")
        matching_dates = ([match for match in re.finditer(PUBLICATION_DATE_PATTERN, quote)
                           if _publication_date_value(match.group()) == observed]
                          if isinstance(quote,str) else [])
        if task.get('original_duration_anchor_version')==1:
            from .financial_sources import original_date_is_interval_boundary,literal_interval
            basis=item.get('reporting_period_basis',{})
            duration=(basis.get('version') in (2,3) and basis.get('part')==part
                and isinstance(quote,str) and quote in basis.get('quote','')
                and item.get('period','').split('/')[-1]==observed)
            if duration and basis.get('version')==3 and quote==basis['quote']:
                interval=literal_interval(quote,allow_shared_year=True)
                if interval==item['period'] and interval.split('/')[-1]==observed:
                    matching_dates=[re.fullmatch(r'[\s\S]+',quote)]
            if not duration:
                from .financial_data import product_selection_enabled, duration_observation_applies
                if product_selection_enabled(task) and duration_observation_applies(item, record):
                    duration = True
            if not duration and original_date_is_interval_boundary(text,quote,observed):
                raise ContractError('原文区间的起止日期不能充当单日观测，请选择完整报告期间')
        if task.get('official_policy_channels_version')==1:
            from .official_sources import fomc_statement_date_match
            header=fomc_statement_date_match(record,text,quote,observed)
            if header is not None:
                matching_dates.append(header)
        if (not isinstance(quote, str) or not quote.strip() or quote not in text
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(observed))
                or not matching_dates):
            raise ContractError("当前原件须逐字定位明确观测日期，不能用获取日期或报告标题猜测")
        if original_date_is_nonobservational(text, quote, observed, matching_dates):
            raise ContractError('送出、发布、签署或生效日期不能充当原件数据观测日期')
        if 'observation_date' in item and item.get('period') != observed:
            period = re.sub(r'\s+', '', str(item.get('period', '')))
            basis = item.get('reporting_period_basis') or item.get('calculation_context_basis', {}).get('period', {})
            statement = re.sub(r'\s+', '', str(item.get('text', '')) + text)
            if not period:
                raise ContractError('当前原件的所属期间不能为空')
            if period not in statement:
                # A different page can establish the financial duration, but
                # a free-form quote or another original cannot supply it.
                basis_text = next((p['text'] for p in parts if p['locator'] == basis.get('part')), '')
                basis_quote = basis.get('quote')
                canonical_interval = type(basis.get('version')) is int and basis['version'] in (2,3)
                if canonical_interval:
                    validate_reporting_period_basis(item,record)
                    if period.split('/')[-1] != observed:
                        raise ContractError('规范报告区间的终点必须与同原件观测锚点相同')
                elif (basis.get('kind') != 'material' or basis.get('artifact_id') != record['id']
                        or basis.get('snapshot_hash') != expected['snapshot_hash']
                        or not isinstance(basis_quote, str) or not basis_quote.strip()
                        or basis_quote not in basis_text
                        or period not in re.sub(r'\s+', '', basis_quote)):
                    raise ContractError('财务所属期间须在同原件明确定位，不从观测日期猜测单季度或累计期间')
    return True


DIRECT_CODE_REFERENCE_VERSION = 1
CURRENT_MARKET_OBSERVATION_VERSION = 1
DIRECT_CODE_OPERATIONS = frozenset({
    'ifind.cmd_history_quotation', 'ifind.basic_data_service', 'tinyshare.daily',
    'tinyshare.fund_daily', 'tinyshare.daily_basic', 'official.etf_pcf',
})
DIRECT_CODE_LIMITATION = (
    '仅为本次取得的指定供应商完整代码行情快照，不证明历史证券身份、名称转换、证券归属、'
    '基金持仓、产业暴露或因果；未复权价格变化不等于持有人总回报。')
CANDIDATE_CODE_LIMITATION = (
    '仅核对实际发现名单中完整供应商代码的当前取得行情，名单仅提供查询出处；'
    '不确认名称映射、历史身份、行业或指数成分资格、名单完整性、权重、产业暴露及因果。'
    '不得把名单返回日当首次公开日，未复权价格变化不等于持有人总回报。')


def candidate_code_references(task, record):
    """Bind an explicitly selected quote to actual prior discovery rows.

    This is a provider-code observation, not an issuer/name mapping or an
    adoption of the discovery API's raw financial/industry/member fields.
    The model must select one exact reference; no missing identity is repaired.
    """
    if (type(task.get('candidate_code_observation_version')) is not int
            or task['candidate_code_observation_version'] != 1
            or type(task.get('version')) is not int
            or record.get('source') != 'ifind' or record.get('operation') not in ('cmd_history_quotation', 'basic_data_service')
            or current_snapshot_reference(task, record) is None
            or type(record.get('research_version')) is not int):
        return []
    records = task.get('source_records', [])
    declaration = task.get('original_input')
    if (len([row for row in records if row.get('id') == record['id']]) != 1
            or not any(row == record for row in records)
            or not isinstance(declaration, dict) or declaration.get('question') != task.get('question')
            or not isinstance(declaration.get('objects'), list)
            or declaration.get('objects') != task.get('objects')):
        return []
    code = record.get('parameters', {}).get('codes')
    names_enabled = (type(task.get('name_candidate_observation_version')) is int
        and task['name_candidate_observation_version'] == 1
        and type(task.get('identity_candidate_lookup_version')) is int
        and task['identity_candidate_lookup_version'] == 2)
    code_pattern = r'(?:\d{6}\.(?:SH|SZ)|\d{4,5}\.HK)' if names_enabled else r'\d{6}\.(?:SH|SZ)'
    if not isinstance(code, str) or not re.fullmatch(code_pattern, code) or not subject_allowed(task, code):
        return []
    from .object_discovery import retained_discovery_results, callable_discovery_codes
    from .index_sources import retained_official_member_sources
    official_members={row['artifact_id']:row['members'] for row in retained_official_member_sources(task)}
    discovery = {row['artifact_id']: row for row in retained_discovery_results(task)}
    discovery_codes = set(callable_discovery_codes(task))
    name_candidates = {row['artifact_id']:row for row in identity_candidates(task)} if names_enabled else {}
    choices = []
    for parent in records:
        if ((parent.get('source') != 'ifind' and parent.get('id') not in official_members) or parent.get('task_id') != task['id']
                or type(parent.get('research_version')) is not int or parent['research_version'] != task['version']
                or len([row for row in records if row.get('id') == parent.get('id')]) != 1
                or current_query_reference(task, parent) is None
                or parent['acquisition']['dispatch_index'] >= record['acquisition']['dispatch_index']
                or timepoint(parent['obtained_at']) > timepoint(record['obtained_at'])):
            continue
        if parent.get('id') in official_members:
            locations=[dict(excel_row=row['excel_row']) for row in official_members[parent['id']] if row['code']==code]
        elif parent.get('operation') == 'data_pool':
            try:
                from .index_sources import index_response
                members = index_response(task, parent['parameters'], parent['snapshot'])['members']
            except (ContractError, KeyError, TypeError, ValueError):
                continue
            locations = [dict(row=row['row']) for row in members if row['code'] == code]
        elif parent.get('operation') == 'smart_stock_picking' and code in discovery_codes:
            members = discovery.get(parent['id'], {}).get('candidates', [])
            locations = [location for row in members if row['code'] == code for location in row['row_locations']]
        elif parent.get('operation') == 'get_thscode' and code in name_candidates.get(parent['id'],{}).get('exact_lookup_codes',[]):
            # All candidates remain recorded. Only a later explicit quote of
            # this exact full code can establish a provider-code observation.
            # It cannot manufacture a unique name, market or listing period.
            locations = [dict(table=table_index,cell=cell_index,token=token_index)
                for table_index,row in enumerate(parent['snapshot']['tables'])
                for cell_index,value in enumerate(row['table']['thscode'])
                for token_index,token in enumerate(value.split(',')) if token == code]
        else:
            continue
        if not locations:
            continue
        for location in locations:
            choices.append(dict(version=1, kind='candidate_provider_observation', **identity_scope(task),
                declaration_hash=fingerprint(declaration), artifact_id=record['id'], snapshot_hash=record['hash'],
                request_hash=record['request_hash'], provider_code=code, object=code,
                discovery=dict(artifact_id=parent['id'], snapshot_hash=parent['hash'],
                    request_hash=parent['request_hash'], operation=parent['operation'], row_locator=location)))
    return choices


def validate_candidate_code_identity(item, task, artifact):
    identity = item.get('identity')
    if (not isinstance(identity, dict) or set(identity) != {'mode', 'provider_code', 'object', 'basis_ref'}
            or identity.get('mode') != 'candidate_provider_observation'):
        raise ContractError('候选代码观察不能混入名称映射、有效期或自填依据')
    reference = identity.get('basis_ref')
    if (not isinstance(reference, dict) or any(type(reference.get(key)) is not int
            for key in ('version', 'research_version'))
            or not _candidate_code_reference_matches(task, artifact, reference)
            or identity['provider_code'] != reference['provider_code']
            or identity['object'] != reference['object'] or item.get('object') != reference['object']):
        raise ContractError('候选代码观察须引用同任务真实先行名单及后续行情，不能补填证券身份')
    if item.get('nature') != '已实现事实' or not validate_current_snapshot(item, task):
        raise ContractError('候选代码观察只核对当前取得的原始行情，不批准历史或因果用途')
    return True


def _candidate_code_reference_matches(task, artifact, reference):
    # Python dict equality treats False as 0. Wire/Skill references must retain
    # exact JSON types, including nested row locations, not only equal values.
    try:
        digest = fingerprint(reference)
        return any(digest == fingerprint(expected) for expected in candidate_code_references(task, artifact))
    except (TypeError, ValueError, RecursionError):
        return False


def expand_candidate_code_reference(task, artifact, spec):
    if 'candidate_code_reference' not in spec:
        return spec
    if any(key in spec for key in ('identity', 'identity_basis_reference', 'direct_code_reference')):
        raise ContractError('候选代码引用不得与名称映射或用户直接代码引用混用')
    reference = spec['candidate_code_reference']
    if (not isinstance(reference, dict) or any(type(reference.get(key)) is not int
            for key in ('version', 'research_version'))
            or not _candidate_code_reference_matches(task, artifact, reference)
            or spec.get('object') != reference['object']):
        raise ContractError('候选代码引用不属于本任务同版实际发现及行情来源')
    expanded = copy.deepcopy(spec)
    expanded.pop('candidate_code_reference')
    expanded['identity'] = dict(mode='candidate_provider_observation', provider_code=reference['provider_code'],
        object=reference['object'], basis_ref=copy.deepcopy(reference))
    return expanded


def direct_code_reference(task, record):
    """A live observation of an explicitly supplied code, never a name mapping."""
    availability = current_snapshot_reference(task, record)
    if availability is None or record['source'] + '.' + record['operation'] not in DIRECT_CODE_OPERATIONS:
        return None
    originals = [item for item in task.get('source_records', []) if item.get('id') == record['id']]
    declaration = task.get('original_input')
    if (len(originals) != 1 or originals[0] != record or not isinstance(declaration, dict)
            or declaration.get('question') != task.get('question')
            or not isinstance(declaration.get('objects'), list)):
        return None
    code = record['parameters'].get('codes' if record['source'] == 'ifind' else 'ts_code')
    if (not isinstance(code, str) or not re.fullmatch(r'(?:\d{6}\.(?:SH|SZ)|\d{4,5}\.HK)', code)
            or code not in declared_security_codes(declaration,
                encoding=task.get('security_declaration_encoding', 1))):
        return None
    # Calculation binding can refine the mutable plan before its aggregate
    # hash is refreshed. The immutable user declaration and identity_scope
    # above are this reference's boundary, not a transient plan fingerprint.
    result = {'version': DIRECT_CODE_REFERENCE_VERSION, 'kind': 'direct_provider_observation',
        **identity_scope(task), 'declaration_hash': fingerprint(declaration),
        'artifact_id': record['id'], 'snapshot_hash': record['hash'], 'request_hash': record['request_hash'],
        'provider_code': code, 'object': code}
    if task.get('security_declaration_encoding') == 2:
        result.update(version=2, declaration_encoding=2)
    return result


def validate_direct_code_identity(item, task, artifact):
    identity = item.get('identity')
    if (not isinstance(identity, dict) or set(identity) != {'mode', 'provider_code', 'object', 'basis_ref'}
            or identity.get('mode') != 'direct_provider_observation'):
        raise ContractError('直接代码观察不能混入身份映射有效期或自填依据')
    expected = direct_code_reference(task, artifact)
    if (expected is None or identity['basis_ref'] != expected
            or any(type(identity['basis_ref'].get(key)) is not int for key in ('version', 'research_version'))):
        raise ContractError('直接代码观察未绑定用户明确完整代码及本任务实际取数，不用于名称映射或历史研究')
    if identity['provider_code'] != expected['provider_code'] or identity['object'] != expected['object'] or item.get('object') != expected['object']:
        raise ContractError('直接代码观察的对象与请求代码不一致')
    if item.get('nature') != '已实现事实' or not validate_current_snapshot(item, task):
        raise ContractError('直接代码观察只能核对当前取得的原始行情事实，不能改变可得性或材料性质')
    return True


def expand_direct_code_reference(task, artifact, spec):
    """Expand a deliberate reference; never repair an incomplete mapping."""
    if 'direct_code_reference' not in spec:
        return spec
    if 'identity' in spec or 'identity_basis_reference' in spec:
        raise ContractError('直接代码引用不能与身份映射格式混用')
    reference = spec['direct_code_reference']
    expected = direct_code_reference(task, artifact)
    if (not isinstance(reference, dict) or expected is None or reference != expected
            or any(type(reference.get(key)) is not int for key in ('version', 'research_version'))
            or spec.get('object') != expected['object']):
        raise ContractError('直接代码引用不属于本次明确代码的当前行情快照')
    expanded = copy.deepcopy(spec)
    expanded.pop('direct_code_reference')
    expanded['identity'] = {'mode': 'direct_provider_observation', 'provider_code': expected['provider_code'],
        'object': expected['object'], 'basis_ref': copy.deepcopy(reference)}
    return expanded


def validate_ifind_identity_request(task, params):
    """Single exact lookup from user input, not an instrument-universe search."""
    if not isinstance(params, dict) or "functionpara" not in params or not isinstance(params["functionpara"], dict):
        raise ContractError("身份转换须使用已登记的查询与功能参数")
    settings = params["functionpara"]
    mode = settings.get("mode")
    if mode not in ("seccode", "secname") or set(params) != {mode, "functionpara"}:
        raise ContractError("身份转换仅支持单个证券代码或名称，不接受自定义字段")
    expected = IDENTITY_PARAMETERS_BY_MODE[mode]
    candidate_lookup = (identity_candidate_lookup_enabled(task) and mode == 'secname'
        and settings == dict(expected,isexact='0'))
    if settings != expected and not candidate_lookup:
        raise ContractError("身份转换只作精确匹配，不扫描市场、不隐式筛除历史证券")
    query = params[mode]
    if not isinstance(query, str) or not query.strip() or query != query.strip() or len(query) > 80 or any(char in query for char in ",;\n\r"):
        raise ContractError("身份转换须为一个明确代码或名称")
    if not _identity_query_allowed(task, mode, query):
        raise ContractError('用户已排除此对象，本轮不查询其证券身份')
    texts = [scope_question(task), *task.get("objects", [])]
    from .object_discovery import identity_query_candidates
    candidates = identity_query_candidates(task)
    if not candidate_lookup:
        candidates += [dict(code=code,name=item['query']) for item in identity_candidates(task) for code in item['exact_lookup_codes']]
    candidate_values = {candidate['code'].split('.')[0] if mode == 'seccode' else candidate['name']
                        for candidate in candidates if subject_allowed(task, candidate['code'])}
    if mode == "seccode":
        if not re.fullmatch(r"\d{4,6}", query) or not (query in candidate_values or any(re.search(r"(?<!\d)" + re.escape(query) + r"(?!\d)", text) for text in texts)):
            raise ContractError("身份转换代码须来自用户输入，不能猜测或扩大对象范围")
    elif len(query) < 2 or not (query in candidate_values or _identity_name_in_scope(task, query)):
        raise ContractError("身份转换名称须来自用户输入，不由模型新增候选")
    return query, mode


def validate_ifind_report_request(task, params, *, require_identity=True):
    """Single-security query with explicit, documented type-filter selection."""
    if not isinstance(params, dict) or set(params) != {"codes", "functionpara", "beginrDate", "endrDate", "outputpara"}:
        raise ContractError("公告查询仅接受已登记的代码、类型、日期和七项输出字段")
    code = params["codes"]
    codes = set(available_security_codes(task))
    if (not isinstance(code, str) or not re.fullmatch(r"(?:\d{6}\.(?:SH|SZ)|\d{4,5}\.HK)", code)
            or require_identity and code not in codes):
        raise ContractError("公告查询须指定本任务明确的单只SH/SZ或HK证券代码")
    selected = params['functionpara']
    if (type(task.get('filing_keyword_filter_version')) is int
            and task['filing_keyword_filter_version'] == 1 and isinstance(selected, dict)
            and 'keyWord' in selected):
        keyword = selected['keyWord']
        if (not isinstance(keyword, str) or not 1 <= len(keyword) <= 40 or keyword != keyword.strip()
                or any(ord(character) < 32 or ord(character) == 127 for character in keyword)):
            raise ContractError('公告标题关键词须为1至40字的明确文字，不含控制字符或首尾空白')
        selected = {key: value for key, value in selected.items() if key != 'keyWord'}
    if not isinstance(selected, dict) or selected not in ({}, {"reportType": "901"}):
        raise ContractError("公告类型过滤须显式为空对象或reportType=901，不猜类型、不扫描板块")
    profiles = filing_filter_profiles(task, [code])
    if profiles is not None and selected not in profiles[0]['functionpara_options']:
        raise ContractError('该代码的公告类型过滤未登记，不把901套用于基金报告，也不自动改为其他过滤重派')
    outputs = params["outputpara"]
    if not isinstance(outputs, str) or len(outputs.split(",")) != 7 or set(outputs.split(",")) != {key + ":Y" for key in IFIND_REPORT_FIELDS}:
        raise ContractError("公告查询必须明确请求七项已登记原始字段")
    for key in ("beginrDate", "endrDate"):
        if not isinstance(params[key], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", params[key]):
            raise ContractError("公告查询日期须为YYYY-MM-DD")
        timepoint(params[key])
    if params["beginrDate"] > params["endrDate"] or params["endrDate"] > timepoint(task.get("cutoff")).astimezone(timezone(timedelta(hours=8))).date().isoformat():
        raise ContractError("公告查询窗口倒置或超过研究截止日期")
    return [params["beginrDate"], params["endrDate"]]


_DIRECT_FILING_OPTION = re.compile(
    r'(?:(?:仅|只|优先)?(?:用|使用)iFinD(?:查询)?|(?:不|别|不要|无需)下载PDF|'
    r'(?:可|允许|不用|不使用)(?:查|查询|使用|用)?(?:官方(?:渠道|公告|目录|来源)?|巨潮(?:资讯)?|披露易)|'
    r'(?:不|不要|无需)(?:分析|核对)正文|(?:生成|整理成)(?:一份|一页)?报告)', re.I)
_DIRECT_FILING_QUESTION = re.compile(
    r'(?:请|帮我)?(?:(?:仅|只)?(?:使用|用)iFinD)?(?:仅|只)?'
    r'(?:列出|列一下|查询|查找|查一下|查看|核对|获取|查齐|查全|找全|查)'
    r'(?:@标的(?:从|自|在|于|的)?@日期(?:期间)?|(?:从|自|在|于)?@日期(?:期间)?@标的)'
    r'(?:的)?(?:公告目录|公告清单|公告列表|披露目录|披露清单|'
    r'(?:全部|所有)(?:公告(?:目录|清单|列表)?|披露(?:目录|清单)?)|'
    r'(?:全部|所有)?公告标题(?:及|与|和)(?:公告)?日期)'
    r'(?:并(?:生成|整理成)(?:一份|一页)?报告)?', re.I)


def filing_directory_scope(task, *, clause=None):
    """Compile one literal directory request with no disclosure analysis."""
    from .draft_sources import local_scope_text
    from .numeric_integrity import draft_day_intervals

    if (task.get('draft_first_version') != 1 or task.get('research_mode') != 'current'
            or task.get('intent') != '综合研究' or task.get('plan_version') != 1
            or task.get('materials') or task.get('pending_materials') or task.get('update_from')
            or task.get('calculation_specs') or task.get('requested_theses')):
        return None
    questions = task['questions']
    if len(questions) != 1:
        return None
    question, = questions
    needs = question['evidence_needs']
    if (not question['required'] or question['question'] != task['question'] or question.get('answer_scope')
            or question['method_needs'] or question.get('evidence_need_constraints') or question.get('evidence_period_targets')
            or any(question.get('evidence_need_scopes', {}).get(need) != 'query_provenance' for need in needs)
            or question['kind'] != 'fact' and task.get('question_origin') != 'default'):
        return None
    text = scope_question(task) if clause is None else clause
    clauses = re.split(r'[，,。；;！？!?\n]+', local_scope_text(text))
    clauses = [re.sub(r'\s+', '', part) for part in clauses]
    main = [part for part in clauses if part and not _DIRECT_FILING_OPTION.fullmatch(part)]
    if len(main) != 1:
        return None
    sentence, = main
    codes = security_code_mentions(sentence, encoding=task.get('security_declaration_encoding', 1))
    declared = declared_security_codes(task)
    if len(codes) != 1 or codes[0] not in declared:
        return None
    code, = codes
    if clause is None and (declared != codes or any(item != code for item in task['objects'])):
        return None
    intervals = draft_day_intervals(sentence)
    if len(intervals) != 1 or sentence.count(code) != 1:
        return None
    left, right, start, end = intervals[0]
    normalized = (sentence[:left] + '@日期' + sentence[right:]).replace(code, '@标的')
    if end.isoformat() > market_date(task['cutoff']) or not _DIRECT_FILING_QUESTION.fullmatch(normalized):
        return None
    return dict(codes=code, functionpara={}, beginrDate=start.isoformat(), endrDate=end.isoformat(),
                outputpara=FILING_REQUEST_REGISTRY['ifind.report_query']['parameters']['outputpara'])


def filing_directory_result(task, parameters=None, *, include_official=True):
    """Project verified directory metadata and coverage for the requested window."""
    if parameters is None:
        parameters = filing_directory_scope(task)
        if parameters is None:
            return None
    result = _ifind_directory_result(task, parameters)
    if not include_official:
        return result
    from .announcement_sources import directory_coverage, directory_query, exhaustive_directory

    if exhaustive_directory(task, parameters['codes']):
        query = directory_query(task, parameters)
        official = directory_coverage(task, query) if query else None
        result.update(ifind_status=result['status'], official=official)
        if official and official['complete']:
            result['status'] = 'returned' if result['rows'] or official['rows'] else 'empty'
        elif result['status'] != 'unavailable' or official and official['source_refs']:
            result['status'] = 'partial'
    return result


def _ifind_directory_result(task, parameters):
    question_id = task['questions'][0]['id']
    result = dict(status='unavailable', rows=[], rejected=0)
    matches = [row for row in task['source_records'] if row.get('parameters') == parameters
               and (row.get('source'), row.get('operation')) == ('ifind', 'report_query')
               and ('question_ids' not in row or question_id in row['question_ids'])
               and current_query_reference(task, row) is not None]
    if len(matches) != 1:
        return result
    record, = matches
    if sum(row.get('id') == record['id'] for row in task['source_records']) != 1:
        return result
    try:
        catalogue = _verified_filing_catalog(task, record)
    except ContractError:
        return result
    rows = []
    rejected = catalogue['catalog_coverage'].get('invalid_location_count', 0)
    for entry in catalogue['catalog_entries']:
        day, title = entry['report_date'], entry['title']
        if (entry['issuer'] != parameters['codes'] or not isinstance(day, str)
                or not parameters['beginrDate'] <= day <= parameters['endrDate']
                or '公告日期格式未知' in entry['gaps'] or not isinstance(title, str) or not title.strip()):
            rejected += 1
            continue
        rows.append(dict(date=day, title=title, source_id=entry['source_id']))
    return dict(status='partial' if rejected else 'returned' if rows else 'empty',
                rows=sorted(rows, key=lambda row: row['date'], reverse=True),
                rejected=rejected, source_ref=record['id'])


def _filing_block_rows(value, path):
    if not isinstance(value, dict):
        raise ContractError("公告目录须为明确列名的记录或table列数组")
    if set(IFIND_REPORT_FIELDS).issubset(value):
        if any(isinstance(value[key], (dict, list)) for key in IFIND_REPORT_FIELDS):
            raise ContractError("公告目录行字段不能混入列数组")
        yield value, path, {key: path + [key] for key in IFIND_REPORT_FIELDS}
        return
    if not isinstance(value.get("table"), dict):
        raise ContractError("公告目录字段不完整或结构未知，不认作空目录")
    columns = value["table"]
    required = set(IFIND_REPORT_FIELDS)
    scalar_code = "thscode" not in columns and isinstance(value.get("thscode"), str)
    if scalar_code:
        required.remove("thscode")
    if not required.issubset(columns) or any(not isinstance(columns[key], list) for key in required):
        raise ContractError("公告目录列名或数组缺失，不按位置猜测字段")
    lengths = {len(columns[key]) for key in required}
    if len(lengths) != 1:
        raise ContractError("公告目录列数组长度不一致")
    for row in range(next(iter(lengths))):
        record = {key: columns[key][row] for key in required}
        mapping = {key: path + ["table", key, row] for key in required}
        if scalar_code:
            record["thscode"], mapping["thscode"] = value["thscode"], path + ["thscode"]
        yield record, path + ["table", row], mapping


def _filing_rows(payload, *, on_invalid=None):
    """Only explicit names are recognized; positional arrays are not a schema."""
    roots = [key for key in ("tables", "data") if key in payload]
    if len(roots) != 1 or not isinstance(payload[roots[0]], list):
        raise ContractError("公告目录响应根结构未登记，不能认作空目录")
    root = roots[0]
    for index, value in enumerate(payload[root]):
        path = [root, index]
        try:
            yield from _filing_block_rows(value, path)
        except ContractError as error:
            if on_invalid is None:
                raise
            on_invalid(value, path, str(error))


def _filing_catalog(task, record):
    """Keep the raw snapshot even when a new provider structure is unsupported."""
    result = {"catalog_entries": [], "catalog_status": "受限", "catalog_limitations": [
        "仅文档与离线结构契约支持，真实HTTP响应结构和权限未验收；目录命中不等于正文证据。",
        "reportDate为公告日期，ctime为发布时间；不以获取时间或公告日期补造盘中发布时间。",
        "单次返回不证明窗口内公告完整覆盖，不自动分页、批量下载或推定修订关系。"],
        "catalog_coverage": {"requested": copy.deepcopy(record["parameters"]), "returned_rows": 0,
                             "completeness": "未证实", "report_dates": []}}
    business_code = record["snapshot"].get("errorcode")
    if isinstance(business_code, bool) or business_code not in (0, "0"):
        return result
    version = record.get("catalog_read_version")
    if version is not None and (type(version) is not int or version != 1):
        raise ContractError("公告目录读取版本未登记")
    partial = version == 1
    entries, ids, duplicate_ids = [], set(), set()
    issues = []
    issue_count = 0

    def note_issue(path, reason):
        nonlocal issue_count
        issue_count += 1
        if len(issues) < 20:
            issues.append(dict(row_locator=path, reason=reason))

    def register_id(value):
        if type(value) not in (str, int) or not str(value).strip():
            return None
        source_id = str(value)
        if source_id in ids:
            duplicate_ids.add(source_id)
        ids.add(source_id)
        return source_id

    def invalid_block(value, path, reason):
        # Even an unreadable row/block can contain a conflicting selector.
        # Count its explicit seq values without aligning any malformed columns.
        if isinstance(value, dict):
            register_id(value.get("seq"))
            columns = value.get("table")
            if isinstance(columns, dict) and isinstance(columns.get("seq"), list):
                for source_id in columns["seq"]:
                    register_id(source_id)
        note_issue(path, reason)

    try:
        privacy = record["snapshot"].get("_researchhelper_privacy", {})
        if not isinstance(privacy, dict) or not isinstance(privacy.get("redacted_url_paths", []), list):
            raise ContractError("业务快照的地址脱敏标记异常")
        for raw, path, mapping in _filing_rows(record["snapshot"], on_invalid=invalid_block if partial else None):
            source_id = register_id(raw["seq"])
            issue = None
            if any(isinstance(raw[key], (dict, list, bool)) for key in IFIND_REPORT_FIELDS):
                issue = "公告目录原始字段类型异常"
            elif source_id is None or (source_id in duplicate_ids and not partial):
                issue = "公告目录seq缺失或重复，不能唯一选择原件"
            if issue:
                if not partial:
                    raise ContractError(issue)
                note_issue(path, issue)
                continue
            gaps = []
            for key in IFIND_REPORT_FIELDS:
                if raw[key] is None or raw[key] == "":
                    gaps.append(key + "缺失")
            report_date = raw["reportDate"]
            try:
                if not isinstance(report_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date):
                    raise ContractError("公告日期格式未知")
                timepoint(report_date)
                if not record["parameters"]["beginrDate"] <= report_date <= record["parameters"]["endrDate"]:
                    gaps.append("公告日期超出查询窗口")
            except ContractError:
                gaps.append("公告日期格式未知")
            published = raw["ctime"]
            try:
                if timepoint(published) > timepoint(task["cutoff"]):
                    gaps.append("发布时间超过研究截止时点")
            except ContractError:
                gaps.append("发布时间缺失、格式或时区不明确")
                published = None
            if raw["thscode"] != record["parameters"]["codes"]:
                gaps.append("响应证券身份超出请求范围")
            for key in ("secName", "reportTitle", "pdfURL"):
                if not isinstance(raw[key], str) or not raw[key].strip():
                    gaps.append(key + "不是明确文本")
            redacted_url = mapping["pdfURL"] in privacy.get("redacted_url_paths", [])
            vendor_locator = _ifind_resource_locator(raw["pdfURL"]) if redacted_url else None
            if not vendor_locator:
                try:
                    _filing_url(raw["pdfURL"])
                except ContractError as error:
                    gaps.append(str(error))
                if redacted_url:
                    gaps.append("原件地址的临时授权已移除，不能将公开定位当下载地址")
            entries.append({"catalog_id": record["id"], "source_id": str(source_id), "issuer": raw["thscode"],
                "source": "ifind", "title": raw["reportTitle"], "published_at": published,
                "report_date": report_date, "original_url": vendor_locator or raw["pdfURL"], "security_name": raw["secName"],
                "original_fetch": "ifind_temporary_resource" if vendor_locator else "public_https",
                "publication_precision": "未知" if published is None else "日" if len(published) == 10 else "原字段时间",
                "query_coverage": {"requested": copy.deepcopy(record["parameters"]), "completeness": "未证实"},
                "catalog_ref": {"artifact_id": record["id"], "snapshot_hash": record["hash"],
                    "task_id": record["catalog_origin"]["task_id"], "research_version": record["catalog_origin"]["research_version"], "request_hash": record["request_hash"],
                    "row_locator": path, "field_mapping": mapping, "original_fields": copy.deepcopy(raw)},
                "downloadable": not [gap for gap in gaps if gap not in ("ctime缺失", "发布时间缺失、格式或时区不明确")],
                "gaps": gaps, "source_check": "待核验", "applicability_check": "待核验"})
    except ContractError as error:
        result.update(status="返回结构异常", catalog_error=str(error))
        return result
    if duplicate_ids:
        for entry in entries:
            if entry["source_id"] in duplicate_ids:
                note_issue(entry["catalog_ref"]["row_locator"], "公告目录seq重复，不能唯一选择原件")
        entries = [entry for entry in entries if entry["source_id"] not in duplicate_ids]
    coverage = result["catalog_coverage"]
    if issue_count:
        coverage.update(invalid_location_count=issue_count, invalid_locations=issues)
        if not entries:
            result.update(status="返回结构异常", catalog_error="公告目录无可解析且唯一的条目")
            return result
    status = "成功待核验" if entries else "成功但为空"
    result.update(catalog_entries=entries, catalog_status="部分成功待核验" if issue_count else status,
                  status=status)
    coverage.update(returned_rows=len(entries),
        report_dates=sorted({entry["report_date"] for entry in entries if isinstance(entry["report_date"], str)}))
    return result


def _filing_url(value):
    if not isinstance(value, str) or any(ord(char) <= 32 or ord(char) >= 127 for char in value) or "\\" in value:
        raise ContractError("公告原件URL格式不安全")
    try:
        url = urlsplit(value)
        allowed = url.scheme == "https" and url.hostname in FILING_ORIGINAL_HOSTS and url.port in (None, 443)
    except ValueError:
        raise ContractError("公告原件URL格式不安全") from None
    if not allowed or url.username or url.password or url.query or url.fragment or "?" in value or "#" in value:
        raise ContractError("公告原件仅允许已登记公共HTTPS主机，无凭据、查询参数或重定向")
    return url


def _verified_filing_catalog(task, record):
    _bound(record, task)
    if record.get("kind") != "api" or record.get("source") != "ifind" or record.get("operation") != "report_query":
        raise ContractError("公告原件未绑定report_query快照")
    validate_ifind_report_request(task, record.get("parameters"))
    digest = fingerprint(record.get("snapshot"))
    request_hash = fingerprint({"source": "ifind", "operation": "report_query", "parameters": record["parameters"]})
    origin = record.get("catalog_origin")
    if not isinstance(origin, dict) or origin.get("task_id") != task["id"] or type(origin.get("research_version")) is not int or not 1 <= origin["research_version"] <= task["version"]:
        raise ContractError("公告目录原取得版本不明确或不属于本任务")
    expected_id = "rh_source_" + fingerprint([task["id"], origin["research_version"], request_hash, digest])[:24]
    if record.get("hash") != digest or record.get("request_hash") != request_hash or record["id"] != expected_id or record.get("business_code") != record["snapshot"].get("errorcode"):
        raise ContractError("公告目录原始响应、请求或版本身份被修改")
    rebuilt = _filing_catalog(task, record)
    if any(record.get(key) != rebuilt.get(key) for key in ("catalog_entries", "catalog_status", "catalog_coverage", "catalog_limitations", "status")):
        raise ContractError("公告目录候选被修改或已失效")
    return rebuilt


def validate_filing_catalog_binding(task, directory):
    """Re-derive all directory fields, not merely trust a caller supplied hash."""
    if not isinstance(directory, dict) or not isinstance(directory.get("catalog_ref"), dict):
        raise ContractError("公告原件缺少留存目录绑定")
    ref = directory["catalog_ref"]
    matches = [r for r in task.get("source_records", []) if r.get("id") == ref.get("artifact_id")]
    if len(matches) != 1:
        raise ContractError("公告目录不在本任务唯一归档")
    rebuilt = _verified_filing_catalog(task, matches[0])
    expected = next((row for row in rebuilt["catalog_entries"] if row["source_id"] == directory.get("source_id")), None)
    if expected is None or directory != expected:
        raise ContractError("公告目录字段、原始定位或版本绑定不一致")
    return copy.deepcopy(expected)


def validate_filing_original_request(task, params):
    if not isinstance(params, dict) or set(params) != {"catalog_id", "source_id"} or any(not isinstance(v, str) or not v for v in params.values()):
        raise ContractError("公告原件仅接受catalog_id/source_id，不接受外部URL")
    records = [r for r in task.get("source_records", []) if r.get("id") == params["catalog_id"]]
    if len(records) != 1:
        raise ContractError("选中公告目录未在本任务归档")
    entries = records[0].get("catalog_entries", [])
    selected = next((row for row in entries if row.get("source_id") == params["source_id"]), None)
    directory = validate_filing_catalog_binding(task, selected)
    if not directory["downloadable"]:
        raise ContractError("公告候选身份、发布时间或原件地址存在缺口，不能自动下载")
    if directory.get("original_fetch") == "ifind_temporary_resource":
        if _ifind_resource_locator(directory["original_url"]) != directory["original_url"]:
            raise ContractError("iFinD原件公开定位不符合留存目录")
    else:
        _filing_url(directory["original_url"])
    return directory


def filing_selection_identity(task, params):
    """Catalogue aliases do not create another document or retrieval need."""
    directory = validate_filing_original_request(task, params)
    return fingerprint({"source_id": directory["source_id"],
                        "original_fields": directory["catalog_ref"]["original_fields"]})


def validate_filing_selection_reason(need):
    if not isinstance(need.get("selection_reason"), str) or not need["selection_reason"].strip():
        raise ContractError("下载原件须说明对应研究问题需要核对的内容，不按整个目录批量下载")


def material_record(task, parsed, payload):
    """Original bytes and extraction are one content-bound, task-local record."""
    original = base64.b64encode(payload).decode()
    snapshot = {key: copy.deepcopy(value) for key, value in parsed.items() if key not in ("obtained_at", "parse_runtime")}
    snapshot["original_base64"] = original
    record = dict(snapshot, id="rh_material_" + fingerprint(snapshot)[:24], kind="material", source="用户提供材料",
                snapshot=snapshot, snapshot_hash=fingerprint(snapshot), content_version=1,
                task_id=task["id"], research_version=task["version"], obtained_at=parsed["obtained_at"])
    if "parse_runtime" in parsed:
        record["parse_runtime"] = copy.deepcopy(parsed["parse_runtime"])
    return record


def official_material_record(task, parsed, payload, parameters, origin, obtained_at):
    record = material_record(task, parsed, payload)
    record['snapshot']['official_origin'] = copy.deepcopy(origin)
    snapshot_hash = fingerprint(record['snapshot'])
    request_hash = fingerprint(dict(source='official', operation='release_original', parameters=parameters))
    record.update(source='official', operation='release_original', parameters=copy.deepcopy(parameters),
                  request_hash=request_hash, snapshot_hash=snapshot_hash, obtained_at=obtained_at,
                  id='rh_official_'+fingerprint([task['id'],task['version'],request_hash,snapshot_hash])[:24],
                  status='成功待核验')
    return record


def filing_record(task, directory, name=None, payload=None, related=None, *, _parsed=None):
    """A directory hit without bytes never becomes body evidence."""
    if not isinstance(directory, dict):
        raise ContractError("公告目录必须为字段记录")
    directory = safe_parameters(directory)
    if "catalog_ref" in directory:
        validate_filing_catalog_binding(task, directory)
    for key in ("issuer", "source", "source_id", "title", "original_url"):
        _require_text(directory.get(key), "公告目录" + key)
    if directory.get("published_at") is not None or "catalog_ref" not in directory and task.get("intent") != DOCUMENT_CONTENT_INTENT:
        timepoint(directory.get("published_at"))
    url = urlsplit(directory["original_url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query:
        raise ContractError("公告原件地址须为无凭据的HTTPS地址；带查询签名的地址不能公开归档")
    coverage = directory.get("query_coverage")
    if not isinstance(coverage, dict) or not coverage:
        raise ContractError("公告目录缺少实际检索范围")
    relation = directory.get("relation")
    if relation:
        if not isinstance(relation, dict) or relation.get("kind") not in ("更正", "补充", "撤回", "替代"):
            raise ContractError("公告修订关系未登记")
        target = next((x for x in related or [] if x["id"] == relation.get("target")), None)
        if not target or target.get("kind") != "filing" or target["directory"]["issuer"] != directory["issuer"]:
            raise ContractError("公告修订必须指向本任务同一主体的原件")
        if timepoint(directory["published_at"]) < timepoint(target["directory"]["published_at"]):
            raise ContractError("公告修订不能早于原公告")
    snapshot = {"directory": copy.deepcopy(directory)}
    if payload is not None:
        try:
            parsed = _parsed if _parsed is not None else extract_material(name or "original.pdf", payload)
            if parsed.get("hash") != hashlib.sha256(payload).hexdigest():
                raise ContractError("公告解析与原件内容不一致")
        except Exception:
            if "catalog_ref" not in directory:
                raise
            # Preserve downloaded bytes even if the PDF parser cannot read them.
            parsed = {"name": Path(name or "original.pdf").name, "hash": hashlib.sha256(payload).hexdigest(),
                      "extract_version": "1", "parts": [], "status": "解析受限", "source_check": "待核验",
                      "applicability_check": "待核验", "limitation": "原件已获取但PDF无法解析，不生成正文或核验标签"}
        snapshot["material"] = {key: value for key, value in parsed.items() if key not in ("obtained_at", "parse_runtime")}
        snapshot["material"]["original_base64"] = base64.b64encode(payload).decode()
    digest = fingerprint(snapshot)
    record = {"id": "rh_filing_" + digest[:24], "kind": "filing", "source": directory["source"],
              "directory": directory, "snapshot": snapshot, "hash": digest, "content_version": 1,
              "task_id": task["id"], "research_version": task["version"], "obtained_at": now(),
              "stages": {"directory": "已获取", "download": "已获取" if payload is not None else "未获取",
                         "parse": snapshot.get("material", {}).get("status", "未解析"),
                         "source_check": "待核验", "applicability_check": "待核验"}}
    record["event_group"] = "rh_event_" + fingerprint([directory["issuer"], directory["source_id"]])[:24]
    if payload is not None and "parse_runtime" in parsed:
        record["parse_runtime"] = copy.deepcopy(parsed["parse_runtime"])
    for prior in related or []:
        if prior.get("kind") == "filing" and prior["directory"]["issuer"] == directory["issuer"]:
            same_bytes = payload is not None and prior["snapshot"].get("material", {}).get("hash") == snapshot["material"]["hash"]
            if same_bytes or prior["directory"]["source_id"] == directory["source_id"]:
                record["event_group"] = prior["event_group"]
                break
    return record


def _number(value):
    if isinstance(value, bool):
        raise ContractError("布尔值不是金融数字")
    try:
        result = Decimal(str(value).replace(",", "").replace("−", "-"))
    except (InvalidOperation, ValueError):
        raise ContractError("原始数字无法确定性解析") from None
    if not result.is_finite():
        raise ContractError("原始数字不是有限值")
    return result


def _is_basic_financial(record):
    return isinstance(record, dict) and record.get('source') == 'ifind' and record.get('operation') == 'basic_data_service'


def _controlled_financial_definitions():
    """Only the installed reviewed catalogue can authorize financial semantics."""
    from .ifind_financial_catalog import evidence_definitions
    return evidence_definitions()


def _basic_financial_contract(record):
    from .ifind_financial_evidence import basic_financial_contract
    try:
        definitions = _controlled_financial_definitions()
    except (ImportError, AttributeError):
        definitions = {}
    if (type(record.get('fixed_financial_catalog_version')) is not int
            or record['fixed_financial_catalog_version'] != 1
            or record.get('fixed_financial_catalog_digest') != fingerprint(definitions)):
        definitions = {}
    contract = basic_financial_contract(record, definitions)
    for field in contract['fields'].values():
        field.update(raw_unit=field['unit'], factor=1)
    for table in contract['table_contexts'].values():
        for name in table['fields']:
            table['fields'][name] = copy.deepcopy(contract['fields'][name])
    return contract


def _basic_financial_cell(record, locator):
    if (not isinstance(locator, dict) or set(locator) != {'table', 'row', 'field'}
            or type(locator['table']) is not int or type(locator['row']) is not int):
        raise ContractError('基础财务须定位原始表、行和字段')
    contract = _basic_financial_contract(record)
    matches = [cell for cell in contract['cells'] if cell['record_locator'] == locator]
    if len(matches) != 1:
        raise ContractError(contract.get('reason', '基础财务单元没有完整受控字段定义'))
    return matches[0]


def _api_field_definition(record):
    if _is_basic_financial(record):
        contract = _basic_financial_contract(record)
        if contract['status'] != 'registered_constraints':
            return None
        return dict(version=1, fields=contract['fields'], documentation=IFIND_MANUAL)
    return FIELD_REGISTRY.get(str(record.get('source')) + '.' + str(record.get('operation')))


def _api_cell(artifact, locator):
    if _is_basic_financial(artifact):
        cell = _basic_financial_cell(artifact, locator)
        return cell['raw_value'], cell['provider_code'], cell['report_end'], locator['field']
    raw = artifact["snapshot"]
    if not isinstance(locator.get("field"), str):
        raise ContractError("字段定位必须为已登记名称")
    if artifact.get('source')=='official' and artifact.get('operation')=='index_file':
        parsed=raw.get('parsed',{});row=locator.get('row')
        if (type(row) is not int or not 1<=row<=len(parsed.get('members',[])) or locator['field']!='weight'
                or locator.get('sheet')!=parsed.get('sheet') or locator.get('column')!=9):
            raise ContractError('指数权重单元定位不存在')
        member=parsed['members'][row-1]
        return member.get('weight'),parsed.get('index_code'),member.get('observation_date'),'weight'
    if artifact["source"] == "ifind":
        tables = raw.get("tables")
        if not isinstance(tables, list) or not isinstance(locator.get("table"), int) or isinstance(locator["table"], bool) or not 0 <= locator["table"] < len(tables):
            raise ContractError("响应表定位不存在")
        table = tables[locator["table"]]
        if not isinstance(table, dict) or not isinstance(table.get("table"), dict):
            raise ContractError("响应表结构异常")
        row, field = locator.get("row"), locator.get("field")
        values = table.get("table", {}).get(field)
        dates = table.get("time", raw.get("time"))
        if not isinstance(row, int) or isinstance(row, bool) or not isinstance(values, list) or not 0 <= row < len(values) or not isinstance(dates, list) or len(dates) != len(values):
            raise ContractError("字段、日期或行定位不完整")
        return values[row], table.get("thscode"), dates[row], field
    rows = raw.get("data")
    row, field = locator.get("row"), locator.get("field")
    if not isinstance(row, int) or isinstance(row, bool) or not isinstance(rows, list) or not 0 <= row < len(rows) or not isinstance(rows[row], dict) or field not in rows[row]:
        raise ContractError("记录或字段定位不存在")
    return rows[row][field], rows[row].get("ts_code"), rows[row].get("trade_date"), field


def _source_extract(item, task):
    """Re-extract every critical claim from retained bytes or its exact API cell."""
    artifact = item.get("artifact")
    if not isinstance(artifact, dict):
        raise ContractError("真实证据缺少原件或接口快照记录")
    _bound(artifact, task)
    if artifact.get('source') == 'official' and artifact.get('operation') == 'etf_pcf':
        from .etf_sources import validate_request as validate_etf_request, validate_snapshot
        validate_etf_request(task, artifact.get('parameters'))
        validate_snapshot(artifact, task['cutoff'])
    if artifact.get('source') == 'official' and artifact.get('operation') == 'release_original':
        from .official_sources import validate_original
        validate_original(task, artifact)
    document_only = validate_evidence_usage(item, task, artifact)
    expected_hash = artifact.get("snapshot_hash", artifact.get("hash"))
    if fingerprint(artifact.get("snapshot")) != expected_hash or item["snapshot_hash"] != expected_hash or item["snapshot"] != artifact["snapshot"]:
        raise ContractError("原始材料哈希或内容不一致")
    locator = item.get("record_locator")
    if not isinstance(locator, dict):
        raise ContractError("证据缺少结构化定位")
    if artifact["kind"] == "api":
        if artifact.get('source')=='official' and artifact.get('operation')=='index_file':
            from .index_sources import validate_weight_item
            validate_weight_item(item,task,artifact)
            definition=FIELD_REGISTRY['official.index_file']
            if item.get('field_definition')!={'registry':'official.index_file','version':definition['version'],
                    'field':'weight','definition':definition['fields']['weight'],'documentation':definition['documentation']}:
                raise ContractError('指数权重字段定义与登记单位不符')
            return
        if artifact.get("source") == "ifind" and "ifind." + str(artifact.get("operation")) in FINANCIAL_QUERY_REGISTRY:
            raise ContractError("财务语义查询只保留原始返回字段，单位、范围与历史版本未核验，不能直接作数字证据或替换所需指标")
        if artifact.get("source") == "ifind" and "ifind." + str(artifact.get("operation")) in SERIES_REQUEST_REGISTRY:
            raise ContractError("日期序列原始记录的单位、证券类型和历史版本尚未核验，不能直接作数字证据或计算输入")
        if artifact.get('source') == 'ifind' and 'ifind.' + str(artifact.get('operation')) in INDEX_REQUEST_REGISTRY:
            raise ContractError('指数成分原始名单仅供同任务后续取数定位，不能作为价格、权重或投资判断的金融证据')
        if artifact.get("source") == "ifind" and "ifind." + str(artifact.get("operation")) in ECONOMIC_REQUEST_REGISTRY:
            from .economic_observations import enabled, validate as validate_economic_observation
            if enabled(task):
                validate_economic_observation(item, task, artifact)
                return
            raise ContractError("宏观原始序列的指标含义、单位和修订版本未核验，不能直接作数字证据或计算输入")
        if artifact.get('source') == 'ifind' and 'ifind.' + str(artifact.get('operation')) in CALENDAR_REQUEST_REGISTRY:
            raise ContractError('交易所日历只核对日期覆盖，不作为价格、证券状态或投资判断的金融证据')
        if artifact.get("source") == "tinyshare" and "tinyshare." + str(artifact.get("operation")) in FINANCIAL_REQUEST_REGISTRY:
            raise ContractError("财报原始记录的金额单位和历史版本未核验，不能直接提取为数字证据或计算输入")
        if business_payload(artifact["snapshot"]) != artifact["snapshot"]:
            raise ContractError("业务快照不能包含凭据或无关私密字段")
        code = artifact["snapshot"].get("errorcode") if artifact["source"] == "ifind" else artifact["snapshot"].get("code", 0)
        if isinstance(code, bool) or code not in (0, "0"):
            raise ContractError("业务错误不能通过修改请求状态变为有效数据")
        if artifact.get("status") != "成功待核验":
            raise ContractError("业务请求未得到可核验非空响应")
        if fingerprint({"source": artifact["source"], "operation": artifact["operation"], "parameters": artifact["parameters"]}) != artifact["request_hash"]:
            raise ContractError("接口请求参数指纹不一致")
        definition = _api_field_definition(artifact)
        raw_value, code, date, field = _api_cell(artifact, locator)
        if artifact['source'] == 'ifind' and artifact['operation'] == 'cmd_history_quotation':
            from .draft_sources import bound_ifind_market_scope, market_cell_binding_enabled, _observation_cell_selected
            if market_cell_binding_enabled(task):
                scope = bound_ifind_market_scope(task, artifact, latest_only=False)
                if not _observation_cell_selected(scope, locator['table'], field, locator['row']):
                    raise ContractError('行情单元的证券代码、日期或字段未能唯一核对')
        if not definition or field not in definition["fields"]:
            raise ContractError("字段定义与单位未登记")
        expected = _registered_api_fields(artifact, code).get(field)
        if expected is None:
            raise ContractError("当前接口表的证券命名空间及字段单位未登记")
        if item.get("field_definition") != {"registry": artifact["source"] + "." + artifact["operation"],
                "version": definition["version"], "field": field, "definition": expected, "documentation": definition["documentation"]}:
            raise ContractError("字段定义的引用或版本不匹配")
        mapping = item.get("identity", {})
        if not isinstance(mapping, dict):
            raise ContractError("证券身份映射必须为字段记录")
        direct = mapping.get('mode') in ('direct_provider_observation', 'candidate_provider_observation')
        if mapping.get('mode') == 'candidate_provider_observation':
            validate_candidate_code_identity(item, task, artifact)
        elif direct:
            validate_direct_code_identity(item, task, artifact)
        else:
            for key in ("provider_code", "object", "basis", "valid_from", "valid_to"):
                _require_text(mapping.get(key), "证券身份映射" + key)
        if mapping["provider_code"] != code or mapping["object"] != item["object"]:
            raise ContractError("接口证券代码与任务主体映射不匹配，不自动截断港股代码")
        date = str(date)
        date = date[:4] + "-" + date[4:6] + "-" + date[6:8] if re.fullmatch(r"\d{8}", date) else date
        if artifact["source"] == "tinyshare" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ContractError("Tinyshare交易日字段不是明确的日频日期")
        timepoint(date)
        expected_period = _basic_financial_cell(artifact, locator)['period'] if _is_basic_financial(artifact) else date
        if item["period"] != expected_period:
            raise ContractError("观测期或证券映射有效期不匹配")
        if not direct:
            timepoint(mapping["valid_from"])
            timepoint(mapping["valid_to"])
            if not mapping["valid_from"] <= date <= mapping["valid_to"]:
                raise ContractError("观测期或证券映射有效期不匹配")
            validate_identity_period_basis(item, task)
        params = artifact["parameters"]
        if _is_basic_financial(artifact):
            if current_snapshot_reference(task, artifact) is None:
                raise ContractError('基础财务只能绑定本任务当前实际取数及固定目录版本')
            validate_metadata_request(task, artifact['operation'], params, before=artifact)
            requested = [row['indicator'] for row in params['indipara']]
            codes = params['codes'].split(',')
            start = end = _basic_financial_cell(artifact, locator)['report_end']
        elif artifact["source"] == "ifind":
            settings = params.get("functionpara", {})
            if not isinstance(settings, dict) or not isinstance(params.get("indicators"), str) or not isinstance(params.get("codes"), str):
                raise ContractError("行情请求设置结构异常")
            if settings.get("Currency") != "RMB" or str(settings.get("CPS")) != "1" or settings.get("Fill") != "Omit":
                raise ContractError("行情币种、复权或填充口径不符合登记定义")
            requested, start, end = params.get("indicators", "").split(","), params.get("startdate"), params.get("enddate")
            codes = params.get("codes", "").split(",")
        elif artifact['source'] == 'official' and artifact['operation'] == 'etf_pcf':
            start = end = artifact['snapshot']['exchange_origin']['valuation_date']
            requested, codes = ['unit_nav'], [params['ts_code']]
        else:
            start, end = validate_tinyshare_request(task, artifact["operation"], params)
            requested = params["fields"].split(",")
            codes = params.get("ts_code", "").split(",")
        if code not in codes or field not in requested or not isinstance(start, str) or not isinstance(end, str) or not start <= date <= end:
            raise ContractError("响应对象、字段或观测期超出请求范围")
        if _number(item.get("raw_value")) != _number(raw_value):
            raise ContractError("提取值不等于接口原始字段")
        if isinstance(item.get("conversion_factor"), bool) or any(item.get(key) != expected[key] for key in ("raw_unit", "unit")) or item.get("conversion_factor") != expected["factor"]:
            raise ContractError("原始单位、输出单位或换算倍数与字段登记不符")
        if _number(item.get("value")) != _number(raw_value) * _number(expected["factor"]):
            raise ContractError("标准化值与原值换算不一致")
        return
    if artifact["kind"] not in ("material", "filing"):
        raise ContractError("原始材料类别未登记")
    if artifact["kind"] == "filing" and (artifact.get("operation") == "filing_original" or "catalog_ref" in artifact.get("directory", {})):
        validate_filing_catalog_binding(task, artifact["directory"])
        retained = next((r for r in task.get("source_records", []) if r.get("id") == artifact["id"]), None)
        if retained is not None:
            _bound(retained, task)
    material = artifact["snapshot"].get("material") if artifact["kind"] == "filing" else artifact["snapshot"]
    if not material or material.get("status") != "已解析":
        raise ContractError("只有目录或解析受限，不能认定正文事实")
    try:
        payload = base64.b64decode(material["original_base64"], validate=True)
    except (ValueError, KeyError):
        raise ContractError("原件字节缺失或损坏") from None
    if hashlib.sha256(payload).hexdigest() != material["hash"]:
        raise ContractError("原件字节与文件哈希不一致")
    from .material_parser import verified_material
    extracted = verified_material(material, payload)
    if extracted["parts"] != material["parts"] or extracted["extract_version"] != material["extract_version"]:
        raise ContractError("摘录不是当前提取版本的原文")
    parts = {part["locator"]: part["text"] for part in extracted["parts"]}
    text = parts.get(locator.get("part"), "")
    excerpt = item.get("text")
    if not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in text:
        raise ContractError("摘录无法定位至原件页码或段落")
    if artifact["kind"] == "filing":
        directory = artifact["snapshot"]["directory"]
        if directory != artifact.get("directory") or item["object"] != directory["issuer"]:
            raise ContractError("公告主体或公开时间与目录不一致")
        if document_only or validate_current_snapshot(item, task):
            pass  # Original identity and bytes remain mandatory; availability is not asserted.
        elif directory.get("published_at") is not None:
            if item["published_at"] != directory["published_at"]:
                raise ContractError("公告公开时间与目录不一致")
        else:
            reference = item.get("publication", {}).get("basis_ref", {})
            if reference.get("kind") != "material" or reference.get("artifact_id") != artifact["id"]:
                raise ContractError("目录发布时间未知，须由同一PDF原文明确定位发布日")
            validate_publication_basis(item, task.get("source_records", []) + task.get("materials", []))
    if item.get("value") is not None:
        token = _require_text(item.get("raw_token"), "数字原文")
        # Locate a complete numeric token. A substring must not discard the
        # original sign or borrow digits from scientific notation.
        number_pattern = r"(?<![\d.,eE+\-−＋－])" + re.escape(token) + r"(?![\d.,eE])"
        matches = list(re.finditer(number_pattern, excerpt))
        if not matches:
            raise ContractError("数字原文无法定位，不能由模型补齐")
        unit_text = parts.get(locator.get("unit_part", locator.get("part")), "")
        if isinstance(item.get("conversion_factor"), bool) or item["raw_unit"] not in unit_text or item["raw_unit"] != item["unit"] or item.get("conversion_factor") != 1:
            raise ContractError("材料数字单位无法核验；仅支持已核对的原始单位")
        from .numeric_integrity import (literal_quantity_qualifiers, literal_quantity_ranges,
                                        quantity_qualifiers_enabled, quantity_ranges_enabled)
        if quantity_qualifiers_enabled(task):
            # A selected excerpt can start at the number or end before 左右.
            # Recheck the actual page/paragraph, not the shortened excerpt.
            adjacent_unit = re.compile(r"\s*" + re.escape(item["raw_unit"]))
            intervals = tuple(literal_quantity_ranges(text)) if quantity_ranges_enabled(task) else ()
            for selected in re.finditer(re.escape(excerpt), text):
                for match in matches:
                    start, end = selected.start() + match.start(), selected.start() + match.end()
                    if any(span.start <= start and end <= span.end for span in intervals):
                        raise ContractError("原文是数量区间，须保留完整区间摘录，不能采纳端点为单值")
                    unit = adjacent_unit.match(text, end)
                    if literal_quantity_qualifiers(text, start, unit.end() if unit else end):
                        raise ContractError("原文含数量限定词，须保留完整文字摘录，不能采纳为精确数值")
        selector = locator.get('financial_amount_column', {})
        if isinstance(selector, dict) and type(selector.get('version')) is int and selector['version'] == 2:
            from .financial_data import monetary_cell
            original_number = monetary_cell(token)
        else:
            original_number = _number(token)
        if original_number != _number(item.get("raw_value")) or original_number != _number(item["value"]):
            raise ContractError("材料数字与原文不一致")
        from .financial_sources import validate_financial_amount_column
        validate_financial_amount_column(item, artifact, task=task)


def evidence_binding(item):
    keys = ("id", "object", "source", "record_locator", "snapshot_hash", "published_at", "publication",
            "period", "nature", "raw_value", "raw_unit", "value", "unit", "conversion_factor", "raw_token",
            "text", "identity", "question_ids", "field_definition", "event_group")
    values = {key: item.get(key) for key in keys}
    for key in ("calculation_context", "calculation_context_basis", "economic_context"):
        if key in item:
            values[key] = item[key]
    if "usage_scope" in item:
        values["usage_scope"] = item["usage_scope"]
    if "availability" in item:
        values["availability"] = item["availability"]
    if 'observation_date' in item:
        values['observation_date'] = item['observation_date']
    if 'observation_period' in item:
        values['observation_period'] = item['observation_period']
    if 'source_label' in item:
        values['source_label'] = item['source_label']
        values['date_role'] = item.get('date_role')
    return fingerprint(values)


def reference_groups(evidence, refs):
    """Group verified citations by raw carrier and known filing event.

    A carrier is not an independent confirmation. Different extracts or file
    names do not create new carriers; unknown event identity stays unknown.
    Callers validate evidence first. This helper grants no source approval.
    """
    result = {"source_groups": [], "event_groups": [], "unidentified_source_refs": [], "unidentified_event_refs": []}
    for ref in dict.fromkeys(refs):
        item = evidence[ref]
        artifact = item.get("artifact", {})
        kind, snapshot = artifact.get("kind"), artifact.get("snapshot", {})
        material = snapshot.get("material", {}) if kind == "filing" else snapshot if kind == "material" else {}
        digest = material.get("hash")
        if digest:
            source = "rh_source_" + fingerprint(["original_bytes", digest])[:24]
        elif kind == "api" and artifact.get("hash"):
            source = "rh_source_" + fingerprint([artifact.get("source"), artifact.get("operation"), artifact["hash"]])[:24]
        else:
            source = None
        if source:
            if source not in result["source_groups"]:
                result["source_groups"].append(source)
        else:
            result["unidentified_source_refs"].append(ref)
        event = artifact.get("event_group") if kind == "filing" else None
        if event and item.get("event_group") == event:
            if event not in result["event_groups"]:
                result["event_groups"].append(event)
        else:
            result["unidentified_event_refs"].append(ref)
    return result


def _api_context_date(raw):
    value = str(raw)
    if re.fullmatch(r"\d{8}", value):
        value = value[:4] + "-" + value[4:6] + "-" + value[6:]
    timepoint(value)
    return value


def _api_field_family(record, code):
    """Resolve only registered quote-unit namespaces, never task/model labels."""
    definition = FIELD_REGISTRY.get(str(record.get("source")) + "." + str(record.get("operation")))
    if not definition:
        return None
    namespaces = definition.get("instrument_namespaces")
    if namespaces is None:
        return "price"
    if not isinstance(code, str):
        return None
    return next((family for family, pattern in namespaces.items() if re.fullmatch(pattern, code)), None)


def _registered_api_fields(record, code):
    if _is_basic_financial(record):
        contract = _basic_financial_contract(record)
        for index, table in enumerate(record.get('snapshot', {}).get('tables', [])):
            if table.get('thscode') == code:
                return contract['table_contexts'].get(str(index), {}).get('fields', {})
        return {}
    definition = FIELD_REGISTRY.get(str(record.get("source")) + "." + str(record.get("operation")))
    family = _api_field_family(record, code)
    if not definition or family is None:
        return {}
    fields = definition.get("field_families", {}).get(family, definition["fields"])
    if "raw_observation_fields" in definition:
        indicators = record.get("parameters", {}).get("indicators")
        requested = indicators.split(",") if isinstance(indicators, str) else []
        if definition["raw_observation_fields"].keys() & requested:
            return {field: fields[field] for field in requested if field in fields}
    return fields


def api_calculation_context_contract(record):
    """Describe source-proven context, never approve a row or financial method.

    Extraction and validation share this definition. Unit labels, security
    suffixes and model assumptions cannot supply missing currency/price basis.
    Other originals may supply context, but require their own retained anchors.
    """
    if _is_basic_financial(record):
        return _basic_financial_contract(record)
    price_fields = {"security_id", "currency", "price_basis", "price_kind", "observation_date",
                    "window_start", "window_end", "corporate_action_status"}
    index_fields = {"index_id", "index_variant", "index_currency", "index_base", "level_kind",
                    "observation_date", "window_start", "window_end"}
    result = {"version": 3, "status": "unavailable", "context_is_approval": False,
              "artifact_id": record.get("id"), "snapshot_hash": record.get("snapshot_hash", record.get("hash")),
              "registered_numeric_fields": [], "row_fields": [], "fixed_fields": {}, "column_fields": {},
              "unavailable_fields": sorted(price_fields), "unverified_context": {}, "basis_templates": {},
              "rule": "仅说明同一快照可核验的口径，不批准身份、证据、时点或方法。缺失口径须另有真实原件依据，不能从元、代码后缀或默认参数推定。"}
    if record.get('source') == 'official' and record.get('operation') == 'etf_pcf':
        from .etf_sources import calculation_context
        return calculation_context(record, result)
    definition = FIELD_REGISTRY.get(str(record.get("source")) + "." + str(record.get("operation")))
    if record.get("kind") != "api" or not definition:
        result["reason"] = "接口没有登记数字字段及计算口径；原始响应不能直接作为计算输入。"
        return result
    params, snapshot = record.get("parameters"), record.get("snapshot")
    if (not isinstance(params, dict) or not isinstance(snapshot, dict)
            or record.get("status") != "成功待核验"
            or fingerprint(snapshot) != result["snapshot_hash"]
            or fingerprint({"source": record.get("source"), "operation": record.get("operation"),
                            "parameters": params}) != record.get("request_hash")):
        result["reason"] = "原始请求或响应未通过结构一致性校验。"
        return result
    collection = snapshot.get("tables" if record["source"] == "ifind" else "data")
    if not isinstance(collection, list):
        result["reason"] = "接口响应行表集合不是数组，不能提供数字位置或计算口径。"
        return result
    observed = set()
    table_contexts = {}
    if record["source"] == "ifind":
        for table_index, table in enumerate(collection):
            if isinstance(table, dict) and isinstance(table.get("table"), dict):
                family = _api_field_family(record, table.get("thscode"))
                fields = sorted(set(table["table"]).intersection(_registered_api_fields(record, table.get("thscode"))))
                if fields:
                    table_contexts[str(table_index)] = {"unit_kind": family, "registered_numeric_fields": fields}
                    observed.update(fields)
    else:
        for row in collection:
            if isinstance(row, dict):
                observed.update(row)
    registered = sorted(observed.intersection(definition["fields"]))
    if not registered:
        result["reason"] = "实际响应未提供已登记数字字段。"
        return result
    fixed = {}
    settings = params.get("functionpara", {}) if record["source"] == "ifind" else {}
    if isinstance(settings, dict):
        if settings.get("Currency") in ("RMB", "CNY"):
            fixed["currency"] = "CNY"
        if str(settings.get("CPS")) == "1":
            fixed["price_basis"] = "unadjusted"
    for field, names in (("window_start", ("startdate", "start_date")), ("window_end", ("enddate", "end_date"))):
        raw = next((params[name] for name in names if name in params), None)
        try:
            fixed[field] = _api_context_date(raw)
        except ContractError:
            pass
    row_fields = ["security_id", "fund_id", "entity_id", "observation_date"]
    column_fields = {"price_kind": {"close": "close"}} if "close" in registered else {}
    context_fields = price_fields
    if record["source"] == "ifind":
        for table in table_contexts.values():
            index = table["unit_kind"] == "index_level"
            table_fixed = {key: value for key, value in fixed.items()
                           if not index or key in {"window_start", "window_end"}}
            table_rows = ["index_id", "entity_id", "observation_date"] if index else row_fields
            table_columns = {"level_kind" if index else "price_kind": {"close": "close"}} if "close" in table["registered_numeric_fields"] else {}
            table.update(fixed_fields=table_fixed, row_fields=table_rows, column_fields=table_columns,
                         unavailable_fields=sorted((index_fields if index else price_fields) - set(table_fixed) - set(table_rows) - set(table_columns)))
        contexts = list(table_contexts.values())
        # Shared hints contain only fields proved for every registered table.
        # Each cell validator selects its own table instead of using the union.
        fixed = {key: value for key, value in contexts[0]["fixed_fields"].items()
                 if all(table["fixed_fields"].get(key) == value for table in contexts)}
        row_fields = [key for key in contexts[0]["row_fields"] if all(key in table["row_fields"] for table in contexts)]
        column_fields = {key: value for key, value in contexts[0]["column_fields"].items()
                         if all(table["column_fields"].get(key) == value for table in contexts)}
        context_fields = set().union(*(index_fields if table["unit_kind"] == "index_level" else price_fields for table in contexts))
        result["table_contexts"] = table_contexts
        result["rule"] += "混合响应按table_contexts逐表核验；顶层共有口径仅取交集，不能跨表沿用单位、主体类别或口径。"
    basis_template = {"kind": "api", "artifact_id": record["id"], "snapshot_hash": result["snapshot_hash"]}
    # The anchor field identifies the normalized calculation-context key, not
    # the provider's query parameter or numeric column. Supply exact detached
    # templates so a model need not invent this protocol translation. Values,
    # identity, availability and suitability still pass their original gates.
    basis_templates = {field: dict(basis_template, field=field)
                       for field in sorted(set(row_fields) | set(fixed) | set(column_fields))}
    for table in table_contexts.values():
        for field in set(table["row_fields"]) | set(table["fixed_fields"]) | set(table["column_fields"]):
            basis_templates[field] = dict(basis_template, field=field)
    result.update(status="registered_constraints", registered_numeric_fields=registered,
                  row_fields=row_fields, fixed_fields=fixed, column_fields=column_fields,
                  unavailable_fields=sorted(context_fields - set(row_fields) - set(fixed) - set(column_fields)),
                  unverified_context={"corporate_action_status": {"value": "unknown", "basis": {"kind": "unverified"}}} if "corporate_action_status" in context_fields else {},
                  identity_rule="主体字段来自本证据核验的identity.object，交易日来自定位行；合同不自动核验证券名称、类型或映射。",
                  basis_template=basis_template, basis_templates=basis_templates,
                  basis_field_rule="依据field必须等于calculation_context的同名键，如currency、price_basis、window_start；不能写Currency、CPS、startdate、close等接口参数或数值列名。")
    return result


def api_cell_candidates(record, maximum=16, *, task=None):
    """Readonly locations from the actual validator, never evidence approval.

    No inferred identity, publication time, currency, or financial method.
    Missing/invalid values are not filled. Indices always address the original
    arrays; a bounded scan never claims to exhaust the source.
    """
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 64:
        raise ContractError("接口位置候选上限须为1至64的整数")
    if not isinstance(record, dict):
        raise ContractError("接口位置候选须绑定原始记录对象")
    contract = api_calculation_context_contract(record)
    result = {"version": 1, "cells": [], "maximum": maximum, "scan_complete": False,
              "invalid_positions": 0, "scan_units": 0, "maximum_scan_units": 512,
              "candidate_is_approval": False, "absence_is_proof": False}
    if contract["status"] != "registered_constraints":
        result["reason"] = contract["reason"]
        return result
    raw = record["snapshot"]
    status = raw.get("errorcode") if record["source"] == "ifind" else raw.get("code", 0)
    if isinstance(status, bool) or status not in (0, "0"):
        result["reason"] = "业务错误响应不生成数字位置候选"
        return result
    quote_scope = None
    if task is not None and record['source'] == 'ifind' and record['operation'] == 'cmd_history_quotation':
        from .draft_sources import bound_ifind_market_scope, market_cell_binding_enabled, _observation_cell_selected
        if market_cell_binding_enabled(task):
            quote_scope = bound_ifind_market_scope(task, record, latest_only=False)
    fields = contract["registered_numeric_fields"]
    def scan_slot():
        # Charge both containers and cells so malformed/empty rows cannot
        # bypass the traversal bound. This is not a research request charge.
        if result["scan_units"] >= result["maximum_scan_units"]:
            result["reason"] = "位置扫描达到上限，完整响应仍保留；未展示不代表不存在"
            return False
        result["scan_units"] += 1
        return True
    def positions():
        if record["source"] == "ifind":
            for table_index, table in enumerate(raw.get("tables", [])):
                if not scan_slot():
                    return
                if not isinstance(table, dict) or not isinstance(table.get("table"), dict):
                    continue
                for field in fields:
                    values = table["table"].get(field)
                    if isinstance(values, list):
                        for row_index in range(len(values)):
                            if not scan_slot():
                                return
                            yield {"table": table_index, "row": row_index, "field": field}
        else:
            for row_index, row in enumerate(raw.get("data", [])):
                if not scan_slot():
                    return
                if isinstance(row, dict):
                    for field in fields:
                        if field in row:
                            if not scan_slot():
                                return
                            yield {"row": row_index, "field": field}
    for locator in positions():
        if quote_scope is not None and not _observation_cell_selected(
                quote_scope, locator['table'], locator['field'], locator['row']):
            result["invalid_positions"] += 1
            continue
        if len(result["cells"]) >= maximum:
            result["reason"] = "位置候选达到上限，完整响应仍保留；未展示不代表不存在"
            return result
        try:
            value, code, date, field = _api_cell(record, locator)
            if field not in _registered_api_fields(record, code):
                raise ContractError("当前表的字段单位未登记")
            _number(value)
            period = (_basic_financial_cell(record, locator)['period']
                      if _is_basic_financial(record) else _api_context_date(date))
            if not isinstance(code, str) or not code.strip():
                raise ContractError("接口主体代码缺失")
        except ContractError:
            result["invalid_positions"] += 1
            continue
        result["cells"].append({"proposal_fields": {"artifact_id": record["id"],
            "record_locator": locator, "period": period}, "source_metadata": {
                "snapshot_hash": contract["snapshot_hash"], "provider_code": code,
                "raw_value": copy.deepcopy(value), "raw_observation_date": copy.deepcopy(date),
                "field_registry": record["source"] + "." + record["operation"], "field": field}})
    result["scan_complete"] = "reason" not in result
    return result


def _api_calculation_context_values(record, locator, identity):
    contract = api_calculation_context_contract(record)
    if contract["status"] != "registered_constraints":
        raise ContractError(contract["reason"])
    _, code, date, column = _api_cell(record, locator)
    row_contract = contract.get("table_contexts", {}).get(str(locator.get("table")), contract)
    if column not in _registered_api_fields(record, code) or column not in row_contract["registered_numeric_fields"]:
        raise ContractError("计算口径API字段未登记")
    derived = {field: identity.get("object") for field in row_contract["row_fields"] if field != "observation_date"}
    derived.update(row_contract["fixed_fields"])
    if not _is_basic_financial(record):
        derived['observation_date'] = _api_context_date(date)
    for field, columns in row_contract["column_fields"].items():
        if column in columns:
            derived[field] = columns[column]
    return derived


def api_cell_choices(task, record, maximum=64):
    """Selectable current close cells, not automatic facts or model answers.

    Reuse the registered row decoder, request context and direct-code proof.
    No dates, corporate actions, identity periods or currency are guessed.
    """
    from .economic_observations import is_series, contract as economic_observations
    if is_series(record):
        return economic_observations(task, record)['cells'][:maximum]
    direct = direct_code_reference(task, record)
    quote = record.get('source')=='ifind' and record.get('operation')=='cmd_history_quotation'
    financial = _is_basic_financial(record) and current_snapshot_reference(task, record) is not None
    official_nav = (record.get('source')=='official' and record.get('operation')=='etf_pcf'
        and type(task.get('official_etf_cell_selection_version')) is int
        and task['official_etf_cell_selection_version']==1 and task.get('research_mode')=='current'
        and task.get('intent')!='原件内容核对')
    if not (quote or official_nav or financial):
        return []
    proofs = [('direct_code_reference',direct)] if direct is not None else []
    if (not proofs and type(task.get('name_candidate_observation_version')) is int
            and task['name_candidate_observation_version']==1):
        proofs = [('candidate_code_reference',ref) for ref in candidate_code_references(task,record)
                  if ref['discovery']['operation']=='get_thscode']
    if not proofs:
        return []
    required = ({'fund_id','currency','nav_kind','valuation_date','share_class','observation_date'}
        if official_nav else {'security_id','currency','price_basis','price_kind','observation_date','window_start','window_end'})
    if financial:
        required = {'entity_id', 'currency', 'period', 'accounting_scope', 'accounting_standard', 'metric'}
    choices = []
    cells = api_cell_candidates(record,maximum,task=task)['cells']
    for identity_field,identity_reference in proofs:
        for cell in cells:
            locator = cell['proposal_fields']['record_locator']
            if (not financial and locator['field']!=('unit_nav' if official_nav else 'close')
                    or cell['source_metadata']['provider_code']!=identity_reference['object']):
                continue
            values = _api_calculation_context_values(record,locator,{'object':identity_reference['object']})
            if (not required.issubset(values)
                    or not financial and (values.get('nav_kind')!='official' if official_nav else values.get('price_kind')!='close')):
                continue
            values = {key:values[key] for key in required}
            from .etf_sources import share_context_enabled
            methods = {method for question in task.get('questions', []) for method in question.get('method_needs', [])}
            methods.update(spec['method'] for spec in task.get('calculation_specs', []))
            if quote and share_context_enabled(task) and 'etf_daily_nav_premium_v1' in methods:
                values['fund_id'] = identity_reference['object']
            if quote:
                values['corporate_action_status'] = 'unknown'
            reference = api_value_reference(record)
            template = dict(cell['proposal_fields'],object=identity_reference['object'],nature='已实现事实',
                **{identity_field:identity_reference},api_value_reference=reference,
                availability=current_snapshot_reference(task,record),published_at=None,
                publication={'at':None,'basis_ref':None,'precision':'未知','timezone':'未知','certainty':'未知',
                             'basis':'首次公开时间未知，当前可得性另由实际取数账本核验'},
                calculation_context=values,api_context_reference={'version':1,'artifact_id':record['id'],'snapshot_hash':record['hash']})
            # Resolve original positions, not model arithmetic or repaired data.
            template.pop('period')
            expanded = expand_api_value_reference(task,record,template)
            ident = 'rh_cell_' + fingerprint([identity_reference,reference,locator,values])[:24]
            choices.append({'candidate_id':ident,'artifact_id':record['id'],'object':expanded['object'],
                'period':expanded['period'],'value':expanded['value'],'unit':expanded['unit'],
                'record_locator':copy.deepcopy(locator),'calculation_context':copy.deepcopy(values),
                'source_approved':False,'limitation':('仅本次上交所清单的实际净值、所属日与上市交易份额，不证明历史首发、实时套利或投资收益。'
                    if official_nav else '仅本次取得的受控定义财务累计原值；不证明历史首次可得，不以归母替代报表净利润。'
                    if financial else DIRECT_CODE_LIMITATION if direct else CANDIDATE_CODE_LIMITATION)})
            if identity_field=='candidate_code_reference':
                choices[-1][identity_field]=copy.deepcopy(identity_reference)
    return choices


API_CELL_CHOICE_COVERAGE_VERSION = 1


def api_cell_choice_coverage(record, choices, maximum=64, *, task=None):
    """Check the selection catalogue independently of short location hints.

    Both scans retain the original finite bounds. A complete selection scan
    means only that registered eligible cells were enumerated; it does not
    prove a complete trading calendar, valid values or financial approval.
    """
    if not isinstance(choices, list) or any(not isinstance(item, dict) for item in choices):
        raise ContractError('单元选择覆盖须绑定实际候选列表')
    from .economic_observations import is_series, contract as economic_observations
    if task is not None and is_series(record):
        cells = economic_observations(task, record)['cells']
        complete = bool(cells) and len(cells) <= maximum and fingerprint(cells) == fingerprint(choices)
        return dict(version=API_CELL_CHOICE_COVERAGE_VERSION, selection_complete=complete,
            scan_complete=len(cells) <= maximum, eligible_positions=len(cells), invalid_positions=0,
            maximum=maximum, maximum_scan_units=100000, snapshot_hash=record.get('hash'),
            choices_hash=fingerprint(choices), candidate_is_approval=False, absence_is_proof=False)
    catalogue = api_cell_candidates(record, maximum, task=task)
    expected = {fingerprint(cell['proposal_fields']['record_locator']) for cell in catalogue['cells']}
    offered = {fingerprint(item.get('record_locator')) for item in choices}
    complete = (bool(choices) and catalogue['scan_complete']
        and len(choices) == len(catalogue['cells']) == len(offered)
        and offered == expected and all(item.get('artifact_id') == record.get('id') for item in choices))
    return dict(version=API_CELL_CHOICE_COVERAGE_VERSION, selection_complete=complete,
        scan_complete=catalogue['scan_complete'], eligible_positions=len(catalogue['cells']),
        invalid_positions=catalogue['invalid_positions'], maximum=maximum,
        maximum_scan_units=catalogue['maximum_scan_units'], snapshot_hash=record.get('hash'),
        choices_hash=fingerprint(choices), candidate_is_approval=False, absence_is_proof=False)


def expand_api_cell_selection(task, spec):
    """Expand an explicit selected cell; never repair an older proposal."""
    if not isinstance(spec,dict) or 'api_cell_selection' not in spec:
        return spec
    if set(spec)!={'api_cell_selection','question_ids','review'} or not isinstance(spec['api_cell_selection'],str):
        raise ContractError('单元格选择仅提交候选id、question_ids与review，不混用数值、身份或批准字段')
    matches = [(record,choice) for record in task.get('source_records',[]) for choice in api_cell_choices(task,record)
               if choice['candidate_id']==spec['api_cell_selection']]
    if len(matches)!=1:
        raise ContractError('单元格选择不属于本任务同版、实际取得且未变化的来源')
    record,choice = matches[0]
    return _api_cell_selection_spec(task, record, choice, spec)


def _api_cell_selection_spec(task, record, choice, spec):
    """Use a freshly resolved locator; the normal validators still follow."""
    from .economic_observations import is_series, selection_spec as economic_spec
    if is_series(record):
        return economic_spec(task, record, choice, spec)
    identity = ({'candidate_code_reference':copy.deepcopy(choice['candidate_code_reference'])}
        if 'candidate_code_reference' in choice else {'direct_code_reference':direct_code_reference(task,record)})
    return {'id':choice['candidate_id'],'artifact_id':record['id'],'object':choice['object'],
        'record_locator':copy.deepcopy(choice['record_locator']),'nature':'已实现事实',
        **identity,'api_value_reference':api_value_reference(record),
        'availability':current_snapshot_reference(task,record),'published_at':None,
        'publication':{'at':None,'basis_ref':None,'precision':'未知','timezone':'未知','certainty':'未知',
                       'basis':'首次公开时间未知，当前可得性另由实际取数账本核验'},
        'calculation_context':copy.deepcopy(choice['calculation_context']),
        'api_context_reference':{'version':1,'artifact_id':record['id'],'snapshot_hash':record['hash']},
        'question_ids':copy.deepcopy(spec['question_ids']),'review':copy.deepcopy(spec['review'])}


def expand_api_cell_selections(task, specs):
    """Decode explicit same-source groups without approving or losing rows.

    Sharing a review is only a wire-format choice. Each expanded cell keeps
    its own original locator, value, period, identity and context, then follows
    the ordinary source/applicability validators. Different needs use separate
    groups or individual selections. No automatic filling or row selection.
    """
    if not isinstance(specs, list):
        raise ContractError('单元格提取规格须为列表')
    expanded, selected, catalog = [], set(), None

    def resolve(ident, spec):
        nonlocal catalog
        if catalog is None:
            catalog = {}
            for record in task.get('source_records', []):
                for choice in api_cell_choices(task, record):
                    catalog.setdefault(choice['candidate_id'], []).append((record, choice))
        matches = catalog.get(ident, [])
        if len(matches) != 1:
            raise ContractError('单元格选择不属于本任务同版、实际取得且未变化的来源')
        record, choice = matches[0]
        return _api_cell_selection_spec(task, record, choice, spec)

    for spec in specs:
        if isinstance(spec,dict) and 'index_weight_table_selection' in spec:
            from .index_sources import expand_weight_selection
            group=expand_weight_selection(task,spec)
            used={item.get('id') for item in expanded if isinstance(item,dict)}
            if used.intersection(item['id'] for item in group):
                raise ContractError('同一权重单元不能重复选择')
            expanded.extend(group)
            if len(expanded)>1000:raise ContractError('权重及其他提取合计超过1000项')
            continue
        if not isinstance(spec, dict) or 'api_cell_selections' not in spec:
            if isinstance(spec, dict) and 'api_cell_selection' in spec:
                if (set(spec) != {'api_cell_selection', 'question_ids', 'review'}
                        or not isinstance(spec['api_cell_selection'], str)):
                    raise ContractError('单元格选择仅提交候选id、question_ids与review，不混用完整格式')
                value = resolve(spec['api_cell_selection'], spec)
                if value['id'] in selected:
                    raise ContractError('同一单元不能在选择批次中重复提交')
                selected.add(value['id'])
                expanded.append(value)
            else:
                expanded.append(spec)
            continue
        if set(spec) != {'api_cell_selections', 'question_ids', 'review'}:
            raise ContractError('单元格分组只提交候选列表、question_ids与review，不混用完整格式')
        ids = spec['api_cell_selections']
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 64
                or any(not isinstance(ident, str) or not ident for ident in ids)
                or len(set(ids)) != len(ids)):
            raise ContractError('单元格分组须为1至64个不重复的候选id')
        group = [resolve(ident, spec) for ident in ids]
        if len({item['artifact_id'] for item in group}) != 1:
            raise ContractError('共享核验说明的单元格分组不能跨原件')
        for item in group:
            if item['id'] in selected:
                raise ContractError('同一单元不能在选择批次中重复提交')
            selected.add(item['id'])
        expanded.extend(group)
    if len(selected) > 64:
        raise ContractError('本次短引用单元总数不能超过64，完整格式仍保留')
    return expanded


MATERIAL_CONTEXT_TOKENS = {
        "currency": {"CNY": ("人民币",)}, "price_basis": {"unadjusted": ("未复权",)},
        "price_kind": {"close": ("收盘价",)}, "nav_kind": {"official": ("单位净值", "基金份额净值")},
        "accounting_scope": {"consolidated": ("合并利润表", "合并现金流量表"), "parent": ("母公司利润表", "母公司现金流量表")},
        "accounting_standard": {"CAS": ("企业会计准则",), "IFRS": ("国际财务报告准则", "International Financial Reporting Standards"),
                                "US_GAAP": ("美国公认会计原则", "US GAAP", "U.S. GAAP")},
        "metric": {"revenue": ("营业收入",), "operating_cost": ("营业成本",),
                   "net_profit": ("净利润",), "operating_cash_flow": ("经营活动产生的现金流量净额",),
                   "management_fee": ("管理费",), "custody_fee": ("托管费",)},
        "fee_basis": {"net_assets": ("基金资产净值", "基金净资产")},
        "rate_period": {"annual": ("年费率", "年管理费率", "年托管费率")},
        "index_variant": {"price": ("价格指数",), "gross_total_return": ("全收益指数", "总收益指数"),
                          "net_total_return": ("净全收益指数", "净总收益指数", "净收益指数")},
        "level_kind": {"close": ("收盘点位",)},
        "corporate_action_status": {"present": ("发生公司行为", "存在公司行为"), "verified_none": ("无公司行为", "未发生公司行为")},
}
MATERIAL_CONTEXT_LITERAL_FIELDS = frozenset({
    "security_id", "fund_id", "entity_id", "share_class", "period", "observation_date",
    "valuation_time", "valuation_date", "window_start", "window_end", "index_id", "index_currency", "index_base"})
FEE_METRIC_TOKENS = {"management_fee": ("管理费", "管理人报酬"), "custody_fee": ("托管费",)}


LISTED_ETF_UNIT_PREFIX = 'listed_etf_unit:'
LISTED_REPORT_UNIT_PREFIX = 'listed_report_unit:'
_ETF_TRADING_CODE = re.compile(r'(?<![A-Za-z0-9])(?:场内)?交易代码\s*[:：]?\s*([0-9]{6})(?![0-9])')
_ETF_OPERATION = re.compile(r'基金运作方式\s*(?:为|[:：])?\s*交易型开放式')
_MULTIPLE_SHARE_CLASSES = re.compile(r'(?:下属分级|下属各类|各类基金份额|份额类别|(?<![A-Za-z])[A-ZＡ-Ｚ]\s*类(?:基金)?份额)')
_UNCONFIRMED_TRADING_CODE = re.compile(r'(?:拟|计划|尚未|并非|不是|非)\s*(?:使用|采用|启用|确定|的)?\s*(?:场内)?交易代码')


def listed_etf_unit_context_candidates(view, object_code):
    """Locate an explicitly identified listed ETF unit, not a guessed A/C class.

    A positive product-operation field and unique trading-code field must be
    present in one retained part. Ambiguous class disclosures stay unresolved.
    The returned line range is only an optional, non-approving source address.
    """
    if not isinstance(object_code, str) or not re.fullmatch(r'\d{6}\.(?:SH|SZ)', object_code):
        return []
    parts = view.get('parts', [])
    if any(_MULTIPLE_SHARE_CLASSES.search(part['text']) or _UNCONFIRMED_TRADING_CODE.search(part['text']) for part in parts):
        return []
    if {match.group(1) for part in parts for match in _ETF_TRADING_CODE.finditer(part['text'])} != {object_code[:6]}:
        return []
    from .financial_sources import original_line_index, selected_original_span
    lines = original_line_index(view)
    result = []
    for fragment, part in enumerate(parts):
        text = part['text']
        codes = list(_ETF_TRADING_CODE.finditer(text))
        operation = _ETF_OPERATION.search(text)
        if not operation or {match.group(1) for match in codes} != {object_code[:6]}:
            continue
        first, last = min(codes[0].start(), operation.start()), max(codes[-1].end(), operation.end())
        positioned, offset = [], 0
        for line in (line for line in lines if line['fragment_index'] == fragment):
            end = offset + len(line['text'])
            if end > first and offset < last:
                positioned.append(line['index'])
            offset = end
        if not positioned or positioned[-1] - positioned[0] >= 200:
            continue
        span = dict(start=positioned[0], end=positioned[-1])
        selected = selected_original_span(lines, span)
        result.append(dict(field='share_class', value=LISTED_ETF_UNIT_PREFIX + object_code,
            span=span, **selected, basis='explicit_etf_operation_and_trading_code',
            candidate_is_approval=False, ac_category_inferred=False))
    return result


def listed_report_unit_context_candidates(view, object_code, *, report_date=None):
    """A report's explicitly listed fund unit, not a trading-code mapping.

    Require one primary code, ETF operation, exchange and already-past listing
    date in the SAME part. A/C, future listings, other codes and inferred
    exchange suffixes never qualify. The descriptor is not an A/C category.
    """
    venues = {'SH':'上海证券交易所', 'SZ':'深圳证券交易所'}
    if not isinstance(object_code,str) or not re.fullmatch(r'\d{6}\.(?:SH|SZ)',object_code):
        return []
    report_date = report_date if report_date is not None else view.get('directory',{}).get('report_date')
    try:
        if not isinstance(report_date,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',report_date):
            return []
        timepoint(report_date)
    except ContractError:
        return []
    parts = view.get('parts',[])
    code_pattern = re.compile(r'基金主代码\s*[:：]?\s*([0-9]{6})(?![0-9])')
    if (any(_MULTIPLE_SHARE_CLASSES.search(p['text']) or re.search(r'(?:拟|计划|尚未|并非|不是)\s*(?:上市|基金主代码)',p['text']) for p in parts)
            or {m[1] for p in parts for m in code_pattern.finditer(p['text'])} != {object_code[:6]}):
        return []
    listing = re.compile(r'基金份额上市(?:的)?证券\s*交\s*易\s*所\s*[:：]?\s*(上海证券交易所|深圳证券交易所)')
    dated = re.compile(r'上市日期\s*[:：]?\s*('+PUBLICATION_DATE_PATTERN+')')
    from .financial_sources import original_line_index, selected_original_span
    lines, result = original_line_index(view), []
    for fragment,part in enumerate(parts):
        text = part['text']
        code, operation, venue, date = code_pattern.search(text), _ETF_OPERATION.search(text), listing.search(text), dated.search(text)
        if not all((code,operation,venue,date)) or venue[1] != venues[object_code[-2:]]:
            continue
        try:
            if _publication_date_value(date[1]) > report_date:
                continue
        except (ValueError,ContractError):
            continue
        first, last = min(m.start() for m in (code,operation,venue,date)), max(m.end() for m in (code,operation,venue,date))
        positions, offset = [], 0
        for line in (row for row in lines if row['fragment_index']==fragment):
            end = offset + len(line['text'])
            if end > first and offset < last:positions.append(line['index'])
            offset = end
        if not positions or positions[-1]-positions[0] >= 200:
            continue
        span = dict(start=positions[0],end=positions[-1])
        result.append(dict(field='share_class',value=LISTED_REPORT_UNIT_PREFIX+object_code,
            span=span,**selected_original_span(lines,span),basis='explicit_primary_code_etf_operation_and_share_listing',
            candidate_is_approval=False,ac_category_inferred=False,trading_code_mapping_approved=False))
    return result


def material_calculation_context_contract(fields):
    """Describe the same registered values used by source validation, not facts."""
    selected = set(fields)
    canonical_values={field:copy.deepcopy(values) for field,values in MATERIAL_CONTEXT_TOKENS.items()
        if field in selected}
    if 'fee_basis' in selected and 'metric' in selected:
        canonical_values['metric'].update(copy.deepcopy(FEE_METRIC_TOKENS))
    return {"version": 1,
            "canonical_values": canonical_values,
            "literal_fields": sorted((selected & MATERIAL_CONTEXT_LITERAL_FIELDS) - {'valuation_date'}),
            **({'listed_etf_unit_identity': {
                'field':'share_class', 'prefix':LISTED_ETF_UNIT_PREFIX,
                'rule':'仅当同原件明确基金运作方式为交易型开放式且交易代码唯一匹配本对象时，用该上市交易份额标识。不是A/C类别；缺少明确字段或存在多类别歧义时不提供，不由代码前缀、简称或缺少A/C文字推断。'}}
               if 'share_class' in selected else {}),
            **({'normalized_date_fields': {'valuation_date': {
                'format': 'YYYY-MM-DD', 'rule': '仅把所定位原文的明确年月日表示为同一日；须等于当前原值所属日，不补钟点或推断可交易时刻。'}}}
               if 'valuation_date' in selected else {}),
            "annual_numeric_unit": {"value": "annual", "source_unit": "%/年",
                                    "rule": "须紧随当前原值数字，定位同一数值摘录或明确单位表头；不能借其他费用行"}
                                   if "rate_period" in selected else None,
            "rule": "枚举字段使用canonical_values的键，不把中文原文标签作为值；literal_fields保留原件中的身份或期间。"
                    "别名仅描述核验语法，不提供本次事实、依据、批准或默认值。缺少原件依据仍保留缺口。",
            "automatic_adoption": False, "automatic_approval": False}


def _explicit_annual_rate(item, quote):
    """An annual suffix belongs to this numeric rate, never a nearby quantity."""
    token = item.get("raw_token")
    if not isinstance(token, str) or not token:
        return False
    from .fee_sources import annual_fee_mentions
    metric=item.get('calculation_context',{}).get('metric')
    return any(match['metric']==metric and match['raw_token']==token
        for match in annual_fee_mentions(quote))


_NON_OFFICIAL_NAV_LABEL = re.compile(
    r'(?:累计|参考|估算|预估|估计|复权)\s*(?:基金\s*)?(?:份额\s*)?(?:单位\s*)?净值')


def validate_official_nav_number(item, quote):
    """Bind a per-share NAV label to this number, not any nearby quantity.

    These labels are used by exchange disclosures. A recognised spelling is
    not source approval, proof of share class, currency, or valuation date.
    Ambiguous multi-column tables still need their own registered selectors.
    """
    label = r'(?:单位净值|基金份额净值)'
    if 'IOPV' in quote.upper() or _NON_OFFICIAL_NAV_LABEL.search(quote):
        raise ContractError('累计、参考或估算净值不能作为当日官方单位净值')
    token = item.get('raw_token')
    if not isinstance(token, str) or not token:
        raise ContractError('官方单位净值须定位明确原值，不由文字摘录生成计算输入')
    pattern = label + r'\s*(?:[（(]\s*元\s*[）)]\s*)?(?:为|[:：])?\s*' + re.escape(token) + r'(?![0-9A-Za-z_.,%％])'
    if re.search(pattern, quote) is None:
        raise ContractError('所选原值未与官方单位或基金份额净值标签明确对应，不能借相邻收益率或资产金额')
    return True


def validate_calculation_context_basis(item, records=(), *, task=None):
    """Check source anchors, not general table semantics or financial suitability."""
    if "calculation_context" not in item:
        return
    context, bases = item["calculation_context"], item.get("calculation_context_basis")
    if not isinstance(context, dict) or not context or not isinstance(bases, dict) or set(context) != set(bases):
        raise ContractError("计算口径及逐字段来源依据缺失或不一致")
    tokens, literal_fields = MATERIAL_CONTEXT_TOKENS, MATERIAL_CONTEXT_LITERAL_FIELDS
    for field, value in context.items():
        anchor = bases[field]
        if not isinstance(value, str) or not value.strip() or not isinstance(anchor, dict):
            raise ContractError("计算口径及来源定位须为明确字段，不能使用布尔批准标签")
        if field == "corporate_action_status" and value == "unknown" and anchor == {"kind": "unverified"}:
            continue
        if field not in tokens and field not in literal_fields:
            raise ContractError("计算口径字段未登记")
        record = next((r for r in records if r.get("id") == anchor.get("artifact_id")), None)
        if record is None or record.get("task_id") != item.get("task_id") or record.get("research_version") != item.get("research_version"):
            raise ContractError("计算口径原件未在本任务及版本归档")
        digest = record.get("snapshot_hash", record.get("hash"))
        if anchor.get("snapshot_hash") != digest or fingerprint(record.get("snapshot")) != digest:
            raise ContractError("计算口径原件哈希不一致")
        if anchor.get("kind") == "api":
            artifact = item.get("artifact", {})
            if field == 'share_class' and artifact.get('id') != record['id']:
                from .etf_sources import validate_share_context
                validate_share_context(task, item, record, anchor)
                continue
            if record.get("kind") != "api" or artifact.get("id") != record["id"] or item.get("snapshot_hash") != digest:
                raise ContractError("计算口径API依据须绑定当前证据同一快照及字段")
            if anchor.get("field") != field:
                raise ContractError("计算口径API依据field须为" + field + "，不能使用接口参数或行情列名")
            definition = _api_field_definition(record)
            _, code, date, column = _api_cell(record, item.get("record_locator", {}))
            if not definition or column not in definition["fields"]:
                raise ContractError("计算口径API字段未登记")
            identity = item.get("identity", {})
            if identity.get("provider_code") != code or identity.get("object") != item.get("object"):
                raise ContractError("计算口径证券身份不一致")
            derived = _api_calculation_context_values(record, item.get("record_locator", {}), identity)
            if value != derived.get(field):
                raise ContractError("计算口径不能由当前API字段或请求参数确定")
            if field in ("observation_date", "window_start", "window_end"):
                timepoint(value)
            continue
        if anchor.get("kind") != "material" or record.get("kind") not in ("material", "filing"):
            raise ContractError("计算口径须定位真实材料或已登记API依据")
        material = record["snapshot"].get("material") if record["kind"] == "filing" else record["snapshot"]
        if not isinstance(material, dict) or material.get("status") != "已解析":
            raise ContractError("计算口径材料尚未解析")
        try:
            original = base64.b64decode(material["original_base64"], validate=True)
        except (KeyError, ValueError):
            raise ContractError("计算口径原件字节缺失") from None
        if hashlib.sha256(original).hexdigest() != material["hash"]:
            raise ContractError("计算口径原件字节哈希不一致")
        from .material_parser import verified_material
        extracted = verified_material(material, original)
        if extracted["parts"] != material["parts"] or extracted["extract_version"] != material["extract_version"]:
            raise ContractError("计算口径摘录与原件提取版本不一致")
        text = next((p["text"] for p in extracted["parts"] if p["locator"] == anchor.get("part")), "")
        quote = anchor.get("quote")
        if not isinstance(quote, str) or not quote.strip() or quote not in text:
            raise ContractError("计算口径摘录不在指定原件位置")
        accepted = tokens.get(field, {}).get(value, ()) if field in tokens else (value,)
        if field=='metric' and value in FEE_METRIC_TOKENS:
            accepted=FEE_METRIC_TOKENS[value]
        # Chinese financial labels can immediately precede their numeric value.
        # Literal identities/dates still require exact alphanumeric boundaries.
        right_boundary = r"(?![A-Za-z])" if field in ("metric", "level_kind") else r"(?![A-Za-z0-9])"
        explicit_annual = (field == "rate_period" and value == "annual" and
                           context.get("metric") in ("management_fee", "custody_fee") and _explicit_annual_rate(item, quote))
        pdf_metric = (field == 'metric' and value in FINANCIAL_METRIC_LABELS
                      and item.get('record_locator', {}).get('financial_column', {}).get('version') in (2, 3))
        from .pdf_material import FINANCIAL_CONTEXT_EXTRACT_VERSIONS
        financial_currency = (field == 'currency' and context.get('metric') in FINANCIAL_METRIC_LABELS
                              and material.get('extract_version') in FINANCIAL_CONTEXT_EXTRACT_VERSIONS)
        if financial_currency:
            from .financial_sources import validate_financial_currency_basis
            validate_financial_currency_basis(item, record, anchor)
        if field == 'accounting_standard' and context.get('metric') in FINANCIAL_METRIC_LABELS:
            from .financial_sources import financial_statement_unit_scope_enabled,validate_financial_standard_basis
            if financial_statement_unit_scope_enabled(task):
                validate_financial_standard_basis(item,record,anchor)
        fee_period = (field == 'period' and context.get('metric') in ('management_fee','custody_fee')
                      and item.get('reporting_period_basis') == anchor)
        valuation_date = False
        listed_unit = False
        if field == 'share_class' and value.startswith(LISTED_ETF_UNIT_PREFIX):
            expected = LISTED_ETF_UNIT_PREFIX + str(item.get('object', ''))
            candidates = listed_etf_unit_context_candidates(extracted, item.get('object'))
            listed_unit = value == expected and any(candidate['part'] == anchor.get('part')
                and candidate['quote'] in quote for candidate in candidates)
            if not listed_unit:
                raise ContractError('上市ETF份额身份须由同原件明确交易型开放式及唯一交易代码证明，不推断A/C或跨基金使用')
        if field == 'share_class' and value.startswith(LISTED_REPORT_UNIT_PREFIX):
            candidates = listed_report_unit_context_candidates(extracted,item.get('object'),
                report_date=record.get('directory',{}).get('report_date'))
            listed_unit = value == LISTED_REPORT_UNIT_PREFIX+str(item.get('object','')) and any(
                candidate['part']==anchor.get('part') and candidate['quote'] in quote for candidate in candidates)
            if not listed_unit:
                raise ContractError('报告份额须以同原件基金主代码、ETF运作方式、交易所及已上市日期证明，不推定交易代码或A/C')
        if field == 'valuation_date':
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is None or value != item.get('period'):
                raise ContractError('估值日须为与当前原值所属日一致的明确日期，不能填时间戳或补所属期')
            timepoint(value)
            valuation_date = any(_publication_date_value(match.group()) == value
                                 for match in re.finditer(PUBLICATION_DATE_PATTERN, quote))
        if fee_period:
            # A PDF can wrap a literal duration. Use the same explicitly bound
            # reporting-period proof as current-original review; only whitespace
            # is ignored in this context label, never in the source quotation.
            if value != item.get('period'):
                raise ContractError('费用计算期间须与同原件报告期间依据一致')
            validate_reporting_period_basis(item,record)
        if pdf_metric:
            # PDF wrapping can split a label inside its actual ruled cell.
            # The full cell/period/amount proof replaces only token matching,
            # never original-byte verification or financial applicability.
            validate_financial_metric_number(item, record, anchor, task=task)
        if not explicit_annual and not pdf_metric and not financial_currency and not fee_period and not valuation_date and not listed_unit and (not accepted or not any(
                re.search(r"(?<![A-Za-z0-9])" + re.escape(token) + right_boundary, quote) for token in accepted)):
            raise ContractError("计算口径值与所定位原文不一致")
        if field == 'nav_kind':
            if 'IOPV' in quote.upper() or _NON_OFFICIAL_NAV_LABEL.search(quote):
                raise ContractError('累计、参考或估算净值不能作为当日官方单位净值')
            if 'value' in item:
                if quote not in item.get('text', '') or anchor.get('part') != item.get('record_locator', {}).get('part'):
                    raise ContractError('净值类型须定位当前数值摘录，不能借另一行标签')
                validate_official_nav_number(item, quote)
        if field in ("index_variant", "level_kind"):
            if quote not in item.get("text", "") or anchor.get("part") != item.get("record_locator", {}).get("part"):
                raise ContractError("指数版本和收盘口径须定位当前点位摘录，不能借用另一行标签")
            if field == "index_variant" and value == "gross_total_return" and any(
                    token in quote for token in tokens["index_variant"]["net_total_return"]):
                raise ContractError("净全收益版本不能作为全收益版本依据")
        if field == "metric" and value == "operating_cost" and "营业总成本" in quote:
            raise ContractError("营业总成本不能作为营业成本依据")
        if field == "metric" and not pdf_metric:
            validate_financial_metric_number(item, record, anchor, task=task)
        if field == 'accounting_scope':
            validate_financial_statement_scope(item, record, anchor)
        if field == "metric" and value in ("net_profit", "operating_cash_flow"):
            excerpt = item.get("text")
            if not isinstance(excerpt, str) or quote not in excerpt:
                raise ContractError("净利润或现金流指标依据须位于当前数值摘录，不能借用另一行标签")
            if value == "net_profit" and any(token in excerpt for token in ("归属于", "归属母公司", "归母", "扣除非经常", "扣非", "每股")):
                raise ContractError("归母、扣非或每股指标不能作为报表净利润")
        if field == 'metric' and value in ('management_fee', 'custody_fee'):
            excerpt = item.get('text')
            if not isinstance(excerpt, str) or quote not in excerpt:
                raise ContractError('费用类别依据须位于当前数值摘录，不能借用其他费用行')
            from .fee_sources import validate_fee_component_binding
            validate_fee_component_binding(item)
        if field == 'rate_period' and context.get('metric') in ('management_fee', 'custody_fee'):
            artifact_id = item.get('artifact', {}).get('id', item.get('artifact_id'))
            locator = item.get('record_locator', {})
            excerpt = item.get('text', '')
            same_excerpt = isinstance(excerpt, str) and quote in excerpt and anchor.get('part') == locator.get('part')
            explicit_unit_header = locator.get('unit_part') and anchor.get('part') == locator['unit_part']
            if record['id'] != artifact_id or not (same_excerpt or explicit_unit_header):
                raise ContractError('费用年度频率须定位同一数值原件的摘录或明确单位表头，不能借其他原件')
            if same_excerpt and not _explicit_annual_rate(item,quote):
                raise ContractError('年度频率须属于当前费用原值，不能借同段其他费用条款')
        if field == "corporate_action_status" and value == "present" and any(token in quote for token in ("未发生公司行为", "无公司行为", "不存在公司行为")):
            raise ContractError("公司行为存在状态与原文否定表述不一致")


def _publication_date_value(raw):
    """Compare original date tokens, without rewriting quotes or source bytes."""
    if '年' in raw:
        year, month, day = re.findall(r'\d+', raw)
        raw = f'{int(year):04}-{int(month):02}-{int(day):02}'
    return timepoint(raw).date().isoformat()


def _validate_publication_quote(text, quote, published):
    """Check literal date association, without inferring publication metadata.

    Release cues and dates are paired within their actual original clause.
    Other dates may remain in a complete quote but cannot borrow that pairing.
    A clipped quote must not hide a planned/negative qualifier in the original.
    This structural check does not prove the financial meaning of a release.
    """
    declared_day = timepoint(published).date().isoformat()
    date_pattern = PUBLICATION_DATE_PATTERN

    def date_value(match):
        return _publication_date_value(match.group())

    def distance(left, right):
        return max(left.start() - right.end(), right.start() - left.end(), 0)

    # No occurrence index exists in the current contract. If identical quoted
    # words have both planned and released uses within this part, the anchor is
    # ambiguous; the host must select a more precise quote or a different part.
    starts = [match.start() for match in re.finditer(re.escape(quote), text)]
    for start in starts:
        bound = False
        for marker in PUBLICATION_CUE_RE.finditer(quote):
            absolute = start + marker.start()
            separators = list(re.finditer(r'[。；;，,！？!?]', text[:absolute]))
            clause_start = separators[-1].end() if separators else 0
            following = re.search(r'[。；;，,！？!?]', text[absolute:])
            clause_end = absolute + following.start() if following else len(text)
            clause = text[clause_start:clause_end]
            cue_start = absolute - clause_start
            cue = next(candidate for candidate in PUBLICATION_CUE_RE.finditer(clause)
                       if candidate.start() == cue_start)
            dates = list(re.finditer(date_pattern, clause))
            if not dates:
                continue
            nearest_distance = min(distance(cue, date) for date in dates)
            nearest = [date for date in dates if distance(cue, date) == nearest_distance]
            if {date_value(date) for date in nearest} != {declared_day}:
                continue
            if not any(start <= clause_start + date.start() and clause_start + date.end() <= start + len(quote)
                       for date in nearest):
                continue  # Both the release cue and its bound date must be quoted.
            labels = list(PUBLICATION_OTHER_DATE_RE.finditer(clause))
            if any(distance(label, date) <= nearest_distance and
                   distance(label, date) == min(distance(label, other) for other in dates)
                   for label in labels for date in nearest):
                continue
            prefix = clause[max(0, cue_start - 32):cue_start]
            after = [clause[cue.end():date.start()] for date in nearest if date.start() >= cue.end()]
            if PUBLICATION_UNCERTAIN_RE.search(prefix) or any(
                    PUBLICATION_UNCERTAIN_RE.search(span) or re.search(r'\b(?:by|before|after)\b', span, re.I)
                    for span in after):
                raise ContractError('发布依据属于计划或未发布语句，不能声明已确认公开时间；截取文字不改变原文含义')
            bound = True
        if not bound:
            raise ContractError('公开时间未对应所引发布语句；须准确定位该原件发布日期，不能借报告期或生效日')


def validate_publication_basis(item, records):
    """Recheck every research publication claim against its retained original."""
    reference = item.get("publication", {}).get("basis_ref")
    if not isinstance(reference, dict):
        raise ContractError("首次公开时间缺少原始来源定位")
    artifact_id = _evidence_artifact_id(item)
    if not artifact_id or reference.get("artifact_id") != artifact_id:
        raise ContractError("公开时间依据不对应本条证据原件；未核验的外部发布关系不能替代")
    record = _unique_artifact(records, artifact_id)
    if record is None or reference.get("snapshot_hash") != record.get("snapshot_hash", record.get("hash")):
        raise ContractError("公开时间依据不在本任务或内容版本已变化")
    if fingerprint(record.get("snapshot")) != reference["snapshot_hash"]:
        raise ContractError("公开时间原始快照的内容哈希不一致")
    published = item.get("published_at")
    if reference.get("kind") == "directory":
        if record.get("kind") != "filing" or reference.get("field") != "published_at" or \
                not record["snapshot"]["directory"].get("published_at") or record["snapshot"]["directory"]["published_at"] != published:
            raise ContractError("公开时间与公告目录原始字段不一致")
        return
    material = record.get("snapshot", {}).get("material") if record.get("kind") == "filing" else record.get("snapshot") if record.get("kind") == "material" else None
    if not material or material.get("status") != "已解析":
        raise ContractError("未解析原件不能作为公开时间依据")
    try:
        original = base64.b64decode(material["original_base64"], validate=True)
    except (ValueError, KeyError):
        raise ContractError("公开时间依据缺少完整原件字节") from None
    if hashlib.sha256(original).hexdigest() != material["hash"]:
        raise ContractError("公开时间原件字节与哈希不符")
    from .material_parser import verified_material
    extracted = verified_material(material, original)
    if extracted["parts"] != material["parts"] or extracted["extract_version"] != material["extract_version"]:
        raise ContractError("公开时间摘录无法从原件重新提取")
    text = next((p["text"] for p in material["parts"] if p["locator"] == reference.get("part")), "")
    quote = reference.get("quote")
    if not isinstance(quote, str) or not quote or quote not in text or PUBLICATION_CUE_RE.search(quote) is None:
        raise ContractError("原文未明确定位发布或披露日期，所属期与生效日不能替代")
    stamp = timepoint(published)
    matching_date = (published in quote if len(published) != 10 else any(
        _publication_date_value(match.group()) == stamp.date().isoformat()
        for match in re.finditer(PUBLICATION_DATE_PATTERN, quote)))
    if not matching_date:
        raise ContractError("声明公开时间不是所定位的原文日期，不能补造盘中时间")
    _validate_publication_quote(text, quote, published)


def verification_receipt(item, task, component, status, reason):
    version = 2 if component == "EvidenceCollector" and item.get("artifact", {}).get("kind") == "api" else 1
    identity = item.get('identity')
    if component == 'EvidenceCollector' and isinstance(identity, dict) and identity.get('mode') == 'direct_provider_observation':
        version = 3
    if component == 'EvidenceCollector' and isinstance(identity, dict) and identity.get('mode') == 'candidate_provider_observation':
        version = 4
    value = {"component": component, "version": version, "task_id": task["id"], "research_version": task["version"],
            "input_hash": task["input_hash"], "evidence_hash": evidence_binding(item), "snapshot_hash": item["snapshot_hash"],
            "status": status, "reason": reason}
    if component == "ResearchAnalyzer":
        value["review_hash"] = fingerprint(item.get("applicability_review"))
    return value


def _validate_research_publication(item, task, records):
    """Host, model and restored receipts use the same temporal evidence gate."""
    if not validate_evidence_usage(item, task, item.get("artifact")) and not validate_current_snapshot(item, task, records):
        validate_publication_basis(item, records)


def _registered_fixture_evidence(item, task):
    """Only exact registered case evidence can use the synthetic protocol."""
    if item.get("source") != "合成验收原件":
        return False
    from .cases import get_case
    key = task.get("fixture")
    if not isinstance(key, str) or not key:
        raise ContractError("合成标签不能代替本任务登记的验收案例原件")
    try:
        case = get_case(key)
    except (KeyError, ValueError, TypeError):
        raise ContractError("验收案例未登记，不能跳过原件核验") from None
    if any(task.get(field) != case[field] for field in ("question", "intent", "objects")):
        raise ContractError("验收案例与本任务研究范围不一致")
    original = next((record for record in case["evidence"] if record["id"] == item.get("id")), None)
    runtime = {"task_id": task["id"], "research_version": task["version"], "content_version": 1}
    if original is None or any(item.get(field) != value for field, value in original.items()) or \
            set(item) - set(original) - set(runtime) or \
            any(item[field] != value for field, value in runtime.items() if field in item):
        raise ContractError("合成证据不是本案例登记的原始内容，不能跳过核验")
    return True


def validate_provenance(item, task):
    """Bare success flags are never sufficient for non-fixture material."""
    if _registered_fixture_evidence(item, task):
        return
    registered = task.get("source_records", []) + task.get("materials", [])
    artifact = item.get("artifact")
    retained = _unique_artifact(registered, _evidence_artifact_id(item))
    if (retained is None or not isinstance(artifact, dict) or artifact.get("id") != retained["id"]
            or retained.get("snapshot") != artifact.get("snapshot")
            or retained.get("snapshot_hash", retained.get("hash")) != item.get("snapshot_hash")):
        raise ContractError("原件未在本任务唯一归档，不能只提交核验标签或自造原件")
    _bound(retained, task)
    if item.get("event_group") != retained.get("event_group") or artifact.get("event_group") != retained.get("event_group"):
        raise ContractError("事件分组不属于留存原件，不能把摘录编号作为独立事件")
    _source_extract(item, task)
    validate_calculation_context_basis(item, registered, task=task)
    _validate_research_publication(item, task, registered)
    for component, key in (("EvidenceCollector", "source_receipt"), ("ResearchAnalyzer", "applicability_receipt")):
        receipt = item.get(key)
        if not isinstance(receipt, dict) or receipt != verification_receipt(item, task, component, "通过", receipt.get("reason")) or not receipt.get("reason"):
            raise ContractError("两类核验记录缺失、跨任务或依据已变化")
    from .analyzer import ResearchAnalyzer
    ResearchAnalyzer().assess(task, copy.deepcopy(item), item.get("applicability_review"), task.get("source_records", []))


API_VALUE_REFERENCE_VERSION = 1
API_VALUE_FIELDS = frozenset({"period", "raw_value", "raw_unit", "unit", "value", "conversion_factor"})


def api_value_reference(artifact):
    """Describe an optional value decoder, not a verified evidence item."""
    contract = api_calculation_context_contract(artifact)
    if contract["status"] != "registered_constraints":
        return None
    registry = artifact["source"] + "." + artifact["operation"]
    return {"version": API_VALUE_REFERENCE_VERSION, "artifact_id": artifact["id"],
            "snapshot_hash": contract["snapshot_hash"], "field_registry": registry,
            "field_registry_version": _api_field_definition(artifact)["version"]}


def expand_api_value_reference(task, artifact, spec):
    """Read a deliberately selected cell and its registered numeric definition.

    Identity, availability, interpretation and calculation context stay explicit.
    A compact proposal cannot override any derived field or repair a legacy one.
    All ordinary evidence validators still run after this lossless decoding.
    """
    if "api_value_reference" not in spec:
        return spec
    reference = spec["api_value_reference"]
    required = {"version", "artifact_id", "snapshot_hash", "field_registry", "field_registry_version"}
    if (not isinstance(reference, dict) or set(reference) != required
            or type(reference["version"]) is not int
            or reference["version"] != API_VALUE_REFERENCE_VERSION
            or type(reference["field_registry_version"]) is not int):
        raise ContractError("API数值引用须提供登记版本、同源快照和字段定义版本")
    if API_VALUE_FIELDS.intersection(spec) or "raw_token" in spec:
        raise ContractError("API数值引用不能与手填数字、单位或观测期混用")
    _bound(artifact, task)
    expected = api_value_reference(artifact)
    if expected is None or reference != expected or spec.get("artifact_id") != artifact.get("id"):
        raise ContractError("API数值引用不属于同任务同版的已登记原始响应")
    locator = spec.get("record_locator")
    locator_fields = {"row", "field", "table"} if artifact["source"] == "ifind" else {"row", "field"}
    if not isinstance(locator, dict) or set(locator) != locator_fields:
        raise ContractError("API数值引用须以原始数组的明确行、字段和必要表序号定位")
    raw_value, code, date, field = _api_cell(artifact, locator)
    definition = _registered_api_fields(artifact, code).get(field)
    if definition is None:
        raise ContractError("API数值引用的字段及单位未登记")
    number, factor = _number(raw_value), _number(definition["factor"])
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = max(28, len(number.as_tuple().digits) + len(factor.as_tuple().digits))
            value = number * factor
    except DecimalException:
        raise ContractError("API登记单位换算超出可表示范围") from None
    if not value.is_finite():
        raise ContractError("API登记单位换算不是有限值")
    if value == value.to_integral_value() and value.adjusted() < 4096:
        serialized_value = int(value)
    else:
        serialized_value = float(value)
        if not math.isfinite(serialized_value) or _number(serialized_value) != value:
            raise ContractError("API数值超出JSON数字的无损表示精度，保留原响应，不自动舍入")
    expanded = copy.deepcopy(spec)
    expanded.pop("api_value_reference")
    period = _basic_financial_cell(artifact, locator)['period'] if _is_basic_financial(artifact) else _api_context_date(date)
    expanded.update(period=period, raw_value=copy.deepcopy(raw_value),
                    raw_unit=definition["raw_unit"], unit=definition["unit"],
                    conversion_factor=definition["factor"], value=serialized_value)
    return expanded


def expand_api_context_reference(task, artifact, spec):
    """Decode an explicit compact proposal, never repair legacy references.

    The proposer still supplies every context value and the exact retained
    source ID/hash. Only redundant per-field anchor serialization is generated
    here. Original source, temporal, value and applicability gates remain
    unchanged, and saved evidence always uses the existing expanded format.
    """
    if "api_context_reference" not in spec:
        return spec
    reference = spec["api_context_reference"]
    if (not isinstance(reference, dict)
            or set(reference) != {"version", "artifact_id", "snapshot_hash"}
            or type(reference["version"]) is not int or reference["version"] != 1):
        raise ContractError("紧凑API口径引用须使用已登记版本及完整原件绑定")
    if "calculation_context_basis" in spec:
        raise ContractError("紧凑API引用与逐字段来源不可混用，不能覆盖已有错误依据")
    _bound(artifact, task)
    if (artifact.get("kind") != "api" or spec.get("artifact_id") != artifact.get("id")
            or reference["artifact_id"] != artifact.get("id")
            or reference["snapshot_hash"] != artifact.get("snapshot_hash", artifact.get("hash"))):
        raise ContractError("紧凑API引用不属于当前证据同一原件和快照")
    contract = api_calculation_context_contract(artifact)
    if contract["status"] != "registered_constraints":
        raise ContractError("紧凑API引用没有可核验的登记口径")
    context = spec.get("calculation_context")
    if (not isinstance(context, dict) or not context
            or any(not isinstance(value, str) or not value.strip() for value in context.values())):
        raise ContractError("紧凑API引用须显式提交非空口径值，不自动推定")
    bases = {}
    for field, value in context.items():
        if field == "corporate_action_status" and value == "unknown":
            bases[field] = {"kind": "unverified"}
        elif field in contract["basis_templates"]:
            bases[field] = copy.deepcopy(contract["basis_templates"][field])
        else:
            raise ContractError("紧凑API引用不能补造接口缺失口径或公司行为结论")
    expanded = copy.deepcopy(spec)
    expanded.pop("api_context_reference")
    expanded["calculation_context_basis"] = bases
    return expanded


def candidate_evidence(task, artifact, spec):
    """Produce source-checked evidence; never approve its research applicability."""
    if not isinstance(spec, dict) or not _SAFE_ID.fullmatch(str(spec.get("id", ""))):
        raise ContractError("证据标识未登记或无效")
    _bound(artifact, task)
    from .source_labels import expand as expand_source_label
    spec = expand_source_label(task, artifact, spec)
    if not isinstance(spec.get("record_locator"), dict):
        raise ContractError("证据必须提供字段或页码的结构化定位")
    spec = expand_candidate_code_reference(task, artifact, spec)
    spec = expand_direct_code_reference(task, artifact, spec)
    spec = expand_identity_basis_reference(task, spec)
    spec = expand_api_value_reference(task, artifact, spec)
    if (type(task.get('fixed_financial_context_version')) is int
            and task['fixed_financial_context_version'] == 1 and _is_basic_financial(artifact)
            and not {'calculation_context', 'calculation_context_basis', 'api_context_reference'}.intersection(spec)
            and current_snapshot_reference(task, artifact) is not None):
        # Decode only the unique retained cell's reviewed metadata. Explicit
        # context, including null or wrong context, is never replaced. Identity,
        # date, source and suitability still pass the ordinary gates below.
        cell = _basic_financial_cell(artifact, spec['record_locator'])
        required = {'entity_id', 'metric', 'currency', 'period', 'accounting_scope', 'accounting_standard'}
        values = cell.get('calculation_context', {})
        if required <= set(values) and all(isinstance(values[key], str) and values[key] for key in required):
            spec = copy.deepcopy(spec)
            spec['calculation_context'] = {key: values[key] for key in sorted(required)}
            spec['api_context_reference'] = dict(version=1, artifact_id=artifact['id'],
                                                  snapshot_hash=artifact.get('snapshot_hash', artifact['hash']))
    spec = expand_api_context_reference(task, artifact, spec)
    from .etf_sources import join_price_share_context
    spec = join_price_share_context(task, artifact, spec)
    item = copy.deepcopy(spec)
    item.pop("review", None)
    item.pop("event_group", None)  # Only a retained filing can assign event identity.
    item.update(artifact=copy.deepcopy(artifact), snapshot=copy.deepcopy(artifact["snapshot"]),
                snapshot_hash=artifact.get("snapshot_hash", artifact["hash"]), source=artifact["source"],
                obtained_at=artifact["obtained_at"], task_id=task["id"], research_version=task["version"], content_version=1,
                source_check="待核验", applicability_check="待核验")
    _evidence_artifact_id(item)
    item["locator"] = str(spec.get("record_locator", {}))
    document_only = validate_evidence_usage(item, task, artifact)
    current_only = validate_current_snapshot(item, task)
    if 'observation_date' in item and not current_only:
        raise ContractError('独立观测日期须绑定本次原件的实际取得与逐字定位，不接受自由日期字段')
    if 'observation_period' in item and not current_only:
        raise ContractError('统计所属月须绑定本次统计局原件的实际取得与明确月份，不接受自由期间字段')
    if artifact["kind"] == "api":
        from .economic_observations import is_series, cell as economic_cell
        if is_series(artifact):
            if 'field_definition' in spec:
                raise ContractError('宏观字段定义由留存原件派生，不接受提案自填')
            item['field_definition'] = copy.deepcopy(economic_cell(task, artifact, spec['record_locator'])['field_definition'])
        definition = _api_field_definition(artifact)
        registered_fields = {}
        if definition:
            _, code, _, field = _api_cell(artifact, spec.get("record_locator", {}))
            registered_fields = _registered_api_fields(artifact, code)
        if definition and field in registered_fields:
            item["field_definition"] = {"registry": artifact["source"] + "." + artifact["operation"],
                "version": definition["version"], "field": field, "definition": copy.deepcopy(registered_fields[field]),
                "documentation": definition["documentation"]}
    if artifact.get("event_group"):
        item["event_group"] = artifact["event_group"]
    if artifact["kind"] == "api" and not item.get("text"):
        item["text"] = f"{spec.get('record_locator', {}).get('field', '')}：{spec.get('raw_value')}{spec.get('raw_unit', '')}"
    for key in ("object", "published_at", "period", "nature", "unit"):
        if not ((document_only or current_only) and key == "published_at"):
            _require_text(item.get(key), "证据" + key)
    if item["nature"] not in NATURES:
        raise ContractError("事实、条款、展望、估计和推算须明确区分")
    _source_extract(item, task)
    validate_calculation_context_basis(item, task.get("source_records", []) + task.get("materials", []), task=task)
    _validate_research_publication(item, task, task.get("source_records", []) + task.get("materials", []))
    item["source_check"] = "通过"
    item["source_receipt"] = verification_receipt(item, task, "EvidenceCollector", "通过", "原件完整性、定位、原值及登记单位一致")
    return item


def extract_material(name, payload, *, extract_version=None):
    """Return immutable bytes identity and page/paragraph extracts, never execute text."""
    if len(payload) > 25 * 1024 * 1024:
        raise ContractError("材料超过25MB，请拆分材料")
    suffix = Path(name).suffix.lower()
    version = material_extract_version(name) if extract_version is None else extract_version
    if version not in material_extract_versions(name):
        raise ContractError('材料提取版本未登记，不改写留存正文')
    digest = hashlib.sha256(payload).hexdigest()
    if suffix == ".pdf":
        if version == '1':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(payload))
            parts = [{"locator": f"第{index + 1}页", "text": page.extract_text() or ""}
                     for index, page in enumerate(reader.pages)]
        else:
            from .pdf_material import extract_parts
            parts = extract_parts(payload, extract_version=version)
    elif suffix == ".docx":
        from docx import Document
        doc = Document(io.BytesIO(payload))
        parts = [{"locator": f"段落{index + 1}", "text": para.text} for index, para in enumerate(doc.paragraphs)]
        parts += [{"locator": f"表{index + 1}", "text": "\n".join("\t".join(cell.text for cell in row.cells) for row in table.rows)}
                  for index, table in enumerate(doc.tables)]
    elif suffix in (".txt", ".md"):
        parts = [{"locator": f"段落{index + 1}", "text": text}
                 for index, text in enumerate(payload.decode("utf-8-sig").split("\n\n"))]
    elif suffix in (".html", ".htm"):
        from .html_material import extract_html
        parts = extract_html(payload, extract_version=version)
    else:
        raise ContractError("仅支持PDF、TXT、Markdown、DOCX和HTML")
    readable = any(part["text"].strip() and 'metadata_field' not in part for part in parts)
    limitation = "" if readable else "未提取到正文，可能为扫描件；不生成伪造摘录"
    if suffix == ".pdf":
        unreadable = sum(bool(part.get('pdf_text_limited')) for part in parts)
        if unreadable:
            limitation = f'原件已保留，{unreadable}页文字编码无法可靠读取，未生成这些页面的摘录。'
        if readable and any(part.get('pdf_layout_limited') for part in parts):
            limitation += "部分页面版面提取受限，仅保留文字层；未提取表格，不据此补算金融数值。"
    if suffix in (".html", ".htm"):
        from .html_material import LIMITATION
        limitation = LIMITATION if readable else "未提取到静态正文，动态页面需提供原始材料；不生成伪造摘录"
    return {"name": Path(name).name, "hash": digest, "extract_version": version, "parts": parts,
            "status": "已解析" if readable else "解析受限", "source_check": "待核验", "applicability_check": "待核验",
            "limitation": limitation, "obtained_at": now()}


class _PublicFilingConnection(http.client.HTTPSConnection):
    """Pinned direct DNS or an explicitly configured loopback CONNECT proxy."""

    def __init__(self, host, port=443, timeout=30, *, use_local_proxy=False):
        super().__init__(host, port=port, timeout=timeout, context=verified_ssl_context())
        self.use_local_proxy = use_local_proxy

    def connect(self):
        deadline = time.monotonic() + self.timeout
        proxy = _local_https_proxy() if self.use_local_proxy else None
        addresses = socket.getaddrinfo(proxy[0] if proxy else self.host, proxy[1] if proxy else self.port, type=socket.SOCK_STREAM)
        allowed = (lambda address: ipaddress.ip_address(address[4][0]).is_loopback) if proxy else (lambda address: ipaddress.ip_address(address[4][0]).is_global)
        if not addresses or any(not allowed(address) for address in addresses):
            raise ContractError("本地代理未解析为回环地址" if proxy else "公告原件主机未解析为公共网络地址")
        if proxy:
            self.set_tunnel(self.host, self.port)
        family, kind, protocol, _, target = addresses[0]
        raw = socket.socket(family, kind, protocol)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ContractError("公告原件解析主机超过时间预算")
            raw.settimeout(remaining)
            raw.connect(target)
            if proxy:
                self.sock = raw
                self._tunnel()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ContractError("公告原件连接超过时间预算")
            raw.settimeout(remaining)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            self.sock = None
            raise


def _local_https_proxy():
    """Respect only a user-configured local proxy, not task-supplied routing."""
    value = urllib.request.getproxies().get("https")
    if not value:
        return None
    try:
        url = urlsplit(value)
        local = url.hostname == "localhost" or ipaddress.ip_address(url.hostname).is_loopback
        port = url.port or 80
    except (ValueError, TypeError):
        raise ContractError("公告下载代理配置无效；仅支持无凭据的本地HTTP CONNECT代理") from None
    if (url.scheme != "http" or not local or url.username or url.password or url.path not in ("", "/")
            or url.query or url.fragment):
        raise ContractError("公告下载仅支持已配置的回环HTTP CONNECT代理，不自动改用其他路由")
    return url.hostname, port


def _download_filing_pdf(value, timeout, *, vendor_source_id=None):
    url = urlsplit(_ifind_authorized_resource(value, vendor_source_id)) if vendor_source_id is not None else _filing_url(value)
    deadline = time.monotonic() + timeout
    connection = _PublicFilingConnection(url.hostname, port=443, timeout=timeout, use_local_proxy=True)
    try:
        target = (url.path or "/") + ("?" + url.query if url.query else "")
        connection.request("GET", target, headers={"Accept": "application/pdf", "Accept-Encoding": "identity"})
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ContractError("公告原件连接超过时间预算")
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
        response = connection.getresponse()
        if response.status != 200:
            raise ContractError("公告原件HTTP状态未通过，禁止重定向或自动重试")
        encoding = response.getheader("Content-Encoding")
        if encoding and encoding.lower() != "identity":
            raise ContractError("公告原件不接受压缩传输或不明确的字节大小")
        length = response.getheader("Content-Length")
        if length is not None and (not re.fullmatch(r"[0-9]+", length) or int(length) > FILING_MAX_BYTES):
            raise ContractError("公告原件大小无效或超过25MB")
        body = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ContractError("公告原件下载超过时间预算")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            # One buffered read per deadline check, not a fill-the-buffer loop.
            chunk = response.read1(min(65536, FILING_MAX_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > FILING_MAX_BYTES:
                raise ContractError("公告原件超过25MB")
        if time.monotonic() > deadline:
            raise ContractError("公告原件下载超过时间预算")
        if length is not None and len(body) != int(length):
            raise ContractError("公告原件下载不完整")
        if not body.startswith(b"%PDF-"):
            raise ContractError("公告原件不是PDF，不按扩展名猜测内容")
        return bytes(body)
    finally:
        connection.close()


class EvidenceCollector:
    def __init__(self, offline=True, journal=None):
        self.offline = offline
        self.journal = journal
        self.query_journal = None
        self.original_keeper = None
        # Short-lived process memory only. Never add this token to task state.
        self._ifind_access_cache = None
        # Never persist provider-signed resource URLs. Their retained public
        # locator is not sufficient to download after a process restart.
        self._ifind_original_cache = {}

    @staticmethod
    def _ifind_error_suffix(payload, event):
        """Only a bounded numeric supplier code may enter user diagnostics.

        Free-form errmsg can contain credentials or customer data. Keep that
        out of the ledger and error text, even when the HTTP request failed.
        """
        code = payload.get('errorcode') if isinstance(payload, dict) else None
        if (type(code) is int and abs(code) <= 999999999999 or
                isinstance(code, str) and re.fullmatch(r'-?\d{1,12}', code)):
            event['business_code'] = code
            return '，错误码' + str(code)
        return ''

    def _http_json(self, task, request, timeout, event):
        search = request.get_method() == 'GET' and urlsplit(request.full_url).path == '/ds_service_basic/api/v1/search_edb'
        with open_verified_https(request, timeout=timeout, opener=urllib.request.urlopen,
                                 **({'follow_redirects': False} if search else {})) as response:
            if search:
                raw = response.read(1000001)
                if len(raw) > 1000000:
                    raise ContractError('EDB搜索响应超过限额，保留失败不截断目录')
                return {'text': raw.decode('utf-8')}
            try:
                return json.load(response, object_pairs_hook=unique_json_object)
            except DuplicateJSONField:
                event.update(status='返回结构异常', failure_code='duplicate_response_field')
                raise

    def _download_original(self, task, url, timeout, event, source_id=None):
        return _download_filing_pdf(url, timeout, vendor_source_id=source_id) if source_id else _download_filing_pdf(url, timeout)

    def _require_tinyshare(self):
        try:
            import tinyshare as ts
            return ts
        except ImportError:
            raise ContractError("Tinyshare运行依赖尚未安装，本次未派发请求") from None

    def _tinyshare_rows(self, task, operation, params, token, timeout, event, provider):
        client = provider.pro_api(token, timeout=timeout)
        result = getattr(client, operation)(**params)
        if result is None or not hasattr(result, "to_dict"):
            raise ContractError("Tinyshare返回结构异常，不当作无数据")
        return result.to_dict(orient="records")

    def _finish_query(self, task, record):
        if self.journal is None:
            return
        if record is not None and self.query_journal is not None:
            self.query_journal(task, record)
        else:
            self.journal(task)

    def _tinyshare_checkpoint(self, task, event, record=None):
        if self.journal:
            try:
                self._finish_query(task, record)
            except ContractError:
                raise
            except Exception:
                event.update(status="检查点保存失败", failure_code="source_checkpoint_failed")
                raise ContractError("Tinyshare调用检查点保存失败，未继续派发；已登记用量保留") from None

    def external(self, task, source, operation, params, credentials):
        if self.offline:
            raise ContractError("离线验收已阻断全部外部数据调用")
        params = validate_source_request(task, source, operation, params)
        if source == 'official':
            return self._official(task, operation, params)
        if source == "ifind" and operation == "filing_original":
            return self._filing_original(task, params, credentials)
        if not isinstance(credentials, dict):
            raise ContractError("数据源凭据必须来自ResearchHelper独立配置")
        if source == "ifind":
            access = self._credential(credentials.get("access_token", ""))
            refresh = self._credential(credentials.get("refresh_token", ""))
            if not access and not refresh:
                raise ContractError("请配置ResearchHelper独立Access Token或Refresh Token")
            token = self._resolve_ifind_access(task, refresh) if refresh else access
            if operation in IFIND_METADATA_OPERATIONS:
                return self._ifind_metadata(task, operation, params, token, (access, refresh))
            return self._ifind(task, operation, params, token, (access, refresh))
        if source == "tinyshare":
            token = credentials.get("token")
            if not isinstance(token, str) or not token:
                raise ContractError("需提供独立Tinyshare凭据，不读取其他项目配置")
            return self._tinyshare(task, operation, params, token)
        if source == "wind":
            raise ContractError("Wind查询与字段口径未登记，本轮不能派发")
        raise ContractError("未知数据源")

    def _official(self, task, operation, params):
        if operation in ('etf_pcf', ETF_LISTING) or 'official.'+operation in ETF_TRADING_REGISTRY:
            return self._official_etf(task, params, operation=operation)
        if operation in (HK_EVENT_HISTORY, MAINLAND_EVENT_HISTORY, H15_TREASURY_YIELDS, LPR_HISTORY, NFRA_BANK_STATISTICS):
            return self._official_event_history(task, operation, params)
        if 'official.'+operation in INDEX_REQUEST_REGISTRY:
            return self._official_index(task,operation,params)
        from .official_sources import validate_request
        from .source_transport import official_html, official_announcement
        from .announcement_sources import CHANNELS as ANNOUNCEMENT_CHANNELS
        selected = validate_request(task, operation, params)
        channel = (params.get('channel') if operation == 'release_catalog' else
            next(record['parameters']['channel'] for record in task['source_records']
                 if record['id'] == params['catalog_id']))
        from .miit_releases import CHANNELS as MIIT_CHANNELS
        announcement = channel in ANNOUNCEMENT_CHANNELS
        miit = channel in MIIT_CHANNELS
        if announcement and operation=='release_catalog':
            recovered=self._recover_official_catalogue(task,params)
            if recovered is not None:return recovered
        issuer_payload = (self._official_issuer(task, params)
                          if announcement and operation == 'release_catalog' else None)
        active=getattr(self,'active',None)
        if active is not None:active(task)
        url = selected if operation == 'release_catalog' else selected['url']
        remaining = task['ledger']['limits']['seconds']-task['ledger']['used']['seconds']-task['ledger']['reserve']['seconds']
        if remaining <= 0:
            raise ContractError('官方资料获取预算不足，保留交付预留')
        digest = fingerprint(dict(source='official',operation=operation,parameters=params))
        charge(task['ledger'],'data_requests')
        event = dict(kind='data_requests',source='official',operation=operation,fingerprint=digest,
            batch=task['batch'],status='已派发',time=now(),attempt=1)
        task['ledger']['dispatches'].append(event)
        if self.journal:self.journal(task)
        started=time.monotonic()
        try:
            options = dict(active=active, on_stage=(lambda:self.journal(task)) if self.journal else None)
            if announcement:
                payload = official_announcement(task, params, 'catalogue' if operation=='release_catalog' else 'original',
                    started, min(30, remaining), event, issuer_raw=issuer_payload, selected=selected, **options)
            else:
                payload = official_html(task, url, started, min(30, remaining), event, **options)
            obtained=now()
            suffix = '.pdf' if announcement and operation=='release_original' else '.html'
            name = (channel+'-catalogue-'+digest[:12]+('.json' if announcement else '.html')
                    if operation=='release_catalog' else selected['id']+suffix)
            if operation == 'release_catalog' and not announcement:
                from .official_sources import catalogue_original_name
                name = catalogue_original_name(params, digest)
            download = None
            if operation == 'release_original':
                download = self._retain_original_download(task, 'material', name, payload, params, event, obtained)
            elif self.original_keeper is not None and not (announcement or miit):
                self.original_keeper(task, 'material', name, payload)
            if announcement and operation == 'release_catalog':
                self._retain_official_response(task, params, 'catalogue', payload, event)
            if active is not None:active(task)
            if operation=='release_catalog':
                record=self._official_catalogue_record(task,params,payload,issuer_payload,obtained,digest)
            else:
                extract_version = 'html-miit-1' if miit else 'html-pbc-1' if channel == 'pbc_news' else None
                parsed=self._parse_official_material(task,name,payload,
                    remaining-(time.monotonic()-started), event, extract_version=extract_version)
                record=official_material_record(task,parsed,payload,params,selected,obtained)
                if download is not None:
                    record['download_receipt'] = copy.deepcopy(download)
            event.update(status='成功',response_snapshot_hash=record.get('snapshot_hash',record['hash']))
            if announcement and operation == 'release_catalog':
                _record_current_acquisition(task, record, event)
            _record_current_original(task, record, event)
            return record
        except DuplicateJSONField:
            event.update(status='返回结构异常', failure_code='duplicate_response_field')
            raise
        except Exception:
            event['status'] = '原件已获取，研究未完成' if event.get('download_receipt') else '失败'
            raise
        finally:
            charge(task['ledger'],'seconds',max(0,time.monotonic()-started),delivery=True)
            if self.journal:self.journal(task)

    def _recover_official_catalogue(self, task, params):
        from .announcement_sources import retained_response, retained_issuer_payload
        retained=retained_response(task,params,'catalogue')
        if retained is None:return None
        from .official_sources import validate_request
        validate_request(task,'release_catalog',params)
        issuer_payload=retained_issuer_payload(task,params)
        if issuer_payload is None:return None
        active=getattr(self,'active',None)
        if active is not None:active(task)
        checkpoint=retained['checkpoint']
        digest=fingerprint(dict(source='official',operation='release_catalog',parameters=params))
        record=self._official_catalogue_record(task,params,retained['payload'],issuer_payload,
            checkpoint['obtained_at'],digest)
        record['response_recovery']=dict(checkpoint_id=checkpoint['id'],
            original_dispatch_fingerprint=checkpoint['dispatch_fingerprint'],
            recovered_batch=task['batch'],new_network_requests=0,financial_approval=False)
        return record

    def _official_catalogue_record(self, task, params, payload, issuer_payload, obtained, digest):
        from .official_sources import parse_catalogue
        snapshot=parse_catalogue(payload,task['cutoff'],channel=params['channel'],
            parameters=params,issuer_payload=issuer_payload)
        return dict(kind='api',snapshot=snapshot,hash=fingerprint(snapshot),
            id='rh_official_'+fingerprint([task['id'],task['version'],digest,fingerprint(snapshot)])[:24],
            source='official',content_version=1,task_id=task['id'],research_version=task['version'],
            operation='release_catalog',parameters=copy.deepcopy(params),request_hash=digest,
            obtained_at=obtained,status='成功待核验')

    def _parse_official_material(self, task, name, payload, timeout, event, *, extract_version=None):
        return extract_material(name, payload, extract_version=extract_version)

    def _retain_official_response(self, task, params, role, payload, event):
        # API JSON is an API response checkpoint, never a user material with
        # a renamed suffix. Save bytes before parsing or cancellation checks.
        digest = hashlib.sha256(payload).hexdigest()
        item = dict(task_id=task['id'], research_version=task['version'], channel=params['channel'],
            code=params.get('code'), role=role, sha256=digest, original_base64=base64.b64encode(payload).decode(),
            dispatch_fingerprint=event['fingerprint'])
        identity = fingerprint(item)
        event['response_sha256'] = digest
        rows = task.setdefault('official_response_checkpoints', [])
        from .announcement_sources import response_checkpoint_payload
        for row in rows:
            if not isinstance(row, dict) or row.get('id') != identity:
                continue
            try:
                response_checkpoint_payload(task, row)
                break
            except ContractError:
                continue
        else:
            rows.append(dict(item, id=identity, obtained_at=now()))
        if self.journal: self.journal(task)

    def _official_issuer(self, task, params):
        from .announcement_sources import request_issuer, retained_issuer_payload
        from .source_transport import official_announcement
        retained=retained_issuer_payload(task,params)
        if retained is not None:return retained
        active = getattr(self, 'active', None)
        if active is not None: active(task)
        remaining = task['ledger']['limits']['seconds']-task['ledger']['used']['seconds']-task['ledger']['reserve']['seconds']
        if remaining <= 0: raise ContractError('证券目录获取预算不足，保留交付预留')
        parent = fingerprint(dict(source='official', operation='release_catalog', parameters=params))
        digest = fingerprint(dict(parent_request_hash=parent, role='issuer_lookup'))
        charge(task['ledger'], 'data_requests')
        event = dict(kind='data_requests', source='official', operation='release_catalog', role='issuer_lookup',
            fingerprint=digest, parent_request_hash=parent, batch=task['batch'], status='已派发', time=now(), attempt=1)
        task['ledger']['dispatches'].append(event)
        if self.journal: self.journal(task)
        started = time.monotonic()
        try:
            payload = official_announcement(task, params, 'lookup', started, min(30, remaining), event,
                active=active, on_stage=(lambda:self.journal(task)) if self.journal else None)
            self._retain_official_response(task, params, 'issuer_lookup', payload, event)
            request_issuer(payload, params)
            event.update(status='成功', response_sha256=hashlib.sha256(payload).hexdigest())
            return payload
        except DuplicateJSONField:
            event.update(status='返回结构异常', failure_code='duplicate_response_field')
            raise
        except Exception:
            event['status'] = '失败'; raise
        finally:
            charge(task['ledger'], 'seconds', max(0, time.monotonic()-started), delivery=True)
            if self.journal: self.journal(task)

    def _official_etf(self, task, params, *, operation='etf_pcf'):
        from .etf_sources import validate_request, parse_snapshot
        from .etf_listing import validate_request as validate_etf_listing
        from .etf_trading import validate_request as validate_etf_trading
        from .source_transport import official_etf, official_etf_listing, official_etf_trading

        listing = operation == ETF_LISTING
        if listing:
            validate_etf_listing(task, params)
        elif operation != 'etf_pcf':
            validate_etf_trading(task, operation, params)
        else:
            validate_request(task, params)
        active = getattr(self, 'active', None)
        if active is not None:
            active(task)
        remaining = task['ledger']['limits']['seconds']-task['ledger']['used']['seconds']-task['ledger']['reserve']['seconds']
        if remaining <= 0:
            raise ContractError('ETF清单获取预算不足，保留交付预留')
        digest = fingerprint(dict(source='official', operation=operation, parameters=params))
        charge(task['ledger'], 'data_requests')
        event = dict(kind='data_requests', source='official', operation=operation, fingerprint=digest,
            batch=task['batch'], status='已派发', time=now(), attempt=1)
        task['ledger']['dispatches'].append(event)
        if self.journal:
            self.journal(task)
        started = time.monotonic()
        record = None
        try:
            options = dict(active=active, on_stage=(lambda: self.journal(task)) if self.journal else None)
            if listing:
                snapshot = official_etf_listing(task, params['market'], started, min(30, remaining), event, **options)
            elif operation != 'etf_pcf':
                snapshot = official_etf_trading(task, operation, params['ts_code'], started,
                    min(30, remaining), event, **options)
            else:
                payload = official_etf(task, params['ts_code'], started, min(30, remaining), event, **options)
                snapshot = parse_snapshot(payload, params['ts_code'], task['cutoff'])
            # API bytes and their hash belong to this archived API snapshot,
            # not the separate PDF/TXT/MD/DOCX/HTML user-material contract.
            if active is not None:
                active(task)
            record = dict(kind='api', source='official', operation=operation,
                parameters=copy.deepcopy(params), request_hash=digest, snapshot=snapshot,
                hash=fingerprint(snapshot), obtained_at=now(), status='成功待核验',
                content_version=1, task_id=task['id'], research_version=task['version'])
            record['id'] = 'rh_official_'+fingerprint([task['id'], task['version'], digest, record['hash']])[:24]
            event.update(status='成功', response_snapshot_hash=record['hash'])
            _record_current_acquisition(task, record, event)
            return record
        except DuplicateJSONField:
            event.update(status='返回结构异常', failure_code='duplicate_response_field')
            raise
        except Exception:
            event['status'] = '失败'
            raise
        finally:
            charge(task['ledger'], 'seconds', max(0, time.monotonic()-started), delivery=True)
            self._finish_query(task, record)

    def _official_event_history(self, task, operation, params):
        from .event_market import parse_close_history, validated_mainland_history
        from .lpr_rates import validated_snapshot as validated_lpr_history
        from .nfra_bank_statistics import validate_snapshot as validate_nfra_snapshot, validated_request as nfra_request
        from .source_transport import (official_h15_treasury_yields, official_hsi_history, official_lpr_history,
                                       official_mainland_index_history, official_nfra_bank_statistics)
        from .treasury_rates import validated_snapshot as validated_h15_history

        active = getattr(self, 'active', None)
        if active:
            active(task)
        ledger = task['ledger']
        remaining = ledger['limits']['seconds'] - ledger['used']['seconds'] - ledger['reserve']['seconds']
        if remaining <= 0:
            raise ContractError('事件窗口取数时间不足，保留交付预留')
        digest = fingerprint(dict(source='official', operation=operation, parameters=params))
        charge(ledger, 'data_requests')
        event = dict(kind='data_requests', source='official', operation=operation, fingerprint=digest,
                     batch=task['batch'], status='已派发', time=now(), attempt=1)
        ledger['dispatches'].append(event)
        if self.journal:
            self.journal(task)
        started = time.monotonic()
        record = None
        try:
            options = dict(active=active, on_stage=(lambda: self.journal(task)) if self.journal else None)
            if operation == HK_EVENT_HISTORY:
                snapshot = official_hsi_history(task, params['codes'], started, min(20, remaining), event, **options)
                rows = parse_close_history(snapshot['original_text'], **params)
                snapshot['rows'] = rows
            elif operation == MAINLAND_EVENT_HISTORY:
                snapshot = official_mainland_index_history(task, params, started, min(20, remaining), event, **options)
                validated_mainland_history(snapshot, params)
            elif operation == LPR_HISTORY:
                snapshot = official_lpr_history(task, params, started, min(20, remaining), event, **options)
                validated_lpr_history(snapshot, params)
            elif operation == NFRA_BANK_STATISTICS:
                selected = nfra_request(task, params)
                snapshot = official_nfra_bank_statistics(task, selected['url'], started, min(20, remaining), event, **options)
                validate_nfra_snapshot(task, selected, snapshot)
            else:
                snapshot = official_h15_treasury_yields(task, params, started, min(20, remaining), event, **options)
                validated_h15_history(snapshot, params)
            if active:
                active(task)
            record = dict(kind='api', source='official', operation=operation, parameters=copy.deepcopy(params),
                          task_id=task['id'], research_version=task['version'], content_version=1,
                          status='成功待核验', snapshot=snapshot, hash=fingerprint(snapshot),
                          request_hash=digest, obtained_at=now())
            record['id'] = 'rh_official_'+fingerprint([task['id'], task['version'], digest, record['hash']])[:24]
            event['status'] = '成功'
            return _record_current_acquisition(task, record, event)
        except Exception:
            event['status'] = '失败'
            raise
        finally:
            charge(ledger, 'seconds', max(0, time.monotonic()-started), delivery=True)
            self._finish_query(task, record)

    def _official_index(self,task,operation,params):
        from .index_sources import validate_official_index_request
        from .source_transport import official_index
        selected=validate_official_index_request(task,operation,params)
        active=getattr(self,'active',None)
        if active is not None:active(task)
        remaining=task['ledger']['limits']['seconds']-task['ledger']['used']['seconds']-task['ledger']['reserve']['seconds']
        if remaining<=0:raise ContractError('官方指数取数时间不足，保留交付预留')
        digest=fingerprint(dict(source='official',operation=operation,parameters=params))
        attempt=None
        if operation=='index_file':
            key=fingerprint([task['id'],task['version'],selected['url']])
            attempts=task.setdefault('index_file_attempts',{})
            if key in attempts:raise ContractError('同一官方指数文件本版本已派发，不能改目录或日期重复下载；已有原件按原日期使用')
            attempt=dict(status='已登记',url=selected['url'],batch=task['batch'])
            attempts[key]=attempt
        charge(task['ledger'],'data_requests')
        event=dict(kind='data_requests',source='official',operation=operation,fingerprint=digest,
            batch=task['batch'],status='已派发',time=now(),attempt=1)
        task['ledger']['dispatches'].append(event)
        if self.journal:self.journal(task)
        started=time.monotonic()
        record = None
        try:
            snapshot=official_index(task,selected,started,min(30,remaining),event,active=active,
                on_stage=(lambda:self.journal(task)) if self.journal else None)
            original=base64.b64decode(snapshot['original_base64'],validate=True)
            if hashlib.sha256(original).hexdigest()!=snapshot['original_sha256']:
                raise ContractError('官方指数原件字节校验失败')
            observed=snapshot.get('parsed',{}).get('observation_date')
            snapshot.update(code=0,index_code=selected['index_code'],role=selected['role'])
            if operation=='index_file':
                snapshot['catalogue_reference']={key:selected[key] for key in ('catalog_id','catalog_hash','id')}
            status='成功待核验'
            if observed and observed>market_date(task['cutoff']):status='返回日期晚于截止时点'
            elif params.get('as_of') is not None and observed!=params['as_of']:status='返回日期不符，原件保留但不采用'
            record=dict(kind='api',source='official',operation=operation,parameters=copy.deepcopy(params),
                task_id=task['id'],research_version=task['version'],content_version=1,status=status,
                snapshot=snapshot,hash=fingerprint(snapshot),request_hash=digest,obtained_at=now())
            record['id']='rh_index_'+fingerprint([task['id'],task['version'],digest,record['hash']])[:24]
            event['status']='成功'
            if attempt is not None:attempt['status']='已留存'
            _record_current_acquisition(task,record,event)
            return record
        except Exception:
            if attempt is not None:attempt['status']='失败或中断'
            event['status']='返回结构异常' if event.get('failure_code')=='duplicate_response_field' else '失败'
            raise
        finally:
            charge(task['ledger'],'seconds',max(0,time.monotonic()-started),delivery=True)
            self._finish_query(task, record)

    def _retain_original_download(self, task, kind, name, payload, parameters, event, obtained_at, directory=None):
        """Commit bytes and their send identity before invoking any parser."""
        if self.original_keeper is None:
            return None
        original_id = (self.original_keeper(task, kind, name, payload, directory) if directory is not None
                       else self.original_keeper(task, kind, name, payload))
        if (not isinstance(original_id, str) or not original_id.startswith('rh_original_')
                or not all(event.get(key) is True for key in
                           ('transport_isolated', 'network_request_started', 'network_worker_reaped'))):
            return None
        receipt = dict(version=1, task_id=task['id'], research_version=task['version'], batch=event['batch'],
            dispatch_index=task['ledger']['dispatches'].index(event), source=event['source'], operation=event['operation'],
            parameters=copy.deepcopy(parameters), request_hash=event['fingerprint'], original_id=original_id,
            original_hash=hashlib.sha256(payload).hexdigest(), byte_count=len(payload), kind=kind, name=name,
            obtained_at=obtained_at, status='已留存')
        event['download_receipt'] = receipt
        if self.journal:
            self.journal(task)
        return receipt

    def _filing_original(self, task, params, credentials=None):
        directory = validate_filing_original_request(task, params)
        private_url = None
        if directory.get("original_fetch") == "ifind_temporary_resource":
            private_url = self._ifind_original_url(task, directory, credentials)
        available = task["ledger"]["limits"]["seconds"] - task["ledger"]["used"]["seconds"] - task["ledger"]["reserve"]["seconds"]
        if available <= 0:
            raise ContractError("公告原件获取时间预算不足，保留交付预留")
        request_hash = fingerprint({"source": "ifind", "operation": "filing_original", "parameters": params})
        charge(task["ledger"], "data_requests")
        event = {"kind": "data_requests", "source": "ifind", "operation": "filing_original", "fingerprint": request_hash, "attempt": 1,
                 "batch": task["batch"], "status": "已派发", "time": now()}
        task["ledger"]["dispatches"].append(event)
        if self.journal:
            self.journal(task)
        started = time.monotonic()
        original_obtained = False
        try:
            payload = self._download_original(task, private_url or directory["original_url"], min(30, available), event,
                directory["source_id"] if private_url else None)
            original_obtained = True
            obtained_at = now()
            download = self._retain_original_download(task, "filing", "original.pdf", payload, params, event, obtained_at, directory)
            record = self._original_record(task, directory, payload, available - (time.monotonic() - started), event)
            if download is not None:
                record.update(download_receipt=copy.deepcopy(download), obtained_at=download['obtained_at'])
            status = "成功待核验" if record["stages"]["parse"] == "已解析" else "解析受限"
            record.update(operation="filing_original", parameters=copy.deepcopy(params), request_hash=request_hash, status=status)
            event["status"] = "成功"
            _record_current_original(task, record, event)
            self._checkpoint_original(task, record)
            return record
        except ContractError:
            event["status"] = "原件已获取，研究未完成" if original_obtained else "原件获取失败"
            raise
        except Exception:
            event["status"] = "原件已获取，研究未完成" if original_obtained else "原件获取失败"
            if original_obtained:
                raise ContractError("公告原件已获取，但保存或解析未完成；检查留存原件，不自动重新下载") from None
            raise ContractError("公告原件获取失败；保留目录和已派发用量，不自动重试或换源") from None
        finally:
            task["ledger"]["used"]["seconds"] += max(0, time.monotonic() - started)
            if self.journal:
                self.journal(task)

    def _original_record(self, task, directory, payload, timeout, event):
        return filing_record(task, directory, "original.pdf", payload, task.get("source_records", []))

    def _checkpoint_original(self, task, record):
        pass  # Embedded callers retain the existing single-checkpoint contract.

    def _remember_ifind_originals(self, task, record, payload):
        current = time.monotonic()
        self._ifind_original_cache = {key: item for key, item in self._ifind_original_cache.items() if item[1] > current}
        for row in record.get("catalog_entries", []):
            if row.get("original_fetch") != "ifind_temporary_resource" or not row["downloadable"]:
                continue
            source_id = row["source_id"]
            try:
                # The unredacted response is ephemeral. Read only the exact
                # URL cell retained by the validated catalogue projection.
                value = payload
                for key in row["catalog_ref"]["field_mapping"]["pdfURL"]:
                    value = value[key]
                url = _ifind_authorized_resource(value, source_id)
            except (ContractError, IndexError, KeyError, TypeError):
                continue
            self._ifind_original_cache[(task["id"], record["id"], source_id)] = (url, current + 180)
        while len(self._ifind_original_cache) > 256:
            self._ifind_original_cache.pop(next(iter(self._ifind_original_cache)))

    def _ifind_original_url(self, task, directory, credentials):
        key = (task["id"], directory["catalog_id"], directory["source_id"])
        cached = self._ifind_original_cache.pop(key, None)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        needed = 2 + self.ifind_authentication_cost(credentials)
        if task["ledger"]["limits"]["data_requests"] - task["ledger"]["used"]["data_requests"] < needed:
            raise ContractError("重新取得原件授权须保留鉴权、目录与下载的原预算，不派发无法完成的查询")
        catalog = next(record for record in task["source_records"] if record["id"] == directory["catalog_id"])
        # An expired/restarted capability needs a real, budgeted directory
        # request. It is not an automatic retry or a budget reset.
        fresh = self.external(task, "ifind", "report_query", catalog["parameters"], credentials)
        selected = next((row for row in fresh.get("catalog_entries", []) if row["source_id"] == directory["source_id"]), None)
        if not selected or selected["catalog_ref"]["original_fields"] != directory["catalog_ref"]["original_fields"]:
            raise ContractError("iFinD重新取得的目录与已选择原件不一致，不静默替换")
        if not any(record["id"] == fresh["id"] for record in task["source_records"]):
            fresh.update(question_ids=catalog.get("question_ids", []), refresh_for=catalog["id"])
            task["source_records"].append(fresh)
        if self.journal:
            self.journal(task)
        # A refresh returns capabilities for the whole directory, not only
        # the selected file. Bind matching capabilities to the retained
        # directory too, so another genuinely different original does not
        # refresh the same query again. Public fields must match exactly;
        # this neither substitutes changed metadata nor approves content.
        retained_rows = {row['source_id']: row for row in catalog.get('catalog_entries', [])}
        for row in fresh.get('catalog_entries', []):
            retained = retained_rows.get(row['source_id'])
            if not retained or retained['catalog_ref']['original_fields'] != row['catalog_ref']['original_fields']:
                continue
            capability = self._ifind_original_cache.get((task['id'], fresh['id'], row['source_id']))
            if capability and capability[1] > time.monotonic():
                self._ifind_original_cache[(task['id'], catalog['id'], row['source_id'])] = capability
        while len(self._ifind_original_cache) > 256:
            self._ifind_original_cache.pop(next(iter(self._ifind_original_cache)))
        cached = self._ifind_original_cache.pop(key, None)
        if not cached or cached[1] <= time.monotonic():
            raise ContractError("iFinD未返回可用临时原件授权，保留目录，不猜测下载地址")
        return cached[0]

    @staticmethod
    def _credential(value):
        if not isinstance(value, str) or len(value) > 16384:
            raise ContractError("iFinD凭据格式无效，请检查ResearchHelper独立配置")
        value = value.strip()
        if any(ord(char) < 32 for char in value):
            raise ContractError("iFinD凭据格式无效，请检查ResearchHelper独立配置")
        return value

    def _cached_ifind_access(self, identity):
        cache = self._ifind_access_cache
        if cache and cache[0] == identity and cache[2] > time.monotonic():
            return cache[1]
        return None

    def ifind_authentication_cost(self, credentials):
        """Count the additional authentication request for an iFinD sequence."""
        if not isinstance(credentials, dict):
            raise ContractError('数据源凭据必须来自ResearchHelper独立配置')
        self._credential(credentials.get('access_token', ''))
        refresh = self._credential(credentials.get('refresh_token', ''))
        if not refresh:
            return 0
        return int(self._cached_ifind_access(hashlib.sha256(refresh.encode()).hexdigest()) is None)

    def _resolve_ifind_access(self, task, refresh):
        """Official get_access_token obtains the current token; never force-update it.

        Transport contract: https://quantapi.10jqka.com.cn/gwstatic/static/
        ds_web/quantapi-web/example.html (HTTP interface Python example).
        """
        identity = hashlib.sha256(refresh.encode()).hexdigest()
        cached = self._cached_ifind_access(identity)
        if cached is not None:
            return cached
        self._ifind_access_cache = None
        available = task["ledger"]["limits"]["seconds"] - task["ledger"]["used"]["seconds"] - task["ledger"]["reserve"]["seconds"]
        if available <= 0:
            raise ContractError("鉴权时间预算不足，保留交付预留")
        charge(task["ledger"], "data_requests")
        event = {"kind": "data_requests", "source": "ifind", "operation": "get_access_token", "purpose": "鉴权",
                 "fingerprint": fingerprint({"source": "ifind", "operation": "get_access_token"}),
                 "attempt": 1, "batch": task["batch"], "status": "已派发", "time": now()}
        task["ledger"]["dispatches"].append(event)
        if self.journal:
            self.journal(task)
        started = time.monotonic()
        try:
            request = urllib.request.Request("https://quantapi.51ifind.com/api/v1/get_access_token",
                                             data=b"", headers={"Content-Type": "application/json"}, method="POST")
            request.add_unredirected_header("refresh_token", refresh)
            payload = self._http_json(task, request, min(30, available), event)
            if not isinstance(payload, dict) or isinstance(payload.get("errorcode"), bool) or payload.get("errorcode") not in (0, "0"):
                event["status"] = "鉴权失败"
                detail = self._ifind_error_suffix(payload, event)
                raise ContractError("iFinD鉴权未通过：get_access_token" + detail + "。检查独立Refresh Token与账号权限；不自动重试或刷新")
            data = payload.get("data")
            token = data.get("access_token") if isinstance(data, dict) else None
            try:
                token = self._credential(token)
                if not token:
                    raise ContractError("空令牌")
            except ContractError:
                event["status"] = "返回结构异常"
                raise ContractError("iFinD鉴权未返回有效Access Token，不派发数据查询") from None
            event["status"] = "成功"
            self._ifind_access_cache = (identity, token, time.monotonic() + 300)
            return token
        except urllib.error.HTTPError as error:
            error.close()
            event.update(status="鉴权HTTP未通过", http_status=error.code,
                supplier_rejection_category=IFIND_HTTP_REJECTIONS.get(error.code, "http_failure"))
            raise ContractError("iFinD鉴权未通过：get_access_token，HTTP状态" + str(error.code)
                + "。检查ResearchHelper的Refresh Token与账号权限") from None
        except ContractError:
            raise
        except Exception:
            event["status"] = "鉴权传输失败"
            raise ContractError("iFinD鉴权请求失败；已派发用量保留，不自动重试或换源") from None
        finally:
            task["ledger"]["used"]["seconds"] += max(0, time.monotonic() - started)
            if self.journal:
                self.journal(task)

    def _wait_ifind_metadata(self, task, operation, available):
        wait = metadata_wait_seconds(task, operation)
        if wait >= available:
            raise ContractError('策略等待将占用交付时间预留，未继续派发')
        end = time.monotonic()+wait
        while time.monotonic() < end:
            if callable(getattr(self, 'active', None)):
                self.active(task)
            time.sleep(min(.1, max(0, end-time.monotonic())))

    def _ifind_metadata(self, task, operation, params, token, private_values=()):
        """One authenticated, task-metered GET, no retries or vendor code."""
        available = task['ledger']['limits']['seconds']-task['ledger']['used']['seconds']-task['ledger']['reserve']['seconds']
        if available <= 0:
            raise ContractError('元数据取数时间预算不足，保留交付预留')
        wait_started = time.monotonic()
        self._wait_ifind_metadata(task, operation, available)
        waited = max(0, time.monotonic()-wait_started)
        task['ledger']['used']['seconds'] += waited
        available -= waited
        charge(task['ledger'], 'data_requests')
        event = dict(kind='data_requests', source='ifind', operation=operation, attempt=1,
            fingerprint=fingerprint(dict(source='ifind', operation=operation, parameters=params)),
            batch=task['batch'], status='已派发', time=now())
        task['ledger']['dispatches'].append(event)
        started = time.monotonic()
        record = None
        try:
            if self.journal:
                self.journal(task)
            wire = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                for key, value in params.items()}
            url = IFIND_METADATA_BASE+operation+('?' + urlencode(wire) if wire else '')
            request = urllib.request.Request(url, method='GET')
            request.add_unredirected_header('access_token', token)
            payload = self._http_json(task, request, min(30, available), event)
            event['status'] = '成功'
            record = snapshot_record(task, 'ifind', operation, params, payload, (token, *private_values))
            if record['status'] != '成功待核验':
                event['status'] = record['status']
            _record_current_acquisition(task, record, event)
            return record
        except DuplicateJSONField:
            event.update(status='返回结构异常', failure_code='duplicate_response_field')
            raise
        except ContractError:
            if event.get('failure_code') != 'duplicate_response_field':
                event['status'] = '元数据未通过'
            raise
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            event.update(status='元数据HTTP未通过', http_status=status,
                supplier_rejection_category=IFIND_HTTP_REJECTIONS.get(status, 'http_failure'))
            raise ContractError('iFinD元数据HTTP状态'+str(status)+'，保留一次用量，不重试或换源') from None
        except Exception:
            event['status'] = '元数据调用失败'
            raise ContractError('iFinD元数据未取得；保留用量，不重试、不换源、不把失败当成空目录') from None
        finally:
            task['ledger']['used']['seconds'] += max(0, time.monotonic()-started)
            self._finish_query(task, record)

    def _ifind(self, task, operation, params, token, private_values=()):
        request_hash = fingerprint({"source": "ifind", "operation": operation, "parameters": params})
        gateway_policy = (type(task.get('ifind_gateway_recovery_version')) is int
            and task['ifind_gateway_recovery_version'] == 1
            and 'ifind.' + operation in _query_request_operations() and operation != 'basic_data_service')
        previous = [event for event in task['ledger']['dispatches']
            if event.get('kind') == 'data_requests' and event.get('fingerprint') == request_hash]
        gateway_retried = any(event.get('gateway_retry_scheduled') is True for event in previous)
        retry_of = None
        attempts = 1 if operation == 'basic_data_service' else 3
        for attempt in range(attempts):
            prior_count = sum(event.get('kind') == 'data_requests' and event.get('fingerprint') == request_hash
                              for event in task['ledger']['dispatches'])
            if gateway_policy and prior_count >= 3:
                raise ContractError('同一iFinD请求已达到三次累计派发上限，保留账本')
            if retry_of is not None and callable(getattr(self, 'active', None)):
                self.active(task)
            available = task["ledger"]["limits"]["seconds"] - task["ledger"]["used"]["seconds"] - task["ledger"]["reserve"]["seconds"]
            if available <= 0:
                raise ContractError("数据请求时间预算不足，保留交付预留")
            if operation == 'basic_data_service':
                wait_started = time.monotonic()
                self._wait_ifind_metadata(task, operation, available)
                waited = max(0, time.monotonic()-wait_started)
                task['ledger']['used']['seconds'] += waited
                available -= waited
            charge(task["ledger"], "data_requests")
            event = {"kind": "data_requests", "source": "ifind", "operation": operation,
                     "fingerprint": request_hash, "attempt": prior_count + 1 if gateway_policy else attempt + 1,
                     "batch": task["batch"], "status": "已派发", "time": now()}
            if retry_of is not None:
                event['gateway_retry_of_dispatch'] = retry_of
            task["ledger"]["dispatches"].append(event)
            if self.journal:
                self.journal(task)
            started = time.monotonic()
            request = urllib.request.Request("https://quantapi.51ifind.com/api/v1/" + operation,
                data=json.dumps(params).encode(), headers={"Content-Type": "application/json"}, method="POST")
            request.add_unredirected_header("access_token", token)
            record = None
            try:
                payload = self._http_json(task, request, min(30, available), event)
                if not isinstance(payload, dict):
                    event["status"] = "返回结构异常"
                    raise ContractError("iFinD返回结构异常，不当作无数据")
                if isinstance(payload.get("errorcode"), bool) or payload.get("errorcode") not in (0, "0"):
                    event["status"] = "业务失败"
                    detail = self._ifind_error_suffix(payload, event)
                    quota = IFIND_QUOTA_ERRORS.get(str(event.get('business_code')))
                    if quota:
                        event.update(status='额度已用尽', supplier_rejection_category='quota_exhausted')
                        raise ContractError('iFinD'+quota+detail+'。')
                    if operation == 'get_thscode' and event.get('business_code') in (-6102,'-6102'):
                        event['supplier_rejection_category'] = 'ambiguous_identity'
                        raise ContractError('iFinD名称或代码对应多个证券：' + operation + detail +
                            '。需查看实际候选并明确上市市场，不代表没有数据或没有权限；本次不自动重试')
                    if event.get('business_code') in (-4210,'-4210'):
                        event['supplier_rejection_category'] = 'invalid_parameters'
                        raise ContractError('iFinD请求参数有误：' + operation + detail +
                            '。核对供应商实际返回的代码、字段与参数，不猜前导零或把参数错误当成权限不足')
                    if event.get("business_code") in (-4001, "-4001"):
                        # Official HTTP manual: -4001 means "no data". Keep
                        # this request as an unmet evidence need, not an
                        # approved empty result or proof that an event or
                        # security does not exist. Like OH's provider boundary,
                        # persist only a fixed category, never vendor errmsg.
                        event["status"] = "未返回数据"
                        event["supplier_rejection_category"] = "no_data"
                        raise ContractError("iFinD查询未返回数据：" + operation + detail +
                            "。仅表示本次请求范围未取得数据，不能据此证明标的或事件不存在；不自动重试或换源")
                    raise ContractError("iFinD业务失败：" + operation + detail + "。检查独立权限与参数；不自动重试或换源")
                event["status"] = "成功"
                record = snapshot_record(task, "ifind", operation, params, payload, (token, *private_values))
                if operation == 'basic_data_service':
                    from .financial_sources import fixed_financial_enabled
                    if fixed_financial_enabled(task):
                        record['fixed_financial_catalog_version'] = 1
                        record['fixed_financial_catalog_digest'] = fingerprint(_controlled_financial_definitions())
                if operation == 'basic_data_service' and record['status'] != '成功待核验':
                    event['status'] = record['status']
                if operation == 'date_sequence' and record['status'] == '返回结构异常':
                    event['status'] = record['status']
                _record_current_acquisition(task, record, event)
                if operation == "report_query":
                    self._remember_ifind_originals(task, record, payload)
                return record
            except ContractError:
                raise
            except urllib.error.HTTPError as error:
                event["http_status"] = error.code
                error.close()
                event["status"] = "HTTP请求未通过" if error.code in (401, 403) else "HTTP调用失败"
                if error.code in IFIND_HTTP_REJECTIONS:
                    event["supplier_rejection_category"] = IFIND_HTTP_REJECTIONS[error.code]
                if error.code == 401:
                    raise ContractError("iFinD认证未通过：" + operation + "，HTTP状态401。检查ResearchHelper的iFinD令牌") from None
                # Only a known gateway/service failure of a read-only business
                # query, after the isolated worker is reaped. No auth refresh,
                # 429/504, unknown response, URL change or unmetered retry.
                if gateway_policy and type(error.code) is int and error.code in (502, 503):
                    event['supplier_rejection_category'] = 'upstream_unavailable'
                    requests_left = task['ledger']['limits']['data_requests'] - task['ledger']['used']['data_requests']
                    seconds_left = (available - max(0, time.monotonic() - started))
                    if (not gateway_retried and attempt < 2 and prior_count + 1 < 3
                            and requests_left >= 1 and seconds_left > .25
                            and event.get('network_request_started') is True
                            and event.get('network_worker_reaped') is True):
                        gateway_retried = True
                        event['gateway_retry_scheduled'] = True
                        retry_of = len(task['ledger']['dispatches']) - 1
                        time.sleep(.25)
                        continue
                    raise ContractError('iFinD服务暂不可用：' + operation + '，HTTP状态' + str(error.code)
                        + '。保留失败记录与用量；未换源或刷新凭据') from None
                raise ContractError("iFinD请求未通过：" + operation + "，HTTP状态" + str(error.code) + "。检查独立权限与参数；不自动重试或换源") from None
            except Exception:
                event["status"] = "传输失败"
                if gateway_policy:
                    raise ContractError('iFinD传输结果未确认，保留账本，不自动重发或换源') from None
                if operation == 'basic_data_service':
                    raise ContractError('基础财务传输未确认，保留一次用量，不重试或换源') from None
                if attempt == 2:
                    raise ContractError("iFinD传输失败，已使用三次请求；保留账本") from None
                time.sleep(.2 * (attempt + 1))
            finally:
                task["ledger"]["used"]["seconds"] += max(0, time.monotonic() - started)
                self._finish_query(task, record)

    def _tinyshare(self, task, operation, params, token):
        # Import only after the offline guard and exact operation/field checks.
        # No tushare import, global set_token, SDK login or silent fallback.
        provider = self._require_tinyshare()
        available = task["ledger"]["limits"]["seconds"] - task["ledger"]["used"]["seconds"] - task["ledger"]["reserve"]["seconds"]
        if available <= 0:
            raise ContractError("数据请求时间预算不足，保留交付预留")
        charge(task["ledger"], "data_requests")
        event = {"kind": "data_requests", "fingerprint": fingerprint({"source": "tinyshare", "operation": operation, "parameters": params}),
                 "attempt": 1, "batch": task["batch"], "status": "已派发", "time": now()}
        task["ledger"]["dispatches"].append(event)
        self._tinyshare_checkpoint(task, event)
        started = time.monotonic()
        record = None
        try:
            rows = self._tinyshare_rows(task, operation, params, token, min(30, available), event, provider)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ContractError("Tinyshare记录结构异常，不当作无数据")
            # Missing cells remain missing; never fill price/volume with zero.
            for row in rows:
                for key, value in row.items():
                    if isinstance(value, float) and not math.isfinite(value):
                        row[key] = None
            payload = business_payload({"code": 0, "data": rows}, (token,))
            event["status"] = "成功" if rows else "成功但为空"
            record = snapshot_record(task, "tinyshare", operation, params, payload)
            _record_current_acquisition(task, record, event)
            if "tinyshare." + operation in FINANCIAL_REQUEST_REGISTRY and record["status"] == "返回结构异常":
                event.update(status="返回结构异常", failure_code="financial_response_mismatch")
            return record
        except ContractError:
            if not event.get("failure_code"):
                event["status"] = "返回结构异常"
            raise
        except Exception as error:
            event["status"] = "调用失败待核查"
            # Provider errors are classified, not copied: they may contain credentials.
            message = str(error).lower()
            if "过期" in message or "expired" in message:
                event.update(status="授权已过期", failure_code="credential_expired")
                raise ContractError("Tinyshare授权码已过期，请更新ResearchHelper独立配置；已派发用量保留，不自动换源") from None
            if type(error).__name__ == "TinySharePermissionError":
                event.update(status="权限未通过", failure_code="permission_denied")
                raise ContractError("Tinyshare授权或接口权限未通过，请核对独立配置；已派发用量保留，不自动换源") from None
            raise ContractError("Tinyshare调用失败；保留账本，不猜测权限、不自动重试或换源") from None
        finally:
            task["ledger"]["used"]["seconds"] += max(0, time.monotonic() - started)
            self._tinyshare_checkpoint(task, event, record)
