# ToothSeg Backend Notes

Date: 2026-07-13

## Scope

ToothSeg is an explicit experimental backend for non-clinical research and
education preview. It produces an individual-tooth labelmap using FDI tooth
numbers. It is not a fallback for TotalSegmentator or DentalSegmentator.

## Upstream and licenses

- Source: `https://github.com/MIC-DKFZ/ToothSeg`
- Integration reference: `b29d1017fa124f89645fa56f98649e3f3f43bdb0`
- Source license: Apache-2.0
- Model record: `10.5281/zenodo.14893540`
- Model file: `ToothSeg.zip` (about 920 MB)
- Model MD5: `5d8dd061cce9529943567aeba3271143`
- Model license: CC BY 4.0

The app does not bundle the model. Selecting ToothSeg for the first time opens
an explicit preparation sheet. The downloader supports HTTP range resume,
verifies the published MD5, safely extracts only runtime files, and removes the
archive after installation. This keeps the installed checkpoint set near 500
MB instead of retaining both checkpoints plus the archive.

The tooth-pair distribution file used by the published self-correction step is
downloaded from the pinned source commit and verified with SHA-256
`82ab04892277d36013be5ba9763ac334ea073fca7ebe8679086f1e33ed64ff29`.

During the first download, the preparation sheet shows downloaded/total size,
percentage, current transfer speed, and estimated remaining time. A resumed
download includes the existing partial size and is marked as resumed. During
inference, the app shows the active semantic or instance branch, sliding-window
count, percentage, and nnU-Net's estimated remaining time.

Installed data lives under:

```text
~/Library/Application Support/TotalSegmentatorWrapperMac/models/toothseg/
```

## Inference path

1. Run or reuse the TotalSegmentator craniofacial preflight.
2. For the explicit post-TotalSegmentator refine path, build a dental ROI from
   `teeth_upper` and `teeth_lower` with a fixed 12 mm margin. The standalone
   experimental ToothSeg choice keeps its separate 5 mm default.
3. Use the cropped NIfTI for the semantic branch.
4. Resample a separate instance-branch ROI input to 0.2 mm isotropic.
5. Run Dataset 121 fold 5, saving semantic probabilities.
6. Run Dataset 123 fold 5 at the published border/core instance configuration.
7. Convert border/core output to tooth instances and restore it to the ROI grid.
8. Apply the published minimum-cost tooth-label assignment.
9. Re-embed the FDI result into the full source shape and affine.
10. Store final labels as literal FDI values (11-18, 21-28, 31-38, 41-48).

The normalized result is:

```text
segmentations/toothseg/toothseg_fdi_multilabel.nii.gz
segmentations/toothseg/toothseg_fdi_multilabel.nii.gz.labels.json
```

Both nnU-Net branches are invoked with `-device mps`. The runner removes
`PYTORCH_ENABLE_MPS_FALLBACK`; CPU or another segmentation model is never used
as a silent fallback. Test-time augmentation is disabled for this Mac preview
path to control runtime and memory use. On MPS, the semantic inference tile is
adapted from `256 x 256 x 256` to `192 x 192 x 192`; the network and published
weights are unchanged. Already verified checkpoints are migrated in place by
patching `plans.json`, so this memory-safety update does not download the 920 MB
archive again. `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0` is not used.

## CLI

```bash
python -m totalsegmentator_wrapper_mac toothseg-status \
  --model-root "$HOME/Library/Application Support/TotalSegmentatorWrapperMac/models/toothseg" \
  --json /tmp/toothseg-status.json

python -m totalsegmentator_wrapper_mac toothseg-prepare \
  --model-root "$HOME/Library/Application Support/TotalSegmentatorWrapperMac/models/toothseg" \
  --json /tmp/toothseg-prepare.json \
  --progress-log /tmp/toothseg-prepare.log

python -m totalsegmentator_wrapper_mac run \
  --backend toothseg \
  --task teeth \
  --input input.nii.gz \
  --output case \
  --device mps \
  --toothseg-nnunet-results "$HOME/Library/Application Support/TotalSegmentatorWrapperMac/models/toothseg/nnUNet_results"

# Explicit second-stage refine reusing a completed TotalSegmentator case.
python -m totalsegmentator_wrapper_mac run \
  --backend toothseg \
  --task teeth \
  --input input.nii.gz \
  --output case \
  --device mps \
  --toothseg-refine \
  --teeth-craniofacial-case case \
  --teeth-crop-margin-mm 12 \
  --toothseg-nnunet-results "$HOME/Library/Application Support/TotalSegmentatorWrapperMac/models/toothseg/nnUNet_results"
```

