from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts import verify_license_distribution as distribution_guard
from scripts.verify_license_distribution import (
    KNOWN_NON_BUNDLED_CHECKPOINT_HASHES,
    find_archive_model_payloads,
    find_tree_model_payloads,
)


class IOSTGNetDistributionGuardTests(unittest.TestCase):
    def test_source_guard_rejects_any_tracked_checkpoint_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "adapter.py"
            checkpoint = root / "user_checkpoint.h5"
            source.write_text("pass\n", encoding="utf-8")
            checkpoint.write_bytes(b"checkpoint")

            self.assertEqual(
                find_tree_model_payloads(
                    root,
                    [source, checkpoint],
                    reject_all_checkpoint_extensions=True,
                ),
                ["user_checkpoint.h5"],
            )

    def test_wheel_guard_rejects_large_checkpoint_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "test.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("pkg/adapter.py", b"pass\n")
                archive.writestr("pkg/renamed_model.ckpt", b"x" * (1024 * 1024 + 1))
            with zipfile.ZipFile(wheel) as archive:
                self.assertEqual(
                    find_archive_model_payloads(archive),
                    ["pkg/renamed_model.ckpt"],
                )

    def test_app_tree_guard_rejects_ckpts_new_zip_even_when_small(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_bundle = root / "ckpts(new).zip"
            with zipfile.ZipFile(checkpoint_bundle, "w") as archive:
                archive.writestr("readme.txt", "user-provided TGNet weights")

            self.assertEqual(
                find_tree_model_payloads(root, [checkpoint_bundle]),
                ["ckpts(new).zip"],
            )

    def test_app_tree_guard_rejects_named_checkpoint_even_when_small(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "tgnet_bdl.h5"
            checkpoint.write_bytes(b"small fixture")

            self.assertEqual(
                find_tree_model_payloads(root, [checkpoint]),
                ["tgnet_bdl.h5"],
            )

    def test_app_tree_guard_inspects_renamed_zip_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_bundle = root / "assets.zip"
            with zipfile.ZipFile(checkpoint_bundle, "w") as archive:
                archive.writestr("nested/tgnet_fps.h5", b"fixture")

            self.assertEqual(
                find_tree_model_payloads(root, [checkpoint_bundle]),
                ["assets.zip!nested/tgnet_fps.h5"],
            )

    def test_app_tree_guard_inspects_wheel_format_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "assets.whl"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("nested/tgnet_bdl.h5", b"fixture")

            self.assertEqual(
                find_tree_model_payloads(root, [archive_path]),
                ["assets.whl!nested/tgnet_bdl.h5"],
            )

    def test_archive_guard_detects_known_checkpoint_hash_after_rename(self) -> None:
        payload = b"renamed TGNet fixture"
        import hashlib

        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "test.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("pkg/model-data.bin", payload)
            with mock.patch.dict(
                KNOWN_NON_BUNDLED_CHECKPOINT_HASHES,
                {digest: "TGNet fixture"},
            ):
                with zipfile.ZipFile(wheel) as archive:
                    self.assertEqual(
                        find_archive_model_payloads(archive),
                        ["pkg/model-data.bin"],
                    )

    def test_benign_small_zip_is_not_treated_as_model_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "templates.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("template/readme.txt", "fixture")

            self.assertEqual(find_tree_model_payloads(root, [archive_path]), [])

    def test_archive_guard_fails_closed_on_member_and_size_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "payload.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("one.txt", "123")
                archive.writestr("two.txt", "456")
            with zipfile.ZipFile(archive_path) as archive:
                with mock.patch.object(distribution_guard, "MAX_ARCHIVE_MEMBERS", 1):
                    self.assertIn("<unsafe archive:", find_archive_model_payloads(archive)[0])
            with zipfile.ZipFile(archive_path) as archive:
                with mock.patch.object(
                    distribution_guard,
                    "MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES",
                    5,
                ):
                    self.assertIn("<unsafe archive:", find_archive_model_payloads(archive)[0])

    def test_archive_guard_fails_closed_on_unsafe_member_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "payload.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.txt", "fixture")
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIn("unsafe path", find_archive_model_payloads(archive)[0])


if __name__ == "__main__":
    unittest.main()
