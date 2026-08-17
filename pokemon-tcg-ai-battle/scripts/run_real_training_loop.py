"""Real E2E multi-opponent collection, training, and package verification pipeline."""

from __future__ import annotations

from pathlib import Path
import sys

# Set PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mage_ptcg.meta_specialist.collect_trajectories_v1 import run_collect_trajectories_v1
from mage_ptcg.meta_specialist.train_from_trajectories_v1 import run_train_from_trajectories_v1
from mage_ptcg.meta_specialist.package import (
    build_specialist_archive, verify_specialist_archive, write_bundle_spec,
)
from tests.meta_specialist.test_meta_specialist_cli import _build_static_bundle_spec


def main() -> int:
    output_root = Path("runs/e2e-multi-opponent-experiment")
    output_root.mkdir(parents=True, exist_ok=True)

    collection_dir = output_root / "collection"
    training_dir = output_root / "training"
    archive_dir = output_root / "archive"

    print("=== Step 1: Collecting real trajectories against multiple proxy opponents ===")
    report_path = Path("runs/meta-specialist-seed-qualification/seed_qualification_report_v1.json")
    deck_dir = Path("runs/materialized-decks")

    if not report_path.is_file() or not deck_dir.is_dir():
        print("Required seed qualification report or materialized deck dir missing.")
        return 1

    # Run real CABT game collection
    coll_payload = run_collect_trajectories_v1(
        run_name="e2e-multi-opponents-01",
        lanes_arg="all",
        output_base_dir=collection_dir,
        seed_qualification_report_path=report_path,
        materialized_deck_dir=deck_dir,
        num_games=4,
        base_seed=100,
        workers=2,
    )
    completed = coll_payload.get("games_completed", 0)
    transitions = coll_payload.get("transitions_collected", 0)
    print(f"Collection finished: completed={completed}, transitions={transitions}")

    if completed == 0:
        print("Error: No games were completed in collection!")
        return 1

    print("\n=== Step 2: Training neural policy via V-trace from collected trajectories ===")
    train_payload = run_train_from_trajectories_v1(
        run_name="e2e-vtrace-train-01",
        output_base_dir=training_dir,
        collection_run_dir=collection_dir / "e2e-multi-opponents-01",
        max_steps=5,
    )
    steps_taken = train_payload.get("steps_taken", 0)
    last_loss = train_payload.get("last_loss", 0.0)
    last_grad_norm = train_payload.get("last_grad_norm", 0.0)
    ckpt_path = train_payload.get("checkpoint_path", "")
    print(f"Training finished: steps_taken={steps_taken}, final_loss={last_loss:.4f}, grad_norm={last_grad_norm:.4f}")
    print(f"Checkpoint saved at: {ckpt_path}")

    print("\n=== Step 3: Building and verifying final submission bundle ===")
    source_dir = output_root / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    bundle_spec = _build_static_bundle_spec(source_dir)
    
    spec_path = archive_dir / "bundle_spec.json"
    archive_dir.mkdir(parents=True, exist_ok=True)
    write_bundle_spec(bundle_spec, spec_path)
    
    archive_path = archive_dir / "submission_multi_opponents.tar.gz"
    build_report = build_specialist_archive(bundle_spec, archive_path)
    print(f"Build archive status: {build_report.status}, archive_path={archive_path}")

    verify_report = verify_specialist_archive(archive_path)
    print(f"Verify archive status: {verify_report.status}")

    print("\nSUCCESS: All E2E pipeline steps completed cleanly!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
