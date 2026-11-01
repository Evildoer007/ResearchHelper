"""Current self-contained Skill fixture, never copied from an old source layout."""
import importlib.util
from pathlib import Path
import shutil
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def build_skill_fixture(installation):
    installation=Path(installation)
    spec=importlib.util.spec_from_file_location('researchhelper_skill_fixture',ROOT/'packaging/skill/build_skill.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    with tempfile.TemporaryDirectory(prefix='skill-fixture-build-',dir=installation.parent) as folder:
        builder.skill(output=Path(folder),install=False)
        shutil.copytree(Path(folder)/'researchhelper',installation)
    return installation


def source_file(relative):
    relative=str(relative)
    if relative.startswith('app/web/'):
        return ROOT/'products/app/frontend'/relative.removeprefix('app/web/')
    return ROOT/relative
