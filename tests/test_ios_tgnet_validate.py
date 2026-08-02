from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from totalsegmentator_wrapper_mac.ios_tgnet_final import TGNET_FINAL_CHECKPOINTS
from totalsegmentator_wrapper_mac.ios_tgnet_validate import (
    TGNetSelectionValidationError,
    main,
    validate_selection,
)


class IOSTGNetSelectionValidationTests(unittest.TestCase):
    @staticmethod
    def _fixture_specifications(contents: dict[str, bytes]) -> dict[str, dict[str, object]]:
        return {
            role: {
                "filename": specification["filename"],
                "size_bytes": len(contents[str(specification["filename"])]),
                "sha256": hashlib.sha256(
                    contents[str(specification["filename"])]
                ).hexdigest(),
            }
            for role, specification in TGNET_FINAL_CHECKPOINTS.items()
        }

    @staticmethod
    def _fixture_contents() -> dict[str, bytes]:
        return {
            "tgnet_fps.h5": b"fps fixture",
            "tgnet_bdl.h5": b"boundary fixture",
        }

    def _patch_fixture_specifications(
        self,
        specifications: dict[str, dict[str, object]],
    ):
        return patch.multiple(
            "totalsegmentator_wrapper_mac.ios_tgnet_validate",
            TGNET_FINAL_CHECKPOINTS=specifications,
        ), patch.multiple(
            "totalsegmentator_wrapper_mac.ios_tgnet_final",
            TGNET_FINAL_CHECKPOINTS=specifications,
        )

    def test_accepts_directory_only_after_both_pinned_hashes_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contents = {
                "tgnet_fps.h5": b"fps fixture",
                "tgnet_bdl.h5": b"boundary fixture",
            }
            specifications = {
                role: {
                    "filename": specification["filename"],
                    "size_bytes": len(contents[str(specification["filename"])]),
                    "sha256": specification["sha256"],
                }
                for role, specification in TGNET_FINAL_CHECKPOINTS.items()
            }
            for filename, payload in contents.items():
                (root / filename).write_bytes(payload)

            def pinned_hash(path: Path) -> str:
                for specification in TGNET_FINAL_CHECKPOINTS.values():
                    if path.name == specification["filename"]:
                        return str(specification["sha256"])
                raise AssertionError(path)

            with patch(
                "totalsegmentator_wrapper_mac.ios_tgnet_validate._sha256",
                side_effect=pinned_hash,
            ), patch(
                "totalsegmentator_wrapper_mac.ios_tgnet_validate.TGNET_FINAL_CHECKPOINTS",
                specifications,
            ), patch(
                "totalsegmentator_wrapper_mac.ios_tgnet_final.TGNET_FINAL_CHECKPOINTS",
                specifications,
            ), patch(
                "totalsegmentator_wrapper_mac.ios_tgnet_final._sha256",
                side_effect=pinned_hash,
            ):
                document = validate_selection(root)

            self.assertEqual(document["status"], "success")
            self.assertEqual(document["selection_type"], "directory")
            self.assertEqual(
                document["variant"],
                "published-behavior-fps-plus-boundary",
            )
            self.assertEqual(
                [item["role"] for item in document["checkpoints"]],
                ["fps", "boundary"],
            )

    def test_rejects_non_zip_file_with_actionable_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "checkpoint.h5"
            selected.write_bytes(b"not-the-pinned-set")
            with self.assertRaisesRegex(ValueError, r"ckpts\(new\)\.zip"):
                validate_selection(selected)

    def test_accepts_direct_otherwise_valid_checkpoint_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contents = self._fixture_contents()
            archive = root / "ckpts(new).zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                for filename, payload in contents.items():
                    bundle.writestr(filename, payload)
            specifications = self._fixture_specifications(contents)
            validate_patch, final_patch = self._patch_fixture_specifications(
                specifications
            )
            with validate_patch, final_patch:
                document = validate_selection(archive)

            self.assertEqual(document["status"], "success")
            self.assertEqual(document["selection_type"], "zip")

    def test_rejects_symlink_to_otherwise_valid_checkpoint_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_directory = root / "ckpts(new)"
            checkpoint_directory.mkdir()
            contents = self._fixture_contents()
            for filename, payload in contents.items():
                (checkpoint_directory / filename).write_bytes(payload)
            selection = root / "checkpoint-alias"
            selection.symlink_to(checkpoint_directory, target_is_directory=True)
            specifications = self._fixture_specifications(contents)
            validate_patch, final_patch = self._patch_fixture_specifications(
                specifications
            )
            with validate_patch, final_patch, self.assertRaises(
                TGNetSelectionValidationError
            ) as raised:
                validate_selection(selection)

            self.assertEqual(raised.exception.code, "tgnet_selection_invalid")
            self.assertEqual(
                raised.exception.safe_detail,
                "指定のckpts(new).zip、またはその展開済みフォルダを選択してください。",
            )

    def test_rejects_symlink_to_otherwise_valid_checkpoint_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contents = self._fixture_contents()
            archive = root / "ckpts(new).zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                for filename, payload in contents.items():
                    bundle.writestr(filename, payload)
            selection = root / "checkpoint-alias.zip"
            selection.symlink_to(archive)
            specifications = self._fixture_specifications(contents)
            validate_patch, final_patch = self._patch_fixture_specifications(
                specifications
            )
            with validate_patch, final_patch, self.assertRaises(
                TGNetSelectionValidationError
            ) as raised:
                validate_selection(selection)

            self.assertEqual(raised.exception.code, "tgnet_selection_invalid")
            self.assertEqual(
                raised.exception.safe_detail,
                "指定のckpts(new).zip、またはその展開済みフォルダを選択してください。",
            )

    def test_failure_keeps_raw_details_in_local_log_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selected = root / "ckpts(new)"
            selected.mkdir()
            result = root / "validation.json"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(
                    main(["--model", str(selected), "--json", str(result)]),
                    2,
                )
            document = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(document["status"], "failed")
            self.assertEqual(
                document["message"],
                "TGNetの重みを確認できませんでした。",
            )
            self.assertEqual(
                document["error_code"],
                "tgnet_checkpoint_set_incomplete",
            )
            self.assertEqual(
                document["safe_detail"],
                "必要な2つのcheckpointが揃っていないか、配置が異なります。",
            )
            self.assertNotIn("details", document)
            self.assertNotIn(str(root), json.dumps(document, ensure_ascii=False))
            self.assertIn("missing required files", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
