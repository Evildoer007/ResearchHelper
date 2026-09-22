from __future__ import annotations

import sys
import types

from core.optionhelper_compat import apply_quote_compatibility_patches


def test_quote_compat_excludes_internal_normalized_guarantee_points(monkeypatch):
    modules = types.ModuleType("modules")
    pricer = types.ModuleType("modules.pricer")
    service = types.ModuleType("modules.pricer.service")
    service._QUOTE_TERM_EXCLUSIONS = frozenset({"monitor", "pricing_methods"})
    pricer.service = service
    modules.pricer = pricer
    monkeypatch.setitem(sys.modules, "modules", modules)
    monkeypatch.setitem(sys.modules, "modules.pricer", pricer)
    monkeypatch.setitem(sys.modules, "modules.pricer.service", service)

    assert apply_quote_compatibility_patches() == [
        "exclude_internal_normalized_guarantee_points"
    ]
    assert service._QUOTE_TERM_EXCLUSIONS == frozenset(
        {"monitor", "pricing_methods", "G"}
    )
    assert apply_quote_compatibility_patches() == []


def test_quote_compat_is_noop_when_optionhelper_is_not_importable(monkeypatch):
    monkeypatch.delitem(sys.modules, "modules", raising=False)
    monkeypatch.delitem(sys.modules, "modules.pricer", raising=False)
    monkeypatch.delitem(sys.modules, "modules.pricer.service", raising=False)

    # The normal Research Helper interpreter does not put an OptionHelper Skill's
    # scripts directory on sys.path, so absence is a valid no-op state.
    assert apply_quote_compatibility_patches() == []
