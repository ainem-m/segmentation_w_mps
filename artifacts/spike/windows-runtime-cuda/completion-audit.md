# Read-only completion audit

The requested `$subagent-implementation-manager` skill was unavailable. An
independent read-only completion subagent audited the implementation and
evidence instead. The authorized internal pure-Python `acvl-utils` wheel route
is treated as approved.

| Criterion | Result |
| --- | --- |
| Base SHA and dedicated branch/worktree | PASS |
| Windows 11 x64 machine | **FAIL** — observed Windows 10 build 19045 |
| 91-wheel binary-only hashed closure | PASS |
| Reproducible internal `acvl-utils` wheel | PASS |
| Offline hash-checked install | PASS |
| `pip check` and production imports | PASS |
| Strict CUDA tensor stages | PASS |
| Real TotalSegmentator `craniofacial_structures` | PASS |
| Requested/resolved/fallback manifest | PASS |
| Hidden-GPU typed failure/no backend | PASS |
| Invalid-index typed failure/no backend | PASS |
| Artifact size/SHA-256 | PASS, 14/14 |
| Offline preview | PASS |
| NIfTI masks | PASS, 7/7 loadable and 6/7 nonempty |
| Coordinator/runner/macOS profile targeted tests | PASS, 44 tests |
| macOS CLI and RUN_STAGE/RUN_PROGRESS target contracts | PASS |
| Full Python test suite | **FAIL**, 259 tests; 9 failures, 8 errors, 3 skips |
| `git diff --check` | PASS |
| Job Object cancellation | UNVERIFIED |
| WPF | UNVERIFIED |
| DICOM/MSVC | UNVERIFIED |
| Installer/update/rollback | UNVERIFIED |

## Overall result

**FAIL.**

The two blocking criteria are the unavailable Windows 11 x64 host and the
non-green full Python suite. The runtime/CUDA vertical slice itself passed on
the available Windows 10 x64 machine, but it must not be reported as a
Windows 11 runtime/CUDA spike pass.
