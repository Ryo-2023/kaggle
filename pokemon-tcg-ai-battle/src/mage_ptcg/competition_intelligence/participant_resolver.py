"""Fail-closed replay participant classification for O3 live acquisition."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Sequence

from .contracts import SourceKind


@dataclass(frozen=True, slots=True)
class TeamIdentity:
    """Non-secret team identity supplied by environment or local configuration."""

    team_id: str | None = None
    team_name: str | None = None

    def __post_init__(self) -> None:
        if not self.team_id and not self.team_name:
            raise ValueError("at least one of team_id or team_name is required")


@dataclass(frozen=True)
class OwnSubmissionBootstrap:
    """Ephemeral proof that a submission ID came from an authenticated listing."""

    submission_id: str
    submission_source_hash: str
    authenticated_own_listing: bool = True

    def __post_init__(self) -> None:
        if not self.submission_id or len(self.submission_source_hash) != 64:
            raise ValueError("bootstrap requires submission_id and a SHA-256 listing hash")
        if not self.authenticated_own_listing:
            raise ValueError("bootstrap requires an authenticated own listing")


@dataclass(frozen=True)
class BootstrapResolution:
    identity: TeamIdentity | None
    agent_indices: tuple[int, ...]
    reason: str
    matched_submission_id: bool = False

    @property
    def identity_hash(self) -> str | None:
        if self.identity is None:
            return None
        value = f"{self.identity.team_id or ''}\x00{self.identity.team_name or ''}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ParticipantResolution:
    source_kind: SourceKind | None
    reason: str
    matched_participants: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentIndexResolution:
    agent_indices: tuple[int, ...]
    reason: str


def resolve_episode_agent_mapping(
    episode: Mapping[str, Any], bootstrap: OwnSubmissionBootstrap,
) -> BootstrapResolution:
    """Verify exactly one SDK episode side for an authenticated own submission."""
    agents = episode.get("_normalized_agents")
    if not isinstance(agents, Sequence) or isinstance(agents, (str, bytes)):
        return BootstrapResolution(None, (), "episode_agent_mapping_missing")
    matches = [agent for agent in agents if isinstance(agent, Mapping) and agent.get("submission_id") == bootstrap.submission_id]
    if len(matches) != 1:
        return BootstrapResolution(
            None, (),
            "episode_agent_mapping_missing" if not matches else "episode_agent_mapping_ambiguous",
            bool(matches),
        )
    match = matches[0]
    index = match.get("agent_index")
    if type(index) is not int or index < 0:
        return BootstrapResolution(None, (), "episode_agent_index_invalid", True)
    team_id = _scalar(match.get("team_id"))
    team_name = _scalar(match.get("team_name"))
    if not team_id and not team_name:
        return BootstrapResolution(None, (), "episode_side_identity_missing", True)
    return BootstrapResolution(
        TeamIdentity(team_id=team_id, team_name=team_name), (index,), "episode_submission_side_verified", True,
    )


def identities_compatible(expected: TeamIdentity, observed: TeamIdentity) -> bool:
    """Treat unspecified explicit identity fields as unconstrained, not unequal."""
    return (
        (expected.team_id is None or expected.team_id == observed.team_id)
        and (expected.team_name is None or expected.team_name == observed.team_name)
    )


def replay_matches_episode_agent(replay: Mapping[str, Any], identity: TeamIdentity, agent_index: int) -> bool:
    """Reject only visible replay contradictions to a verified episode mapping.

    Replay metadata need not repeat team identity.  The episode-agent record is
    authoritative for side ownership, while any Replay fields that *are*
    present must agree at the same side.
    """
    info = replay.get("info")
    if not isinstance(info, Mapping) or agent_index < 0:
        return False
    agents = info.get("Agents")
    if isinstance(agents, Sequence) and not isinstance(agents, (str, bytes)):
        if agent_index >= len(agents):
            return False
        agent = agents[agent_index]
        if isinstance(agent, Mapping):
            agent_id = _scalar(agent.get("TeamId", agent.get("teamId")))
            agent_name = _scalar(agent.get("TeamName", agent.get("teamName", agent.get("Name"))))
            if identity.team_id and agent_id and agent_id != identity.team_id:
                return False
            if identity.team_name and agent_name and agent_name != identity.team_name:
                return False
    ids = _string_values(info.get("TeamIds"))
    names = _string_values(info.get("TeamNames"))
    if identity.team_id and agent_index < len(ids) and ids[agent_index] != identity.team_id:
        return False
    if identity.team_name and agent_index < len(names) and names[agent_index] != identity.team_name:
        return False
    return True


def _string_values(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def classify_replay_participants(replay: Mapping[str, Any], identity: TeamIdentity | None) -> ParticipantResolution:
    """Return OWN only for one unambiguous exact participant identity match.

    Kaggle replay schemas vary, so unknown/missing fields, duplicate matches,
    or name-only ambiguity are quarantined rather than being upgraded to OWN.
    """
    if identity is None:
        return ParticipantResolution(None, "team_identity_missing")
    info = replay.get("info")
    if not isinstance(info, Mapping):
        return ParticipantResolution(None, "replay_info_missing")

    team_ids = _string_values(info.get("TeamIds"))
    team_names = _string_values(info.get("TeamNames"))
    agents = info.get("Agents")
    if not team_ids and not team_names and not isinstance(agents, Sequence):
        return ParticipantResolution(None, "participant_fields_missing")

    # Team ID is authoritative.  A matching name is corroboration, not a
    # second match (the prior O3 implementation incorrectly called that
    # normal situation ambiguous when both fields were configured).
    if identity.team_id:
        id_matches = tuple(f"team_id:{value}" for value in team_ids if value == identity.team_id)
        if len(id_matches) == 1:
            return ParticipantResolution(SourceKind.OWN_KAGGLE, "exact_team_id_match", id_matches)
        if len(id_matches) > 1:
            return ParticipantResolution(None, "ambiguous_team_id_match", id_matches)
    name_matches: tuple[str, ...] = ()
    if identity.team_name:
        name_matches = tuple(f"team_name:{value}" for value in team_names if value == identity.team_name)
    # A schema that offers only opaque agent records cannot prove ownership.
    if not name_matches:
        if not team_ids and not team_names:
            return ParticipantResolution(None, "participant_identity_unparseable")
        return ParticipantResolution(SourceKind.PUBLIC_OTHER, "no_exact_team_identity_match")
    if len(name_matches) != 1:
        return ParticipantResolution(None, "ambiguous_team_name_match", name_matches)
    return ParticipantResolution(SourceKind.OWN_KAGGLE, "exact_team_name_match", name_matches)


def resolve_own_agent_indices(replay: Mapping[str, Any], identity: TeamIdentity | None) -> AgentIndexResolution:
    """Resolve only the owner's agent indices; never guess a fixed seat."""
    participant = classify_replay_participants(replay, identity)
    if participant.source_kind is not SourceKind.OWN_KAGGLE or identity is None:
        return AgentIndexResolution((), "owner_not_proven")
    info = replay.get("info")
    if not isinstance(info, Mapping):
        return AgentIndexResolution((), "replay_info_missing")
    agents = info.get("Agents")
    indices: list[int] = []
    if isinstance(agents, Sequence) and not isinstance(agents, (str, bytes)):
        for index, agent in enumerate(agents):
            if not isinstance(agent, Mapping):
                continue
            team_id = agent.get("TeamId", agent.get("teamId"))
            team_name = agent.get("TeamName", agent.get("teamName"))
            if identity.team_id and team_id == identity.team_id:
                indices.append(index)
            elif not identity.team_id and identity.team_name and team_name == identity.team_name:
                indices.append(index)
    if not indices:
        # Some replay schemas expose parallel TeamIds without agent objects.
        team_ids = _string_values(info.get("TeamIds"))
        team_names = _string_values(info.get("TeamNames"))
        if identity.team_id:
            indices = [index for index, value in enumerate(team_ids) if value == identity.team_id]
        elif identity.team_name:
            indices = [index for index, value in enumerate(team_names) if value == identity.team_name]
    if len(indices) != 1:
        return AgentIndexResolution((), "agent_index_unresolved_or_ambiguous")
    return AgentIndexResolution((indices[0],), "exact_owner_agent_index")


