from __future__ import annotations

import argparse
import configparser
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable
import warnings

import numpy as np
import pandas as pd

import adaptive_peer_selection as adaptive
import anomaly_model as core

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)


SIGNAL_COLUMNS = [
    ("historical_peer", "historical_peer_z", "historical_peer_score", "gecmis peer beklentisi"),
    ("current_peer", "current_peer_z", "current_peer_score", "ayni ay peer medyani"),
    ("peer_trend", "peer_trend_z", "peer_trend_score", "peer trendi"),
    ("feature_ratio", "feature_ratio_z", "feature_ratio_score", "ana metrik/referans feature orani"),
    ("self_history", "self_history_z", "self_history_score", "musterinin kendi gecmisi"),
    ("customer_trend", "customer_trend_z", "customer_trend_score", "musteri trendi"),
    ("customer_seasonal", "customer_seasonal_z", "customer_seasonal_score", "musteri sezonalligi"),
    ("customer_recent_regime", "customer_recent_regime_z", "customer_recent_regime_score", "musterinin son 3 ay rejimi"),
]

PEER_KEY_COLUMNS = [
    "feature_ratio_bucket",
    "exposure_bucket",
    "behavior_cluster",
    "_global_key",
]

DEFAULT_ORACLE_INFO_DIR = Path(r"C:\Users\Acer\dc_all_pipe\oracle_info")
ORACLE_IDENTIFIER_MAX_LEN = 30
INTERNAL_SOURCE_ROLES = (
    "customer_id",
    "invoice_month",
    "exposure_feature",
    "main_metric",
    "reference_feature",
)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Production monthly scoring entrypoint for single-variable anomalies.")
    parser.add_argument(
        "--input",
        default="data/raw/encrypted_final.csv",
        help="CSV containing historical months plus the scoring month.",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/production",
        help="Fallback output directory when separate output dirs are not provided.",
    )
    parser.add_argument("--decision-output-dir", default=None, help="Directory for the production decision table.")
    parser.add_argument("--detail-output-dir", default=None, help="Directory for the customer/peer detail table.")
    parser.add_argument("--contract-output-dir", default=None, help="Directory for the implementation contract JSON.")
    parser.add_argument("--encoding", default="auto")
    parser.add_argument("--sep", default="auto")
    parser.add_argument("--scoring-month", default="last", help="last, YYYYMM, YYYYMMDD, or date-like value.")
    parser.add_argument("--rolling-window-months", type=int, default=36)
    parser.add_argument("--watch-top-rate", type=float, default=0.030)
    parser.add_argument("--high-top-rate", type=float, default=0.0075)
    parser.add_argument("--include-prior-score-diagnostic", action="store_true")
    parser.add_argument("--write-oracle", action="store_true", help="Write decision/detail outputs to Oracle.")
    parser.add_argument("--oracle-info-dir", default=str(DEFAULT_ORACLE_INFO_DIR))
    parser.add_argument("--oracle-config-file", default="ora_config.ini")
    parser.add_argument("--oracle-job-file", default="job.ini")
    parser.add_argument("--oracle-section", default=None)
    parser.add_argument("--oracle-owner", default=None)
    parser.add_argument("--oracle-decision-table", default="ANOMALY_DECISIONS")
    parser.add_argument("--oracle-detail-table", default="ANOMALY_DECISION_DETAIL")
    parser.add_argument(
        "--oracle-write-mode",
        choices=["append", "delete_insert", "truncate_insert", "replace"],
        default=None,
        help="append uses INI if_exists/default append; delete_insert deletes same scoring_month before insert.",
    )
    parser.add_argument("--oracle-chunksize", type=int, default=None)
    parser.add_argument("--oracle-no-create-table", action="store_true")
    return parser.parse_args()


def fmt_num(value: Any, digits: int = 2) -> str:
    if pd.isna(value):
        return "n/a"
    return f"{float(value):.{digits}f}"


def profile_input_column_map(profile: dict[str, Any]) -> dict[str, str]:
    source_map = profile.get("input_column_map")
    if isinstance(source_map, dict):
        return {str(k): str(v) for k, v in source_map.items() if not str(k).startswith("__")}
    return {}


def restore_input_columns(frame: pd.DataFrame, profile: dict[str, Any]) -> pd.DataFrame:
    col_map = profile_input_column_map(profile)
    out = pd.DataFrame(index=frame.index)
    for logical in INTERNAL_SOURCE_ROLES:
        normalized = logical
        source_name = col_map.get(logical)
        if not source_name or normalized not in frame.columns:
            continue
        out[source_name] = frame[normalized]
    return out


def input_output_columns(profile: dict[str, Any], available_columns: list[str]) -> list[str]:
    source_columns = [str(col) for col in profile.get("source_columns", [])]
    if source_columns:
        return [col for col in source_columns if col in available_columns]
    mapped_columns = list(dict.fromkeys(profile_input_column_map(profile).values()))
    if mapped_columns:
        return [col for col in mapped_columns if col in available_columns]
    return [col for col in ["customer_id", "invoice_month", "main_metric"] if col in available_columns]


def detail_output_columns(profile: dict[str, Any], available_columns: list[str]) -> list[str]:
    ordered: list[str] = []
    available = list(available_columns)

    def add_existing(columns: list[str]) -> None:
        for col in columns:
            if col in available and col not in ordered:
                ordered.append(col)

    add_existing(input_output_columns(profile, available))
    add_existing([col for col in available if col not in ordered])
    return ordered


def source_columns_for_scoring(prepared: pd.DataFrame, profile: dict[str, Any], scoring_month: int) -> pd.DataFrame:
    source_columns = [col for col in profile.get("source_columns", []) if col in prepared.columns]
    if not source_columns:
        return pd.DataFrame()
    columns = list(dict.fromkeys(["customer_id", "invoice_month", *source_columns]))
    source_rows = prepared.loc[prepared["invoice_month"].eq(scoring_month), columns].copy()
    return source_rows.drop_duplicates(["customer_id", "invoice_month"], keep="last")


def apply_source_column_policy(
    profile: dict[str, Any],
    output_source_columns: list[str] | None = None,
    exclude_source_columns: list[str] | None = None,
) -> dict[str, Any]:
    out = dict(profile)
    source_columns = [str(col) for col in out.get("source_columns", [])]
    if output_source_columns is not None:
        allowed = set(str(col) for col in output_source_columns)
        source_columns = [col for col in source_columns if col in allowed]
    excluded = set(str(col) for col in (exclude_source_columns or []))
    if excluded:
        source_columns = [col for col in source_columns if col not in excluded]
    out["source_columns"] = source_columns
    return out


def peer_role_exclusions(profile: dict[str, Any]) -> list[str]:
    normalized_names = set(INTERNAL_SOURCE_ROLES)
    source_map = profile.get("input_column_map", {})
    if not isinstance(source_map, dict):
        return []
    excluded = [
        str(source_col)
        for source_col in source_map.values()
        if source_col and str(source_col) not in normalized_names
    ]
    excluded.extend(str(col) for col in profile.get("excluded_variables", []))
    return list(dict.fromkeys(excluded))


def add_model_period(frame: pd.DataFrame, scoring_month: int) -> pd.DataFrame:
    out = frame.copy()
    out["MODEL_DONEM_AY"] = int(scoring_month)
    return out


def anomaly_flag_from_label(label: Any) -> int:
    return 0 if str(label) in {"NORMAL", "NOT_SCORED", "nan", "None"} else 1


def operational_decision(action_label: Any, anomaly_label: Any, is_scored: bool = True) -> str:
    if not is_scored:
        return "NOT_SCORED"
    action = str(action_label)
    label = str(anomaly_label)
    if action == "NORMAL" or label == "NORMAL":
        return "NORMAL"
    if action.startswith("LIKELY_"):
        return "LIKELY_ANOMALY"
    if action.startswith("WATCHLIST"):
        return "WATCHLIST"
    if "PEER_ASSIGNMENT_MISMATCH" in action:
        return "REVIEW_PEER_ASSIGNMENT"
    if "PEER_SELF_CONFLICT" in action:
        return "REVIEW_SIGNAL_CONFLICT"
    if "LOW_CONFIDENCE" in action or "SPARSE_HISTORY" in action:
        return "REVIEW_LOW_CONFIDENCE"
    return "REVIEW_MAIN_METRIC_ANOMALY"


