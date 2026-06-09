#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="configs/anomaly.yaml"
DATA_SOURCE_CONFIG_PATH=""
SKIP_PEER_QUALITY_REPORT=0
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_DIR=""
HEARTBEAT_SECONDS=""

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
    --skip-peer-quality-report)
      SKIP_PEER_QUALITY_REPORT=1
      shift
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --log-dir)
      LOG_DIR="$2"
      shift 2
      ;;
    --heartbeat-seconds)
      HEARTBEAT_SECONDS="$2"
      shift 2
      ;;
    --help|-h)
      exec "$(dirname "$0")/run_configured_anomaly_pipeline.sh" --help
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

ARGS=("--config" "$CONFIG_PATH" "--python" "$PYTHON_BIN")

if [[ -n "$DATA_SOURCE_CONFIG_PATH" ]]; then
  ARGS+=("--data-source-config" "$DATA_SOURCE_CONFIG_PATH")
fi

if [[ "$SKIP_PEER_QUALITY_REPORT" -eq 1 ]]; then
  ARGS+=("--skip-peer-quality-report")
fi

if [[ -n "$LOG_DIR" ]]; then
  ARGS+=("--log-dir" "$LOG_DIR")
fi

if [[ -n "$HEARTBEAT_SECONDS" ]]; then
  ARGS+=("--heartbeat-seconds" "$HEARTBEAT_SECONDS")
fi

exec "$(dirname "$0")/run_configured_anomaly_pipeline.sh" "${ARGS[@]}"
