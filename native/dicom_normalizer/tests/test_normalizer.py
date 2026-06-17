#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


EXPLICIT_LE = "1.2.840.10008.1.2.1"
IMPLICIT_LE = "1.2.840.10008.1.2"
JPEG_BASELINE = "1.2.840.10008.1.2.4.50"
CT_SOP = "1.2.840.10008.5.1.4.1.1.2"
ENHANCED_CT_SOP = "1.2.840.10008.5.1.4.1.1.2.1"
SC_SOP = "1.2.840.10008.5.1.4.1.1.7"
DICOMDIR_SOP = "1.2.840.10008.1.3.10"


def elem_explicit(group: int, element: int, vr: str, value: bytes | str | int) -> bytes:
    if isinstance(value, str):
        raw = value.encode("ascii")
    elif isinstance(value, int):
        raw = struct.pack("<H", value)
    else:
        raw = value
    if len(raw) % 2:
        raw += b" "
    header = struct.pack("<HH", group, element) + vr.encode("ascii")
    if vr in {"OB", "OD", "OF", "OL", "OW", "SQ", "UC", "UR", "UT", "UN"}:
        return header + b"\0\0" + struct.pack("<I", len(raw)) + raw
    return header + struct.pack("<H", len(raw)) + raw


def elem_implicit(group: int, element: int, value: bytes | str | int) -> bytes:
    if isinstance(value, str):
        raw = value.encode("ascii")
    elif isinstance(value, int):
        raw = struct.pack("<H", value)
    else:
        raw = value
    if len(raw) % 2:
        raw += b" "
    return struct.pack("<HHI", group, element, len(raw)) + raw


