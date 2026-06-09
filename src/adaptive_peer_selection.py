from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Mapping

import numpy as np
import pandas as pd


DEFAULT_BLOCKED_VALUES: dict[str, tuple[str, ...]] = {
    "behavior_cluster": ("behavior_unknown", "", "nan", "None"),
    "turnover_bucket": ("turnover_unknown", "", "nan", "None"),
}

DEFAULT_OBJECTIVE_WEIGHTS: dict[str, float] = {
    "representability": 0.25,
    "distribution": 0.20,
    "stability": 0.15,
    "specificity": 0.30,
    "support": 0.10,
}

DEFAULT_TECHNICAL_EXCLUSIONS = {
    "customer_id",
    "invoice_month",
    "calendar_month",
    "month_ord",
    "month_of_year",
    "bill_amount",
    "log_bill",
    "turnover_amt",
    "turnover_for_model",
    "log_turnover",
    "bill_to_turnover_log_ratio",
    "bill_to_turnover_ratio",
    "valid_bill_for_model",
    "negative_bill_flag",
    "zero_bill_flag",
    "source_row_count",
    "customer_obs_count_total",
    "previous_month_ord",
    "month_gap_from_previous",
    "active_subscriber",
    "active_subscriber_missing_flag",
    "turnover_positive_flag",
    "behavior_history_n",
    "behavior_level_bucket",
    "behavior_volatility_bucket",
    "behavior_trend_bucket",
    "behavior_median_bill",
    "behavior_volatility_log",
    "behavior_trend_slope",
}

DEFAULT_PREFERRED_VARIABLES = (
    "customer_segment",
    "turnover_bucket",
    "sector",
    "branch_id",
    "active_subscriber_bucket",
    "behavior_cluster",
)

VARIABLE_NAME_ALIASES = {
    "customer_segment": "segment",
    "turnover_bucket": "turnover",
    "sector": "sector",
    "active_subscriber_bucket": "active",
    "branch_id": "branch",
    "behavior_cluster": "behavior",
    "_global_key": "global",
}


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
    min_distribution_score: float = 35.0
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
    mandatory_variables: tuple[str, ...] = ("customer_segment",)
    fallback_variables: tuple[str, ...] = ("sector", "turnover_bucket", "active_subscriber_bucket")
    explicit_levels: tuple[PeerCandidate, ...] = ()
    include_global: bool = True
    max_variables_per_peer: int = 6
    candidate_strategy: str = "priority_path"
    selection_mode: str = "objective"
    objective_weights: Mapping[str, float] | None = None
    excluded_variables: tuple[str, ...] = ()
    max_inferred_cardinality: int = 250
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
            excluded_variables=tuple(str(item) for item in values.get("exclude_variables", values.get("excluded_variables", ()))),
            max_inferred_cardinality=int(values.get("max_inferred_cardinality", cls.max_inferred_cardinality)),
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
        excluded_variables=dedupe([*config.excluded_variables, *[str(item) for item in extra_exclusions]]),
        max_inferred_cardinality=config.max_inferred_cardinality,
        blocked_values=config.blocked_values,
    )


def peer_level_name(columns: tuple[str, ...] | list[str]) -> str:
    if not columns:
        return "global"
    return "_".join(VARIABLE_NAME_ALIASES.get(col, col) for col in columns)


def available_peer_variables(frame: list[str] | pd.Index | pd.DataFrame, config: PeerSelectionConfig) -> tuple[str, ...]:
    columns = set(str(col) for col in (frame.columns if isinstance(frame, pd.DataFrame) else frame))
    if config.priority_variables:
        return tuple(var for var in dedupe(config.priority_variables) if var in columns and var not in config.excluded_variables)
    return infer_peer_variables(frame, config)


def infer_peer_variables(frame: list[str] | pd.Index | pd.DataFrame, config: PeerSelectionConfig) -> tuple[str, ...]:
    columns = list(frame.columns if isinstance(frame, pd.DataFrame) else frame)
    excluded = set(DEFAULT_TECHNICAL_EXCLUSIONS) | set(config.excluded_variables)
    preferred = [col for col in DEFAULT_PREFERRED_VARIABLES if col in columns and col not in excluded]
    extras: list[str] = []
    for column in columns:
        if column in excluded or column in preferred or column.startswith("_"):
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
            if not is_dimension or unique_count <= 1:
                continue
        extras.append(str(column))
    return dedupe([*preferred, *extras])


