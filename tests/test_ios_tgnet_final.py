from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch

from totalsegmentator_wrapper_mac.ios_tgnet_final import (
    RUNTIME_ARCHITECTURE,
    TGNET_FINAL_CHECKPOINTS,
    _combine_first_heads,
    _checkpoint_metadata,
    _decode_official_first_heads,
    _instance_fdi_mapping,
    _internal_to_fdi,
    _maximum_unique_type_assignment,
    _nearest_label_counts,
    _sample_index_sha256,
    _strict_component_state,
    materialize_checkpoint_archive,
    validate_checkpoint_directory_layout,
)
from totalsegmentator_wrapper_mac.ios_model_dispatch import detect_model_family


class IOSTGNetFinalTests(unittest.TestCase):
    def test_checkpoint_set_requires_official_fps_and_boundary_files(self) -> None:
        expected = {
            "tgnet_fps.h5",
            "tgnet_bdl.h5",
        }
        self.assertEqual(
            {str(item["filename"]) for item in TGNET_FINAL_CHECKPOINTS.values()},
            expected,
        )
        self.assertEqual(TGNET_FINAL_CHECKPOINTS["fps"]["size_bytes"], 64_037_327)
        self.assertEqual(TGNET_FINAL_CHECKPOINTS["boundary"]["size_bytes"], 511_103)
        contents = {
            "tgnet_fps.h5": b"fps fixture",
            "tgnet_bdl.h5": b"boundary fixture",
        }
        specifications = {
            role: {
                "filename": item["filename"],
                "size_bytes": len(contents[str(item["filename"])]),
                "sha256": hashlib.sha256(
                    contents[str(item["filename"])]
                ).hexdigest(),
            }
            for role, item in TGNET_FINAL_CHECKPOINTS.items()
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "ckpts(new)" / "tgnet"
            nested.mkdir(parents=True)
            for filename, payload in contents.items():
                (nested / filename).write_bytes(payload)
            with patch(
                "totalsegmentator_wrapper_mac.ios_tgnet_final.TGNET_FINAL_CHECKPOINTS",
                specifications,
            ):
                layout = validate_checkpoint_directory_layout(root)
                self.assertEqual(set(layout), set(TGNET_FINAL_CHECKPOINTS))
                self.assertEqual(detect_model_family(root), "tgnet-final")

                (root / "unknown_extra.h5").touch()
                self.assertEqual(
                    set(validate_checkpoint_directory_layout(root)),
                    set(TGNET_FINAL_CHECKPOINTS),
                )

                duplicate = root / "duplicate"
                duplicate.mkdir()
                duplicate_name = "tgnet_fps.h5"
                (duplicate / duplicate_name).write_bytes(contents[duplicate_name])
                with self.assertRaisesRegex(ValueError, "multiple"):
                    validate_checkpoint_directory_layout(root)

    def test_checkpoint_set_reports_all_missing_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "missing"):
                validate_checkpoint_directory_layout(Path(tmp))

    def test_official_bundle_zip_materializes_only_the_required_checkpoints(self) -> None:
        contents = {
            "first.h5": b"first checkpoint",
            "second.h5": b"second checkpoint",
        }
        specifications = {
            role: {
                "filename": filename,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for role, (filename, payload) in zip(
                ("first", "second"), contents.items(), strict=True
            )
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "ckpts(new).zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                for filename, payload in contents.items():
                    bundle.writestr(f"ckpts(new)/tgnet/{filename}", payload)
                bundle.writestr("ckpts(new)/pointnet/unused.h5", b"unused")
            destination = root / "selected"
            with patch(
                "totalsegmentator_wrapper_mac.ios_tgnet_final.TGNET_FINAL_CHECKPOINTS",
                specifications,
            ):
                layout = materialize_checkpoint_archive(archive, destination)
            self.assertEqual(
                {path.name for path in layout.values()},
                set(contents),
            )
            self.assertFalse((destination / "unused.h5").exists())

    def test_bundle_zip_rejects_duplicate_or_hash_mismatched_required_files(self) -> None:
        expected = b"expected"
        specifications = {
            "first": {
                "filename": "first.h5",
                "size_bytes": len(expected),
                "sha256": hashlib.sha256(expected).hexdigest(),
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            duplicate = root / "duplicate.zip"
            with zipfile.ZipFile(duplicate, "w") as bundle:
                bundle.writestr("one/first.h5", expected)
                bundle.writestr("two/first.h5", expected)
            mismatch = root / "mismatch.zip"
            with zipfile.ZipFile(mismatch, "w") as bundle:
                bundle.writestr("first.h5", b"wrong!!!")
            with patch(
                "totalsegmentator_wrapper_mac.ios_tgnet_final.TGNET_FINAL_CHECKPOINTS",
                specifications,
            ):
                with self.assertRaisesRegex(ValueError, "multiple"):
                    materialize_checkpoint_archive(duplicate, root / "duplicate-out")
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    materialize_checkpoint_archive(mismatch, root / "mismatch-out")

    def test_directory_validation_rejects_wrong_size_before_hash(self) -> None:
        payload = b"expected"
        specifications = {
            "first": {
                "filename": "first.h5",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "totalsegmentator_wrapper_mac.ios_tgnet_final.TGNET_FINAL_CHECKPOINTS",
            specifications,
        ):
            root = Path(tmp)
            (root / "first.h5").write_bytes(b"short")
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                validate_checkpoint_directory_layout(root)
            (root / "first.h5").write_bytes(b"wrong!!!")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                validate_checkpoint_directory_layout(root)

    def test_archive_rejects_oversized_required_member_before_extraction(self) -> None:
        payload = b"expected"
        specifications = {
            "first": {
                "filename": "first.h5",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "totalsegmentator_wrapper_mac.ios_tgnet_final.TGNET_FINAL_CHECKPOINTS",
            specifications,
        ):
            root = Path(tmp)
            archive = root / "oversized.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("first.h5", payload + b"oversized")
            destination = root / "selected"
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                materialize_checkpoint_archive(archive, destination)
            self.assertFalse(destination.exists())

    def test_accidental_broad_directory_selection_has_a_bounded_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "totalsegmentator_wrapper_mac.ios_tgnet_final.MAX_CHECKPOINT_SCAN_ENTRIES",
            3,
        ):
            root = Path(tmp)
            for index in range(4):
                (root / f"unrelated-{index}.txt").touch()
            with self.assertRaisesRegex(ValueError, "too many entries"):
                validate_checkpoint_directory_layout(root)

    def test_checkpoint_provenance_records_filename_without_temporary_path(self) -> None:
        metadata = _checkpoint_metadata(
            role="offset",
            path=Path("/tmp/tgnet-checkpoints-random/offset.h5"),
            sha256="a" * 64,
            validation={"architecture_validation": "passed"},
            batchnorm_layers=12,
        )
        self.assertEqual(metadata["filename"], "offset.h5")
        self.assertNotIn("path", metadata)
        self.assertNotIn("/tmp/", str(metadata))

    def test_active_component_validation_is_strict(self) -> None:
        expected = {
            "weight": torch.zeros((2, 3)),
            "bias": torch.zeros((2,)),
        }
        checkpoint = {
            "second_ins_cent_model.weight": torch.zeros((2, 3)),
            "second_ins_cent_model.bias": torch.zeros((2,)),
            "cent_model.old": torch.zeros((1,)),
        }
        component, metadata = _strict_component_state(
            checkpoint,
            prefix="second_ins_cent_model",
            expected_state=expected,
            permitted_inactive_prefixes=("cent_model",),
        )
        self.assertEqual(set(component), set(expected))
        self.assertEqual(metadata["architecture_validation"], "passed")
        self.assertEqual(metadata["inactive_extra_tensor_count"], 1)
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            _strict_component_state(
                checkpoint | {"unknown.old": torch.zeros((1,))},
                prefix="second_ins_cent_model",
                expected_state=expected,
                permitted_inactive_prefixes=("cent_model",),
            )

    def test_first_ensemble_sums_class_logits_and_applies_mask_gate(self) -> None:
        result = _combine_first_heads(
            class_logits=[
                np.asarray([[9, 1, 8], [9, 8, 1]], dtype=np.float32),
                np.asarray([[9, 2, 7], [9, 7, 2]], dtype=np.float32),
            ],
            mask_logits=[
                np.asarray([[8, 1], [1, 8]], dtype=np.float32),
            ],
            offsets=[
                np.asarray([[1, 0, 0], [3, 0, 0]], dtype=np.float32),
            ],
        )
        np.testing.assert_array_equal(result["classes"], [0, 1])
        np.testing.assert_array_equal(result["offsets"], [[1, 0, 0], [3, 0, 0]])
        self.assertEqual(result["metadata"]["class_fusion"], "sum-logits")

    def test_official_main_uses_semantic_background_for_preliminary_crops(self) -> None:
        result = _decode_official_first_heads(
            class_logits=np.asarray(
                [
                    [9, 1, 0, 0, 0, 0, 0, 0, 0, 0],
                    [1, 9, 0, 0, 0, 0, 0, 0, 0, 0],
                ],
                dtype=np.float32,
            ),
            mask_logits=np.asarray(
                [[0, 9], [9, 0]], dtype=np.float32
            ),
            offsets=np.asarray(
                [[1, 0, 0], [2, 0, 0]], dtype=np.float32
            ),
        )
        np.testing.assert_array_equal(result["classes"], [0, 1])
        np.testing.assert_array_equal(result["preliminary_mask"], [False, True])
        self.assertEqual(
            result["metadata"]["preliminary_crop_mask_source"],
            "semantic-class-argmax-is-nonzero",
        )
        self.assertEqual(
            result["metadata"]["first_mask_head_role"],
            "diagnostic-only",
        )

    def test_official_boundary_model_uses_3072_point_crops(self) -> None:
        self.assertEqual(RUNTIME_ARCHITECTURE["boundary_crop_points"], 3_072)

    def test_structured_tooth_type_assignment_uses_unique_maximum_score(self) -> None:
        scores = np.zeros((2, 8), dtype=np.float32)
        scores[0, :2] = [10.0, 1.0]
        scores[1, :2] = [9.0, 8.0]
        np.testing.assert_array_equal(
            _maximum_unique_type_assignment(scores),
            [1, 2],
        )

    def test_boundary_ratio_counts_the_nearest_points_label(self) -> None:
        labels = np.asarray(
            [[2, 1, 1, 1], [3, 3, 2, 3]],
            dtype=np.int16,
        )
        np.testing.assert_array_equal(_nearest_label_counts(labels), [1, 3])

    def test_internal_semantics_map_to_jaw_specific_fdi(self) -> None:
        internal = np.asarray([0, 1, 8, 9, 16], dtype=np.int16)
        np.testing.assert_array_equal(
            _internal_to_fdi(internal, "upper"),
            [0, 11, 18, 21, 28],
        )
        np.testing.assert_array_equal(
            _internal_to_fdi(internal, "lower"),
            [0, 31, 38, 41, 48],
        )

    def test_official_output_preserves_separate_instances_with_same_fdi(self) -> None:
        instances = np.asarray([1, 1, 2, 2], dtype=np.int16)
        internal = np.asarray([6, 6, 6, 6], dtype=np.int16)
        self.assertEqual(
            _instance_fdi_mapping(instances, internal, "upper"),
            {1: 16, 2: 16},
        )

    def test_sample_index_hash_is_stable_and_order_sensitive(self) -> None:
        first = np.asarray([3, 1, 4, 1, 5], dtype=np.int64)
        second = np.asarray([1, 3, 4, 1, 5], dtype=np.int64)
        self.assertEqual(
            _sample_index_sha256(first),
            _sample_index_sha256(first.astype(np.uint32)),
        )
        self.assertNotEqual(
            _sample_index_sha256(first),
            _sample_index_sha256(second),
        )


if __name__ == "__main__":
    unittest.main()
