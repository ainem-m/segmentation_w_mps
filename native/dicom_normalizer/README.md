# totalsegmentator-wrapper-dicom-normalizer

Mac-oriented C++ DICOM intake and rescue binary for TotalSegmentator Wrapper workflows.

The parser and pixel decoder use GDCM. JPEG, JPEG-LS, JPEG 2000, and RLE
transfer syntaxes are decoded in-process; successful metadata parsing alone is
never treated as proof that pixel data is readable.

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

GDCM 3.x development files are required to build. On macOS,
`scripts/build_dicom_normalizer_mac.sh` can bundle the native runtime under
`build/dicom_normalizer/lib` and rewrite load paths to `@loader_path`. This is
not, by itself, a macOS-version compatibility attestation: a release targeting
macOS 14+ must independently inspect every bundled Mach-O load command and
reject a helper whose `minos` exceeds the declared deployment target. A helper
built against the host's Homebrew runtime must not be assumed runnable on an
older supported macOS release.

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
  --dicom-dir path/to/dicom_folder_or_single_file \
  --output artifacts/dicom_audit_cpp.json
```

Classifications:

```text
original_ct_geometry_ok
secondary_capture_rescue_candidate
geometry_rescue_candidate
compressed_pixel_data
pixel_decode_failed
dicomdir_only
reject
```

The audit JSON also records `next_action`, `reject_reason`,
`requires_external_tool`, DICOMDIR reference counts, and optional tool
availability for `gdcmconv`, `dcmdjpeg`, and `dcmconv`.

## Series Selection

Pass `--series-key` whenever the audit supplied one. It is the stable selection
handle and always takes precedence when both selectors are present. A legacy
`--series-number` request is accepted only if it identifies exactly one series;
duplicate Series Numbers fail with `ambiguous_series_number_use_series_key`.
The normalizer never picks the largest duplicate series as a tie-breaker.

If Series Instance UID is missing, the fallback key is path-free and separates
files by the available Study Instance UID and Frame of Reference UID, plus the
existing Series Number and Series Description values. UID and description
components are SHA-256 digests in the key. Files that also lack both Study
Instance UID and Frame of Reference UID remain visible in the audit but are
classified `reject` with `missing_stable_series_grouping_identity`; conversion
and rescue do not guess a grouping from filenames or directory layout.

A present Series Instance UID remains the canonical raw selection key. Before
conversion or rescue, every file under that key must have consistent Study
Instance UID and Frame of Reference UID values, including consistent tag
presence. Mixed values or partial tag loss are classified `reject`; the
normalizer does not split a duplicated Series UID using geometry or paths.

## Convert Clean

```bash
totalsegmentator-wrapper-dicom-normalizer convert-clean \
  --dicom-dir path/to/dicom_folder_or_single_file \
  --series-key <audit-series-key> \
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
  --dicom-dir path/to/dicom_folder_or_single_file \
  --series-key <audit-series-key> \
  --patched-spacing 0.6,0.6,0.9375 \
  --output artifacts/case_rescue
```

This path:

```text
- accepts only secondary_capture_rescue_candidate or geometry_rescue_candidate series
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

The raw `export-rescue-stack` path directly writes GDCM-decoded samples without
applying DICOM display or modality transforms. That manual raw-export path has a
strict pixel-semantics boundary:

```text
- MONOCHROME2 only
- unsigned 8-bit or signed/unsigned 16-bit decoded samples
- Rescale Slope and Rescale Intercept both absent, or exactly 1 and 0
- no Modality LUT
- no Shared or Per-frame Pixel Value Transformation Sequence
```

MONOCHROME1, signed 8-bit, non-identity or incomplete rescale pairs, a Modality
LUT, and Shared/Per-frame pixel-value transforms fail before a raw stack artifact
is written. This check does not turn an otherwise valid CT series into an audit
reject. `convert-clean`, `prepare-rescue`, and `prepare-viewer-export` continue to
route DICOM through dcm2niix, which applies supported DICOM pixel transforms.

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

Compressed transfer syntaxes are decoded and, when needed for dcm2niix, losslessly
transcoded to Explicit VR Little Endian with embedded GDCM. A decode failure is a
hard `pixel_decode_failed` result; it never silently falls back to metadata-only
acceptance. Enhanced or multi-frame CT pixel data is decoded, but it is never
accepted as clean CT geometry without complete per-frame validation. It is
routed through the explicit shape-confirmation rescue path as
`geometry_rescue_candidate` with `per_frame_geometry_not_fully_validated`
provenance. Only the manual raw `export-rescue-stack` operation is subject to
the stricter no-transform boundary above.
