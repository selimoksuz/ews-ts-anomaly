from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import adaptive_peer_selection as adaptive  # noqa: E402
import anomaly_config  # noqa: E402
import configured_anomaly_pipeline as configured_pipeline  # noqa: E402
import fatura_anomaly_implementation as implementation  # noqa: E402
import fatura_peer_anomaly_model as core  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark row-wise vs vectorized score aggregation.")
    parser.add_argument("--config", default="configs/anomaly.yaml")
    parser.add_argument("--data-source-config", default=None)
    parser.add_argument("--output-dir", default="outputs/analysis/performance")
    parser.add_argument("--min-aggregation-speedup", type=float, default=1.10)
    parser.add_argument("--min-total-speedup", type=float, default=1.00)
    parser.add_argument("--tolerance", type=float, default=1e-8)
    return parser.parse_args()


def load_prepared_data(config_path: Path, data_source_config_path: str | None) -> dict[str, Any]:
    pipeline_config = anomaly_config.load_yaml_config(config_path)
    project_root = configured_pipeline.project_root_from_config(config_path, pipeline_config)
    data_source_config = configured_pipeline.load_data_source_config(pipeline_config, project_root, data_source_config_path)
    source = configured_pipeline.selected_data_source(pipeline_config, data_source_config)
    model = pipeline_config.get("model", {})
    column_map = anomaly_config.column_map_from_config(pipeline_config)
    input_path, source_frame, input_name, _ = configured_pipeline.read_source_frame(pipeline_config, source, project_root)
    if source_frame is not None:
        prepared, profile = core.prepare_source_frame(source_frame, column_map=column_map, source_name=input_name)
    elif input_path is not None:
        prepared, profile = core.read_source(
            input_path,
            encoding=str(source.get("encoding", "auto")),
            sep=str(source.get("sep", "auto")),
            column_map=column_map,
        )
    else:
        raise ValueError("No input source was resolved.")

    scoring_month = int(prepared["invoice_month"].max()) if model.get("scoring_month", "last") == "last" else int(model["scoring_month"])
    prepared = implementation.apply_rolling_window(
        prepared,
        scoring_month,
        int(model.get("rolling_window_months", 36)),
    )
    profile = implementation.apply_source_column_policy(
        profile,
        *anomaly_config.source_column_policy(source),
    )
    peer_config = adaptive.with_excluded_variables(
        anomaly_config.peer_config_from_config(pipeline_config),
        implementation.peer_role_exclusions(profile),
    )
    return {
        "prepared": prepared,
        "scoring_month": scoring_month,
        "watch_top_rate": float(model.get("watch_top_rate", 0.030)),
        "high_top_rate": float(model.get("high_top_rate", 0.0075)),
        "peer_config": peer_config,
        "support_thresholds": anomaly_config.support_thresholds_from_config(pipeline_config),
        "scoring_weights": model.get("scoring_weights", {}),
        "score_aggregation": model.get("score_aggregation", {}),
        "source_name": source.get("name", input_name),
        "row_count": int(len(prepared)),
    }


