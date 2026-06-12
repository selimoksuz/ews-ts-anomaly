from __future__ import annotations

import argparse
from datetime import datetime
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

import anomaly_model as core


def log_step(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


NORMALIZED_TO_OUTPUT = {
    "customer_id": "ENTITY_ID",
    "exposure_bucket": "EXPOSURE_BUCKET",
    "feature_ratio_bucket": "FEATURE_RATIO_BUCKET",
    "feature_bucket_q3": "FEATURE_BUCKET_Q3",
    "feature_bucket_q4": "FEATURE_BUCKET_Q4",
    "feature_bucket_q5": "FEATURE_BUCKET_Q5",
    "feature_bucket_q8": "FEATURE_BUCKET_Q8",
    "behavior_cluster": "DAVRANIS_CLUSTER",
    "behavior_level_bucket": "DAVRANIS_SEVIYE_BUCKET",
    "behavior_volatility_bucket": "DAVRANIS_VOLATILITE_BUCKET",
    "behavior_trend_bucket": "DAVRANIS_TREND_BUCKET",
    "_global_key": "_GLOBAL_KEY",
}


PEER_COLUMN_ALIASES = {
    "feature_ratio_bucket": "feature_ratio_bucket",
    "exposure_bucket": "exposure_bucket",
}


REPORT_COLUMN_ALIASES = {
    "PEER_TEMSIL_SKORU": ["peer_representability_score"],
    "PEER_TEMSIL_DURUMU": ["peer_representability_status"],
    "PEER_DAGILIM_SKORU": ["peer_distribution_quality_score"],
    "PEER_DAGILIM_DURUMU": ["peer_distribution_status"],
    "PEER_TAIL_RATE": ["peer_tail_rate"],
    "PEER_KALIBRASYON_SKORU": ["peer_calibration_score"],
    "PEER_KALIBRASYON_AY_ADET": ["peer_calibration_n"],
    "PEER_KALIBRASYON_MEDYAN_ABS_RESIDUAL": ["peer_calibration_abs_residual_median"],
    "PEER_KALIBRASYON_INTERVAL_KAPSAMA": ["peer_calibration_interval_coverage"],
    "PEER_KALIBRASYON_FALSE_ALARM_ORANI": ["peer_calibration_false_alarm_rate"],
    "PEER_SECIM_GEREKCESI": ["peer_selection_reason"],
    "PEER_GECMIS_ADET": ["hist_n"],
    "PEER_SEZON_AY_ADET": ["moy_n"],
    "PEER_RECENT_ADET": ["recent_n"],
    "PEER_GUNCEL_ADET": ["current_n", "PEER_AYLIK_MUSTERI_ADET", "peer_month_customer_count"],
    "DAVRANIS_CLUSTER": ["behavior_cluster", "DAVRANIS_CLUSTER_REBUILT"],
}


def ensure_report_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for target, sources in REPORT_COLUMN_ALIASES.items():
        matched = [source for source in sources if source in out.columns]
        if not matched:
            continue
        if target not in out.columns:
            out[target] = out[matched[0]]
            matched = matched[1:]
        for source in matched:
            out[target] = out[target].combine_first(out[source])
    return out


def normalize_merge_key(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if column not in frame.columns:
        raise KeyError(f"Required merge key is missing: {column}")
    out = frame.copy()
    key = out[column].astype("string").str.strip()
    key = key.mask(key.str.lower().isin(["", "nan", "none", "null", "<na>"]), pd.NA)
    out[column] = key
    return out


def report_column_mapping(prepared: pd.DataFrame | None = None) -> dict[str, str]:
    mapping = dict(NORMALIZED_TO_OUTPUT)
    profile = prepared.attrs.get("profile", {}) if prepared is not None and hasattr(prepared, "attrs") else {}
    source_map = dict(profile.get("input_column_map", {}))
    for logical in ["customer_id"]:
        if source_map.get(logical):
            mapping[logical] = str(source_map[logical])
    for column in profile.get("segment_columns", []):
        mapping[str(column)] = str(column)
    return mapping


def infer_period_column(frame: pd.DataFrame, scoring_month: int, preferred: str | None = None) -> str:
    for column in [preferred, "MODEL_DONEM_AY", "invoice_month", "period_month"]:
        if column and column in frame.columns:
            return str(column)
    for column in frame.columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.notna().any() and values.eq(scoring_month).any():
            return str(column)
    raise KeyError("Could not infer scoring period column")


def infer_report_id_column(decisions: pd.DataFrame, scoring_keys: pd.DataFrame) -> str:
    mapping = scoring_keys.attrs.get("normalized_to_output", {})
    preferred = [mapping.get("customer_id"), "ENTITY_ID", "CUSTOMER_ID", "ENTITY_KEY", "ID"]
    for column in preferred:
        if column and column in decisions.columns and column in scoring_keys.columns:
            return str(column)
    common = [column for column in scoring_keys.columns if column in decisions.columns]
    if common:
        return str(common[0])
    raise KeyError("Required merge key is missing: customer id column")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build peer quality analysis report from anomaly outputs.")
    parser.add_argument("--input", default="data/raw/encrypted_final.csv")
    parser.add_argument(
        "--evidence-csv",
        default="outputs/analysis/decision_detail/encrypted_final_anomaly_decision_detail_202603.csv",
        help="Detail/evidence CSV containing scoring-month peer/model columns.",
    )
    parser.add_argument(
        "--decision-csv",
        default=None,
        help="Backward-compatible alias for --evidence-csv.",
    )
    parser.add_argument("--output-dir", default="outputs/analysis/peer_quality_report")
    parser.add_argument("--scoring-month", default="last")
    parser.add_argument("--encoding", default="auto")
    parser.add_argument("--sep", default="auto")
    parser.add_argument("--column-map-json", default=None)
    parser.add_argument("--derived-features-json", default=None)
    return parser.parse_args()


def pct(series: pd.Series) -> float:
    valid = pd.to_numeric(series, errors="coerce").dropna()
    if len(valid) == 0:
        return float("nan")
    return float(valid.mean())


def q(series: pd.Series, quantile: float) -> float:
    valid = pd.to_numeric(series, errors="coerce").dropna()
    if len(valid) == 0:
        return float("nan")
    return float(valid.quantile(quantile))


def safe_div(numerator: pd.Series, denominator: pd.Series | float) -> pd.Series:
    return numerator.astype(float) / np.maximum(pd.Series(denominator, index=numerator.index).astype(float), 1e-9)


def numeric_with_default(frame: pd.DataFrame, column: str, default: float) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(default).astype(float)


def build_scoring_context(prepared: pd.DataFrame, scoring_month: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    history = prepared.loc[prepared["invoice_month"].lt(scoring_month)].copy()
    scoring = prepared.loc[prepared["invoice_month"].eq(scoring_month)].copy()
    output_mapping = report_column_mapping(prepared)
    profile = prepared.attrs.get("profile", {}) if hasattr(prepared, "attrs") else {}
    derived_features = profile.get("derived_features", {})
    history, scoring, effective_segment_cols, segment_report = core.apply_segment_optimizations(
        history,
        scoring,
        profile,
    )
    if effective_segment_cols:
        profile = dict(profile)
        profile["effective_segment_columns"] = effective_segment_cols
        profile["segment_optimization_report"] = segment_report
    feature_edges = core.fit_feature_bucket_edges(history, derived_features)
    legacy_edges = core.fit_reference_feature_edges(history)
    scoring = core.assign_feature_buckets(scoring, feature_edges)
    history = core.assign_feature_buckets(history, feature_edges)
    scoring = core.assign_feature_ratio_bucket(scoring, legacy_edges)
    history = core.assign_feature_ratio_bucket(history, legacy_edges)
    if bool(prepared.attrs.get("behavior_peer_enabled", False)):
        behavior = core.build_behavior_clusters(history)
        scoring = core.assign_behavior_clusters(scoring, behavior)
        history = core.assign_behavior_clusters(history, behavior)
    scoring["_global_key"] = "ALL"
    history["_global_key"] = "ALL"
    segment_cols = [str(col) for col in profile.get("effective_segment_columns", profile.get("segment_columns", [])) if str(col) in scoring.columns]
    wanted_cols = [
        "customer_id",
        *segment_cols,
        "_global_key",
    ]
    out = scoring[[col for col in wanted_cols if col in scoring.columns]].copy()
    out = out.rename(
        columns={
            "customer_id": output_mapping.get("customer_id", "ENTITY_ID"),
            **{column: output_mapping.get(column, column) for column in segment_cols},
            "_global_key": "_GLOBAL_KEY",
        }
    )
    out.attrs["normalized_to_output"] = output_mapping
    return out, history, scoring


def load_scoring_context(
    input_path: Path,
    scoring_month: int,
    encoding: str,
    sep: str,
    column_map: dict[str, str] | None = None,
    derived_features_config: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prepared, _ = core.read_source(
        input_path,
        encoding,
        sep,
        column_map=column_map,
        derived_features_config=derived_features_config,
    )
    return build_scoring_context(prepared, scoring_month)


def scoring_context_from_source_frame(
    source_frame: pd.DataFrame,
    scoring_month: int,
    column_map: dict[str, str] | None = None,
    source_name: str | None = None,
    derived_features_config: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prepared, _ = core.prepare_source_frame(
        source_frame,
        column_map=column_map,
        source_name=source_name,
        derived_features_config=derived_features_config,
    )
    return build_scoring_context(prepared, scoring_month)


def load_scoring_keys(
    input_path: Path,
    scoring_month: int,
    encoding: str,
    sep: str,
    column_map: dict[str, str] | None = None,
    derived_features_config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    scoring_keys, _, _ = load_scoring_context(
        input_path,
        scoring_month,
        encoding,
        sep,
        column_map=column_map,
        derived_features_config=derived_features_config,
    )
    return scoring_keys


def add_peer_instance_keys(decisions: pd.DataFrame, scoring_keys: pd.DataFrame) -> pd.DataFrame:
    report_mapping = scoring_keys.attrs.get("normalized_to_output", dict(NORMALIZED_TO_OUTPUT))
    id_column = infer_report_id_column(decisions, scoring_keys)
    decision_keys = normalize_merge_key(decisions, id_column)
    scoring_keys = normalize_merge_key(scoring_keys, id_column)
    out = decision_keys.merge(scoring_keys, on=id_column, how="left")
    out["ENTITY_ID"] = out[id_column]
    for output_col in set(report_mapping.values()).union(NORMALIZED_TO_OUTPUT.values()):
        if not output_col or output_col in {id_column, "ENTITY_ID"}:
            continue
        left_col = f"{output_col}_x"
        right_col = f"{output_col}_y"
        if output_col not in out.columns and (left_col in out.columns or right_col in out.columns):
            left_values = out[left_col] if left_col in out.columns else pd.Series(pd.NA, index=out.index)
            right_values = out[right_col] if right_col in out.columns else pd.Series(pd.NA, index=out.index)
            out[output_col] = left_values.combine_first(right_values)
        elif output_col in out.columns:
            if left_col in out.columns:
                out[output_col] = out[output_col].combine_first(out[left_col])
            if right_col in out.columns:
                out[output_col] = out[output_col].combine_first(out[right_col])
    if "DAVRANIS_CLUSTER" not in out.columns and "DAVRANIS_CLUSTER_REBUILT" in out.columns:
        out["DAVRANIS_CLUSTER"] = out["DAVRANIS_CLUSTER_REBUILT"]
    if "DAVRANIS_SEVIYE_BUCKET" not in out.columns and "DAVRANIS_SEVIYE_BUCKET_REBUILT" in out.columns:
        out["DAVRANIS_SEVIYE_BUCKET"] = out["DAVRANIS_SEVIYE_BUCKET_REBUILT"]
    if "DAVRANIS_VOLATILITE_BUCKET" not in out.columns and "DAVRANIS_VOLATILITE_BUCKET_REBUILT" in out.columns:
        out["DAVRANIS_VOLATILITE_BUCKET"] = out["DAVRANIS_VOLATILITE_BUCKET_REBUILT"]
    if "DAVRANIS_TREND_BUCKET" not in out.columns and "DAVRANIS_TREND_BUCKET_REBUILT" in out.columns:
        out["DAVRANIS_TREND_BUCKET"] = out["DAVRANIS_TREND_BUCKET_REBUILT"]
    if "EXPOSURE_BUCKET" in out.columns:
        out["EXPOSURE_BUCKET"] = out["EXPOSURE_BUCKET"].fillna("exposure_unknown")
    if "FEATURE_RATIO_BUCKET" in out.columns:
        out["FEATURE_RATIO_BUCKET"] = out["FEATURE_RATIO_BUCKET"].fillna("feature_ratio_unknown")
    out["_GLOBAL_KEY"] = "ALL"

    out["PEER_KEY_DEGERLERI"] = vectorized_peer_key_values(out, "PEER_KOLONLARI", report_mapping)
    return out


def add_support_counts_from_reason(decisions: pd.DataFrame) -> pd.DataFrame:
    out = decisions.copy()
    if "PEER_SECIM_GEREKCESI" not in out.columns:
        return out

    patterns = {
        "PEER_GECMIS_ADET": r"destek hist=([0-9]+)",
        "PEER_SEZON_AY_ADET": r"season=([0-9]+)",
        "PEER_RECENT_ADET": r"recent=([0-9]+)",
        "PEER_GUNCEL_ADET": r"current=([0-9]+)",
    }
    text = out["PEER_SECIM_GEREKCESI"].fillna("").astype(str)
    for col, pattern in patterns.items():
        if col not in out.columns:
            out[col] = text.str.extract(pattern, flags=re.IGNORECASE)[0].astype(float)
    return out


def parse_peer_columns(columns_text: Any) -> list[str]:
    text = str(columns_text)
    if text in {"", "nan", "None", "global"}:
        return []
    return [PEER_COLUMN_ALIASES.get(column, column) for column in text.split("+") if column]


def safe_string_values(values: pd.Series) -> pd.Series:
    return values.astype("string").fillna("<NA>").astype(object)


def vectorized_peer_key_values(
    frame: pd.DataFrame,
    peer_columns_col: str,
    report_mapping: dict[str, str],
) -> pd.Series:
    result = pd.Series("global=ALL", index=frame.index, dtype=object)
    if peer_columns_col not in frame.columns:
        return result

    peer_columns = frame[peer_columns_col].fillna("global").astype(str)
    global_mask = peer_columns.isin(["", "nan", "None", "global"])
    for columns_text in peer_columns.loc[~global_mask].drop_duplicates():
        row_mask = peer_columns.eq(columns_text)
        parts: list[pd.Series] = []
        for column in [part for part in str(columns_text).split("+") if part]:
            output_col = report_mapping.get(column, NORMALIZED_TO_OUTPUT.get(column))
            if output_col is None:
                continue
            values = (
                safe_string_values(frame.loc[row_mask, output_col])
                if output_col in frame.columns
                else pd.Series("<NA>", index=frame.index[row_mask], dtype=object)
            )
            parts.append(column + "=" + values)
        if not parts:
            result.loc[row_mask] = columns_text
            continue
        key_values = parts[0]
        for part in parts[1:]:
            key_values = key_values + " | " + part
        result.loc[row_mask] = key_values
    return result


def build_normalized_peer_key(row: pd.Series, columns: list[str]) -> str:
    if not columns:
        return "global=ALL"
    labels = {
        "feature_ratio_bucket": "feature_ratio_bucket",
        "exposure_bucket": "exposure_bucket",
    }
    return " | ".join(f"{labels.get(column, column)}={row.get(column, np.nan)}" for column in columns)


def vectorized_normalized_peer_key(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    if not columns:
        return pd.Series("global=ALL", index=frame.index, dtype=object)
    labels = {
        "feature_ratio_bucket": "feature_ratio_bucket",
        "exposure_bucket": "exposure_bucket",
    }
    parts: list[pd.Series] = []
    for column in columns:
        values = (
            safe_string_values(frame[column])
            if column in frame.columns
            else pd.Series("<NA>", index=frame.index, dtype=object)
        )
        parts.append(f"{labels.get(column, column)}=" + values)
    out = parts[0]
    for part in parts[1:]:
        out = out + " | " + part
    return out


def aggregate_main_metric_stats(frame: pd.DataFrame, columns: list[str], level_name: str, prefix: str) -> pd.DataFrame:
    key = core.group_key(columns)
    stats_columns = [
        "PEER_SEVIYE",
        "PEER_KEY_DEGERLERI",
        f"{prefix}_ortalama",
        f"{prefix}_medyan",
        f"{prefix}_ortalama_medyan_oran",
        f"{prefix}_std",
        f"{prefix}_std_medyan_oran",
        f"{prefix}_min",
        f"{prefix}_max",
    ]
    required = set(key + ["valid_main_metric_for_model", "main_metric"])
    if len(frame) == 0 or not required.issubset(frame.columns):
        return pd.DataFrame(columns=stats_columns)

    valid = frame.loc[frame["valid_main_metric_for_model"] & frame["main_metric"].notna(), key + ["main_metric"]].copy()
    if len(valid) == 0:
        return pd.DataFrame(columns=stats_columns)
    valid["main_metric"] = pd.to_numeric(valid["main_metric"], errors="coerce")
    valid = valid.loc[valid["main_metric"].notna()]
    if len(valid) == 0:
        return pd.DataFrame(columns=stats_columns)
    valid["_main_metric_sq"] = valid["main_metric"].astype(float) ** 2

    stats = (
        valid.groupby(key, dropna=False)
        .agg(
            **{
                f"{prefix}_ortalama": ("main_metric", "mean"),
                f"{prefix}_medyan": ("main_metric", "median"),
                "_main_metric_sum": ("main_metric", "sum"),
                "_main_metric_sq_sum": ("_main_metric_sq", "sum"),
                "_main_metric_count": ("main_metric", "count"),
                f"{prefix}_min": ("main_metric", "min"),
                f"{prefix}_max": ("main_metric", "max"),
            }
        )
        .reset_index()
    )
    mean_values = stats[f"{prefix}_ortalama"].astype(float)
    variance = (stats["_main_metric_sq_sum"].astype(float) / stats["_main_metric_count"].clip(lower=1).astype(float)) - (
        mean_values**2
    )
    stats[f"{prefix}_std"] = np.sqrt(np.maximum(variance, 0.0))
    stats = stats.drop(columns=["_main_metric_sum", "_main_metric_sq_sum", "_main_metric_count"])
    stats[f"{prefix}_ortalama_medyan_oran"] = (
        stats[f"{prefix}_ortalama"].astype(float) / np.maximum(stats[f"{prefix}_medyan"].astype(float), 1e-6)
    )
    stats[f"{prefix}_std_medyan_oran"] = (
        stats[f"{prefix}_std"].astype(float) / np.maximum(stats[f"{prefix}_medyan"].astype(float), 1e-6)
    )
    stats["PEER_SEVIYE"] = level_name
    stats["PEER_KEY_DEGERLERI"] = vectorized_normalized_peer_key(stats, columns)
    return stats[stats_columns]


def add_peer_main_metric_stats(
    peer_instance_summary: pd.DataFrame,
    history: pd.DataFrame,
    scoring: pd.DataFrame,
) -> pd.DataFrame:
    if peer_instance_summary.empty or "PEER_SEVIYE" not in peer_instance_summary.columns:
        return peer_instance_summary

    stat_frames: list[pd.DataFrame] = []
    level_columns = peer_instance_summary.loc[:, ["PEER_SEVIYE"]].copy()
    if "PEER_KOLONLARI" in peer_instance_summary.columns:
        level_columns["PEER_KOLONLARI"] = peer_instance_summary["PEER_KOLONLARI"]
    else:
        level_columns["PEER_KOLONLARI"] = peer_instance_summary["PEER_SEVIYE"]

    for row in level_columns.drop_duplicates().itertuples(index=False):
        level_name = str(row.PEER_SEVIYE)
        columns_text = getattr(row, "PEER_KOLONLARI", level_name)
        columns = parse_peer_columns(columns_text)
        current_stats = aggregate_main_metric_stats(scoring, columns, level_name, "peer_guncel_ana_metrik")
        history_stats = aggregate_main_metric_stats(history, columns, level_name, "peer_gecmis_ana_metrik")
        merged = current_stats.merge(history_stats, on=["PEER_SEVIYE", "PEER_KEY_DEGERLERI"], how="outer")
        stat_frames.append(merged)

    if not stat_frames:
        return peer_instance_summary

    stats = pd.concat(stat_frames, ignore_index=True).drop_duplicates(["PEER_SEVIYE", "PEER_KEY_DEGERLERI"])
    return peer_instance_summary.merge(stats, on=["PEER_SEVIYE", "PEER_KEY_DEGERLERI"], how="left")


def summarize_frame(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    work = df.copy()
    for col in group_cols:
        if col not in work.columns:
            return pd.DataFrame(columns=group_cols)
    optional_numeric_cols = [
        "PEER_GUNCEL_ADET",
        "PEER_GECMIS_ADET",
        "PEER_SEZON_AY_ADET",
        "PEER_RECENT_ADET",
        "PEER_KALIBRASYON_SKORU",
        "PEER_KALIBRASYON_AY_ADET",
        "PEER_KALIBRASYON_MEDYAN_ABS_RESIDUAL",
        "PEER_KALIBRASYON_INTERVAL_KAPSAMA",
        "PEER_KALIBRASYON_FALSE_ALARM_ORANI",
    ]
    for col in optional_numeric_cols:
        if col not in work.columns:
            work[col] = np.nan
    work["IS_WATCH_OR_ANOM"] = numeric_with_default(work, "ANOMALI_FLAG", 0.0)
    work["IS_HIGH_MAIN_METRIC"] = work["ANOMALI_ETIKETI"].isin(
        ["HIGH_MAIN_METRIC_ANOMALY", "HIGH_BILL_ANOMALY"]
    ).astype(float)
    work["IS_LOW_MAIN_METRIC"] = work["ANOMALI_ETIKETI"].isin(
        ["LOW_MAIN_METRIC_ANOMALY", "LOW_BILL_ANOMALY"]
    ).astype(float)
    work["IS_WATCHLIST"] = work["ANOMALI_ETIKETI"].astype(str).str.startswith("WATCHLIST").astype(float)
    entity_col = "ENTITY_ID" if "ENTITY_ID" in work.columns else next(
        (col for col in work.columns if col not in group_cols and str(col).upper().endswith("ID")),
        work.columns[0],
    )

    agg = (
        work.groupby(group_cols, dropna=False)
        .agg(
            musteri_adet=(entity_col, "nunique"),
            anomaly_watch_adet=("IS_WATCH_OR_ANOM", "sum"),
            anomaly_watch_oran=("IS_WATCH_OR_ANOM", "mean"),
            high_main_metric_adet=("IS_HIGH_MAIN_METRIC", "sum"),
            low_main_metric_adet=("IS_LOW_MAIN_METRIC", "sum"),
            watchlist_adet=("IS_WATCHLIST", "sum"),
            temsil_skor_medyan=("PEER_TEMSIL_SKORU", "median"),
            temsil_skor_p10=("PEER_TEMSIL_SKORU", lambda x: q(x, 0.10)),
            temsil_skor_p25=("PEER_TEMSIL_SKORU", lambda x: q(x, 0.25)),
            temsil_skor_p75=("PEER_TEMSIL_SKORU", lambda x: q(x, 0.75)),
            dagilim_skor_medyan=("PEER_DAGILIM_SKORU", "median"),
            dagilim_skor_p10=("PEER_DAGILIM_SKORU", lambda x: q(x, 0.10)),
            peer_tail_rate_medyan=("PEER_TAIL_RATE", "median"),
            kalibrasyon_skor_medyan=("PEER_KALIBRASYON_SKORU", "median"),
            kalibrasyon_ay_adet_medyan=("PEER_KALIBRASYON_AY_ADET", "median"),
            kalibrasyon_abs_residual_medyan=("PEER_KALIBRASYON_MEDYAN_ABS_RESIDUAL", "median"),
            kalibrasyon_interval_kapsama_medyan=("PEER_KALIBRASYON_INTERVAL_KAPSAMA", "median"),
            kalibrasyon_false_alarm_oran_medyan=("PEER_KALIBRASYON_FALSE_ALARM_ORANI", "median"),
            peer_guncel_adet_medyan=("PEER_GUNCEL_ADET", "median"),
            peer_guncel_adet_min=("PEER_GUNCEL_ADET", "min"),
            peer_gecmis_adet_medyan=("PEER_GECMIS_ADET", "median"),
            peer_sezon_adet_medyan=("PEER_SEZON_AY_ADET", "median"),
            peer_recent_adet_medyan=("PEER_RECENT_ADET", "median"),
            guven_medyan=("GUVEN_SKORU", "median"),
            skor_medyan=("ANOMALI_SKORU", "median"),
        )
        .reset_index()
    )
    count = float(len(work))
    agg["musteri_pay"] = agg["musteri_adet"] / max(count, 1.0)
    return agg.sort_values(["musteri_adet", "anomaly_watch_oran"], ascending=[False, False])


def weak_peer_review(peer_instance_summary: pd.DataFrame) -> pd.DataFrame:
    work = peer_instance_summary.copy()
    temsil_skor = numeric_with_default(work, "temsil_skor_medyan", 50.0)
    dagilim_skor = numeric_with_default(work, "dagilim_skor_medyan", 50.0)
    kalibrasyon_skor = numeric_with_default(work, "kalibrasyon_skor_medyan", 50.0)
    anomaly_watch_oran = numeric_with_default(work, "anomaly_watch_oran", 0.0)
    peer_guncel_adet_min = numeric_with_default(work, "peer_guncel_adet_min", 0.0)
    mean_median_ratio = numeric_with_default(work, "peer_guncel_ana_metrik_ortalama_medyan_oran", 1.0)
    std_median_ratio = numeric_with_default(work, "peer_guncel_ana_metrik_std_medyan_oran", 0.0)
    work["temsil_skor_medyan"] = temsil_skor
    work["dagilim_skor_medyan"] = dagilim_skor
    work["kalibrasyon_skor_medyan"] = kalibrasyon_skor
    work["anomaly_watch_oran"] = anomaly_watch_oran
    work["peer_guncel_adet_min"] = peer_guncel_adet_min
    work["peer_guncel_ana_metrik_ortalama_medyan_oran"] = mean_median_ratio
    work["peer_guncel_ana_metrik_std_medyan_oran"] = std_median_ratio
    work["review_skoru"] = (
        (100 - temsil_skor) * 0.30
        + (100 - dagilim_skor) * 0.25
        + (100 - kalibrasyon_skor) * 0.25
        + anomaly_watch_oran * 100 * 0.20
        + np.where(peer_guncel_adet_min < 25, 10, 0)
        + np.minimum(np.maximum(mean_median_ratio - 1.0, 0.0) / 3.0, 1.0) * 15
        + np.minimum(np.maximum(std_median_ratio, 0.0) / 3.0, 1.0) * 15
    )
    reasons = pd.Series("", index=work.index, dtype=object)

    def append_reason(mask: pd.Series | np.ndarray, text: str) -> None:
        nonlocal reasons
        mask_series = pd.Series(mask, index=work.index).fillna(False).astype(bool)
        if not bool(mask_series.any()):
            return
        current = reasons.loc[mask_series]
        reasons.loc[mask_series] = np.where(current.eq(""), text, current + "; " + text)

    append_reason(work["temsil_skor_medyan"].lt(60), "temsil dusuk")
    append_reason(work["dagilim_skor_medyan"].lt(60), "heavy-tail/dagilim zayif")
    append_reason(work["peer_guncel_ana_metrik_ortalama_medyan_oran"].ge(4.0), "ortalama/medyan orani yuksek")
    append_reason(work["peer_guncel_ana_metrik_std_medyan_oran"].ge(3.0), "std/medyan orani yuksek")
    append_reason(work["kalibrasyon_skor_medyan"].lt(60), "gecmis kalibrasyon zayif")
    append_reason(work["peer_guncel_adet_min"].lt(25), "current destek sinirda")
    append_reason(work["anomaly_watch_oran"].ge(0.08), "anomaly/watchlist orani yuksek")
    work["review_nedeni"] = reasons.mask(reasons.eq(""), "izleme")
    return work.sort_values("review_skoru", ascending=False)


def write_csvs(out_dir: Path, tables: dict[str, pd.DataFrame]) -> None:
    for name, frame in tables.items():
        frame.to_csv(out_dir / f"{name}.csv", index=False, encoding="utf-8-sig")


def write_excel(out_path: Path, tables: dict[str, pd.DataFrame]) -> None:
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for name, frame in tables.items():
            sheet_name = name[:31]
            frame.to_excel(writer, sheet_name=sheet_name, index=False)


def write_charts(out_dir: Path, peer_level_summary: pd.DataFrame, distribution_summary: pd.DataFrame) -> dict[str, str]:
    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return {}

    charts: dict[str, str] = {}

    peer_chart = chart_dir / "peer_level_customer_count.png"
    level = peer_level_summary.sort_values("musteri_adet", ascending=True)
    plt.figure(figsize=(10, 5))
    plt.barh(level["PEER_SEVIYE"].astype(str), level["musteri_adet"].astype(float), color="#2f6f9f")
    plt.title("Peer seviyesi bazinda musteri adedi")
    plt.xlabel("Musteri adedi")
    plt.tight_layout()
    plt.savefig(peer_chart, dpi=140)
    plt.close()
    charts["peer_level"] = "charts/peer_level_customer_count.png"

    dist_chart = chart_dir / "peer_distribution_status.png"
    dist = distribution_summary.sort_values("musteri_adet", ascending=False)
    plt.figure(figsize=(8, 4))
    plt.bar(dist["PEER_DAGILIM_DURUMU"].astype(str), dist["musteri_adet"].astype(float), color="#7c3aed")
    plt.title("Peer dagilim durumu bazinda musteri adedi")
    plt.ylabel("Musteri adedi")
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(dist_chart, dpi=140)
    plt.close()
    charts["distribution"] = "charts/peer_distribution_status.png"
    return charts


def fmt_pct(value: float) -> str:
    if pd.isna(value):
        return "n/a"
    return f"{value * 100:.1f}%"


def fmt_num(value: Any, decimals: int = 1) -> str:
    if pd.isna(value):
        return "n/a"
    return f"{float(value):.{decimals}f}"


def markdown_table(frame: pd.DataFrame, cols: list[str], n: int = 10) -> str:
    sub = frame.loc[:, [c for c in cols if c in frame.columns]].head(n).copy()
    if sub.empty:
        return "_Kayit yok._"
    for col in sub.columns:
        if pd.api.types.is_float_dtype(sub[col]):
            sub[col] = sub[col].map(lambda v: "" if pd.isna(v) else f"{v:.4f}")
        else:
            sub[col] = sub[col].fillna("").astype(str)
    headers = list(sub.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in sub.iterrows():
        values = [str(row[col]).replace("|", "/") for col in headers]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_markdown_report(
    out_path: Path,
    decisions: pd.DataFrame,
    peer_level_summary: pd.DataFrame,
    peer_instance_summary: pd.DataFrame,
    status_summary: pd.DataFrame,
    distribution_summary: pd.DataFrame,
    behavior_summary: pd.DataFrame,
    weak_review: pd.DataFrame,
) -> None:
    total = len(decisions)
    behavior_rows = int(decisions["PEER_SEVIYE"].astype(str).str.contains("behavior", na=False).sum())
    homogeneous = int((decisions["PEER_DAGILIM_DURUMU"] == "HOMOGENEOUS_PEER").sum())
    heavy = int((decisions["PEER_DAGILIM_DURUMU"] == "HEAVY_TAIL_PEER_REVIEW").sum())
    strong_good = int(decisions["PEER_TEMSIL_DURUMU"].isin(["STRONG_PEER_REPRESENTATION", "GOOD_PEER_REPRESENTATION"]).sum())

    peer_metric_cols = [
        "PEER_SEVIYE",
        "PEER_KEY_DEGERLERI",
        "peer_guncel_ana_metrik_medyan",
        "peer_guncel_ana_metrik_ortalama",
        "peer_guncel_ana_metrik_ortalama_medyan_oran",
        "peer_guncel_ana_metrik_std",
        "peer_guncel_ana_metrik_std_medyan_oran",
        "peer_guncel_ana_metrik_min",
        "peer_guncel_ana_metrik_max",
        "peer_gecmis_ana_metrik_medyan",
        "peer_gecmis_ana_metrik_ortalama",
        "peer_gecmis_ana_metrik_ortalama_medyan_oran",
        "peer_gecmis_ana_metrik_std",
        "peer_gecmis_ana_metrik_std_medyan_oran",
        "peer_gecmis_ana_metrik_min",
        "peer_gecmis_ana_metrik_max",
    ]

    body = f"""# Ana Metrik Peer Kalite Analiz Raporu

## Executive Summary

- Toplam skorlanan musteri: **{total:,}**.
- Strong/Good peer temsil kapsami: **{strong_good:,}** musteri ({fmt_pct(strong_good / total)}).
- Homogeneous peer kapsami: **{homogeneous:,}** musteri ({fmt_pct(homogeneous / total)}).
- Heavy-tail review peer kapsami: **{heavy:,}** musteri ({fmt_pct(heavy / total)}).
- Behavior-based peer secilen musteri: **{behavior_rows:,}** ({fmt_pct(behavior_rows / total)}).

## Peer Seviyesi Kapsam ve Temsil

{markdown_table(peer_level_summary, ["PEER_SEVIYE", "musteri_adet", "musteri_pay", "temsil_skor_medyan", "dagilim_skor_medyan", "kalibrasyon_skor_medyan", "peer_guncel_adet_medyan", "anomaly_watch_oran"], 20)}

## Peer Instance Ana Metrik Dagilimi

{markdown_table(peer_instance_summary, peer_metric_cols, 30)}

## Peer Temsil Durumu

{markdown_table(status_summary, ["PEER_TEMSIL_DURUMU", "musteri_adet", "musteri_pay", "anomaly_watch_oran", "dagilim_skor_medyan", "guven_medyan"], 20)}

## Peer Dagilim Kalitesi

{markdown_table(distribution_summary, ["PEER_DAGILIM_DURUMU", "musteri_adet", "musteri_pay", "anomaly_watch_oran", "temsil_skor_medyan", "peer_tail_rate_medyan"], 20)}

## Behavior Cluster Kapsami

{markdown_table(behavior_summary, ["DAVRANIS_CLUSTER", "musteri_adet", "musteri_pay", "anomaly_watch_oran", "temsil_skor_medyan", "dagilim_skor_medyan"], 25)}

## Review Gerektiren Peer Gruplari

{markdown_table(weak_review, ["PEER_SEVIYE", "PEER_KEY_DEGERLERI", "review_skoru", "review_nedeni", "peer_guncel_ana_metrik_medyan", "peer_guncel_ana_metrik_ortalama", "peer_guncel_ana_metrik_ortalama_medyan_oran", "peer_guncel_ana_metrik_std", "peer_guncel_ana_metrik_std_medyan_oran", "peer_guncel_ana_metrik_min", "peer_guncel_ana_metrik_max", "temsil_skor_medyan", "dagilim_skor_medyan", "kalibrasyon_skor_medyan", "anomaly_watch_oran"], 30)}

## Metod Notu

- Peer merkez olcusu medyandir.
- Sapma olcusu MAD tabanli robust scale'dir.
- Peer dagilim kalitesi log skew, log kurtosis, robust tail rate, raw ortalama/medyan ve raw std/medyan oranlariyla izlenir.
- Peer kalibrasyonu scoring ayi kullanmadan gecmis holdout aylarda peer expected isabetini olcer.
- Bu rapor karar modelini yeniden skorlamaz; mevcut final decision tablosunun peer kalitesini denetler.
"""
    out_path.write_text(body, encoding="utf-8")


def html_table(frame: pd.DataFrame, cols: list[str], n: int = 20) -> str:
    sub = frame.loc[:, [c for c in cols if c in frame.columns]].head(n).copy()
    for col in sub.columns:
        if pd.api.types.is_float_dtype(sub[col]):
            sub[col] = sub[col].map(lambda v: "" if pd.isna(v) else f"{v:.4f}")
    return sub.to_html(index=False, escape=True, classes="data-table")


def write_html_report(
    out_path: Path,
    decisions: pd.DataFrame,
    peer_level_summary: pd.DataFrame,
    peer_instance_summary: pd.DataFrame,
    status_summary: pd.DataFrame,
    distribution_summary: pd.DataFrame,
    behavior_summary: pd.DataFrame,
    weak_review: pd.DataFrame,
    charts: dict[str, str],
) -> None:
    total = len(decisions)
    behavior_rows = int(decisions["PEER_SEVIYE"].astype(str).str.contains("behavior", na=False).sum())
    homogeneous = int((decisions["PEER_DAGILIM_DURUMU"] == "HOMOGENEOUS_PEER").sum())
    heavy = int((decisions["PEER_DAGILIM_DURUMU"] == "HEAVY_TAIL_PEER_REVIEW").sum())
    strong_good = int(decisions["PEER_TEMSIL_DURUMU"].isin(["STRONG_PEER_REPRESENTATION", "GOOD_PEER_REPRESENTATION"]).sum())
    html_body = f"""<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<title>Ana Metrik Peer Kalite Analiz Raporu</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 32px; color: #1f2933; }}
h1, h2 {{ color: #102a43; }}
.summary {{ display: grid; grid-template-columns: repeat(4, minmax(140px, 1fr)); gap: 12px; margin: 18px 0 28px; }}
.card {{ border: 1px solid #d9e2ec; border-radius: 6px; padding: 12px; background: #f8fbff; }}
.metric {{ font-size: 24px; font-weight: 700; }}
.label {{ font-size: 12px; color: #52606d; margin-top: 4px; }}
.data-table {{ border-collapse: collapse; width: 100%; margin: 10px 0 28px; font-size: 12px; }}
.data-table th, .data-table td {{ border: 1px solid #d9e2ec; padding: 6px 8px; vertical-align: top; }}
.data-table th {{ background: #edf2f7; text-align: left; }}
.note {{ background: #fff8e6; border: 1px solid #f0d98c; padding: 12px; border-radius: 6px; }}
.chart {{ max-width: 100%; border: 1px solid #d9e2ec; border-radius: 6px; margin: 8px 0 24px; }}
</style>
</head>
<body>
<h1>Ana Metrik Peer Kalite Analiz Raporu</h1>
<h2>Executive Summary</h2>
<div class="summary">
<div class="card"><div class="metric">{total:,}</div><div class="label">Skorlanan musteri</div></div>
<div class="card"><div class="metric">{fmt_pct(strong_good / total)}</div><div class="label">Strong/Good peer temsil</div></div>
<div class="card"><div class="metric">{fmt_pct(homogeneous / total)}</div><div class="label">Homogeneous peer kapsami</div></div>
<div class="card"><div class="metric">{fmt_pct(behavior_rows / total)}</div><div class="label">Behavior peer secimi</div></div>
</div>
<p>Heavy-tail review peer kapsami {heavy:,} musteri ({fmt_pct(heavy / total)}). Bu segmentler karar disi birakilmadi; model bu peerlerde peer agirligini ve guveni sinirliyor.</p>
<h2>Peer Seviyesi Kapsam ve Temsil</h2>
{f'<img class="chart" src="{html.escape(charts["peer_level"])}" alt="Peer seviyesi musteri adedi">' if "peer_level" in charts else ""}
{html_table(peer_level_summary, ["PEER_SEVIYE", "musteri_adet", "musteri_pay", "temsil_skor_medyan", "dagilim_skor_medyan", "kalibrasyon_skor_medyan", "peer_guncel_adet_medyan", "anomaly_watch_oran"], 25)}
<h2>Peer Instance Ana Metrik Dagilimi</h2>
{html_table(peer_instance_summary, ["PEER_SEVIYE", "PEER_KEY_DEGERLERI", "peer_guncel_ana_metrik_medyan", "peer_guncel_ana_metrik_ortalama", "peer_guncel_ana_metrik_ortalama_medyan_oran", "peer_guncel_ana_metrik_std", "peer_guncel_ana_metrik_std_medyan_oran", "peer_guncel_ana_metrik_min", "peer_guncel_ana_metrik_max", "peer_gecmis_ana_metrik_medyan", "peer_gecmis_ana_metrik_ortalama", "peer_gecmis_ana_metrik_ortalama_medyan_oran", "peer_gecmis_ana_metrik_std", "peer_gecmis_ana_metrik_std_medyan_oran", "peer_gecmis_ana_metrik_min", "peer_gecmis_ana_metrik_max"], 40)}
<h2>Peer Temsil Durumu</h2>
{html_table(status_summary, ["PEER_TEMSIL_DURUMU", "musteri_adet", "musteri_pay", "anomaly_watch_oran", "dagilim_skor_medyan", "guven_medyan"], 20)}
<h2>Peer Dagilim Kalitesi</h2>
{f'<img class="chart" src="{html.escape(charts["distribution"])}" alt="Peer dagilim durumu musteri adedi">' if "distribution" in charts else ""}
{html_table(distribution_summary, ["PEER_DAGILIM_DURUMU", "musteri_adet", "musteri_pay", "anomaly_watch_oran", "temsil_skor_medyan", "peer_tail_rate_medyan"], 20)}
<h2>Behavior Cluster Kapsami</h2>
{html_table(behavior_summary, ["DAVRANIS_CLUSTER", "musteri_adet", "musteri_pay", "anomaly_watch_oran", "temsil_skor_medyan", "dagilim_skor_medyan"], 30)}
<h2>Review Gerektiren Peer Gruplari</h2>
{html_table(weak_review, ["PEER_SEVIYE", "PEER_KEY_DEGERLERI", "review_skoru", "review_nedeni", "peer_guncel_ana_metrik_medyan", "peer_guncel_ana_metrik_ortalama", "peer_guncel_ana_metrik_ortalama_medyan_oran", "peer_guncel_ana_metrik_std", "peer_guncel_ana_metrik_std_medyan_oran", "peer_guncel_ana_metrik_min", "peer_guncel_ana_metrik_max", "temsil_skor_medyan", "dagilim_skor_medyan", "kalibrasyon_skor_medyan", "anomaly_watch_oran"], 40)}
<div class="note"><strong>Metod notu:</strong> Peer merkez olcusu medyan, ana anomaly residual'i MAD tabanli robust scale ile hesaplanir. Peer dagilim kalitesi ise log skew/kurtosis/tail-rate yaninda raw ortalama/medyan ve std/medyan oranlarini da cezalandirir.</div>
</body>
</html>
"""
    out_path.write_text(html_body, encoding="utf-8")


def build_peer_quality_tables(
    decisions: pd.DataFrame,
    scoring_keys: pd.DataFrame,
    history: pd.DataFrame,
    scoring: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    decisions = ensure_report_columns(decisions)
    enriched = add_peer_instance_keys(decisions, scoring_keys)
    enriched = ensure_report_columns(enriched)
    enriched = add_support_counts_from_reason(enriched)
    enriched = ensure_report_columns(enriched)
    peer_level_summary = summarize_frame(enriched, ["PEER_SEVIYE"])
    peer_instance_summary = summarize_frame(enriched, ["PEER_SEVIYE", "PEER_KOLONLARI", "PEER_KEY_DEGERLERI"])
    peer_instance_summary = add_peer_main_metric_stats(peer_instance_summary, history, scoring)
    status_summary = summarize_frame(enriched, ["PEER_TEMSIL_DURUMU"])
    distribution_summary = summarize_frame(enriched, ["PEER_DAGILIM_DURUMU"])
    behavior_summary = summarize_frame(enriched, ["DAVRANIS_CLUSTER"])
    weak_review = weak_peer_review(peer_instance_summary)
    return peer_level_summary, peer_instance_summary, status_summary, distribution_summary, behavior_summary, weak_review


def write_peer_quality_outputs(
    out_dir: Path,
    scoring_month: int,
    decisions: pd.DataFrame,
    peer_level_summary: pd.DataFrame,
    peer_instance_summary: pd.DataFrame,
    status_summary: pd.DataFrame,
    distribution_summary: pd.DataFrame,
    behavior_summary: pd.DataFrame,
    weak_review: pd.DataFrame,
) -> dict[str, Any]:
    decisions = ensure_report_columns(decisions)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "peer_level_summary": peer_level_summary,
        "peer_instance_summary": peer_instance_summary,
        "peer_representability_summary": status_summary,
        "peer_distribution_summary": distribution_summary,
        "behavior_cluster_summary": behavior_summary,
        "weak_peer_review": weak_review,
    }
    write_csvs(out_dir, tables)
    write_excel(out_dir / f"peer_quality_report_{scoring_month}.xlsx", tables)
    charts = write_charts(out_dir, peer_level_summary, distribution_summary)
    write_markdown_report(
        out_dir / f"peer_quality_report_{scoring_month}.md",
        decisions,
        peer_level_summary,
        peer_instance_summary,
        status_summary,
        distribution_summary,
        behavior_summary,
        weak_review,
    )
    write_html_report(
        out_dir / f"peer_quality_report_{scoring_month}.html",
        decisions,
        peer_level_summary,
        peer_instance_summary,
        status_summary,
        distribution_summary,
        behavior_summary,
        weak_review,
        charts,
    )
    return {
        "scoring_month": int(scoring_month),
        "output_dir": str(out_dir),
        "decision_rows": int(len(decisions)),
        "peer_levels": int(decisions["PEER_SEVIYE"].nunique(dropna=True)),
        "peer_instances": int(len(peer_instance_summary)),
        "behavior_peer_rows": int(decisions["PEER_SEVIYE"].astype(str).str.contains("behavior", na=False).sum()),
        "weak_peer_review_rows": int(len(weak_review)),
    }


def generate_peer_quality_report_from_frames(
    source_frame: pd.DataFrame,
    evidence_frame: pd.DataFrame,
    output_dir: Path,
    scoring_month: int,
    column_map: dict[str, str] | None = None,
    source_name: str | None = None,
    derived_features_config: dict[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    def emit(message: str) -> None:
        if progress is not None:
            progress(f"peer_quality_detail {message}")

    emit("decision_filter_start")
    period_col = infer_period_column(
        evidence_frame,
        scoring_month,
        (column_map or {}).get("invoice_month") if isinstance(column_map, dict) else None,
    )
    decisions = evidence_frame.loc[pd.to_numeric(evidence_frame[period_col], errors="coerce").astype("Int64").eq(scoring_month)].copy()
    emit(f"decision_filter_done rows={len(decisions):,}")
    emit("scoring_context_start")
    scoring_keys, history, scoring = scoring_context_from_source_frame(
        source_frame,
        scoring_month,
        column_map=column_map,
        source_name=source_name,
        derived_features_config=derived_features_config,
    )
    emit(
        "scoring_context_done "
        f"history_rows={len(history):,} scoring_rows={len(scoring):,} scoring_keys={len(scoring_keys):,}"
    )
    emit("peer_quality_tables_start")
    tables = build_peer_quality_tables(decisions, scoring_keys, history, scoring)
    emit(
        "peer_quality_tables_done "
        f"peer_levels={len(tables[0]):,} peer_instances={len(tables[1]):,} weak_reviews={len(tables[5]):,}"
    )
    emit("peer_quality_outputs_start")
    result = write_peer_quality_outputs(output_dir, scoring_month, decisions, *tables)
    emit("peer_quality_outputs_done")
    return result


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    decision_path = Path(args.decision_csv or args.evidence_csv)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log_step("peer_quality_detail file_read_start")
    decisions = pd.read_csv(decision_path)
    column_map = json.loads(args.column_map_json) if args.column_map_json else None
    derived_features_config = json.loads(args.derived_features_json) if args.derived_features_json else None
    period_col = infer_period_column(
        decisions,
        core.normalize_scoring_month(args.scoring_month, None) if str(args.scoring_month).lower() not in {"last", "latest", "max"} else 0,
        (column_map or {}).get("invoice_month") if isinstance(column_map, dict) else None,
    )
    scoring_month = core.normalize_scoring_month(args.scoring_month, decisions[period_col])
    decisions = decisions.loc[pd.to_numeric(decisions[period_col], errors="coerce").astype("Int64").eq(scoring_month)].copy()
    log_step(f"peer_quality_detail file_read_done decision_rows={len(decisions):,}")
    log_step("peer_quality_detail scoring_context_start")
    scoring_keys, history, scoring = load_scoring_context(
        input_path,
        scoring_month,
        args.encoding,
        args.sep,
        column_map=column_map,
        derived_features_config=derived_features_config,
    )
    log_step(
        "peer_quality_detail scoring_context_done "
        f"history_rows={len(history):,} scoring_rows={len(scoring):,} scoring_keys={len(scoring_keys):,}"
    )
    log_step("peer_quality_detail peer_quality_tables_start")
    tables = build_peer_quality_tables(decisions, scoring_keys, history, scoring)
    log_step(
        "peer_quality_detail peer_quality_tables_done "
        f"peer_levels={len(tables[0]):,} peer_instances={len(tables[1]):,} weak_reviews={len(tables[5]):,}"
    )
    log_step("peer_quality_detail peer_quality_outputs_start")
    print(write_peer_quality_outputs(out_dir, scoring_month, decisions, *tables))
    log_step("peer_quality_detail peer_quality_outputs_done")


if __name__ == "__main__":
    main()
