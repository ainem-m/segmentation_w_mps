from __future__ import annotations

import hashlib
import io
import json
import errno
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import totalsegmentator_wrapper_mac.toothseg_setup as toothseg_setup
from totalsegmentator_wrapper_mac.cli import main as cli_main
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
    def test_cli_classifies_disk_full_without_claiming_archive_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result_json = root / "result.json"
            progress_log = root / "progress.log"
            private_path = "/Users/patient/private-toothseg.zip"
            stderr = io.StringIO()
            with patch(
                "totalsegmentator_wrapper_mac.toothseg_setup.install_toothseg_model",
                side_effect=OSError(
                    errno.ENOSPC,
                    f"No space left on device: {private_path}",
                ),
            ), redirect_stderr(stderr):
                rc = cli_main(
                    [
                        "toothseg-prepare",
                        "--model-root",
                        str(root / "models"),
                        "--progress-log",
                        str(progress_log),
                        "--json",
                        str(result_json),
                    ]
                )

            payload = json.loads(result_json.read_text(encoding="utf-8"))
            self.assertEqual(rc, 1)
            self.assertEqual(payload["error_code"], "insufficient_disk_space")
            self.assertNotIn("sha256", payload)
            self.assertIn("insufficient_disk_space", stderr.getvalue())
            self.assertNotIn(private_path, stderr.getvalue())
            self.assertNotIn(private_path, json.dumps(payload))
            self.assertIn(private_path, progress_log.read_text(encoding="utf-8"))

    def test_cli_keeps_non_disk_prepare_failure_generic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result_json = root / "result.json"
            private_path = "/Users/patient/private-toothseg.zip"
            progress_log = root / "progress.log"
            stderr = io.StringIO()
            with patch(
                "totalsegmentator_wrapper_mac.toothseg_setup.install_toothseg_model",
                side_effect=RuntimeError(f"failed at {private_path}"),
            ), redirect_stderr(stderr):
                rc = cli_main(
                    [
                        "toothseg-prepare",
                        "--model-root",
                        str(root / "models"),
                        "--progress-log",
                        str(progress_log),
                        "--json",
                        str(result_json),
                    ]
                )

            payload = json.loads(result_json.read_text(encoding="utf-8"))
            self.assertEqual(rc, 1)
            self.assertEqual(payload["error_code"], "model_prepare_failed")
            self.assertNotIn(private_path, stderr.getvalue())
            self.assertNotIn(private_path, json.dumps(payload))
            self.assertIn(private_path, progress_log.read_text(encoding="utf-8"))

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
            model_url = "https://example.test/ToothSeg.zip"
            distributions_url = "https://example.test/fdi_pair_distrs.json"

            def open_fixture(request: str | object, **_kwargs: object) -> _BytesResponse:
                url = request if isinstance(request, str) else request.full_url  # type: ignore[union-attr]
                if url == model_url:
                    return _BytesResponse(archive.read_bytes(), url=model_url)
                if url == distributions_url:
                    return _BytesResponse(distributions.read_bytes(), url=distributions_url)
                raise AssertionError(f"unexpected fixture URL: {url}")

            progress_output = io.StringIO()
            with (
                redirect_stdout(progress_output),
                patch.object(toothseg_setup, "PAIR_DISTRIBUTIONS_URL", distributions_url),
                patch.object(toothseg_setup, "PAIR_DISTRIBUTIONS_SHA256", distributions_sha256),
                patch.object(toothseg_setup.urllib.request, "urlopen", side_effect=open_fixture),
            ):
                result = install_toothseg_model(
                    model_url=model_url,
                    model_zip=model_root / "ToothSeg.zip",
                    expected_md5=expected_md5,
                    nnunet_results=model_root / "nnUNet_results",
                    pair_distributions_url=distributions_url,
                    pair_distributions_sha256=distributions_sha256,
                )

            self.assertEqual(result["model_state"], "ready")
            self.assertTrue(result["md5_verified"])
            self.assertEqual(result["doi"], "10.5281/zenodo.14893540")
            self.assertEqual(result["license"], "CC-BY-4.0")
            self.assertEqual(
                result["license_url"],
                "https://creativecommons.org/licenses/by/4.0/",
            )
            self.assertEqual(
                result["creators"],
                [
                    "Fabian Isensee",
                    "Niels van Nistelrooij",
                    "Lars Krämer",
                    "Shankeeth Vinayahalingam",
                ],
            )
            self.assertFalse(result["checkpoints_modified"])
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
            for legacy_absent_key in (
                "semantic_mps_patch_size",
                "runtime_files",
                "integrity_manifest_source",
                "legacy_marker_migrated",
            ):
                marker.pop(legacy_absent_key)
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

            with (
                patch.object(toothseg_setup, "PAIR_DISTRIBUTIONS_URL", distributions_url),
                patch.object(toothseg_setup, "PAIR_DISTRIBUTIONS_SHA256", distributions_sha256),
                patch.object(toothseg_setup.urllib.request, "urlopen") as urlopen,
            ):
                migrated = install_toothseg_model(
                    model_url="https://unreachable.invalid/ToothSeg.zip",
                    model_zip=model_root / "ToothSeg.zip",
                    expected_md5=expected_md5,
                    nnunet_results=model_root / "nnUNet_results",
                    pair_distributions_url=distributions_url,
                    pair_distributions_sha256=distributions_sha256,
                )
            urlopen.assert_not_called()
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
                json.dumps(
                    {
                        "schema": toothseg_setup.PARTIAL_DOWNLOAD_SCHEMA,
                        "url": "https://example.test/ToothSeg.zip",
                        "expected_md5": "abc",
                        "total_bytes": None,
                    }
                ),
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
                with patch.object(
                    toothseg_setup.urllib.request,
                    "urlopen",
                    return_value=_BytesResponse(
                        archive.read_bytes(),
                        url="https://example.test/ToothSeg.zip",
                    ),
                ):
                    install_toothseg_model(
                        model_url="https://example.test/ToothSeg.zip",
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
            archive.writestr(
                f"{prefix}/fold_5/checkpoint_final.pth",
                _fake_pytorch_checkpoint_bytes(),
            )
            archive.writestr(f"{prefix}/fold_5/checkpoint_best.pth", b"unused")
        if unsafe:
            archive.writestr("ToothSeg/../../escaped.txt", "no")


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324 - test fixture integrity.


def _fake_pytorch_checkpoint_bytes() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_STORED) as checkpoint:
        checkpoint.writestr("archive/data.pkl", b"fixture-pickle-metadata")
        checkpoint.writestr("archive/version", b"3\n")
        checkpoint.writestr("archive/byteorder", b"little")
        checkpoint.writestr("archive/data/0", b"tensor-storage")
    return payload.getvalue()


class _BytesResponse:
    def __init__(self, payload: bytes, *, url: str) -> None:
        self.payload = payload
        self.url = url
        self.status = 200
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self) -> _BytesResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        payload, self.payload = self.payload, b""
        return payload

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url


if __name__ == "__main__":
    unittest.main()