def strongest_signal(row: pd.Series) -> tuple[str, str, float, float]:
    def output_signal_name(value: str) -> str:
        return value

    primary_signal = row.get("primary_signal_name", "")
    if isinstance(primary_signal, str) and primary_signal:
        signal_lookup = {signal_name: (z_col, score_col, signal_label) for signal_name, z_col, score_col, signal_label in SIGNAL_COLUMNS}
        if primary_signal in signal_lookup:
            z_col, score_col, signal_label = signal_lookup[primary_signal]
            score = row.get("primary_signal_score", row.get(score_col, np.nan))
            return (output_signal_name(primary_signal), signal_label, row.get(z_col, np.nan), score)
    best = ("none", "major sinyal yok", np.nan, 0.0)
    direction = str(row.get("anomaly_direction", "NONE"))
    candidates: list[tuple[str, str, float, float]] = []
    for signal_name, z_col, score_col, signal_label in SIGNAL_COLUMNS:
        score = row.get(score_col, np.nan)
        if pd.isna(score):
            continue
        z_value = row.get(z_col, np.nan)
        if pd.isna(z_value):
            continue
        candidate = (output_signal_name(signal_name), signal_label, float(z_value), float(score))
        candidates.append(candidate)
        aligned = (
            direction == "NONE"
            or (direction == "LOW" and float(z_value) < 0)
            or (direction == "HIGH" and float(z_value) > 0)
        )
        if aligned and float(score) >= float(best[3]):
            best = candidate
    if best[0] == "none" and candidates:
        best = max(candidates, key=lambda item: item[3])
    return best


def safe_ratio(numerator: Any, denominator: Any) -> float:
    if pd.isna(numerator) or pd.isna(denominator):
        return float("nan")
    denom = float(denominator)
    if abs(denom) < 1e-9:
        return float("nan")
    return float(numerator) / denom


def display_peer_columns(value: Any) -> str:
    replacements = {
        "feature_ratio_bucket": "feature_ratio_bucket",
        "exposure_bucket": "exposure_bucket",
    }
    text = str(value)
    parts = [replacements.get(part, part) for part in text.split("+")]
    return "+".join(parts)


def decision_label_text(row: pd.Series) -> str:
    label = str(row.get("anomaly_label", "NORMAL"))
    action = str(row.get("action_label", "NORMAL"))
    if label == "NORMAL":
        return "anomali degil"
    if "PEER_ASSIGNMENT_MISMATCH" in action:
        return "peer atama review"
    if "PEER_SELF_CONFLICT" in action:
        return "sinyal celiskisi review"
    if "LOW_CONFIDENCE" in action or "SPARSE_HISTORY" in action:
        return "ek inceleme review"
    if action.startswith("WATCHLIST"):
        return "watchlist"
    if label in {"HIGH_MAIN_METRIC_ANOMALY", "HIGH_BILL_ANOMALY"} or action.startswith("LIKELY_HIGH"):
        return "yuksek ana metrik anomalisi"
    if label in {"LOW_MAIN_METRIC_ANOMALY", "LOW_BILL_ANOMALY"} or action.startswith("LIKELY_LOW"):
        return "dusuk ana metrik anomalisi"
    return "anomali review"


def strongest_signal_detail(row: pd.Series) -> str:
    signal_name = str(row.get("strongest_signal_name", "none"))
    actual = row.get("main_metric", np.nan)
    expected = row.get("expected_main_metric", np.nan)

    if signal_name == "customer_seasonal":
        seasonal = row.get("customer_seasonal_expected_main_metric", np.nan)
        return (
            f"Skorlanan ana metrik {fmt_num(actual)}; musterinin ayni sezon beklentisi "
            f"{fmt_num(seasonal)}; ana metrik/sezonsal beklenti orani {fmt_num(safe_ratio(actual, seasonal), 3)}."
        )
    if signal_name == "customer_trend":
        trend = row.get("customer_trend_expected_main_metric", np.nan)
        return (
            f"Skorlanan ana metrik {fmt_num(actual)}; musterinin trend beklentisi "
            f"{fmt_num(trend)}; ana metrik/trend beklenti orani {fmt_num(safe_ratio(actual, trend), 3)}."
        )
    if signal_name == "self_history":
        prior = row.get("prior_median_main_metric", np.nan)
        return (
            f"Skorlanan ana metrik {fmt_num(actual)}; musterinin gecmis medyani "
            f"{fmt_num(prior)}; ana metrik/kendi medyan orani {fmt_num(safe_ratio(actual, prior), 3)}."
        )
    if signal_name == "customer_recent_regime":
        recent = row.get("customer_recent3_median_main_metric", np.nan)
        return (
            f"Skorlanan ana metrik {fmt_num(actual)}; musterinin son 3 ay medyani "
            f"{fmt_num(recent)}; ana metrik/son 3 ay medyan orani {fmt_num(safe_ratio(actual, recent), 3)}."
        )
    if signal_name == "current_peer":
        current_peer = row.get("current_peer_median_main_metric", np.nan)
        return (
            f"Skorlanan ana metrik {fmt_num(actual)}; ayni ay peer medyani "
            f"{fmt_num(current_peer)}; ana metrik/current peer orani {fmt_num(safe_ratio(actual, current_peer), 3)}."
        )
    if signal_name == "historical_peer":
        return (
            f"Skorlanan ana metrik {fmt_num(actual)}; peer beklenen ana metrik "
            f"{fmt_num(expected)}; ana metrik/peer beklenen orani {fmt_num(safe_ratio(actual, expected), 3)}."
        )
    if signal_name == "peer_trend":
        peer_trend = row.get("peer_trend_expected_main_metric", np.nan)
        return (
            f"Skorlanan ana metrik {fmt_num(actual)}; peer trend beklentisi "
            f"{fmt_num(peer_trend)}; ana metrik/peer trend orani {fmt_num(safe_ratio(actual, peer_trend), 3)}."
        )
    if signal_name == "feature_ratio":
        main_reference_ratio = row.get("main_to_reference_ratio", np.nan)
        numerator_col = row.get("ratio_numerator_source_col", "ana_metrik")
        denominator_col = row.get("ratio_denominator_source_col", "referans_feature")
        return (
            f"Skorlanan ana metrik {fmt_num(actual)}; referans feature {denominator_col}="
            f"{fmt_num(row.get('reference_feature', np.nan))}; {numerator_col}/{denominator_col} "
            f"orani {fmt_num(main_reference_ratio, 6)}."
        )
    return (
        f"Skorlanan ana metrik {fmt_num(actual)}; beklenen ana metrik {fmt_num(expected)}; "
        f"ana metrik/beklenen orani {fmt_num(safe_ratio(actual, expected), 3)}."
    )


def augment_scores_for_outputs(scores: pd.DataFrame) -> pd.DataFrame:
    if len(scores) == 0:
        return scores.copy()
    out = scores.copy()
    signal_rows = out.apply(strongest_signal, axis=1, result_type="expand")
    signal_rows.columns = [
        "strongest_signal_name",
        "strongest_signal_label",
        "strongest_signal_z",
        "strongest_signal_score_pct",
    ]
    out = pd.concat([out.reset_index(drop=True), signal_rows.reset_index(drop=True)], axis=1)
    out["operational_decision"] = out.apply(
        lambda row: operational_decision(row.get("action_label"), row.get("anomaly_label"), True),
        axis=1,
    )
    out["is_main_metric_anomaly"] = out["anomaly_label"].isin(
        ["HIGH_MAIN_METRIC_ANOMALY", "LOW_MAIN_METRIC_ANOMALY", "HIGH_BILL_ANOMALY", "LOW_BILL_ANOMALY"]
    )
    out["human_readable_reason"] = out.apply(build_human_reason, axis=1)
    return out


def build_human_reason(row: pd.Series) -> str:
    label = str(row.get("anomaly_label", "NORMAL"))
    score = row.get("final_anomaly_score", np.nan)
    signal = row.get("strongest_signal_label", "major sinyal yok")
    signal_score = row.get("strongest_signal_score_pct", np.nan)
    signal_detail = strongest_signal_detail(row)
    decision = decision_label_text(row)

    if label == "NORMAL":
        raw_score = row.get("raw_evidence_score", score)
        raw_text = ""
        seasonal_text = ""
        score_value = float(score) if pd.notna(score) else 0.0
        if pd.notna(raw_score) and float(raw_score) >= 90.0 and float(raw_score) > score_value + 2.0:
            raw_text = (
                f" Ham evidence skoru {fmt_num(raw_score, 1)}; final skor ay ici operasyonel esik "
                "ve effect guardrail ile kalibre edildi."
            )
        recent_z = row.get("customer_recent_regime_z", np.nan)
        seasonal_z = row.get("customer_seasonal_z", np.nan)
        recent_enabled = bool(row.get("customer_recent_regime_evidence_enabled", True))
        if (not recent_enabled) and pd.notna(recent_z) and pd.notna(seasonal_z):
            seasonal_text = (
                f" Son 3 ay rejim sapmasi var (z={fmt_num(recent_z, 2)}), ancak ayni ay sezon "
                f"beklentisiyle uyumlu (sezon z={fmt_num(seasonal_z, 2)}); bu nedenle karar sinyali yapilmadi."
            )
        return (
            f"Anomali degil. Ana sinyal: {signal} ({fmt_num(signal_score, 1)}%). {signal_detail} "
            f"Karar: {decision}; operasyonel skor {fmt_num(score, 1)}."
            f"{seasonal_text}{raw_text}"
        )

    return (
        f"Ana neden: {signal} ({fmt_num(signal_score, 1)}%). {signal_detail} "
        f"Karar: {decision}; skor {fmt_num(score, 1)}."
    )


