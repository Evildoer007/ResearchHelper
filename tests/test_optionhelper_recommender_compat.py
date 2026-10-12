from core.optionhelper_recommender_worker import recommendation_request_body


def test_non_restrictive_full_loss_limit_is_deferred_to_formal_quote():
    body = recommendation_request_body(
        "看涨 561160.SH，3个月，最大损失100%，推荐结构",
        {"underlying": "561160.SH", "max_loss": "100%", "horizon": "3个月"},
    )
    assert "max_loss" not in body["constraints"]
    assert "最大损失100%" not in body["prompt"]
    assert body["constraints"]["underlying"] == "561160.SH"


def test_restrictive_loss_limit_is_kept_for_optionhelper_validation():
    body = recommendation_request_body(
        "最大损失20%",
        {"underlying": "561160.SH", "max_loss": "20%"},
    )
    assert body["constraints"]["max_loss"] == "20%"
    assert "最大损失20%" in body["prompt"]
