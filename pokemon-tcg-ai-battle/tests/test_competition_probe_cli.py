"""Exit-code contract tests for the reproducible C2b CLI."""

from __future__ import annotations

import pytest

from scripts.probe_competition import main


def test_invalid_arguments_use_documented_exit_code_three() -> None:
    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 3


def test_invalid_timeout_uses_documented_exit_code_three(tmp_path) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--competition", "pokemon-tcg-ai-battle", "--timeout", "0", "--output-dir", str(tmp_path)])
    assert error.value.code == 3
