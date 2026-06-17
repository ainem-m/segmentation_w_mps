# Three-Case Demo Readiness

Date: 2026-06-12

## Summary

Experimental `teeth` MPS is now demo-ready as a precomputed, non-clinical
preview path. Three representative cases completed on the target Mac with:

```text
actual_device: mps
fallback_reason: null
torch.mps_fallback_env: null
mps_gate.convtranspose3d_fp32: passed
full-space teeth labelmap: exists
offline surface_preview/index.html: exists
```

This is a workflow and visual-inspection proof, not a quantitative accuracy
claim and not a clinical claim.

## Three-Case Result Table

| Case | Input | Source shape / spacing | Teeth elapsed | Labels | ROI | Surface preview |
|---|---|---:|---:|---:|---:|---|
| DZ-CBCT jawcrop 0.5 mm | `DZ-CBCT_jawcrop_0p5mm.nii.gz` | `230 x 220 x 160`, `0.5 mm` | `98.48 s` | `56` | `190 x 152 x 132`, ratio `0.471` | yes, step `2` |
| Case02 native CBCT | `case02_cbct_Eq_1.nii.gz` | `512 x 512 x 395`, `~0.203 mm` | `112.12 s` | `54` | `395 x 321 x 308`, ratio `0.377` | yes, step `2` |
| STS24 open data 0026 | `STS24_Train_Unlabeled_0026.nii.gz` | `640 x 640 x 400`, `0.25 mm` | `85.40 s` | `55` | `302 x 265 x 234`, ratio `0.114` | yes, step `4` |

## Artifact Map

```text
artifacts/cli_smoke/teeth_smoke_15min_margin10_mps/
artifacts/cli_smoke/case02_teeth_native_mps_margin5/
artifacts/cli_smoke/sts24_0026_teeth_mps_margin5/
```

Each case contains:

```text
logs/benchmark.json
logs/teeth_child_benchmark.json
logs/teeth_roi.json
segmentations/teeth_experimental/teeth_multilabel_fullspace.nii.gz
surface_preview/index.html
surface_preview/preview_summary.json
surface_preview/combined/*.stl
surface_preview/labels/*.stl
```

## Demo Positioning

Use the offline HTML surface preview as the primary visual demo path. The live
demo should not rerun long preflight steps unless there is a specific reason to
show elapsed-time behavior.

Recommended short demo sequence:

```text
1. Show MPS proof with `python -m totalsegmentator_wrapper_mac doctor`.
2. Show one existing case summary / benchmark JSON.
3. Open a precomputed `surface_preview/index.html`.
4. Rotate, pan, toggle jaws / dental hard tissue / pulp.
5. Point to full-space NIfTI and STL outputs as export artifacts.
```

Public phrasing:

```text
Apple Silicon/MPS can complete local dental-arch segmentation previews across
multiple CBCT cases and export inspectable NIfTI/STL/HTML artifacts.
```

Avoid:

```text
- clinical accuracy claims
- diagnosis or treatment-planning claims
- commercial-tool comparisons
- CUDA comparisons
- DICOM normalizer claims
```

## Caveats

- STS24 0026 is open data and unlabeled, so it supports workflow proof and
  visual QA, not quantitative accuracy evaluation.
- CPU timing remains deferred; CPU is treated as effectively too slow for the
  current interactive preview path.
- Slicer remains optional for later review. Current visual QA is centered on
  the Slicer-free offline HTML preview.
- `teeth` remains opt-in experimental behavior. Default `task=teeth` without
  `--experimental-teeth` should continue to fast-fail.
