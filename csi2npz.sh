#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_ROOT="$ROOT/CSI_data"
DB_ROOT="$DATA_ROOT/db"
ARTIFACTS_ROOT="$DATA_ROOT/artifacts"
INTERMEDIATES_ROOT="$DATA_ROOT/intermediates"
PROCESSING_ROOT="$DATA_ROOT/processing code"
DEFAULT_PYTHON="/home/tonic/miniconda3/envs/ax210test/bin/python"
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON}"

TOLERANCE_US=750
MISSING_POLICY="interpolate"
SUBCARRIERS=2025
FS=100
DURATION_SEC="auto"
PROCESS_ALL=0
SESSIONS=()

usage() {
    cat <<'EOF'
Usage:
  ./csi2npz.sh SESSION [SESSION ...]
  ./csi2npz.sh --all

Options:
  --tolerance-us VALUE        Matching tolerance in microseconds (default: 750)
  --missing-policy POLICY     interpolate, nan, or zero (default: interpolate)
  --subcarriers COUNT         Output subcarriers (default: 2025, PicoSense-compatible)
  --fs HZ                     Output time sampling rate (default: 100)
  --duration-sec SEC|auto     Fixed output duration; auto rounds capture length (default: auto)
  --all                       Process every session under CSI_data/db
  -h, --help                  Show this help

CSI processing:
  Removes HE-SU cyclic shift diversity phase like PicoScenes.
  Resamples the nominal 160 MHz bandwidth to PicoSense-compatible 2025 points by default.

Examples:
  ./csi2npz.sh 20260613-120000_test
  ./csi2npz.sh 20260613-120000_test --tolerance-us 300
  ./csi2npz.sh --all
EOF
}

while (($#)); do
    case "$1" in
        --tolerance-us)
            [[ $# -ge 2 ]] || { echo "Missing value for --tolerance-us"; exit 2; }
            TOLERANCE_US="$2"
            shift 2
            ;;
        --missing-policy)
            [[ $# -ge 2 ]] || { echo "Missing value for --missing-policy"; exit 2; }
            MISSING_POLICY="$2"
            shift 2
            ;;
        --subcarriers)
            [[ $# -ge 2 ]] || { echo "Missing value for --subcarriers"; exit 2; }
            SUBCARRIERS="$2"
            shift 2
            ;;
        --fs)
            [[ $# -ge 2 ]] || { echo "Missing value for --fs"; exit 2; }
            FS="$2"
            shift 2
            ;;
        --duration-sec)
            [[ $# -ge 2 ]] || { echo "Missing value for --duration-sec"; exit 2; }
            DURATION_SEC="$2"
            shift 2
            ;;
        --all)
            PROCESS_ALL=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            usage
            exit 2
            ;;
        *)
            SESSIONS+=("$1")
            shift
            ;;
    esac
done

case "$MISSING_POLICY" in
    interpolate|nan|zero) ;;
    *)
        echo "Invalid --missing-policy: $MISSING_POLICY"
        exit 2
        ;;
esac

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python environment not found: $PYTHON_BIN"
    echo "Set PYTHON_BIN to a Python with numpy installed."
    exit 1
fi

if ((PROCESS_ALL)); then
    mapfile -t SESSIONS < <(
        find "$DB_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort
    )
fi

if ((${#SESSIONS[@]} == 0)); then
    echo "No session specified."
    usage
    exit 2
fi

for session in "${SESSIONS[@]}"; do
    session_db="$DB_ROOT/$session"
    session_artifacts="$ARTIFACTS_ROOT/$session"

    if [[ ! -d "$session_db" ]]; then
        echo "Session DB not found: $session_db"
        exit 1
    fi
    if [[ ! -d "$session_artifacts/arrays" ]]; then
        echo "Session arrays not found: $session_artifacts/arrays"
        exit 1
    fi
    for topic in 1 2 3 4; do
        if [[ ! -d "$session_db/csi.rx.$topic" ]]; then
            echo "Missing CSV directory: $session_db/csi.rx.$topic"
            exit 1
        fi
        if [[ ! -d "$session_artifacts/arrays/csi.rx.$topic" ]]; then
            echo "Missing array directory: $session_artifacts/arrays/csi.rx.$topic"
            exit 1
        fi
    done

    echo "Processing: $session"
    "$PYTHON_BIN" "$PROCESSING_ROOT/csi_matcher.py" \
        --db-root "$DB_ROOT" \
        --intermediates-root "$INTERMEDIATES_ROOT" \
        --exp-names "$session" \
        --tolerance-us "$TOLERANCE_US"

    "$PYTHON_BIN" "$PROCESSING_ROOT/csi_merger.py" \
        --artifacts-root "$ARTIFACTS_ROOT" \
        --intermediates-root "$INTERMEDIATES_ROOT" \
        --exp-names "$session" \
        --dataset-type rt \
        --subcarriers "$SUBCARRIERS" \
        --missing-policy "$MISSING_POLICY" \
        --fs "$FS" \
        --duration-sec "$DURATION_SEC"

    merged_npz="$INTERMEDIATES_ROOT/$session/merged_csi/${session}_merged.npz"
    if [[ ! -f "$merged_npz" ]]; then
        echo "Merged NPZ not found: $merged_npz"
        exit 1
    fi

    echo "Merged:  $merged_npz"
done
