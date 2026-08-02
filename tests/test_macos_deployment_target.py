from __future__ import annotations

import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.verify_macos_deployment_target import (
    CPU_TYPE_ARM64,
    LC_BUILD_VERSION,
    LC_VERSION_MIN_MACOSX,
    MachODeploymentTargetError,
    MacOSVersion,
    parse_macho,
    scan_directory,
    verify_macho_bytes,
    verify_app_machos,
    verify_wheel_machos,
)


CPU_TYPE_X86_64 = 0x01000007


def packed_version(major: int, minor: int = 0, patch: int = 0) -> int:
    return (major << 16) | (minor << 8) | patch


def thin_macho(
    minimum: tuple[int, int, int] = (14, 0, 0),
    *,
    cputype: int = CPU_TYPE_ARM64,
    legacy: bool = False,
    include_minimum_command: bool = True,
) -> bytes:
    if include_minimum_command:
        if legacy:
            command = struct.pack(
                "<IIII",
                LC_VERSION_MIN_MACOSX,
                16,
                packed_version(*minimum),
                packed_version(14, 4, 0),
            )
        else:
            command = struct.pack(
                "<IIIIII",
                LC_BUILD_VERSION,
                24,
                1,
                packed_version(*minimum),
                packed_version(14, 4, 0),
                0,
            )
    else:
        command = struct.pack("<II", 0x19, 8)
    header = struct.pack(
        "<IiiIIIII",
        0xFEEDFACF,
        cputype,
        0,
        2,
        1,
        len(command),
        0,
        0,
    )
    return header + command


