# 41 Open Source License Audit

Date: 2026-07-28

## Decision

The original TotalSegmentator Wrapper for Mac source code, documentation, and
first-party application resources are offered under Apache License 2.0.
Third-party code, model checkpoints, sample data, model outputs, and marks keep
their own terms and are not relicensed by this project.

This is a repository and packaging audit, not legal advice. Before public
release, the copyright holder should confirm that the two Git author identities
used in this repository represent the same contributor and that no employment,
contract, or assignment term limits the contributor's authority to license the
work.

## First-party provenance

`git shortlog -sne --all` shows commits only from:

- `ainem-m <ainemmikkeler@gmail.com>`
- `ainem <77418538+ainem-m@users.noreply.github.com>`

No other author identity is present in Git history. Searches for copied-code
markers did not identify a wholesale copied module. The ToothSeg postprocessing
workflow follows the published upstream algorithm and should remain described
as an adaptation; upstream code and model notices must be retained whenever
code is copied or modified in the future.

## Distribution inventory

| Item | Distribution | Terms / evidence | Apache-2.0 scope |
| --- | --- | --- | --- |
| Wrapper Python, Swift, C++ and project docs | source, wheel, app | root `LICENSE` and `NOTICE` | yes |
| TotalSegmentator 2.14.0 code | installed during setup | upstream Apache-2.0 `LICENSE` | no; third party |
| TotalSegmentator model weights | downloaded during setup | task-dependent upstream terms | no; third party |
| DentalSegmentator checkpoint | first-use Zenodo download | DOI `10.5281/zenodo.10829675`, CC BY 4.0 | no; third party |
| ToothSeg code integration | dependency/integration | upstream Apache-2.0 and pinned source notice | no; third party |
| ToothSeg checkpoint | first-use Zenodo download | DOI `10.5281/zenodo.14893540`, CC BY 4.0 | no; third party |
| dcm2niix and DICOM runtime libraries | bundled in app/wheel | license files under `resources/third_party/licenses/` | no; third party |
| Python dependencies and bundled runtime | bundled/installed | generated strict license inventory | no; third party |
| Sample 1 source NIfTI | bundled | rights-holder authorization in `docs/43_OPEN_SOURCE_PUBLICATION_DECISIONS.md`; hashes in `resources/sample1/sample_manifest.json` | first-party data authorization; not source code |
| Sample 1 model outputs/images | bundled | `resources/sample1/THIRD_PARTY_NOTICES.txt` and applicable model notices | no; model-derived artifacts retain third-party attribution |
| UI manual screenshots | repository docs | first-party UI captures; some show authorized Sample 1/model output | screenshot composition is first-party; applicable model attribution remains |
| Third-party names and marks | source/docs/UI | nominative provenance references only | no |

## Model metadata verification

The Zenodo records were checked through the official Zenodo API on 2026-07-28.

DentalSegmentator record `10829675` reports:

- title `DentalSegmentator nnU-Net pretrained model for CBCT image segmentation`
- creator `Dot, Gauthier`, ORCID `0000-0003-2014-2623`
- license identifier `cc-by-4.0`
- file `Dataset112_DentalSegmentator_v100.zip`
- MD5 `b71cd5230168d28a4f71b078265b76be`

ToothSeg record `14893540` reports:

- creators Fabian Isensee, Niels van Nistelrooij, Lars Krämer, and Shankeeth Vinayahalingam
- license identifier `cc-by-4.0`
- file `ToothSeg.zip`
- MD5 `5d8dd061cce9529943567aeba3271143`

The application downloads both model archives separately; neither checkpoint is
part of the Apache-2.0 application bundle.

## TotalSegmentator task audit

The packaged application and public CLI allow exactly:

- `craniofacial_structures` — upstream task ID 115
- `teeth` — upstream task ID 113

The pinned TotalSegmentator 2.14.0 `python_api.py` defines both before its
`Commercial models` section and neither calls `show_license_info()`. The setup
also downloads task ID 297, the open 3 mm `total` model used only as the
`--robust_crop` helper. A previous note called this helper ID 298; upstream
2.14.0 shows that 298 is the 6 mm non-robust helper and 297 is correct for the
application's robust crop path.

No task from the upstream commercial-model section is exposed, predownloaded,
or included. Release tests must keep the allowlist and setup weight IDs aligned.

## Assets and publication decision

The previous 3D Slicer SampleData-derived bundle was removed before the 0.3.0
release. Sample 1 now uses a CT of the project rights holder, who explicitly
authorized publication of the derived NIfTI, model output, and preview
artifacts. The raw DICOM remains excluded from source, app, DMG, release, and
website distribution. The authorization record is
`docs/43_OPEN_SOURCE_PUBLICATION_DECISIONS.md`; artifact paths and SHA-256
values are recorded in `resources/sample1/sample_manifest.json`.

Comparison images under `resources/model_comparison/` and the web preview images
are renders derived from the authorized CT and model outputs. They remain
subject to the applicable model notices; their pixels are not relicensed as
Apache-2.0.

## Release blockers

- Build a new app and DMG. Existing 0.1.x/0.2.x immutable release objects were
  created before this license transition and must not be relabelled or reused.
- Run the strict dependency inventory with zero unresolved items.
- Verify source, wheel, app, and mounted DMG contain the wrapper `LICENSE`,
  `NOTICE`, DentalSegmentator notice, ToothSeg notice, and no first-party
  proprietary wording.
- For a public macOS binary, complete Developer ID signing, notarization,
  stapling, and Gatekeeper checks on the newly built artifact.
