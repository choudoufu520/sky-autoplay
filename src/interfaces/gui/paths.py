from __future__ import annotations

import os
import sys


def base_path() -> str:
    """Return the base directory: _MEIPASS when packaged, project root otherwise."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.abspath(".")


def default_mapping_path() -> str:
    return os.path.join(base_path(), "configs", "mapping.example.yaml")
