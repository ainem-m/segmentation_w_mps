#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import ast
import os
import shutil
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


def elem_sequence(group: int, element: int, items: list[bytes]) -> bytes:
    payload = b"".join(
        struct.pack("<HHI", 0xFFFE, 0xE000, len(item)) + item
        for item in items
    )
    return elem_explicit(group, element, "SQ", payload)


def write_dicom(
    path: Path,
    *,
    sop: str = CT_SOP,
    transfer_syntax: str = EXPLICIT_LE,
    sop_instance_uid: str | None = None,
    series_number: int | None = 1,
    instance_number: int | None = None,
    study_uid: str | None = None,
    frame_of_reference_uid: str | None = None,
    series_uid: str = "1.2.826.0.1.3680043.10.543.1",
    include_series_uid: bool = True,
    description: str = "AXIAL CT",
    modality: str = "CT",
    image_type: str = "ORIGINAL\\PRIMARY\\AXIAL",
    rows: int = 32,
    columns: int = 32,
    geometry: bool = True,
    include_pixel_spacing: bool | None = None,
    include_image_position: bool | None = None,
    include_image_orientation: bool | None = None,
    number_of_frames: int | None = None,
    secondary_capture: bool = False,
    samples_per_pixel: int = 1,
    photometric_interpretation: str = "MONOCHROME2",
    bits_allocated: int = 16,
    pixel_representation: int = 1,
    pixel_bytes: bytes | None = None,
    pixel_spacing: str = "0.5\\0.5",
    slice_thickness: str | None = None,
    spacing_between_slices: str | None = None,
    image_position: str | None = None,
    image_orientation: str | None = None,
    rescale_slope: str | None = None,
    rescale_intercept: str | None = None,
    modality_lut: bool = False,
    shared_pixel_value_transform: bool = False,
    per_frame_pixel_value_transform: bool = False,
    malformed_encapsulated_pixel_data: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(b"\0" * 128 + b"DICM")
    data += elem_explicit(0x0002, 0x0002, "UI", sop)
    data += elem_explicit(0x0002, 0x0003, "UI", sop_instance_uid or f"{series_uid}.{instance_number or 1}")
    data += elem_explicit(0x0002, 0x0010, "UI", transfer_syntax)
    data += elem_explicit(0x0002, 0x0012, "UI", "1.2.826.0.1.3680043.10.543.999")
    data += elem_explicit(0x0008, 0x0016, "UI", sop)
    data += elem_explicit(0x0008, 0x0018, "UI", sop_instance_uid or f"{series_uid}.{instance_number or 1}")
    data += elem_explicit(0x0008, 0x0008, "CS", image_type)
    data += elem_explicit(0x0008, 0x0060, "CS", modality)
    data += elem_explicit(0x0008, 0x103E, "LO", description)
    if study_uid is not None:
        data += elem_explicit(0x0020, 0x000D, "UI", study_uid)
    if frame_of_reference_uid is not None:
        data += elem_explicit(0x0020, 0x0052, "UI", frame_of_reference_uid)
    if include_series_uid:
        data += elem_explicit(0x0020, 0x000E, "UI", series_uid)
    if series_number is not None:
        data += elem_explicit(0x0020, 0x0011, "IS", str(series_number))
    if instance_number is not None:
        data += elem_explicit(0x0020, 0x0013, "IS", str(instance_number))
    data += elem_explicit(0x0028, 0x0010, "US", rows)
    data += elem_explicit(0x0028, 0x0011, "US", columns)
    data += elem_explicit(0x0028, 0x0002, "US", samples_per_pixel)
    data += elem_explicit(0x0028, 0x0004, "CS", photometric_interpretation)
    if samples_per_pixel > 1:
        data += elem_explicit(0x0028, 0x0006, "US", 0)
    data += elem_explicit(0x0028, 0x0100, "US", bits_allocated)
    data += elem_explicit(0x0028, 0x0101, "US", bits_allocated)
    data += elem_explicit(0x0028, 0x0102, "US", bits_allocated - 1)
    data += elem_explicit(0x0028, 0x0103, "US", pixel_representation)
    if rescale_intercept is not None:
        data += elem_explicit(0x0028, 0x1052, "DS", rescale_intercept)
    if rescale_slope is not None:
        data += elem_explicit(0x0028, 0x1053, "DS", rescale_slope)
    if modality_lut:
        lut_item = (
            elem_explicit(0x0028, 0x3002, "US", struct.pack("<3H", 2, 0, 16))
            + elem_explicit(0x0028, 0x3006, "OW", struct.pack("<2H", 0, 1))
        )
        data += elem_sequence(0x0028, 0x3000, [lut_item])
    if shared_pixel_value_transform or per_frame_pixel_value_transform:
        transform_item = (
            elem_explicit(0x0028, 0x1052, "DS", "0")
            + elem_explicit(0x0028, 0x1053, "DS", "1")
        )
        transform_sequence = elem_sequence(0x0028, 0x9145, [transform_item])
        if shared_pixel_value_transform:
            data += elem_sequence(0x5200, 0x9229, [transform_sequence])
        if per_frame_pixel_value_transform:
            data += elem_sequence(0x5200, 0x9230, [transform_sequence])
    if number_of_frames is not None:
        data += elem_explicit(0x0028, 0x0008, "IS", str(number_of_frames))
    if include_pixel_spacing if include_pixel_spacing is not None else geometry:
        data += elem_explicit(0x0028, 0x0030, "DS", pixel_spacing)
    if include_image_position if include_image_position is not None else geometry:
        data += elem_explicit(0x0020, 0x0032, "DS", image_position or f"0\\0\\{instance_number or series_number or 0}")
    if include_image_orientation if include_image_orientation is not None else geometry:
        data += elem_explicit(0x0020, 0x0037, "DS", image_orientation or "1\\0\\0\\0\\1\\0")
    if slice_thickness is not None:
        data += elem_explicit(0x0018, 0x0050, "DS", slice_thickness)
    if spacing_between_slices is not None:
        data += elem_explicit(0x0018, 0x0088, "DS", spacing_between_slices)
    if secondary_capture:
        data += elem_explicit(0x0028, 0x0301, "CS", "YES")
    if malformed_encapsulated_pixel_data:
        data += struct.pack("<HH", 0x7FE0, 0x0010) + b"OB\0\0" + struct.pack("<I", 0xFFFFFFFF)
        data += struct.pack("<HHI", 0xFFFE, 0xE000, 0)
        data += struct.pack("<HHI", 0xFFFE, 0xE000, 8) + b"NOTJPEG"
        data += struct.pack("<HHI", 0xFFFE, 0xE0DD, 0)
    else:
        frame_count = number_of_frames or 1
        bytes_per_sample = bits_allocated // 8
        pixels = pixel_bytes
        if pixels is None:
            pixels = (
                b"\0"
                * rows
                * columns
                * frame_count
                * samples_per_pixel
                * bytes_per_sample
            )
        pixel_vr = "OB" if bits_allocated == 8 else "OW"
        data += elem_explicit(0x7FE0, 0x0010, pixel_vr, pixels)
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
    output = (
        dicom_dir / "audit.json"
        if dicom_dir.is_dir()
        else dicom_dir.with_name(f"{dicom_dir.stem}_audit.json")
    )
    proc = run(binary, "audit", "--dicom-dir", str(dicom_dir), "--output", str(output))
    assert proc.returncode == 0, proc.stderr
    return json.loads(output.read_text(encoding="utf-8"))


def series_by_status(payload: dict, status: str) -> dict:
    for series in payload["series"]:
        if series["classification"]["status"] == status:
            return series
    raise AssertionError(f"status not found: {status}")


def series_by_uid(payload: dict, uid: str) -> dict:
    for series in payload["series"]:
        if series["series_instance_uid"] == uid:
            return series
    raise AssertionError(f"series UID not found: {uid}")


def fnv1a64(data: bytes) -> str:
    value = 1469598103934665603
    for byte in data:
        value ^= byte
        value = (value * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f"{value:x}"


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
    assert payload["capabilities"]["export_rescue_stack"] is True
    assert payload["capabilities"]["native_compressed_pixel_decode"] is True
    assert payload["capabilities"]["native_lossless_transcode"] is True
    assert payload["capabilities"]["enhanced_ct_per_frame_geometry_validation"] is False
    assert payload["dicom_backend"]["name"] == "GDCM"
    assert payload["dicom_backend"]["version"]
    assert "optional_tools" in payload

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "doctor.json"
        proc = run(binary, "doctor", "--output", str(output))
        assert proc.returncode == 0, proc.stderr
        assert json.loads(output.read_text(encoding="utf-8"))["status"] == "ok"


def test_malformed_compressed_never_falls_back(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for index in range(32):
            write_dicom(
                root / "compressed" / f"ct_{index:04d}.dcm",
                transfer_syntax=JPEG_BASELINE,
                series_number=8,
                series_uid="1.2.3.compressed",
                malformed_encapsulated_pixel_data=True,
            )
            write_dicom(
                root / "missing" / f"ct_{index:04d}.dcm",
                geometry=False,
                series_number=9,
                series_uid="1.2.3.missing",
            )
        payload = load_audit(binary, root)
        compressed = series_by_uid(payload, "1.2.3.compressed")
        assert compressed["classification"]["status"] == "pixel_decode_failed", compressed
        assert compressed["classification"]["requires_external_tool"] is False
        assert compressed["pixel_decode_failure_count"] == 32
        assert compressed["parser_backend"] == "gdcm"
        missing = series_by_status(payload, "reject")
        assert missing["classification"]["reject_reason"]


def test_enhanced_ct_decodes_and_requires_explicit_geometry_rescue(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_dicom(
            root / "enhanced_ct.dcm",
            sop=ENHANCED_CT_SOP,
            series_number=90,
            series_uid="1.2.3.enhanced",
            number_of_frames=32,
        )
        payload = load_audit(binary, root)
        series = series_by_uid(payload, "1.2.3.enhanced")
        assert series["pixel_decode_ok_count"] == 1
        assert series["classification"]["status"] == "geometry_rescue_candidate"
        assert series["classification"]["requires_external_tool"] is False
        assert series["classification"]["next_action"] == (
            "prepare_rescue_with_explicit_spacing"
        )
        assert "per_frame_geometry_not_fully_validated" in series["classification"]["reasons"]


def test_real_compressed_codecs_and_native_transcode(binary: Path, gdcmconv: Path) -> None:
    codec_options = {
        "jpeg": "-J",
        "jpeg2000": "-K",
        "jpegls": "-L",
        "rle": "-R",
    }
    expected_bytes = 32 * 32 * 2
    expected_checksum = fnv1a64(b"\0" * expected_bytes)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for series_number, (name, option) in enumerate(codec_options.items(), start=80):
            uid = f"1.2.826.0.1.3680043.10.543.8{series_number}"
            raw = root / f"{name}_raw.dcm"
            compressed = root / f"{name}_compressed.dcm"
            write_dicom(raw, series_number=series_number, series_uid=uid, instance_number=1)
            proc = subprocess.run(
                [str(gdcmconv), option, str(raw), str(compressed)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            assert proc.returncode == 0, f"{name}: {proc.stdout}\n{proc.stderr}"
            raw.unlink()
            series_dir = root / name
            series_dir.mkdir()
            for index in range(32):
                shutil.copyfile(compressed, series_dir / f"slice_{index:04d}.dcm")
            compressed.unlink()

        payload = load_audit(binary, root)
        assert payload["dicom_backend"]["name"] == "GDCM"
        for series_number, name in enumerate(codec_options, start=80):
            uid = f"1.2.826.0.1.3680043.10.543.8{series_number}"
            series = series_by_uid(payload, uid)
            assert series["classification"]["status"] == "original_ct_geometry_ok", name
            assert series["classification"]["requires_external_tool"] is False
            assert "native_gdcm_decode_ok" in series["classification"]["reasons"]
            assert series["compressed_transfer_syntax_count"] == 32
            assert series["pixel_decode_attempted_count"] == 32
            assert series["pixel_decode_ok_count"] == 32
            assert series["pixel_decode_failure_count"] == 0
            assert series["decoded_bytes_first"] == expected_bytes
            assert series["decoded_fnv1a64_first"] == expected_checksum

        jpeg_uid = "1.2.826.0.1.3680043.10.543.880"
        fake = root / "fake_dcm2niix.py"
        write_fake_dcm2niix(fake, shape=(32, 32, 32), spacing=(0.5, 0.5, 1.0))
        output = root / "converted"
        proc = run(
            binary,
            "convert-clean",
            "--dicom-dir",
            str(root),
            "--series-key",
            jpeg_uid,
            "--output",
            str(output),
            "--dcm2niix",
            str(fake),
        )
        assert proc.returncode == 0, proc.stderr
        isolated_payload = load_audit(binary, output / "isolated_series")
        isolated = series_by_uid(isolated_payload, jpeg_uid)
        assert isolated["compressed_transfer_syntax_count"] == 0
        assert isolated["pixel_decode_ok_count"] == 32
        assert isolated["decoded_bytes_first"] == expected_bytes
        assert isolated["decoded_fnv1a64_first"] == expected_checksum


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


def test_single_multiframe_dicom_file_input_and_rescue_stack(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dicom = root / "single_multiframe.dcm"
        expected_voxels = tuple(
            frame * 100 + offset
            for frame in range(1, 41)
            for offset in range(8)
        )
        write_dicom(
            dicom,
            sop=SC_SOP,
            series_number=202,
            series_uid="1.2.3.sc.single.multi",
            description="AXIAL BO",
            modality="OT",
            image_type="DERIVED\\SECONDARY\\SCREEN SAVE\\AXIAL",
            rows=2,
            columns=4,
            geometry=False,
            number_of_frames=40,
            secondary_capture=True,
            pixel_bytes=struct.pack("<320h", *expected_voxels),
        )

        payload = load_audit(binary, dicom)
        series = series_by_uid(payload, "1.2.3.sc.single.multi")
        assert series["classification"]["status"] == "secondary_capture_rescue_candidate"
        assert series["effective_frame_count"] == 40

        output = root / "single_multiframe_output"
        proc = run(
            binary,
            "export-rescue-stack",
            "--dicom-dir",
            str(dicom),
            "--series-number",
            "202",
            "--output",
            str(output),
        )
        assert proc.returncode == 0, proc.stderr
        header, pixels = read_npy(output / "preview_stack.npy")
        assert header["shape"] == (4, 2, 40)
        assert len(pixels) == 4 * 2 * 40 * 2
        assert struct.unpack("<320h", pixels) == expected_voxels
        manifest = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))
        assert manifest["array"]["shape_xyz"] == [4, 2, 40]
        assert manifest["ordering"]["source"] == "frame_number"
        assert manifest["source"]["entry_count"] == 40
        assert [entry["frame_number"] for entry in manifest["source"]["entries"]] == list(
            range(1, 41)
        )


def test_single_multiframe_identity_rescale_remains_rescueable(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dicom = root / "single_multiframe_identity.dcm"
        write_dicom(
            dicom,
            sop=ENHANCED_CT_SOP,
            series_number=203,
            series_uid="1.2.3.enhanced.identity",
            description="AXIAL CT",
            modality="CT",
            image_type="ORIGINAL\\PRIMARY\\AXIAL",
            rows=2,
            columns=4,
            geometry=False,
            number_of_frames=40,
            rescale_slope="1",
            rescale_intercept="0",
        )
        payload = load_audit(binary, dicom)
        series = series_by_uid(payload, "1.2.3.enhanced.identity")
        assert series["classification"]["status"] == "geometry_rescue_candidate"

        output = root / "identity_output"
        proc = run(
            binary,
            "export-rescue-stack",
            "--dicom-dir",
            str(dicom),
            "--series-key",
            "1.2.3.enhanced.identity",
            "--output",
            str(output),
        )
        assert proc.returncode == 0, proc.stderr
        header, _ = read_npy(output / "preview_stack.npy")
        assert header["shape"] == (4, 2, 40)


def test_geometry_evidence_and_secondary_capture_references(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for index in range(32):
            write_dicom(
                root / "clean" / f"ct_{index:04d}.dcm",
                series_number=190,
                study_uid="1.2.3.study.geometry",
                frame_of_reference_uid="1.2.3.frame.geometry",
                series_uid="1.2.3.geometry.evidence",
                instance_number=index + 1,
                slice_thickness="0.9375",
                spacing_between_slices="1.25",
            )
        for series_number, plane in ((191, "CORONAL"), (192, "SAGITTAL")):
            for index in range(32):
                write_dicom(
                    root / plane.lower() / f"sc_{index:04d}.dcm",
                    sop=SC_SOP,
                    series_number=series_number,
                    series_uid=f"1.2.3.sc.reference.{series_number}",
                    instance_number=index + 1,
                    description=f"{plane} BO",
                    modality="OT",
                    image_type=f"DERIVED\\SECONDARY\\SCREEN SAVE\\{plane}",
                    geometry=False,
                    secondary_capture=True,
                    slice_thickness="0.9375",
                )

        payload = load_audit(binary, root)
        clean = series_by_uid(payload, "1.2.3.geometry.evidence")
        assert clean["classification"]["status"] == "original_ct_geometry_ok"
        assert clean["classification"]["next_action"] == "convert_clean"
        assert clean["study_key_sha256"] == hashlib.sha256(
            b"1.2.3.study.geometry"
        ).hexdigest()
        assert clean["frame_of_reference_key_sha256"] == hashlib.sha256(
            b"1.2.3.frame.geometry"
        ).hexdigest()
        assert "1.2.3.study.geometry" not in json.dumps(clean)
        assert clean["slice_thickness"]["present_count"] == 32
        assert clean["slice_thickness"]["valid_numeric_count"] == 32
        assert clean["slice_thickness"]["consistent"] is True
        assert clean["slice_thickness"]["values_mm"] == [0.9375]
        assert clean["spacing_between_slices"]["present_count"] == 32
        assert clean["spacing_between_slices"]["valid_numeric_count"] == 32
        assert clean["spacing_between_slices"]["consistent"] is True
        assert clean["spacing_between_slices"]["values_mm"] == [1.25]

        references = [
            item
            for item in payload["series"]
            if item["classification"]["status"] == "secondary_capture_reference_candidate"
        ]
        assert len(references) == 2
        assert {
            item["classification"]["next_action"]
            for item in references
        } == {"use_as_rescue_reference_series"}
        assert all(
            "reference_only" in item["classification"]["reasons"]
            for item in references
        )
        assert payload["classification_counts"].get(
            "secondary_capture_rescue_candidate", 0
        ) == 0


def test_partial_geometry_ct_is_rescue_candidate(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for index in range(32):
            write_dicom(
                root / "missing_xy" / f"ct_{index:04d}.dcm",
                series_number=194,
                series_uid="1.2.3.partial.missing.xy",
                instance_number=index + 1,
                include_pixel_spacing=False,
                slice_thickness="1.2",
                image_position=f"0\\0\\{index * 0.8}",
            )
            write_dicom(
                root / "missing_z" / f"ct_{index:04d}.dcm",
                series_number=195,
                series_uid="1.2.3.partial.missing.z",
                instance_number=index + 1,
                include_image_position=False,
                spacing_between_slices="1.5",
                slice_thickness="2.0",
                pixel_spacing="0.4\\0.6",
            )
            irregular_position = index * 0.8 if index < 16 else 12.0 + (index - 15) * 1.4
            write_dicom(
                root / "irregular_z" / f"ct_{index:04d}.dcm",
                series_number=196,
                series_uid="1.2.3.partial.irregular.z",
                instance_number=index + 1,
                include_pixel_spacing=False,
                slice_thickness="1.1",
                image_position=f"0\\0\\{irregular_position}",
            )

        payload = load_audit(binary, root)
        missing_xy = series_by_uid(payload, "1.2.3.partial.missing.xy")
        assert missing_xy["classification"]["status"] == "geometry_rescue_candidate"
        assert missing_xy["pixel_spacing_mm"] is None
        assert missing_xy["projected_slice_spacing_mm"] == 0.8
        assert "missing_pixel_spacing" in missing_xy["classification"]["reasons"]

        missing_z = series_by_uid(payload, "1.2.3.partial.missing.z")
        assert missing_z["classification"]["status"] == "geometry_rescue_candidate"
        assert missing_z["pixel_spacing_mm"] == {"row": 0.4, "column": 0.6}
        assert missing_z["projected_slice_spacing_mm"] is None
        assert missing_z["spacing_between_slices"]["values_mm"] == [1.5]
        assert missing_z["slice_thickness"]["values_mm"] == [2]

        irregular_z = series_by_uid(payload, "1.2.3.partial.irregular.z")
        assert irregular_z["classification"]["status"] == "geometry_rescue_candidate"
        assert irregular_z["projected_slice_spacing_mm"] is None
        assert irregular_z["slice_thickness"]["values_mm"] == [1.1]

        output = root / "exported_partial"
        proc = run(
            binary,
            "export-rescue-stack",
            "--dicom-dir",
            str(root),
            "--series-key",
            "1.2.3.partial.missing.z",
            "--output",
            str(output),
        )
        assert proc.returncode == 0, proc.stderr
        manifest = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))
        assert manifest["classification"] == "geometry_rescue_candidate"
        assert stat.S_IMODE(output.stat().st_mode) == 0o700
        assert stat.S_IMODE((output / "preview_stack.npy").stat().st_mode) == 0o600
        assert stat.S_IMODE((output / "source_manifest.json").stat().st_mode) == 0o600


def test_ordered_content_sha256_manifest_is_path_independent(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        instances = (3, 1, 2)
        source_files: list[Path] = []
        for instance in instances:
            path = source / f"patient-name-{instance}.dcm"
            write_dicom(
                path,
                sop=SC_SOP,
                series_number=193,
                series_uid="1.2.3.sha.manifest",
                instance_number=instance,
                description="AXIAL BO",
                modality="OT",
                image_type="DERIVED\\SECONDARY\\SCREEN SAVE\\AXIAL",
                geometry=False,
                secondary_capture=True,
            )
            source_files.append(path)

        renamed = root / "renamed"
        renamed.mkdir()
        for index, path in enumerate(reversed(source_files)):
            shutil.copyfile(path, renamed / f"unrelated-{index}.bin")

        source_payload = load_audit(binary, source)
        renamed_payload = load_audit(binary, renamed)
        source_manifest = series_by_uid(
            source_payload, "1.2.3.sha.manifest"
        )["ordered_content_manifest"]
        renamed_manifest = series_by_uid(
            renamed_payload, "1.2.3.sha.manifest"
        )["ordered_content_manifest"]

        assert source_manifest == renamed_manifest
        assert source_manifest["algorithm"] == "sha256"
        assert source_manifest["ordering"] == "instance_number_then_content_sha256"
        assert source_manifest["entry_count"] == 3
        assert [entry["instance_number"] for entry in source_manifest["entries"]] == [1, 2, 3]
        assert all("path" not in entry for entry in source_manifest["entries"])
        manifest_text = json.dumps(source_manifest)
        assert "patient-name" not in manifest_text
        assert str(source) not in manifest_text

        expected_hashes = {
            instance: hashlib.sha256(path.read_bytes()).hexdigest()
            for instance, path in zip(instances, source_files, strict=True)
        }
        assert {
            entry["instance_number"]: entry["content_sha256"]
            for entry in source_manifest["entries"]
        } == expected_hashes
        canonical = "".join(
            f"I:{instance}\tH:{expected_hashes[instance]}\n"
            for instance in sorted(expected_hashes)
        ).encode("ascii")
        assert source_manifest["manifest_sha256"] == hashlib.sha256(canonical).hexdigest()


def read_npy(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    assert data[:6] == b"\x93NUMPY"
    assert data[6:8] == b"\x01\x00"
    header_length = struct.unpack_from("<H", data, 8)[0]
    header = ast.literal_eval(data[10 : 10 + header_length].decode("ascii").strip())
    return header, data[10 + header_length :]


def test_export_rescue_stack_patterned_voxel_order(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dicom = root / "dicom"
        for instance in range(32, 0, -1):
            values = [instance * 100 + offset for offset in range(6)]
            write_dicom(
                dicom / f"filesystem-order-{33 - instance:03d}.dcm",
                sop=SC_SOP,
                series_number=194,
                series_uid="1.2.3.stack.pattern",
                instance_number=instance,
                description="AXIAL BO",
                modality="OT",
                image_type="DERIVED\\SECONDARY\\SCREEN SAVE\\AXIAL",
                rows=2,
                columns=3,
                geometry=False,
                secondary_capture=True,
                pixel_bytes=struct.pack("<6h", *values),
            )

        output = root / "output"
        proc = run(
            binary,
            "export-rescue-stack",
            "--dicom-dir",
            str(dicom),
            "--series-number",
            "194",
            "--output",
            str(output),
        )
        assert proc.returncode == 0, proc.stderr
        metadata = json.loads(
            (output / "source_manifest.json").read_text(encoding="utf-8")
        )
        assert metadata["status"] == "success"
        assert metadata["classification"] == "secondary_capture_rescue_candidate"
        assert metadata["array"]["shape_xyz"] == [3, 2, 32]
        assert metadata["array"]["size_x"] == 3
        assert metadata["array"]["size_y"] == 2
        assert metadata["array"]["size_z"] == 32
        assert metadata["array"]["axis_order"] == ["x", "y", "z"]
        assert metadata["array"]["storage_order"] == "x_fastest"
        assert metadata["array"]["fortran_order"] is True
        assert metadata["array"]["dtype"] == "<i2"
        assert metadata["ordering"]["source"] == "instance_number"
        assert metadata["ordering"]["ambiguous"] is False
        assert all("path" not in entry for entry in metadata["source"]["entries"])

        header, payload = read_npy(output / "preview_stack.npy")
        assert header == {
            "descr": "<i2",
            "fortran_order": True,
            "shape": (3, 2, 32),
        }
        assert len(payload) == 32 * 2 * 3 * 2
        actual = struct.unpack("<192h", payload)
        expected = tuple(
            instance * 100 + offset
            for instance in range(1, 33)
            for offset in range(6)
        )
        assert actual == expected
        size_x, size_y, size_z = header["shape"]
        for z in range(size_z):
            for y in range(size_y):
                for x in range(size_x):
                    fortran_index = x + size_x * (y + size_y * z)
                    assert actual[fortran_index] == (z + 1) * 100 + y * size_x + x


def test_rescue_pixel_semantics_are_rejected_before_export(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cases = (
            (
                "monochrome1",
                "rescue_monochrome1_unsupported",
                {
                    "photometric_interpretation": "MONOCHROME1",
                    "bits_allocated": 8,
                    "pixel_representation": 0,
                    "pixel_bytes": bytes([1, 1, 2, 3, 4, 5, 6, 7]),
                },
            ),
            (
                "signed8",
                "rescue_signed_8bit_unsupported",
                {
                    "bits_allocated": 8,
                    "pixel_representation": 1,
                    "pixel_bytes": bytes([1, 2, 3, 4, 5, 6, 7, 8]),
                },
            ),
            (
                "nonidentity_rescale",
                "rescue_nonidentity_rescale_transform",
                {"rescale_slope": "1", "rescale_intercept": "-1024"},
            ),
            (
                "incomplete_rescale",
                "rescue_incomplete_rescale_transform",
                {"rescale_slope": "1"},
            ),
            (
                "modality_lut",
                "rescue_modality_lut_unsupported",
                {"modality_lut": True},
            ),
            (
                "shared_pixel_value_transform",
                "rescue_shared_pixel_value_transform_unsupported",
                {"shared_pixel_value_transform": True},
            ),
            (
                "per_frame_pixel_value_transform",
                "rescue_per_frame_pixel_value_transform_unsupported",
                {"per_frame_pixel_value_transform": True},
            ),
        )
        for index, (name, expected_reason, kwargs) in enumerate(cases, start=1):
            dicom = root / name
            series_number = 2000 + index
            series_uid = f"1.2.3.semantics.{name}"
            for instance in range(1, 33):
                write_dicom(
                    dicom / f"{instance:04d}.dcm",
                    sop=SC_SOP,
                    series_number=series_number,
                    series_uid=series_uid,
                    instance_number=instance,
                    description="AXIAL BO",
                    modality="OT",
                    image_type="DERIVED\\SECONDARY\\SCREEN SAVE\\AXIAL",
                    rows=2,
                    columns=4,
                    geometry=False,
                    secondary_capture=True,
                    **kwargs,
                )
            payload = load_audit(binary, dicom)
            series = series_by_uid(payload, series_uid)
            assert (
                series["classification"]["status"]
                == "secondary_capture_rescue_candidate"
            )
            assert series["classification"]["reject_reason"] is None

            output = root / f"{name}_output"
            proc = run(
                binary,
                "export-rescue-stack",
                "--dicom-dir",
                str(dicom),
                "--series-key",
                series_uid,
                "--output",
                str(output),
            )
            assert proc.returncode != 0
            assert expected_reason in proc.stderr
            assert not (output / "preview_stack.npy").exists()
            assert not (output / "source_manifest.json").exists()


def test_normal_ct_rescale_remains_clean_and_converts(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dicom = root / "dicom"
        series_uid = "1.2.3.clean.nonidentity.rescale"
        for instance in range(1, 33):
            write_dicom(
                dicom / f"{instance:04d}.dcm",
                series_number=2051,
                series_uid=series_uid,
                instance_number=instance,
                rows=2,
                columns=4,
                rescale_slope="1",
                rescale_intercept="-1024",
            )

        payload = load_audit(binary, dicom)
        series = series_by_uid(payload, series_uid)
        assert series["classification"]["status"] == "original_ct_geometry_ok"
        assert series["classification"]["reject_reason"] is None

        fake = root / "fake_dcm2niix.py"
        write_fake_dcm2niix(fake, shape=(2, 4, 32), spacing=(0.5, 0.5, 1.0))
        output = root / "clean_output"
        proc = run(
            binary,
            "convert-clean",
            "--dicom-dir",
            str(dicom),
            "--series-key",
            series_uid,
            "--output",
            str(output),
            "--dcm2niix",
            str(fake),
        )
        assert proc.returncode == 0, proc.stderr
        metadata = json.loads(
            (output / "convert_clean_metadata.json").read_text(encoding="utf-8")
        )
        assert metadata["status"] == "success"
        assert metadata["selected_series"]["classification"] == "original_ct_geometry_ok"


def test_missing_series_uid_uses_study_and_frame_identity_without_paths(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dicom = root / "dicom"
        identities = (
            ("1.2.826.0.1.3680043.10.543.101", "1.2.826.0.1.3680043.10.543.201"),
            ("1.2.826.0.1.3680043.10.543.101", "1.2.826.0.1.3680043.10.543.202"),
            ("1.2.826.0.1.3680043.10.543.102", "1.2.826.0.1.3680043.10.543.201"),
            ("1.2.826.0.1.3680043.10.543.103", None),
            (None, "1.2.826.0.1.3680043.10.543.203"),
        )
        for group, (study_uid, frame_uid) in enumerate(identities, start=1):
            for instance in range(1, 33):
                write_dicom(
                    dicom / f"group-{group}" / f"patient-name-{instance:04d}.dcm",
                    sop_instance_uid=f"1.2.826.0.1.3680043.10.543.9.{group}.{instance}",
                    study_uid=study_uid,
                    frame_of_reference_uid=frame_uid,
                    series_uid=f"1.2.826.0.1.3680043.10.543.8.{group}",
                    include_series_uid=False,
                    series_number=2111,
                    instance_number=instance,
                    description="MALFORMED AXIAL CT",
                )

        payload = load_audit(binary, dicom)
        assert payload["series_count"] == 5
        assert len(payload["series"]) == 5
        assert {series["file_count"] for series in payload["series"]} == {32}
        assert {
            series["classification"]["status"] for series in payload["series"]
        } == {"original_ct_geometry_ok"}
        keys = {series["series_key"] for series in payload["series"]}
        assert len(keys) == 5
        assert all(key.startswith("missing-series-uid:") for key in keys)
        assert all(series["series_instance_uid"] is None for series in payload["series"])
        assert {
            series["study_key_sha256"] for series in payload["series"]
        } == {
            hashlib.sha256(uid.encode("utf-8")).hexdigest() if uid is not None else None
            for uid, _ in identities
        }
        assert {
            series["frame_of_reference_key_sha256"] for series in payload["series"]
        } == {
            hashlib.sha256(uid.encode("utf-8")).hexdigest() if uid is not None else None
            for _, uid in identities
        }

        public_audit = json.dumps(payload, sort_keys=True)
        assert str(root) not in public_audit
        assert "patient-name" not in public_audit
        for study_uid, frame_uid in identities:
            if study_uid is not None:
                assert study_uid not in public_audit
            if frame_uid is not None:
                assert frame_uid not in public_audit


def test_missing_all_stable_series_identity_is_fail_closed(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dicom = root / "dicom"
        for instance in range(1, 33):
            write_dicom(
                dicom / f"patient-name-{instance:04d}.dcm",
                sop_instance_uid=f"1.2.826.0.1.3680043.10.543.7.{instance}",
                series_uid="1.2.826.0.1.3680043.10.543.7",
                include_series_uid=False,
                series_number=2112,
                instance_number=instance,
                description="MALFORMED AXIAL CT",
            )

        payload = load_audit(binary, dicom)
        assert payload["series_count"] == 1
        series = payload["series"][0]
        assert series["series_key"].startswith("missing-series-uid:")
        assert series["file_count"] == 32
        assert series["classification"]["status"] == "reject"
        assert (
            series["classification"]["reject_reason"]
            == "missing_stable_series_grouping_identity"
        )
        assert "missing_stable_series_grouping_identity" in series["classification"]["reasons"]

        output = root / "must-not-convert"
        fake = root / "fake_dcm2niix.py"
        write_fake_dcm2niix(fake)
        proc = run(
            binary,
            "convert-clean",
            "--dicom-dir",
            str(dicom),
            "--series-key",
            series["series_key"],
            "--output",
            str(output),
            "--dcm2niix",
            str(fake),
        )
        assert proc.returncode != 0
        assert "missing_stable_series_grouping_identity" in proc.stderr
        assert not output.exists()

        public_audit = json.dumps(payload, sort_keys=True)
        assert str(root) not in public_audit
        assert "patient-name" not in public_audit


def test_series_uid_group_rejects_cross_file_identity_inconsistency(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        study_a = "1.2.826.0.1.3680043.10.543.301"
        study_b = "1.2.826.0.1.3680043.10.543.302"
        frame_a = "1.2.826.0.1.3680043.10.543.401"
        frame_b = "1.2.826.0.1.3680043.10.543.402"
        cases = (
            (
                "mixed-values",
                [(study_a, frame_a)] * 16 + [(study_b, frame_b)] * 16,
                {"mixed_study_instance_uid", "mixed_frame_of_reference_uid"},
            ),
            (
                "inconsistent-presence",
                [(study_a, frame_a)] * 16 + [(None, None)] * 16,
                {
                    "inconsistent_study_instance_uid_presence",
                    "inconsistent_frame_of_reference_uid_presence",
                },
            ),
            (
                "consistent-present",
                [(study_a, frame_a)] * 32,
                set(),
            ),
        )

        for case_index, (name, identities, expected_reasons) in enumerate(cases, start=1):
            dicom = root / name / "dicom"
            series_uid = f"1.2.826.0.1.3680043.10.543.500.{case_index}"
            for instance, (study_uid, frame_uid) in enumerate(identities, start=1):
                write_dicom(
                    dicom / f"cohort-{instance > 16}" / f"patient-name-{instance:04d}.dcm",
                    sop_instance_uid=f"{series_uid}.{instance}",
                    study_uid=study_uid,
                    frame_of_reference_uid=frame_uid,
                    series_uid=series_uid,
                    series_number=2200 + case_index,
                    instance_number=instance,
                    description="AXIAL CT",
                )

            payload = load_audit(binary, dicom)
            assert payload["series_count"] == 1
            series = payload["series"][0]
            assert series["series_key"] == series_uid
            assert series["series_instance_uid"] == series_uid
            assert series["file_count"] == 32

            public_audit = json.dumps(payload, sort_keys=True)
            assert str(root) not in public_audit
            assert "patient-name" not in public_audit
            assert study_a not in public_audit
            assert study_b not in public_audit
            assert frame_a not in public_audit
            assert frame_b not in public_audit

            if not expected_reasons:
                assert series["classification"]["status"] == "original_ct_geometry_ok"
                continue

            assert series["classification"]["status"] == "reject"
            assert expected_reasons.issubset(set(series["classification"]["reasons"]))
            assert all(
                reason in series["classification"]["reject_reason"]
                for reason in expected_reasons
            )

            fake = root / name / "fake_dcm2niix.py"
            write_fake_dcm2niix(fake)
            clean_output = root / name / "must-not-convert"
            clean = run(
                binary,
                "convert-clean",
                "--dicom-dir",
                str(dicom),
                "--series-key",
                series_uid,
                "--output",
                str(clean_output),
                "--dcm2niix",
                str(fake),
            )
            assert clean.returncode != 0
            assert all(reason in clean.stderr for reason in expected_reasons)
            assert not clean_output.exists()

            rescue_output = root / name / "must-not-rescue"
            rescue = run(
                binary,
                "export-rescue-stack",
                "--dicom-dir",
                str(dicom),
                "--series-key",
                series_uid,
                "--output",
                str(rescue_output),
            )
            assert rescue.returncode != 0
            assert all(reason in rescue.stderr for reason in expected_reasons)
            assert not rescue_output.exists()


def test_duplicate_series_number_requires_series_key(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        series_number = 2042
        selected_uid = "1.2.3.duplicate.selected"
        other_uid = "1.2.3.duplicate.other"
        for uid, marker, count in ((selected_uid, 100, 32), (other_uid, 200, 40)):
            for instance in range(1, count + 1):
                write_dicom(
                    root / uid / f"{instance:04d}.dcm",
                    sop=SC_SOP,
                    series_number=series_number,
                    series_uid=uid,
                    instance_number=instance,
                    description="AXIAL BO",
                    modality="OT",
                    image_type="DERIVED\\SECONDARY\\SCREEN SAVE\\AXIAL",
                    rows=2,
                    columns=3,
                    geometry=False,
                    secondary_capture=True,
                    pixel_bytes=struct.pack("<6h", *(marker + offset for offset in range(6))),
                )

        ambiguous_output = root / "ambiguous_output"
        ambiguous = run(
            binary,
            "export-rescue-stack",
            "--dicom-dir",
            str(root),
            "--series-number",
            str(series_number),
            "--output",
            str(ambiguous_output),
        )
        assert ambiguous.returncode != 0
        assert "ambiguous_series_number_use_series_key" in ambiguous.stderr
        assert not (ambiguous_output / "preview_stack.npy").exists()

        selected_output = root / "selected_output"
        selected = run(
            binary,
            "export-rescue-stack",
            "--dicom-dir",
            str(root),
            "--series-key",
            selected_uid,
            "--output",
            str(selected_output),
        )
        assert selected.returncode == 0, selected.stderr
        header, payload = read_npy(selected_output / "preview_stack.npy")
        assert header["shape"] == (3, 2, 32)
        assert struct.unpack("<6h", payload[:12]) == tuple(100 + offset for offset in range(6))


def test_export_rescue_stack_rejects_unsupported_inputs(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def write_stack(
            name: str,
            series_number: int,
            **kwargs: object,
        ) -> Path:
            dicom = root / name
            for instance in range(1, 33):
                write_dicom(
                    dicom / f"{instance:04d}.dcm",
                    sop=SC_SOP,
                    series_number=series_number,
                    series_uid=f"1.2.3.unsupported.{series_number}",
                    instance_number=instance,
                    description="AXIAL BO",
                    modality="OT",
                    image_type="DERIVED\\SECONDARY\\SCREEN SAVE\\AXIAL",
                    rows=2,
                    columns=4,
                    geometry=False,
                    secondary_capture=True,
                    **kwargs,
                )
            return dicom

        rgb = write_stack(
            "rgb",
            195,
            samples_per_pixel=3,
            photometric_interpretation="RGB",
            bits_allocated=8,
            pixel_representation=0,
        )
        ybr = write_stack(
            "ybr",
            196,
            samples_per_pixel=3,
            photometric_interpretation="YBR_FULL",
            bits_allocated=8,
            pixel_representation=0,
        )
        mixed = root / "mixed"
        for instance in range(1, 33):
            bits = 8 if instance == 32 else 16
            write_dicom(
                mixed / f"{instance:04d}.dcm",
                sop=SC_SOP,
                series_number=197,
                series_uid="1.2.3.unsupported.mixed",
                instance_number=instance,
                description="AXIAL BO",
                modality="OT",
                image_type="DERIVED\\SECONDARY\\SCREEN SAVE\\AXIAL",
                rows=2,
                columns=4,
                geometry=False,
                secondary_capture=True,
                bits_allocated=bits,
                pixel_representation=0,
            )
        ambiguous = root / "ambiguous"
        for ordinal in range(1, 33):
            instance = 31 if ordinal == 32 else ordinal
            write_dicom(
                ambiguous / f"{ordinal:04d}.dcm",
                sop=SC_SOP,
                series_number=199,
                series_uid="1.2.3.unsupported.ambiguous",
                sop_instance_uid=f"1.2.3.unsupported.ambiguous.{ordinal}",
                instance_number=instance,
                description="AXIAL BO",
                modality="OT",
                image_type="DERIVED\\SECONDARY\\SCREEN SAVE\\AXIAL",
                rows=2,
                columns=4,
                geometry=False,
                secondary_capture=True,
            )

        for name, series_number, dicom, expected_error in (
            ("rgb", 195, rgb, "unsupported_samples_per_pixel"),
            ("ybr", 196, ybr, "unsupported_samples_per_pixel"),
            ("mixed", 197, mixed, "mixed_pixel_format"),
            ("ambiguous", 199, ambiguous, "ambiguous_instance_order"),
        ):
            output = root / f"{name}_output"
            proc = run(
                binary,
                "export-rescue-stack",
                "--dicom-dir",
                str(dicom),
                "--series-number",
                str(series_number),
                "--output",
                str(output),
            )
            assert proc.returncode != 0
            assert expected_error in proc.stderr
            assert not (output / "preview_stack.npy").exists()
            assert not (output / "preview_stack.npy.partial").exists()
            assert not (output / "source_manifest.json").exists()


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
        assert "prepare-rescue requires a geometry rescue candidate" in bad_rescue.stderr

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
    if len(sys.argv) != 3:
        print("usage: test_normalizer.py <binary> <gdcmconv>", file=sys.stderr)
        return 2
    binary = Path(sys.argv[1])
    gdcmconv = Path(sys.argv[2])
    tests = [
        test_doctor,
        test_clean_ct,
        test_ct_without_original_image_type_is_clean_when_geometry_is_complete,
        test_malformed_compressed_never_falls_back,
        test_enhanced_ct_decodes_and_requires_explicit_geometry_rescue,
        test_dicomdir_and_implicit,
        test_secondary_capture_variants,
        test_single_multiframe_dicom_file_input_and_rescue_stack,
        test_single_multiframe_identity_rescale_remains_rescueable,
        test_geometry_evidence_and_secondary_capture_references,
        test_partial_geometry_ct_is_rescue_candidate,
        test_ordered_content_sha256_manifest_is_path_independent,
        test_export_rescue_stack_patterned_voxel_order,
        test_rescue_pixel_semantics_are_rejected_before_export,
        test_normal_ct_rescale_remains_clean_and_converts,
        test_missing_series_uid_uses_study_and_frame_identity_without_paths,
        test_missing_all_stable_series_identity_is_fail_closed,
        test_series_uid_group_rejects_cross_file_identity_inconsistency,
        test_duplicate_series_number_requires_series_key,
        test_export_rescue_stack_rejects_unsupported_inputs,
        test_convert_clean_and_prepare_rescue,
        test_viewer_export_mpr_mixed_candidate_and_prepare,
        test_viewer_export_without_axial_group_is_not_ai_rescue_candidate,
    ]
    for test in tests:
        test(binary)
        print(f"ok {test.__name__}")
    test_real_compressed_codecs_and_native_transcode(binary, gdcmconv)
    print("ok test_real_compressed_codecs_and_native_transcode")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
