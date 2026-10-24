from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import Any, Callable

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError, ExtractorError, UnsupportedError

from ..config import SETTINGS_DIR
from ..models import Playlist, Track
from ..utils import UserError, clean_title_for_match, short_error, stable_hash
from .common import clean_text, platform_from_host

CACHE_DIR = SETTINGS_DIR / ".cache" / "yt-dlp"


class _Logger:
    def __init__(self, log: Callable[[str], None] | None = None):
        self._log = log
        self._seen: set[str] = set()

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        self._emit(msg)

    def error(self, msg: str) -> None:
        self._emit(msg)

    def _emit(self, msg: str) -> None:
        if not self._log:
            return
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(msg)).strip()
        msg = msg.removeprefix("WARNING: ").removeprefix("ERROR: ")
        if not msg or msg in self._seen or len(self._seen) > 40:
            return
        self._seen.add(msg)
        self._log(f"yt-dlp: {msg}")


def _base_opts(settings: dict[str, Any], *, flat: bool = True) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "ignoreerrors": True,
        "extract_flat": "in_playlist" if flat else False,
        "playlistend": max(1, int(settings.get("max_tracks") or 1000)) + 1,
        "socket_timeout": 30,
        "retries": 3,
        "extractor_retries": 2,
        "fragment_retries": 3,
        "cachedir": str(CACHE_DIR),
        "nocheckcertificate": False,
        "geo_bypass": True,
        "logger": _Logger(),
    }
    proxy = (settings.get("proxy") or "").strip()
    if proxy:
        opts["proxy"] = proxy
    browser = (settings.get("cookies_browser") or "").strip()
    if browser:
        opts["cookiesfrombrowser"] = (browser, None, None, None)
    return opts


def _thumb(entry: dict[str, Any]) -> str | None:
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    thumbs = entry.get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        for thumb in reversed(thumbs):
            if isinstance(thumb, dict) and thumb.get("url"):
                return thumb["url"]
    return None


def _split_artist_title(title: str, artist: str) -> tuple[str, str]:
    title = clean_text(title)
    artist = clean_text(artist)
    if artist and title.casefold().startswith(artist.casefold() + " - "):
        title = title[len(artist) + 3 :].strip()
    if not artist and " - " in title:
        left, right = title.split(" - ", 1)
        if 0 < len(left) <= 60 and right.strip():
            artist, title = left.strip(), right.strip()
    return title, artist


def _entry_url(entry: dict[str, Any], platform: str) -> str | None:
    url = entry.get("webpage_url") or entry.get("original_url")
    if url:
        return url
    url = entry.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    entry_id = entry.get("id") or (url if isinstance(url, str) else None)
    if entry_id and platform == "youtube":
        return f"https://www.youtube.com/watch?v={entry_id}"
    if entry_id and platform == "soundcloud":
        return f"https://soundcloud.com/{entry_id}"
    return None


def _availability(entry: dict[str, Any]) -> bool:
    availability = str(entry.get("availability") or "").lower()
    if availability in {"private", "premium_only", "subscriber_only", "needs_auth", "unlisted"}:
        return False
    if entry.get("is_private"):
        return False
    return True


def _track_from_entry(
    entry: dict[str, Any],
    platform: str,
    index: int,
    fallback_cover: str | None = None,
) -> Track | None:
    if not isinstance(entry, dict):
        return None
    url = _entry_url(entry, platform)
    if not url:
        return None
    entry_id = str(entry.get("id") or stable_hash(url, 16))
    raw_title = entry.get("track") or entry.get("title") or entry.get("alt_title") or ""
    raw_artist = entry.get("artist") or entry.get("creator") or entry.get("uploader") or entry.get("channel") or ""
    if isinstance(raw_artist, str) and raw_artist.endswith(" - Topic"):
        raw_artist = raw_artist[: -len(" - Topic")]
    title, artist = _split_artist_title(str(raw_title), str(raw_artist))
    artists = [artist] if artist else []
    duration = entry.get("duration")
    try:
        duration = int(round(float(duration))) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    album = entry.get("album") if isinstance(entry.get("album"), str) else ""
    year = entry.get("release_year") or entry.get("release_date")
    if year and not isinstance(year, str):
        year = str(year)
    if isinstance(year, str) and len(year) >= 4:
        year = year[:4]
    return Track(
        id=f"{platform}:{entry_id}",
        title=title or f"Трек {index + 1}",
        artists=artists,
        album=album,
        duration=duration,
        cover_url=_thumb(entry) or fallback_cover,
        source_url=url,
        year=year or None,
        track_number=index + 1,
        available=_availability(entry),
        extra={"ytdlp": True},
    )


