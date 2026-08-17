"""Permission tests: default deny, intersection, and fail-closed parsing."""

from __future__ import annotations

import pytest

from mage_ptcg.competition_intelligence.contracts import (
    SOURCE_ENVELOPE_SCHEMA_VERSION,
    AcquisitionMode,
    AllowedUse,
    ContractError,
    SourceEnvelope,
    SourceKind,
)
from mage_ptcg.competition_intelligence.permissions import (
    DEFAULT_ALLOWED_USES,
    filter_for_use,
    has_all_permissions,
    has_permission,
    intersect_allowed_uses,
    require_permission,
    validate_allowed_uses_subset,
)


def _envelope(source_kind: SourceKind, allowed_uses: frozenset[AllowedUse]) -> SourceEnvelope:
    return SourceEnvelope(
        schema_version=SOURCE_ENVELOPE_SCHEMA_VERSION,
        source_id=f"src:{source_kind.value}",
        source_kind=source_kind,
        acquisition_mode=AcquisitionMode.LOCAL_ONLY,
        acquired_at="2026-07-18T00:00:00Z",
        observed_at=None,
        origin_reference="x",
        owner_scope="self",
        visibility="private",
        allowed_uses=allowed_uses,
        terms_snapshot_hash=None,
        raw_sha256="a" * 64,
        parser_version="v1",
        redaction_version="v1",
    )


class TestDefaultDeny:
    def test_public_other_default_is_archive_only(self) -> None:
        assert DEFAULT_ALLOWED_USES[SourceKind.PUBLIC_OTHER] == frozenset({AllowedUse.ARCHIVE})

    def test_team_shared_default_is_empty_requires_explicit_manifest(self) -> None:
        assert DEFAULT_ALLOWED_USES[SourceKind.TEAM_SHARED] == frozenset()

    def test_missing_permission_only_grants_archive_for_public_other(self) -> None:
        envelope = _envelope(SourceKind.PUBLIC_OTHER, frozenset({AllowedUse.ARCHIVE}))
        assert has_permission(envelope, AllowedUse.ARCHIVE)
        assert not has_permission(envelope, AllowedUse.ANALYSIS)
        assert not has_permission(envelope, AllowedUse.TRAINING)


class TestPermissionChecks:
    def test_require_permission_raises_when_missing(self) -> None:
        envelope = _envelope(SourceKind.PUBLIC_OTHER, frozenset({AllowedUse.ARCHIVE}))
        with pytest.raises(PermissionError):
            require_permission(envelope, AllowedUse.TRAINING, purpose="dataset export")

    def test_require_permission_passes_when_granted(self) -> None:
        envelope = _envelope(SourceKind.LOCAL_SELFPLAY, frozenset({AllowedUse.ARCHIVE, AllowedUse.TRAINING}))
        require_permission(envelope, AllowedUse.TRAINING, purpose="dataset export")

    def test_has_all_permissions(self) -> None:
        envelope = _envelope(SourceKind.LOCAL_SELFPLAY, frozenset({AllowedUse.ARCHIVE, AllowedUse.ANALYSIS}))
        assert has_all_permissions(envelope, [AllowedUse.ARCHIVE, AllowedUse.ANALYSIS])
        assert not has_all_permissions(envelope, [AllowedUse.ARCHIVE, AllowedUse.TRAINING])


class TestIntersection:
    def test_intersect_of_two_sources_is_the_narrower_one(self) -> None:
        a = _envelope(SourceKind.LOCAL_SELFPLAY, frozenset({AllowedUse.ARCHIVE, AllowedUse.ANALYSIS, AllowedUse.TRAINING}))
        b = _envelope(SourceKind.PUBLIC_OTHER, frozenset({AllowedUse.ARCHIVE}))
        assert intersect_allowed_uses([a, b]) == frozenset({AllowedUse.ARCHIVE})

    def test_intersect_of_empty_iterable_is_empty(self) -> None:
        assert intersect_allowed_uses([]) == frozenset()

    def test_public_other_never_leaks_training_into_a_mixed_snapshot(self) -> None:
        local = _envelope(SourceKind.LOCAL_SELFPLAY, frozenset({AllowedUse.ARCHIVE, AllowedUse.TRAINING}))
        public = _envelope(SourceKind.PUBLIC_OTHER, frozenset({AllowedUse.ARCHIVE}))
        joint = intersect_allowed_uses([local, public])
        assert AllowedUse.TRAINING not in joint

    def test_filter_for_use(self) -> None:
        granted = _envelope(SourceKind.LOCAL_SELFPLAY, frozenset({AllowedUse.ARCHIVE, AllowedUse.TRAINING}))
        denied = _envelope(SourceKind.PUBLIC_OTHER, frozenset({AllowedUse.ARCHIVE}))
        result = filter_for_use([granted, denied], AllowedUse.TRAINING)
        assert result == [granted]


class TestValidateAllowedUsesSubset:
    def test_parses_known_names(self) -> None:
        result = validate_allowed_uses_subset(["ARCHIVE", "ANALYSIS"])
        assert result == frozenset({AllowedUse.ARCHIVE, AllowedUse.ANALYSIS})

    def test_rejects_unknown_name_as_contract_error_not_value_error(self) -> None:
        with pytest.raises(ContractError):
            validate_allowed_uses_subset(["ARCHIVE", "NOT_A_REAL_USE"])
