from scripts.run_teacher_intervention_lifecycle import assignment_for_index


def test_intervention_assignment_is_balanced_within_side() -> None:
    rows = [(index % 2, assignment_for_index(index, "intervene")) for index in range(64)]
    for side in (0, 1):
        own = [arm for assigned_side, arm in rows if assigned_side == side]
        assert own.count("control") == own.count("treatment") == 16


def test_collection_has_no_treatment_assignment() -> None:
    assert {assignment_for_index(index, "collect") for index in range(4)} == {"control"}