def build_not_scored_reason(row: pd.Series) -> str:
    reason = row.get("not_scored_reason", "UNKNOWN")
    return (
        f"Skorlanamadi: {reason}. Ana metrik doldurulmadi; bu musteri icin secilebilir peer destegi "
        "veya gecerli ana metrik bilgisi yeterli olmadigi icin anomali karari uretilemedi."
    )


def build_decision_table(
    scores: pd.DataFrame,
    not_scored: pd.DataFrame,
    profile: dict[str, Any],
    scoring_month: int,
    prepared: pd.DataFrame | None = None,
) -> pd.DataFrame:
    scored = scores.copy()
    scored["ANOMALI_FLAG"] = scored["anomaly_label"].map(anomaly_flag_from_label).astype(int)
    source_rows = source_columns_for_scoring(prepared, profile, scoring_month) if prepared is not None else pd.DataFrame()
    source_column_names = set(str(col) for col in profile.get("source_columns", []))
    internal_key_cols = [col for col in ["customer_id", "invoice_month"] if col not in source_column_names]
    if len(source_rows):
        scored_out = scored[["customer_id", "invoice_month"]].merge(source_rows, on=["customer_id", "invoice_month"], how="left")
        scored_out = scored_out.drop(columns=internal_key_cols, errors="ignore")
    else:
        scored_out = restore_input_columns(scored, profile)
    scored_out["ANOMALI_FLAG"] = scored["ANOMALI_FLAG"].to_numpy()
    scored_out["ANOMALI_SKORU"] = pd.to_numeric(scored["final_anomaly_score"], errors="coerce").to_numpy()
    scored_out["ANOMALI_NEDENI"] = scored["human_readable_reason"].to_numpy()

    pieces = [scored_out]

    if len(not_scored):
        ns = not_scored.copy()
        ns["ANOMALI_FLAG"] = 0
        ns["operational_decision"] = "NOT_SCORED"
        ns["anomaly_label"] = "NOT_SCORED"
        ns["anomaly_direction"] = "NONE"
        ns["action_label"] = "NOT_SCORED"
        ns["human_readable_reason"] = ns.apply(build_not_scored_reason, axis=1)

        if len(source_rows):
            ns_out = ns[["customer_id", "invoice_month"]].merge(source_rows, on=["customer_id", "invoice_month"], how="left")
            ns_out = ns_out.drop(columns=internal_key_cols, errors="ignore")
        else:
            ns_out = restore_input_columns(ns, profile)
        ns_out["ANOMALI_FLAG"] = ns["ANOMALI_FLAG"].to_numpy()
        ns_out["ANOMALI_SKORU"] = 0.0
        ns_out["ANOMALI_NEDENI"] = ns["human_readable_reason"].to_numpy()
        pieces.append(ns_out)

    out = pd.concat(pieces, ignore_index=True, sort=False)
    input_cols = input_output_columns(profile, list(out.columns))
    output_cols = input_cols + ["ANOMALI_FLAG", "ANOMALI_SKORU", "ANOMALI_NEDENI"]
    source_names = profile_input_column_map(profile)
    sort_cols = [
        col
        for col in [source_names.get("customer_id", "customer_id"), source_names.get("invoice_month", "invoice_month")]
        if col in out.columns
    ]
    if sort_cols:
        return out[output_cols].sort_values(sort_cols).reset_index(drop=True)
    return out[output_cols].reset_index(drop=True)


def month_range(start_period: int, end_period: int) -> list[int]:
    start_year, start_month = divmod(int(start_period), 100)
    end_year, end_month = divmod(int(end_period), 100)
    periods: list[int] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        periods.append(year * 100 + month)
        month += 1
        if month == 13:
            year += 1
            month = 1
    return periods


def build_peer_monthly_stats(prepared: pd.DataFrame, peer_group_columns: list[str]) -> dict[str, pd.DataFrame]:
    valid = prepared.loc[prepared["valid_main_metric_for_model"] & prepared["main_metric"].notna()].copy()
    ratio_enabled = bool(prepared.attrs.get("feature_ratio", {}).get("enabled", False))
    valid["main_to_reference_ratio"] = np.where(
        ratio_enabled & valid["reference_feature_for_model"].astype(float).gt(0),
        valid["main_metric"] / valid["reference_feature_for_model"].astype(float),
        np.nan,
    )
    out: dict[str, pd.DataFrame] = {}
    for group_cols_text in sorted(set(col for col in peer_group_columns if isinstance(col, str) and col)):
        cols = [] if group_cols_text == "global" else group_cols_text.split("+")
        key_cols = ["_global_key"] if not cols else cols
        stats = (
            valid.groupby(["invoice_month", *key_cols], dropna=False)
            .agg(
                peer_month_row_count=("customer_id", "size"),
                peer_month_customer_count=("customer_id", "nunique"),
                peer_month_main_metric_median=("main_metric", "median"),
                peer_month_main_metric_mean=("main_metric", "mean"),
                peer_month_reference_feature_median=("reference_feature_for_model", "median"),
                peer_month_main_to_reference_median=("main_to_reference_ratio", "median"),
            )
            .reset_index()
        )
        stats = stats.rename(columns={col: f"peer_key_{col}" for col in key_cols})
        stats["peer_group_columns"] = group_cols_text
        out[group_cols_text] = stats
    return out


def build_detail_context(scores: pd.DataFrame, not_scored: pd.DataFrame) -> pd.DataFrame:
    dynamic_peer_cols = sorted(
        {
            col
            for cols_text in scores.get("peer_group_columns", pd.Series(dtype=object)).dropna().astype(str)
            for col in ([] if cols_text == "global" else cols_text.split("+"))
            if col in scores.columns
        }
    )
    scored_cols = [
        "customer_id",
        "invoice_month",
        "calendar_month",
        "feature_ratio_bucket",
        "exposure_bucket",
        "main_metric",
        "reference_feature",
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
        "expected_main_metric",
        "current_peer_median_main_metric",
        "prior_median_main_metric",
        "peer_trend_expected_main_metric",
        "customer_trend_expected_main_metric",
        "customer_seasonal_expected_main_metric",
        "customer_recent3_median_main_metric",
        "customer_recent3_range_log",
        "actual_to_expected_ratio",
        "main_to_reference_ratio",
        "historical_peer_z",
        "current_peer_z",
        "peer_trend_z",
        "feature_ratio_z",
        "self_history_z",
        "customer_trend_z",
        "customer_seasonal_z",
        "customer_recent_regime_z",
        "historical_peer_score",
        "current_peer_score",
        "peer_trend_score",
        "feature_ratio_score",
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
        "peer_mean_median_ratio",
        "peer_mean_median_gap_log",
        "peer_std_median_ratio",
        "peer_current_iqr_log",
        "peer_history_monthly_iqr_log",
        "peer_current_mad_log",
        "peer_history_monthly_mad_log",
        "peer_selection_reason",
        "scoring_strategy",
        "model_challenger_score",
        "model_challenger_warning",
        "pca_challenger_score",
        "if_challenger_score",
        "lof_challenger_score",
        "pca_challenger_anomaly_flag",
        "if_challenger_anomaly_flag",
        "lof_challenger_anomaly_flag",
        "behavior_cluster",
        "behavior_history_n",
        "behavior_level_bucket",
        "behavior_volatility_bucket",
        "behavior_trend_bucket",
        "behavior_median_main_metric",
        "behavior_volatility_log",
        "behavior_trend_slope",
        "final_anomaly_score",
        "confidence",
        "anomaly_direction",
        "anomaly_label",
        "action_label",
        "operational_decision",
        "evidence_strength",
        "signal_consistency",
        "peer_alignment_status",
        "peer_alignment_direction",
        "peer_alignment_peer_z",
        "peer_alignment_customer_z",
        "scoreability_status",
        "reason_codes",
        "human_readable_reason",
        "strongest_signal_name",
        "strongest_signal_label",
        "strongest_signal_z",
        "strongest_signal_score_pct",
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
        "model_fill_policy",
        "previous_score_month",
        "previous_final_anomaly_score",
        "score_delta_vs_previous_month",
        "score_trend_diagnostic",
    ]
    scored_cols.extend(col for col in dynamic_peer_cols if col not in scored_cols)
    scored = scores[[col for col in scored_cols if col in scores.columns]].copy()
    scored["is_scored"] = True

    if len(not_scored):
        ns = not_scored.copy()
        ns["is_scored"] = False
        ns["anomaly_label"] = "NOT_SCORED"
        ns["action_label"] = "NOT_SCORED"
        ns["operational_decision"] = "NOT_SCORED"
        ns["final_anomaly_score"] = 0.0
        ns["model_challenger_score"] = 0.0
        ns["pca_challenger_score"] = 0.0
        ns["if_challenger_score"] = 0.0
        ns["lof_challenger_score"] = 0.0
        ns["pca_challenger_anomaly_flag"] = 0
        ns["if_challenger_anomaly_flag"] = 0
        ns["lof_challenger_anomaly_flag"] = 0
        ns["model_challenger_warning"] = "NOT_SCORED"
        ns["evidence_strength"] = "not_scored"
        ns["signal_consistency"] = "not_scored"
        ns["human_readable_reason"] = ns.apply(build_not_scored_reason, axis=1)
        ns["reason_codes"] = ns.get("not_scored_reason", "NOT_SCORED")
        for col in scored_cols:
            if col not in ns.columns:
                ns[col] = np.nan
        scored = pd.concat([scored, ns[[col for col in scored.columns if col in ns.columns]]], ignore_index=True, sort=False)

    rename = {
        "invoice_month": "scoring_invoice_month",
        "calendar_month": "scoring_calendar_month",
        "feature_ratio_bucket": "scoring_feature_ratio_bucket",
        "exposure_bucket": "scoring_exposure_bucket",
        "main_metric": "scoring_main_metric",
        "reference_feature": "scoring_reference_feature",
        "expected_main_metric": "scoring_peer_expected_main_metric",
        "prior_median_main_metric": "scoring_customer_prior_median_main_metric",
        "human_readable_reason": "decision_reason_sentence",
        "final_anomaly_score": "anomaly_score",
        "confidence": "confidence_pct",
    }
    context = scored.rename(columns=rename)
    peer_key_columns = list(dict.fromkeys([*PEER_KEY_COLUMNS, *dynamic_peer_cols]))
    for col in peer_key_columns:
        source = rename.get(col, col)
        if source in context.columns:
            context[f"peer_key_{col}"] = context[source]
        elif col == "_global_key":
            context[f"peer_key_{col}"] = "ALL"
        else:
            context[f"peer_key_{col}"] = np.nan
    context["peer_key__global_key"] = "ALL"
    return context


