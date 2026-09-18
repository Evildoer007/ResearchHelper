"""主题检索与官方暴露的离线回归；不联网，不使用真实客户或行情数据。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core import instruments, market_confirmation as mc
from core.brief import Brief
from core.provider import FetchResult, iFinDProvider


class DiscoveryProvider(iFinDProvider):
    def __init__(self, names):
        self.names = names
        self.total_data_vol = 0

    def available(self):
        return True

    def _ensure_login(self):
        pass

    def get_basic(self, codes, indicators, params=""):
        data = {code: {key: self.names.get(code, "") if key == "ths_stock_short_name_stock" else ""
                       for key in indicators} for code in codes}
        return FetchResult(True, "offline", data=data)


def make_brief(topic, sectors=None):
    raw = f"未来一个月{topic}行业的投资机会"
    return Brief(原始需求=raw, 主题=raw, 市场范围="A股", 涉及板块=sectors or [], ok=True)


def discover(b, names, amounts=None, searched_names=None):
    response = {"errorcode": 0, "tables": [{"table": {
        "基金代码": list(names), "基金简称": searched_names or list(names.values()),
    }}]}
    ths = SimpleNamespace(THS_iwencai=lambda *args: response)
    with patch.dict("sys.modules", {"iFinDPy": ths}), patch("core.history.series", side_effect=(
        lambda code, *args, **kwargs: (amounts or {}).get(code, [3e7] * 20)
    )):
        return mc.discover_etfs(b, provider=DiscoveryProvider(names))


class ETFDiscoveryScopeTests(unittest.TestCase):
    def test_dynamic_discovery_uses_short_topic_for_multiple_themes(self):
        for topic, name in (
            ("机器人", "机器人产业ETF"), ("消费电子", "消费电子ETF"),
            ("汽车电子", "智能驾驶ETF"), ("光模块", "光通信ETF"),
            ("锂电池与固态电池", "新能源电池ETF"), ("低空经济", "低空经济ETF"),
        ):
            with self.subTest(topic=topic):
                b = make_brief(topic, ["机械设备"])
                b.ETF检索词 = [topic]
                rows = discover(b, {"159530.SZ": name, "512800.SH": "银行ETF"})
                self.assertEqual([row["code"] for row in rows], ["159530.SZ"])
                self.assertEqual(rows[0]["exposure"]["scope"], topic)
                self.assertEqual(rows[0]["exposure_level"], "direct")
                self.assertNotIn(b.原始需求, mc._discovery_terms(b))

    def test_legacy_full_question_is_normalized(self):
        item = instruments.get("562500.SH")
        short = mc.assess_exposure("机器人", item, official_name=item.官方名)
        full = mc.assess_exposure("未来一个月机器人行业的投资机会", item, official_name=item.官方名)
        self.assertEqual(full.level, short.level)
        self.assertEqual(full.scope, "机器人")

    def test_specific_theme_and_parser_topic_are_not_broadened(self):
        b = make_brief("消费电子")
        b.研究主题 = "消费"
        self.assertEqual(mc._proposed_theme(b), "消费电子")
        b = make_brief("机器人减速器")
        b.研究主题 = "机器人减速器"
        self.assertEqual(mc._proposed_theme(b), "机器人减速器")
        item = instruments.get("562500.SH")
        self.assertEqual(mc.assess_exposure("人形机器人", item, official_name=item.官方名).level, "partial")

    def test_specific_topic_search_is_first_and_combined_topics_are_split(self):
        b = make_brief("人形机器人")
        self.assertEqual(mc._discovery_terms(b)[0], "人形机器人")
        self.assertIn("机器人", mc._discovery_terms(b))
        b = make_brief("锂电池与固态电池")
        self.assertIn("锂电池", mc._discovery_terms(b))
        self.assertIn("固态电池", mc._discovery_terms(b))

    def test_confirmed_specific_theme_is_not_upgraded_by_broad_original_question(self):
        b = make_brief("机器人")
        value = mc.Confirmation("A股", "人形机器人", "562500.SH", research_theme="人形机器人",
                                research_mode="theme_etf")
        with patch("core.history.series", return_value=[3e7] * 20), \
                patch.object(mc, "_official_major_constituents", return_value=[]):
            result = mc.verify(value, b, provider=DiscoveryProvider({"562500.SH": "华夏中证机器人ETF"}))
        self.assertFalse(result.ok)
        self.assertEqual(result.exposure.level, "partial")
        self.assertTrue(any("部分暴露" in message for message in result.errors))

    def test_final_submission_keeps_hard_liquidity_floor(self):
        b = make_brief("机器人")
        value = mc.Confirmation("A股", "机器人", "562500.SH", research_theme="机器人",
                                research_mode="theme_etf")
        with patch("core.history.series", return_value=[3e6] * 20), \
                patch.object(mc, "_official_major_constituents", return_value=[]):
            result = mc.verify(value, b, provider=DiscoveryProvider({"562500.SH": "华夏中证机器人ETF"}))
        self.assertFalse(result.ok)
        self.assertTrue(any("0.1" in message and "最低门槛" in message for message in result.errors))

    def test_robot_catalog_is_available_when_dynamic_search_fails(self):
        b = make_brief("机器人", ["机械设备"])
        with patch.object(mc, "discover_etfs", return_value=[]):
            rows = mc._suggestions(b, provider=DiscoveryProvider({}))
        self.assertTrue({"562500.SH", "159770.SZ"}.issubset({row["code"] for row in rows}))
        self.assertNotIn("512800.SH", {row["code"] for row in rows})

    def test_merge_preserves_dynamic_official_facts_and_both_origins(self):
        b = make_brief("机器人")
        dynamic = {"code": "562500.SH", "name": "华夏中证机器人ETF", "origin": "动态发现",
                   "tracking_index": "test-index", "note": "官方事实及流动性预检通过"}
        with patch.object(mc, "discover_etfs", return_value=[dynamic]):
            rows = mc._suggestions(b, provider=DiscoveryProvider({}))
        merged = [row for row in rows if row["code"] == "562500.SH"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["name"], dynamic["name"])
        self.assertEqual(merged[0]["tracking_index"], "test-index")
        self.assertEqual(merged[0]["origins"], ["常用池", "动态发现"])

    def test_rejection_audit_distinguishes_exposure_and_liquidity(self):
        b = make_brief("机器人")
        rows = discover(b, {"159530.SZ": "机器人ETF", "562500.SH": "机器人ETF",
                            "159770.SZ": "机器人ETF", "512800.SH": "银行ETF"},
                        {"159530.SZ": [3e6] * 20, "562500.SH": [3e7] * 3},
                        searched_names=["机器人ETF"] * 4)
        self.assertEqual([row["code"] for row in rows], ["159770.SZ"])
        rejected = {row["code"]: row for row in b._etf_discovery_audit if row["status"] == "rejected"}
        self.assertEqual(rejected["512800.SH"]["stage"], "exposure")
        self.assertIn("低于0.1亿元", rejected["159530.SZ"]["reason"])
        self.assertIn("样本不足", rejected["562500.SH"]["reason"])

    def test_empty_candidates_show_service_gap_even_without_product_word(self):
        b = make_brief("低空经济")
        with patch("core.universe.validate_industries", return_value=([], [])), \
                patch.object(mc, "_theme_basket_candidates", return_value=[]):
            payload = mc.proposal(b, provider=SimpleNamespace(available=lambda: False))
        self.assertEqual(payload["suggested_instruments"], [])
        self.assertIn("动态检索不可用", payload["discovery_notice"])
        self.assertTrue(payload["etf_discovery_audit"])
        self.assertEqual(payload["etf_search_terms"], ["低空经济"])

    def test_empty_theme_does_not_search_entire_etf_market(self):
        b = Brief(原始需求="", 主题="", ok=True)
        with patch.dict("sys.modules", {"iFinDPy": SimpleNamespace(THS_iwencai=lambda *args: self.fail("不得全市场搜索"))}):
            self.assertEqual(mc.discover_etfs(b, provider=DiscoveryProvider({})), [])
        self.assertEqual(mc._discovery_terms(b), ())
        self.assertEqual(b._etf_discovery_audit[0]["stage"], "theme")

    def test_search_drops_unrelated_parser_terms_and_keeps_controlled_aliases(self):
        b = make_brief("消费电子")
        b.ETF检索词 = ["消费", "无关主题", b.原始需求, "562500.SH", "智能终端"]
        terms = mc._discovery_terms(b)
        self.assertIn("消费电子", terms)
        self.assertIn("智能终端", terms)
        self.assertNotIn("消费", terms)
        self.assertNotIn("无关主题", terms)
        self.assertNotIn(b.原始需求, terms)
