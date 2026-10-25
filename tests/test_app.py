from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import support
from pldl.config import DEFAULT_SETTINGS, load_settings, save_settings
from pldl.utils import UserError
from support import make_playlist
from app import app as flask_app
from app import APP_TOKEN


AUTH = {"X-App-Token": APP_TOKEN}


class FakeJob:
    id = "job-1"
    indices = [0]

    def snapshot(self, include_tracks: bool = True):
        return {"id": self.id, "include_tracks": include_tracks}


class AppTestCase(unittest.TestCase):
    def setUp(self) -> None:
        flask_app.config.update(TESTING=False)
        self.client = flask_app.test_client()

    def post(self, path: str, payload: dict | None = None, **kwargs):
        headers = {**AUTH, **kwargs.pop("headers", {})}
        return self.client.post(path, json=payload or {}, headers=headers, **kwargs)


class GuardAndHealthTests(AppTestCase):
    def test_health_returns_json_and_security_headers(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_index_renders(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PLDL", response.data)
        self.assertIn(APP_TOKEN.encode(), response.data)

    def test_rejects_foreign_host(self) -> None:
        response = self.client.get("/api/health", headers={"Host": "evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()["ok"])

    def test_post_requires_app_token(self) -> None:
        response = self.client.post("/api/resolve", json={"url": "https://example.com"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("токен", response.get_json()["error"]["message"].lower())

    def test_post_rejects_foreign_origin_and_cross_site(self) -> None:
        with patch("app.resolve") as resolver:
            response = self.client.post(
                "/api/resolve",
                json={"url": "https://example.com"},
                headers={**AUTH, "Origin": "https://evil.example"},
            )
            self.assertEqual(response.status_code, 403)
            resolver.assert_not_called()

            response = self.client.post(
                "/api/resolve",
                json={"url": "https://example.com"},
                headers={**AUTH, "Sec-Fetch-Site": "cross-site"},
            )
            self.assertEqual(response.status_code, 403)
            resolver.assert_not_called()

    def test_post_accepts_local_origin(self) -> None:
        playlist = make_playlist([], title="Empty")
        with patch("app.resolve", return_value=playlist):
            response = self.client.post(
                "/api/resolve",
                json={"url": "https://example.com"},
                headers={**AUTH, "Origin": "http://127.0.0.1:8765"},
            )
        self.assertEqual(response.status_code, 200)


class StatusAndResolveTests(AppTestCase):
    def test_status_payload(self) -> None:
        settings = dict(DEFAULT_SETTINGS)
        with patch("app.load_settings", return_value=settings), patch(
            "app.detect_ffmpeg", return_value="C:\\ffmpeg.exe"
        ):
            response = self.client.get("/api/status")

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["ffmpeg"]["found"])
        self.assertEqual(data["ffmpeg"]["path"], "C:\\ffmpeg.exe")
        self.assertTrue(data["settings"]["secrets_set"])
        self.assertTrue(data["platforms"])

    def test_resolve_endpoint(self) -> None:
        playlist = make_playlist([], title="My List")
        with patch("app.resolve", return_value=playlist) as resolver, patch(
            "app.load_settings", return_value={}
        ):
            response = self.post("/api/resolve", {"url": "https://example.com/list"})

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["playlist"]["title"], "My List")
        resolver.assert_called_once_with("https://example.com/list", {})

    def test_resolve_requires_url(self) -> None:
        response = self.post("/api/resolve", {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("ссылку", response.get_json()["error"]["message"])

    def test_resolve_requires_json_body(self) -> None:
        response = self.client.post(
            "/api/resolve",
            data="not-json",
            content_type="text/plain",
            headers=AUTH,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("JSON", response.get_json()["error"]["message"])


class DownloadTests(AppTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.playlist = make_playlist(
            [
                SimpleNamespace(title="One", artist="A", album="", duration=1, cover_url=None),
                SimpleNamespace(title="Two", artist="B", album="", duration=1, cover_url=None),
            ],
            title="List",
        )

    def test_download_defaults_to_all_tracks(self) -> None:
        job = FakeJob()
        with patch("app.get_playlist", return_value=self.playlist), patch(
            "app.manager.create", return_value=job
        ) as create, patch("app.load_settings", return_value={}):
            response = self.post("/api/download", {"playlist_id": "abc"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["job"]["id"], "job-1")
        args = create.call_args.args
        self.assertEqual(args[0], self.playlist)
        self.assertEqual(args[1], [0, 1])

    def test_download_accepts_explicit_indices_and_overrides(self) -> None:
        job = FakeJob()
        with patch("app.get_playlist", return_value=self.playlist), patch(
            "app.manager.create", return_value=job
        ) as create, patch("app.load_settings", return_value={}):
            response = self.post(
                "/api/download",
                {"playlist_id": "abc", "indices": [1], "format": "m4a", "quality": 128},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(create.call_args.args[1], [1])
        self.assertEqual(create.call_args.args[2]["format"], "m4a")
        self.assertEqual(create.call_args.args[2]["quality"], 128)

    def test_download_rejects_missing_playlist_and_bad_indices(self) -> None:
        response = self.post("/api/download", {})
        self.assertEqual(response.status_code, 400)

        with patch("app.get_playlist", return_value=self.playlist):
            response = self.post("/api/download", {"playlist_id": "abc", "indices": "bad"})
            self.assertEqual(response.status_code, 400)

            response = self.post("/api/download", {"playlist_id": "abc", "indices": ["bad"]})
            self.assertEqual(response.status_code, 400)


class JobEndpointTests(AppTestCase):
    def test_list_get_cancel_and_clear(self) -> None:
        manager = Mock()
        manager.list.return_value = [FakeJob()]
        manager.get.return_value = FakeJob()
        manager.cancel.return_value = FakeJob()
        manager.clear_finished.return_value = ["job-1"]

        with patch("app.manager", manager):
            listed = self.client.get("/api/jobs")
            fetched = self.client.get("/api/jobs/job-1")
            cancelled = self.post("/api/jobs/job-1/cancel")
            cleared = self.post("/api/jobs/clear")

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.get_json()["jobs"]), 1)
        self.assertFalse(listed.get_json()["jobs"][0]["include_tracks"])
        self.assertEqual(fetched.status_code, 200)
        self.assertTrue(fetched.get_json()["job"]["include_tracks"])
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cleared.get_json()["removed"], ["job-1"])

    def test_missing_job_returns_404(self) -> None:
        manager = Mock()
        manager.get.side_effect = UserError("not found", code="job_not_found")
        with patch("app.manager", manager):
            response = self.client.get("/api/jobs/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"]["code"], "job_not_found")

    def test_retry_copies_job_options_and_returns_new_job(self) -> None:
        original = FakeJob()
        original.playlist = SimpleNamespace(title="List")
        original.options = {"format": "m4a", "quality": 128, "output_dir": "C:\\Music"}
        manager = Mock()
        manager.get.return_value = original
        manager.retry.return_value = FakeJob()

        settings = dict(DEFAULT_SETTINGS)
        with patch("app.manager", manager), patch("app.load_settings", return_value=settings), patch(
            "app.normalize_settings", side_effect=lambda value, current=None: value
        ):
            response = self.post("/api/jobs/job-1/retry")

        self.assertEqual(response.status_code, 201)
        manager.retry.assert_called_once()
        passed_settings = manager.retry.call_args.args[1]
        self.assertEqual(passed_settings["format"], "m4a")
        self.assertEqual(passed_settings["quality"], 128)
        self.assertEqual(passed_settings["output_dir"], "C:\\Music")

    def test_unexpected_api_error_is_json_500(self) -> None:
        manager = Mock()
        manager.list.side_effect = RuntimeError("boom")
        with patch("app.manager", manager), patch("app.logger.exception") as log_exception:
            response = self.client.get("/api/jobs")
        log_exception.assert_called_once()
        self.assertEqual(response.status_code, 500)
        self.assertIn("Внутренняя ошибка", response.get_json()["error"]["message"])

    def test_unknown_api_route_is_json_404(self) -> None:
        response = self.client.get("/api/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()["ok"])


class SettingsEndpointTests(AppTestCase):
    def test_saves_settings_and_reports_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.detect_ffmpeg", return_value="C:\\ffmpeg.exe"):
                response = self.post(
                    "/api/settings",
                    {
                        "output_dir": directory,
                        "format": "m4a",
                        "quality": 128,
                        "playlist_folder": False,
                        "index_prefix": False,
                    },
                )
            data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["settings"]["format"], "m4a")
        self.assertEqual(data["settings"]["quality"], 128)
        self.assertFalse(data["settings"]["playlist_folder"])
        self.assertFalse(data["settings"]["index_prefix"])
        self.assertTrue(data["ffmpeg"]["found"])
        save_settings(DEFAULT_SETTINGS)

    def test_empty_secret_keeps_existing_and_clear_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            save_settings(
                {
                    **DEFAULT_SETTINGS,
                    "output_dir": directory,
                    "spotify_client_secret": "secret",
                }
            )
            with patch("app.detect_ffmpeg", return_value=None):
                response = self.post("/api/settings", {"spotify_client_secret": ""})
            self.assertTrue(response.get_json()["settings"]["secrets_set"]["spotify_client_secret"])

            with patch("app.detect_ffmpeg", return_value=None):
                response = self.post("/api/settings", {"spotify_client_secret": "__clear__"})
            self.assertFalse(response.get_json()["settings"]["secrets_set"]["spotify_client_secret"])
            self.assertFalse(load_settings()["spotify_client_secret"])

        save_settings(DEFAULT_SETTINGS)

    def test_reports_missing_ffmpeg_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.detect_ffmpeg", return_value=None):
                response = self.post(
                    "/api/settings",
                    {"output_dir": directory, "format": "mp3"},
                )
        warnings = response.get_json()["warnings"]
        self.assertTrue(any("FFmpeg" in warning for warning in warnings))
        save_settings(DEFAULT_SETTINGS)


class FolderEndpointTests(AppTestCase):
    def test_folder_picker_returns_path_and_cancel(self) -> None:
        with patch("app.pick_folder", return_value="C:\\Music"):
            response = self.post("/api/folder/pick", {})
        self.assertEqual(response.get_json()["path"], "C:\\Music")
        self.assertFalse(response.get_json()["cancelled"])

        with patch("app.pick_folder", return_value=""):
            response = self.post("/api/folder/pick", {})
        self.assertTrue(response.get_json()["cancelled"])

    def test_folder_picker_failure_is_user_error(self) -> None:
        with patch("app.pick_folder", side_effect=RuntimeError("no dialog")):
            response = self.post("/api/folder/pick", {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("выбор папки", response.get_json()["error"]["message"])

    def test_open_folder_calls_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("app.open_path") as open_path:
            response = self.post("/api/open-folder", {"path": directory})
            open_path.assert_called_once_with(str(Path(directory).resolve()))
        self.assertEqual(response.status_code, 200)

    def test_open_missing_explicit_folder_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing")
            response = self.post("/api/open-folder", {"path": missing})
        self.assertEqual(response.status_code, 400)
        self.assertIn("не создана", response.get_json()["error"]["message"])


if __name__ == "__main__":
    unittest.main()