def fat_macho(slices: list[tuple[int, bytes]]) -> bytes:
    entry_size = 20
    header_size = 8 + len(slices) * entry_size
    next_offset = 4096
    entries: list[bytes] = []
    placements: list[tuple[int, bytes]] = []
    for cputype, payload in slices:
        entries.append(
            struct.pack(">iiIII", cputype, 0, next_offset, len(payload), 12)
        )
        placements.append((next_offset, payload))
        next_offset = ((next_offset + len(payload) + 4095) // 4096) * 4096
    result = bytearray(struct.pack(">II", 0xCAFEBABE, len(slices)) + b"".join(entries))
    if len(result) > header_size:
        raise AssertionError("invalid fixture header")
    for offset, payload in placements:
        result.extend(b"\0" * (offset - len(result)))
        result.extend(payload)
    return bytes(result)


class MacOSDeploymentTargetTests(unittest.TestCase):
    def test_build_version_macos_14_arm64_passes(self) -> None:
        verified = verify_macho_bytes(thin_macho(), "fixture")
        self.assertEqual(len(verified.slices), 1)
        self.assertEqual(verified.slices[0].architecture, "arm64")
        self.assertEqual(verified.slices[0].minimum_macos, MacOSVersion(14, 0))
        self.assertEqual(verified.slices[0].command, "LC_BUILD_VERSION")

    def test_macos_15_and_26_fail_against_14_floor(self) -> None:
        for major in (15, 26):
            with self.subTest(major=major):
                with self.assertRaisesRegex(
                    MachODeploymentTargetError,
                    rf"minos {major}\.0 exceeds supported macOS 14\.0",
                ):
                    verify_macho_bytes(thin_macho((major, 0, 0)), "fixture")

    def test_legacy_version_min_command_is_supported(self) -> None:
        slices = parse_macho(thin_macho((13, 4, 0), legacy=True), "legacy")
        self.assertEqual(slices[0].minimum_macos, MacOSVersion(13, 4))
        self.assertEqual(slices[0].command, "LC_VERSION_MIN_MACOSX")

    def test_fat_binary_checks_every_slice_and_requires_arm64(self) -> None:
        universal = fat_macho(
            [
                (CPU_TYPE_ARM64, thin_macho(cputype=CPU_TYPE_ARM64)),
                (CPU_TYPE_X86_64, thin_macho((13, 0, 0), cputype=CPU_TYPE_X86_64)),
            ]
        )
        verified = verify_macho_bytes(universal, "universal")
        self.assertEqual(
            {item.architecture for item in verified.slices},
            {"arm64", "x86_64"},
        )

        incompatible_slice = fat_macho(
            [
                (CPU_TYPE_ARM64, thin_macho(cputype=CPU_TYPE_ARM64)),
                (CPU_TYPE_X86_64, thin_macho((15, 0, 0), cputype=CPU_TYPE_X86_64)),
            ]
        )
        with self.assertRaisesRegex(MachODeploymentTargetError, "x86_64 minos 15.0"):
            verify_macho_bytes(incompatible_slice, "universal")

        x86_only = fat_macho(
            [(CPU_TYPE_X86_64, thin_macho(cputype=CPU_TYPE_X86_64))]
        )
        with self.assertRaisesRegex(MachODeploymentTargetError, "arm64 slice is missing"):
            verify_macho_bytes(x86_only, "x86-only")

    def test_missing_minimum_and_malformed_recognized_macho_fail(self) -> None:
        with self.assertRaisesRegex(
            MachODeploymentTargetError,
            "no macOS minimum-version load command",
        ):
            verify_macho_bytes(
                thin_macho(include_minimum_command=False),
                "missing-minimum",
            )
        with self.assertRaisesRegex(MachODeploymentTargetError, "truncated"):
            verify_macho_bytes(b"\xcf\xfa\xed\xfe", "truncated")

        malformed_fat = fat_macho(
            [(CPU_TYPE_ARM64, thin_macho(cputype=CPU_TYPE_ARM64))]
        )
        malformed_fat = malformed_fat[:15]
        with self.assertRaisesRegex(
            MachODeploymentTargetError,
            "truncated or out-of-bounds Mach-O structure",
        ):
            verify_macho_bytes(malformed_fat, "malformed-fat")

    def test_wheel_native_entry_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "fixture.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("package/__init__.py", "")
                archive.writestr("package/native.cpython-312-darwin.so", thin_macho())
            verified = verify_wheel_machos(wheel)
            self.assertEqual(len(verified), 1)
            self.assertIn("native.cpython-312-darwin.so", verified[0].label)

            incompatible = Path(tmp) / "incompatible.whl"
            with zipfile.ZipFile(incompatible, "w") as archive:
                archive.writestr(
                    "package/native.cpython-312-darwin.so",
                    thin_macho((15, 0, 0)),
                )
            with self.assertRaisesRegex(MachODeploymentTargetError, "minos 15.0"):
                verify_wheel_machos(incompatible)

            corrupt = Path(tmp) / "corrupt.whl"
            with zipfile.ZipFile(corrupt, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("package/native.so", thin_macho())
            payload = bytearray(corrupt.read_bytes())
            fixture_offset = payload.find(b"\xcf\xfa\xed\xfe")
            self.assertGreater(fixture_offset, 0)
            payload[fixture_offset + 8] ^= 0x01
            corrupt.write_bytes(payload)
            with self.assertRaisesRegex(
                MachODeploymentTargetError,
                "invalid wheel ZIP",
            ):
                verify_wheel_machos(corrupt)

    def test_app_scan_aggregates_all_incompatible_required_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            macos = app / "Contents" / "MacOS"
            native = app / "Contents" / "Resources" / "bin"
            macos.mkdir(parents=True)
            native.mkdir(parents=True)
            (macos / "TotalSegmentatorWrapperForMac").write_bytes(thin_macho())
            (native / "totalsegmentator-wrapper-dicom-normalizer").write_bytes(
                thin_macho((26, 0, 0))
            )
            (native / "dcm2niix").write_bytes(thin_macho((15, 0, 0)))
            with self.assertRaises(MachODeploymentTargetError) as caught:
                verify_app_machos(app)
            message = str(caught.exception)
            self.assertIn("totalsegmentator-wrapper-dicom-normalizer", message)
            self.assertIn("minos 26.0", message)
            self.assertIn("dcm2niix", message)
            self.assertIn("minos 15.0", message)

    def test_native_suffix_with_non_macho_payload_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake.dylib").write_text("not a binary", encoding="utf-8")
            with self.assertRaisesRegex(
                MachODeploymentTargetError,
                "not a recognized Mach-O",
            ):
                scan_directory(root)


if __name__ == "__main__":
    unittest.main()
