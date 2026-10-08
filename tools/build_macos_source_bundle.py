"""生成可复制到Mac的净化源码包，不包含本机凭证、客户资料或运行状态。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
OUTPUT = ROOT / "dist" / f"ResearchHelper-{VERSION}-mac-source.zip"
ARCHIVE_ROOT = f"ResearchHelper-{VERSION}-mac-source"

EXCLUDED_TOP_LEVEL = {
    ".agents", ".codex", ".git", ".optionhelper", ".pytest_cache",
    ".venv", ".venv-macos",
    ".release-venv", ".release-venv310", ".vscode", "build", "data",
    "data_cache", "dist", "output", "references", "result", "sources", "tmp",
}
EXCLUDED_FILES = {
    "config.local.json", ".env", "DESIGN.md", "history_titles.json",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".key", ".pem", ".p12", ".pfx"}


def included_files(root: Path = ROOT) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not relative.parts or relative.parts[0] in EXCLUDED_TOP_LEVEL:
            continue
        if "__pycache__" in relative.parts or path.is_dir():
            continue
        if path.name in EXCLUDED_FILES or path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def build(output: Path = OUTPUT) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    files = included_files()
    manifest_files: list[dict[str, object]] = []
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            archive.writestr(f"{ARCHIVE_ROOT}/{relative}", content)
            manifest_files.append({
                "path": relative,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest().upper(),
            })
        manifest = {
            "version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "purpose": "macOS build source; local secrets and runtime data excluded",
            "files": manifest_files,
        }
        archive.writestr(
            f"{ARCHIVE_ROOT}/source-bundle-manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    return output


if __name__ == "__main__":
    target = build()
    digest = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    print(target)
    print(f"SHA256={digest}")
