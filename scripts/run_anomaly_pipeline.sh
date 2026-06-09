#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="configs/anomaly.yaml"
DATA_SOURCE_CONFIG_PATH=""
ENABLE_ORACLE_OUTPUT=0
SKIP_PEER_QUALITY_REPORT=0
PYTHON_BIN="${PYTHON_BIN:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="${LOG_DIR:-outputs/logs}"
HEARTBEAT_SECONDS="${HEARTBEAT_SECONDS:-60}"

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
    --log-dir)
      LOG_DIR="$2"
      shift 2
      ;;
    --heartbeat-seconds)
      HEARTBEAT_SECONDS="$2"
      shift 2
      ;;
    --help|-h)
      cat <<'EOF'
Usage:
  scripts/run_anomaly_pipeline.sh [options]

Options:
  --config PATH                  Pipeline config path. Default: configs/anomaly.yaml
  --data-source-config PATH      Data source config path. Default comes from anomaly.yaml
  --enable-oracle-output         Enable Oracle sink write for this run
  --skip-peer-quality-report     Skip peer quality report generation
  --python BIN                   Python binary. Default: python3 or PYTHON_BIN env var
  --log-dir PATH                 Run log directory. Default: outputs/logs or LOG_DIR env var
  --heartbeat-seconds N          Print alive heartbeat every N seconds. Default: 60; 0 disables
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

ARGS=("src/anomaly_pipeline.py" "--config" "$CONFIG_PATH")

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
mkdir -p "$LOG_DIR"
RUN_TS="$(date -u '+%Y%m%dT%H%M%SZ')"
LOG_FILE="${LOG_DIR%/}/anomaly_pipeline_${RUN_TS}.log"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] run_start"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] project_root=$PROJECT_ROOT"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] log_file=$PROJECT_ROOT/$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] command=$PYTHON_BIN ${ARGS[*]}"

HEARTBEAT_PID=""
cleanup_heartbeat() {
  if [[ -n "$HEARTBEAT_PID" ]]; then
    kill "$HEARTBEAT_PID" >/dev/null 2>&1 || true
    wait "$HEARTBEAT_PID" 2>/dev/null || true
  fi
}

if [[ "$HEARTBEAT_SECONDS" -gt 0 ]]; then
  (
    while true; do
      sleep "$HEARTBEAT_SECONDS"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] heartbeat status=running"
    done
  ) &
  HEARTBEAT_PID=$!
  trap cleanup_heartbeat EXIT
fi

set +e
"$PYTHON_BIN" "${ARGS[@]}"
STATUS=$?
set -e

cleanup_heartbeat
trap - EXIT
echo "[$(date '+%Y-%m-%d %H:%M:%S')] run_end status=$STATUS"
exit "$STATUS"
