# totalsegmentator-wrapper-dicom-normalizer

C++ DICOM intake and rescue binary for TotalSegmentator Wrapper workflows.

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
`scripts/build_dicom_normalizer_mac.sh` also bundles the complete native runtime
under `build/dicom_normalizer/lib` and rewrites load paths to `@loader_path`, so
the shipped helper does not require Homebrew on the user's Mac.

On Windows, the build requires the exact GDCM 3.2.6 SDK headers/CMake config
and the approved `python_gdcm-3.2.6-cp312-cp312-win_amd64.whl` static
libraries. The official GDCM Windows DLL build uses the VS2013 runtime and is
intentionally rejected as an unsafe C++ ABI boundary. Configure from an MSVC
x64 developer shell:

```powershell
cmake -S native/dicom_normalizer -B build/dicom_normalizer-win `
  -G Ninja -DCMAKE_BUILD_TYPE=Release `
  -DGDCM_DIR="<GDCM SDK>\lib\gdcm-3.2" `
  -DDICOM_NORMALIZER_GDCM_STATIC_LIB_DIR="<app-private Python>\Lib\site-packages\_gdcm"
cmake --build build/dicom_normalizer-win
ctest --test-dir build/dicom_normalizer-win --output-on-failure
```

The Windows child-process path uses `CreateProcessW`, explicit inherited
handles, UTF-16 paths, a bounded wait, and a kill-on-close Job Object. It does
not invoke a shell. The executable embeds `longPathAware=true`.

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
pixel_decode_failed
enhanced_ct_geometry_unverified
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

Compressed transfer syntaxes are decoded and, when needed for dcm2niix, losslessly
transcoded to Explicit VR Little Endian with embedded GDCM. A decode failure is a
hard `pixel_decode_failed` result; it never silently falls back to metadata-only
acceptance. Enhanced CT pixel data is decoded, but the series remains
`enhanced_ct_geometry_unverified` until every per-frame functional-group geometry
item can be validated.

Windows 10 MSVC build and synthetic conversion evidence is recorded under
`artifacts/spike/windows-dicom-msvc/`. The resulting helper has GDCM and its
codecs statically linked and has no GDCM/VS2013 runtime dependency. Windows 11,
clean-machine packaging, redistribution approval, and cross-host macOS
semantic comparison remain unverified.
