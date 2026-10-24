from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import requests

from ..models import Playlist, Track
from ..utils import UserError, make_session, short_error
from .common import clean_text

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
WEB_TOKEN_URL = "https://open.spotify.com/get_access_token?reason=transport&productType=web_player"
OPEN_URL = "https://open.spotify.com/"


class SpotifyAuthError(UserError):
    pass


@dataclass
class _Token:
    value: str
    expires_at: float


class SpotifyClient:
    def __init__(self, session: requests.Session, settings: dict[str, Any]):
        self.session = session
        self.client_id = (settings.get("spotify_client_id") or "").strip()
        self.client_secret = (settings.get("spotify_client_secret") or "").strip()
        self.market = (settings.get("spotify_market") or "US").upper()
        self._token: _Token | None = None
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Referer": "https://open.spotify.com/",
                "Origin": "https://open.spotify.com",
            }
        )

    def _fetch_token(self) -> _Token:
        if self.client_id and self.client_secret:
            try:
                response = self.session.post(
                    TOKEN_URL,
                    data={"grant_type": "client_credentials"},
                    auth=(self.client_id, self.client_secret),
                    timeout=30,
                )
            except requests.RequestException as exc:
                raise SpotifyAuthError(
                    f"Не удалось подключиться к Spotify: {short_error(exc)}",
                    hint="Проверьте интернет/VPN или настройки прокси.",
                ) from exc
            if response.status_code != 200:
                raise SpotifyAuthError(
                    f"Spotify не выдал токен (HTTP {response.status_code}).",
                    hint="Проверьте Client ID и Client Secret в настройках Spotify.",
                )
            try:
                data = response.json()
                access = data["access_token"]
            except (ValueError, KeyError) as exc:
                raise SpotifyAuthError("Spotify вернул некорректный ответ при выдаче токена.") from exc
            return _Token(access, time.time() + int(data.get("expires_in", 3600)) - 60)

        # Anonymous web-player token. Works for public playlists where Spotify is reachable.
        try:
            response = self.session.get(WEB_TOKEN_URL, timeout=30)
        except requests.RequestException as exc:
            raise SpotifyAuthError(
                f"Не удалось получить гостевой токен Spotify: {short_error(exc)}",
                hint="Включите VPN/прокси или задайте Client ID/Secret в настройках.",
            ) from exc
        if response.status_code == 200:
            try:
                data = response.json()
                access = data.get("accessToken")
                if access:
                    expires = int(data.get("expirationTime") or 0) / 1000
                    return _Token(access, expires - 60 if expires > time.time() else time.time() + 1800)
            except (ValueError, AttributeError):
                pass

        # Sometimes the token is embedded in the web-player HTML.
        try:
            page = self.session.get(OPEN_URL, timeout=30, headers={"Accept": "text/html"})
            if page.status_code == 200:
                match = re.search(r'"accessToken"\s*:\s*"([^"]+)"', page.text)
                if match:
                    return _Token(match.group(1), time.time() + 1800)
        except requests.RequestException:
            pass

        status = response.status_code
        hint = "Включите VPN/прокси или задайте Client ID/Secret в настройках Spotify."
        if status == 451:
            raise SpotifyAuthError(
                "Spotify недоступен из текущей сети (HTTP 451).",
                hint=hint,
                code="spotify_region",
            )
        if status == 403:
            raise SpotifyAuthError(
                "Spotify заблокировал запрос (HTTP 403).",
                hint=hint,
                code="spotify_region",
            )
        raise SpotifyAuthError(
            f"Spotify не выдал гостевой токен (HTTP {status}).",
            hint=hint,
            code="spotify_auth",
        )

    def token(self) -> str:
        if not self._token or self._token.expires_at <= time.time():
            self._token = self._fetch_token()
        return self._token.value

    def api(self, path: str, params: dict[str, Any] | None = None) -> Any:
        last_error: UserError | None = None
        for attempt in range(2):
            try:
                response = self.session.get(
                    API + path,
                    params=params,
                    headers={"Authorization": f"Bearer {self.token()}"},
                    timeout=35,
                )
            except requests.RequestException as exc:
                raise UserError(
                    f"Ошибка сети Spotify: {short_error(exc)}",
                    hint="Проверьте интернет/VPN/прокси.",
                ) from exc
            if response.status_code == 401 and attempt == 0:
                self._token = None
                continue
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "10")
                raise UserError(
                    f"Spotify ограничил запросы (429). Повторите через {retry_after} сек.",
                    hint="Можно уменьшить количество одновременных загрузок или подождать.",
                )
            if response.status_code == 404:
                raise UserError("Плейлист, альбом или трек Spotify не найден (404).")
            if response.status_code in (403, 451):
                hint = "Включите VPN/прокси (Spotify недоступен в РФ) или укажите Client ID/Secret."
                if "URL Blocked" in response.text or response.status_code == 451:
                    raise UserError(
                        f"Spotify недоступен из текущей сети (HTTP {response.status_code}).",
                        hint=hint,
                        code="spotify_region",
                    )
                raise UserError(
                    f"Spotify запретил доступ к этим данным (HTTP {response.status_code}).",
                    hint=hint,
                    code="spotify_forbidden",
                )
            if response.status_code >= 500:
                last_error = UserError(
                    f"Spotify временно недоступен (HTTP {response.status_code}).",
                    hint="Попробуйте позже.",
                )
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                raise last_error
            if response.status_code != 200:
                raise UserError(f"Spotify вернул HTTP {response.status_code}.")
            try:
                return response.json()
            except ValueError as exc:
                raise UserError("Spotify вернул некорректный ответ.") from exc
        raise last_error or UserError("Не удалось получить данные Spotify.")


