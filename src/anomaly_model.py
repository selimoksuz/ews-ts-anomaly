from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

import adaptive_peer_selection as adaptive


COLUMN_ALIASES = {
    "branch_id": ["SUBE_KD"],
    "customer_id": ["MUSTERINO", "KVKK_CUST"],
    "customer_segment": ["SEGMENTAD"],
    "invoice_month": ["DONEM_AY"],
    "sector": ["REF_ALTFAALIYET"],
    "bill_amount": ["FATURA_TTR"],
    "turnover_amt": ["TURNOVER_AMT"],
    "active_subscriber": ["AKTIF_ABONE"],
}

MIN_HIST_ROWS = 120
MIN_MOY_ROWS = 15
MIN_RECENT_ROWS = 25
MIN_CURRENT_ROWS = 20
MIN_RATIO_ROWS = 50
MIN_SELF_HISTORY_ROWS = 2
MIN_CUSTOMER_TREND_ROWS = 6
MIN_CUSTOMER_SEASONAL_ROWS = 2
MIN_BEHAVIOR_HISTORY_ROWS = 6
MIN_CUSTOMER_RECENT_REGIME_ROWS = 3
MAX_CUSTOMER_RECENT_REGIME_RANGE_LOG = 0.35
MIN_PEER_DISTRIBUTION_SCORE = 35.0
MIN_LOG_SCALE = 0.20
Z_SCORE_CAP = 3.0

DEFAULT_SCORING_WEIGHTS: dict[str, Any] = {
    "customer_explainability": {
        "self_support": 0.25,
        "recent_12_coverage": 0.25,
        "gap_quality": 0.20,
        "trend_available": 0.20,
        "seasonal_available": 0.10,
    },
    "customer_final_weight": {
        "buckets": [
            {"min_score": 75, "weight": 0.80},
            {"min_score": 50, "weight": 0.65},
            {"min_score": 25, "weight": 0.35},
        ],
        "default_weight": 0.15,
    },
    "peer_quality_factor": {
        "enabled": True,
        "source": "peer_distribution_quality_score",
        "min_factor": 0.35,
        "max_factor": 1.00,
    },
    "confidence": {
        "data_gap_penalty_weight": 0.25,
        "data_gap_penalty_cap": 25.0,
        "distribution_penalty_floor": 60.0,
        "distribution_penalty_weight": 0.35,
        "no_self_history_cap": 75.0,
        "coarse_peer_cap": 65.0,
    },
}

DEFAULT_SCORE_AGGREGATION: dict[str, Any] = {
    "method": "directional_empirical_evidence",
    "family_combination": "simes",
    "max_signals_per_family": 3,
    "min_abs_z_for_evidence": 0.50,
    "min_p_value": 0.001,
    "evidence_thresholds": {
        "strong_signal_p": 0.01,
        "moderate_signal_p": 0.05,
        "conflict_signal_p": 0.05,
    },
    "label_guardrails": {
        "enabled": True,
        "min_watch_abs_z": 1.50,
        "min_high_abs_z": 2.50,
        "min_watch_log_effect": 0.14,
        "min_high_log_effect": 0.26,
    },
    "customer_reliability": {
        "strong_min_prior_n": 6,
        "strong_min_coverage_12m": 0.50,
        "strong_max_gap_months": 1,
        "partial_min_prior_n": 2,
    },
    "peer_reliability": {
        "min_peer_objective_score": 60.0,
        "min_peer_distribution_score": 35.0,
        "coarse_peer_review_only": True,
    },
    "recent_regime": {
        "enabled": True,
        "min_recent_months": MIN_CUSTOMER_RECENT_REGIME_ROWS,
        "max_recent_range_log": MAX_CUSTOMER_RECENT_REGIME_RANGE_LOG,
    },
    "challenger_models": {
        "enabled": True,
        "methods": ["pca", "isolation_forest", "lof"],
        "random_state": 42,
        "min_rows": 200,
        "pca_variance_to_keep": 0.80,
        "pca_max_components": 6,
        "lof_neighbors": 35,
        "flag_threshold": 95.0,
    },
    "confidence": {
        "customer_driver_floor": 55.0,
        "peer_driver_floor": 45.0,
        "combined_driver_bonus": 8.0,
        "same_direction_bonus": 5.0,
        "conflict_penalty": 20.0,
        "data_gap_penalty_weight": 0.25,
        "data_gap_penalty_cap": 25.0,
        "distribution_penalty_floor": 60.0,
        "distribution_penalty_weight": 0.35,
        "no_self_history_cap": 75.0,
        "coarse_peer_cap": 65.0,
    },
}

SIGNAL_DEFINITIONS: tuple[dict[str, str], ...] = (
    {"name": "self_history", "family": "customer", "z_col": "self_history_z", "score_col": "self_history_score"},
    {"name": "customer_trend", "family": "customer", "z_col": "customer_trend_z", "score_col": "customer_trend_score"},
    {
        "name": "customer_seasonal",
        "family": "customer",
        "z_col": "customer_seasonal_z",
        "score_col": "customer_seasonal_score",
    },
    {
        "name": "customer_recent_regime",
        "family": "customer",
        "z_col": "customer_recent_regime_z",
        "score_col": "customer_recent_regime_score",
    },
    {"name": "historical_peer", "family": "peer", "z_col": "historical_peer_z", "score_col": "historical_peer_score"},
    {"name": "current_peer", "family": "peer", "z_col": "current_peer_z", "score_col": "current_peer_score"},
    {"name": "peer_trend", "family": "peer", "z_col": "peer_trend_z", "score_col": "peer_trend_score"},
    {
        "name": "turnover_intensity",
        "family": "peer",
        "z_col": "turnover_intensity_z",
        "score_col": "turnover_intensity_score",
    },
)

DEFAULT_DERIVED_FEATURES: dict[str, Any] = {
    "feature_ratio": {
        "enabled": "auto",
        "numerator": "bill_amount",
        "denominator": "turnover_amt",
        "use_as_peer_variable": True,
        "use_as_anomaly_signal": True,
        "quality_gate": {
            "enabled": True,
            "max_denominator_missing_or_zero_rate": 0.50,
            "min_monthly_valid_coverage": 0.50,
            "min_peer_ratio_rows": MIN_RATIO_ROWS,
            "min_peer_ratio_mad": 0.001,
        },
    },
    "behavior_peer": {
        "enabled": False,
        "use_as_peer_variable": True,
    },
}


@dataclass
class ModelRun:
    scoring_month: int
    train_rows: int
    scoring_rows: int
    scores: pd.DataFrame
    not_scored: pd.DataFrame
    thresholds: dict[str, float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Peer-based monthly single-metric anomaly scoring.")
    parser.add_argument("--input", default="data/raw/input.csv")
    parser.add_argument("--output-dir", default="outputs/development/peer_anomaly_exploration")
    parser.add_argument("--encoding", default="auto")
    parser.add_argument("--sep", default="auto")
    parser.add_argument("--scoring-month", default="last", help="last, YYYYMM, YYYYMMDD, or date-like value.")
    parser.add_argument("--backtest-months", type=int, default=4)
    parser.add_argument("--watch-top-rate", type=float, default=0.030)
    parser.add_argument("--high-top-rate", type=float, default=0.0075)
    return parser.parse_args()


def period_ord(period_int: int) -> int:
    year = int(period_int) // 100
    month = int(period_int) % 100
    if month < 1 or month > 12:
        raise ValueError(f"Invalid DONEM_AY value: {period_int}")
    return year * 12 + month


def period_label(period_int: int) -> str:
    year = int(period_int) // 100
    month = int(period_int) % 100
    return f"{year}-{month:02d}"


def normalize_scoring_month(value: Any, available_periods: pd.Series | list[Any] | None = None) -> int:
    if value is None:
        value = "last"
    text = str(value).strip()
    lowered = text.lower()
    if lowered in {"last", "latest", "max"}:
        if available_periods is None:
            raise ValueError("scoring_month='last' requires available periods.")
        periods = pd.to_numeric(pd.Series(available_periods), errors="coerce").dropna()
        if periods.empty:
            raise ValueError("scoring_month='last' could not be resolved because available periods are empty.")
        return int(periods.max())

    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]

    candidates: list[tuple[int, int]] = []
    year_first = re.fullmatch(r"(\d{4})[-_/.\s]?(\d{1,2})(?:[-_/.\s]?\d{1,2})?", text)
    if year_first:
        candidates.append((int(year_first.group(1)), int(year_first.group(2))))

    day_first = re.fullmatch(r"\d{1,2}[-_/.\s](\d{1,2})[-_/.\s](\d{4})", text)
    if day_first:
        candidates.append((int(day_first.group(2)), int(day_first.group(1))))

    month_first = re.fullmatch(r"(\d{1,2})[-_/.\s](\d{4})", text)
    if month_first:
        candidates.append((int(month_first.group(2)), int(month_first.group(1))))

    digits = re.sub(r"\D", "", text)
    if len(digits) == 6:
        candidates.append((int(digits[:4]), int(digits[4:6])))
    elif len(digits) >= 8:
        candidates.append((int(digits[:4]), int(digits[4:6])))
        candidates.append((int(digits[4:8]), int(digits[2:4])))

    parsed = pd.to_datetime(text, errors="coerce", dayfirst=False)
    if pd.notna(parsed):
        candidates.append((int(parsed.year), int(parsed.month)))
    for year, month in candidates:
        period = int(year * 100 + month)
        try:
            period_ord(period)
        except ValueError:
            continue
        return period

    raise ValueError(
        "Invalid scoring_month. Use 'last', YYYYMM, YYYYMMDD, YYYY-MM, YYYY-MM-DD, "
        "YYYY/MM/DD, or DD.MM.YYYY style values."
    )


def first_nonnull(series: pd.Series) -> Any:
    nonnull = series.dropna()
    if len(nonnull) == 0:
        return np.nan
    return nonnull.iloc[0]


def robust_mad(series: pd.Series, min_scale: float = MIN_LOG_SCALE) -> float:
    values = pd.Series(series).dropna().astype(float).to_numpy()
    if values.size == 0:
        return float("nan")
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    scale = 1.4826 * mad
    if not np.isfinite(scale):
        return float("nan")
    return float(max(scale, min_scale))


def robust_tail_rate(series: pd.Series, z_threshold: float = 3.0) -> float:
    values = pd.Series(series).dropna().astype(float).to_numpy()
    if values.size < 20:
        return float("nan")
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    scale = 1.4826 * mad
    if not np.isfinite(scale) or scale <= 0:
        scale = float(np.std(values))
    if not np.isfinite(scale) or scale <= 0:
        return float("nan")
    return float(np.mean(np.abs((values - median) / scale) > z_threshold))


def score_from_z(z: pd.Series | np.ndarray) -> np.ndarray:
    arr = np.asarray(z, dtype=float)
    return np.minimum(100.0, np.abs(arr) / Z_SCORE_CAP * 100.0)


def quantile_bucket(values: pd.Series, edges: list[float], labels: list[str], default: str) -> pd.Series:
    out = pd.Series(default, index=values.index, dtype=object)
    valid = values.notna()
    if not edges or not valid.any():
        return out
    bucket_no = np.searchsorted(np.asarray(edges), values.loc[valid].astype(float).to_numpy(), side="right")
    bucket_no = np.clip(bucket_no, 0, len(labels) - 1)
    out.loc[valid] = [labels[int(i)] for i in bucket_no]
    return out


def unique_quantile_edges(values: pd.Series, quantiles: list[float], min_count: int) -> list[float]:
    valid = values.dropna().astype(float)
    if len(valid) < min_count:
        return []
    edges = np.quantile(valid, quantiles)
    return [float(x) for x in np.unique(edges) if np.isfinite(x)]


