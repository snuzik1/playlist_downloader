from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests

from ..models import Playlist, Track
from ..utils import UserError, make_session, short_error
from .common import clean_text

API = "https://api.vk.com/method"
API_VERSION = "5.199"


def _api(session: requests.Session, method: str, params: dict[str, Any], token: str) -> Any:
    query = {"access_token": token, "v": API_VERSION, "lang": "ru", **params}
    try:
        response = session.get(f"{API}/{method}", params=query, timeout=35)
    except requests.RequestException as exc:
        raise UserError(
            f"Не удалось связаться с VK: {short_error(exc)}",
            hint="Проверьте интернет/VPN/прокси.",
        ) from exc
    if response.status_code != 200:
        raise UserError(f"VK API вернул HTTP {response.status_code}.")
    try:
        data = response.json()
    except ValueError as exc:
        raise UserError("VK API вернул некорректный ответ.") from exc
    if isinstance(data, dict) and data.get("error"):
        error = data["error"]
        code = error.get("error_code")
        message = clean_text(error.get("error_msg")) or "неизвестная ошибка"
        if code in (5, 28):
            raise UserError(
                "Токен VK недействителен или истёк.",
                hint="Обновите токен в настройках → VK Музыка.",
                code="vk_token",
            )
        if code == 15:
            raise UserError(
                "У этого токена нет доступа к аудио VK.",
                hint="Нужен пользовательский токен с правом audio.",
                code="vk_token",
            )
        if code in (6, 9, 29):
            raise UserError("VK ограничил частоту запросов. Подождите и повторите.")
        if code == 100:
            raise UserError(f"VK не принял параметры запроса: {message}.")
        raise UserError(f"VK API: {message} (код {code}).")
    return data.get("response") if isinstance(data, dict) else None


def _playlist_id_from_url(url: str) -> tuple[str, str, str | None] | None:
    decoded = unquote(url)
    match = re.search(r"(?:audio_playlist|music/(?:playlist|album)/|/playlist/)(-?\d+)_(\d+)", decoded)
    if match:
        access_key = None
        values = parse_qs(urlparse(decoded).query)
        for key in ("access_key", "access_key_id"):
            if values.get(key):
                access_key = values[key][0]
                break
        if not access_key:
            # Old share links keep the key after a slash: ?z=audio_playlist-1_2/abcdef
            for key in ("z", "w"):
                for value in values.get(key, []):
                    if "/" not in value:
                        continue
                    maybe_key = value.rsplit("/", 1)[-1]
                    if re.fullmatch(r"[A-Za-z0-9_-]{6,}", maybe_key):
                        access_key = maybe_key
                        break
                if access_key:
                    break
        return match.group(1), match.group(2), access_key
    parsed = urlparse(decoded)
    values = parse_qs(parsed.query)
    access_key = None
    for key in ("access_key", "access_key_id"):
        if values.get(key):
            access_key = values[key][0]
            break
    for key in ("z", "w"):
        for value in values.get(key, []):
            match = re.search(r"audio_playlist(-?\d+)_(\d+)", value)
            if match:
                if not access_key and "/" in value:
                    maybe_key = value.rsplit("/", 1)[-1]
                    if re.fullmatch(r"[A-Za-z0-9_-]{6,}", maybe_key):
                        access_key = maybe_key
                return match.group(1), match.group(2), access_key
    return None


