"""Copy bounded pipeline summaries to the handoff paths used by runbooks."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--artifact-root", type=Path, required=True)
parser.add_argument("--run-dir", type=Path)
parser.add_argument("--dataset", type=Path)
parser.add_argument("--model-dir", type=Path)
parser.add_argument("--phase", choices=("population", "league", "dataset", "training", "holdout"), required=True)
args = parser.parse_args()
root = args.artifact_root; summaries = root / "summaries"; summaries.mkdir(parents=True, exist_ok=True)
if args.phase == "league": source = args.run_dir / "run_summary.json"; fault = args.run_dir / "fault_summary.json"
elif args.phase == "dataset": source = args.dataset.with_suffix(".summary.json"); fault = None
elif args.phase == "training": source = args.model_dir / "training_summary.json"; fault = None
elif args.phase == "holdout": source = summaries / "student_v1_holdout_evaluation.json"; fault = None
else: source = root / "artifacts" / "opponent_registry.json"; fault = None
value = json.loads(source.read_text(encoding="utf-8"))
if args.phase == "population":
    from collections import Counter
    value = {"schema_version": "offline-scaleup-population-summary-v1", "population_id": value["population_id"], "semantic_population_digest": value["semantic_population_digest"], "entries": len(value["entries"]), "by_type": dict(Counter(item["opponent_type"] for item in value["entries"]))}
payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
if len(payload.encode("utf-8")) > 20_000: raise SystemExit("summary exceeds 20KB")
(summaries / "latest_run_summary.json").write_text(payload + "\n", encoding="utf-8")
if fault is not None: (summaries / "latest_fault_summary.json").write_text(fault.read_text(encoding="utf-8"), encoding="utf-8")
if args.phase == "training": (summaries / "latest_training_summary.json").write_text(payload + "\n", encoding="utf-8")
if args.phase == "holdout": (summaries / "latest_holdout_summary.json").write_text(payload + "\n", encoding="utf-8")
