from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import support  # noqa: F401 - sets PLDL_HOME before application imports
from pldl import platforms
from pldl.models import Track
from pldl.platforms import deezer, spotify, vk, yandex, ytdlp_source
from pldl.platforms.common import absolute_url, clean_text, fix_yandex_cover, platform_from_host
from pldl.utils import UserError
from support import make_playlist


class CommonTests(unittest.TestCase):
    def test_clean_text_unescapes_html_and_collapses_whitespace(self) -> None:
        self.assertEqual(clean_text("  A &amp; <b>B</b>  "), "A & B")

    def test_absolute_url(self) -> None:
        self.assertIsNone(absolute_url(None))
        self.assertEqual(absolute_url("//cdn.example/cover"), "https://cdn.example/cover")
        self.assertEqual(absolute_url("http://cdn.example/cover"), "http://cdn.example/cover")
        self.assertEqual(absolute_url("cdn.example/cover"), "https://cdn.example/cover")

    def test_fix_yandex_cover_replaces_size_placeholder(self) -> None:
        self.assertEqual(
            fix_yandex_cover("https://avatars.yandex.net/get-music/%%", "400x400"),
            "https://avatars.yandex.net/get-music/400x400",
        )
        self.assertIsNone(fix_yandex_cover(None))

    def test_platform_from_host(self) -> None:
        self.assertEqual(platform_from_host("www.soundcloud.com"), "soundcloud")
        self.assertEqual(platform_from_host("m.music.yandex.ru"), "yandex")
        self.assertEqual(platform_from_host("music.youtube.com"), "youtube")
        self.assertEqual(platform_from_host("artist.bandcamp.com"), "bandcamp")
        self.assertEqual(platform_from_host("example.com"), "generic")


class DeezerTests(unittest.TestCase):
    def test_parse_track(self) -> None:
        raw = {
            "id": 123,
            "title": "Song",
            "duration": 180.9,
            "artist": {"name": "Artist"},
            "album": {
                "title": "Album",
                "cover_xl": "https://cdn.example/cover.jpg",
                "release_date": "2024-03-01",
                "artist": {"name": "Album Artist"},
            },
            "track_position": 2,
            "disk_number": 1,
            "readable": False,
        }

        track = deezer._parse_track(raw, "fallback")

        assert track is not None
        self.assertEqual(track.id, "dz:123")
        self.assertEqual(track.title, "Song")
        self.assertEqual(track.artists, ["Artist"])
        self.assertEqual(track.album, "Album")
        self.assertEqual(track.duration, 180)
        self.assertEqual(track.cover_url, "https://cdn.example/cover.jpg")
        self.assertEqual(track.source_url, "https://www.deezer.com/track/123")
        self.assertEqual(track.year, "2024")
        self.assertEqual(track.track_number, 2)
        self.assertEqual(track.disc_number, 1)
        self.assertFalse(track.available)
        self.assertEqual(track.artist, "Artist")

    def test_parse_track_uses_album_artist_and_fallback_cover(self) -> None:
        raw = {
            "id": 5,
            "title": "Song",
            "album": {"artist": {"name": "Album Artist"}},
        }
        track = deezer._parse_track(raw, "fallback-cover")
        assert track is not None
        self.assertEqual(track.artists, ["Album Artist"])
        self.assertEqual(track.cover_url, "fallback-cover")
        self.assertIsNone(track.duration)

    def test_parse_track_without_id_returns_none(self) -> None:
        self.assertIsNone(deezer._parse_track({"title": "Song"}))

    def test_resolve_routes_playlist_with_locale_prefix(self) -> None:
        session = Mock()
        with patch.object(deezer, "make_session", return_value=session), patch.object(
            deezer, "_playlist", return_value="result"
        ) as playlist:
            result = deezer.resolve("https://www.deezer.com/ru/playlist/123", {})

        self.assertEqual(result, "result")
        playlist.assert_called_once_with(session, "123")
        session.close.assert_called_once()

    def test_resolve_routes_album_track_and_artist(self) -> None:
        cases = [
            ("https://www.deezer.com/album/456", "_album", ("456",)),
            ("https://www.deezer.com/track/789", "_track", ("789",)),
        ]
        for url, function_name, args in cases:
            with self.subTest(url=url), patch.object(
                deezer, "make_session", return_value=Mock()
            ), patch.object(deezer, function_name, return_value="result") as function:
                result = deezer.resolve(url, {})
                self.assertEqual(result, "result")
                function.assert_called_once_with(function.call_args.args[0], *args)

    def test_resolve_rejects_non_deezer_host(self) -> None:
        with patch.object(deezer, "make_session") as make_session:
            with self.assertRaisesRegex(UserError, "Deezer"):
                deezer.resolve("https://example.com/playlist/1", {})
        make_session.assert_not_called()


