import json

from core.report_edits import (
    apply_edits, apply_saved_revision, chart_sections, editable_fields, ensure_edit_markers, load_revision,
    logic_sections, save_revision,
)
from core.report_edits import edit_region, section_region


LEGACY = """<!doctype html><html><body><div class="page">
<h1>场外衍生品投资策略 <span class="accent">—— 原标题</span></h1>
<div class="concl"><span class="lbl">核心结论</span>原结论</div>
<div class="logic"><span class="tag">策略逻辑一</span><span class="ltitle">标题一</span>
<div class="body">正文一<b>重点</b></div><div class="chart"><img src="x"></div></div>
<div class="logic"><span class="tag">策略逻辑二</span><span class="ltitle">标题二</span>
<div class="body">正文二</div></div>
<div class="under"><div class="u-why"><b>与研究主题的关联及选取原因</b>：原说明</div></div>
<section class="quote"><table><tr><td>8.8%</td></tr></table></section>
<div class="foot">数据来源：iFinD</div></div></body></html>"""


def test_legacy_report_gets_editable_fields_and_logic_sections():
    marked = ensure_edit_markers(LEGACY)
    fields = editable_fields(marked)
    assert fields["report_title"] == "原标题"
    assert fields["core_conclusion"] == "原结论"
    assert fields["logic_1_body"] == "正文一重点"
    assert fields["underlying_reason"] == "原说明"
    assert logic_sections(marked) == ["logic_1", "logic_2"]


def test_apply_edits_escapes_html_preserves_locked_quote_and_reorders_logic():
    rendered = apply_edits(
        LEGACY,
        fields={"report_title": "新标题<script>", "logic_2_body": "第二段\n**重点**"},
        logic_order=["logic_2", "logic_1"], hidden_logic=["logic_1"],
    )
    assert "新标题&lt;script&gt;" in rendered
    assert "第二段<br><b>重点</b>" in rendered
    assert "标题一" not in rendered
    assert "策略逻辑一" in rendered and "策略逻辑二" not in rendered
    assert "8.8%" in rendered and "数据来源：iFinD" in rendered


def test_revision_sidecar_keeps_base_immutable_and_reapplies_after_base_change(tmp_path):
    base = tmp_path / "onepager.html"
    base.write_text(LEGACY, encoding="utf-8")
    original = base.read_text(encoding="utf-8")
    final, sidecar, payload = save_revision(
        base, fields={"core_conclusion": "人工结论"},
        logic_order=["logic_1", "logic_2"], hidden_logic=[],
    )
    assert base.read_text(encoding="utf-8") == original
    assert "人工结论" in final.read_text(encoding="utf-8")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["revision"] == 1
    base.write_text(original.replace("8.8%", "9.9%"), encoding="utf-8")
    reapplied = apply_saved_revision(base)
    assert reapplied == final
    updated = final.read_text(encoding="utf-8")
    assert "人工结论" in updated and "9.9%" in updated
    assert load_revision(base)["base_sha256"] == payload["base_sha256"]


def test_individual_chart_can_be_removed_without_hiding_logic():
    chart = section_region(
        "logic_1_chart_1",
        '<div class="rh-editable-chart"><div class="c-title">'
        + edit_region("logic_1_chart_1_title", "原图题")
        + '</div><div class="chart">图表数据</div></div>',
    )
    source = LEGACY.replace('<div class="chart"><img src="x"></div>', chart)
    assert chart_sections(source) == ["logic_1_chart_1"]
    rendered = apply_edits(
        source, logic_order=["logic_1", "logic_2"], hidden_logic=[],
        hidden_charts=["logic_1_chart_1"],
    )
    assert "图表数据" not in rendered
    assert "正文一" in rendered


def test_legacy_chart_and_subtitle_are_supported_in_revision():
    source = LEGACY.replace(
        '<div class="concl">',
        '<div class="sub">策略研究 · 报告生成 2026年9月21日</div><div class="concl">',
    )
    assert chart_sections(source) == ["logic_1_chart_1"]
    rendered = apply_edits(source, hidden_charts=["logic_1_chart_1"])
    assert '<img src="x">' not in rendered
    assert "报告生成" not in rendered
    assert "研究策略·2026年9月21日" in rendered
    assert "正文一" in rendered


def test_deleting_only_side_chart_restores_full_width_body():
    chart = section_region(
        "logic_1_chart_1", '<div class="rh-editable-chart">图表内容</div>',
    )
    source = LEGACY.replace(
        '<div class="body">正文一<b>重点</b></div><div class="chart"><img src="x"></div>',
        '<div class="logic-split"><div class="body">正文一<b>重点</b></div>'
        f'<div class="logic-side-chart"><div class="chart-block chart-block--side">{chart}</div></div></div>',
    )
    rendered = apply_edits(source, hidden_charts=["logic_1_chart_1"])
    assert "图表内容" not in rendered
    assert "logic-split" not in rendered
    assert '<div class="body">' in rendered


def test_deleting_one_of_two_charts_reflows_only_compact_remainder():
    compact = section_region(
        "logic_1_chart_1",
        '<!-- RH_CHART_LAYOUT:side --><div class="rh-editable-chart">'
        '<div class="chart chart--number-cards">小图</div></div>',
    )
    wide = section_region(
        "logic_1_chart_2",
        '<!-- RH_CHART_LAYOUT:full --><div class="rh-editable-chart">'
        '<div class="chart">宽图</div></div>',
    )
    source = LEGACY.replace(
        '<div class="body">正文一<b>重点</b></div><div class="chart"><img src="x"></div>',
        '<div class="body">正文一<b>重点</b></div>'
        f'<div class="chart-row">{compact}{wide}</div>',
    )
    compact_only = apply_edits(source, hidden_charts=["logic_1_chart_2"])
    assert '<div class="logic-split"><div class="body">' in compact_only
    assert '<div class="logic-side-chart">' in compact_only
    assert "小图" in compact_only and "宽图" not in compact_only
    assert compact_only.count("<div") == compact_only.count("</div>")
    wide_only = apply_edits(source, hidden_charts=["logic_1_chart_1"])
    assert 'class="logic-split"' not in wide_only
    assert '<div class="chart-row">' in wide_only
    assert "宽图" in wide_only and "小图" not in wide_only


def test_hidden_chart_survives_revision_reapplication(tmp_path):
    base = tmp_path / "onepager.html"
    base.write_text(LEGACY, encoding="utf-8")
    final, sidecar, _ = save_revision(
        base, fields={}, logic_order=["logic_1", "logic_2"], hidden_logic=[],
        hidden_charts=["logic_1_chart_1"],
    )
    assert 'src="x"' not in final.read_text(encoding="utf-8")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["hidden_charts"] == ["logic_1_chart_1"]
    base.write_text(LEGACY.replace("8.8%", "9.9%"), encoding="utf-8")
    apply_saved_revision(base)
    assert 'src="x"' not in final.read_text(encoding="utf-8")
    assert "9.9%" in final.read_text(encoding="utf-8")
