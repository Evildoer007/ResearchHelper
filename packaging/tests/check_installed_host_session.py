"""Actual installed Skill entrypoint, isolated project and no source overlay."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from tests.rebuild_sdk_wire_acceptance import LOCAL_NETWORK_SANDBOX

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "result/acceptance/host-boundary-release"
INSTALLATION = ROOT / ".skills/researchhelper"
PYTHON = "/opt/anaconda3/envs/MachineLearning/bin/python"


def run():
    before = {str(p.relative_to(INSTALLATION)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in INSTALLATION.rglob("*") if p.is_file()}
    project = Path(tempfile.mkdtemp(prefix="installed-host-", dir=BASE))
    program = '''import importlib.util,json,sys
from pathlib import Path
installation,project=map(Path,sys.argv[1:])
spec=importlib.util.spec_from_file_location("installed_host_session",installation/"scripts/session.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
import researchhelper
from researchhelper.contracts import ContractError
assert Path(researchhelper.__file__).resolve().is_relative_to(installation)
session=module.Session(project)
task=session.create("核对所提供材料",questions=[{"id":"q1","kind":"fact","question":"材料写了什么"}])
checks=[]
for field in ("evidence","theses","evidence_specs","calculations","data_requests"):
 for value in ({},"",False,0):
  before=session.status(task["id"])
  try:session.run(task["id"],**{field:value})
  except ContractError:pass
  else:raise AssertionError("wrong container accepted")
  assert session.status(task["id"])==before
  checks.append(field+":"+type(value).__name__)
for value in ([],"",False,0):
 before=session.status(task["id"])
 try:session.run(task["id"],answers=value)
 except ContractError:pass
 else:raise AssertionError("wrong answers accepted")
 assert session.status(task["id"])==before
 checks.append("answers:"+type(value).__name__)
result=session.run(task["id"],defer_delivery=True)
assert not result.get("delivery") and not result["ledger"]["dispatches"]
assert all(q["status"]!="已回答" for q in result["questions"])
checks.append("omitted arguments preserve typed empty checkpoint")
print(json.dumps({"status":"passed","checks":checks,"engine_path":researchhelper.__file__,
 "session_path":module.__file__,"real_calls":0,"second_model_started":False}))
'''
    profile = LOCAL_NETWORK_SANDBOX + '(deny file-write* (subpath ' + json.dumps(str(INSTALLATION)) + '))'
    completed = subprocess.run(["/usr/bin/sandbox-exec", "-p", profile, PYTHON, "-I", "-B", "-c", program,
        str(INSTALLATION), str(project)], cwd=project, capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise AssertionError(completed.stderr)
    record = json.loads(completed.stdout)
    assert before == {str(p.relative_to(INSTALLATION)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in INSTALLATION.rglob("*") if p.is_file()}
    record.update(installation_unchanged=True, project=str(project), no_source_overlay=True)
    path = project / "receipt.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return {"status":record["status"], "checks":len(record["checks"]), "receipt":str(path)}
