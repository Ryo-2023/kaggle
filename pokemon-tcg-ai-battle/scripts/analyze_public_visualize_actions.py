"""Count only public Replay visualization action categories.

No card, deck, hand, prize, log, or search payload is read.  The output is a
category-level descriptive summary and cannot be used as an expert label.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row}) or ["status"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, object]] = []
    for path in sorted(args.raw_dir.glob("episode-*-replay.json")):
        payload = json.loads(path.read_text(encoding="utf-8")); episode_id = path.stem.removeprefix("episode-").removesuffix("-replay")
        for step in payload.get("steps", []):
            if not isinstance(step, Sequence) or not step or not isinstance(step[0], Mapping): continue
            visualize = step[0].get("visualize")
            if not isinstance(visualize, Sequence): continue
            for turn, frame in enumerate(visualize):
                if not isinstance(frame, Mapping): continue
                action, select = frame.get("action"), frame.get("select")
                if not isinstance(action, Sequence) or len(action) != 2 or not isinstance(select, Mapping): continue
                actors = [seat for seat, selected in enumerate(action) if isinstance(selected, Sequence) and len(selected) > 0]
                if len(actors) != 1: continue
                records.append({"episode_id": episode_id, "frame": turn, "actor_seat": actors[0], "select_type": select.get("type"), "context": select.get("context"), "selected_count": len(action[actors[0]])})
    write_csv(args.output / "replay_public_action_records.csv", records)
    by = Counter((str(row["select_type"]), str(row["context"])) for row in records)
    summary = [{"select_type": key[0], "context": key[1], "count": count} for key, count in sorted(by.items())]
    write_csv(args.output / "replay_public_action_summary.csv", summary)
    (args.output / "replay_style_policy_registry.json").write_text(json.dumps([
        {"policy_id": "replay-style-setup-v1", "source": "public category frequency", "coverage": "MAIN/selection category only", "fallback": "Rule v0", "status": "LOCAL_MIMIC_NOT_TEAM_REPRODUCTION"},
        {"policy_id": "replay-style-tempo-v1", "source": "public category frequency", "coverage": "MAIN/selection category only", "fallback": "Rule v0", "status": "LOCAL_MIMIC_NOT_TEAM_REPRODUCTION"},
    ], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(records), "categories": len(summary)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
