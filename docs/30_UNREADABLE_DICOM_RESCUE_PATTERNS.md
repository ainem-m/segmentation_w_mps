# 30 Unreadable DICOM Rescue Patterns

## Summary

Goal: make `totalsegmentator-wrapper-dicom-normalizer` rescue more DICOM folders than a normal
viewer import path, while preserving explicit warnings and avoiding false
claims of clean CT geometry.

External references reviewed:

```text
pydicom dataset basics:
  files may lack the PS3.10 DICM prefix / File Meta header and still contain
  DICOM datasets; force-reading is possible but must be treated carefully.

pydicom pixel data notes:
  Transfer Syntax UID controls encoding/compression, compressed frames are
  encapsulated, and decompression may require external plugins.

David Clunie's medical image format FAQ:
  File Meta is explicit VR little endian, DICOMDIR is an index object, and
  transfer syntax changes after the meta header.

dcm2niix notes:
  transfer syntax support varies, some compressed formats may need DCMTK/GDCM
  pre-conversion, and force-merge can be useful for certain 2D slice stacks.
```

URLs:

```text
https://pydicom.github.io/pydicom/stable/tutorials/dataset_basics.html
https://pydicom.github.io/pydicom/stable/tutorials/pixel_data/introduction.html
https://www.dclunie.com/medical-image-faq/html/part2.html
https://www.nitrc.org/plugins/mwiki/index.php/dcm2nii:MainPage
```

## Pattern Table

| Pattern | Current handling | Next target |
|---|---|---|
| PS3.10 file with DICM prefix | audit supported | keep |
| File Meta without DICM prefix | audit supported | keep |
| Implicit VR Little Endian | audit supported | keep |
| Explicit VR Big Endian | audit supported for metadata | validate with real sample |
| Secondary Capture axial stack | rescue candidate | prepare-rescue supported |
| Secondary Capture multi-frame axial stack | rescue candidate | prepare-rescue supported |
| Viewer/MPR export mixed geometry in one CT Series | `viewer_export_mpr_mixed_candidate`; user selects one geometry group | `prepare-viewer-export` supported for selected group only |
| Coronal/sagittal screen-save | reject | keep |
| Scout/localizer | reject | keep |
| Dose report / SR-like report | reject | improve SOP coverage |
| DICOMDIR | index object classified as `dicomdir_only`; referenced file IDs counted | add deeper directory-record parsing only if real failures require it |
| Compressed JPEG/JPEG-LS/JPEG2000/RLE | GDCM decodes pixels in-process; clean CT may be losslessly transcoded before dcm2niix | validate additional vendor samples |
| Invalid compressed payload | hard `pixel_decode_failed`; no metadata-only fallback | keep |
| Enhanced CT multi-frame | pixels decoded, but classified `enhanced_ct_geometry_unverified` | validate every shared/per-frame functional-group geometry item |
| Missing geometry original CT | reject | normalize only with validated geometry source |

## Current C++ Additions

`totalsegmentator-wrapper-dicom-normalizer audit` now records:

```text
dicm_prefix_count
file_meta_count
transfer_syntax_uid
transfer_syntax_name
compressed_transfer_syntax
number_of_frames_max
effective_frame_count
has_pixel_data_count
samples_per_pixel
photometric_interpretation
bits_allocated
pixel_representation
```

It also classifies:

```text
DICOMDIR -> dicomdir_only / audit_referenced_files
compressed pixel data -> native GDCM decode, then normal geometry classification
invalid compressed pixel data -> pixel_decode_failed
Enhanced CT or multi-frame CT -> enhanced_ct_geometry_unverified
single-file multi-frame Secondary Capture axial stack -> secondary_capture_rescue_candidate
viewer/MPR export mixed geometry -> viewer_export_mpr_mixed_candidate
```

Each series classification now includes:

```text
status
grade
rescue_grade
reasons
reject_reason
next_action
requires_external_tool
recommendation
```

Top-level audit JSON also records:

```text
optional_tools.gdcmconv / dcmdjpeg / dcmconv
optional_tools.any_transcoder
dicomdir.referenced_file_ids
dicomdir.resolved_reference_count
dicomdir.missing_reference_count
```

## Verified Synthetic Cases

