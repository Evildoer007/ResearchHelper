"""Resolve development resources separately from self-contained App/Skill payloads."""
from pathlib import Path

from .contracts import ContractError


def default_resource_root():
    package = Path(__file__).resolve().parent
    if package.parent.name == 'src' and package.parent.parent.name == 'core':
        return package.parents[2]
    return package.parent


def resource_directory(root, kind):
    root = Path(root).resolve()
    development = root / 'products/app/frontend'
    if development.is_dir():
        directories = {'frontend': development, 'data': root / 'products/app/data',
                       'vendor': root / 'assets/vendor'}
    elif (root / 'app/web').is_dir():
        # App/Skill/exported-demo runtime resources do not contain the dev tree.
        directories = {'frontend': root / 'app/web', 'data': root / 'app/data',
                       'vendor': root / 'assets/vendor'}
    else:
        raise ContractError('ResearchHelper资源布局不完整，不能猜测其他产品路径')
    directories['report_design'] = (root / 'modules/visualrenderer/assets') if development.is_dir() else directories['frontend'] / 'report-design'
    if kind not in directories:
        raise ContractError('未知ResearchHelper资源类别')
    return directories[kind]
