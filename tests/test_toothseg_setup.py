from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from totalsegmentator_wrapper_mac.toothseg_setup import (
    SEMANTIC_MPS_PATCH_SIZE,
    install_toothseg_model,
    toothseg_model_status,
)


SEMANTIC_DATASET = "Dataset121_ToothFairy2_Teeth"
INSTANCE_DATASET = "Dataset123_ToothFairy2fixed_teeth_spacing02_brd3px"
SEMANTIC_TRAINER = (
    "nnUNetTrainer_onlyMirror01_DASegOrd0__nnUNetPlans__"
    "3d_fullres_resample_torch_256_bs8_ctnorm"
)
INSTANCE_TRAINER = "nnUNetTrainer__nnUNetPlans__3d_fullres_resample_torch_192_bs8_ctnorm"


class ToothSegSetupTests(unittest.TestCase):
    def test_install_extracts_only_runtime_files_and_marks_both_branches_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "source.zip"
            _write_model_archive(archive)
            expected_md5 = _md5(archive)
            distributions = root / "fdi_pair_distrs.json"
            distributions.write_text('{"means": [], "covs": []}', encoding="utf-8")
            distributions_sha256 = hashlib.sha256(distributions.read_bytes()).hexdigest()
            model_root = root / "models"

            progress_output = io.StringIO()
            with redirect_stdout(progress_output):
                result = install_toothseg_model(
                    model_url=archive.as_uri(),
                    model_zip=model_root / "ToothSeg.zip",
                    expected_md5=expected_md5,
                    nnunet_results=model_root / "nnUNet_results",
                    pair_distributions_url=distributions.as_uri(),
                    pair_distributions_sha256=distributions_sha256,
                )

            self.assertEqual(result["model_state"], "ready")
            self.assertTrue(result["md5_verified"])
            progress_events = [
                json.loads(line.removeprefix("TOOTHSEG_PREP_PROGRESS "))
                for line in progress_output.getvalue().splitlines()
                if line.startswith("TOOTHSEG_PREP_PROGRESS ")
            ]
            download_events = [event for event in progress_events if event["stage"] == "download"]
            self.assertTrue(download_events)
            self.assertEqual(download_events[-1]["percent"], 100)
            self.assertIn("eta_seconds", download_events[-1])
            self.assertEqual(progress_events[-1]["stage"], "complete")
            self.assertFalse((model_root / "ToothSeg.zip").exists())
            for dataset, trainer in (
                (SEMANTIC_DATASET, SEMANTIC_TRAINER),
                (INSTANCE_DATASET, INSTANCE_TRAINER),
            ):
                installed = model_root / "nnUNet_results" / dataset / trainer
                self.assertTrue((installed / "dataset.json").is_file())
                self.assertTrue((installed / "plans.json").is_file())
                self.assertTrue((installed / "fold_5" / "checkpoint_final.pth").is_file())
                self.assertFalse((installed / "fold_5" / "checkpoint_best.pth").exists())
            semantic_plan = json.loads(
                (
                    model_root
                    / "nnUNet_results"
                    / SEMANTIC_DATASET
                    / SEMANTIC_TRAINER
                    / "plans.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                semantic_plan["configurations"]["3d_fullres_resample_torch_256_bs8_ctnorm"]["patch_size"],
                list(SEMANTIC_MPS_PATCH_SIZE),
            )

            status = toothseg_model_status(
                model_root=model_root,
                expected_md5=expected_md5,
                expected_pair_distributions_sha256=distributions_sha256,
            )
            self.assertEqual(status["status"], "ready")

            fixed_hash_status = toothseg_model_status(
                model_root=model_root,
                expected_md5=expected_md5,
            )
            self.assertEqual(fixed_hash_status["status"], "resumable")

            marker_path = model_root / "nnUNet_results" / ".toothseg_model_ready.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker.pop("semantic_mps_patch_size")
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            semantic_plan["configurations"]["3d_fullres_resample_torch_256_bs8_ctnorm"]["patch_size"] = [
                256,
                256,
                256,
            ]
            (
                model_root
                / "nnUNet_results"
                / SEMANTIC_DATASET
                / SEMANTIC_TRAINER
                / "plans.json"
            ).write_text(json.dumps(semantic_plan), encoding="utf-8")

            migrated = install_toothseg_model(
                model_url="https://unreachable.invalid/ToothSeg.zip",
                model_zip=model_root / "ToothSeg.zip",
                expected_md5=expected_md5,
                nnunet_results=model_root / "nnUNet_results",
                pair_distributions_url="https://unreachable.invalid/fdi_pair_distrs.json",
                pair_distributions_sha256=distributions_sha256,
            )
            self.assertTrue(migrated["reused_existing_checkpoints"])
            self.assertFalse(migrated["downloaded"])
            self.assertEqual(
                toothseg_model_status(
                    model_root=model_root,
                    expected_md5=expected_md5,
                    expected_pair_distributions_sha256=distributions_sha256,
                )["status"],
                "ready",
            )

    def test_partial_download_is_reported_as_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_root = Path(tmp)
            partial = model_root / "ToothSeg.zip.part"
            partial.write_bytes(b"partial")
            partial.with_name("ToothSeg.zip.part.json").write_text(
                json.dumps({"url": "https://example.test/ToothSeg.zip", "expected_md5": "abc"}),
                encoding="utf-8",
            )

            status = toothseg_model_status(model_root=model_root, expected_md5="abc")

            self.assertEqual(status["status"], "resumable")
            self.assertEqual(status["model_state"], "resumable")

    def test_archive_with_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "source.zip"
            _write_model_archive(archive, unsafe=True)
            expected_md5 = _md5(archive)

            with self.assertRaisesRegex(RuntimeError, "unsafe ToothSeg archive member"):
                install_toothseg_model(
                    model_url=archive.as_uri(),
                    model_zip=root / "models" / "ToothSeg.zip",
                    expected_md5=expected_md5,
                    nnunet_results=root / "models" / "nnUNet_results",
                )


def _write_model_archive(path: Path, *, unsafe: bool = False) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for dataset, trainer in (
            (SEMANTIC_DATASET, SEMANTIC_TRAINER),
            (INSTANCE_DATASET, INSTANCE_TRAINER),
        ):
            prefix = f"ToothSeg/{dataset}/{trainer}"
            archive.writestr(f"{prefix}/dataset.json", "{}")
            if dataset == SEMANTIC_DATASET:
                plans = {
                    "configurations": {
                        "3d_fullres_resample_torch_256_bs8_ctnorm": {
                            "inherits_from": "3d_fullres_resample_torch_256_bs8"
                        },
                        "3d_fullres_resample_torch_256_bs8": {"patch_size": [256, 256, 256]},
                    }
                }
            else:
                plans = {"configurations": {}}
            archive.writestr(f"{prefix}/plans.json", json.dumps(plans))
            archive.writestr(f"{prefix}/fold_5/checkpoint_final.pth", b"weights")
            archive.writestr(f"{prefix}/fold_5/checkpoint_best.pth", b"unused")
        if unsafe:
            archive.writestr("ToothSeg/../../escaped.txt", "no")


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324 - test fixture integrity.


if __name__ == "__main__":
    unittest.main()
