"""Run the installed Skill on a retained original, with current host reasoning.

This is a real-material, host-authored development study, not a synthetic model
replay or an App model-quality test. No network, credentials or old task writes.
"""

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import tempfile
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[2]
INSTALLATION = ROOT / ".skills/researchhelper"
PRIOR = ROOT / "result/acceptance/current-authorized-history-validation/isolated-lpr-2moea2w3"
BASE = ROOT / "result/host-research/lpr-20250520"
ORIGINAL_HASH = "635db43b1a562ac39225acde2faf31b0669b3f96d29ac5bfced16cbc66f1979c"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frozen_files(root):
    return {str(path): digest(path) for path in root.rglob("*") if path.is_file()}


def run(*, candidate=False):
    if not isinstance(candidate, bool):
        raise ValueError("Candidate mode must be explicit")
    protected = frozen_files(PRIOR)
    installed = frozen_files(INSTALLATION)
    database = PRIOR / "data/researchhelper/researchhelper.sqlite3"
    with sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
        old = json.loads(connection.execute("SELECT body FROM tasks WHERE id=?",
            ("rh_d4eef63071ca40ec8ec8c3222a03d46b",)).fetchone()[0])
    original = old["materials"][0]
    payload = base64.b64decode(original["original_base64"], validate=True)
    assert hashlib.sha256(payload).hexdigest() == ORIGINAL_HASH
    BASE.mkdir(parents=True, exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="study-", dir=BASE))
    installation = INSTALLATION
    if candidate:
        with zipfile.ZipFile(ROOT / "dist/0.10.0/ResearchHelper-Skill-0.10.0.zip") as bundle:
            assert all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in bundle.namelist())
            bundle.extractall(project / "candidate-installation")
        installation = project / "candidate-installation/researchhelper"
        shutil.copyfile(ROOT / "core/src/researchhelper/engine.py", installation / "researchhelper/engine.py")
    module = importlib.util.spec_from_file_location("installed_retained_lpr_session", installation / "scripts/session.py")
    installed_session = importlib.util.module_from_spec(module)
    module.loader.exec_module(installed_session)
    from researchhelper.planning import question_specs
    assert Path(sys.modules["researchhelper"].__file__).is_relative_to(installation)
    try:
        with patch.object(socket.socket, "connect", side_effect=AssertionError("No external calls in this study")):
            session = installed_session.Session(project)
            assert session.agent.model is None and session.agent.offline
            task = session.create(old["question"], entry=old["entry"], intent=old["intent"],
                objects=old["objects"], cutoff=old["cutoff"], questions=question_specs(old), parent=old["id"])
            material = session.material(task["id"], original["name"], payload)
            catalog = session.sources(task["id"])
            requests = [{"artifact_id": material["id"], "snapshot_hash": material["snapshot_hash"], "queries": [],
                "parts": [{"part": part["locator"], "start": 0, "end": len(part["text"])} for part in material["parts"]],
                "reason": "逐段审阅本次留存HTML的全部可提取静态文字，核对报价、发布文字及信息边界",
                "question_ids": ["rates", "margin"]}]
            reading = session.read(task["id"], requests, catalog["binding"])
            (project / "reading.json").write_text(json.dumps(reading, ensure_ascii=False, indent=2))
            parts = {part["locator"]: part["text"] for part in material["parts"]}
            assert set(parts) == {"标题1", "段落1", "段落2", "段落3", "段落4"}
            assert "1年期LPR为3.0%" in parts["段落4"] and "5年期以上LPR为3.5%" in parts["段落4"]
            assert all(term not in "\n".join(parts.values()) for term in ("净息差", "重定价", "存款利率"))
            tools = session.tools(task["id"])
            requirements = tools["evidence"]["question_requirements"]
            publication = {"at": "2025-05-20", "basis": "留存标题明确载明同日受权公布公告，按日期精度核验，不将无时区分钟文字补成秒精度", 
                "precision": "日", "timezone": "Asia/Shanghai", "certainty": "已确认",
                "basis_ref": {"kind": "material", "artifact_id": material["id"], "snapshot_hash": material["snapshot_hash"],
                    "part": "标题1", "quote": parts["标题1"]}}
            def spec(ident, question, part, need_texts, *, token=None):
                review_ids = [need["id"] for need in requirements[question] if need["text"] in need_texts]
                assert len(review_ids) == len(need_texts)
                value = {"id": ident, "artifact_id": material["id"], "object": "LPR", "text": parts[part],
                    "record_locator": {"part": part}, "published_at": "2025-05-20", "publication": publication,
                    "period": "2025-05-20", "nature": "已实现事实", "unit": "%" if token else "不适用",
                    "question_ids": [question], "review": {"question_ids": [question], "allowed_natures": ["已实现事实"],
                        "conditions": ["只核对本次留存HTML可提取的静态全文，不证明原站动态正文或银行实际经营变化", 
                            "不引入行情、前值、银行报表或其他来源，不把未披露当作实际不变"],
                        "reason": "宿主已阅读全部静态页段；该逐字摘录与指定需求对应，发布时间与报价分别核验", 
                        "evidence_need_ids": {question: review_ids}}}
                if token:
                    value.update(raw_token=token, raw_unit="%", raw_value=float(token), value=float(token), conversion_factor=1)
                return value
            specifications = [
                spec("lpr_1y", "rates", "段落4", ["一年期LPR"], token="3.0"),
                spec("lpr_5y", "rates", "段落4", ["五年期以上LPR"], token="3.5"),
                spec("publication_text", "rates", "段落2", ["公告页面所载发布日期与发布时间文字（2025-05-20 09:00）"]),
                spec("announcement_scope", "margin", "段落4", [
                    "公告中的LPR水平", "公告正文中除LPR水平外是否载明银行净息差、资产负债重定价或存贷款执行利率等相关信息"]),
            ]
            checkpoint = session.run(task["id"], evidence_specs=specifications, reading_hash=reading["reading_hash"], defer_delivery=True)
            assert checkpoint["host_checkpoint"] and not checkpoint["delivery"]
            tools = session.tools(task["id"])
            assert set(tools["analysis"]["by_question"]["rates"]["evidence_ids"]) == {"lpr_1y", "lpr_5y", "publication_text"}
            assert tools["analysis"]["by_question"]["margin"]["evidence_ids"] == ["announcement_scope"]
            margin = ("仅依据该公告不能识别银行净息差变化方向。留存正文只列出LPR报价及有效期，"
                      "没有载明银行净息差、资产负债重定价或存贷款执行利率。上升、下降和不变在本次材料范围内均无法排除。"
                      "这是对该材料识别边界的回答，不代表银行净息差没有变化，也不构成买卖建议。")
            answers = {"rates": {"refs": ["lpr_1y", "lpr_5y", "publication_text"], "calculation_refs": [],
                "numeric_segments": {"answer": [{"text": "该公告载明：一年期LPR为"},
                    {"source": "evidence", "ref": "lpr_1y", "path": "value", "format": "signed", "digits": 1},
                    {"text": "，五年期以上LPR为"},
                    {"source": "evidence", "ref": "lpr_5y", "path": "value", "format": "signed", "digits": 1},
                    {"text": "。页面载有2025-05-20 09:00的时间文字；公开时点按日期精度核验，不据此证明盘中可得性。"}]}},
                "margin": {"answer": margin, "refs": ["announcement_scope"], "calculation_refs": []}}
            theses = [{"title": "LPR公告不足以识别银行净息差方向", "judgment": "证据不足", "question_ids": ["margin"],
                "support": ["announcement_scope"], "counter": [], "calculation_refs": [],
                "investment_meaning": "不能从该公告报价直接推出银行盈利变化、估值变化或买卖结论。", 
                "limitation": "本次范围仅限该留存原件。相反方向均不能排除；未取得其他银行经营或重定价依据，不推定净方向。",
                "tracking": None}]
            final = session.run(task["id"], evidence_specs=specifications, answers=answers, theses=theses,
                reading_hash=reading["reading_hash"])
            assert final["status"] == "已完成", final.get("error") or final["gaps"]
            assert all(question["status"] == "已回答" for question in final["questions"])
            assert final["ledger"]["used"]["model_calls"] == final["ledger"]["used"]["data_requests"] == 0
            assert not final["synthetic"] and final["run_mode"] == "host"
            report = session.export(task["id"])
            final = session.status(task["id"])
            record = {"status": "host_research_completed", "project": str(project), "task_id": final["id"],
                "research_version": final["version"], "report": str(report), "report_sha256": digest(report),
                "material_sha256": ORIGINAL_HASH, "retained_from": old["id"], "prior_task_unchanged": True,
                "host": "current Codex Agent", "external_model_api_calls": 0, "supplier_calls": 0,
                "skill_runtime": "isolated candidate with engine source overlay" if candidate else "installed release",
                "candidate_source_sha256": digest(ROOT / "core/src/researchhelper/engine.py") if candidate else None,
                "candidate_manifest_verified": not candidate,
                "host_usage_accounted_by": "Codex conversation, not App model ledger", "synthetic": False,
                "app_autonomous_research_verified": False, "independent_quality_review": False,
                "reading_scope": "全部五个静态提取页段，不执行HTML或确认原站动态内容", 
                "question_answers": {q["id"]: q["answer"] for q in final["questions"]},
                "evidence_count": len(final["evidence"]), "thesis_count": len(final["theses"]),
                "ledger": final["ledger"], "foreground_opened": False, "production_store_touched": False}
            (project / "receipt.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
            return record
    finally:
        assert frozen_files(PRIOR) == protected, "Original failed study changed"
        assert frozen_files(INSTALLATION) == installed, "Installed Skill changed"
