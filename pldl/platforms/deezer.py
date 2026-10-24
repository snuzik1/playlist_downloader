from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import unquote, urlparse

import requests

from ..models import Playlist, Track
from ..utils import UserError, make_session, short_error
from .common import clean_text, get_json

API = "https://api.deezer.com"


def _api(session: requests.Session, path: str, params: dict[str, Any] | None = None) -> Any:
    data = get_json(session, API + path, params=params, what="данные Deezer", timeout=30)
    if isinstance(data, dict) and data.get("error"):
        error = data["error"]
        message = error.get("message") or error.get("type") or "неизвестная ошибка"
        if error.get("code") == 800:
            raise UserError("Deezer ограничил запросы. Попробуйте позже.")
        raise UserError(f"Deezer: {message}.")
    if isinstance(data, dict) and data.get("data") is None and "error" not in data and not data.get("id"):
        raise UserError("Deezer вернул пустой ответ.")
    return data


def _parse_track(raw: dict[str, Any], fallback_cover: str | None = None) -> Track | None:
    track_id = raw.get("id")
    if not track_id:
        return None
    artist = raw.get("artist") if isinstance(raw.get("artist"), dict) else {}
    album = raw.get("album") if isinstance(raw.get("album"), dict) else {}
    album_artist = album.get("artist") if isinstance(album.get("artist"), dict) else None
    artists = [clean_text(artist.get("name"))] if artist.get("name") else []
    if not artists and isinstance(album_artist, dict) and album_artist.get("name"):
        artists = [clean_text(album_artist["name"])]
    release = str(album.get("release_date") or raw.get("release_date") or "")
    return Track(
        id=f"dz:{track_id}",
        title=clean_text(raw.get("title")) or "Без названия",
        artists=artists,
        album=clean_text(album.get("title")),
        duration=int(raw["duration"]) if isinstance(raw.get("duration"), (int, float)) else None,
        cover_url=album.get("cover_xl") or album.get("cover_big") or fallback_cover,
        source_url=f"https://www.deezer.com/track/{track_id}",
        year=release[:4] if release[:4].isdigit() else None,
        track_number=raw.get("track_position") if isinstance(raw.get("track_position"), int) else None,
        disc_number=raw.get("disk_number") if isinstance(raw.get("disk_number"), int) else None,
        available=raw.get("readable", True) is not False,
    )


def _all_playlist_tracks(
    session: requests.Session,
    playlist_id: str,
    first: dict[str, Any] | None = None,
) -> list[Track]:
    first = first or _api(session, f"/playlist/{playlist_id}")
    tracks: list[Track] = []
    seen: set[str] = set()
    cover = first.get("picture_xl") or first.get("picture_big")
    raw_items = (first.get("tracks") or {}).get("data") or []
    for raw in raw_items:
        track = _parse_track(raw, cover)
        if track and track.id not in seen:
            seen.add(track.id)
            tracks.append(track)
    total = first.get("nb_tracks") or 0
    index = len(raw_items)
    while total and index < total and index < 20000:
        page = _api(
            session,
            f"/playlist/{playlist_id}/tracks",
            params={"limit": 100, "index": index},
        )
        items = page.get("data") or []
        if not items:
            break
        for raw in items:
            track = _parse_track(raw, cover)
            if track and track.id not in seen:
                seen.add(track.id)
                tracks.append(track)
        index += len(items)
        if len(items) < 100:
            break
        time.sleep(0.05)
    return tracks


def _playlist(session: requests.Session, playlist_id: str) -> Playlist:
    data = _api(session, f"/playlist/{playlist_id}")
    tracks = _all_playlist_tracks(session, playlist_id, data)
    if not tracks:
        raise UserError("В этом плейлисте Deezer нет доступных треков.")
    creator = data.get("creator") or {}
    return Playlist(
        platform="deezer",
        kind="playlist",
        title=clean_text(data.get("title")) or "Плейлист Deezer",
        author=clean_text(creator.get("name")),
        description=clean_text(data.get("description")),
        cover_url=data.get("picture_xl") or data.get("picture_big"),
        tracks=tracks,
        source_url=f"https://www.deezer.com/playlist/{playlist_id}",
        download_mode="match",
    )


