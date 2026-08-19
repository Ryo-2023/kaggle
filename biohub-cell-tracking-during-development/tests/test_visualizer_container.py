from pathlib import Path


def test_compose_exposes_visual_inspector_only_on_loopback() -> None:
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()
    assert '"127.0.0.1:8765:8765"' in compose