def build_adaptive_peer_candidates(
    frame_columns: list[str] | pd.Index | pd.DataFrame,
    has_turnover_signal: bool,
    config: PeerSelectionConfig | None = None,
) -> list[PeerCandidate]:
    rules = config or PeerSelectionConfig()
    variables = list(available_peer_variables(frame_columns, rules))
    if not has_turnover_signal:
        variables = [var for var in variables if var != "turnover_bucket"]

    if rules.explicit_levels:
        return _validated_explicit_candidates(rules.explicit_levels, variables, rules.include_global)

    mandatory = [var for var in rules.mandatory_variables if var in variables]
    optional = [var for var in variables if var not in mandatory]
    if rules.candidate_strategy == "all_combinations":
        return _build_all_combination_candidates(variables, mandatory, optional, rules)

    return _build_priority_path_candidates(variables, mandatory, optional, rules)


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
        candidate_frame["valid_bill_for_model"]
        & candidate_frame["hist_n"].ge(thresholds.min_history_rows)
        & candidate_frame["moy_n"].ge(thresholds.min_season_rows)
        & candidate_frame["recent_n"].ge(thresholds.min_recent_rows)
        & candidate_frame["current_n"].ge(thresholds.min_current_rows)
        & candidate_frame["peer_distribution_quality_score"].ge(thresholds.min_distribution_score)
    )
    for column, values in dict(blocked_values or DEFAULT_BLOCKED_VALUES).items():
        if column in candidate.columns and column in candidate_frame.columns:
            mask = mask & ~candidate_frame[column].astype(str).isin(values)
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
    distribution_score = support_value(row, "peer_distribution_quality_score")
    if distribution_score < thresholds.min_distribution_score:
        failed.append(f"distribution_score={distribution_score:.1f}<{thresholds.min_distribution_score:.0f}")
    if not bool(row.get("valid_bill_for_model", False)):
        failed.append("invalid_bill")
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
    return float(
        100.0
        * (
            0.22 * hist_support
            + 0.13 * season_support
            + 0.18 * recent_support
            + 0.22 * current_support
            + 0.10 * specificity_score(candidate, config)
            + 0.15 * distribution_quality
        )
    )


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
    return 100.0 * (
        0.22 * hist_support
        + 0.13 * season_support
        + 0.18 * recent_support
        + 0.22 * current_support
        + 0.10 * specificity
        + 0.15 * distribution_quality
    )


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
    return {
        "representability": representability_score(row, candidate, thresholds, config),
        "distribution": min(max(support_value(row, "peer_distribution_quality_score"), 0.0), 100.0),
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
    stability = stability_score_frame(frame)
    specificity = np.full(len(frame), 100.0 * specificity_score(candidate, config), dtype=float)
    support = support_strength_score_frame(frame, thresholds)
    return (
        weights.get("representability", 0.0) * representability
        + weights.get("distribution", 0.0) * distribution
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
) -> np.ndarray:
    values = np.asarray(scores, dtype=float)
    if not candidate.columns or len(candidate.columns) == 1 or specificity_score(candidate, config) < 0.45:
        return np.full(len(values), "COARSE_PEER_REVIEW", dtype=object)
    return np.select(
        [values >= 80.0, values >= 60.0, values >= 40.0],
        ["STRONG_PEER_REPRESENTATION", "GOOD_PEER_REPRESENTATION", "MEDIUM_PEER_REPRESENTATION"],
        default="WEAK_PEER_REPRESENTATION",
    ).astype(object)


def selection_reason(row: pd.Series, candidate: PeerCandidate, prior_attempts: list[str]) -> str:
    selected = (
        f"Secilen peer={candidate.name}; kolonlar={'+'.join(candidate.columns) if candidate.columns else 'global'}; "
        f"destek hist={support_value(row, 'hist_n'):.0f}, season={support_value(row, 'moy_n'):.0f}, "
        f"recent={support_value(row, 'recent_n'):.0f}, current={support_value(row, 'current_n'):.0f}; "
        f"dagilim={row.get('peer_distribution_status')}, "
        f"dagilim_skoru={support_value(row, 'peer_distribution_quality_score'):.1f}, "
        f"tail={support_value(row, 'peer_tail_rate'):.3f}, "
        f"objective={support_value(row, 'peer_objective_score'):.1f}."
    )
    if not prior_attempts:
        return selected + " Bu aday destek ve kalite esiklerini gecti."
    return selected + " Destek gecmeyen adaylar: " + " | ".join(prior_attempts[:4])
