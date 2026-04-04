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
    return tuple(int(x) for x in v.lstrip("v").split("."))


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def get_app_dir() -> Path:
    return Path(sys.executable).parent


def check_for_update() -> UpdateInfo:
    req = urllib.request.Request(GITHUB_API, headers={"Accept": "application/vnd.github.v3+json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

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
        has_update = _parse_version(remote_version) > _parse_version(__version__)
    except (ValueError, TypeError):
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
        candidates = list(extract_dir.iterdir())
        inner = candidates[0] if candidates else extract_dir

    bat = zip_path.parent / "_update.bat"
    bat.write_text(
        f"""@echo off
title Updating SkyMusicAutomation...
echo Waiting for application to exit...
timeout /t 2 /nobreak >nul
xcopy /s /e /y "{inner}\\*" "{app_dir}\\" >nul
echo Update complete. Restarting...
start "" "{app_dir}\\SkyMusicAutomation.exe"
del /q "{zip_path}"
rmdir /s /q "{extract_dir}"
del "%~f0"
""",
        encoding="utf-8",
    )

    subprocess.Popen(
        ["cmd.exe", "/c", str(bat)],
        creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
    )
    sys.exit(0)
