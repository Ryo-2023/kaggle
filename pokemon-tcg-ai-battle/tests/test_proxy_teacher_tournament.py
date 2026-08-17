from scripts.run_proxy_teacher_tournament import schedule


def test_schedule_excludes_self_and_preserves_per_candidate_budget():
    rows = []
    for index in range(7):
        rows.append({"asset_id": f"dev/a{index}", "local_runtime_status": "PROXY_RUNTIME_PASSED", "extraction_path": f"/tmp/a{index}", "policy_hash": f"p{index}", "deck_hash": f"d{index}"})
    for index in range(8):
        rows.append({"asset_id": f"agents/a{index}", "local_runtime_status": "PROXY_RUNTIME_PASSED", "extraction_path": f"/tmp/b{index}", "policy_hash": f"q{index}", "deck_hash": f"e{index}"})
    rows.append({"asset_id": "dev/waterbox_search_v3", "local_runtime_status": "PROXY_RUNTIME_PASSED", "extraction_path": "/tmp/w", "policy_hash": "water", "deck_hash": "water"})
    items = schedule(rows, 96)
    assert sum(item["games"] for item in items if item["candidate_id"] == "dev/a0") == 96
    assert all(not (item["candidate_id"] == item["opponent_id"]) for item in items)
