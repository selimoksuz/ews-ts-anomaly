from __future__ import annotations

from pathlib import Path
from typing import Any

import adaptive_peer_selection as adaptive


def load_yaml_config(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required. Install project dependencies with: python -m pip install -r requirements.txt") from exc

    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return payload


def resolve_path(value: str | None, base_dir: Path) -> Path | None:
    if value is None:
        return None
    path = Path(str(value))
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def column_map_from_config(config: dict[str, Any]) -> dict[str, str]:
    columns = config.get("columns", {})
    if not isinstance(columns, dict):
        raise ValueError("columns must be a mapping of logical name to source column.")
    if isinstance(columns.get("required"), dict):
        return {
            str(logical): str(source)
            for logical, source in columns["required"].items()
            if source is not None and str(source).strip().lower() not in {"", "auto"}
        }
    return {str(logical): str(source) for logical, source in columns.items() if source is not None}


def peer_config_from_config(config: dict[str, Any]) -> adaptive.PeerSelectionConfig:
    return adaptive.PeerSelectionConfig.from_mapping(config.get("peer_selection", {}))


def support_thresholds_from_config(config: dict[str, Any]) -> adaptive.PeerSupportThresholds:
    return adaptive.PeerSupportThresholds.from_mapping(config.get("peer_support", {}))


def output_path(config: dict[str, Any], key: str, base_dir: Path, default: str) -> Path:
    outputs = config.get("outputs", {})
    value = outputs.get(key, default) if isinstance(outputs, dict) else default
    resolved = resolve_path(str(value), base_dir)
    if resolved is None:
        raise ValueError(f"Output path is missing: {key}")
    return resolved


def source_column_policy(config: dict[str, Any]) -> tuple[list[str] | None, list[str]]:
    source = config.get("source", config)
    output_columns = source.get("output_columns", "all") if isinstance(source, dict) else "all"
    exclude_columns = source.get("exclude_output_columns", []) if isinstance(source, dict) else []
    if output_columns in {None, "all", "*"}:
        include = None
    elif isinstance(output_columns, list):
        include = [str(col) for col in output_columns]
    else:
        raise ValueError("source.output_columns must be 'all' or a list of source column names.")
    exclude = [str(col) for col in exclude_columns] if isinstance(exclude_columns, list) else []
    return include, exclude


def bool_config(config: dict[str, Any], path: tuple[str, ...], default: bool = False) -> bool:
    current: Any = config
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return bool(current)
