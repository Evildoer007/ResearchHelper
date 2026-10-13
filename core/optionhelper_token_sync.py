"""OptionHelper 本地凭证同步。

Research Helper 的本机配置是 Refresh Token 的唯一编辑入口；OptionHelper 仍按
自身约定从项目 ``.optionhelper/memory.md`` 读取。这里调用当前已激活 Skill 的
原子保存函数，在两个存储之间建立明确且不泄露凭证的同步边界。
"""

from __future__ import annotations

import hmac
import importlib.util
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TokenSyncResult:
    ok: bool
    changed: bool = False
    message: str = ""


def _load_environment_module(skill_root: Path):
    script = skill_root / "scripts" / "environment_check.py"
    if not script.is_file():
        raise FileNotFoundError("OptionHelper Skill 缺少 scripts/environment_check.py")
    module_name = f"optionhelper_environment_check_{abs(hash(str(script.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise ImportError("无法载入 OptionHelper 的 Refresh Token 保存组件")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sync_optionhelper_refresh_token(
    token: str,
    project_root: str | Path,
    skill_root: str | Path,
) -> TokenSyncResult:
    """同步已确认的 Token；返回值和日志均不包含凭证原文。"""

    value = str(token or "").strip()
    if not value:
        return TokenSyncResult(ok=True, message="Research Helper 未配置 iFinD Refresh Token。")
    project = Path(project_root).expanduser().resolve()
    skill = Path(skill_root).expanduser().resolve()
    try:
        module = _load_environment_module(skill)
        current = module.load_ifind_refresh_token(project_root=project, skill_root=skill)
        if current and hmac.compare_digest(str(current), value):
            return TokenSyncResult(ok=True, message="OptionHelper Refresh Token 已同步。")
        module.save_ifind_refresh_token(value, project, skill_root=skill)
    except (OSError, ValueError, AttributeError, ImportError) as error:
        return TokenSyncResult(
            ok=False,
            message=f"无法同步 OptionHelper Refresh Token：{type(error).__name__}: {error}",
        )
    return TokenSyncResult(ok=True, changed=True, message="OptionHelper Refresh Token 已更新。")


def sync_configured_optionhelper_refresh_token(
    project_root: str | Path,
    *,
    token: str,
    skill_root: str | Path | None,
) -> TokenSyncResult:
    """报价前同步当前运行配置；未安装 Skill 时交由原有就绪检查说明。"""

    if not str(token or "").strip() or not skill_root:
        return TokenSyncResult(ok=True)
    return sync_optionhelper_refresh_token(token, project_root, skill_root)
