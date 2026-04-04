"""Sky automation package."""

from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("sky-music-automation")
except Exception:
    __version__ = "0.0.0-dev"
