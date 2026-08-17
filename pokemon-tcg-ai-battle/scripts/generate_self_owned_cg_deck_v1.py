"""Generate and bind an official-data-only self-owned CG deck candidate.

This command has an explicit ``--execute`` gate.  Card generation reads only
the official card CSV and the versioned role specification.  Optional public
roots are used after that boundary only for canonical-hash collision auditing;
their deck contents are never copied into the generated candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Iterable, Mapping

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.self_owned_cg_deck_v1 import (  # noqa: E402
    SelfOwnedDeckV1Error,
    canonical_deck_sha256_v1,
    generate_self_owned_deck_v1,
    load_card_catalog_v1,
    load_self_owned_deck_spec_v1,
)
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import (  # noqa: E402
    SelfOwnedCgPackageV1Error,
    materialize_self_owned_cg_package_v1,
    verify_self_owned_cg_package_v1,
    write_self_owned_deck_artifact_v1,
)


SCHEMA = "self-owned-cg-deck-generation-v1"
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
}
DEFAULT_CARD_DB = _ROOT / "data/raw/EN_Card_Data.csv"
DEFAULT_SPEC = _ROOT / "configs/meta_specialist/self_owned_cg_deck_spec_v1.json"
DEFAULT_SOURCE_PACKAGE = _ROOT / "runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1"


class SelfOwnedCgDeckCliV1Error(ValueError):
    """Raised when candidate generation cannot satisfy its artifact contract."""


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SelfOwnedCgDeckCliV1Error(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SelfOwnedCgDeckCliV1Error("manifest is not canonical JSON") from exc


def _semantic_sha(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
    except FileExistsError:
        raise
    except OSError as exc:
        raise SelfOwnedCgDeckCliV1Error(f"cannot write artifact: {path}") from exc


def _parse_deck_tokens(path: Path) -> tuple[int, ...] | None:
    try:
        values = tuple(int(token) for token in path.read_bytes().decode("utf-8").split())
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if len(values) != 60 or any(type(value) is not int or value <= 0 for value in values):
        return None
    return values


def _iter_public_files(root: Path) -> Iterable[Path]:
    if root.is_symlink() or not root.is_dir():
        raise SelfOwnedCgDeckCliV1Error(f"public scan root must be a regular directory: {root}")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_file() and path.name == "deck.csv":
            yield path
        elif path.is_file() and path.name.endswith("manifest.json"):
            yield path


def scan_public_canonical_hashes_v1(roots: Iterable[str | Path]) -> tuple[str, ...]:
    """Collect only canonical identities for collision avoidance.

    Malformed files are ignored as non-deck evidence.  A valid ``deck.csv``
    contributes its order-independent canonical hash; a manifest contributes a
    hash only when it explicitly contains a 64-hex ``canonical_deck_sha256``.
    """
    hashes: set[str] = set()
    for raw_root in roots:
        root = Path(raw_root).resolve()
        for path in _iter_public_files(root):
            if path.name == "deck.csv":
                values = _parse_deck_tokens(path)
                if values is not None:
                    hashes.add(canonical_deck_sha256_v1(values))
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            value = payload.get("canonical_deck_sha256") if isinstance(payload, Mapping) else None
            if isinstance(value, str) and len(value) == 64:
                try:
                    int(value, 16)
                except ValueError:
                    continue
                hashes.add(value)
    return tuple(sorted(hashes))


def run_generation_v1(
    *,
    output: str | Path,
    card_db: str | Path = DEFAULT_CARD_DB,
    spec: str | Path = DEFAULT_SPEC,
    source_package: str | Path = DEFAULT_SOURCE_PACKAGE,
    public_scan_roots: Iterable[str | Path] = (),
    seed: int = 20260816,
    ordinal: int = 0,
) -> dict[str, object]:
    """Generate, seal, package, and verify one research-only candidate."""
    output_root = Path(output).resolve()
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    public_roots = tuple(Path(root).resolve() for root in public_scan_roots)
    card_path = Path(card_db).resolve()
    spec_path = Path(spec).resolve()
    source_path = Path(source_package).resolve()
    catalog = load_card_catalog_v1(card_path)
    deck_spec = load_self_owned_deck_spec_v1(spec_path)
    forbidden = scan_public_canonical_hashes_v1(public_roots)
    candidate = generate_self_owned_deck_v1(
        catalog=catalog,
        spec=deck_spec,
        seed=seed,
        ordinal=ordinal,
        forbidden_canonical_hashes=forbidden,
    )
    output_root.mkdir(parents=True, exist_ok=False)
    generator_sha = _sha256_file(Path(__file__).resolve())
    artifact_manifest = write_self_owned_deck_artifact_v1(
        candidate,
        output_root / "deck-artifact",
        card_database_sha256=catalog.source_sha256,
        role_spec_sha256=deck_spec.source_sha256,
        generator_source_sha256=generator_sha,
    )
    package_manifest = materialize_self_owned_cg_package_v1(
        source_package=source_path,
        candidate_deck=output_root / "deck-artifact/deck.csv",
        output_package=output_root / "package",
        candidate_id=candidate.candidate_id,
    )
    verify_self_owned_cg_package_v1(output_root / "package")
    body: dict[str, object] = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "candidate_id": candidate.candidate_id,
        "archetype_id": candidate.archetype_id,
        "seed": candidate.seed,
        "candidate_ordinal": candidate.candidate_ordinal,
        "card_database_path": str(card_path),
        "card_database_sha256": catalog.source_sha256,
        "role_spec_path": str(spec_path),
        "role_spec_sha256": deck_spec.source_sha256,
        "generator_source_sha256": generator_sha,
        "source_policy_package": str(source_path),
        "parent_deck": None,
        "public_parent_read": False,
        "public_scan_roots": [str(root) for root in public_roots],
        "public_scan_hashes": list(forbidden),
        "public_collision_count": int(candidate.canonical_deck_sha256 in set(forbidden)),
        "card_count": len(candidate.card_ids),
        "canonical_deck_sha256": candidate.canonical_deck_sha256,
        "deck_file_sha256": candidate.deck_file_sha256,
        "deck_artifact_manifest_sha256": artifact_manifest["manifest_sha256"],
        "package_manifest_sha256": package_manifest["manifest_sha256"],
        "package_policy_sha256": package_manifest["policy_sha256"],
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE),
        "artifact_paths": {
            "deck_artifact": "deck-artifact",
            "package": "package",
        },
    }
    body["manifest_sha256"] = _semantic_sha(body)
    _write_exclusive(output_root / "generation_manifest.json", _canonical_json(body))
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="enable artifact generation")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--card-db", type=Path, default=DEFAULT_CARD_DB)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--source-package", type=Path, default=DEFAULT_SOURCE_PACKAGE)
    parser.add_argument("--public-scan-root", type=Path, action="append", default=[])
    parser.add_argument("--seed", type=int, default=20260816)
    parser.add_argument("--ordinal", type=int, default=0)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        result = run_generation_v1(
            output=args.output,
            card_db=args.card_db,
            spec=args.spec,
            source_package=args.source_package,
            public_scan_roots=args.public_scan_root,
            seed=args.seed,
            ordinal=args.ordinal,
        )
    except (SelfOwnedDeckV1Error, SelfOwnedCgPackageV1Error, SelfOwnedCgDeckCliV1Error, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
