"""Conservative, source-linked checks for a company in a narrow research theme.

A concept-stock search is a recall mechanism, not evidence that the company's
business is materially exposed to the theme.  Only an excerpt from a primary
disclosure can be promoted to a direct/indirect association here.  Everything
else stays visible to the analyst as an unverified candidate.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .evidence_discovery import SearchHit

_PRIMARY_HOSTS = (
    "cninfo.com.cn", "sse.com.cn", "szse.cn", "bse.cn",
)
_NEGATIVE = re.compile(r"(暂无|尚无|没有|未有|不涉及|不生产|不研发|不从事|无关|不存在|未开展|不构成).{0,22}(业务|产品|收入|订单|人形机器人|机器人)")
_DIRECT = re.compile(r"研发|生产|销售|量产|交付|主营|核心产品|产品|业务|收入|订单")
_INDIRECT = re.compile(r"供应|配套|客户|产业链|上游|下游|参股|合作")


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
