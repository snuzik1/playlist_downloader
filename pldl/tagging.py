from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from .utils import short_error


def _clean(value: Any) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    return re.sub(r"\s+", " ", text)


def _picture_mime(data: bytes, fallback: str | None = None) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return fallback or "image/jpeg"


def tag_file(
    path: Path,
    meta: dict[str, Any],
    cover: bytes | None = None,
    cover_mime: str | None = None,
) -> list[str]:
    """Write tags. Returns a list of non-fatal warnings."""
    warnings: list[str] = []
    suffix = path.suffix.lower()
    title = _clean(meta.get("title"))
    artist = _clean(meta.get("artist"))
    album = _clean(meta.get("album"))
    album_artist = _clean(meta.get("album_artist")) or artist
    year = _clean(meta.get("year"))[:4]
    number = meta.get("track_number")
    total = meta.get("total_tracks")
    disc = meta.get("disc_number")
    comment = _clean(meta.get("comment"))
    mime = _picture_mime(cover, cover_mime) if cover else None

    try:
        if suffix == ".mp3":
            from mutagen.id3 import APIC, COMM, ID3, TALB, TIT2, TPE1, TPE2, TPOS, TRCK, TSSE, TYER

            try:
                tags = ID3(path)
            except Exception:  # noqa: BLE001
                tags = ID3()
            for key in ("TIT2", "TPE1", "TALB", "TPE2", "TRCK", "TPOS", "TYER", "TDRC", "TSSE", "COMM", "APIC"):
                try:
                    tags.delall(key)
                except Exception:  # noqa: BLE001
                    pass
            tags.add(TIT2(encoding=3, text=title))
            if artist:
                tags.add(TPE1(encoding=3, text=artist))
                tags.add(TPE2(encoding=3, text=album_artist))
            if album:
                tags.add(TALB(encoding=3, text=album))
            if year.isdigit():
                tags.add(TYER(encoding=3, text=year))
            if isinstance(number, int) and number > 0:
                tags.add(TRCK(encoding=3, text=f"{number}/{total}" if total else str(number)))
            if isinstance(disc, int) and disc > 0:
                tags.add(TPOS(encoding=3, text=str(disc)))
            if comment:
                tags.add(COMM(encoding=3, lang="rus", desc="", text=comment))
            tags.add(TSSE(encoding=3, text="PLDL"))
            if cover:
                tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
            tags.save(path, v2_version=3)

        elif suffix in {".m4a", ".mp4", ".m4b"}:
            from mutagen.mp4 import MP4, MP4Cover

            audio = MP4(path)
            for key in ("\xa9nam", "\xa9ART", "aART", "\xa9alb", "\xa9day", "\xa9cmt", "trkn", "disk", "covr"):
                if key in audio:
                    del audio[key]
            audio["\xa9nam"] = [title]
            if artist:
                audio["\xa9ART"] = [artist]
                audio["aART"] = [album_artist]
            if album:
                audio["\xa9alb"] = [album]
            if year.isdigit():
                audio["\xa9day"] = [year]
            if comment:
                audio["\xa9cmt"] = [comment]
            if isinstance(number, int) and number > 0:
                audio["trkn"] = [(number, int(total) if total else 0)]
            if isinstance(disc, int) and disc > 0:
                audio["disk"] = [(disc, 0)]
            if cover:
                fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
                audio["covr"] = [MP4Cover(cover, imageformat=fmt)]
            audio.save()

        elif suffix == ".flac":
            from mutagen.flac import FLAC, Picture

            audio = FLAC(path)
            for key in ("title", "artist", "album", "albumartist", "date", "tracknumber", "tracktotal", "discnumber", "comment"):
                if key in audio:
                    del audio[key]
            audio.clear_pictures()
            audio["title"] = title
            if artist:
                audio["artist"] = artist
                audio["albumartist"] = album_artist
            if album:
                audio["album"] = album
            if year.isdigit():
                audio["date"] = year
            if isinstance(number, int) and number > 0:
                audio["tracknumber"] = str(number)
                if total:
                    audio["tracktotal"] = str(total)
            if isinstance(disc, int) and disc > 0:
                audio["discnumber"] = str(disc)
            if comment:
                audio["comment"] = comment
            if cover:
                picture = Picture()
                picture.type = 3
                picture.mime = mime
                picture.desc = "Cover"
                picture.data = cover
                audio.add_picture(picture)
            audio.save()

        elif suffix in {".opus", ".ogg"}:
            from mutagen.flac import Picture
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis

            audio = OggOpus(path) if suffix == ".opus" else OggVorbis(path)
            for key in ("title", "artist", "album", "albumartist", "date", "tracknumber", "tracktotal", "discnumber", "comment", "metadata_block_picture"):
                if key in audio:
                    del audio[key]
            audio["title"] = title
            if artist:
                audio["artist"] = artist
                audio["albumartist"] = album_artist
            if album:
                audio["album"] = album
            if year.isdigit():
                audio["date"] = year
            if isinstance(number, int) and number > 0:
                audio["tracknumber"] = str(number)
                if total:
                    audio["tracktotal"] = str(total)
            if isinstance(disc, int) and disc > 0:
                audio["discnumber"] = str(disc)
            if comment:
                audio["comment"] = comment
            if cover:
                picture = Picture()
                picture.type = 3
                picture.mime = mime
                picture.desc = "Cover"
                picture.data = cover
                audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
            audio.save()
        else:
            warnings.append(f"Теги для формата {suffix or 'без расширения'} не поддерживаются.")
    except Exception as exc:  # noqa: BLE001 - tagging must not lose a downloaded file
        warnings.append(f"Не удалось записать теги: {short_error(exc)}")
    return warnings