```text
multi-frame Secondary Capture:
  audit: secondary_capture_rescue_candidate
  prepare-rescue: success
  output shape: 32 x 32 x 40
  output spacing: 0.6 x 0.6 x 0.9375

no-DICM-prefix implicit VR CT-like file:
  audit: parsed as DICOM
  classification: reject because geometry is incomplete and slice count is too small

Case02 clean CBCT:
  audit: original_ct_geometry_ok
  next_action: convert_clean

unnamed_1002002 viewer/MPR export:
  audit: viewer_export_mpr_mixed_candidate
  groups:
    g001 sagittal_like 89 files, preview_only
    g002 coronal_like 89 files, preview_only
    g003 axial_like 81 files, rescue_go_with_warning
  prepare-viewer-export g003: success
  output shape: 713 x 713 x 81
  output spacing: 0.125 x 0.125 x 1.0
  mpr_preview: axial/coronal/sagittal middle-slice PGM files with min/max and
    uniform_or_empty metadata
```

## Current C++ Commands

```bash
totalsegmentator-wrapper-dicom-normalizer doctor

totalsegmentator-wrapper-dicom-normalizer audit \
  --dicom-dir <dir> \
  --output <audit.json>

totalsegmentator-wrapper-dicom-normalizer convert-clean \
  --dicom-dir <dir> \
  --series-number <n> \
  --output <artifact_dir>

totalsegmentator-wrapper-dicom-normalizer prepare-rescue \
  --dicom-dir <dir> \
  --series-number <n> \
  --patched-spacing X,Y,Z \
  --output <artifact_dir>

totalsegmentator-wrapper-dicom-normalizer prepare-viewer-export \
  --dicom-dir <dir> \
  --series-number <n> \
  --group-id <gNNN> \
  --output <artifact_dir>
```

`convert-clean` is allowed only for `original_ct_geometry_ok`.
`prepare-rescue` is allowed only for `secondary_capture_rescue_candidate` and
still requires explicit spacing. Neither command starts segmentation.
`prepare-viewer-export` is allowed only for `viewer_export_mpr_mixed_candidate`.
It re-audits the series, isolates the selected group, runs dcm2niix on selected
files only, requires exactly one NIfTI, validates header shape/spacing against
the group, writes `viewer_export_metadata.json`, and emits `mpr_preview/*.pgm`
middle-slice images. The app flow is:

```text
audit -> select viewer/MPR geometry group -> prepare-viewer-export
      -> slice preview confirmation -> preview creation
```

The generated NIfTI is not accepted as the preview input until the user confirms the
slice preview. If preview images are missing or all middle slices are uniform,
the app blocks preview creation and asks the user to choose another group or obtain
the original axial CT DICOM. `prepare-viewer-export` itself does not start
segmentation automatically.

Distribution policy: this is a rescue preview path, not original CT recovery.
Only `axial_like` or `oblique_axial_like` groups are preview candidates. Coronal and
sagittal groups remain reference-only. The app preserves spacing/affine and
does not fuse three directions into a higher-resolution CT. If the slice spacing
is coarse relative to in-plane spacing, the UI warns that 3D results may look
stair-stepped and remain non-diagnostic preview.

`doctor` reports whether optional transcoders are available. The current binary
reports GDCM as its primary backend and includes native compressed pixel decoding
and lossless transcoding. Optional command-line transcoders are diagnostic only;
they are not a silent fallback. Synthetic codec fixtures cover JPEG, JPEG-LS,
JPEG 2000, and RLE. Enhanced CT remains blocked from clean conversion until
per-frame geometry validation is implemented.

## Safety Position

The normalizer should be more permissive at intake than Slicer, but stricter in
metadata. It can rescue a stack for inspection, but it must never silently
promote a rescue volume into clean CT.

Rules:

```text
- preserve rescue warning flags
- require explicit spacing for screen-save rescue
- do not OCR burned-in text automatically yet
- do not infer original CT geometry from screen-save pixels
- do not pass a mixed viewer/MPR export series to dcm2niix as a whole and then
  pick an output retrospectively
- do not allow coronal/sagittal viewer groups to enter the preview creation path in v1
- require slice preview confirmation before viewer/MPR export rescue enters the
  preview creation path
- do not fuse 3-direction viewer/MPR exports into a high-resolution CT in the
  distribution build
- do not auto-run segmentation after rescue
- prefer original axial CT DICOM whenever available
```
