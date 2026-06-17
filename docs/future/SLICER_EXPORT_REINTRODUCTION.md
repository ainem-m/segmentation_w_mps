# Future Slicer Export Reintroduction

Date: 2026-06-18

## Summary

Slicer export is intentionally not part of the current distributed app path.
The active review/export route is:

```text
case output -> surface_preview/index.html -> smoothed STL files
```

However, Slicer remains a useful downstream editor/export environment. Future
versions should be able to add an optional Slicer handoff without disturbing the
current offline HTML preview path.

## Current State

Removed from the active code path:

```text
src/totalsegmentator_wrapper_mac/slicer_export.py
templates/open_in_slicer.py
scripts/check_slicer_dicom_import.py
docs/08_SLICER_HANDOFF_SPEC.md
docs/18_SLICER_HANDOFF_GENERATOR_NOTES.md
```

Current run output intentionally does not create:

```text
case/slicer/open_in_slicer.py
case/slicer/label_names.json
case/slicer/label_colors.json
```

Current run output still creates the data needed to reintroduce Slicer export:

```text
case/input/source.nii.gz              optional, if input copying is enabled
case/segmentations/**/*.nii.gz
case/logs/benchmark.json
case/logs/mask_stats.json
case/README_OUTPUT.md
case/surface_preview/index.html       after surface-preview generation
```

## Reintroduction Shape

Add Slicer handoff as an optional export layer, not as the default result path.

Suggested output layout:

```text
case/
  slicer/
    open_in_slicer.py
    label_names.json
    label_colors.json
```

Suggested command:

```bash
python -m totalsegmentator_wrapper_mac slicer-export --case <case_dir>
```

Possible UI placement:

```text
Result screen -> 詳細/書き出し -> Slicer用スクリプトを作成
```

Do not auto-launch Slicer in v1. Generate files and let the user open them
manually.

## Intended Behavior

The generated script should:

```text
1. Locate the case output root.
2. Load the source NIfTI volume when available.
3. Load generated segmentation NIfTI files.
4. Prefer teeth_multilabel_fullspace.nii.gz over teeth_multilabel_roi.nii.gz.
5. Set human-readable node names.
6. Set conservative display colors when possible.
7. Print clear non-clinical and manual-review instructions.
```

Initial acceptable behavior:

```text
[ ] source volume visible when available
[ ] segmentation labelmaps visible
[ ] user can manually convert/edit/export in Slicer
```

Preferred later behavior:

```text
[ ] labelmaps converted to Slicer Segmentation nodes
[ ] segment names/colors are preserved
[ ] Segment Editor opens with the generated segmentation
[ ] optional STL export helper writes under case/stl/ or case/surface_preview/
```

## Safety Rules

- Keep offline HTML preview as the default route.
- Do not require Slicer for setup, preview creation, or distribution validation.
- Do not write into Slicer directories.
- Do not depend on Slicer Python for TotalSegmentator inference.
- Generated Slicer scripts must be self-contained and robust to spaces in paths.
- Preserve input/output geometry; do not flip or reorient unless validated.
- Keep all output non-clinical and manually reviewed.

## Validation Plan

Unit/static:

```text
[ ] slicer-export command creates only case/slicer/* files
[ ] existing run command does not create case/slicer/
[ ] script contains no system-specific absolute dependency except case paths
[ ] teeth ROI output is skipped when fullspace output exists
```

Manual:

```text
[ ] open generated script in 3D Slicer
[ ] source CT and masks align visually
[ ] no gross orientation flip or offset
[ ] label names are understandable
[ ] user can manually export STL if desired
```

## Historical Notes

The earlier prototype generated `open_in_slicer.py`, `label_names.json`, and
`label_colors.json` directly from the runner. That made Slicer appear as part of
the main output contract. The current product direction is cleaner if Slicer is
an explicit optional export command layered on top of the case folder.
