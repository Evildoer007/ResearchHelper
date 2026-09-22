"""Narrow compatibility fixes for the currently activated OptionHelper release.

The Skill itself remains immutable and continues to pass its signed content check.
Release-specific interoperability fixes therefore live at the Research Helper host
boundary and are applied only when the affected private surface is present.
"""

from __future__ import annotations

from typing import Any


def apply_quote_compatibility_patches() -> list[str]:
    """Apply safe, idempotent fixes needed before ``run_project_request``.

    OptionHelper release ``f3c988e`` exposes the derived ``G`` term (internal
    normalised guarantee points) in ``reference_quote_fact``. Reporter correctly
    rejects public labels containing the internal points convention, but this
    leaves an empty label and aborts an otherwise successful quote. ``G`` is not
    a client contract input and is already represented by the public guarantee
    ratio, so it must not enter the external quote table.
    """

    applied: list[str] = []
    try:
        from modules.pricer import service as pricer_service  # type: ignore[import-not-found]
    except (ImportError, ModuleNotFoundError):
        return applied

    exclusions: Any = getattr(pricer_service, "_QUOTE_TERM_EXCLUSIONS", None)
    if not isinstance(exclusions, frozenset) or "G" in exclusions:
        return applied
    pricer_service._QUOTE_TERM_EXCLUSIONS = exclusions | frozenset({"G"})
    applied.append("exclude_internal_normalized_guarantee_points")
    return applied


__all__ = ("apply_quote_compatibility_patches",)
