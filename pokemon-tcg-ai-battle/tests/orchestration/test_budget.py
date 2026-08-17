import pytest

from scripts.orchestration.budget import Budget, BudgetExceeded


def _limits() -> dict[str, int]:
    return {
        "max_tasks": 2,
        "max_provider_calls": 1,
        "max_repair_attempts_per_task": 1,
        "max_elapsed_seconds": 10,
        "max_prompt_bytes_per_call": 10,
        "max_diff_lines_for_auto_integration": 20,
    }


def test_budget_rejects_provider_before_charge() -> None:
    budget = Budget(_limits(), provider_calls=1)
    with pytest.raises(BudgetExceeded, match="MAX_PROVIDER_CALLS"):
        budget.check(additional_provider_calls=1)


def test_budget_enforces_prompt_per_call_and_elapsed() -> None:
    budget = Budget(_limits())
    with pytest.raises(BudgetExceeded, match="MAX_PROMPT"):
        budget.check(prompt_bytes_for_call=11)
    with pytest.raises(BudgetExceeded, match="MAX_ELAPSED"):
        budget.check(elapsed_seconds=10)


def test_budget_records_unknown_proxy_or_exact_usage() -> None:
    budget = Budget(_limits())
    budget.charge_provider(7)
    unknown = budget.to_dict()
    assert unknown["token_usage"] == "unknown"
    assert unknown["proxy_usage"] == {
        "provider_calls": 1,
        "prompt_bytes": 7,
        "elapsed_seconds": 0.0,
    }
    budget.record_usage(3, 5)
    assert budget.to_dict()["token_usage"]["total_tokens"] == 8


def test_budget_keeps_known_usage_separate_after_an_unknown_call() -> None:
    budget = Budget(_limits())
    budget.record_usage(3, 5)
    budget.record_usage(None, None)
    value = budget.to_dict()
    assert value["token_usage"] == "unknown"
    assert value["known_measured_usage"] == {
        "input_tokens": 3,
        "output_tokens": 5,
        "total_tokens": 8,
    }


def test_legacy_budget_with_exact_counts_restores_as_incomplete() -> None:
    legacy = Budget(_limits(), exact_input_tokens=3, exact_output_tokens=5).to_dict()
    legacy.pop("token_usage_complete")
    restored = Budget.from_dict(legacy).to_dict()
    assert restored["token_usage"] == "unknown"
    assert restored["known_measured_usage"] == {
        "input_tokens": 3,
        "output_tokens": 5,
        "total_tokens": 8,
    }
