"""DeepSeek 模型目录、运行前校验与显式降级。

模型 ID 属于供应商运行时能力，不能作为应用常量长期写死。本模块只信任
认证后的 ``GET /models`` 返回值；最近一次成功目录会缓存到 ``data_cache``，
仅用于接口短暂不可达时的提示与校验，不把新出现的模型自动设为默认值。
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import requests


DEFAULT_MODEL = "deepseek-flash"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _models_url(base_url: str) -> str:
    return f"{str(base_url or 'https://api.deepseek.com').rstrip('/')}/models"


@dataclass
class ModelCatalog:
    ok: bool
    models: list[str] = field(default_factory=list)
    fetched_at: str = ""
    source: str = ""
    error: str = ""
    live_error: str = ""

    def payload(self) -> dict:
        return asdict(self)


@dataclass
class ModelPlan:
    ok: bool
    requested: dict[str, str] = field(default_factory=dict)
    effective: dict[str, str] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    fallback_used: dict[str, str] = field(default_factory=dict)
    error: str = ""


def load_cached_catalog(cache_path: str | Path) -> ModelCatalog:
    path = Path(cache_path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ModelCatalog(False, source="none", error="尚无可用的模型目录缓存")
    models = sorted({str(item).strip() for item in value.get("models", []) if str(item).strip()})
    if not models:
        return ModelCatalog(False, source="none", error="模型目录缓存为空")
    return ModelCatalog(True, models=models, fetched_at=str(value.get("fetched_at") or ""),
                        source="cache")


def fetch_model_catalog(*, api_key: str, base_url: str, cache_path: str | Path,
                        timeout: int = 12,
                        request_get: Callable | None = None) -> ModelCatalog:
    """读取账号当前可用模型并原子更新缓存；绝不记录 API Key。"""
    if not str(api_key or "").strip():
        return ModelCatalog(False, source="none", error="未配置 DEEPSEEK_API_KEY")
    getter = request_get or requests.get
    started = time.perf_counter()
    try:
        response = getter(
            _models_url(base_url),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:180]}")
        body = response.json()
        items = body.get("data", []) if isinstance(body, dict) else []
        models = sorted({str(item.get("id") or "").strip() for item in items
                         if isinstance(item, dict) and str(item.get("id") or "").strip()})
        if not models:
            raise RuntimeError("/models 未返回任何模型 ID")
        catalog = ModelCatalog(True, models=models, fetched_at=_now(), source="live")
        path = Path(cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(catalog.payload(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        return catalog
    except Exception as error:  # noqa: BLE001 - 网络边界统一转成结构化结果
        live_error = f"{type(error).__name__}: {str(error)[:240]}"
        cached = load_cached_catalog(cache_path)
        if cached.ok:
            cached.live_error = live_error
            return cached
        return ModelCatalog(False, source="none", error=live_error)
    finally:
        _ = time.perf_counter() - started


def build_model_plan(*, catalog: ModelCatalog, requested: dict[str, str],
                     fallback: str, allow_fallback: bool) -> ModelPlan:
    """校验快速/质量档位；只有调用方明确允许时才使用备用模型。"""
    clean = {slot: str(model or "").strip() for slot, model in requested.items()}
    if not catalog.ok:
        # 目录不可得时不猜测供应商状态；保留明确配置，让真实调用返回最终错误。
        return ModelPlan(True, requested=clean, effective=dict(clean),
                         error=f"模型目录不可用，未完成预检：{catalog.error}")
    available = set(catalog.models)
    unavailable = {slot: model for slot, model in clean.items() if model not in available}
    if not unavailable:
        return ModelPlan(True, requested=clean, effective=dict(clean))
    fallback = str(fallback or "").strip()
    if not fallback or fallback not in available:
        missing = "、".join(f"{slot}={model}" for slot, model in unavailable.items())
        return ModelPlan(False, requested=clean, effective={}, unavailable=unavailable,
                         error=f"模型不可用：{missing}；备用模型 {fallback or '未配置'} 也不可用")
    if not allow_fallback:
        return ModelPlan(False, requested=clean, effective={}, unavailable=unavailable,
                         error="存在不可用模型，需分析师明确确认是否本次改用备用模型")
    effective = dict(clean)
    used: dict[str, str] = {}
    for slot in unavailable:
        effective[slot] = fallback
        used[slot] = fallback
    return ModelPlan(True, requested=clean, effective=effective, unavailable=unavailable,
                     fallback_used=used)


def suggest_model_configuration(models: list[str], *, fast: str, quality: str,
                                fallback: str) -> tuple[dict[str, str], dict[str, str]]:
    """Suggest valid logical slots after a live catalog refresh.

    This only updates the settings form; persistence still requires the
    analyst to press Save. It prevents an editable combo box from silently
    retaining a retired model ID that is no longer in the live directory.
    """
    available = list(dict.fromkeys(str(item).strip() for item in models if str(item).strip()))
    if not available:
        return {"fast": fast, "quality": quality, "fallback": fallback}, {}

    fast_choice = fast if fast in available else (
        "deepseek-flash" if "deepseek-flash" in available else available[0])
    quality_preferred = next((item for item in available if "pro" in item.lower()), "")
    quality_choice = quality if quality in available else (quality_preferred or fast_choice)
    fallback_choice = fallback if fallback in available else fast_choice
    choices = {"fast": fast_choice, "quality": quality_choice, "fallback": fallback_choice}
    previous = {"fast": fast, "quality": quality, "fallback": fallback}
    replaced = {slot: f"{previous[slot]} → {model}" for slot, model in choices.items()
                if previous[slot] != model}
    return choices, replaced


def test_chat_model(*, api_key: str, base_url: str, model: str, timeout: int = 30,
                    request_post: Callable | None = None) -> dict:
    """用极小非 JSON 请求验证模型可真正完成推理，而不只出现在目录中。"""
    if not str(api_key or "").strip():
        return {"ok": False, "error": "未配置 DEEPSEEK_API_KEY"}
    poster = request_post or requests.post
    try:
        response = poster(
            f"{str(base_url).rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": str(model).strip(),
                "messages": [{"role": "user", "content": "只回复 OK"}],
                "stream": False,
                "max_tokens": 8,
                "thinking": {"type": "disabled"},
            },
            timeout=timeout,
        )
        if response.status_code != 200:
            return {"ok": False, "error": f"HTTP {response.status_code}: {response.text[:180]}"}
        body = response.json()
        actual = str(body.get("model") or model)
        return {"ok": True, "requested_model": model, "effective_model": actual,
                "validated_at": _now()}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": f"{type(error).__name__}: {str(error)[:240]}"}
