from __future__ import annotations

import argparse
import logging
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from pldl import APP_NAME, __version__
from pldl.config import (
    DEFAULT_SETTINGS,
    SECRET_KEYS,
    load_settings,
    normalize_settings,
    public_settings,
    save_settings,
)
from pldl.folder_picker import pick_folder
from pldl.jobs import JobManager
from pldl.platforms import clear_cache, get_playlist, resolve
from pldl.utils import UserError, find_ffmpeg, open_path, short_error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logger = logging.getLogger("pldl.web")

PLATFORMS = [
    {
        "id": "yandex",
        "label": "Яндекс Музыка",
        "mode": "match",
        "note": "Плейлисты, альбомы, треки и исполнители",
    },
    {
        "id": "soundcloud",
        "label": "SoundCloud",
        "mode": "direct",
        "note": "Сеты, треки и профили",
    },
    {
        "id": "spotify",
        "label": "Spotify",
        "mode": "match",
        "note": "Нужен доступ к Spotify (VPN/прокси)",
    },
    {
        "id": "vk",
        "label": "VK Музыка",
        "mode": "direct",
        "note": "Нужен токен VK с доступом к аудио",
    },
    {
        "id": "deezer",
        "label": "Deezer",
        "mode": "match",
        "note": "Плейлисты, альбомы, треки и исполнители",
    },
    {
        "id": "youtube",
        "label": "YouTube / Music",
        "mode": "direct",
        "note": "Плейлисты, треки и каналы",
    },
    {
        "id": "bandcamp",
        "label": "Bandcamp",
        "mode": "direct",
        "note": "Альбомы и треки",
    },
    {
        "id": "generic",
        "label": "Другие сайты",
        "mode": "direct",
        "note": "Всё, что поддерживает yt-dlp",
    },
]

APP_TOKEN = secrets.token_urlsafe(32)
manager = JobManager()
_ffmpeg_lock = threading.Lock()
_ffmpeg_cache: tuple[float, str, str | None] = (0.0, "", None)


