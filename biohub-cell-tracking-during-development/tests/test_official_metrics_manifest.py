from biohub.official_metrics import UPSTREAM_BLOBS, UPSTREAM_COMMIT


def test_official_metrics_are_pinned_to_known_upstream_blobs() -> None:
    assert UPSTREAM_COMMIT == "075fc5f5a52d11077f9dc2b074644618f26939e2"
    assert UPSTREAM_BLOBS == {
        "metrics.py": "e536cdc9f0877542ab227ec701ef0fdbb667189a",
        "division_metrics.py": "9afa8630f3d7a294f9a25fca81cce1e0c7c7aeca",
    }
