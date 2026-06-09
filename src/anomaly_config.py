from __future__ import annotations

from pathlib import Path
from typing import Any

import adaptive_peer_selection as adaptive

VARIABLE_GROUP_ALIASES = {
    "id_variables": ("id_variables", "id_vars", "ids"),
    "time_variables": ("time_variables", "time_vars", "date_variables", "dates"),
    "segment_variables": ("segment_variables", "segment_vars", "segments"),
    "feature_variables": ("feature_variables", "feature_vars", "features"),
}

ROLE_HINTS = {
    "customer_segment": ("segmentad", "segment", "customer_segment", "musteri_segment"),
    "sector": ("ref_altfaaliyet", "altfaaliyet", "faaliyet", "sector", "sektor"),
    "branch_id": ("sube_kd", "sube", "branch", "branch_id"),
    "turnover_amt": ("turnover_amt", "turnover", "ciro"),
    "active_subscriber": ("aktif_abone", "active_subscriber", "abone"),
}

VARIABLE_TOKEN_ALIASES = {
    "segment_variables",
    "segments",
    "segment_vars",
    "feature_peer_variables",
    "feature_buckets",
}


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


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [] if value.strip().lower() in {"", "auto", "none", "null"} else [value.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _clean_key(value: str) -> str:
    return "".join(ch.lower() for ch in str(value).strip() if ch.isalnum() or ch == "_")


def _matches_hint(column: str, hints: tuple[str, ...]) -> bool:
    clean = _clean_key(column)
    return any(_clean_key(hint) in clean for hint in hints)


def variable_groups_from_config(config: dict[str, Any]) -> dict[str, list[str]]:
    raw = config.get("variables", {})
    if not isinstance(raw, dict):
        return {key: [] for key in VARIABLE_GROUP_ALIASES}
    groups: dict[str, list[str]] = {}
    for canonical, aliases in VARIABLE_GROUP_ALIASES.items():
        value = next((raw.get(alias) for alias in aliases if alias in raw), [])
        groups[canonical] = _as_string_list(value)
    return groups


def role_map_from_variable_groups(config: dict[str, Any]) -> dict[str, str]:
    groups = variable_groups_from_config(config)
    out: dict[str, str] = {}
    ids = groups["id_variables"]
    times = groups["time_variables"]
    features = groups["feature_variables"]
    segments = groups["segment_variables"]

    if ids:
        out["customer_id"] = ids[0]
    if times:
        out["invoice_month"] = times[0]

    variables = config.get("variables", {})
    main_feature = variables.get("main_feature") if isinstance(variables, dict) else None
    if main_feature:
        out["bill_amount"] = str(main_feature)
    elif features:
        out["bill_amount"] = features[0]

    for role in ("turnover_amt", "active_subscriber"):
        found = next((col for col in features if _matches_hint(col, ROLE_HINTS[role])), None)
        if found:
            out[role] = found

    for role in ("customer_segment", "sector", "branch_id"):
        found = next((col for col in segments if _matches_hint(col, ROLE_HINTS[role])), None)
        if found:
            out[role] = found
    if "customer_segment" not in out and segments:
        out["customer_segment"] = segments[0]
    return out


def column_map_from_config(config: dict[str, Any]) -> dict[str, str]:
    from_groups = role_map_from_variable_groups(config)
    columns = config.get("columns", {})
    if not isinstance(columns, dict):
        raise ValueError("columns must be a mapping of logical name to source column.")
    if isinstance(columns.get("required"), dict):
        legacy = {
            str(logical): str(source)
            for logical, source in columns["required"].items()
            if source is not None and str(source).strip().lower() not in {"", "auto"}
        }
        return {**from_groups, **legacy}
    legacy = {str(logical): str(source) for logical, source in columns.items() if source is not None}
    return {**from_groups, **legacy}


def peer_variable_names_from_config(config: dict[str, Any]) -> list[str]:
    groups = variable_groups_from_config(config)
    role_map = role_map_from_variable_groups(config)
    reverse_role_map = {source: role for role, source in role_map.items()}
    peers: list[str] = []
    for column in groups["segment_variables"]:
        peers.append(reverse_role_map.get(column, column))
    if "turnover_amt" in role_map:
        peers.append("turnover_bucket")
    if "active_subscriber" in role_map:
        peers.append("active_subscriber_bucket")
    return list(dict.fromkeys(peers))


def _expand_peer_variable_tokens(value: Any, config: dict[str, Any]) -> tuple[str, ...]:
    items = _as_string_list(value)
    if not items:
        return tuple()
    peers = peer_variable_names_from_config(config)
    expanded: list[str] = []
    for item in items:
        token = item.strip()
        if token.lower() in VARIABLE_TOKEN_ALIASES:
            expanded.extend(peers)
        elif token.lower() == "auto":
            continue
        else:
            expanded.append(token)
    return tuple(dict.fromkeys(expanded))


def peer_config_from_config(config: dict[str, Any]) -> adaptive.PeerSelectionConfig:
    raw = config.get("peer_selection", {})
    values = dict(raw) if isinstance(raw, dict) else {}
    peer_variables = peer_variable_names_from_config(config)
    priority = values.get("priority_variables", "auto")
    if peer_variables and (priority is None or str(priority).lower() == "auto"):
        values["priority_variables"] = peer_variables
    else:
        values["priority_variables"] = _expand_peer_variable_tokens(priority, config)
    for key in ("mandatory_variables", "fallback_variables", "exclude_variables", "excluded_variables"):
        if key in values:
            values[key] = _expand_peer_variable_tokens(values[key], config)
    return adaptive.PeerSelectionConfig.from_mapping(values)


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
