"""Compose independent product builders inside one frozen source transaction."""
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))
from core.bootstrap import configure_imports
configure_imports()
from release_contract import OUTPUT, VERSION
from source_snapshot import source_files, digest
from build_environment import build_platform
from artifact_integrity import directory_record
from shared_payload import runtime_identity
from build_progress import stage


def _builder(relative):
    spec = importlib.util.spec_from_file_location('rh_' + Path(relative).stem, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def skill(*, install=False, pdf_renderer=None):
    return _builder('packaging/skill/build_skill.py').skill(install=install, output=OUTPUT, pdf_renderer=pdf_renderer)


def demo(*, pdf_renderer=None):
    return _builder('packaging/demo/build_demo.py').demo(output=OUTPUT, pdf_renderer=pdf_renderer)


def macos(*, pdf_renderer=None):
    return _builder('packaging/app/build_app.py').macos(output=OUTPUT, pdf_renderer=pdf_renderer)


def windows(skill_archive):
    return _builder('packaging/app/windows/build_windows.py').windows(output=OUTPUT, skill_archive=skill_archive)


def release_record(artifacts):
    target = build_platform()
    components = {'skill': OUTPUT / 'researchhelper/manifest.json', 'demo': OUTPUT / 'demo/manifest.json',
                  'app': OUTPUT / ('app-manifest.json' if target == 'macos' else 'windows-app-manifest.json')}
    record = {'version': VERSION, 'mode': 'offline-candidate', 'acceptance_mode': 'offline',
              'platform': target, 'architecture': 'arm64' if target == 'macos' else 'x64',
              'runtime': runtime_identity(ROOT),
              'component_manifests': {key: {'path': str(path), 'sha256': digest(path)} for key, path in components.items()},
              'source_hashes': {p.relative_to(ROOT).as_posix(): digest(p) for p in source_files(ROOT)},
              'artifacts': {key: {'path': value, 'sha256': digest(value), 'bytes': Path(value).stat().st_size}
                            for key, value in artifacts.items() if Path(value).is_file()},
              'directory_artifacts': {key: directory_record(value) for key, value in artifacts.items()
                                      if Path(value).is_dir()},
              'notarized': False, 'real_model_verified': False, 'real_provider_verified': False,
              'runtime_policy': {'app': 'authenticated-admin-consent',
                                 'skill': 'explicit-host-source-consent', 'demo': 'locked-offline'}}
    (OUTPUT / 'release-manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return artifacts


def build(*, install_skill=False):
    target = build_platform()
    OUTPUT.mkdir(parents=True, exist_ok=False)
    with stage('3/7', '组装独立Skill，共用唯一研究内核'):
        pdf_renderer=_builder('packaging/app/build_app.py').macos_pdf(output=OUTPUT) if target=='macos' else None
        artifacts = {'skill': str(skill(install=install_skill,pdf_renderer=pdf_renderer))}
    with stage('4/7', '生成离线demo并运行固定案例'):
        artifacts['demo'] = str(demo(pdf_renderer=pdf_renderer))
    with stage('5/7', '构建macOS App与DMG' if target == 'macos' else '构建Windows原生App与安装器'):
        if target == 'macos':
            artifacts['dmg'] = str(macos(pdf_renderer=pdf_renderer))
        else:
            artifacts['setup'] = str(windows(artifacts['skill']))
    return release_record(artifacts)


if __name__ == '__main__':
    print(json.dumps(build(), ensure_ascii=False))
