# Read-only completion audit

| Criterion | Result | Evidence |
| --- | --- | --- |
| Fixed DentalSegmentator operation only | PASS | protocol and WPF contract tests |
| App-private model gate | PASS | ready marker, archive hashes, missing-model unit test |
| No dependency update/re-resolution | PASS | existing runtime; offline no-deps source install |
| WPF model comparison matches existing references | PASS | UI screenshots and 23-button contract |
| Strict CUDA request and actual device separated | PASS | run manifest |
| No CPU fallback | PASS | run manifest and hidden-GPU negative |
| Real DentalSegmentator CUDA completion | PASS | WPF/supervisor/events evidence |
| Exactly one terminal completion event | PASS | events and supervisor evidence |
| Job/GPU process cleanup | PASS | active count 0; no observed PID survivors |
| Verified promotion and artifacts | PASS | supervisor and artifact manifest |
| Nonempty NIfTI and offline local preview | PASS | artifact manifest and preview scan |
| stdout JSONL/privacy contract | PASS | 25/25 parsed; sensitive-pattern scan empty |
| Existing macOS CLI/progress contracts | PASS | full Python suite |
| Windows 11 | UNVERIFIED | separate host required |
| Individual Teeth / ToothSeg Windows execution | UNVERIFIED | intentionally outside this slice |
| Installer/signing/update/rollback | UNVERIFIED | intentionally outside this slice |

Blocking FAIL: none.
