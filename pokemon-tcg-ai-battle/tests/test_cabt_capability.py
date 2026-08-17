"""Tests for fail-closed cabt capability diagnostics."""

from __future__ import annotations

from types import SimpleNamespace

from scripts.cabt_capability import EXPECTED_PACKAGE, diagnose_cabt_capability


def test_package_absent_has_deterministic_safe_report() -> None:
    def missing(_name: str):
        raise ModuleNotFoundError("private import detail")

    report = diagnose_cabt_capability(module_loader=missing)

    assert report["status"] == "UNAVAILABLE"
    assert report["reason_code"] == "PACKAGE_NOT_INSTALLED"
    assert report["missing_requirements"] == [EXPECTED_PACKAGE]
    assert report["import_error_type"] is None
    assert "private" not in str(report)


def test_import_failure_does_not_serialize_exception_text() -> None:
    def broken(_name: str):
        raise RuntimeError("/private/plugin/path token=secret")

    report = diagnose_cabt_capability(module_loader=broken)

    assert report["reason_code"] == "PACKAGE_IMPORT_FAILED"
    assert report["import_error_type"] == "RuntimeError"
    assert "secret" not in str(report)


def test_unregistered_plugin_and_asset_failure_are_distinct() -> None:
    missing_plugin = SimpleNamespace(__file__="/private/site-packages/kaggle_environments/__init__.py", environments={})
    report = diagnose_cabt_capability(module_loader=lambda _name: missing_plugin, version_loader=lambda _name: "1.32.0")
    assert report["reason_code"] == "PLUGIN_NOT_REGISTERED"
    assert report["kaggle_environments_path"] == "<site-packages>/kaggle_environments"

    def missing_asset(_name: str):
        raise RuntimeError("asset unavailable")

    asset_module = SimpleNamespace(
        __file__="/private/site-packages/kaggle_environments/__init__.py",
        environments={"cabt": object()},
        make=missing_asset,
    )
    asset_report = diagnose_cabt_capability(module_loader=lambda _name: asset_module, version_loader=lambda _name: "1.32.0")
    assert asset_report["reason_code"] == "COMPETITION_ASSET_MISSING"
    assert asset_report["import_error_type"] == "RuntimeError"


def test_ready_and_version_mismatch_are_distinct() -> None:
    module = SimpleNamespace(
        __file__="/private/site-packages/kaggle_environments/__init__.py",
        environments={"cabt": object(), "rps": object()},
        make=lambda _name: object(),
    )
    ready = diagnose_cabt_capability(module_loader=lambda _name: module, version_loader=lambda _name: "1.32.0")
    assert ready["status"] == "READY"
    assert ready["actual_execution_allowed"] is True
    assert ready["engine_seed_supported"] is False
    assert ready["available_environments"] == ["cabt", "rps"]

    mismatch = diagnose_cabt_capability(module_loader=lambda _name: module, version_loader=lambda _name: "0.0.1")
    assert mismatch["reason_code"] == "VERSION_INCOMPATIBLE"
