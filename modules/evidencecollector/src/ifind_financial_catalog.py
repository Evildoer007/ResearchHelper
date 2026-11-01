"""Reviewed iFinD metadata, reusable without borrowing another task's authority.

This module contains no business values, account policy, credentials or I/O.
The retained diagnostic identifiers are provenance only, never runtime files.
Dates, securities, account limits and financial evidence remain task-local checks.
"""

from copy import deepcopy
from hashlib import sha256
import json


CATALOGUE_VERSION = 1
_REPORT_TYPES = {
    "合并报表": "1", "母公司报表": "2",
    "合并报表(调整)": "3", "母公司报表(调整)": "4",
}
_PARAMETERS = [
    {
        "name": "D", "describe": "报告期", "type": "enum_or_input",
        "choice": {}, "format": "YYYYMMDD",
        "validation": {
            "explicit_quarter_end_required": True,
            "not_after_task_cutoff": True, "not_after_current_date": True,
            "validated_by": "upstream_request_validator",
        },
    },
    {"name": "C", "describe": "报表类型", "type": "enum", "choice": _REPORT_TYPES},
]
_PROVENANCE_ROOT = "result/acceptance/ifind-financial-metadata-20261023/"
_INDICATOR_DIGEST = "63156949cf9740ce83d078c6e0d7c22b6c8db190bc64193dc4a43618118042b3"
_PARAMETER_DIGEST = "5d7f0d20c1647a81db26c983817c1ec5f5c59743b501bb658d67a7704fb495ff"


def _reviewed_definition(indicator, name, metric, parameter_file, *,
                         provenance_root=_PROVENANCE_ROOT,
                         indicator_provenance_root=None, reviewed_on="2026-10-23",
                         indicator_file="3-get_indicators.json",
                         indicator_digest=_INDICATOR_DIGEST,
                         parameter_digest=_PARAMETER_DIGEST,
                         catalogue_path="//股票全部指标/财务报表(新准则)/保险公司/利润表"):
    return {
        "indicator": indicator, "name": name, "meaning": name,
        "metric": metric, "version": 1, "market": "A股",
        "function_mode": "基础函数", "operation": "basic_data_service",
        "metadata_status": "reviewed", "parameter_reuse_allowed": True,
        "parameter_template": deepcopy(_PARAMETERS),
        "supported_report_types": deepcopy(_REPORT_TYPES),
        "data_attributes": {key: "unverified" for key in (
            "unit", "currency", "accounting_standard", "consolidation_scope",
            "security_identity", "historical_availability", "financial_values",
            "industry_applicability",
        )},
        "financial_values_approved": False,
        "source_proof": {
            "reviewed_on": reviewed_on,
            "documentation": "https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/ai-skill.html",
            "indicator_response": {
                "artifact": (indicator_provenance_root or provenance_root) + indicator_file,
                "sha256": indicator_digest,
            },
            "parameter_response": {
                "artifact": provenance_root + parameter_file,
                "sha256": parameter_digest,
            },
            "observed_catalogue_path": [catalogue_path],
            "scope": "字段名称及有序参数已经核对；目录路径不证明全行业适用性。",
        },
    }


_CATALOGUE = {
    "version": CATALOGUE_VERSION,
    "source": "ifind",
    "trust_root": "reviewed_builtin_python_definitions",
    "authorization_granted": False,
    "financial_values_approved": False,
    "limitations": "仅复用字段与参数定义。主体范围、账号权限、限额及金额适用性须由当前任务独立验证。归母净利润不得替代报表净利润。",
    "definitions": {
        "ths_revenue_stock": _reviewed_definition(
            "ths_revenue_stock", "营业收入", "revenue", "4-get_indicator_params.json"),
        "ths_np_atoopc_stock": _reviewed_definition(
            "ths_np_atoopc_stock", "归属于母公司所有者的净利润",
            "net_profit_attributable_to_parent", "5-get_indicator_params.json"),
    },
}