def _album_tracks(session: requests.Session, album: dict[str, Any]) -> list[Track]:
    album_id = album.get("id")
    if not album_id:
        return []
    data = _api(session, f"/album/{album_id}")
    tracks: list[Track] = []
    seen: set[str] = set()
    cover = data.get("cover_xl") or data.get("cover_big")
    raw_items = (data.get("tracks") or {}).get("data") or []
    for raw in raw_items:
        if isinstance(raw, dict) and not raw.get("album"):
            raw = {**raw, "album": {**data, "cover_xl": cover}}
        track = _parse_track(raw, cover)
        if track and track.id not in seen:
            seen.add(track.id)
            tracks.append(track)
    total = data.get("nb_tracks") or 0
    index = len(raw_items)
    while total and index < total and index < 2000:
        page = _api(
            session,
            f"/album/{album_id}/tracks",
            params={"limit": 100, "index": index},
        )
        items = page.get("data") or []
        if not items:
            break
        for raw in items:
            if isinstance(raw, dict) and not raw.get("album"):
                raw = {**raw, "album": {**data, "cover_xl": cover}}
            track = _parse_track(raw, cover)
            if track and track.id not in seen:
                seen.add(track.id)
                tracks.append(track)
        index += len(items)
        if len(items) < 100:
            break
    return tracks


def _album(session: requests.Session, album_id: str) -> Playlist:
    data = _api(session, f"/album/{album_id}")
    tracks = _album_tracks(session, data)
    if not tracks:
        raise UserError("В этом альбоме Deezer нет доступных треков.")
    artist = data.get("artist") or {}
    return Playlist(
        platform="deezer",
        kind="album",
        title=clean_text(data.get("title")) or "Альбом Deezer",
        author=clean_text(artist.get("name")),
        cover_url=data.get("cover_xl") or data.get("cover_big"),
        tracks=tracks,
        source_url=f"https://www.deezer.com/album/{album_id}",
        download_mode="match",
        description=clean_text(data.get("release_date")),
    )


def _track(session: requests.Session, track_id: str) -> Playlist:
    data = _api(session, f"/track/{track_id}")
    track = _parse_track(data)
    if not track:
        raise UserError("Не удалось прочитать трек Deezer.")
    return Playlist(
        platform="deezer",
        kind="track",
        title=track.title,
        author=track.artist,
        cover_url=track.cover_url,
        tracks=[track],
        source_url=f"https://www.deezer.com/track/{track_id}",
        download_mode="match",
    )


def _artist(session: requests.Session, artist_id: str, max_tracks: int) -> Playlist:
    data = _api(session, f"/artist/{artist_id}")
    albums: list[dict[str, Any]] = []
    offset = 0
    while True:
        albums_data = _api(
            session,
            f"/artist/{artist_id}/albums",
            params={"limit": 100, "index": offset},
        )
        items = albums_data.get("data") or []
        albums.extend(item for item in items if isinstance(item, dict))
        if not items or len(items) < 100:
            break
        offset += len(items)
        time.sleep(0.05)
    tracks: list[Track] = []
    seen: set[str] = set()
    warnings: list[str] = []
    for album in albums:
        if len(tracks) >= max_tracks:
            warnings.append(f"Достигнут лимит {max_tracks} треков.")
            break
        try:
            for track in _album_tracks(session, album):
                if track.id not in seen:
                    seen.add(track.id)
                    tracks.append(track)
                if len(tracks) >= max_tracks:
                    break
        except UserError:
            continue
        time.sleep(0.03)
    if not tracks:
        raise UserError("У этого исполнителя Deezer нет доступных треков.")
    return Playlist(
        platform="deezer",
        kind="artist",
        title=f"{clean_text(data.get('name')) or 'Исполнитель'} — треки",
        author=clean_text(data.get("name")),
        cover_url=data.get("picture_xl") or data.get("picture_big"),
        tracks=tracks,
        source_url=f"https://www.deezer.com/artist/{artist_id}",
        download_mode="match",
        warnings=warnings,
    )


def resolve(url: str, settings: dict[str, Any]) -> Playlist:
    host = (urlparse(url).hostname or "").lower()
    if not host.endswith("deezer.com"):
        raise UserError("Это не ссылка Deezer.")
    path = unquote(urlparse(url).path)
    parts = [part for part in path.split("/") if part]
    if parts and re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", parts[0], re.IGNORECASE):
        parts = parts[1:]
    if not parts:
        raise UserError("Не удалось понять ссылку Deezer.")

    session = make_session(settings)
    try:
        if parts[0] == "playlist" and len(parts) >= 2:
            return _playlist(session, parts[1])
        if parts[0] == "album" and len(parts) >= 2:
            return _album(session, parts[1])
        if parts[0] == "track" and len(parts) >= 2:
            return _track(session, parts[1])
        if parts[0] == "artist" and len(parts) >= 2:
            return _artist(session, parts[1], int(settings.get("max_tracks") or 1000))
    finally:
        session.close()
    raise UserError(
        "Не удалось разобрать ссылку Deezer.",
        hint="Поддерживаются плейлисты, альбомы, треки и исполнители Deezer.",
    )
