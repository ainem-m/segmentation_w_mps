# totalsegmentator-wrapper-dicom-normalizer

Mac-oriented C++ DICOM intake and rescue binary for TotalSegmentator Wrapper workflows.

Current phase:

```text
metadata audit
clean CT dcm2niix conversion
explicit secondary-capture rescue preparation
```

## Build

```bash
cmake -S native/dicom_normalizer -B build/dicom_normalizer
cmake --build build/dicom_normalizer --parallel
```

The binary is:

```text
build/dicom_normalizer/totalsegmentator-wrapper-dicom-normalizer
```

## Audit

```bash
totalsegmentator-wrapper-dicom-normalizer doctor
```

`doctor` reports the normalizer version, supported commands, and optional
external transcoder availability.

```bash
totalsegmentator-wrapper-dicom-normalizer audit \
  --dicom-dir path/to/dicom_folder \
  --output artifacts/dicom_audit_cpp.json
```

Classifications:

```text
original_ct_geometry_ok
secondary_capture_rescue_candidate
compressed_pixel_data
needs_dicom_library
dicomdir_only
reject
```

The audit JSON also records `next_action`, `reject_reason`,
`requires_external_tool`, DICOMDIR reference counts, and optional tool
availability for `gdcmconv`, `dcmdjpeg`, and `dcmconv`.

## Convert Clean

```bash
totalsegmentator-wrapper-dicom-normalizer convert-clean \
  --dicom-dir path/to/dicom_folder \
  --series-number 3 \
  --output artifacts/case_clean
```

This path:

```text
- accepts only original_ct_geometry_ok series
- isolates selected DICOM files with safe sequential filenames
- runs dcm2niix with uncompressed NIfTI output
- writes convert_clean_metadata.json with provenance
- never starts segmentation
```

## Prepare Rescue

```bash
totalsegmentator-wrapper-dicom-normalizer prepare-rescue \
  --dicom-dir path/to/dicom_folder \
  --series-number 200 \
  --patched-spacing 0.6,0.6,0.9375 \
  --output artifacts/case_rescue
```

This path:

```text
- accepts only secondary_capture_rescue_candidate series
- requires explicit spacing
- isolates selected DICOM files with safe sequential filenames
- runs dcm2niix with uncompressed NIfTI output
- patches NIfTI-1 pixdim/qform/sform to the requested spacing
- writes rescue_metadata.json with warning flags
- writes rescue_validation.json with NIfTI shape/spacing readback
- writes dependency-free PGM MPR preview slices when possible
```

The patched NIfTI uses identity orientation and explicit spacing. Treat it as a
pseudo volume for rescue inspection, not as clean original CT geometry.

## Design Boundary

This binary is intentionally separate from the Python `totalsegmentator-wrapper-mac`
package. The Python package remains NIfTI-first and handles MPS inference and
surface previews. This C++ binary owns messy DICOM intake.

Case03-like data is treated as:

```text
secondary_capture_rescue_candidate
rescue/pseudo-volume only
not segmentation-grade original CT
```

Rescue outputs preserve warning metadata:

```text
secondary_capture: true
geometry_inferred: true
burned_in_annotation: true
not_segmentation_grade_original_ct: true
manual_spacing_required: true
```

Compressed transfer syntaxes are detected and classified, but this binary does
not implement native pixel decompression. If DCMTK/GDCM tools are found they are
reported as available adapters; otherwise the JSON marks the series as requiring
an external tool.
