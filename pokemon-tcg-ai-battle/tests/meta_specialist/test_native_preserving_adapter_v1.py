import pytest

from mage_ptcg.meta_specialist.native_preserving_adapter_v1 import (
    NativePreservingAdapterError,
    NativeScoreConfigV1,
    NativePreservingPolicyV1,
    build_native_guarded_score_policy_v1,
    build_native_score_policy_v1,
    load_native_module_v1,
)


def _selection(options, *, minimum=1, maximum=1):
    return {"select": {"option": list(options), "minCount": minimum, "maxCount": maximum}}


def test_eligible_override_changes_action_but_native_is_the_baseline():
    calls = []

    def native(obs):
        calls.append("native")
        return [0]

    def override(obs, native_action):
        assert native_action == [0]
        return [1]

    policy = NativePreservingPolicyV1(
        native_agent=native,
        override=override,
        eligibility=lambda obs: True,
        baseline_policy_sha256="a" * 64,
        candidate_config_sha256="b" * 64,
    )
    assert policy(_selection([{}, {}])) == [1]
    assert calls == ["native"]
    assert policy.snapshot().override_applied == 1
    assert policy.snapshot().native_calls == 1


def test_unknown_or_invalid_override_falls_back_exactly_to_native():
    def native(obs):
        return [0]

    for override in (
        lambda obs, action: [99],
        lambda obs, action: [0, 0],
        lambda obs, action: (_ for _ in ()).throw(RuntimeError("bad")),
        lambda obs, action: None,
    ):
        policy = NativePreservingPolicyV1(
            native_agent=native,
            override=override,
            eligibility=lambda obs: True,
            baseline_policy_sha256="a" * 64,
            candidate_config_sha256="b" * 64,
        )
        assert policy(_selection([{}, {}])) == [0]
        assert policy.snapshot().fallbacks == 1


def test_override_is_not_called_for_deck_registration_or_ineligible_state():
    seen = []

    def override(obs, action):
        seen.append(obs)
        return [0]

    policy = NativePreservingPolicyV1(
        native_agent=lambda obs: [10, 11],
        override=override,
        eligibility=lambda obs: False,
        baseline_policy_sha256="a" * 64,
        candidate_config_sha256="b" * 64,
    )
    assert policy({"select": None}) == [10, 11]
    assert policy(_selection([{}, {}])) == [10, 11]
    assert seen == []
    assert policy.snapshot().skipped == 2


def test_metadata_hashes_are_strict():
    with pytest.raises(NativePreservingAdapterError, match="SHA"):
        NativePreservingPolicyV1(
            native_agent=lambda obs: [0],
            override=lambda obs, action: [0],
            eligibility=lambda obs: True,
            baseline_policy_sha256="bad",
            candidate_config_sha256="b" * 64,
        )


class _Option:
    def __init__(self, kind):
        self.type = kind


class _Obs:
    def __init__(self, kinds, context="MAIN"):
        self.select = type(
            "Select",
            (),
            {"option": [_Option(kind) for kind in kinds], "minCount": 1, "maxCount": 1, "context": context},
        )()


class _ScoreModule:
    @staticmethod
    def to_observation_class(value):
        return value

    @staticmethod
    def score_option(obs, option):
        return (10.0 if option.type == "PLAY" else 0.0, option.type)


def test_native_score_policy_uses_module_scores_only_in_eligible_context():
    config = NativeScoreConfigV1.from_mapping({"ATTACK": 25.0, "PLAY": -25.0})
    policy = build_native_score_policy_v1(
        native_agent=lambda obs: [0],
        native_module=_ScoreModule,
        config=config,
        baseline_policy_sha256="a" * 64,
    )
    obs = _selection([{}, {}])
    obs["select"]["context"] = "MAIN"
    obs["_native_object"] = _Obs(["PLAY", "ATTACK"])
    assert policy(obs) == [1]
    non_main = _selection([{}, {}])
    non_main["_native_object"] = _Obs(["PLAY", "ATTACK"], context="DISCARD")
    assert policy(non_main) == [0]


def test_guarded_score_policy_keeps_native_when_gain_is_below_threshold():
    config = NativeScoreConfigV1.from_mapping({"ATTACK": 5.0})

    class Module:
        @staticmethod
        def to_observation_class(value):
            return value

        @staticmethod
        def score_option(obs, option):
            return option.value

    class Option:
        def __init__(self, value, kind="PLAY"):
            self.value = value
            self.type = kind

    native_object = _Obs(["PLAY", "ATTACK"])
    native_object.select.option[0].value = 100.0
    native_object.select.option[1].value = 100.0
    policy = build_native_guarded_score_policy_v1(
        native_agent=lambda obs: [0],
        native_module=Module,
        config=config,
        baseline_policy_sha256="a" * 64,
        min_score_gain=10.0,
    )
    # Replace the fixture options with score-bearing objects after building the
    # observation; the wrapper must preserve the native action for a small gain.
    native_object.select.option = [Option(100.0), Option(104.0, "ATTACK")]
    obs = _selection([{}, {}])
    obs["select"]["context"] = "MAIN"
    obs["_native_object"] = native_object
    assert policy(obs) == [0]
    assert policy.snapshot().fallbacks == 1


def test_guarded_score_policy_applies_only_a_large_single_main_gain():
    config = NativeScoreConfigV1.from_mapping({"ATTACK": 5.0})

    class Module:
        @staticmethod
        def to_observation_class(value):
            return value

        @staticmethod
        def score_option(obs, option):
            return option.value

    class Option:
        def __init__(self, value, kind="PLAY"):
            self.value = value
            self.type = kind

    native_object = _Obs(["PLAY", "ATTACK"])
    native_object.select.option = [Option(100.0), Option(130.0, "ATTACK")]
    policy = build_native_guarded_score_policy_v1(
        native_agent=lambda obs: [0],
        native_module=Module,
        config=config,
        baseline_policy_sha256="a" * 64,
        min_score_gain=10.0,
    )
    obs = _selection([{}, {}])
    obs["select"]["context"] = "MAIN"
    obs["_native_object"] = native_object
    assert policy(obs) == [1]
    assert policy.snapshot().override_applied == 1


def test_guarded_score_policy_fails_closed_for_multiselect_and_bad_native_action():
    config = NativeScoreConfigV1.from_mapping({"ATTACK": 5.0})

    class Module:
        @staticmethod
        def to_observation_class(value):
            return value

        @staticmethod
        def score_option(obs, option):
            return 100.0

    native_object = _Obs(["PLAY", "ATTACK"])
    multi = _selection([{}, {}], minimum=1, maximum=2)
    multi["select"]["context"] = "MAIN"
    multi["_native_object"] = native_object
    policy = build_native_guarded_score_policy_v1(
        native_agent=lambda obs: [0, 1],
        native_module=Module,
        config=config,
        baseline_policy_sha256="a" * 64,
    )
    assert policy(multi) == [0, 1]
    assert policy.snapshot().skipped == 1


def test_native_module_loader_isolated_and_requires_agent(tmp_path):
    path = tmp_path / "main.py"
    path.write_text("def agent(obs):\n    return [0]\n", encoding="utf-8")
    module = load_native_module_v1(path)
    assert callable(module.agent)
    bad = tmp_path / "bad.py"
    bad.write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(NativePreservingAdapterError, match="agent"):
        load_native_module_v1(bad)
