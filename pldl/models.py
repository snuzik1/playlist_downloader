from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .utils import format_duration, stable_hash


PLATFORM_LABELS = {
    "yandex": "Яндекс Музыка",
    "soundcloud": "SoundCloud",
    "spotify": "Spotify",
    "vk": "VK Музыка",
    "deezer": "Deezer",
    "youtube": "YouTube",
    "bandcamp": "Bandcamp",
    "apple": "Apple Music",
    "generic": "Другой сайт",
}

MODE_LABELS = {
    "direct": "Прямая загрузка",
    "match": "Поиск аудио",
}


@dataclass(slots=True)
class Track:
    id: str
    title: str
    artists: list[str] = field(default_factory=list)
    album: str = ""
    duration: int | None = None
    cover_url: str | None = None
    source_url: str | None = None
    stream_url: str | None = None
    year: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    available: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def artist(self) -> str:
        return ", ".join(a.strip() for a in self.artists if a and a.strip())

    @property
    def key(self) -> str:
        if self.id:
            return str(self.id)
        return stable_hash(f"{self.artist}|{self.title}|{self.duration}", 20)

    @property
    def query(self) -> str:
        title = self.title.strip()
        artist = self.artist
        if artist and artist.casefold() not in title.casefold():
            return f"{artist} - {title}"
        return f"{title} {artist}".strip()

    def public(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "title": self.title,
            "artists": self.artists,
            "artist": self.artist,
            "album": self.album,
            "duration": self.duration,
            "duration_label": format_duration(self.duration),
            "cover": self.cover_url,
            "url": self.source_url,
            "available": self.available,
        }


@dataclass(slots=True)
class Playlist:
    platform: str
    kind: str
    title: str
    tracks: list[Track]
    source_url: str
    author: str = ""
    description: str = ""
    cover_url: str | None = None
    download_mode: str = "match"
    warnings: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    @property
    def total_duration(self) -> int:
        return sum(t.duration or 0 for t in self.tracks)

    def ensure_id(self) -> str:
        if not self.id:
            self.id = stable_hash(f"{self.platform}|{self.source_url}|{len(self.tracks)}", 18)
        return self.id

    def public(self, resolved_in: float | None = None) -> dict[str, Any]:
        self.ensure_id()
        platform_label = PLATFORM_LABELS.get(self.platform, self.platform)
        mode_label = MODE_LABELS.get(self.download_mode, self.download_mode)
        data: dict[str, Any] = {
            "id": self.id,
            "url": self.source_url,
            "platform": self.platform,
            "platform_label": platform_label,
            "kind": self.kind,
            "title": self.title or "Без названия",
            "author": self.author,
            "description": self.description,
            "cover": self.cover_url,
            "track_count": len(self.tracks),
            "total_duration": self.total_duration,
            "total_duration_label": format_duration(self.total_duration),
            "download_mode": self.download_mode,
            "mode_label": mode_label,
            "warnings": self.warnings,
            "tracks": [track.public(i) for i, track in enumerate(self.tracks)],
        }
        if resolved_in is not None:
            data["resolved_in"] = round(resolved_in, 2)
        return data
