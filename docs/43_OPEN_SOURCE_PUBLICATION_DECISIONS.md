# 43 Open Source Publication Decisions

Date: 2026-07-28

This record supports Gate 1 and Gate 2 of the open-source publication runbook.
It records project-owner decisions and does not provide legal advice.

## First-party licensing authority

Chino Keisuke confirmed that the Git and project identities `ainem-m`,
`ainem`, `lacramy`, and `chino keisuke` refer to the same first-party rights
holder. The rights holder authorized publication of the first-party wrapper
under the Apache License 2.0.

Decision status: confirmed for publication.

## Sample 1 source and publication surface

Chino Keisuke provided a CT of themself and explicitly authorized creation of
a public copy. The original DICOM was not modified. A non-destructive working
copy was produced with the following verified properties:

- 394 DICOM instances;
- patient name normalized to `chinokeisuke`;
- birth date and sex intentionally retained in the evidence copy;
- patient ID, institution, staff, device, acquisition date, and acquisition
  time metadata removed;
- linkage UIDs regenerated consistently;
- private-tag, forbidden-metadata, original-institution, old-UID,
  pixel-mismatch, and manifest-hash mismatch counts all zero;
- all slices visually reviewed with no burned-in text observed;
- `BurnedInAnnotation=NO`; and
- `PatientIdentityRemoved=NO`, because the evidence copy intentionally retains
  identity.

Evidence archive SHA256:
`bef04de1ab2f19a5e970b3591244b9de5139686f8690876a7e58fe473b7e6769`.

The raw public DICOM copy is source evidence only. It must not be committed,
bundled in the app or DMG, uploaded to R2 or GitHub Releases, or deployed on
the public pages. The project publication surface will contain only a derived
jaw-crop NIfTI and previews generated from that source. The derived artifacts
must receive their own provenance record and hash verification before
publication.

Decision status: redistribution authorized; raw DICOM excluded from project
distribution.

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
  `TotalSegmentator Wrapper for Mac-0.3.0-20260728-oss1-arm64.dmg`

No local `cloudflare/r2/releases/0.3.0/` release directory existed when this
identity was selected. The planned public versioned `release.json` URL
returned HTTP 404 on 2026-07-28.

The checked-in public pages and redirects continue to describe and resolve the
actually published 0.2.1 artifact until the signed and notarized 0.3.0
artifact, hashes, and stable manifest pass the distribution gates. They must
be updated together at Gate 10 and must not be deployed earlier.
