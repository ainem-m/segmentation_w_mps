# 17 CPU and ROI Benchmark Notes

Date: 2026-06-11

## Summary

Additional benchmark attempts were made after the full-resolution DZ-CBCT CPU
run was stopped at 30+ minutes. The useful result is that the MPS path is stable
on multiple DZ-CBCT-derived inputs, while CPU is effectively too slow to run for
the current preview path and also cannot run inside the Codex sandbox.

Use these results as implementation notes, not as clinical or accuracy claims.

## Jaw Crop Input

Created from `artifacts/samples/DZ-CBCT.nii.gz`:

```text
Output: artifacts/samples/DZ-CBCT_jawcrop.nii.gz
Shape: 460 x 440 x 320
Spacing: 0.25 x 0.25 x 0.25 mm
Voxel slices from DZ-CBCT.nii.gz:
  x: 100..560
  y: 80..520
  z: 40..360
```

MPS run:

```text
Output: artifacts/cli_smoke/dz_cbct_jawcrop_craniofacial_mps
Status: success
Return code: 0
Actual device: mps
Elapsed seconds: 123.79
```

Important TotalSegmentator timings:

```text
Rough crop resampling: 2.89 s
Rough crop prediction: 10.47 s
Main resampling: 4.69 s
Main prediction: 75.92 s
Saving segmentations: 17.41 s
```

Nonzero voxel counts:

```text
head.nii.gz           46,052,432
mandible.nii.gz        4,099,224
sinus_frontal.nii.gz           0
sinus_maxillary.nii.gz 1,551,696
skull.nii.gz           2,588,144
teeth_lower.nii.gz       657,728
teeth_upper.nii.gz       723,376
```

## Too-Tight Teeth ROI

A smaller ROI was derived from `teeth_lower` and `teeth_upper` masks:

```text
Output: artifacts/samples/DZ-CBCT_teeth_roi.nii.gz
Shape: 334 x 274 x 216
Spacing: 0.25 x 0.25 x 0.25 mm
Voxel slices from DZ-CBCT_jawcrop.nii.gz:
  x: 70..404
  y: 6..280
  z: 48..264
```

MPS returned exit code 0, but TotalSegmentator reported:

```text
INFO: Crop is empty. Returning empty segmentation.
Elapsed seconds: 22.86
```

Decision:

```text
This ROI is too tight for the TotalSegmentator craniofacial crop helper.
Do not use tight tooth-only crops for the benchmark gate.
```

## 0.5 mm Downsampled Jaw Crop

Created by SimpleITK linear resampling from `DZ-CBCT_jawcrop.nii.gz`:

```text
Output: artifacts/samples/DZ-CBCT_jawcrop_0p5mm.nii.gz
Original shape: 460 x 440 x 320
Original spacing: 0.25 x 0.25 x 0.25 mm
New shape: 230 x 220 x 160
New spacing: 0.5 x 0.5 x 0.5 mm
```

MPS run:

```text
Output: artifacts/cli_smoke/dz_cbct_jawcrop_0p5mm_craniofacial_mps
Status: success
Return code: 0
Actual device: mps
Elapsed seconds: 130.48
```

Important TotalSegmentator timings:

```text
Rough crop resampling: 0.44 s
Rough crop prediction: 13.78 s
Main resampling: 2.57 s
Main prediction: 87.17 s
Saving segmentations: 17.72 s
```

Nonzero voxel counts:

```text
head.nii.gz            5,759,424
mandible.nii.gz          515,030
sinus_frontal.nii.gz           0
sinus_maxillary.nii.gz   192,440
skull.nii.gz             320,624
teeth_lower.nii.gz        82,779
teeth_upper.nii.gz        90,958
```

Observation:

```text
Downsampling reduced input size but did not make MPS faster in this run.
TotalSegmentator still ran the same 12 main inference chunks, and the measured
wall time was slightly higher than the 0.25 mm jaw crop run.
```

## CPU Attempts

Full-resolution DZ-CBCT:

```text
Input: artifacts/samples/DZ-CBCT.nii.gz
Command: totalsegmentator_wrapper_mac benchmark
Outcome: CPU leg stopped after 30+ minutes with no completed output masks.
Interpretation: effectively not runnable for the current preview path.
```

0.25 mm jaw crop:

```text
Input: artifacts/samples/DZ-CBCT_jawcrop.nii.gz
Outcome: CPU leg entered 12-chunk main inference but remained at 0/12 long
enough to make the crop unsuitable as a quick benchmark. Stopped manually.
Interpretation: still too slow for interactive preview validation.
```

0.5 mm jaw crop inside Codex sandbox:

```text
Input: artifacts/samples/DZ-CBCT_jawcrop_0p5mm.nii.gz
Outcome: failed before inference.
Failure: multiprocessing.Manager() could not bind a local socket.
Error: PermissionError: [Errno 1] Operation not permitted
```

0.5 mm jaw crop outside Codex sandbox:

```text
Input: artifacts/samples/DZ-CBCT_jawcrop_0p5mm.nii.gz
Outcome: rough crop completed, main inference entered 0/12, then the run was
stopped after roughly 10 minutes with no completed output masks.
Interpretation: downsampling did not make CPU practical enough for the current
preview gate.
```

## Operational Findings

```text
- Real TotalSegmentator runs should be executed outside the Codex sandbox.
- MPS is unavailable inside the sandbox.
- CPU TotalSegmentator can also fail inside the sandbox because nnUNet uses
  multiprocessing.Manager(), which needs local socket binding.
- When running outside the sandbox, set MPLCONFIGDIR and XDG_CACHE_HOME to
  workspace artifact directories if matplotlib/fontconfig cache warnings appear.
- Tight tooth-only crops are not valid smoke inputs for craniofacial_structures
  because the crop helper can return an empty crop.
- CPU timing is deferred until there is time for an unattended long run.
```

## Decision

For the current preview gate:

```text
[x] Treat full-resolution DZ-CBCT MPS as the primary performance/demo result.
[x] Treat DZ-CBCT jaw crop MPS as an additional successful runner smoke.
[x] Treat CPU as effectively not runnable for the current preview path.
[x] Defer exact CPU timing until there is time for an unattended long run, a
    smaller representative public sample, or a controlled low-resolution
    benchmark input.
[x] Do not block backend runner/UI work on CPU completion.
```
