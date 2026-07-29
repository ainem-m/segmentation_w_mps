# Read-only completion audit

The requested `$subagent-implementation-manager` skill was unavailable. An
independent read-only completion subagent audited commit
`605da9797b410e352447ae602f4bed229a1d9a34` and the evidence instead. The
authorized internal pure-Python `acvl-utils` wheel route is treated as
approved.

| Criterion | Result |
| --- | --- |
| Base SHA and dedicated branch/worktree | PASS |
| Windows portability change scope | PASS, 4 files |
| LF policy scope | PASS, 10 manifest-hashed text assets |
| Full Python test suite | PASS, 259 tests, 3 skipped |
| Focused Windows/CUDA/macOS regression | PASS, 94 tests, 1 skipped |
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
| macOS CLI and RUN_STAGE/RUN_PROGRESS target contracts | PASS |
| `git diff --check` | PASS |
| Windows 11 x64 machine | **FAIL**, observed Windows 10 build 19045 |
| Job Object cancellation | UNVERIFIED |
| WPF | UNVERIFIED |
| DICOM/MSVC | UNVERIFIED |
| Installer/update/rollback | UNVERIFIED |

## Overall result

**FAIL.**

The runtime/CUDA vertical slice and the complete Python suite pass on the
available Windows 10 x64 host. The only remaining blocking criterion is that
the requested Windows 11 x64 host was not available, so this must not be
reported as a Windows 11 runtime/CUDA spike pass.
