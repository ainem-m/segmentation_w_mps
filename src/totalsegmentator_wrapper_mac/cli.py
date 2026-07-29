from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from totalsegmentator_wrapper_mac.benchmark import write_json
from totalsegmentator_wrapper_mac.dicom_normalizer_bridge import (
    inspect_dicom_normalizer,
    run_dicom_normalizer_audit,
    run_dicom_normalizer_convert_clean,
    run_dicom_normalizer_export_rescue_stack,
    run_dicom_normalizer_prepare_rescue,
    run_dicom_normalizer_prepare_viewer_export,
)
from totalsegmentator_wrapper_mac.device import smoke_test_mps_convtranspose3d


TASKS = ("craniofacial_structures", "teeth")
BACKENDS = ("totalsegmentator", "dentalsegmentator", "toothseg")
DEVICES = ("auto", "mps", "cpu")
SMOOTH_PRESET_NAMES = ("none", "slicer_like", "medium", "strong")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="totalsegmentator-wrapper-mac")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local Python, torch, and MPS state.")
    doctor.add_argument("--json", type=Path, default=None, help="Optional JSON output path.")

    update_check = subparsers.add_parser(
        "update-check",
        help="Manually check a static HTTPS update manifest.",
    )
    update_check.add_argument("--manifest-url", required=True)
    update_check.add_argument("--json", type=Path, default=None, help="Optional JSON output path.")
    update_check.add_argument("--current-version", default=None)
    update_check.add_argument("--timeout-sec", type=float, default=5.0)
    update_check.add_argument(
        "--allowed-link-host",
        action="append",
        default=None,
        help="Optional additional HTTPS host allowed for update/release links.",
    )

    setup = subparsers.add_parser(
        "setup",
        help="Create the permission-safe local runtime under Application Support.",
    )
    setup.add_argument("--json", required=True, type=Path, help="Setup result JSON output path.")
    setup.add_argument("--wheel", type=Path, default=None, help="Packaged wheel to install.")
    setup.add_argument("--python", dest="python_executable", type=Path, default=None)
    setup.add_argument("--constraints", type=Path, default=None, help="Pinned pip constraints for runtime setup.")
    setup.add_argument("--bundle-manifest", type=Path, default=None, help="Packaged app setup manifest to record in setup state.")
    setup.add_argument(
        "--allow-network",
        action="store_true",
        help="Allow pip to download runtime dependencies during setup.",
    )
    setup.add_argument("--dry-run", action="store_true")
    setup.add_argument("--skip-install", action="store_true", help="Build setup state without running venv/pip.")
    setup.add_argument("--skip-mps-check", action="store_true", help="Skip installed doctor/MPS gate.")
    setup.add_argument(
        "--skip-dentalseg-model",
        action="store_true",
        help="Defer DentalSegmentator model preparation to dentalseg-prepare.",
    )
    setup.add_argument("--use-existing-env", action="store_true", help="Reuse the App Support venv if already bootstrapped.")
    setup.add_argument("--progress-log", type=Path, default=None, help="Append user-facing setup progress lines here.")

    dentalseg_status = subparsers.add_parser(
        "dentalseg-status",
        help="Report the machine-readable DentalSegmentator model preparation state.",
    )
    dentalseg_status.add_argument("--model-root", required=True, type=Path)
    dentalseg_status.add_argument("--json", required=True, type=Path)

    dentalseg_prepare = subparsers.add_parser(
        "dentalseg-prepare",
        help="Prepare the DentalSegmentator model without running inference.",
    )
    dentalseg_prepare.add_argument("--model-root", required=True, type=Path)
    dentalseg_prepare.add_argument("--json", required=True, type=Path)
    dentalseg_prepare.add_argument("--progress-log", required=True, type=Path)

    toothseg_status = subparsers.add_parser(
        "toothseg-status",
        help="Report the machine-readable ToothSeg model preparation state.",
    )
    toothseg_status.add_argument("--model-root", required=True, type=Path)
    toothseg_status.add_argument("--json", required=True, type=Path)

    toothseg_prepare = subparsers.add_parser(
        "toothseg-prepare",
        help="Prepare both ToothSeg nnU-Net branches without running inference.",
    )
    toothseg_prepare.add_argument("--model-root", required=True, type=Path)
    toothseg_prepare.add_argument("--json", required=True, type=Path)
    toothseg_prepare.add_argument("--progress-log", required=True, type=Path)

    dicom_audit = subparsers.add_parser(
        "dicom-audit",
        help="Classify DICOM series by metadata without conversion or pixel-data rescue.",
    )
    dicom_audit.add_argument("--dicom-dir", required=True, type=Path)
    dicom_audit.add_argument("--json", type=Path, default=None, help="Optional JSON output path.")

    dicom_normalizer = subparsers.add_parser(
        "dicom-normalizer-audit",
        help="Launch the external C++ DICOM normalizer audit binary.",
    )
    dicom_normalizer.add_argument("--dicom-dir", required=True, type=Path)
    dicom_normalizer.add_argument("--output", required=True, type=Path)
    dicom_normalizer.add_argument("--binary", type=Path, default=None)
    dicom_normalizer.add_argument("--timeout-sec", type=int, default=300)

    dicom_convert = subparsers.add_parser(
        "dicom-normalizer-convert-clean",
        help="Launch the external C++ DICOM normalizer clean CT conversion path.",
    )
    dicom_convert.add_argument("--dicom-dir", required=True, type=Path)
    dicom_convert.add_argument("--output", required=True, type=Path)
    dicom_convert.add_argument("--series-number", type=int, default=None)
    dicom_convert.add_argument("--series-key", default=None)
    dicom_convert.add_argument("--binary", type=Path, default=None)
    dicom_convert.add_argument("--dcm2niix", type=Path, default=None)
    dicom_convert.add_argument("--timeout-sec", type=int, default=900)

    nifti_preview = subparsers.add_parser(
        "nifti-preview",
        help="Write non-inference center MPR images and empty-volume metadata.",
    )
    nifti_preview.add_argument("--input", required=True, type=Path)
    nifti_preview.add_argument("--output-dir", required=True, type=Path)
    nifti_preview.add_argument("--output-json", required=True, type=Path)

    dicom_rescue = subparsers.add_parser(
        "dicom-normalizer-prepare-rescue",
        help="Launch the external C++ DICOM normalizer secondary-capture rescue path.",
    )
    dicom_rescue.add_argument("--dicom-dir", required=True, type=Path)
    dicom_rescue.add_argument("--output", required=True, type=Path)
    dicom_rescue.add_argument("--series-number", type=int, default=None)
    dicom_rescue.add_argument("--series-key", default=None)
    dicom_rescue.add_argument("--patched-spacing", required=True)
    dicom_rescue.add_argument("--binary", type=Path, default=None)
    dicom_rescue.add_argument("--dcm2niix", type=Path, default=None)
    dicom_rescue.add_argument("--timeout-sec", type=int, default=900)

    dicom_export_rescue_stack = subparsers.add_parser(
        "dicom-normalizer-export-rescue-stack",
        help="Export a deterministic decoded Secondary Capture stack and safe source manifest.",
    )
    dicom_export_rescue_stack.add_argument("--dicom-dir", required=True, type=Path)
    dicom_export_rescue_stack.add_argument("--output", required=True, type=Path)
    dicom_export_rescue_stack.add_argument("--series-number", type=int, default=None)
    dicom_export_rescue_stack.add_argument("--series-key", default=None)
    dicom_export_rescue_stack.add_argument("--binary", type=Path, default=None)
    dicom_export_rescue_stack.add_argument("--timeout-sec", type=int, default=900)

    dicom_viewer_export = subparsers.add_parser(
        "dicom-normalizer-prepare-viewer-export",
        help="Launch the external C++ DICOM normalizer viewer/MPR export rescue path.",
    )
    dicom_viewer_export.add_argument("--dicom-dir", required=True, type=Path)
    dicom_viewer_export.add_argument("--output", required=True, type=Path)
    dicom_viewer_export.add_argument("--series-number", type=int, default=None)
    dicom_viewer_export.add_argument("--series-key", default=None)
    dicom_viewer_export.add_argument("--group-id", required=True)
    dicom_viewer_export.add_argument("--binary", type=Path, default=None)
    dicom_viewer_export.add_argument("--dcm2niix", type=Path, default=None)
    dicom_viewer_export.add_argument("--timeout-sec", type=int, default=900)

    rescue_estimate = subparsers.add_parser(
        "dicom-rescue-estimate",
        help="Create an editable spacing candidate from a native-decoded XYZ volume.",
    )
    rescue_estimate.add_argument("--volume", required=True, type=Path)
    rescue_estimate.add_argument("--source-manifest-sha256", required=True)
    rescue_estimate.add_argument(
        "--spacing-hints",
        default="unknown,unknown,unknown",
        help="X,Y,Z millimetres; use 'unknown' for unavailable axes.",
    )
    rescue_estimate.add_argument("--evidence", type=Path, default=None)
    rescue_estimate.add_argument("--coronal-reference", type=Path, default=None)
    rescue_estimate.add_argument("--sagittal-reference", type=Path, default=None)
    rescue_estimate.add_argument("--axial-slice-step-mm", type=float, default=None)
    rescue_estimate.add_argument("--coronal-count", type=int, default=None)
    rescue_estimate.add_argument("--coronal-slice-step-mm", type=float, default=None)
    rescue_estimate.add_argument("--sagittal-count", type=int, default=None)
    rescue_estimate.add_argument("--sagittal-slice-step-mm", type=float, default=None)
    rescue_estimate.add_argument("--max-registration-evaluations", type=int, default=64)
    rescue_estimate.add_argument("--output", required=True, type=Path)

    rescue_preview = subparsers.add_parser(
        "dicom-rescue-preview",
        help="Apply confirmed rescue geometry to a preview volume without inference.",
    )
    rescue_preview.add_argument("--volume", required=True, type=Path)
    rescue_preview.add_argument("--geometry", required=True, type=Path)
    rescue_preview.add_argument("--output-volume", required=True, type=Path)
    rescue_preview.add_argument("--output", required=True, type=Path)

    rescue_finalize = subparsers.add_parser(
        "dicom-rescue-finalize",
        help="Write and read back pseudo-NIfTI after explicit geometry confirmation.",
    )
    rescue_finalize.add_argument("--volume", required=True, type=Path)
    rescue_finalize.add_argument("--geometry", required=True, type=Path)
    rescue_finalize.add_argument("--confirmation-token", required=True)
    rescue_finalize.add_argument("--output-nifti", required=True, type=Path)
    rescue_finalize.add_argument("--output", required=True, type=Path)

    run = subparsers.add_parser("run", help="Run TotalSegmentator and write case logs.")
    run.add_argument("--input", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--backend", choices=BACKENDS, default="totalsegmentator")
    run.add_argument("--task", choices=TASKS, default="craniofacial_structures")
    run.add_argument("--device", choices=DEVICES, default="auto")
    run.add_argument("--execution-profile", choices=("macos-app",), default=None)
    run.add_argument("--require-mps", action="store_true")
    run.add_argument(
        "--result-json",
        type=Path,
        default=None,
        help="Optional redacted app-facing run result JSON.",
    )
    run.add_argument("--totalseg-bin", default="TotalSegmentator")
    run.add_argument("--totalseg-home", type=Path, default=None)
    run.add_argument("--totalseg-weights", type=Path, default=None)
    run.add_argument("--dentalseg-bin", default="nnUNetv2_predict")
    run.add_argument("--dentalseg-model-dir", type=Path, default=None)
    run.add_argument("--dentalseg-model-zip", type=Path, default=None)
    run.add_argument("--dentalseg-nnunet-raw", type=Path, default=None)
    run.add_argument("--dentalseg-nnunet-preprocessed", type=Path, default=None)
    run.add_argument("--dentalseg-nnunet-results", type=Path, default=None)
    run.add_argument("--dentalseg-dataset-id", default="112")
    run.add_argument("--dentalseg-configuration", default="3d_fullres")
    run.add_argument("--dentalseg-trainer", default="nnUNetTrainer")
    run.add_argument("--dentalseg-plans", default="nnUNetPlans")
    run.add_argument(
        "--dentalseg-fold",
        action="append",
        default=None,
        help="Fold to use for DentalSegmentator nnU-Net inference. Repeat to ensemble. Defaults to 0.",
    )
    run.add_argument("--dentalseg-disable-tta", action="store_true")
    run.add_argument("--dentalseg-not-on-device", action="store_true")
    run.add_argument("--dentalseg-npp", type=int, default=1)
    run.add_argument("--dentalseg-nps", type=int, default=1)
    run.add_argument("--dentalseg-timeout-sec", type=int, default=7200)
    run.add_argument("--toothseg-bin", default="nnUNetv2_predict")
    run.add_argument("--toothseg-nnunet-results", type=Path, default=None)
    run.add_argument("--toothseg-timeout-sec", type=int, default=7200)
    run.add_argument(
        "--toothseg-refine",
        action="store_true",
        help="Run ToothSeg explicit refine path (second-stage only).",
    )
    run.add_argument("--no-copy-input", action="store_true")
    run.add_argument(
        "--robust-crop",
        action="store_true",
        help="Pass TotalSegmentator --robust_crop for craniofacial preflight cases.",
    )
    run.add_argument(
        "--higher-order-resampling",
        action="store_true",
        help="Pass TotalSegmentator --higher_order_resampling for smoother segmentation resampling.",
    )
    run.add_argument("--experimental-teeth", action="store_true")
    run.add_argument("--teeth-dry-run", action="store_true")
    run.add_argument("--teeth-timeout-sec", type=int, default=3600)
    run.add_argument("--teeth-crop-margin-mm", type=float, default=20.0)
    run.add_argument("--teeth-craniofacial-case", type=Path, default=None)
    run.add_argument("--teeth-force-split", action="store_true")
    run.add_argument(
        "--teeth-robust-craniofacial-preflight",
        action="store_true",
        help="Use TotalSegmentator --robust_crop for the internal craniofacial preflight.",
    )
    run.add_argument(
        "--skip-device-check",
        action="store_true",
        help="Skip MPS smoke test. Intended only for tests with fake runners.",
    )

    benchmark = subparsers.add_parser("benchmark", help="Run CPU and MPS once for one task.")
    benchmark.add_argument("--input", required=True, type=Path)
    benchmark.add_argument("--output", required=True, type=Path)
    benchmark.add_argument("--task", choices=TASKS, default="craniofacial_structures")
    benchmark.add_argument("--totalseg-bin", default="TotalSegmentator")
    benchmark.add_argument("--totalseg-home", type=Path, default=None)
    benchmark.add_argument("--totalseg-weights", type=Path, default=None)
    benchmark.add_argument(
        "--higher-order-resampling",
        action="store_true",
        help="Pass TotalSegmentator --higher_order_resampling for both CPU and MPS runs.",
    )
    benchmark.add_argument("--skip-device-check", action="store_true")

    summary = subparsers.add_parser("summary", help="Summarize a completed case output folder.")
    summary.add_argument("--case", required=True, type=Path)
    summary.add_argument("--output", type=Path, default=None)
    summary.add_argument("--format", choices=("markdown", "text"), default="markdown")

    surface = subparsers.add_parser(
        "surface-preview",
        help="Generate smoothed STL files and an offline HTML surface preview.",
    )
    surface.add_argument("--case", required=True, type=Path)
    surface.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Optional input labelmap. Defaults to the case teeth fullspace labelmap.",
    )
    surface.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to <case>/surface_preview.",
    )
    surface.add_argument("--min-voxels", type=int, default=1)
    surface.add_argument(
        "--preview-step-size",
        type=int,
        default=2,
        help="Marching-cubes step size for embedded browser preview meshes.",
    )
    surface.add_argument(
        "--smooth-preset",
        choices=SMOOTH_PRESET_NAMES,
        default="slicer_like",
    )
    surface.add_argument("--smooth-iterations", type=int, default=None)
    surface.add_argument("--smooth-lambda", dest="smooth_lambda", type=float, default=None)
    surface.add_argument("--smooth-mu", type=float, default=None)
    surface_mode = surface.add_mutually_exclusive_group()
    surface_mode.add_argument(
        "--defer-stl",
        action="store_true",
        help="Return after the browser preview is ready and finish detailed STL files in the background.",
    )
    surface_mode.add_argument("--stl-only", action="store_true", help=argparse.SUPPRESS)

    slicer_export = subparsers.add_parser(
        "slicer-export",
        help="Write a file-only 3D Slicer import folder without launching Slicer.",
    )
    slicer_export.add_argument("--case", required=True, type=Path)
    slicer_export.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Optional source CT NIfTI to copy into the Slicer import folder.",
    )
    slicer_export.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to <case>/slicer_export.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        result = smoke_test_mps_convtranspose3d().to_dict()
        result["dicom_normalizer"] = inspect_dicom_normalizer()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.json:
            write_json(args.json, result)
        return 0 if result["status"] == "pass" else 1

    if args.command == "update-check":
        from totalsegmentator_wrapper_mac import __version__
        from totalsegmentator_wrapper_mac.update_check import check_for_update

        result = check_for_update(
            manifest_url=args.manifest_url,
            current_version=args.current_version or __version__,
            timeout_sec=args.timeout_sec,
            allowed_link_hosts=set(args.allowed_link_host or []) or None,
        ).to_dict()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.json:
            write_json(args.json, result)
        return 0 if result["status"] != "failed" else 1

    if args.command == "setup":
        from totalsegmentator_wrapper_mac.setup_manager import run_setup

        result = run_setup(
            python_executable=args.python_executable,
            wheel=args.wheel,
            constraints=args.constraints,
            bundle_manifest=args.bundle_manifest,
            allow_network=args.allow_network,
            dry_run=args.dry_run,
            skip_install=args.skip_install,
            skip_mps_check=args.skip_mps_check,
            use_existing_env=args.use_existing_env,
            skip_dentalseg_model=args.skip_dentalseg_model,
            progress_log=args.progress_log,
        )
        payload = result.to_dict()
        if os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_SUPPRESS_STDOUT_JSON") != "1":
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        write_json(args.json, payload)
        return 0 if result.status == "success" else 1

    if args.command in {"dentalseg-status", "dentalseg-prepare"}:
        from totalsegmentator_wrapper_mac.dentalsegmentator_setup import (
            dentalsegmentator_model_status,
            install_dentalsegmentator_model,
        )
        from totalsegmentator_wrapper_mac.setup_manager import (
            DENTALSEGMENTATOR_DATASET_ID,
            DENTALSEGMENTATOR_DATASET_NAME,
            DENTALSEGMENTATOR_MODEL_MD5,
            DENTALSEGMENTATOR_MODEL_URL,
        )

        model_root = args.model_root.expanduser().resolve()
        if args.command == "dentalseg-status":
            payload = dentalsegmentator_model_status(
                model_root=model_root,
                expected_md5=DENTALSEGMENTATOR_MODEL_MD5,
                dataset_id=DENTALSEGMENTATOR_DATASET_ID,
                dataset_name=DENTALSEGMENTATOR_DATASET_NAME,
            )
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            write_json(args.json, payload)
            return 0 if payload["status"] != "failed" else 1

        args.progress_log.parent.mkdir(parents=True, exist_ok=True)
        args.progress_log.write_text("DENTALSEG_PROGRESS status=running\n", encoding="utf-8")
        try:
            payload = install_dentalsegmentator_model(
                model_url=DENTALSEGMENTATOR_MODEL_URL,
                model_zip=model_root / "Dataset112_DentalSegmentator_v100.zip",
                expected_md5=DENTALSEGMENTATOR_MODEL_MD5,
                nnunet_results=model_root / "nnUNet_results",
                nnunet_raw=model_root / "nnUNet_raw",
                nnunet_preprocessed=model_root / "nnUNet_preprocessed",
                dataset_id=DENTALSEGMENTATOR_DATASET_ID,
                dataset_name=DENTALSEGMENTATOR_DATASET_NAME,
            )
        except Exception as exc:  # noqa: BLE001
            payload = {
                "schema": "totalsegmentator_wrapper_mac.dentalsegmentator_model_setup.v1",
                "status": "failed",
                "model_state": "failed",
                "error_code": "model_prepare_failed",
                "safe_reason": "DentalSegmentator model preparation did not complete.",
                "mps_state": "not_applicable",
                "occurred_at": datetime.now(UTC).isoformat(),
            }
            with args.progress_log.open("a", encoding="utf-8") as handle:
                handle.write("DENTALSEG_PROGRESS status=failed\n")
            print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
            write_json(args.json, payload)
            return 1
        with args.progress_log.open("a", encoding="utf-8") as handle:
            handle.write("DENTALSEG_PROGRESS status=success\n")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        write_json(args.json, payload)
        return 0

    if args.command in {"toothseg-status", "toothseg-prepare"}:
        from totalsegmentator_wrapper_mac.setup_manager import (
            TOOTHSEG_MODEL_FILENAME,
            TOOTHSEG_MODEL_MD5,
            TOOTHSEG_MODEL_URL,
        )
        from totalsegmentator_wrapper_mac.toothseg_setup import (
            install_toothseg_model,
            toothseg_model_status,
        )

        model_root = args.model_root.expanduser().resolve()
        if args.command == "toothseg-status":
            payload = toothseg_model_status(
                model_root=model_root,
                expected_md5=TOOTHSEG_MODEL_MD5,
            )
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            write_json(args.json, payload)
            return 0 if payload["status"] != "failed" else 1

        args.progress_log.parent.mkdir(parents=True, exist_ok=True)
        args.progress_log.write_text("TOOTHSEG_PROGRESS status=running\n", encoding="utf-8")
        try:
            payload = install_toothseg_model(
                model_url=TOOTHSEG_MODEL_URL,
                model_zip=model_root / TOOTHSEG_MODEL_FILENAME,
                expected_md5=TOOTHSEG_MODEL_MD5,
                nnunet_results=model_root / "nnUNet_results",
            )
        except Exception:  # noqa: BLE001
            payload = {
                "schema": "totalsegmentator_wrapper_mac.toothseg_model_setup.v1",
                "status": "failed",
                "model_state": "failed",
                "error_code": "model_prepare_failed",
                "safe_reason": "ToothSeg model preparation did not complete.",
                "mps_state": "not_applicable",
                "occurred_at": datetime.now(UTC).isoformat(),
            }
            with args.progress_log.open("a", encoding="utf-8") as handle:
                handle.write("TOOTHSEG_PROGRESS status=failed\n")
            print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
            write_json(args.json, payload)
            return 1
        with args.progress_log.open("a", encoding="utf-8") as handle:
            handle.write("TOOTHSEG_PROGRESS status=success\n")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        write_json(args.json, payload)
        return 0

    if args.command == "dicom-audit":
        from totalsegmentator_wrapper_mac.dicom_audit import audit_dicom_directory, write_audit_json

        result = audit_dicom_directory(args.dicom_dir)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.json:
            write_audit_json(args.json, result)
        return 0

    if args.command == "dicom-normalizer-audit":
        result = run_dicom_normalizer_audit(
            dicom_dir=args.dicom_dir,
            output_json=args.output,
            binary=args.binary,
            timeout_sec=args.timeout_sec,
        )
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.status == "success" else result.returncode or 1

    if args.command == "dicom-normalizer-convert-clean":
        result = run_dicom_normalizer_convert_clean(
            dicom_dir=args.dicom_dir,
            output_dir=args.output,
            series_number=args.series_number,
            series_key=args.series_key,
            binary=args.binary,
            dcm2niix=args.dcm2niix,
            timeout_sec=args.timeout_sec,
        )
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.status == "success" else result.returncode or 1

    if args.command == "nifti-preview":
        from totalsegmentator_wrapper_mac.nifti_preview import write_nifti_preview

        result = write_nifti_preview(
            input_path=args.input,
            output_dir=args.output_dir,
            output_json=args.output_json,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.command == "dicom-normalizer-prepare-rescue":
        result = run_dicom_normalizer_prepare_rescue(
            dicom_dir=args.dicom_dir,
            output_dir=args.output,
            series_number=args.series_number,
            series_key=args.series_key,
            patched_spacing=args.patched_spacing,
            binary=args.binary,
            dcm2niix=args.dcm2niix,
            timeout_sec=args.timeout_sec,
        )
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.status == "success" else result.returncode or 1

    if args.command == "dicom-normalizer-export-rescue-stack":
        result = run_dicom_normalizer_export_rescue_stack(
            dicom_dir=args.dicom_dir,
            output_dir=args.output,
            series_number=args.series_number,
            series_key=args.series_key,
            binary=args.binary,
            timeout_sec=args.timeout_sec,
        )
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.status == "success" else result.returncode or 1

    if args.command == "dicom-normalizer-prepare-viewer-export":
        result = run_dicom_normalizer_prepare_viewer_export(
            dicom_dir=args.dicom_dir,
            output_dir=args.output,
            series_number=args.series_number,
            series_key=args.series_key,
            group_id=args.group_id,
            binary=args.binary,
            dcm2niix=args.dcm2niix,
            timeout_sec=args.timeout_sec,
        )
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.status == "success" else result.returncode or 1

    if args.command in {
        "dicom-rescue-estimate",
        "dicom-rescue-preview",
        "dicom-rescue-finalize",
    }:
        from totalsegmentator_wrapper_mac.rescue_geometry import safe_rescue_error
        from totalsegmentator_wrapper_mac.rescue_pipeline import (
            PIPELINE_VERSION,
            RescuePipelineError,
            create_estimate,
            create_preview,
            finalize_rescue,
            geometry_values_from_mapping,
            load_decoded_reference_image,
            load_decoded_volume,
            write_decoded_volume,
            write_metadata,
            write_preview_artifacts,
        )
        from totalsegmentator_wrapper_mac.rescue_estimation import estimate_rescue_spacing

        source_hash: str | None = getattr(args, "source_manifest_sha256", None)
        stage = args.command.removeprefix("dicom-rescue-")
        try:
            volume = load_decoded_volume(args.volume)
            if args.command == "dicom-rescue-estimate":
                raw_hints = [value.strip() for value in args.spacing_hints.split(",")]
                if len(raw_hints) != 3:
                    raise RescuePipelineError("spacing hints must contain X,Y,Z")
                hints = tuple(
                    None if value.lower() in {"", "unknown", "none", "null"} else float(value)
                    for value in raw_hints
                )
                evidence: dict[str, object] = {}
                if args.evidence is not None:
                    loaded_evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
                    if not isinstance(loaded_evidence, dict):
                        raise RescuePipelineError("rescue evidence must be a JSON object")
                    evidence = loaded_evidence
                references = {}
                if args.coronal_reference is not None:
                    references["coronal"] = load_decoded_reference_image(
                        args.coronal_reference
                    )
                if args.sagittal_reference is not None:
                    references["sagittal"] = load_decoded_reference_image(
                        args.sagittal_reference
                    )
                try:
                    metadata = estimate_rescue_spacing(
                        volume,
                        source_manifest_sha256=args.source_manifest_sha256,
                        spacing_hints_xyz=hints,
                        reference_images=references,
                        axial_slice_step_mm=args.axial_slice_step_mm,
                        coronal_count=args.coronal_count,
                        coronal_slice_step_mm=args.coronal_slice_step_mm,
                        sagittal_count=args.sagittal_count,
                        sagittal_slice_step_mm=args.sagittal_slice_step_mm,
                        max_registration_evaluations=args.max_registration_evaluations,
                        used_series=evidence.get("used_series", ()),
                        used_dicom_tags=evidence.get("used_dicom_tags", ()),
                    )
                except (RescuePipelineError, ValueError, TypeError, KeyError, AttributeError):
                    sources = [
                        str(value)
                        for value in evidence.get("spacing_sources", ())
                        if isinstance(value, str)
                    ]
                    sources.append("automatic_estimation_failed_manual_fallback")
                    metadata = create_estimate(
                        volume,
                        spacing_hints_xyz=hints,
                        source_manifest_sha256=args.source_manifest_sha256,
                        spacing_sources=sources,
                        used_series=evidence.get("used_series", ()),
                        used_dicom_tags=evidence.get("used_dicom_tags", ()),
                        registration={
                            "metric": "multi_scale_normalized_mutual_information",
                            "converged": False,
                            "residual": None,
                            "top2_score_margin": None,
                            "ambiguous": True,
                        },
                    )
                    metadata["estimate"]["status"] = "fallback_initial_candidate"
                    metadata["estimate"]["confidence"]["overall"] = "unknown"
                    metadata["estimate"]["confidence"]["convergence"] = False
                    metadata["estimate"]["confidence"]["limitations"].append(
                        "automatic_estimation_failed"
                    )
            else:
                geometry = json.loads(args.geometry.read_text(encoding="utf-8"))
                if not isinstance(geometry, dict):
                    raise RescuePipelineError("rescue geometry must be a JSON object")
                estimated, confirmed, source_hash, transform = geometry_values_from_mapping(geometry)
                if args.command == "dicom-rescue-preview":
                    preview, metadata = create_preview(
                        volume,
                        estimated_spacing_xyz=estimated,
                        confirmed_spacing_xyz=confirmed,
                        transform=transform,
                        source_manifest_sha256=source_hash,
                        estimate_metadata=geometry,
                    )
                    write_decoded_volume(args.output_volume, preview)
                    metadata["outputs"] = write_preview_artifacts(
                        args.output_volume.parent / "images",
                        preview,
                        metadata["preview"]["spacing_xyz"],
                    )
                else:
                    metadata = finalize_rescue(
                        volume,
                        output_path=args.output_nifti,
                        estimated_spacing_xyz=estimated,
                        confirmed_spacing_xyz=confirmed,
                        transform=transform,
                        source_manifest_sha256=source_hash,
                        confirmation_token=args.confirmation_token,
                        estimate_metadata=geometry,
                    )
            write_metadata(args.output, metadata)
            print(json.dumps(metadata, indent=2, ensure_ascii=False))
            return 0
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            json.JSONDecodeError,
            RescuePipelineError,
        ):
            error = safe_rescue_error(
                code=f"rescue_{stage}_failed",
                stage=stage,
                reason="rescue request was invalid or could not be completed",
                tool_version=PIPELINE_VERSION,
                source_hash=source_hash,
            )
            write_metadata(args.output, error)
            print(json.dumps(error, indent=2, ensure_ascii=False), file=sys.stderr)
            return 2

    if args.command == "run":
        from totalsegmentator_wrapper_mac.runner_totalseg import run_totalsegmentator

        if args.task == "teeth" and args.robust_crop:
            parser.error(
                "--robust-crop is only supported with --task craniofacial_structures; "
                "use --teeth-robust-craniofacial-preflight for experimental teeth preflight."
            )
        if args.backend == "dentalsegmentator" and args.robust_crop:
            parser.error("--robust-crop is only supported with --backend totalsegmentator.")
        if args.backend == "dentalsegmentator" and args.higher_order_resampling:
            parser.error(
                "--higher-order-resampling is only supported with --backend totalsegmentator."
            )
        if args.backend == "toothseg" and args.task != "teeth":
            parser.error("--backend toothseg requires --task teeth.")
        if args.backend == "toothseg" and (args.robust_crop or args.higher_order_resampling):
            parser.error("ToothSeg does not support TotalSegmentator resampling/crop flags.")
        if args.toothseg_refine and args.backend != "toothseg":
            parser.error("--toothseg-refine can be used only with --backend toothseg")
        if args.toothseg_refine and args.task != "teeth":
            parser.error("--toothseg-refine can be used only with --task teeth")
        result = run_totalsegmentator(
            input_path=args.input,
            output_root=args.output,
            task=args.task,
            requested_device=args.device,
            execution_profile=args.execution_profile,
            require_mps=args.require_mps,
            backend=args.backend,
            totalseg_bin=args.totalseg_bin,
            totalseg_home=args.totalseg_home,
            totalseg_weights=args.totalseg_weights,
            dentalseg_bin=args.dentalseg_bin,
            dentalseg_model_dir=args.dentalseg_model_dir,
            dentalseg_model_zip=args.dentalseg_model_zip,
            dentalseg_nnunet_raw=args.dentalseg_nnunet_raw,
            dentalseg_nnunet_preprocessed=args.dentalseg_nnunet_preprocessed,
            dentalseg_nnunet_results=args.dentalseg_nnunet_results,
            dentalseg_dataset_id=args.dentalseg_dataset_id,
            dentalseg_configuration=args.dentalseg_configuration,
            dentalseg_trainer=args.dentalseg_trainer,
            dentalseg_plans=args.dentalseg_plans,
            dentalseg_folds=tuple(args.dentalseg_fold or ["0"]),
            dentalseg_disable_tta=args.dentalseg_disable_tta,
            dentalseg_not_on_device=args.dentalseg_not_on_device,
            dentalseg_npp=args.dentalseg_npp,
            dentalseg_nps=args.dentalseg_nps,
            dentalseg_timeout_sec=args.dentalseg_timeout_sec,
            toothseg_bin=args.toothseg_bin,
            toothseg_nnunet_results=args.toothseg_nnunet_results,
            toothseg_timeout_sec=args.toothseg_timeout_sec,
            copy_input=not args.no_copy_input,
            skip_device_check=args.skip_device_check,
            robust_crop=args.robust_crop,
            higher_order_resampling=args.higher_order_resampling,
            experimental_teeth=args.experimental_teeth,
            teeth_dry_run=args.teeth_dry_run,
            teeth_timeout_sec=args.teeth_timeout_sec,
            teeth_crop_margin_mm=args.teeth_crop_margin_mm,
            teeth_craniofacial_case=args.teeth_craniofacial_case,
            teeth_force_split=args.teeth_force_split,
            teeth_robust_craniofacial_preflight=args.teeth_robust_craniofacial_preflight,
            toothseg_refine=args.toothseg_refine,
        )
        if args.result_json is not None:
            safe_payload = {
                "schema": "totalsegmentator_wrapper_mac.safe_run_result.v1",
                "status": result.status,
                "feature": args.backend,
                "task": args.task,
                "error_code": result.error_code,
                "safe_reason": result.safe_reason,
                "mps_state": result.mps_state,
                "occurred_at": result.occurred_at,
                "teeth_detected": result.teeth_detected,
                "refine_available": result.refine_available,
            }
            write_json(args.result_json, safe_payload)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.status == "success" else result.returncode or 1

    if args.command == "benchmark":
        return _benchmark(args)

    if args.command == "summary":
        from totalsegmentator_wrapper_mac.case_summary import format_case_summary_markdown, format_case_summary_text

        text = (
            format_case_summary_markdown(args.case)
            if args.format == "markdown"
            else format_case_summary_text(args.case)
        )
        print(text)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        return 0

    if args.command == "surface-preview":
        from totalsegmentator_wrapper_mac.surface_preview import (
            STL_GENERATION_LOG_FILENAME,
            mark_surface_preview_stl_status,
            run_surface_preview,
            run_surface_preview_stl_only,
            smoothing_config_from_options,
        )

        smoothing = smoothing_config_from_options(
            preset=args.smooth_preset,
            iterations=args.smooth_iterations,
            lambda_value=args.smooth_lambda,
            mu=args.smooth_mu,
        )
        if args.stl_only:
            try:
                result = run_surface_preview_stl_only(
                    case_dir=args.case,
                    input_path=args.input,
                    output_dir=args.output,
                    min_voxels=args.min_voxels,
                    smoothing=smoothing,
                )
            except Exception as exc:
                output_dir = args.output or args.case / "surface_preview"
                if (output_dir / "preview_summary.json").exists():
                    mark_surface_preview_stl_status(
                        output_dir=output_dir,
                        status="failed",
                        error_type=type(exc).__name__,
                    )
                raise
        else:
            result = run_surface_preview(
                case_dir=args.case,
                input_path=args.input,
                output_dir=args.output,
                min_voxels=args.min_voxels,
                preview_step_size=args.preview_step_size,
                smoothing=smoothing,
                detailed_stl=not args.defer_stl,
            )
            if args.defer_stl:
                output_dir = Path(result["output_dir"])
                worker_command = [
                    sys.executable,
                    "-m",
                    "totalsegmentator_wrapper_mac",
                    "surface-preview",
                    "--case",
                    str(args.case),
                    "--output",
                    str(output_dir),
                    "--min-voxels",
                    str(args.min_voxels),
                    "--smooth-preset",
                    smoothing.preset,
                    "--smooth-iterations",
                    str(smoothing.iterations),
                    "--smooth-lambda",
                    str(smoothing.lambda_value),
                    "--smooth-mu",
                    str(smoothing.mu),
                    "--stl-only",
                ]
                if args.input is not None:
                    worker_command.extend(["--input", str(args.input)])
                log_path = output_dir / STL_GENERATION_LOG_FILENAME
                mark_surface_preview_stl_status(
                    output_dir=output_dir,
                    status="running",
                )
                try:
                    with log_path.open("ab", buffering=0) as log_file:
                        subprocess.Popen(
                            worker_command,
                            stdin=subprocess.DEVNULL,
                            stdout=log_file,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                            close_fds=True,
                        )
                except Exception as exc:
                    mark_surface_preview_stl_status(
                        output_dir=output_dir,
                        status="failed",
                        error_type=type(exc).__name__,
                    )
                    raise
                result["stl_generation"] = {"status": "running"}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.command == "slicer-export":
        from totalsegmentator_wrapper_mac.slicer_export import run_slicer_export

        result = run_slicer_export(
            case_dir=args.case,
            source_path=args.source,
            output_dir=args.output,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    raise AssertionError(f"unhandled command: {args.command}")


def _benchmark(args: argparse.Namespace) -> int:
    from totalsegmentator_wrapper_mac.benchmark import write_json
    from totalsegmentator_wrapper_mac.runner_totalseg import run_totalsegmentator

    output = args.output.resolve()
    cpu = run_totalsegmentator(
        input_path=args.input,
        output_root=output / "cpu",
        task=args.task,
        requested_device="cpu",
        totalseg_bin=args.totalseg_bin,
        totalseg_home=args.totalseg_home,
        totalseg_weights=args.totalseg_weights,
        copy_input=True,
        skip_device_check=args.skip_device_check,
        higher_order_resampling=args.higher_order_resampling,
    )
    mps = run_totalsegmentator(
        input_path=args.input,
        output_root=output / "mps",
        task=args.task,
        requested_device="mps",
        totalseg_bin=args.totalseg_bin,
        totalseg_home=args.totalseg_home,
        totalseg_weights=args.totalseg_weights,
        copy_input=True,
        skip_device_check=args.skip_device_check,
        higher_order_resampling=args.higher_order_resampling,
    )
    speedup = None
    if cpu.status == "success" and mps.status == "success" and mps.elapsed_seconds > 0:
        speedup = cpu.elapsed_seconds / mps.elapsed_seconds
    summary = {
        "task": args.task,
        "input": args.input.name,
        "cpu": cpu.to_dict(),
        "mps": mps.to_dict(),
        "speedup": speedup,
    }
    write_json(output / "benchmark_summary.json", summary)
    (output / "benchmark_summary.md").write_text(
        "\n".join(
            [
                "# Benchmark Summary",
                "",
                f"Task: `{args.task}`",
                f"Input basename: `{args.input.name}`",
                "",
                "| Device | Status | Elapsed seconds |",
                "|---|---:|---:|",
                f"| CPU | {cpu.status} | {cpu.elapsed_seconds:.2f} |",
                f"| MPS | {mps.status} | {mps.elapsed_seconds:.2f} |",
                "",
                f"Speedup: `{speedup}`",
                "",
                "Non-clinical research/education preview only.",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if cpu.status == "success" and mps.status == "success" else 1
