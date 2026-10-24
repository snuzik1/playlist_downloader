from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# Keep settings/probe files out of the repository during tests.
_TEST_HOME = tempfile.TemporaryDirectory(prefix="pldl-tests-")
os.environ["PLDL_HOME"] = _TEST_HOME.name

from pldl.config import SETTINGS_PATH, normalize_settings, public_settings  # noqa: E402
from pldl.models import Playlist, Track  # noqa: E402
from pldl.platforms import detect_platform  # noqa: E402
from pldl.utils import UserError, extract_url, format_duration, sanitize_filename  # noqa: E402


class UrlTests(unittest.TestCase):
    def test_extracts_url_from_share_text(self) -> None:
        self.assertEqual(
            extract_url('Смотри: "https://open.spotify.com/playlist/abc?si=1".'),
            "https://open.spotify.com/playlist/abc?si=1",
        )

    def test_expands_spotify_uri(self) -> None:
        self.assertEqual(
            extract_url("spotify:track:4uLU6hMCjMI75M1A2tKUQC"),
            "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
        )

    def test_adds_scheme_to_bare_url(self) -> None:
        self.assertEqual(
            extract_url("музыка: music.yandex.ru/users/test/playlists/1"),
            "https://music.yandex.ru/users/test/playlists/1",
        )

    def test_rejects_text_without_url(self) -> None:
        with self.assertRaises(UserError):
            extract_url("просто текст без ссылки")


class PlatformTests(unittest.TestCase):
    def test_detects_supported_platforms(self) -> None:
        cases = {
            "https://open.spotify.com/playlist/abc": "spotify",
            "https://soundcloud.com/artist/sets/example": "soundcloud",
            "https://music.yandex.ru/users/test/playlists/1": "yandex",
            "https://vk.com/music/playlist/1_2": "vk",
            "https://www.deezer.com/album/1": "deezer",
            "https://www.youtube.com/playlist?list=abc": "youtube",
            "https://artist.bandcamp.com/album/example": "bandcamp",
            "https://example.com/playlist": "generic",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(detect_platform(url), expected)


class FilenameTests(unittest.TestCase):
    def test_replaces_windows_forbidden_characters(self) -> None:
        self.assertEqual(sanitize_filename("AC/DC: Live?*"), "AC DC Live")

    def test_prefixes_reserved_windows_names(self) -> None:
        self.assertEqual(sanitize_filename("CON"), "_CON")
        self.assertEqual(sanitize_filename("con.txt"), "_con.txt")

    def test_uses_fallback_for_empty_name(self) -> None:
        self.assertEqual(sanitize_filename("   ", fallback="fallback"), "fallback")

    def test_limits_length(self) -> None:
        self.assertEqual(len(sanitize_filename("a" * 200, max_len=140)), 140)

    def test_formats_duration(self) -> None:
        self.assertEqual(format_duration(3661), "1:01:01")
        self.assertEqual(format_duration(None), "--:--")


class SettingsTests(unittest.TestCase):
    def test_normalizes_invalid_values(self) -> None:
        settings = normalize_settings(
            {
                "output_dir": str(Path(_TEST_HOME.name) / "music"),
                "format": "wav",
                "quality": 999,
                "concurrency": 99,
                "match_source": "unknown",
                "cookies_browser": "netscape",
                "spotify_market": "USA",
                "max_tracks": 99999,
            }
        )
        self.assertEqual(settings["format"], "mp3")
        self.assertEqual(settings["quality"], 320)
        self.assertEqual(settings["concurrency"], 6)
        self.assertEqual(settings["match_source"], "auto")
        self.assertEqual(settings["cookies_browser"], "")
        self.assertEqual(settings["spotify_market"], "US")
        self.assertEqual(settings["max_tracks"], 5000)

    def test_falls_back_to_valid_m4a_quality(self) -> None:
        settings = normalize_settings({"format": "m4a", "quality": 320})
        self.assertEqual(settings["quality"], 256)

    def test_public_settings_mask_secrets(self) -> None:
        settings = normalize_settings(
            {
                "output_dir": str(Path(_TEST_HOME.name) / "music"),
                "spotify_client_secret": "secret",
                "vk_token": "token",
            }
        )
        public = public_settings(settings, None)
        self.assertEqual(public["spotify_client_secret"], "")
        self.assertEqual(public["vk_token"], "")
        self.assertEqual(
            public["secrets_set"],
            {"spotify_client_secret": True, "vk_token": True},
        )

    def test_settings_stay_in_test_home(self) -> None:
        self.assertEqual(SETTINGS_PATH.parent, Path(_TEST_HOME.name))


class ModelTests(unittest.TestCase):
    def test_track_query_and_key(self) -> None:
        track = Track(id="1", title="Song", artists=["Artist"], duration=180)
        self.assertEqual(track.artist, "Artist")
        self.assertEqual(track.query, "Artist - Song")
        self.assertEqual(track.key, "1")

    def test_playlist_total_duration_and_id(self) -> None:
        playlist = Playlist(
            platform="test",
            kind="playlist",
            title="Test",
            tracks=[
                Track(id="1", title="One", duration=180),
                Track(id="2", title="Two", duration=None),
            ],
            source_url="https://example.com/playlist",
        )
        self.assertEqual(playlist.total_duration, 180)
        playlist_id = playlist.ensure_id()
        self.assertEqual(playlist_id, playlist.id)
        self.assertEqual(len(playlist.public()["tracks"]), 2)


if __name__ == "__main__":
    unittest.main()
