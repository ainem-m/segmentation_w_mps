from __future__ import annotations

import hashlib
import stat
import subprocess
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from scripts import repair_macos_release_dependency_wheels as repair


class RepairMacOSReleaseDependencyWheelsTests(unittest.TestCase):
    def test_policy_is_exactly_pinned_to_open3d(self) -> None:
        self.assertEqual(
            repair.REPAIR_POLICY,
            "0.4.1-exact-open3d-0.19.0-v1",
        )
        self.assertEqual(
            repair.SIGNED_REPAIR_POLICY,
            "0.4.1-exact-open3d-0.19.0-developer-id-v1",
        )
        self.assertEqual(repair.RELEASE_TEAM_IDENTIFIER, "8632JF4773")
        self.assertEqual(
            repair.OPEN3D_SPEC,
            repair.WheelSpec(
                "open3d",
                "open3d-0.19.0-cp312-cp312-macosx_10_15_universal2.whl",
                "9e4a8d29443ba4c83010d199d56c96bf553dd970d3351692ab271759cbe2d7ac",
                "b71b3ffd13427a01a6d1caab8af98d6dc9d1eb3c60ce2b32cbe4ce602168153d",
                "open3d-0.19.0.dist-info",
            ),
        )
        self.assertEqual(
            set(repair.OPEN3D_REMOVED_MEMBERS),
            {
                "open3d/cpu/open3d_tf_ops.dylib",
                "open3d/cpu/open3d_torch_ops.dylib",
            },
        )
        self.assertEqual(
            repair.OPEN3D_RETAINED_MACHOS,
            (
                "open3d/cpu/pybind.cpython-312-darwin.so",
                "open3d/libomp.dylib",
                "open3d/libtbb.12.dylib",
            ),
        )

    def test_safe_member_rejects_traversal_and_symlink(self) -> None:
        traversal = zipfile.ZipInfo("../escape")
        with self.assertRaisesRegex(repair.WheelRepairError, "unsafe member path"):
            repair._safe_member_path(traversal)

        link = zipfile.ZipInfo("package/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaisesRegex(repair.WheelRepairError, "non-regular member"):
            repair._safe_member_path(link)

    def test_extract_rejects_duplicate_member_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheel = root / "duplicate.whl"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(wheel, "w") as archive:
                    archive.writestr("package/value.py", "first")
                    archive.writestr("package/value.py", "second")
            with self.assertRaisesRegex(repair.WheelRepairError, "duplicate member"):
                repair._extract_wheel(wheel, root / "extracted")

    def test_record_and_repack_are_complete_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unpacked = root / "unpacked"
            dist_info = unpacked / "demo-1.0.dist-info"
            package = unpacked / "demo"
            dist_info.mkdir(parents=True)
            package.mkdir()
            (dist_info / "METADATA").write_text(
                "Metadata-Version: 2.4\nName: demo\nVersion: 1.0\n",
                encoding="utf-8",
            )
            (dist_info / "WHEEL").write_text(
                "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                encoding="utf-8",
            )
            (dist_info / "RECORD").write_text("stale\n", encoding="utf-8")
            (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")

            record_name = repair._regenerate_record(unpacked, "demo-1.0.dist-info")
            self.assertEqual(record_name, "demo-1.0.dist-info/RECORD")
            first = root / "first.whl"
            second = root / "second.whl"
            repair._repack_wheel(unpacked, first)
            repair._repack_wheel(unpacked, second)
            repair._verify_record(first, "demo-1.0.dist-info")
            repair._verify_record(second, "demo-1.0.dist-info")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).hexdigest(),
                hashlib.sha256(second.read_bytes()).hexdigest(),
            )

    def test_open3d_build_config_patch_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "open3d" / "_build_config.py"
            config.parent.mkdir()
            config.write_text(
                "_build_config = {\n"
                '    "BUILD_TENSORFLOW_OPS" : True,\n'
                '    "BUILD_PYTORCH_OPS" : True,\n'
                '    "Tensorflow_VERSION" : "2.16.2",\n'
                '    "Pytorch_VERSION" : "2.2.2",\n'
                "}\n",
                encoding="utf-8",
            )
            relative = repair._patch_open3d_build_config(root)
            self.assertEqual(relative, "open3d/_build_config.py")
            text = config.read_text(encoding="utf-8")
            self.assertIn('"BUILD_TENSORFLOW_OPS" : False', text)
            self.assertIn('"BUILD_PYTORCH_OPS" : False', text)
            self.assertIn('"Tensorflow_VERSION" : ""', text)
            self.assertIn('"Pytorch_VERSION" : ""', text)
            with self.assertRaisesRegex(
                repair.WheelRepairError,
                "precondition changed",
            ):
                repair._patch_open3d_build_config(root)

    def test_ad_hoc_sign_has_no_identity_or_timestamp_expansion(self) -> None:
        commands: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "native.dylib"
            binary.write_bytes(b"native")
            repair._ad_hoc_sign(binary, runner=runner)

        self.assertEqual(
            commands[0][:-1],
            [
                "/usr/bin/codesign",
                "--force",
                "--sign",
                "-",
                "--timestamp=none",
            ],
        )
        self.assertEqual(
            commands[1][:-1],
            [
                "/usr/bin/codesign",
                "--verify",
                "--strict",
                "--verbose=2",
            ],
        )

    def test_exact_tree_change_guard_rejects_unapproved_bytes(self) -> None:
        before = {"keep": "a", "change": "b", "remove": "c"}
        after = {"keep": "a", "change": "d"}
        repair._assert_exact_tree_changes(
            before,
            after,
            changed={"change"},
            removed={"remove"},
        )
        with self.assertRaisesRegex(repair.WheelRepairError, "outside"):
            repair._assert_exact_tree_changes(
                before,
                {"keep": "different", "change": "d"},
                changed={"change"},
                removed={"remove"},
            )

    def test_output_directory_must_be_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(repair.WheelRepairError, "must be absent"):
                repair.rewrite_open3d_release_wheel(
                    open3d_wheel=root / repair.OPEN3D_FILENAME,
                    output_directory=root,
                )


if __name__ == "__main__":
    unittest.main()
