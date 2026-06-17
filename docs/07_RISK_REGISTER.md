# 07 Risk Register

## Risk 1: MPS operator support is incomplete

Issue:

```text
The Mac/MPS claim depends on PyTorch operators required by nnU-Net/TotalSegmentator working on MPS.
```

Mitigation:

```text
- Run ConvTranspose3D FP32 smoke test before any app work.
- Use FP32 only.
- Avoid autocast/mixed precision.
- Record PyTorch version.
- Provide CPU fallback, but never label CPU fallback as MPS.
```

Decision:

```text
If MPS smoke test fails, stop and resolve dependency version first.
```

## Risk 2: TotalSegmentator MPS path works for one task but not another

Mitigation:

```text
- Test craniofacial_structures first.
- Test teeth second.
- Publish with honest task support matrix.
```

Support matrix example:

```text
craniofacial_structures: tested MPS
teeth: experimental MPS
head_glands_cavities: deferred
```

## Risk 3: DICOM input creates support burden

Mitigation:

```text
- MVP is NIfTI-first.
- DICOM direct input is not promised.
- DICOM normalizer is deferred.
- If DICOM is attempted, pass through TotalSegmentator only.
```

Public wording:

```text
入力はまずNIfTI想定です。DICOM直接入力は環境・データにより失敗する可能性があります。
```

## Risk 4: Users expect clinical tool

Mitigation:

```text
- Non-clinical notice in app, README, output folder, and launch post.
- Do not include measurements, risk labels, reports, or planning tools.
- Do not call it diagnosis, treatment planning, implant planning, or airway analysis.
```

## Risk 5: PHI / patient data leakage

Mitigation:

```text
- Do not log PatientName, PatientID, BirthDate, AccessionNumber.
- Hash input paths if needed.
- Store logs locally.
- If failure reports are requested, ask for logs only, not DICOM files.
```

## Risk 6: App size becomes too large

Mitigation:

```text
- Use script/managed environment first.
- Do not bundle weights.
- Do not bundle Slicer.
- Start with arm64 only.
```

## Risk 7: Surface preview fails

Mitigation:

```text
- Keep raw NIfTI masks and logs even if mesh generation fails.
- Let the app regenerate only `surface-preview --case <output>` without rerunning inference.
- Keep STL exports and HTML viewer fully offline.
```

## Risk 8: Public benchmark is weak

Mitigation:

```text
- Do not overclaim.
- Publish exact machine/version/task.
- If speedup is modest, frame as “route works” rather than “爆速”.
- Invite community benchmark results.
```

## Risk 9: Dependency breakage

Mitigation:

```text
- Pin after successful tests.
- Save versions in benchmark logs.
- Provide `doctor` command in later phase.
- Keep backend isolated from user Python where possible.
```

## Risk 10: Scope creep

Mitigation:

```text
- All new feature requests go to Deferred Scope unless required for MPS benchmark, CT intake, or surface preview.
- No DICOM normalizer, ONNX, or DentalSegmentator exact mode before Mac Preview proof.
```
