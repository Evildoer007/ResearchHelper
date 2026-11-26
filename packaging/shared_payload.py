"""Assemble the same core and capability implementations for every delivery."""
from pathlib import Path
import hashlib
import shutil
import json
from release_contract import ROOT, MODULES, PROTOCOL_ID

BROWSER_NOTICES = {
    'Bloub': ('b4bb3c1', ('Bloub-LICENSE.txt', 'Bloub-SOURCE.md')),
    'ThinkingOrbs': ('0.3.1', ('ThinkingOrbs-LICENSE.txt', 'ThinkingOrbs-SOURCE.md')),
    'Apache ECharts': ('6.0.0', ('Apache-ECharts-LICENSE.txt', 'Apache-ECharts-NOTICE.txt',
                              'Apache-ECharts-d3-LICENSE.txt', 'Apache-ECharts-SOURCE.md')),
}


def copy_browser_notices(resources, *, packages=None, root=ROOT):
    """Copy an explicit notice set, not arbitrary files from a license folder."""
    root, resources = Path(root).resolve(), Path(resources)
    selected = tuple(BROWSER_NOTICES) if packages is None else tuple(packages)
    if len(selected) != len(set(selected)) or any(name not in BROWSER_NOTICES for name in selected):
        raise ValueError('Unknown or repeated browser notice package')
    pending = []
    for name in selected:
        version, filenames = BROWSER_NOTICES[name]
        for filename in filenames:
            source = root / 'LICENSES' / filename
            if (source.is_symlink() or not source.is_file() or
                    (root / 'LICENSES').is_symlink()):
                raise RuntimeError('Missing vendored browser license: ' + filename)
            pending.append((name, version, source))
    # Validate the entire group before creating or partially copying output.
    directory = resources / 'licenses'
    if directory.is_symlink():
        raise RuntimeError('Browser notice destination cannot be a symbolic link')
    directory.mkdir(parents=True, exist_ok=True)
    inventory = []
    for name, version, source in pending:
        target = directory / source.name
        if target.is_symlink():
            raise RuntimeError('Browser notice destination cannot be a symbolic link')
        shutil.copyfile(source, target)
        if target.read_bytes() != source.read_bytes():
            raise RuntimeError('Browser notice copy does not match source: ' + source.name)
        inventory.append({'package': name, 'version': version,
                          'file': str(target.relative_to(resources))})
    return inventory


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob('*')) if p.is_file() and '__pycache__' not in p.parts
            and p != Path(root) / 'manifest.json'}


def copy_tree(source, target):
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))


def copy_report_design(frontend, root=ROOT):
    """Bundle the report shell, named definitions and composition renderer."""
    from researchhelper.layout import research_library_guides
    source = Path(root) / 'modules/visualrenderer/assets'
    destination = Path(frontend) / 'report-design'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'library-guides.json').write_text(
        json.dumps(research_library_guides(root), ensure_ascii=False), encoding='utf-8')
    for name in ('report.html', 'evidence-charts.js', 'table-pagination.js', 'templates/report-standard.template.json', 'templates/report-detailed.template.json'):
        (destination / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination / name)


def runtime_sources(root=ROOT):
    root = Path(root)
    files = list((root / 'core/src/researchhelper').glob('*.py'))
    for name in MODULES:
        files.extend((root / 'modules' / name / 'src').glob('*.py'))
    names = [file.name for file in files]
    if len(names) != len(set(names)):
        raise RuntimeError('Shared runtime module names conflict')
    return sorted(files)


def copy_runtime(target, root=ROOT):
    target = Path(target)
    if target.exists():
        raise FileExistsError('Runtime payload must be built in an empty directory')
    target.mkdir(parents=True)
    for source in runtime_sources(root):
        if source.is_symlink() or not source.resolve().is_relative_to(Path(root).resolve()):
            raise RuntimeError('Runtime source is outside the build snapshot')
        shutil.copyfile(source, target / source.name)
    shutil.copyfile(Path(root) / 'VERSION', target / 'VERSION')
    verify_runtime(target, runtime_identity(root))


def runtime_identity(root=ROOT):
    root = Path(root)
    files = {source.name: hashlib.sha256(source.read_bytes()).hexdigest() for source in runtime_sources(root)}
    files['VERSION'] = hashlib.sha256((root / 'VERSION').read_bytes()).hexdigest()
    identity = {'version': (root / 'VERSION').read_text().strip(), 'protocol_id': PROTOCOL_ID, 'files': files}
    identity['sha256'] = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return identity


def verify_runtime(directory, expected):
    directory = Path(directory)
    actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()
              if path.is_file() and (path.suffix == '.py' or path.name == 'VERSION')}
    if actual != expected['files']:
        raise RuntimeError('共享研究内核的版本或源码身份不一致')


def copy_pdf_renderer(resources, renderer=None, *, root=ROOT):
    """A complete macOS release shares one already compiled offline renderer."""
    import sys
    if sys.platform!='darwin':return
    resources=Path(resources);target=resources/'app/tools/ResearchHelperHTMLPDF'
    target.parent.mkdir(parents=True,exist_ok=True)
    if renderer is None:
        import importlib.util,tempfile
        # Direct component builds also remain self-contained. The normal
        # release path passes the shared worker and compiles it only once.
        with tempfile.TemporaryDirectory(prefix='pdf-build-',dir=resources.parent) as folder:
            spec=importlib.util.spec_from_file_location('rh_pdf_builder',Path(root)/'packaging/app/build_app.py')
            builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
            return copy_pdf_renderer(resources,builder.macos_pdf(Path(folder)),root=root)
    renderer=Path(renderer)
    if renderer.is_symlink() or not renderer.is_file():raise RuntimeError('PDF转换组件不可用')
    shutil.copyfile(renderer,target);target.chmod(0o755)
    if target.read_bytes()!=renderer.read_bytes():raise RuntimeError('PDF转换组件复制不一致')
