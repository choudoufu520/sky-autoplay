from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.domain.chart import ChartDocument
from src.domain.mapping import MappingConfig


def load_mapping(path: Path) -> MappingConfig:
    payload = _read_yaml(path)
    config = MappingConfig.model_validate(payload)
    if config.default_profile not in config.profiles:
        raise ValueError(
            f"default_profile '{config.default_profile}' not found in profiles keys: {list(config.profiles)}"
        )
    return config


def save_mapping(path: Path, config: MappingConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump()
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def save_chart(path: Path, chart: ChartDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(chart.model_dump_json(indent=2), encoding="utf-8")


def load_chart(path: Path) -> ChartDocument:
    return ChartDocument.model_validate_json(path.read_text(encoding="utf-8"))


def load_play_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    data = _read_yaml(path)
    if not isinstance(data, dict):
        raise ValueError("play config must be a mapping object")
    return data


def _read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)
