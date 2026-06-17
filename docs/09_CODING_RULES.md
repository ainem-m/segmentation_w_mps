# 09 Coding Rules

## Development style

Build the smallest vertical slice first.

Priority order:

```text
1. MPS smoke test
2. TotalSegmentator MPS run
3. benchmark log
4. Slicer handoff
5. minimal UI
```

## Error handling

All failures must produce actionable messages.

Bad:

```text
Segmentation failed.
```

Good:

```text
MPS smoke test failed: ConvTranspose3D FP32 did not run on MPS.
Torch version: x.y.z. Try a PyTorch build that includes MPS ConvTranspose3D support, or run with --device cpu.
```

## Logging

Log files:

```text
logs/run.log
logs/environment.json
logs/benchmark.json
```

Never log:

```text
PatientName
PatientID
BirthDate
AccessionNumber
InstitutionName
```

If path logging is needed, use:

```text
- basename only
- hash
- or user-approved debug mode
```

## Device reporting

Always distinguish:

```text
requested_device
actual_device
fallback_reason
```

Never silently fallback from MPS to CPU without recording it.

## Precision

Use FP32.

Do not enable:

```text
autocast
mixed precision
fp16
bf16
```

unless separately validated.

## Subprocesses

Prefer subprocess for TotalSegmentator:

```text
subprocess.run([...], capture_output=True, text=True)
```

Record:

```text
- command without PHI
- stdout
- stderr
- exit code
- elapsed time
```

## Dependency checks

Provide functions:

```text
check_python()
check_torch()
check_mps()
check_totalsegmentator()
check_slicer_path()
```

Future CLI command:

```bash
dentalseg doctor
```

## File formats

MVP:

```text
Input: NIfTI
Output: TotalSegmentator raw outputs + Slicer script
```

Avoid DICOM-specific logic in MVP.

## Source separation

Keep modules independent:

```text
device.py      no TotalSegmentator dependency
runner.py      no UI dependency
slicer.py      no torch dependency
benchmark.py   no UI dependency
```

## Clinical language filter

Do not use these terms in UI/public text:

```text
diagnosis
treatment planning
implant planning
surgical planning
airway assessment
risk analysis
safe distance
clinical decision
```

Use:

```text
non-clinical
research/education
visualization
preliminary segmentation
manual review required
```

## Commit discipline for coding agents

Make small commits by phase:

```text
phase0-skeleton
phase1-mps-smoke-test
phase2-totalseg-runner
phase3-benchmark-logs
phase4-slicer-handoff
phase5-mac-preview-ui
```

Do not mix UI and backend in one large change.
