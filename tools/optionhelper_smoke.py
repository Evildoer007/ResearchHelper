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
from core.optionhelper_recommender_worker import DeepSeekAgentPort, recommendation_request_body
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
                request = recommendation_request_body(
                    f"看涨 {code}，3个月，最大损失100%，接受本金波动，推荐结构",
                    {"underlying": code, "market_view": "看涨", "horizon": "3个月",
                     "max_loss": "100%", "principal_fluctuation": True},
                )
                try:
                    response = tool_entry.run_recommendation_request(
                        request, project_root=project, agent_port=DeepSeekAgentPort("offline-stub"),
                    )
                except Exception as error:
                    # 新版 OptionHelper 会把内部推荐异常收束成 unavailable；测试失败时
                    # 输出其审计尾部，便于识别协议变化，而不是只留下笼统的“条件不足”。
                    paths = tool_entry.bootstrap_runtime()
                    layout = tool_entry.LocalProjectLayout.create(
                        skill_root=paths.project_root, project_root=project,
                    )
                    layout.initialize()
                    authority = tool_entry.LocalHostAuthority(layout)
                    case = tool_entry._recommendation_case(
                        prompt=request["prompt"], workflow="recommendation",
                        requested_outputs=(), body=request, paths=paths,
                        layout=layout, authority=authority,
                    )
                    from modules.recommender.service import RecommenderService
                    detail = RecommenderService(
                        agent_port=DeepSeekAgentPort("offline-stub"),
                        knowledge_port=tool_entry._LocalKnowledgePort(paths, case.catalog_version),
                    ).recommend_fixed(case, workflow="recommendation")
                    raise AssertionError(json.dumps({
                        "public_error": f"{type(error).__name__}: {error}",
                        "case_constraints": dict(case.confirmed_constraints),
                        "recommendation_set": detail.get("recommendation_set"),
                    }, ensure_ascii=False)) from error
                assert response["ok"] is True, response.get("message")
                assert response["recommendation"]["candidates"], "No validated candidates"
                results.append({"code": code, "candidate_count": len(response["recommendation"]["candidates"])})
        assert seen_roles == ["Interpreter", "Selector", "Reviewer"] * 2
        # Explicit dates/variants survive Research Helper's quote envelope unchanged.
        request = build_project_request({"selection": {"underlyings": ["561160.SH"]},
                                         "pricing_config": {"valuation_date": "2026-09-15"}})
        assert request["pricing_config"]["valuation_date"] == "2026-09-15"
        fresh = build_project_request({"selection": {"underlyings": ["561910.SH"]}})
        assert "valuation_date" not in fresh.get("pricing_config", {})
        assert fresh.get("pricing_config", {}).get("path_count") == 10000
        return {"ok": True, "mode": "offline-stub", "recommendations": results,
                "role_receipts": "validated", "network_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_root", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.skill_root), ensure_ascii=False))
