from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import anomaly_config
import anomaly_io
import fatura_anomaly_implementation as implementation
import fatura_peer_quality_report as peer_quality_report


def log_step(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the configurable monthly anomaly pipeline.")
    parser.add_argument("--config", default="configs/anomaly.yaml")
    parser.add_argument("--data-source-config", default=None)
    parser.add_argument("--oracle-config", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--enable-oracle-output", action="store_true")
    parser.add_argument("--skip-peer-quality-report", action="store_true")
    return parser.parse_args()


def project_root_from_config(config_path: Path, pipeline_config: dict[str, Any]) -> Path:
    configured_root = pipeline_config.get("project_root")
    if configured_root:
        resolved = anomaly_config.resolve_path(str(configured_root), config_path.parent)
        if resolved is not None:
            return resolved
    if config_path.parent.name.lower() == "configs":
        return config_path.parent.parent
    return Path.cwd()


def load_data_source_config(
    pipeline_config: dict[str, Any],
    project_root: Path,
    override_path: str | None,
) -> dict[str, Any]:
    configured_path = override_path or pipeline_config.get("data_source_config_path") or "configs/data_source.yaml"
    if not configured_path:
        return {}
    resolved = anomaly_config.resolve_path(str(configured_path), project_root)
    if resolved is None:
        return {}
    return anomaly_config.load_yaml_config(resolved)


def resolve_named_connection(data_source_config: dict[str, Any], connection_ref: Any) -> dict[str, Any]:
    if isinstance(connection_ref, dict):
        return dict(connection_ref)
    if connection_ref is None:
        return {}
    connections = data_source_config.get("connections", {})
    if not isinstance(connections, dict):
        return {}
    resolved = connections.get(str(connection_ref), {})
    return dict(resolved) if isinstance(resolved, dict) else {}


def selected_data_source(pipeline_config: dict[str, Any], data_source_config: dict[str, Any]) -> dict[str, Any]:
    source_name = str(data_source_config.get("active_source") or data_source_config.get("default_source") or "default")
    sources = data_source_config.get("sources", {})
    if not isinstance(sources, dict) or source_name not in sources:
        legacy_source = pipeline_config.get("source", {})
        if isinstance(legacy_source, dict) and legacy_source:
            return {**legacy_source, "name": legacy_source.get("name") or source_name}
        raise ValueError(f"Data source not found in data source config: {source_name}")
    source = dict(sources[source_name])
    source["name"] = source.get("name") or source_name
    source["connection"] = resolve_named_connection(data_source_config, source.get("connection"))
    return source


def selected_output_sink(
    pipeline_config: dict[str, Any],
    data_source_config: dict[str, Any],
    enable_oracle_output: bool,
) -> dict[str, Any]:
    sink_name = data_source_config.get("active_sink") or data_source_config.get("default_sink")
    sinks = data_source_config.get("sinks", {})
    if not sink_name or not isinstance(sinks, dict) or str(sink_name) not in sinks:
        return {"type": "none", "enabled": False}
    sink = dict(sinks[str(sink_name)])
    sink["name"] = sink.get("name") or str(sink_name)
    sink["connection"] = resolve_named_connection(data_source_config, sink.get("connection"))
    if enable_oracle_output:
        sink["enabled"] = True
    return sink


def read_source_frame(
    pipeline_config: dict[str, Any],
    source: dict[str, Any],
    project_root: Path,
    write_snapshot: bool = True,
) -> tuple[Path | None, Any, str, Path | None]:
    source_type = str(source.get("type", "csv")).lower()
    source_name = str(source.get("name") or "anomaly_input")
    if source_type == "csv":
        input_path = anomaly_config.resolve_path(str(source.get("path")), project_root)
        if input_path is None:
            raise ValueError("source.path is required for csv source.")
        return input_path, None, source.get("name") or input_path.stem, input_path
    if source_type == "oracle":
        frame = anomaly_io.read_oracle_frame({"connection": source.get("connection", {}), "input": source})
        snapshot_path = None
        if write_snapshot:
            staging_dir = anomaly_config.output_path(
                pipeline_config,
                "staging_dir",
                project_root,
                "outputs/staging",
            )
            snapshot_path = staging_dir / f"{implementation.safe_output_stem(source_name)}_source_snapshot.csv"
            anomaly_io.write_source_snapshot(frame, snapshot_path)
        return None, frame, source_name, snapshot_path
    raise ValueError(f"Unsupported source.type: {source_type}")


def oracle_write_options(output_sink: dict[str, Any]) -> dict[str, Any]:
    if not output_sink or str(output_sink.get("type", "none")).lower() != "oracle":
        return {"write_oracle": False}
    connection = output_sink.get("connection", {})
    return {
        "write_oracle": bool(output_sink.get("enabled", False)),
        "oracle_info_dir": Path(str(connection.get("info_dir", implementation.DEFAULT_ORACLE_INFO_DIR))),
        "oracle_config_file": str(connection.get("config_file", "ora_config.ini")),
        "oracle_job_file": str(connection.get("job_file", "job.ini")),
        "oracle_section": connection.get("section"),
        "oracle_owner": output_sink.get("owner") or connection.get("owner"),
        "oracle_decision_table": str(output_sink.get("decision_table", "ANOMALY_DECISIONS")),
        "oracle_detail_table": str(output_sink.get("detail_table", "ANOMALY_DECISION_DETAIL")),
        "oracle_write_mode": output_sink.get("write_mode", "delete_insert"),
        "oracle_chunksize": output_sink.get("chunksize"),
        "oracle_create_table": bool(output_sink.get("create_table", True)),
        "oracle_connection_config": connection,
    }


def run_peer_quality_report(
    pipeline_config: dict[str, Any],
    project_root: Path,
    source_snapshot_path: Path,
    detail_csv: Path,
    scoring_month: int,
    column_map: dict[str, str],
) -> dict[str, Any]:
    reports = pipeline_config.get("reports", {})
    peer_quality = reports.get("peer_quality", {}) if isinstance(reports, dict) else {}
    output_dir = anomaly_config.resolve_path(
        str(peer_quality.get("output_dir", "outputs/analysis/peer_quality_report")),
        project_root,
    )
    if output_dir is None:
        raise ValueError("peer_quality output_dir is missing.")
    log_step(f"peer_quality_report_start output_dir={output_dir}")
    cmd = [
        sys.executable,
        "src/peer_quality_report.py",
        "--input",
        str(source_snapshot_path),
        "--evidence-csv",
        str(detail_csv),
        "--output-dir",
        str(output_dir),
        "--scoring-month",
        str(scoring_month),
        "--column-map-json",
        json.dumps(column_map, ensure_ascii=False),
    ]
    subprocess.run(cmd, cwd=project_root, check=True)
    log_step("peer_quality_report_done")
    return {"output_dir": str(output_dir), "status": "generated"}


def peer_quality_output_dir(pipeline_config: dict[str, Any], project_root: Path) -> Path:
    reports = pipeline_config.get("reports", {})
    peer_quality = reports.get("peer_quality", {}) if isinstance(reports, dict) else {}
    output_dir = anomaly_config.resolve_path(
        str(peer_quality.get("output_dir", "outputs/analysis/peer_quality_report")),
        project_root,
    )
    if output_dir is None:
        raise ValueError("peer_quality output_dir is missing.")
    return output_dir


def run_peer_quality_report_from_frames(
    pipeline_config: dict[str, Any],
    project_root: Path,
    source_frame: Any,
    detail_frame: Any,
    scoring_month: int,
    column_map: dict[str, str],
    source_name: str,
) -> dict[str, Any]:
    output_dir = peer_quality_output_dir(pipeline_config, project_root)
    log_step(f"peer_quality_report_start output_dir={output_dir} mode=in_memory")
    result = peer_quality_report.generate_peer_quality_report_from_frames(
        source_frame=source_frame,
        evidence_frame=detail_frame,
        output_dir=output_dir,
        scoring_month=scoring_month,
        column_map=column_map,
        source_name=source_name,
    )
    log_step("peer_quality_report_done")
    return {"output_dir": str(output_dir), "status": "generated", **result}


def is_oracle_to_oracle(source: dict[str, Any], output_sink: dict[str, Any]) -> bool:
    return (
        str(source.get("type", "")).lower() == "oracle"
        and str(output_sink.get("type", "")).lower() == "oracle"
        and bool(output_sink.get("enabled", False))
    )


def run_from_config(
    config_path: Path,
    data_source_config_path: str | None = None,
    skip_peer_quality_report: bool = False,
    enable_oracle_output: bool = False,
) -> dict[str, Any]:
    log_step(f"pipeline_start config={config_path}")
    pipeline_config = anomaly_config.load_yaml_config(config_path)
    project_root = project_root_from_config(config_path, pipeline_config)
    log_step(f"project_root={project_root}")
    data_source_config = load_data_source_config(pipeline_config, project_root, data_source_config_path)
    source = selected_data_source(pipeline_config, data_source_config)
    output_sink = selected_output_sink(pipeline_config, data_source_config, enable_oracle_output)
    oracle_to_oracle = is_oracle_to_oracle(source, output_sink)
    log_step(
        "source_selected "
        f"name={source.get('name')} type={source.get('type', 'csv')} "
        f"sink={output_sink.get('name', 'none')} sink_enabled={bool(output_sink.get('enabled', False))}"
    )
    model = pipeline_config.get("model", {})
    column_map = anomaly_config.column_map_from_config(pipeline_config)
    peer_config = anomaly_config.peer_config_from_config(pipeline_config)
    support_thresholds = anomaly_config.support_thresholds_from_config(pipeline_config)
    output_source_columns, exclude_source_columns = anomaly_config.source_column_policy(source)
    reports_enabled = anomaly_config.bool_config(pipeline_config, ("reports", "peer_quality", "enabled"), True)
    outputs_config = pipeline_config.get("outputs", {})
    write_local_tables = bool(outputs_config.get("write_local_tables", not oracle_to_oracle))
    write_contract = bool(outputs_config.get("write_contract", not oracle_to_oracle))
    return_output_tables = reports_enabled and not skip_peer_quality_report and oracle_to_oracle
    log_step("source_read_start")
    input_path, source_frame, input_name, source_snapshot_path = read_source_frame(
        pipeline_config,
        source,
        project_root,
        write_snapshot=not oracle_to_oracle,
    )
    if source_frame is not None:
        snapshot_text = f" snapshot={source_snapshot_path}" if source_snapshot_path is not None else " snapshot=skipped"
        log_step(f"source_read_done rows={len(source_frame):,}{snapshot_text}")
    else:
        log_step(f"source_read_done input_path={input_path}")
    oracle_options = oracle_write_options(output_sink)

    log_step("model_scoring_start")
    result = implementation.run_implementation_scoring(
        input_path=input_path,
        source_frame=source_frame,
        input_name=input_name,
        output_source_columns=output_source_columns,
        exclude_source_columns=exclude_source_columns,
        output_dir=anomaly_config.output_path(pipeline_config, "base_dir", project_root, "outputs/production"),
        decision_output_dir=anomaly_config.output_path(
            pipeline_config,
            "decision_dir",
            project_root,
            "outputs/production/decision_table",
        ),
        detail_output_dir=anomaly_config.output_path(
            pipeline_config,
            "detail_dir",
            project_root,
            "outputs/analysis/decision_detail",
        ),
        contract_output_dir=anomaly_config.output_path(
            pipeline_config,
            "contract_dir",
            project_root,
            "outputs/production/contracts",
        ),
        input_column_map=column_map,
        encoding=str(source.get("encoding", "auto")),
        sep=str(source.get("sep", "auto")),
        scoring_month=str(model.get("scoring_month", "last")),
        rolling_window_months=int(model.get("rolling_window_months", 36)),
        watch_top_rate=float(model.get("watch_top_rate", 0.030)),
        high_top_rate=float(model.get("high_top_rate", 0.0075)),
        include_prior_score_diagnostic=bool(model.get("include_prior_score_diagnostic", True)),
        peer_config=peer_config,
        support_thresholds=support_thresholds,
        scoring_weights=model.get("scoring_weights", {}),
        score_aggregation=model.get("score_aggregation", {}),
        write_local_tables=write_local_tables,
        write_contract=write_contract,
        return_output_tables=return_output_tables,
        progress_callback=log_step,
        **oracle_options,
    )
    output_tables = result.pop("_output_tables", None)
    log_step(
        "model_scoring_done "
        f"scoring_month={result.get('scoring_month')} "
        f"local_tables_written={write_local_tables}"
    )

    if reports_enabled and not skip_peer_quality_report and source_frame is not None and output_tables is not None:
        result["peer_quality_report"] = run_peer_quality_report_from_frames(
            pipeline_config,
            project_root,
            source_frame,
            output_tables["detail"],
            int(result["scoring_month"]),
            column_map,
            input_name,
        )
    elif reports_enabled and not skip_peer_quality_report and source_snapshot_path is not None and result.get("paths", {}).get("detail_table_csv"):
        result["peer_quality_report"] = run_peer_quality_report(
            pipeline_config,
            project_root,
            source_snapshot_path,
            Path(result["paths"]["detail_table_csv"]),
            int(result["scoring_month"]),
            column_map,
        )
    else:
        log_step("peer_quality_report_skipped")
    log_step("pipeline_done")
    return result


def main() -> None:
    args = parse_args()
    data_source_config_path = args.data_source_config or args.oracle_config
    result = run_from_config(
        Path(args.config).resolve(),
        data_source_config_path,
        args.skip_peer_quality_report,
        enable_oracle_output=args.enable_oracle_output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
