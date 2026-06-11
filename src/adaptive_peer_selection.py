from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Mapping

import numpy as np
import pandas as pd


DEFAULT_BLOCKED_VALUES: dict[str, tuple[str, ...]] = {
    "behavior_cluster": ("behavior_unknown", "", "nan", "None"),
    "behavior_level_bucket": ("level_unknown", "", "nan", "None"),
    "behavior_volatility_bucket": ("vol_unknown", "", "nan", "None"),
    "behavior_trend_bucket": ("trend_unknown", "", "nan", "None"),
    "feature_ratio_bucket": (
        "reference_feature_missing",
        "reference_feature_zero",
        "reference_feature_unknown",
        "",
        "nan",
        "None",
    ),
    "feature_bucket": ("feature_missing", "feature_zero", "feature_unknown", "", "nan", "None"),
}

DEFAULT_OBJECTIVE_WEIGHTS: dict[str, float] = {
    "representability": 0.20,
    "distribution": 0.30,
    "calibration": 0.20,
    "stability": 0.10,
    "specificity": 0.10,
    "support": 0.10,
}

DEFAULT_DISTRIBUTION_QUALITY: dict[str, float] = {
    "iqr_weight": 0.30,
    "mad_weight": 0.25,
    "tail_weight": 0.20,
    "skew_weight": 0.05,
    "kurtosis_weight": 0.05,
    "mean_median_weight": 0.05,
    "std_median_weight": 0.10,
    "current_iqr_log_full_penalty": 1.60,
    "history_iqr_log_full_penalty": 1.80,
    "current_mad_log_full_penalty": 0.90,
    "history_mad_log_full_penalty": 1.00,
    "skew_full_penalty": 2.0,
    "kurtosis_full_penalty": 6.0,
    "tail_full_penalty": 0.10,
    "mean_median_full_penalty_ratio": 8.0,
    "std_median_full_penalty_ratio": 12.0,
}

DEFAULT_TECHNICAL_EXCLUSIONS = {
    "customer_id",
    "invoice_month",
    "calendar_month",
    "month_ord",
    "month_of_year",
    "main_metric",
    "log_main_metric",
    "reference_feature",
    "reference_feature_for_model",
    "log_reference_feature",
    "main_to_reference_log_ratio",
    "main_to_reference_ratio",
    "valid_main_metric_for_model",
    "negative_main_metric_flag",
    "zero_main_metric_flag",
    "source_row_count",
    "customer_obs_count_total",
    "previous_month_ord",
    "month_gap_from_previous",
    "exposure_feature",
    "exposure_feature_missing_flag",
    "reference_feature_positive_flag",
    "behavior_history_n",
    "behavior_level_bucket",
    "behavior_volatility_bucket",
    "behavior_trend_bucket",
    "behavior_median_main_metric",
    "behavior_volatility_log",
    "behavior_trend_slope",
}

DEFAULT_TECHNICAL_PREFIXES = (
    "actual_to_expected_",
    "anomaly_",
    "confidence",
    "current_",
    "customer_explainability_",
    "customer_family_",
    "customer_recent3_",
    "customer_recent_",
    "customer_seasonal_",
    "customer_trend_",
    "data_gap_",
    "evidence_",
    "expected_",
    "final_",
    "gap_",
    "hist_",
    "historical_",
    "is_disconnected_",
    "is_new_",
    "last_",
    "log_",
    "model_",
    "moy_",
    "peer_",
    "previous_",
    "primary_",
    "prior_",
    "ratio_",
    "reason_",
    "recent_",
    "score_",
    "secondary_",
    "self_history_",
    "signal_",
    "source_",
    "feature_ratio_",
    "watchlist_",
)

DEFAULT_PREFERRED_VARIABLES = (
    "feature_ratio_bucket",
    "exposure_bucket",
    "behavior_cluster",
)

VARIABLE_NAME_ALIASES = {
    "feature_ratio_bucket": "feature_ratio_bucket",
    "feature_bucket_q3": "feature_q3",
    "feature_bucket_q4": "feature_q4",
    "feature_bucket_q5": "feature_q5",
    "feature_bucket_q8": "feature_q8",
    "exposure_bucket": "exposure_bucket",
    "behavior_cluster": "behavior",
    "behavior_level_bucket": "behavior_level",
    "behavior_volatility_bucket": "behavior_volatility",
    "behavior_trend_bucket": "behavior_trend",
    "_global_key": "global",
}

BEHAVIOR_BUCKET_COLUMNS = (
    "behavior_level_bucket",
    "behavior_volatility_bucket",
    "behavior_trend_bucket",
)


