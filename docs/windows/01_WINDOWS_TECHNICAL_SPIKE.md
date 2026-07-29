# Windows Technical Spike

Date: 2026-07-30

## Goal

Reduce the uncertainty that most affects the Windows architecture and estimate.
The spike proves one strict NIfTI/TotalSegmentator vertical slice. It does not
build the full Windows product.

Recommended staffing:

```text
two engineers for ten working days: 16–20 person-days
or
one engineer for three to four weeks
```

The two preferred ownership tracks are:

```text
Windows shell/deployment:
  WPF, Job Object, clean-machine setup, installer

runtime/backend/native:
  Python closure, CUDA proof, coordinator, C++/GDCM
```

## Pre-spike Mac work

The following work can be completed without a Windows machine:

```text
- freeze the existing CLI and artifact contracts
- inventory platform assumptions
- define coordinator protocol v1
- implement and unit-test platform-neutral protocol parsing and events
- retain macOS CLI compatibility tests
- prepare reference sample outputs and semantic comparison rules
- preserve the Windows decisions and stop conditions in version control
```

Mac checks do not satisfy any Windows runtime, CUDA, native build, installer, or
process-tree criterion.

## Spike sequence

### 1. Baseline and command surface

Record:

```text
git commit and branch
Python, .NET, CMake, Visual Studio and Windows SDK versions
Windows build and GPU information
all Swift -> Python/C++ command shapes
all result/progress/artifact contracts
```

Retain an evidence manifest with hashes. Do not store raw DICOM or patient
identifiers.

### 2. Binary dependency closure

Build the exact candidate runtime using Windows binary artifacts only.

Pass:

```text
- the runtime imports every release-scope package
- no core dependency is built from source
- repeated builds resolve the same exact versions
- the runtime works without user Python, Conda, or a compiler
```

Stop and re-estimate if PyTorch or another central compiled dependency needs a
maintained private source build.

### 3. Strict CUDA proof

The doctor must record:

```text
- Python and PyTorch provenance
- torch CUDA build
- selected device index
- device name and properties
- driver information where available
- representative FP32 Conv3d/normalization/ConvTranspose3d result
- peak CUDA allocation
- typed failure reason
```

Run a small real application inference through the production runner.

Pass:

```text
requested_policy = cuda_required
resolved_device = cuda:<index>
fallback_allowed = false
fallback_occurred = false
```

Hiding the GPU or choosing an invalid index must produce a typed failure and
must not start CPU inference.

### 4. Process-tree ownership

The Windows host must:

```text
- create a Job Object
- enable kill-on-close
- create the coordinator suspended
- assign it to the Job Object
- resume it
- send graceful cancellation first
- escalate with TerminateJobObject
```

Test a synthetic parent/child/grandchild tree and real inference children.

Pass:

```text
- no related process remains after authoritative cancellation
- no GPU process remains
- staged output is not promoted
```

### 5. Coordinator vertical slice

Use protocol v1 with:

```text
operation: capabilities | segment
input: NIfTI only
backend: TotalSegmentator only
task: craniofacial_structures only
device policy: cuda_required | cpu_required
```

Requirements:

```text
- request paths arrive over stdin
- stdout contains JSON Lines events only
- stderr contains diagnostics only
- events include protocol version, operation id and sequence
- errors use stable codes
- unknown optional fields are ignored
- unsupported protocol versions fail before opening input data
```

Do not change the existing CLI stdout-single-JSON contract.

### 6. Native DICOM build feasibility

Build the existing C++ helper with MSVC and an exact GDCM dependency. Use
absolute paths and no shell execution for child tools.

Run the existing synthetic harness plus:

```text
spaces
Japanese paths
long paths
read-only and malformed inputs
cancellation and timeout
compressed transfer syntaxes represented by the allowed corpus
```

Parity is semantic:

```text
same audit category and series grouping
same warning/error classification
equivalent NIfTI voxel array, shape, dtype, affine and orientation
```

### 7. WPF and clean-machine vertical slice

The representative shell shows:

```text
runtime state
sample selection
run
progress
cancel
typed failure
open output
```

Publish a self-contained test build and install it per-user on a clean Windows
11 standard account with no Python or .NET runtime installed.

Pass:

```text
- install without administrator approval
- first setup completes or fails recoverably
- second launch works offline after successful setup
- uninstall removes the shell but preserves user-selected outputs
```

### 8. Fault injection

Interrupt runtime/model handling at:

```text
partial download
after download before verification
mid extraction
after verification before activation
immediately after activation
```

Also test wrong hashes, corrupt archives, low disk, stale partials, concurrent
setup, and file locks.

Pass:

```text
- current points to either the old known-good runtime or the fully verified new
  runtime
- partial extraction never becomes current
- recovery is idempotent
```

## Spike stop conditions

Stop relying on the current architecture and estimate if:

```text
1. binary dependency closure is not reproducible
2. real CUDA application execution cannot be proven
3. critical process descendants escape authoritative cancellation
4. DICOM requires replacement of core classification logic
5. redistribution or model terms block the planned artifact
6. existing output contracts require model-version changes
7. setup interruption requires manual hidden-directory repair
```

Stopping means issuing a revised architecture decision, not abandoning the
Windows product.

## Expected evidence

The Windows machine should retain, outside source control when large or
machine-specific:

```text
baseline and toolchain report
platform portability inventory
command surface
dependency lock and artifact hashes
runtime manifest and SBOM
device doctor results
request/event/run manifests
process-tree before/after evidence
setup fault matrix
DICOM build and test report
output parity report
network observation
installer and clean-machine report
updated architecture decision
```

The checked-in summary must redact user paths, machine identifiers, tokens,
patient data, and private URLs.