def _resource_path(name: str) -> str:
    """Path to bundled templates/static, including inside a PyInstaller exe."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base / name)


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=_resource_path("static"),
        template_folder=_resource_path("templates"),
    )
    app.config.update(
        APP_TOKEN=APP_TOKEN,
        MAX_CONTENT_LENGTH=64 * 1024,
        JSON_AS_ASCII=False,
        SEND_FILE_MAX_AGE_DEFAULT=0,
    )
    app.register_error_handler(UserError, _handle_user_error)
    app.register_error_handler(HTTPException, _handle_http_error)
    app.register_error_handler(Exception, _handle_unexpected_error)
    app.before_request(_guard)
    app.after_request(_security_headers)
    app.add_url_rule("/", "index", index)
    app.add_url_rule("/api/health", "api_health", api_health, methods=["GET"])
    app.add_url_rule("/api/status", "api_status", api_status, methods=["GET"])
    app.add_url_rule("/api/resolve", "api_resolve", api_resolve, methods=["POST"])
    app.add_url_rule("/api/download", "api_download", api_download, methods=["POST"])
    app.add_url_rule("/api/jobs", "api_jobs", api_jobs, methods=["GET"])
    app.add_url_rule("/api/jobs/clear", "api_jobs_clear", api_jobs_clear, methods=["POST"])
    app.add_url_rule("/api/jobs/<job_id>", "api_job", api_job, methods=["GET"])
    app.add_url_rule("/api/jobs/<job_id>/cancel", "api_job_cancel", api_job_cancel, methods=["POST"])
    app.add_url_rule("/api/jobs/<job_id>/retry", "api_job_retry", api_job_retry, methods=["POST"])
    app.add_url_rule("/api/settings", "api_settings", api_settings, methods=["POST"])
    app.add_url_rule("/api/folder/pick", "api_folder_pick", api_folder_pick, methods=["POST"])
    app.add_url_rule("/api/open-folder", "api_open_folder", api_open_folder, methods=["POST"])
    return app


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _hostname_of(value: str) -> str:
    try:
        return (urlparse("//" + value).hostname or "").lower()
    except ValueError:
        return ""


def _is_local_host(value: str) -> bool:
    return _hostname_of(value) in {"127.0.0.1", "localhost", "::1"}


def _guard():
    if not _is_local_host(request.host):
        return _json_error("Доступ разрешён только с этого компьютера.", status=403)

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        token = request.headers.get("X-App-Token", "")
        if not secrets.compare_digest(token, app.config["APP_TOKEN"]):
            return _json_error("Недействительный токен приложения. Обновите страницу.", status=403)
        origin = request.headers.get("Origin")
        if origin and not _is_local_host(urlparse(origin).netloc):
            return _json_error("Запрос с постороннего сайта отклонён.", status=403)
        site = request.headers.get("Sec-Fetch-Site")
        if site and site not in {"same-origin", "none"}:
            return _json_error("Запрос с постороннего сайта отклонён.", status=403)
    return None


def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data: blob: https: http:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "form-action 'self'"
    )
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def _json_error(message: str, status: int = 400, hint: str | None = None, code: str | None = None):
    payload: dict[str, Any] = {"ok": False, "error": {"message": message}}
    if hint:
        payload["error"]["hint"] = hint
    if code:
        payload["error"]["code"] = code
    return jsonify(payload), status


def _handle_user_error(exc: UserError):
    status = 404 if exc.code in {"job_not_found", "playlist_expired"} else 400
    return _json_error(exc.message, status=status, hint=exc.hint, code=exc.code)


def _handle_http_error(exc: HTTPException):
    if request.path.startswith("/api/"):
        return _json_error(exc.description or exc.name, status=exc.code or 500)
    return exc


def _handle_unexpected_error(exc: Exception):
    logger.exception("unhandled request error")
    if request.path.startswith("/api/"):
        return _json_error(
            "Внутренняя ошибка приложения. Подробности в консоли.",
            status=500,
            hint=short_error(exc),
        )
    return "Внутренняя ошибка приложения.", 500


def _json_body() -> dict[str, Any]:
    if not request.is_json:
        raise UserError("Ожидается JSON-запрос.", code="bad_request")
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise UserError("Некорректный формат запроса.", code="bad_request")
    return data


def detect_ffmpeg(configured: str) -> str | None:
    """FFmpeg detection is a little expensive on Windows; cache it briefly."""
    global _ffmpeg_cache
    now = time.monotonic()
    with _ffmpeg_lock:
        expires, cached_key, cached_value = _ffmpeg_cache
        if expires > now and cached_key == configured:
            return cached_value
    value = find_ffmpeg(configured)
    with _ffmpeg_lock:
        _ffmpeg_cache = (now + 15.0, configured, value)
    return value


def _settings_and_ffmpeg() -> tuple[dict[str, Any], str | None]:
    settings = load_settings()
    return settings, detect_ffmpeg(settings.get("ffmpeg_path", ""))


def _status_payload() -> dict[str, Any]:
    settings, ffmpeg = _settings_and_ffmpeg()
    public = public_settings(settings, ffmpeg)
    return {
        "ok": True,
        "app": {
            "name": APP_NAME,
            "version": __version__,
        },
        "settings": public,
        "ffmpeg": {
            "found": bool(ffmpeg),
            "path": ffmpeg or "",
            "configured": settings.get("ffmpeg_path", ""),
        },
        "platforms": PLATFORMS,
        "defaults": DEFAULT_SETTINGS,
    }


def _ensure_writable_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise OSError("путь существует, но это не папка")
        with tempfile.NamedTemporaryFile(prefix=".pldl_check_", dir=str(path), delete=True):
            pass
    except OSError as exc:
        raise UserError(
            f"Папка недоступна для записи: {short_error(exc)}",
            hint="Выберите другую папку в настройках.",
        ) from exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def index():
    return render_template(
        "index.html",
        token=app.config["APP_TOKEN"],
        version=__version__,
        app_name=APP_NAME,
    )


def api_health():
    return jsonify({"ok": True, "version": __version__})


def api_status():
    return jsonify(_status_payload())


def api_resolve():
    data = _json_body()
    url = str(data.get("url") or "").strip()
    if not url:
        raise UserError("Вставьте ссылку на плейлист, альбом, трек или профиль.")
    settings = load_settings()
    started = time.time()
    playlist = resolve(url, settings)
    logger.info("resolved %s (%s tracks) in %.2fs", playlist.platform, len(playlist.tracks), time.time() - started)
    return jsonify({"ok": True, "playlist": playlist.public(time.time() - started)})


def api_download():
    data = _json_body()
    playlist_id = str(data.get("playlist_id") or "").strip()
    if not playlist_id:
        raise UserError("Не указан плейлист. Нажмите «Разобрать» ещё раз.")

    playlist = get_playlist(playlist_id)
    raw_indices = data.get("indices")
    if raw_indices is None:
        indices = list(range(len(playlist.tracks)))
    elif isinstance(raw_indices, list):
        try:
            indices = [int(value) for value in raw_indices]
        except (TypeError, ValueError) as exc:
            raise UserError("Некорректный список выбранных треков.") from exc
    else:
        raise UserError("Некорректный список выбранных треков.")

    settings = load_settings()
    overrides: dict[str, Any] = {}
    for key in ("format", "quality", "playlist_folder", "index_prefix", "concurrency"):
        if key in data:
            overrides[key] = data[key]
    job_settings = normalize_settings(overrides, settings)

    job = manager.create(playlist, indices, job_settings)
    logger.info("job %s started: %s (%s tracks)", job.id, playlist.title, len(job.indices))
    return jsonify({"ok": True, "job": job.snapshot(include_tracks=False)}), 201


def api_jobs():
    return jsonify({"ok": True, "jobs": [job.snapshot(include_tracks=False) for job in manager.list()]})


def api_jobs_clear():
    removed = manager.clear_finished()
    logger.info("cleared %s finished jobs", len(removed))
    return jsonify({"ok": True, "removed": removed})


def api_job(job_id: str):
    job = manager.get(job_id)
    return jsonify({"ok": True, "job": job.snapshot()})


def api_job_cancel(job_id: str):
    job = manager.cancel(job_id)
    return jsonify({"ok": True, "job": job.snapshot(include_tracks=False)})


def api_job_retry(job_id: str):
    original = manager.get(job_id)
    settings = load_settings()
    # Keep the destination/format of the original job, but pick up fresh
    # network, FFmpeg and token settings from the current configuration.
    for key in ("output_dir", "format", "quality", "playlist_folder", "index_prefix", "concurrency"):
        if key in original.options:
            settings[key] = original.options[key]
    settings = normalize_settings(settings)
    job = manager.retry(job_id, settings)
    logger.info("job %s retry started: %s", job.id, original.playlist.title)
    return jsonify({"ok": True, "job": job.snapshot(include_tracks=False)}), 201


def api_settings():
    data = _json_body()
    current = load_settings()
    clean: dict[str, Any] = {}
    for key in DEFAULT_SETTINGS:
        if key not in data:
            continue
        value = data[key]
        if key in SECRET_KEYS:
            text = str(value or "").strip()
            if text == "__clear__":
                value = ""
            elif not text:
                continue
        clean[key] = value

    candidate = normalize_settings(clean, current)
    _ensure_writable_dir(Path(candidate["output_dir"]))
    saved = save_settings(candidate)
    clear_cache()
    ffmpeg = detect_ffmpeg(saved.get("ffmpeg_path", ""))

    warnings: list[str] = []
    if saved.get("ffmpeg_path") and not ffmpeg:
        warnings.append("FFmpeg не найден по указанному пути — конвертация не будет работать.")
    elif not ffmpeg and saved.get("format") != "original":
        warnings.append("FFmpeg не найден. Установите его или укажите путь в настройках.")

    logger.info("settings saved (format=%s, output=%s)", saved.get("format"), saved.get("output_dir"))
    return jsonify(
        {
            "ok": True,
            "settings": public_settings(saved, ffmpeg),
            "ffmpeg": {"found": bool(ffmpeg), "path": ffmpeg or ""},
            "warnings": warnings,
        }
    )


def api_folder_pick():
    data = _json_body()
    settings = load_settings()
    initial = str(data.get("initial") or settings.get("output_dir") or "").strip()
    try:
        path = pick_folder(initial)
    except Exception as exc:  # noqa: BLE001 - native dialogs fail in many ways
        logger.warning("folder picker failed: %s", exc)
        raise UserError(
            "Не удалось открыть системный выбор папки.",
            hint="Введите путь к папке вручную.",
        ) from exc
    return jsonify({"ok": True, "path": path or "", "cancelled": not path})


def api_open_folder():
    data = _json_body()
    settings = load_settings()
    raw = str(data.get("path") or "").strip()
    if raw:
        target = Path(raw).expanduser()
    else:
        target = Path(settings["output_dir"]).expanduser()

    try:
        target = target.resolve()
    except OSError as exc:
        raise UserError(f"Некорректный путь: {short_error(exc)}") from exc

    if not target.exists():
        if raw:
            raise UserError("Эта папка ещё не создана.")
        _ensure_writable_dir(target)
    if not target.is_dir():
        target = target.parent
    try:
        open_path(str(target))
    except OSError as exc:
        raise UserError(f"Не удалось открыть папку: {short_error(exc)}") from exc
    return jsonify({"ok": True, "path": str(target)})


app = create_app()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _pick_port(preferred: int) -> int:
    for port in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit(f"Не удалось найти свободный порт (начиная с {preferred}).")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description="PLDL — локальный загрузчик плейлистов")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PLDL_PORT", "8765")))
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
    args = parser.parse_args(argv)

    port = _pick_port(max(1, min(65535, args.port)))
    url = f"http://127.0.0.1:{port}/"
    settings, ffmpeg = _settings_and_ffmpeg()

    print()
    print(f"  {APP_NAME} {__version__} — локальный загрузчик плейлистов")
    print(f"  Интерфейс:  {url}")
    print(f"  Папка:      {settings.get('output_dir')}")
    print(f"  FFmpeg:     {ffmpeg or 'не найден (откройте настройки)'}")
    print("  Остановить: Ctrl+C")
    print()

    if not args.no_browser:
        threading.Timer(0.9, lambda: webbrowser.open(url)).start()

    try:
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    print("\nСервер остановлен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
