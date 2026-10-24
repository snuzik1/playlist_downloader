from __future__ import annotations

import math
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests
from yt_dlp.utils import DownloadCancelled

from . import tagging
from .platforms import ytdlp_source
from .utils import (
    UserError,
    add_suffix,
    clean_title_for_match,
    format_duration,
    make_session,
    normalize_text,
    short_error,
    unique_path,
)

BAD_KEYWORDS = (
    "karaoke",
    "instrumental",
    "cover",
    "remix",
    "mashup",
    "reaction",
    "tutorial",
    "lesson",
    "1 hour",
    "10 hours",
    "loop",
    "nightcore",
    "slowed",
    "sped up",
    "8d audio",
    "acapella",
    "a cappella",
    "ringtone",
    "demo",
    "teaser",
    "trailer",
)

_SEARCH_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_SEARCH_LOCK = threading.Lock()
_SEARCH_TTL = 1800


@dataclass
class EngineCallbacks:
    state: Callable[[str, float | None, float | None, int | None], None]
    log: Callable[[str], None]
    cancel: Callable[[], bool]
    source: Callable[[str, str], None]


class CoverCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, tuple[bytes, str] | None] = {}

    def get(self, url: str | None, workdir: Path, ffmpeg: str, session: requests.Session) -> tuple[bytes, str] | None:
        if not url:
            return None
        with self._lock:
            if url in self._data:
                return self._data[url]
        result = _fetch_cover(url, workdir, ffmpeg, session)
        with self._lock:
            if len(self._data) > 150:
                self._data.clear()
            self._data[url] = result
        return result


def _fetch_cover(url: str, workdir: Path, ffmpeg: str, session: requests.Session) -> tuple[bytes, str] | None:
    try:
        response = session.get(url, timeout=25, stream=True, headers={"Accept": "image/*,*/*;q=0.8"})
        if response.status_code != 200:
            response.close()
            return None
        content_type = (response.headers.get("Content-Type") or "").lower()
        data = b""
        for chunk in response.iter_content(65536):
            if not chunk:
                continue
            data += chunk
            if len(data) > 12 * 1024 * 1024:
                return None
        response.close()
    except requests.RequestException:
        return None
    if not data:
        return None
    mime = "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    elif data[:6] in {b"GIF87a", b"GIF89a"}:
        mime = "image/gif"
    elif "png" in content_type:
        mime = "image/png"

    if mime in {"image/jpeg", "image/png"}:
        return data, mime

    # Most players do not understand WebP/GIF covers. Convert to JPEG.
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "cover.src"
    dst = workdir / "cover.jpg"
    try:
        src.write_bytes(data)
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(dst)],
            capture_output=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode == 0 and dst.is_file():
            return dst.read_bytes(), "image/jpeg"
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        for path in (src, dst):
            try:
                path.unlink()
            except OSError:
                pass
    return data, mime


def _cache_search(key: str) -> list[dict[str, Any]] | None:
    with _SEARCH_LOCK:
        item = _SEARCH_CACHE.get(key)
        if item and time.time() - item[0] < _SEARCH_TTL:
            return item[1]
        if item:
            _SEARCH_CACHE.pop(key, None)
    return None


def _store_search(key: str, value: list[dict[str, Any]]) -> None:
    with _SEARCH_LOCK:
        if len(_SEARCH_CACHE) > 300:
            _SEARCH_CACHE.clear()
        _SEARCH_CACHE[key] = (time.time(), value)


