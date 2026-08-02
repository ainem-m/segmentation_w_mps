# 0.4.0 Release Plan

Date: 2026-07-31

> **Withdrawn record — not a publication instruction.** Live
> `releases/stable/update.json` remains the exact 0.3.0 payload. The 0.4.0
> `release.json` is marked `channel=withdrawn`, and this historical plan must
> not be used to upload objects, change download redirects, or repoint the
> legacy stable manifest. A future 0.4.1 release is manual-DMG only until a
> verified `stable-v2` manifest is published; `stable-v2` currently returns
> HTTP 404.

## Scope

Version 0.4.0 adds research-only intra-oral PLY/STL segmentation paths,
including the independently implemented TGNet FPS-plus-boundary path.
The packaged application accepts only the specified two-checkpoint set selected
by the user and verifies its pinned SHA-256 values before inference. The
release build also includes the DICOM intake corrections already present in
the current source and privacy-preserving error-report guidance:
the app copies an allowlisted structured report and opens the existing
account-optional Google support form without uploading files or logs.

## TGNet checkpoint policy

- The application links to the designated checkpoint distribution page, but
  does not bundle, automatically download, or redistribute TGNet checkpoints.
- The user obtains `ckpts(new).zip` from that page and reviews the terms shown
  by the distributor.
- The checkpoint license remains `not-verified`.
- Every run records `source=user-provided`, `bundled_by_app=false`, both
  SHA-256 values, checkpoint roles, and strict architecture validation.
- A directory is rejected unless the required filenames, SHA-256 values,
  active keys, tensor shapes, roles, and class counts all match.

## `ckpts(new)` compatibility evidence

The product path uses only `tgnet_fps.h5` and `tgnet_bdl.h5` from the
user-selected `ckpts(new).zip` or its automatically expanded directory. These
are the application-facing names of the two pinned members extracted from the
designated archive; the application does not accept an arbitrary replacement
checkpoint through this UI.
The independently implemented decoder now matches the published main path:

- the first grouping mask is semantic class argmax other than class 0;
- both the second FPS pass and boundary model use 3,072-point crops;
- boundary candidates use the nearest sampled point's label frequency among
  its 40 neighbors, with the published 0.7 threshold;
- per-scan BatchNorm statistics, exact 24,000-point FPS, and MPS
  fallback-free execution remain mandatory.

The fixed implementation was selected on 10 isolated tuning cases and then
checked once on 10 isolated validation cases. Ground truth is never passed to
product inference. Mean golden-instance IoU was `0.956216` on tuning and
`0.942165` on validation; tooth-only FDI accuracy was `0.811767` and
`0.759422`, respectively. The evaluation summaries are identified by SHA-256
`10c08ce942db26cb267e8ff1799bfa16dee8ac4f17960903e1eec192a17d60ac`
(tuning) and
`e2ef975d37d933309ec93ead0480bea5e848d11aff8389013ba054f17fa5f775`
(validation).

The final `ios_upper.ply` regression emitted 14 unique tooth STL files with
FDI 11–17 and 21–27 in `43.97 s`, on MPS with zero fallback and zero
non-author pruning events. The result summary SHA-256 is
`5162dd40c089f7112c254a225190ee0a5a123c36100ae7c6903fa4e8ed6b37cd`.

## Release identity

- app and wheel version: `0.4.0`
- published DMG:
  `TotalSegmentator Wrapper for Mac-0.4.0-20260731-final-arm64.dmg`
- supported architecture: Apple Silicon arm64
- minimum macOS: 13.0
- intended use: research, education, and verification only

## Publication boundary

The stable update manifest, public download redirect, and website were changed
from 0.3.0 to 0.4.0 only after the exact 0.4.0 DMG passed:

1. full tests and strict MPS product smoke tests;
2. wheel, app, and DMG model-weight non-bundling checks;
3. third-party license inventory with zero unresolved entries;
4. Developer ID signing;
5. Apple notarization and stapling;
6. Gatekeeper assessment and zero-environment installation verification;
7. immutable DMG SHA-256 and release metadata generation.

All gates passed. The immutable 0.4.0 objects were uploaded first and verified
through both the R2 API and the public custom domain. The stable manifest was
then switched, followed by the two production Pages deployments. The 0.3.0
objects remain available as rollback artifacts but are no longer the public
download or update target.

## Publication decision

