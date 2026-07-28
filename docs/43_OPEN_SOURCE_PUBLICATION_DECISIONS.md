# 43 Open Source Publication Decisions

Date: 2026-07-28

This record supports Gate 1 and Gate 2 of the open-source publication runbook.
It records project-owner decisions and does not provide legal advice.

## First-party licensing authority

The project maintainer confirmed that they hold the rights to the first-party
wrapper code, documentation, and original application resources, and
authorized their publication under the Apache License 2.0. The detailed
identity and authorization evidence is retained outside the Git repository.

Decision status: confirmed for publication.

## Sample 1 source and publication surface

The source-data rights holder explicitly authorized public distribution of the
derived jaw-region NIfTI, model outputs, and preview artifacts used by Sample
1. Identity-bearing source evidence and the raw DICOM are retained outside the
Git repository. They must not be committed, bundled in the app or DMG,
uploaded to R2 or GitHub Releases, or deployed on the public pages.

The previous 3D Slicer SampleData-derived Sample 1 was removed. The replacement
bundle is recorded in `resources/sample1/sample_manifest.json` and contains:

- `input/owner_cbct_jawcrop_0p5mm.nii.gz`
  (`69fc10771a9677a3b5f1f597a5f938d8b889633044cd8da7e6221fd123607824`);
- `teeth_result/toothseg_fdi_multilabel_0p5mm.nii.gz`
  (`57fa3cc887990b347cd13dc9a6ec1a43c88d89214eed1cd9ce553efda7465996`);
- its FDI label sidecar; and
- offline binary-geometry preview artifacts whose individual hashes are
  recorded in the manifest.

Decision status: redistribution authorized; derived artifacts verified; raw
DICOM excluded from project distribution.

## Historical Sample 1

The previous Sample 1 was derived from the 3D Slicer SampleData
`CBCT-MR Head` dataset. The upstream SampleData source states that this dataset
was donated to the 3D Slicer project for unrestricted use. That notice was
recorded with the historical bundle. The historical files remain reachable in
Git history under that stated permission, but they are removed from the 0.3.0
source tree, app, website, DMG, and release artifacts. They must not be restored
as the current bundled or web sample.

## Third-party audit confirmation

The publication audit confirmed the following upstream metadata and runtime
allowlist:

- DentalSegmentator model: Zenodo DOI `10.5281/zenodo.10829675`, CC BY 4.0;
- ToothSeg model: Zenodo DOI `10.5281/zenodo.14893540`, CC BY 4.0; and
- TotalSegmentator 2.14.0: only `craniofacial_structures` (115), `teeth`
  (113), and robust-crop helper weight 297 are allowed by this wrapper.

Decision status: confirmed against the audit record.

## Release identity

- release version: `0.3.0`
- release build ID: `20260728-oss1`
- minimum supported version: `0.2.1`
- planned DMG:
  `TotalSegmentator Wrapper for Mac-0.3.0-20260729-final-arm64.dmg`

No local `cloudflare/r2/releases/0.3.0/` release directory existed when this
identity was selected. The planned public versioned `release.json` URL
returned HTTP 404 on 2026-07-28.

The checked-in public pages and redirects continue to describe and resolve the
actually published 0.2.1 artifact until the signed and notarized 0.3.0
artifact, hashes, and stable manifest pass the distribution gates. They must
be updated together at Gate 10 and must not be deployed earlier.
