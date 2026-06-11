from __future__ import annotations

from pathlib import Path
from typing import Any

import adaptive_peer_selection as adaptive

VARIABLE_GROUP_ALIASES = {
    "id_variables": ("id_variables", "id_vars", "ids"),
    "time_variables": ("time_variables", "time_vars", "date_variables", "dates"),
    "segment_variables": ("segment_variables", "segment_vars", "segments"),
    "feature_variables": ("feature_variables", "feature_vars", "features"),
    "exclude_variables": ("exclude_variables", "excluded_variables", "ignore_variables", "ignored_variables"),
}

VARIABLE_TOKEN_ALIASES = {
    "segment_variables",
    "segments",
    "segment_vars",
}

DEFAULT_FEATURE_BUCKET_VARIANTS = ("q3", "q4", "q5", "q8")
SEGMENT_VARIABLES_META_KEY = "__segment_variables__"
SEGMENT_VARIABLES_AUTO_META_KEY = "__segment_variables_auto__"
EXCLUDED_VARIABLES_META_KEY = "__excluded_variables__"


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


def _is_auto_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "auto"
    if isinstance(value, (list, tuple, set)) and len(value) == 1:
        return _is_auto_value(next(iter(value)))
    return False


def _clean_key(value: str) -> str:
    return "".join(ch.lower() for ch in str(value).strip() if ch.isalnum() or ch == "_")


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def derived_features_from_config(config: dict[str, Any]) -> dict[str, Any]:
    model = _as_mapping(config.get("model", {}))
    raw = _as_mapping(model.get("derived_features", config.get("derived_features", {})))
    if "ratio_feature" in raw and "feature_ratio" not in raw:
        raw["feature_ratio"] = raw["ratio_feature"]
    return raw


def _resolve_variable_reference(reference: Any, role_map: dict[str, str], groups: dict[str, list[str]]) -> str | None:
    if reference is None:
        return None
    text = str(reference).strip()
    lowered = text.lower()
    if lowered in {"", "none", "null", "false"}:
        return None
    if lowered in {"main_feature", "target_variable", "amount_variable", "main_metric"}:
        return role_map.get("main_metric")
    if text in role_map:
        return role_map[text]
    for values in groups.values():
        if text in values:
            return text
    return text


def _ratio_feature_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = derived_features_from_config(config)
    ratio = raw.get("feature_ratio", {})
    return dict(ratio) if isinstance(ratio, dict) else {}


def _behavior_peer_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = derived_features_from_config(config)
    behavior = raw.get("behavior_peer", {})
    return dict(behavior) if isinstance(behavior, dict) else {}


def _feature_bucket_names_from_config(config: dict[str, Any]) -> list[str]:
    ratio = _ratio_feature_config(config)
    raw = ratio.get("peer_bucket_variants", {})
    if raw is False:
        return []
    if isinstance(raw, dict):
        if str(raw.get("enabled", True)).strip().lower() in {"false", "0", "no", "hayir"}:
            return []
        variants = raw.get("variants", DEFAULT_FEATURE_BUCKET_VARIANTS)
    elif isinstance(raw, list):
        variants = raw
    else:
        variants = DEFAULT_FEATURE_BUCKET_VARIANTS
    names: list[str] = []
    for idx, item in enumerate(variants):
        if isinstance(item, dict):
            raw_name = item.get("name", f"q{idx + 1}")
            quantiles = item.get("quantiles", [])
            if isinstance(quantiles, list) and not quantiles:
                continue
        else:
            raw_name = str(item)
        clean = _clean_key(str(raw_name))[:24]
        if clean:
            names.append(f"feature_bucket_{clean}")
    return list(dict.fromkeys(names))