def _deep_merge(base: Mapping[str, Any], overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {str(key): value for key, value in base.items()}
    if not overrides:
        return out
    for key, value in overrides.items():
        if isinstance(out.get(key), Mapping) and isinstance(value, Mapping):
            out[str(key)] = _deep_merge(out[key], value)
        else:
            out[str(key)] = value
    return out


def _auto_bool(value: Any, default: bool) -> bool:
    if isinstance(value, str) and value.strip().lower() == "auto":
        return default
    if value is None:
        return default
    return bool(value)


def normalize_derived_features_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    values = _deep_merge(DEFAULT_DERIVED_FEATURES, config)
    ratio = dict(values.get("feature_ratio", {}))
    behavior = dict(values.get("behavior_peer", {}))
    values["feature_ratio"] = ratio
    values["behavior_peer"] = behavior
    return values


def feature_ratio_settings(
    config: Mapping[str, Any] | None,
    cols: Mapping[str, str] | None = None,
    *,
    denominator_usable: bool = True,
) -> dict[str, Any]:
    def source_column(reference: str, default_role: str) -> str:
        if not cols:
            return reference
        lowered = str(reference).strip().lower()
        if lowered in {"main_feature", "target_variable", "amount_variable", "bill_amount"}:
            return str(cols.get("bill_amount", reference))
        if reference in cols:
            return str(cols[reference])
        if reference in set(cols.values()):
            return str(reference)
        return str(cols.get(default_role, reference))

    values = normalize_derived_features_config(config)
    ratio = dict(values.get("feature_ratio", {}))
    quality_gate = dict(ratio.get("quality_gate", {}))
    denominator_role = str(ratio.get("denominator", "turnover_amt"))
    numerator_role = str(ratio.get("numerator", "bill_amount"))
    has_denominator = bool(cols and (denominator_role in cols or denominator_role in set(cols.values())))
    enabled_default = has_denominator and bool(denominator_usable)
    enabled = _auto_bool(ratio.get("enabled", "auto"), enabled_default)
    signal_enabled = enabled and _auto_bool(ratio.get("use_as_anomaly_signal", True), True)
    peer_enabled = enabled and _auto_bool(ratio.get("use_as_peer_variable", True), True)
    return {
        "enabled": bool(enabled),
        "use_as_anomaly_signal": bool(signal_enabled),
        "use_as_peer_variable": bool(peer_enabled),
        "numerator_role": numerator_role,
        "denominator_role": denominator_role,
        "numerator_source_col": source_column(numerator_role, "bill_amount"),
        "denominator_source_col": source_column(denominator_role, "turnover_amt"),
        "quality_gate": quality_gate,
        "quality_gate_enabled": bool(quality_gate.get("enabled", True)),
    }


def behavior_peer_enabled(config: Mapping[str, Any] | None) -> bool:
    values = normalize_derived_features_config(config)
    behavior = dict(values.get("behavior_peer", {}))
    return bool(behavior.get("enabled", False)) and bool(behavior.get("use_as_peer_variable", True))


def robust_group_stats(frame: pd.DataFrame, key: list[str], value_col: str, prefix: str) -> pd.DataFrame:
    columns = key + [f"{prefix}_n", f"{prefix}_median", f"{prefix}_mad"]
    if len(frame) == 0:
        return pd.DataFrame(columns=columns)

    out = (
        frame.groupby(key, dropna=False)
        .agg(
            **{
                f"{prefix}_n": (value_col, "size"),
                f"{prefix}_median": (value_col, "median"),
            }
        )
        .reset_index()
    )
    deviations = frame[key + [value_col]].merge(out[key + [f"{prefix}_median"]], on=key, how="left")
    deviations["_abs_dev"] = (deviations[value_col].astype(float) - deviations[f"{prefix}_median"].astype(float)).abs()
    mad = (
        deviations.groupby(key, dropna=False)["_abs_dev"]
        .median()
        .reset_index()
        .rename(columns={"_abs_dev": f"{prefix}_mad"})
    )
    out = out.merge(mad, on=key, how="left")
    scale = 1.4826 * out[f"{prefix}_mad"].astype(float)
    out[f"{prefix}_mad"] = np.where(np.isfinite(scale) & (scale > 0), np.maximum(scale, MIN_LOG_SCALE), MIN_LOG_SCALE)
    return out[columns]


def trend_group_stats(frame: pd.DataFrame, key: list[str], prefix: str) -> pd.DataFrame:
    columns = key + [f"{prefix}_trend_n", f"{prefix}_trend_slope", f"{prefix}_trend_intercept"]
    if len(frame) == 0:
        return pd.DataFrame(columns=columns)

    valid = frame.loc[frame["month_ord"].notna() & frame["log_bill"].notna(), key + ["month_ord", "log_bill"]].copy()
    if len(valid) == 0:
        return pd.DataFrame(columns=columns)

    support = valid.groupby(key, dropna=False).size().reset_index(name=f"{prefix}_trend_n")
    monthly = (
        valid.groupby(key + ["month_ord"], dropna=False)["log_bill"]
        .median()
        .reset_index()
    )
    monthly["_x"] = monthly["month_ord"].astype(float)
    monthly["_y"] = monthly["log_bill"].astype(float)
    monthly["_xy"] = monthly["_x"] * monthly["_y"]
    monthly["_x2"] = monthly["_x"] * monthly["_x"]
    agg = (
        monthly.groupby(key, dropna=False)
        .agg(
            **{
                "_sum_x": ("_x", "sum"),
                "_sum_y": ("_y", "sum"),
                "_sum_xy": ("_xy", "sum"),
                "_sum_x2": ("_x2", "sum"),
                "_month_n": ("_y", "size"),
            }
        )
        .reset_index()
        .merge(support, on=key, how="left")
    )
    n = agg["_month_n"].astype(float)
    denom = agg["_sum_x2"] - (agg["_sum_x"] * agg["_sum_x"] / n)
    numer = agg["_sum_xy"] - (agg["_sum_x"] * agg["_sum_y"] / n)
    slope = np.where((n >= 2) & (denom > 0), numer / denom, np.nan)
    slope = np.clip(slope.astype(float), -1.0, 1.0)
    intercept = np.where(n >= 2, (agg["_sum_y"] / n) - slope * (agg["_sum_x"] / n), np.nan)
    agg[f"{prefix}_trend_slope"] = slope
    agg[f"{prefix}_trend_intercept"] = intercept
    return agg[columns]


def build_behavior_clusters(history: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "customer_id",
        "behavior_cluster",
        "behavior_history_n",
        "behavior_level_bucket",
        "behavior_volatility_bucket",
        "behavior_trend_bucket",
        "behavior_median_bill",
        "behavior_volatility_log",
        "behavior_trend_slope",
    ]
    valid = history.loc[history["valid_bill_for_model"] & history["log_bill"].notna()].copy()
    if len(valid) == 0 or valid["customer_id"].nunique(dropna=False) == len(valid):
        return pd.DataFrame(columns=columns)

    base = robust_group_stats(valid, ["customer_id"], "log_bill", "behavior")
    trend = trend_group_stats(valid, ["customer_id"], "behavior")
    out = base.merge(trend, on="customer_id", how="left")
    out = out.rename(
        columns={
            "behavior_n": "behavior_history_n",
            "behavior_median": "behavior_median_log",
            "behavior_mad": "behavior_volatility_log",
        }
    )
    eligible = out["behavior_history_n"].fillna(0).ge(MIN_BEHAVIOR_HISTORY_ROWS)

    level_edges = unique_quantile_edges(out.loc[eligible, "behavior_median_log"], [0.20, 0.40, 0.60, 0.80], 100)
    vol_edges = unique_quantile_edges(out.loc[eligible, "behavior_volatility_log"], [0.33, 0.66], 100)
    trend_edges = unique_quantile_edges(out.loc[eligible, "behavior_trend_slope"], [0.33, 0.66], 100)

    out["behavior_level_bucket"] = quantile_bucket(
        out["behavior_median_log"],
        level_edges,
        ["level_q1", "level_q2", "level_q3", "level_q4", "level_q5"],
        "level_unknown",
    )
    out["behavior_volatility_bucket"] = quantile_bucket(
        out["behavior_volatility_log"],
        vol_edges,
        ["vol_low", "vol_mid", "vol_high"],
        "vol_unknown",
    )
    out["behavior_trend_bucket"] = quantile_bucket(
        out["behavior_trend_slope"],
        trend_edges,
        ["trend_down", "trend_stable", "trend_up"],
        "trend_unknown",
    )
    out.loc[~eligible, ["behavior_level_bucket", "behavior_volatility_bucket", "behavior_trend_bucket"]] = [
        "level_unknown",
        "vol_unknown",
        "trend_unknown",
    ]
    out["behavior_cluster"] = np.where(
        eligible,
        out["behavior_level_bucket"].astype(str)
        + "|"
        + out["behavior_volatility_bucket"].astype(str)
        + "|"
        + out["behavior_trend_bucket"].astype(str),
        "behavior_unknown",
    )
    out["behavior_median_bill"] = np.expm1(out["behavior_median_log"])
    return out[columns]


def assign_behavior_clusters(frame: pd.DataFrame, behavior: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if len(behavior):
        out = out.merge(behavior, on="customer_id", how="left")
    else:
        for col in [
            "behavior_cluster",
            "behavior_history_n",
            "behavior_level_bucket",
            "behavior_volatility_bucket",
            "behavior_trend_bucket",
            "behavior_median_bill",
            "behavior_volatility_log",
            "behavior_trend_slope",
        ]:
            out[col] = np.nan
    out["behavior_cluster"] = out["behavior_cluster"].fillna("behavior_unknown")
    out["behavior_level_bucket"] = out["behavior_level_bucket"].fillna("level_unknown")
    out["behavior_volatility_bucket"] = out["behavior_volatility_bucket"].fillna("vol_unknown")
    out["behavior_trend_bucket"] = out["behavior_trend_bucket"].fillna("trend_unknown")
    return out


def peer_distribution_quality_score(skew: float, kurtosis: float, tail_rate: float) -> float:
    skew_penalty = min(abs(float(skew)) / 2.0, 1.0) if pd.notna(skew) else 0.50
    kurtosis_penalty = min(max(float(kurtosis), 0.0) / 6.0, 1.0) if pd.notna(kurtosis) else 0.50
    tail_penalty = min(max(float(tail_rate), 0.0) / 0.06, 1.0) if pd.notna(tail_rate) else 0.50
    score = 100.0 * (1.0 - (0.30 * skew_penalty + 0.25 * kurtosis_penalty + 0.45 * tail_penalty))
    return float(np.clip(score, 0.0, 100.0))


def peer_distribution_status(score: float) -> str:
    if pd.isna(score):
        return "PEER_DISTRIBUTION_UNKNOWN"
    if score >= 80:
        return "HOMOGENEOUS_PEER"
    if score >= 60:
        return "USABLE_HEAVY_TAIL_PEER"
    if score >= MIN_PEER_DISTRIBUTION_SCORE:
        return "HEAVY_TAIL_PEER_REVIEW"
    return "UNSTABLE_PEER_DISTRIBUTION"


def peer_distribution_stats(frame: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    columns = key + [
        "peer_distribution_n",
        "peer_log_ratio_skew",
        "peer_log_ratio_kurtosis",
        "peer_tail_rate",
        "peer_distribution_quality_score",
        "peer_distribution_status",
    ]
    valid = frame.loc[frame["valid_bill_for_model"] & frame["log_bill"].notna(), key + ["log_bill"]].copy()
    if len(valid) == 0:
        return pd.DataFrame(columns=columns)

    grouped = valid.groupby(key, dropna=False)["log_bill"]
    out = grouped.agg(
        peer_distribution_n="size",
        peer_log_ratio_skew="skew",
        peer_log_ratio_kurtosis=lambda x: x.kurt(),
        peer_tail_rate=robust_tail_rate,
    ).reset_index()
    out["peer_distribution_quality_score"] = [
        peer_distribution_quality_score(row["peer_log_ratio_skew"], row["peer_log_ratio_kurtosis"], row["peer_tail_rate"])
        for _, row in out.iterrows()
    ]
    out["peer_distribution_status"] = out["peer_distribution_quality_score"].map(peer_distribution_status)
    return out[columns]


def peer_calibration_stats(
    frame: pd.DataFrame,
    key: list[str],
    scoring_month: int,
    calibration_months: int = 6,
    min_prior_months: int = 3,
) -> pd.DataFrame:
    columns = key + [
        "peer_calibration_n",
        "peer_calibration_abs_residual_median",
        "peer_calibration_interval_coverage",
        "peer_calibration_false_alarm_rate",
        "peer_calibration_score",
    ]
    if len(frame) == 0:
        return pd.DataFrame(columns=columns)

    valid = frame.loc[frame["valid_bill_for_model"] & frame["log_bill"].notna(), key + ["month_ord", "month_of_year", "log_bill"]].copy()
    if len(valid) == 0:
        return pd.DataFrame(columns=columns)

    scoring_ord = period_ord(scoring_month)
    min_calibration_ord = scoring_ord - max(int(calibration_months), 1)
    base_frame = valid.loc[valid["month_ord"].lt(min_calibration_ord)].copy()
    calibration_frame = valid.loc[valid["month_ord"].between(min_calibration_ord, scoring_ord - 1)].copy()
    if len(base_frame) == 0 or len(calibration_frame) == 0:
        return pd.DataFrame(columns=columns)

    base_stats = robust_group_stats(base_frame, key, "log_bill", "cal_base")
    base_months = base_frame.groupby(key, dropna=False)["month_ord"].nunique().reset_index(name="cal_base_month_n")
    base_stats = base_stats.merge(base_months, on=key, how="left")
    base_stats = base_stats.loc[base_stats["cal_base_month_n"].fillna(0).ge(int(min_prior_months))]
    if len(base_stats) == 0:
        return pd.DataFrame(columns=columns)

    seasonal = (
        base_frame.groupby(key + ["month_of_year"], dropna=False)["log_bill"]
        .median()
        .reset_index(name="cal_seasonal_median")
    )
    monthly = (
        calibration_frame.groupby(key + ["month_ord", "month_of_year"], dropna=False)["log_bill"]
        .median()
        .reset_index(name="peer_month_log_median")
    )
    work = monthly.merge(
        base_stats[key + ["cal_base_median", "cal_base_mad"]],
        on=key,
        how="inner",
    )
    work = work.merge(seasonal, on=key + ["month_of_year"], how="left")
    seasonal_adjustment = work["cal_seasonal_median"].astype(float) - work["cal_base_median"].astype(float)
    work["_expected"] = work["cal_base_median"].astype(float) + seasonal_adjustment.fillna(0.0)
    work["_residual"] = (
        work["peer_month_log_median"].astype(float) - work["_expected"].astype(float)
    ) / np.maximum(work["cal_base_mad"].astype(float), MIN_LOG_SCALE)
    work["_abs_residual"] = work["_residual"].abs()
    work["_covered"] = work["_abs_residual"].le(3.0).astype(float)
    work["_false_alarm"] = work["_abs_residual"].ge(2.5).astype(float)
    if len(work) == 0:
        return pd.DataFrame(columns=columns)

    out = (
        work.groupby(key, dropna=False)
        .agg(
            peer_calibration_n=("_residual", "size"),
            peer_calibration_abs_residual_median=("_abs_residual", "median"),
            peer_calibration_interval_coverage=("_covered", "mean"),
            peer_calibration_false_alarm_rate=("_false_alarm", "mean"),
        )
        .reset_index()
    )
    residual_score = 1.0 - np.minimum(out["peer_calibration_abs_residual_median"].astype(float) / 3.0, 1.0)
    out["peer_calibration_score"] = 100.0 * (
        0.45 * residual_score
        + 0.35 * out["peer_calibration_interval_coverage"].astype(float)
        + 0.20 * (1.0 - out["peer_calibration_false_alarm_rate"].astype(float))
    )
    out["peer_calibration_score"] = out["peer_calibration_score"].clip(0.0, 100.0)
    return out[columns]


def detect_read_options(input_path: Path, encoding: str, sep: str) -> tuple[str, str]:
    encodings = ["utf-8", "utf-8-sig", "cp1254"] if encoding == "auto" else [encoding]
    seps = [",", ";"] if sep == "auto" else [sep]
    for enc in encodings:
        for candidate_sep in seps:
            try:
                sample = pd.read_csv(input_path, sep=candidate_sep, decimal=",", encoding=enc, nrows=5)
            except Exception:
                continue
            if len(sample.columns) >= 5:
                return enc, candidate_sep
    raise ValueError("Could not detect CSV encoding/separator.")


def resolve_columns(raw: pd.DataFrame, column_map: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    resolved: dict[str, str] = {}
    missing: list[str] = []
    source_map = column_map or {}
    use_alias_fallback = not bool(source_map)
    for logical, candidates in COLUMN_ALIASES.items():
        mapped_col = source_map.get(logical)
        if mapped_col:
            found = mapped_col if mapped_col in raw.columns else None
        else:
            found = next((col for col in candidates if col in raw.columns), None) if use_alias_fallback else None
        if found is None and logical in {"customer_id", "invoice_month", "bill_amount"}:
            missing.append(logical)
        elif found is not None:
            resolved[logical] = found
    return resolved, missing


def to_numeric_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    text = series.astype(str).str.strip()
    has_comma_decimal = text.str.contains(",", regex=False)
    normalized = text.where(~has_comma_decimal, text.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    normalized = normalized.replace({"": np.nan, "nan": np.nan, "None": np.nan, "NULL": np.nan})
    return pd.to_numeric(normalized, errors="coerce")


def prepare_source_frame(
    raw: pd.DataFrame,
    column_map: dict[str, str] | None = None,
    source_name: str | None = None,
    derived_features_config: Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cols, missing = resolve_columns(raw, column_map)
    if missing:
        raise ValueError(f"Missing required logical columns: {missing}")

    normalized = pd.DataFrame(
        {
            "branch_id": raw[cols["branch_id"]] if "branch_id" in cols else "UNKNOWN_BRANCH",
            "customer_id": raw[cols["customer_id"]].astype(str),
            "customer_segment": raw[cols["customer_segment"]] if "customer_segment" in cols else "UNKNOWN_SEGMENT",
            "invoice_month": to_numeric_series(raw[cols["invoice_month"]]).astype(int),
            "sector": raw[cols["sector"]] if "sector" in cols else "UNKNOWN_SECTOR",
            "bill_amount": to_numeric_series(raw[cols["bill_amount"]]).astype(float),
            "turnover_amt": (
                to_numeric_series(raw[cols["turnover_amt"]]).astype(float)
                if "turnover_amt" in cols
                else pd.Series(np.nan, index=raw.index, dtype=float)
            ),
        }
    )
    if "active_subscriber" in cols:
        normalized["active_subscriber_missing_flag"] = raw[cols["active_subscriber"]].isna()
        normalized["active_subscriber"] = to_numeric_series(raw[cols["active_subscriber"]]).fillna(1).astype(float)
    else:
        normalized["active_subscriber"] = 1.0
        normalized["active_subscriber_missing_flag"] = True

    source_columns = list(raw.columns)
    for column in source_columns:
        if column not in normalized.columns:
            normalized[column] = raw[column]

    duplicate_customer_month_rows = int(normalized.duplicated(["customer_id", "invoice_month"]).sum())
    if duplicate_customer_month_rows == 0:
        monthly = normalized.copy()
        monthly["source_row_count"] = 1
    else:
        reverse_map = {source_col: logical for logical, source_col in cols.items()}
        aggregations: dict[str, tuple[str, Any]] = {
            "branch_id": ("branch_id", first_nonnull),
            "customer_segment": ("customer_segment", first_nonnull),
            "sector": ("sector", first_nonnull),
            "active_subscriber": ("active_subscriber", "max"),
            "active_subscriber_missing_flag": ("active_subscriber_missing_flag", "max"),
            "bill_amount": ("bill_amount", "sum"),
            "turnover_amt": ("turnover_amt", first_nonnull),
            "source_row_count": ("bill_amount", "size"),
        }
        source_aggregation_by_logical = {
            "bill_amount": "sum",
            "active_subscriber": "max",
            "active_subscriber_missing_flag": "max",
        }
        for column in source_columns:
            if column in aggregations:
                continue
            logical = reverse_map.get(column)
            aggregations[column] = (column, source_aggregation_by_logical.get(logical, first_nonnull))
        monthly = (
            normalized.groupby(["customer_id", "invoice_month"], as_index=False)
            .agg(**aggregations)
            .copy()
        )

    monthly["month_ord"] = monthly["invoice_month"].map(period_ord).astype(int)
    monthly["month_of_year"] = monthly["invoice_month"].astype(int) % 100
    monthly["calendar_month"] = monthly["invoice_month"].map(period_label)
    monthly["bill_amount"] = monthly["bill_amount"].astype(float)
    monthly["turnover_amt"] = monthly["turnover_amt"].astype(float)
    monthly["active_subscriber_missing_flag"] = monthly["active_subscriber_missing_flag"].fillna(False).astype(bool)
    monthly["active_subscriber"] = monthly["active_subscriber"].fillna(1).astype(float)
    turnover_missing_rate = float(monthly["turnover_amt"].isna().mean())
    turnover_missing_or_zero_rate = float(monthly["turnover_amt"].fillna(0).le(0).mean())
    turnover_diff = (monthly["bill_amount"] - monthly["turnover_amt"]).abs()
    turnover_equal_bill_rate = float((turnover_diff <= 1e-6).mean())
    turnover_usable_for_model = "turnover_amt" in cols and turnover_missing_rate < 0.98 and turnover_equal_bill_rate < 0.98
    raw_ratio_config = dict(normalize_derived_features_config(derived_features_config).get("feature_ratio", {}))
    ratio_explicitly_enabled = str(raw_ratio_config.get("enabled", "auto")).strip().lower() in {"true", "1", "yes", "evet"}
    if ratio_explicitly_enabled and "turnover_amt" not in cols:
        raise ValueError(
            "model.derived_features.feature_ratio is enabled, but its denominator column could not be resolved."
        )
    if ratio_explicitly_enabled and not turnover_usable_for_model:
        raise ValueError(
            "model.derived_features.feature_ratio is enabled, but the denominator is missing, zero, "
            "or effectively identical to the main metric feature."
        )
    ratio_settings = feature_ratio_settings(
        derived_features_config,
        cols,
        denominator_usable=bool(turnover_usable_for_model),
    )
    ratio_enabled = bool(ratio_settings["enabled"])
    quality_gate = dict(ratio_settings.get("quality_gate", {}))
    gate_enabled = bool(ratio_settings.get("quality_gate_enabled", True))
    ratio_valid_coverage = float(monthly["turnover_amt"].gt(0).mean()) if "turnover_amt" in monthly else 0.0
    max_missing_or_zero = float(quality_gate.get("max_denominator_missing_or_zero_rate", 0.50))
    min_monthly_coverage = float(quality_gate.get("min_monthly_valid_coverage", 0.50))
    gate_reasons: list[str] = []
    if gate_enabled and ratio_enabled:
        if turnover_missing_or_zero_rate > max_missing_or_zero:
            gate_reasons.append(
                f"denominator_missing_or_zero_rate={turnover_missing_or_zero_rate:.3f}>{max_missing_or_zero:.3f}"
            )
        if ratio_valid_coverage < min_monthly_coverage:
            gate_reasons.append(f"monthly_valid_coverage={ratio_valid_coverage:.3f}<{min_monthly_coverage:.3f}")
    ratio_quality_gate_passed = bool(ratio_enabled and (not gate_enabled or not gate_reasons))
    ratio_settings["quality_gate_passed"] = ratio_quality_gate_passed
    ratio_settings["quality_gate_reasons"] = "; ".join(gate_reasons) if gate_reasons else "passed"
    ratio_settings["denominator_missing_or_zero_rate"] = turnover_missing_or_zero_rate
    ratio_settings["monthly_valid_coverage"] = ratio_valid_coverage
    ratio_settings["use_as_anomaly_signal_requested"] = bool(ratio_settings["use_as_anomaly_signal"])
    ratio_settings["use_as_anomaly_signal"] = bool(ratio_settings["use_as_anomaly_signal"] and ratio_quality_gate_passed)
    monthly["turnover_for_model"] = np.where(turnover_usable_for_model, monthly["turnover_amt"], np.nan)
    monthly["active_subscriber_bucket"] = pd.cut(
        monthly["active_subscriber"],
        bins=[-np.inf, 1, 2, 4, 9, np.inf],
        labels=["active_1", "active_2", "active_3_4", "active_5_9", "active_10_plus"],
    ).astype(str)
    monthly["_global_key"] = "ALL"

    monthly["valid_bill_for_model"] = monthly["bill_amount"].notna() & monthly["bill_amount"].ge(0)
    monthly["negative_bill_flag"] = monthly["bill_amount"].lt(0).fillna(False)
    monthly["zero_bill_flag"] = monthly["bill_amount"].eq(0).fillna(False)
    monthly["log_bill"] = np.where(monthly["valid_bill_for_model"], np.log1p(monthly["bill_amount"]), np.nan)
    monthly["turnover_positive_flag"] = monthly["turnover_for_model"].astype(float).gt(0)
    monthly["log_turnover"] = np.where(monthly["turnover_positive_flag"], np.log1p(monthly["turnover_for_model"]), np.nan)
    monthly["bill_to_turnover_log_ratio"] = np.where(ratio_enabled, monthly["log_bill"] - monthly["log_turnover"], np.nan)

    monthly = monthly.sort_values(["customer_id", "invoice_month"]).reset_index(drop=True)
    if monthly["customer_id"].nunique(dropna=False) == len(monthly):
        monthly["customer_obs_count_total"] = 1
        monthly["previous_month_ord"] = np.nan
        monthly["month_gap_from_previous"] = np.nan
    else:
        monthly["customer_obs_count_total"] = monthly.groupby("customer_id")["invoice_month"].transform("size")
        monthly["previous_month_ord"] = monthly.groupby("customer_id")["month_ord"].shift(1)
        monthly["month_gap_from_previous"] = monthly["month_ord"] - monthly["previous_month_ord"]

    profile = {
        "source_rows": int(len(raw)),
        "monthly_rows": int(len(monthly)),
        "duplicate_customer_month_rows": duplicate_customer_month_rows,
        "customer_count": int(monthly["customer_id"].nunique()),
        "customers_with_multiple_months": int((monthly.groupby("customer_id").size() >= 2).sum()),
        "period_min": int(monthly["invoice_month"].min()),
        "period_max": int(monthly["invoice_month"].max()),
        "period_count": int(monthly["invoice_month"].nunique()),
        "segment_count": int(monthly["customer_segment"].nunique()),
        "sector_count": int(monthly["sector"].nunique()),
        "branch_count": int(monthly["branch_id"].nunique()),
        "customer_id_column": cols["customer_id"],
        "input_column_map": {logical: source_col for logical, source_col in cols.items()},
        "source_columns": source_columns,
        "source_name": source_name,
        "detected_encoding": None,
        "detected_separator": None,
        "active_subscriber_present": "active_subscriber" in cols,
        "active_subscriber_missing_rate": float(monthly["active_subscriber_missing_flag"].mean()),
        "turnover_equal_bill_rate": turnover_equal_bill_rate,
        "turnover_usable_for_model": bool(turnover_usable_for_model),
        "turnover_missing_rate": turnover_missing_rate,
        "turnover_missing_or_zero_rate": turnover_missing_or_zero_rate,
        "derived_features": normalize_derived_features_config(derived_features_config),
        "feature_ratio": ratio_settings,
        "feature_ratio_enabled": bool(ratio_settings["enabled"]),
        "feature_ratio_signal_enabled": bool(ratio_settings["use_as_anomaly_signal"]),
        "feature_ratio_signal_requested": bool(ratio_settings["use_as_anomaly_signal_requested"]),
        "feature_ratio_quality_gate_passed": bool(ratio_settings["quality_gate_passed"]),
        "feature_ratio_quality_gate_reasons": str(ratio_settings["quality_gate_reasons"]),
        "feature_ratio_monthly_valid_coverage": ratio_valid_coverage,
        "feature_ratio_peer_enabled": bool(ratio_settings["use_as_peer_variable"]),
        "behavior_peer_enabled": behavior_peer_enabled(derived_features_config),
    }
    monthly.attrs["profile"] = profile
    monthly.attrs["derived_features"] = profile["derived_features"]
    monthly.attrs["feature_ratio"] = ratio_settings
    monthly.attrs["behavior_peer_enabled"] = profile["behavior_peer_enabled"]
    return monthly, profile


def read_source(
    input_path: Path,
    encoding: str,
    sep: str,
    column_map: dict[str, str] | None = None,
    derived_features_config: Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    detected_encoding, detected_sep = detect_read_options(input_path, encoding, sep)
    raw = pd.read_csv(input_path, sep=detected_sep, decimal=",", encoding=detected_encoding)
    monthly, profile = prepare_source_frame(
        raw,
        column_map=column_map,
        source_name=str(input_path),
        derived_features_config=derived_features_config,
    )
    profile["detected_encoding"] = detected_encoding
    profile["detected_separator"] = detected_sep
    return monthly, profile


def fit_turnover_edges(history: pd.DataFrame) -> list[float]:
    positive = history.loc[history["turnover_for_model"].astype(float).gt(0), "log_turnover"].dropna()
    if len(positive) < 100:
        return []
    edges = np.quantile(positive, [0.20, 0.40, 0.60, 0.80])
    return [float(x) for x in np.unique(edges) if np.isfinite(x)]


def assign_turnover_bucket(df: pd.DataFrame, edges: list[float]) -> pd.DataFrame:
    out = df.copy()
    out["turnover_bucket"] = "turnover_missing"
    turnover_source = out["turnover_for_model"].astype(float)
    out.loc[turnover_source.fillna(0).eq(0), "turnover_bucket"] = "turnover_zero"
    pos = turnover_source.gt(0)
    if edges:
        bucket_no = np.searchsorted(np.asarray(edges), out.loc[pos, "log_turnover"].to_numpy(), side="right") + 1
        out.loc[pos, "turnover_bucket"] = [f"turnover_q{int(i)}" for i in bucket_no]
    else:
        out.loc[pos, "turnover_bucket"] = "turnover_positive"
    return out


def group_key(cols: list[str]) -> list[str]:
    return cols if cols else ["_global_key"]


def filter_frame_to_scoring_keys(frame: pd.DataFrame, scoring: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    if len(frame) == 0 or key == ["_global_key"]:
        return frame
    if any(col not in frame.columns or col not in scoring.columns for col in key):
        return frame
    scoring_keys = scoring[key].drop_duplicates()
    if len(scoring_keys) == 0:
        return frame.iloc[0:0].copy()
    return frame.merge(scoring_keys, on=key, how="inner")


def build_level_stats(history: pd.DataFrame, scoring: pd.DataFrame, scoring_month: int, cols: list[str]) -> pd.DataFrame:
    key = group_key(cols)
    scoring_moy = int(scoring["month_of_year"].iloc[0])
    scoring_ord = int(scoring["month_ord"].iloc[0])

    hist_valid_all = history.loc[history["valid_bill_for_model"] & history["log_bill"].notna()].copy()
    scoring_valid = scoring.loc[scoring["valid_bill_for_model"] & scoring["log_bill"].notna()].copy()
    hist_valid = filter_frame_to_scoring_keys(hist_valid_all, scoring, key)
    recent = hist_valid.loc[hist_valid["month_ord"].between(scoring_ord - 3, scoring_ord - 1)]
    moy = hist_valid.loc[hist_valid["month_of_year"].eq(scoring_moy)]
    ratio = hist_valid.loc[hist_valid["bill_to_turnover_log_ratio"].notna()]

    base = robust_group_stats(hist_valid, key, "log_bill", "hist")
    recent_stats = robust_group_stats(recent, key, "log_bill", "recent")
    moy_stats = robust_group_stats(moy, key, "log_bill", "moy")
    current_stats = robust_group_stats(scoring_valid, key, "log_bill", "current")
    ratio_stats = robust_group_stats(ratio, key, "bill_to_turnover_log_ratio", "ratio")
    trend_stats = trend_group_stats(hist_valid, key, "peer")
    distribution_stats = peer_distribution_stats(hist_valid, key)
    calibration_stats = peer_calibration_stats(hist_valid, key, scoring_month)

    stats = base.merge(recent_stats, on=key, how="left")
    stats = stats.merge(moy_stats, on=key, how="left")
    stats = stats.merge(current_stats, on=key, how="left")
    stats = stats.merge(ratio_stats, on=key, how="left")
    stats = stats.merge(trend_stats, on=key, how="left")
    stats = stats.merge(distribution_stats, on=key, how="left")
    stats = stats.merge(calibration_stats, on=key, how="left")
    stats["peer_distribution_quality_score"] = stats["peer_distribution_quality_score"].fillna(50.0)
    stats["peer_distribution_status"] = stats["peer_distribution_status"].fillna("PEER_DISTRIBUTION_UNKNOWN")
    stats["peer_calibration_score"] = stats["peer_calibration_score"].fillna(50.0)
    stats["peer_calibration_n"] = stats["peer_calibration_n"].fillna(0)
    stats["peer_calibration_abs_residual_median"] = stats["peer_calibration_abs_residual_median"].fillna(np.nan)
    stats["peer_calibration_interval_coverage"] = stats["peer_calibration_interval_coverage"].fillna(np.nan)
    stats["peer_calibration_false_alarm_rate"] = stats["peer_calibration_false_alarm_rate"].fillna(np.nan)
    stats["scoring_month"] = scoring_month
    return stats


def build_self_stats(history: pd.DataFrame, scoring_ord: int) -> pd.DataFrame:
    valid = history.loc[history["valid_bill_for_model"] & history["log_bill"].notna()].copy()
    scoring_moy = int((scoring_ord - 1) % 12 + 1)
    empty = pd.DataFrame(
        columns=[
            "customer_id",
            "prior_n",
            "prior_median",
            "prior_mad",
            "prior_12_n",
            "last_month_ord",
            "last_log_bill",
            "customer_trend_n",
            "customer_trend_slope",
            "customer_trend_intercept",
            "customer_seasonal_n",
            "customer_seasonal_median",
            "customer_seasonal_mad",
            "customer_recent3_n",
            "customer_recent3_median",
            "customer_recent3_mad",
            "customer_recent3_min",
            "customer_recent3_max",
        ]
    )
    if len(valid) == 0:
        return empty
    if valid["customer_id"].nunique(dropna=False) == len(valid):
        return empty
    counts = valid.groupby("customer_id", dropna=False).size()
    eligible_ids = counts.loc[counts.ge(MIN_SELF_HISTORY_ROWS)].index
    valid = valid.loc[valid["customer_id"].isin(eligible_ids)].copy()
    if len(valid) == 0:
        return empty
    base = robust_group_stats(valid, ["customer_id"], "log_bill", "prior")
    customer_trend = trend_group_stats(valid, ["customer_id"], "customer")
    customer_seasonal = robust_group_stats(
        valid.loc[valid["month_of_year"].eq(scoring_moy)],
        ["customer_id"],
        "log_bill",
        "customer_seasonal",
    )
    customer_recent3 = robust_group_stats(
        valid.loc[valid["month_ord"].between(scoring_ord - 3, scoring_ord - 1)],
        ["customer_id"],
        "log_bill",
        "customer_recent3",
    )
    customer_recent3_range = (
        valid.loc[valid["month_ord"].between(scoring_ord - 3, scoring_ord - 1)]
        .groupby("customer_id", dropna=False)
        .agg(customer_recent3_min=("log_bill", "min"), customer_recent3_max=("log_bill", "max"))
        .reset_index()
    )
    recent_12 = (
        valid.loc[valid["month_ord"].between(scoring_ord - 12, scoring_ord - 1)]
        .groupby("customer_id", dropna=False)
        .size()
        .rename("prior_12_n")
        .reset_index()
    )
    last = valid.sort_values(["customer_id", "month_ord"]).groupby("customer_id", dropna=False).tail(1)
    last = last[["customer_id", "month_ord", "log_bill"]].rename(columns={"month_ord": "last_month_ord", "log_bill": "last_log_bill"})
    out = base.merge(recent_12, on="customer_id", how="left")
    out = out.merge(customer_trend, on="customer_id", how="left")
    out = out.merge(customer_seasonal, on="customer_id", how="left")
    out = out.merge(customer_recent3, on="customer_id", how="left")
    out = out.merge(customer_recent3_range, on="customer_id", how="left")
    out = out.merge(last, on="customer_id", how="left")
    out["prior_12_n"] = out["prior_12_n"].fillna(0).astype(float)
    out["customer_recent3_n"] = out["customer_recent3_n"].fillna(0).astype(float)
    return out


def support_value(row: pd.Series, col: str) -> float:
    value = row.get(col, np.nan)
    if pd.isna(value):
        return 0.0
    return float(value)


def peer_level_specificity(level_name: str) -> float:
    behavior_bonus = 0.08 if "behavior" in level_name else 0.0
    if level_name == "global":
        return 0.25
    if "branch" in level_name and "active" in level_name:
        return min(1.00, 1.00 + behavior_bonus)
    if "branch" in level_name:
        return min(1.00, 0.92 + behavior_bonus)
    if "active" in level_name and "sector" in level_name and "turnover" in level_name:
        return min(1.00, 0.90 + behavior_bonus)
    if "sector" in level_name and "turnover" in level_name:
        return min(1.00, 0.84 + behavior_bonus)
    if "active" in level_name and "sector" in level_name:
        return min(1.00, 0.78 + behavior_bonus)
    if "turnover" in level_name:
        return min(1.00, 0.72 + behavior_bonus)
    if "sector" in level_name:
        return min(1.00, 0.64 + behavior_bonus)
    if "active" in level_name:
        return min(1.00, 0.56 + behavior_bonus)
    if "segment" in level_name:
        return min(1.00, 0.48 + behavior_bonus)
    if "behavior" in level_name:
        return 0.52
    return min(1.00, 0.35 + behavior_bonus)


def support_failure_summary(row: pd.Series, level_name: str) -> str:
    checks = [
        ("hist", "hist_n", MIN_HIST_ROWS),
        ("season", "moy_n", MIN_MOY_ROWS),
        ("recent", "recent_n", MIN_RECENT_ROWS),
        ("current", "current_n", MIN_CURRENT_ROWS),
    ]
    failed = [
        f"{label}={support_value(row, col):.0f}<{threshold}"
        for label, col, threshold in checks
        if support_value(row, col) < threshold
    ]
    if "behavior" in level_name and row.get("behavior_cluster", "behavior_unknown") == "behavior_unknown":
        failed.append("behavior_cluster_missing")
    distribution_score = support_value(row, "peer_distribution_quality_score")
    if distribution_score < MIN_PEER_DISTRIBUTION_SCORE:
        failed.append(f"distribution_score={distribution_score:.1f}<{MIN_PEER_DISTRIBUTION_SCORE:.0f}")
    if not bool(row.get("valid_bill_for_model", False)):
        failed.append("invalid_bill")
    if not failed:
        failed.append("support_ok")
    return f"{level_name}: " + ", ".join(failed)


def peer_selection_reason(row: pd.Series, prior_attempts: list[str]) -> str:
    selected = (
        f"Secilen peer={row.get('peer_group_level_name')}; kolonlar={row.get('peer_group_columns')}; "
        f"destek hist={support_value(row, 'hist_n'):.0f}, season={support_value(row, 'moy_n'):.0f}, "
        f"recent={support_value(row, 'recent_n'):.0f}, current={support_value(row, 'current_n'):.0f}; "
        f"dagilim={row.get('peer_distribution_status')}, dagilim_skoru={support_value(row, 'peer_distribution_quality_score'):.1f}, "
        f"tail={support_value(row, 'peer_tail_rate'):.3f}."
    )
    if not prior_attempts:
        return selected + " Daha dar peer bu seviyedir; destek esikleri gecildi."
    return selected + " Daha dar adaylar elendi: " + " | ".join(prior_attempts[:4])


def peer_representability_score(row: pd.Series) -> float:
    hist_support = min(support_value(row, "hist_n") / 800.0, 1.0)
    season_support = min(support_value(row, "moy_n") / 100.0, 1.0)
    recent_support = min(support_value(row, "recent_n") / 150.0, 1.0)
    current_support = min(support_value(row, "current_n") / 120.0, 1.0)
    specificity = peer_level_specificity(str(row.get("peer_group_level_name", "")))
    distribution_quality = min(max(support_value(row, "peer_distribution_quality_score"), 0.0) / 100.0, 1.0)
    return float(
        100.0
        * (
            0.22 * hist_support
            + 0.13 * season_support
            + 0.18 * recent_support
            + 0.22 * current_support
            + 0.10 * specificity
            + 0.15 * distribution_quality
        )
    )


def peer_representability_status(score: float, level_name: str) -> str:
    if level_name in {"global", "segment", "sector"}:
        return "COARSE_PEER_REVIEW"
    if score >= 80:
        return "STRONG_PEER_REPRESENTATION"
    if score >= 60:
        return "GOOD_PEER_REPRESENTATION"
    if score >= 40:
        return "MEDIUM_PEER_REPRESENTATION"
    return "WEAK_PEER_REPRESENTATION"


def merged_scoring_weights(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    def merge(base: Any, extra: Any) -> Any:
        if isinstance(base, dict) and isinstance(extra, Mapping):
            out = {key: merge(value, extra.get(key)) for key, value in base.items()}
            for key, value in extra.items():
                if key not in out:
                    out[str(key)] = value
            return out
        return base if extra is None else extra

    return merge(DEFAULT_SCORING_WEIGHTS, dict(overrides or {}))


def normalized_weight_section(config: Mapping[str, Any], section: str) -> dict[str, float]:
    raw = dict(config.get(section, DEFAULT_SCORING_WEIGHTS[section]))
    weights = {
        str(key): max(float(value), 0.0)
        for key, value in raw.items()
        if isinstance(value, int | float)
    }
    total = sum(weights.values())
    if total <= 0:
        return normalized_weight_section(DEFAULT_SCORING_WEIGHTS, section)
    return {key: value / total for key, value in weights.items()}


def config_float(config: Mapping[str, Any], section: str, key: str) -> float:
    return float(dict(config.get(section, {})).get(key, DEFAULT_SCORING_WEIGHTS[section][key]))


def config_bool(config: Mapping[str, Any], section: str, key: str) -> bool:
    return bool(dict(config.get(section, {})).get(key, DEFAULT_SCORING_WEIGHTS[section][key]))


def customer_explainability_score(
    row: pd.Series,
    has_self: bool,
    has_customer_trend: bool,
    has_customer_seasonal: bool,
    scoring_weights: Mapping[str, Any] | None = None,
) -> float:
    if bool(row.get("is_new_customer_in_scoring_month", False)):
        return 0.0
    weights = normalized_weight_section(merged_scoring_weights(scoring_weights), "customer_explainability")
    self_support = min(support_value(row, "prior_n") / 6.0, 1.0)
    coverage = min(max(support_value(row, "prior_12_coverage"), 0.0), 1.0)
    gap = row.get("gap_months_before_scoring", np.nan)
    gap_quality = 0.0 if pd.isna(gap) else max(0.0, 1.0 - min(float(gap), 6.0) / 6.0)
    return float(
        100.0
        * (
            weights.get("self_support", 0.0) * self_support
            + weights.get("recent_12_coverage", 0.0) * coverage
            + weights.get("gap_quality", 0.0) * gap_quality
            + weights.get("trend_available", 0.0) * float(has_customer_trend)
            + weights.get("seasonal_available", 0.0) * float(has_customer_seasonal)
        )
    )


def customer_explainability_status(score: float) -> str:
    if score >= 75:
        return "CUSTOMER_SELF_EXPLAINABLE"
    if score >= 50:
        return "CUSTOMER_PARTIAL_EXPLAINABLE"
    if score > 0:
        return "CUSTOMER_LIMITED_HISTORY"
    return "CUSTOMER_NOT_EXPLAINABLE"


def customer_bucket_weight(
    customer_score: float,
    has_customer_signal: bool,
    scoring_weights: Mapping[str, Any] | None = None,
) -> float:
    if not has_customer_signal:
        return 0.0
    config = merged_scoring_weights(scoring_weights)
    bucket_config = dict(config.get("customer_final_weight", {}))
    buckets = bucket_config.get("buckets", [])
    if isinstance(buckets, list):
        for bucket in sorted(buckets, key=lambda item: float(dict(item).get("min_score", 0.0)), reverse=True):
            current = dict(bucket)
            if customer_score >= float(current.get("min_score", 0.0)):
                return float(current.get("weight", 0.0))
    return float(bucket_config.get("default_weight", DEFAULT_SCORING_WEIGHTS["customer_final_weight"]["default_weight"]))


def scoring_strategy(customer_weight: float, peer_status: str, has_customer_signal: bool) -> str:
    if customer_weight >= 0.75:
        return "CUSTOMER_FIRST"
    if customer_weight >= 0.50:
        return "CUSTOMER_PEER_HYBRID"
    if has_customer_signal:
        return "PEER_FIRST_WITH_CUSTOMER_CHECK"
    if peer_status == "COARSE_PEER_REVIEW":
        return "PEER_FIRST_COARSE_REVIEW"
    return "PEER_FIRST"


def merged_score_aggregation(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    def merge(base: Any, extra: Any) -> Any:
        if isinstance(base, dict) and isinstance(extra, Mapping):
            out = {key: merge(value, extra.get(key)) for key, value in base.items()}
            for key, value in extra.items():
                if key not in out:
                    out[str(key)] = value
            return out
        return base if extra is None else extra

    return merge(DEFAULT_SCORE_AGGREGATION, dict(overrides or {}))


def aggregation_float(config: Mapping[str, Any], section: str, key: str) -> float:
    return float(dict(config.get(section, {})).get(key, DEFAULT_SCORE_AGGREGATION[section][key]))


def aggregation_bool(config: Mapping[str, Any], section: str, key: str) -> bool:
    return bool(dict(config.get(section, {})).get(key, DEFAULT_SCORE_AGGREGATION[section][key]))


def simes_p_value(p_values: list[float], max_signals: int) -> float:
    clean = sorted(float(value) for value in p_values if np.isfinite(float(value)) and float(value) < 1.0)
    if not clean:
        return 1.0
    clean = clean[: max(1, int(max_signals))]
    m = len(clean)
    return float(min(min(m * value / rank, 1.0) for rank, value in enumerate(clean, start=1)))


def add_directional_signal_evidence(frame: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    out = frame.copy()
    min_abs_z = float(config.get("min_abs_z_for_evidence", 0.50))
    min_p_value = float(config.get("min_p_value", 0.001))
    for signal in SIGNAL_DEFINITIONS:
        name = signal["name"]
        z = pd.to_numeric(out.get(signal["z_col"], pd.Series(np.nan, index=out.index)), errors="coerce")
        raw_score = pd.Series(score_from_z(z), index=out.index).clip(lower=0.0, upper=100.0)
        z_p = (1.0 - raw_score / 100.0).clip(lower=min_p_value, upper=1.0)
        empirical_p = pd.Series(1.0, index=out.index, dtype=float)

        pos = z.ge(min_abs_z)
        if bool(pos.any()):
            ranks = z.loc[pos].rank(ascending=False, method="min")
            empirical_p.loc[pos] = (ranks / (int(pos.sum()) + 1.0)).clip(lower=min_p_value, upper=1.0)

        neg = z.le(-min_abs_z)
        if bool(neg.any()):
            ranks = z.loc[neg].rank(ascending=True, method="min")
            empirical_p.loc[neg] = (ranks / (int(neg.sum()) + 1.0)).clip(lower=min_p_value, upper=1.0)

        valid_direction = pos | neg
        conservative_p = pd.Series(1.0, index=out.index, dtype=float)
        conservative_p.loc[valid_direction] = np.maximum(z_p.loc[valid_direction], empirical_p.loc[valid_direction])
        out[f"{name}_p_value"] = conservative_p
        out[f"{name}_evidence_score"] = (100.0 * (1.0 - conservative_p)).clip(lower=0.0, upper=100.0)
    return out


def signal_records(row: pd.Series, family: str | None, direction: str | None, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    min_abs_z = float(config.get("min_abs_z_for_evidence", 0.50))
    records: list[dict[str, Any]] = []
    for signal in SIGNAL_DEFINITIONS:
        if family is not None and signal["family"] != family:
            continue
        z = row.get(signal["z_col"], np.nan)
        if pd.isna(z) or abs(float(z)) < min_abs_z:
            continue
        signal_direction = "HIGH" if float(z) > 0 else "LOW"
        if direction is not None and signal_direction != direction:
            continue
        p_value = float(row.get(f"{signal['name']}_p_value", 1.0))
        records.append(
            {
                "name": signal["name"],
                "family": signal["family"],
                "direction": signal_direction,
                "z": float(z),
                "p_value": p_value,
                "score": float(row.get(f"{signal['name']}_evidence_score", 0.0)),
            }
        )
    return sorted(records, key=lambda item: (item["p_value"], -item["score"]))


def family_direction_evidence(row: pd.Series, family: str, direction: str, config: Mapping[str, Any]) -> dict[str, Any]:
    records = signal_records(row, family, direction, config)
    max_signals = int(config.get("max_signals_per_family", 3))
    family_p = simes_p_value([record["p_value"] for record in records], max_signals)
    primary = records[0] if records else {}
    return {
        "p_value": family_p,
        "score": float(100.0 * (1.0 - family_p)),
        "direction": direction,
        "primary_signal": primary.get("name", ""),
        "primary_z": primary.get("z", np.nan),
        "primary_signal_score": primary.get("score", np.nan),
        "records": records,
    }


def best_family_evidence(row: pd.Series, family: str, config: Mapping[str, Any]) -> dict[str, Any]:
    high = family_direction_evidence(row, family, "HIGH", config)
    low = family_direction_evidence(row, family, "LOW", config)
    return high if high["p_value"] <= low["p_value"] else low


def customer_reliability_status(row: pd.Series, config: Mapping[str, Any]) -> str:
    rules = dict(config.get("customer_reliability", {}))
    prior_n = numeric_or_default(row, "prior_n", 0.0)
    coverage = numeric_or_default(row, "prior_12_coverage", 0.0)
    gap = row.get("gap_months_before_scoring", np.nan)
    gap_ok = pd.notna(gap) and float(gap) <= float(rules.get("strong_max_gap_months", 1))
    if bool(row.get("is_new_customer_in_scoring_month", False)) or prior_n <= 0:
        return "CUSTOMER_NEW"
    if (
        prior_n >= float(rules.get("strong_min_prior_n", 6))
        and coverage >= float(rules.get("strong_min_coverage_12m", 0.50))
        and gap_ok
    ):
        return "CUSTOMER_STRONG"
    if prior_n >= float(rules.get("partial_min_prior_n", 2)):
        return "CUSTOMER_PARTIAL"
    return "CUSTOMER_WEAK"


def peer_reliability_status(row: pd.Series, config: Mapping[str, Any]) -> str:
    rules = dict(config.get("peer_reliability", {}))
    objective_ok = numeric_or_default(row, "peer_objective_score", 0.0) >= float(rules.get("min_peer_objective_score", 60.0))
    distribution_ok = numeric_or_default(row, "peer_distribution_quality_score", 0.0) >= float(
        rules.get("min_peer_distribution_score", 35.0)
    )
    coarse = str(row.get("peer_representability_status", "")) == "COARSE_PEER_REVIEW"
    if not objective_ok or not distribution_ok:
        return "PEER_WEAK"
    if coarse and bool(rules.get("coarse_peer_review_only", True)):
        return "PEER_COARSE_REVIEW"
    return "PEER_STRONG"


def aggregate_evidence_row(row: pd.Series, config: Mapping[str, Any]) -> pd.Series:
    thresholds = dict(config.get("evidence_thresholds", {}))
    moderate_p = float(thresholds.get("moderate_signal_p", 0.05))
    conflict_p = float(thresholds.get("conflict_signal_p", 0.05))

    customer = best_family_evidence(row, "customer", config)
    peer = best_family_evidence(row, "peer", config)
    customer_status = customer_reliability_status(row, config)
    peer_status = peer_reliability_status(row, config)
    customer_usable = customer_status in {"CUSTOMER_STRONG", "CUSTOMER_PARTIAL"} and customer["p_value"] <= moderate_p
    peer_usable = peer_status == "PEER_STRONG" and peer["p_value"] <= moderate_p
    same_direction = customer["direction"] == peer["direction"]
    conflict = customer["p_value"] <= conflict_p and peer["p_value"] <= conflict_p and not same_direction

    if customer_usable and peer_usable and same_direction:
        final_p = simes_p_value([customer["p_value"], peer["p_value"]], 2)
        driver = "CUSTOMER_PEER_COMBINED"
        direction = customer["direction"]
    elif customer_usable and peer_usable and not same_direction:
        final_p = min(customer["p_value"], peer["p_value"])
        driver = "CUSTOMER_PEER_CONFLICT"
        direction = customer["direction"] if customer["p_value"] <= peer["p_value"] else peer["direction"]
    elif customer_usable:
        final_p = customer["p_value"]
        driver = "CUSTOMER"
        direction = customer["direction"]
    elif peer_usable:
        final_p = peer["p_value"]
        driver = "PEER"
        direction = peer["direction"]
    else:
        final_p = min(customer["p_value"], peer["p_value"])
        driver = "INSUFFICIENT_EVIDENCE" if final_p >= 1.0 else "WEAK_EVIDENCE"
        direction = customer["direction"] if customer["p_value"] <= peer["p_value"] else peer["direction"]

    final_score = float(np.clip(100.0 * (1.0 - final_p), 0.0, 100.0))
    primary_family = "customer" if customer["p_value"] <= peer["p_value"] else "peer"
    primary = customer if primary_family == "customer" else peer
    secondary = peer if primary_family == "customer" else customer
    secondary_signal_name = (
        secondary["primary_signal"]
        if secondary["p_value"] < 1.0 and (secondary["direction"] == direction or driver == "CUSTOMER_PEER_CONFLICT")
        else ""
    )
    customer_weight = 0.0
    peer_weight = 0.0
    if driver == "CUSTOMER_PEER_COMBINED":
        customer_weight = 0.50
        peer_weight = 0.50
    elif driver == "CUSTOMER_PEER_CONFLICT":
        customer_weight = 0.50
        peer_weight = 0.50
    elif driver.startswith("CUSTOMER"):
        customer_weight = 1.0
    elif driver == "PEER":
        peer_weight = 1.0
    elif primary_family == "customer":
        customer_weight = 1.0
    else:
        peer_weight = 1.0

    confidence_cfg = dict(config.get("confidence", {}))
    if driver == "CUSTOMER_PEER_COMBINED":
        confidence = max(
            numeric_or_default(row, "customer_explainability_score", 0.0),
            numeric_or_default(row, "peer_representability_score", 0.0),
        ) + float(confidence_cfg.get("combined_driver_bonus", 8.0))
    elif driver.startswith("CUSTOMER"):
        confidence = max(
            numeric_or_default(row, "customer_explainability_score", 0.0),
            float(confidence_cfg.get("customer_driver_floor", 55.0)),
        )
    elif driver == "PEER":
        confidence = max(
            numeric_or_default(row, "peer_representability_score", 0.0),
            float(confidence_cfg.get("peer_driver_floor", 45.0)),
        )
    else:
        confidence = max(
            numeric_or_default(row, "customer_explainability_score", 0.0),
            numeric_or_default(row, "peer_representability_score", 0.0),
        )
    if same_direction and customer["p_value"] <= moderate_p and peer["p_value"] <= moderate_p:
        confidence += float(confidence_cfg.get("same_direction_bonus", 5.0))
    if conflict:
        confidence -= float(confidence_cfg.get("conflict_penalty", 20.0))

    gap_penalty = min(
        numeric_or_default(row, "data_gap_score", 100.0) * float(confidence_cfg.get("data_gap_penalty_weight", 0.25)),
        float(confidence_cfg.get("data_gap_penalty_cap", 25.0)),
    )
    distribution_penalty = max(
        0.0,
        (
            float(confidence_cfg.get("distribution_penalty_floor", 60.0))
            - numeric_or_default(row, "peer_distribution_quality_score", 50.0)
        )
        * float(confidence_cfg.get("distribution_penalty_weight", 0.35)),
    )
    confidence = max(confidence - gap_penalty - distribution_penalty, 0.0)
    if pd.isna(row.get("self_history_z", np.nan)):
        confidence = min(confidence, float(confidence_cfg.get("no_self_history_cap", 75.0)))
    if row.get("peer_representability_status") == "COARSE_PEER_REVIEW":
        confidence = min(confidence, float(confidence_cfg.get("coarse_peer_cap", 65.0)))

    component_weights = {f"{signal['name']}_final_weight": 0.0 for signal in SIGNAL_DEFINITIONS}
    if primary["primary_signal"]:
        component_weights[f"{primary['primary_signal']}_final_weight"] = customer_weight if primary_family == "customer" else peer_weight
    if secondary_signal_name:
        component_weights[f"{secondary_signal_name}_final_weight"] = peer_weight if primary_family == "customer" else customer_weight

    return pd.Series(
        {
            "customer_signal_score": customer["score"],
            "peer_signal_score": peer["score"],
            "customer_family_p_value": customer["p_value"],
            "peer_family_p_value": peer["p_value"],
            "customer_family_direction": customer["direction"],
            "peer_family_direction": peer["direction"],
            "customer_reliability_status": customer_status,
            "peer_reliability_status": peer_status,
            "primary_signal_name": primary["primary_signal"],
            "primary_signal_family": primary_family,
            "primary_signal_p_value": primary["p_value"],
            "primary_signal_score": primary["score"],
            "secondary_signal_name": secondary_signal_name,
            "secondary_signal_p_value": secondary["p_value"] if secondary_signal_name else np.nan,
            "evidence_driver": driver,
            "evidence_conflict_flag": bool(conflict),
            "final_anomaly_score": final_score,
            "signed_anomaly_signal": final_score if direction == "HIGH" else -final_score,
            "anomaly_direction": direction,
            "confidence": float(np.clip(confidence, 0.0, 100.0)),
            "customer_final_weight": customer_weight,
            "peer_final_weight": peer_weight,
            **component_weights,
        }
    )


def apply_directional_evidence_aggregation(frame: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    out = add_directional_signal_evidence(frame, config)
    evidence = vectorized_evidence_aggregation(out, config)
    return pd.concat([out.drop(columns=[col for col in evidence.columns if col in out.columns]), evidence], axis=1)


def _family_direction_arrays(
    frame: pd.DataFrame,
    family: str,
    direction: str,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    signals = [signal for signal in SIGNAL_DEFINITIONS if signal["family"] == family]
    n_rows = len(frame)
    if not signals:
        return {
            "p": np.ones(n_rows, dtype=float),
            "score": np.zeros(n_rows, dtype=float),
            "direction": np.full(n_rows, direction, dtype=object),
            "signal": np.full(n_rows, "", dtype=object),
            "z": np.full(n_rows, np.nan, dtype=float),
            "signal_score": np.full(n_rows, np.nan, dtype=float),
        }

    min_abs_z = float(config.get("min_abs_z_for_evidence", 0.50))
    p_cols = [f"{signal['name']}_p_value" for signal in signals]
    score_cols = [f"{signal['name']}_evidence_score" for signal in signals]
    z_cols = [signal["z_col"] for signal in signals]
    p_matrix = frame[p_cols].to_numpy(dtype=float, copy=True)
    score_matrix = frame[score_cols].to_numpy(dtype=float, copy=True)
    z_matrix = frame[z_cols].to_numpy(dtype=float, copy=True)

    if direction == "HIGH":
        valid = z_matrix >= min_abs_z
    else:
        valid = z_matrix <= -min_abs_z
    p_matrix = np.where(valid, p_matrix, 1.0)

    max_signals = max(1, int(config.get("max_signals_per_family", 3)))
    sorted_p = np.sort(p_matrix, axis=1)[:, :max_signals]
    m = (sorted_p < 1.0).sum(axis=1)
    ranks = np.arange(1, sorted_p.shape[1] + 1, dtype=float)
    simes_candidates = np.where(
        sorted_p < 1.0,
        (m[:, None] * sorted_p) / ranks[None, :],
        1.0,
    )
    family_p = np.minimum(np.nanmin(simes_candidates, axis=1), 1.0)
    family_p = np.where(m > 0, family_p, 1.0)

    primary_idx = np.argmin(p_matrix, axis=1)
    row_idx = np.arange(n_rows)
    has_primary = m > 0
    signal_names = np.asarray([signal["name"] for signal in signals], dtype=object)
    primary_signal = np.where(has_primary, signal_names[primary_idx], "")
    primary_z = np.where(has_primary, z_matrix[row_idx, primary_idx], np.nan)
    primary_score = np.where(has_primary, score_matrix[row_idx, primary_idx], np.nan)

    return {
        "p": family_p,
        "score": np.clip(100.0 * (1.0 - family_p), 0.0, 100.0),
        "direction": np.full(n_rows, direction, dtype=object),
        "signal": primary_signal,
        "z": primary_z,
        "signal_score": primary_score,
    }


def _best_family_arrays(frame: pd.DataFrame, family: str, config: Mapping[str, Any]) -> dict[str, Any]:
    high = _family_direction_arrays(frame, family, "HIGH", config)
    low = _family_direction_arrays(frame, family, "LOW", config)
    choose_high = high["p"] <= low["p"]
    return {
        key: np.where(choose_high, high[key], low[key])
        for key in ["p", "score", "direction", "signal", "z", "signal_score"]
    }


def vectorized_evidence_aggregation(frame: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    n_rows = len(frame)
    index = frame.index
    thresholds = dict(config.get("evidence_thresholds", {}))
    moderate_p = float(thresholds.get("moderate_signal_p", 0.05))
    conflict_p = float(thresholds.get("conflict_signal_p", 0.05))
    customer = _best_family_arrays(frame, "customer", config)
    peer = _best_family_arrays(frame, "peer", config)

    rules_customer = dict(config.get("customer_reliability", {}))
    prior_n = pd.to_numeric(frame.get("prior_n", pd.Series(0.0, index=index)), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    coverage = pd.to_numeric(frame.get("prior_12_coverage", pd.Series(0.0, index=index)), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    gap = pd.to_numeric(frame.get("gap_months_before_scoring", pd.Series(np.nan, index=index)), errors="coerce").to_numpy(dtype=float)
    is_new = frame.get("is_new_customer_in_scoring_month", pd.Series(False, index=index)).fillna(False).astype(bool).to_numpy()
    customer_status = np.full(n_rows, "CUSTOMER_WEAK", dtype=object)
    customer_status[is_new | (prior_n <= 0)] = "CUSTOMER_NEW"
    strong_customer = (
        (prior_n >= float(rules_customer.get("strong_min_prior_n", 6)))
        & (coverage >= float(rules_customer.get("strong_min_coverage_12m", 0.50)))
        & np.isfinite(gap)
        & (gap <= float(rules_customer.get("strong_max_gap_months", 1)))
        & ~(is_new | (prior_n <= 0))
    )
    partial_customer = (
        (prior_n >= float(rules_customer.get("partial_min_prior_n", 2)))
        & ~strong_customer
        & ~(is_new | (prior_n <= 0))
    )
    customer_status[partial_customer] = "CUSTOMER_PARTIAL"
    customer_status[strong_customer] = "CUSTOMER_STRONG"

    rules_peer = dict(config.get("peer_reliability", {}))
    peer_objective = pd.to_numeric(frame.get("peer_objective_score", pd.Series(0.0, index=index)), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    peer_distribution = (
        pd.to_numeric(frame.get("peer_distribution_quality_score", pd.Series(0.0, index=index)), errors="coerce")
        .fillna(0.0)
        .to_numpy(dtype=float)
    )
    peer_repr = frame.get("peer_representability_status", pd.Series("", index=index)).astype(str).to_numpy()
    peer_status = np.full(n_rows, "PEER_STRONG", dtype=object)
    peer_status[
        (peer_objective < float(rules_peer.get("min_peer_objective_score", 60.0)))
        | (peer_distribution < float(rules_peer.get("min_peer_distribution_score", 35.0)))
    ] = "PEER_WEAK"
    peer_status[
        (peer_status == "PEER_STRONG")
        & (peer_repr == "COARSE_PEER_REVIEW")
        & bool(rules_peer.get("coarse_peer_review_only", True))
    ] = "PEER_COARSE_REVIEW"

    customer_usable = np.isin(customer_status, ["CUSTOMER_STRONG", "CUSTOMER_PARTIAL"]) & (customer["p"] <= moderate_p)
    peer_usable = (peer_status == "PEER_STRONG") & (peer["p"] <= moderate_p)
    same_direction = customer["direction"] == peer["direction"]
    conflict = (customer["p"] <= conflict_p) & (peer["p"] <= conflict_p) & ~same_direction

    final_p = np.minimum(customer["p"], peer["p"])
    direction = np.where(customer["p"] <= peer["p"], customer["direction"], peer["direction"])
    driver = np.full(n_rows, "WEAK_EVIDENCE", dtype=object)
    driver[final_p >= 1.0] = "INSUFFICIENT_EVIDENCE"
    combined = customer_usable & peer_usable & same_direction
    conflict_driver = customer_usable & peer_usable & ~same_direction
    customer_driver = customer_usable & ~peer_usable
    peer_driver = peer_usable & ~customer_usable
    combined_min_p = np.minimum(customer["p"][combined], peer["p"][combined])
    combined_max_p = np.maximum(customer["p"][combined], peer["p"][combined])
    final_p[combined] = np.minimum(1.0, np.minimum(2.0 * combined_min_p, combined_max_p))
    direction[combined] = customer["direction"][combined]
    driver[combined] = "CUSTOMER_PEER_COMBINED"
    final_p[conflict_driver] = np.minimum(customer["p"][conflict_driver], peer["p"][conflict_driver])
    direction[conflict_driver] = np.where(
        customer["p"][conflict_driver] <= peer["p"][conflict_driver],
        customer["direction"][conflict_driver],
        peer["direction"][conflict_driver],
    )
    driver[conflict_driver] = "CUSTOMER_PEER_CONFLICT"
    final_p[customer_driver] = customer["p"][customer_driver]
    direction[customer_driver] = customer["direction"][customer_driver]
    driver[customer_driver] = "CUSTOMER"
    final_p[peer_driver] = peer["p"][peer_driver]
    direction[peer_driver] = peer["direction"][peer_driver]
    driver[peer_driver] = "PEER"

    final_score = np.clip(100.0 * (1.0 - final_p), 0.0, 100.0)
    primary_is_customer = customer["p"] <= peer["p"]
    primary_family = np.where(primary_is_customer, "customer", "peer")
    primary_signal = np.where(primary_is_customer, customer["signal"], peer["signal"])
    primary_p = np.where(primary_is_customer, customer["p"], peer["p"])
    primary_score = np.where(primary_is_customer, customer["score"], peer["score"])
    secondary_signal = np.where(primary_is_customer, peer["signal"], customer["signal"])
    secondary_direction = np.where(primary_is_customer, peer["direction"], customer["direction"])
    secondary_p = np.where(primary_is_customer, peer["p"], customer["p"])
    secondary_signal = np.where(
        (secondary_p < 1.0) & ((secondary_direction == direction) | conflict_driver),
        secondary_signal,
        "",
    )
    secondary_p = np.where(secondary_signal != "", secondary_p, np.nan)

    customer_weight = np.zeros(n_rows, dtype=float)
    peer_weight = np.zeros(n_rows, dtype=float)
    customer_weight[combined] = 0.50
    peer_weight[combined] = 0.50
    customer_weight[conflict_driver] = 0.50
    peer_weight[conflict_driver] = 0.50
    customer_weight[customer_driver] = 1.0
    peer_weight[peer_driver] = 1.0
    remaining = ~(combined | conflict_driver | customer_driver | peer_driver)
    customer_weight[remaining & primary_is_customer] = 1.0
    peer_weight[remaining & ~primary_is_customer] = 1.0

    confidence_cfg = dict(config.get("confidence", {}))
    customer_explain = (
        pd.to_numeric(frame.get("customer_explainability_score", pd.Series(0.0, index=index)), errors="coerce")
        .fillna(0.0)
        .to_numpy(dtype=float)
    )
    peer_represent = (
        pd.to_numeric(frame.get("peer_representability_score", pd.Series(0.0, index=index)), errors="coerce")
        .fillna(0.0)
        .to_numpy(dtype=float)
    )
    confidence = np.maximum(customer_explain, peer_represent)
    confidence[combined] = (
        np.maximum(customer_explain[combined], peer_represent[combined]) + float(confidence_cfg.get("combined_driver_bonus", 8.0))
    )
    confidence[conflict_driver] = np.maximum(
        customer_explain[conflict_driver],
        float(confidence_cfg.get("customer_driver_floor", 55.0)),
    )
    confidence[customer_driver] = np.maximum(customer_explain[customer_driver], float(confidence_cfg.get("customer_driver_floor", 55.0)))
    confidence[peer_driver] = np.maximum(peer_represent[peer_driver], float(confidence_cfg.get("peer_driver_floor", 45.0)))
    confidence += np.where(
        same_direction & (customer["p"] <= moderate_p) & (peer["p"] <= moderate_p),
        float(confidence_cfg.get("same_direction_bonus", 5.0)),
        0.0,
    )
    confidence -= np.where(conflict, float(confidence_cfg.get("conflict_penalty", 20.0)), 0.0)
    data_gap = pd.to_numeric(frame.get("data_gap_score", pd.Series(100.0, index=index)), errors="coerce").fillna(100.0).to_numpy(dtype=float)
    gap_penalty = np.minimum(
        data_gap * float(confidence_cfg.get("data_gap_penalty_weight", 0.25)),
        float(confidence_cfg.get("data_gap_penalty_cap", 25.0)),
    )
    distribution_penalty = np.maximum(
        0.0,
        (float(confidence_cfg.get("distribution_penalty_floor", 60.0)) - peer_distribution)
        * float(confidence_cfg.get("distribution_penalty_weight", 0.35)),
    )
    confidence = np.maximum(confidence - gap_penalty - distribution_penalty, 0.0)
    no_self = frame.get("self_history_z", pd.Series(np.nan, index=index)).isna().to_numpy()
    confidence[no_self] = np.minimum(confidence[no_self], float(confidence_cfg.get("no_self_history_cap", 75.0)))
    coarse = peer_repr == "COARSE_PEER_REVIEW"
    confidence[coarse] = np.minimum(confidence[coarse], float(confidence_cfg.get("coarse_peer_cap", 65.0)))

    component_weights = {f"{signal['name']}_final_weight": np.zeros(n_rows, dtype=float) for signal in SIGNAL_DEFINITIONS}
    for signal in SIGNAL_DEFINITIONS:
        name = signal["name"]
        primary_mask = primary_signal == name
        secondary_mask = secondary_signal == name
        component_weights[f"{name}_final_weight"][primary_mask] = np.where(
            primary_is_customer[primary_mask],
            customer_weight[primary_mask],
            peer_weight[primary_mask],
        )
        component_weights[f"{name}_final_weight"][secondary_mask] = np.where(
            primary_is_customer[secondary_mask],
            peer_weight[secondary_mask],
            customer_weight[secondary_mask],
        )

    return pd.DataFrame(
        {
            "customer_signal_score": customer["score"],
            "peer_signal_score": peer["score"],
            "customer_family_p_value": customer["p"],
            "peer_family_p_value": peer["p"],
            "customer_family_direction": customer["direction"],
            "peer_family_direction": peer["direction"],
            "customer_reliability_status": customer_status,
            "peer_reliability_status": peer_status,
            "primary_signal_name": primary_signal,
            "primary_signal_family": primary_family,
            "primary_signal_p_value": primary_p,
            "primary_signal_score": primary_score,
            "secondary_signal_name": secondary_signal,
            "secondary_signal_p_value": secondary_p,
            "evidence_driver": driver,
            "evidence_conflict_flag": conflict,
            "final_anomaly_score": final_score,
            "signed_anomaly_signal": np.where(direction == "HIGH", final_score, -final_score),
            "anomaly_direction": direction,
            "confidence": np.clip(confidence, 0.0, 100.0),
            "customer_final_weight": customer_weight,
            "peer_final_weight": peer_weight,
            **component_weights,
        },
        index=index,
    )


def percentile_score(values: np.ndarray) -> np.ndarray:
    clean = np.asarray(values, dtype=float)
    out = np.zeros(len(clean), dtype=float)
    valid = np.isfinite(clean)
    if not bool(valid.any()):
        return out
    ranks = pd.Series(clean[valid]).rank(method="average", pct=True).to_numpy(dtype=float)
    out[valid] = 100.0 * ranks
    return out


def robust_feature_matrix(frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
    features: list[np.ndarray] = []
    for column in columns:
        values = pd.to_numeric(frame.get(column, pd.Series(np.nan, index=frame.index)), errors="coerce").to_numpy(dtype=float)
        finite = np.isfinite(values)
        if not bool(finite.any()):
            scaled = np.zeros(len(frame), dtype=float)
        else:
            median = float(np.nanmedian(values[finite]))
            scale = robust_mad(pd.Series(values[finite]), MIN_LOG_SCALE)
            scaled = (np.where(finite, values, median) - median) / max(scale, MIN_LOG_SCALE)
        features.append(np.clip(scaled, -8.0, 8.0))
        features.append((~finite).astype(float))
    if not features:
        return np.empty((len(frame), 0), dtype=float)
    return np.vstack(features).T


def pca_challenger_score(matrix: np.ndarray, variance_to_keep: float, max_components: int) -> np.ndarray:
    if matrix.shape[0] < 3 or matrix.shape[1] == 0:
        return np.zeros(matrix.shape[0], dtype=float)
    centered = matrix - np.mean(matrix, axis=0, keepdims=True)
    try:
        _, singular_values, vt = np.linalg.svd(centered, full_matrices=False)
    except np.linalg.LinAlgError:
        return np.zeros(matrix.shape[0], dtype=float)
    if len(singular_values) == 0:
        return np.zeros(matrix.shape[0], dtype=float)
    variance = singular_values**2
    total_variance = float(np.sum(variance))
    if total_variance <= 0:
        return np.zeros(matrix.shape[0], dtype=float)
    cumulative = np.cumsum(variance) / total_variance
    n_components = int(np.searchsorted(cumulative, float(variance_to_keep), side="left") + 1)
    n_components = max(1, min(n_components, int(max_components), vt.shape[0]))
    components = vt[:n_components]
    reconstructed = centered @ components.T @ components
    reconstruction_error = np.mean((centered - reconstructed) ** 2, axis=1)
    return percentile_score(reconstruction_error)


def sklearn_challenger_scores(matrix: np.ndarray, config: Mapping[str, Any]) -> tuple[dict[str, np.ndarray], list[str]]:
    methods = {str(method).lower() for method in config.get("methods", [])}
    try:
        from sklearn.ensemble import IsolationForest
        from sklearn.neighbors import LocalOutlierFactor
    except Exception as exc:
        skipped = []
        if "isolation_forest" in methods:
            skipped.append(f"isolation_forest skipped: scikit-learn unavailable ({type(exc).__name__})")
        if "lof" in methods:
            skipped.append(f"lof skipped: scikit-learn unavailable ({type(exc).__name__})")
        return {}, skipped

    scores: dict[str, np.ndarray] = {}
    skipped: list[str] = []
    random_state = int(config.get("random_state", 42))
    if "isolation_forest" in methods and matrix.shape[0] >= 20:
        iso = IsolationForest(n_estimators=100, contamination="auto", random_state=random_state)
        iso.fit(matrix)
        scores["isolation_forest"] = percentile_score(-iso.decision_function(matrix))
    elif "isolation_forest" in methods:
        skipped.append("isolation_forest skipped: scoring row count below 20")
    if "lof" in methods and matrix.shape[0] >= 20:
        neighbors = max(5, min(int(config.get("lof_neighbors", 35)), matrix.shape[0] - 1))
        lof = LocalOutlierFactor(n_neighbors=neighbors, contamination="auto")
        lof.fit_predict(matrix)
        scores["lof"] = percentile_score(-lof.negative_outlier_factor_)
    elif "lof" in methods:
        skipped.append("lof skipped: scoring row count below 20")
    return scores, skipped


def add_challenger_diagnostics(frame: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    out = frame.copy()
    challenger = dict(config.get("challenger_models", {}))
    if not bool(challenger.get("enabled", False)):
        out["model_challenger_score"] = np.nan
        out["model_challenger_warning"] = "Challenger model disabled."
        out["pca_challenger_anomaly_flag"] = 0
        out["if_challenger_anomaly_flag"] = 0
        out["lof_challenger_anomaly_flag"] = 0
        return out
    if len(out) < int(challenger.get("min_rows", 200)):
        out["model_challenger_score"] = np.nan
        out["model_challenger_warning"] = "Challenger model skipped: scoring row count below minimum."
        out["pca_challenger_anomaly_flag"] = 0
        out["if_challenger_anomaly_flag"] = 0
        out["lof_challenger_anomaly_flag"] = 0
        return out

    feature_columns = [
        "self_history_z",
        "customer_trend_z",
        "customer_seasonal_z",
        "customer_recent_regime_z",
        "historical_peer_z",
        "current_peer_z",
        "peer_trend_z",
        "turnover_intensity_z",
        "data_gap_score",
        "peer_distribution_quality_score",
        "peer_calibration_score",
        "peer_representability_score",
        "actual_to_expected_ratio",
    ]
    matrix = robust_feature_matrix(out, feature_columns)
    method_scores: dict[str, np.ndarray] = {}
    methods = {str(method).lower() for method in challenger.get("methods", [])}
    if "pca" in methods:
        method_scores["pca"] = pca_challenger_score(
            matrix,
            float(challenger.get("pca_variance_to_keep", 0.80)),
            int(challenger.get("pca_max_components", 6)),
        )
    sklearn_scores, skipped_methods = sklearn_challenger_scores(matrix, challenger)
    method_scores.update(sklearn_scores)
    flag_threshold = float(challenger.get("flag_threshold", 95.0))
    out["pca_challenger_anomaly_flag"] = (
        method_scores.get("pca", np.zeros(len(out), dtype=float)) >= flag_threshold
    ).astype(int)
    out["if_challenger_anomaly_flag"] = (
        method_scores.get("isolation_forest", np.zeros(len(out), dtype=float)) >= flag_threshold
    ).astype(int)
    out["lof_challenger_anomaly_flag"] = (
        method_scores.get("lof", np.zeros(len(out), dtype=float)) >= flag_threshold
    ).astype(int)
    if not method_scores:
        out["model_challenger_score"] = np.nan
        skipped_text = "; ".join(skipped_methods) if skipped_methods else "no challenger method produced scores"
        out["model_challenger_warning"] = f"Challenger model skipped: {skipped_text}."
        return out

    stacked = np.vstack(list(method_scores.values()))
    score = np.nanmean(stacked, axis=0)
    out["model_challenger_score"] = np.clip(score, 0.0, 100.0)
    method_text = "+".join(sorted(method_scores))
    skipped_suffix = "" if not skipped_methods else " Atlanan yontemler: " + "; ".join(skipped_methods) + "."
    out["model_challenger_warning"] = np.select(
        [
            out["model_challenger_score"].ge(95.0),
            out["model_challenger_score"].ge(80.0),
        ],
        [
            f"Challenger ({method_text}) residual feature uzayinda yuksek ayrisma gosteriyor; production karari robust sistemdir.{skipped_suffix}",
            f"Challenger ({method_text}) residual feature uzayinda izleme sinyali gosteriyor; production karari robust sistemdir.{skipped_suffix}",
        ],
        default=f"Challenger ({method_text}) ek anomaly kaniti gormedi; production karari robust sistemdir.{skipped_suffix}",
    )
    return out


def score_scoring_month(
    prepared: pd.DataFrame,
    scoring_month: int,
    watch_top_rate: float,
    high_top_rate: float,
    peer_config: adaptive.PeerSelectionConfig | None = None,
    support_thresholds: adaptive.PeerSupportThresholds | None = None,
    scoring_weights: Mapping[str, Any] | None = None,
    score_aggregation: Mapping[str, Any] | None = None,
    derived_features_config: Mapping[str, Any] | None = None,
) -> ModelRun:
    history_raw = prepared.loc[prepared["invoice_month"].lt(scoring_month)].copy()
    scoring_raw = prepared.loc[prepared["invoice_month"].eq(scoring_month)].copy()
    if len(scoring_raw) == 0:
        raise ValueError(f"No rows found for scoring month {scoring_month}")

    edges = fit_turnover_edges(history_raw)
    history = assign_turnover_bucket(history_raw, edges)
    scoring = assign_turnover_bucket(scoring_raw, edges)
    profile = prepared.attrs.get("profile", {}) if hasattr(prepared, "attrs") else {}
    effective_derived = derived_features_config or profile.get("derived_features", {})
    ratio_settings = profile.get("feature_ratio") or feature_ratio_settings(effective_derived, None)
    ratio_signal_enabled = bool(ratio_settings.get("use_as_anomaly_signal", False))
    ratio_peer_enabled = bool(ratio_settings.get("use_as_peer_variable", False))
    ratio_quality_gate = dict(ratio_settings.get("quality_gate", {}))
    min_peer_ratio_rows = float(ratio_quality_gate.get("min_peer_ratio_rows", MIN_RATIO_ROWS))
    min_peer_ratio_mad = float(ratio_quality_gate.get("min_peer_ratio_mad", 0.001))
    behavior_enabled = bool(profile.get("behavior_peer_enabled", behavior_peer_enabled(effective_derived)))
    if behavior_enabled:
        behavior = build_behavior_clusters(history)
        history = assign_behavior_clusters(history, behavior)
        scoring = assign_behavior_clusters(scoring, behavior)
    scoring["_row_id"] = np.arange(len(scoring))

    scoring_ord = int(scoring["month_ord"].iloc[0])
    self_stats = build_self_stats(history, scoring_ord)
    scoring = scoring.merge(self_stats, on="customer_id", how="left")
    peer_rules = peer_config or adaptive.PeerSelectionConfig()
    support_rules = support_thresholds or adaptive.PeerSupportThresholds(
        min_history_rows=MIN_HIST_ROWS,
        min_season_rows=MIN_MOY_ROWS,
        min_recent_rows=MIN_RECENT_ROWS,
        min_current_rows=MIN_CURRENT_ROWS,
        min_distribution_score=MIN_PEER_DISTRIBUTION_SCORE,
    )
    weight_config = merged_scoring_weights(scoring_weights)
    aggregation_config = merged_score_aggregation(score_aggregation)
    peer_levels = adaptive.build_adaptive_peer_candidates(
        scoring,
        has_turnover_signal=bool(ratio_peer_enabled and history["turnover_for_model"].notna().any()),
        config=peer_rules,
    )

    scored_parts: list[pd.DataFrame] = []
    assigned_row_ids: set[int] = set()
    peer_attempts: dict[int, list[str]] = {}

    for peer_order, peer_candidate in enumerate(peer_levels):
        level_name = peer_candidate.name
        cols = list(peer_candidate.columns)
        remaining = scoring.copy()
        if len(remaining) == 0:
            break
        stats = build_level_stats(history, remaining, scoring_month, cols)
        key = group_key(cols)
        candidate = remaining.merge(stats, on=key, how="left")
        passes = adaptive.support_mask(candidate, peer_candidate, support_rules, peer_rules.blocked_values)
        failed_reason = (
            f"{level_name}: support thresholds not passed "
            f"(hist>={support_rules.min_history_rows}, season>={support_rules.min_season_rows}, "
            f"recent>={support_rules.min_recent_rows}, current>={support_rules.min_current_rows}, "
            f"distribution>={support_rules.min_distribution_score:.0f})"
        )
        for row_id in candidate.loc[~passes, "_row_id"].astype(int).to_numpy():
            peer_attempts.setdefault(int(row_id), []).append(failed_reason)
        if not bool(passes.any()):
            continue

        selected = candidate.loc[passes].copy()
        selected["peer_candidate_order"] = peer_order
        selected["peer_group_level_name"] = level_name
        selected["peer_group_columns"] = "+".join(cols) if cols else "global"
        selected["peer_representability_score"] = adaptive.representability_score_frame(
            selected,
            peer_candidate,
            support_rules,
            peer_rules,
        )
        selected["peer_representability_status"] = adaptive.representability_status_series(
            selected["peer_representability_score"],
            peer_candidate,
            peer_rules,
        )
        selected["peer_support_score"] = adaptive.support_strength_score_frame(selected, support_rules)
        selected["peer_stability_score"] = adaptive.stability_score_frame(selected)
        selected["peer_specificity_score"] = 100.0 * adaptive.specificity_score(peer_candidate, peer_rules)
        selected["peer_objective_score"] = adaptive.objective_score_frame(
            selected,
            peer_candidate,
            support_rules,
            peer_rules,
        )
        selected["expected_log_bill"] = selected["recent_median"] + (selected["moy_median"] - selected["hist_median"])
        selected["expected_bill_amount"] = np.expm1(selected["expected_log_bill"])
        selected["current_peer_median_bill"] = np.expm1(selected["current_median"])
        selected["prior_median_bill"] = np.where(
            selected["prior_median"].notna(),
            np.expm1(selected["prior_median"]),
            np.nan,
        )
        selected["actual_to_expected_ratio"] = selected["bill_amount"] / np.maximum(
            selected["expected_bill_amount"],
            1e-6,
        )
        selected["bill_to_turnover_ratio"] = np.where(
            selected["turnover_for_model"].astype(float).gt(0),
            selected["bill_amount"] / selected["turnover_for_model"].astype(float),
            np.nan,
        )
        selected["peer_seasonality_adjustment_log"] = selected["moy_median"] - selected["hist_median"]
        selected["peer_trend_expected_log"] = np.where(
            selected["peer_trend_n"].fillna(0).ge(MIN_HIST_ROWS),
            selected["peer_trend_intercept"] + selected["peer_trend_slope"] * selected["month_ord"].astype(float),
            np.nan,
        )
        selected["peer_trend_expected_bill"] = np.where(
            pd.notna(selected["peer_trend_expected_log"]),
            np.expm1(selected["peer_trend_expected_log"]),
            np.nan,
        )
        selected["customer_trend_expected_log"] = np.where(
            selected["customer_trend_n"].fillna(0).ge(MIN_CUSTOMER_TREND_ROWS),
            selected["customer_trend_intercept"] + selected["customer_trend_slope"] * selected["month_ord"].astype(float),
            np.nan,
        )
        selected["customer_trend_expected_bill"] = np.where(
            pd.notna(selected["customer_trend_expected_log"]),
            np.expm1(selected["customer_trend_expected_log"]),
            np.nan,
        )
        selected["customer_seasonal_expected_bill"] = np.where(
            selected["customer_seasonal_n"].fillna(0).ge(MIN_CUSTOMER_SEASONAL_ROWS),
            np.expm1(selected["customer_seasonal_median"]),
            np.nan,
        )
        selected["customer_recent3_median_bill"] = np.where(
            selected["customer_recent3_median"].notna(),
            np.expm1(selected["customer_recent3_median"]),
            np.nan,
        )
        selected["customer_recent3_range_log"] = (
            selected["customer_recent3_max"].astype(float) - selected["customer_recent3_min"].astype(float)
        )
        selected["historical_scale"] = np.nanmax(
            np.vstack(
                [
                    selected["hist_mad"].to_numpy(dtype=float),
                    selected["recent_mad"].to_numpy(dtype=float),
                    selected["moy_mad"].to_numpy(dtype=float),
                ]
            ),
            axis=0,
        )
        selected["historical_scale"] = np.maximum(selected["historical_scale"], MIN_LOG_SCALE)
        selected["current_scale"] = np.maximum(selected["current_mad"].astype(float), MIN_LOG_SCALE)
        selected["historical_peer_z"] = (selected["log_bill"] - selected["expected_log_bill"]) / selected["historical_scale"]
        selected["current_peer_z"] = (selected["log_bill"] - selected["current_median"]) / selected["current_scale"]
        selected["peer_trend_z"] = np.where(
            selected["peer_trend_expected_log"].notna(),
            (selected["log_bill"] - selected["peer_trend_expected_log"]) / selected["historical_scale"],
            np.nan,
        )
        ratio_peer_gate = (
            ratio_signal_enabled
            & selected["bill_to_turnover_log_ratio"].notna()
            & selected["ratio_n"].fillna(0).ge(min_peer_ratio_rows)
            & selected["ratio_mad"].fillna(0).ge(min_peer_ratio_mad)
        )
        selected["turnover_intensity_z"] = np.where(
            ratio_peer_gate,
            (selected["bill_to_turnover_log_ratio"] - selected["ratio_median"]) / np.maximum(selected["ratio_mad"].astype(float), MIN_LOG_SCALE),
            np.nan,
        )
        selected["ratio_numerator_source_col"] = str(ratio_settings.get("numerator_source_col", "bill_amount"))
        selected["ratio_denominator_source_col"] = str(ratio_settings.get("denominator_source_col", ""))
        selected["feature_ratio_enabled"] = bool(ratio_settings.get("enabled", False))
        selected["feature_ratio_signal_enabled"] = ratio_signal_enabled
        selected["feature_ratio_signal_requested"] = bool(ratio_settings.get("use_as_anomaly_signal_requested", ratio_signal_enabled))
        selected["feature_ratio_quality_gate_passed"] = bool(ratio_settings.get("quality_gate_passed", ratio_signal_enabled))
        selected["feature_ratio_quality_gate_reasons"] = str(ratio_settings.get("quality_gate_reasons", "passed"))
        selected["feature_ratio_peer_gate_passed"] = ratio_peer_gate
        selected["feature_ratio_peer_gate_reasons"] = np.select(
            [
                ~selected["feature_ratio_enabled"],
                selected["feature_ratio_signal_requested"] & ~selected["feature_ratio_quality_gate_passed"],
                ratio_signal_enabled & selected["bill_to_turnover_log_ratio"].isna(),
                ratio_signal_enabled & selected["ratio_n"].fillna(0).lt(min_peer_ratio_rows),
                ratio_signal_enabled & selected["ratio_mad"].fillna(0).lt(min_peer_ratio_mad),
                ratio_peer_gate,
            ],
            [
                "feature_ratio_disabled",
                selected["feature_ratio_quality_gate_reasons"],
                "customer_ratio_missing",
                "peer_ratio_rows_below_gate",
                "peer_ratio_mad_below_gate",
                "passed",
            ],
            default="not_requested",
        )
        selected["feature_ratio_peer_enabled"] = ratio_peer_enabled
        selected["self_history_z"] = np.where(
            selected["prior_n"].ge(MIN_SELF_HISTORY_ROWS),
            (selected["log_bill"] - selected["prior_median"]) / np.maximum(selected["prior_mad"].astype(float), MIN_LOG_SCALE),
            np.nan,
        )
        selected["customer_trend_z"] = np.where(
            selected["customer_trend_expected_log"].notna(),
            (selected["log_bill"] - selected["customer_trend_expected_log"]) / np.maximum(selected["prior_mad"].astype(float), MIN_LOG_SCALE),
            np.nan,
        )
        selected["customer_seasonal_z"] = np.where(
            selected["customer_seasonal_n"].fillna(0).ge(MIN_CUSTOMER_SEASONAL_ROWS),
            (selected["log_bill"] - selected["customer_seasonal_median"])
            / np.maximum(selected["customer_seasonal_mad"].astype(float), MIN_LOG_SCALE),
            np.nan,
        )
        recent_regime_config = dict(aggregation_config.get("recent_regime", {}))
        recent_regime_ok = (
            bool(recent_regime_config.get("enabled", True))
            & selected["customer_recent3_n"].fillna(0).ge(
                float(recent_regime_config.get("min_recent_months", MIN_CUSTOMER_RECENT_REGIME_ROWS))
            )
            & selected["customer_recent3_range_log"].le(
                float(recent_regime_config.get("max_recent_range_log", MAX_CUSTOMER_RECENT_REGIME_RANGE_LOG))
            )
            & selected["customer_recent3_median"].notna()
        )
        selected["customer_recent_regime_z"] = np.where(
            recent_regime_ok,
            (selected["log_bill"] - selected["customer_recent3_median"])
            / np.maximum(selected["customer_recent3_mad"].astype(float), MIN_LOG_SCALE),
            np.nan,
        )
        selected["gap_months_before_scoring"] = np.where(
            selected["last_month_ord"].notna(),
            np.maximum(selected["month_ord"].astype(float) - selected["last_month_ord"].astype(float) - 1.0, 0.0),
            np.nan,
        )
        selected["prior_12_coverage"] = np.where(
            selected["prior_n"].fillna(0).gt(0),
            np.minimum(selected["prior_12_n"].fillna(0).astype(float) / 12.0, 1.0),
            0.0,
        )
        gap_component = np.minimum(selected["gap_months_before_scoring"].fillna(12).astype(float) / 6.0 * 100.0, 100.0)
        coverage_component = (1.0 - selected["prior_12_coverage"].astype(float)) * 100.0
        selected["data_gap_score"] = np.maximum(gap_component, coverage_component)
        selected["is_new_customer_in_scoring_month"] = selected["prior_n"].fillna(0).eq(0)
        selected["is_disconnected_customer"] = selected["gap_months_before_scoring"].fillna(999).ge(2) | selected[
            "prior_12_coverage"
        ].lt(0.50)
        selected["model_fill_policy"] = "NO_MAIN_METRIC_FILLING"
        selected["scoreability_status"] = np.select(
            [
                selected["is_new_customer_in_scoring_month"],
                selected["prior_n"].fillna(0).lt(MIN_SELF_HISTORY_ROWS),
                selected["customer_trend_z"].notna() & selected["customer_seasonal_z"].notna(),
                selected["customer_trend_z"].notna(),
                selected["customer_recent_regime_z"].notna(),
                selected["self_history_z"].notna(),
            ],
            [
                "PEER_ONLY_NEW_CUSTOMER",
                "PEER_ONLY_SPARSE_CUSTOMER_HISTORY",
                "CUSTOMER_TREND_AND_SEASONAL_AVAILABLE",
                "CUSTOMER_TREND_AVAILABLE",
                "CUSTOMER_RECENT_REGIME_AVAILABLE",
                "CUSTOMER_HISTORY_AVAILABLE",
            ],
            default="PEER_ONLY_NO_CUSTOMER_HISTORY",
        )

        hist_component = score_from_z(selected["historical_peer_z"])
        current_component = score_from_z(selected["current_peer_z"])
        peer_trend_component = score_from_z(selected["peer_trend_z"])
        turnover_component = score_from_z(selected["turnover_intensity_z"])
        self_component = score_from_z(selected["self_history_z"])
        customer_trend_component = score_from_z(selected["customer_trend_z"])
        customer_seasonal_component = score_from_z(selected["customer_seasonal_z"])
        customer_recent_regime_component = score_from_z(selected["customer_recent_regime_z"])

        has_self = selected["self_history_z"].notna().to_numpy()
        has_customer_trend = selected["customer_trend_z"].notna().to_numpy()
        has_customer_seasonal = selected["customer_seasonal_z"].notna().to_numpy()
        has_peer_trend = selected["peer_trend_z"].notna().to_numpy()
        has_turnover = selected["turnover_intensity_z"].notna().to_numpy()
        selected["historical_peer_score"] = hist_component
        selected["current_peer_score"] = current_component
        selected["peer_trend_score"] = peer_trend_component
        selected["turnover_intensity_score"] = turnover_component
        selected["self_history_score"] = self_component
        selected["customer_trend_score"] = customer_trend_component
        selected["customer_seasonal_score"] = customer_seasonal_component
        selected["customer_recent_regime_score"] = customer_recent_regime_component

        selected["customer_explainability_score"] = [
            customer_explainability_score(
                row,
                bool(has_self[idx]),
                bool(has_customer_trend[idx]),
                bool(has_customer_seasonal[idx]),
                weight_config,
            )
            for idx, (_, row) in enumerate(selected.iterrows())
        ]
        selected["customer_explainability_status"] = selected["customer_explainability_score"].map(customer_explainability_status)
        selected = apply_directional_evidence_aggregation(selected, aggregation_config)
        selected["scoring_strategy"] = selected["evidence_driver"]

        scored_parts.append(selected)

    if scored_parts:
        candidate_scores = pd.concat(scored_parts, ignore_index=True)
        candidate_counts = (
            candidate_scores.groupby("_row_id").size().rename("peer_eligible_candidate_count").reset_index()
        )
        selection_mode = str(peer_rules.selection_mode).lower()
        sort_columns = [
            "_row_id",
            "peer_objective_score",
            "peer_calibration_score",
            "peer_representability_score",
            "peer_distribution_quality_score",
            "peer_specificity_score",
        ]
        sort_ascending = [True, False, False, False, False, False]
        if selection_mode in {"first_supported", "first_pass", "narrow_first"}:
            sort_columns = ["_row_id", "peer_candidate_order"]
            sort_ascending = [True, True]
        scores = (
            candidate_scores.sort_values(
                sort_columns,
                ascending=sort_ascending,
            )
            .drop_duplicates("_row_id", keep="first")
            .reset_index(drop=True)
        )
        scores = scores.merge(candidate_counts, on="_row_id", how="left")
        scores["peer_selection_reason"] = scores.apply(
            lambda row: adaptive.selection_reason(
                row,
                adaptive.PeerCandidate(
                    name=str(row.get("peer_group_level_name", "global")),
                    columns=tuple()
                    if str(row.get("peer_group_columns", "global")) == "global"
                    else tuple(str(row.get("peer_group_columns", "")).split("+")),
                ),
                peer_attempts.get(int(row["_row_id"]), []),
            ),
            axis=1,
        )
        scores["peer_selection_reason"] = scores["peer_selection_reason"].astype(str) + (
            " Objective function ile "
            + scores["peer_eligible_candidate_count"].fillna(1).astype(int).astype(str)
            + " uygun aday arasindan secildi."
        )
        assigned_row_ids = set(scores["_row_id"].astype(int).tolist())
    else:
        scores = pd.DataFrame()

    not_scored = scoring.loc[~scoring["_row_id"].isin(assigned_row_ids)].copy()
    if len(not_scored):
        not_scored["not_scored_reason"] = np.where(
            not_scored["valid_bill_for_model"],
            "INSUFFICIENT_PEER_SUPPORT",
            "INVALID_MAIN_METRIC",
        )
        not_scored["is_new_customer_in_scoring_month"] = not_scored["prior_n"].fillna(0).eq(0)
        not_scored["gap_months_before_scoring"] = np.where(
            not_scored["last_month_ord"].notna(),
            np.maximum(not_scored["month_ord"].astype(float) - not_scored["last_month_ord"].astype(float) - 1.0, 0.0),
            np.nan,
        )
        not_scored["prior_12_coverage"] = np.where(
            not_scored["prior_n"].fillna(0).gt(0),
            np.minimum(not_scored["prior_12_n"].fillna(0).astype(float) / 12.0, 1.0),
            0.0,
        )
        not_scored["is_disconnected_customer"] = not_scored["gap_months_before_scoring"].fillna(999).ge(2) | not_scored[
            "prior_12_coverage"
        ].lt(0.50)
        not_scored["scoreability_status"] = np.where(
            not_scored["valid_bill_for_model"],
            "NOT_SCORED_INSUFFICIENT_PEER_SUPPORT",
            "NOT_SCORED_INVALID_MAIN_METRIC",
        )
        not_scored["model_fill_policy"] = "NO_MAIN_METRIC_FILLING"

    if len(scores):
        scores = label_scores(scores, watch_top_rate, high_top_rate, aggregation_config.get("label_guardrails", {}))
        scores["peer_alignment_status"] = scores.apply(peer_alignment_status, axis=1)
        scores["peer_alignment_direction"] = scores.apply(peer_alignment_direction, axis=1)
        scores["peer_alignment_peer_z"] = scores.apply(peer_alignment_peer_z, axis=1)
        scores["peer_alignment_customer_z"] = scores.apply(peer_alignment_customer_z, axis=1)
        scores["reason_codes"] = scores.apply(build_reason_codes, axis=1)
        scores["signal_consistency"] = scores.apply(signal_consistency, axis=1)
        scores["evidence_strength"] = scores.apply(evidence_strength, axis=1)
        scores["action_label"] = scores.apply(action_label, axis=1)
        scores["reason_explanation"] = scores.apply(reason_explanation, axis=1)
        scores = add_challenger_diagnostics(scores, aggregation_config)
        thresholds = {
            "watchlist_threshold": float(scores["watchlist_threshold_used"].iloc[0]),
            "high_anomaly_threshold": float(scores["high_anomaly_threshold_used"].iloc[0]),
        }
        thresholds.update(
            {
                f"label_guardrail_{key}": value
                for key, value in dict(aggregation_config.get("label_guardrails", {})).items()
                if isinstance(value, (bool, int, float, str))
            }
        )
        dynamic_peer_cols = list(
            dict.fromkeys(col for candidate in peer_levels for col in candidate.columns if col in scores.columns)
        )
        keep_cols = [
            "customer_id",
            "branch_id",
            "calendar_month",
            "invoice_month",
            "customer_segment",
            "sector",
            "turnover_bucket",
            "behavior_cluster",
            "behavior_history_n",
            "behavior_level_bucket",
            "behavior_volatility_bucket",
            "behavior_trend_bucket",
            "behavior_median_bill",
            "behavior_volatility_log",
            "behavior_trend_slope",
            "active_subscriber",
            "active_subscriber_bucket",
            "active_subscriber_missing_flag",
            "bill_amount",
            "turnover_amt",
            "ratio_numerator_source_col",
            "ratio_denominator_source_col",
            "feature_ratio_enabled",
            "feature_ratio_signal_enabled",
            "feature_ratio_signal_requested",
            "feature_ratio_quality_gate_passed",
            "feature_ratio_quality_gate_reasons",
            "feature_ratio_peer_gate_passed",
            "feature_ratio_peer_gate_reasons",
            "feature_ratio_peer_enabled",
            "log_bill",
            "expected_log_bill",
            "expected_bill_amount",
            "current_peer_median_bill",
            "prior_median_bill",
            "peer_seasonality_adjustment_log",
            "peer_trend_expected_bill",
            "customer_trend_expected_bill",
            "customer_seasonal_expected_bill",
            "customer_recent3_median_bill",
            "customer_recent3_range_log",
            "actual_to_expected_ratio",
            "bill_to_turnover_ratio",
            "historical_peer_z",
            "current_peer_z",
            "peer_trend_z",
            "turnover_intensity_z",
            "self_history_z",
            "customer_trend_z",
            "customer_seasonal_z",
            "customer_recent_regime_z",
            "historical_peer_score",
            "current_peer_score",
            "peer_trend_score",
            "turnover_intensity_score",
            "self_history_score",
            "customer_trend_score",
            "customer_seasonal_score",
            "customer_recent_regime_score",
            "customer_signal_score",
            "peer_signal_score",
            "customer_family_p_value",
            "peer_family_p_value",
            "customer_family_direction",
            "peer_family_direction",
            "customer_reliability_status",
            "peer_reliability_status",
            "primary_signal_name",
            "primary_signal_family",
            "primary_signal_p_value",
            "primary_signal_score",
            "secondary_signal_name",
            "secondary_signal_p_value",
            "evidence_driver",
            "evidence_conflict_flag",
            "customer_explainability_score",
            "customer_explainability_status",
            "peer_representability_score",
            "peer_representability_status",
            "peer_objective_score",
            "peer_support_score",
            "peer_stability_score",
            "peer_specificity_score",
            "peer_calibration_score",
            "peer_calibration_n",
            "peer_calibration_abs_residual_median",
            "peer_calibration_interval_coverage",
            "peer_calibration_false_alarm_rate",
            "peer_eligible_candidate_count",
            "peer_distribution_quality_score",
            "peer_distribution_status",
            "peer_log_ratio_skew",
            "peer_log_ratio_kurtosis",
            "peer_tail_rate",
            "peer_selection_reason",
            "scoring_strategy",
            "model_challenger_score",
            "model_challenger_warning",
            "pca_challenger_anomaly_flag",
            "if_challenger_anomaly_flag",
            "lof_challenger_anomaly_flag",
            "final_anomaly_score",
            "score_percentile",
            "confidence",
            "anomaly_direction",
            "anomaly_label",
            "is_high_anomaly",
            "is_watchlist_or_anomaly",
            "reason_codes",
            "reason_explanation",
            "signal_consistency",
            "peer_alignment_status",
            "peer_alignment_direction",
            "peer_alignment_peer_z",
            "peer_alignment_customer_z",
            "evidence_strength",
            "action_label",
            "peer_group_level_name",
            "peer_group_columns",
            "hist_n",
            "moy_n",
            "recent_n",
            "current_n",
            "ratio_n",
            "prior_n",
            "prior_12_n",
            "customer_trend_n",
            "customer_seasonal_n",
            "customer_recent3_n",
            "prior_12_coverage",
            "gap_months_before_scoring",
            "data_gap_score",
            "is_new_customer_in_scoring_month",
            "is_disconnected_customer",
            "scoreability_status",
            "model_fill_policy",
            "source_row_count",
        ]
        keep_cols.extend(col for col in dynamic_peer_cols if col not in keep_cols)
        scores = scores[[col for col in keep_cols if col in scores.columns]].sort_values(
            "final_anomaly_score", ascending=False
        )
    else:
        thresholds = {"watchlist_threshold": float("nan"), "high_anomaly_threshold": float("nan")}

    return ModelRun(
        scoring_month=scoring_month,
        train_rows=int(len(history)),
        scoring_rows=int(len(scoring)),
        scores=scores,
        not_scored=not_scored,
        thresholds=thresholds,
    )


def label_scores(
    scores: pd.DataFrame,
    watch_top_rate: float,
    high_top_rate: float,
    label_guardrails: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    out = scores.copy()
    watch_thr = max(60.0, float(out["final_anomaly_score"].quantile(max(0.0, 1.0 - watch_top_rate))))
    high_thr = max(80.0, float(out["final_anomaly_score"].quantile(max(0.0, 1.0 - high_top_rate))))
    out["watchlist_threshold_used"] = watch_thr
    out["high_anomaly_threshold_used"] = high_thr
    out["score_percentile"] = out["final_anomaly_score"].rank(pct=True)

    guardrails = dict(label_guardrails or {})
    if bool(guardrails.get("enabled", False)):
        signal_z_cols = [
            "self_history_z",
            "customer_trend_z",
            "customer_seasonal_z",
            "customer_recent_regime_z",
            "historical_peer_z",
            "current_peer_z",
            "peer_trend_z",
            "turnover_intensity_z",
        ]
        z_matrix = np.vstack(
            [
                pd.to_numeric(out.get(col, pd.Series(np.nan, index=out.index)), errors="coerce")
                .fillna(0.0)
                .abs()
                .to_numpy(dtype=float)
                for col in signal_z_cols
            ]
        )
        max_abs_z = np.nanmax(z_matrix, axis=0)
        ratio = pd.to_numeric(out.get("actual_to_expected_ratio", pd.Series(np.nan, index=out.index)), errors="coerce")
        log_effect = np.log(np.maximum(ratio.to_numpy(dtype=float), 1e-6))
        abs_log_effect = np.abs(np.where(np.isfinite(log_effect), log_effect, 0.0))
        out["label_guardrail_max_abs_z"] = max_abs_z
        out["label_guardrail_abs_log_effect"] = abs_log_effect
        watch_effect_ok = (
            (max_abs_z >= float(guardrails.get("min_watch_abs_z", 1.50)))
            & (abs_log_effect >= float(guardrails.get("min_watch_log_effect", 0.14)))
        )
        high_effect_ok = (
            (max_abs_z >= float(guardrails.get("min_high_abs_z", 2.50)))
            & (abs_log_effect >= float(guardrails.get("min_high_log_effect", 0.26)))
        )
    else:
        out["label_guardrail_max_abs_z"] = np.nan
        out["label_guardrail_abs_log_effect"] = np.nan
        watch_effect_ok = np.ones(len(out), dtype=bool)
        high_effect_ok = np.ones(len(out), dtype=bool)

    high = out["final_anomaly_score"].ge(high_thr) & high_effect_ok
    watch = out["final_anomaly_score"].ge(watch_thr) & watch_effect_ok & ~high
    out["anomaly_label"] = "NORMAL"
    out.loc[watch & out["anomaly_direction"].eq("HIGH"), "anomaly_label"] = "WATCHLIST_HIGH"
    out.loc[watch & out["anomaly_direction"].eq("LOW"), "anomaly_label"] = "WATCHLIST_LOW"
    out.loc[high & out["anomaly_direction"].eq("HIGH"), "anomaly_label"] = "HIGH_MAIN_METRIC_ANOMALY"
    out.loc[high & out["anomaly_direction"].eq("LOW"), "anomaly_label"] = "LOW_MAIN_METRIC_ANOMALY"
    out["is_high_anomaly"] = out["anomaly_label"].isin(["HIGH_MAIN_METRIC_ANOMALY", "LOW_MAIN_METRIC_ANOMALY"])
    out["is_watchlist_or_anomaly"] = out["anomaly_label"].ne("NORMAL")
    return out


def build_reason_codes(row: pd.Series) -> str:
    reasons: list[str] = []
    if abs(float(row.get("historical_peer_z", 0.0))) >= 2.5:
        reasons.append("HISTORICAL_PEER_JUMP" if row["historical_peer_z"] > 0 else "HISTORICAL_PEER_DROP")
    if abs(float(row.get("current_peer_z", 0.0))) >= 2.5:
        reasons.append("CURRENT_PEER_JUMP" if row["current_peer_z"] > 0 else "CURRENT_PEER_DROP")
    peer_trend_z = row.get("peer_trend_z", np.nan)
    if pd.notna(peer_trend_z) and abs(float(peer_trend_z)) >= 2.5:
        reasons.append("PEER_TREND_JUMP" if peer_trend_z > 0 else "PEER_TREND_DROP")
    turnover_z = row.get("turnover_intensity_z", np.nan)
    if pd.notna(turnover_z) and abs(float(turnover_z)) >= 2.5:
        reasons.append("FEATURE_RATIO_HIGH" if turnover_z > 0 else "FEATURE_RATIO_LOW")
    self_z = row.get("self_history_z", np.nan)
    if pd.notna(self_z) and abs(float(self_z)) >= 2.5:
        reasons.append("SELF_HISTORY_JUMP" if self_z > 0 else "SELF_HISTORY_DROP")
        if has_peer_self_conflict(row):
            reasons.append("PEER_SELF_CONFLICT")
    customer_trend_z = row.get("customer_trend_z", np.nan)
    if pd.notna(customer_trend_z) and abs(float(customer_trend_z)) >= 2.5:
        reasons.append("CUSTOMER_TREND_JUMP" if customer_trend_z > 0 else "CUSTOMER_TREND_DROP")
    customer_seasonal_z = row.get("customer_seasonal_z", np.nan)
    if pd.notna(customer_seasonal_z) and abs(float(customer_seasonal_z)) >= 2.5:
        reasons.append("CUSTOMER_SEASONAL_JUMP" if customer_seasonal_z > 0 else "CUSTOMER_SEASONAL_DROP")
    customer_recent_regime_z = row.get("customer_recent_regime_z", np.nan)
    if pd.notna(customer_recent_regime_z) and abs(float(customer_recent_regime_z)) >= 2.5:
        reasons.append(
            "CUSTOMER_RECENT_REGIME_JUMP" if customer_recent_regime_z > 0 else "CUSTOMER_RECENT_REGIME_DROP"
        )
    if has_peer_self_conflict(row) and "PEER_SELF_CONFLICT" not in reasons:
        reasons.append("PEER_SELF_CONFLICT")
    if has_customer_peer_mismatch(row):
        reasons.append("CUSTOMER_STABLE_PEER_MISMATCH")
    alignment_status = row.get("peer_alignment_status", "")
    if alignment_status == "YAPISAL_PEER_ALTINDA_MUSTERI_STABIL":
        reasons.append("STRUCTURAL_PEER_BELOW_SELF_STABLE")
    elif alignment_status == "YAPISAL_PEER_USTUNDE_MUSTERI_STABIL":
        reasons.append("STRUCTURAL_PEER_ABOVE_SELF_STABLE")
    if pd.isna(row.get("prior_n", np.nan)) or float(row.get("prior_n", 0.0)) < MIN_SELF_HISTORY_ROWS:
        reasons.append("CUSTOMER_HISTORY_UNAVAILABLE")
    if pd.isna(row.get("prior_n", np.nan)) or float(row.get("prior_n", 0.0)) == 0:
        reasons.append("FIRST_SEEN_IN_SCORING_MONTH")
    gap_months = row.get("gap_months_before_scoring", np.nan)
    if pd.notna(gap_months) and float(gap_months) >= 2:
        reasons.append("RECENT_DATA_GAP")
    coverage = row.get("prior_12_coverage", np.nan)
    if pd.notna(coverage) and float(coverage) < 0.50:
        reasons.append("LOW_12M_COVERAGE")
    if row.get("peer_group_level_name") in {"segment", "global"}:
        reasons.append("COARSE_PEER_GROUP")
    if float(row.get("confidence", 100.0)) < 55:
        reasons.append("LOW_CONFIDENCE")
    return ", ".join(reasons) if reasons else "NO_MAJOR_DRIVER"


def numeric_or_default(row: pd.Series, col: str, default: float) -> float:
    value = row.get(col, default)
    if pd.isna(value):
        return default
    return float(value)


def max_abs_signal(row: pd.Series, cols: list[str]) -> float:
    values = [abs(float(row.get(col))) for col in cols if pd.notna(row.get(col, np.nan))]
    return max(values) if values else 0.0


def strongest_signed_signal(row: pd.Series, cols: list[str]) -> float:
    values = [float(row.get(col)) for col in cols if pd.notna(row.get(col, np.nan))]
    if not values:
        return 0.0
    return max(values, key=lambda value: abs(value))


def has_peer_self_conflict(row: pd.Series) -> bool:
    peer_signal = strongest_signed_signal(row, ["historical_peer_z", "current_peer_z", "peer_trend_z", "turnover_intensity_z"])
    customer_signal = strongest_signed_signal(
        row,
        ["self_history_z", "customer_trend_z", "customer_seasonal_z", "customer_recent_regime_z"],
    )
    if abs(peer_signal) >= 2.5 and abs(customer_signal) >= 2.5 and (peer_signal * customer_signal) < 0:
        return True
    if abs(customer_signal) < 2.5:
        return False
    direction = row.get("anomaly_direction", "")
    return (direction == "HIGH" and customer_signal < 0) or (direction == "LOW" and customer_signal > 0)


def has_customer_peer_mismatch(row: pd.Series) -> bool:
    if row.get("anomaly_label", "NORMAL") == "NORMAL":
        return False
    if numeric_or_default(row, "prior_n", 0.0) < MIN_CUSTOMER_TREND_ROWS:
        return False
    peer_signal = max_abs_signal(row, ["historical_peer_z", "current_peer_z", "peer_trend_z", "turnover_intensity_z"])
    customer_signal = max_abs_signal(row, ["self_history_z", "customer_trend_z", "customer_seasonal_z", "customer_recent_regime_z"])
    return peer_signal >= 2.5 and customer_signal < 1.5


def peer_alignment(row: pd.Series) -> tuple[str, str, float, float]:
    if numeric_or_default(row, "prior_n", 0.0) < MIN_CUSTOMER_TREND_ROWS:
        return ("MUSTERI_GECMISI_YETERSIZ", "UNKNOWN", np.nan, np.nan)

    peer_signal = strongest_signed_signal(
        row,
        ["historical_peer_z", "current_peer_z", "peer_trend_z", "turnover_intensity_z"],
    )
    customer_signal = strongest_signed_signal(
        row,
        ["self_history_z", "customer_trend_z", "customer_seasonal_z", "customer_recent_regime_z"],
    )
    peer_abs = abs(peer_signal)
    customer_abs = abs(customer_signal)

    if peer_abs < 2.5:
        return ("PEER_UYUMLU", "NONE", peer_signal, customer_signal)
    if customer_abs >= 2.5 and (peer_signal * customer_signal) < 0:
        return ("PEER_MUSTERI_CELISKILI", "HIGH" if peer_signal > 0 else "LOW", peer_signal, customer_signal)
    if customer_abs < 1.5:
        if peer_signal < 0:
            return ("YAPISAL_PEER_ALTINDA_MUSTERI_STABIL", "LOW", peer_signal, customer_signal)
        return ("YAPISAL_PEER_USTUNDE_MUSTERI_STABIL", "HIGH", peer_signal, customer_signal)
    if peer_signal < 0 and customer_signal < 0:
        return ("PEER_ALTINDA_VE_MUSTERI_ICI_DUSUS", "LOW", peer_signal, customer_signal)
    if peer_signal > 0 and customer_signal > 0:
        return ("PEER_USTUNDE_VE_MUSTERI_ICI_YUKSELIS", "HIGH", peer_signal, customer_signal)
    return ("PEER_FARKI_VAR_MUSTERI_SINYALI_ORTA", "HIGH" if peer_signal > 0 else "LOW", peer_signal, customer_signal)


def peer_alignment_status(row: pd.Series) -> str:
    return peer_alignment(row)[0]


def peer_alignment_direction(row: pd.Series) -> str:
    return peer_alignment(row)[1]


def peer_alignment_peer_z(row: pd.Series) -> float:
    return peer_alignment(row)[2]


def peer_alignment_customer_z(row: pd.Series) -> float:
    return peer_alignment(row)[3]


def signal_consistency(row: pd.Series) -> str:
    if row.get("anomaly_label", "NORMAL") == "NORMAL":
        return "normal"
    if has_peer_self_conflict(row):
        return "peer_self_conflict"
    if has_customer_peer_mismatch(row):
        return "customer_stable_peer_mismatch"
    return "consistent_or_peer_only"


def evidence_strength(row: pd.Series) -> str:
    if row.get("anomaly_label", "NORMAL") == "NORMAL":
        return "normal"

    confidence = numeric_or_default(row, "confidence", 0.0)
    prior_n = numeric_or_default(row, "prior_n", 0.0)
    data_gap = numeric_or_default(row, "data_gap_score", 100.0)
    strong_components = 0
    for col in [
        "historical_peer_z",
        "current_peer_z",
        "peer_trend_z",
        "turnover_intensity_z",
        "self_history_z",
        "customer_trend_z",
        "customer_seasonal_z",
        "customer_recent_regime_z",
    ]:
        value = row.get(col, np.nan)
        if pd.notna(value) and abs(float(value)) >= 2.5:
            strong_components += 1

    if has_customer_peer_mismatch(row):
        return "medium" if confidence >= 55 else "weak"
    if has_peer_self_conflict(row):
        return "medium" if confidence >= 55 and strong_components >= 1 else "weak"
    if confidence >= 75 and prior_n >= 6 and data_gap <= 50 and strong_components >= 2:
        return "strong"
    if confidence >= 55 and strong_components >= 1:
        return "medium"
    return "weak"


def action_label(row: pd.Series) -> str:
    label = str(row.get("anomaly_label", "NORMAL"))
    if label == "NORMAL":
        return "NORMAL"

    direction = "HIGH" if "HIGH" in label else "LOW"
    confidence = numeric_or_default(row, "confidence", 0.0)
    prior_n = numeric_or_default(row, "prior_n", 0.0)
    data_gap = numeric_or_default(row, "data_gap_score", 100.0)

    if confidence < 55:
        return f"REVIEW_LOW_CONFIDENCE_{direction}"
    if prior_n < 2 or data_gap >= 80:
        return f"REVIEW_SPARSE_HISTORY_{direction}"
    if has_customer_peer_mismatch(row):
        return f"REVIEW_PEER_ASSIGNMENT_MISMATCH_{direction}"
    if has_peer_self_conflict(row):
        return f"REVIEW_PEER_SELF_CONFLICT_{direction}"
    if label.startswith("WATCHLIST"):
        return f"WATCHLIST_{direction}"
    if row.get("evidence_strength") == "strong":
        return f"LIKELY_{direction}_MAIN_METRIC_ANOMALY"
    return f"REVIEW_{direction}_MAIN_METRIC_ANOMALY"


def fmt_num(value: Any, digits: int = 2) -> str:
    if pd.isna(value):
        return "n/a"
    return f"{float(value):.{digits}f}"


def reason_explanation(row: pd.Series) -> str:
    label = str(row.get("anomaly_label", "NORMAL"))
    action = str(row.get("action_label", "NORMAL"))
    ratio = row.get("actual_to_expected_ratio", np.nan)
    expected = row.get("expected_bill_amount", np.nan)
    actual = row.get("bill_amount", np.nan)
    peer = row.get("peer_group_level_name", "unknown_peer")
    status = row.get("scoreability_status", "unknown_scoreability")
    gap = row.get("data_gap_score", np.nan)
    score_trend = row.get("score_trend_diagnostic", "")

    if label == "NORMAL":
        trend_text = "" if not score_trend or pd.isna(score_trend) else f" Score trend={score_trend}."
        return (
            "No material main metric anomaly. "
            f"Actual/expected ratio={fmt_num(ratio, 3)}, score={fmt_num(row.get('final_anomaly_score'), 1)}, "
            f"confidence={fmt_num(row.get('confidence'), 1)}.{trend_text}"
        )

    if "PEER_ASSIGNMENT_MISMATCH" in action:
        lead = "Peer assignment review: customer is stable by its own history but differs strongly from assigned peer."
    elif "PEER_SELF_CONFLICT" in action:
        lead = "Review required: peer signal and customer-history signal point in opposite directions."
    elif "SPARSE_HISTORY" in action:
        lead = "Review required: customer history is too sparse, so decision is mainly peer-based."
    elif "LOW_CONFIDENCE" in action:
        lead = "Review required: main metric signal exists but support/confidence is low."
    elif action.startswith("LIKELY_HIGH"):
        lead = "Likely high main metric anomaly: value is materially above customer/peer expectation."
    elif action.startswith("LIKELY_LOW"):
        lead = "Likely low main metric anomaly: value is materially below customer/peer expectation."
    elif action.startswith("WATCHLIST"):
        lead = "Watchlist: main metric differs from expectation but does not meet high-anomaly action strength."
    else:
        lead = "Review main metric anomaly: value differs materially from expectation."

    trend_text = "" if not score_trend or pd.isna(score_trend) else f" Score trend={score_trend}."
    return (
        f"{lead} Actual={fmt_num(actual, 2)}, expected={fmt_num(expected, 2)}, "
        f"actual/expected ratio={fmt_num(ratio, 3)}, peer_level={peer}, scoreability={status}, "
        f"data_gap_score={fmt_num(gap, 1)}, reason_codes={row.get('reason_codes', 'n/a')}.{trend_text}"
    )


def summarize_run(run: ModelRun, profile: dict[str, Any]) -> dict[str, Any]:
    scores = run.scores
    label_counts = scores["anomaly_label"].value_counts().to_dict() if len(scores) else {}
    action_counts = scores["action_label"].value_counts().to_dict() if len(scores) and "action_label" in scores else {}
    evidence_counts = (
        scores["evidence_strength"].value_counts().to_dict() if len(scores) and "evidence_strength" in scores else {}
    )
    consistency_counts = (
        scores["signal_consistency"].value_counts().to_dict() if len(scores) and "signal_consistency" in scores else {}
    )
    scoreability_counts = (
        scores["scoreability_status"].value_counts().to_dict() if len(scores) and "scoreability_status" in scores else {}
    )
    has_recurring_customers = int(profile["customers_with_multiple_months"]) > 0
    return {
        "scoring_month": int(run.scoring_month),
        "scoring_month_label": period_label(run.scoring_month),
        "train_rows": run.train_rows,
        "scoring_rows": run.scoring_rows,
        "scored_rows": int(len(scores)),
        "not_scored_rows": int(len(run.not_scored)),
        "profile": profile,
        "thresholds": run.thresholds,
        "label_counts": {str(k): int(v) for k, v in label_counts.items()},
        "action_counts": {str(k): int(v) for k, v in action_counts.items()},
        "evidence_strength_counts": {str(k): int(v) for k, v in evidence_counts.items()},
        "signal_consistency_counts": {str(k): int(v) for k, v in consistency_counts.items()},
        "scoreability_counts": {str(k): int(v) for k, v in scoreability_counts.items()},
        "median_score": float(scores["final_anomaly_score"].median()) if len(scores) else None,
        "p95_score": float(scores["final_anomaly_score"].quantile(0.95)) if len(scores) else None,
        "p99_score": float(scores["final_anomaly_score"].quantile(0.99)) if len(scores) else None,
        "median_confidence": float(scores["confidence"].median()) if len(scores) else None,
        "customers_with_multiple_months": int(profile["customers_with_multiple_months"]),
        "model_limitation": (
            "Recurring customer histories are available, so the self-history component and data-gap score are active."
            if has_recurring_customers
            else (
                "Customer id is unique per row in the current file, so customer self-history is unavailable. "
                "The current run uses peer-only scoring with a confidence cap; stable recurring customer ids "
                "will activate the self-history component."
            )
        ),
    }


def build_diagnostic_tables(prepared: pd.DataFrame, run: ModelRun, backtest_summary: pd.DataFrame) -> dict[str, pd.DataFrame]:
    scores = run.scores.copy()
    tables: dict[str, pd.DataFrame] = {}
    tables["model_feature_audit"] = pd.DataFrame(
        [
            {
                "requirement": "OOT scoring month",
                "status": "YES",
                "fields": "scoring_month, train_rows, scoring_rows",
                "scoring_use": "latest month scored after fitting prior months only",
            },
            {
                "requirement": "Customer self-history",
                "status": "YES",
                "fields": "self_history_z, self_history_score, prior_n",
                "scoring_use": "weighted when customer has at least 2 prior valid bills",
            },
            {
                "requirement": "Customer trend",
                "status": "YES",
                "fields": "customer_trend_z, customer_trend_expected_bill, customer_trend_n",
                "scoring_use": "weighted when customer has at least 6 prior valid bills",
            },
            {
                "requirement": "Customer seasonality",
                "status": "YES",
                "fields": "customer_seasonal_z, customer_seasonal_expected_bill, customer_seasonal_n",
                "scoring_use": "weighted when customer has same-month history",
            },
            {
                "requirement": "Peer trend",
                "status": "YES",
                "fields": "peer_trend_z, peer_trend_expected_bill",
                "scoring_use": "weighted when selected peer has enough historical support",
            },
            {
                "requirement": "Peer seasonality",
                "status": "YES",
                "fields": "peer_seasonality_adjustment_log, expected_bill_amount",
                "scoring_use": "month-of-year peer adjustment in expected value",
            },
            {
                "requirement": "Safe peer fallback",
                "status": "YES",
                "fields": "peer_group_level_name, peer_group_columns, hist_n, current_n",
                "scoring_use": "narrow peers are tried first and broader peers are used if support fails",
            },
            {
                "requirement": "Branch as peer signal",
                "status": "YES_SUPPORT_GATED",
                "fields": "branch_id, peer_group_level_name",
                "scoring_use": "branch is used only when support thresholds pass",
            },
            {
                "requirement": "No main metric filling",
                "status": "YES",
                "fields": "model_fill_policy",
                "scoring_use": "main metric is not filled/interpolated for scoring",
            },
            {
                "requirement": "New customer flag",
                "status": "YES",
                "fields": "is_new_customer_in_scoring_month, scoreability_status",
                "scoring_use": "new customers are peer-only or not-scored depending on peer support",
            },
            {
                "requirement": "Disconnected customer flag",
                "status": "YES",
                "fields": "is_disconnected_customer, gap_months_before_scoring, prior_12_coverage, data_gap_score",
                "scoring_use": "data gaps reduce confidence and drive review labels",
            },
            {
                "requirement": "Human-readable reason",
                "status": "YES",
                "fields": "reason_codes, reason_explanation, action_label",
                "scoring_use": "technical codes and readable explanation are both exported",
            },
            {
                "requirement": "Previous-score trend",
                "status": "YES_DIAGNOSTIC_ONLY",
                "fields": "previous_final_anomaly_score, score_delta_vs_previous_month, score_trend_diagnostic",
                "scoring_use": "reported after scoring and not used in final score",
            },
            {
                "requirement": "Source imputation variable",
                "status": "NOT_IN_CURRENT_FILE",
                "fields": "n/a",
                "scoring_use": "current encrypted_final.csv has no separate imputation column",
            },
        ]
    )
    tables["month_counts"] = (
        prepared.groupby("invoice_month")
        .agg(row_count=("customer_id", "size"), customer_count=("customer_id", "nunique"))
        .reset_index()
        .assign(calendar_month=lambda x: x["invoice_month"].map(period_label))
    )
    if len(scores):
        tables["label_summary"] = (
            scores.groupby("anomaly_label")
            .agg(rows=("customer_id", "size"), median_score=("final_anomaly_score", "median"), median_confidence=("confidence", "median"))
            .reset_index()
            .sort_values("rows", ascending=False)
        )
        tables["peer_level_summary"] = (
            scores.groupby("peer_group_level_name")
            .agg(
                rows=("customer_id", "size"),
                median_score=("final_anomaly_score", "median"),
                high_anomaly_rate=("is_high_anomaly", "mean"),
                watch_or_anomaly_rate=("is_watchlist_or_anomaly", "mean"),
                median_confidence=("confidence", "median"),
            )
            .reset_index()
            .sort_values("rows", ascending=False)
        )
        tables["segment_summary"] = (
            scores.groupby("customer_segment")
            .agg(
                rows=("customer_id", "size"),
                high_anomaly_count=("is_high_anomaly", "sum"),
                watch_or_anomaly_count=("is_watchlist_or_anomaly", "sum"),
                high_anomaly_rate=("is_high_anomaly", "mean"),
                median_score=("final_anomaly_score", "median"),
                median_bill=("bill_amount", "median"),
            )
            .reset_index()
            .sort_values("rows", ascending=False)
        )
        tables["sector_top_anomaly_summary"] = (
            scores.groupby("sector")
            .agg(
                rows=("customer_id", "size"),
                high_anomaly_count=("is_high_anomaly", "sum"),
                watch_or_anomaly_count=("is_watchlist_or_anomaly", "sum"),
                high_anomaly_rate=("is_high_anomaly", "mean"),
                median_score=("final_anomaly_score", "median"),
                p95_score=("final_anomaly_score", lambda s: s.quantile(0.95)),
            )
            .reset_index()
            .sort_values(["high_anomaly_count", "p95_score"], ascending=False)
            .head(50)
        )
        tables["top_anomalies"] = scores.head(500)
        audit_cols = [
            "customer_id",
            "calendar_month",
            "customer_segment",
            "sector",
            "branch_id",
            "turnover_bucket",
            "active_subscriber_bucket",
            "bill_amount",
            "expected_bill_amount",
            "current_peer_median_bill",
            "prior_median_bill",
            "peer_trend_expected_bill",
            "customer_trend_expected_bill",
            "customer_seasonal_expected_bill",
            "customer_recent3_median_bill",
            "customer_recent3_range_log",
            "actual_to_expected_ratio",
            "turnover_amt",
            "bill_to_turnover_ratio",
            "final_anomaly_score",
            "previous_final_anomaly_score",
            "score_delta_vs_previous_month",
            "score_trend_diagnostic",
            "confidence",
            "anomaly_direction",
            "anomaly_label",
            "signal_consistency",
            "evidence_strength",
            "action_label",
            "reason_codes",
            "reason_explanation",
            "historical_peer_z",
            "current_peer_z",
            "peer_trend_z",
            "turnover_intensity_z",
            "self_history_z",
            "customer_trend_z",
            "customer_seasonal_z",
            "customer_recent_regime_z",
            "prior_n",
            "customer_trend_n",
            "customer_seasonal_n",
            "customer_recent3_n",
            "prior_12_coverage",
            "gap_months_before_scoring",
            "data_gap_score",
            "is_new_customer_in_scoring_month",
            "is_disconnected_customer",
            "scoreability_status",
            "model_fill_policy",
            "peer_group_level_name",
            "hist_n",
            "current_n",
            "ratio_n",
        ]
        tables["customer_logic_audit"] = scores.loc[
            scores["anomaly_label"].ne("NORMAL"),
            [col for col in audit_cols if col in scores.columns],
        ].head(200)
        tables["logical_test_summary"] = (
            scores.groupby(
                ["anomaly_label", "signal_consistency", "evidence_strength", "action_label", "scoreability_status"],
                dropna=False,
            )
            .agg(
                rows=("customer_id", "size"),
                median_score=("final_anomaly_score", "median"),
                median_confidence=("confidence", "median"),
                median_actual_to_expected_ratio=("actual_to_expected_ratio", "median"),
                median_data_gap_score=("data_gap_score", "median"),
                median_prior_n=("prior_n", "median"),
            )
            .reset_index()
            .sort_values(["anomaly_label", "rows"], ascending=[True, False])
        )
        tables["scoreability_summary"] = (
            scores.groupby(["scoreability_status", "peer_group_level_name"], dropna=False)
            .agg(
                rows=("customer_id", "size"),
                high_anomaly_count=("is_high_anomaly", "sum"),
                watch_or_anomaly_count=("is_watchlist_or_anomaly", "sum"),
                median_score=("final_anomaly_score", "median"),
                median_confidence=("confidence", "median"),
                median_data_gap_score=("data_gap_score", "median"),
            )
            .reset_index()
            .sort_values(["scoreability_status", "rows"], ascending=[True, False])
        )

        non_normal_ids = scores.loc[scores["anomaly_label"].ne("NORMAL"), "customer_id"].head(40)
        normal_rows = scores.loc[scores["anomaly_label"].eq("NORMAL")]
        normal_control_ids = pd.concat(
            [
                normal_rows["customer_id"].head(10),
                normal_rows["customer_id"].tail(10),
            ],
            ignore_index=True,
        )
        sample_ids = pd.concat([non_normal_ids, normal_control_ids], ignore_index=True).drop_duplicates()
        score_context_cols = [
            "customer_id",
            "anomaly_label",
            "action_label",
            "signal_consistency",
            "evidence_strength",
            "final_anomaly_score",
            "previous_final_anomaly_score",
            "score_delta_vs_previous_month",
            "score_trend_diagnostic",
            "confidence",
            "actual_to_expected_ratio",
            "data_gap_score",
            "scoreability_status",
            "reason_codes",
            "reason_explanation",
        ]
        series_cols = [
            "customer_id",
            "calendar_month",
            "invoice_month",
            "branch_id",
            "customer_segment",
            "sector",
            "active_subscriber",
            "active_subscriber_missing_flag",
            "turnover_bucket",
            "bill_amount",
            "turnover_amt",
        ]
        audited_series = prepared.loc[
            prepared["customer_id"].isin(sample_ids),
            [col for col in series_cols if col in prepared.columns],
        ].copy()
        audited_series["is_scoring_month"] = audited_series["invoice_month"].eq(run.scoring_month)
        audited_series = audited_series.merge(
            scores[[col for col in score_context_cols if col in scores.columns]],
            on="customer_id",
            how="left",
        )
        tables["audited_customer_series"] = audited_series.sort_values(["customer_id", "invoice_month"])
    if len(run.not_scored):
        tables["not_scored_summary"] = (
            run.not_scored["not_scored_reason"].value_counts().rename_axis("not_scored_reason").reset_index(name="rows")
        )
    else:
        tables["not_scored_summary"] = pd.DataFrame(columns=["not_scored_reason", "rows"])
    tables["backtest_summary"] = backtest_summary
    return tables


def run_backtests(
    prepared: pd.DataFrame,
    scoring_month: int,
    backtest_months: int,
    watch_top_rate: float,
    high_top_rate: float,
) -> pd.DataFrame:
    all_months = sorted(prepared["invoice_month"].unique().tolist())
    prior_months = [m for m in all_months if m < scoring_month]
    selected = prior_months[-backtest_months:] if backtest_months > 0 else []
    rows: list[dict[str, Any]] = []
    for month in selected:
        run = score_scoring_month(prepared, int(month), watch_top_rate, high_top_rate)
        scores = run.scores
        rows.append(
            {
                "scoring_month": int(month),
                "calendar_month": period_label(int(month)),
                "train_rows": run.train_rows,
                "scoring_rows": run.scoring_rows,
                "scored_rows": int(len(scores)),
                "not_scored_rows": int(len(run.not_scored)),
                "high_anomaly_count": int(scores["is_high_anomaly"].sum()) if len(scores) else 0,
                "watch_or_anomaly_count": int(scores["is_watchlist_or_anomaly"].sum()) if len(scores) else 0,
                "median_score": float(scores["final_anomaly_score"].median()) if len(scores) else np.nan,
                "p95_score": float(scores["final_anomaly_score"].quantile(0.95)) if len(scores) else np.nan,
                "p99_score": float(scores["final_anomaly_score"].quantile(0.99)) if len(scores) else np.nan,
                "median_confidence": float(scores["confidence"].median()) if len(scores) else np.nan,
                "watchlist_threshold": run.thresholds["watchlist_threshold"],
                "high_anomaly_threshold": run.thresholds["high_anomaly_threshold"],
            }
        )
    return pd.DataFrame(rows)


def write_report_note(output_dir: Path, source_stem: str, summary: dict[str, Any], tables: dict[str, pd.DataFrame]) -> Path:
    label_counts = summary["label_counts"]
    top_label_lines = "\n".join(f"- {k}: {v}" for k, v in label_counts.items())
    if summary["profile"]["customers_with_multiple_months"] > 0:
        history_text = (
            f"The current CSV has {summary['profile']['customer_count']} customer ids and "
            f"{summary['profile']['customers_with_multiple_months']} ids with more than one month. "
            "Customer self-history is active where prior history is sufficient, and data-gap score is reported separately."
        )
        limitation_text = (
            "Main metric anomaly and data-gap anomaly are separate. A row can have a high data_gap_score because the customer "
            "history is sparse even when the main metric is not anomalous."
        )
    else:
        history_text = (
            f"The current CSV has {summary['profile']['customer_count']} customer ids and "
            "0 ids with more than one month. Because the customer id is unique per row, this run cannot validate "
            "customer self-history or disconnected customer-month patterns."
        )
        limitation_text = (
            "This file does not contain recurring customer histories. Treat customer-level conclusions as peer-relative "
            "invoice anomalies, not confirmed customer time-series anomalies, until a stable customer key is supplied."
        )
    ratio_cfg = dict(summary["profile"].get("feature_ratio", {}))
    ratio_text = (
        "Feature ratio: enabled; main metric is compared with selected reference feature "
        f"{ratio_cfg.get('denominator_source_col', 'n/a')} when peer support is sufficient."
        if summary["profile"].get("feature_ratio_signal_enabled", False)
        else "Feature ratio: disabled; no ratio-derived signal is used in final evidence aggregation."
    )
    peer_text = (
        "Peer hierarchy: selected segment variables and enabled derived peer variables are tried first, "
        "then broader fallbacks down to global. A narrow peer is used only when support thresholds pass."
    )
    note = f"""# Generic Peer Anomaly Technical Note

## Technical summary

Scoring month is {summary["scoring_month_label"]}. The model trained only on prior months and scored {summary["scored_rows"]} rows in the scoring month. High anomaly threshold is {summary["thresholds"]["high_anomaly_threshold"]:.2f}; watchlist threshold is {summary["thresholds"]["watchlist_threshold"]:.2f}.

{history_text}

## Label counts

{top_label_lines}

## Method

- Holdout: the latest DONEM_AY is treated as out-of-time implementation data.
- {peer_text}
- Expected value: recent peer level plus historical month-of-year adjustment, fit only from months before the scoring month.
- Customer-first layer: when enough customer history exists, customer self-history, customer trend, and customer same-month seasonality receive higher combined weight than peer signals.
- Peer fallback layer: when customer history is sparse or missing, peer history, peer trend, current-month peer distribution, and enabled feature-ratio evidence carry the score.
- Current peer check: same scoring-month peer robust z-score, used only for batch monthly scoring.
- {ratio_text}
- Score: evidence aggregation of customer self-history, customer trend, customer seasonality, recent regime, historical peer, peer trend, current peer, and enabled feature-ratio signal when available.
- Data gap: separate score from gap months and prior 12-month coverage.
- Action labels: likely main-metric anomaly is reserved for rows with sufficient evidence; low confidence, sparse history, and peer/self-history conflicts are routed to review labels.
- Score trend diagnostic: previous-month model score deltas are reported as diagnostic-only fields and are not used in the final score.
- Diagnostics: customer_logic_audit and audited_customer_series show the customer-level evidence behind each decision.

## Main limitation

{limitation_text}
"""
    path = output_dir / f"{source_stem}_peer_anomaly_technical_note.md"
    path.write_text(note, encoding="utf-8")
    return path


def write_outputs(
    output_dir: Path,
    source_stem: str,
    prepared: pd.DataFrame,
    run: ModelRun,
    backtest_summary: pd.DataFrame,
    profile: dict[str, Any],
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_run(run, profile)
    tables = build_diagnostic_tables(prepared, run, backtest_summary)

    scores_path = output_dir / f"{source_stem}_anomaly_scores_oot_{run.scoring_month}.csv"
    top_path = output_dir / f"{source_stem}_top_anomalies_oot_{run.scoring_month}.csv"
    not_scored_path = output_dir / f"{source_stem}_not_scored_oot_{run.scoring_month}.csv"
    diagnostics_path = output_dir / f"{source_stem}_anomaly_diagnostics_{run.scoring_month}.xlsx"
    summary_path = output_dir / f"{source_stem}_anomaly_summary_{run.scoring_month}.json"

    run.scores.to_csv(scores_path, index=False, encoding="utf-8-sig")
    tables.get("top_anomalies", pd.DataFrame()).to_csv(top_path, index=False, encoding="utf-8-sig")
    run.not_scored.to_csv(not_scored_path, index=False, encoding="utf-8-sig")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with pd.ExcelWriter(diagnostics_path, engine="openpyxl") as writer:
        pd.DataFrame([summary["profile"]]).to_excel(writer, index=False, sheet_name="data_profile")
        pd.DataFrame([summary["thresholds"]]).to_excel(writer, index=False, sheet_name="thresholds")
        for name, table in tables.items():
            table.to_excel(writer, index=False, sheet_name=name[:31])

    note_path = write_report_note(output_dir, source_stem, summary, tables)
    return {
        "scores_csv": str(scores_path),
        "top_anomalies_csv": str(top_path),
        "not_scored_csv": str(not_scored_path),
        "diagnostics_xlsx": str(diagnostics_path),
        "summary_json": str(summary_path),
        "technical_note_md": str(note_path),
    }


def attach_prior_score_diagnostic(
    prepared: pd.DataFrame,
    run: ModelRun,
    watch_top_rate: float,
    high_top_rate: float,
    peer_config: adaptive.PeerSelectionConfig | None = None,
    support_thresholds: adaptive.PeerSupportThresholds | None = None,
    scoring_weights: Mapping[str, Any] | None = None,
    score_aggregation: Mapping[str, Any] | None = None,
    derived_features_config: Mapping[str, Any] | None = None,
) -> ModelRun:
    prior_months = sorted(m for m in prepared["invoice_month"].unique().tolist() if m < run.scoring_month)
    if not prior_months or len(run.scores) == 0:
        return run

    previous_month = int(prior_months[-1])
    previous_run = score_scoring_month(
        prepared,
        previous_month,
        watch_top_rate,
        high_top_rate,
        peer_config=peer_config,
        support_thresholds=support_thresholds,
        scoring_weights=scoring_weights,
        score_aggregation=score_aggregation,
        derived_features_config=derived_features_config,
    )
    if len(previous_run.scores) == 0:
        return run

    previous_scores = previous_run.scores[
        ["customer_id", "final_anomaly_score", "anomaly_label", "action_label"]
    ].rename(
        columns={
            "final_anomaly_score": "previous_final_anomaly_score",
            "anomaly_label": "previous_anomaly_label",
            "action_label": "previous_action_label",
        }
    )
    scores = run.scores.merge(previous_scores, on="customer_id", how="left")
    scores["previous_score_month"] = np.where(scores["previous_final_anomaly_score"].notna(), previous_month, np.nan)
    scores["score_delta_vs_previous_month"] = scores["final_anomaly_score"] - scores["previous_final_anomaly_score"]
    scores["score_trend_diagnostic"] = np.select(
        [
            scores["previous_final_anomaly_score"].isna(),
            scores["score_delta_vs_previous_month"].ge(30),
            scores["score_delta_vs_previous_month"].le(-30),
            scores["score_delta_vs_previous_month"].abs().ge(15),
        ],
        [
            "NO_PREVIOUS_MONTH_SCORE",
            "SCORE_SPIKE_UP_DIAGNOSTIC_ONLY",
            "SCORE_DROP_DOWN_DIAGNOSTIC_ONLY",
            "SCORE_MOVED_MODERATELY_DIAGNOSTIC_ONLY",
        ],
        default="SCORE_STABLE_DIAGNOSTIC_ONLY",
    )
    scores["reason_explanation"] = scores.apply(reason_explanation, axis=1)
    run.scores = scores.sort_values("final_anomaly_score", ascending=False)
    return run


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    prepared, profile = read_source(input_path, args.encoding, args.sep)
    scoring_month = normalize_scoring_month(args.scoring_month, prepared["invoice_month"])
    backtest_summary = run_backtests(prepared, scoring_month, args.backtest_months, args.watch_top_rate, args.high_top_rate)
    run = score_scoring_month(prepared, scoring_month, args.watch_top_rate, args.high_top_rate)
    run = attach_prior_score_diagnostic(prepared, run, args.watch_top_rate, args.high_top_rate)
    paths = write_outputs(output_dir, input_path.stem, prepared, run, backtest_summary, profile)
    summary = summarize_run(run, profile)
    print(json.dumps({"summary": summary, "paths": paths}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
