# Future Slicer Export Reintroduction

Date: 2026-06-18

## Summary

Slicer export is intentionally not part of the current distributed app path.
The active review/export route is:

```text
case output -> surface_preview/index.html -> smoothed STL files
```

However, Slicer remains a useful downstream editor/export environment. The
preferred reintroduction is a file-only handoff: the app writes files that
3D Slicer can import, opens the export folder in Finder, and lets the user open
3D Slicer manually.

Decision fixed on 2026-07-06:

```text
- Do not auto-launch Slicer.
- Do not search for a user's Slicer.app installation path.
- Do not make "run this Python script in Slicer" the primary user flow.
- Do generate a Slicer-readable export folder that supports drag-and-drop import.
```

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
case/slicer_export/
case/slicer/open_in_slicer.py
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
The product behavior should be "write Slicer import files", not "control
Slicer".

Suggested output layout:

```text
case/
  slicer_export/
    source.nii.gz                       optional copy, only when available
    segmentation.seg.nrrd               preferred if metadata export is implemented
    segmentation_labelmap.nii.gz        fallback labelmap export
    segmentation_ColorTable.ctbl        fallback label names/colors for Slicer
    label_names.json                    machine-readable sidecar
    label_colors.json                   machine-readable sidecar
    README_SLICER_IMPORT.md
```

Suggested command:

```bash
python -m totalsegmentator_wrapper_mac slicer-export --case <case_dir> [--source <source.nii.gz>]
```

Possible UI placement:

```text
Result screen -> 詳細/書き出し -> Slicerで開くファイルを書き出す
```

After successful export, the app may open `case/slicer_export/` in Finder. It
must not start Slicer itself.

## Intended Behavior

The export command should:

```text
1. Locate the case output root.
2. Prefer teeth_multilabel_fullspace.nii.gz over teeth_multilabel_roi.nii.gz.
3. If teeth fullspace is absent, build/use the craniofacial arch-jaw multilabel
   labelmap from raw TotalSegmentator masks.
4. Write a Slicer-readable segmentation export.
5. Include human-readable label names and conservative colors.
6. Copy the source NIfTI when the caller provides one or when case/input/source
   exists.
7. Write a short README with drag-and-drop import instructions, manual review
   instructions, and non-clinical scope.
```

Initial acceptable behavior:

```text
[ ] user can drag source.nii.gz and segmentation export files into Slicer
[ ] segmentation can be converted/opened as a Slicer Segmentation node
[ ] user can edit the segmentation in Segment Editor
[ ] user can manually export STL or labelmaps from Slicer if desired
```

Preferred later behavior:

```text
[ ] segmentation.seg.nrrd preserves segment names/colors without extra clicks
[ ] README includes screenshots or one-page import instructions
[ ] optional advanced open_in_slicer.py is included as a convenience only
```

## Non-Goals

Do not implement these as part of the primary flow:

```text
- Slicer.app path discovery
- Slicer auto-launch
- AppleScript / open -a Slicer automation
- requiring users to execute a Python script in Slicer
- Slicer extension development
- DICOM SEG export
```

An `open_in_slicer.py` helper may be added later for advanced users, but it must
be secondary. The product must remain fully usable through normal Slicer
drag-and-drop import.

## Safety Rules

- Keep offline HTML preview as the default route.
- Do not require Slicer for setup, preview creation, or distribution validation.
- Do not write into Slicer directories.
- Do not depend on Slicer Python for TotalSegmentator inference.
- Do not require users to run a generated Python script.
- Do not search for or hard-code Slicer install paths.
- Preserve input/output geometry; do not flip or reorient unless validated.
- Keep all output non-clinical and manually reviewed.

## Validation Plan

Unit/static:

```text
[ ] slicer-export command creates only case/slicer_export/* files
[ ] existing run command does not create case/slicer/
[ ] existing run command does not create case/slicer_export/
[ ] export code does not invoke Slicer, open -a, AppleScript, or Slicer.app lookup
[ ] teeth ROI output is skipped when fullspace output exists
[ ] README_SLICER_IMPORT.md contains drag-and-drop import instructions
[ ] fallback labelmap export includes label names/colors sidecars
```

Manual:

```text
[ ] open 3D Slicer manually
[ ] drag exported source and segmentation files into Slicer
[ ] source CT and segmentation align visually
[ ] no gross orientation flip or offset
[ ] label names are understandable
[ ] segmentation can be edited in Segment Editor
[ ] user can manually export STL if desired
```

## Historical Notes

The earlier prototype generated `open_in_slicer.py`, `label_names.json`, and
`label_colors.json` directly from the runner. That made Slicer appear as part of
the main output contract and pushed users toward a Python-script workflow.

The current product direction is cleaner if Slicer is an explicit optional
file export layered on top of the case folder. A script can exist as an advanced
convenience later, but the main user path should be drag-and-drop import into
Slicer.
