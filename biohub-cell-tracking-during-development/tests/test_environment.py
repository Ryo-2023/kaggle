from __future__ import annotations

import sys


def test_python_version_is_311() -> None:
    assert sys.version_info[:2] == (3, 11)


def test_core_dependencies_import() -> None:
    import numpy  # noqa: F401
    import polars  # noqa: F401
    import scipy  # noqa: F401
    import torch  # noqa: F401
    import tracksdata  # noqa: F401
    import zarr  # noqa: F401


def test_project_package_imports() -> None:
    import biohub

    assert biohub.__version__ == "0.1.0"
