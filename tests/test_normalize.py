"""Tests for the pure path/extension normalizers (contract section 1.1)."""

from mediascanmonitor.normalize import normalize_extension, normalize_path


def test_normalize_extension_strips_dot_lowercases_and_trims() -> None:
    assert normalize_extension(".MKV") == "mkv"
    assert normalize_extension("MP4") == "mp4"
    assert normalize_extension(" .Srt ") == "srt"
    assert normalize_extension("mkv") == "mkv"
    assert normalize_extension("  .MP4 ") == "mp4"
    assert normalize_extension("..ass") == "ass"  # multiple leading dots
    assert normalize_extension("tar.gz") == "tar.gz"  # interior dot preserved
    assert normalize_extension("") == ""
    assert normalize_extension(" . ") == ""  # nothing left after stripping


def test_normalize_path_collapses_separators_and_trailing_slash() -> None:
    assert normalize_path("/data/tv/") == "/data/tv"
    assert normalize_path("/data/tv") == "/data/tv"
    assert normalize_path("/data//tv/") == "/data/tv"
    assert normalize_path("/data//media/../media/tv/") == "/data/media/tv"
    assert normalize_path("  /data/media  ") == "/data/media"  # surrounding whitespace
    assert normalize_path("/") == "/"


def test_normalize_path_is_pure_lexical_not_absolute() -> None:
    # a relative path is NOT anchored to the CWD here (absoluteness is validated at the
    # schema boundary); the function stays lexical and deterministic
    assert normalize_path("data/tv/") == "data/tv"
