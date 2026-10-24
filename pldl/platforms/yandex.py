from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse

import requests

from ..models import Playlist, Track
from ..utils import UserError, make_session, short_error
from .common import absolute_url, clean_text, fix_yandex_cover, get_json

API = "https://api.music.yandex.net"
HEADERS = {
    "X-Yandex-Music-Client": "YandexMusicDesktopAppWindows/5.42.0",
    "Accept": "application/json",
}


def _api(session: requests.Session, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    data = get_json(
        session,
        API + path,
        params=params,
        headers=HEADERS,
        what="данные Яндекс Музыки",
        timeout=35,
    )
    if not isinstance(data, dict):
        raise UserError("Яндекс Музыка вернула неожиданный ответ.")
    error = data.get("error")
    if error:
        message = error.get("message") or error.get("name") or "неизвестная ошибка"
        raise UserError(f"Яндекс Музыка: {message}.")
    result = data.get("result")
    if result is None:
        raise UserError("Яндекс Музыка не вернула данные. Возможно, ссылка неверная.")
    return result


def _cover(uri: str | None) -> str | None:
    return fix_yandex_cover(uri, "1000x1000")


def _parse_track(raw: dict[str, Any]) -> Track | None:
    if not isinstance(raw, dict):
        return None
    track_id = raw.get("id") or raw.get("realId")
    if not track_id:
        return None
    albums = raw.get("albums") if isinstance(raw.get("albums"), list) else []
    album = albums[0] if albums and isinstance(albums[0], dict) else {}
    album_id = album.get("id")
    position = album.get("trackPosition") or {}
    artists = []
    for artist in raw.get("artists") or []:
        if isinstance(artist, dict) and artist.get("name"):
            artists.append(str(artist["name"]))
    duration_ms = raw.get("durationMs")
    duration = int(round(duration_ms / 1000)) if isinstance(duration_ms, (int, float)) else None
    year = album.get("year") or raw.get("year")
    source_url = None
    if album_id:
        source_url = f"https://music.yandex.ru/album/{album_id}/track/{track_id}"
    return Track(
        id=f"ym:{track_id}",
        title=clean_text(raw.get("title")) or "Без названия",
        artists=artists,
        album=clean_text(album.get("title")),
        duration=duration,
        cover_url=_cover(album.get("coverUri") or raw.get("coverUri")),
        source_url=source_url,
        year=str(year) if year else None,
        track_number=position.get("index") if isinstance(position.get("index"), int) else None,
        disc_number=position.get("volume") if isinstance(position.get("volume"), int) else None,
        available=bool(raw.get("available", True)),
        extra={"preview": bool(raw.get("previewDurationMs"))},
    )


def _parse_playlist(data: dict[str, Any]) -> tuple[str, str, str, str | None, list[Track], int]:
    raw_tracks = data.get("tracks") or []
    tracks: list[Track] = []
    seen: set[str] = set()
    for item in raw_tracks:
        raw = item.get("track") if isinstance(item, dict) and isinstance(item.get("track"), dict) else item
        track = _parse_track(raw)
        if not track or track.id in seen:
            continue
        seen.add(track.id)
        tracks.append(track)

    owner = data.get("owner") or {}
    title = clean_text(data.get("title")) or "Плейлист Яндекс Музыки"
    author = clean_text(owner.get("name") or owner.get("login") or "")
    description = clean_text(data.get("descriptionFormatted") or data.get("description"))
    cover = _cover(data.get("ogImage")) or _cover((data.get("cover") or {}).get("uri") if isinstance(data.get("cover"), dict) else None)
    expected = int(data.get("trackCount") or 0)
    return title, author, description, cover, tracks, expected


def _resolve_playlist(session: requests.Session, owner: str, kind: str) -> Playlist:
    data = _api(session, f"/users/{owner}/playlists/{kind}")
    if not isinstance(data, dict):
        raise UserError("Не удалось прочитать плейлист Яндекс Музыки.")
    title, author, description, cover, tracks, expected = _parse_playlist(data)
    warnings: list[str] = []
    if expected and len(tracks) < expected:
        warnings.append(f"Яндекс Музыка вернула {len(tracks)} из {expected} треков — часть недоступна.")
    if not tracks:
        raise UserError("В этом плейлисте нет доступных треков.")
    return Playlist(
        platform="yandex",
        kind="playlist",
        title=title,
        author=author,
        description=description,
        cover_url=cover,
        tracks=tracks,
        source_url=f"https://music.yandex.ru/users/{owner}/playlists/{kind}",
        download_mode="match",
        warnings=warnings,
    )


def _resolve_album(session: requests.Session, album_id: str) -> Playlist:
    data = _api(session, f"/albums/{album_id}/with-tracks")
    if not isinstance(data, dict):
        raise UserError("Не удалось прочитать альбом Яндекс Музыки.")
    volumes = data.get("volumes") if isinstance(data.get("volumes"), list) else []
    tracks: list[Track] = []
    seen: set[str] = set()
    for volume in volumes:
        if not isinstance(volume, list):
            continue
        for raw in volume:
            track = _parse_track(raw)
            if track and track.id not in seen:
                seen.add(track.id)
                tracks.append(track)
    if not tracks:
        raise UserError("В этом альбоме нет доступных треков.")
    artists = [a.get("name") for a in data.get("artists") or [] if isinstance(a, dict) and a.get("name")]
    title = clean_text(data.get("title")) or "Альбом Яндекс Музыки"
    author = ", ".join(artists)
    year = data.get("year")
    if year:
        title = f"{title} ({year})"
    return Playlist(
        platform="yandex",
        kind="album",
        title=title,
        author=author,
        cover_url=_cover(data.get("coverUri")),
        tracks=tracks,
        source_url=f"https://music.yandex.ru/album/{album_id}",
        download_mode="match",
        description=clean_text(data.get("description")),
    )


def _resolve_track(session: requests.Session, track_id: str, album_id: str | None) -> Playlist:
    data = _api(session, f"/tracks/{track_id}")
    raw = data[0] if isinstance(data, list) and data else data
    track = _parse_track(raw) if isinstance(raw, dict) else None
    if not track:
        raise UserError("Не удалось прочитать трек Яндекс Музыки.")
    if album_id and not track.source_url:
        track.source_url = f"https://music.yandex.ru/album/{album_id}/track/{track_id}"
    return Playlist(
        platform="yandex",
        kind="track",
        title=track.title,
        author=track.artist,
        cover_url=track.cover_url,
        tracks=[track],
        source_url=track.source_url or f"https://music.yandex.ru/track/{track_id}",
        download_mode="match",
        description=track.album,
    )


def _resolve_artist(session: requests.Session, artist_id: str, albums_link: bool = False) -> Playlist:
    info = _api(session, f"/artists/{artist_id}")
    artist_obj = info.get("artist") if isinstance(info, dict) else {}
    name = clean_text((artist_obj or {}).get("name")) or "Исполнитель"
    cover = _cover(((artist_obj or {}).get("cover") or {}).get("uri"))

    tracks: list[Track] = []
    seen: set[str] = set()
    page = 0
    total = None
    while page < 50:
        result = _api(
            session,
            f"/artists/{artist_id}/tracks",
            params={"page": page, "pageSize": 100},
        )
        if not isinstance(result, dict):
            break
        pager = result.get("pager") or {}
        if isinstance(pager.get("total"), int):
            total = pager["total"]
        items = result.get("tracks") or []
        for raw in items:
            track = _parse_track(raw)
            if track and track.id not in seen:
                seen.add(track.id)
                tracks.append(track)
        if not items or len(items) < 100:
            break
        page += 1

    if not tracks:
        raise UserError("У этого исполнителя не нашлось доступных треков.")
    warnings: list[str] = []
    if albums_link:
        warnings.append("Ссылка ведёт на список альбомов — загружены все треки исполнителя.")
    if total and len(tracks) < total:
        warnings.append(f"Доступно {len(tracks)} из {total} треков исполнителя.")
    return Playlist(
        platform="yandex",
        kind="artist",
        title=f"{name} — треки",
        author=name,
        cover_url=cover,
        tracks=tracks,
        source_url=f"https://music.yandex.ru/artist/{artist_id}",
        download_mode="match",
        warnings=warnings,
    )


def resolve(url: str, settings: dict[str, Any]) -> Playlist:
    host = (urlparse(url).hostname or "").lower()
    if not host.startswith("music.yandex.") and not host.startswith("y.music.yandex."):
        raise UserError("Это не ссылка на Яндекс Музыку.")
    path = unquote(urlparse(url).path)
    parts = [part for part in path.split("/") if part]
    if not parts:
        raise UserError(
            "Не удалось понять ссылку Яндекс Музыки.",
            hint="Откройте плейлист, альбом или исполнителя и скопируйте ссылку из адресной строки.",
        )

    session = make_session(settings)
    try:
        if parts[0] == "users" and len(parts) >= 4 and parts[2] == "playlists":
            return _resolve_playlist(session, parts[1], parts[3])
        if parts[0] == "album" and len(parts) >= 3 and parts[2] == "track":
            return _resolve_track(session, parts[3] if len(parts) > 3 else parts[2], parts[1])
        if parts[0] == "album" and len(parts) >= 2:
            return _resolve_album(session, parts[1])
        if parts[0] == "track" and len(parts) >= 2:
            return _resolve_track(session, parts[1], None)
        if parts[0] == "artist" and len(parts) >= 2:
            return _resolve_artist(session, parts[1], albums_link=len(parts) > 2 and parts[2] == "albums")
        if parts[0] == "playlists" and len(parts) >= 2:
            raise UserError(
                "Такие короткие ссылки Яндекс Музыки пока не поддерживаются.",
                hint="Откройте плейлист и скопируйте полную ссылку вида music.yandex.ru/users/…/playlists/….",
            )
    finally:
        session.close()

    raise UserError(
        "Не удалось разобрать ссылку Яндекс Музыки.",
        hint="Поддерживаются плейлисты, альбомы, треки и страницы исполнителей.",
    )
