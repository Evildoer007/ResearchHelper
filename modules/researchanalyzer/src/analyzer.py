"""Research applicability checks, separate from original-source extraction.

The host Agent supplies the financial reading and its conditions. This module
checks those recorded obligations; it never turns a disclosure into a realized
result, guesses publication times, or approves a provider's access rights.
"""

from __future__ import annotations

import copy

from .contracts import (ContractError, DOCUMENT_CONTENT_LIMITATION, timepoint, validate_evidence_usage,
                        validate_question_coverage)
from .evidence import (NATURES, CURRENT_SNAPSHOT_LIMITATION, CURRENT_ORIGINAL_LIMITATION, DIRECT_CODE_LIMITATION,
                       CANDIDATE_CODE_LIMITATION, validate_candidate_code_identity,
                       validate_direct_code_identity, validate_current_snapshot, verification_receipt)


class ResearchAnalyzer:
    @staticmethod
    def _review(selected, allowed, conditions, review, coverage):
        """Preserve explicit submitted text and bind it to canonical obligations."""
        result = {"question_ids": list(selected), "allowed_natures": list(allowed),
                  "conditions": list(conditions), "reason": review['reason'].strip()}
        if coverage:
            result['evidence_need_ids'] = coverage
        if 'evidence_need_texts' in review:
            result['evidence_need_texts'] = copy.deepcopy(review['evidence_need_texts'])
        if 'temporal_use' in review:
            result['temporal_use'] = review['temporal_use']
        return result

    def assess(self, task, item, review, records=()):
        if item.get("source_check") != "通过":
            raise ContractError("研究适用性不能替代来源与提取核验")
        if not isinstance(review, dict) or not isinstance(review.get("reason"), str) or not review["reason"].strip():
            raise ContractError("适用性检查缺少采用依据")
        selected = item.get("question_ids")
        questions = {q["id"] for q in task["questions"]}
        if not isinstance(selected, list) or not selected or not all(isinstance(x, str) and x in questions for x in selected) or set(review.get("question_ids", [])) != set(selected):
            raise ContractError("材料适用性必须绑定本任务的实际研究问题")
        allowed = review.get("allowed_natures")
        if not isinstance(allowed, list) or not allowed or not set(allowed).issubset(NATURES) or item["nature"] not in allowed:
            raise ContractError("材料性质不适用于当前问题，预测不冒充已实现事实")
        conditions = review.get("conditions")
        if not isinstance(conditions, list) or not conditions or not all(isinstance(x, str) and x.strip() for x in conditions):
            raise ContractError("适用性检查须记录范围、口径或附注条件")
        coverage = validate_question_coverage(task, selected, review)
        from .data_requirements import validate_links
        validate_links(task,item,coverage)
        from .statistical_metrics import validate_level
        validate_level(item, task, coverage)
        if validate_current_snapshot(item, task):
            limitations = [CURRENT_SNAPSHOT_LIMITATION]
            if item['availability']['kind'] == 'current_original':
                if review.get('temporal_use') != 'current_version':
                    raise ContractError('首次公开时间未知的当前原件只可用于当前版本研究，不能批准历史或事件因果用途')
                limitations.append(CURRENT_ORIGINAL_LIMITATION)
                for record in records:
                    relation = record.get('directory', {}).get('relation', {})
                    if (record.get('task_id') == task['id'] and record.get('research_version') == task['version']
                            and relation.get('target') == item['artifact']['id']
                            and relation.get('kind') in ('更正', '撤回', '替代')):
                        raise ContractError('当前原件已有明确更正、撤回或替代关系，须重新评估，不继续采用旧依据')
            if item.get('identity', {}).get('mode') == 'direct_provider_observation':
                validate_direct_code_identity(item, task, item['artifact'])
                limitations.append(DIRECT_CODE_LIMITATION)
            if item.get('identity', {}).get('mode') == 'candidate_provider_observation':
                validate_candidate_code_identity(item, task, item['artifact'])
                limitations.append(CANDIDATE_CODE_LIMITATION)
            item["applicability_check"] = "通过"
            item["applicability_review"] = self._review(selected, allowed,
                list(dict.fromkeys(conditions + limitations)), review, coverage)
            item["applicability_receipt"] = verification_receipt(item, task, "ResearchAnalyzer", "通过", review["reason"].strip())
            return item
        if validate_evidence_usage(item, task, item.get("artifact")):
            item["applicability_check"] = "通过"
            item["applicability_review"] = self._review(selected, allowed,
                list(dict.fromkeys(conditions + [DOCUMENT_CONTENT_LIMITATION])), review, coverage)
            item["applicability_receipt"] = verification_receipt(item, task, "ResearchAnalyzer", "通过", review["reason"].strip())
            return item
        publication = item.get("publication")
        if not isinstance(publication, dict) or publication.get("at") != item["published_at"] or not publication.get("basis"):
            raise ContractError("缺少首次公开时间的独立依据，获取时间和所属期不能替代")
        precision = "日" if len(item["published_at"]) == 10 else "秒"
        if publication.get("precision") != precision or publication.get("timezone") not in ("Asia/Shanghai", "UTC") or publication.get("certainty") != "已确认":
            raise ContractError("公开时间的精度、时区或确定性未核验")
        published = timepoint(item["published_at"])
        expected_offset = 28800 if publication["timezone"] == "Asia/Shanghai" else 0
        if published.utcoffset().total_seconds() != expected_offset:
            raise ContractError("公开时间与声明时区不一致")
        if published > timepoint(task["cutoff"]):
            raise ContractError("材料首次可得时间晚于研究截止时点")
        artifact = item["artifact"]
        if artifact["kind"] == "api" and len(item["period"]) == 10 and published.date().isoformat() < item["period"]:
            raise ContractError("日线数据不能在其观测日之前已经公开")
        if artifact["kind"] == "filing":
            for record in records:
                if record.get("kind") != "filing":
                    continue
                directory = record["directory"]
                relation = directory.get("relation", {})
                if relation.get("target") == artifact["id"] and relation.get("kind") in ("更正", "撤回", "替代") and timepoint(directory["published_at"]) <= timepoint(task["cutoff"]):
                    raise ContractError("截止时点前已有更正、撤回或替代，原依据须重新评估")
        item["applicability_check"] = "通过"
        item["applicability_review"] = self._review(selected, allowed, conditions, review, coverage)
        item["applicability_receipt"] = verification_receipt(item, task, "ResearchAnalyzer", "通过", review["reason"].strip())
        return item
