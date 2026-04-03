from __future__ import annotations

import time
from abc import ABC, abstractmethod


class BaseInputBackend(ABC):
    @abstractmethod
    def key_down(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def key_up(self, key: str) -> None:
        raise NotImplementedError

    def tap(self, key: str, duration_ms: int = 30) -> None:
        self.key_down(key)
        time.sleep(max(duration_ms, 1) / 1000)
        self.key_up(key)


class DryRunInputBackend(BaseInputBackend):
    def key_down(self, key: str) -> None:
        print(f"[dry-input] down {key}")

    def key_up(self, key: str) -> None:
        print(f"[dry-input] up   {key}")


class PynputInputBackend(BaseInputBackend):
    def __init__(self) -> None:
        try:
            from pynput.keyboard import Controller  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise RuntimeError("pynput not installed, install with: pip install '.[input]'") from exc
        self._controller = Controller()

    def key_down(self, key: str) -> None:
        self._controller.press(key)

    def key_up(self, key: str) -> None:
        self._controller.release(key)