def variable_groups_from_config(config: dict[str, Any]) -> dict[str, list[str]]:
    raw = config.get("variables", {})
    if not isinstance(raw, dict):
        return {key: [] for key in VARIABLE_GROUP_ALIASES}
    groups: dict[str, list[str]] = {}
    for canonical, aliases in VARIABLE_GROUP_ALIASES.items():
        value = next((raw.get(alias) for alias in aliases if alias in raw), [])
        groups[canonical] = [] if canonical == "segment_variables" and _is_auto_value(value) else _as_string_list(value)
    return groups


def segment_variables_auto_from_config(config: dict[str, Any]) -> bool:
    raw = config.get("variables", {})
    if not isinstance(raw, dict):
        return False
    value = next((raw.get(alias) for alias in VARIABLE_GROUP_ALIASES["segment_variables"] if alias in raw), [])
    return _is_auto_value(value)


def role_map_from_variable_groups(config: dict[str, Any]) -> dict[str, str]:
    groups = variable_groups_from_config(config)
    out: dict[str, str] = {}
    ids = groups["id_variables"]
    times = groups["time_variables"]
    features = groups["feature_variables"]

    if ids:
        out["customer_id"] = ids[0]
    if times:
        out["invoice_month"] = times[0]

    variables = config.get("variables", {})
    main_feature = variables.get("main_feature") if isinstance(variables, dict) else None
    if main_feature:
        out["main_metric"] = str(main_feature)
    elif features:
        out["main_metric"] = features[0]

    ratio = _ratio_feature_config(config)
    ratio_enabled = str(ratio.get("enabled", "auto")).strip().lower()
    denominator = _resolve_variable_reference(ratio.get("denominator"), out, groups)
    if denominator and ratio_enabled not in {"false", "0", "no", "hayir"}:
        out["reference_feature"] = denominator

    bucket_features = derived_features_from_config(config).get("bucket_features", [])
    if isinstance(bucket_features, list):
        first_active_like = next(
            (
                _resolve_variable_reference(item.get("source"), out, groups)
                for item in bucket_features
                if isinstance(item, dict)
                and str(item.get("internal_role", "")).strip().lower() in {"exposure_feature", "exposure"}
            ),
            None,
        )
        if first_active_like:
            out["exposure_feature"] = first_active_like
    return out


def column_map_from_config(config: dict[str, Any]) -> dict[str, Any]:
    from_groups = role_map_from_variable_groups(config)
    groups = variable_groups_from_config(config)
    if segment_variables_auto_from_config(config):
        from_groups[SEGMENT_VARIABLES_AUTO_META_KEY] = True
    elif groups["segment_variables"]:
        from_groups[SEGMENT_VARIABLES_META_KEY] = groups["segment_variables"]
    if groups.get("exclude_variables"):
        from_groups[EXCLUDED_VARIABLES_META_KEY] = groups["exclude_variables"]
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
    if segment_variables_auto_from_config(config):
        return []
    groups = variable_groups_from_config(config)
    role_map = role_map_from_variable_groups(config)
    reverse_role_map = {source: role for role, source in role_map.items()}
    peers: list[str] = []
    for column in groups["segment_variables"]:
        peers.append(reverse_role_map.get(column, column))
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
    groups = variable_groups_from_config(config)
    peer_variables = peer_variable_names_from_config(config)
    priority = values.get("priority_variables", "auto")
    if peer_variables and not segment_variables_auto_from_config(config) and (priority is None or str(priority).lower() == "auto"):
        values["priority_variables"] = peer_variables
    else:
        values["priority_variables"] = _expand_peer_variable_tokens(priority, config)
    for key in ("mandatory_variables", "fallback_variables", "exclude_variables", "excluded_variables"):
        if key in values:
            values[key] = _expand_peer_variable_tokens(values[key], config)
    if groups.get("exclude_variables"):
        explicit_exclusions = list(values.get("exclude_variables", values.get("excluded_variables", ())))
        values["exclude_variables"] = list(dict.fromkeys([*explicit_exclusions, *groups["exclude_variables"]]))
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
