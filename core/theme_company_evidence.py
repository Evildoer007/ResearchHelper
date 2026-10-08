"""Conservative, source-linked checks for a company in a narrow research theme.

A concept-stock search is a recall mechanism, not evidence that the company's
business is materially exposed to the theme.  Only an excerpt from a primary
disclosure can be promoted to a direct/indirect association here.  Everything
else stays visible to the analyst as an unverified candidate.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .evidence_discovery import SearchHit
from llm.client import DeepSeekClient

_PRIMARY_HOSTS = (
    "cninfo.com.cn", "sse.com.cn", "szse.cn", "bse.cn",
)
_NEGATIVE = re.compile(r"(暂无|尚无|没有|未有|不涉及|不生产|不研发|不从事|无关|不存在|未开展|不构成).{0,22}(业务|产品|收入|订单|人形机器人|机器人)")
_DIRECT = re.compile(r"研发|生产|销售|量产|交付|主营|核心产品|产品|业务|收入|订单")
_INDIRECT = re.compile(r"供应|配套|客户|产业链|上游|下游|参股|合作")


def plan_theme_company_search(theme: str, client: DeepSeekClient | None = None,
                              *, max_segments: int = 6) -> tuple[list[dict], list[str]]:
    """Let the LLM plan *what to search*, never which securities are true.

    The fallback keeps the feature usable without an LLM, while callers can
    clearly record that no intelligent chain decomposition was available.
    """
    theme = re.sub(r"\s+", " ", str(theme or "")).strip()
    fallback = [{
        "segment": theme,
        "keywords": [theme],
        "query": f'{theme} A股 上市公司 产品 业务 年报 公告',
    }] if theme else []
    client = client or DeepSeekClient(purpose="fast")
    if not theme or not client.available():
        return fallback, ["未配置 LLM，未执行产业链拆解；将使用主题检索和 iFinD 补漏。"]
    request = {
        "task": "把细分投资主题拆成可用于发现A股相关企业的产业链环节和公开资料检索式",
        "theme": theme,
        "rules": [
            "生成3至6个互不重复的具体产品、零部件、设备、材料或服务环节",
            "每个环节只生成一个简短检索式，必须包含A股/上市公司以及年报、公告、产品、业务中的至少一个",
            "优先寻找公司官网、年报、交易所或法定披露，不要回答有哪些公司",
            "不得生成证券名称、证券代码、收入、订单或其他事实",
            "只返回合法 JSON 对象",
        ],
        "output": {"segments": [{"segment": "环节", "keywords": ["关键词"], "query": "检索式"}]},
    }
    response = client.chat_json(
        "你是金融研究检索规划器。你只拆产业链并生成检索式，不列举公司；只返回 JSON。",
        json.dumps(request, ensure_ascii=False), max_tokens=1100,
    )
    if not response.ok or not isinstance(response.data, dict):
        return fallback, [f"LLM 产业链拆解失败，改用主题检索：{response.error or '返回格式无效'}"]
    raw_segments = response.data.get("segments") or response.data.get("产业链环节") or []
    result: list[dict] = []
    seen: set[str] = set()
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue
        segment = re.sub(r"\s+", " ", str(raw.get("segment") or raw.get("环节") or "")).strip()[:40]
        query = re.sub(r"\s+", " ", str(raw.get("query") or raw.get("检索式") or "")).strip()[:120]
        keywords = [re.sub(r"\s+", " ", str(item)).strip()[:30]
                    for item in (raw.get("keywords") or raw.get("关键词") or [])]
        keywords = [item for item in keywords if item]
        if not segment or not query or segment in seen:
            continue
        if not any(token in query for token in ("A股", "上市公司")):
            query = f"{query} A股 上市公司"
        if not any(token in query for token in ("年报", "公告", "产品", "业务", "披露")):
            query = f"{query} 产品 业务 年报 公告"
        result.append({"segment": segment, "keywords": keywords or [segment], "query": query})
        seen.add(segment)
        if len(result) >= max_segments:
            break
    return (result or fallback), ([] if result else ["LLM 未返回可用环节，已使用主题检索。"])


def extract_company_leads(theme: str, materials: list[dict], client: DeepSeekClient | None = None,
                          *, limit: int = 15) -> tuple[list[dict], list[str]]:
    """Extract names only from supplied search text and verify verbatim support.

    This is intentionally not a free-form "name companies" prompt. A lead is
    retained only when its company name and quoted passage occur in the source
    text selected by the model. Security identity is verified by the caller.
    """
    client = client or DeepSeekClient(purpose="fast")
    if not materials or not client.available():
        return [], (["没有可供 LLM 抽取的检索材料。"] if not materials else ["未配置 LLM，跳过公司抽取。"])
    sources = []
    source_map: dict[str, dict] = {}
    for index, material in enumerate(materials[:24], start=1):
        source_id = f"S{index}"
        text = re.sub(r"\s+", " ", str(material.get("text") or "")).strip()[:1800]
        if len(text) < 30:
            continue
        source = {
            "source_id": source_id,
            "segment": str(material.get("segment") or ""),
            "title": str(material.get("title") or "")[:180],
            "url": str(material.get("url") or "")[:500],
            "text": text,
        }
        sources.append(source)
        source_map[source_id] = source
    if not sources:
        return [], ["检索结果没有足够正文，未形成 LLM 公司线索。"]
    request = {
        "task": "只从给定资料中抽取与研究主题存在具体产品、业务、供应、合作或产业链关系的A股上市公司线索",
        "theme": theme,
        "sources": sources,
        "rules": [
            "公司名称必须逐字出现在对应source的title或text中，禁止凭记忆补公司",
            "evidence_quote必须逐字摘自对应source的text，且须包含公司名称或由标题明确该公司主体",
            "代码仅在source中明确出现时填写，否则留空；不得猜证券代码",
            "排除只说行业背景、股票涨跌、概念股列表以及明确否认相关业务的内容",
            "每家公司最多一条，优先具体产品、量产、收入、订单、供应或合作表述",
            "只返回合法 JSON 对象",
        ],
        "output": {"companies": [{
            "company_name": "公司简称", "code": "可空", "segment": "产业链环节",
            "source_id": "S1", "evidence_quote": "逐字原文",
        }]},
    }
    response = client.chat_json(
        "你是证据约束的公司线索抽取器。只能复制输入资料中的公司名和原文，不得使用模型记忆；只返回 JSON。",
        json.dumps(request, ensure_ascii=False), max_tokens=1800,
    )
    if not response.ok or not isinstance(response.data, dict):
        return [], [f"LLM 公司线索抽取失败：{response.error or '返回格式无效'}"]
    raw_companies = response.data.get("companies") or response.data.get("公司") or []
    output: list[dict] = []
    rejected = 0
    seen: set[str] = set()
    for raw in raw_companies:
        if not isinstance(raw, dict):
            rejected += 1
            continue
        name = re.sub(r"\s+", "", str(raw.get("company_name") or raw.get("公司名称") or "")).strip()[:30]
        source_id = str(raw.get("source_id") or "").strip()
        quote = re.sub(r"\s+", " ", str(raw.get("evidence_quote") or raw.get("原文") or "")).strip()[:500]
        source = source_map.get(source_id)
        if not name or name in seen or source is None or len(quote) < 8:
            rejected += 1
            continue
        source_text = re.sub(r"\s+", " ", source["text"])
        source_title = re.sub(r"\s+", "", source["title"])
        if quote not in source_text or (name not in re.sub(r"\s+", "", quote) and name not in source_title):
            rejected += 1
            continue
        if _NEGATIVE.search(quote):
            rejected += 1
            continue
        code = str(raw.get("code") or raw.get("证券代码") or "").strip().upper()
        if code and code not in source["text"] and code.split(".")[0] not in source["text"]:
            code = ""
        output.append({
            "name": name, "code": code,
            "segment": str(raw.get("segment") or source.get("segment") or "").strip()[:50],
            "reason": quote, "source_title": source["title"], "source_url": source["url"],
        })
        seen.add(name)
        if len(output) >= limit:
            break
    warnings = ([f"LLM 返回的 {rejected} 条公司线索未通过逐字原文校验，已丢弃。"] if rejected else [])
    return output, warnings


def _primary_source(url: str) -> bool:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in _PRIMARY_HOSTS)


def _excerpt(theme: str, name: str, hit: SearchHit, *, raw_only: bool = False) -> str:
    """Return a short verbatim passage, never a model-written relationship."""
    title = str(hit.title or "")
    if not theme:
        return ""
    for raw in ((hit.raw_content,) if raw_only else (hit.raw_content, hit.summary)):
        content = re.sub(r"\s+", " ", str(raw or "")).strip()
        for sentence in re.split(r"(?<=[。！？；;])|\n", content):
            sentence = sentence.strip()
            if theme not in sentence or len(sentence) > 600:
                continue
            # A company-titled annual report can contain generic industry
            # background.  Require an explicit company subject in the passage.
            if name in sentence or (name in title and re.search(r"(?:本公司|公司|集团|我们)", sentence)):
                return sentence[:220]
    return ""


def assess_company_hits(theme: str, name: str, hits: list[SearchHit]) -> dict:
    """Classify only what a traceable passage actually says.

    The distinction is about *documented association*, not revenue materiality
    or a forecast.  Even a direct source remains unselected until an analyst
    explicitly checks the company in the confirmation dialog.
    """
    secondary: dict | None = None
    for hit in hits:
        primary = _primary_source(hit.url)
        # Search-result summaries are not guaranteed to be verbatim page text.
        # A primary URL with only a snippet still cannot prove the business link.
        passage = _excerpt(theme, name, hit, raw_only=primary)
        if not passage or _NEGATIVE.search(passage):
            continue
        source = {"reason": passage, "source_title": hit.title, "source_url": hit.url}
        if primary:
            if _INDIRECT.search(passage):
                return {"association": "indirect", **source}
            if _DIRECT.search(passage):
                return {"association": "direct", **source}
        elif secondary is None:
            secondary = {"association": "concept_only", **source}
    return secondary or {"association": "unverified", "reason": "尚无可核实的具体业务关联原文", "source_title": "", "source_url": ""}


def search_company_association(theme: str, name: str, searcher) -> dict:
    """Retrieve candidate text; a search miss never becomes a positive claim."""
    try:
        hits = searcher(f'"{name}" "{theme}" 产品 业务 年报 公告', limit=4)
    except Exception:
        hits = []
    return assess_company_hits(theme, name, hits)
