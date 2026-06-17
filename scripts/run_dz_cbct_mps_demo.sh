#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

INPUT="${1:-artifacts/samples/DZ-CBCT.nii.gz}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT="${2:-runs/dz_cbct_craniofacial_mps_$STAMP}"

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export TOTALSEG_HOME_DIR="${TOTALSEG_HOME_DIR:-$ROOT/artifacts/totalseg_home}"
export TOTALSEG_WEIGHTS_PATH="${TOTALSEG_WEIGHTS_PATH:-$ROOT/artifacts/totalseg_weights}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$ROOT/artifacts/matplotlib_cache}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$ROOT/artifacts/cache}"

"$ROOT/.venv/bin/python" -m totalsegmentator_wrapper_mac run \
  --input "$INPUT" \
  --output "$OUTPUT" \
  --task craniofacial_structures \
  --device mps \
  --totalseg-bin "$ROOT/.venv/bin/TotalSegmentator" \
  --no-copy-input

"$ROOT/.venv/bin/python" -m totalsegmentator_wrapper_mac summary \
  --case "$OUTPUT" \
  --output "$OUTPUT/CASE_SUMMARY.md"

echo "Output: $OUTPUT"
echo "Summary: $OUTPUT/CASE_SUMMARY.md"
echo "Surface preview: $OUTPUT/surface_preview/index.html"
