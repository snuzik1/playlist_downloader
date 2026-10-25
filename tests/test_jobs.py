from __future__ import annotations

import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import support  # noqa: F401 - sets PLDL_HOME before application imports
from pldl.jobs import MANIFEST_NAME, Job, JobManager
from pldl.utils import UserError
from support import make_options, make_playlist, make_track


def new_job(
    tracks,
    *,
    output_dir: Path,
    options: dict | None = None,
    indices: list[int] | None = None,
    title: str = "Test Playlist",
) -> Job:
    playlist = make_playlist(tracks, title=title)
    playlist.ensure_id()
    job = Job(
        playlist,
        list(range(len(tracks))) if indices is None else indices,
        options or make_options(),
        output_dir,
    )
    job.playlist_dir = output_dir
    return job


class NamingTests(unittest.TestCase):
    def test_deduplicates_names_without_depending_on_selection(self) -> None:
        tracks = [
            make_track(0, title="Same", artist="A"),
            make_track(1, title="Same", artist="A"),
            make_track(2, title="Third", artist=""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            job = new_job(
                tracks,
                output_dir=Path(directory),
                indices=[1],
                options=make_options(index_prefix=False),
            )
            manager = JobManager()
            names = manager._all_base_names(job)

        self.assertEqual(
            names,
            {
                0: "A - Same",
                1: "A - Same (2)",
                2: "Third",
            },
        )

    def test_strips_repeated_artist_prefix(self) -> None:
        track = make_track(0, title="Artist - Song", artist="Artist")
        with tempfile.TemporaryDirectory() as directory:
            job = new_job([track], output_dir=Path(directory), options=make_options(index_prefix=False))
            name = JobManager()._base_name_for(job, track, 0)
        self.assertEqual(name, "Artist - Song")

    def test_does_not_prefix_single_track(self) -> None:
        track = make_track(0, title="Song", artist="Artist")
        with tempfile.TemporaryDirectory() as directory:
            job = new_job([track], output_dir=Path(directory))
            name = JobManager()._base_name_for(job, track, 0)
        self.assertEqual(name, "Artist - Song")

    def test_expected_paths_by_format(self) -> None:
        manager = JobManager()
        base = Path("01. Artist - Song")
        self.assertEqual(manager._expected_paths(base, "mp3"), [Path("01. Artist - Song.mp3")])
        self.assertEqual(manager._expected_paths(base, "m4a"), [Path("01. Artist - Song.m4a")])
        original = manager._expected_paths(base, "original")
        self.assertEqual({path.suffix for path in original}, {".mp3", ".m4a", ".opus", ".ogg", ".flac"})


class ManifestTests(unittest.TestCase):
    def test_manifest_path_is_flat_or_per_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = new_job([make_track(0)], output_dir=root)
            manager = JobManager()

            self.assertEqual(manager._manifest_path(job), root / f".pldl_{job.playlist.id}.json")
            job.playlist_dir = root / "Playlist"
            self.assertEqual(manager._manifest_path(job), root / "Playlist" / MANIFEST_NAME)

    def test_load_manifest_resets_invalid_version_and_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = new_job([make_track(0)], output_dir=root)
            manager = JobManager()
            manager._manifest_path(job).write_text(
                json.dumps({"version": 99, "tracks": []}),
                encoding="utf-8",
            )

            manifest = manager._load_manifest(job)

        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["tracks"], {})
        self.assertEqual(manifest["source"], job.playlist.source_url)
        self.assertEqual(manifest["platform"], job.playlist.platform)
        self.assertEqual(manifest["title"], job.playlist.title)


class PrepareStatesTests(unittest.TestCase):
    def test_skips_manifest_entry(self) -> None:
        track = make_track(0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = new_job([track], output_dir=root)
            existing = root / "Already Downloaded.mp3"
            existing.write_bytes(b"x" * 2048)
            manifest = {"tracks": {track.key: {"file": existing.name}}}

            pending, skipped = JobManager()._prepare_states(job, manifest)

            state = job.states[0]
            self.assertEqual((pending, skipped), (0, 1))
            self.assertEqual(state.status, "skipped")
            self.assertEqual(state.filename, existing.name)
            self.assertIsNotNone(state.finished_at)

    def test_skips_existing_audio_and_records_manifest(self) -> None:
        tracks = [make_track(0, title="Song", artist="Artist"), make_track(1, title="Other")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = new_job(tracks, output_dir=root)
            existing = root / "01. Artist - Song.mp3"
            existing.write_bytes(b"x" * 2048)
            manifest = {"tracks": {}}

            pending, skipped = JobManager()._prepare_states(job, manifest)

            state = job.states[0]
            self.assertEqual((pending, skipped), (1, 1))
            self.assertEqual(state.status, "skipped")
            self.assertEqual(state.filename, existing.name)
            self.assertEqual(manifest["tracks"][tracks[0].key]["file"], existing.name)
            self.assertTrue(manifest["tracks"][tracks[0].key]["skipped_existing"])

    def test_deletes_tiny_existing_file_and_downloads_again(self) -> None:
        track = make_track(0, title="Song", artist="Artist")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = new_job([track], output_dir=root)
            broken = root / "Artist - Song.mp3"
            broken.write_bytes(b"tiny")
            manifest = {"tracks": {}}

            pending, skipped = JobManager()._prepare_states(job, manifest)

            self.assertEqual((pending, skipped), (1, 0))
            self.assertFalse(broken.exists())
            self.assertEqual(job.states[0].status, "pending")
            self.assertEqual(job.states[0].base, root / "Artist - Song")

    def test_original_format_accepts_any_supported_suffix(self) -> None:
        track = make_track(0, title="Song", artist="Artist")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = new_job([track], output_dir=root, options=make_options(format="original", quality=0))
            existing = root / "Artist - Song.flac"
            existing.write_bytes(b"x" * 2048)
            manifest = {"tracks": {}}

            pending, skipped = JobManager()._prepare_states(job, manifest)

            self.assertEqual((pending, skipped), (0, 1))
            self.assertEqual(job.states[0].filename, existing.name)


class PlaylistDirectoryTests(unittest.TestCase):
    def test_reuses_empty_and_matching_directories(self) -> None:
        track = make_track(0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = new_job([track], output_dir=root, title="AC/DC: Live")
            manager = JobManager()

            first = manager._choose_playlist_dir(job)
            self.assertEqual(first.name, "AC DC Live")
            self.assertEqual(manager._choose_playlist_dir(job), first)

            (first / "unrelated.txt").write_text("x", encoding="utf-8")
            second = manager._choose_playlist_dir(job)
            self.assertEqual(second.name, "AC DC Live (2)")
            self.assertNotEqual(second, first)

            (second / MANIFEST_NAME).write_text(
                json.dumps({"source": job.playlist.source_url}),
                encoding="utf-8",
            )
            self.assertEqual(manager._choose_playlist_dir(job), second)


class SnapshotTests(unittest.TestCase):
    def test_counts_progress_and_track_rows(self) -> None:
        tracks = [
            make_track(0, title="One"),
            make_track(1, title="Two"),
            make_track(2, title="Three"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            job = new_job(tracks, output_dir=Path(directory))
            job.status = "running"
            job.started_at = time.time() - 10
            job.set_state(0, status="done", progress=100)
            job.set_state(1, status="skipped", progress=100)
            job.set_state(2, status="downloading", progress=50, speed=1000.0, eta=5)

            snapshot = job.snapshot()

        self.assertEqual(snapshot["total"], 3)
        self.assertEqual(snapshot["done"], 1)
        self.assertEqual(snapshot["skipped"], 1)
        self.assertEqual(snapshot["active"], 1)
        self.assertEqual(snapshot["pending"], 0)
        self.assertEqual(snapshot["speed"], 1000.0)
        self.assertEqual(snapshot["eta"], 5)
        self.assertEqual(snapshot["current"], "Three")
        self.assertAlmostEqual(snapshot["progress"], 83.3)
        self.assertFalse(snapshot["terminal"])
        self.assertEqual(len(snapshot["tracks"]), 3)
        self.assertEqual(snapshot["tracks"][2]["number"], 3)
        self.assertEqual(len(snapshot["logs"]), 0)

    def test_completed_job_reports_full_progress_and_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = new_job([make_track(0)], output_dir=Path(directory))
            job.status = "completed"
            snapshot = job.snapshot(include_tracks=False)

        self.assertEqual(snapshot["progress"], 100.0)
        self.assertTrue(snapshot["terminal"])
        self.assertNotIn("tracks", snapshot)
        self.assertNotIn("logs", snapshot)

    def test_set_state_clamps_and_finalizes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = new_job([make_track(0)], output_dir=Path(directory))

            job.set_state(
                0,
                status="error",
                progress=150,
                speed=100.0,
                eta=3,
                filename="file.mp3",
                error="failed",
                hint="hint",
                source="https://source",
            )
            state = job.states[0]
            job.set_state(99, status="done")

        self.assertEqual(state.progress, 100.0)
        self.assertEqual(state.status, "error")
        self.assertIsNone(state.speed)
        self.assertIsNone(state.eta)
        self.assertIsNotNone(state.finished_at)
        self.assertEqual(state.filename, "file.mp3")
        self.assertEqual(state.error, "failed")
        self.assertEqual(state.hint, "hint")
        self.assertEqual(state.source, "https://source")

    def test_log_deduplicates_and_trims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = new_job([make_track(0)], output_dir=Path(directory))
            with contextlib.redirect_stdout(io.StringIO()):
                job.log("same")
                job.log("same")
                for index in range(305):
                    job.log(f"message {index}")

        self.assertEqual(len(job.logs), 300)
        self.assertEqual(job.logs[-1].split("  ", 1)[-1], "message 304")


class RunTests(unittest.TestCase):
    @staticmethod
    def fake_process(track, base, index, playlist, settings, ffmpeg, workdir, callbacks, cover_cache):
        callbacks.state("downloading", 10, None, None)
        path = Path(str(base) + ".mp3")
        path.write_bytes(b"x" * 2048)
        return {
            "path": path,
            "filename": path.name,
            "source_url": "https://example.com/source",
            "warnings": [],
        }

    def test_runs_job_and_writes_manifest(self) -> None:
        tracks = [make_track(0, title="Song", artist="Artist"), make_track(1, title="Other", artist="")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = new_job(
                tracks,
                output_dir=root,
                options=make_options(playlist_folder=False),
            )
            manager = JobManager()
            with contextlib.redirect_stdout(io.StringIO()), patch(
                "pldl.utils.find_ffmpeg", return_value="ffmpeg"
            ), patch("pldl.jobs.process_track", side_effect=self.fake_process):
                manager._run(job)

            self.assertEqual(job.status, "completed")
            self.assertEqual(job.states[0].status, "done")
            self.assertEqual(job.states[1].status, "done")
            self.assertTrue((root / "01. Artist - Song.mp3").is_file())
            self.assertTrue((root / "02. Other.mp3").is_file())
            manifest = json.loads((root / f".pldl_{job.playlist.id}.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["tracks"]), 2)
            self.assertEqual(job.snapshot()["progress"], 100.0)
            self.assertFalse((root / ".pldl_tmp" / job.id).exists())

    def test_missing_ffmpeg_marks_pending_tracks_as_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = new_job(
                [make_track(0)],
                output_dir=Path(directory),
                options=make_options(playlist_folder=False),
            )
            with contextlib.redirect_stdout(io.StringIO()), patch(
                "pldl.utils.find_ffmpeg", return_value=None
            ), patch("pldl.jobs.process_track") as process:
                JobManager()._run(job)

            self.assertEqual(job.status, "error")
            self.assertEqual(job.states[0].status, "error")
            self.assertIn("FFmpeg", job.states[0].error or "")
            process.assert_not_called()

    def test_existing_file_skips_ffmpeg_and_completes(self) -> None:
        track = make_track(0, title="Song", artist="Artist")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Artist - Song.mp3").write_bytes(b"x" * 2048)
            job = new_job(
                [track],
                output_dir=root,
                options=make_options(playlist_folder=False),
            )
            with contextlib.redirect_stdout(io.StringIO()), patch(
                "pldl.utils.find_ffmpeg"
            ) as find_ffmpeg:
                JobManager()._run(job)

            self.assertEqual(job.status, "completed")
            self.assertEqual(job.states[0].status, "skipped")
            find_ffmpeg.assert_not_called()

    def test_track_error_marks_job_completed_with_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = new_job(
                [make_track(0)],
                output_dir=Path(directory),
                options=make_options(playlist_folder=False),
            )
            with contextlib.redirect_stdout(io.StringIO()), patch(
                "pldl.utils.find_ffmpeg", return_value="ffmpeg"
            ), patch(
                "pldl.jobs.process_track",
                side_effect=UserError("boom", hint="try again"),
            ):
                JobManager()._run(job)

            self.assertEqual(job.status, "completed_with_errors")
            self.assertEqual(job.states[0].status, "error")
            self.assertEqual(job.states[0].error, "boom")
            self.assertEqual(job.states[0].hint, "try again")


class ManagerTests(unittest.TestCase):
    def test_get_missing_raises(self) -> None:
        with self.assertRaises(UserError):
            JobManager().get("missing")

    def test_cancel_active_and_terminal_jobs(self) -> None:
        manager = JobManager()
        with tempfile.TemporaryDirectory() as directory:
            active = new_job([make_track(0)], output_dir=Path(directory))
            active.status = "running"
            finished = new_job([make_track(0)], output_dir=Path(directory))
            finished.status = "completed"
            manager.jobs[active.id] = active
            manager.jobs[finished.id] = finished

            self.assertIs(manager.cancel(active.id), active)
            self.assertTrue(active.cancelled)
            self.assertEqual(active.status, "cancelling")
            self.assertIs(manager.cancel(finished.id), finished)
            self.assertFalse(finished.cancelled)
            self.assertEqual(finished.status, "completed")

    def test_clear_finished_removes_only_terminal_jobs(self) -> None:
        manager = JobManager()
        manager.jobs = {
            "done": SimpleNamespace(status="completed"),
            "error": SimpleNamespace(status="error"),
            "running": SimpleNamespace(status="running"),
        }

        removed = manager.clear_finished()

        self.assertEqual(set(removed), {"done", "error"})
        self.assertIn("running", manager.jobs)

    def test_retry_recreates_only_failed_tracks(self) -> None:
        manager = JobManager()
        with tempfile.TemporaryDirectory() as directory:
            job = new_job(
                [make_track(0), make_track(1), make_track(2)],
                output_dir=Path(directory),
            )
            job.status = "completed_with_errors"
            job.set_state(0, status="error", error="x")
            job.set_state(1, status="done")
            job.set_state(2, status="cancelled")
            manager.jobs[job.id] = job

            sentinel = object()
            with patch.object(manager, "create", return_value=sentinel) as create:
                result = manager.retry(job.id)

            self.assertIs(result, sentinel)
            create.assert_called_once_with(job.playlist, [0, 2], job.options)

    def test_retry_rejects_running_and_clean_jobs(self) -> None:
        manager = JobManager()
        with tempfile.TemporaryDirectory() as directory:
            running = new_job([make_track(0)], output_dir=Path(directory))
            running.status = "running"
            manager.jobs[running.id] = running
            with self.assertRaisesRegex(UserError, "выполняется"):
                manager.retry(running.id)

            clean = new_job([make_track(0)], output_dir=Path(directory))
            clean.status = "completed"
            clean.set_state(0, status="done")
            manager.jobs[clean.id] = clean
            with self.assertRaisesRegex(UserError, "нет треков"):
                manager.retry(clean.id)

    def test_create_rejects_empty_selection(self) -> None:
        manager = JobManager()
        playlist = make_playlist([make_track(0)])
        with self.assertRaisesRegex(UserError, "ни одного трека"):
            manager.create(playlist, [], {})
        with self.assertRaisesRegex(UserError, "ни одного трека"):
            manager.create(playlist, [99], {})

    def test_prune_keeps_latest_twenty_finished_jobs(self) -> None:
        manager = JobManager()
        manager.jobs = {
            f"job-{index}": SimpleNamespace(
                id=f"job-{index}",
                status="completed",
                created_at=float(index),
            )
            for index in range(25)
        }

        manager._prune()

        self.assertEqual(len(manager.jobs), 20)
        self.assertNotIn("job-0", manager.jobs)
        self.assertNotIn("job-4", manager.jobs)
        self.assertIn("job-5", manager.jobs)

    def test_prune_does_not_remove_active_jobs(self) -> None:
        manager = JobManager()
        manager.jobs = {
            "active": SimpleNamespace(id="active", status="running", created_at=0.0),
            **{
                f"job-{index}": SimpleNamespace(
                    id=f"job-{index}",
                    status="completed",
                    created_at=float(index + 1),
                )
                for index in range(19)
            },
        }

        manager._prune()

        self.assertEqual(len(manager.jobs), 20)
        self.assertIn("active", manager.jobs)


if __name__ == "__main__":
    unittest.main()
