from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from src.application.updater import UpdateInfo, apply_update, check_for_update, download_update


class CheckUpdateWorker(QThread):
    finished = Signal(object)
    error = Signal(str)

    def run(self) -> None:
        try:
            info = check_for_update()
            self.finished.emit(info)
        except BaseException as exc:
            try:
                self.error.emit(str(exc))
            except Exception:
                pass


class DownloadUpdateWorker(QThread):
    progress = Signal(int, int)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            zip_path = download_update(
                self._url,
                progress_callback=self._on_progress,
            )
            if not self._cancelled:
                self.finished.emit(str(zip_path))
        except BaseException as exc:
            if not self._cancelled:
                try:
                    self.error.emit(str(exc))
                except Exception:
                    pass

    def _on_progress(self, downloaded: int, total: int) -> None:
        if self._cancelled:
            raise InterruptedError("Download cancelled")
        self.progress.emit(downloaded, total)
