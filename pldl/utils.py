from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_BARE_URL_RE = re.compile(
    r"(?:www\.)?(?:soundcloud\.com|on\.soundcloud\.com|music\.yandex\.(?:ru|com|kz|by|ua)|"
    r"open\.spotify\.com|spotify\.link|spoti\.fi|vk\.com|vk\.ru|vk\.cc|"
    r"youtube\.com|youtu\.be|deezer\.com|deezer\.page\.link|bandcamp\.com|"
    r"music\.apple\.com)/[^\s<>\"']*",
    re.IGNORECASE,
)


class UserError(Exception):
    """An error that is safe and useful to show to the user."""

    def __init__(self, message: str, hint: str | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def timestamp() -> int:
    return int(time.time())


def stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8", "replace")).hexdigest()[:length]


def extract_url(text: str) -> str:
    """Pull a URL out of pasted text, including share-text and spotify: URIs."""
    text = (text or "").strip()
    if not text:
        raise UserError("Вставьте ссылку на плейлист, альбом или профиль.")

    if text.lower().startswith("spotify:"):
        parts = text.split(":")
        if len(parts) >= 3 and parts[1] in {"playlist", "album", "track", "artist"}:
            return f"https://open.spotify.com/{parts[1]}/{parts[2]}"

    match = _URL_RE.search(text)
    if match:
        url = match.group(0)
    else:
        bare = _BARE_URL_RE.search(text)
        if not bare:
            raise UserError(
                "Не вижу ссылку. Поддерживаются SoundCloud, Яндекс Музыка, "
                "Spotify, VK, Deezer, YouTube и другие сайты.",
                hint="Скопируйте ссылку из адресной строки или меню «Поделиться».",
            )
        url = "https://" + bare.group(0)

    url = url.strip().strip("«»\"'")
    url = url.rstrip(".,;!?")
    if url.endswith(")") and "(" not in url:
        url = url.rstrip(")")
    return url


def resolve_short_url(url: str, session: requests.Session, timeout: int = 20) -> str:
    """Follow redirects for short links (spotify.link, on.soundcloud.com, vk.cc…)."""
    host = (urlparse(url).hostname or "").lower()
    short_hosts = {
        "spotify.link",
        "spoti.fi",
        "on.soundcloud.com",
        "vk.cc",
        "deezer.page.link",
        "music.apple.com",
    }
    if not any(host == h or host.endswith("." + h) for h in short_hosts):
        return url
    if "music.apple.com" in host:
        return url
    try:
        response = session.get(url, allow_redirects=True, timeout=timeout, stream=True)
        response.close()
        return response.url or url
    except requests.RequestException:
        return url


def sanitize_filename(name: Any, max_len: int = 140, fallback: str = "Без названия") -> str:
    value = unicodedata.normalize("NFC", str(name or ""))
    value = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        value = fallback
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?", value):
        value = "_" + value
    if len(value) > max_len:
        value = value[:max_len].rstrip(" .")
    return value or fallback


def add_suffix(path: Path, suffix: str) -> Path:
    """Append an extension instead of replacing the part after the last dot.

    Playlist file names often contain dots (for example ``01. Artist - Title``),
    so ``Path.with_suffix`` would silently truncate them.
    """
    return path.with_name(f"{path.name}{suffix}")


def unique_path(path: Path, taken: set[str] | None = None) -> Path:
    taken = taken if taken is not None else set()
    key = str(path).lower()
    if not path.exists() and key not in taken:
        taken.add(key)
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 1000):
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        key = str(candidate).lower()
        if not candidate.exists() and key not in taken:
            taken.add(key)
            return candidate
    raise UserError(f"Не удалось подобрать имя файла для «{path.name}».")


def format_duration(seconds: float | int | None) -> str:
    if not seconds or seconds < 0:
        return "--:--"
    seconds = int(round(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def human_bytes(value: float | int | None) -> str:
    if not value:
        return "0 Б"
    value = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def clean_title_for_match(value: str) -> str:
    value = unicodedata.normalize("NFC", value or "")
    value = re.sub(
        r"[\(\[\{][^\)\]\}]*(?:official|video|audio|lyrics?|visualizer|"
        r"remaster(?:ed)?|explicit|hd|hq|4k|m/v|mv|premiere)[^\)\]\}]*[\)\]\}]",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s+", " ", value).strip(" -–—")
    return value


def expand_path(value: str, base: Path) -> str:
    value = (value or "").strip().strip('"').strip("'")
    if not value:
        return ""
    value = os.path.expandvars(os.path.expanduser(value))
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return str(path.resolve())


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def make_session(settings: dict[str, Any] | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
    )
    proxy = (settings or {}).get("proxy", "").strip()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "POST"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=12, pool_maxsize=12)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def short_error(exc: BaseException, limit: int = 400) -> str:
    text = str(exc) or exc.__class__.__name__
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("ERROR: "):
        text = text[7:]
    return text[:limit]


def find_ffmpeg(configured: str = "") -> str | None:
    candidates: list[str] = []
    if configured:
        configured_path = Path(configured)
        if configured_path.is_dir():
            names = ("ffmpeg.exe", "ffmpeg") if sys.platform.startswith("win") else ("ffmpeg",)
            for name in names:
                candidates.extend([str(configured_path / name), str(configured_path / "bin" / name)])
        else:
            candidates.append(str(configured_path))
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(found)
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates.extend(
                glob.glob(
                    os.path.join(
                        local,
                        "Microsoft",
                        "WinGet",
                        "Packages",
                        "Gyan.FFmpeg*",
                        "**",
                        "bin",
                        "ffmpeg.exe",
                    ),
                    recursive=True,
                )
            )
        candidates.extend(
            [
                r"C:\ffmpeg\bin\ffmpeg.exe",
                r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            ]
        )
    else:
        candidates.extend(["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            if not Path(candidate).is_file():
                continue
            kwargs: dict[str, Any] = {}
            if sys.platform.startswith("win"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            result = subprocess.run(
                [candidate, "-version"],
                capture_output=True,
                timeout=8,
                **kwargs,
            )
            if result.returncode == 0:
                return str(Path(candidate))
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def open_path(path: str) -> None:
    target = str(Path(path))
    if sys.platform.startswith("win"):
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def is_probably_url(value: str) -> bool:
    return bool(_URL_RE.search(value or "") or _BARE_URL_RE.search(value or ""))
