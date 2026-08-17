"""Large-scale (10,000+ steps) V-trace training pipeline script."""

from __future__ import annotations

from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mage_ptcg.meta_specialist.collect_trajectories_v1 import run_collect_trajectories_v1
from mage_ptcg.meta_specialist.train_from_trajectories_v1 import run_train_from_trajectories_v1


def main() -> int:
    output_root = Path("runs/large-scale-vtrace-run-10k")
    output_root.mkdir(parents=True, exist_ok=True)

    collection_dir = output_root / "collection"
    training_dir = output_root / "training"

    report_path = Path("runs/meta-specialist-seed-qualification/seed_qualification_report_v1.json")
    deck_dir = Path("runs/materialized-decks")

    print("[P2 Large Scale Training] Step 1: Collecting extensive multi-opponent game trajectories...")
    start_time = time.time()

    coll_payload = run_collect_trajectories_v1(
        run_name="large-scale-collection-10k",
        lanes_arg="all",
        output_base_dir=collection_dir,
        seed_qualification_report_path=report_path,
        materialized_deck_dir=deck_dir,
        num_games=10,
        base_seed=1000,
        workers=2,
    )
    completed = coll_payload.get("games_completed", 0)
    transitions = coll_payload.get("transitions_collected", 0)
    print(f"Collection completed: games={completed}, transitions={transitions}, elapsed={time.time() - start_time:.2f}s")

    if completed == 0:
        print("Error: No games were completed in collection!")
        return 1

    print("\n[P2 Large Scale Training] Step 2: Running 10,000 V-trace policy gradient steps...")
    train_start = time.time()
    train_payload = run_train_from_trajectories_v1(
        run_name="vtrace-train-10000-steps",
        output_base_dir=training_dir,
        collection_run_dir=collection_dir / "large-scale-collection-10k",
        max_steps=10000,
        checkpoint_interval_steps=1000,
    )

    steps_taken = train_payload.get("steps_taken", 0)
    last_loss = train_payload.get("last_loss", 0.0)
    last_grad_norm = train_payload.get("last_grad_norm", 0.0)
    ckpt_path = train_payload.get("checkpoint_path", "")

    print(f"Training completed: steps_taken={steps_taken}, final_loss={last_loss:.6f}, grad_norm={last_grad_norm:.6f}, elapsed={time.time() - train_start:.2f}s")
    print(f"10,000 Step Checkpoint saved at: {ckpt_path}")

    print("\nP2 Large-Scale V-trace Training completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
