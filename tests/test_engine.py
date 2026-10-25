from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from yt_dlp.utils import DownloadCancelled

import support
from pldl import engine
from pldl.engine import CoverCache, EngineCallbacks
from pldl.models import Playlist, Track
from pldl.utils import UserError
from support import make_playlist, make_track


class Callbacks:
    def __init__(self) -> None:
        self.states: list[tuple[str, float | None, float | None, int | None]] = []
        self.logs: list[str] = []
        self.sources: list[tuple[str, str]] = []
        self.cancel_flag = False

    def state(self, status: str, progress: float | None, speed: float | None, eta: int | None) -> None:
        self.states.append((status, progress, speed, eta))

    def log(self, message: str) -> None:
        self.logs.append(message)

    def cancel(self) -> bool:
        return self.cancel_flag

    def source(self, url: str, title: str) -> None:
        self.sources.append((url, title))

    def build(self) -> EngineCallbacks:
        return EngineCallbacks(
            state=self.state,
            log=self.log,
            cancel=self.cancel,
            source=self.source,
        )


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        *,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks or []
        self.closed = False

    def iter_content(self, chunk_size: int):
        return iter(self.chunks)

    def close(self) -> None:
        self.closed = True


class CandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.track = Track(id="1", title="Song", artists=["Artist"], duration=180)

    def test_score_prefers_official_topic_match(self) -> None:
        good = {
            "title": "Artist - Song (Official Audio)",
            "channel": "Artist - Topic",
            "duration": 181,
            "view_count": 100_000,
        }
        bad = {
            "title": "Artist - Song karaoke version",
            "channel": "Karaoke Channel",
            "duration": 240,
        }
        self.assertGreater(
            engine._score_candidate(self.track, good),
            engine._score_candidate(self.track, bad),
        )

    def test_pick_best_candidate(self) -> None:
        good = {
            "url": "https://example.com/good",
            "title": "Artist - Song (Official Audio)",
            "channel": "Artist - Topic",
            "duration": 181,
        }
        worse = {
            "url": "https://example.com/worse",
            "title": "Artist - Song live",
            "channel": "Some Channel",
            "duration": 230,
        }
        self.assertEqual(engine._pick_candidate(self.track, [worse, good]), good)

    def test_pick_rejects_wrong_duration(self) -> None:
        candidate = {
            "title": "Artist - Song",
            "channel": "Artist - Topic",
            "duration": 500,
        }
        self.assertIsNone(engine._pick_candidate(self.track, [candidate]))

    def test_pick_rejects_unrelated_title_without_duration(self) -> None:
        candidate = {"title": "Completely Different", "channel": "Nobody"}
        self.assertIsNone(engine._pick_candidate(self.track, [candidate]))

    def test_pick_empty_list(self) -> None:
        self.assertIsNone(engine._pick_candidate(self.track, []))


class StateHookTests(unittest.TestCase):
    def test_reports_progress_speed_and_eta(self) -> None:
        callbacks = Callbacks()
        hook = engine._state_hook(callbacks.build())

        hook(
            {
                "status": "downloading",
                "total_bytes": 100,
                "downloaded_bytes": 20,
                "speed": 1024.0,
                "eta": 7,
            }
        )
        status, percent, speed, eta = callbacks.states[-1]
        self.assertEqual(status, "downloading")
        self.assertAlmostEqual(percent, 21.6)
        self.assertEqual(speed, 1024.0)
        self.assertEqual(eta, 7)

        hook({"status": "finished"})
        self.assertEqual(callbacks.states[-1], ("downloading", 84.0, None, None))

    def test_unknown_total_uses_estimate(self) -> None:
        callbacks = Callbacks()
        hook = engine._state_hook(callbacks.build())
        hook({"status": "downloading", "total_bytes": 0, "downloaded_bytes": 8 * 1024 * 1024})
        self.assertEqual(callbacks.states[-1][1], 84.0)

    def test_cancel_raises(self) -> None:
        callbacks = Callbacks()
        callbacks.cancel_flag = True
        hook = engine._state_hook(callbacks.build())
        with self.assertRaises(DownloadCancelled):
            hook({"status": "downloading", "downloaded_bytes": 1})


class FindSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine._SEARCH_CACHE.clear()

    def tearDown(self) -> None:
        engine._SEARCH_CACHE.clear()

    def test_uses_configured_source_and_caches_result(self) -> None:
        callbacks = Callbacks()
        settings = {"match_source": "soundcloud"}
        track = Track(id="1", title="Song", artists=["Artist"], duration=180)
        candidate = {
            "url": "https://soundcloud.com/artist/song",
            "title": "Artist - Song",
            "duration": 181,
            "channel": "Artist",
        }
        with patch.object(engine.ytdlp_source, "search", return_value=[candidate]) as search:
            source, chosen = engine._find_source(track, settings, callbacks.build())
            second = engine._find_source(track, settings, callbacks.build())

        self.assertEqual(source, "soundcloud")
        self.assertEqual(chosen, candidate)
        self.assertEqual(second, ("soundcloud", candidate))
        search.assert_called_once_with(track.query, "soundcloud", settings, limit=8)
        self.assertTrue(any("Найдено на SoundCloud" in line for line in callbacks.logs))

    def test_falls_back_to_second_source(self) -> None:
        callbacks = Callbacks()
        track = Track(id="1", title="Song", artists=["Artist"], duration=180)
        candidate = {
            "url": "https://soundcloud.com/artist/song",
            "title": "Artist - Song",
            "duration": 180,
            "channel": "Artist",
        }
        with patch.object(engine.ytdlp_source, "search", side_effect=[[], [candidate]]) as search:
            source, chosen = engine._find_source(track, {}, callbacks.build())

        self.assertEqual(source, "soundcloud")
        self.assertEqual(chosen, candidate)
        self.assertEqual(search.call_count, 2)
        self.assertTrue(any("ничего не найдено" in line for line in callbacks.logs))

    def test_raises_when_nothing_found(self) -> None:
        callbacks = Callbacks()
        track = Track(id="1", title="Song", artists=["Artist"], duration=180)
        with patch.object(engine.ytdlp_source, "search", return_value=[]):
            with self.assertRaises(UserError):
                engine._find_source(track, {}, callbacks.build())

    def test_cancel_prevents_search(self) -> None:
        callbacks = Callbacks()
        callbacks.cancel_flag = True
        track = Track(id="1", title="Song", artists=["Artist"])
        with patch.object(engine.ytdlp_source, "search") as search:
            with self.assertRaises(DownloadCancelled):
                engine._find_source(track, {}, callbacks.build())
        search.assert_not_called()


class DownloadHttpTests(unittest.TestCase):
    def test_downloads_file_and_reports_progress(self) -> None:
        callbacks = Callbacks()
        response = FakeResponse(
            headers={"Content-Length": "2048"},
            chunks=[b"a" * 1024, b"b" * 1024],
        )
        session = Mock()
        session.get.return_value = response

        with tempfile.TemporaryDirectory() as directory:
            result = engine._download_http(
                "https://example.com/audio.mp3?token=1",
                Path(directory),
                callbacks.build(),
                session,
            )
            self.assertEqual(result.name, "raw.mp3")
            self.assertEqual(result.stat().st_size, 2048)

        self.assertEqual(callbacks.states[0], ("downloading", 6, None, None))
        self.assertAlmostEqual(callbacks.states[-1][1], 84.0)
        self.assertTrue(response.closed)
        self.assertEqual(session.get.call_args.kwargs["headers"]["Referer"], "https://vk.com/")

    def test_uses_content_type_when_url_has_no_audio_suffix(self) -> None:
        callbacks = Callbacks()
        response = FakeResponse(
            headers={"Content-Type": "audio/mp4", "Content-Length": "2048"},
            chunks=[b"x" * 2048],
        )
        session = Mock()
        session.get.return_value = response

        with tempfile.TemporaryDirectory() as directory:
            result = engine._download_http(
                "https://example.com/download?id=1",
                Path(directory),
                callbacks.build(),
                session,
            )
            self.assertEqual(result.suffix, ".m4a")

    def test_rejects_http_error(self) -> None:
        callbacks = Callbacks()
        response = FakeResponse(status=403)
        session = Mock()
        session.get.return_value = response

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(UserError, "HTTP 403"):
                engine._download_http(
                    "https://example.com/audio.mp3",
                    Path(directory),
                    callbacks.build(),
                    session,
                )
        self.assertTrue(response.closed)

    def test_rejects_short_file(self) -> None:
        callbacks = Callbacks()
        response = FakeResponse(chunks=[b"x" * 10])
        session = Mock()
        session.get.return_value = response

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(UserError, "пустой"):
                engine._download_http(
                    "https://example.com/audio.mp3",
                    Path(directory),
                    callbacks.build(),
                    session,
                )

    def test_cancel_raises_and_closes_response(self) -> None:
        callbacks = Callbacks()
        callbacks.cancel_flag = True
        response = FakeResponse(chunks=[b"x" * 2048])
        session = Mock()
        session.get.return_value = response

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DownloadCancelled):
                engine._download_http(
                    "https://example.com/audio.mp3",
                    Path(directory),
                    callbacks.build(),
                    session,
                )
        self.assertTrue(response.closed)


