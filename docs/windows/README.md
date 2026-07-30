# Windows Port

This directory is the canonical handoff for the Windows feasibility track.
The existing macOS product remains the released implementation while the
Windows path is validated.

Read in this order:

1. [`00_WINDOWS_PORT_ADR.md`](00_WINDOWS_PORT_ADR.md) — provisional architecture
   decisions and decision gates.
2. [`01_WINDOWS_TECHNICAL_SPIKE.md`](01_WINDOWS_TECHNICAL_SPIKE.md) — bounded
   implementation and verification sequence.
3. [`02_WINDOWS_VERIFICATION_MATRIX.md`](02_WINDOWS_VERIFICATION_MATRIX.md) —
   evidence required before alpha, beta, or parity claims.
4. [`03_GPT_PRO_CONSULTATION.md`](03_GPT_PRO_CONSULTATION.md) — external
   consultation record and local disposition.
5. [`04_COORDINATOR_PROTOCOL_V1.md`](04_COORDINATOR_PROTOCOL_V1.md) — the
   implemented request/event boundary and its remaining Windows work.

## Current status

```text
Architecture direction: provisional
Platform-neutral coordinator: real Windows CUDA completion/cancel evidence recorded
Real TotalSegmentator CPU inference: unverified
Windows runtime proof: Windows 10 engineering pass; Windows 11 unverified
Windows CUDA proof: Windows 10 engineering pass; Windows 11 unverified
Windows process-tree proof: Windows 10 engineering pass; Windows 11 unverified
Windows coordinator vertical slice: Windows 10 engineering pass; Windows 11 unverified
Windows DICOM native build: Windows 10 engineering pass; Windows 11 and cross-host semantic parity unverified
Windows WPF coordinator shell: Windows 10 engineering pass; accessibility/clean Windows 11 install unverified
Windows WPF clean-DICOM intake: Windows 10 engineering pass; Windows 11, rescue, and clean packaging unverified
Windows installer/signing: unverified
```

The first Windows milestone is a technical spike, not a public release:

```text
NIfTI sample
  -> versioned coordinator request
  -> strict CUDA TotalSegmentator run
  -> progress and typed result events
  -> authoritative process-tree cancellation
  -> offline HTML preview
```

The bounded clean-DICOM slice now audits a local folder, converts one
`original_ct_geometry_ok` series with the app-private native tools, and passes
the verified NIfTI to the unchanged coordinator only after an explicit Run
action. It does not add DICOM to protocol v1 and does not implement
secondary-capture rescue.

Do not start DICOM rescue, DentalSegmentator, ToothSeg, AMD, ARM64, or a
production updater until the spike gates in the documents above have been
evaluated.
