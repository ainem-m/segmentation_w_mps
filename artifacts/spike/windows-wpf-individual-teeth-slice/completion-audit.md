# Read-only completion audit

| Criterion | Result | Evidence |
| --- | --- | --- |
| Fixed Individual Teeth operation only | PASS | protocol and WPF contract tests |
| App-private Dataset113 model gate | PASS | archive/checkpoint hashes and missing-model test |
| No dependency update or re-resolution | PASS | existing runtime; offline no-index no-deps install |
| UI reference copy, images, and buttons retained | PASS | comparison/selected screenshots and 23-button contract |
| Swift-compatible fixed 5 mm robust route | PASS | runner contract tests and real benchmark |
| Strict requested and resolved device separated | PASS | run manifest |
| No CPU fallback | PASS | run manifest and hidden-GPU negative |
| Real TotalSegmentator `teeth` CUDA completion | PASS | WPF, supervisor, events, and artifact evidence |
| Exactly one terminal completion event | PASS | 136 JSONL events and supervisor evidence |
| Job process cleanup | PASS | active count 0 and no survivors |
| Verified staging promotion | PASS | supervisor and artifact manifest |
| Nonempty full-space/ROI NIfTI masks | PASS | artifact manifest and 54-label validation |
| Offline local preview | PASS | supervisor evidence and artifact manifest |
| stdout JSONL privacy contract | PASS | 136/136 parsed; sensitive-pattern scan empty |
| Existing macOS CLI/progress contracts | PASS | full Python suite |
| Windows 11 | UNVERIFIED | separate host required |
| Clean model packaging/distribution | UNVERIFIED | engineering cache only |
| Individual Teeth real cancellation | UNVERIFIED | new normal path did not alter Job ownership |
| ToothSeg execution | UNVERIFIED | intentionally outside this slice |
| DICOM rescue / installer / signing / update | UNVERIFIED | intentionally outside this slice |

Blocking FAIL: none.