_SUPPLEMENT_PROVENANCE = {
    "provenance_root": "result/acceptance/fixed-financial-definitions-20261023-v2/",
    "indicator_file": "2-get_indicators.json",
    "indicator_digest": "97fd83fb38c3938f8a5e4ee7ab4bb6bace3e2662d495b4158c42569704651ee0",
    "parameter_digest": "5d7f0d20c1647a81db26c983817c1ec5f5c59743b501bb658d67a7704fb495ff",
}
_CATALOGUE["definitions"].update({
    "ths_operating_cost_stock": _reviewed_definition(
        "ths_operating_cost_stock", "营业成本", "operating_cost", "3-get_indicator_params.json",
        catalogue_path="//股票全部指标/财务报表(新准则)/一般企业/利润表",
        **_SUPPLEMENT_PROVENANCE),
    "ths_np_stock": _reviewed_definition(
        "ths_np_stock", "净利润", "net_profit", "4-get_indicator_params.json",
        catalogue_path="//股票全部指标/财务报表(新准则)/一般企业/利润表",
        **_SUPPLEMENT_PROVENANCE),
    "ths_ncf_from_oa_stock": _reviewed_definition(
        "ths_ncf_from_oa_stock", "经营活动产生的现金流量净额", "operating_cash_flow",
        "5-get_indicator_params.json",
        catalogue_path="//股票全部指标/财务报表(新准则)/一般企业/现金流量表/经营活动产生的现金流量",
        **_SUPPLEMENT_PROVENANCE),
})

# Official directory and D/C definitions were acquired independently. Neither
# response establishes currency, unit, historical availability or business data.
_BALANCE_PROVENANCE = {
    "provenance_root": "result/acceptance/working-capital-parameters-live-b5eqnh4c/",
    "indicator_provenance_root": "result/acceptance/working-capital-fields-live-cxxudagw/",
    "indicator_file": "2-get_indicators.json",
    "indicator_digest": "8a58efa4f2d91bb83a69b356fe16bd61ab97a19fb3df0954a607a5f87981be67",
    "parameter_digest": "5d7f0d20c1647a81db26c983817c1ec5f5c59743b501bb658d67a7704fb495ff",
    "reviewed_on": "2026-10-27",
}
for _indicator, _name, _metric, _file, _section in (
    ("ths_account_receivable_stock", "应收账款", "accounts_receivable", "1-get_indicator_params.json", "流动资产"),
    ("ths_inventory_stock", "存货", "inventory", "2-get_indicator_params.json", "流动资产"),
    ("ths_accounts_payable_stock", "应付账款", "accounts_payable", "3-get_indicator_params.json", "流动负债"),
):
    _CATALOGUE["definitions"][_indicator] = dict(_reviewed_definition(
        _indicator, _name, _metric, _file,
        catalogue_path="//股票全部指标/财务报表(新准则)/一般企业/资产负债表/" + _section,
        **_BALANCE_PROVENANCE), period_kind="point_in_time")


def _fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(encoded).hexdigest()


