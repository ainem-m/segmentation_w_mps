# 14 TotalSegmentator Smoke Notes

Date: 2026-06-11

## Summary

TotalSegmentator 2.14.0 was installed into the project `.venv` and the
`craniofacial_structures` task was run with both CPU and MPS on a small public
TotalSegmentator test CT NIfTI.

The MPS path executed successfully:

```text
Task: craniofacial_structures
Input: TotalSegmentator tests/reference_files/example_ct_sm.nii.gz
Input shape: 122 x 101 x 30
Spacing: 3.0 x 3.0 x 3.0 mm
Torch: 2.12.0
TotalSegmentator: 2.14.0
MPS available: true, when run outside Codex sandbox
```

This is a pipeline smoke result only. The sample is not a dental CBCT/head
volume, so `craniofacial_structures` produced empty masks.

## Commands and Environment

Keep TotalSegmentator config and weights inside ignored workspace artifacts:

```bash
export TOTALSEG_HOME_DIR=artifacts/totalseg_home
export TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights
```

Install TotalSegmentator:

```bash
env UV_CACHE_DIR=.uv-cache uv pip install --python .venv/bin/python TotalSegmentator
```

Disable usage stats for this local smoke environment:

```bash
env TOTALSEG_HOME_DIR=artifacts/totalseg_home \
    TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights \
    .venv/bin/python -c "from totalsegmentator.config import setup_totalseg, set_config_key; setup_totalseg(); set_config_key('send_usage_stats', False); set_config_key('statistics_disclaimer_shown', True)"
```

Download the public small CT sample:

```bash
curl -L https://raw.githubusercontent.com/wasserth/TotalSegmentator/master/tests/reference_files/example_ct_sm.nii.gz \
  -o artifacts/samples/example_ct_sm.nii.gz
```

## Weight Download Findings

`totalseg_download_weights -t craniofacial_structures` fails in
TotalSegmentator 2.14.0 because the CLI `choices` list omits
`craniofacial_structures` and `teeth`, even though the internal `task_to_id` map
contains them.

Workaround used:

```bash
env TOTALSEG_HOME_DIR=artifacts/totalseg_home \
    TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights \
    .venv/bin/python -c "from totalsegmentator.config import setup_totalseg, set_config_key; from totalsegmentator.libs import download_pretrained_weights; setup_totalseg(); set_config_key('send_usage_stats', False); set_config_key('statistics_disclaimer_shown', True); download_pretrained_weights(115)"
```

Weights downloaded:

```text
craniofacial_structures: ID 115, Dataset115_mandible
teeth: ID 113, Dataset113_ToothFairy3
crop helper: ID 298, Dataset298_TotalSegmentator_total_6mm_1559subj
```

## craniofacial_structures Result

First run included a crop-helper weight download in the CPU timing, so it is
not a fair CPU/MPS comparison:

```text
CPU first run: 137.13 s
MPS first run: 24.58 s
Speedup reported by script: 5.58x
Issue: CPU timing included Dataset298 download/extraction.
```

Cached rerun after all required weights were present:

```text
CPU cached run: 19.15 s
MPS cached run: 16.87 s
Cached speedup: 1.13x
```

Both cached runs exited successfully and wrote the expected output files:

```text
head.nii.gz
mandible.nii.gz
sinus_frontal.nii.gz
sinus_maxillary.nii.gz
skull.nii.gz
teeth_lower.nii.gz
teeth_upper.nii.gz
```

All output masks were empty on this sample:

```text
nonzero voxels: 0 for every craniofacial output mask
log message: INFO: Crop is empty. Returning empty segmentation.
```

Interpretation:

```text
MPS execution path works for TotalSegmentator craniofacial_structures.
This input is not suitable for visual validation, Slicer demo, or publication.
A real head/dental CBCT-derived NIfTI is required for the next validation gate.
```

## teeth Result

Plain CLI attempt:

```bash
env TOTALSEG_HOME_DIR=artifacts/totalseg_home \
    TOTALSEG_WEIGHTS_PATH=artifacts/totalseg_weights \
    .venv/bin/TotalSegmentator \
      -i artifacts/samples/example_ct_sm.nii.gz \
      -o artifacts/totalseg_smoke/teeth_mps/output \
      -ta teeth \
      --device mps
```

Result:

```text
exit code: 1
failure: TypeError: expected string or bytes-like object, got 'NoneType'
location: totalsegmentator/python_api.py validate_device_type_api()
```

Cause:

```text
For tasks with crop_model, TotalSegmentator recursively calls totalsegmentator()
with device=convert_device_to_string(device). In 2.14.0, convert_device_to_string()
returns a value only for torch.device objects. The CLI path keeps device as the
string "mps", so the recursive crop_model call receives device=None.
```

Runtime monkey patch finding:

```text
Patching convert_device_to_string() in-process to return string devices lets the
teeth task get past the TypeError and enter the 231-step teeth inference loop.
The run was stopped manually at 31/231 steps because this was only a smoke
investigation and the public sample is anatomically unsuitable.
```

Decision:

```text
Treat teeth on TotalSegmentator 2.14.0 CLI as blocked until the runner includes
a workaround or a newer upstream version fixes device propagation. Do not spend
long runtime on teeth until a real dental/head NIfTI sample is available.
```

Implementation note:

```text
The backend runner now fast-fails task=teeth with a clear failed result instead
of entering the upstream TypeError path. This keeps the UI/preview workflow
predictable until a tested workaround is added.
```

## Next Gate

Before backend/UI work, run `craniofacial_structures` on a real head or
dental-CBCT-derived NIfTI:

```text
[x] MPS exit code 0 on DZ-CBCT
[x] output masks are non-empty on DZ-CBCT
[ ] CPU/MPS benchmark is deferred until a smaller representative input is selected
[ ] Slicer visual alignment is deferred, not a Phase 3 blocker
```

The MPS/non-empty gate is now satisfied by DZ-CBCT, so backend runner
implementation can proceed. Do not block backend runner or UI work on CPU
completion; see `docs/17_CPU_AND_ROI_BENCHMARK_NOTES.md`.
