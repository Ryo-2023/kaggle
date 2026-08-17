import pytest

from scripts.orchestration.model_router import route_task
from scripts.orchestration.overnight_plan import ModelRoute


ROUTING = {
    "economy": ModelRoute("economy-model", "low"),
    "standard": ModelRoute("standard-model", "medium"),
    "deep": ModelRoute("deep-model", "high"),
}


@pytest.mark.parametrize(
    ("kwargs", "tier", "model", "effort"),
    [
        ({"complexity": "simple", "risk": "low"}, "economy", "economy-model", "low"),
        ({"complexity": "normal", "risk": "low"}, "standard", "standard-model", "medium"),
        ({"complexity": "complex", "risk": "low"}, "deep", "deep-model", "high"),
        ({"complexity": "algorithm", "risk": "low"}, "deep", "deep-model", "high"),
        ({"complexity": "simple", "risk": "low", "large_diff": True}, "deep", "deep-model", "high"),
        ({"complexity": "simple", "risk": "low", "control_plane": True}, "deep", "deep-model", "high"),
    ],
)
def test_routing_uses_selected_tier_profile(kwargs, tier, model, effort) -> None:
    result = route_task(ROUTING, **kwargs)
    assert (result.tier, result.model, result.reasoning_effort) == (tier, model, effort)
    assert result.fallback is None


def test_repair_escalates_actual_profile_and_review_is_separate() -> None:
    repair = route_task(ROUTING, complexity="simple", risk="low", repair=True)
    review = route_task(ROUTING, complexity="normal", risk="low", review=True)
    assert (repair.tier, repair.model, repair.reasoning_effort) == (
        "standard",
        "standard-model",
        "medium",
    )
    assert "independent-review" in review.reasons