def _photo(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("photo_1200", "photo_600", "photo_300", "photo_270", "photo_200", "photo_100"):
            if value.get(key):
                return value[key]
    return None


def _track_from_item(item: dict[str, Any], playlist_title: str, fallback_cover: str | None) -> Track | None:
    if not isinstance(item, dict) or not item.get("id"):
        return None
    owner_id = item.get("owner_id")
    track_id = item.get("id")
    artist = clean_text(item.get("artist"))
    title = clean_text(item.get("title"))
    if not artist and " - " in title:
        artist, title = [part.strip() for part in title.split(" - ", 1)]
    return Track(
        id=f"vk:{owner_id}_{track_id}",
        title=title or "Без названия",
        artists=[artist] if artist else [],
        album=playlist_title,
        duration=int(item["duration"]) if isinstance(item.get("duration"), (int, float)) else None,
        cover_url=fallback_cover,
        source_url=f"https://vk.com/audio?z=audio{owner_id}_{track_id}",
        stream_url=item.get("url") or None,
        available=bool(item.get("url")),
        extra={"vk_owner_id": owner_id, "vk_id": track_id},
    )


def _playlist(
    session: requests.Session,
    token: str,
    owner_id: str,
    album_id: str,
    access_key: str | None,
    max_tracks: int,
) -> Playlist:
    title = "Плейлист VK"
    cover = None
    try:
        data = _api(
            session,
            "audio.getPlaylists",
            {"owner_id": owner_id, "count": 100, "offset": 0},
            token,
        )
        for item in (data or {}).get("items") or []:
            if str(item.get("id")) == str(album_id):
                title = clean_text(item.get("title")) or title
                cover = _photo(item.get("photo"))
                break
    except UserError:
        pass

    tracks: list[Track] = []
    seen: set[str] = set()
    offset = 0
    total = None
    while offset < max_tracks:
        params: dict[str, Any] = {
            "owner_id": owner_id,
            "album_id": album_id,
            "count": 100,
            "offset": offset,
        }
        if access_key:
            params["access_key"] = access_key
        data = _api(
            session,
            "audio.get",
            params,
            token,
        )
        if not isinstance(data, dict):
            break
        if isinstance(data.get("count"), int):
            total = data["count"]
        items = data.get("items") or []
        for item in items:
            track = _track_from_item(item, title, cover)
            if track and track.id not in seen:
                seen.add(track.id)
                tracks.append(track)
            if len(tracks) >= max_tracks:
                break
        if not items or len(items) < 100 or len(tracks) >= max_tracks:
            break
        offset += len(items)
        if total is not None and offset >= total:
            break
    warnings = []
    if total and len(tracks) < total:
        warnings.append(f"VK вернул {len(tracks)} из {total} треков.")
    if not tracks:
        raise UserError("В этом плейлисте VK нет доступных треков.")
    return Playlist(
        platform="vk",
        kind="playlist",
        title=title,
        author="",
        cover_url=cover,
        tracks=tracks,
        source_url=f"https://vk.com/music/playlist/{owner_id}_{album_id}",
        download_mode="direct",
        warnings=warnings,
    )


def _owner_from_screen_name(session: requests.Session, token: str, screen_name: str) -> tuple[int, str, str | None]:
    data = _api(session, "utils.resolveScreenName", {"screen_name": screen_name}, token)
    if not isinstance(data, dict) or not data.get("object_id"):
        raise UserError(f"VK не нашёл профиль «{screen_name}».")
    object_id = int(data["object_id"])
    kind = data.get("type")
    owner_id = -object_id if kind in {"group", "page", "event"} else object_id
    name = screen_name
    cover = None
    try:
        if owner_id < 0:
            group = _api(
                session,
                "groups.getById",
                {"group_ids": str(abs(owner_id)), "fields": "photo_200"},
                token,
            )
            if isinstance(group, dict):
                group = (group.get("groups") or [group])[0]
            elif isinstance(group, list):
                group = group[0] if group else {}
            if isinstance(group, dict):
                name = clean_text(group.get("name")) or name
                cover = _photo(group.get("photo_200"))
        else:
            users = _api(
                session,
                "users.get",
                {"user_ids": str(owner_id), "fields": "photo_200"},
                token,
            )
            if isinstance(users, list) and users:
                user = users[0]
                name = clean_text(f"{user.get('first_name', '')} {user.get('last_name', '')}") or name
                cover = _photo(user.get("photo_200"))
    except UserError:
        pass
    return owner_id, name, cover


def _account(session: requests.Session, token: str, owner_id: int, name: str, cover: str | None, max_tracks: int) -> Playlist:
    tracks: list[Track] = []
    seen: set[str] = set()
    offset = 0
    total = None
    while offset < max_tracks:
        data = _api(
            session,
            "audio.get",
            {"owner_id": owner_id, "count": 100, "offset": offset},
            token,
        )
        if not isinstance(data, dict):
            break
        if isinstance(data.get("count"), int):
            total = data["count"]
        items = data.get("items") or []
        for item in items:
            track = _track_from_item(item, "", cover)
            if track and track.id not in seen:
                seen.add(track.id)
                tracks.append(track)
            if len(tracks) >= max_tracks:
                break
        if not items or len(items) < 100 or len(tracks) >= max_tracks:
            break
        offset += len(items)
        if total is not None and offset >= total:
            break
    warnings = []
    if total and len(tracks) < total:
        warnings.append(f"VK вернул {len(tracks)} из {total} аудиозаписей.")
    if not tracks:
        raise UserError(
            "В аудиозаписях этого профиля VK ничего не найдено.",
            hint="Проверьте, что токен имеет доступ к аудио и профиль не закрыт.",
        )
    return Playlist(
        platform="vk",
        kind="artist",
        title=f"{name} — аудиозаписи",
        author=name,
        cover_url=cover,
        tracks=tracks,
        source_url=f"https://vk.com/audios{owner_id}" if owner_id > 0 else f"https://vk.com/club{abs(owner_id)}",
        download_mode="direct",
        warnings=warnings,
    )


def resolve(url: str, settings: dict[str, Any]) -> Playlist:
    token = (settings.get("vk_token") or "").strip()
    if not token:
        raise UserError(
            "Для VK Музыки нужен токен VK с доступом к аудио.",
            hint="Откройте настройки → VK Музыка и вставьте токен. Без него VK не отдаёт музыку.",
            code="vk_token_missing",
        )
    max_tracks = int(settings.get("max_tracks") or 1000)
    session = make_session(settings)
    try:
        playlist_ids = _playlist_id_from_url(url)
        if playlist_ids:
            owner_id, album_id, access_key = playlist_ids
            return _playlist(session, token, owner_id, album_id, access_key, max_tracks)

        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        if not parts or parts[0] in {"music", "audio", "audios", "feed", "video", "wall"}:
            if parts and parts[0].startswith("audios") and re.fullmatch(r"-?\d+", parts[0][6:]):
                owner_id = int(parts[0][6:])
                try:
                    _, name, cover = _owner_from_screen_name(session, token, str(abs(owner_id)))
                except UserError:
                    name, cover = str(owner_id), None
                return _account(session, token, owner_id, name, cover, max_tracks)
            raise UserError(
                "Не удалось понять ссылку VK.",
                hint="Поддерживаются ссылки вида vk.com/music/playlist/… и ссылки на профили.",
            )
        screen_name = parts[0]
        owner_id, name, cover = _owner_from_screen_name(session, token, screen_name)
        return _account(session, token, owner_id, name, cover, max_tracks)
    finally:
        session.close()
