# 29 C++ DICOM Normalizer / Rescue Binary Plan

## Summary

Start a separate Mac-oriented C++ binary for DICOM intake, normalization, and
rescue. This is intentionally separate from the Python `totalsegmentator-wrapper-mac`
package, which stays NIfTI-first and MPS-inference-focused.

The first C++ deliverable is a standalone metadata audit binary:

```bash
totalsegmentator-wrapper-dicom-normalizer audit \
  --dicom-dir path/to/dicom_folder \
  --output artifacts/dicom_audit_cpp.json
```

The binary should eventually handle difficult dental exports such as secondary
capture screen-save stacks, but it must represent those outputs as
`rescue/pseudo-volume` with explicit warnings, never as clean original CT.

## Product Split

```text
totalsegmentator-wrapper-mac
  Python package
  NIfTI-first
  MPS inference
  surface preview
  no DICOM repair

totalsegmentator-wrapper-dicom-normalizer
  C++ Mac binary
  DICOM series audit
  clean CT conversion orchestration
  rescue pseudo-volume builder
  provenance and warning metadata
```

## Python App Bridge

The Python preview app can launch this binary as a fallback/intake step without
owning DICOM normalization itself.

Current bridge behavior:

```text
totalsegmentator_wrapper_mac.dicom_normalizer_bridge
  finds the C++ binary from:
    1. explicit path
    2. TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER
    3. build/dicom_normalizer/totalsegmentator-wrapper-dicom-normalizer
    4. PATH

SwiftUI app:
  Browse DICOM selects a folder
  Run on a folder launches C++ audit instead of TotalSegmentator
  DICOM Audit button launches the same audit explicitly
  output goes to <case>/logs/dicom_normalizer_audit.json
```

This keeps the Python package NIfTI-first while allowing a user-facing app to
route unreadable or non-mainline DICOM folders into the C++ intake path.

## Why C++

C++ is a better home for this layer because DICOM ingest needs:

```text
- deterministic filesystem traversal and streaming metadata reads
- fewer Python packaging dependencies in the end-user app
- reusable CLI / app / Python-binding surface
- future integration with DCMTK, GDCM, ITK, or an embedded dcm2niix path
- strict provenance and safety flags before segmentation sees any volume
```

But C++ does not magically recover missing geometry. For Case03-like data, the
normalizer can only build a pseudo volume when rules are explicit and warnings
are preserved.

## Case03 Target Behavior

Case03 was:

```text
SOP Class: Secondary Capture Image Storage
ImageType: DERIVED\SECONDARY\SCREEN SAVE\AVERAGE
BurnedInAnnotation: YES
PixelSpacing: missing
ImagePositionPatient: missing
ImageOrientationPatient: missing
AXIAL BO series: 512 x 512 x 138
```

The intended C++ normalizer behavior is:

```text
audit:
  classify AXIAL BO as secondary_capture_rescue_candidate
  classify coronal/sagittal screen saves as reject
  classify Dose Report as reject

prepare-rescue:
  require explicit --series-number
  require explicit --patched-spacing X,Y,Z
  isolate selected series
  run or embed dcm2niix
  patch qform/sform/header spacing
  generate MPR PNG montage
  write provenance JSON with rescue warnings
```

Every rescue output must include:

```text
secondary_capture: true
geometry_inferred: true
burned_in_annotation: true
not_segmentation_grade_original_ct: true
manual_spacing_required: true
```

## CLI Roadmap

### Phase 1: audit

Implemented first.

```bash
totalsegmentator-wrapper-dicom-normalizer audit \
  --dicom-dir <dir> \
  --output <audit.json>
```

Output classification:

```text
original_ct_geometry_ok
secondary_capture_rescue_candidate
compressed_pixel_data
needs_dicom_library
dicomdir_only
reject
```

The audit reads DICOM metadata only. It does not inspect pixel data or write
volumes. It also reports `next_action`, `reject_reason`,
`requires_external_tool`, optional DCMTK/GDCM tool availability, and DICOMDIR
reference counts.

### Phase 2: convert-clean

```bash
totalsegmentator-wrapper-dicom-normalizer convert-clean \
  --dicom-dir <dir> \
  --series-number <n> \
  --output <artifact_dir>
```

Implemented as a dcm2niix wrapper. It copies the selected series into
`output/isolated_series`, writes uncompressed NIfTI output into
`output/dcm2niix`, saves `logs/dcm2niix_clean.log`, and writes
`convert_clean_metadata.json`. It is allowed only for
`original_ct_geometry_ok` and does not start segmentation.

### Phase 3: prepare-rescue

```bash
totalsegmentator-wrapper-dicom-normalizer prepare-rescue \
  --dicom-dir <dir> \
  --series-number 200 \
  --patched-spacing 0.6,0.6,0.9375 \
  --output <artifact_dir>
```

Implemented as an explicit rescue path for `secondary_capture_rescue_candidate`.
It requires explicit spacing. No OCR or silent inference.

Current behavior:

```text
select series by --series-number
copy selected DICOM files into output/isolated_series with safe sequential names
run dcm2niix with uncompressed NIfTI output
patch NIfTI-1 pixdim/qform/sform to the explicit spacing
write rescue_metadata.json with warning flags
write rescue_validation.json with NIfTI shape/spacing readback
write dependency-free PGM MPR preview slices when possible
```

Current limitation:

```text
The patched rescue NIfTI uses an identity orientation with explicit spacing.
This is appropriate only for rescue/pseudo-volume inspection, not for clean CT
geometry preservation.
```

## Dependencies

Phase 1 is dependency-light and implemented with a small metadata parser for
common Little Endian DICOM files.

Future dependency options:

```text
DCMTK:
  robust metadata parsing and transfer syntax support

GDCM:
  broad DICOM read coverage

dcm2niix:
  conversion engine for clean CT and pseudo-volume rescue

ITK:
  image writing, orientation, and geometry operations
```

Do not add these dependencies until the audit CLI shape and Case02/03/04
classification behavior are stable.

## Safety Rules

```text
- Never call rescue output "normalized CT".
- Never auto-infer spacing from burned-in text.
- Never erase Secondary Capture / inferred geometry warnings.
- Never feed rescue output into teeth segmentation automatically.
- Always prefer original axial CT DICOM when available.
- Do not include PHI-bearing file paths in shareable JSON.
```
