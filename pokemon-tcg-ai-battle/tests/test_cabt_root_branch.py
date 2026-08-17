from __future__ import annotations

from mage_ptcg.optimization.cabt_root_branch import NativeBranchTrial, assess_trials


def _trial(after: str, *, root: str = "root", actor: str = "actor", legal: str = "legal") -> NativeBranchTrial:
    return NativeBranchTrial(0, root, actor, legal, after, "after-actor", "after-legal")


def test_rng_divergence_keeps_native_fork_diagnostic_only() -> None:
    result = assess_trials(root_full_digest="root", root_actor_digest="actor", root_legal_digest="legal",
                           parent_after_full_digest="root", repeated=[_trial("a"), _trial("b")],
                           alternate=_trial("c"), rng_continuation="UNCONTROLLED_NATIVE_RANDOM_DEVICE")
    assert result.status == "DIAGNOSTIC_ONLY"
    assert result.prebranch_equal and result.parent_isolated
    assert not result.same_action_deterministic


def test_only_identical_rng_continuation_can_enable_limited_ctde() -> None:
    result = assess_trials(root_full_digest="root", root_actor_digest="actor", root_legal_digest="legal",
                           parent_after_full_digest="root", repeated=[_trial("a"), _trial("a")],
                           alternate=_trial("b"), rng_continuation="PROVEN_IDENTICAL")
    assert result.status == "CTDE_READY_WITH_LIMITATIONS"
    assert result.different_action_diverged


def test_child_failure_is_unsafe() -> None:
    failed = NativeBranchTrial(3, None, None, None, None, None, None, "RuntimeError")
    result = assess_trials(root_full_digest="root", root_actor_digest="actor", root_legal_digest="legal",
                           parent_after_full_digest="root", repeated=[failed], alternate=None,
                           rng_continuation="UNCONTROLLED_NATIVE_RANDOM_DEVICE")
    assert result.status == "UNSAFE"
