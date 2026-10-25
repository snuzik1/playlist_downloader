from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests

import support  # noqa: F401 - sets PLDL_HOME before application imports
from pldl.utils import (
    UserError,
    add_suffix,
    atomic_write_json,
    clean_title_for_match,
    expand_path,
    extract_url,
    format_duration,
    human_bytes,
    is_probably_url,
    make_session,
    normalize_text,
    read_json,
    resolve_short_url,
    sanitize_filename,
    short_error,
    stable_hash,
    unique_path,
)


class UrlUtilityTests(unittest.TestCase):
    def test_extracts_url_and_strips_trailing_punctuation(self) -> None:
        self.assertEqual(
            extract_url("Смотрите (https://example.com/track/1)."),
            "https://example.com/track/1",
        )

    def test_keeps_balanced_parentheses_in_url(self) -> None:
        self.assertEqual(
            extract_url("https://example.com/wiki/Song_(Remix)"),
            "https://example.com/wiki/Song_(Remix)",
        )

    def test_adds_scheme_to_bare_supported_url(self) -> None:
        self.assertEqual(
            extract_url("open.spotify.com/track/abc"),
            "https://open.spotify.com/track/abc",
        )

    def test_rejects_unsupported_spotify_uri(self) -> None:
        with self.assertRaises(UserError):
            extract_url("spotify:user:someone")

    def test_is_probably_url(self) -> None:
        self.assertTrue(is_probably_url("https://example.com/a"))
        self.assertTrue(is_probably_url("послушай youtu.be/abc"))
        self.assertFalse(is_probably_url("просто текст"))


class FilenameUtilityTests(unittest.TestCase):
    def test_add_suffix_keeps_dots_in_stem(self) -> None:
        self.assertEqual(
            add_suffix(Path("01. Artist - Title"), ".mp3").name,
            "01. Artist - Title.mp3",
        )

    def test_unique_path_uses_disk_and_taken_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "song.mp3"
            original.write_bytes(b"x")

            taken: set[str] = set()
            first = unique_path(original, taken)
            second = unique_path(original, taken)

            self.assertEqual(first.name, "song (2).mp3")
            self.assertEqual(second.name, "song (3).mp3")
            self.assertIn(str(first).lower(), taken)
            self.assertIn(str(second).lower(), taken)

    def test_sanitize_filename_trims_dots_and_controls(self) -> None:
        self.assertEqual(sanitize_filename(" .Name. "), "Name")
        self.assertEqual(sanitize_filename("bad\x00name"), "bad name")
        self.assertEqual(sanitize_filename("", fallback="fallback"), "fallback")


class TextUtilityTests(unittest.TestCase):
    def test_format_duration_rounds_and_handles_invalid(self) -> None:
        self.assertEqual(format_duration(59.6), "1:00")
        self.assertEqual(format_duration(0), "--:--")
        self.assertEqual(format_duration(-1), "--:--")
        self.assertEqual(format_duration(None), "--:--")

    def test_human_bytes(self) -> None:
        self.assertEqual(human_bytes(0), "0 Б")
        self.assertEqual(human_bytes(1023), "1023 Б")
        self.assertEqual(human_bytes(1024), "1.0 КБ")
        self.assertEqual(human_bytes(1536), "1.5 КБ")
        self.assertEqual(human_bytes(1024**2), "1.0 МБ")
        self.assertEqual(human_bytes(2.5 * 1024**3), "2.5 ГБ")

    def test_normalize_text_removes_accents_and_expands_ampersand(self) -> None:
        self.assertEqual(normalize_text("Beyoncé & AC/DC"), "beyonce and ac dc")

    def test_clean_title_for_match_removes_video_markers(self) -> None:
        self.assertEqual(clean_title_for_match("Song (Official Video) [HD]"), "Song")

    def test_short_error_strips_ansi_error_prefix_and_whitespace(self) -> None:
        error = RuntimeError("ERROR: \x1b[31m  something   failed \x1b[0m")
        self.assertEqual(short_error(error), "something failed")

    def test_short_error_truncates(self) -> None:
        self.assertEqual(len(short_error(RuntimeError("x" * 100), limit=12)), 12)

    def test_stable_hash_is_stable_and_limited(self) -> None:
        first = stable_hash("same value", 8)
        self.assertEqual(first, stable_hash("same value", 8))
        self.assertEqual(len(first), 8)
        self.assertNotEqual(first, stable_hash("other value", 8))


class PathAndJsonTests(unittest.TestCase):
    def test_expand_path_handles_relative_absolute_and_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self.assertEqual(expand_path("", base), "")
            self.assertEqual(expand_path("music", base), str((base / "music").resolve()))
            self.assertEqual(expand_path(str(base), base), str(base.resolve()))

    def test_expand_path_expands_environment_variables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            os.environ["PLDL_TEST_PATH"] = str(base / "from-env")
            try:
                self.assertEqual(
                    expand_path("$PLDL_TEST_PATH/track", base),
                    str((base / "from-env" / "track").resolve()),
                )
            finally:
                os.environ.pop("PLDL_TEST_PATH", None)

    def test_json_roundtrip_preserves_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "data.json"
            atomic_write_json(path, {"title": "Песня", "number": 2})

            self.assertEqual(read_json(path), {"title": "Песня", "number": 2})
            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))

    def test_read_json_returns_default_for_invalid_or_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(read_json(path, {"default": True}), {"default": True})
            self.assertEqual(read_json(Path(directory) / "missing.json", 42), 42)


class SessionTests(unittest.TestCase):
    def test_make_session_sets_headers_proxy_and_retries(self) -> None:
        session = make_session({"proxy": "http://127.0.0.1:8080"})
        try:
            self.assertIn("Mozilla/5.0", session.headers["User-Agent"])
            self.assertEqual(session.proxies["http"], "http://127.0.0.1:8080")
            self.assertEqual(session.proxies["https"], "http://127.0.0.1:8080")
            adapter = session.get_adapter("https://example.com")
            self.assertEqual(adapter.max_retries.total, 3)
        finally:
            session.close()

    def test_resolve_short_url_skips_regular_urls(self) -> None:
        session = Mock()
        self.assertEqual(
            resolve_short_url("https://example.com/track", session),
            "https://example.com/track",
        )
        session.get.assert_not_called()

    def test_resolve_short_url_follows_redirect(self) -> None:
        session = Mock()
        response = Mock()
        response.url = "https://open.spotify.com/playlist/final"
        session.get.return_value = response

        self.assertEqual(
            resolve_short_url("https://spotify.link/abc", session),
            "https://open.spotify.com/playlist/final",
        )
        response.close.assert_called_once()

    def test_resolve_short_url_returns_original_on_network_error(self) -> None:
        session = Mock()
        session.get.side_effect = requests.RequestException("boom")
        self.assertEqual(
            resolve_short_url("https://spotify.link/abc", session),
            "https://spotify.link/abc",
        )


if __name__ == "__main__":
    unittest.main()
