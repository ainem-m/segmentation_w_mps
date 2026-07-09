# 22 Surface Preview Notes

Date: 2026-06-11

## Summary

`teeth_multilabel_fullspace.nii.gz` can now be exported to smoothed STL files
and a fully offline HTML surface viewer without launching 3D Slicer. Standard
`craniofacial_structures` outputs can also be previewed: when the individual
teeth fullspace labelmap is absent, `surface-preview` derives a compact
arch/jaw labelmap from `raw_totalseg` masks.

This is a visual inspection/export layer only. It does not alter the source
NIfTI, segmentation outputs, benchmark JSON, or experimental teeth runner.

## Command

```bash
PYTHONPATH=src .venv/bin/python -m totalsegmentator_wrapper_mac surface-preview \
  --case artifacts/cli_smoke/teeth_smoke_15min_margin10_mps
```

Defaults:

```text
input:
<case>/segmentations/teeth_experimental/teeth_multilabel_fullspace.nii.gz

fallback input for standard arch/jaw preview:
<case>/segmentations/raw_totalseg/{teeth_upper,teeth_lower,mandible,skull}.nii.gz
-> <case>/segmentations/derived/craniofacial_arch_jaw_multilabel.nii.gz

output:
<case>/surface_preview/
```

The fallback labelmap is for visual preview only. It preserves the source affine
and records source masks in `preview_summary.json`; it does not replace the
original TotalSegmentator binary masks.

## Outputs

```text
surface_preview/
  index.html
  preview_summary.json
  README_SURFACE_PREVIEW.md
  combined/
    dental_hard_tissue_smooth.stl
    jaws_smooth.stl
    pulp_smooth.stl
    all_nonzero_smooth.stl
  labels/
    label_011_upper_right_central_incisor_fdi11_smooth.stl
    ...
```

STL does not carry label or color metadata. Label identity is preserved in
per-label filenames and in `preview_summary.json`.

## Smoothing

Default preset:

```text
preset: slicer_like
iterations: 10
lambda: 0.5
mu: -0.53
```

Smoothing is mesh-level Taubin smoothing after marching cubes. It is not voxel
or labelmap smoothing.

Thin structures use reduced smoothing:

```text
pulp labels: 3 iterations
canal labels: 3 iterations
labels with <500 voxels: 3 iterations
combined/pulp_smooth.stl: 3 iterations
```

CLI overrides:

```text
--smooth-preset none|slicer_like|medium|strong
--smooth-iterations <int>
--smooth-lambda <float>
--smooth-mu <float>
```

The legacy raw STL script remains available and defaults to no smoothing:

```bash
PYTHONPATH=src scripts/export_labelmap_to_stl.py \
  --input path/to/teeth_multilabel_fullspace.nii.gz \
  --output-dir path/to/stl \
  --combined
```

## Offline HTML Viewer

`surface_preview/index.html` is self-contained:

```text
no http:// references
no https:// references
no CDN
no external script src
```

It embeds lower-resolution preview meshes generated with marching-cubes
`step_size=2`. Full-quality smoothed STL files remain the export artifacts.

High-resolution native CBCT cases can make the embedded HTML meshes large. For
preview-only browser weight reduction, keep the full-quality STL export as-is
and increase only the embedded preview mesh step size:

```bash
PYTHONPATH=src .venv/bin/python -m totalsegmentator_wrapper_mac surface-preview \
  --case artifacts/cli_smoke/case02_teeth_native_mps_margin5 \
  --preview-step-size 3
```

Use `--preview-step-size 3` or `4` first on ~0.2 mm CBCT. The default remains
`2` for compatibility with the first validated sample.

Values above `4` are allowed but are treated as coarse inspection previews.
`preview_summary.json` records:

```text
preview.warning: small structures may be under-sampled
```

when `--preview-step-size` is greater than `4`.

The viewer uses dependency-free inline WebGL when available, with a Canvas 2D
fallback. Shading is computed from mesh normals in the browser. The default
material preset is `rich`, which keeps the preview lightweight while adding
wrapped diffuse light, two-layer specular highlights, rim light, warm material
tint, and small emission/subsurface-style lifts for thin structures. Additional
presets are available for standard, realistic, neutral, and high-contrast
preview styling.

