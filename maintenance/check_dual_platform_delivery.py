"""Record dual-platform source checks and an actual macOS double-click build.

No Windows result is synthesized. Accepted releases and installed Apps are read-only.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
import re
from candidate_acceptance import selected_candidate, verify_candidate

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('RESEARCHHELPER_ACCEPTANCE_DIR') or ROOT / 'result/maintenance/dual-platform-20261010').resolve()


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def run():
    if not OUT.is_relative_to(ROOT / 'result/maintenance'):
        raise RuntimeError('验收记录须位于ResearchHelper目录')
    if (OUT / 'acceptance.json').exists():
        raise FileExistsError('已有验收记录不得覆盖，请使用新的验收目录')
    OUT.mkdir(parents=True, exist_ok=True)
    candidate_path = selected_candidate(ROOT)
    candidate = verify_candidate(candidate_path, ROOT)
    tests = ['tests.test_dual_platform_build_offline', 'tests.test_oh_repository_layout_offline',
             'tests.test_report_print_surface_offline', 'tests.test_chat_contracts_offline',
             'tests.test_research_workflow_matrix_offline', 'tests.test_run_control_offline',
             'tests.test_packaging_acceptance_integrity_offline', 'tests.test_current_candidate_privacy_offline',
             'tests.test_repository_alignment_offline']
    completed = subprocess.run([sys.executable, '-B', '-u', '-m', 'unittest', *tests],
                               cwd=ROOT, capture_output=True, text=True, timeout=60)
    (OUT / 'offline-tests.txt').write_text(completed.stdout + completed.stderr)
    if completed.returncode:
        raise RuntimeError('Scoped offline checks failed; see retained log')
    count = re.search(r'Ran (\d+) tests?', completed.stderr)
    skipped = re.search(r'skipped=(\d+)', completed.stderr)
    if not count:
        raise RuntimeError('测试日志缺少实际执行数量')
    with zipfile.ZipFile(candidate['artifacts']['skill']['path']) as package:
        if package.testzip() is not None:
            raise RuntimeError('Candidate Skill zip is damaged')
    mount = Path(tempfile.mkdtemp(prefix='dual-platform-dmg-', dir=ROOT / 'result/acceptance'))
    attached = False
    try:
        subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-plist',
                        '-mountpoint', str(mount), candidate['artifacts']['dmg']['path']],
                       check=True, capture_output=True, timeout=30)
        attached = True
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(mount / 'ResearchHelper.app')],
                       check=True, capture_output=True, timeout=30)
        # Run the exact read-only mounted distribution, not another latest build.
        runtime = subprocess.run([sys.executable, '-B', str(ROOT / 'maintenance/check_migrated_candidate.py')],
            cwd=ROOT, env=dict(os.environ, RESEARCHHELPER_ACCEPTANCE_DIR=str(OUT),
                              RESEARCHHELPER_CANDIDATE_RECEIPT=str(candidate_path),
                              RESEARCHHELPER_ACCEPTANCE_BUNDLE=str(mount / 'ResearchHelper.app')),
            check=True, capture_output=True, text=True, timeout=45)
        embedded = json.loads(runtime.stdout)
        if embedded['candidate'] != str(candidate_path.relative_to(ROOT)) or embedded['manifest_sha256'] != candidate['manifest_sha256']:
            raise RuntimeError('包内后端验收身份与DMG不一致')
    finally:
        if attached:
            subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], check=True, capture_output=True, timeout=15)
        mount.rmdir()
    if verify_candidate(candidate_path, ROOT) != candidate:
        raise RuntimeError('验收期间候选身份变化')
    executed = int(count.group(1))
    skipped_count = int(skipped.group(1)) if skipped else 0
    record = {'status': 'dual_platform_source_and_macos_candidate_checked',
              'python_checks': {'run': executed, 'passed': executed - skipped_count, 'skipped': skipped_count,
                                'log': str((OUT / 'offline-tests.txt').relative_to(ROOT))},
              'candidate': str(candidate_path.relative_to(ROOT)), 'source_hashes_match': True,
              'demo_integrity': 'full_directory_verified',
              'macos': {'entry_execution': 'not_asserted_by_this_checker', 'dmg_signature': 'read_only_mounted_verified',
                        'embedded_backend': embedded, 'installed': False, 'published': False},
              'windows': {'entry_and_builder_implemented': True, 'native_host_implemented': True,
                          'compiled': False, 'installer_built': False, 'installed': False,
                          'native_ui_verified': False, 'reason': 'No Windows x64 build host available'},
              'optionhelper_operations': 'read_only', 'real_model_calls': 0, 'real_data_requests': 0,
              'demo_overwritten': False, 'existing_release_overwritten': False, 'all_prd_verified': False}
    (OUT / 'acceptance.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({'status': record['status'], 'receipt': str(OUT / 'acceptance.json')}))


if __name__ == '__main__':
    run()
