from __future__ import annotations

import json
import hashlib
import io
import errno
import os
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import totalsegmentator_wrapper_mac.totalseg_weights_setup as weights_setup
from totalsegmentator_wrapper_mac.totalseg_weights_setup import (
    DownloadProgressWriter,
    WeightAsset,
    load_setup_weight_manifest,
    parse_tqdm_download_progress,
    prepare_setup_weights,
    prepare_weight_asset,
    validate_setup_weights_registry,
)


class TotalSegWeightsSetupTests(unittest.TestCase):
    def test_official_manifest_describes_checksum_provenance_without_immutable_claim(self) -> None:
        manifest_path = Path(weights_setup.__file__).with_name(
            "totalseg_setup_weights_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            manifest["checksum_policy"],
            "Publisher-provided GitHub release digest where available; otherwise "
            "a locally observed SHA-256 value carried by this application for the "
            "pinned official GitHub release URL. Locally observed values are not "
            "publisher-provided digests. For assets without a publisher digest, "
            "observation date and source evidence are not preserved; revalidation "
            "by an approved official-asset download is required before release.",
        )
        self.assertNotIn("immutable", manifest["checksum_policy"].lower())
        self.assertNotIn("2026-08-01", json.dumps(manifest, sort_keys=True))

        assets = {asset["task_id"]: asset for asset in manifest["assets"]}
        self.assertTrue(assets[113]["publisher_digest_available"])
        self.assertEqual(assets[113]["sha256_source"], "github-release-digest")
        for task_id in (115, 297):
            with self.subTest(task_id=task_id):
                self.assertFalse(assets[task_id]["publisher_digest_available"])
                self.assertEqual(
                    assets[task_id]["sha256_source"],
                    "approved-official-asset-revalidation",
                )
                self.assertNotIn("sha256_observed_at", assets[task_id])
                self.assertNotIn("local_observation_evidence", assets[task_id])
                self.assertFalse(
                    assets[task_id]["revalidation_required_before_release"]
                )
                evidence = assets[task_id]["revalidation_evidence"]
                self.assertEqual(evidence["official_url"], assets[task_id]["url"])
                self.assertEqual(evidence["sha256"], assets[task_id]["sha256"])
                self.assertEqual(evidence["approval"], "approved-for-release")

    def test_manifest_rejects_missing_unverified_local_digest_revalidation_marker(self) -> None:
        manifest_path = Path(weights_setup.__file__).with_name(
            "totalseg_setup_weights_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        local_asset = next(
            asset for asset in manifest["assets"] if asset["task_id"] == 115
        )
        local_asset["sha256_source"] = "locally-observed-official-asset"
        local_asset["publisher_digest_available"] = False
        local_asset["local_observation_evidence"] = "not-preserved-unverified"
        local_asset.pop("revalidation_evidence")
        local_asset.pop("revalidation_required_before_release", None)

        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "manifest.json"
            candidate.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                weights_setup.SetupWeightsManifestError,
                "checksum provenance",
            ):
                load_setup_weight_manifest(candidate)

    def test_manifest_accepts_only_strict_approved_revalidation_evidence(self) -> None:
        manifest_path = Path(weights_setup.__file__).with_name(
            "totalseg_setup_weights_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        local_asset = next(
            asset for asset in manifest["assets"] if asset["task_id"] == 115
        )
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "manifest.json"
            candidate.write_text(json.dumps(manifest), encoding="utf-8")
            assets = load_setup_weight_manifest(candidate)
            self.assertEqual(
                next(asset for asset in assets if asset.task_id == 115).sha256_source,
                "approved-official-asset-revalidation",
            )
            local_asset["revalidation_evidence"]["verified_at_utc"] = (
                "2026-99-99T00:00:00Z"
            )
            candidate.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                weights_setup.SetupWeightsManifestError,
                "checksum provenance",
            ):
                load_setup_weight_manifest(candidate)
            local_asset["revalidation_evidence"]["verified_at_utc"] = (
                "2026-08-01T00:00:00Z"
            )
            local_asset["revalidation_evidence"]["official_url"] = (
                "https://example.invalid/unapproved.zip"
            )
            candidate.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                weights_setup.SetupWeightsManifestError,
                "checksum provenance",
            ):
                load_setup_weight_manifest(candidate)

    def test_manifest_rejects_unpreserved_observation_date(self) -> None:
        manifest_path = Path(weights_setup.__file__).with_name(
            "totalseg_setup_weights_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        local_asset = next(
            asset for asset in manifest["assets"] if asset["task_id"] == 115
        )
        local_asset["sha256_observed_at"] = "2026-08-01"

        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "manifest.json"
            candidate.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                weights_setup.SetupWeightsManifestError,
                "checksum provenance",
            ):
                load_setup_weight_manifest(candidate)

    def test_official_manifest_maps_all_setup_tasks(self) -> None:
        assets = {asset.task_id: asset for asset in load_setup_weight_manifest()}

        self.assertEqual(set(assets), {113, 115, 297})
        self.assertEqual(assets[113].release_tag, "v2.5.0-weights")
        self.assertEqual(assets[113].filename, "Dataset113_ToothFairy3.zip")
        self.assertEqual(assets[113].size_bytes, 232_066_830)
        self.assertEqual(assets[113].sha256_source, "github-release-digest")
        self.assertEqual(assets[115].release_tag, "v2.5.0-weights")
        self.assertEqual(assets[297].release_tag, "v2.0.0-weights")
        self.assertEqual(
            assets[115].sha256_source,
            "approved-official-asset-revalidation",
        )
        self.assertEqual(
            assets[297].sha256_source,
            "approved-official-asset-revalidation",
        )
        self.assertTrue(all(asset.totalsegmentator_version == "2.14.0" for asset in assets.values()))

    def test_setup_sequence_uses_pinned_version_and_records_three_task_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = load_setup_weight_manifest()
            for asset in assets:
                target = root / "weights" / asset.dataset_dir
                for relative in asset.required_files:
                    path = target / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_valid_required_file_content(relative))
            progress_log = root / "launcher.log"

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ):
                results = prepare_setup_weights(
                    (115, 297, 113),
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    progress_log=progress_log,
                )

            self.assertEqual([item["status"] for item in results], ["skipped", "skipped", "skipped"])
            registry = validate_setup_weights_registry(root / "weights")
            self.assertEqual(
                registry["schema"],
                "totalsegmentator_wrapper_mac.setup_weights_registry.v2",
            )
            self.assertEqual(registry["integrity_source"], "legacy-deep-validation")
            self.assertFalse(registry["archive_verified"])
            self.assertEqual({item["task_id"] for item in registry["assets"]}, {113, 115, 297})
            for entry in registry["assets"]:
                self.assertEqual(entry["integrity_source"], "legacy-deep-validation")
                self.assertFalse(entry["archive_verified"])
                self.assertIsNone(entry["archive_sha256"])
                for required in entry["required_files"]:
                    self.assertRegex(required["sha256"], r"^[0-9a-f]{64}$")
            payloads = _progress_payloads(progress_log)
            self.assertEqual([item["index"] for item in payloads], [1, 2, 3])
            self.assertTrue(all(item["task_total"] == 3 for item in payloads))

    def test_setup_sequence_rejects_runtime_version_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
            return_value="2.15.0",
        ):
            with self.assertRaisesRegex(
                weights_setup.SetupWeightsManifestError,
                "require version 2.14.0",
            ):
                prepare_setup_weights(
                    (115,),
                    weights_root=Path(tmp) / "weights",
                    cache_root=Path(tmp) / "cache",
                    progress_log=None,
                )

    def test_fresh_verified_archives_publish_official_v2_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = [_fixture_asset(root, task_id=task_id) for task_id in (115, 297, 113)]
            archives = [archive for archive, _asset in fixtures]
            assets = tuple(asset for _archive, asset in fixtures)
            opener = _QueueOpener(
                [_FakeResponse(archive, status=200) for archive in archives]
            )
            manifest_sha = "a" * 64

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.load_setup_weight_manifest",
                return_value=assets,
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.setup_weight_manifest_sha256",
                return_value=manifest_sha,
            ):
                results = prepare_setup_weights(
                    (115, 297, 113),
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    progress_log=None,
                    opener=opener,
                )
                registry = validate_setup_weights_registry(root / "weights")

            self.assertTrue(all(item["archive_verified"] is True for item in results))
            self.assertEqual(registry["schema"], weights_setup.REGISTRY_SCHEMA)
            self.assertEqual(registry["integrity_source"], "official-archive-sha256")
            self.assertTrue(registry["archive_verified"])
            self.assertFalse(registry["legacy_migration"])
            for asset, entry in zip(
                sorted(assets, key=lambda item: item.task_id),
                registry["assets"],
                strict=True,
            ):
                self.assertEqual(entry["archive_sha256"], asset.sha256)
                self.assertEqual(entry["archive_sha256_source"], asset.sha256_source)

    def test_interrupted_multi_task_setup_skips_receipted_task_and_resumes_next(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = [_fixture_asset(root, task_id=task_id) for task_id in (115, 297, 113)]
            archives = [archive for archive, _asset in fixtures]
            assets = tuple(asset for _archive, asset in fixtures)
            manifest_sha = "a" * 64
            first_opener = _QueueOpener(
                [
                    _FakeResponse(archives[0], status=200),
                    _FakeResponse(
                        archives[1],
                        status=200,
                        fail_after=23,
                        max_read=11,
                    ),
                ]
            )

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.load_setup_weight_manifest",
                return_value=assets,
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.setup_weight_manifest_sha256",
                return_value=manifest_sha,
            ):
                with self.assertRaisesRegex(ConnectionError, "fixture interruption"):
                    prepare_setup_weights(
                        (115, 297, 113),
                        weights_root=root / "weights",
                        cache_root=root / "cache",
                        progress_log=None,
                        opener=first_opener,
                    )

                receipt = weights_setup._task_receipt_path(  # noqa: SLF001 - receipt contract.
                    root / "weights", assets[0]
                )
                self.assertTrue(receipt.is_file())
                partial = root / "cache" / f"{assets[1].filename}.part"
                resume_from = partial.stat().st_size
                self.assertGreater(resume_from, 0)
                self.assertLess(resume_from, len(archives[1]))

                second_opener = _QueueOpener(
                    [
                        _FakeResponse(
                            archives[1][resume_from:],
                            status=206,
                            headers={
                                "Content-Range": (
                                    f"bytes {resume_from}-{len(archives[1]) - 1}/"
                                    f"{len(archives[1])}"
                                )
                            },
                        ),
                        _FakeResponse(archives[2], status=200),
                    ]
                )
                results = prepare_setup_weights(
                    (115, 297, 113),
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    progress_log=None,
                    opener=second_opener,
                )
                registry = validate_setup_weights_registry(root / "weights")

            self.assertEqual(
                [item["status"] for item in results],
                ["verified_receipt_skipped", "installed", "installed"],
            )
            self.assertEqual(
                second_opener.range_headers,
                [f"bytes={resume_from}-", None],
            )
            self.assertTrue(registry["archive_verified"])
            self.assertEqual(registry["setup_weights_manifest_sha256"], manifest_sha)

    def test_receipts_preserve_official_provenance_when_registry_publish_crashes(self) -> None:
        """A registry crash must not downgrade fully receipted official assets."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = [_fixture_asset(root, task_id=task_id) for task_id in (115, 297, 113)]
            archives = [archive for archive, _asset in fixtures]
            assets = tuple(asset for _archive, asset in fixtures)
            manifest_sha = "a" * 64

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.load_setup_weight_manifest",
                return_value=assets,
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.setup_weight_manifest_sha256",
                return_value=manifest_sha,
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup._write_ready_registry_at",
                side_effect=RuntimeError("fixture crash after receipts"),
            ):
                with self.assertRaisesRegex(RuntimeError, "after receipts"):
                    prepare_setup_weights(
                        (115, 297, 113),
                        weights_root=root / "weights",
                        cache_root=root / "cache",
                        progress_log=None,
                        opener=_QueueOpener([_FakeResponse(archive, status=200) for archive in archives]),
                    )

            self.assertFalse((root / "weights" / weights_setup.REGISTRY_FILENAME).exists())
            self.assertTrue(
                all(
                    weights_setup._task_receipt_path(root / "weights", asset).is_file()  # noqa: SLF001
                    for asset in assets
                )
            )
            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.load_setup_weight_manifest",
                return_value=assets,
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.setup_weight_manifest_sha256",
                return_value=manifest_sha,
            ):
                results = prepare_setup_weights(
                    (115, 297, 113),
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    progress_log=None,
                    opener=_QueueOpener([]),
                )

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.load_setup_weight_manifest",
                return_value=assets,
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.setup_weight_manifest_sha256",
                return_value=manifest_sha,
            ):
                registry = validate_setup_weights_registry(root / "weights")
            self.assertEqual(
                [item["status"] for item in results],
                ["verified_receipt_skipped"] * 3,
            )
            self.assertEqual(registry["integrity_source"], "official-archive-sha256")
            self.assertTrue(registry["archive_verified"])
            self.assertFalse(registry["legacy_migration"])

    def test_task_receipt_is_strictly_bound_to_manifest_identity_and_file_hashes(self) -> None:
        mutations = ("corrupt", "stale", "target", "manifest")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive, asset = _fixture_asset(root, task_id=115)
                manifest_sha = "a" * 64
                prepare_weight_asset(
                    asset,
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    opener=_QueueOpener([_FakeResponse(archive, status=200)]),
                    manifest_sha256=manifest_sha,
                )
                receipt = weights_setup._task_receipt_path(  # noqa: SLF001 - receipt contract.
                    root / "weights", asset
                )
                payload = json.loads(receipt.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema"], weights_setup.TASK_RECEIPT_SCHEMA)
                self.assertEqual(payload["setup_weights_manifest_sha256"], manifest_sha)
                self.assertEqual(payload["archive_sha256"], asset.sha256)
                self.assertEqual(payload["archive_sha256_source"], asset.sha256_source)
                self.assertEqual(payload["source"], "official-release-asset")
                self.assertEqual(payload["dataset_dir"], asset.dataset_dir)

                next_manifest_sha = manifest_sha
                if mutation == "corrupt":
                    receipt.write_text("not json", encoding="utf-8")
                elif mutation == "stale":
                    payload["url"] = "https://example.invalid/stale.zip"
                    receipt.write_text(json.dumps(payload), encoding="utf-8")
                elif mutation == "target":
                    target = root / "weights" / asset.dataset_dir / asset.required_files[0]
                    target.write_bytes(b"[]")
                else:
                    next_manifest_sha = "b" * 64

                opener = _QueueOpener([_FakeResponse(archive, status=200)])
                result = prepare_weight_asset(
                    asset,
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    opener=opener,
                    manifest_sha256=next_manifest_sha,
                )

                self.assertEqual(result["status"], "installed")
                self.assertEqual(len(opener.requests), 1)

    def test_normal_download_validates_and_publishes_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            opener = _QueueOpener([_FakeResponse(archive, status=200)])

            result = prepare_weight_asset(
                asset,
                weights_root=root / "weights",
                cache_root=root / "cache",
                opener=opener,
                chunk_size=7,
            )

            self.assertEqual(result["status"], "installed")
            self.assertTrue(result["archive_verified"])
            self.assertEqual(result["integrity_source"], "official-archive-sha256")
            self.assertTrue((root / "weights" / asset.dataset_dir / asset.required_files[0]).is_file())
            self.assertFalse((root / "cache" / f"{asset.filename}.part").exists())

    def test_cross_device_cache_and_weights_stage_on_weights_filesystem(self) -> None:
        """A cache on another volume must not make atomic model publication fail."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            cache = root / "cache"
            weights = root / "weights"
            target = (weights / asset.dataset_dir).resolve()
            original_replace = weights_setup.os.replace
            free_space_calls: list[tuple[Path, int, int]] = []

            def reject_cross_device_publish(
                source: str | os.PathLike[str],
                destination: str | os.PathLike[str],
                *,
                src_dir_fd: int | None = None,
                dst_dir_fd: int | None = None,
            ) -> None:
                if src_dir_fd is None or dst_dir_fd is None:
                    source_path = Path(source).resolve()
                    destination_path = Path(destination).resolve()
                    if source_path.is_relative_to(cache.resolve()) and destination_path == target:
                        raise OSError(errno.EXDEV, "cross-device link")
                    original_replace(source, destination)
                    return
                source_identity = (os.fstat(src_dir_fd).st_dev, os.fstat(src_dir_fd).st_ino)
                destination_identity = (os.fstat(dst_dir_fd).st_dev, os.fstat(dst_dir_fd).st_ino)
                cache_identity = (cache.stat().st_dev, cache.stat().st_ino)
                weights_identity = (weights.stat().st_dev, weights.stat().st_ino)
                if (
                    source_identity == cache_identity
                    and destination_identity == weights_identity
                    and os.fspath(destination) == asset.dataset_dir
                ):
                    raise OSError(errno.EXDEV, "cross-device link")
                original_replace(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            def record_free_space(
                root: weights_setup._RootDirectory,  # noqa: SLF001 - fd setup contract.
                required_bytes: int,
                *,
                metadata_entries: int,
            ) -> None:
                free_space_calls.append((root.path.resolve(), required_bytes, metadata_entries))

            with patch.object(weights_setup.os, "replace", side_effect=reject_cross_device_publish), patch.object(
                weights_setup,
                "_require_free_space_fd",
                side_effect=record_free_space,
            ):
                result = prepare_weight_asset(
                    asset,
                    weights_root=weights,
                    cache_root=cache,
                    opener=_QueueOpener([_FakeResponse(archive, status=200)]),
                )

            self.assertEqual(result["status"], "installed")
            self.assertTrue((weights / asset.dataset_dir).is_dir())
            self.assertTrue(
                any(path == cache.resolve() and required == len(archive) for path, required, _ in free_space_calls)
            )
            self.assertTrue(
                any(
                    path == weights.resolve()
                    and required == sum(info.file_size for info in zipfile.ZipFile(io.BytesIO(archive)).infolist())
                    for path, required, _ in free_space_calls
                )
            )

    def test_partial_symlink_swap_after_fd_open_cannot_write_outside_cache(self) -> None:
        """The write fd stays pinned and a swapped path is rejected, not followed."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            cache = root / "cache"
            partial = cache / f"{asset.filename}.part"
            prefix = archive[:17]
            _write_partial(cache, asset, prefix)
            outside = root / "outside.bin"
            outside.write_bytes(b"do not overwrite")
            response = _SwapPathOnFirstReadResponse(
                archive[len(prefix):],
                status=206,
                headers={
                    "Content-Range": f"bytes {len(prefix)}-{len(archive) - 1}/{len(archive)}"
                },
                swap_path=partial,
                outside_target=outside,
            )

            with self.assertRaises(weights_setup.SetupWeightsIntegrityError):
                prepare_weight_asset(
                    asset,
                    weights_root=root / "weights",
                    cache_root=cache,
                    opener=_QueueOpener([response]),
                    chunk_size=7,
                )

            self.assertTrue(partial.is_symlink())
            self.assertEqual(outside.read_bytes(), b"do not overwrite")
            self.assertFalse((root / "weights" / asset.dataset_dir).exists())

    def test_root_path_swap_after_open_cannot_redirect_model_publish(self) -> None:
        """A rename/symlink swap after root open leaves all writes on its pinned fd."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            weights = root / "weights"
            moved_weights = root / "weights-pinned-original"
            outside = root / "outside"
            outside.mkdir()

            def opener(_request: object, timeout: int) -> _FakeResponse:
                self.assertGreater(timeout, 0)
                weights.rename(moved_weights)
                weights.symlink_to(outside, target_is_directory=True)
                return _FakeResponse(archive, status=200)

            result = prepare_weight_asset(
                asset,
                weights_root=weights,
                cache_root=root / "cache",
                opener=opener,
            )

            self.assertEqual(result["status"], "installed")
            self.assertTrue((moved_weights / asset.dataset_dir).is_dir())
            self.assertFalse((outside / asset.dataset_dir).exists())

    def test_root_rejects_arbitrary_ancestor_symlink_before_any_model_write(self) -> None:
        """Only verified macOS top-level aliases may be traversed during root open."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            outside = root / "outside"
            outside.mkdir()
            alias_parent = root / "user-controlled-alias"
            alias_parent.symlink_to(outside, target_is_directory=True)
            requested_weights = alias_parent / "weights"
            opener = _QueueOpener([_FakeResponse(archive, status=200)])

            with self.assertRaises(weights_setup.SetupWeightsIntegrityError):
                prepare_weight_asset(
                    asset,
                    weights_root=requested_weights,
                    cache_root=root / "cache",
                    opener=opener,
                )

            self.assertEqual(opener.requests, [])
            self.assertFalse((outside / "weights").exists())

    def test_root_does_not_call_realpath_between_preopen_checks_and_fd_pin(self) -> None:
        """The former lstat→realpath swap hook cannot redirect setup anymore."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            weights = root / "weights"
            weights.mkdir()
            moved_weights = root / "weights-before-resolution"
            outside = root / "outside"
            outside.mkdir()
            original_realpath = weights_setup.os.path.realpath
            swapped = False

            def swap_during_legacy_realpath(value: str | os.PathLike[str], *args: object, **kwargs: object) -> str:
                nonlocal swapped
                if not swapped and os.fspath(value) == os.fspath(weights):
                    weights.rename(moved_weights)
                    weights.symlink_to(outside, target_is_directory=True)
                    swapped = True
                return original_realpath(value, *args, **kwargs)

            with patch.object(
                weights_setup.os.path,
                "realpath",
                side_effect=swap_during_legacy_realpath,
            ):
                result = prepare_weight_asset(
                    asset,
                    weights_root=weights,
                    cache_root=root / "cache",
                    opener=_QueueOpener([_FakeResponse(archive, status=200)]),
                )

            self.assertFalse(swapped)
            self.assertEqual(result["status"], "installed")
            self.assertTrue((weights / asset.dataset_dir).is_dir())
            self.assertFalse((outside / asset.dataset_dir).exists())

    def test_fd_disk_reserve_accounts_for_blocks_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp) / "cache"
            with weights_setup._opened_roots(  # noqa: SLF001 - fd reserve contract.
                root_path,
                weights_root=None,
                label="test",
            ) as (root, _):
                block = 4096
                # Raw payload bytes would fit in three blocks, but the required
                # data blocks, metadata blocks, and bounded reserve do not.
                constrained = SimpleNamespace(
                    f_frsize=block,
                    f_bsize=block,
                    f_bavail=3,
                )
                with patch.object(weights_setup.os, "fstatvfs", return_value=constrained):
                    with self.assertRaises(OSError) as raised:
                        weights_setup._require_free_space_fd(  # noqa: SLF001
                            root,
                            4097,
                            metadata_entries=3,
                        )

            self.assertEqual(raised.exception.errno, errno.ENOSPC)

    def test_atomic_json_cleanup_does_not_remove_post_publish_replacement(self) -> None:
        """A successful rename must not unlink a new temp name created afterwards."""

        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp) / "cache"
            with weights_setup._opened_roots(  # noqa: SLF001 - atomic write contract.
                root_path,
                weights_root=None,
                label="test",
            ) as (root, _):
                original_replace = weights_setup.os.replace
                replacement_payload = b"preserve replacement"

                def replace_then_recreate(
                    source: str,
                    destination: str,
                    *,
                    src_dir_fd: int,
                    dst_dir_fd: int,
                ) -> None:
                    original_replace(
                        source,
                        destination,
                        src_dir_fd=src_dir_fd,
                        dst_dir_fd=dst_dir_fd,
                    )
                    descriptor = os.open(
                        source,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=src_dir_fd,
                    )
                    try:
                        os.write(descriptor, replacement_payload)
                    finally:
                        os.close(descriptor)

                with patch.object(weights_setup.os, "replace", side_effect=replace_then_recreate):
                    weights_setup._write_json_atomic_at(  # noqa: SLF001
                        root,
                        "receipt.json",
                        {"ok": True},
                        label="test receipt",
                    )

                replacement = next(root_path.glob(".receipt.json.tmp-*"))
                self.assertEqual(replacement.read_bytes(), replacement_payload)

    def test_recovery_preserves_unrecognized_staging_and_backup_lookalikes(self) -> None:
        """Recovery may clean only transaction artifacts it can prove it created."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            weights = root / "weights"
            assets = load_setup_weight_manifest()
            for asset in assets:
                target = weights / asset.dataset_dir
                for relative in asset.required_files:
                    path = target / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_valid_required_file_content(relative))

            unrelated_stage = cache / ".totalseg-staging-not-ours"
            unrelated_stage.mkdir(parents=True)
            stage_sentinel = unrelated_stage / "preserve.txt"
            stage_sentinel.write_text("preserve", encoding="utf-8")
            unrelated_backup = weights / f".{assets[0].dataset_dir}.previous-not-ours"
            unrelated_backup.mkdir(parents=True)
            backup_sentinel = unrelated_backup / "preserve.txt"
            backup_sentinel.write_text("preserve", encoding="utf-8")
            strict_unowned_stage = weights / f".totalseg-staging-115-{'a' * 32}"
            strict_unowned_stage.mkdir(parents=True)
            strict_stage_sentinel = strict_unowned_stage / "preserve.txt"
            strict_stage_sentinel.write_text("preserve", encoding="utf-8")
            strict_unowned_backup = (
                weights / f".{assets[0].dataset_dir}.previous-{'b' * 32}"
            )
            strict_unowned_backup.mkdir(parents=True)
            strict_backup_sentinel = strict_unowned_backup / "preserve.txt"
            strict_backup_sentinel.write_text("preserve", encoding="utf-8")
            unrelated_registry_temp = (
                weights / f".{weights_setup.REGISTRY_FILENAME}.tmp-not-ours"
            )
            unrelated_registry_temp.write_text("preserve", encoding="utf-8")

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ):
                prepare_setup_weights(
                    (115, 297, 113),
                    weights_root=weights,
                    cache_root=cache,
                    progress_log=None,
                )

            self.assertEqual(stage_sentinel.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(backup_sentinel.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(strict_stage_sentinel.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(strict_backup_sentinel.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(unrelated_registry_temp.read_text(encoding="utf-8"), "preserve")

    def test_recovery_removes_owned_weights_staging_and_registry_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            weights = root / "weights"
            assets = load_setup_weight_manifest()
            for asset in assets:
                target = weights / asset.dataset_dir
                for relative in asset.required_files:
                    path = target / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_valid_required_file_content(relative))

            asset = next(item for item in assets if item.task_id == 115)
            nonce = "c" * 32
            staging = weights / weights_setup._staging_artifact_name(asset, nonce)  # noqa: SLF001
            staging.mkdir()
            (staging / "interrupted.bin").write_bytes(b"interrupted")
            marker = weights_setup._staging_marker_path(weights, asset, nonce)  # noqa: SLF001
            weights_setup._write_recovery_marker(  # noqa: SLF001
                marker,
                kind="staging",
                asset=asset,
                nonce=nonce,
                artifact_name=staging.name,
            )
            registry_temp = weights / f".{weights_setup.REGISTRY_FILENAME}.tmp-{'d' * 32}"
            registry_temp.write_text("interrupted", encoding="utf-8")
            os.chmod(registry_temp, 0o600)

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ):
                prepare_setup_weights(
                    (115, 297, 113),
                    weights_root=weights,
                    cache_root=cache,
                    progress_log=None,
                )

            self.assertFalse(staging.exists())
            self.assertFalse(marker.exists())
            self.assertFalse(registry_temp.exists())

    def test_download_requests_identity_encoding_and_accepts_same_origin_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            opener = _QueueOpener(
                [
                    _FakeResponse(
                        archive,
                        status=200,
                        url="https://example.invalid:443/official.zip",
                    )
                ]
            )

            prepare_weight_asset(
                asset,
                weights_root=root / "weights",
                cache_root=root / "cache",
                opener=opener,
            )

            self.assertEqual(opener.accept_encoding_headers, ["identity"])

    def test_official_download_accepts_only_github_release_asset_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, fixture_asset = _fixture_asset(root, task_id=115)
            official_asset = WeightAsset(
                **{
                    **fixture_asset.__dict__,
                    "url": (
                        "https://github.com/wasserth/TotalSegmentator/releases/"
                        "download/v2.5.0-weights/Dataset115_mandible.zip"
                    ),
                }
            )

            prepare_weight_asset(
                official_asset,
                weights_root=root / "accepted-weights",
                cache_root=root / "accepted-cache",
                opener=_QueueOpener(
                    [
                        _FakeResponse(
                            archive,
                            status=200,
                            url=(
                                "https://release-assets.githubusercontent.com/"
                                "github-production-release-asset/official.zip"
                            ),
                        )
                    ]
                ),
            )

            with self.assertRaisesRegex(
                weights_setup.SetupWeightsIntegrityError,
                "response host",
            ):
                prepare_weight_asset(
                    official_asset,
                    weights_root=root / "rejected-weights",
                    cache_root=root / "rejected-cache",
                    opener=_QueueOpener(
                        [
                            _FakeResponse(
                                archive,
                                status=200,
                                url="https://objects.example.test/official.zip",
                            )
                        ]
                    ),
                )

    def test_download_rejects_insecure_redirect_and_nonidentity_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            cases = (
                _FakeResponse(archive, status=200, url="http://objects.example.test/model.zip"),
                _FakeResponse(archive, status=200, url="https://objects.example.test:444/model.zip"),
                _FakeResponse(archive, status=200, url="https://user@objects.example.test/model.zip"),
                _FakeResponse(
                    archive,
                    status=200,
                    headers={"Content-Encoding": "gzip"},
                ),
            )
            for index, response in enumerate(cases):
                with self.subTest(index=index), self.assertRaisesRegex(
                    weights_setup.SetupWeightsIntegrityError,
                    "HTTPS|Content-Encoding",
                ):
                    prepare_weight_asset(
                        asset,
                        weights_root=root / f"weights-{index}",
                        cache_root=root / f"cache-{index}",
                        opener=_QueueOpener([response]),
                    )

    def test_nonregular_partial_and_sidecar_fail_without_recursive_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            for suffix in (".part", ".part.json"):
                with self.subTest(suffix=suffix):
                    cache = root / suffix.removeprefix(".")
                    hostile = cache / f"{asset.filename}{suffix}"
                    hostile.mkdir(parents=True)
                    sentinel = hostile / "preserve.txt"
                    sentinel.write_text("preserve", encoding="utf-8")

                    with self.assertRaisesRegex(
                        weights_setup.SetupWeightsIntegrityError,
                        "regular file",
                    ):
                        prepare_weight_asset(
                            asset,
                            weights_root=root / f"weights-{suffix}",
                            cache_root=cache,
                            opener=_QueueOpener([_FakeResponse(archive, status=200)]),
                        )

                    self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_hardlinked_partial_is_rejected_without_touching_other_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            cache = root / "cache"
            cache.mkdir()
            unrelated = root / "preserve.bin"
            unrelated.write_bytes(b"preserve")
            partial = cache / f"{asset.filename}.part"
            os.link(unrelated, partial)
            (cache / f"{asset.filename}.part.json").write_text(
                json.dumps(asset.sidecar_payload(), sort_keys=True),
                encoding="utf-8",
            )
            opener = _QueueOpener([_FakeResponse(archive, status=200)])

            with self.assertRaisesRegex(
                weights_setup.SetupWeightsIntegrityError,
                "private regular file",
            ):
                prepare_weight_asset(
                    asset,
                    weights_root=root / "weights",
                    cache_root=cache,
                    opener=opener,
                )

            self.assertEqual(unrelated.read_bytes(), b"preserve")
            self.assertEqual(opener.requests, [])

    def test_interruption_keeps_partial_then_uses_strict_range_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=297)
            first = _QueueOpener([_FakeResponse(archive, status=200, fail_after=19)])
            with self.assertRaises(ConnectionError):
                prepare_weight_asset(
                    asset,
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    opener=first,
                    chunk_size=8,
                )
            partial = root / "cache" / f"{asset.filename}.part"
            resume_from = partial.stat().st_size
            self.assertGreater(resume_from, 0)
            self.assertLess(resume_from, len(archive))

            progress_log = root / "launcher.log"
            second = _QueueOpener(
                [
                    _FakeResponse(
                        archive[resume_from:],
                        status=206,
                        headers={"Content-Range": f"bytes {resume_from}-{len(archive) - 1}/{len(archive)}"},
                    )
                ]
            )
            result = prepare_weight_asset(
                asset,
                weights_root=root / "weights",
                cache_root=root / "cache",
                progress_log=progress_log,
                opener=second,
                chunk_size=8,
            )

            self.assertEqual(result["status"], "installed")
            self.assertEqual(second.range_headers, [f"bytes={resume_from}-"])
            self.assertEqual(second.accept_encoding_headers, ["identity"])
            payloads = _progress_payloads(progress_log)
            resumed = [item for item in payloads if item.get("resumed")]
            self.assertTrue(resumed)
            self.assertEqual(resumed[0]["resume_from_bytes"], resume_from)

    def test_inactivity_timeout_is_bounded_and_preserves_resumable_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            opener = _QueueOpener(
                [
                    _FakeResponse(
                        archive,
                        status=200,
                        fail_after=19,
                        max_read=8,
                        failure=TimeoutError("fixture inactivity timeout"),
                    )
                ]
            )
            with self.assertRaisesRegex(TimeoutError, "inactivity timeout"):
                prepare_weight_asset(
                    asset,
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    opener=opener,
                    timeout_sec=7,
                    chunk_size=8,
                )

            partial = root / "cache" / f"{asset.filename}.part"
            sidecar = root / "cache" / f"{asset.filename}.part.json"
            self.assertTrue(partial.is_file())
            self.assertGreater(partial.stat().st_size, 0)
            self.assertLess(partial.stat().st_size, len(archive))
            self.assertTrue(sidecar.is_file())
            self.assertEqual(opener.timeouts, [7])

    def test_default_inactivity_timeout_is_two_minutes(self) -> None:
        self.assertEqual(
            weights_setup.DEFAULT_DOWNLOAD_INACTIVITY_TIMEOUT_SEC,
            120,
        )

    def test_range_ignored_restarts_without_concatenating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=113)
            _write_partial(root / "cache", asset, archive[:17])
            opener = _QueueOpener([
                _FakeResponse(archive, status=200),
                _FakeResponse(archive, status=200),
            ])

            result = prepare_weight_asset(
                asset,
                weights_root=root / "weights",
                cache_root=root / "cache",
                opener=opener,
                chunk_size=9,
            )

            self.assertEqual(result["status"], "installed")
            self.assertEqual(opener.range_headers, ["bytes=17-", None])

    def test_range_416_discards_partial_and_retries_full_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=113)
            _write_partial(root / "cache", asset, archive[:17])
            opener = _QueueOpener(
                [
                    urllib.error.HTTPError(asset.url, 416, "range", {}, None),
                    _FakeResponse(archive, status=200),
                ]
            )

            result = prepare_weight_asset(
                asset,
                weights_root=root / "weights",
                cache_root=root / "cache",
                opener=opener,
                chunk_size=9,
            )

            self.assertEqual(result["status"], "installed")
            self.assertEqual(opener.range_headers, ["bytes=17-", None])

    def test_multiple_short_206_responses_continue_with_new_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=297)
            first_offset = 13
            second_offset = 31
            _write_partial(root / "cache", asset, archive[:first_offset])
            opener = _QueueOpener(
                [
                    _FakeResponse(
                        archive[first_offset:second_offset],
                        status=206,
                        headers={"Content-Range": f"bytes {first_offset}-{second_offset - 1}/{len(archive)}"},
                    ),
                    _FakeResponse(
                        archive[second_offset:],
                        status=206,
                        headers={"Content-Range": f"bytes {second_offset}-{len(archive) - 1}/{len(archive)}"},
                    ),
                ]
            )

            result = prepare_weight_asset(
                asset,
                weights_root=root / "weights",
                cache_root=root / "cache",
                opener=opener,
                chunk_size=7,
            )

            self.assertEqual(result["status"], "installed")
            self.assertEqual(
                opener.range_headers,
                [f"bytes={first_offset}-", f"bytes={second_offset}-"],
            )

    def test_response_without_content_length_is_bounded_and_rejects_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            response = _FakeResponse(
                archive + b"unexpected trailing bytes",
                status=200,
                include_content_length=False,
            )

            with self.assertRaisesRegex(ValueError, "exceeded expected size"):
                prepare_weight_asset(
                    asset,
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    opener=_QueueOpener([response]),
                    chunk_size=len(archive) * 2,
                )

            self.assertLessEqual(response.bytes_read, len(archive) + 1)
            self.assertFalse((root / "weights" / asset.dataset_dir).exists())

    def test_mismatched_sidecar_discards_partial_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            _write_partial(root / "cache", asset, b"unrelated", url="https://invalid.example/old.zip")
            opener = _QueueOpener([_FakeResponse(archive, status=200)])

            prepare_weight_asset(asset, weights_root=root / "weights", cache_root=root / "cache", opener=opener)

            self.assertEqual(opener.range_headers, [None])

    def test_complete_partial_is_verified_without_another_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=113)
            _write_partial(root / "cache", asset, archive)
            opener = _QueueOpener([])

            result = prepare_weight_asset(
                asset,
                weights_root=root / "weights",
                cache_root=root / "cache",
                opener=opener,
            )

            self.assertEqual(result["status"], "installed")
            self.assertEqual(opener.requests, [])

    def test_hash_mismatch_is_not_published_or_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            asset = _replace_asset(asset, sha256="0" * 64)

            with self.assertRaisesRegex(ValueError, "SHA-256"):
                prepare_weight_asset(
                    asset,
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    opener=_QueueOpener([_FakeResponse(archive, status=200)]),
                )

            self.assertFalse((root / "weights" / asset.dataset_dir).exists())
            self.assertFalse((root / "cache" / f"{asset.filename}.part").exists())

    def test_corrupt_zip_is_not_published_even_when_hash_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corrupt = b"this is not a ZIP archive"
            asset = _asset_for_bytes(corrupt, task_id=297)

            with self.assertRaises(zipfile.BadZipFile):
                prepare_weight_asset(
                    asset,
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    opener=_QueueOpener([_FakeResponse(corrupt, status=200)]),
                )

            self.assertFalse((root / "weights" / asset.dataset_dir).exists())

    def test_rejects_zip_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=113, extra_member=("../escape.txt", b"bad"))
            with self.assertRaisesRegex(ValueError, "unsafe ZIP member"):
                prepare_weight_asset(
                    asset,
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    opener=_QueueOpener([_FakeResponse(archive, status=200)]),
                )
            self.assertFalse((root / "escape.txt").exists())

    def test_rejects_missing_expected_model_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = _zip_bytes({"wrong/model.txt": b"not a model"})
            asset = _asset_for_bytes(archive, task_id=297)
            with self.assertRaisesRegex(ValueError, "expected model structure"):
                prepare_weight_asset(
                    asset,
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    opener=_QueueOpener([_FakeResponse(archive, status=200)]),
                )

    def test_deep_checkpoint_validation_precedes_task_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = 297
            dataset = f"Dataset{task_id}_fixture"
            archive = _zip_bytes(
                {
                    f"{dataset}/trainer/plans.json": b"{}",
                    f"{dataset}/trainer/dataset.json": b"{}",
                    f"{dataset}/trainer/fold_0/checkpoint_final.pth": b"not a checkpoint",
                }
            )
            asset = _asset_for_bytes(archive, task_id=task_id)

            with self.assertRaisesRegex(ValueError, "expected model structure"):
                prepare_weight_asset(
                    asset,
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    opener=_QueueOpener([_FakeResponse(archive, status=200)]),
                    manifest_sha256="a" * 64,
                )

            self.assertFalse((root / "weights" / dataset).exists())
            self.assertFalse(
                weights_setup._task_receipt_path(  # noqa: SLF001 - receipt contract.
                    root / "weights", asset
                ).exists()
            )

    def test_complete_existing_model_without_receipt_is_reacquired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=113)
            target = root / "weights" / asset.dataset_dir
            for relative in asset.required_files:
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(_valid_required_file_content(relative))
            opener = _QueueOpener([_FakeResponse(archive, status=200)])

            result = prepare_weight_asset(asset, weights_root=root / "weights", cache_root=root / "cache", opener=opener)

            self.assertEqual(result["status"], "installed")
            self.assertEqual(len(opener.requests), 1)

    def test_valid_task_receipt_skips_when_unreceipted_skip_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=113)
            manifest_sha = "a" * 64
            prepare_weight_asset(
                asset,
                weights_root=root / "weights",
                cache_root=root / "cache",
                opener=_QueueOpener([_FakeResponse(archive, status=200)]),
                manifest_sha256=manifest_sha,
            )
            opener = _QueueOpener([])

            result = prepare_weight_asset(
                asset,
                weights_root=root / "weights",
                cache_root=root / "cache",
                opener=opener,
                manifest_sha256=manifest_sha,
            )

            self.assertEqual(result["status"], "verified_receipt_skipped")
            self.assertTrue(result["archive_verified"])
            self.assertEqual(result["integrity_source"], "official-archive-sha256")
            self.assertEqual(opener.requests, [])

    def test_incomplete_existing_checkpoint_is_replaced_instead_of_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=115)
            target = root / "weights" / asset.dataset_dir
            for relative in asset.required_files:
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(
                    b"truncated checkpoint"
                    if relative.endswith("checkpoint_final.pth")
                    else _valid_required_file_content(relative)
                )
            opener = _QueueOpener([_FakeResponse(archive, status=200)])

            result = prepare_weight_asset(
                asset,
                weights_root=root / "weights",
                cache_root=root / "cache",
                opener=opener,
            )

            self.assertEqual(result["status"], "installed")
            self.assertEqual(len(opener.requests), 1)
            checkpoint = target / "trainer/fold_0/checkpoint_final.pth"
            self.assertTrue(zipfile.is_zipfile(checkpoint))

    def test_file_at_dataset_target_is_safely_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, asset = _fixture_asset(root, task_id=297)
            target = root / "weights" / asset.dataset_dir
            target.parent.mkdir(parents=True)
            target.write_text("stale non-directory target", encoding="utf-8")

            result = prepare_weight_asset(
                asset,
                weights_root=root / "weights",
                cache_root=root / "cache",
                opener=_QueueOpener([_FakeResponse(archive, status=200)]),
            )

            self.assertEqual(result["status"], "installed")
            self.assertTrue(target.is_dir())
            self.assertFalse(list((root / "weights").glob(f".{asset.dataset_dir}.previous-*")))

    def test_setup_lock_rejects_a_concurrent_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp) / "cache"
            with weights_setup.exclusive_setup_lock(cache_root):
                with self.assertRaisesRegex(
                    weights_setup.SetupWeightsBusyError,
                    "already running",
                ):
                    with weights_setup.exclusive_setup_lock(cache_root):
                        self.fail("a second setup unexpectedly acquired the same cache lock")

    def test_setup_lock_serializes_shared_weights_root_across_distinct_caches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_a = root / "cache-a"
            cache_b = root / "cache-b"
            weights = root / "weights"
            with weights_setup.exclusive_setup_lock(cache_a, weights_root=weights):
                with self.assertRaisesRegex(
                    weights_setup.SetupWeightsBusyError,
                    "already running",
                ):
                    with weights_setup.exclusive_setup_lock(cache_b, weights_root=weights):
                        self.fail("a second setup unexpectedly acquired the shared weights lock")

    def test_setup_lock_has_a_canonical_order_for_swapped_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            with weights_setup.exclusive_setup_lock(first, weights_root=second):
                with self.assertRaisesRegex(
                    weights_setup.SetupWeightsBusyError,
                    "already running",
                ):
                    with weights_setup.exclusive_setup_lock(second, weights_root=first):
                        self.fail("swapped cache/weights roots unexpectedly acquired both locks")

    def test_setup_lock_deduplicates_case_insensitive_root_aliases_by_inode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            canonical = parent / "CaseRoot"
            canonical.mkdir()
            alias = parent / "caseroot"
            if not alias.exists() or alias.stat().st_ino != canonical.stat().st_ino:
                self.skipTest("requires a case-insensitive filesystem")

            with weights_setup.exclusive_setup_lock(canonical, weights_root=alias) as roots:
                cache, weights = roots
                self.assertEqual(cache.identity, weights.identity)

    def test_setup_lock_rejects_symlink_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / "cache"
            cache_root.mkdir()
            unrelated = root / "unrelated.txt"
            unrelated.write_text("preserve me", encoding="utf-8")
            lock_path = cache_root / ".totalsegmentator-wrapper-weights-setup.lock"
            lock_path.symlink_to(unrelated)

            with self.assertRaises(OSError):
                with weights_setup.exclusive_setup_lock(cache_root):
                    self.fail("a symlink lock unexpectedly opened")

            self.assertEqual(unrelated.read_text(encoding="utf-8"), "preserve me")

    def test_setup_lock_rejects_hardlink_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / "cache"
            cache_root.mkdir()
            unrelated = root / "unrelated.txt"
            unrelated.write_text("preserve me", encoding="utf-8")
            lock_path = cache_root / ".totalsegmentator-wrapper-weights-setup.lock"
            os.link(unrelated, lock_path)

            with self.assertRaises(weights_setup.SetupWeightsError):
                with weights_setup.exclusive_setup_lock(cache_root):
                    self.fail("a hardlink lock unexpectedly opened")

            self.assertEqual(unrelated.read_text(encoding="utf-8"), "preserve me")

    def test_setup_preserves_markerless_legacy_staging_after_lock_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = load_setup_weight_manifest()
            for asset in assets:
                target = root / "weights" / asset.dataset_dir
                for relative in asset.required_files:
                    path = target / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_valid_required_file_content(relative))
            stale = root / "cache" / f".totalseg-staging-115-{'e' * 32}"
            stale.mkdir(parents=True)
            (stale / "orphaned.bin").write_bytes(b"orphaned")

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ):
                prepare_setup_weights(
                    (115, 297, 113),
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    progress_log=None,
                )

            self.assertTrue(stale.exists())
            self.assertEqual((stale / "orphaned.bin").read_bytes(), b"orphaned")

    def test_setup_restores_valid_orphan_backup_then_cleans_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = load_setup_weight_manifest()
            backup_asset = next(asset for asset in assets if asset.task_id == 115)
            nonce = "f" * 32
            backup = root / "weights" / weights_setup._backup_artifact_name(  # noqa: SLF001
                backup_asset,
                nonce,
            )
            for asset in assets:
                destination = root / "weights" / asset.dataset_dir
                if asset.task_id == 115:
                    destination = backup
                for relative in asset.required_files:
                    path = destination / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_valid_required_file_content(relative))
            marker = weights_setup._backup_marker_path(  # noqa: SLF001
                root / "weights",
                backup_asset,
                nonce,
            )
            weights_setup._write_recovery_marker(  # noqa: SLF001
                marker,
                kind="backup",
                asset=backup_asset,
                nonce=nonce,
                artifact_name=backup.name,
            )

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ):
                results = prepare_setup_weights(
                    (115, 297, 113),
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    progress_log=None,
                )

            self.assertEqual([item["status"] for item in results], ["skipped"] * 3)
            restored = root / "weights" / next(asset.dataset_dir for asset in assets if asset.task_id == 115)
            self.assertTrue(restored.is_dir())
            self.assertFalse(list((root / "weights").glob(".*.previous-*")))

    def test_stale_registry_cannot_launder_structurally_valid_old_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = load_setup_weight_manifest()
            for asset in assets:
                target = root / "weights" / asset.dataset_dir
                for relative in asset.required_files:
                    path = target / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_valid_required_file_content(relative))
            registry = root / "weights" / weights_setup.REGISTRY_FILENAME
            registry.write_text(
                json.dumps(
                    {
                        "schema": weights_setup.REGISTRY_SCHEMA,
                        "totalsegmentator_version": "2.14.0",
                        "setup_weights_manifest_sha256": "0" * 64,
                        "assets": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.prepare_weight_asset",
                side_effect=lambda asset, **_: {
                    "status": "installed",
                    "task_id": asset.task_id,
                    "target": str(root / "weights" / asset.dataset_dir),
                    "archive_verified": True,
                    "integrity_source": "official-archive-sha256",
                },
            ) as prepare:
                prepare_setup_weights(
                    (115, 297, 113),
                    weights_root=root / "weights",
                    cache_root=root / "cache",
                    progress_log=None,
                )

            self.assertEqual(prepare.call_count, 3)
            self.assertTrue(
                all(
                    call.kwargs.get("allow_structure_only_skip") is False
                    for call in prepare.call_args_list
                )
            )

    def test_v1_registry_is_not_silently_migrated_or_blessed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = load_setup_weight_manifest()
            for asset in assets:
                target = root / "weights" / asset.dataset_dir
                for relative in asset.required_files:
                    path = target / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_valid_required_file_content(relative))
            registry = root / "weights" / weights_setup.REGISTRY_FILENAME
            registry.write_text(
                json.dumps(
                    {
                        "schema": "totalsegmentator_wrapper_mac.setup_weights_registry.v1",
                        "totalsegmentator_version": "2.14.0",
                        "setup_weights_manifest_sha256": weights_setup.setup_weight_manifest_sha256(),
                        "assets": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.prepare_weight_asset",
                side_effect=ConnectionError("verified archive reacquisition required"),
            ) as prepare:
                with self.assertRaisesRegex(ConnectionError, "reacquisition required"):
                    prepare_setup_weights(
                        (115, 297, 113),
                        weights_root=root / "weights",
                        cache_root=root / "cache",
                        progress_log=None,
                    )

            self.assertIs(prepare.call_args.kwargs["allow_structure_only_skip"], False)
            self.assertEqual(
                json.loads(registry.read_text(encoding="utf-8"))["schema"],
                "totalsegmentator_wrapper_mac.setup_weights_registry.v1",
            )

    def test_no_registry_tree_migrates_only_after_deep_checkpoint_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = load_setup_weight_manifest()
            for asset in assets:
                target = root / "weights" / asset.dataset_dir
                for relative in asset.required_files:
                    path = target / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_valid_required_file_content(relative))
            broken = root / "weights" / assets[0].dataset_dir / assets[0].required_files[-1]
            broken.write_bytes(b"same tree shape, invalid checkpoint")

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.prepare_weight_asset",
                side_effect=ConnectionError("deep validation rejected legacy migration"),
            ) as prepare:
                with self.assertRaisesRegex(ConnectionError, "deep validation"):
                    prepare_setup_weights(
                        (115, 297, 113),
                        weights_root=root / "weights",
                        cache_root=root / "cache",
                        progress_log=None,
                    )

            self.assertIs(prepare.call_args.kwargs["allow_structure_only_skip"], False)
            self.assertFalse((root / "weights" / weights_setup.REGISTRY_FILENAME).exists())

    def test_registry_v2_rejects_same_size_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = load_setup_weight_manifest()
            for asset in assets:
                target = root / "weights" / asset.dataset_dir
                for relative in asset.required_files:
                    path = target / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_valid_required_file_content(relative))
            weights_setup._write_ready_registry(  # noqa: SLF001 - registry contract.
                root / "weights",
                assets,
                manifest_sha256=weights_setup.setup_weight_manifest_sha256(),
                integrity_source="legacy-deep-validation",
                archive_verified=False,
            )
            validate_setup_weights_registry(root / "weights")

            target = root / "weights" / assets[0].dataset_dir / assets[0].required_files[0]
            original_size = target.stat().st_size
            target.write_bytes(b"[]")
            self.assertEqual(target.stat().st_size, original_size)

            with self.assertRaisesRegex(
                weights_setup.SetupWeightsIntegrityError,
                "SHA-256 mismatch",
            ):
                validate_setup_weights_registry(root / "weights")

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.prepare_weight_asset",
                side_effect=ConnectionError("same-size tamper requires archive reacquisition"),
            ) as prepare:
                with self.assertRaisesRegex(ConnectionError, "archive reacquisition"):
                    prepare_setup_weights(
                        (115, 297, 113),
                        weights_root=root / "weights",
                        cache_root=root / "cache",
                        progress_log=None,
                    )
            self.assertIs(prepare.call_args.kwargs["allow_structure_only_skip"], False)

    def test_interrupted_forced_refresh_keeps_stale_registry_as_retry_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = load_setup_weight_manifest()
            for asset in assets:
                target = root / "weights" / asset.dataset_dir
                for relative in asset.required_files:
                    path = target / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_valid_required_file_content(relative))
            registry = root / "weights" / weights_setup.REGISTRY_FILENAME
            stale_payload = {
                "schema": weights_setup.REGISTRY_SCHEMA,
                "totalsegmentator_version": "2.14.0",
                "setup_weights_manifest_sha256": "0" * 64,
                "assets": [],
            }
            registry.write_text(json.dumps(stale_payload), encoding="utf-8")

            calls = 0

            def interrupted(asset, **kwargs):
                nonlocal calls
                calls += 1
                self.assertIs(kwargs.get("allow_structure_only_skip"), False)
                if calls == 2:
                    raise ConnectionError("fixture interruption")
                return {
                    "status": "installed",
                    "task_id": asset.task_id,
                    "target": str(root / "weights" / asset.dataset_dir),
                }

            with patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.metadata.version",
                return_value="2.14.0",
            ), patch(
                "totalsegmentator_wrapper_mac.totalseg_weights_setup.prepare_weight_asset",
                side_effect=interrupted,
            ):
                with self.assertRaises(ConnectionError):
                    prepare_setup_weights(
                        (115, 297, 113),
                        weights_root=root / "weights",
                        cache_root=root / "cache",
                        progress_log=None,
                    )

            self.assertEqual(json.loads(registry.read_text(encoding="utf-8")), stale_payload)

    def test_manifest_rejects_paths_that_escape_managed_roots(self) -> None:
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "totalsegmentator_wrapper_mac"
            / "totalseg_setup_weights_manifest.json"
        )
        original = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutations = (
            ("dataset_dir", ".."),
            ("filename", "../Dataset115_mandible.zip"),
        )
        for field, value in mutations:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                payload = json.loads(json.dumps(original))
                payload["assets"][0][field] = value
                if field == "filename":
                    item = payload["assets"][0]
                    item["url"] = (
                        "https://github.com/wasserth/TotalSegmentator/releases/download/"
                        f"{item['release_tag']}/{value}"
                    )
                    item["revalidation_evidence"]["filename"] = value
                    item["revalidation_evidence"]["official_url"] = item["url"]
                candidate = Path(tmp) / "manifest.json"
                candidate.write_text(json.dumps(payload), encoding="utf-8")

                with self.assertRaisesRegex(ValueError, "manifest|filename|dataset"):
                    load_setup_weight_manifest(candidate)

    def test_parses_real_tqdm_download_line(self) -> None:
        progress = parse_tqdm_download_progress(
            "Downloading:  98%|█████████▊| 227M/232M [04:13<00:13, 388kB/s]"
        )

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertEqual(progress["percent"], 98)
        self.assertEqual(progress["completed_bytes"], 227_000_000)
        self.assertEqual(progress["total_bytes"], 232_000_000)
        self.assertEqual(progress["eta_seconds"], 13)
        self.assertEqual(progress["rate_bps"], 388_000)

    def test_rejects_non_download_output(self) -> None:
        self.assertIsNone(parse_tqdm_download_progress("setup_totalseg complete"))

    def test_writer_records_task_identity_and_actual_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            progress_log = Path(tmp) / "launcher.log"
            writer = DownloadProgressWriter(progress_log, task_ids=(115, 297, 113))

            writer.start_task(297, index=2)
            writer.consume(
                "Downloading:  50%|█████     | 116M/232M [02:00<02:00, 966kB/s]\r"
            )
            writer.complete_task(297, index=2)

            payloads = []
            for line in progress_log.read_text(encoding="utf-8").splitlines():
                self.assertTrue(line.startswith("SETUP_DOWNLOAD_PROGRESS "))
                payloads.append(json.loads(line.split(" ", 1)[1]))
            self.assertEqual([item["status"] for item in payloads], ["starting", "downloading", "complete"])
            self.assertEqual(payloads[1]["task_id"], 297)
            self.assertEqual(payloads[1]["index"], 2)
            self.assertEqual(payloads[1]["task_total"], 3)
            self.assertEqual(payloads[1]["percent"], 50)
            self.assertEqual(payloads[1]["completed_bytes"], 116_000_000)
            self.assertEqual(payloads[1]["total_bytes"], 232_000_000)


if __name__ == "__main__":
    unittest.main()


class _FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        status: int,
        headers: dict[str, str] | None = None,
        fail_after: int | None = None,
        max_read: int | None = None,
        include_content_length: bool = True,
        url: str = "https://example.invalid/asset.zip",
        failure: BaseException | None = None,
    ) -> None:
        self.status = status
        self.headers = ({"Content-Length": str(len(payload))} if include_content_length else {}) | (headers or {})
        self._stream = io.BytesIO(payload)
        self._fail_after = fail_after
        self._max_read = max_read
        self.bytes_read = 0
        self._url = url
        self._failure = failure or ConnectionError("fixture interruption")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stream.close()

    def read(self, size: int = -1) -> bytes:
        if self._fail_after is not None and self._stream.tell() >= self._fail_after:
            raise self._failure
        if self._max_read is not None:
            size = self._max_read if size < 0 else min(size, self._max_read)
        chunk = self._stream.read(size)
        self.bytes_read += len(chunk)
        return chunk

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self._url


class _SwapPathOnFirstReadResponse(_FakeResponse):
    """Deterministically replaces an already-open partial path during streaming."""

    def __init__(
        self,
        payload: bytes,
        *,
        status: int,
        swap_path: Path,
        outside_target: Path,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(payload, status=status, headers=headers)
        self._swap_path = swap_path
        self._outside_target = outside_target
        self._swapped = False

    def read(self, size: int = -1) -> bytes:
        if not self._swapped:
            self._swap_path.unlink()
            self._swap_path.symlink_to(self._outside_target)
            self._swapped = True
        return super().read(size)


class _QueueOpener:
    def __init__(self, responses: list[_FakeResponse | BaseException]) -> None:
        self.responses = responses
        self.requests: list[object] = []
        self.range_headers: list[str | None] = []
        self.accept_encoding_headers: list[str | None] = []
        self.timeouts: list[int] = []

    def __call__(self, request: object, timeout: int):
        self.requests.append(request)
        self.timeouts.append(timeout)
        headers = getattr(request, "headers", {})
        self.range_headers.append(headers.get("Range"))
        self.accept_encoding_headers.append(
            request.get_header("Accept-encoding") if hasattr(request, "get_header") else None
        )
        if not self.responses:
            raise AssertionError("unexpected network request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def _valid_checkpoint_bytes() -> bytes:
    return _zip_bytes(
        {
            "checkpoint/data.pkl": b"fixture metadata",
            "checkpoint/data/0": b"fixture tensor bytes",
        }
    )


def _valid_required_file_content(relative: str) -> bytes:
    if relative.endswith(".json"):
        return b"{}"
    if relative.endswith("checkpoint_final.pth"):
        return _valid_checkpoint_bytes()
    return b"fixture"


def _asset_for_bytes(archive: bytes, *, task_id: int) -> WeightAsset:
    dataset = f"Dataset{task_id}_fixture"
    return WeightAsset(
        task_id=task_id,
        totalsegmentator_version="2.14.0",
        release_tag="fixture",
        filename=f"{dataset}.zip",
        url=f"https://example.invalid/{dataset}.zip",
        size_bytes=len(archive),
        sha256=hashlib.sha256(archive).hexdigest(),
        sha256_source="fixture",
        dataset_dir=dataset,
        required_files=("trainer/plans.json", "trainer/dataset.json", "trainer/fold_0/checkpoint_final.pth"),
    )


def _fixture_asset(
    root: Path,
    *,
    task_id: int,
    extra_member: tuple[str, bytes] | None = None,
) -> tuple[bytes, WeightAsset]:
    dataset = f"Dataset{task_id}_fixture"
    files = {
        f"{dataset}/trainer/plans.json": b"{}",
        f"{dataset}/trainer/dataset.json": b"{}",
        f"{dataset}/trainer/fold_0/checkpoint_final.pth": _valid_checkpoint_bytes(),
    }
    if extra_member is not None:
        files[extra_member[0]] = extra_member[1]
    archive = _zip_bytes(files)
    return archive, _asset_for_bytes(archive, task_id=task_id)


def _replace_asset(asset: WeightAsset, *, sha256: str) -> WeightAsset:
    return WeightAsset(**{**asset.__dict__, "sha256": sha256})


def _write_partial(cache_root: Path, asset: WeightAsset, content: bytes, *, url: str | None = None) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    (cache_root / f"{asset.filename}.part").write_bytes(content)
    (cache_root / f"{asset.filename}.part.json").write_text(
        json.dumps(asset.sidecar_payload() | {"url": url or asset.url}, sort_keys=True),
        encoding="utf-8",
    )


def _progress_payloads(path: Path) -> list[dict[str, object]]:
    return [json.loads(line.split(" ", 1)[1]) for line in path.read_text(encoding="utf-8").splitlines()]
