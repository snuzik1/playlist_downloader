from __future__ import annotations

import copy
import logging
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ensure_output_dir
from .engine import CoverCache, EngineCallbacks, process_track
from .models import Playlist, Track
from .utils import (
    UserError,
    add_suffix,
    atomic_write_json,
    format_duration,
    read_json,
    sanitize_filename,
    short_error,
    stable_hash,
)
from yt_dlp.utils import DownloadCancelled

logger = logging.getLogger("pldl.jobs")

MANIFEST_NAME = ".pldl_manifest.json"
AUDIO_SUFFIXES = {".mp3", ".m4a", ".opus", ".ogg", ".flac"}
ACTIVE_STATUSES = {"preparing", "searching", "downloading", "converting", "tagging"}


@dataclass
class TrackState:
    index: int
    track: Track
    status: str = "pending"
    progress: float = 0.0
    speed: float | None = None
    eta: int | None = None
    filename: str | None = None
    error: str | None = None
    hint: str | None = None
    source: str | None = None
    base: Path | None = None
    started_at: float | None = None
    finished_at: float | None = None


class Job:
    def __init__(
        self,
        playlist: Playlist,
        indices: list[int],
        options: dict[str, Any],
        output_dir: Path,
    ):
        self.id = uuid.uuid4().hex[:12]
        self.playlist = playlist
        self.indices = indices
        self.options = copy.deepcopy(options)
        self.output_dir = output_dir
        self.playlist_dir = output_dir
        self.states: dict[int, TrackState] = {
            index: TrackState(index=index, track=playlist.tracks[index]) for index in indices
        }
        self.status = "queued"
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.cancel_event = threading.Event()
        self.lock = threading.RLock()
        self.logs: list[str] = []
        self.cover_cache = CoverCache()
        self.ffmpeg: str | None = None

    def log(self, message: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')}  {message}"
        with self.lock:
            if self.logs and self.logs[-1].endswith(message):
                return
            self.logs.append(line)
            if len(self.logs) > 300:
                self.logs = self.logs[-300:]
        try:
            print(f"[job {self.id}] {message}", flush=True)
        except UnicodeEncodeError:
            print(f"[job {self.id}] {message.encode('ascii', 'replace').decode()}", flush=True)

    def set_state(
        self,
        index: int,
        *,
        status: str | None = None,
        progress: float | None = None,
        speed: float | None = None,
        eta: int | None = None,
        filename: str | None = None,
        error: str | None = None,
        hint: str | None = None,
        source: str | None = None,
    ) -> None:
        with self.lock:
            state = self.states.get(index)
            if not state:
                return
            if status is not None:
                state.status = status
                if status in {"done", "skipped", "error", "cancelled"}:
                    state.progress = 100.0
                    state.speed = None
                    state.eta = None
                    state.finished_at = time.time()
                elif status in {"preparing", "searching"}:
                    state.started_at = state.started_at or time.time()
            if progress is not None:
                state.progress = max(0.0, min(100.0, float(progress)))
            if state.status in {"done", "skipped", "error", "cancelled"}:
                state.speed = None
                state.eta = None
            else:
                state.speed = speed
                state.eta = eta
            if filename:
                state.filename = filename
            if error is not None:
                state.error = error
            if hint is not None:
                state.hint = hint
            if source is not None:
                state.source = source

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def snapshot(self, include_tracks: bool = True) -> dict[str, Any]:
        with self.lock:
            states = [self.states[i] for i in sorted(self.states)]
            total = len(states)
            done = sum(1 for s in states if s.status == "done")
            skipped = sum(1 for s in states if s.status == "skipped")
            failed = sum(1 for s in states if s.status == "error")
            cancelled = sum(1 for s in states if s.status == "cancelled")
            pending = sum(1 for s in states if s.status in {"pending", "queued"})
            active = [s for s in states if s.status in ACTIVE_STATUSES]
            processed = done + skipped + failed + cancelled
            partial = sum(s.progress for s in active) / 100.0
            progress = ((processed + partial) / total * 100.0) if total else 0.0
            if self.status in {"completed", "completed_with_errors"}:
                progress = 100.0
            speed = sum(s.speed or 0 for s in active) or None
            eta = active[0].eta if len(active) == 1 else None
            current = active[0].track.title if active else None
            elapsed = (self.finished_at or time.time()) - (self.started_at or self.created_at)

            track_data = []
            if include_tracks:
                for state in states:
                    track_data.append(
                        {
                            "index": state.index,
                            "number": state.index + 1,
                            "title": state.track.title,
                            "artist": state.track.artist,
                            "album": state.track.album,
                            "duration": state.track.duration,
                            "duration_label": format_duration(state.track.duration),
                            "cover": state.track.cover_url,
                            "status": state.status,
                            "progress": round(state.progress, 1),
                            "speed": state.speed,
                            "eta": state.eta,
                            "filename": state.filename,
                            "error": state.error,
                            "hint": state.hint,
                            "source": state.source,
                        }
                    )

            payload = {
                "id": self.id,
                "status": self.status,
                "terminal": self.status in {"completed", "completed_with_errors", "cancelled", "error"},
                "title": self.playlist.title,
                "platform": self.playlist.platform,
                "source_url": self.playlist.source_url,
                "mode": self.playlist.download_mode,
                "output_dir": str(self.output_dir),
                "playlist_dir": str(self.playlist_dir),
                "format": self.options.get("format"),
                "quality": self.options.get("quality"),
                "total": total,
                "playlist_total": len(self.playlist.tracks),
                "done": done,
                "skipped": skipped,
                "failed": failed,
                "cancelled": cancelled,
                "pending": pending,
                "active": len(active),
                "progress": round(progress, 1),
                "speed": speed,
                "eta": eta,
                "current": current,
                "elapsed": int(elapsed),
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "log_lines": len(self.logs),
                "warnings": list(self.playlist.warnings),
            }
            if include_tracks:
                payload["tracks"] = track_data
                payload["logs"] = list(self.logs[-200:])
            return payload


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()

    def create(self, playlist: Playlist, indices: list[int], options: dict[str, Any]) -> Job:
        indices = sorted({int(i) for i in indices if 0 <= int(i) < len(playlist.tracks)})
        if not indices:
            raise UserError("Не выбрано ни одного трека.")
        try:
            output_dir = ensure_output_dir(options)
        except OSError as exc:
            raise UserError(
                f"Не удалось создать папку загрузок: {short_error(exc)}",
                hint="Проверьте путь к папке в настройках.",
            ) from exc
        job = Job(playlist, indices, options, output_dir)
        with self.lock:
            self.jobs[job.id] = job
            self._prune()
        thread = threading.Thread(target=self._run, args=(job,), name=f"pldl-{job.id}", daemon=True)
        thread.start()
        return job

    def list(self) -> list[Job]:
        with self.lock:
            jobs = list(self.jobs.values())
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return jobs

    def _prune(self) -> None:
        finished = [
            job
            for job in self.jobs.values()
            if job.status in {"completed", "completed_with_errors", "cancelled", "error"}
        ]
        if len(finished) <= 20:
            return
        finished.sort(key=lambda job: job.created_at)
        for job in finished[: len(finished) - 20]:
            self.jobs.pop(job.id, None)

    def get(self, job_id: str) -> Job:
        with self.lock:
            job = self.jobs.get(job_id)
        if not job:
            raise UserError("Задание не найдено. Возможно, приложение перезапускалось.", code="job_not_found")
        return job

    def clear_finished(self) -> list[str]:
        with self.lock:
            finished = [
                job_id
                for job_id, job in self.jobs.items()
                if job.status in {"completed", "completed_with_errors", "cancelled", "error"}
            ]
            for job_id in finished:
                self.jobs.pop(job_id, None)
        return finished

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status in {"completed", "completed_with_errors", "cancelled", "error"}:
            return job
        job.cancel_event.set()
        job.status = "cancelling"
        job.log("Останавливаю загрузку...")
        return job

    def retry(self, job_id: str, options: dict[str, Any] | None = None) -> Job:
        job = self.get(job_id)
        if job.status in {"queued", "running", "cancelling"}:
            raise UserError("Задание ещё выполняется.")
        indices = [s.index for s in job.states.values() if s.status in {"error", "cancelled"}]
        if not indices:
            raise UserError("В этом задании нет треков с ошибками.")
        job.log(f"Повторная попытка для {len(indices)} треков.")
        return self.create(job.playlist, indices, options or job.options)

    def _choose_playlist_dir(self, job: Job) -> Path:
        playlist = job.playlist
        title = sanitize_filename(playlist.title, 90, "Плейлист")
        for attempt in range(1, 200):
            candidate = job.output_dir / title if attempt == 1 else job.output_dir / f"{title} ({attempt})"
            if not candidate.exists():
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
            if not candidate.is_dir():
                continue
            try:
                non_empty = any(candidate.iterdir())
            except OSError:
                non_empty = True
            manifest = read_json(candidate / MANIFEST_NAME, {}) or {}
            if isinstance(manifest, dict) and manifest.get("source") == playlist.source_url:
                return candidate
            if not non_empty:
                return candidate
        fallback = job.output_dir / f"{title} ({stable_hash(playlist.source_url, 6)})"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    def _manifest_path(self, job: Job) -> Path:
        if job.playlist_dir.resolve() == job.output_dir.resolve():
            return job.output_dir / f".pldl_{job.playlist.id}.json"
        return job.playlist_dir / MANIFEST_NAME

    def _load_manifest(self, job: Job) -> dict[str, Any]:
        path = self._manifest_path(job)
        data = read_json(path, {})
        if not isinstance(data, dict) or data.get("version") != 1:
            data = {}
        data.setdefault("version", 1)
        data.setdefault("tracks", {})
        if not isinstance(data.get("tracks"), dict):
            data["tracks"] = {}
        data["source"] = job.playlist.source_url
        data["platform"] = job.playlist.platform
        data["title"] = job.playlist.title
        data["updated"] = time.time()
        return data

    def _save_manifest(self, job: Job, manifest: dict[str, Any]) -> None:
        manifest["updated"] = time.time()
        atomic_write_json(self._manifest_path(job), manifest)

    def _base_name_for(self, job: Job, track: Track, index: int) -> str:
        title = track.title
        artist = track.artist
        if artist and title.casefold().startswith(artist.casefold() + " - "):
            title = title[len(artist) + 3 :].strip()
        name = f"{artist} - {title}" if artist else title
        if not name.strip():
            name = f"Трек {index + 1}"
        if job.options.get("index_prefix") and len(job.playlist.tracks) > 1:
            name = f"{index + 1:02d}. {name}"
        return sanitize_filename(name, 135)

    def _all_base_names(self, job: Job) -> dict[int, str]:
        """Names must not depend on which subset of tracks is selected."""
        names: dict[int, str] = {}
        taken: set[str] = set()
        for index, track in enumerate(job.playlist.tracks):
            name = self._base_name_for(job, track, index)
            stem = name
            counter = 2
            while stem.casefold() in taken:
                stem = f"{name} ({counter})"
                counter += 1
            taken.add(stem.casefold())
            names[index] = stem
        return names

    def _expected_paths(self, base: Path, fmt: str) -> list[Path]:
        if fmt == "original":
            return [add_suffix(base, suffix) for suffix in AUDIO_SUFFIXES]
        return [add_suffix(base, ".mp3" if fmt == "mp3" else ".m4a")]

    def _prepare_states(self, job: Job, manifest: dict[str, Any]) -> tuple[int, int]:
        names = self._all_base_names(job)
        skipped = 0
        for state in (job.states[i] for i in sorted(job.states)):
            track = state.track
            entry = manifest["tracks"].get(track.key)
            if isinstance(entry, dict) and entry.get("file"):
                existing = job.playlist_dir / entry["file"]
                if existing.is_file() and existing.stat().st_size > 1024:
                    state.status = "skipped"
                    state.progress = 100
                    state.filename = existing.name
                    state.finished_at = time.time()
                    skipped += 1
                    continue

            base = job.playlist_dir / names[state.index]
            state.base = base

            existing_paths = self._expected_paths(base, job.options["format"])
            existing = None
            for path in existing_paths:
                if path.is_file():
                    if path.stat().st_size > 1024:
                        existing = path
                    else:
                        try:
                            path.unlink()
                        except OSError:
                            pass
                    break
            if existing:
                state.status = "skipped"
                state.progress = 100
                state.filename = existing.name
                state.finished_at = time.time()
                manifest["tracks"][track.key] = {
                    "file": str(existing.relative_to(job.playlist_dir)),
                    "format": job.options["format"],
                    "quality": job.options.get("quality"),
                    "title": track.title,
                    "artist": track.artist,
                    "done_at": time.time(),
                    "skipped_existing": True,
                }
                skipped += 1
        return len(job.states) - skipped, skipped

    def _run(self, job: Job) -> None:
        job.status = "running"
        job.started_at = time.time()
        job.log(f"Старт: {job.playlist.title} ({len(job.states)} треков)")
        manifest_lock = threading.Lock()
        temp_root: Path | None = None
        try:
            job.playlist_dir = (
                self._choose_playlist_dir(job)
                if job.options.get("playlist_folder")
                and (len(job.playlist.tracks) > 1 or job.playlist.kind != "track")
                else job.output_dir
            )
            job.playlist_dir.mkdir(parents=True, exist_ok=True)
            manifest = self._load_manifest(job)
            with manifest_lock:
                self._save_manifest(job, manifest)
            job.log(f"Папка: {job.playlist_dir}")

            pending, skipped = self._prepare_states(job, manifest)
            with manifest_lock:
                self._save_manifest(job, manifest)
            if skipped:
                job.log(f"Пропущено уже скачанных: {skipped}")
            if job.cancelled:
                job.status = "cancelled"
                job.log("Загрузка отменена до начала.")
                return
            if pending == 0:
                job.status = "completed"
                job.log("Все треки уже на месте.")
                return

            from .utils import find_ffmpeg

            job.ffmpeg = find_ffmpeg(job.options.get("ffmpeg_path", ""))
            if not job.ffmpeg:
                message = "FFmpeg не найден. Установите FFmpeg или укажите путь в настройках."
                job.log(message)
                for state in job.states.values():
                    if state.status in {"pending", "queued"}:
                        job.set_state(state.index, status="error", error=message)
                job.status = "error"
                return

            temp_root = job.output_dir / ".pldl_tmp" / job.id
            temp_root.mkdir(parents=True, exist_ok=True)
            states = [
                state
                for state in (job.states[i] for i in sorted(job.states))
                if state.status not in {"skipped", "done"}
            ]
            concurrency = max(1, min(int(job.options.get("concurrency") or 2), len(states)))
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [executor.submit(self._download_one, job, state, manifest, manifest_lock, temp_root) for state in states]
                for future in futures:
                    try:
                        future.result()
                    except Exception:  # noqa: BLE001
                        logger.exception("worker crashed")
            shutil.rmtree(temp_root, ignore_errors=True)

            with manifest_lock:
                self._save_manifest(job, manifest)
            failed = sum(1 for s in job.states.values() if s.status == "error")
            cancelled = sum(1 for s in job.states.values() if s.status == "cancelled")
            if job.cancelled:
                job.status = "cancelled"
            elif failed:
                job.status = "completed_with_errors"
            elif cancelled:
                job.status = "cancelled"
            else:
                job.status = "completed"
            job.log(
                f"Готово: успешно {sum(1 for s in job.states.values() if s.status == 'done')}, "
                f"пропущено {sum(1 for s in job.states.values() if s.status == 'skipped')}, "
                f"ошибок {failed}, отменено {cancelled}."
            )
        except UserError as exc:
            job.status = "error"
            job.log(f"Ошибка: {exc.message}")
            if exc.hint:
                job.log(f"Совет: {exc.hint}")
            for state in job.states.values():
                if state.status in {"pending", "queued", *ACTIVE_STATUSES}:
                    job.set_state(state.index, status="error", error=exc.message, hint=exc.hint)
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.log(f"Непредвиденная ошибка: {short_error(exc)}")
            logger.error("job failed: %s", traceback.format_exc())
            for state in job.states.values():
                if state.status in {"pending", "queued", *ACTIVE_STATUSES}:
                    job.set_state(state.index, status="error", error=short_error(exc))
        finally:
            if temp_root:
                shutil.rmtree(temp_root, ignore_errors=True)
            job.finished_at = time.time()

    def _download_one(
        self,
        job: Job,
        state: TrackState,
        manifest: dict[str, Any],
        manifest_lock: threading.Lock,
        temp_root: Path,
    ) -> None:
        if job.cancelled:
            job.set_state(state.index, status="cancelled")
            return
        if not state.base:
            job.set_state(state.index, status="error", error="Не удалось определить имя файла.")
            return
        track = state.track
        job.set_state(state.index, status="preparing", progress=0)
        workdir = temp_root / f"{state.index:05d}_{stable_hash(track.key, 6)}"

        def state_callback(status: str, progress: float | None, speed: float | None, eta: int | None) -> None:
            job.set_state(state.index, status=status, progress=progress, speed=speed, eta=eta)

        def log_callback(message: str) -> None:
            job.log(f"[{track.artist or 'трек'} - {track.title}] {message}")

        def source_callback(url: str, title: str) -> None:
            job.set_state(state.index, source=url)

        callbacks = EngineCallbacks(
            state=state_callback,
            log=log_callback,
            cancel=lambda: job.cancelled,
            source=source_callback,
        )

        try:
            result = process_track(
                track,
                state.base,
                state.index,
                job.playlist,
                job.options,
                job.ffmpeg or "",
                workdir,
                callbacks,
                job.cover_cache,
            )
            path: Path = result["path"]
            try:
                relative = str(path.relative_to(job.playlist_dir))
            except ValueError:
                relative = path.name
            with manifest_lock:
                manifest["tracks"][track.key] = {
                    "file": relative,
                    "format": job.options["format"],
                    "quality": job.options.get("quality"),
                    "title": track.title,
                    "artist": track.artist,
                    "source_url": result.get("source_url"),
                    "done_at": time.time(),
                }
                self._save_manifest(job, manifest)
            job.set_state(state.index, status="done", progress=100, filename=path.name)
            for warning in result.get("warnings") or []:
                job.log(f"Предупреждение: {warning}")
            job.log(f"OK: {track.artist} - {track.title}")
        except DownloadCancelled:
            job.set_state(state.index, status="cancelled")
            job.log(f"Отменено: {track.artist} - {track.title}")
        except UserError as exc:
            job.set_state(state.index, status="error", error=exc.message, hint=exc.hint)
            job.log(f"ОШИБКА: {track.artist} - {track.title}: {exc.message}")
            if exc.hint:
                job.log(f"Совет: {exc.hint}")
        except Exception as exc:  # noqa: BLE001
            logger.error("track failed: %s", traceback.format_exc())
            job.set_state(state.index, status="error", error=short_error(exc))
            job.log(f"ОШИБКА: {track.artist} - {track.title}: {short_error(exc)}")
        finally:
            if state.status in {"pending", "preparing", "searching", "downloading", "converting", "tagging"}:
                job.set_state(state.index, status="error", error="Загрузка прервана.")