def build_month_comment(row: pd.Series) -> str:
    if bool(row.get("is_scoring_month", False)):
        return row.get("decision_reason_sentence", "")
    if bool(row.get("customer_main_metric_missing_flag", False)):
        return "Bu ay musteri ana metrigi yok; deger doldurulmadi ve gap/coverage bilgisinde takip edilir."
    ratio = row.get("customer_vs_peer_month_ratio", np.nan)
    if pd.isna(ratio):
        return "Bu ay musteri ana metrigi var; peer medyani hesaplanamadigi icin aylik peer karsilastirmasi yok."
    percentile = row.get("customer_vs_peer_ratio_percentile", np.nan)
    reference_n = row.get("customer_vs_peer_ratio_reference_n", np.nan)
    if pd.notna(percentile):
        return (
            f"Gecmis ayda ana metrik/peer orani {fmt_num(ratio, 2)}; secili peer oran "
            f"dagiliminda persentil {fmt_num(float(percentile) * 100, 1)} "
            f"(referans n={fmt_num(reference_n, 0)})."
        )
    return f"Gecmis ayda musteri ana metrigi peer medyanina yakin; oran {fmt_num(ratio, 2)}."


def add_peer_ratio_context(detail: pd.DataFrame) -> pd.DataFrame:
    out = detail.copy()
    out["customer_vs_peer_ratio_percentile"] = np.nan
    out["customer_vs_peer_ratio_reference_n"] = np.nan

    scoring = out["is_scoring_month"].fillna(False)
    missing = out["customer_main_metric_missing_flag"].fillna(False)
    ratio = out["customer_vs_peer_month_ratio"]
    valid_mask = ~scoring & ~missing & ratio.notna() & ratio.astype(float).gt(0)
    if not bool(valid_mask.any()):
        return out

    work = out.loc[valid_mask].copy()
    work["_log_ratio"] = np.log(work["customer_vs_peer_month_ratio"].astype(float).clip(lower=1e-9))
    peer_key_cols = [
        col
        for col in out.columns
        if col.startswith("peer_key_") and work[col].notna().any()
    ]
    group_cols = ["peer_group_level_name", "peer_group_columns", *peer_key_cols]
    group_size = work.groupby(group_cols, dropna=False)["_log_ratio"].transform("size")
    group_pct = work.groupby(group_cols, dropna=False)["_log_ratio"].rank(pct=True, method="average")

    out.loc[work.index, "customer_vs_peer_ratio_percentile"] = group_pct.astype(float)
    out.loc[work.index, "customer_vs_peer_ratio_reference_n"] = group_size.astype(float)
    return out


def assign_month_comments(detail: pd.DataFrame) -> pd.Series:
    comments = pd.Series("Gecmis ayda musteri ana metrigi peer medyanina yakin.", index=detail.index, dtype=object)
    scoring = detail["is_scoring_month"].fillna(False)
    missing = detail["customer_main_metric_missing_flag"].fillna(False) & ~scoring
    ratio = detail["customer_vs_peer_month_ratio"]
    comments.loc[missing] = "Bu ay musteri ana metrigi yok; deger doldurulmadi ve gap/coverage bilgisinde takip edilir."
    comments.loc[ratio.isna() & ~missing & ~scoring] = (
        "Bu ay musteri ana metrigi var; peer medyani hesaplanamadigi icin aylik peer karsilastirmasi yok."
    )
    contextual = ~missing & ~scoring & ratio.notna() & detail["customer_vs_peer_ratio_percentile"].notna()
    comments.loc[contextual] = (
        "Gecmis ayda ana metrik/peer orani "
        + ratio.loc[contextual].map(lambda value: fmt_num(value, 2))
        + "; secili peer oran dagiliminda persentil "
        + (detail.loc[contextual, "customer_vs_peer_ratio_percentile"] * 100).map(lambda value: fmt_num(value, 1))
        + " (referans n="
        + detail.loc[contextual, "customer_vs_peer_ratio_reference_n"].map(lambda value: fmt_num(value, 0))
        + ")."
    )
    comments.loc[scoring] = detail.loc[scoring, "decision_reason_sentence"].fillna("")
    return comments


