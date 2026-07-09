from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from totalsegmentator_wrapper_mac.dicom_normalizer_bridge import (
    inspect_dicom_normalizer,
    run_dicom_normalizer_audit,
    run_dicom_normalizer_convert_clean,
    run_dicom_normalizer_prepare_rescue,
    run_dicom_normalizer_prepare_viewer_export,
)
from totalsegmentator_wrapper_mac.device import smoke_test_mps_convtranspose3d


TASKS = ("craniofacial_structures", "teeth")
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
    setup.add_argument("--use-existing-env", action="store_true", help="Reuse the App Support venv if already bootstrapped.")
    setup.add_argument("--progress-log", type=Path, default=None, help="Append user-facing setup progress lines here.")

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

    run = subparsers.add_parser("run", help="Run TotalSegmentator and write case logs.")
    run.add_argument("--input", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--task", choices=TASKS, default="craniofacial_structures")
    run.add_argument("--device", choices=DEVICES, default="auto")
    run.add_argument("--totalseg-bin", default="TotalSegmentator")
    run.add_argument("--totalseg-home", type=Path, default=None)
    run.add_argument("--totalseg-weights", type=Path, default=None)
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
        from totalsegmentator_wrapper_mac.benchmark import write_json

        result = smoke_test_mps_convtranspose3d().to_dict()
        result["dicom_normalizer"] = inspect_dicom_normalizer()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.json:
            write_json(args.json, result)
        return 0 if result["status"] == "pass" else 1

    if args.command == "update-check":
        from totalsegmentator_wrapper_mac import __version__
        from totalsegmentator_wrapper_mac.benchmark import write_json
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
        from totalsegmentator_wrapper_mac.benchmark import write_json
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
            progress_log=args.progress_log,
        )
        payload = result.to_dict()
        if os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_SETUP_SUPPRESS_STDOUT_JSON") != "1":
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        write_json(args.json, payload)
        return 0 if result.status == "success" else 1

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

    if args.command == "run":
        from totalsegmentator_wrapper_mac.runner_totalseg import run_totalsegmentator

        if args.task == "teeth" and args.robust_crop:
            parser.error(
                "--robust-crop is only supported with --task craniofacial_structures; "
                "use --teeth-robust-craniofacial-preflight for experimental teeth preflight."
            )
        result = run_totalsegmentator(
            input_path=args.input,
            output_root=args.output,
            task=args.task,
            requested_device=args.device,
            totalseg_bin=args.totalseg_bin,
            totalseg_home=args.totalseg_home,
            totalseg_weights=args.totalseg_weights,
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
        )
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
            run_surface_preview,
            smoothing_config_from_options,
        )

        smoothing = smoothing_config_from_options(
            preset=args.smooth_preset,
            iterations=args.smooth_iterations,
            lambda_value=args.smooth_lambda,
            mu=args.smooth_mu,
        )
        result = run_surface_preview(
            case_dir=args.case,
            input_path=args.input,
            output_dir=args.output,
            min_voxels=args.min_voxels,
            preview_step_size=args.preview_step_size,
            smoothing=smoothing,
        )
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
