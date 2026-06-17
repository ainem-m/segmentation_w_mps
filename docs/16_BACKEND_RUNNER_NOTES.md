# 16 Backend Runner Notes

Date: 2026-06-11

## Summary

Phase 3 backend runner skeleton is implemented.

Implemented commands:

```bash
python -m totalsegmentator_wrapper_mac doctor
python -m totalsegmentator_wrapper_mac run --input sample.nii.gz --output out --task craniofacial_structures --device mps
python -m totalsegmentator_wrapper_mac benchmark --input sample.nii.gz --output bench_out --task craniofacial_structures
python -m totalsegmentator_wrapper_mac summary --case out --output out/CASE_SUMMARY.md
```

The runner now generates Slicer handoff files, but it does not launch Slicer.
Slicer visual validation is deferred by project decision.

## Implemented Behavior

```text
device.py
- CPU/MPS/Auto device resolution
- ConvTranspose3D FP32 smoke test for MPS
- MPS request fails loudly if smoke test fails
- Auto falls back to CPU with fallback_reason

runner_totalseg.py
- subprocess TotalSegmentator runner
- sanitized command logging
- streamed stdout/stderr capture into logs/run.log
- elapsed_seconds capture
- output layout creation
- child process group cleanup on interruption
- Slicer handoff generation after run completion
- task=teeth fast-fails with a clear known-upstream-bug message

slicer_export.py
- open_in_slicer.py generation
- label_names.json / label_colors.json generation for existing masks
- logs/mask_stats.json generation with nonzero voxel counts when masks are readable
- README_OUTPUT.md generation with non-clinical notice

case_summary.py
- Markdown/text summary generation from benchmark.json and mask_stats.json

benchmark.py
- environment.json metadata
- input basename/path_hash/dimensions/spacing
- benchmark.json writer

cli.py
- doctor
- run
- benchmark
```

Output layout for `run`:

```text
out/
  input/source.nii.gz              optional, skipped with --no-copy-input
  segmentations/raw_totalseg/
  slicer/open_in_slicer.py
  slicer/label_names.json
  slicer/label_colors.json
  logs/environment.json
  logs/benchmark.json
  logs/mask_stats.json
  logs/run.log
  README_OUTPUT.md
```

## Real MPS Runner Smoke

Command:

```bash
env PYTHONPATH=src \
    TOTALSEG_HOME_DIR=artifacts/totalseg_home \
    TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights \
    .venv/bin/python -m totalsegmentator_wrapper_mac run \
      --input artifacts/samples/DZ-CBCT.nii.gz \
      --output artifacts/cli_smoke/dz_cbct_craniofacial_mps \
      --task craniofacial_structures \
      --device mps \
      --totalseg-bin .venv/bin/TotalSegmentator \
      --no-copy-input
```

Result:

```text
status: success
returncode: 0
requested_device: mps
actual_device: mps
elapsed_seconds: 397.19
ConvTranspose3D FP32 smoke: pass
```

Generated files:

```text
artifacts/cli_smoke/dz_cbct_craniofacial_mps/logs/environment.json
artifacts/cli_smoke/dz_cbct_craniofacial_mps/logs/benchmark.json
artifacts/cli_smoke/dz_cbct_craniofacial_mps/logs/run.log
artifacts/cli_smoke/dz_cbct_craniofacial_mps/segmentations/raw_totalseg/*.nii.gz
```

Nonzero output masks matched the previous ad hoc run:

```text
head.nii.gz          86,742,694
mandible.nii.gz       4,629,868
sinus_frontal.nii.gz          0
sinus_maxillary.nii.gz 3,586,520
skull.nii.gz          8,589,396
teeth_lower.nii.gz      644,816
teeth_upper.nii.gz      730,186
```

## Test Status

```bash
env PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
env PYTHONPATH=src .venv/bin/python -m compileall src tests
```

Both passed.

## Known Limits

```text
- CPU vs MPS benchmark on DZ-CBCT was attempted through the new benchmark command.
- The CPU leg did not finish after 30+ minutes and was manually stopped before output masks/logs were written.
- Treat full-resolution DZ-CBCT CPU as impractically slow, effectively not runnable
  for the current preview path.
- A 0.25 mm DZ-CBCT jaw crop MPS runner smoke succeeded in 123.79 s.
- A 0.5 mm downsampled jaw crop MPS runner smoke succeeded in 130.48 s.
- CPU still did not produce a completed smoke result on the 0.25 mm or 0.5 mm jaw crops.
  Defer exact CPU timing until there is time for an unattended long run.
- TotalSegmentator CPU also needs outside-sandbox execution because nnUNet multiprocessing.Manager()
  needs local socket binding.
- Slicer handoff files are generated, but visual validation in Slicer is deferred.
- TotalSegmentator 2.14.0 teeth CLI still has the crop_model device propagation bug described in docs/14;
  the runner blocks it early with a clear failed result.
- `--skip-device-check` exists only to test fake runners without MPS.
```

## Next Step

Use the successful MPS run as the primary demo number for full-resolution
DZ-CBCT. Do not block backend runner/UI work on CPU. For now, record CPU as
effectively too slow to run in the preview path. CPU-vs-MPS speedup claims should
wait until there is time for an unattended CPU run or until a smaller
representative public sample is selected:

```bash
env PYTHONPATH=src \
    TOTALSEG_HOME_DIR=artifacts/totalseg_home \
    TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights \
    .venv/bin/python -m totalsegmentator_wrapper_mac benchmark \
      --input artifacts/samples/<downsampled-or-smaller-head-ct>.nii.gz \
      --output artifacts/benchmarks/<sample>_craniofacial \
      --task craniofacial_structures \
      --totalseg-bin .venv/bin/TotalSegmentator
```

For the full-resolution DZ-CBCT sample, record:

```text
MPS runner elapsed_seconds: 397.19
CPU benchmark: effectively not runnable for the current preview path;
  stopped after 30+ minutes with no completed output
Additional MPS jaw crop smoke: 123.79 s
Additional MPS 0.5 mm jaw crop smoke: 130.48 s
```

See `docs/17_CPU_AND_ROI_BENCHMARK_NOTES.md` for the ROI and CPU attempt
details.
