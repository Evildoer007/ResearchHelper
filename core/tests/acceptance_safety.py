"""Foreground QA is opt-in; ordinary validation must not touch the desktop."""
import os


def require_foreground_consent():
    """Call only after the user explicitly permits interactive testing."""
    if os.environ.get("RESEARCHHELPER_ALLOW_FOREGROUND_TESTS") != "1":
        raise RuntimeError("前台测试默认禁用。仅在用户明确允许后启用；后台验收不需要此权限。")