The application remains pre-1.0, and its established CT/DICOM paths and release
verification are not alpha-level. Therefore 0.4.0 was published as the normal
stable-channel release rather than as a separate application-wide alpha.
Intra-oral scan segmentation remains identified as a research beta feature,
and TGNet's user-provided checkpoint terms remain `not-verified`.

The notarized alpha2-labelled candidate and the published final-labelled DMG
are byte-for-byte identical. Renaming did not modify the signed artifact:

- candidate:
  `TotalSegmentator Wrapper for Mac-0.4.0-20260731-alpha2-arm64.dmg`
- published:
  `TotalSegmentator Wrapper for Mac-0.4.0-20260731-final-arm64.dmg`
- SHA-256 for both:
  `198e6cccdd35c1c18ba21317eed091c3bdd9e31487a9c5b3237c7320158b343a`

The separate historical alpha manifest was not repointed to this release.

## Published 0.4.0 evidence

- Apple notary submission: `da95ae96-7cdd-4df1-9228-569a4cde6438`
- submission status: `Accepted`
- Developer ID signing and stapler validation: passed
- DMG Gatekeeper assessment: `accepted`, source `Notarized Developer ID`
- mounted app Gatekeeper assessment: `accepted`, source
  `Notarized Developer ID`
- final DMG SHA-256:
  `198e6cccdd35c1c18ba21317eed091c3bdd9e31487a9c5b3237c7320158b343a`
- Developer ID-signed wheel SHA-256:
  `2c1ce4d96269a703a0ad9158f1da5212b29de9f4a106c8a6f17398ce5ed7017c`
- automated tests: `354 passed`, `2 skipped`
- clean-HOME DMG install verification: passed; bundled Python 3.12.8,
  Torch 2.12.0, MPS doctor without fallback, bundled DICOM normalizer,
  TotalSegmentator setup, cache isolation, and final app signature passed
- third-party license inventory unresolved count: `0`
- model checkpoint payloads in wheel, app, and DMG: `0`
- source and signed-wheel copies of the TGNet selection validator, final
  adapter, network, dispatcher, and MeshSegNet adapter: byte-for-byte identical
- stable manifest:
  `https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable/update.json`
- immutable release:
  `https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.4.0/release.json`
- public app page: `https://totalsegmentator.lacramy.com/`
- public app hub: `https://app.lacramy.com/`
- TotalSegmentator Pages production deployment:
  `38e09e05.totalsegmentator-wrapper-mac.pages.dev`
- Lacramy Apps Pages production deployment:
  `fa1ba17e.lacramy-apps.pages.dev`
- public DMG response: HTTP 200, content length `60046477`
- 0.3.0-to-0.4.0 update check: `update_available`, non-critical

During cutover, an earlier 404 existence probe remained briefly in the custom
domain's negative cache after the immutable DMG upload. The stable manifest was
immediately rolled back to 0.3.0, the no-query public DMG URL was rechecked
until it returned HTTP 200 with the expected SHA-256, and only then was the
stable manifest switched to 0.4.0 again. No broken stable download target was
left in place.

## Superseded alpha1 notarization evidence

The following evidence applies only to the earlier alpha1 artifact. It remains
recorded for traceability and does not validate the current source or alpha2
candidate.

- Apple notary submission: `02167e8b-cbf3-4017-b71e-954da6654d78`
- submission status: `Accepted`
- stapler validation: passed
- DMG Gatekeeper assessment: `accepted`, source `Notarized Developer ID`
- mounted app Gatekeeper assessment: `accepted`, source
  `Notarized Developer ID`
- final DMG SHA-256:
  `bc78112acdb51aa8ef73e19821a032a907482fb76490952a580d4e9c9a4d383b`
- Developer ID-signed wheel SHA-256:
  `1ac3855a2747805fb261b4cfec05520a4a745c925fcabec41d07b01aeef2dc1b`
- automated tests: `351 passed`, `2 skipped`
- real user-provided specified checkpoint pair on `ios_upper.ply`: MPS
  fallback-free inference passed in `43.97 s`; 14 unique FDI STL files emitted
- clean-HOME DMG install verification: passed; bundled Python 3.12.8,
  MPS doctor, bundled DICOM normalizer, cache isolation, and final app
  signature all passed
- third-party license inventory unresolved count: `0`
- model checkpoint payloads in source, wheel, app, and DMG: `0`
