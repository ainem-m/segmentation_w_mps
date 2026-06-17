# Template: generated open_in_slicer.py
# This script is intended to be run inside 3D Slicer:
#   Slicer --python-script open_in_slicer.py

import json
from pathlib import Path

import slicer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "input" / "source.nii.gz"
SEG_DIR = ROOT / "segmentations"
LABEL_NAMES = ROOT / "slicer" / "label_names.json"
LABEL_COLORS = ROOT / "slicer" / "label_colors.json"

print(f"[TotalSegmentator Wrapper] Opening case: {ROOT}")

if SOURCE.exists():
    loaded, volume_node = slicer.util.loadVolume(str(SOURCE), returnNode=True)
    print(f"[TotalSegmentator Wrapper] source volume loaded: {loaded}")
else:
    volume_node = None
    print(f"[TotalSegmentator Wrapper] source volume not found: {SOURCE}")

label_names = {}
if LABEL_NAMES.exists():
    label_names = json.loads(LABEL_NAMES.read_text(encoding="utf-8"))

label_colors = {}
if LABEL_COLORS.exists():
    label_colors = json.loads(LABEL_COLORS.read_text(encoding="utf-8"))

# v0.1 conservative behavior: load every .nii.gz under segmentations as labelmap.
loaded_labelmaps = []
for path in sorted(SEG_DIR.rglob("*.nii.gz")):
    if path.name == "source.nii.gz":
        continue
    loaded, node = slicer.util.loadLabelVolume(str(path), returnNode=True)
    if loaded:
        name = label_names.get(path.stem, path.stem)
        node.SetName(name)
        loaded_labelmaps.append(node)
        print(f"[TotalSegmentator Wrapper] loaded labelmap: {path}")

print("[TotalSegmentator Wrapper] Loaded labelmaps:", len(loaded_labelmaps))
print("[TotalSegmentator Wrapper] If needed, use Segmentations module or Segment Editor to convert/edit/export.")

# Try to switch to Segment Editor for user convenience.
try:
    slicer.util.selectModule("SegmentEditor")
except Exception as exc:  # noqa
    print("[TotalSegmentator Wrapper] Could not switch to SegmentEditor:", exc)
