from pathlib import Path


def test_broad_reference_config_is_closed_and_excludes_smoke_false() -> None:
    from scripts.measure_v4_checkpoint_broad_arena_v1 import broad_opponent_ids_v1

    ids, source_sha = broad_opponent_ids_v1()
    assert len(ids) == 24
    assert len(set(ids)) == 24
    assert "public_archaludon_cinderace_r7" not in ids
    assert len(source_sha) == 64


def test_broad_wrapper_has_explicit_research_only_boundary() -> None:
    from scripts.measure_v4_checkpoint_broad_arena_v1 import BROAD_ARENA_SCHEMA_V1

    assert BROAD_ARENA_SCHEMA_V1 == "meta-specialist-v4-broad-arena-checkpoint-strength-v1"
