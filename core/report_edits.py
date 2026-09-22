"""一页通人工修订覆盖层。

自动生成的 HTML 保持为事实底稿；人工修订只保存允许编辑的文字字段、论点顺序
和显隐状态。最终 HTML 每次都由自动稿重新套用覆盖层生成，正式报价更新后也能
重新应用，不需要让分析师直接修改整份 HTML。
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
import re
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping


EDIT_START = "<!-- RH_EDIT_START:{key} -->"
EDIT_END = "<!-- RH_EDIT_END:{key} -->"
SECTION_START = "<!-- RH_SECTION_START:{key} -->"
SECTION_END = "<!-- RH_SECTION_END:{key} -->"

_EDIT_PATTERN = re.compile(
    r"<!-- RH_EDIT_START:(?P<key>[a-z0-9_]+) -->(?P<body>.*?)"
    r"<!-- RH_EDIT_END:(?P=key) -->",
    re.DOTALL,
)
_SECTION_PATTERN = re.compile(
    r"<!-- RH_SECTION_START:(?P<key>logic_[0-9]+) -->(?P<body>.*?)"
    r"<!-- RH_SECTION_END:(?P=key) -->",
    re.DOTALL,
)
_CHART_SECTION_PATTERN = re.compile(
    r"<!-- RH_SECTION_START:(?P<key>logic_[0-9]+_chart_[0-9]+) -->(?P<body>.*?)"
    r"<!-- RH_SECTION_END:(?P=key) -->",
    re.DOTALL,
)
_CN_NUM = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")


def edit_region(key: str, rendered_html: str) -> str:
    return EDIT_START.format(key=key) + rendered_html + EDIT_END.format(key=key)


def section_region(key: str, rendered_html: str) -> str:
    return SECTION_START.format(key=key) + rendered_html + SECTION_END.format(key=key)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"br", "p", "div", "li"} and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li"} and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r" *\n *", "\n", value)
        return value.strip()


def _plain_text(fragment: str) -> str:
    parser = _TextExtractor()
    parser.feed(fragment)
    return parser.text()


def _render_text(value: object) -> str:
    """只允许纯文本、换行及 ``**加粗**``，拒绝把粘贴 HTML 带入客户报告。"""
    escaped = html_lib.escape(str(value or ""), quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def ensure_edit_markers(source: str) -> str:
    """为旧报告补上最小编辑锚点；新报告已由 renderer 原生输出这些锚点。"""
    # 历史稿已有发布日期时沿用原日期并统一副标题用语；不凭修订时间伪造旧稿日期。
    source = re.sub(
        r'<div class="sub">\s*策略研究\s*·\s*报告生成\s*(\d{4}年\d{1,2}月\d{1,2}日)[^<]*</div>',
        lambda match: f'<div class="sub">研究策略·{match.group(1)}</div>', source, count=1,
    )
    if "RH_EDIT_START:report_title" not in source:
        source = re.sub(
            r'(<span class="accent">\s*——\s*)(.*?)(</span>)',
            lambda m: m.group(1) + edit_region("report_title", m.group(2)) + m.group(3),
            source, count=1, flags=re.DOTALL,
        )
    if "RH_EDIT_START:core_conclusion" not in source:
        source = re.sub(
            r'(<div class="concl"><span class="lbl">核心结论</span>)(.*?)(</div>)',
            lambda m: m.group(1) + edit_region("core_conclusion", m.group(2)) + m.group(3),
            source, count=1, flags=re.DOTALL,
        )

    title_index = 0
    body_index = 0

    def title_repl(match: re.Match[str]) -> str:
        nonlocal title_index
        title_index += 1
        return match.group(1) + edit_region(f"logic_{title_index}_title", match.group(2)) + match.group(3)

    def body_repl(match: re.Match[str]) -> str:
        nonlocal body_index
        body_index += 1
        return match.group(1) + edit_region(f"logic_{body_index}_body", match.group(2)) + match.group(3)

    if "RH_EDIT_START:logic_1_title" not in source:
        source = re.sub(r'(<span class="ltitle">)(.*?)(</span>)', title_repl, source, flags=re.DOTALL)
    if "RH_EDIT_START:logic_1_body" not in source:
        source = re.sub(r'(<div class="body">)(.*?)(</div>)', body_repl, source, flags=re.DOTALL)
    if "RH_EDIT_START:underlying_reason" not in source:
        source = re.sub(
            r'(<div class="u-why"><b>与研究主题的关联及选取原因</b>：)(.*?)(</div>)',
            lambda m: m.group(1) + edit_region("underlying_reason", m.group(2)) + m.group(3),
            source, count=1, flags=re.DOTALL,
        )
    if SECTION_START.format(key="logic_1") not in source:
        source = _wrap_legacy_logic_sections(source)
    source = _wrap_legacy_chart_sections(source)
    return source


def _wrap_legacy_logic_sections(source: str) -> str:
    """用平衡 div 扫描为旧版 ``.logic`` 区块补锚点，不重排或重写其它 HTML。"""
    start_pattern = re.compile(r'<div class="logic">')
    div_pattern = re.compile(r'<div\b[^>]*>|</div\s*>', re.I)
    output: list[str] = []
    cursor = 0
    index = 1
    while True:
        start = start_pattern.search(source, cursor)
        if start is None:
            output.append(source[cursor:])
            break
        depth = 0
        end = None
        for token in div_pattern.finditer(source, start.start()):
            if token.group(0).lower().startswith("</div"):
                depth -= 1
                if depth == 0:
                    end = token.end()
                    break
            else:
                depth += 1
        if end is None:
            output.append(source[cursor:])
            break
        output.append(source[cursor:start.start()])
        output.append(section_region(f"logic_{index}", source[start.start():end]))
        cursor = end
        index += 1
    return "".join(output)


def _wrap_balanced_divs(fragment: str, *, class_name: str, key_prefix: str) -> str:
    """Wrap matching top-level div blocks without attempting to parse or rewrite their contents."""
    start_pattern = re.compile(
        rf'<div class="(?:[^"]*?\s)?{re.escape(class_name)}(?:\s[^"]*)?">', re.I,
    )
    div_pattern = re.compile(r'<div\b[^>]*>|</div\s*>', re.I)
    output: list[str] = []
    cursor = 0
    index = 1
    while True:
        start = start_pattern.search(fragment, cursor)
        if start is None:
            output.append(fragment[cursor:])
            break
        depth = 0
        end = None
        for token in div_pattern.finditer(fragment, start.start()):
            if token.group(0).lower().startswith("</div"):
                depth -= 1
                if depth == 0:
                    end = token.end()
                    break
            else:
                depth += 1
        if end is None:
            output.append(fragment[cursor:])
            break
        output.append(fragment[cursor:start.start()])
        output.append(section_region(f"{key_prefix}_chart_{index}", fragment[start.start():end]))
        cursor = end
        index += 1
    return "".join(output)


def _wrap_legacy_chart_sections(source: str) -> str:
    """Give reports generated before chart deletion support one removable section per chart."""
    def replace_logic(match: re.Match[str]) -> str:
        key = match.group("key")
        body = match.group("body")
        if f"RH_SECTION_START:{key}_chart_" in body:
            return match.group(0)
        # Reports from different renderer versions have one of these outer chart units.
        for class_name in ("rh-editable-chart", "chart-switch", "chart"):
            wrapped = _wrap_balanced_divs(body, class_name=class_name, key_prefix=key)
            if wrapped != body:
                break
        return SECTION_START.format(key=key) + wrapped + SECTION_END.format(key=key)

    return _SECTION_PATTERN.sub(replace_logic, source)


def editable_fields(source: str) -> dict[str, str]:
    marked = ensure_edit_markers(source)
    return {match.group("key"): _plain_text(match.group("body")) for match in _EDIT_PATTERN.finditer(marked)}


def logic_sections(source: str) -> list[str]:
    return [match.group("key") for match in _SECTION_PATTERN.finditer(ensure_edit_markers(source))]


def chart_sections(source: str) -> list[str]:
    return [match.group("key") for match in _CHART_SECTION_PATTERN.finditer(ensure_edit_markers(source))]


def field_label(key: str) -> str:
    if key == "report_title":
        return "报告标题"
    if key == "core_conclusion":
        return "核心结论"
    if key == "underlying_reason":
        return "挂钩标的关联说明"
    match = re.fullmatch(r"logic_(\d+)_(title|body)", key)
    if match:
        return f"策略逻辑{match.group(1)}" + ("标题" if match.group(2) == "title" else "正文")
    match = re.fullmatch(r"logic_(\d+)_chart_(\d+)_title", key)
    if match:
        return f"策略逻辑{match.group(1)}·图表{match.group(2)}标题"
    return key


def _reflow_single_compact_chart(block: str) -> str:
    """两图删至一张后，仅将已知适合半栏的图移到正文旁边。"""
    row = re.search(r'<div class="chart-row">', block)
    if row is None or 'class="logic-split"' in block:
        return block
    div_tokens = re.finditer(r'<div\b[^>]*>|</div\s*>', block[row.start():], flags=re.I)
    depth = 0
    row_end = None
    for token in div_tokens:
        depth += -1 if token.group(0).lower().startswith("</div") else 1
        if depth == 0:
            row_end = row.start() + token.end()
            break
    if row_end is None:
        return block
    chart = block[row.end():row_end - len("</div>")]
    compact = ("RH_CHART_LAYOUT:side" in chart or
               "chart--number-cards" in chart or
               any(marker in chart for marker in ('class="g-row"', 'class="tc"', 'class="cc"')))
    if not compact:
        return block
    body = re.search(r'(<div class="body">.*?</div>)\s*$', block[:row.start()], flags=re.DOTALL)
    if body is None:
        return block
    split = (
        '<div class="logic-split">' + body.group(1) +
        '<div class="logic-side-chart"><div class="chart-block chart-block--side">' +
        chart + '</div></div></div>'
    )
    return block[:body.start()] + split + block[row_end:]


def apply_edits(source: str, *, fields: Mapping[str, object] | None = None,
                logic_order: list[str] | None = None,
                hidden_logic: list[str] | None = None,
                hidden_charts: list[str] | None = None) -> str:
    marked = ensure_edit_markers(source)
    changes = dict(fields or {})

    def replace_field(match: re.Match[str]) -> str:
        key = match.group("key")
        body = _render_text(changes[key]) if key in changes else match.group("body")
        return EDIT_START.format(key=key) + body + EDIT_END.format(key=key)

    rendered = _EDIT_PATTERN.sub(replace_field, marked)
    hidden_chart_keys = set(hidden_charts or [])
    rendered = _CHART_SECTION_PATTERN.sub(
        lambda match: "" if match.group("key") in hidden_chart_keys else match.group(0), rendered,
    )
    matches = list(_SECTION_PATTERN.finditer(rendered))
    if not matches:
        return rendered
    blocks = {match.group("key"): match.group(0) for match in matches}
    default_order = [match.group("key") for match in matches]
    requested = [key for key in (logic_order or []) if key in blocks]
    order = requested + [key for key in default_order if key not in requested]
    hidden = set(hidden_logic or [])
    visible = [key for key in order if key not in hidden]
    rendered_blocks: list[str] = []
    for index, key in enumerate(visible):
        label = _CN_NUM[index] if index < len(_CN_NUM) else str(index + 1)
        block = blocks[key]
        visible_chart_count = len(list(_CHART_SECTION_PATTERN.finditer(block)))
        if visible_chart_count == 0:
            block = re.sub(r'<div class="chart-block[^"]*">\s*</div>', "", block)
            block = re.sub(r'<div class="chart-row">\s*</div>', "", block)
            block = re.sub(r'<div class="logic-side-chart">\s*</div>', "", block)
            block = re.sub(
                r'<div class="logic-split">\s*(<div class="body">.*?</div>)\s*</div>',
                r"\1", block, flags=re.DOTALL,
            )
        elif visible_chart_count == 1:
            block = _reflow_single_compact_chart(block)
        block = re.sub(
            r'(<span class="tag">)策略逻辑[^<]*(</span>)',
            rf"\1策略逻辑{label}\2",
            block, count=1,
        )
        rendered_blocks.append(block)
    replacement = "\n".join(rendered_blocks)
    result = rendered[:matches[0].start()] + replacement + rendered[matches[-1].end():]
    if hidden_chart_keys:
        # Nested chart wrappers may become empty after an individual chart is removed.
        result = re.sub(r'<div class="chart-block[^"]*">\s*</div>', "", result)
        result = re.sub(r'<div class="chart-row">\s*</div>', "", result)
        result = re.sub(r'<div class="logic-side-chart">\s*</div>', "", result)
    return result


def sidecar_path(base_html: str | Path) -> Path:
    base = Path(base_html)
    return base.with_name(base.stem + "_人工修订.json")


def final_html_path(base_html: str | Path) -> Path:
    base = Path(base_html)
    return base.with_name(base.stem + "_人工修订.html")


def final_pdf_path(base_html: str | Path) -> Path:
    return final_html_path(base_html).with_suffix(".pdf")


def load_revision(base_html: str | Path) -> dict[str, Any]:
    path = sidecar_path(base_html)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save_revision(base_html: str | Path, *, fields: Mapping[str, object],
                  logic_order: list[str], hidden_logic: list[str],
                  hidden_charts: list[str] | None = None) -> tuple[Path, Path, dict[str, Any]]:
    base = Path(base_html).resolve()
    source = base.read_text(encoding="utf-8")
    previous = load_revision(base)
    revision = int(previous.get("revision") or 0) + 1
    payload: dict[str, Any] = {
        "version": 2,
        "active": True,
        "revision": revision,
        "edited_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "base_html": str(base),
        "base_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "fields": {str(key): str(value) for key, value in fields.items()},
        "logic_order": list(logic_order),
        "hidden_logic": list(hidden_logic),
        "hidden_charts": list(hidden_charts or []),
    }
    final = final_html_path(base)
    rendered = apply_edits(source, fields=payload["fields"], logic_order=logic_order,
                           hidden_logic=hidden_logic, hidden_charts=hidden_charts)
    final.write_text(rendered, encoding="utf-8")
    target = sidecar_path(base)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                         prefix=target.stem + "-", suffix=".tmp",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return final, target, payload


def apply_saved_revision(base_html: str | Path) -> Path | None:
    base = Path(base_html)
    payload = load_revision(base)
    if not payload or payload.get("active") is not True or not base.is_file():
        return None
    source = base.read_text(encoding="utf-8")
    rendered = apply_edits(
        source,
        fields=payload.get("fields") if isinstance(payload.get("fields"), Mapping) else {},
        logic_order=list(payload.get("logic_order") or []),
        hidden_logic=list(payload.get("hidden_logic") or []),
        hidden_charts=list(payload.get("hidden_charts") or []),
    )
    final = final_html_path(base)
    final.write_text(rendered, encoding="utf-8")
    return final


__all__ = (
    "apply_edits", "apply_saved_revision", "chart_sections", "edit_region", "editable_fields", "ensure_edit_markers", "field_label",
    "final_html_path", "final_pdf_path", "load_revision", "logic_sections", "save_revision",
    "section_region", "sidecar_path",
)
