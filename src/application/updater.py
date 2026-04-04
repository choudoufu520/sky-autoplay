from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path

from src import __version__

GITHUB_REPO = "choudoufu520/sky-autoplay"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ASSET_NAME = "SkyMusicAutomation-windows.zip"


@dataclass(slots=True)
class UpdateInfo:
    tag: str
    version: str
    current_version: str
    name: str
    body: str
    download_url: str
    published_at: str
    has_update: bool


def _parse_version(v: str) -> tuple[int, ...]:
    import re
    clean = re.sub(r"[^0-9.].*", "", v.lstrip("v"))
    return tuple(int(x) for x in clean.split(".") if x)


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def get_app_dir() -> Path:
    return Path(sys.executable).parent


def check_for_update() -> UpdateInfo:
    try:
        req = urllib.request.Request(GITHUB_API, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        raise ConnectionError(f"Network error: {exc}") from exc

    tag: str = data.get("tag_name", "")
    remote_version = tag.lstrip("v")
    name: str = data.get("name", tag)
    body: str = data.get("body", "")
    published: str = data.get("published_at", "")

    download_url = ""
    for asset in data.get("assets", []):
        if asset.get("name") == ASSET_NAME:
            download_url = asset.get("browser_download_url", "")
            break

    has_update = False
    try:
        remote_parts = _parse_version(remote_version)
        local_parts = _parse_version(__version__)
        if remote_parts and local_parts:
            has_update = remote_parts > local_parts
    except Exception:
        pass

    return UpdateInfo(
        tag=tag,
        version=remote_version,
        current_version=__version__,
        name=name,
        body=body,
        download_url=download_url,
        published_at=published,
        has_update=has_update,
    )


DownloadProgress = type("DownloadProgress", (), {})


def download_update(
    url: str,
    on_progress: type[None] | None = None,
    progress_callback: object | None = None,
) -> Path:
    """Download zip to temp dir and return the path."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="sky_update_"))
    zip_path = tmp_dir / ASSET_NAME

    def _report(block_num: int, block_size: int, total_size: int) -> None:
        if progress_callback and callable(progress_callback):
            downloaded = block_num * block_size
            progress_callback(downloaded, total_size)

    urllib.request.urlretrieve(url, str(zip_path), reporthook=_report)
    return zip_path


def apply_update(zip_path: Path) -> None:
    """Extract the downloaded zip and replace the current installation via a helper script."""
    if not is_frozen():
        raise RuntimeError("Auto-update is only supported for packaged builds.")

    app_dir = get_app_dir()
    extract_dir = zip_path.parent / "extracted"
    shutil.unpack_archive(str(zip_path), str(extract_dir))

    inner = extract_dir / "SkyMusicAutomation"
    if not inner.exists():
        if (extract_dir / "SkyMusicAutomation.exe").exists():
            inner = extract_dir
        else:
            candidates = [c for c in extract_dir.iterdir() if c.is_dir()]
            inner = candidates[0] if len(candidates) == 1 else extract_dir

    pid = os.getpid()
    exe_path = app_dir / "SkyMusicAutomation.exe"

    log_path = zip_path.parent / "_update.log"
    bat = zip_path.parent / "_update.bat"
    bat.write_text(
        f"""@echo off
title Updating SkyMusicAutomation...
set "LOG={log_path}"
echo [%date% %time%] Update started, waiting for PID {pid} >> "%LOG%"
echo Source: {inner} >> "%LOG%"
echo Target: {app_dir} >> "%LOG%"
:waitloop
tasklist /FI "PID eq {pid}" 2>NUL | find /I "{pid}" >NUL
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto waitloop
)
echo [%date% %time%] Process exited, copying files... >> "%LOG%"
xcopy /s /e /y "{inner}\\*" "{app_dir}\\" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] xcopy failed, retry in 3s... >> "%LOG%"
    timeout /t 3 /nobreak >nul
    xcopy /s /e /y "{inner}\\*" "{app_dir}\\" >> "%LOG%" 2>&1
)
echo [%date% %time%] Copy done, restarting... >> "%LOG%"
start "" "{exe_path}"
timeout /t 3 /nobreak >nul
del /q "{zip_path}" >nul 2>&1
rmdir /s /q "{extract_dir}" >nul 2>&1
del "%~f0"
""",
        encoding="utf-8",
    )

    subprocess.Popen(
        ["cmd.exe", "/c", str(bat)],
        creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
    )
    sys.exit(0)
