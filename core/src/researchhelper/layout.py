"""Resolve development resources separately from self-contained App/Skill payloads."""
import json
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


def research_library_guides(root):
    """Read the curated Markdown sections also used by the file demo."""
    root = Path(root)
    if not (root / 'products/app/frontend').is_dir():
        asset = resource_directory(root, 'report_design') / 'library-guides.json'
        return json.loads(asset.read_text(encoding='utf-8')) if asset.is_file() else {}

    def section(path, title):
        lines = (root / path).read_text(encoding='utf-8').splitlines()
        start = lines.index('## ' + title) + 1
        result = []
        fenced = False
        for line in lines[start:]:
            if not fenced and line.startswith('## '):
                break
            result.append(line)
            if line.startswith('```'):
                fenced = not fenced
        return result

    methods = section('references/knowledge.md', '三、从问题选择方法')
    starts = [index for index, line in enumerate(methods) if line.startswith('### ')]
    ends = starts[1:] + [len(methods)]
    questions = [[methods[start][4:], '\n'.join(methods[start + 1:end]).strip()]
                 for start, end in zip(starts, ends)]
    if not questions or any(not body for _, body in questions):
        raise ValueError('研究方法指南缺少问题或操作步骤')
    report_guide = 'modules/reportcomposer/module.md'
    return {'questions': questions,
            'report': '\n'.join(section(report_guide, '对客策略简报')).strip(),
            'workpaper': '\n'.join(section(report_guide, '内部详细报告')).strip()}


_REPORT_RESOURCES = {
    'frontend': ('report.css', 'publication.css', 'draft-figures.js',
                 'visual-library.js', 'visual-catalog-data.js'),
    'report_design': ('report.html', 'evidence-charts.js', 'table-pagination.js',
                      'templates/report-standard.template.json',
                      'templates/report-detailed.template.json'),
    'vendor': ('echarts.min.js',),
}


def require_report_resources(root):
    """Check the shared HTML renderer before spending a research budget.

    This checks installation resources, never financial completeness. Data-only
    host checkpoints do not call it; the optional native PDF worker is checked
    by its exporter only when a PDF is requested. No installation is modified.
    """
    unavailable = []
    try:
        directories = {kind: resource_directory(root, kind)
                       for kind in _REPORT_RESOURCES}
    except ContractError:
        raise ContractError('报告组件目录不完整，请重新安装ResearchHelper后恢复本任务') from None
    for kind, names in _REPORT_RESOURCES.items():
        for name in names:
            path = directories[kind] / name
            try:
                # Packaged report resources are ordinary local files. Refuse
                # redirected resources rather than guessing another product.
                if path.resolve(strict=True) != path or not path.is_file():
                    raise OSError('Report resource is not a regular local file')
                if not path.read_text(encoding='utf-8').strip():
                    raise OSError('Report resource is empty')
            except (OSError, UnicodeError, RuntimeError):
                unavailable.append(name)
    if unavailable:
        names = '、'.join(unavailable)
        raise ContractError(f'报告组件缺失或无法读取：{names}。请重新安装ResearchHelper后恢复本任务，已有资料与用量保留')
