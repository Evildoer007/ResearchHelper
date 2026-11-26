"""Verify approved native icon bytes; routine builds never render a new logo."""
import hashlib
import json
from pathlib import Path


def verify_icons(root):
    root = Path(root).resolve()
    record = json.loads((root / 'assets/icons/icon-manifest.json').read_text())
    required = {'products/app/frontend/app-icon.svg', 'products/app/frontend/app-icon-dark.svg',
                'products/app/frontend/brand.svg'}
    required.update('assets/icons/' + name for name in ('researchhelper-mark.svg', 'researchhelper-logo.svg'))
    required.update('assets/icons/researchhelper-app-icon-tile-' + theme + '.' + extension
                    for theme in ('light', 'dark') for extension in ('svg', 'png', 'ico', 'icns'))
    if set(record.get('source_hashes', {})) != required:
        raise RuntimeError('受控图标清单不完整')
    for name, expected in record['source_hashes'].items():
        path = root / name
        if any(part.is_symlink() for part in (path, *path.parents) if part != root and part.is_relative_to(root)):
            raise RuntimeError('图标来源不能是符号链接')
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise RuntimeError('图标来源越界或缺失')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('受控图标与当前Logo不匹配，须独立审核后更新')
    for canonical, runtime in [('researchhelper-mark.svg', 'brand.svg'), ('researchhelper-logo.svg', 'login/wordmark.svg'), ('researchhelper-app-icon-tile-light.svg', 'app-icon.svg'), ('researchhelper-app-icon-tile-dark.svg', 'app-icon-dark.svg')]:
        if (root / 'assets/icons' / canonical).read_bytes() != (root / 'products/app/frontend' / runtime).read_bytes():
            raise RuntimeError('运行图标副本与统一Logo源不一致：' + canonical)
    return {**{extension: root / ('assets/icons/researchhelper-app-icon-tile-light.' + extension)
               for extension in ('png', 'ico', 'icns')},
            **{'dark_' + extension: root / ('assets/icons/researchhelper-app-icon-tile-dark.' + extension)
               for extension in ('png', 'ico', 'icns')}}