class ConvertTests(unittest.TestCase):
    def test_original_copies_known_formats(self) -> None:
        for suffix, output_suffix in (
            (".mp3", ".mp3"),
            (".m4a", ".m4a"),
            (".flac", ".flac"),
            (".ogg", ".ogg"),
            (".oga", ".ogg"),
            (".opus", ".opus"),
        ):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                raw = root / f"raw{suffix}"
                raw.write_bytes(b"audio-bytes")
                target, warnings = engine._convert(
                    root / "converted",
                    raw,
                    "original",
                    0,
                    "",
                )
                self.assertEqual(target.name, f"converted{output_suffix}")
                self.assertEqual(target.read_bytes(), b"audio-bytes")
                self.assertEqual(warnings, [])

    def test_original_unknown_falls_back_to_m4a(self) -> None:
        def fake_ffmpeg(ffmpeg: str, args: list[str]) -> subprocess.CompletedProcess[bytes]:
            Path(args[-1]).write_bytes(b"converted")
            return subprocess.CompletedProcess(args, 0, b"", b"")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw.unknown"
            raw.write_bytes(b"audio")
            with patch.object(engine, "_ffmpeg", side_effect=fake_ffmpeg) as ffmpeg:
                target, warnings = engine._convert(root / "converted", raw, "original", 0, "ffmpeg")

            self.assertEqual(target.suffix, ".m4a")
            self.assertTrue(any("M4A 256" in warning for warning in warnings))
            ffmpeg.assert_called_once()

    def test_mp3_matching_bitrate_is_copied_without_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw.mp3"
            raw.write_bytes(b"audio")
            with patch.object(engine, "_audio_bitrate_kbps", return_value=320), patch.object(
                engine, "_ffmpeg"
            ) as ffmpeg:
                target, warnings = engine._convert(root / "converted", raw, "mp3", 320, "ffmpeg")

            self.assertEqual(target.suffix, ".mp3")
            self.assertEqual(warnings, [])
            ffmpeg.assert_not_called()

    def test_mp3_mismatched_bitrate_is_transcoded_with_warning(self) -> None:
        def fake_ffmpeg(ffmpeg: str, args: list[str]) -> subprocess.CompletedProcess[bytes]:
            Path(args[-1]).write_bytes(b"converted")
            return subprocess.CompletedProcess(args, 0, b"", b"")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw.mp3"
            raw.write_bytes(b"audio")
            with patch.object(engine, "_audio_bitrate_kbps", return_value=128), patch.object(
                engine, "_ffmpeg", side_effect=fake_ffmpeg
            ) as ffmpeg:
                target, warnings = engine._convert(root / "converted", raw, "mp3", 320, "ffmpeg")

            self.assertEqual(target.suffix, ".mp3")
            self.assertTrue(any("128" in warning and "320" in warning for warning in warnings))
            args = ffmpeg.call_args.args[1]
            self.assertIn("libmp3lame", args)
            self.assertIn("320k", args)

    def test_m4a_failure_raises_user_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw.m4a"
            raw.write_bytes(b"broken")
            failure = subprocess.CompletedProcess([], 1, b"", b"conversion failed")
            with patch.object(engine, "_audio_bitrate_kbps", return_value=None), patch.object(
                engine, "_ffmpeg", return_value=failure
            ):
                with self.assertRaisesRegex(UserError, "M4A"):
                    engine._convert(root / "converted", raw, "m4a", 256, "ffmpeg")

    def test_unknown_target_format_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "raw.mp3"
            raw.write_bytes(b"audio")
            with self.assertRaisesRegex(UserError, "Неизвестный формат"):
                engine._convert(Path(directory) / "converted", raw, "wav", 0, "")

    def test_audio_bitrate_reads_fixture(self) -> None:
        bitrate = engine._audio_bitrate_kbps(support.FIXTURES / "silence.mp3")
        self.assertIsNotNone(bitrate)
        self.assertGreater(bitrate or 0, 0)