@dataclass(frozen=True)
class PeerCandidate:
    name: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class PeerSupportThresholds:
    min_history_rows: int = 120
    min_season_rows: int = 15
    min_recent_rows: int = 25
    min_current_rows: int = 20
    min_distribution_score: float = 20.0
    strong_history_rows: int = 800
    strong_season_rows: int = 100
    strong_recent_rows: int = 150
    strong_current_rows: int = 120

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "PeerSupportThresholds":
        if not values:
            return cls()
        allowed = {field for field in cls.__dataclass_fields__}
        clean = {key: value for key, value in values.items() if key in allowed}
        return cls(**clean)


@dataclass(frozen=True)
class PeerSelectionConfig:
    priority_variables: tuple[str, ...] = ()
    mandatory_variables: tuple[str, ...] = ()
    fallback_variables: tuple[str, ...] = ()
    explicit_levels: tuple[PeerCandidate, ...] = ()
    include_global: bool = True
    max_variables_per_peer: int = 6
    candidate_strategy: str = "objective_lattice"
    selection_mode: str = "objective"
    objective_weights: Mapping[str, float] | None = None
    distribution_quality: Mapping[str, float] | None = None
    excluded_variables: tuple[str, ...] = ()
    max_inferred_cardinality: int = 250
    max_candidate_count: int = 0
    blocked_values: Mapping[str, tuple[str, ...]] | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "PeerSelectionConfig":
        if not values:
            return cls()
        explicit_levels = tuple(_parse_explicit_levels(values.get("explicit_levels", [])))
        raw_priority = values.get("priority_variables", ())
        priority_variables = (
            ()
            if raw_priority is None or (isinstance(raw_priority, str) and raw_priority.lower() == "auto")
            else (tuple([str(raw_priority)]) if isinstance(raw_priority, str) else tuple(str(item) for item in raw_priority))
        )
        blocked = {
            str(key): tuple(str(item) for item in value)
            for key, value in dict(values.get("blocked_values", DEFAULT_BLOCKED_VALUES)).items()
        }
        return cls(
            priority_variables=priority_variables,
            mandatory_variables=tuple(str(item) for item in values.get("mandatory_variables", cls.mandatory_variables)),
            fallback_variables=tuple(str(item) for item in values.get("fallback_variables", cls.fallback_variables)),
            explicit_levels=explicit_levels,
            include_global=bool(values.get("include_global", cls.include_global)),
            max_variables_per_peer=int(values.get("max_variables_per_peer", cls.max_variables_per_peer)),
            candidate_strategy=str(values.get("candidate_strategy", cls.candidate_strategy)),
            selection_mode=str(values.get("selection_mode", cls.selection_mode)),
            objective_weights={
                str(key): float(value)
                for key, value in dict(values.get("objective_weights", DEFAULT_OBJECTIVE_WEIGHTS)).items()
            },
            distribution_quality={
                str(key): float(value)
                for key, value in dict(values.get("distribution_quality", DEFAULT_DISTRIBUTION_QUALITY)).items()
            },
            excluded_variables=tuple(str(item) for item in values.get("exclude_variables", values.get("excluded_variables", ()))),
            max_inferred_cardinality=int(values.get("max_inferred_cardinality", cls.max_inferred_cardinality)),
            max_candidate_count=int(values.get("max_candidate_count", cls.max_candidate_count)),
            blocked_values=blocked,
        )


def _parse_explicit_levels(raw_levels: Any) -> list[PeerCandidate]:
    levels: list[PeerCandidate] = []
    if not isinstance(raw_levels, list):
        return levels
    for raw in raw_levels:
        if isinstance(raw, dict):
            columns = tuple(str(col) for col in raw.get("columns", []))
            name = str(raw.get("name") or peer_level_name(columns))
            levels.append(PeerCandidate(name=name, columns=columns))
        elif isinstance(raw, list):
            columns = tuple(str(col) for col in raw)
            levels.append(PeerCandidate(name=peer_level_name(columns), columns=columns))
    return levels


