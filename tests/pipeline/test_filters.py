from mediascanmonitor.pipeline.filters import extension_matches, is_ignored

IGNORE = frozenset({"@eaDir", "#snapshot"})


def test_is_ignored_matches_ignore_dir_as_path_segment() -> None:
    assert is_ignored("/data/tv/@eaDir/thumb.jpg", IGNORE) is True


def test_is_ignored_matches_when_ignore_dir_is_final_segment() -> None:
    assert is_ignored("/data/tv/show/#snapshot", IGNORE) is True


def test_is_ignored_does_not_match_substring_of_a_segment() -> None:
    # "@eaDir" is a substring of "foo@eaDir" but NOT a whole path segment.
    assert is_ignored("/data/tv/foo@eaDir/file.mkv", IGNORE) is False


def test_is_ignored_clean_path_is_not_ignored() -> None:
    assert is_ignored("/data/tv/Shoresy/S01/ep.mkv", IGNORE) is False


def test_is_ignored_empty_ignore_set_never_ignores() -> None:
    assert is_ignored("/data/tv/@eaDir/x.mkv", frozenset()) is False


def test_extension_matches_empty_set_means_all() -> None:
    # Invariant 1: empty set => match every file.
    assert extension_matches("/data/tv/show/ep.mkv", frozenset()) is True
    assert extension_matches("/data/tv/show/notes.txt", frozenset()) is True


def test_extension_matches_hit() -> None:
    assert extension_matches("/data/tv/show/ep.mkv", frozenset({"mkv", "mp4"})) is True


def test_extension_matches_miss() -> None:
    assert extension_matches("/data/tv/show/ep.avi", frozenset({"mkv", "mp4"})) is False


def test_extension_matches_is_case_insensitive() -> None:
    # FolderRoute.extensions are normalized lowercase; the file on disk may be uppercase.
    assert extension_matches("/data/tv/show/EP.MKV", frozenset({"mkv"})) is True


def test_extension_matches_no_extension_is_a_miss() -> None:
    assert extension_matches("/data/tv/show/README", frozenset({"mkv"})) is False


def test_extension_matches_dotfile_has_no_extension() -> None:
    # ".hidden" is a dotfile, not a "hidden"-extension file.
    assert extension_matches("/data/tv/show/.hidden", frozenset({"hidden"})) is False


def test_extension_matches_ignores_dots_in_directory_names() -> None:
    # The dot is in a directory name; the basename "movie" has no extension.
    assert extension_matches("/data/tv/v1.2/movie", frozenset({"2"})) is False
