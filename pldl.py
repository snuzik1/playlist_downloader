from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from pldl import APP_NAME, __version__
from pldl.config import FORMATS, load_settings, normalize_settings, save_settings
from pldl.jobs import JobManager
from pldl.platforms import resolve
from pldl.utils import UserError, find_ffmpeg, format_duration, open_path, short_error

TERMINAL = {"completed", "completed_with_errors", "cancelled", "error"}


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass


def _parse_selection(value: str | None, total: int) -> list[int]:
    if not value or value.strip().lower() in {"all", "*", "все", "всё"}:
        return list(range(total))
    result: set[int] = set()
    cleaned = value.replace("–", "-").replace("—", "-")
    for chunk in cleaned.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            if "-" in chunk:
                left, right = chunk.split("-", 1)
                start, end = int(left), int(right)
                if start > end:
                    start, end = end, start
                numbers = range(start, end + 1)
            else:
                numbers = (int(chunk),)
        except ValueError as exc:
            raise UserError(f"Не понял выбор треков: «{chunk}».") from exc
        for number in numbers:
            if not 1 <= number <= total:
                raise UserError(f"Трека №{number} нет: в плейлисте {total} трек(ов).")
            result.add(number - 1)
    if not result:
        raise UserError("Не выбрано ни одного трека.")
    return sorted(result)


def _speed(value: float | None) -> str:
    if not value:
        return ""
    value = float(value)
    for unit in ("Б/с", "КБ/с", "МБ/с", "ГБ/с"):
        if value < 1024 or unit == "ГБ/с":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ/с"


