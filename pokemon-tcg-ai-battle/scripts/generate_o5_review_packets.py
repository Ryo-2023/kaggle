"""Generate human-reviewable O5 Current Meta review packets.

Never activates population, never fabricates permission, never guesses a
Rules attestation status. Reads an EXISTING O5 Deck Archetype Registry
snapshot (produced by the already-implemented, already-canonical O5
Registry / branch inventory tooling) and turns it into two separate,
per-deck review packets a human can act on:

* a Rules attestation packet (for PUBLIC_OTHER/environment-sourced decks)
* a Team permission packet (for TEAM_SHARED/branch-sourced decks)

The registry path is always a required CLI argument -- never hardcoded --
since the snapshot lives outside this repository (see
docs/evidence/o5-registry-archetype-discovery-v1.md and
o5-activation-opponent-factory-v1.md for where prior runs wrote theirs).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from mage_ptcg.competition_intelligence.o5_activation import RulesUseGate, write_review_packets  # noqa: E402
from mage_ptcg.competition_intelligence.o5_registry import O5_REGISTRY_SCHEMA_VERSION  # noqa: E402


class ReviewPacketError(RuntimeError):
    """Raised for a malformed or unsupported registry snapshot."""


def _load_registry(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != O5_REGISTRY_SCHEMA_VERSION:
        raise ReviewPacketError(f"unsupported or malformed registry snapshot at {path}")
    return data


def _deck_row(deck_hash: str, deck: dict, *, links_by_deck: dict[str, list[dict]]) -> dict[str, object]:
    cards = deck.get("cards", [])
    provenance = deck.get("provenance", [])
    source_kinds = sorted({str(item.get("source_kind")) for item in provenance})
    branch_refs = sorted({str(item.get("branch_ref")) for item in provenance if item.get("branch_ref")})
    commit_shas = sorted({str(item.get("commit_sha")) for item in provenance if item.get("commit_sha")})
    blob_shas = sorted({str(item.get("git_blob_sha")) for item in provenance if item.get("git_blob_sha")})
    links = links_by_deck.get(deck_hash, [])
    verified_links = [link for link in links if link.get("link_status") == "VERIFIED_LINK"]
    is_team_shared = source_kinds == ["TEAM_SHARED"]
    is_public_other = "PUBLIC_OTHER" in source_kinds or "OWN_KAGGLE" in source_kinds

    if is_team_shared:
        attestation_status = "NOT_APPLICABLE_TEAM_SHARED_ONLY"
        permission_status = "TEAM_SHARED_PENDING_PERMISSION"
        blocking_reason = "no signed team-artifact-permission-v1 manifest received for this branch/commit"
        required_signer_action = (
            "branch owner or repository admin fills reports/team_permission_manifest_templates/"
            "permission.template.json (provider_id_hash, repository, commit_or_branch, artifact_selectors, "
            "allowed_use) and signs reviewed_at/reviewed_by_hash"
        )
    elif is_public_other:
        attestation_status = "UNVERIFIED_RULES_CONSTRAINT"
        permission_status = "NOT_APPLICABLE_PUBLIC_SOURCE"
        blocking_reason = "no VERIFIED competition-rules-attestation-v2 received"
        required_signer_action = (
            "a human reviews reports/rules_attestation_template.yaml against the official Kaggle competition "
            "rules and sets review_status=VERIFIED with reviewed_at/reviewed_by_hash/evidence"
        )
    else:
        attestation_status = "UNKNOWN_SOURCE_KIND"
        permission_status = "UNKNOWN_SOURCE_KIND"
        blocking_reason = f"unrecognized source_kind combination: {source_kinds}"
        required_signer_action = "human review required before this row can be classified"

    return {
        "deck_hash": deck_hash,
        "deck_source_branch_refs": branch_refs[:10],
        "deck_source_branch_ref_count": len(branch_refs),
        "deck_source_commit_shas": commit_shas[:10],
        "deck_source_git_blob_shas": blob_shas[:10],
        "card_count": len(cards),
        "exact_card_counts": dict(sorted(Counter(cards).items())),
        "ruleset_card_pool_version": deck.get("card_pool_version"),
        "legality_evidence": (
            "60-card multiset validated via canonical_deck_hash() at ingestion; "
            f"{len(blob_shas)} distinct git blob(s) share this exact multiset"
        ),
        "visibility": "TEAM_SHARED" if is_team_shared else ("PUBLIC_OTHER_OR_OWN_KAGGLE" if is_public_other else "UNKNOWN"),
        "owner_or_team": branch_refs[:5] if branch_refs else ["UNKNOWN"],
        "requested_use": ["deck_classification", "agent_execution", "local_evaluation"],
        "allowed_use": [],
        "prohibited_use": ["redistribution"],
        "attestation_status": attestation_status,
        "permission_status": permission_status,
        "blocking_reason": blocking_reason,
        "required_signer_action": required_signer_action,
        "linked_agent_count": len(links),
        "verified_agent_link_count": len(verified_links),
    }


def build_review_rows(registry: dict[str, object]) -> list[dict[str, object]]:
    links_by_deck: dict[str, list[dict]] = defaultdict(list)
    for link in registry.get("agent_deck_links", []):
        deck_hash = link.get("deck_hash")
        if deck_hash:
            links_by_deck[str(deck_hash)].append(link)
    rows = [
        _deck_row(deck_hash, deck, links_by_deck=links_by_deck)
        for deck_hash, deck in sorted(registry.get("deck_lists", {}).items())
    ]
    return rows


def _markdown_table(rows: list[dict[str, object]], *, columns: list[str]) -> str:
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(str(row.get(column, ""))[:80] for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        registry = _load_registry(args.registry_path)
    except ReviewPacketError as exc:
        print(f"cannot read registry snapshot: {exc}", file=sys.stderr)
        return 3
    rows = build_review_rows(registry)
    team_rows = [row for row in rows if row["visibility"] == "TEAM_SHARED"]
    public_rows = [row for row in rows if row["visibility"] == "PUBLIC_OTHER_OR_OWN_KAGGLE"]
    unknown_rows = [row for row in rows if row["visibility"] == "UNKNOWN"]

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Reuse the existing, already-canonical baseline templates rather than
    # reinventing the Rules attestation / permission manifest schema.
    write_review_packets(output_dir, rules_gate=RulesUseGate.unverified(), pending_artifacts=registry.get("branch_artifacts", []))

    (output_dir / "team_permission_deck_review.json").write_text(
        json.dumps({"schema_version": "o5-team-permission-deck-review-v1", "deck_count": len(team_rows), "decks": team_rows}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "rules_attestation_deck_review.json").write_text(
        json.dumps({"schema_version": "o5-rules-attestation-deck-review-v1", "deck_count": len(public_rows), "decks": public_rows}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    columns = [
        "deck_hash", "deck_source_branch_ref_count", "card_count", "ruleset_card_pool_version",
        "permission_status", "attestation_status", "linked_agent_count", "verified_agent_link_count",
    ]
    team_md = (
        f"# O5 Team Permission Deck Review\n\n"
        f"{len(team_rows)} TEAM_SHARED exact deck(s) require a signed team-artifact-permission-v1 manifest "
        f"before O5 can activate them. 0 are activated by this packet.\n\n"
        + _markdown_table(team_rows, columns=columns)
        + "\n"
    )
    (output_dir / "team_permission_deck_review.md").write_text(team_md, encoding="utf-8")

    if public_rows:
        rules_md = (
            f"# O5 Rules Attestation Deck Review\n\n"
            f"{len(public_rows)} PUBLIC_OTHER/OWN_KAGGLE exact deck(s) require a VERIFIED Rules attestation "
            f"before O5 can classify/analyze them. 0 are activated by this packet.\n\n"
            + _markdown_table(public_rows, columns=columns)
            + "\n"
        )
    else:
        rules_md = (
            "# O5 Rules Attestation Deck Review\n\n"
            "This registry snapshot contains 0 PUBLIC_OTHER/OWN_KAGGLE exact decks -- every observed exact "
            "deck in this snapshot is TEAM_SHARED (see team_permission_deck_review.md instead). A VERIFIED "
            "Rules attestation is still required before any future environment-captured (leaderboard/replay) "
            "deck can move past CAPTURE_ONLY, per reports/rules_attestation_review_packet.md.\n"
        )
    (output_dir / "rules_attestation_deck_review.md").write_text(rules_md, encoding="utf-8")

    summary = {
        "schema_version": "o5-current-meta-review-summary-v1",
        "registry_schema_version": registry.get("schema_version"),
        "total_exact_decks": len(rows),
        "team_shared_decks": len(team_rows),
        "public_or_own_kaggle_decks": len(public_rows),
        "unknown_source_decks": len(unknown_rows),
        "activated_decks": 0,
    }
    (output_dir / "current_meta_review_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
