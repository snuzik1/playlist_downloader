from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any

from .utils import atomic_write_json, expand_path, read_json


def _base_dir() -> Path:
    """Folder for settings/cache: next to PLDL.exe when frozen."""
    override = os.environ.get("PLDL_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()


def _settings_dir() -> Path:
    """Keep settings portable, but fall back to LOCALAPPDATA for read-only folders."""
    try:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        probe = BASE_DIR / f".pldl_probe_{os.getpid()}"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return BASE_DIR
    except OSError:
        fallback = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PLDL"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


SETTINGS_DIR = _settings_dir()
SETTINGS_PATH = SETTINGS_DIR / "settings.json"
MUSIC_DIR = Path.home() / "Music"
DEFAULT_OUTPUT = MUSIC_DIR / "PLDL" if MUSIC_DIR.exists() else Path.home() / "Downloads" / "PLDL"

DEFAULT_SETTINGS: dict[str, Any] = {
    "output_dir": str(DEFAULT_OUTPUT),
    "format": "mp3",
    "quality": 320,
    "playlist_folder": True,
    "index_prefix": True,
    "concurrency": 2,
    "match_source": "auto",
    "cookies_browser": "",
    "proxy": "",
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "spotify_market": "US",
    "vk_token": "",
    "max_tracks": 1000,
    "ffmpeg_path": "",
}

FORMATS = {
    "mp3": [320, 192, 128],
    "m4a": [256, 128],
    "original": [],
}
COOKIE_BROWSERS = {"", "chrome", "chromium", "edge", "firefox", "brave", "opera", "vivaldi", "safari"}
MATCH_SOURCES = {"auto", "youtube", "soundcloud"}
SECRET_KEYS = ("spotify_client_secret", "vk_token")


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "да"}:
        return True
    if text in {"0", "false", "no", "off", "нет", ""}:
        return False
    return fallback


def normalize_settings(payload: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    base = copy.deepcopy(DEFAULT_SETTINGS)
    if current:
        base.update(current)

    for key in DEFAULT_SETTINGS:
        if key in payload:
            base[key] = payload[key]

    output_dir = expand_path(str(base.get("output_dir") or DEFAULT_OUTPUT), BASE_DIR)
    base["output_dir"] = output_dir or str(DEFAULT_OUTPUT)

    fmt = str(base.get("format") or "mp3").lower()
    if fmt not in FORMATS:
        fmt = "mp3"
    base["format"] = fmt
    qualities = FORMATS[fmt]
    if qualities:
        quality = _as_int(base.get("quality"), qualities[0])
        if quality not in qualities:
            quality = qualities[0]
        base["quality"] = quality
    else:
        base["quality"] = 0

    base["playlist_folder"] = _as_bool(base.get("playlist_folder"), True)
    base["index_prefix"] = _as_bool(base.get("index_prefix"), True)
    base["concurrency"] = max(1, min(6, _as_int(base.get("concurrency"), 2)))

    match_source = str(base.get("match_source") or "auto").lower()
    base["match_source"] = match_source if match_source in MATCH_SOURCES else "auto"

    browser = str(base.get("cookies_browser") or "").lower()
    base["cookies_browser"] = browser if browser in COOKIE_BROWSERS else ""

    base["proxy"] = str(base.get("proxy") or "").strip()
    base["spotify_client_id"] = str(base.get("spotify_client_id") or "").strip()
    base["spotify_client_secret"] = str(base.get("spotify_client_secret") or "").strip()
    market = str(base.get("spotify_market") or "US").upper()
    base["spotify_market"] = market if len(market) == 2 and market.isalpha() else "US"
    base["vk_token"] = str(base.get("vk_token") or "").strip()
    base["max_tracks"] = max(1, min(5000, _as_int(base.get("max_tracks"), 1000)))
    base["ffmpeg_path"] = expand_path(str(base.get("ffmpeg_path") or ""), BASE_DIR) if base.get("ffmpeg_path") else ""

    return base


def load_settings() -> dict[str, Any]:
    data = read_json(SETTINGS_PATH, {})
    if not isinstance(data, dict):
        data = {}
    return normalize_settings(data)


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_settings(settings)
    atomic_write_json(SETTINGS_PATH, normalized)
    return normalized


def public_settings(settings: dict[str, Any], ffmpeg: str | None) -> dict[str, Any]:
    data = copy.deepcopy(settings)
    secrets_set: dict[str, bool] = {}
    for key in SECRET_KEYS:
        secrets_set[key] = bool(str(settings.get(key) or "").strip())
        data[key] = ""
    try:
        output_exists = Path(data["output_dir"]).is_dir()
    except OSError:
        output_exists = False
    data["secrets_set"] = secrets_set
    data["output_dir_exists"] = output_exists
    data["ffmpeg_detected"] = ffmpeg or ""
    data["settings_path"] = str(SETTINGS_PATH)
    data["formats"] = FORMATS
    return data


def ensure_output_dir(settings: dict[str, Any]) -> Path:
    path = Path(expand_path(settings.get("output_dir", ""), BASE_DIR) or DEFAULT_OUTPUT)
    path.mkdir(parents=True, exist_ok=True)
    return path