def _scalar(value: Any) -> str | None:
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value).strip()
    return None


def _submission_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(item for item in (_scalar(v) for v in value) if item is not None)
    item = _scalar(value)
    return (item,) if item else ()


def _mapping_candidates(replay: Mapping[str, Any], submission_id: str) -> list[int]:
    """Find side indices only from explicit submission-to-agent fields."""
    info = replay.get("info") if isinstance(replay.get("info"), Mapping) else replay
    candidates: list[int] = []
    for key in ("SubmissionIds", "submissionIds", "submission_ids", "SubmissionId", "submissionId"):
        values = _submission_values(info.get(key)) if isinstance(info, Mapping) else ()
        candidates.extend(index for index, value in enumerate(values) if value == submission_id)
    agents = info.get("Agents") if isinstance(info, Mapping) else None
    if isinstance(agents, Sequence) and not isinstance(agents, (str, bytes)):
        for index, agent in enumerate(agents):
            if not isinstance(agent, Mapping):
                continue
            for key in ("SubmissionId", "submissionId", "submission_id", "SubmissionIds", "submissionIds"):
                if submission_id in _submission_values(agent.get(key)):
                    candidates.append(index)
                    break
    return sorted(set(candidates))


def bootstrap_identity_from_replay(
    replay: Mapping[str, Any], bootstrap: OwnSubmissionBootstrap,
) -> BootstrapResolution:
    """Derive a run-local identity only from an explicit replay side mapping."""
    if not bootstrap.authenticated_own_listing:
        return BootstrapResolution(None, (), "bootstrap_not_authenticated")
    indices = _mapping_candidates(replay, bootstrap.submission_id)
    if len(indices) != 1:
        reason = "replay_submission_mapping_missing" if not indices else "replay_submission_mapping_ambiguous"
        return BootstrapResolution(None, (), reason, bool(indices))
    index = indices[0]
    info = replay.get("info") if isinstance(replay.get("info"), Mapping) else replay
    agents = info.get("Agents") if isinstance(info, Mapping) else None
    agent = agents[index] if isinstance(agents, Sequence) and not isinstance(agents, (str, bytes)) and index < len(agents) else None
    team_id = _scalar(agent.get("TeamId", agent.get("teamId"))) if isinstance(agent, Mapping) else None
    team_name = _scalar(agent.get("TeamName", agent.get("teamName", agent.get("Name")))) if isinstance(agent, Mapping) else None
    if isinstance(info, Mapping):
        ids = _string_values(info.get("TeamIds"))
        names = _string_values(info.get("TeamNames"))
        team_id = team_id or (ids[index] if index < len(ids) else None)
        team_name = team_name or (names[index] if index < len(names) else None)
    if not team_id and not team_name:
        return BootstrapResolution(None, (), "replay_side_identity_missing", True)
    return BootstrapResolution(TeamIdentity(team_id=team_id, team_name=team_name), (index,), "replay_submission_side_verified", True)


__all__ = [
    "AgentIndexResolution", "BootstrapResolution", "OwnSubmissionBootstrap", "ParticipantResolution", "TeamIdentity",
    "bootstrap_identity_from_replay", "classify_replay_participants", "identities_compatible", "replay_matches_episode_agent",
    "resolve_episode_agent_mapping", "resolve_own_agent_indices",
]