def _score_candidate(track: Any, candidate: dict[str, Any]) -> float:
    title = normalize_text(clean_title_for_match(track.title))
    artist = normalize_text(track.artist)
    target_all = f"{artist} {title}".strip()
    cand_title = normalize_text(clean_title_for_match(candidate.get("title", "")))
    channel = normalize_text(candidate.get("channel", ""))
    score = 0.0

    target_duration = track.duration
    cand_duration = candidate.get("duration")
    if target_duration and cand_duration:
        diff = abs(int(cand_duration) - int(target_duration))
        if diff <= 3:
            score += 45
        elif diff <= 6:
            score += 36
        elif diff <= 12:
            score += 24
        elif diff <= 20:
            score += 12
        elif diff <= 35:
            score += 0
        elif diff <= 60:
            score -= 22
        else:
            score -= 65
    elif target_duration and not cand_duration:
        score -= 4

    if title and len(title) >= 3 and title in cand_title:
        score += 30
    else:
        try:
            from difflib import SequenceMatcher

            score += SequenceMatcher(None, title, cand_title).ratio() * 15
        except Exception:  # noqa: BLE001
            pass
    if artist and artist in cand_title:
        score += 14
    if artist and artist in channel:
        score += 12
    if target_all and target_all in cand_title:
        score += 8
    if "topic" in channel:
        score += 16
    if "vevo" in channel or "official" in channel:
        score += 6
    target_low = normalize_text(track.title)
    for keyword in BAD_KEYWORDS:
        if keyword in cand_title and keyword not in target_low and keyword not in title:
            score -= 18
    views = candidate.get("view_count")
    if isinstance(views, (int, float)) and views > 0:
        score += min(5.0, math.log10(views + 1))
    return score