def _eta(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _print_playlist(playlist: Any) -> None:
    print(f"  Платформа: {playlist.platform}  •  {playlist.kind}")
    if playlist.author:
        print(f"  Автор:     {playlist.author}")
    print(f"  Треков:    {len(playlist.tracks)}  •  {format_duration(playlist.total_duration)}")
    if playlist.download_mode == "match":
        print("  Аудио:     поиск на YouTube/SoundCloud по метаданным сервиса")
    else:
        print("  Аудио:     прямая загрузка")
    for warning in playlist.warnings:
        print(f"  ! {warning}")


def _run_job(manager: JobManager, playlist: Any, indices: list[int], settings: dict[str, Any]) -> str:
    job = manager.create(playlist, indices, settings)
    tty = sys.stdout.isatty()
    width = max(60, min(140, shutil.get_terminal_size((110, 20)).columns))
    last_line = ""
    last_plain = 0.0
    interrupted = False
    try:
        while True:
            snapshot = job.snapshot()
            done = snapshot["done"]
            skipped = snapshot["skipped"]
            failed = snapshot["failed"]
            cancelled = snapshot["cancelled"]
            processed = done + skipped + failed + cancelled
            total = snapshot["total"]
            speed = _speed(snapshot.get("speed"))
            eta = _eta(snapshot.get("eta"))
            parts = [
                f"{snapshot['progress']:5.1f}%",
                f"{processed}/{total}",
                f"ок {done}",
            ]
            if skipped:
                parts.append(f"пропущено {skipped}")
            if failed:
                parts.append(f"ошибок {failed}")
            if cancelled:
                parts.append(f"отменено {cancelled}")
            if speed:
                parts.append(speed)
            if eta:
                parts.append(f"осталось {eta}")
            line = "  " + "  ".join(parts)
            if tty:
                sys.stdout.write("\r" + line[: width - 1].ljust(width - 1))
                sys.stdout.flush()
            elif time.time() - last_plain >= 5 or snapshot["status"] in TERMINAL:
                print(line)
                last_plain = time.time()
            if snapshot["status"] in TERMINAL:
                if tty:
                    sys.stdout.write("\r" + " " * (width - 1) + "\r")
                    sys.stdout.flush()
                break
            time.sleep(0.6)
    except KeyboardInterrupt:
        interrupted = True
        print("\n  Останавливаю загрузку...")
        manager.cancel(job.id)
        deadline = time.time() + 12
        while time.time() < deadline:
            if job.snapshot()["status"] in TERMINAL:
                break
            time.sleep(0.3)

    snapshot = job.snapshot()
    print(
        f"  Итог: {snapshot['done']} готово, {snapshot['skipped']} пропущено, "
        f"{snapshot['failed']} с ошибками."
    )
    if snapshot.get("playlist_dir"):
        print(f"  Папка: {snapshot['playlist_dir']}")

    errors = [track for track in snapshot["tracks"] if track["status"] == "error"]
    for track in errors[:12]:
        hint = f" — {track['hint']}" if track.get("hint") else ""
        print(f"  ОШИБКА: {track['artist']} — {track['title']}: {track['error']}{hint}")
    if len(errors) > 12:
        print(f"  …и ещё {len(errors) - 12} ошибок.")

    if interrupted:
        return "cancelled"
    return snapshot["status"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pldl.py",
        description="PLDL — скачивает плейлисты, альбомы и треки в локальную папку.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            '  pldl.py "https://music.yandex.ru/users/.../playlists/123"\n'
            '  pldl.py URL --select 1-10,15 --format m4a --quality 256\n'
            '  pldl.py URL --list\n'
            '  pldl.py URL --no-folder --no-index\n'
        ),
    )
    parser.add_argument("urls", nargs="+", help="ссылки на плейлисты, альбомы, треки или профили")
    parser.add_argument("-o", "--output", help="папка загрузки")
    parser.add_argument("-f", "--format", choices=sorted(FORMATS), help="формат аудио")
    parser.add_argument("-q", "--quality", type=int, help="битрейт (например 320, 192, 128, 256)")
    parser.add_argument("-s", "--select", help="выбрать треки: 1-5,8,12 (нумерация с 1)")
    parser.add_argument("-c", "--concurrency", type=int, help="сколько треков качать одновременно (1-6)")
    parser.add_argument("--match-source", choices=("auto", "youtube", "soundcloud"), help="где искать аудио")
    parser.add_argument("--proxy", help="HTTP/SOCKS-прокси, например http://127.0.0.1:1080")
    parser.add_argument("--cookies", help="браузер для cookies: chrome, edge, firefox…")
    parser.add_argument("--ffmpeg", help="путь к ffmpeg.exe или папке с ним")
    parser.add_argument("--no-folder", action="store_true", help="не создавать отдельную папку плейлиста")
    parser.add_argument("--no-index", action="store_true", help="не добавлять номера к именам файлов")
    parser.add_argument("--list", action="store_true", help="только показать треки, без загрузки")
    parser.add_argument("--dry-run", action="store_true", help="разобрать ссылку, но не скачивать")
    parser.add_argument("--open", action="store_true", help="открыть папку после загрузки")
    parser.add_argument("--save-settings", action="store_true", help="сохранить переданные параметры в settings.json")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = load_settings()
    overrides: dict[str, Any] = {}
    if args.output:
        overrides["output_dir"] = args.output
    if args.format:
        overrides["format"] = args.format
    if args.quality is not None:
        overrides["quality"] = args.quality
    if args.concurrency is not None:
        overrides["concurrency"] = args.concurrency
    if args.match_source:
        overrides["match_source"] = args.match_source
    if args.proxy is not None:
        overrides["proxy"] = args.proxy
    if args.cookies is not None:
        overrides["cookies_browser"] = args.cookies
    if args.ffmpeg:
        overrides["ffmpeg_path"] = args.ffmpeg
    if args.no_folder:
        overrides["playlist_folder"] = False
    if args.no_index:
        overrides["index_prefix"] = False

    settings = normalize_settings(overrides, settings)
    fmt = settings["format"]
    if args.quality is not None and args.format and args.quality not in FORMATS[fmt]:
        parser.error(f"для {fmt.upper()} доступны битрейты: {', '.join(map(str, FORMATS[fmt])) or 'без ограничений'}")

    if args.save_settings:
        settings = save_settings(settings)
        print(f"Настройки сохранены: {Path(settings['output_dir'])}")

    ffmpeg = find_ffmpeg(settings.get("ffmpeg_path", ""))
    if settings["format"] != "original" and not ffmpeg:
        print("ВНИМАНИЕ: FFmpeg не найден. Установите его или укажите --ffmpeg.", file=sys.stderr)
    if settings["playlist_folder"]:
        print(f"Папка загрузки: {settings['output_dir']}")

    manager = JobManager()
    exit_code = 0
    for position, url in enumerate(args.urls, start=1):
        if len(args.urls) > 1:
            print(f"\n[{position}/{len(args.urls)}] {url}")
        try:
            playlist = resolve(url, settings)
        except UserError as exc:
            print(f"ОШИБКА: {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"Совет: {exc.hint}", file=sys.stderr)
            exit_code = 1
            continue
        except KeyboardInterrupt:
            print("\nПрервано.", file=sys.stderr)
            return 130

        print(f"\n  {playlist.title}")
        _print_playlist(playlist)

        try:
            indices = _parse_selection(args.select, len(playlist.tracks))
        except UserError as exc:
            print(f"ОШИБКА: {exc.message}", file=sys.stderr)
            exit_code = 2
            continue

        if args.list:
            for number, index in enumerate(indices, start=1):
                track = playlist.tracks[index]
                duration = format_duration(track.duration)
                print(f"  {number:>4}. {track.artist or '—'} — {track.title}  [{duration}]")
            continue

        if args.dry_run:
            print(f"  Выбрано: {len(indices)} из {len(playlist.tracks)}. Загрузка не запускалась.")
            continue

        print()
        status = _run_job(manager, playlist, indices, settings)
        if status != "completed":
            exit_code = 1
        if args.open:
            folder = Path(settings["output_dir"])
            try:
                # The job chooses a playlist folder; open the most recent one if possible.
                jobs = [job for job in manager.list() if job.playlist.source_url == playlist.source_url]
                if jobs:
                    folder = jobs[0].playlist_dir
                open_path(str(folder))
            except OSError as exc:
                print(f"Не удалось открыть папку: {short_error(exc)}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