def _calibrated_definition(indicator, page, line, statement_page, statement_name):
    claims = {
        "indicator": indicator, "unit": "元", "currency": "CNY",
        "accounting_standard": "CAS", "metric": _CATALOGUE["definitions"][indicator]["metric"],
        "statement_scope": "consolidated", "report_type": "1",
        "parameter_order": ["D", "C"], "period_kind": "cumulative",
        "applicable_codes": ["600519.SH"], "applicable_periods": ["20260630"],
    }
    payload = {
        "claims": deepcopy(claims),
        "original_title": "贵州茅台：贵州茅台2026年半年度报告",
        "vendor_document_id": "5246648228",
        "original_obtained_at": "2026-10-19T19:01:56.896606+00:00",
        "original_pdf_sha256": "0e10aa26be46b1cf3cd03f06e834c7fb98d5dd0d661b96f8fddd4af7e846a4f6",
        "original_snapshot_hash": "f0dba159590ec53469e17cd57f0798e9c3a9edd6edd58a1694e526287a6f1426",
        "calibration_artifact": "result/acceptance/fixed-financial-calibration-20261023-v1/receipt.json",
        "calibration_sha256": "2b03880729c4a8eab9c0a7ecbad771caa466413b3ab8e2c36fda37ca41aa94fd",
        "field_locator": {"pdf_page": page, "line": line, "report_column": "2026年半年度"},
        "excerpts": [
            {"pdf_page": statement_page, "text": statement_name + "\n2026 年 1—6月\n单位：元 币种：人民币"},
            {"pdf_page": 41, "text": "本公司所编制的财务报表符合企业会计准则的要求"},
            {"pdf_page": 42, "text": "本公司合并财务报表按照《企业会计准则第 33 号－合并财务报表》及相关规定的要求编\n制"},
        ],
        "scope": "仅适用于该主体、期间、C=1及指定字段；新任务必须重新取得业务数值。",
    }
    basis = {
        "kind": "vendor_document",
        "source": "https://ft.10jqka.com.cn/standardgwapi/api/news_service/pubnote/download/resource",
        "locator": "供应商文档5246648228；PDF第" + str(page) + "页第" + str(line)
                   + "行；表头第" + str(statement_page) + "页；准则第41—42页",
        # Calibration became available at review time, not at the earlier PDF
        # acquisition time. This prevents backdating its authority to old calls.
        "obtained_at": "2026-10-23T13:34:30+00:00",
        "payload": payload, "hash": _fingerprint(payload),
    }
    return {"claims": claims, "basis": basis}


_EVIDENCE_DEFINITIONS = {
    "ths_revenue_stock": _calibrated_definition("ths_revenue_stock", 30, 16, 30, "合并利润表"),
    "ths_operating_cost_stock": _calibrated_definition("ths_operating_cost_stock", 30, 21, 30, "合并利润表"),
    "ths_np_stock": _calibrated_definition("ths_np_stock", 31, 16, 30, "合并利润表"),
    "ths_ncf_from_oa_stock": _calibrated_definition("ths_ncf_from_oa_stock", 34, 15, 33, "合并现金流量表"),
}


# Pin the reviewed content, including provenance and safety boundaries. Updating
# a definition requires reviewing its evidence and updating this digest together.
_EXPECTED_DIGEST = "e909888ea6209fcd86e38573b795be644eb6358cfd6e34f02bf795201a4812bf"


def _content_digest():
    return _fingerprint({"catalogue": _CATALOGUE, "evidence_definitions": _EVIDENCE_DEFINITIONS})


def catalogue_digest():
    """Return the canonical SHA256 or fail closed on changed builtin content.

    This checks content consistency, not authenticity against hostile code edits.
    Deployed package integrity remains the responsibility of release verification.
    """
    digest = _content_digest()
    if digest != _EXPECTED_DIGEST:
        raise ValueError("iFinD固定字段目录完整性校验失败")
    return digest


def catalogue():
    """Return an independent metadata snapshot, never an acquisition permit."""
    catalogue_digest()
    return deepcopy(_CATALOGUE)


def definition(indicator):
    """Return reviewed metadata or None; unknown fields require discovery."""
    catalogue_digest()
    if not isinstance(indicator, str):
        return None
    return deepcopy(_CATALOGUE["definitions"].get(indicator))


def parameter_template(indicator):
    """Return D/C in vendor order without relative dates or default values."""
    row = definition(indicator)
    return row["parameter_template"] if row is not None else None


def evidence_definitions():
    """Return scoped document-backed metadata, never retained business amounts.

    The consumer must enforce applicable codes, periods and C before using a
    definition. The generic catalogue remains unverified for units.
    """
    catalogue_digest()
    return deepcopy(_EVIDENCE_DEFINITIONS)
