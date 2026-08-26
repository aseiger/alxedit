"""Unit tests for session metadata and the per-session diff summary."""

from __future__ import annotations

from pathlib import Path

from alxedit2 import sessions
from alxedit2 import settings as cfg


def test_session_diff_stats_in_sync(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("1\n2\n")
    sid = sessions.create_session(tmp_path)
    sessions.copy_to_mirror(tmp_path, sid, tmp_path / "a.txt")
    assert sessions.session_diff_stats(tmp_path, sid) == (0, 0, 0)


def test_session_diff_stats_counts_added_removed_deleted(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("1\n2\n3\n")
    (tmp_path / "b.txt").write_text("x\n")
    sid = sessions.create_session(tmp_path)
    sessions.copy_to_mirror(tmp_path, sid, tmp_path / "a.txt")
    sessions.copy_to_mirror(tmp_path, sid, tmp_path / "b.txt")

    # a.txt: one line replaced by two -> difflib replace: +2/-2
    (tmp_path / "a.txt").write_text("1\nX\nY\n")
    # b.txt: deleted on disk -> -1
    (tmp_path / "b.txt").unlink()
    # c.txt: new since the session -> +2
    (tmp_path / "c.txt").write_text("n1\nn2\n")

    changed, added, removed = sessions.session_diff_stats(tmp_path, sid)
    assert changed == 3
    assert added == 4  # a: +2, c: +2
    assert removed == 3  # a: -2, b: -1


def test_session_diff_stats_binary_counts_file_not_lines(tmp_path: Path) -> None:
    (tmp_path / "img.bin").write_bytes(b"\xff\xfe\x00")  # not utf-8
    sid = sessions.create_session(tmp_path)
    sessions.copy_to_mirror(tmp_path, sid, tmp_path / "img.bin")
    (tmp_path / "img.bin").write_bytes(b"\xff\xfe\x01")
    changed, added, removed = sessions.session_diff_stats(tmp_path, sid)
    assert (changed, added, removed) == (1, 0, 0)


def test_session_diff_stats_respects_settings(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("a\n")
    (tmp_path / "drop.log").write_text("l1\nl2\n")
    sid = sessions.create_session(tmp_path)
    sessions.copy_to_mirror(tmp_path, sid, tmp_path / "keep.txt")
    (tmp_path / "drop.log").write_text("l1\nl2\nl3\n")
    stats = sessions.session_diff_stats(tmp_path, sid, cfg.Settings(rules=(("ignore", "*.log"),)))
    # drop.log is ignored -> outside the tracked set -> not in the summary
    assert stats == (0, 0, 0)