def dedupe(items: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return tuple(out)


def with_excluded_variables(config: PeerSelectionConfig, extra_exclusions: list[str] | tuple[str, ...]) -> PeerSelectionConfig:
    return PeerSelectionConfig(
        priority_variables=config.priority_variables,
        mandatory_variables=config.mandatory_variables,
        fallback_variables=config.fallback_variables,
        explicit_levels=config.explicit_levels,
        include_global=config.include_global,
        max_variables_per_peer=config.max_variables_per_peer,
        candidate_strategy=config.candidate_strategy,
        selection_mode=config.selection_mode,
        objective_weights=config.objective_weights,
        distribution_quality=config.distribution_quality,
        excluded_variables=dedupe([*config.excluded_variables, *[str(item) for item in extra_exclusions]]),
        max_inferred_cardinality=config.max_inferred_cardinality,
        max_candidate_count=config.max_candidate_count,
        blocked_values=config.blocked_values,
    )


def with_allowed_variables(config: PeerSelectionConfig, allowed_variables: list[str] | tuple[str, ...]) -> PeerSelectionConfig:
    allowed = dedupe([str(item) for item in allowed_variables])
    allowed_set = set(allowed)
    priority = tuple(var for var in config.priority_variables if var in allowed_set) if config.priority_variables else allowed
    if not priority:
        priority = ("__no_segment_variable__",)
    mandatory = tuple(var for var in config.mandatory_variables if var in allowed_set)
    fallback = tuple(var for var in config.fallback_variables if var in allowed_set)
    explicit = tuple(
        candidate
        for candidate in config.explicit_levels
        if candidate.columns and all(column in allowed_set for column in candidate.columns)
    )
    return PeerSelectionConfig(
        priority_variables=priority,
        mandatory_variables=mandatory,
        fallback_variables=fallback,
        explicit_levels=explicit,
        include_global=config.include_global,
        max_variables_per_peer=config.max_variables_per_peer,
        candidate_strategy=config.candidate_strategy,
        selection_mode=config.selection_mode,
        objective_weights=config.objective_weights,
        distribution_quality=config.distribution_quality,
        excluded_variables=config.excluded_variables,
        max_inferred_cardinality=config.max_inferred_cardinality,
        max_candidate_count=config.max_candidate_count,
        blocked_values=config.blocked_values,
    )


def peer_level_name(columns: tuple[str, ...] | list[str]) -> str:
    if not columns:
        return "global"
    return "_".join(VARIABLE_NAME_ALIASES.get(col, col) for col in columns)


def peer_columns_display(columns: tuple[str, ...] | list[str]) -> str:
    if not columns:
        return "global"
    return "+".join(VARIABLE_NAME_ALIASES.get(col, col) for col in columns)


def available_peer_variables(frame: list[str] | pd.Index | pd.DataFrame, config: PeerSelectionConfig) -> tuple[str, ...]:
    columns = set(str(col) for col in (frame.columns if isinstance(frame, pd.DataFrame) else frame))
    if config.priority_variables:
        return tuple(var for var in dedupe(config.priority_variables) if var in columns and var not in config.excluded_variables)
    return infer_peer_variables(frame, config)


def infer_peer_variables(frame: list[str] | pd.Index | pd.DataFrame, config: PeerSelectionConfig) -> tuple[str, ...]:
    columns = list(frame.columns if isinstance(frame, pd.DataFrame) else frame)
    excluded = set(DEFAULT_TECHNICAL_EXCLUSIONS) | set(config.excluded_variables)
    preferred: list[str] = []
    for col in DEFAULT_PREFERRED_VARIABLES:
        if col not in columns or col in excluded:
            continue
        if isinstance(frame, pd.DataFrame):
            unique_count = int(frame[col].nunique(dropna=True))
            if unique_count <= 1 or unique_count > config.max_inferred_cardinality:
                continue
        preferred.append(col)
    extras: list[str] = []
    for column in columns:
        if column in excluded or column in preferred or column.startswith("_"):
            continue
        if str(column).startswith(DEFAULT_TECHNICAL_PREFIXES):
            continue
        if isinstance(frame, pd.DataFrame):
            series = frame[column]
            unique_count = int(series.nunique(dropna=True))
            is_dimension = (
                pd.api.types.is_object_dtype(series)
                or pd.api.types.is_bool_dtype(series)
                or pd.api.types.is_categorical_dtype(series)
                or unique_count <= config.max_inferred_cardinality
            )
            if not is_dimension or unique_count <= 1 or unique_count > config.max_inferred_cardinality:
                continue
        extras.append(str(column))
    return dedupe([*preferred, *extras])


def build_adaptive_peer_candidates(
    frame_columns: list[str] | pd.Index | pd.DataFrame,
    has_feature_ratio_signal: bool,
    config: PeerSelectionConfig | None = None,
) -> list[PeerCandidate]:
    rules = config or PeerSelectionConfig()
    variables = list(available_peer_variables(frame_columns, rules))
    high_cardinality_variables = _high_cardinality_peer_variables(frame_columns, variables, rules.max_inferred_cardinality)
    if not has_feature_ratio_signal:
        variables = [var for var in variables if var != "feature_ratio_bucket" and not var.startswith("feature_bucket_")]

    if rules.explicit_levels:
        return limit_candidates(_validated_explicit_candidates(rules.explicit_levels, variables, rules.include_global), rules)

    mandatory = [var for var in rules.mandatory_variables if var in variables]
    optional = [var for var in variables if var not in mandatory]
    if rules.candidate_strategy == "all_combinations":
        return limit_candidates(_build_all_combination_candidates(variables, mandatory, optional, rules), rules)
    if rules.candidate_strategy in {"auto", "objective_lattice", "adaptive_lattice"}:
        return limit_candidates(
            _build_objective_lattice_candidates(variables, mandatory, optional, rules, high_cardinality_variables),
            rules,
        )

    return limit_candidates(_build_priority_path_candidates(variables, mandatory, optional, rules), rules)


def _build_all_combination_candidates(
    variables: list[str],
    mandatory: list[str],
    optional: list[str],
    rules: PeerSelectionConfig,
) -> list[PeerCandidate]:
    candidates: list[tuple[str, ...]] = []
    if mandatory:
        max_optional = max(0, rules.max_variables_per_peer - len(mandatory))
        for size in range(min(max_optional, len(optional)), -1, -1):
            for subset in combinations(optional, size):
                candidates.append(tuple(mandatory + list(subset)))
    else:
        max_size = min(rules.max_variables_per_peer, len(optional))
        for size in range(max_size, 0, -1):
            for subset in combinations(optional, size):
                candidates.append(tuple(subset))

    fallback = [var for var in rules.fallback_variables if var in variables and var not in mandatory]
    max_fallback_size = min(3, len(fallback), rules.max_variables_per_peer)
    for size in range(max_fallback_size, 0, -1):
        for subset in combinations(fallback, size):
            candidates.append(tuple(subset))

    if rules.include_global:
        candidates.append(tuple())

    return _sort_and_format_candidates(candidates, variables)


def _build_priority_path_candidates(
    variables: list[str],
    mandatory: list[str],
    optional: list[str],
    rules: PeerSelectionConfig,
) -> list[PeerCandidate]:
    candidates: list[tuple[str, ...]] = []
    max_optional = max(0, rules.max_variables_per_peer - len(mandatory))
    selected_optional = optional[:max_optional]
    base = mandatory if mandatory else []
    full = tuple(base + selected_optional)

    min_size = len(base) if base else 1
    for size in range(len(full), min_size - 1, -1):
        candidates.append(tuple(full[:size]))

    for removed in selected_optional:
        alternate = tuple(base + [var for var in selected_optional if var != removed])
        if len(alternate) >= min_size:
            candidates.append(alternate)

    fallback = [var for var in rules.fallback_variables if var in variables and var not in base]
    if fallback:
        fallback_full = tuple(fallback[: min(3, len(fallback), rules.max_variables_per_peer)])
        for size in range(len(fallback_full), 0, -1):
            candidates.append(tuple(fallback_full[:size]))

    if rules.include_global:
        candidates.append(tuple())

    return _sort_and_format_candidates(candidates, variables)


def _is_feature_bucket_variable(variable: str) -> bool:
    return variable == "feature_ratio_bucket" or variable.startswith("feature_bucket_")


def _is_behavior_variable(variable: str) -> bool:
    return variable == "behavior_cluster" or variable in BEHAVIOR_BUCKET_COLUMNS


def _is_exposure_bucket_variable(variable: str) -> bool:
    return variable == "exposure_bucket"


def _is_engine_derived_peer_variable(variable: str) -> bool:
    return (
        _is_feature_bucket_variable(variable)
        or _is_behavior_variable(variable)
        or _is_exposure_bucket_variable(variable)
    )


def _limited_combinations(variables: list[str], max_size: int) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []
    upper = min(max_size, len(variables))
    for size in range(upper, 0, -1):
        out.extend(tuple(combo) for combo in combinations(variables, size))
    return out


def _append_if_valid(
    candidates: list[tuple[str, ...]],
    columns: tuple[str, ...],
    max_variables_per_peer: int,
) -> None:
    deduped = dedupe(columns)
    if not deduped or len(deduped) > max_variables_per_peer:
        return
    feature_bucket_count = sum(1 for col in deduped if col.startswith("feature_bucket_"))
    if feature_bucket_count > 1:
        return
    if "feature_ratio_bucket" in deduped and feature_bucket_count:
        return
    if "behavior_cluster" in deduped and any(col in BEHAVIOR_BUCKET_COLUMNS for col in deduped):
        return
    candidates.append(tuple(deduped))


def _build_objective_lattice_candidates(
    variables: list[str],
    mandatory: list[str],
    optional: list[str],
    rules: PeerSelectionConfig,
    high_cardinality_variables: set[str] | None = None,
) -> list[PeerCandidate]:
    high_cardinality_variables = high_cardinality_variables or set()
    base_variables = [var for var in variables if not _is_engine_derived_peer_variable(var)]
    if mandatory:
        base_variables = list(dedupe([*mandatory, *[var for var in base_variables if var not in mandatory]]))
    feature_buckets = [var for var in variables if _is_feature_bucket_variable(var)]
    exposure_buckets = [var for var in variables if _is_exposure_bucket_variable(var)]
    behavior_components = [var for var in BEHAVIOR_BUCKET_COLUMNS if var in variables]
    has_behavior_cluster = "behavior_cluster" in variables

    candidates: list[tuple[str, ...]] = []
    base_combos = _limited_combinations(base_variables, min(3, rules.max_variables_per_peer))
    if mandatory:
        mandatory_set = set(mandatory)
        base_combos = [combo for combo in base_combos if mandatory_set.issubset(combo)]
    if not base_combos and optional:
        base_combos = [(var,) for var in optional if not _is_engine_derived_peer_variable(var)]

    for combo in base_combos:
        _append_if_valid(candidates, combo, rules.max_variables_per_peer)

    behavior_sets: list[tuple[str, ...]] = []
    if len(behavior_components) >= 2:
        behavior_sets.append(tuple(behavior_components[:2]))
    if behavior_components:
        behavior_sets.append((behavior_components[0],))
    if has_behavior_cluster:
        behavior_sets.append(("behavior_cluster",))

    primary_bases = base_combos or [tuple()]
    derived_bases = [
        base
        for base in primary_bases
        if len(base) <= 2 and not any(var in high_cardinality_variables for var in base)
    ] or [tuple()]
    for base in derived_bases:
        for behavior_set in behavior_sets:
            _append_if_valid(candidates, tuple([*base, *behavior_set]), rules.max_variables_per_peer)

    for bucket in feature_buckets:
        for base in derived_bases:
            _append_if_valid(candidates, tuple([*base, bucket]), rules.max_variables_per_peer)
            for exposure in exposure_buckets:
                _append_if_valid(candidates, tuple([*base, bucket, exposure]), rules.max_variables_per_peer)

    for exposure in exposure_buckets:
        for base in derived_bases:
            _append_if_valid(candidates, tuple([*base, exposure]), rules.max_variables_per_peer)

    for fallback in rules.fallback_variables:
        if fallback in variables:
            _append_if_valid(candidates, (fallback,), rules.max_variables_per_peer)

    for bucket in feature_buckets:
        _append_if_valid(candidates, (bucket,), rules.max_variables_per_peer)
    for behavior_set in behavior_sets:
        _append_if_valid(candidates, behavior_set, rules.max_variables_per_peer)

    if rules.include_global:
        candidates.append(tuple())

    return _sort_and_format_candidates(candidates, variables)


def _high_cardinality_peer_variables(
    frame: list[str] | pd.Index | pd.DataFrame,
    variables: list[str],
    max_cardinality: int,
) -> set[str]:
    if not isinstance(frame, pd.DataFrame):
        return set()
    out: set[str] = set()
    for variable in variables:
        if variable not in frame.columns:
            continue
        unique_count = int(frame[variable].nunique(dropna=True))
        if unique_count > int(max_cardinality):
            out.add(variable)
    return out


def _validated_explicit_candidates(
    raw_candidates: tuple[PeerCandidate, ...],
    variables: list[str],
    include_global: bool,
) -> list[PeerCandidate]:
    available = set(variables)
    out: list[PeerCandidate] = []
    for candidate in raw_candidates:
        if all(col in available for col in candidate.columns):
            out.append(candidate)
    if include_global and not any(not candidate.columns for candidate in out):
        out.append(PeerCandidate(name="global", columns=tuple()))
    return out


def _sort_and_format_candidates(raw_candidates: list[tuple[str, ...]], priority_variables: list[str]) -> list[PeerCandidate]:
    rank = {var: idx for idx, var in enumerate(priority_variables)}
    unique = list(dict.fromkeys(tuple(candidate) for candidate in raw_candidates))

    def sort_key(candidate: tuple[str, ...]) -> tuple[int, int, tuple[int, ...]]:
        ranks = tuple(rank.get(var, 999) for var in candidate)
        return (-len(candidate), sum(ranks), ranks)

    return [PeerCandidate(name=peer_level_name(candidate), columns=candidate) for candidate in sorted(unique, key=sort_key)]


def limit_candidates(candidates: list[PeerCandidate], rules: PeerSelectionConfig) -> list[PeerCandidate]:
    limit = int(rules.max_candidate_count or 0)
    if limit <= 0 or len(candidates) <= limit:
        return candidates
    limited = candidates[:limit]
    if rules.include_global and not any(not candidate.columns for candidate in limited):
        global_candidate = next((candidate for candidate in candidates if not candidate.columns), PeerCandidate("global", tuple()))
        if limited:
            limited[-1] = global_candidate
        else:
            limited.append(global_candidate)
    return limited


def support_value(row: pd.Series, col: str) -> float:
    value = row.get(col, np.nan)
    if pd.isna(value):
        return 0.0
    return float(value)


def support_mask(
    candidate_frame: pd.DataFrame,
    candidate: PeerCandidate,
    thresholds: PeerSupportThresholds,
    blocked_values: Mapping[str, tuple[str, ...]] | None = None,
) -> pd.Series:
    mask = (
        candidate_frame["valid_main_metric_for_model"]
        & candidate_frame["hist_n"].ge(thresholds.min_history_rows)
        & candidate_frame["moy_n"].ge(thresholds.min_season_rows)
        & candidate_frame["recent_n"].ge(thresholds.min_recent_rows)
        & candidate_frame["current_n"].ge(thresholds.min_current_rows)
        & candidate_frame["peer_distribution_quality_score"].ge(thresholds.min_distribution_score)
    )
    for column, values in dict(blocked_values or DEFAULT_BLOCKED_VALUES).items():
        if column in candidate.columns and column in candidate_frame.columns:
            mask = mask & ~candidate_frame[column].astype(str).isin(values)
    generic_blocked = tuple(dict(blocked_values or DEFAULT_BLOCKED_VALUES).get("feature_bucket", ()))
    for column in candidate.columns:
        if column.startswith("feature_bucket_") and column in candidate_frame.columns and generic_blocked:
            mask = mask & ~candidate_frame[column].astype(str).isin(generic_blocked)
    return mask


def support_failure_summary(
    row: pd.Series,
    candidate: PeerCandidate,
    thresholds: PeerSupportThresholds,
    blocked_values: Mapping[str, tuple[str, ...]] | None = None,
) -> str:
    checks = [
        ("hist", "hist_n", thresholds.min_history_rows),
        ("season", "moy_n", thresholds.min_season_rows),
        ("recent", "recent_n", thresholds.min_recent_rows),
        ("current", "current_n", thresholds.min_current_rows),
    ]
    failed = [
        f"{label}={support_value(row, col):.0f}<{threshold}"
        for label, col, threshold in checks
        if support_value(row, col) < threshold
    ]
    for column, values in dict(blocked_values or DEFAULT_BLOCKED_VALUES).items():
        if column in candidate.columns and str(row.get(column, "")) in values:
            failed.append(f"{column}_missing")
    generic_blocked = tuple(dict(blocked_values or DEFAULT_BLOCKED_VALUES).get("feature_bucket", ()))
    for column in candidate.columns:
        if column.startswith("feature_bucket_") and str(row.get(column, "")) in generic_blocked:
            failed.append(f"{column}_missing")
    distribution_score = support_value(row, "peer_distribution_quality_score")
    if distribution_score < thresholds.min_distribution_score:
        failed.append(f"distribution_score={distribution_score:.1f}<{thresholds.min_distribution_score:.0f}")
    if not bool(row.get("valid_main_metric_for_model", False)):
        failed.append("invalid_main_metric")
    if not failed:
        failed.append("support_ok")
    return f"{candidate.name}: " + ", ".join(failed)


def specificity_score(candidate: PeerCandidate, config: PeerSelectionConfig | None = None) -> float:
    rules = config or PeerSelectionConfig()
    variables = list(dedupe(rules.priority_variables or DEFAULT_PREFERRED_VARIABLES))
    if not candidate.columns:
        return 0.25
    rank = {var: idx for idx, var in enumerate(variables)}
    max_rank = max(len(variables) - 1, 1)
    depth = min(len(candidate.columns) / max(len(variables), 1), 1.0)
    priority = np.mean([1.0 - (rank.get(var, max_rank) / max_rank) for var in candidate.columns])
    return float(np.clip(0.25 + 0.55 * depth + 0.20 * priority, 0.25, 1.0))


def representability_score(
    row: pd.Series,
    candidate: PeerCandidate,
    thresholds: PeerSupportThresholds,
    config: PeerSelectionConfig | None = None,
) -> float:
    hist_support = min(support_value(row, "hist_n") / max(thresholds.strong_history_rows, 1), 1.0)
    season_support = min(support_value(row, "moy_n") / max(thresholds.strong_season_rows, 1), 1.0)
    recent_support = min(support_value(row, "recent_n") / max(thresholds.strong_recent_rows, 1), 1.0)
    current_support = min(support_value(row, "current_n") / max(thresholds.strong_current_rows, 1), 1.0)
    distribution_quality = min(max(support_value(row, "peer_distribution_quality_score"), 0.0) / 100.0, 1.0)
    base_support = (
        0.26 * hist_support
        + 0.15 * season_support
        + 0.21 * recent_support
        + 0.26 * current_support
        + 0.12 * specificity_score(candidate, config)
    )
    distribution_factor = 0.50 + 0.50 * distribution_quality
    return float(100.0 * base_support * distribution_factor)


def _numeric_array(frame: pd.DataFrame, col: str, default: float = 0.0) -> np.ndarray:
    if col not in frame.columns:
        return np.full(len(frame), default, dtype=float)
    return pd.to_numeric(frame[col], errors="coerce").fillna(default).to_numpy(dtype=float)


def representability_score_frame(
    frame: pd.DataFrame,
    candidate: PeerCandidate,
    thresholds: PeerSupportThresholds,
    config: PeerSelectionConfig | None = None,
) -> np.ndarray:
    hist_support = np.minimum(_numeric_array(frame, "hist_n") / max(thresholds.strong_history_rows, 1), 1.0)
    season_support = np.minimum(_numeric_array(frame, "moy_n") / max(thresholds.strong_season_rows, 1), 1.0)
    recent_support = np.minimum(_numeric_array(frame, "recent_n") / max(thresholds.strong_recent_rows, 1), 1.0)
    current_support = np.minimum(_numeric_array(frame, "current_n") / max(thresholds.strong_current_rows, 1), 1.0)
    distribution_quality = np.minimum(np.maximum(_numeric_array(frame, "peer_distribution_quality_score"), 0.0) / 100.0, 1.0)
    specificity = specificity_score(candidate, config)
    base_support = (
        0.26 * hist_support
        + 0.15 * season_support
        + 0.21 * recent_support
        + 0.26 * current_support
        + 0.12 * specificity
    )
    distribution_factor = 0.50 + 0.50 * distribution_quality
    return 100.0 * base_support * distribution_factor


def support_strength_score(row: pd.Series, thresholds: PeerSupportThresholds) -> float:
    hist_support = min(support_value(row, "hist_n") / max(thresholds.strong_history_rows, 1), 1.0)
    season_support = min(support_value(row, "moy_n") / max(thresholds.strong_season_rows, 1), 1.0)
    recent_support = min(support_value(row, "recent_n") / max(thresholds.strong_recent_rows, 1), 1.0)
    current_support = min(support_value(row, "current_n") / max(thresholds.strong_current_rows, 1), 1.0)
    return float(100.0 * (0.30 * hist_support + 0.20 * season_support + 0.25 * recent_support + 0.25 * current_support))


def support_strength_score_frame(frame: pd.DataFrame, thresholds: PeerSupportThresholds) -> np.ndarray:
    hist_support = np.minimum(_numeric_array(frame, "hist_n") / max(thresholds.strong_history_rows, 1), 1.0)
    season_support = np.minimum(_numeric_array(frame, "moy_n") / max(thresholds.strong_season_rows, 1), 1.0)
    recent_support = np.minimum(_numeric_array(frame, "recent_n") / max(thresholds.strong_recent_rows, 1), 1.0)
    current_support = np.minimum(_numeric_array(frame, "current_n") / max(thresholds.strong_current_rows, 1), 1.0)
    return 100.0 * (0.30 * hist_support + 0.20 * season_support + 0.25 * recent_support + 0.25 * current_support)


def stability_score(row: pd.Series) -> float:
    hist_median = row.get("hist_median", np.nan)
    recent_median = row.get("recent_median", np.nan)
    hist_mad = support_value(row, "hist_mad")
    recent_mad = support_value(row, "recent_mad")
    if pd.isna(hist_median) or pd.isna(recent_median):
        return 50.0

    scale = max(hist_mad, recent_mad, 0.10)
    median_shift = abs(float(recent_median) - float(hist_median)) / scale
    if hist_mad <= 0 or recent_mad <= 0:
        scale_shift = 0.50
    else:
        scale_shift = abs(float(np.log(max(recent_mad, 1e-6) / max(hist_mad, 1e-6))))

    penalty = 0.65 * min(median_shift / 3.0, 1.0) + 0.35 * min(scale_shift / 1.5, 1.0)
    return float(np.clip(100.0 * (1.0 - penalty), 0.0, 100.0))


def stability_score_frame(frame: pd.DataFrame) -> np.ndarray:
    hist_median = pd.to_numeric(frame.get("hist_median", pd.Series(np.nan, index=frame.index)), errors="coerce").to_numpy(dtype=float)
    recent_median = pd.to_numeric(frame.get("recent_median", pd.Series(np.nan, index=frame.index)), errors="coerce").to_numpy(dtype=float)
    hist_mad = _numeric_array(frame, "hist_mad")
    recent_mad = _numeric_array(frame, "recent_mad")
    scale = np.maximum.reduce([hist_mad, recent_mad, np.full(len(frame), 0.10, dtype=float)])
    median_shift = np.abs(recent_median - hist_median) / scale
    scale_shift = np.where(
        (hist_mad <= 0) | (recent_mad <= 0),
        0.50,
        np.abs(np.log(np.maximum(recent_mad, 1e-6) / np.maximum(hist_mad, 1e-6))),
    )
    penalty = 0.65 * np.minimum(median_shift / 3.0, 1.0) + 0.35 * np.minimum(scale_shift / 1.5, 1.0)
    out = np.clip(100.0 * (1.0 - penalty), 0.0, 100.0)
    out[np.isnan(hist_median) | np.isnan(recent_median)] = 50.0
    return out


def objective_weights(config: PeerSelectionConfig | None = None) -> dict[str, float]:
    raw = dict((config.objective_weights if config else None) or DEFAULT_OBJECTIVE_WEIGHTS)
    positive = {key: max(float(value), 0.0) for key, value in raw.items()}
    total = sum(positive.values())
    if total <= 0:
        return DEFAULT_OBJECTIVE_WEIGHTS.copy()
    return {key: value / total for key, value in positive.items()}


def objective_components(
    row: pd.Series,
    candidate: PeerCandidate,
    thresholds: PeerSupportThresholds,
    config: PeerSelectionConfig | None = None,
) -> dict[str, float]:
    calibration_value = row.get("peer_calibration_score", np.nan)
    calibration = 50.0 if pd.isna(calibration_value) else float(calibration_value)
    return {
        "representability": representability_score(row, candidate, thresholds, config),
        "distribution": min(max(support_value(row, "peer_distribution_quality_score"), 0.0), 100.0),
        "calibration": min(max(calibration, 0.0), 100.0),
        "stability": stability_score(row),
        "specificity": 100.0 * specificity_score(candidate, config),
        "support": support_strength_score(row, thresholds),
    }


def objective_score(
    row: pd.Series,
    candidate: PeerCandidate,
    thresholds: PeerSupportThresholds,
    config: PeerSelectionConfig | None = None,
) -> float:
    components = objective_components(row, candidate, thresholds, config)
    weights = objective_weights(config)
    return float(sum(weights.get(key, 0.0) * value for key, value in components.items()))


def objective_score_frame(
    frame: pd.DataFrame,
    candidate: PeerCandidate,
    thresholds: PeerSupportThresholds,
    config: PeerSelectionConfig | None = None,
) -> np.ndarray:
    weights = objective_weights(config)
    representability = representability_score_frame(frame, candidate, thresholds, config)
    distribution = np.minimum(np.maximum(_numeric_array(frame, "peer_distribution_quality_score"), 0.0), 100.0)
    calibration = np.minimum(np.maximum(_numeric_array(frame, "peer_calibration_score", 50.0), 0.0), 100.0)
    stability = stability_score_frame(frame)
    specificity = np.full(len(frame), 100.0 * specificity_score(candidate, config), dtype=float)
    support = support_strength_score_frame(frame, thresholds)
    return (
        weights.get("representability", 0.0) * representability
        + weights.get("distribution", 0.0) * distribution
        + weights.get("calibration", 0.0) * calibration
        + weights.get("stability", 0.0) * stability
        + weights.get("specificity", 0.0) * specificity
        + weights.get("support", 0.0) * support
    )


def representability_status(score: float, candidate: PeerCandidate, config: PeerSelectionConfig | None = None) -> str:
    if not candidate.columns or len(candidate.columns) == 1:
        return "COARSE_PEER_REVIEW"
    if specificity_score(candidate, config) < 0.45:
        return "COARSE_PEER_REVIEW"
    if score >= 80:
        return "STRONG_PEER_REPRESENTATION"
    if score >= 60:
        return "GOOD_PEER_REPRESENTATION"
    if score >= 40:
        return "MEDIUM_PEER_REPRESENTATION"
    return "WEAK_PEER_REPRESENTATION"


def representability_status_series(
    scores: pd.Series | np.ndarray,
    candidate: PeerCandidate,
    config: PeerSelectionConfig | None = None,
    distribution_scores: pd.Series | np.ndarray | None = None,
) -> np.ndarray:
    values = np.asarray(scores, dtype=float)
    if not candidate.columns or len(candidate.columns) == 1 or specificity_score(candidate, config) < 0.45:
        return np.full(len(values), "COARSE_PEER_REVIEW", dtype=object)
    statuses = np.select(
        [values >= 80.0, values >= 60.0, values >= 40.0],
        ["STRONG_PEER_REPRESENTATION", "GOOD_PEER_REPRESENTATION", "MEDIUM_PEER_REPRESENTATION"],
        default="WEAK_PEER_REPRESENTATION",
    ).astype(object)
    if distribution_scores is not None:
        distribution = np.asarray(distribution_scores, dtype=float)
        limited = np.isfinite(distribution) & (distribution < 60.0) & (values >= 40.0)
        statuses[limited] = "LIMITED_WIDE_PEER_REVIEW"
    return statuses


def selection_reason(row: pd.Series, candidate: PeerCandidate, prior_attempts: list[str]) -> str:
    selected = (
        f"Secilen peer={candidate.name}; kolonlar={peer_columns_display(candidate.columns)}; "
        f"destek hist={support_value(row, 'hist_n'):.0f}, season={support_value(row, 'moy_n'):.0f}, "
        f"recent={support_value(row, 'recent_n'):.0f}, current={support_value(row, 'current_n'):.0f}; "
        f"dagilim={row.get('peer_distribution_status')}, "
        f"dagilim_skoru={support_value(row, 'peer_distribution_quality_score'):.1f}, "
        f"tail={support_value(row, 'peer_tail_rate'):.3f}, "
        f"kalibrasyon={support_value(row, 'peer_calibration_score'):.1f}, "
        f"objective={support_value(row, 'peer_objective_score'):.1f}."
    )
    if not prior_attempts:
        return selected + " Bu aday destek ve kalite esiklerini gecti."
    return selected + " Destek gecmeyen adaylar: " + " | ".join(prior_attempts[:4])
