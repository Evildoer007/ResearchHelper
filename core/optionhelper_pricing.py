"""Research Helper 向 OptionHelper 发起正式报价时使用的定价默认值。"""

from __future__ import annotations

from typing import Any, Mapping


# 正式参考报价的 Monte Carlo 默认路径数。OptionHelper 刻意不替调用方选择
# 精度，因此由 Research Helper 在正式报价边界明确给出；分析师显式填写的
# path_count 始终优先。
FORMAL_QUOTE_MONTE_CARLO_PATH_COUNT = 100_000


def _needs_default_path_count(pricing: Mapping[str, Any],
                              base: Mapping[str, Any] | None = None) -> bool:
    base = base or {}
    method = pricing.get("model_method", base.get("model_method"))
    path_count = pricing.get("path_count", base.get("path_count"))
    return method != "analytical" and path_count is None


def apply_formal_quote_pricing_defaults(body: Mapping[str, Any]) -> dict[str, Any]:
    """复制报价请求并为 Monte Carlo 报价补充默认路径数。

    没有 ``quote_variants`` 时默认值写入顶层 ``pricing_config``；组合报价则
    逐个参数版本写入，避免某个明确采用 analytical 的版本继承 Monte Carlo
    专用字段。函数不修改调用方传入的对象，也不替换显式路径数。
    """

    result = dict(body)
    raw_base = result.get("pricing_config")
    if raw_base is not None and not isinstance(raw_base, Mapping):
        # 类型错误由既有请求校验给出，不能在这里吞掉或改写。
        return result
    base_pricing = dict(raw_base or {})

    raw_variants = result.get("quote_variants")
    if isinstance(raw_variants, list) and raw_variants:
        variants: list[Any] = []
        for raw_variant in raw_variants:
            if not isinstance(raw_variant, Mapping):
                variants.append(raw_variant)
                continue
            variant = dict(raw_variant)
            raw_pricing = variant.get("pricing_config")
            if raw_pricing is not None and not isinstance(raw_pricing, Mapping):
                variants.append(variant)
                continue
            pricing = dict(raw_pricing or {})
            if _needs_default_path_count(pricing, base_pricing):
                pricing["path_count"] = FORMAL_QUOTE_MONTE_CARLO_PATH_COUNT
            variant["pricing_config"] = pricing
            variants.append(variant)
        result["quote_variants"] = variants
        if raw_base is not None:
            result["pricing_config"] = base_pricing
        return result

    if _needs_default_path_count(base_pricing):
        base_pricing["path_count"] = FORMAL_QUOTE_MONTE_CARLO_PATH_COUNT
    result["pricing_config"] = base_pricing
    return result


__all__ = (
    "FORMAL_QUOTE_MONTE_CARLO_PATH_COUNT",
    "apply_formal_quote_pricing_defaults",
)