## Limits

- The 0.2 mm instance input can be large. A 160 mm field of view becomes 800
  voxels per axis before nnU-Net sliding-window inference. Automatic ROI
  cropping limits this growth, but wide or oblique dental ROIs can still take
  substantial time.
- Metal artifacts, touching teeth, unusual anatomy, retained roots, and missing
  teeth can still cause merged, missing, or incorrectly numbered output.
- This is a visualization/research preview and requires manual review.

## 2026-07-13 initial cropped MPS smoke

The strict app-profile path was exercised with the published checkpoints on a
real CBCT crop from the bundled sample. The crop was `130 x 115 x 110` at 0.5
mm spacing; the instance input became approximately `325 x 288 x 275` at 0.2
mm spacing. The semantic branch completed its MPS prediction in 67 seconds and
the instance branch processed 27 sliding windows in 9 minutes 6 seconds. The
complete backend, including export and post-processing, finished in 707.1
seconds with `actual_device=mps`, `mps_state=validated`, and no fallback.

The output retained the source shape and affine, contained 17 non-empty FDI
labels in this deliberately cropped field of view, and generated 17 individual
STLs plus the HTML surface preview. This is an integration smoke result, not a
clinical accuracy claim or a full-FOV runtime benchmark.

## 2026-07-13 full sample MPS OOM regression

The unmodified `256 x 256 x 256` semantic tile failed on the `230 x 220 x 160`
sample at 0.5 mm spacing during its first semantic window. MPS had allocated
15.54 GiB, other allocations used 4.00 GiB, and the next 2.00 GiB allocation
exceeded the 20.13 GiB limit.

With the automatic 5 mm dental ROI and `192 x 192 x 192` semantic tile, the same
input completed under the strict app profile. The ROI was `170 x 141 x 112`
(33.16% of the source voxel volume). Semantic inference took 216.8 seconds;
instance inference processed 48 windows in 1,414.4 seconds; the complete backend
took 1,648.8 seconds. The result reported `actual_device=mps`,
`mps_state=validated`, and no fallback.

The final labelmap was re-embedded at `230 x 220 x 160` with the source affine,
contained 136,826 non-zero voxels across 28 FDI labels, and produced 28
individual STLs plus the HTML surface preview. This verifies the OOM regression
and output geometry, not clinical segmentation accuracy.

## 2026-07-22 explicit 12 mm refine MPS E2E

The bundled `230 x 220 x 160` sample at 0.5 mm spacing was run through the
explicit second-stage path using an already completed TotalSegmentator case.
The runner reused that case's non-empty `teeth_upper` and `teeth_lower` masks;
it did not launch another TotalSegmentator preflight. The fixed 12 mm margin
produced a `198 x 156 x 140` ROI (53.41% of the source voxel volume). The
near-whole-volume safety check now validates the unexpanded teeth-mask bounding
box, so a legitimate large requested margin is not mistaken for a corrupt mask.

The semantic branch processed 12 windows and took 442.9 seconds. The 0.2 mm
instance input was `546 x 467 x 415`; the instance branch processed 80 windows
and took 1,876.6 seconds. The complete backend finished in 2,342.0 seconds with
`actual_device=mps`, `mps_state=validated`, no fallback, and no MPS OOM. A
macOS `footprint` sample during instance inference reported 10 GB physical
footprint and an 11 GB observed peak.

The final labelmap matched the source `230 x 220 x 160` shape and affine,
contained 136,101 non-zero voxels across 29 FDI labels, and generated a separate
ToothSeg HTML/STL preview under `surface_preview/toothseg/`. The original
craniofacial labelmap SHA-256 remained unchanged. These measurements establish
runtime and geometry behavior on the tested M1 16 GB Mac; they are not a
clinical accuracy evaluation.