def merge_peer_stats(detail: pd.DataFrame, peer_stats: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if not peer_stats:
        return detail
    detail = detail.copy()
    detail["_detail_row_id"] = np.arange(len(detail))
    pieces: list[pd.DataFrame] = []
    handled = pd.Series(False, index=detail.index)
    peer_stat_cols = [
        "peer_month_row_count",
        "peer_month_customer_count",
        "peer_month_main_metric_median",
        "peer_month_main_metric_mean",
        "peer_month_reference_feature_median",
        "peer_month_main_to_reference_median",
    ]
    for group_cols_text, stats in peer_stats.items():
        mask = detail["peer_group_columns"].eq(group_cols_text)
        if not bool(mask.any()):
            continue
        cols = [] if group_cols_text == "global" else group_cols_text.split("+")
        key_cols = ["_global_key"] if not cols else cols
        merge_keys = ["invoice_month", *[f"peer_key_{col}" for col in key_cols]]
        sub = detail.loc[mask].merge(stats, on=merge_keys, how="left", suffixes=("", "_peer_stats"))
        pieces.append(sub)
        handled.loc[mask] = True

    rest = detail.loc[~handled].copy()
    for col in peer_stat_cols:
        rest[col] = np.nan
    pieces.append(rest)
    out = pd.concat(pieces, ignore_index=True, sort=False)
    return out.sort_values("_detail_row_id").drop(columns=["_detail_row_id"])


def build_detail_table(
    prepared: pd.DataFrame,
    scores: pd.DataFrame,
    not_scored: pd.DataFrame,
    scoring_month: int,
    profile: dict[str, Any],
    derived_features_config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    history = prepared.copy()
    history_before_scoring = history.loc[history["invoice_month"].lt(scoring_month)]
    effective_derived = derived_features_config or profile.get("derived_features", {})
    feature_edges = core.fit_feature_bucket_edges(history_before_scoring, effective_derived)
    legacy_edges = core.fit_reference_feature_edges(history_before_scoring)
    history = core.assign_feature_buckets(history, feature_edges)
    history = core.assign_feature_ratio_bucket(history, legacy_edges)
    behavior_enabled = bool(profile.get("behavior_peer_enabled", core.behavior_peer_enabled(derived_features_config)))
    if behavior_enabled:
        behavior = core.build_behavior_clusters(history.loc[history["invoice_month"].lt(scoring_month)])
        history = core.assign_behavior_clusters(history, behavior)
    ratio_enabled = bool(profile.get("feature_ratio_enabled", False))
    history["main_to_reference_ratio"] = np.where(
        ratio_enabled & history["reference_feature_for_model"].astype(float).gt(0),
        history["main_metric"] / history["reference_feature_for_model"].astype(float),
        np.nan,
    )

    context = build_detail_context(scores, not_scored)
    periods = month_range(int(history["invoice_month"].min()), int(scoring_month))
    month_frame = pd.DataFrame({"invoice_month": periods})
    month_frame["calendar_month"] = month_frame["invoice_month"].map(core.period_label)
    first_month = (
        history.groupby("customer_id", dropna=False)["invoice_month"]
        .min()
        .reset_index(name="customer_first_invoice_month")
    )
    customers = context[["customer_id"]].drop_duplicates().merge(first_month, on="customer_id", how="left")
    customers["customer_first_invoice_month"] = customers["customer_first_invoice_month"].fillna(scoring_month).astype(int)
    grid = customers.merge(month_frame, how="cross")
    grid = grid.loc[grid["invoice_month"].ge(grid["customer_first_invoice_month"])].drop(
        columns=["customer_first_invoice_month"]
    )

    series_cols = [
        "customer_id",
        "invoice_month",
        "exposure_feature",
        "exposure_bucket",
        "exposure_feature_missing_flag",
        "feature_ratio_bucket",
        "main_metric",
        "reference_feature",
        "main_to_reference_ratio",
        "source_row_count",
        "customer_obs_count_total",
        "month_gap_from_previous",
    ]
    for col in profile.get("source_columns", []):
        if col not in series_cols and col in history.columns:
            series_cols.append(col)
    customer_series = history[[col for col in series_cols if col in history.columns]].rename(
        columns={
            "exposure_feature": "customer_month_exposure_feature",
            "exposure_bucket": "customer_month_exposure_bucket",
            "exposure_feature_missing_flag": "customer_month_exposure_feature_missing_flag",
            "feature_ratio_bucket": "customer_month_feature_ratio_bucket",
            "main_metric": "customer_main_metric",
            "reference_feature": "customer_reference_feature",
            "main_to_reference_ratio": "customer_main_to_reference_ratio",
        }
    )
    detail = grid.merge(customer_series, on=["customer_id", "invoice_month"], how="left")
    detail = detail.merge(context, on="customer_id", how="left")
    detail["is_scoring_month"] = detail["invoice_month"].eq(scoring_month)
    detail["customer_main_metric_missing_flag"] = detail["customer_main_metric"].isna()

    peer_stats = build_peer_monthly_stats(history, context["peer_group_columns"].dropna().astype(str).tolist())
    detail = merge_peer_stats(detail, peer_stats)
    detail["customer_vs_peer_month_ratio"] = detail["customer_main_metric"] / detail["peer_month_main_metric_median"].clip(lower=1e-6)
    detail = add_peer_ratio_context(detail)
    detail["month_level_comment"] = assign_month_comments(detail)

    scoring_only_cols = [
        "scoring_main_metric",
        "scoring_peer_expected_main_metric",
        "current_peer_median_main_metric",
        "scoring_customer_prior_median_main_metric",
        "customer_trend_expected_main_metric",
        "customer_seasonal_expected_main_metric",
        "customer_recent3_median_main_metric",
        "customer_recent3_range_log",
        "peer_trend_expected_main_metric",
        "actual_to_expected_ratio",
        "anomaly_score",
        "confidence_pct",
        "anomaly_label",
        "anomaly_direction",
        "operational_decision",
        "action_label",
        "evidence_strength",
        "signal_consistency",
        "peer_alignment_status",
        "peer_alignment_direction",
        "peer_alignment_peer_z",
        "peer_alignment_customer_z",
        "scoreability_status",
        "reason_codes",
        "decision_reason_sentence",
        "strongest_signal_name",
        "strongest_signal_label",
        "strongest_signal_z",
        "strongest_signal_score_pct",
        "historical_peer_z",
        "historical_peer_score",
        "current_peer_z",
        "current_peer_score",
        "peer_trend_z",
        "peer_trend_score",
        "feature_ratio_z",
        "feature_ratio_score",
        "feature_ratio_signal_requested",
        "feature_ratio_quality_gate_passed",
        "feature_ratio_quality_gate_reasons",
        "feature_ratio_peer_gate_passed",
        "feature_ratio_peer_gate_reasons",
        "self_history_z",
        "self_history_score",
        "customer_trend_z",
        "customer_trend_score",
        "customer_seasonal_z",
        "customer_seasonal_score",
        "customer_recent_regime_z",
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
        "pca_challenger_score",
        "if_challenger_score",
        "lof_challenger_score",
        "pca_challenger_anomaly_flag",
        "if_challenger_anomaly_flag",
        "lof_challenger_anomaly_flag",
        "behavior_cluster",
        "behavior_history_n",
        "behavior_level_bucket",
        "behavior_volatility_bucket",
        "behavior_trend_bucket",
        "behavior_median_main_metric",
        "behavior_volatility_log",
        "behavior_trend_slope",
        "previous_score_month",
        "previous_final_anomaly_score",
        "score_delta_vs_previous_month",
        "score_trend_diagnostic",
    ]
    non_scoring_mask = ~detail["is_scoring_month"]
    for col in scoring_only_cols:
        if col in detail.columns:
            if pd.api.types.is_bool_dtype(detail[col]):
                detail[col] = detail[col].astype("boolean")
                detail.loc[non_scoring_mask, col] = pd.NA
            else:
                detail.loc[non_scoring_mask, col] = np.nan

    source_names = profile_input_column_map(profile)
    out = pd.DataFrame(index=detail.index)
    used_detail_columns: set[str] = set()
    for source_col in profile.get("source_columns", []):
        if source_col in detail.columns:
            out[source_col] = detail[source_col]
            used_detail_columns.add(source_col)
    for logical, source_name in source_names.items():
        if logical == "customer_id":
            out[source_name] = detail["customer_id"]
            used_detail_columns.add("customer_id")
        elif logical == "invoice_month":
            out[source_name] = detail["invoice_month"]
            used_detail_columns.add("invoice_month")
        elif logical == "exposure_feature":
            out[source_name] = detail["customer_month_exposure_feature"]
            used_detail_columns.add("customer_month_exposure_feature")
        elif logical == "main_metric":
            out[source_name] = detail["customer_main_metric"]
            used_detail_columns.add("customer_main_metric")
        elif logical == "reference_feature":
            out[source_name] = detail["customer_reference_feature"]
            used_detail_columns.add("customer_reference_feature")

    out["ANA_METRIK_EKSIK_MI"] = detail["customer_main_metric_missing_flag"]
    used_detail_columns.add("customer_main_metric_missing_flag")
    ratio_settings = profile.get("feature_ratio", {})
    ratio_enabled = bool(profile.get("feature_ratio_enabled", False))
    if ratio_enabled:
        out["ORAN_PAY_KOLON"] = str(ratio_settings.get("numerator_source_col", ""))
        out["ORAN_PAYDA_KOLON"] = str(ratio_settings.get("denominator_source_col", ""))
        out["MUSTERI_ANA_METRIK_PAYDA_ORANI"] = detail["customer_main_to_reference_ratio"]
        used_detail_columns.add("customer_main_to_reference_ratio")
    out["MUSTERI_TOPLAM_AY_ADET"] = detail["customer_obs_count_total"]
    out["ONCEKI_AYA_GAP"] = detail["month_gap_from_previous"]
    out["PEER_SEVIYE"] = detail["peer_group_level_name"]
    out["PEER_KOLONLARI"] = detail["peer_group_columns"].map(display_peer_columns)
    out["PEER_AYLIK_MUSTERI_ADET"] = detail["peer_month_customer_count"]
    out["PEER_AYLIK_SATIR_ADET"] = detail["peer_month_row_count"]
    out["PEER_AYLIK_ANA_METRIK_MEDYAN"] = detail["peer_month_main_metric_median"]
    out["PEER_AYLIK_ANA_METRIK_ORTALAMA"] = detail["peer_month_main_metric_mean"]
    used_detail_columns.update(
        [
            "customer_obs_count_total",
            "month_gap_from_previous",
            "peer_group_level_name",
            "peer_group_columns",
            "peer_month_customer_count",
            "peer_month_row_count",
            "peer_month_main_metric_median",
            "peer_month_main_metric_mean",
        ]
    )
    if ratio_enabled:
        out["PEER_AYLIK_ORAN_PAYDA_MEDYAN"] = detail["peer_month_reference_feature_median"]
        out["PEER_AYLIK_ANA_METRIK_PAYDA_ORAN_MEDYAN"] = detail["peer_month_main_to_reference_median"]
        used_detail_columns.update(["peer_month_reference_feature_median", "peer_month_main_to_reference_median"])
    out["MUSTERI_PEER_ANA_METRIK_ORANI"] = detail["customer_vs_peer_month_ratio"]
    out["MUSTERI_PEER_ORAN_PCTL"] = detail["customer_vs_peer_ratio_percentile"]
    out["MUSTERI_PEER_ORAN_REF_N"] = detail["customer_vs_peer_ratio_reference_n"]
    out["AYLIK_YORUM"] = detail["month_level_comment"]
    used_detail_columns.update(
        [
            "customer_vs_peer_month_ratio",
            "customer_vs_peer_ratio_percentile",
            "customer_vs_peer_ratio_reference_n",
            "month_level_comment",
        ]
    )

    def add_contract_column(source_col: str, output_col: str) -> None:
        if source_col in detail.columns:
            out[output_col] = detail[source_col]
            used_detail_columns.add(source_col)

    add_contract_column("anomaly_score", "ANOMALI_SKORU")
    add_contract_column("confidence_pct", "GUVEN_SKORU")
    add_contract_column("anomaly_label", "ANOMALI_ETIKETI")
    add_contract_column("anomaly_direction", "ANOMALI_YONU")
    add_contract_column("operational_decision", "OPERASYON_KARARI")
    add_contract_column("action_label", "AKSIYON_KARARI")
    add_contract_column("scoreability_status", "VERI_YETERLILIK_DURUMU")
    add_contract_column("decision_reason_sentence", "ANOMALI_NEDENI")

    technical_columns = {
        "month_ord",
        "month_of_year",
        "calendar_month",
        "period_label",
    }
    for source_col in detail.columns:
        if source_col in used_detail_columns or source_col in technical_columns:
            continue
        if source_col.startswith("_") or source_col in out.columns:
            continue
        out[source_col] = detail[source_col]
    out["MODEL_DONEM_AY"] = int(scoring_month)
    invoice_source_col = source_names.get("invoice_month", "invoice_month")
    anomaly_flag = pd.Series(0, index=out.index, dtype=int)
    anomaly_flag.loc[
        out.get("ANOMALI_ETIKETI", pd.Series(index=out.index, dtype=object)).notna()
        & ~out.get("ANOMALI_ETIKETI", pd.Series(index=out.index, dtype=object)).isin(["NORMAL", "NOT_SCORED"])
    ] = 1
    out["ANOMALI_FLAG"] = anomaly_flag.astype(int)

    ordered_cols = detail_output_columns(profile, list(out.columns))
    sort_cols = [
        col
        for col in [source_names.get("customer_id", "customer_id"), source_names.get("invoice_month", "invoice_month")]
        if col in out.columns
    ]
    if sort_cols:
        return out[ordered_cols].sort_values(sort_cols)
    return out[ordered_cols]


def add_run_columns(frame: pd.DataFrame, scoring_month: int) -> pd.DataFrame:
    out = frame.copy()
    out.insert(0, "scoring_month", int(scoring_month))
    out.insert(1, "scoring_calendar_month", core.period_label(scoring_month))
    return out


def read_oracle_job_config(info_dir: Path, job_file: str) -> dict[str, str]:
    path = info_dir / job_file
    if not path.exists():
        return {}
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if not parser.has_section("JOB"):
        return {}
    return {key: value for key, value in parser["JOB"].items()}


def read_oracle_connection_config(info_dir: Path, config_file: str, section: str | None) -> tuple[str, dict[str, str]]:
    path = info_dir / config_file
    if not path.exists():
        raise FileNotFoundError(f"Oracle config file not found: {path}")

    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    selected_section = section
    if selected_section is None:
        sections = parser.sections()
        if len(sections) != 1:
            raise ValueError("Oracle section must be provided when config has multiple sections.")
        selected_section = sections[0]

    if not parser.has_section(selected_section):
        raise ValueError(f"Oracle config section not found: {selected_section}")
    cfg = {key: value for key, value in parser[selected_section].items()}
    required = ["user", "password", "host", "port", "service_name"]
    missing = [key for key in required if not cfg.get(key)]
    if missing:
        raise ValueError(f"Oracle config missing required keys: {missing}")
    return selected_section, cfg


def direct_oracle_connection_config(connection_config: dict[str, Any] | None) -> tuple[str, dict[str, str]] | None:
    if not connection_config:
        return None
    cfg = {str(key): str(value) for key, value in connection_config.items() if value is not None}
    has_direct_dsn = bool(cfg.get("dsn"))
    has_host_dsn = all(cfg.get(key) for key in ["host", "port", "service_name"])
    if not (cfg.get("user") and cfg.get("password") and (has_direct_dsn or has_host_dsn)):
        return None
    selected_section = cfg.get("section") or "YAML_DIRECT"
    return selected_section, cfg


def resolve_oracle_connection_config(
    info_dir: Path,
    config_file: str,
    section: str | None,
    connection_config: dict[str, Any] | None = None,
) -> tuple[str, dict[str, str]]:
    direct = direct_oracle_connection_config(connection_config)
    if direct is not None:
        return direct
    return read_oracle_connection_config(info_dir, config_file, section)


def oracle_dsn(conn_cfg: dict[str, str]) -> str:
    try:
        import oracledb
    except ImportError as exc:
        raise RuntimeError("Oracle yazimi icin Python paketi gerekli: pip install oracledb") from exc
    if conn_cfg.get("dsn"):
        return str(conn_cfg["dsn"])
    return oracledb.makedsn(conn_cfg["host"], int(conn_cfg["port"]), service_name=conn_cfg["service_name"])


def validate_oracle_identifier(value: str, label: str) -> str:
    normalized = str(value).strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,29}", normalized):
        raise ValueError(f"Invalid Oracle {label}: {value!r}")
    return normalized


def oracle_object_name(value: str) -> str:
    return oracle_column_name(value)


def oracle_column_name(column: str) -> str:
    candidate = str(column)
    candidate = re.sub(r"[^A-Za-z0-9_$#]", "_", candidate).upper()
    if not re.match(r"^[A-Z]", candidate):
        candidate = f"C_{candidate}"
    candidate = re.sub(r"_+", "_", candidate).strip("_")
    if len(candidate) <= ORACLE_IDENTIFIER_MAX_LEN:
        return candidate
    digest = hashlib.sha1(candidate.encode("utf-8")).hexdigest()[:6].upper()
    stem_len = ORACLE_IDENTIFIER_MAX_LEN - len(digest) - 1
    stem = candidate[:stem_len].rstrip("_")
    return f"{stem}_{digest}"


def oracle_column_map(columns: list[str]) -> dict[str, str]:
    used: set[str] = set()
    out: dict[str, str] = {}
    for column in columns:
        base = oracle_column_name(column)
        candidate = base
        counter = 1
        while candidate in used:
            suffix = f"_{counter}"
            candidate = f"{base[: ORACLE_IDENTIFIER_MAX_LEN - len(suffix)]}{suffix}"
            counter += 1
        used.add(candidate)
        out[column] = candidate
    return out


def oracle_dtype_for_series(name: str, series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "NUMBER(1)"
    if pd.api.types.is_integer_dtype(series):
        return "NUMBER(38)"
    if pd.api.types.is_float_dtype(series):
        return "NUMBER"
    non_null = series.dropna()
    max_len = int(non_null.astype(str).str.len().max()) if len(non_null) else 0
    likely_long_text = bool(re.search(r"(REASON|NEDEN|COMMENT|YORUM|WARNING|UYARI|GEREKCE)", str(name).upper()))
    if max_len > 3900 or likely_long_text:
        return "CLOB"
    return "VARCHAR2(4000 CHAR)"


def dataframe_for_oracle(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str], dict[str, str]]:
    out = frame.copy()
    bool_cols = [col for col in out.columns if pd.api.types.is_bool_dtype(out[col])]
    for col in bool_cols:
        out[col] = out[col].astype("Int64")
    col_map = oracle_column_map(list(out.columns))
    dtype_map = {col_map[col]: oracle_dtype_for_series(col, frame[col]) for col in frame.columns}
    out = out.rename(columns=col_map)
    out = out.astype(object).where(pd.notna(out), None)
    return out, col_map, dtype_map


def oracle_table_exists(connection: Any, owner: str, table: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "select count(*) from all_tables where owner = :owner and table_name = :table_name",
            {"owner": owner, "table_name": table},
        )
        return int(cursor.fetchone()[0]) > 0


def oracle_table_columns(connection: Any, owner: str, table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            select column_name
            from all_tab_columns
            where owner = :owner and table_name = :table_name
            """,
            {"owner": owner, "table_name": table},
        )
        return {str(row[0]).upper() for row in cursor.fetchall()}


def oracle_table_columns_ordered(connection: Any, owner: str, table: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            select column_name
            from all_tab_columns
            where owner = :owner and table_name = :table_name
            order by column_id
            """,
            {"owner": owner, "table_name": table},
        )
        return [str(row[0]).upper() for row in cursor.fetchall()]


def create_oracle_table(connection: Any, owner: str, table: str, dtype_map: dict[str, str]) -> None:
    columns_sql = ",\n  ".join(f"{col} {dtype}" for col, dtype in dtype_map.items())
    ddl = f"create table {owner}.{table} (\n  {columns_sql}\n)"
    with connection.cursor() as cursor:
        cursor.execute(ddl)


def drop_oracle_table(connection: Any, owner: str, table: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"drop table {owner}.{table} purge")


def prepare_oracle_table(
    connection: Any,
    owner: str,
    table: str,
    dtype_map: dict[str, str],
    mode: str,
    create_table: bool,
    scoring_month: int,
    table_role: str,
    period_column: str | None = None,
) -> None:
    exists = oracle_table_exists(connection, owner, table)
    if mode == "replace" and exists:
        drop_oracle_table(connection, owner, table)
        exists = False
    if not exists:
        if not create_table:
            raise ValueError(f"Oracle table does not exist and create_table is disabled: {owner}.{table}")
        create_oracle_table(connection, owner, table, dtype_map)
        return

    existing_columns_ordered = oracle_table_columns_ordered(connection, owner, table)
    existing_columns = set(existing_columns_ordered)
    expected_columns_ordered = list(dtype_map)
    missing_columns = set(expected_columns_ordered) - existing_columns
    extra_columns = existing_columns - set(expected_columns_ordered)
    order_changed = existing_columns_ordered != expected_columns_ordered
    if missing_columns or extra_columns or order_changed:
        if not create_table:
            raise ValueError(
                f"Oracle table {owner}.{table} schema differs and create_table is disabled: "
                f"missing={sorted(missing_columns)}, extra={sorted(extra_columns)}, order_changed={order_changed}"
            )
        drop_oracle_table(connection, owner, table)
        create_oracle_table(connection, owner, table, dtype_map)
        return

    with connection.cursor() as cursor:
        if mode == "truncate_insert":
            cursor.execute(f"truncate table {owner}.{table}")
        elif mode == "delete_insert":
            if "MODEL_DONEM_AY" in dtype_map:
                cursor.execute(
                    f"delete from {owner}.{table} where MODEL_DONEM_AY = :scoring_month",
                    {"scoring_month": scoring_month},
                )
            elif table_role == "decision" and period_column and period_column in dtype_map:
                cursor.execute(
                    f"delete from {owner}.{table} where {period_column} = :scoring_month",
                    {"scoring_month": scoring_month},
                )
            else:
                cursor.execute(f"delete from {owner}.{table}")


def insert_oracle_dataframe(connection: Any, owner: str, table: str, frame: pd.DataFrame, chunksize: int) -> int:
    columns = list(frame.columns)
    placeholders = ", ".join(f":{idx + 1}" for idx in range(len(columns)))
    sql = f"insert into {owner}.{table} ({', '.join(columns)}) values ({placeholders})"
    rows_inserted = 0
    with connection.cursor() as cursor:
        for start in range(0, len(frame), chunksize):
            chunk = frame.iloc[start : start + chunksize]
            rows = [tuple(row) for row in chunk.itertuples(index=False, name=None)]
            cursor.executemany(sql, rows)
            rows_inserted += len(rows)
    return rows_inserted


def write_outputs_to_oracle(
    decision_table: pd.DataFrame,
    detail_table: pd.DataFrame,
    scoring_month: int,
    info_dir: Path,
    config_file: str,
    job_file: str,
    oracle_section: str | None,
    owner: str | None,
    decision_table_name: str,
    detail_table_name: str,
    write_mode: str | None,
    chunksize: int | None,
    create_table: bool,
    connection_config: dict[str, Any] | None = None,
    decision_period_column: str | None = None,
) -> dict[str, Any]:
    try:
        import oracledb
    except ImportError as exc:
        raise RuntimeError("Oracle yazimi icin Python paketi gerekli: pip install oracledb") from exc

    job_cfg = read_oracle_job_config(info_dir, job_file)
    section = oracle_section or job_cfg.get("oracle_section")
    selected_section, conn_cfg = resolve_oracle_connection_config(info_dir, config_file, section, connection_config)
    owner_candidate = owner or job_cfg.get("output_owner") or job_cfg.get("score_owner")
    if not owner_candidate:
        raise ValueError("Oracle owner is required. Provide --oracle-owner or set connection.owner/output.owner in data_source.yaml.")
    owner_name = validate_oracle_identifier(owner_candidate, "owner")
    decision_name = oracle_object_name(decision_table_name)
    detail_name = oracle_object_name(detail_table_name)
    mode = write_mode or job_cfg.get("if_exists") or "append"
    if mode not in {"append", "delete_insert", "truncate_insert", "replace"}:
        raise ValueError(f"Unsupported Oracle write mode: {mode}")
    write_chunksize = int(chunksize or job_cfg.get("chunksize") or 10000)

    decision_oracle, decision_col_map, decision_dtypes = dataframe_for_oracle(decision_table)
    detail_oracle, detail_col_map, detail_dtypes = dataframe_for_oracle(detail_table)
    decision_period_oracle = decision_col_map.get(decision_period_column or "", None)
    dsn = oracle_dsn(conn_cfg)

    with oracledb.connect(user=conn_cfg["user"], password=conn_cfg["password"], dsn=dsn) as connection:
        prepare_oracle_table(
            connection,
            owner_name,
            decision_name,
            decision_dtypes,
            mode,
            create_table,
            scoring_month,
            table_role="decision",
            period_column=decision_period_oracle,
        )
        decision_rows = insert_oracle_dataframe(connection, owner_name, decision_name, decision_oracle, write_chunksize)
        prepare_oracle_table(
            connection,
            owner_name,
            detail_name,
            detail_dtypes,
            mode,
            create_table,
            scoring_month,
            table_role="detail",
            period_column=None,
        )
        detail_rows = insert_oracle_dataframe(connection, owner_name, detail_name, detail_oracle, write_chunksize)
        connection.commit()

    return {
        "oracle_section": selected_section,
        "owner": owner_name,
        "decision_table": decision_name,
        "detail_table": detail_name,
        "write_mode": mode,
        "chunksize": write_chunksize,
        "decision_rows_inserted": decision_rows,
        "detail_rows_inserted": detail_rows,
        "decision_column_map": decision_col_map,
        "detail_column_map": detail_col_map,
    }


def safe_output_stem(value: str | None) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "anomaly_input")).strip("_")
    return candidate or "anomaly_input"


