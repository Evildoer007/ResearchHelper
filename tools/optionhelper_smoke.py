"""Offline integration smoke test using installed Skill and a stub model.

No network calls, real quotes, client data, or credentials are used. Execute with
the selected OptionHelper interpreter, passing the separately built Skill path.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Fix Research Helper's core before the Skill adds its runtime path.
from core.optionhelper_recommender_worker import DeepSeekAgentPort
from core.optionhelper_quote_worker import build_project_request


def run(skill: Path) -> dict:
    sys.path.insert(0, str(skill.resolve() / "scripts"))
    with tempfile.TemporaryDirectory(prefix="research-helper-optionhelper-smoke-") as temporary:
        project = Path(temporary)
        os.environ.update(
            OPTIONHELPER_RUNTIME_ROOT=str(project / ".optionhelper" / "runtime"),
            OPTIONHELPER_DATA_ROOT=str(project / "data"),
            OPTIONHELPER_RESULT_ROOT=str(project / "result"),
        )
        import tool_entry
        import llm.client
        seen_roles = []

        class StubClient:
            def __init__(self, **kwargs):
                pass

            def chat_json(self, system, prompt, **kwargs):
                payload = json.loads(prompt)
                role = payload["role"].split(".")[-1]
                domain = payload["input"]
                seen_roles.append(role)
                if role == "Interpreter":
                    result = {
                        "confirmed_constraints": domain["confirmed_constraints"],
                        "missing_information": [], "next_question": None,
                        "research_queries": ["看涨期权"],
                    }
                elif role == "Selector":
                    refs = [item for item in domain["evidence"] if item["product_id"] == "1.1"]
                    assert refs, "Real product catalog did not return call evidence"
                    constraints = domain["case"]["confirmed_constraints"]
                    underlying = constraints["underlying"]
                    result = {"proposals": [{
                        "product_id": "1.1", "product_name": "看涨期权",
                        "underlyings": [underlying], "reason": "用于离线协议测试的看涨结构",
                        "suitable_for": ["看涨"], "not_suitable_for": ["看跌"],
                        "main_risks": ["期权费损失"], "library_status": "ready",
                        "evidence_ref_ids": [item["evidence_id"] for item in refs],
                        "missing_inputs": [],
                    }]}
                elif role == "Reviewer":
                    result = {"reviews": [{
                        "product_id": item["product_id"], "hard_reject": False,
                        "rejection_reason": None, "additional_not_suitable_for": [],
                        "additional_risks": [], "rank_adjustment": 0,
                    } for item in domain["proposals"]]}
                else:
                    raise AssertionError(f"Unexpected role: {role}")
                return SimpleNamespace(ok=True, data=result)

        results = []
        with patch.object(llm.client, "DeepSeekClient", StubClient), contextlib.redirect_stdout(sys.stderr):
            for code in ("561160.SH", "561910.SH"):
                response = tool_entry.run_recommendation_request({
                    "prompt": f"看涨 {code}，3个月，最大损失100%，接受本金波动，推荐结构",
                    "constraints": {"underlying": code, "market_view": "看涨", "horizon": "3个月",
                                    "max_loss": "100%", "principal_fluctuation": True},
                }, project_root=project, agent_port=DeepSeekAgentPort("offline-stub"))
                assert response["ok"] is True, response.get("message")
                assert response["recommendation"]["candidates"], "No validated candidates"
                results.append({"code": code, "candidate_count": len(response["recommendation"]["candidates"])})
        assert seen_roles == ["Interpreter", "Selector", "Reviewer"] * 2
        # Explicit dates/variants survive Research Helper's quote envelope unchanged.
        request = build_project_request({"selection": {"underlyings": ["561160.SH"]},
                                         "pricing_config": {"valuation_date": "2026-09-15"}})
        assert request["pricing_config"]["valuation_date"] == "2026-09-15"
        assert "pricing_config" not in build_project_request({"selection": {"underlyings": ["561910.SH"]}})
        return {"ok": True, "mode": "offline-stub", "recommendations": results,
                "role_receipts": "validated", "network_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_root", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.skill_root), ensure_ascii=False))
