import pytest
from scripts.orchestration.reviewer import parse_review, review_allows_integration


def test_reviewer_requires_exact_low_pass_schema() -> None:
    assert review_allows_integration(parse_review('{"verdict":"PASS","risk":"LOW","findings":[],"auto_integration_allowed":true}'))
    with pytest.raises(ValueError): parse_review('{"verdict":"PASS"}')


@pytest.mark.parametrize(
    "payload",
    [
        '{"verdict":"PASS_WITH_NOTES","risk":"LOW","findings":[],"auto_integration_allowed":true}',
        '{"verdict":"PASS","risk":"MEDIUM","findings":[],"auto_integration_allowed":true}',
        '{"verdict":"PASS","risk":"HIGH","findings":[],"auto_integration_allowed":true}',
        '{"verdict":"REJECT","risk":"LOW","findings":[],"auto_integration_allowed":true}',
    ],
)
def test_non_clean_low_pass_cannot_integrate(payload: str) -> None:
    assert review_allows_integration(parse_review(payload)) is False
