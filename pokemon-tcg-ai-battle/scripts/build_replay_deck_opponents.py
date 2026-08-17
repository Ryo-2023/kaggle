"""Create reproducible, development-only opponent bundles from public exact decks.

Each bundle uses the literal public ``visualize`` deck and one local legal
policy.  It is intentionally labelled as a replay-deck opponent, never as a
reproduction of the source team's private policy.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


POLICIES = ("rule-v0", "setup-heavy", "aggressive-tempo")
ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wrapper(policy: str) -> str:
    synthetic = "" if policy == "rule-v0" else (
        "from mage_ptcg.opponents.synthetic_stress_v1 import make_synthetic_stress_agent\n"
        f"_agent = make_synthetic_stress_agent(kind={policy!r}, deck=_deck, seed=20260726).as_agent()\n"
    )
    rule = "_agent = make_rule_agent(deck=_deck, seed=20260726)\n" if policy == "rule-v0" else ""
    return f'''import sys
from pathlib import Path

_ROOT = {str(ROOT)!r}
for _entry in (_ROOT, _ROOT + "/src"):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)
from main import make_rule_agent, read_deck_csv
_deck = read_deck_csv(Path(__file__).with_name("deck.csv"))
{synthetic}{rule}for _name in list(sys.modules):
    if _name == "agents" or _name.startswith("agents."):
        del sys.modules[_name]
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
while _ROOT + "/src" in sys.path:
    sys.path.remove(_ROOT + "/src")

def agent(obs, configuration=None):
    del configuration
    return _agent(obs)
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck-registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = list(csv.DictReader(args.deck_registry.open(encoding="utf-8")))
    unique: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("card_count") == "60" and row.get("deck_hash"):
            unique.setdefault(row["deck_hash"], row)
    manifest = []
    for deck_hash, row in sorted(unique.items()):
        cards = json.loads(row["cards_json"])
        if len(cards) != 60 or any(type(card) is not int for card in cards):
            raise ValueError(f"invalid exact deck {deck_hash}")
        for policy in POLICIES:
            name = f"replay-{deck_hash[:12]}-{policy}"
            directory = args.output / name
            directory.mkdir()
            deck_path, main_path = directory / "deck.csv", directory / "main.py"
            deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
            main_path.write_text(wrapper(policy), encoding="utf-8")
            manifest.append({
                "opponent_id": name, "deck_hash": deck_hash, "policy_id": policy,
                "policy_fingerprint": hashlib.sha256(policy.encode()).hexdigest(),
                "deck_source_episode": row.get("episode_id"), "deck_source_team": row.get("team_name"),
                "fidelity": "EXACT_PUBLIC_DECK__LOCAL_POLICY", "entrypoint": "main.py:agent",
                "deck_path": str(deck_path), "main_path": str(main_path),
                "deck_file_sha256": digest(deck_path), "adapter_sha256": digest(main_path),
            })
    (args.output / "replay_deck_opponent_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"unique_decks": len(unique), "opponents": len(manifest)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
