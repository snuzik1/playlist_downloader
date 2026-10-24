from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def pick_folder(initial: str = "") -> str | None:
    initial_path = str(Path(initial).expanduser()) if initial else str(Path.home())
    if sys.platform.startswith("win"):
        escaped = initial_path.replace("'", "''")
        script = f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Выберите папку для музыки'
$dialog.ShowNewFolderButton = $true
if (Test-Path -LiteralPath '{escaped}') {{ $dialog.SelectedPath = '{escaped}' }}
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
    [Console]::WriteLine($dialog.SelectedPath)
}}
"""
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-STA",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    script,
                ],
                capture_output=True,
                timeout=600,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
            )
        except (OSError, subprocess.SubprocessError):
            return None
        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        return lines[-1] if lines else None

    try:
        if sys.platform == "darwin":
            result = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'POSIX path of (choose folder with prompt "Выберите папку для музыки")',
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
        elif shutil.which("zenity"):
            result = subprocess.run(
                ["zenity", "--file-selection", "--directory", "--title=Выберите папку для музыки"],
                capture_output=True,
                text=True,
                timeout=600,
                cwd=initial_path if Path(initial_path).is_dir() else None,
            )
        elif shutil.which("kdialog"):
            result = subprocess.run(
                ["kdialog", "--getexistingdirectory", initial_path, "Выберите папку для музыки"],
                capture_output=True,
                text=True,
                timeout=600,
            )
        else:
            return None
        if result.returncode != 0:
            return None
        path = (result.stdout or "").strip()
        return path or None
    except (OSError, subprocess.SubprocessError):
        return None
