"""Independent acceptance host, copied next to fixtures outside the repository."""

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def main():
    installation, project, pdf, docx = map(Path, sys.argv[1:5])
    specification = importlib.util.spec_from_file_location("installed_skill", installation / "scripts/session.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    session = module.Session(project)
    from researchhelper import engine
    from researchhelper.contracts import ContractError
    from researchhelper.evidence import extract_material
    assert Path(engine.__file__).resolve().is_relative_to(installation.resolve())
    task = session.create("核对原件内容", intent="原件内容核对")
    if not sys.flags.dont_write_bytecode:
        try:
            session.material(task["id"], "original.pdf", pdf.read_bytes())
        except ContractError as error:
            assert "不写字节码" in str(error)
        else:
            raise AssertionError("Isolated host without -B started a parser")
        assert not session.archive.get(task["id"])["materials"]
        print(json.dumps({"status": "passed", "unsafe_host_rejected": True}))
        return
    records = []
    for path in (pdf, docx):
        payload = path.read_bytes()
        record = session.material(task["id"], path.name, payload)
        expected = extract_material(path.name, payload)
        assert record["parts"] == expected["parts"] and record["parts"]
        assert record["hash"] == hashlib.sha256(payload).hexdigest()
        assert base64.b64decode(record["original_base64"]) == payload
        diagnostics = record["parse_runtime"]
        assert diagnostics["parser_worker_reaped"] and diagnostics["transport_isolated"]
        assert diagnostics["transport_sender_stopped"] and diagnostics["transport_receiver_stopped"]
        assert diagnostics["network_request_started"] is False
        assert record["source_check"] == record["applicability_check"] == "待核验"
        assert "parse_runtime" not in record["snapshot"]
        records.append({"type": path.suffix, "page_or_paragraph_count": len(record["parts"]),
            "original_hash": record["hash"], "parser_reaped": True})
    session.material(task["id"], pdf.name, pdf.read_bytes())
    final = session.archive.get(task["id"])
    assert len(final["materials"]) == 2
    assert not final["evidence"]
    assert final["ledger"]["used"]["model_calls"] == final["ledger"]["used"]["data_requests"] == 0
    assert session.agent.model is None
    print(json.dumps({"status": "passed", "cases": records, "duplicate_original_reused": True,
        "model_calls": 0, "data_requests": 0, "engine_in_installed_skill": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
