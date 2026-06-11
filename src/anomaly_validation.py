from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sys
import time
from typing import Any

import numpy as np
import pandas as pd

import adaptive_peer_selection as adaptive
import anomaly_config
import anomaly_implementation as implementation
import anomaly_model as core
import anomaly_pipeline as pipeline


@dataclass
class ValidationContext:
    config_path: Path
    pipeline_config: dict[str, Any]
    project_root: Path
    source: dict[str, Any]
    source_frame: pd.DataFrame | None
    input_path: Path | None
    input_name: str
    prepared_full: pd.DataFrame
    profile: dict[str, Any]
    column_map: dict[str, str]
    derived_features: dict[str, Any]
    peer_config: adaptive.PeerSelectionConfig
    support_thresholds: adaptive.PeerSupportThresholds
    model_config: dict[str, Any]
    rolling_window_months: int
    scoring_month: int


def log_step(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run rolling backtest and output integrity validation.")
    parser.add_argument("--config", default="configs/anomaly.yaml")
    parser.add_argument("--data-source-config", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--backtest-months", type=int, default=None)
    parser.add_argument("--stress-test-sample-size", type=int, default=None)
    parser.add_argument("--skip-stress-test", action="store_true")
    parser.add_argument("--synthetic-sample-size", dest="stress_test_sample_size", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--skip-synthetic", dest="skip_stress_test", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if np.isfinite(value):
            return float(value)
        return None
    if isinstance(value, (np.ndarray,)):
        return value.tolist()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return str(value)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")


def report_settings(config: dict[str, Any], project_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    reports = config.get("reports", {}) if isinstance(config.get("reports"), dict) else {}
    validation = reports.get("validation", {}) if isinstance(reports.get("validation"), dict) else {}
    output_dir = args.output_dir or validation.get("output_dir", "outputs/analysis/validation_report")
    resolved_output_dir = anomaly_config.resolve_path(str(output_dir), project_root)
    if resolved_output_dir is None:
        raise ValueError("Validation output_dir is missing.")
    return {
        "output_dir": resolved_output_dir,
        "backtest_months": int(args.backtest_months or validation.get("backtest_months", 6)),
        "stress_test_sample_size": int(
            args.stress_test_sample_size
            or validation.get("stress_test_sample_size", validation.get("synthetic_sample_size", 300))
        ),
        "stress_test_spike_factor": float(
            validation.get("stress_test_spike_factor", validation.get("synthetic_spike_factor", 2.0))
        ),
        "stress_test_drop_factor": float(
            validation.get("stress_test_drop_factor", validation.get("synthetic_drop_factor", 0.25))
        ),
        "skip_stress_test": bool(
            args.skip_stress_test or validation.get("skip_stress_test", validation.get("skip_synthetic", False))
        ),
    }


def load_context(config_path: Path, data_source_config_path: str | None) -> ValidationContext:
    log_step(f"validation_config_load_start config={config_path}")
    pipeline_config = anomaly_config.load_yaml_config(config_path)
    project_root = pipeline.project_root_from_config(config_path, pipeline_config)
    data_source_config = pipeline.load_data_source_config(pipeline_config, project_root, data_source_config_path)
    source = pipeline.selected_data_source(pipeline_config, data_source_config)
    model_config = pipeline_config.get("model", {}) if isinstance(pipeline_config.get("model"), dict) else {}
    column_map = anomaly_config.column_map_from_config(pipeline_config)
    derived_features = anomaly_config.derived_features_from_config(pipeline_config)
    log_step(f"validation_source_read_start source={source.get('name')} type={source.get('type', 'csv')}")
    input_path, source_frame, input_name, _ = pipeline.read_source_frame(
        pipeline_config,
        source,
        project_root,
        write_snapshot=False,
    )
    if source_frame is not None:
        prepared_full, profile = core.prepare_source_frame(
            source_frame,
            column_map=column_map,
            source_name=input_name,
            derived_features_config=derived_features,
        )
    elif input_path is not None:
        prepared_full, profile = core.read_source(
            input_path,
            encoding=str(source.get("encoding", "auto")),
            sep=str(source.get("sep", "auto")),
            column_map=column_map,
            derived_features_config=derived_features,
        )
    else:
        raise ValueError("No input source was resolved.")

    output_source_columns, exclude_source_columns = anomaly_config.source_column_policy(source)
    profile = implementation.apply_source_column_policy(profile, output_source_columns, exclude_source_columns)
    prepared_full.attrs["profile"] = profile
    rolling_window_months = int(model_config.get("rolling_window_months", 36))
    scoring_month = core.normalize_scoring_month(model_config.get("scoring_month", "last"), prepared_full["invoice_month"])
    peer_config = adaptive.with_excluded_variables(
        anomaly_config.peer_config_from_config(pipeline_config),
        implementation.peer_role_exclusions(profile),
    )
    support_thresholds = anomaly_config.support_thresholds_from_config(pipeline_config)
    log_step(
        "validation_source_read_done "
        f"rows={len(prepared_full):,} customers={prepared_full['customer_id'].nunique():,} "
        f"period_min={int(prepared_full['invoice_month'].min())} period_max={int(prepared_full['invoice_month'].max())}"
    )
    return ValidationContext(
        config_path=config_path,
        pipeline_config=pipeline_config,
        project_root=project_root,
        source=source,
        source_frame=source_frame,
        input_path=input_path,
        input_name=input_name,
        prepared_full=prepared_full,
        profile=profile,
        column_map=column_map,
        derived_features=derived_features,
        peer_config=peer_config,
        support_thresholds=support_thresholds,
        model_config=model_config,
        rolling_window_months=rolling_window_months,
        scoring_month=scoring_month,
    )


def selected_backtest_months(prepared: pd.DataFrame, scoring_month: int, backtest_months: int) -> list[int]:
    available = sorted(int(month) for month in prepared["invoice_month"].dropna().unique() if int(month) <= scoring_month)
    candidates = []
    for month in available:
        if prepared["invoice_month"].lt(month).any() and prepared["invoice_month"].eq(month).any():
            candidates.append(month)
    return candidates[-max(backtest_months, 0) :]


def score_month(ctx: ValidationContext, month: int) -> core.ModelRun:
    window = implementation.apply_rolling_window(ctx.prepared_full, month, ctx.rolling_window_months)
    return core.score_scoring_month(
        window,
        month,
        float(ctx.model_config.get("watch_top_rate", 0.030)),
        float(ctx.model_config.get("high_top_rate", 0.0075)),
        peer_config=ctx.peer_config,
        support_thresholds=ctx.support_thresholds,
        scoring_weights=ctx.model_config.get("scoring_weights", {}),
        score_aggregation=ctx.model_config.get("score_aggregation", {}),
        derived_features_config=ctx.derived_features,
    )


def count_label(scores: pd.DataFrame, label: str) -> int:
    if len(scores) == 0 or "anomaly_label" not in scores:
        return 0
    return int(scores["anomaly_label"].eq(label).sum())


def flag_count(scores: pd.DataFrame, column: str) -> int:
    if len(scores) == 0 or column not in scores:
        return 0
    return int(pd.to_numeric(scores[column], errors="coerce").fillna(0).astype(float).gt(0).sum())


def safe_median(scores: pd.DataFrame, column: str) -> float | None:
    if len(scores) == 0 or column not in scores:
        return None
    value = pd.to_numeric(scores[column], errors="coerce").median()
    if pd.isna(value):
        return None
    return float(value)


def safe_quantile(scores: pd.DataFrame, column: str, quantile: float) -> float | None:
    if len(scores) == 0 or column not in scores:
        return None
    value = pd.to_numeric(scores[column], errors="coerce").quantile(quantile)
    if pd.isna(value):
        return None
    return float(value)


def safe_rate(scores: pd.DataFrame, column: str) -> float | None:
    if len(scores) == 0 or column not in scores:
        return None
    value = pd.to_numeric(scores[column], errors="coerce").mean()
    if pd.isna(value):
        return None
    return float(value)


def summarize_month(month: int, run: core.ModelRun, elapsed_seconds: float) -> dict[str, Any]:
    scores = run.scores
    scored_rows = int(len(scores))
    scoring_rows = int(run.scoring_rows)
    watch_count = int(scores["is_watchlist_or_anomaly"].sum()) if scored_rows and "is_watchlist_or_anomaly" in scores else 0
    high_count = int(scores["is_high_anomaly"].sum()) if scored_rows and "is_high_anomaly" in scores else 0
    return {
        "scoring_month": int(month),
        "scoring_calendar_month": core.period_label(month),
        "train_rows": int(run.train_rows),
        "scoring_rows": scoring_rows,
        "scored_rows": scored_rows,
        "not_scored_rows": int(len(run.not_scored)),
        "scored_rate": float(scored_rows / scoring_rows) if scoring_rows else None,
        "normal_count": count_label(scores, "NORMAL"),
        "watchlist_high_count": count_label(scores, "WATCHLIST_HIGH"),
        "watchlist_low_count": count_label(scores, "WATCHLIST_LOW"),
        "high_anomaly_count": count_label(scores, "HIGH_MAIN_METRIC_ANOMALY"),
        "low_anomaly_count": count_label(scores, "LOW_MAIN_METRIC_ANOMALY"),
        "watch_or_anomaly_count": watch_count,
        "watch_or_anomaly_rate": float(watch_count / scored_rows) if scored_rows else None,
        "high_anomaly_total_count": high_count,
        "high_anomaly_rate": float(high_count / scored_rows) if scored_rows else None,
        "median_score": safe_median(scores, "final_anomaly_score"),
        "p95_score": safe_quantile(scores, "final_anomaly_score", 0.95),
        "p99_score": safe_quantile(scores, "final_anomaly_score", 0.99),
        "median_confidence": safe_median(scores, "confidence"),
        "watchlist_threshold": run.thresholds.get("watchlist_threshold"),
        "high_anomaly_threshold": run.thresholds.get("high_anomaly_threshold"),
        "peer_objective_score_median": safe_median(scores, "peer_objective_score"),
        "peer_representability_score_median": safe_median(scores, "peer_representability_score"),
        "peer_distribution_score_median": safe_median(scores, "peer_distribution_quality_score"),
        "peer_calibration_score_median": safe_median(scores, "peer_calibration_score"),
        "peer_calibration_n_median": safe_median(scores, "peer_calibration_n"),
        "feature_ratio_global_gate_pass_rate": safe_rate(scores, "feature_ratio_quality_gate_passed"),
        "feature_ratio_peer_gate_pass_rate": safe_rate(scores, "feature_ratio_peer_gate_passed"),
        "model_challenger_score_median": safe_median(scores, "model_challenger_score"),
        "pca_challenger_score_median": safe_median(scores, "pca_challenger_score"),
        "if_challenger_score_median": safe_median(scores, "if_challenger_score"),
        "lof_challenger_score_median": safe_median(scores, "lof_challenger_score"),
        "pca_challenger_flag_count": flag_count(scores, "pca_challenger_anomaly_flag"),
        "if_challenger_flag_count": flag_count(scores, "if_challenger_anomaly_flag"),
        "lof_challenger_flag_count": flag_count(scores, "lof_challenger_anomaly_flag"),
        "elapsed_seconds": float(elapsed_seconds),
    }


def build_scoreability_breakdown(month: int, run: core.ModelRun) -> pd.DataFrame:
    scores = run.scores
    if len(scores) == 0 or "scoreability_status" not in scores:
        return pd.DataFrame()
    grouped = (
        scores.groupby(["scoreability_status", "peer_group_level_name"], dropna=False)
        .agg(
            rows=("customer_id", "size"),
            watch_or_anomaly_count=("is_watchlist_or_anomaly", "sum"),
            high_anomaly_count=("is_high_anomaly", "sum"),
            median_score=("final_anomaly_score", "median"),
            median_confidence=("confidence", "median"),
            peer_objective_score_median=("peer_objective_score", "median"),
            peer_calibration_score_median=("peer_calibration_score", "median"),
        )
        .reset_index()
    )
    grouped.insert(0, "scoring_month", int(month))
    grouped.insert(1, "scoring_calendar_month", core.period_label(month))
    return grouped


def build_label_breakdown(month: int, run: core.ModelRun) -> pd.DataFrame:
    scores = run.scores
    if len(scores) == 0:
        return pd.DataFrame()
    grouped = (
        scores.groupby(["anomaly_label", "anomaly_direction"], dropna=False)
        .agg(
            rows=("customer_id", "size"),
            median_score=("final_anomaly_score", "median"),
            median_confidence=("confidence", "median"),
            peer_objective_score_median=("peer_objective_score", "median"),
            peer_calibration_score_median=("peer_calibration_score", "median"),
        )
        .reset_index()
    )
    grouped.insert(0, "scoring_month", int(month))
    grouped.insert(1, "scoring_calendar_month", core.period_label(month))
    return grouped


def top_examples(month: int, run: core.ModelRun, limit: int = 200) -> pd.DataFrame:
    columns = [
        "customer_id",
        "invoice_month",
        "customer_segment",
        "sector",
        "branch_id",
        "bill_amount",
        "expected_bill_amount",
        "current_peer_median_bill",
        "prior_median_bill",
        "final_anomaly_score",
        "confidence",
        "anomaly_label",
        "anomaly_direction",
        "action_label",
        "scoreability_status",
        "peer_group_level_name",
        "peer_group_columns",
        "peer_objective_score",
        "peer_calibration_score",
        "reason_codes",
        "reason_explanation",
        "model_challenger_score",
        "pca_challenger_anomaly_flag",
        "if_challenger_anomaly_flag",
        "lof_challenger_anomaly_flag",
    ]
    if len(run.scores) == 0:
        return pd.DataFrame(columns=columns)
    out = run.scores[[col for col in columns if col in run.scores.columns]].head(limit).copy()
    out.insert(0, "scoring_month", int(month))
    out.insert(1, "scoring_calendar_month", core.period_label(month))
    return out


def run_backtest_monitor(ctx: ValidationContext, backtest_months: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, core.ModelRun]:
    months = selected_backtest_months(ctx.prepared_full, ctx.scoring_month, backtest_months)
    if not months:
        raise ValueError("No eligible backtest months found.")
    log_step(f"rolling_backtest_start months={months}")
    summary_rows: list[dict[str, Any]] = []
    scoreability_tables: list[pd.DataFrame] = []
    label_tables: list[pd.DataFrame] = []
    latest_run: core.ModelRun | None = None
    latest_month = months[-1]
    for month in months:
        started = time.perf_counter()
        run = score_month(ctx, month)
        elapsed = time.perf_counter() - started
        log_step(
            "rolling_backtest_month_done "
            f"month={month} scored_rows={len(run.scores):,} not_scored_rows={len(run.not_scored):,} "
            f"elapsed={elapsed:.1f}s"
        )
        summary_rows.append(summarize_month(month, run, elapsed))
        scoreability_tables.append(build_scoreability_breakdown(month, run))
        label_tables.append(build_label_breakdown(month, run))
        if month == latest_month:
            latest_run = run
    if latest_run is None:
        raise RuntimeError("Latest backtest run was not captured.")
    monthly_summary = pd.DataFrame(summary_rows)
    scoreability = pd.concat([table for table in scoreability_tables if len(table)], ignore_index=True, sort=False)
    labels = pd.concat([table for table in label_tables if len(table)], ignore_index=True, sort=False)
    examples = top_examples(latest_month, latest_run)
    return monthly_summary, scoreability, labels, examples, latest_run


def perturb_scoring_values(
    prepared: pd.DataFrame,
    scoring_month: int,
    customer_ids: pd.Series,
    factor: float,
) -> pd.DataFrame:
    out = prepared.copy()
    mask = out["invoice_month"].eq(scoring_month) & out["customer_id"].isin(set(customer_ids.astype(str)))
    out.loc[mask, "bill_amount"] = pd.to_numeric(out.loc[mask, "bill_amount"], errors="coerce") * factor
    out.loc[mask, "log_bill"] = np.log1p(out.loc[mask, "bill_amount"].clip(lower=0))
    out.loc[mask, "valid_bill_for_model"] = out.loc[mask, "bill_amount"].notna() & out.loc[mask, "bill_amount"].ge(0)
    out.attrs = dict(prepared.attrs)
    return out


def detection_rate(run: core.ModelRun, customer_ids: pd.Series, direction: str) -> float | None:
    if len(run.scores) == 0:
        return None
    mask = run.scores["customer_id"].isin(set(customer_ids.astype(str)))
    if not bool(mask.any()):
        return None
    detected = run.scores.loc[mask, "anomaly_label"].ne("NORMAL") & run.scores.loc[mask, "anomaly_direction"].eq(direction)
    return float(detected.mean())


def median_score_delta(base_run: core.ModelRun, perturbed_run: core.ModelRun, customer_ids: pd.Series) -> float | None:
    base = base_run.scores.loc[
        base_run.scores["customer_id"].isin(set(customer_ids.astype(str))),
        ["customer_id", "final_anomaly_score"],
    ].rename(columns={"final_anomaly_score": "base_score"})
    perturbed = perturbed_run.scores.loc[
        perturbed_run.scores["customer_id"].isin(set(customer_ids.astype(str))),
        ["customer_id", "final_anomaly_score"],
    ].rename(columns={"final_anomaly_score": "perturbed_score"})
    merged = base.merge(perturbed, on="customer_id", how="inner")
    if len(merged) == 0:
        return None
    return float((merged["perturbed_score"] - merged["base_score"]).median())


def run_stress_test_sensitivity(
    ctx: ValidationContext,
    base_run: core.ModelRun,
    sample_size: int,
    spike_factor: float,
    drop_factor: float,
) -> dict[str, Any]:
    if len(base_run.scores) == 0:
        return {"status": "skipped", "reason": "no_scored_rows"}
    sample = (
        base_run.scores.loc[base_run.scores["bill_amount"].notna() & base_run.scores["bill_amount"].gt(0), "customer_id"]
        .drop_duplicates()
        .sample(n=min(sample_size, base_run.scores["customer_id"].nunique()), random_state=42)
    )
    if len(sample) == 0:
        return {"status": "skipped", "reason": "no_positive_main_metric_rows"}
    log_step(f"stress_test_sensitivity_start sample={len(sample)}")
    base_window = implementation.apply_rolling_window(ctx.prepared_full, ctx.scoring_month, ctx.rolling_window_months)
    spike_prepared = perturb_scoring_values(base_window, ctx.scoring_month, sample, spike_factor)
    drop_prepared = perturb_scoring_values(base_window, ctx.scoring_month, sample, drop_factor)
    spike_run = core.score_scoring_month(
        spike_prepared,
        ctx.scoring_month,
        float(ctx.model_config.get("watch_top_rate", 0.030)),
        float(ctx.model_config.get("high_top_rate", 0.0075)),
        peer_config=ctx.peer_config,
        support_thresholds=ctx.support_thresholds,
        scoring_weights=ctx.model_config.get("scoring_weights", {}),
        score_aggregation=ctx.model_config.get("score_aggregation", {}),
        derived_features_config=ctx.derived_features,
    )
    drop_run = core.score_scoring_month(
        drop_prepared,
        ctx.scoring_month,
        float(ctx.model_config.get("watch_top_rate", 0.030)),
        float(ctx.model_config.get("high_top_rate", 0.0075)),
        peer_config=ctx.peer_config,
        support_thresholds=ctx.support_thresholds,
        scoring_weights=ctx.model_config.get("scoring_weights", {}),
        score_aggregation=ctx.model_config.get("score_aggregation", {}),
        derived_features_config=ctx.derived_features,
    )
    payload = {
        "status": "generated",
        "sample_size": int(len(sample)),
        "spike_factor": float(spike_factor),
        "drop_factor": float(drop_factor),
        "spike_detection_rate": detection_rate(spike_run, sample, "HIGH"),
        "drop_detection_rate": detection_rate(drop_run, sample, "LOW"),
        "spike_median_score_delta": median_score_delta(base_run, spike_run, sample),
        "drop_median_score_delta": median_score_delta(base_run, drop_run, sample),
    }
    log_step(
        "stress_test_sensitivity_done "
        f"spike_rate={payload['spike_detection_rate']} drop_rate={payload['drop_detection_rate']}"
    )
    return payload


def run_output_integrity(ctx: ValidationContext) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    log_step("output_integrity_start")
    result = implementation.run_implementation_scoring(
        input_path=ctx.input_path,
        source_frame=ctx.source_frame,
        input_name=ctx.input_name,
        output_source_columns=ctx.profile.get("source_columns"),
        exclude_source_columns=[],
        output_dir=Path("unused_validation_output"),
        input_column_map=ctx.column_map,
        encoding=str(ctx.source.get("encoding", "auto")),
        sep=str(ctx.source.get("sep", "auto")),
        scoring_month=str(ctx.scoring_month),
        rolling_window_months=ctx.rolling_window_months,
        watch_top_rate=float(ctx.model_config.get("watch_top_rate", 0.030)),
        high_top_rate=float(ctx.model_config.get("high_top_rate", 0.0075)),
        include_prior_score_diagnostic=bool(ctx.model_config.get("include_prior_score_diagnostic", True)),
        peer_config=ctx.peer_config,
        support_thresholds=ctx.support_thresholds,
        scoring_weights=ctx.model_config.get("scoring_weights", {}),
        score_aggregation=ctx.model_config.get("score_aggregation", {}),
        derived_features_config=ctx.derived_features,
        write_oracle=False,
        write_local_tables=False,
        write_contract=False,
        return_output_tables=True,
        progress_callback=log_step,
    )
    tables = result.get("_output_tables", {})
    decision = tables.get("decision", pd.DataFrame())
    detail = tables.get("detail", pd.DataFrame())
    checks = validate_output_tables(decision, detail)
    log_step(f"output_integrity_done checks={len(checks)}")
    return decision, detail, checks


def validate_output_tables(decision: pd.DataFrame, detail: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def add_check(name: str, status: str, value: Any, detail_text: str) -> None:
        rows.append({"check_name": name, "status": status, "value": value, "detail": detail_text})

    for table_name, frame in [("decision", decision), ("detail", detail)]:
        add_check(f"{table_name}_row_count_positive", "PASS" if len(frame) > 0 else "FAIL", int(len(frame)), "")
        if "ANOMALI_FLAG" in frame.columns:
            flag = pd.to_numeric(frame["ANOMALI_FLAG"], errors="coerce")
            missing = int(flag.isna().sum())
            invalid = int((~flag.dropna().isin([0, 1])).sum())
            add_check(
                f"{table_name}_anomaly_flag_not_missing",
                "PASS" if missing == 0 else "FAIL",
                missing,
                "ANOMALI_FLAG missing count",
            )
            add_check(
                f"{table_name}_anomaly_flag_binary",
                "PASS" if invalid == 0 else "FAIL",
                invalid,
                "ANOMALI_FLAG values must be 0 or 1",
            )
        else:
            add_check(f"{table_name}_anomaly_flag_exists", "FAIL", "missing", "ANOMALI_FLAG column not found")

    if "ANOMALI_NEDENI" in decision.columns:
        missing_reason = int(decision["ANOMALI_NEDENI"].isna().sum() + decision["ANOMALI_NEDENI"].astype(str).str.strip().eq("").sum())
        add_check(
            "decision_reason_not_blank",
            "PASS" if missing_reason == 0 else "FAIL",
            missing_reason,
            "Decision reason must be filled for all decision rows",
        )
    else:
        add_check("decision_reason_exists", "FAIL", "missing", "ANOMALI_NEDENI column not found")

    raw_like_columns = [col for col in decision.columns if col not in {"ANOMALI_FLAG", "ANOMALI_NEDENI"}]
    add_check(
        "decision_contains_raw_input_columns",
        "PASS" if raw_like_columns else "FAIL",
        len(raw_like_columns),
        "Decision table must contain raw input columns plus ANOMALI_FLAG and ANOMALI_NEDENI",
    )
    duplicate_decision_rows = int(decision.duplicated().sum()) if len(decision) else 0
    add_check(
        "decision_duplicate_full_rows",
        "PASS" if duplicate_decision_rows == 0 else "WARN",
        duplicate_decision_rows,
        "Full duplicate decision rows",
    )
    expected_conditional_missing = {
        "PEER_AYLIK_ANA_METRIK_PAYDA_ORAN_MEDYAN",
        "MUSTERI_SON3_REJIM_Z",
        "MUSTERI_SON3_REJIM_SKORU",
        "FEATURE_ORAN_Z",
        "FEATURE_ORAN_SKORU",
        "SECONDARY_SINYAL_P_DEGERI",
    }
    high_missing_cols = []
    conditional_missing_cols = []
    for col in detail.columns:
        missing_rate = float(detail[col].isna().mean()) if len(detail) else 0.0
        if missing_rate >= 0.98:
            if col in expected_conditional_missing:
                conditional_missing_cols.append(col)
            else:
                high_missing_cols.append(col)
    add_check(
        "detail_expected_conditional_missing_columns",
        "PASS",
        len(conditional_missing_cols),
        ",".join(conditional_missing_cols[:30]),
    )
    add_check(
        "detail_extreme_missing_columns",
        "PASS" if not high_missing_cols else "WARN",
        len(high_missing_cols),
        ",".join(high_missing_cols[:30]),
    )
    score_columns = {"ANOMALI_FLAG", "ANOMALI_ETIKETI", "ANOMALI_SKORU"}
    if score_columns.issubset(set(detail.columns)):
        scored_detail = detail.loc[detail["ANOMALI_ETIKETI"].notna()].copy()
        score = pd.to_numeric(scored_detail["ANOMALI_SKORU"], errors="coerce")
        flag = pd.to_numeric(scored_detail["ANOMALI_FLAG"], errors="coerce").fillna(0).astype(int)
        flagged_scores = score.loc[flag.eq(1)]
        if len(flagged_scores) > 0:
            flagged_min = float(flagged_scores.min())
            normal_score_violations = int((flag.eq(0) & score.ge(flagged_min)).sum())
            add_check(
                "detail_normal_score_below_flagged_min",
                "PASS" if normal_score_violations == 0 else "WARN",
                normal_score_violations,
                f"Normal rows should not exceed flagged score floor {flagged_min:.2f}",
            )
        else:
            add_check(
                "detail_normal_score_below_flagged_min",
                "PASS",
                0,
                "No flagged scoring rows; score ordering check skipped",
            )
    else:
        add_check(
            "detail_normal_score_below_flagged_min",
            "WARN",
            "missing_columns",
            "ANOMALI_FLAG, ANOMALI_ETIKETI, or ANOMALI_SKORU missing",
        )
    return pd.DataFrame(rows)


def build_stability_flags(monthly_summary: pd.DataFrame) -> pd.DataFrame:
    if len(monthly_summary) == 0:
        return pd.DataFrame()
    work = monthly_summary.sort_values("scoring_month").copy()
    for column in [
        "watch_or_anomaly_rate",
        "high_anomaly_rate",
        "median_score",
        "peer_objective_score_median",
        "peer_calibration_score_median",
        "scored_rate",
    ]:
        if column in work.columns:
            work[f"{column}_delta"] = pd.to_numeric(work[column], errors="coerce").diff()
    flags = []
    for _, row in work.iterrows():
        month_flags = []
        if abs(float(row.get("watch_or_anomaly_rate_delta", 0.0) or 0.0)) > 0.02:
            month_flags.append("watch_or_anomaly_rate_shift")
        if abs(float(row.get("high_anomaly_rate_delta", 0.0) or 0.0)) > 0.01:
            month_flags.append("high_anomaly_rate_shift")
        if float(row.get("scored_rate", 1.0) or 0.0) < 0.95:
            month_flags.append("scoreability_drop")
        if float(row.get("peer_calibration_score_median", 100.0) or 100.0) < 60.0:
            month_flags.append("peer_calibration_weak")
        flags.append(";".join(month_flags) if month_flags else "stable")
    work["stability_flag"] = flags
    return work


def write_markdown_report(
    output_dir: Path,
    scoring_month: int,
    monthly_summary: pd.DataFrame,
    integrity_checks: pd.DataFrame,
    stress_test: dict[str, Any],
) -> Path:
    latest = monthly_summary.sort_values("scoring_month").tail(1).iloc[0].to_dict() if len(monthly_summary) else {}
    failed = integrity_checks.loc[integrity_checks["status"].eq("FAIL")]
    warn = integrity_checks.loc[integrity_checks["status"].eq("WARN")]
    lines = [
        "# Anomaly Validation Monitor",
        "",
        f"- Scoring month: {scoring_month} ({core.period_label(scoring_month)})",
        f"- Backtest months: {len(monthly_summary)}",
        f"- Latest scored rows: {latest.get('scored_rows', 'n/a')}",
        f"- Latest not scored rows: {latest.get('not_scored_rows', 'n/a')}",
        f"- Latest watch/anomaly rate: {latest.get('watch_or_anomaly_rate', 'n/a')}",
        f"- Latest high anomaly rate: {latest.get('high_anomaly_rate', 'n/a')}",
        f"- Latest peer objective median: {latest.get('peer_objective_score_median', 'n/a')}",
        f"- Latest peer calibration median: {latest.get('peer_calibration_score_median', 'n/a')}",
        "",
        "## Output Integrity",
        "",
        f"- FAIL checks: {len(failed)}",
        f"- WARN checks: {len(warn)}",
    ]
    if len(failed):
        lines.extend(["", "### Failed Checks", ""])
        lines.extend(f"- {row.check_name}: {row.detail} ({row.value})" for row in failed.itertuples())
    if len(warn):
        lines.extend(["", "### Warning Checks", ""])
        lines.extend(f"- {row.check_name}: {row.detail} ({row.value})" for row in warn.itertuples())
    lines.extend(
        [
            "",
            "## Perturbation Stress Test",
            "",
            f"- Status: {stress_test.get('status')}",
            f"- Spike detection rate: {stress_test.get('spike_detection_rate')}",
            f"- Drop detection rate: {stress_test.get('drop_detection_rate')}",
            f"- Spike median score delta: {stress_test.get('spike_median_score_delta')}",
            f"- Drop median score delta: {stress_test.get('drop_median_score_delta')}",
            "",
            "## Files",
            "",
            "- validation_monthly_summary.csv",
            "- validation_stability_flags.csv",
            "- validation_scoreability_breakdown.csv",
            "- validation_label_breakdown.csv",
            "- validation_top_examples.csv",
            "- validation_output_integrity.csv",
            "- validation_stress_test_sensitivity.json",
        ]
    )
    path = output_dir / f"validation_report_{scoring_month}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    ctx = load_context(config_path, args.data_source_config)
    settings = report_settings(ctx.pipeline_config, ctx.project_root, args)
    output_dir: Path = settings["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)

    monthly_summary, scoreability, labels, examples, latest_run = run_backtest_monitor(
        ctx,
        int(settings["backtest_months"]),
    )
    stability = build_stability_flags(monthly_summary)
    stress_test = (
        {"status": "skipped", "reason": "disabled"}
        if settings["skip_stress_test"]
        else run_stress_test_sensitivity(
            ctx,
            latest_run,
            int(settings["stress_test_sample_size"]),
            float(settings["stress_test_spike_factor"]),
            float(settings["stress_test_drop_factor"]),
        )
    )
    _, _, integrity = run_output_integrity(ctx)

    monthly_summary.to_csv(output_dir / "validation_monthly_summary.csv", index=False, encoding="utf-8-sig")
    stability.to_csv(output_dir / "validation_stability_flags.csv", index=False, encoding="utf-8-sig")
    scoreability.to_csv(output_dir / "validation_scoreability_breakdown.csv", index=False, encoding="utf-8-sig")
    labels.to_csv(output_dir / "validation_label_breakdown.csv", index=False, encoding="utf-8-sig")
    examples.to_csv(output_dir / "validation_top_examples.csv", index=False, encoding="utf-8-sig")
    integrity.to_csv(output_dir / "validation_output_integrity.csv", index=False, encoding="utf-8-sig")
    legacy_synthetic_path = output_dir / "validation_synthetic_sensitivity.json"
    if legacy_synthetic_path.exists():
        legacy_synthetic_path.unlink()
    write_json(output_dir / "validation_stress_test_sensitivity.json", stress_test)
    write_json(
        output_dir / "validation_summary.json",
        {
            "scoring_month": ctx.scoring_month,
            "scoring_month_label": core.period_label(ctx.scoring_month),
            "backtest_months": int(settings["backtest_months"]),
            "output_dir": str(output_dir),
            "monthly_summary_rows": int(len(monthly_summary)),
            "output_integrity_fail_count": int(integrity["status"].eq("FAIL").sum()),
            "output_integrity_warn_count": int(integrity["status"].eq("WARN").sum()),
            "stress_test": stress_test,
        },
    )
    report_path = write_markdown_report(output_dir, ctx.scoring_month, monthly_summary, integrity, stress_test)
    log_step(f"validation_done output_dir={output_dir} report={report_path}")


if __name__ == "__main__":
    main()
