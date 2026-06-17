#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <input.nii.gz> <output_dir> [task]" >&2
  exit 2
fi

INPUT="$1"
OUT="$2"
TASK="${3:-craniofacial_structures}"

mkdir -p "$OUT"

echo "[TotalSegmentator Wrapper] Running TotalSegmentator smoke test"
echo "input=$INPUT"
echo "output=$OUT"
echo "task=$TASK"
echo "device=mps"

python - <<'PY'
import torch
print("torch", torch.__version__)
print("mps built", torch.backends.mps.is_built())
print("mps available", torch.backends.mps.is_available())
PY

TotalSegmentator -i "$INPUT" -o "$OUT" -ta "$TASK" --device mps
