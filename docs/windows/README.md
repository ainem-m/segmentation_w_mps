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

## Current status

```text
Architecture direction: provisional
Windows implementation: not started
Windows runtime proof: unverified
Windows CUDA proof: unverified
Windows DICOM native build: unverified
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

Do not start DICOM rescue, DentalSegmentator, ToothSeg, AMD, ARM64, or a
production updater until the spike gates in the documents above have been
evaluated.