def write_dicom(
    path: Path,
    *,
    sop: str = CT_SOP,
    transfer_syntax: str = EXPLICIT_LE,
    sop_instance_uid: str | None = None,
    series_number: int | None = 1,
    instance_number: int | None = None,
    series_uid: str = "1.2.826.0.1.3680043.10.543.1",
    description: str = "AXIAL CT",
    modality: str = "CT",
    image_type: str = "ORIGINAL\\PRIMARY\\AXIAL",
    rows: int = 32,
    columns: int = 32,
    geometry: bool = True,
    number_of_frames: int | None = None,
    secondary_capture: bool = False,
    pixel_spacing: str = "0.5\\0.5",
    image_position: str | None = None,
    image_orientation: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(b"\0" * 128 + b"DICM")
    data += elem_explicit(0x0002, 0x0002, "UI", sop)
    data += elem_explicit(0x0002, 0x0010, "UI", transfer_syntax)
    data += elem_explicit(0x0008, 0x0016, "UI", sop)
    data += elem_explicit(0x0008, 0x0018, "UI", sop_instance_uid or f"{series_uid}.{instance_number or 1}")
    data += elem_explicit(0x0008, 0x0008, "CS", image_type)
    data += elem_explicit(0x0008, 0x0060, "CS", modality)
    data += elem_explicit(0x0008, 0x103E, "LO", description)
    data += elem_explicit(0x0020, 0x000E, "UI", series_uid)
    if series_number is not None:
        data += elem_explicit(0x0020, 0x0011, "IS", str(series_number))
    if instance_number is not None:
        data += elem_explicit(0x0020, 0x0013, "IS", str(instance_number))
    data += elem_explicit(0x0028, 0x0010, "US", rows)
    data += elem_explicit(0x0028, 0x0011, "US", columns)
    data += elem_explicit(0x0028, 0x0002, "US", 1)
    data += elem_explicit(0x0028, 0x0004, "CS", "MONOCHROME2")
    data += elem_explicit(0x0028, 0x0100, "US", 16)
    data += elem_explicit(0x0028, 0x0103, "US", 1)
    if number_of_frames is not None:
        data += elem_explicit(0x0028, 0x0008, "IS", str(number_of_frames))
    if geometry:
        data += elem_explicit(0x0028, 0x0030, "DS", pixel_spacing)
        data += elem_explicit(0x0020, 0x0032, "DS", image_position or f"0\\0\\{instance_number or series_number or 0}")
        data += elem_explicit(0x0020, 0x0037, "DS", image_orientation or "1\\0\\0\\0\\1\\0")
    if secondary_capture:
        data += elem_explicit(0x0028, 0x0301, "CS", "YES")
    data += elem_explicit(0x7FE0, 0x0010, "OW", b"\0\0")
    path.write_bytes(data)


def write_implicit_no_prefix_ct(path: Path) -> None:
    data = bytearray()
    data += elem_implicit(0x0008, 0x0016, CT_SOP)
    data += elem_implicit(0x0008, 0x0060, "CT")
    data += elem_implicit(0x0020, 0x000E, "1.2.3.implicit")
    data += elem_implicit(0x0020, 0x0011, "44")
    data += elem_implicit(0x0028, 0x0010, struct.pack("<H", 16))
    data += elem_implicit(0x0028, 0x0011, struct.pack("<H", 16))
    path.write_bytes(data)


def write_dicomdir(path: Path, file_id: str = "CT\\000001") -> None:
    data = bytearray(b"\0" * 128 + b"DICM")
    data += elem_explicit(0x0002, 0x0002, "UI", DICOMDIR_SOP)
    data += elem_explicit(0x0002, 0x0010, "UI", EXPLICIT_LE)
    data += elem_explicit(0x0008, 0x0016, "UI", DICOMDIR_SOP)
    data += elem_explicit(0x0004, 0x1500, "CS", file_id)
    path.write_bytes(data)


def write_fake_dcm2niix(
    path: Path,
    *,
    shape: tuple[int, int, int] = (4, 5, 6),
    spacing: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> None:
    script = f"""#!/usr/bin/env python3
import os, struct, sys
out_dir = sys.argv[sys.argv.index('-o') + 1]
name = sys.argv[sys.argv.index('-f') + 1]
os.makedirs(out_dir, exist_ok=True)
header = bytearray(352)
struct.pack_into('<I', header, 0, 348)
struct.pack_into('<hhhh', header, 40, 3, {shape[0]}, {shape[1]}, {shape[2]})
struct.pack_into('<h', header, 70, 4)
struct.pack_into('<h', header, 72, 16)
struct.pack_into('<ffff', header, 76, 1.0, {spacing[0]}, {spacing[1]}, {spacing[2]})
struct.pack_into('<f', header, 108, 352.0)
struct.pack_into('<h', header, 252, 0)
struct.pack_into('<h', header, 254, 0)
header[344:348] = b'n+1\\0'
payload = b'\\0\\0' * ({shape[0]} * {shape[1]} * {shape[2]})
open(os.path.join(out_dir, name + '.nii'), 'wb').write(header + payload)
print('fake dcm2niix wrote nifti')
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run(binary: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env["TOTALSEGMENTATOR_WRAPPER_MAC_DISABLE_EXTERNAL_DICOM_TOOLS"] = "1"
    if env:
        merged_env.update(env)
    return subprocess.run(
        [str(binary), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=merged_env,
    )


def load_audit(binary: Path, dicom_dir: Path) -> dict:
    output = dicom_dir / "audit.json"
    proc = run(binary, "audit", "--dicom-dir", str(dicom_dir), "--output", str(output))
    assert proc.returncode == 0, proc.stderr
    return json.loads(output.read_text(encoding="utf-8"))


def series_by_status(payload: dict, status: str) -> dict:
    for series in payload["series"]:
        if series["classification"]["status"] == status:
            return series
    raise AssertionError(f"status not found: {status}")


def test_clean_ct(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for index in range(32):
            write_dicom(
                root / f"ct_{index:04d}.dcm",
                series_number=7,
                series_uid="1.2.3.clean",
            )
        payload = load_audit(binary, root)
        series = series_by_status(payload, "original_ct_geometry_ok")
        assert series["classification"]["next_action"] == "convert_clean"
        assert series["classification"]["requires_external_tool"] is False


def test_ct_without_original_image_type_is_clean_when_geometry_is_complete(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for index in range(32):
            write_dicom(
                root / f"derived_ct_{index:04d}.dcm",
                series_number=71,
                series_uid="1.2.3.derived_geometry_ok",
                image_type="DERIVED\\PRIMARY\\AXIAL",
            )
        payload = load_audit(binary, root)
        series = series_by_status(payload, "original_ct_geometry_ok")
        assert series["classification"]["next_action"] == "convert_clean"
        assert "image_type_not_original_but_geometry_complete" in series["classification"]["reasons"]


def test_doctor(binary: Path) -> None:
    proc = run(binary, "doctor")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    assert payload["capabilities"]["audit"] is True
    assert payload["capabilities"]["convert_clean"] is True
    assert payload["capabilities"]["prepare_rescue"] is True
    assert payload["capabilities"]["native_compressed_pixel_decode"] is False
    assert "optional_tools" in payload

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "doctor.json"
        proc = run(binary, "doctor", "--output", str(output))
        assert proc.returncode == 0, proc.stderr
        assert json.loads(output.read_text(encoding="utf-8"))["status"] == "ok"


def test_compressed_and_missing_geometry(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for index in range(32):
            write_dicom(
                root / "compressed" / f"ct_{index:04d}.dcm",
                transfer_syntax=JPEG_BASELINE,
                series_number=8,
                series_uid="1.2.3.compressed",
            )
            write_dicom(
                root / "missing" / f"ct_{index:04d}.dcm",
                geometry=False,
                series_number=9,
                series_uid="1.2.3.missing",
            )
        payload = load_audit(binary, root)
        compressed = series_by_status(payload, "compressed_pixel_data")
        assert compressed["classification"]["requires_external_tool"] is True
        assert compressed["classification"]["next_action"] == "install_gdcm_or_dcmtk_transcoder"
        missing = series_by_status(payload, "reject")
        assert missing["classification"]["reject_reason"]


def test_dicomdir_and_implicit(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_dicomdir(root / "DICOMDIR", "CT\\000001")
        write_dicom(root / "CT" / "000001", series_number=10, series_uid="1.2.3.dicomdir")
        for index in range(31):
            write_dicom(root / "CT" / f"{index + 2:06d}", series_number=10, series_uid="1.2.3.dicomdir")
        write_implicit_no_prefix_ct(root / "implicit_no_prefix.dcm")
        payload = load_audit(binary, root)
        assert payload["dicomdir"]["dicomdir_file_count"] == 1
        assert payload["dicomdir"]["resolved_reference_count"] == 1
        assert "dicomdir_only" in payload["classification_counts"]


def test_secondary_capture_variants(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for index in range(40):
            write_dicom(
                root / "sc_stack" / f"sc_{index:04d}.dcm",
                sop=SC_SOP,
                series_number=200,
                series_uid="1.2.3.sc.stack",
                description="AXIAL BO",
                modality="OT",
                image_type="DERIVED\\SECONDARY\\SCREEN SAVE\\AXIAL",
                geometry=False,
                secondary_capture=True,
            )
        write_dicom(
            root / "sc_multiframe.dcm",
            sop=SC_SOP,
            series_number=201,
            series_uid="1.2.3.sc.multi",
            description="AXIAL BO",
            modality="OT",
            image_type="DERIVED\\SECONDARY\\SCREEN SAVE\\AXIAL",
            geometry=False,
            number_of_frames=40,
            secondary_capture=True,
        )
        payload = load_audit(binary, root)
        assert payload["classification_counts"]["secondary_capture_rescue_candidate"] == 2


def test_convert_clean_and_prepare_rescue(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fake = root / "fake_dcm2niix.py"
        write_fake_dcm2niix(fake)
        for index in range(32):
            write_dicom(root / "clean" / f"ct_{index:04d}.dcm", series_number=11, series_uid="1.2.3.convert")
        clean_out = root / "clean_out"
        proc = run(
            binary,
            "convert-clean",
            "--dicom-dir",
            str(root / "clean"),
            "--series-number",
            "11",
            "--output",
            str(clean_out),
            "--dcm2niix",
            str(fake),
        )
        assert proc.returncode == 0, proc.stderr
        clean_meta = json.loads((clean_out / "convert_clean_metadata.json").read_text(encoding="utf-8"))
        assert clean_meta["status"] == "success"

        for index in range(32):
            write_dicom(
                root / "clean_no_series_number" / f"ct_{index:04d}.dcm",
                series_number=None,
                series_uid="1.2.3.convert.keyonly",
            )
        clean_key_out = root / "clean_key_out"
        proc = run(
            binary,
            "convert-clean",
            "--dicom-dir",
            str(root / "clean_no_series_number"),
            "--series-key",
            "1.2.3.convert.keyonly",
            "--output",
            str(clean_key_out),
            "--dcm2niix",
            str(fake),
        )
        assert proc.returncode == 0, proc.stderr
        clean_key_meta = json.loads((clean_key_out / "convert_clean_metadata.json").read_text(encoding="utf-8"))
        assert clean_key_meta["status"] == "success"
        assert clean_key_meta["selected_series"]["series_number"] is None
        assert clean_key_meta["selected_series"]["series_instance_uid"] == "1.2.3.convert.keyonly"

        for index in range(40):
            write_dicom(
                root / "rescue" / f"sc_{index:04d}.dcm",
                sop=SC_SOP,
                series_number=202,
                series_uid="1.2.3.rescue",
                description="AXIAL BO",
                modality="OT",
                image_type="DERIVED\\SECONDARY\\SCREEN SAVE\\AXIAL",
                geometry=False,
                secondary_capture=True,
            )
        rescue_out = root / "rescue_out"
        proc = run(
            binary,
            "prepare-rescue",
            "--dicom-dir",
            str(root / "rescue"),
            "--series-number",
            "202",
            "--patched-spacing",
            "0.6,0.6,0.9375",
            "--output",
            str(rescue_out),
            "--dcm2niix",
            str(fake),
        )
        assert proc.returncode == 0, proc.stderr
        validation = json.loads((rescue_out / "rescue_validation.json").read_text(encoding="utf-8"))
        assert validation["status"] == "success"
        assert validation["patched_spacing_matches_requested"] is True
        assert validation["mpr_preview"]["written"] is True
        assert len(validation["mpr_preview"]["previews"]) == 3
        assert validation["mpr_preview"]["previews"][0]["uniform_or_empty"] is True

        bad_convert = run(
            binary,
            "convert-clean",
            "--dicom-dir",
            str(root / "rescue"),
            "--series-number",
            "202",
            "--output",
            str(root / "bad_convert"),
            "--dcm2niix",
            str(fake),
        )
        assert bad_convert.returncode != 0
        assert "convert-clean requires original_ct_geometry_ok" in bad_convert.stderr

        bad_rescue = run(
            binary,
            "prepare-rescue",
            "--dicom-dir",
            str(root / "clean"),
            "--series-number",
            "11",
            "--patched-spacing",
            "0.6,0.6,0.9375",
            "--output",
            str(root / "bad_rescue"),
            "--dcm2niix",
            str(fake),
        )
        assert bad_rescue.returncode != 0
        assert "prepare-rescue requires secondary_capture_rescue_candidate" in bad_rescue.stderr

        missing_spacing = run(
            binary,
            "prepare-rescue",
            "--dicom-dir",
            str(root / "rescue"),
            "--series-number",
            "202",
            "--output",
            str(root / "missing_spacing"),
            "--dcm2niix",
            str(fake),
        )
        assert missing_spacing.returncode != 0
        assert "--patched-spacing" in missing_spacing.stderr


def test_viewer_export_mpr_mixed_candidate_and_prepare(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dicom = root / "viewer_export"
        series_uid = "1.2.3.viewer.export"
        groups = [
            ("sagittal", 1, "0\\1\\0\\0\\0\\-1", lambda i: f"{i}\\0\\0", 64, 72),
            ("coronal", 33, "1\\0\\0\\0\\0\\-1", lambda i: f"0\\{i}\\0", 64, 72),
            ("axial", 65, "1\\0\\0\\0\\-1\\0", lambda i: f"0\\0\\{-i}", 72, 72),
        ]
        for label, start_instance, iop, ipp_fn, rows, columns in groups:
            for offset in range(32):
                instance = start_instance + offset
                write_dicom(
                    dicom / label / f"{instance:06d}.dcm",
                    series_number=1002002,
                    series_uid=series_uid,
                    instance_number=instance,
                    rows=rows,
                    columns=columns,
                    pixel_spacing="0.5\\0.5",
                    image_orientation=iop,
                    image_position=ipp_fn(offset),
                )

        payload = load_audit(binary, dicom)
        series = series_by_status(payload, "viewer_export_mpr_mixed_candidate")
        assert series["classification"]["next_action"] == "select_viewer_export_group"
        groups_json = series["viewer_export_groups"]
        assert len(groups_json) == 3
        recommended = [group for group in groups_json if group["recommendation"] == "recommended"]
        assert len(recommended) == 1
        assert recommended[0]["group_id"] == "g003"
        assert recommended[0]["plane_label"] == "axial_like"
        assert recommended[0]["ai_eligibility"]["status"] == "rescue_go_with_warning"

        fake = root / "fake_dcm2niix.py"
        write_fake_dcm2niix(fake, shape=(72, 72, 32), spacing=(0.5, 0.5, 1.0))
        out = root / "viewer_export_out"
        proc = run(
            binary,
            "prepare-viewer-export",
            "--dicom-dir",
            str(dicom),
            "--series-number",
            "1002002",
            "--group-id",
            "g003",
            "--output",
            str(out),
            "--dcm2niix",
            str(fake),
        )
        assert proc.returncode == 0, proc.stderr
        metadata = json.loads((out / "viewer_export_metadata.json").read_text(encoding="utf-8"))
        assert metadata["status"] == "success"
        assert metadata["selected_group"]["group_id"] == "g003"
        assert metadata["selected_group"]["plane_label"] == "axial_like"
        assert metadata["validation"]["shape_matches_group"] is True
        assert metadata["validation"]["spacing_matches_group"] is True
        assert metadata["provenance"]["viewer_export_rescue"] is True
        assert metadata["provenance"]["not_original_axial_ct"] is True
        assert len(metadata["outputs"]["mpr_preview_paths"]) == 3
        assert len(metadata["outputs"]["mpr_preview"]) == 3
        assert {item["plane"] for item in metadata["outputs"]["mpr_preview"]} == {
            "axial",
            "coronal",
            "sagittal",
        }
        assert all(item["uniform_or_empty"] is True for item in metadata["outputs"]["mpr_preview"])


def test_viewer_export_without_axial_group_is_not_ai_rescue_candidate(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dicom = root / "viewer_export_no_axial"
        series_uid = "1.2.3.viewer.noaxial"
        groups = [
            ("sagittal", 1, "0\\1\\0\\0\\0\\-1", lambda i: f"{i}\\0\\0", 64, 72),
            ("coronal", 33, "1\\0\\0\\0\\0\\-1", lambda i: f"0\\{i}\\0", 64, 72),
        ]
        for label, start_instance, iop, ipp_fn, rows, columns in groups:
            for offset in range(32):
                instance = start_instance + offset
                write_dicom(
                    dicom / label / f"{instance:06d}.dcm",
                    series_number=1002003,
                    series_uid=series_uid,
                    instance_number=instance,
                    rows=rows,
                    columns=columns,
                    pixel_spacing="0.5\\0.5",
                    image_orientation=iop,
                    image_position=ipp_fn(offset),
                )

        payload = load_audit(binary, dicom)
        statuses = [series["classification"]["status"] for series in payload["series"]]
        assert "viewer_export_mpr_mixed_candidate" not in statuses


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: test_normalizer.py <binary>", file=sys.stderr)
        return 2
    binary = Path(sys.argv[1])
    tests = [
        test_doctor,
        test_clean_ct,
        test_ct_without_original_image_type_is_clean_when_geometry_is_complete,
        test_compressed_and_missing_geometry,
        test_dicomdir_and_implicit,
        test_secondary_capture_variants,
        test_convert_clean_and_prepare_rescue,
        test_viewer_export_mpr_mixed_candidate_and_prepare,
        test_viewer_export_without_axial_group_is_not_ai_rescue_candidate,
    ]
    for test in tests:
        test(binary)
        print(f"ok {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
