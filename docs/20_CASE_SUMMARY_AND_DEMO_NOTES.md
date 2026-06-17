# 20 Case Summary and Demo Notes

Date: 2026-06-11

## Summary

Case outputs now include machine-readable mask statistics and a regeneratable
Markdown summary. This supports demo notes and public-facing screenshots without
rerunning segmentation.

## Generated Files

Each completed runner case now writes:

```text
logs/mask_stats.json
README_OUTPUT.md
```

`mask_stats.json` includes:

```text
- mask name
- label key
- nonzero voxel count when readable
- dimensions
- spacing
- read error if a file is not valid NIfTI
```

## Summary Command

```bash
env PYTHONPATH=src .venv/bin/python -m totalsegmentator_wrapper_mac summary \
  --case artifacts/cli_smoke/dz_cbct_craniofacial_mps \
  --output artifacts/cli_smoke/dz_cbct_craniofacial_mps/CASE_SUMMARY.md
```

## Demo Run Script

Run from a normal Mac terminal outside the Codex sandbox:

```bash
scripts/run_dz_cbct_mps_demo.sh
```

Optional arguments:

```bash
scripts/run_dz_cbct_mps_demo.sh path/to/input.nii.gz runs/my_case
```

The DZ-CBCT MPS case summary has been generated at:

```text
artifacts/cli_smoke/dz_cbct_craniofacial_mps/CASE_SUMMARY.md
```

## Demo Numbers

Current primary demo numbers are the three experimental `teeth` MPS cases:

```text
Case: DZ-CBCT jawcrop 0.5 mm
Task: teeth, experimental opt-in
Device: mps
Elapsed seconds: 98.48
Non-empty labels: 56
ROI: 190 x 152 x 132

Case: Case02 native CBCT
Task: teeth, experimental opt-in
Device: mps
Elapsed seconds: 112.12
Non-empty labels: 54
ROI: 395 x 321 x 308

Case: STS24 open data 0026
Task: teeth, experimental opt-in
Device: mps
Elapsed seconds: 85.40
Non-empty labels: 55
ROI: 302 x 265 x 234

All three: fallback_reason=null, torch.mps_fallback_env=null,
MPS ConvTranspose3D gate passed, full-space labelmap exists,
surface_preview/index.html exists.
CPU: effectively not runnable for the current preview path
```

See `docs/27_THREE_CASE_DEMO_READINESS.md` for the consolidated readiness
table and artifact map.

## Next Demo Tasks

```text
[ ] Record a short screen capture from the precomputed offline HTML viewer.
[ ] Show `doctor` output or saved benchmark JSON for MPS proof.
[ ] Show full-space NIfTI and STL outputs as export artifacts.
[ ] Keep long craniofacial preflight runs as precomputed setup, not live demo.
```
