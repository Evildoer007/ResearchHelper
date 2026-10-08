"""A concept hit or valid ticker must never become verified theme exposure."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from core.brief import Brief, TargetRef
from core.evidence_discovery import SearchHit
from core.market_confirmation import Confirmation, proposal, verify
from core.provider import FetchResult
from core.theme_company_evidence import (
    assess_company_hits, extract_company_leads, plan_theme_company_search,
)


class Provider:
    name = "fake"

    def available(self):
        return True

    def get_basic(self, codes, indicators, params=""):
        name = {"000338.SZ": "潍柴动力"}.get(codes[0], "")
        return FetchResult(bool(name), self.name, data={codes[0]: {
            key: name if key == "ths_stock_short_name_stock" else "" for key in indicators
        }})


def _hit(url: str, text: str, title: str = "潍柴动力公告") -> SearchHit:
    return SearchHit(title=title, url=url, raw_content=text)


def test_official_business_passage_is_source_linked_but_not_materiality_claim():
    found = assess_company_hits("人形机器人", "潍柴动力", [
        _hit("https://static.cninfo.com.cn/finalpage/a.pdf",
             "公司研发人形机器人关节产品，相关产品目前尚未贡献收入。")
    ])
    assert found["association"] == "direct"
    assert found["source_url"].startswith("https://static.cninfo.com.cn/")
    assert "研发人形机器人" in found["reason"]


def test_media_concept_and_negative_disclosure_never_become_verified():
    found = assess_company_hits("人形机器人", "潍柴动力", [
        _hit("https://example.com/finance", "人形机器人概念股包括潍柴动力，主营业务有进展。"),
        _hit("https://static.cninfo.com.cn/finalpage/a.pdf",
             "公司没有人形机器人业务和产品。"),
    ])
    assert found["association"] == "concept_only"
    assert found["source_url"] == "https://example.com/finance"


def test_unrelated_company_or_no_verbatim_excerpt_stays_unverified():
    found = assess_company_hits("人形机器人", "潍柴动力", [
        _hit("https://static.cninfo.com.cn/a.pdf", "另一家公司研发人形机器人产品。", "其他公司公告")
    ])
    assert found["association"] == "unverified"


def test_official_search_snippet_without_page_text_is_not_primary_proof():
    hit = SearchHit(title="潍柴动力公告", url="https://static.cninfo.com.cn/a.pdf",
                    summary="潍柴动力研发人形机器人关节产品。")
    assert assess_company_hits("人形机器人", "潍柴动力", [hit])["association"] == "unverified"


def test_industry_background_in_company_filing_is_not_its_business_proof():
    hit = _hit("https://static.cninfo.com.cn/a.pdf",
               "人形机器人行业产品需求增长较快，产业链上下游持续扩张。")
    assert assess_company_hits("人形机器人", "潍柴动力", [hit])["association"] == "unverified"


class FakeLlm:
    def __init__(self, data):
        self.data = data

    def available(self):
        return True

    def chat_json(self, *_args, **_kwargs):
        return type("Result", (), {"ok": True, "data": self.data, "error": ""})()


def test_llm_plans_industry_segments_without_naming_companies():
    plans, warnings = plan_theme_company_search("人形机器人", FakeLlm({"segments": [
        {"segment": "行星滚柱丝杠", "keywords": ["丝杠"], "query": "行星滚柱丝杠 产品"},
        {"segment": "执行器", "keywords": ["执行器"], "query": "执行器 A股 年报"},
    ]}))
    assert not warnings
    assert plans[0]["segment"] == "行星滚柱丝杠"
    assert "A股" in plans[0]["query"]
    assert "年报" in plans[1]["query"]


def test_llm_company_extraction_requires_name_and_verbatim_quote_in_source():
    materials = [{
        "segment": "执行器", "title": "三花智控公告", "url": "https://example.com/a",
        "text": "三花智控已开展人形机器人执行器产品研发，相关项目处于客户验证阶段。",
    }]
    leads, warnings = extract_company_leads("人形机器人", materials, FakeLlm({"companies": [
        {"company_name": "三花智控", "code": "002050.SZ", "segment": "执行器",
         "source_id": "S1", "evidence_quote": "三花智控已开展人形机器人执行器产品研发，相关项目处于客户验证阶段。"},
        {"company_name": "无关公司", "code": "000001.SZ", "segment": "执行器",
         "source_id": "S1", "evidence_quote": "无关公司从事机器人业务。"},
    ]}))
    assert len(leads) == 1
    assert leads[0]["name"] == "三花智控"
    # Code is removed because it was not present in the supplied source. The
    # caller must resolve it through iFinD rather than trust model memory.
    assert leads[0]["code"] == ""
    assert any("逐字原文校验" in item for item in warnings)


def test_llm_retrieval_precedes_parser_and_ifind_concept_fallback():
    brief = Brief(原始需求="人形机器人", 主题="人形机器人", 市场范围="A股",
                  涉及板块=["自动化设备"], ok=True)
    brief.候选标的 = [TargetRef("潍柴动力", "000338.SZ", "ok:潍柴动力")]
    llm = {"code": "002050.SZ", "name": "三花智控", "origin": "LLM 产业链检索",
           "association": "direct", "reason": "公司披露人形机器人执行器业务",
           "source_title": "公告", "source_url": "https://example.com/a"}
    concept = {"code": "000333.SZ", "name": "美的集团", "origin": "iFinD 动态主题发现",
               "association": "concept_only", "reason": "概念命中", "source_title": "问财", "source_url": ""}
    with patch("core.market_confirmation.discover_llm_theme_companies", return_value=[llm]), \
         patch("core.market_confirmation.discover_theme_companies", return_value=[concept]), \
         patch("core.market_confirmation._suggestions", return_value=[]), \
         patch("core.universe.validate_industries", return_value=([], ["自动化设备"])):
        payload = proposal(brief, provider=Provider())
    candidates = payload["theme_basket_candidates"]
    assert [item["code"] for item in candidates[:3]] == ["002050.SZ", "000333.SZ", "000338.SZ"]
    assert candidates[0]["origin"] == "LLM 产业链检索"


def test_parser_suggestion_is_not_core_and_etf_is_preferred_to_unverified_basket():
    brief = Brief(原始需求="未来一个月人形机器人投资机会", 主题="人形机器人投资机会",
                  市场范围="A股", 涉及板块=["自动化设备"], ok=True)
    brief.候选标的 = [TargetRef("潍柴动力", "000338.SZ", "ok:潍柴动力")]
    provider = Provider()
    with patch("core.market_confirmation.discover_theme_companies", return_value=[]), \
         patch("core.market_confirmation._suggestions", return_value=[{"code": "562500.SH", "name": "机器人ETF"}]), \
         patch("core.universe.validate_industries", return_value=([], ["自动化设备"])):
        payload = proposal(brief, provider=provider)
    candidate = payload["theme_basket_candidates"][0]
    assert candidate["code"] == "000338.SZ"
    assert candidate["core"] is False
    assert candidate["association"] == "unverified"
    assert payload["recommended_research_mode"] == "theme_etf"


def test_unverified_company_needs_analyst_source_before_basket_fetch():
    brief = Brief(原始需求="人形机器人", 主题="人形机器人", 市场范围="A股",
                  涉及板块=["自动化设备"], ok=True)
    brief.主题篮子候选池 = [TargetRef("潍柴动力", "000338.SZ", "ok:潍柴动力")]
    brief.主题篮子候选依据 = {"000338.SZ": {"association": "concept_only"}}
    base = dict(market="A股", research_scope="自动化设备", research_theme="人形机器人",
                research_mode="theme_basket", theme_basket_codes=["000338.SZ"])
    with patch("core.universe.validate_industries", return_value=(["自动化设备"], [])):
        missing = verify(Confirmation(**base), brief, provider=Provider())
        sourced = verify(Confirmation(**base, theme_basket_evidence={"000338.SZ": {
            "association": "indirect", "reason": "公司披露零部件配套业务", "source": "年报第12页"
        }}), brief, provider=Provider())
    assert not missing.ok
    assert any("主题关联理由" in error for error in missing.errors)
    assert sourced.ok, sourced.errors


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from gui.app import MarketConfirmationDialog  # noqa: E402


def test_dialog_does_not_preselect_system_concept_candidate(monkeypatch):
    app = QApplication.instance() or QApplication([])
    payload = {
        "topic": "人形机器人投资机会", "proposed_theme": "人形机器人",
        "verified_scope_options": ["自动化设备"], "theme_etf_route": True,
        "theme_etf_scope": "人形机器人", "recommended_research_mode": "theme_etf",
        "theme_basket_candidates": [{
            "code": "000338.SZ", "name": "潍柴动力", "origin": "需求解析建议（待核实）",
            "association": "unverified", "reason": "尚无关联原文", "source_title": "", "core": False,
        }],
    }
    dialog = MarketConfirmationDialog(payload)
    assert dialog.theme_basket_codes[0].checkState() == Qt.CheckState.Unchecked
    assert "主题关联待核实" in dialog.theme_basket_codes[0].text()
    assert dialog._scope_value()[1] == "theme_etf"
    from gui import app as app_module
    monkeypatch.setattr(app_module.ThemeAssociationDialog, "exec",
                        lambda _self: app_module.QDialog.DialogCode.Accepted)
    monkeypatch.setattr(app_module.ThemeAssociationDialog, "value",
                        lambda _self: {"association": "indirect", "reason": "产品配套原文", "source": "年报第12页"})
    dialog.scope.setCurrentIndex(1)
    dialog.theme_basket_codes[0].setCheckState(Qt.CheckState.Checked)
    assert dialog.value()["theme_basket_evidence"]["000338.SZ"]["source"] == "年报第12页"
    dialog.close()
