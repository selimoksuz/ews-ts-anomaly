from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from fatura_anomaly_implementation import run_implementation_scoring


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Development-friendly runner for the monthly invoice anomaly decision outputs."
    )
    parser.add_argument("--input", default="data/raw/encrypted_final.csv")
    parser.add_argument("--output-dir", default="outputs/development")
    parser.add_argument("--decision-output-dir", default="outputs/development/decision_table")
    parser.add_argument("--detail-output-dir", default="outputs/development/detail_table")
    parser.add_argument("--contract-output-dir", default="outputs/development/contracts")
    parser.add_argument("--encoding", default="auto")
    parser.add_argument("--sep", default="auto")
    parser.add_argument("--scoring-month", default="last", help="last, YYYYMM, YYYYMMDD, or date-like value.")
    parser.add_argument("--watch-top-rate", type=float, default=0.030)
    parser.add_argument("--high-top-rate", type=float, default=0.0075)
    parser.add_argument("--include-prior-score-diagnostic", action="store_true", default=True)
    parser.add_argument("--write-oracle", action="store_true")
    parser.add_argument("--oracle-info-dir", default=r"C:\Users\Acer\dc_all_pipe\oracle_info")
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
    )
    parser.add_argument("--oracle-chunksize", type=int, default=None)
    parser.add_argument("--oracle-no-create-table", action="store_true")
    return parser.parse_args()


def run_development(args: argparse.Namespace) -> dict[str, Any]:
    return run_implementation_scoring(
        input_path=Path(args.input),
        output_dir=Path(args.output_dir),
        decision_output_dir=Path(args.decision_output_dir) if args.decision_output_dir else None,
        detail_output_dir=Path(args.detail_output_dir) if args.detail_output_dir else None,
        contract_output_dir=Path(args.contract_output_dir) if args.contract_output_dir else None,
        encoding=args.encoding,
        sep=args.sep,
        scoring_month=args.scoring_month,
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


def main() -> None:
    result = run_development(parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
