from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlparse

from ..models import Playlist
from ..utils import UserError, extract_url, make_session, resolve_short_url, stable_hash
from . import deezer, spotify, vk, yandex, ytdlp_source
from .common import platform_from_host

_CACHE_TTL = 3600
_CACHE_MAX = 20
_cache: OrderedDict[str, tuple[float, Playlist]] = OrderedDict()
_by_id: dict[str, Playlist] = {}
_lock = threading.RLock()


def _fingerprint(settings: dict[str, Any]) -> str:
    keys = (
        "proxy",
        "cookies_browser",
        "spotify_client_id",
        "spotify_client_secret",
        "spotify_market",
        "vk_token",
        "max_tracks",
    )
    return stable_hash("|".join(str(settings.get(key) or "") for key in keys), 20)


def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host in {"open.spotify.com", "spotify.com", "spotify.link", "spoti.fi"} or host.endswith(".spotify.com"):
        return "spotify"
    if host in {"soundcloud.com", "on.soundcloud.com"} or host.endswith(".soundcloud.com"):
        return "soundcloud"
    if host.startswith("music.yandex.") or host.startswith("y.music.yandex."):
        return "yandex"
    if host in {"vk.com", "vk.ru", "vk.cc"} or host.endswith(".vk.com"):
        return "vk"
    if host == "deezer.page.link" or host == "deezer.com" or host.endswith(".deezer.com"):
        return "deezer"
    if host in {"youtube.com", "youtu.be", "music.youtube.com"}:
        return "youtube"
    if host == "bandcamp.com" or host.endswith(".bandcamp.com"):
        return "bandcamp"
    if host == "music.apple.com":
        return "apple"
    return platform_from_host(host)


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _by_id.clear()


def get_playlist(playlist_id: str) -> Playlist:
    with _lock:
        playlist = _by_id.get(playlist_id)
    if not playlist:
        raise UserError(
            "Данные плейлиста устарели. Нажмите «Разобрать» ещё раз.",
            code="playlist_expired",
        )
    return playlist


def _store(playlist: Playlist, key: str) -> Playlist:
    playlist.ensure_id()
    with _lock:
        _cache[key] = (time.time(), playlist)
        _by_id[playlist.id] = playlist
        while len(_cache) > _CACHE_MAX:
            _, old = _cache.popitem(last=False)
            _by_id.pop(old.id, None)
        while len(_by_id) > _CACHE_MAX * 2:
            # Keep only playlists that are still referenced by the timed cache.
            alive = {p.id for _, p in _cache.values()}
            for old_id in list(_by_id):
                if old_id not in alive:
                    _by_id.pop(old_id, None)
    return playlist


def resolve(url: str, settings: dict[str, Any]) -> Playlist:
    url = extract_url(url)
    session = make_session(settings)
    try:
        final_url = resolve_short_url(url, session)
    finally:
        session.close()

    platform = detect_platform(final_url)
    key = stable_hash(f"{final_url}|{_fingerprint(settings)}", 32)
    with _lock:
        cached = _cache.get(key)
        if cached and time.time() - cached[0] < _CACHE_TTL:
            _cache.move_to_end(key)
            playlist = cached[1]
            playlist.ensure_id()
            _by_id[playlist.id] = playlist
            return playlist

    if platform == "apple":
        raise UserError(
            "Apple Music пока не поддерживается.",
            hint="Можно скачать трек/плейлист из другого сервиса или прислать ссылку на поддерживаемый сайт.",
            code="unsupported_platform",
        )

    if platform == "spotify":
        playlist = spotify.resolve(final_url, settings)
    elif platform == "yandex":
        playlist = yandex.resolve(final_url, settings)
    elif platform == "deezer":
        playlist = deezer.resolve(final_url, settings)
    elif platform == "vk":
        playlist = vk.resolve(final_url, settings)
    elif platform in {"soundcloud", "youtube", "bandcamp", "generic"}:
        playlist = ytdlp_source.resolve(final_url, settings, platform)
    else:
        raise UserError(f"Платформа «{platform}» пока не поддерживается.")

    max_tracks = max(1, int(settings.get("max_tracks") or 1000))
    if len(playlist.tracks) > max_tracks:
        playlist.tracks = playlist.tracks[:max_tracks]
        playlist.warnings.append(f"Загружены первые {max_tracks} треков (лимит в настройках).")
    if not playlist.tracks:
        raise UserError("Не удалось найти ни одного трека по этой ссылке.")
    if not playlist.cover_url and playlist.tracks[0].cover_url:
        playlist.cover_url = playlist.tracks[0].cover_url
    if not playlist.source_url:
        playlist.source_url = final_url
    playlist.ensure_id()
    return _store(playlist, key)