class TagMetadataTests(unittest.TestCase):
    def test_prefers_download_info_and_strips_artist_prefix(self) -> None:
        track = Track(
            id="1",
            title="Artist - Song",
            artists=["Artist", "Guest"],
            album="Original Album",
            year="2001",
            disc_number=2,
            source_url="https://example.com/track",
        )
        playlist = Playlist(
            platform="test",
            kind="playlist",
            title="List",
            tracks=[track, Track(id="2", title="Other"), Track(id="3", title="Third")],
            source_url="https://example.com/list",
        )

        meta = engine._tag_metadata(
            track,
            playlist,
            1,
            {
                "track": "Song",
                "artist": "Artist",
                "album": "Downloaded Album",
                "release_year": 2024,
            },
        )

        self.assertEqual(meta["title"], "Song")
        self.assertEqual(meta["artist"], "Artist")
        self.assertEqual(meta["album"], "Downloaded Album")
        self.assertEqual(meta["year"], "2024")
        self.assertEqual(meta["album_artist"], "Artist")
        self.assertEqual(meta["track_number"], 2)
        self.assertEqual(meta["total_tracks"], 3)
        self.assertEqual(meta["disc_number"], 2)
        self.assertEqual(meta["comment"], "Источник: https://example.com/track")

    def test_without_info_uses_playlist_values(self) -> None:
        track = Track(id="1", title="Song", artists=["Artist"], album="Album", year="1999")
        playlist = Playlist(
            platform="test",
            kind="track",
            title="Song",
            tracks=[track],
            source_url="https://example.com/list",
        )
        meta = engine._tag_metadata(track, playlist, 0, None)
        self.assertEqual(meta["title"], "Song")
        self.assertEqual(meta["album"], "Album")
        self.assertEqual(meta["year"], "1999")
        self.assertEqual(meta["total_tracks"], 1)


