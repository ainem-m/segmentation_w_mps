# Windows WPF DentalSegmentator slice verification

Scope: Windows 10 engineering evidence for the first additional model. This is
not a Windows release or a general additional-model framework.

## Result

PASS. The WPF shell selected the fixed DentalSegmentator operation, launched
the production coordinator beneath the existing Job Object supervisor, and
completed one bundled NIfTI `craniofacial_structures` inference on strict
`cuda:0`.

- requested policy: `cuda_required`
- requested index: `0`
- resolved device: `cuda:0`
- fallback allowed / occurred: `false` / `false`
- backend / task: `dentalsegmentator` / `craniofacial_structures`
- terminal event: one `operation_completed`
- coordinator OS exit: `0`
- Job processes after completion: `0`
- observed Job GPU-process survivors: `0`
- final promotion: yes, after artifact verification
- nonempty multilabel NIfTI: 132300 bytes
- offline preview: present, local assets only
- stdout: 25 valid coordinator JSONL lines; privacy scan passed

The hidden-GPU negative run emitted `cuda_unavailable`, performed no CPU
fallback, did not promote final output, and left zero Job processes.

## Checks

- full Python suite: PASS, 288 tests, 3 skipped
- focused coordinator/additional-model/WPF suite: PASS, 54 tests
- `pip check`: PASS
- production import matrix: PASS
- strict CUDA tensor smoke: PASS
- ProcessSupervisor Release build: PASS, 0 warnings, 0 errors
- CoordinatorShell Release build: PASS, 0 warnings, 0 errors
- WPF contract self-test: PASS, 23 buttons
- `git diff --check`: PASS
- macOS CLI/RUN_STAGE/RUN_PROGRESS contract regression tests: PASS in full suite

## UI comparison

The comparison view uses the four existing product reference images and keeps
the original choices visible: standard TotalSegmentator, DentalSegmentator,
Individual Teeth beta, and ToothSeg result refinement. Only the verified fixed
DentalSegmentator path is newly selectable. Individual Teeth and ToothSeg are
explicitly not enabled.

## Unverified

- Windows 11 and clean-machine installation
- production model download, licensing acceptance, rollback, and installer
- Individual Teeth and ToothSeg execution on Windows
- external UI Automation, real keyboard traversal, high contrast, and
  non-96-DPI/manual viewport coverage
- WPF model preparation/download UI
- DentalSegmentator real mid-inference cancellation
- DICOM rescue, WPF packaging/signing, update/rollback