def apply_rolling_window(prepared: pd.DataFrame, scoring_month: int, rolling_window_months: int | None) -> pd.DataFrame:
    if rolling_window_months is None or int(rolling_window_months) <= 0:
        return prepared
    scoring_ord = core.period_ord(scoring_month)
    min_ord = scoring_ord - int(rolling_window_months) + 1
    out = prepared.loc[prepared["month_ord"].between(min_ord, scoring_ord)].copy()
    out.attrs = dict(prepared.attrs)
    return out


def run_implementation_scoring(
    input_path: Path | None,
    output_dir: Path,
    decision_output_dir: Path | None = None,
    detail_output_dir: Path | None = None,
    contract_output_dir: Path | None = None,
    source_frame: pd.DataFrame | None = None,
    input_name: str | None = None,
    input_column_map: dict[str, str] | None = None,
    output_source_columns: list[str] | None = None,
    exclude_source_columns: list[str] | None = None,
    encoding: str = "auto",
    sep: str = "auto",
    scoring_month: str = "last",
    rolling_window_months: int | None = 36,
    watch_top_rate: float = 0.030,
    high_top_rate: float = 0.0075,
    include_prior_score_diagnostic: bool = False,
    peer_config: adaptive.PeerSelectionConfig | None = None,
    support_thresholds: adaptive.PeerSupportThresholds | None = None,
    scoring_weights: dict[str, Any] | None = None,
    score_aggregation: dict[str, Any] | None = None,
    derived_features_config: dict[str, Any] | None = None,
    write_oracle: bool = False,
    oracle_info_dir: Path | None = None,
    oracle_config_file: str = "ora_config.ini",
    oracle_job_file: str = "job.ini",
    oracle_section: str | None = None,
    oracle_owner: str | None = None,
    oracle_decision_table: str = "ANOMALY_DECISIONS",
    oracle_detail_table: str = "ANOMALY_DECISION_DETAIL",
    oracle_write_mode: str | None = None,
    oracle_chunksize: int | None = None,
    oracle_create_table: bool = True,
    oracle_connection_config: dict[str, Any] | None = None,
    write_local_tables: bool = True,
    write_contract: bool = True,
    return_output_tables: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    def progress(message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)

    progress("implementation_prepare_dirs_start")
    decision_dir = decision_output_dir or output_dir
    detail_dir = detail_output_dir or output_dir
    contract_dir = contract_output_dir or output_dir
    if write_local_tables or write_contract:
        output_dir.mkdir(parents=True, exist_ok=True)
    if write_local_tables:
        decision_dir.mkdir(parents=True, exist_ok=True)
        detail_dir.mkdir(parents=True, exist_ok=True)
    if write_contract:
        contract_dir.mkdir(parents=True, exist_ok=True)
    progress("implementation_prepare_dirs_done")

    if source_frame is not None:
        progress(f"source_prepare_start rows={len(source_frame):,} source={input_name}")
        prepared, profile = core.prepare_source_frame(
            source_frame,
            column_map=input_column_map,
            source_name=input_name,
            derived_features_config=derived_features_config,
        )
    elif input_path is not None:
        progress(f"source_prepare_start input_path={input_path}")
        prepared, profile = core.read_source(
            input_path,
            encoding,
            sep,
            column_map=input_column_map,
            derived_features_config=derived_features_config,
        )
    else:
        raise ValueError("Either input_path or source_frame must be provided.")
    progress(
        "source_prepare_done "
        f"monthly_rows={len(prepared):,} customers={prepared['customer_id'].nunique():,}"
    )
    profile = apply_source_column_policy(profile, output_source_columns, exclude_source_columns)

    scoring_month_int = core.normalize_scoring_month(scoring_month, prepared["invoice_month"])
    progress(f"rolling_window_start scoring_month={scoring_month_int} months={rolling_window_months}")
    prepared = apply_rolling_window(prepared, scoring_month_int, rolling_window_months)
    profile["rolling_window_months"] = rolling_window_months
    profile["fit_window_period_min"] = int(prepared["invoice_month"].min())
    profile["fit_window_period_max"] = int(prepared["invoice_month"].max())
    progress(
        "rolling_window_done "
        f"rows={len(prepared):,} period_min={profile['fit_window_period_min']} period_max={profile['fit_window_period_max']}"
    )

    effective_peer_config = adaptive.with_excluded_variables(
        peer_config or adaptive.PeerSelectionConfig(),
        peer_role_exclusions(profile),
    )
    progress("score_scoring_month_start")
    run = core.score_scoring_month(
        prepared,
        scoring_month_int,
        watch_top_rate,
        high_top_rate,
        peer_config=effective_peer_config,
        support_thresholds=support_thresholds,
        scoring_weights=scoring_weights,
        score_aggregation=score_aggregation,
        derived_features_config=derived_features_config,
        progress=progress,
    )
    progress(
        "score_scoring_month_done "
        f"scored_rows={len(run.scores):,} not_scored_rows={len(run.not_scored):,}"
    )
    if include_prior_score_diagnostic:
        progress("prior_score_diagnostic_start")
        run = core.attach_prior_score_diagnostic(
            prepared,
            run,
            watch_top_rate,
            high_top_rate,
            peer_config=effective_peer_config,
            support_thresholds=support_thresholds,
            scoring_weights=scoring_weights,
            score_aggregation=score_aggregation,
            derived_features_config=derived_features_config,
            progress=progress,
        )
        progress("prior_score_diagnostic_done")

    progress("summary_start")
    summary = core.summarize_run(run, profile)
    progress("summary_done")

    progress("output_tables_start")
    scores_for_outputs = augment_scores_for_outputs(run.scores)
    decision_table = build_decision_table(scores_for_outputs, run.not_scored, profile, scoring_month_int, prepared=prepared)
    detail_table = build_detail_table(
        prepared,
        scores_for_outputs,
        run.not_scored,
        scoring_month_int,
        profile,
        derived_features_config=derived_features_config,
    )
    progress(
        "output_tables_done "
        f"decision_rows={len(decision_table):,} detail_rows={len(detail_table):,}"
    )

    source_stem = safe_output_stem(input_name or (input_path.stem if input_path is not None else None))
    paths: dict[str, str] = {}
    if write_local_tables:
        decision_path = decision_dir / f"{source_stem}_anomaly_decisions_{scoring_month_int}.csv"
        detail_path = detail_dir / f"{source_stem}_anomaly_decision_detail_{scoring_month_int}.csv"
        progress("csv_write_start")
        decision_table.to_csv(decision_path, index=False, encoding="utf-8-sig")
        detail_table.to_csv(detail_path, index=False, encoding="utf-8-sig")
        paths["decision_table_csv"] = str(decision_path)
        paths["detail_table_csv"] = str(detail_path)
        progress(f"csv_write_done decision_csv={decision_path} detail_csv={detail_path}")
    else:
        progress("csv_write_skipped")

    oracle_payload: dict[str, Any] | None = None
    if write_oracle:
        progress(
            "oracle_write_start "
            f"decision_table={oracle_decision_table} detail_table={oracle_detail_table} mode={oracle_write_mode}"
        )
        oracle_payload = write_outputs_to_oracle(
            decision_table=decision_table,
            detail_table=detail_table,
            scoring_month=scoring_month_int,
            info_dir=oracle_info_dir or DEFAULT_ORACLE_INFO_DIR,
            config_file=oracle_config_file,
            job_file=oracle_job_file,
            oracle_section=oracle_section,
            owner=oracle_owner,
            decision_table_name=oracle_decision_table,
            detail_table_name=oracle_detail_table,
            write_mode=oracle_write_mode,
            chunksize=oracle_chunksize,
            create_table=oracle_create_table,
            connection_config=oracle_connection_config,
            decision_period_column=profile_input_column_map(profile).get("invoice_month", "invoice_month"),
        )
        progress("oracle_write_done")

    payload = {
        "scoring_month": scoring_month_int,
        "scoring_month_label": core.period_label(scoring_month_int),
        "summary": summary,
        "paths": paths,
        "contract": {
            "target": "none",
            "main_metric_filling": "none",
            "fit_scope": "months before scoring_month only",
            "rolling_window_months": rolling_window_months,
            "score_range": "0-100",
            "previous_score_usage": (
                "diagnostic only" if include_prior_score_diagnostic else "not calculated in this production run"
            ),
            "score_aggregation": core.merged_score_aggregation(score_aggregation),
        "primary_tables": {
            "decision_table": (
                "one row per scoring-month customer; raw input columns plus "
                "ANOMALI_FLAG, ANOMALI_SKORU, and ANOMALI_NEDENI"
            ),
            "detail_table": "one row per customer-month for scoring customers; customer series, model metrics, selected-peer evidence, and explanations",
        },
        },
    }
    if oracle_payload is not None:
        payload["oracle_write"] = {
            key: value
            for key, value in oracle_payload.items()
            if key not in {"decision_column_map", "detail_column_map"}
        }
        payload["oracle_column_maps"] = {
            "decision": oracle_payload["decision_column_map"],
            "detail": oracle_payload["detail_column_map"],
        }
    if return_output_tables:
        payload["_output_tables"] = {
            "decision": decision_table,
            "detail": detail_table,
        }
    if write_contract:
        contract_path = contract_dir / f"{source_stem}_implementation_contract_{scoring_month_int}.json"
        payload["paths"]["implementation_contract_json"] = str(contract_path)
        contract_payload = {
            key: value
            for key, value in payload.items()
            if key != "_output_tables"
        }
        contract_path.write_text(json.dumps(contract_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        progress(f"contract_write_done path={contract_path}")
    else:
        progress("contract_write_skipped")
    return payload


def main() -> None:
    args = parse_args()
    result = run_implementation_scoring(
        input_path=Path(args.input),
        output_dir=Path(args.output_dir),
        decision_output_dir=Path(args.decision_output_dir) if args.decision_output_dir else None,
        detail_output_dir=Path(args.detail_output_dir) if args.detail_output_dir else None,
        contract_output_dir=Path(args.contract_output_dir) if args.contract_output_dir else None,
        encoding=args.encoding,
        sep=args.sep,
        scoring_month=args.scoring_month,
        rolling_window_months=args.rolling_window_months,
        watch_top_rate=args.watch_top_rate,
        high_top_rate=args.high_top_rate,
        include_prior_score_diagnostic=args.include_prior_score_diagnostic,
        write_oracle=args.write_oracle,
        oracle_info_dir=Path(args.oracle_info_dir),
        oracle_config_file=args.oracle_config_file,
        oracle_job_file=args.oracle_job_file,
        oracle_section=args.oracle_section,
        oracle_owner=args.oracle_owner,
        oracle_decision_table=args.oracle_decision_table,
        oracle_detail_table=args.oracle_detail_table,
        oracle_write_mode=args.oracle_write_mode,
        oracle_chunksize=args.oracle_chunksize,
        oracle_create_table=not args.oracle_no_create_table,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