class ProcessTrackTests(unittest.TestCase):
    def test_direct_download_converts_and_tags(self) -> None:
        callbacks = Callbacks()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "out" / "01. Artist - Song"
            base.parent.mkdir(parents=True)
            workdir = root / "work"
            track = make_track(
                0,
                title="Song",
                artist="Artist",
                source_url="https://example.com/source",
            )
            playlist = make_playlist([track])

            def fake_download(url, settings, target_dir, cb):
                raw = Path(target_dir) / "raw.mp3"
                shutil.copy2(support.FIXTURES / "silence.mp3", raw)
                return raw, {
                    "track": "Downloaded Song",
                    "artist": "Downloaded Artist",
                    "album": "Downloaded Album",
                    "release_year": 2022,
                }

            with patch.object(engine, "_download_ytdlp", side_effect=fake_download):
                result = engine.process_track(
                    track,
                    base,
                    0,
                    playlist,
                    {"format": "original", "quality": 0},
                    "",
                    workdir,
                    callbacks.build(),
                    CoverCache(),
                )

            final = Path(result["path"])
            self.assertEqual(final.name, "01. Artist - Song.mp3")
            self.assertTrue(final.is_file())
            self.assertGreater(final.stat().st_size, 1024)
            self.assertEqual(result["source_url"], "https://example.com/source")
            self.assertEqual(result["warnings"], [])
            self.assertFalse(workdir.exists())

            from mutagen.id3 import ID3

            tags = ID3(final)
            self.assertEqual(str(tags["TIT2"]), "Downloaded Song")
            self.assertEqual(str(tags["TPE1"]), "Downloaded Artist")
            self.assertEqual(str(tags["TALB"]), "Downloaded Album")

    def test_existing_final_file_gets_unique_name(self) -> None:
        callbacks = Callbacks()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "out" / "Song"
            base.parent.mkdir(parents=True)
            (base.parent / "Song.mp3").write_bytes(b"x" * 2048)
            workdir = root / "work"
            track = make_track(0, title="Song", artist="Artist", source_url="https://example.com/source")
            playlist = make_playlist([track])

            def fake_download(url, settings, target_dir, cb):
                raw = Path(target_dir) / "raw.mp3"
                shutil.copy2(support.FIXTURES / "silence.mp3", raw)
                return raw, {}

            with patch.object(engine, "_download_ytdlp", side_effect=fake_download):
                result = engine.process_track(
                    track,
                    base,
                    0,
                    playlist,
                    {"format": "original", "quality": 0},
                    "",
                    workdir,
                    callbacks.build(),
                    CoverCache(),
                )

            self.assertEqual(Path(result["path"]).name, "Song (2).mp3")

    def test_cancel_after_download_cleans_workdir(self) -> None:
        callbacks = Callbacks()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workdir = root / "work"
            track = make_track(0, source_url="https://example.com/source")
            playlist = make_playlist([track])

            def fake_download(url, settings, target_dir, cb):
                raw = Path(target_dir) / "raw.mp3"
                shutil.copy2(support.FIXTURES / "silence.mp3", raw)
                callbacks.cancel_flag = True
                return raw, {}

            with patch.object(engine, "_download_ytdlp", side_effect=fake_download):
                with self.assertRaises(DownloadCancelled):
                    engine.process_track(
                        track,
                        root / "Song",
                        0,
                        playlist,
                        {"format": "original", "quality": 0},
                        "",
                        workdir,
                        callbacks.build(),
                        CoverCache(),
                    )

            self.assertFalse(workdir.exists())

    def test_unavailable_track_without_source_raises(self) -> None:
        callbacks = Callbacks()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workdir = root / "work"
            track = make_track(0, available=False)
            playlist = make_playlist([track])

            with self.assertRaisesRegex(UserError, "недоступный"):
                engine.process_track(
                    track,
                    root / "Song",
                    0,
                    playlist,
                    {"format": "original", "quality": 0},
                    "",
                    workdir,
                    callbacks.build(),
                    CoverCache(),
                )
            self.assertFalse(workdir.exists())


class CoverTests(unittest.TestCase):
    def test_cover_cache_caches_successes_and_failures(self) -> None:
        session = Mock()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            engine, "_fetch_cover", return_value=(b"jpeg", "image/jpeg")
        ) as fetch:
            cache = CoverCache()
            workdir = Path(directory)
            self.assertEqual(cache.get("https://example.com/a", workdir, "", session), (b"jpeg", "image/jpeg"))
            self.assertEqual(cache.get("https://example.com/a", workdir, "", session), (b"jpeg", "image/jpeg"))
            fetch.assert_called_once()

            with patch.object(engine, "_fetch_cover", return_value=None) as missing:
                self.assertIsNone(cache.get("https://example.com/b", workdir, "", session))
                self.assertIsNone(cache.get("https://example.com/b", workdir, "", session))
                missing.assert_called_once()

    def test_fetch_cover_returns_png(self) -> None:
        response = FakeResponse(
            headers={"Content-Type": "image/png"},
            chunks=[b"\x89PNG\r\n\x1a\n" + b"data"],
        )
        session = Mock()
        session.get.return_value = response
        with tempfile.TemporaryDirectory() as directory:
            result = engine._fetch_cover(
                "https://example.com/cover",
                Path(directory),
                "",
                session,
            )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[1], "image/png")
        self.assertTrue(response.closed)

    def test_fetch_cover_returns_none_for_http_error(self) -> None:
        response = FakeResponse(status=404)
        session = Mock()
        session.get.return_value = response
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(
                engine._fetch_cover("https://example.com/cover", Path(directory), "", session)
            )
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
