from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from totalsegmentator_wrapper_mac.cli import main
from totalsegmentator_wrapper_mac.rescue_pipeline import read_nifti


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_MODULE = ROOT / "native/dicom_normalizer/tests/test_normalizer.py"


class RescueCppPythonIntegrationTests(unittest.TestCase):
    def test_patterned_secondary_capture_reaches_confirmed_nifti(self) -> None:
        binary_text = os.environ.get("DICOM_NORMALIZER_BINARY", "")
        if not binary_text:
            self.skipTest("DICOM_NORMALIZER_BINARY is not set")
        binary = Path(binary_text)
        self.assertTrue(binary.is_file())

        spec = importlib.util.spec_from_file_location("dicom_fixture_helpers", FIXTURE_MODULE)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dicom_dir = root / "input"
            source_hashes: dict[Path, str] = {}
            for instance in range(1, 33):
                path = dicom_dir / f"slice-{33 - instance:03d}.dcm"
                module.write_dicom(
                    path,
                    sop=module.SC_SOP,
                    series_number=501,
                    series_uid="1.2.3.integration.rescue",
                    instance_number=instance,
                    description="AXIAL BO",
                    modality="OT",
                    image_type="DERIVED\\SECONDARY\\SCREEN SAVE\\AXIAL",
                    rows=2,
                    columns=3,
                    geometry=False,
                    secondary_capture=True,
                    slice_thickness="0.9",
                    pixel_bytes=struct.pack(
                        "<6h", *(instance * 100 + offset for offset in range(6))
                    ),
                )
                source_hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()

            stack_dir = root / "rescue" / "stack"
            environment = os.environ.copy()
            environment["TOTALSEGMENTATOR_WRAPPER_MAC_DISABLE_EXTERNAL_DICOM_TOOLS"] = "1"
            exported = subprocess.run(
                [
                    str(binary),
                    "export-rescue-stack",
                    "--dicom-dir",
                    str(dicom_dir),
                    "--series-number",
                    "501",
                    "--output",
                    str(stack_dir),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(exported.returncode, 0, exported.stderr)
            manifest = json.loads(
                (stack_dir / "source_manifest.json").read_text(encoding="utf-8")
            )
            manifest_hash = manifest["source"]["manifest_sha256"]

            estimate_json = root / "rescue" / "estimate.json"
            preview_volume = root / "rescue" / "preview.npy"
            preview_json = root / "rescue" / "preview.json"
            final_nifti = root / "rescue" / "final.nii"
            final_json = root / "rescue" / "rescue_geometry.v2.json"
            with redirect_stdout(StringIO()):
                estimate_code = main(
                    [
                        "dicom-rescue-estimate",
                        "--volume",
                        str(stack_dir / "preview_stack.npy"),
                        "--source-manifest-sha256",
                        manifest_hash,
                        "--spacing-hints",
                        "unknown,unknown,0.9",
                        "--output",
                        str(estimate_json),
                    ]
                )
                preview_code = main(
                    [
                        "dicom-rescue-preview",
                        "--volume",
                        str(stack_dir / "preview_stack.npy"),
                        "--geometry",
                        str(estimate_json),
                        "--output-volume",
                        str(preview_volume),
                        "--output",
                        str(preview_json),
                    ]
                )
            preview = json.loads(preview_json.read_text(encoding="utf-8"))
            self.assertFalse(preview["inference_started"])
            with redirect_stdout(StringIO()):
                final_code = main(
                    [
                        "dicom-rescue-finalize",
                        "--volume",
                        str(stack_dir / "preview_stack.npy"),
                        "--geometry",
                        str(preview_json),
                        "--confirmation-token",
                        preview["confirmation_token"],
                        "--output-nifti",
                        str(final_nifti),
                        "--output",
                        str(final_json),
                    ]
                )

            self.assertEqual((estimate_code, preview_code, final_code), (0, 0, 0))
            volume, readback = read_nifti(final_nifti)
            self.assertEqual(volume.shape, (3, 2, 32))
            for actual, expected in zip(
                readback["spacing_xyz"], [1.0, 1.0, 0.9], strict=True
            ):
                self.assertAlmostEqual(actual, expected, places=6)
            self.assertEqual(int(volume[2, 1, 31]), 3205)
            final = json.loads(final_json.read_text(encoding="utf-8"))
            self.assertEqual(final["workflow_status"], "finalized")
            self.assertFalse(final["inference_started"])
            self.assertTrue(final["output_validation"]["voxel_payload_consistent"])
            for path, before in source_hashes.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_partial_geometry_ct_uses_spacing_between_slices_end_to_end(self) -> None:
        binary_text = os.environ.get("DICOM_NORMALIZER_BINARY", "")
        if not binary_text:
            self.skipTest("DICOM_NORMALIZER_BINARY is not set")
        binary = Path(binary_text)
        spec = importlib.util.spec_from_file_location(
            "dicom_fixture_helpers_partial",
            FIXTURE_MODULE,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dicom_dir = root / "input"
            source_hashes: dict[Path, str] = {}
            for instance in range(1, 33):
                path = dicom_dir / f"slice-{instance:03d}.dcm"
                module.write_dicom(
                    path,
                    series_number=502,
                    series_uid="1.2.3.integration.partial.geometry",
                    instance_number=instance,
                    description="AXIAL PARTIAL GEOMETRY",
                    rows=2,
                    columns=3,
                    include_image_position=False,
                    pixel_spacing="0.4\\0.6",
                    spacing_between_slices="1.5",
                    slice_thickness="2.0",
                    pixel_bytes=struct.pack(
                        "<6h", *(instance * 100 + offset for offset in range(6))
                    ),
                )
                source_hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()

            environment = os.environ.copy()
            environment["TOTALSEGMENTATOR_WRAPPER_MAC_DISABLE_EXTERNAL_DICOM_TOOLS"] = "1"
            audit_json = root / "audit.json"
            audited = subprocess.run(
                [
                    str(binary),
                    "audit",
                    "--dicom-dir",
                    str(dicom_dir),
                    "--output",
                    str(audit_json),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(audited.returncode, 0, audited.stderr)
            audit = json.loads(audit_json.read_text(encoding="utf-8"))
            series = audit["series"][0]
            self.assertEqual(
                series["classification"]["status"],
                "geometry_rescue_candidate",
            )
            self.assertEqual(
                series["pixel_spacing_mm"],
                {"row": 0.4, "column": 0.6},
            )
            self.assertEqual(
                series["spacing_between_slices"]["values_mm"],
                [1.5],
            )

            stack_dir = root / "rescue" / "stack"
            exported = subprocess.run(
                [
                    str(binary),
                    "export-rescue-stack",
                    "--dicom-dir",
                    str(dicom_dir),
                    "--series-number",
                    "502",
                    "--output",
                    str(stack_dir),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(exported.returncode, 0, exported.stderr)
            manifest = json.loads(
                (stack_dir / "source_manifest.json").read_text(encoding="utf-8")
            )
            manifest_hash = manifest["source"]["manifest_sha256"]
            estimate_json = root / "rescue" / "estimate.json"
            preview_volume = root / "rescue" / "preview.npy"
            preview_json = root / "rescue" / "preview.json"
            final_nifti = root / "rescue" / "final.nii"
            final_json = root / "rescue" / "final.json"
            with redirect_stdout(StringIO()):
                estimate_code = main(
                    [
                        "dicom-rescue-estimate",
                        "--volume",
                        str(stack_dir / "preview_stack.npy"),
                        "--source-manifest-sha256",
                        manifest_hash,
                        "--spacing-hints",
                        "0.6,0.4,1.5",
                        "--axial-slice-step-mm",
                        "1.5",
                        "--output",
                        str(estimate_json),
                    ]
                )
                preview_code = main(
                    [
                        "dicom-rescue-preview",
                        "--volume",
                        str(stack_dir / "preview_stack.npy"),
                        "--geometry",
                        str(estimate_json),
                        "--output-volume",
                        str(preview_volume),
                        "--output",
                        str(preview_json),
                    ]
                )
            preview = json.loads(preview_json.read_text(encoding="utf-8"))
            with redirect_stdout(StringIO()):
                final_code = main(
                    [
                        "dicom-rescue-finalize",
                        "--volume",
                        str(stack_dir / "preview_stack.npy"),
                        "--geometry",
                        str(preview_json),
                        "--confirmation-token",
                        preview["confirmation_token"],
                        "--output-nifti",
                        str(final_nifti),
                        "--output",
                        str(final_json),
                    ]
                )

            self.assertEqual((estimate_code, preview_code, final_code), (0, 0, 0))
            volume, readback = read_nifti(final_nifti)
            self.assertEqual(volume.shape, (3, 2, 32))
            for actual, expected in zip(
                readback["spacing_xyz"],
                [0.6, 0.4, 1.5],
                strict=True,
            ):
                self.assertAlmostEqual(actual, expected, places=6)
            for path, before in source_hashes.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
