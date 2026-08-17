"""長時間 runner の端末表示の契約 (AGENTS.md「長時間実験の端末表示」)。

規律は 2 つある。TTY では**単一の更新式 bar** を出し、局や step ごとの行ログを
出さないこと。非 TTY では 10 秒程度ごとの集約スナップショットだけを出し、詳細を
端末へ複製しないこと。

pipe へ通すと carriage return が解釈されず bar の断片が大量に出る。ここでは
「pipe を検出したら集約モードへ落ちる」ことを検査する。
"""

from __future__ import annotations

import io

import mage_ptcg.meta_specialist.progress_v1 as progress_v1
from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1


class _FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


class _FakePipe(io.StringIO):
    def isatty(self) -> bool:
        return False


def test_a_pipe_gets_sparse_snapshots_not_one_line_per_item() -> None:
    """非 TTY で 1 件 1 行を出さないこと.

    100 件で 100 行出るような runner は、長時間実験のログを判断不能にする。
    """
    stream = _FakePipe()
    reporter = ProgressReporterV1(
        total=100, desc="probe", stream=stream, snapshot_interval_seconds=3600.0
    )
    for _ in range(99):
        reporter.update(1, loss=0.5)
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) <= 2, f"expected sparse snapshots, got {len(lines)} lines"


def test_the_final_update_always_reports_even_inside_the_interval() -> None:
    """最後の 1 件は interval に関係なく必ず出すこと（完了が見えなくなるため）."""
    stream = _FakePipe()
    reporter = ProgressReporterV1(
        total=3, desc="probe", stream=stream, snapshot_interval_seconds=3600.0
    )
    reporter.update(1)
    reporter.update(1)
    reporter.update(1)
    assert "3/3" in stream.getvalue()


def test_a_snapshot_carries_the_values_needed_to_judge_progress() -> None:
    """速度・経過・ETA と、呼び出し側が渡した集計値が載ること."""
    stream = _FakePipe()
    reporter = ProgressReporterV1(
        total=10, desc="probe", stream=stream, snapshot_interval_seconds=0.0
    )
    reporter.update(1, faults=2, records=1234)
    text = stream.getvalue()
    assert "probe: 1/10" in text
    assert "it/s" in text and "elapsed" in text and "eta" in text
    assert "faults=2" in text and "records=1234" in text


def test_a_tty_uses_one_updating_bar_rather_than_appended_lines() -> None:
    """TTY では carriage return による単一行更新になること."""
    stream = _FakeTty()
    reporter = ProgressReporterV1(total=5, desc="probe", stream=stream)
    for _ in range(5):
        reporter.update(1, loss=0.25)
    reporter.close()
    text = stream.getvalue()
    assert "\r" in text, "a TTY bar must rewrite its line rather than append"
    assert text.count("\n") <= 2, "a TTY bar must not emit one line per item"


def test_state_transitions_are_written_without_corrupting_the_bar() -> None:
    """stage の開始・完了・fail-closed の理由は bar を壊さずに出せること."""
    stream = _FakeTty()
    reporter = ProgressReporterV1(total=2, desc="probe", stream=stream)
    reporter.update(1)
    reporter.note("[probe] stage finished")
    reporter.close()
    assert "[probe] stage finished" in stream.getvalue()


def test_floats_are_formatted_compactly_so_the_postfix_stays_readable() -> None:
    stream = _FakePipe()
    reporter = ProgressReporterV1(
        total=1, desc="probe", stream=stream, snapshot_interval_seconds=0.0
    )
    reporter.update(1, loss=0.123456789012345)
    assert "0.1235" in stream.getvalue()


def test_eta_does_not_reuse_setup_time_after_first_completed_unit(monkeypatch) -> None:
    """The first expensive materialization unit must not inflate later ETA."""
    clock = [0.0]
    monkeypatch.setattr(progress_v1.time, "monotonic", lambda: clock[0])
    stream = _FakePipe()
    reporter = ProgressReporterV1(
        total=3, desc="probe", stream=stream, snapshot_interval_seconds=0.0
    )
    clock[0] = 100.0
    reporter.update(1)
    clock[0] = 200.0
    reporter.update(1)
    text = stream.getvalue()
    assert "2/3" in text
    assert "eta 50s" in text


def test_eta_is_unknown_after_only_the_first_unit(monkeypatch) -> None:
    """The first post-materialization unit must not produce a runaway ETA."""
    clock = [0.0]
    monkeypatch.setattr(progress_v1.time, "monotonic", lambda: clock[0])
    stream = _FakePipe()
    reporter = ProgressReporterV1(
        total=16, desc="probe", stream=stream, snapshot_interval_seconds=0.0
    )
    clock[0] = 100.0
    reporter.update(1)
    text = stream.getvalue()
    assert "probe: 1/16" in text
    assert "eta -" in text
