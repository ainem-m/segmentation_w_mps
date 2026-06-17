# 08 Slicer Handoff Spec

## Goal

Make it easy to open the inference result in 3D Slicer for review, correction, and STL export.

Slicer is the editor. TotalSegmentator Wrapper for Mac is the inference launcher.

## Output layout

```text
case_output/
  input/
    source.nii.gz
  segmentations/
    raw_totalseg/
      *.nii.gz
    merged/
      optional_merged_labelmap.nii.gz
  slicer/
    open_in_slicer.py
    label_names.json
    label_colors.json
  logs/
    benchmark.json
    environment.json
    run.log
  README_OUTPUT.md
```

## open_in_slicer.py behavior

The generated script should:

```text
1. Determine its own directory.
2. Locate the case output root.
3. Load source.nii.gz as a volume.
4. Load segmentation outputs.
5. Set display properties if possible.
6. Open Segment Editor if segmentation node exists.
7. Print clear instructions to Slicer Python console.
```

## Acceptable v0.1 behavior

The script may load labelmaps rather than perfect Slicer Segmentation nodes if conversion is brittle.

Minimum acceptable behavior:

```text
[ ] source volume visible
[ ] segmentation labelmap visible
[ ] user can manually convert/edit/export in Slicer
```

Preferred behavior:

```text
[ ] segmentation converted to Slicer Segmentation node
[ ] segment names set
[ ] segment colors set
[ ] Segment Editor opened
```

## Label naming

Use human-readable English first. Japanese labels can be added later.

Examples:

```text
mandible
upper_teeth
lower_teeth
skull_or_maxilla
maxillary_sinus
pharynx
inferior_alveolar_canal_left
inferior_alveolar_canal_right
```

Do not claim a label exists unless it is actually produced by the selected task.

## Coordinate handling

Do not manually flip or reorient unless explicitly verified. Preserve TotalSegmentator output geometry.

Validation in Slicer:

```text
- Load source volume and segmentation.
- Toggle overlay.
- Verify segmentation aligns with anatomy.
- Reject if grossly flipped or offset.
```

## STL export

MVP does not require fully automatic STL export. It may include an optional export helper.

If implemented:

```text
- export only visible/selected segments
- save under case_output/stl/
- record export in log
```

## Script generation style

The script should be self-contained and robust to spaces in paths.

Use absolute paths in generated script to avoid working directory issues.

## User-facing output README

Generate `README_OUTPUT.md` with:

```text
- What was run
- Where outputs are
- How to open in Slicer
- Non-clinical notice
- Known limitations
- Device used
- Runtime
```
