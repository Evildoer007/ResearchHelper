"""Explicit synthetic originals for graph and interface checks, not financial approval."""
import copy

PUBLICATION = "发布日期：2026年7月19日。"


def policy_proposal(agent, task_id, ident, subject, text, question_ids):
    """Retain the complete local original and propose its literal paragraph."""
    task = agent.add_material(task_id, ident + ".txt", (PUBLICATION + "\n\n" + text).encode())
    record = task["materials"][-1]
    return policy_spec(record, ident, subject, text, question_ids)


def policy_spec(record, ident, subject, text, question_ids):
    """A proposal for an existing original, still pending the two real checks."""
    anchor = dict(kind="material", artifact_id=record["id"], snapshot_hash=record["snapshot_hash"],
                  part="段落1", quote=PUBLICATION)
    return dict(id=ident, artifact_id=record["id"], object=subject, text=text,
        record_locator={"part": "段落2"}, period="合成条款期", nature="合同政策条款", unit="不适用",
        question_ids=list(question_ids), published_at="2026-07-19",
        publication=dict(at="2026-07-19", precision="日", timezone="Asia/Shanghai", certainty="已确认",
                         basis="固定合成原件的明确发布日期", basis_ref=anchor),
        review=dict(question_ids=list(question_ids), allowed_natures=["合同政策条款"],
            conditions=["仅核对合成原件的明示条款与软件流程，不代表真实投资分析"],
            reason="按本任务保留的完整原件逐字核对，未填写核验通过标签"))


FEE_PARAMETERS = dict(unit="%/年", period="2026年度规则", fee_kind="management_fee")


def fee_calculation(ident="fee_delta", question_ids=("q1",)):
    return dict(id=ident, method="fee_difference_v2", inputs=dict(fee_a="e1", fee_b="e2"),
                parameters=copy.deepcopy(FEE_PARAMETERS), question_ids=list(question_ids))


def fee_originals(agent, task_id, question_ids=("q1",)):
    """Two directly disclosed single fees, not sums missing from an original."""
    specs = []
    for ident, fund, value in (("e1", "ETF-A", .2), ("e2", "ETF-B", .6)):
        text = (f"合成软件测试规则，非真实产品。{fund}，{fund}份额，2026年度规则。"
                f"管理费按基金资产净值计提，年费率为{value}%/年。发布于2026-07-19。")
        task = agent.add_material(task_id, ident + ".txt", text.encode())
        record = task["materials"][-1]
        anchor = dict(kind="material", artifact_id=record["id"], snapshot_hash=record["snapshot_hash"],
                      part="段落1", quote=text)
        context = dict(fund_id=fund, share_class=fund+"份额", period="2026年度规则",
                       fee_basis="net_assets", rate_period="annual", metric="management_fee")
        specs.append(dict(id=ident, artifact_id=record["id"], object=fund,
            record_locator={"part": "段落1"}, text=text, period="2026年度规则", nature="合同政策条款",
            raw_token=str(value), raw_value=value, value=value, raw_unit="%/年", unit="%/年", conversion_factor=1,
            published_at="2026-07-19", publication=dict(at="2026-07-19", basis="合成原件的明确日期",
                precision="日", timezone="Asia/Shanghai", certainty="已确认", basis_ref=anchor),
            calculation_context=context, calculation_context_basis={key:copy.deepcopy(anchor) for key in context},
            question_ids=list(question_ids), review=dict(question_ids=list(question_ids),
                allowed_natures=["合同政策条款"], conditions=["同期间同类合成单项费用，不推断实际收益"],
                reason="逐项核对两份合成原件的费率、份额及年度口径")))
    return specs
