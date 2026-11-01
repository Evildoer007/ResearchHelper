"""Separate create/read host processes prove a cold installed-Skill recheck."""

import copy
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    installation, project, fixtures = map(Path, sys.argv[1:4])
    mode = sys.argv[4]
    spec = importlib.util.spec_from_file_location("installed_skill", installation / "scripts/session.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    session = module.Session(project)
    from researchhelper import material_parser as parser
    from researchhelper.contracts import ContractError, fingerprint
    from researchhelper.source_reader import _verify_original_text
    assert Path(parser.__file__).resolve().is_relative_to(installation.resolve())
    if mode == "create":
        task = session.create("核对原件内容", intent="原件内容核对")
        for name in ("original.pdf", "terms.docx"):
            session.material(task["id"], name, (fixtures / name).read_bytes())
        print(json.dumps({"task_id": task["id"]}))
        return
    assert not parser._verified_extractions, "Second host must start cold"
    task = session.archive.list()[0]
    selection = [{"artifact_id": record["id"], "snapshot_hash": record["snapshot_hash"],
        "queries": [], "parts": [{"part": record["parts"][0]["locator"], "start": 0, "end": min(100, len(record["parts"][0]["text"]))}],
        "question_ids": ["q1"], "reason": "独立冷启动核对字节与正文"} for record in task["materials"]]
    directory = session.sources(task["id"])
    original = parser.MaterialParser.parse
    calls = []
    def measured(current, *args, **kwargs):
        parsed = original(current, *args, **kwargs)
        runtime = parsed["parse_runtime"]
        assert runtime["parser_worker_reaped"] and runtime["transport_sender_stopped"] and runtime["transport_receiver_stopped"]
        assert not runtime["network_request_started"]
        calls.append(args[0])
        return parsed
    with patch.object(parser.MaterialParser, "parse", measured):
        bundles = [session.read(task["id"], selection, directory["binding"]) for _ in range(3)]
        assert bundles[0] == bundles[1] == bundles[2]
        assert calls == ["original.pdf", "terms.docx"]
        forged = copy.deepcopy(task["materials"][0])
        forged["snapshot"]["parts"][0]["text"] = "forged reading"
        forged["snapshot_hash"] = fingerprint(forged["snapshot"])
        try:
            _verify_original_text(forged)
        except ContractError:
            pass
        else:
            raise AssertionError("Cached extraction accepted rehashed forged text")
    final = session.archive.get(task["id"])
    assert not final["evidence"] and final["ledger"]["used"]["model_calls"] == final["ledger"]["used"]["data_requests"] == 0
    assert session.agent.model is None
    print(json.dumps({"status": "passed", "cold_parsers": calls, "repeated_reading_identical": True,
        "forged_text_rejected": True, "model_calls": 0, "data_requests": 0, "evidence_not_approved": True}))


if __name__ == "__main__":
    main()
