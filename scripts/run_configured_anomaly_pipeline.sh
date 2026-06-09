#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="configs/anomaly.yaml"
DATA_SOURCE_CONFIG_PATH=""
ENABLE_ORACLE_OUTPUT=0
SKIP_PEER_QUALITY_REPORT=0
PYTHON_BIN="${PYTHON_BIN:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config|-c)
      CONFIG_PATH="$2"
      shift 2
      ;;
    --data-source-config|-d)
      DATA_SOURCE_CONFIG_PATH="$2"
      shift 2
      ;;
    --enable-oracle-output)
      ENABLE_ORACLE_OUTPUT=1
      shift
      ;;
    --skip-peer-quality-report)
      SKIP_PEER_QUALITY_REPORT=1
      shift
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --help|-h)
      cat <<'EOF'
Usage:
  scripts/run_configured_anomaly_pipeline.sh [options]

Options:
  --config PATH                  Pipeline config path. Default: configs/anomaly.yaml
  --data-source-config PATH      Data source config path. Default comes from anomaly.yaml
  --enable-oracle-output         Enable Oracle sink write for this run
  --skip-peer-quality-report     Skip peer quality report generation
  --python BIN                   Python binary. Default: python3 or PYTHON_BIN env var
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

ARGS=("src/configured_anomaly_pipeline.py" "--config" "$CONFIG_PATH")

if [[ -n "$DATA_SOURCE_CONFIG_PATH" ]]; then
  ARGS+=("--data-source-config" "$DATA_SOURCE_CONFIG_PATH")
fi

if [[ "$ENABLE_ORACLE_OUTPUT" -eq 1 ]]; then
  ARGS+=("--enable-oracle-output")
fi

if [[ "$SKIP_PEER_QUALITY_REPORT" -eq 1 ]]; then
  ARGS+=("--skip-peer-quality-report")
fi

cd "$PROJECT_ROOT"
"$PYTHON_BIN" "${ARGS[@]}"
