"""CLI end-to-end tests: doctor, ingest-local, rebuild-catalog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.competition_intelligence.cli import build_parser, main


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    parser = build_parser()
    args = parser.parse_args(argv)
    exit_code = args.func(args)
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out)


class TestDoctor:
    def test_doctor_reports_schema_versions_and_writable_root(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code, summary = _run(["doctor", "--run-root", str(tmp_path / "runs")], capsys)
        assert exit_code == 0
        assert summary["ok"] is True
        assert "source_envelope" in summary["schema_versions"]
        assert summary["run_root"]["writable"] is True
        assert summary["runtime_isolation"]["main_py_references_competition_intelligence"] is False

    def test_doctor_flags_invalid_config(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        bad_config = tmp_path / "bad.json"
        bad_config.write_text(json.dumps({"schema_version": "wrong"}), encoding="utf-8")
        exit_code, summary = _run(["doctor", "--config", str(bad_config), "--run-root", str(tmp_path / "runs")], capsys)
        assert exit_code == 1
        assert summary["config"]["valid"] is False


class TestIngestLocal:
    def test_ingest_local_archives_clean_fixture(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        fixture = tmp_path / "fixture.json"
        fixture.write_text(json.dumps({"turn": 1, "phase": "OPENING"}), encoding="utf-8")
        run_dir = tmp_path / "run-1"
        exit_code, summary = _run(
            ["ingest-local", "--run-dir", str(run_dir), "--input", str(fixture), "--source-id", "fixture-1",
             "--acquired-at", "2026-07-18T00:00:00Z"], capsys
        )
        assert exit_code == 0
        assert summary["status"] == "ARCHIVED"
        assert summary["source_id"] == "fixture-1"

    def test_ingest_local_quarantines_secret_bearing_fixture(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fixture = tmp_path / "secret.json"
        fixture.write_text(json.dumps({"api_key": "sk-abcdefghijklmnop1234"}), encoding="utf-8")
        run_dir = tmp_path / "run-1"
        exit_code, summary = _run(
            ["ingest-local", "--run-dir", str(run_dir), "--input", str(fixture), "--acquired-at", "2026-07-18T00:00:00Z"], capsys
        )
        assert exit_code == 0
        assert summary["status"] == "QUARANTINED"

    def test_ingest_local_missing_file_is_a_clean_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        run_dir = tmp_path / "run-1"
        exit_code = main([
            "ingest-local", "--run-dir", str(run_dir), "--input", str(tmp_path / "missing.json"),
            "--acquired-at", "2026-07-18T00:00:00Z",
        ])
        captured = capsys.readouterr()
        assert exit_code == 2
        assert "error" in json.loads(captured.err)


class TestRebuildCatalog:
    def test_rebuild_catalog_after_ingest(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        fixture = tmp_path / "fixture.json"
        fixture.write_text(json.dumps({"turn": 1}), encoding="utf-8")
        run_dir = tmp_path / "run-1"
        _run(
            ["ingest-local", "--run-dir", str(run_dir), "--input", str(fixture), "--source-id", "fixture-1",
             "--acquired-at", "2026-07-18T00:00:00Z"], capsys
        )
        exit_code, summary = _run(["rebuild-catalog", "--run-dir", str(run_dir)], capsys)
        assert exit_code == 0
        assert summary["source_count"] == 1


class TestMainEntrypoint:
    def test_main_returns_zero_on_success(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = main(["doctor", "--run-root", str(tmp_path / "runs")])
        assert exit_code == 0

    def test_help_does_not_crash(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
