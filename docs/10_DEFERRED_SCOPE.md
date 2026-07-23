# 10 Deferred Scope

This document lists features that are important later but explicitly deferred until after the Mac/MPS preview succeeds.

## Deferred until after public preview

```text
- DICOM normalizer inside the Python preview package
- ONNX/Core ML inference
- DentalSegmentator exact nnU-Net backend
- model zoo
- Slicer extension
- VTK preview
- full GUI polish
- batch processing
- mesh smoothing/decimation
- DICOM SEG export
- PACS/DICOMweb
- App Store distribution
```

## Why DICOM normalizer is deferred from the Python preview

DICOM import is likely a major practical hurdle, but it is not the core of this preview.

Core preview claim:

```text
Apple Silicon/MPS can run dental-relevant segmentation locally and show results in an offline 3D preview.
```

DICOM normalizer claim:

```text
This tool can robustly handle messy dental DICOM exports.
```

These are different projects. Mixing them delays the MPS story.

Rule-based rescue of secondary-capture screen-save exports remains outside the
Python preview package. Case-specific rescue procedures may be documented for
engineering reference, and the separate C++ DICOM normalizer project now owns
the path toward clean conversion and rescue pseudo-volume building.

## C++ status

C++ DICOM handling is now justified by the failed/rescue DICOM cases and has
started as a separate Mac-oriented binary under `native/dicom_normalizer/`.
It must stay decoupled from the Python MPS preview until the binary has stable
audit, clean conversion, and rescue provenance behavior.

C++ still introduces:

```text
- build matrix
- codesigning complexity
- dependency management
- crash/security surface
- packaging complexity
```

Do not fold the C++ normalizer into the Python package distribution until those
costs are explicitly handled.

## Why ONNX/Core ML is deferred

ONNX/Core ML could reduce dependency size and improve native distribution, but nnU-Net-style pipelines include preprocessing, sliding-window inference, resampling, and postprocessing. Converting only the neural network is not enough.

Initial inference should use PyTorch/TotalSegmentator directly.

## DentalSegmentator exact mode status

DentalSegmentator is contextually important because prior audience interest came
from it. It is no longer purely deferred: the Mac preview now includes an
explicit opt-in nnU-Net DentalSegmentator backend for the arch/jaw preview path.

Keep the scope narrow. The supported app path is MPS-focused, uses the Zenodo
`Dataset112_DentalSegmentator_v100` weights, writes a multilabel preview
labelmap, and must not silently fallback to TotalSegmentator. Individual tooth
labels, ONNX/Core ML conversion, and a Slicer extension remain deferred.

## Why Slicer extension is deferred

Slicer extension development returns to the environment problem this preview avoids.

The preview should:

```text
run outside Slicer → open results in the app/browser preview
```

not:

```text
run inside Slicer
```

Optional Slicer handoff may be added as a file-only export later:

```text
write Slicer-readable files -> user opens Slicer manually -> drag-and-drop import
```

Do not make Slicer auto-launch, Slicer.app path discovery, or user-run Python
scripts part of the main workflow.

## Feature admission rule

A feature may enter MVP only if it directly supports one of:

```text
- proving MPS inference
- measuring speed
- opening output in offline 3D preview
- making a launch video
```

Otherwise, defer.
