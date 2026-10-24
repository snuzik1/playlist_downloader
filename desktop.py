from __future__ import annotations

import argparse
import ctypes
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pldl import __version__

WINDOW_TITLE = "PLDL — загрузчик плейлистов"
PAGE_TITLE = "PLDL — загрузка плейлистов"
MUTEX_NAME = "Local\\PLDL_Desktop_SingleInstance"
_log_stream = None


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", app_dir())) / name


def _log_path() -> Path:
    try:
        probe = app_dir() / "pldl.log"
        probe.touch(exist_ok=True)
        return probe
    except OSError:
        fallback = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PLDL"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback / "pldl.log"


def setup_logging() -> None:
    global _log_stream
    has_console = sys.stdout is not None and sys.stderr is not None
    if not has_console:
        _log_stream = open(os.devnull, "w", encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = _log_stream
        if sys.stderr is None:
            sys.stderr = _log_stream

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s  %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        handler = RotatingFileHandler(_log_path(), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except OSError:
        pass
    if has_console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)


def _message(text: str, title: str = WINDOW_TITLE, flags: int = 0x10) -> None:
    if sys.platform != "win32":
        print(f"{title}: {text}")
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except Exception:  # noqa: BLE001
        logging.getLogger("pldl.desktop").exception("message box failed")


def _ensure_single_instance() -> tuple[bool, object | None]:
    if sys.platform != "win32":
        return True, None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True, None
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            user32.FindWindowW.restype = ctypes.c_void_p
            hwnd = user32.FindWindowW(None, WINDOW_TITLE) or user32.FindWindowW(None, PAGE_TITLE)
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
            else:
                _message("PLDL уже запущен. Окно приложения должно быть на панели задач.", flags=0x40)
            kernel32.CloseHandle(handle)
            return False, None
        return True, handle
    except Exception:  # noqa: BLE001
        logging.getLogger("pldl.desktop").exception("mutex check failed")
        return True, None


def _find_browser() -> str | None:
    if sys.platform != "win32":
        for name in ("google-chrome", "chromium", "microsoft-edge"):
            found = shutil.which(name)
            if found:
                return found
        return None
    roots = [
        os.environ.get("ProgramFiles(x86)", ""),
        os.environ.get("ProgramFiles", ""),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    candidates = []
    for root in roots:
        if not root:
            continue
        candidates.extend(
            [
                os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"),
            ]
        )
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for path in candidates:
        if path and Path(path).is_file():
            return path
    return None


def _run_browser_window(url: str) -> None:
    browser = _find_browser()
    if not browser:
        raise RuntimeError("Не найден Microsoft Edge или Google Chrome для окна приложения.")
    profile = Path(tempfile.mkdtemp(prefix="pldl-window-"))
    args = [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--window-size=1200,860",
    ]
    logging.getLogger("pldl.desktop").info("fallback window: %s", browser)
    try:
        process = subprocess.Popen(args)
        process.wait()
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def _run_native_window(url: str, debug: bool) -> None:
    import webview

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=1200,
        height=860,
        min_size=(960, 640),
        background_color="#0b0d12",
        text_select=True,
        shadow=False,
    )
    if window is None:
        raise RuntimeError("Не удалось создать окно приложения.")
    icon = resource_path("assets/pldl.ico")
    webview.start(
        debug=debug,
        private_mode=True,
        icon=str(icon) if icon.is_file() else None,
    )


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    logger = logging.getLogger("pldl.desktop")

    parser = argparse.ArgumentParser(description="PLDL — настольное приложение")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PLDL_PORT", "8765")))
    parser.add_argument("--browser", action="store_true", help="открыть в окне Edge/Chrome вместо WebView2")
    parser.add_argument("--debug", action="store_true", help="включить инструменты разработчика WebView2")
    args = parser.parse_args(argv)

    ok, mutex_handle = _ensure_single_instance()
    if not ok:
        return 0

    try:
        from app import _pick_port, app as flask_app
        from werkzeug.serving import make_server

        port = _pick_port(max(1, min(65535, args.port)))
        url = f"http://127.0.0.1:{port}/"
        server = make_server("127.0.0.1", port, flask_app, threaded=True)
        server.daemon_threads = True
        server_thread = threading.Thread(
            target=server.serve_forever,
            name="pldl-server",
            daemon=True,
        )
        server_thread.start()

        logger.info("PLDL %s desktop started on %s", __version__, url)
        try:
            if args.browser:
                _run_browser_window(url)
            else:
                try:
                    _run_native_window(url, args.debug)
                except Exception:  # noqa: BLE001
                    logger.exception("native window failed; falling back to Edge/Chrome")
                    _run_browser_window(url)
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=3)
            logger.info("PLDL desktop stopped")
            if mutex_handle is not None and sys.platform == "win32":
                try:
                    ctypes.windll.kernel32.CloseHandle(mutex_handle)
                except Exception:  # noqa: BLE001
                    pass
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("desktop startup failed")
        _message(
            "Не удалось запустить PLDL.\n\n"
            f"{exc}\n\n"
            f"Подробности: {_log_path()}",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