The viewer can also host alternate geometry variants for preview experiments.
When a payload provides `original` and `sdf` variants, the UI exposes an
`元の形状` / `なめらか補完` switch and changes the displayed mesh buffers
immediately without rerunning the SDF computation. Standard generated previews
do not show this control because they only contain one geometry.

Default visible layers:

```text
dental_hard_tissue: visible, opacity 1.0
jaws: visible, translucent, opacity 0.35
pulp: hidden, opacity 1.0
all_nonzero: hidden, opacity 1.0
```

Only the jaws preview layer is translucent by default. Dental hard tissue,
pulp, and all-nonzero preview layers are rendered solid so WebGL can write
their depth and avoid front/back ordering artifacts during rotation.

WebGL renders the translucent jaws with a jaw-only depth pre-pass:

```text
opaque meshes -> jaw depth-only pre-pass -> jaw translucent front-shell pass
```

This is not full order-independent transparency. It intentionally treats the
jaw as a stable translucent front shell for inspection, which avoids
mesh-internal triangle ordering artifacts while preserving the offline
dependency-free viewer.

Controls:

```text
default mode: Trackpad
Trackpad two-finger scroll: object-grab arcball rotation
Trackpad Command-scroll: pan
Trackpad pinch: zoom
Trackpad secondary/right drag: pan
Mouse mode wheel: zoom
Mouse mode primary/secondary drag: arcball rotation
Mouse mode middle drag: pan
Front/Back/Left/Right/Top/Bottom buttons: yaw/pitch axis views
Fit all button: fit visible meshes, zoom 0.45
Reset button: CameraState default, zoom 0.05, no fit
layer checkboxes
label count and triangle summary
```

Browser pinch events are handled as a Web approximation via `ctrlKey` wheel
events. Command-scroll deltas are accumulated per animation frame before pan is
applied. For normal Trackpad scroll rotation, browser `WheelEvent` deltas are
inverted before the arcball update so the object follows the physical
two-finger motion. Browser rotation uses a substepped object-grab arcball:
large pointer or wheel deltas are split into small updates before each rotation
is applied, and the orientation matrix is re-orthonormalized after each step.
This keeps long rotations continuous around 180 degrees without changing the
depth or transparency pipeline.

WebGL depth is mapped from the current visible mesh view-Z range on every draw,
with 8% range padding and final depth constrained to approximately `[0.02, 0.98]`.

## Real Case Validation

Case:

```text
artifacts/cli_smoke/teeth_smoke_15min_margin10_mps
```

Verification result:

```text
surface_preview/index.html exists
surface_preview/preview_summary.json exists
label_count: 56
label STL count: 56
external HTML references found: false
viewer.renderer: webgl
viewer.fallback_renderer: canvas2d
viewer.camera_mode_default: trackpad
```

Combined STL outputs:

```text
all_nonzero_smooth.stl: 19 MB, 390,204 triangles
dental_hard_tissue_smooth.stl: 8.2 MB, 171,668 triangles
jaws_smooth.stl: 18 MB, 383,004 triangles
pulp_smooth.stl: 783 KB, 16,032 triangles
```

Preview mesh summary embedded in HTML:

```text
jaws: 46,543 vertices, 94,634 triangles, visible
dental_hard_tissue: 20,912 vertices, 41,952 triangles, visible
pulp: 1,694 vertices, 3,288 triangles, hidden
all_nonzero: 46,907 vertices, 94,354 triangles, hidden
```

## Tests

```text
.venv/bin/python -m compileall src tests scripts
passed

env PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
25 tests passed
```

Unit tests cover:

```text
binary STL export is non-empty
Taubin smoothing keeps face count stable and vertices finite
slicer_like smoothing moves vertices while keeping bounds stable
small pulp/canal smoothing uses reduced iterations
surface-preview writes index.html, preview_summary.json, and combined STLs
offline HTML contains no external references
```

## Not Yet Validated

- In-app Browser validation used a temporary localhost server because local
  `file://` URLs are blocked by Browser Use policy in this Codex session.
  The HTML loaded, rendered the WebGL scene, and reported no console errors.
- macOS Quick Look/Preview STL display has not been opened from this session.
- This is not a diagnostic or treatment-planning viewer.