def _pick_candidate(track: Any, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    scored = sorted(
        ((_score_candidate(track, candidate), candidate) for candidate in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, best = scored[0]
    cand_duration = best.get("duration")
    duration_ok = not track.duration or not cand_duration or abs(int(cand_duration) - int(track.duration)) <= 45
    title_norm = normalize_text(clean_title_for_match(track.title))
    candidate_title = normalize_text(clean_title_for_match(best.get("title", "")))
    title_ok = bool(title_norm and title_norm in candidate_title)
    if best_score < 8 and not (duration_ok and title_ok):
        return None
    if track.duration and cand_duration and abs(int(cand_duration) - int(track.duration)) > 120:
        return None
    return best


def _find_source(track: Any, settings: dict[str, Any], cb: EngineCallbacks) -> tuple[str, dict[str, Any]]:
    configured = (settings.get("match_source") or "auto").lower()
    sources = [configured] if configured in {"youtube", "soundcloud"} else ["youtube", "soundcloud"]
    query = track.query
    errors: list[str] = []
    for source in sources:
        if cb.cancel():
            raise DownloadCancelled("cancelled")
        label = "YouTube" if source == "youtube" else "SoundCloud"
        cache_key = f"{source}|{normalize_text(query)}"
        candidates = _cache_search(cache_key)
        if candidates is None:
            cb.log(f"Ищу на {label}: {query}")
            try:
                candidates = ytdlp_source.search(query, source, settings, limit=8)
            except UserError as exc:
                errors.append(f"{label}: {exc.message}")
                cb.log(f"{label}: {exc.message}")
                candidates = []
            _store_search(cache_key, candidates)
        if not candidates:
            cb.log(f"{label}: ничего не найдено")
            continue
        chosen = _pick_candidate(track, candidates)
        if not chosen:
            cb.log(f"{label}: нет подходящего варианта (проверено {len(candidates)})")
            continue
        if source == "youtube":
            cb.log(f"Найдено на YouTube: {chosen.get('title')} ({format_duration(chosen.get('duration'))})")
        else:
            cb.log(f"Найдено на SoundCloud: {chosen.get('title')} ({format_duration(chosen.get('duration'))})")
        return source, chosen
    hint = "Попробуйте изменить название трека, включить VPN/прокси или cookies из браузера."
    if errors:
        hint = errors[-1] + ". " + hint
    raise UserError("Не удалось найти подходящий трек для скачивания.", hint=hint)


def _download_http(
    url: str,
    dest_dir: Path,
    cb: EngineCallbacks,
    session: requests.Session,
) -> Path:
    cb.state("downloading", 6, None, None)
    headers = {
        "Referer": "https://vk.com/",
        "Accept": "*/*",
    }
    try:
        response = session.get(url, headers=headers, timeout=40, stream=True)
    except requests.RequestException as exc:
        raise UserError(f"Не удалось скачать аудио VK: {short_error(exc)}") from exc
    if response.status_code != 200:
        response.close()
        raise UserError(f"VK отдал HTTP {response.status_code} при скачивании аудио.")
    total = int(response.headers.get("Content-Length") or 0)
    suffix = Path(url.split("?")[0]).suffix.lower()
    if suffix not in {".mp3", ".m4a", ".aac", ".ogg", ".opus"}:
        ctype = (response.headers.get("Content-Type") or "").lower()
        suffix = ".m4a" if "mp4" in ctype or "aac" in ctype else ".mp3"
    dest = dest_dir / f"raw{suffix}"
    downloaded = 0
    started = time.time()
    try:
        with dest.open("wb") as handle:
            for chunk in response.iter_content(65536):
                if cb.cancel():
                    raise DownloadCancelled("cancelled")
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                if total:
                    percent = 6 + (downloaded / total) * 78
                    elapsed = max(0.1, time.time() - started)
                    speed = downloaded / elapsed
                    eta = int((total - downloaded) / speed) if speed > 0 else None
                    cb.state("downloading", percent, speed, eta)
                elif downloaded % (1024 * 512) < 65536:
                    cb.state("downloading", 40, downloaded / max(0.1, time.time() - started), None)
    finally:
        response.close()
    if not dest.is_file() or dest.stat().st_size < 1024:
        raise UserError("VK вернул пустой аудиофайл.")
    return dest


def _state_hook(cb: EngineCallbacks, base_percent: float = 6, scale: float = 78) -> Callable[[dict[str, Any]], None]:
    def hook(data: dict[str, Any]) -> None:
        if cb.cancel():
            raise DownloadCancelled("cancelled")
        if data.get("status") == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            percent = base_percent
            if total:
                percent = base_percent + (downloaded / total) * scale
            else:
                percent = min(base_percent + scale, base_percent + (downloaded / (8 * 1024 * 1024)) * scale)
            cb.state("downloading", percent, data.get("speed"), data.get("eta"))
        elif data.get("status") == "finished":
            cb.state("downloading", base_percent + scale, None, None)

    return hook


def _download_ytdlp(
    url: str,
    settings: dict[str, Any],
    workdir: Path,
    cb: EngineCallbacks,
) -> tuple[Path, dict[str, Any]]:
    cb.state("downloading", 6, None, None)
    try:
        return ytdlp_source.download_audio(
            url,
            settings,
            workdir,
            progress_hook=_state_hook(cb),
            log=cb.log,
        )
    except DownloadCancelled:
        raise
    except UserError:
        if cb.cancel():
            raise DownloadCancelled("cancelled")
        raise
    except Exception as exc:  # noqa: BLE001
        if cb.cancel():
            raise DownloadCancelled("cancelled")
        raise UserError(f"Ошибка скачивания: {short_error(exc)}") from exc


def _ffmpeg(ffmpeg: str, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args]
    return subprocess.run(
        command,
        capture_output=True,
        timeout=900,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )


def _audio_bitrate_kbps(path: Path) -> int | None:
    """Best-effort bitrate sniffing for deciding whether transcoding is needed."""
    try:
        if path.suffix.lower() == ".mp3":
            from mutagen.mp3 import MP3

            return int(MP3(path).info.bitrate / 1000)
        if path.suffix.lower() in {".m4a", ".mp4", ".m4b"}:
            from mutagen.mp4 import MP4

            return int(MP4(path).info.bitrate / 1000)
    except Exception:  # noqa: BLE001 - a broken header must not block conversion
        return None
    return None


def _convert(
    base: Path,
    raw: Path,
    fmt: str,
    quality: int,
    ffmpeg: str,
) -> tuple[Path, list[str]]:
    warnings: list[str] = []
    raw_suffix = raw.suffix.lower()

    if fmt == "original":
        if raw_suffix in {".mp3", ".m4a", ".opus", ".ogg", ".oga", ".flac"}:
            target_suffix = {
                ".mp3": ".mp3",
                ".m4a": ".m4a",
                ".opus": ".opus",
                ".ogg": ".ogg",
                ".oga": ".ogg",
                ".flac": ".flac",
            }[raw_suffix]
            target = add_suffix(base, target_suffix)
            shutil.copy2(raw, target)
            return target, warnings
        if raw_suffix in {".mp4", ".aac", ".m4b"}:
            target = add_suffix(base, ".m4a")
            result = _ffmpeg(ffmpeg, ["-i", str(raw), "-vn", "-map", "0:a:0", "-c:a", "copy", "-movflags", "+faststart", str(target)])
            if result.returncode == 0 and target.is_file():
                return target, warnings
            target.unlink(missing_ok=True)
        if raw_suffix in {".webm", ".weba"}:
            target = add_suffix(base, ".opus")
            result = _ffmpeg(ffmpeg, ["-i", str(raw), "-vn", "-map", "0:a:0", "-c:a", "copy", str(target)])
            if result.returncode == 0 and target.is_file():
                return target, warnings
            target.unlink(missing_ok=True)
        if raw_suffix == ".wav":
            target = add_suffix(base, ".flac")
            result = _ffmpeg(ffmpeg, ["-i", str(raw), "-vn", "-map", "0:a:0", "-c:a", "flac", str(target)])
            if result.returncode == 0 and target.is_file():
                return target, warnings
            target.unlink(missing_ok=True)
        # Unknown container: safest portable option.
        fmt = "m4a"
        quality = 256
        warnings.append("Исходный формат не поддержан — сохранено в M4A 256.")

    if fmt == "mp3":
        target = add_suffix(base, ".mp3")
        if raw_suffix == ".mp3":
            bitrate = _audio_bitrate_kbps(raw)
            if not quality or (bitrate is not None and abs(bitrate - int(quality)) <= 16):
                shutil.copy2(raw, target)
                return target, warnings
            if bitrate:
                warnings.append(f"MP3 перекодирован: {bitrate} → {quality} кбит/с.")
        result = _ffmpeg(
            ffmpeg,
            [
                "-i",
                str(raw),
                "-vn",
                "-map",
                "0:a:0",
                "-map_metadata",
                "-1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                f"{quality or 320}k",
                "-ar",
                "44100",
                "-ac",
                "2",
                str(target),
            ],
        )
        if result.returncode != 0 or not target.is_file():
            target.unlink(missing_ok=True)
            error = (result.stderr or b"").decode("utf-8", "replace")[-500:]
            raise UserError(f"FFmpeg не смог конвертировать в MP3. {error}".strip())
        return target, warnings

    if fmt == "m4a":
        target = add_suffix(base, ".m4a")
        if raw_suffix in {".m4a", ".mp4", ".m4b"}:
            bitrate = _audio_bitrate_kbps(raw)
            if not quality or (bitrate is not None and abs(bitrate - int(quality)) <= 20):
                result = _ffmpeg(
                    ffmpeg,
                    ["-i", str(raw), "-vn", "-map", "0:a:0", "-c:a", "copy", "-movflags", "+faststart", str(target)],
                )
                if result.returncode == 0 and target.is_file():
                    return target, warnings
                target.unlink(missing_ok=True)
            elif bitrate:
                warnings.append(f"M4A перекодирован: {bitrate} → {quality} кбит/с.")
        result = _ffmpeg(
            ffmpeg,
            [
                "-i",
                str(raw),
                "-vn",
                "-map",
                "0:a:0",
                "-map_metadata",
                "-1",
                "-c:a",
                "aac",
                "-b:a",
                f"{quality or 256}k",
                "-movflags",
                "+faststart",
                str(target),
            ],
        )
        if result.returncode != 0 or not target.is_file():
            target.unlink(missing_ok=True)
            error = (result.stderr or b"").decode("utf-8", "replace")[-500:]
            raise UserError(f"FFmpeg не смог конвертировать в M4A. {error}".strip())
        return target, warnings

    raise UserError(f"Неизвестный формат: {fmt}")


def _tag_metadata(track: Any, playlist: Any, index: int, info: dict[str, Any] | None) -> dict[str, Any]:
    title = track.title
    artist = track.artist
    album = track.album
    year = track.year
    if info:
        raw_title = info.get("track")
        raw_artist = info.get("artist")
        if isinstance(raw_title, str) and raw_title.strip():
            title = raw_title.strip()
        if isinstance(raw_artist, str) and raw_artist.strip():
            artist = raw_artist.strip()
        if isinstance(info.get("album"), str) and info["album"].strip():
            album = info["album"].strip()
        if info.get("release_year"):
            year = str(info["release_year"])
    if artist and title.casefold().startswith(artist.casefold() + " - "):
        title = title[len(artist) + 3 :].strip()
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "album_artist": track.artists[0] if track.artists else artist,
        "year": year,
        "track_number": index + 1,
        "total_tracks": len(playlist.tracks),
        "disc_number": track.disc_number,
        "comment": f"Источник: {track.source_url or playlist.source_url}",
    }


def process_track(
    track: Any,
    base: Path,
    index: int,
    playlist: Any,
    settings: dict[str, Any],
    ffmpeg: str,
    workdir: Path,
    cb: EngineCallbacks,
    cover_cache: CoverCache,
) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    session = make_session(settings)
    info: dict[str, Any] | None = None
    source_url = track.source_url
    warnings: list[str] = []
    try:
        cb.state("preparing", 1, None, None)
        if track.stream_url:
            raw = _download_http(track.stream_url, workdir, cb, session)
            source_url = track.source_url or "VK"
        elif playlist.download_mode == "match" or (playlist.platform == "vk" and not track.stream_url):
            if playlist.platform == "vk":
                cb.log("VK не выдал прямую ссылку — ищу трек на YouTube/SoundCloud.")
            cb.state("searching", 2, None, None)
            source, chosen = _find_source(track, settings, cb)
            source_url = chosen["url"]
            cb.source(source_url, chosen.get("title") or "")
            raw, info = _download_ytdlp(source_url, settings, workdir, cb)
        elif not track.available and not track.source_url:
            raise UserError("Этот трек помечен как недоступный и не содержит ссылки на источник.")
        else:
            if not track.source_url:
                raise UserError("Нет ссылки на исходный трек.")
            cb.state("downloading", 4, None, None)
            raw, info = _download_ytdlp(track.source_url, settings, workdir, cb)
        if cb.cancel():
            raise DownloadCancelled("cancelled")

        cb.state("converting", 89, None, None)
        temp_base = workdir / "converted"
        result, convert_warnings = _convert(temp_base, raw, settings["format"], int(settings.get("quality") or 0), ffmpeg)
        warnings.extend(convert_warnings)

        cb.state("tagging", 96, None, None)
        cover = None
        cover_mime = None
        cover_url = track.cover_url
        if not cover_url and info:
            cover_url = info.get("thumbnail")
        if cover_url:
            fetched = cover_cache.get(cover_url, workdir, ffmpeg, session)
            if fetched:
                cover, cover_mime = fetched
        meta = _tag_metadata(track, playlist, index, info if playlist.download_mode == "direct" else None)
        warnings.extend(tagging.tag_file(result, meta, cover, cover_mime))

        if cb.cancel():
            raise DownloadCancelled("cancelled")
        final = add_suffix(base, result.suffix)
        if final.exists():
            final = unique_path(final, set())
        shutil.move(str(result), str(final))
        if not final.is_file() or final.stat().st_size < 1024:
            raise UserError("Итоговый файл пустой.")
        return {
            "path": final,
            "filename": final.name,
            "source_url": source_url,
            "warnings": warnings,
        }
    finally:
        session.close()
        shutil.rmtree(workdir, ignore_errors=True)