def _flatten_entries(entries: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(entries, list):
        try:
            entries = list(entries or [])
        except TypeError:
            return result
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("_type") == "playlist" and entry.get("entries"):
            result.extend(_flatten_entries(entry.get("entries")))
        else:
            result.append(entry)
    return result


def _raise_download_error(exc: BaseException) -> None:
    message = short_error(exc)
    low = message.lower()
    hint = None
    if "sign in to confirm" in low or "not a bot" in low or "cookies" in low:
        hint = "Выберите браузер в настройках → «Cookies из браузера» и повторите."
    elif "private" in low or "login" in low or "members-only" in low:
        hint = "Контент приватный. Выберите браузер в настройках → «Cookies из браузера»."
    elif "unsupported url" in low:
        hint = "Этот сайт пока не поддерживается. Попробуйте прямую ссылку на плейлист или трек."
    elif "connection" in low or "timed out" in low or "network" in low or "ssl" in low:
        hint = "Проверьте интернет/VPN/прокси и повторите."
    elif "video unavailable" in low or "not available" in low:
        hint = "Видео/трек недоступен в вашем регионе или удалён."
    raise UserError(f"yt-dlp: {message}", hint=hint)


def _kind_for(platform: str, url: str, is_playlist: bool) -> str:
    path = re.sub(r"^https?://[^/]+", "", url)
    if not is_playlist:
        return "track"
    if platform == "soundcloud":
        return "playlist" if "/sets/" in path else "artist"
    if platform == "youtube":
        if re.search(r"/(?:@|channel/|user/|c/)[^/]+", path) and "/playlist" not in path:
            return "artist"
        return "playlist"
    if platform == "bandcamp":
        return "album" if "/album/" in path else "artist"
    return "playlist"


def resolve(url: str, settings: dict[str, Any], platform_hint: str | None = None) -> Playlist:
    platform = platform_hint or platform_from_host(url.split("/")[2] if "://" in url else "")
    if platform not in {"soundcloud", "youtube", "bandcamp", "generic"}:
        platform = "generic"
    max_tracks = max(1, int(settings.get("max_tracks") or 1000))
    opts = _base_opts(settings, flat=True)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except (DownloadError, ExtractorError, UnsupportedError) as exc:
        _raise_download_error(exc)
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises many things
        _raise_download_error(exc)

    if not isinstance(info, dict):
        raise UserError("yt-dlp не смог разобрать ссылку.")

    entries = info.get("entries")
    is_playlist = bool(entries) or info.get("_type") in {"playlist", "multi_video"}
    if not is_playlist:
        tit = info.get("title") or "Трек"
        entry = {
            **info,
            "title": info.get("track") or tit,
            "artist": info.get("artist") or info.get("creator") or info.get("uploader"),
            "webpage_url": info.get("webpage_url") or info.get("original_url") or url,
        }
        track = _track_from_entry(entry, platform, 0)
        if not track:
            raise UserError("Не удалось прочитать трек.")
        return Playlist(
            platform=platform,
            kind="track",
            title=str(info.get("track") or info.get("title") or track.title),
            author=track.artist,
            cover_url=track.cover_url,
            tracks=[track],
            source_url=info.get("webpage_url") or url,
            download_mode="direct",
            description=clean_text(info.get("description")),
        )

    raw_entries = _flatten_entries(entries)
    playlist_cover = info.get("thumbnail")
    tracks: list[Track] = []
    for index, entry in enumerate(raw_entries[:max_tracks]):
        track = _track_from_entry(entry, platform, index, playlist_cover)
        if track:
            tracks.append(track)
    if not tracks:
        raise UserError(
            "Не удалось найти треки по этой ссылке.",
            hint="Проверьте ссылку или попробуйте страницу самого плейлиста/сета.",
        )

    title = clean_text(info.get("title") or info.get("playlist_title") or info.get("uploader"))
    if not title:
        title = "Плейлист"
    author = clean_text(info.get("uploader") or info.get("channel") or info.get("creator"))
    warnings: list[str] = []
    if len(raw_entries) > max_tracks:
        warnings.append(f"Загружены первые {max_tracks} треков (лимит в настройках).")
    unavailable = sum(1 for track in tracks if not track.available)
    if unavailable:
        warnings.append(f"{unavailable} трек(ов) помечены как недоступные.")

    return Playlist(
        platform=platform,
        kind=_kind_for(platform, url, True),
        title=title,
        author=author,
        description=clean_text(info.get("description")),
        cover_url=playlist_cover or (tracks[0].cover_url if tracks else None),
        tracks=tracks,
        source_url=info.get("webpage_url") or url,
        download_mode="direct",
        warnings=warnings,
    )


def search(query: str, source: str, settings: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    prefix = "scsearch" if source == "soundcloud" else "ytsearch"
    opts = _base_opts(settings, flat=True)
    opts.update({"ignoreerrors": True, "extract_flat": True, "playlistend": limit, "skip_download": True})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"{prefix}{limit}:{query}", download=False)
    except (DownloadError, ExtractorError, UnsupportedError) as exc:
        _raise_download_error(exc)
    except Exception as exc:  # noqa: BLE001
        _raise_download_error(exc)

    entries = _flatten_entries((info or {}).get("entries") or [])
    results: list[dict[str, Any]] = []
    for entry in entries:
        url = _entry_url(entry, "youtube" if source == "youtube" else "soundcloud")
        if not url:
            continue
        duration = entry.get("duration")
        try:
            duration = int(round(float(duration))) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
        results.append(
            {
                "url": url,
                "id": str(entry.get("id") or ""),
                "title": clean_text(entry.get("title")),
                "channel": clean_text(entry.get("channel") or entry.get("uploader") or entry.get("creator")),
                "duration": duration,
                "view_count": entry.get("view_count"),
                "thumbnail": _thumb(entry),
                "description": clean_text(entry.get("description"))[:400],
            }
        )
    return results


def download_audio(
    url: str,
    settings: dict[str, Any],
    tmpdir: Path,
    *,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[Path, dict[str, Any]]:
    tmpdir.mkdir(parents=True, exist_ok=True)
    opts = _base_opts(settings, flat=False)
    opts.update(
        {
            "skip_download": False,
            "ignoreerrors": False,
            "noplaylist": True,
            "extract_flat": False,
            "format": "bestaudio/best",
            "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
            "overwrites": True,
            "progress_hooks": [progress_hook] if progress_hook else [],
            "logger": _Logger(log),
            "retries": 3,
            "fragment_retries": 3,
            "concurrent_fragment_downloads": 1,
            "noprogress": True,
        }
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not isinstance(info, dict):
                raise UserError("yt-dlp не вернул информацию о треке.")
            path = None
            requested = info.get("requested_downloads") or []
            if requested and isinstance(requested[0], dict):
                path = requested[0].get("filepath")
            if not path:
                try:
                    path = ydl.prepare_filename(info)
                except Exception:  # noqa: BLE001
                    path = None
            candidates = []
            if path:
                candidates.append(Path(path))
            candidates.extend(Path(p) for p in glob.glob(str(tmpdir / "*")))
            for candidate in candidates:
                if candidate.is_file() and candidate.stat().st_size > 1024:
                    return candidate, info
            raise UserError("Файл скачался, но не найден на диске.")
    except DownloadCancelled:
        raise
    except (DownloadError, ExtractorError, UnsupportedError) as exc:
        _raise_download_error(exc)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, UserError):
            raise
        _raise_download_error(exc)
    raise UserError("Не удалось скачать аудио.")