def _cover(album: dict[str, Any] | None, fallback: str | None = None) -> str | None:
    images = (album or {}).get("images") or []
    if images and isinstance(images[0], dict):
        return images[0].get("url") or fallback
    return fallback


def _track(raw: dict[str, Any], album_hint: dict[str, Any] | None = None) -> Track | None:
    if not isinstance(raw, dict) or raw.get("is_local"):
        return None
    if raw.get("type") not in (None, "track"):
        return None
    track_id = raw.get("id")
    if not track_id:
        return None
    album = raw.get("album") if isinstance(raw.get("album"), dict) else (album_hint or {})
    artists = [a.get("name") for a in raw.get("artists") or [] if isinstance(a, dict) and a.get("name")]
    release = str(album.get("release_date") or "")
    return Track(
        id=f"sp:{track_id}",
        title=clean_text(raw.get("name")) or "Без названия",
        artists=artists,
        album=clean_text(album.get("name")),
        duration=int(raw["duration_ms"] / 1000) if isinstance(raw.get("duration_ms"), (int, float)) else None,
        cover_url=_cover(album),
        source_url=(raw.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/track/{track_id}",
        year=release[:4] if release[:4].isdigit() else None,
        track_number=raw.get("track_number") if isinstance(raw.get("track_number"), int) else None,
        disc_number=raw.get("disc_number") if isinstance(raw.get("disc_number"), int) else None,
        available=raw.get("is_playable", True) is not False,
    )


def _playlist_api(client: SpotifyClient, playlist_id: str, max_tracks: int) -> Playlist:
    meta = client.api(f"/playlists/{playlist_id}", params={"market": client.market})
    items: list[dict[str, Any]] = []
    total = int((meta.get("tracks") or {}).get("total") or 0)
    offset = 0
    while offset < max_tracks and (offset < total or offset == 0):
        page = client.api(
            f"/playlists/{playlist_id}/tracks",
            params={"limit": 100, "offset": offset, "market": client.market},
        )
        page_items = page.get("items") or []
        items.extend(page_items)
        if not page_items or len(page_items) < 100:
            break
        offset += len(page_items)
        time.sleep(0.05)
    tracks: list[Track] = []
    warnings: list[str] = []
    cover = _cover(meta)
    for item in items[:max_tracks]:
        raw = item.get("track") or item.get("item") if isinstance(item, dict) else None
        track = _track(raw, {"images": (meta.get("images") or []), "name": ""})
        if track:
            if not track.cover_url:
                track.cover_url = cover
            tracks.append(track)
    if len(items) > max_tracks:
        warnings.append(f"Достигнут лимит {max_tracks} треков.")
    if not tracks:
        raise UserError("В этом плейлисте Spotify нет доступных треков.")
    owner = meta.get("owner") or {}
    return Playlist(
        platform="spotify",
        kind="playlist",
        title=clean_text(meta.get("name")) or "Плейлист Spotify",
        author=clean_text(owner.get("display_name") or owner.get("id")),
        description=clean_text(meta.get("description")),
        cover_url=cover,
        tracks=tracks,
        source_url=(meta.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/playlist/{playlist_id}",
        download_mode="match",
        warnings=warnings,
    )


def _album_api(client: SpotifyClient, album_id: str, max_tracks: int) -> Playlist:
    meta = client.api(f"/albums/{album_id}", params={"market": client.market})
    album_hint = {"name": meta.get("name"), "images": meta.get("images") or [], "release_date": meta.get("release_date")}
    tracks: list[Track] = []
    total = int((meta.get("tracks") or {}).get("total") or 0)
    offset = 0
    while offset < total and offset < max_tracks:
        page = client.api(
            f"/albums/{album_id}/tracks",
            params={"limit": 50, "offset": offset, "market": client.market},
        )
        page_items = page.get("items") or []
        for raw in page_items:
            track = _track(raw, album_hint)
            if track:
                tracks.append(track)
        if not page_items or len(page_items) < 50:
            break
        offset += len(page_items)
        time.sleep(0.03)
    if not tracks:
        raise UserError("В этом альбоме Spotify нет доступных треков.")
    artists = [a.get("name") for a in meta.get("artists") or [] if isinstance(a, dict) and a.get("name")]
    return Playlist(
        platform="spotify",
        kind="album",
        title=clean_text(meta.get("name")) or "Альбом Spotify",
        author=", ".join(a for a in artists if a),
        cover_url=_cover(meta),
        tracks=tracks,
        source_url=(meta.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/album/{album_id}",
        download_mode="match",
        description=str(meta.get("release_date") or ""),
    )


def _track_api(client: SpotifyClient, track_id: str) -> Playlist:
    meta = client.api(f"/tracks/{track_id}", params={"market": client.market})
    track = _track(meta)
    if not track:
        raise UserError("Не удалось прочитать трек Spotify.")
    return Playlist(
        platform="spotify",
        kind="track",
        title=track.title,
        author=track.artist,
        cover_url=track.cover_url,
        tracks=[track],
        source_url=track.source_url or f"https://open.spotify.com/track/{track_id}",
        download_mode="match",
        description=track.album,
    )


def _artist_api(client: SpotifyClient, artist_id: str, max_tracks: int) -> Playlist:
    meta = client.api(f"/artists/{artist_id}")
    tracks: list[Track] = []
    seen: set[str] = set()
    warnings: list[str] = []
    albums: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = client.api(
            f"/artists/{artist_id}/albums",
            params={
                "include_groups": "album,single",
                "limit": 50,
                "offset": offset,
                "market": client.market,
            },
        )
        items = page.get("items") or []
        albums.extend(items)
        if not items or len(items) < 50:
            break
        offset += len(items)
        time.sleep(0.05)
    for album in albums:
        if len(tracks) >= max_tracks:
            warnings.append(f"Достигнут лимит {max_tracks} треков.")
            break
        album_id = album.get("id")
        if not album_id:
            continue
        album_hint = {
            "name": album.get("name"),
            "images": album.get("images") or [],
            "release_date": album.get("release_date"),
        }
        try:
            page = client.api(
                f"/albums/{album_id}/tracks",
                params={"limit": 50, "offset": 0, "market": client.market},
            )
            for raw in page.get("items") or []:
                track = _track(raw, album_hint)
                if track and track.id not in seen:
                    seen.add(track.id)
                    tracks.append(track)
                if len(tracks) >= max_tracks:
                    break
        except UserError as exc:
            warnings.append(f"Альбом «{album.get('name')}» пропущен: {exc.message}")
        time.sleep(0.04)
    if not tracks:
        raise UserError("У этого исполнителя Spotify нет доступных треков.")
    return Playlist(
        platform="spotify",
        kind="artist",
        title=f"{clean_text(meta.get('name')) or 'Исполнитель'} — треки",
        author=clean_text(meta.get("name")),
        cover_url=_cover({"images": meta.get("images") or []}),
        tracks=tracks,
        source_url=(meta.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/artist/{artist_id}",
        download_mode="match",
        warnings=warnings,
    )


def _embed_entity(
    kind: str,
    item_id: str,
    settings: dict[str, Any],
) -> tuple[str, str, str, str | None, list[Track], bool, str]:
    session = make_session(settings)
    url = f"https://open.spotify.com/embed/{kind}/{item_id}"
    try:
        try:
            response = session.get(url, timeout=30, headers={"Accept": "text/html"})
        except requests.RequestException as exc:
            raise SpotifyAuthError(
                f"Не удалось открыть страницу Spotify: {short_error(exc)}",
                hint="Включите VPN/прокси или задайте Client ID/Secret в настройках Spotify.",
            ) from exc
    finally:
        session.close()
    if response.status_code in (403, 451):
        raise SpotifyAuthError(
            f"Spotify недоступен из текущей сети (HTTP {response.status_code}).",
            hint="Включите VPN/прокси или задайте Client ID/Secret в настройках Spotify.",
            code="spotify_region",
        )
    if response.status_code != 200:
        raise UserError(f"Не удалось открыть страницу Spotify (HTTP {response.status_code}).")
    match = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        response.text,
        re.DOTALL,
    )
    if not match:
        raise UserError("Spotify вернул страницу без данных плейлиста.")
    try:
        data = json.loads(match.group(1))
    except ValueError as exc:
        raise UserError("Не удалось разобрать данные страницы Spotify.") from exc

    entity: dict[str, Any] | None = None

    def walk(node: Any) -> None:
        nonlocal entity
        if entity is not None:
            return
        if isinstance(node, dict):
            if isinstance(node.get("trackList"), (list, dict)):
                entity = node
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    if not entity:
        raise UserError("В данных Spotify не оказалось списка треков.")

    track_list = entity.get("trackList")
    if isinstance(track_list, dict):
        track_list = track_list.get("items") or track_list.get("data") or []
    if not isinstance(track_list, list):
        track_list = []

    entity_type = str(entity.get("type") or kind)
    title = clean_text(entity.get("name") or entity.get("title")) or "Spotify"
    subtitle = clean_text(entity.get("subtitle") or entity.get("description"))
    cover = None
    cover_art = entity.get("coverArt") or {}
    if isinstance(cover_art, dict):
        sources = cover_art.get("sources") or []
        if sources and isinstance(sources[0], dict):
            cover = sources[0].get("url")
    if not cover:
        visual = entity.get("visualIdentity") or {}
        images = visual.get("image") if isinstance(visual, dict) else []
        if images and isinstance(images[0], dict):
            cover = images[0].get("url")
    if not cover:
        images = entity.get("images") or []
        if images and isinstance(images[0], dict):
            cover = images[0].get("url")

    tracks: list[Track] = []
    for item in track_list:
        if not isinstance(item, dict):
            continue
        uri = str(item.get("uri") or "")
        track_id = uri.split(":")[-1] if uri else str(item.get("id") or "")
        if not track_id:
            continue
        subtitle_item = clean_text(item.get("subtitle") or "")
        duration = item.get("duration")
        if isinstance(duration, (int, float)) and duration > 10000:
            duration = int(duration / 1000)
        elif isinstance(duration, (int, float)):
            duration = int(duration)
        else:
            duration = None
        tracks.append(
            Track(
                id=f"sp:{track_id}",
                title=clean_text(item.get("title") or item.get("name")) or "Без названия",
                artists=[subtitle_item] if subtitle_item else [],
                album=title if entity_type == "album" else "",
                duration=duration,
                cover_url=cover,
                source_url=f"https://open.spotify.com/track/{track_id}",
                available=True,
            )
        )
    if not tracks:
        raise UserError("Spotify не отдал треки для этой ссылки.")

    warning = "Данные Spotify получены из встроенной страницы (ограниченный режим)."
    limited = entity_type == "artist"
    if limited:
        warning += " Для исполнителя доступны только треки со страницы, а не весь каталог."
    return title, subtitle, clean_text(entity.get("description")), cover, tracks, limited, warning


def _resolve_embed(kind: str, item_id: str, max_tracks: int, settings: dict[str, Any]) -> Playlist:
    result = _embed_entity(kind, item_id, settings)
    title, author, description, cover, tracks, limited, warning = result
    tracks = tracks[:max_tracks]
    warnings = [warning]
    if limited:
        warnings.append("Чтобы получить все треки исполнителя, нужен доступ к Spotify API (VPN/прокси).")
    return Playlist(
        platform="spotify",
        kind=kind if kind in {"playlist", "album", "artist", "track"} else "playlist",
        title=title,
        author=author,
        description=description,
        cover_url=cover,
        tracks=tracks,
        source_url=f"https://open.spotify.com/{kind}/{item_id}",
        download_mode="match",
        warnings=warnings,
    )


def resolve(url: str, settings: dict[str, Any]) -> Playlist:
    host = (urlparse(url).hostname or "").lower()
    if not (host.endswith("spotify.com") or host in {"spotify.link", "spoti.fi"}):
        raise UserError("Это не ссылка Spotify.")
    path = unquote(urlparse(url).path)
    parts = [part for part in path.split("/") if part]
    if parts and re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", parts[0], re.IGNORECASE):
        parts = parts[1:]
    if len(parts) < 2 or parts[0] not in {"playlist", "album", "track", "artist"}:
        raise UserError(
            "Не удалось понять ссылку Spotify.",
            hint="Поддерживаются ссылки на плейлист, альбом, трек и исполнителя.",
        )
    kind, item_id = parts[0], parts[1]
    max_tracks = int(settings.get("max_tracks") or 1000)

    session = make_session(settings)
    client = SpotifyClient(session, settings)
    api_error: UserError | None = None
    try:
        if kind == "playlist":
            return _playlist_api(client, item_id, max_tracks)
        if kind == "album":
            return _album_api(client, item_id, max_tracks)
        if kind == "track":
            return _track_api(client, item_id)
        if kind == "artist":
            return _artist_api(client, item_id, max_tracks)
    except UserError as exc:
        api_error = exc
    finally:
        session.close()

    try:
        return _resolve_embed(kind, item_id, max_tracks, settings)
    except UserError as embed_error:
        if api_error:
            if api_error.hint and embed_error.hint and embed_error.hint not in api_error.hint:
                api_error.hint = f"{api_error.hint} {embed_error.hint}"
            raise api_error
        raise embed_error
