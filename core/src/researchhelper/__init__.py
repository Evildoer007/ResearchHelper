"""ResearchHelper shared research runtime. No legacy or OptionHelper imports."""

# Development has one implementation per capability. Relative researchhelper
# imports retain their identity; self-contained deliveries flatten this layout.
from pathlib import Path
_package = Path(__file__).resolve().parent
_version = _package / 'VERSION'
if _package.parent.name == 'src' and _package.parent.parent.name == 'core':
    _project = _package.parents[2]
    _version = _project / 'VERSION'
    for _module in ('evidencecollector', 'researchanalyzer', 'thesisbuilder', 'reportcomposer', 'visualrenderer', 'artifactexporter'):
        _source = _project / 'modules' / _module / 'src'
        if not _source.is_dir() or _source.is_symlink():
            raise RuntimeError('ResearchHelper capability source is missing: ' + _module)
        __path__.append(str(_source))
__version__ = _version.read_text(encoding='utf-8').strip()
