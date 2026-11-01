"""Run synthetic finance cases from an independent read-only Skill install."""

import copy
import importlib.util
import json
from pathlib import Path
import sys


def main():
    installation, project, fixture_path = map(Path, sys.argv[1:4])
    spec = importlib.util.spec_from_file_location("installed_skill", installation / "scripts/session.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    session = module.Session(project)
    from researchhelper import engine
    assert Path(engine.__file__).resolve().is_relative_to(installation.resolve())
    results = []
    for fixture in json.loads(fixture_path.read_text()):
        task = session.create(**fixture["options"])
        originals = {item["id"]: session.material(task["id"], item["name"], item["text"].encode())
            for item in fixture["originals"]}
        host = copy.deepcopy(fixture["host"])

        def rebind(value):
            if isinstance(value, dict):
                original = originals.get(value.get("artifact_id"))
                if original:
                    value["artifact_id"] = original["id"]
                    if "snapshot_hash" in value:
                        value["snapshot_hash"] = original["snapshot_hash"]
                for child in value.values():
                    rebind(child)
            elif isinstance(value, list):
                for child in value:
                    rebind(child)

        rebind(host)
        final = session.run(task["id"], **host)
        assert final["status"] == fixture["status"], final.get("error")
        assert final["ledger"]["used"]["model_calls"] == final["ledger"]["used"]["data_requests"] == 0
        assert len(final["evidence"]) == 2 and final["delivery"]
        report = session.export(task["id"])
        assert report.is_relative_to(project) and report.is_file()
        if fixture["expected"] is None:
            assert not final["calculations"] and any("零或负净利润" in gap for gap in final["gaps"])
        else:
            assert abs(final["calculations"][0]["output"]["value"] - fixture["expected"]) < 1e-9
            assert "<math " in report.read_text()
        results.append({"method": fixture["method"], "status": final["status"],
            "outputs": [item["output"] for item in final["calculations"]],
            "method_bindings": [item["method_binding"] for item in final["calculations"]]})
    assert session.agent.model is None
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
