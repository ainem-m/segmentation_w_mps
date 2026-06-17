# 05 Validation and Benchmarks

## Validation principle

Do not claim Mac/MPS support unless it passes three levels:

```text
Level 1: PyTorch operator smoke test
Level 2: TotalSegmentator task smoke test
Level 3: CPU vs MPS benchmark on sample volume
```

## Level 1: ConvTranspose3D smoke test

Run:

```bash
python scripts/smoke_test_mps_convtranspose3d.py
```

Required result:

```text
mps_available: True
convtranspose3d_fp32: PASS
```

Failure policy:

```text
If this fails, do not proceed to app work.
Update PyTorch version or use nightly/stable candidate that passes.
```

## Level 2: TotalSegmentator MPS smoke test

Run:

```bash
bash scripts/smoke_test_totalseg_mps.sh sample.nii.gz out_smoke
```

Expected:

```text
- craniofacial_structures output exists
- process exits 0
- log contains device mps or command records --device mps
```

Then test:

```bash
TotalSegmentator -i sample.nii.gz -o out_teeth -ta teeth --device mps
```

If `teeth` fails, capture the exact error. Do not hide it.

## Level 3: Benchmark

Run:

```bash
python scripts/benchmark_cpu_vs_mps.py \
  --input sample.nii.gz \
  --task craniofacial_structures \
  --output bench_out
```

Record:

```text
- Mac model/chip
- RAM
- macOS version
- Python version
- PyTorch version
- TotalSegmentator version
- task
- volume dimensions
- spacing
- CPU time
- MPS time
- speedup
```

## Benchmark table format

```markdown
| Machine | RAM | macOS | Task | Volume | CPU | MPS | Speedup | Notes |
|---|---:|---|---|---|---:|---:|---:|---|
| MacBook Pro M2 Pro | 16GB | 14.x | craniofacial_structures | 512x512xN | 00:00 | 00:00 | 0.0x | FP32 |
```

## Accuracy validation for preview

MVP does not need formal Dice validation. It needs visual plausibility only.

Check in Slicer:

```text
[ ] mandible/teeth/skull structures align with CT visually
[ ] no total orientation flip
[ ] output is not empty
[ ] segmentation appears in expected anatomical region
[ ] teeth task labels are not obviously nonsensical
```

## Hard failure criteria

Do not publish if:

```text
- MPS does not actually run
- output is empty
- output is grossly flipped/misaligned
- benchmark shows MPS slower than CPU without explanation
- app silently falls back to CPU while claiming MPS
```

## Soft failure criteria

Publish with caveats if:

```text
- craniofacial_structures works but teeth is unstable
- MPS speedup is modest but visible
- DICOM direct input fails but NIfTI works
- Slicer auto-conversion to Segmentation node needs manual step
```

## Benchmark integrity rules

```text
- Do not cherry-pick only best run without stating it.
- Do not use clinical claims.
- Do not compare against commercial tools.
- Do not compare against CUDA unless measured.
- Always state the hardware and software versions.
```