class SpotifyTests(unittest.TestCase):
    def test_cover(self) -> None:
        self.assertEqual(
            spotify._cover({"images": [{"url": "cover"}]}, "fallback"),
            "cover",
        )
        self.assertEqual(spotify._cover({"images": []}, "fallback"), "fallback")
        self.assertIsNone(spotify._cover(None))

    def test_track_parses_full_metadata(self) -> None:
        raw = {
            "id": "abc",
            "name": "Song",
            "type": "track",
            "artists": [{"name": "Artist"}, {"name": "Guest"}],
            "album": {
                "name": "Album",
                "release_date": "2020-05-01",
                "images": [{"url": "cover"}],
            },
            "duration_ms": 180500,
            "track_number": 2,
            "disc_number": 1,
            "is_playable": False,
            "external_urls": {"spotify": "https://open.spotify.com/track/abc"},
        }

        track = spotify._track(raw)

        assert track is not None
        self.assertEqual(track.id, "sp:abc")
        self.assertEqual(track.title, "Song")
        self.assertEqual(track.artists, ["Artist", "Guest"])
        self.assertEqual(track.album, "Album")
        self.assertEqual(track.duration, 180)
        self.assertEqual(track.cover_url, "cover")
        self.assertEqual(track.year, "2020")
        self.assertEqual(track.track_number, 2)
        self.assertEqual(track.disc_number, 1)
        self.assertFalse(track.available)

    def test_track_uses_album_hint_and_default_url(self) -> None:
        raw = {"id": "abc", "name": "Song", "duration_ms": 1000}
        track = spotify._track(raw, {"name": "Hint", "images": [{"url": "hint-cover"}]})
        assert track is not None
        self.assertEqual(track.album, "Hint")
        self.assertEqual(track.cover_url, "hint-cover")
        self.assertEqual(track.source_url, "https://open.spotify.com/track/abc")

    def test_track_rejects_local_wrong_type_and_missing_id(self) -> None:
        self.assertIsNone(spotify._track({"id": "1", "is_local": True}))
        self.assertIsNone(spotify._track({"id": "1", "type": "episode"}))
        self.assertIsNone(spotify._track({"name": "Song"}))

    def test_resolve_rejects_non_spotify_url(self) -> None:
        with patch.object(spotify, "make_session") as make_session:
            with self.assertRaisesRegex(UserError, "Spotify"):
                spotify.resolve("https://example.com/playlist/abc", {})
        make_session.assert_not_called()

    def test_resolve_uses_api_and_closes_session(self) -> None:
        session = Mock()
        with patch.object(spotify, "make_session", return_value=session), patch.object(
            spotify, "_playlist_api", return_value="api-result"
        ) as api:
            result = spotify.resolve("https://open.spotify.com/playlist/abc", {})

        self.assertEqual(result, "api-result")
        api.assert_called_once_with(api.call_args.args[0], "abc", 1000)
        session.close.assert_called_once()

    def test_resolve_falls_back_to_embed_page(self) -> None:
        session = Mock()
        with patch.object(spotify, "make_session", return_value=session), patch.object(
            spotify, "_playlist_api", side_effect=UserError("region")
        ), patch.object(spotify, "_resolve_embed", return_value="embed-result") as embed:
            result = spotify.resolve("https://open.spotify.com/playlist/abc", {})

        self.assertEqual(result, "embed-result")
        embed.assert_called_once_with("playlist", "abc", 1000, {})
        session.close.assert_called_once()