def rowwise_aggregation(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    out = core.add_directional_signal_evidence(frame, config)
    evidence = out.apply(lambda row: core.aggregate_evidence_row(row, config), axis=1)
    return pd.concat([out.drop(columns=[col for col in evidence.columns if col in out.columns]), evidence], axis=1)


def timed_aggregation(
    fn: Callable[[pd.DataFrame, dict[str, Any]], pd.DataFrame],
    metrics: dict[str, Any],
) -> Callable[[pd.DataFrame, dict[str, Any]], pd.DataFrame]:
    def wrapper(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
        started = time.perf_counter()
        out = fn(frame, config)
        elapsed = time.perf_counter() - started
        metrics["aggregation_seconds"] += elapsed
        metrics["aggregation_calls"] += 1
        metrics["aggregation_rows"] += int(len(frame))
        return out

    return wrapper


def run_scoring(
    prepared: pd.DataFrame,
    scoring_month: int,
    watch_top_rate: float,
    high_top_rate: float,
    peer_config: adaptive.PeerSelectionConfig,
    support_thresholds: adaptive.PeerSupportThresholds,
    scoring_weights: dict[str, Any],
    score_aggregation: dict[str, Any],
    aggregation_fn: Callable[[pd.DataFrame, dict[str, Any]], pd.DataFrame],
) -> tuple[core.ModelRun, dict[str, Any]]:
    original = core.apply_directional_evidence_aggregation
    metrics: dict[str, Any] = {"aggregation_seconds": 0.0, "aggregation_calls": 0, "aggregation_rows": 0}
    core.apply_directional_evidence_aggregation = timed_aggregation(aggregation_fn, metrics)
    gc.collect()
    started = time.perf_counter()
    try:
        run = core.score_scoring_month(
            prepared,
            scoring_month,
            watch_top_rate,
            high_top_rate,
            peer_config=peer_config,
            support_thresholds=support_thresholds,
            scoring_weights=scoring_weights,
            score_aggregation=score_aggregation,
        )
    finally:
        core.apply_directional_evidence_aggregation = original
    metrics["total_seconds"] = time.perf_counter() - started
    metrics["scored_rows"] = int(len(run.scores))
    metrics["not_scored_rows"] = int(len(run.not_scored))
    return run, metrics


def compare_runs(rowwise: core.ModelRun, vectorized: core.ModelRun, tolerance: float) -> dict[str, Any]:
    key_cols = ["customer_id", "invoice_month"]
    numeric_cols = [
        "final_anomaly_score",
        "confidence",
        "customer_signal_score",
        "peer_signal_score",
        "customer_family_p_value",
        "peer_family_p_value",
        "primary_signal_p_value",
        "primary_signal_score",
        "secondary_signal_p_value",
        "customer_final_weight",
        "peer_final_weight",
        "self_history_final_weight",
        "customer_trend_final_weight",
        "customer_seasonal_final_weight",
        "historical_peer_final_weight",
        "current_peer_final_weight",
        "peer_trend_final_weight",
        "turnover_intensity_final_weight",
    ]
    categorical_cols = [
        "anomaly_label",
        "anomaly_direction",
        "evidence_driver",
        "primary_signal_name",
        "primary_signal_family",
        "secondary_signal_name",
        "customer_reliability_status",
        "peer_reliability_status",
        "evidence_conflict_flag",
    ]
    available_numeric = [col for col in numeric_cols if col in rowwise.scores.columns and col in vectorized.scores.columns]
    available_categorical = [col for col in categorical_cols if col in rowwise.scores.columns and col in vectorized.scores.columns]
    left = rowwise.scores[key_cols + available_numeric + available_categorical].copy()
    right = vectorized.scores[key_cols + available_numeric + available_categorical].copy()
    merged = left.merge(right, on=key_cols, how="outer", suffixes=("_rowwise", "_vectorized"), indicator=True)
    missing_rows = int((merged["_merge"] != "both").sum())
    max_numeric_diff = 0.0
    numeric_diff_by_col: dict[str, float] = {}
    for col in available_numeric:
        diff = (
            pd.to_numeric(merged[f"{col}_rowwise"], errors="coerce")
            - pd.to_numeric(merged[f"{col}_vectorized"], errors="coerce")
        ).abs()
        max_diff = float(diff.max(skipna=True)) if bool(diff.notna().any()) else 0.0
        numeric_diff_by_col[col] = max_diff
        max_numeric_diff = max(max_numeric_diff, max_diff)

    categorical_mismatches: dict[str, int] = {}
    for col in available_categorical:
        rowwise_values = merged[f"{col}_rowwise"].fillna("<NA>").astype(str)
        vectorized_values = merged[f"{col}_vectorized"].fillna("<NA>").astype(str)
        categorical_mismatches[col] = int((rowwise_values != vectorized_values).sum())

    output_equivalent = (
        missing_rows == 0
        and max_numeric_diff <= tolerance
        and all(count == 0 for count in categorical_mismatches.values())
    )
    return {
        "output_equivalent": bool(output_equivalent),
        "missing_or_extra_rows": missing_rows,
        "max_numeric_diff": max_numeric_diff,
        "numeric_diff_by_col": numeric_diff_by_col,
        "categorical_mismatches": categorical_mismatches,
        "rowwise_scores": int(len(rowwise.scores)),
        "vectorized_scores": int(len(vectorized.scores)),
    }


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    payload = load_prepared_data(config_path, args.data_source_config)
    vectorized_fn = core.apply_directional_evidence_aggregation

    rowwise_run, rowwise_metrics = run_scoring(
        payload["prepared"],
        payload["scoring_month"],
        payload["watch_top_rate"],
        payload["high_top_rate"],
        payload["peer_config"],
        payload["support_thresholds"],
        payload["scoring_weights"],
        payload["score_aggregation"],
        rowwise_aggregation,
    )
    vectorized_run, vectorized_metrics = run_scoring(
        payload["prepared"],
        payload["scoring_month"],
        payload["watch_top_rate"],
        payload["high_top_rate"],
        payload["peer_config"],
        payload["support_thresholds"],
        payload["scoring_weights"],
        payload["score_aggregation"],
        vectorized_fn,
    )

    comparison = compare_runs(rowwise_run, vectorized_run, float(args.tolerance))
    aggregation_speedup = rowwise_metrics["aggregation_seconds"] / max(vectorized_metrics["aggregation_seconds"], 1e-9)
    total_speedup = rowwise_metrics["total_seconds"] / max(vectorized_metrics["total_seconds"], 1e-9)
    passed = (
        comparison["output_equivalent"]
        and aggregation_speedup >= float(args.min_aggregation_speedup)
        and total_speedup >= float(args.min_total_speedup)
    )
    result = {
        "status": "passed" if passed else "failed",
        "source_name": payload["source_name"],
        "prepared_rows": payload["row_count"],
        "scoring_month": payload["scoring_month"],
        "thresholds": {
            "min_aggregation_speedup": float(args.min_aggregation_speedup),
            "min_total_speedup": float(args.min_total_speedup),
            "tolerance": float(args.tolerance),
        },
        "rowwise": rowwise_metrics,
        "vectorized": vectorized_metrics,
        "speedup": {
            "aggregation": aggregation_speedup,
            "total": total_speedup,
        },
        "comparison": comparison,
    }
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"score_aggregation_benchmark_{payload['scoring_month']}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**result, "output_path": str(output_path)}, ensure_ascii=False, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
