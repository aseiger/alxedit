"""Unit tests for ``.alxeditrc`` parsing and track/ignore semantics."""

from __future__ import annotations

from pathlib import Path

from alxedit2 import sessions
from alxedit2 import settings as cfg


def test_normalize_collapses_dots_and_backslashes() -> None:
    assert cfg.normalize("a/b") == "a/b"
    assert cfg.normalize("./a/b/") == "a/b"
    assert cfg.normalize("a\\b") == "a/b"
    assert cfg.normalize("  .env  ") == ".env"
    assert cfg.normalize("a/./b/") == "a/b"
    assert cfg.normalize("  ") == ""


def test_parse_directives_comments_and_junk() -> None:
    text = (
        "# comment\n"
        "\n"
        "ignore assets/images\n"
        "ignore big.png\n"
        "track .env\n"
        "track .github\n"
        "bogus something\n"   # unknown directive: skipped
        "ignore\n"            # missing path: skipped
        "ignore ../escape\n"  # escapes the root: skipped
        "track  .env\n"       # duplicate: de-duplicated
    )
    s = cfg.parse(text)
    assert s.ignore == ("assets/images", "big.png")
    assert s.track == (".env", ".github")


def test_load_missing_is_empty_and_roundtrip(tmp_path: Path) -> None:
    assert cfg.load(tmp_path) == cfg.Settings()
    s = cfg.Settings(ignore=("assets", "big.png"), track=(".env",))
    cfg.save(tmp_path, s)
    assert cfg.load(tmp_path) == s
    assert (tmp_path / cfg.RC_NAME).is_file()


def test_should_track_defaults_and_overrides() -> None:
    s = cfg.Settings(ignore=("assets",), track=(".env", ".github"))
    assert cfg.should_track(s, "app.js")
    assert not cfg.should_track(s, ".env2")  # untracked dot file
    assert not cfg.should_track(s, ".github2/ci")  # prefix != folder match
    assert cfg.should_track(s, ".env")  # explicitly tracked
    assert cfg.should_track(s, ".github/ci.yml")  # tracked folder covers children
    assert not cfg.should_track(s, "assets/x.png")  # ignored folder covers children
    assert not cfg.should_track(s, "assets")  # the ignored entry itself
    assert cfg.should_track(s, "assets2/x.png")  # not a substring match
    # ignore wins over track
    both = cfg.Settings(ignore=(".env",), track=(".env",))
    assert not cfg.should_track(both, ".env")


def test_matching_is_case_insensitive() -> None:
    assert not cfg.should_track(cfg.Settings(ignore=("Assets",)), "assets/x.png")
    assert cfg.should_track(cfg.Settings(track=(".ENV",)), ".env")


def test_iter_tracked_files_respects_settings(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text("x")
    (tmp_path / ".env").write_text("x")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "big.png").write_text("x")
    (tmp_path / ".alxedit").mkdir()
    (tmp_path / ".alxedit" / "sid").mkdir()

    def names(paths: list[Path]) -> set[str]:
        return {p.name for p in paths}

    default = sessions.iter_tracked_files(tmp_path)
    assert names(default) == {"app.js", "big.png"}

    tracked = sessions.iter_tracked_files(tmp_path, cfg.Settings(track=(".env",)))
    assert names(tracked) == {"app.js", "big.png", ".env"}

    ignored = sessions.iter_tracked_files(
        tmp_path, cfg.Settings(ignore=("assets",))
    )
    assert names(ignored) == {"app.js"}


# --------------------------------------------------------------------------- #
# glob entries
# --------------------------------------------------------------------------- #


def test_glob_extension_matches_anywhere() -> None:
    s = cfg.Settings(ignore=("*.log",))
    assert not cfg.should_track(s, "a.log")
    assert not cfg.should_track(s, "src/deep/a.log")  # * crosses folders
    assert cfg.should_track(s, "app.js")
    assert cfg.should_track(s, "a.log.txt")  # ends with .txt, not .log


def test_glob_folder_wildcard_covers_children() -> None:
    s = cfg.Settings(ignore=("dist/*",))
    assert not cfg.should_track(s, "dist/out.js")
    assert not cfg.should_track(s, "dist/sub/bundle.js")
    assert cfg.should_track(s, "src/out.js")


def test_glob_can_track_dotfolders() -> None:
    s = cfg.Settings(track=(".github/*",))
    assert cfg.should_track(s, ".github/workflows/ci.yml")
    assert cfg.should_track(s, ".github/dependabot.yml")
    assert not cfg.should_track(s, ".env")  # unrelated dot file stays off


def test_glob_is_case_insensitive() -> None:
    s = cfg.Settings(ignore=("*.LOG",))
    assert not cfg.should_track(s, "a.log")
    assert not cfg.should_track(s, "a.LOG")


def test_glob_entries_roundtrip_through_save_load(tmp_path: Path) -> None:
    s = cfg.Settings(ignore=("*.log", "dist/*"), track=(".github/*",))
    cfg.save(tmp_path, s)
    assert cfg.load(tmp_path) == s


def test_iter_tracked_files_respects_globs(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text("x")
    (tmp_path / "debug.log").write_text("x")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "deep.log").write_text("x")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "out.js").write_text("x")
    (tmp_path / "ok.txt").write_text("x")

    s = cfg.Settings(ignore=("*.log", "dist/*"))
    names = {p.name for p in sessions.iter_tracked_files(tmp_path, s)}
    assert names == {"app.js", "ok.txt"}