class VkTests(unittest.TestCase):
    def test_playlist_id_from_url_variants(self) -> None:
        cases = {
            "https://vk.com/music/playlist/-123_456?access_key=abc": ("-123", "456", "abc"),
            "https://vk.com/audio?z=audio_playlist-1_2/abcdef": ("-1", "2", "abcdef"),
            "https://vk.com/playlist/1_2": ("1", "2", None),
            "https://vk.com/audio?z=audio_playlist-1_2": ("-1", "2", None),
            "https://vk.com/artist": None,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(vk._playlist_id_from_url(url), expected)

    def test_photo_prefers_largest_size(self) -> None:
        self.assertEqual(vk._photo("direct"), "direct")
        self.assertEqual(vk._photo({"photo_100": "small", "photo_600": "large"}), "large")
        self.assertIsNone(vk._photo(None))

    def test_track_from_item(self) -> None:
        item = {
            "id": 5,
            "owner_id": -10,
            "artist": "Artist",
            "title": "Song",
            "duration": 180.0,
            "url": "https://example.com/audio.mp3",
        }
        track = vk._track_from_item(item, "Playlist", "cover")

        assert track is not None
        self.assertEqual(track.id, "vk:-10_5")
        self.assertEqual(track.title, "Song")
        self.assertEqual(track.artist, "Artist")
        self.assertEqual(track.album, "Playlist")
        self.assertEqual(track.duration, 180)
        self.assertEqual(track.stream_url, "https://example.com/audio.mp3")
        self.assertTrue(track.available)
        self.assertEqual(track.cover_url, "cover")

    def test_track_from_item_splits_title_and_marks_missing_url(self) -> None:
        track = vk._track_from_item(
            {"id": 1, "owner_id": 2, "title": "Artist - Song"},
            "",
            None,
        )
        assert track is not None
        self.assertEqual(track.artist, "Artist")
        self.assertEqual(track.title, "Song")
        self.assertIsNone(track.stream_url)
        self.assertFalse(track.available)

    def test_track_from_item_without_id_returns_none(self) -> None:
        self.assertIsNone(vk._track_from_item({"title": "Song"}, "", None))

    def test_resolve_requires_token(self) -> None:
        with patch.object(vk, "make_session") as make_session:
            with self.assertRaisesRegex(UserError, "токен"):
                vk.resolve("https://vk.com/music/playlist/1_2", {})
        make_session.assert_not_called()

    def test_resolve_playlist_passes_access_key(self) -> None:
        session = Mock()
        settings = {"vk_token": "token", "max_tracks": 10}
        with patch.object(vk, "make_session", return_value=session), patch.object(
            vk, "_playlist", return_value="result"
        ) as playlist:
            result = vk.resolve("https://vk.com/music/playlist/-1_2?access_key=abc", settings)

        self.assertEqual(result, "result")
        playlist.assert_called_once_with(session, "token", "-1", "2", "abc", 10)
        session.close.assert_called_once()


class YandexTests(unittest.TestCase):
    def test_parse_track(self) -> None:
        raw = {
            "id": "123",
            "title": "Song",
            "artists": [{"name": "Artist"}],
            "albums": [
                {
                    "id": "a1",
                    "title": "Album",
                    "year": 2020,
                    "trackPosition": {"index": 2, "volume": 1},
                    "coverUri": "https://avatars.yandex.net/%%",
                }
            ],
            "durationMs": 180600,
            "available": False,
            "previewDurationMs": 30000,
        }

        track = yandex._parse_track(raw)

        assert track is not None
        self.assertEqual(track.id, "ym:123")
        self.assertEqual(track.title, "Song")
        self.assertEqual(track.artists, ["Artist"])
        self.assertEqual(track.album, "Album")
        self.assertEqual(track.duration, 181)
        self.assertEqual(track.cover_url, "https://avatars.yandex.net/1000x1000")
        self.assertEqual(track.source_url, "https://music.yandex.ru/album/a1/track/123")
        self.assertEqual(track.year, "2020")
        self.assertEqual(track.track_number, 2)
        self.assertEqual(track.disc_number, 1)
        self.assertFalse(track.available)
        self.assertTrue(track.extra["preview"])

    def test_parse_track_requires_id(self) -> None:
        self.assertIsNone(yandex._parse_track({}))
        self.assertIsNone(yandex._parse_track("not a dict"))  # type: ignore[arg-type]

    def test_parse_playlist_deduplicates_tracks(self) -> None:
        raw = {
            "id": "t1",
            "title": "Song",
            "artists": [{"name": "Artist"}],
            "albums": [{"id": "a1", "title": "Album"}],
        }
        data = {
            "title": "My Playlist",
            "owner": {"name": "Owner"},
            "descriptionFormatted": "Description",
            "cover": {"uri": "https://avatars.yandex.net/%%"},
            "trackCount": 3,
            "tracks": [{"track": raw}, raw, {"track": None}],
        }

        title, author, description, cover, tracks, expected = yandex._parse_playlist(data)

        self.assertEqual(title, "My Playlist")
        self.assertEqual(author, "Owner")
        self.assertEqual(description, "Description")
        self.assertEqual(cover, "https://avatars.yandex.net/1000x1000")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(expected, 3)

    def test_resolve_routes_playlist(self) -> None:
        session = Mock()
        with patch.object(yandex, "make_session", return_value=session), patch.object(
            yandex, "_resolve_playlist", return_value="result"
        ) as resolver:
            result = yandex.resolve("https://music.yandex.ru/users/u/playlists/3", {})

        self.assertEqual(result, "result")
        resolver.assert_called_once_with(session, "u", "3")
        session.close.assert_called_once()

    def test_resolve_routes_album_track(self) -> None:
        session = Mock()
        with patch.object(yandex, "make_session", return_value=session), patch.object(
            yandex, "_resolve_track", return_value="result"
        ) as resolver:
            result = yandex.resolve("https://music.yandex.ru/album/10/track/20", {})

        self.assertEqual(result, "result")
        resolver.assert_called_once_with(session, "20", "10")

    def test_resolve_rejects_short_playlist_link(self) -> None:
        session = Mock()
        with patch.object(yandex, "make_session", return_value=session):
            with self.assertRaisesRegex(UserError, "короткие"):
                yandex.resolve("https://music.yandex.ru/playlists/3", {})
        session.close.assert_called_once()

    def test_resolve_rejects_non_yandex_host(self) -> None:
        with patch.object(yandex, "make_session") as make_session:
            with self.assertRaisesRegex(UserError, "Яндекс"):
                yandex.resolve("https://example.com/album/1", {})
        make_session.assert_not_called()


class YtDlpSourceTests(unittest.TestCase):
    def test_split_artist_title(self) -> None:
        self.assertEqual(ytdlp_source._split_artist_title("Artist - Song", "Artist"), ("Song", "Artist"))
        self.assertEqual(ytdlp_source._split_artist_title("Artist - Song", ""), ("Song", "Artist"))
        self.assertEqual(ytdlp_source._split_artist_title("A - B - C", ""), ("B - C", "A"))
        self.assertEqual(ytdlp_source._split_artist_title("Just a title", ""), ("Just a title", ""))

    def test_thumb(self) -> None:
        self.assertEqual(ytdlp_source._thumb({"thumbnail": "thumb"}), "thumb")
        self.assertEqual(
            ytdlp_source._thumb({"thumbnails": [{"url": "small"}, {"url": "large"}]}),
            "large",
        )
        self.assertIsNone(ytdlp_source._thumb({}))

    def test_entry_url_variants(self) -> None:
        self.assertEqual(
            ytdlp_source._entry_url({"webpage_url": "https://example.com/watch"}, "youtube"),
            "https://example.com/watch",
        )
        self.assertEqual(
            ytdlp_source._entry_url({"original_url": "https://example.com/original"}, "youtube"),
            "https://example.com/original",
        )
        self.assertEqual(
            ytdlp_source._entry_url({"url": "https://cdn.example/audio"}, "youtube"),
            "https://cdn.example/audio",
        )
        self.assertEqual(
            ytdlp_source._entry_url({"id": "abc"}, "youtube"),
            "https://www.youtube.com/watch?v=abc",
        )
        self.assertEqual(
            ytdlp_source._entry_url({"id": "artist/song"}, "soundcloud"),
            "https://soundcloud.com/artist/song",
        )
        self.assertIsNone(ytdlp_source._entry_url({"id": "abc"}, "generic"))

    def test_availability(self) -> None:
        self.assertFalse(ytdlp_source._availability({"availability": "private"}))
        self.assertFalse(ytdlp_source._availability({"availability": "premium_only"}))
        self.assertFalse(ytdlp_source._availability({"is_private": True}))
        self.assertTrue(ytdlp_source._availability({}))

    def test_track_from_entry(self) -> None:
        entry = {
            "id": "abc",
            "title": "Artist - Song",
            "uploader": "Artist - Topic",
            "duration": "180.6",
            "album": "Album",
            "release_date": "2021-04-01",
            "thumbnail": "thumb",
            "webpage_url": "https://www.youtube.com/watch?v=abc",
        }

        track = ytdlp_source._track_from_entry(entry, "youtube", 0)

        assert track is not None
        self.assertEqual(track.id, "youtube:abc")
        self.assertEqual(track.title, "Song")
        self.assertEqual(track.artist, "Artist")
        self.assertEqual(track.album, "Album")
        self.assertEqual(track.duration, 181)
        self.assertEqual(track.year, "2021")
        self.assertEqual(track.cover_url, "thumb")
        self.assertEqual(track.source_url, "https://www.youtube.com/watch?v=abc")
        self.assertEqual(track.track_number, 1)

    def test_track_from_entry_uses_fallback_cover_and_title(self) -> None:
        entry = {"id": "abc", "webpage_url": "https://example.com/track", "availability": "private"}
        track = ytdlp_source._track_from_entry(entry, "generic", 4, "playlist-cover")
        assert track is not None
        self.assertEqual(track.title, "Трек 5")
        self.assertEqual(track.cover_url, "playlist-cover")
        self.assertFalse(track.available)

    def test_track_from_entry_rejects_invalid(self) -> None:
        self.assertIsNone(ytdlp_source._track_from_entry("bad", "youtube", 0))  # type: ignore[arg-type]
        self.assertIsNone(ytdlp_source._track_from_entry({"title": "No URL"}, "youtube", 0))

    def test_flatten_entries(self) -> None:
        entries = [
            {"id": "1"},
            {"_type": "playlist", "entries": [{"id": "2"}, None]},
            None,
            {"_type": "playlist", "entries": [{"id": "3"}]},
        ]
        self.assertEqual(
            [entry["id"] for entry in ytdlp_source._flatten_entries(entries)],
            ["1", "2", "3"],
        )
        self.assertEqual(ytdlp_source._flatten_entries(None), [])

    def test_kind_for(self) -> None:
        self.assertEqual(ytdlp_source._kind_for("youtube", "https://youtube.com/watch?v=x", False), "track")
        self.assertEqual(ytdlp_source._kind_for("soundcloud", "https://soundcloud.com/a/sets/b", True), "playlist")
        self.assertEqual(ytdlp_source._kind_for("soundcloud", "https://soundcloud.com/a", True), "artist")
        self.assertEqual(ytdlp_source._kind_for("youtube", "https://youtube.com/@a", True), "artist")
        self.assertEqual(ytdlp_source._kind_for("youtube", "https://youtube.com/playlist?list=x", True), "playlist")
        self.assertEqual(ytdlp_source._kind_for("bandcamp", "https://a.bandcamp.com/album/b", True), "album")
        self.assertEqual(ytdlp_source._kind_for("generic", "https://example.com/a", True), "playlist")

    def test_base_opts_applies_limits_proxy_and_cookies(self) -> None:
        options = ytdlp_source._base_opts(
            {"max_tracks": 50, "proxy": "http://127.0.0.1:8080", "cookies_browser": "chrome"}
        )
        self.assertEqual(options["playlistend"], 51)
        self.assertEqual(options["proxy"], "http://127.0.0.1:8080")
        self.assertEqual(options["cookiesfrombrowser"], ("chrome", None, None, None))
        self.assertEqual(options["extract_flat"], "in_playlist")
        self.assertFalse(ytdlp_source._base_opts({}, flat=False)["extract_flat"])

    def test_logger_cleans_prefix_and_deduplicates(self) -> None:
        messages: list[str] = []
        logger = ytdlp_source._Logger(messages.append)
        logger.warning("\x1b[31mWARNING: same\x1b[0m")
        logger.warning("WARNING: same")
        logger.error("ERROR: other")
        self.assertEqual(messages, ["yt-dlp: same", "yt-dlp: other"])

    def test_raise_download_error_adds_hints(self) -> None:
        with self.assertRaises(UserError) as context:
            ytdlp_source._raise_download_error(RuntimeError("Sign in to confirm you're not a bot"))
        self.assertIn("Cookies", context.exception.hint or "")

        with self.assertRaises(UserError) as context:
            ytdlp_source._raise_download_error(RuntimeError("video unavailable"))
        self.assertIn("недоступен", context.exception.hint or "")

        with self.assertRaises(UserError) as context:
            ytdlp_source._raise_download_error(RuntimeError("unsupported url"))
        self.assertIn("не поддерживается", context.exception.hint or "")


class PlatformRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        platforms.clear_cache()

    def tearDown(self) -> None:
        platforms.clear_cache()

    def test_store_and_get_playlist(self) -> None:
        playlist = make_playlist([Track(id="1", title="Song")], platform="test")
        stored = platforms._store(playlist, "key")

        self.assertIs(stored, playlist)
        self.assertIs(platforms.get_playlist(playlist.id), playlist)

    def test_get_missing_playlist_raises(self) -> None:
        with self.assertRaises(UserError):
            platforms.get_playlist("missing")

    def test_detect_platform_edges(self) -> None:
        self.assertEqual(platforms.detect_platform("https://m.soundcloud.com/a"), "soundcloud")
        self.assertEqual(platforms.detect_platform("https://music.youtube.com/watch?v=x"), "youtube")
        self.assertEqual(platforms.detect_platform("https://sub.bandcamp.com/album/a"), "bandcamp")
        self.assertEqual(platforms.detect_platform("https://vk.ru/audio"), "vk")
        self.assertEqual(platforms.detect_platform("https://example.com/a"), "generic")

    def test_resolve_trims_tracks_and_reuses_cache(self) -> None:
        tracks = [
            Track(id="1", title="One", cover_url="cover"),
            Track(id="2", title="Two"),
            Track(id="3", title="Three"),
        ]
        playlist = make_playlist(tracks, platform="youtube", source_url="https://youtube.com/playlist?list=x")
        playlist.cover_url = None
        settings = {"max_tracks": 2}

        session = Mock()
        with patch.object(platforms, "make_session", return_value=session), patch.object(
            platforms, "resolve_short_url", return_value=playlist.source_url
        ), patch.object(platforms.ytdlp_source, "resolve", return_value=playlist) as resolve_source:
            first = platforms.resolve("https://youtube.com/playlist?list=x", settings)
            second = platforms.resolve("https://youtube.com/playlist?list=x", settings)

        self.assertIs(first, playlist)
        self.assertIs(second, playlist)
        self.assertEqual(len(first.tracks), 2)
        self.assertEqual(first.cover_url, "cover")
        self.assertTrue(any("лимит" in warning for warning in first.warnings))
        resolve_source.assert_called_once()

    def test_resolve_rejects_apple_music(self) -> None:
        with patch.object(platforms, "make_session", return_value=Mock()), patch.object(
            platforms, "resolve_short_url", return_value="https://music.apple.com/album/1"
        ):
            with self.assertRaisesRegex(UserError, "Apple Music"):
                platforms.resolve("https://music.apple.com/album/1", {})


if __name__ == "__main__":
    unittest.main()
