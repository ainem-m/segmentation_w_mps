from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_third_party_license_inventory.py"
OVERRIDE_SCHEMA = "totalsegmentator_wrapper_mac.manual_license_overrides.v1"


def _write_dist(
    site: Path,
    *,
    name: str,
    version: str,
    metadata_lines: list[str],
    license_text: str | None = "Sample license text\n",
) -> None:
    dist_info = site / f"{name}-{version}.dist-info"
    dist_info.mkdir(parents=True)
    metadata_text = "\n".join(
        [
            "Metadata-Version: 2.1",
            f"Name: {name}",
            f"Version: {version}",
            *metadata_lines,
            "",
        ]
    )
    (dist_info / "METADATA").write_text(metadata_text, encoding="utf-8")
    if license_text is not None:
        (dist_info / "LICENSE").write_text(license_text, encoding="utf-8")


def _write_overrides(path: Path, overrides: list[dict]) -> None:
    path.write_text(
        json.dumps({"schema": OVERRIDE_SCHEMA, "overrides": overrides}, indent=2) + "\n",
        encoding="utf-8",
    )


class LicenseInventoryTests(unittest.TestCase):
    def test_site_path_is_required_to_avoid_ambient_python_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "out"
            overrides = root / "manual-overrides.json"
            _write_overrides(overrides, [])

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--output-dir",
                    str(output),
                    "--manual-overrides",
                    str(overrides),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--site-path is required", result.stderr)

    def test_resolved_inventory_copies_package_runtime_and_manual_license_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = root / "site"
            runtime = root / "python-runtime"
            output = root / "out"
            site.mkdir()
            runtime.mkdir()
            (runtime / "LICENSE.txt").write_text("Python runtime license\n", encoding="utf-8")
            overrides = root / "manual-overrides.json"
            (root / "lgplpkg-LICENSE.txt").write_text("Reviewed LGPL text\n", encoding="utf-8")
            _write_overrides(
                overrides,
                [
                    {
                        "package": "lgplpkg",
                        "version": "1.0.0",
                        "license": "LGPL-3.0-or-later",
                        "source_url": "https://example.test/lgplpkg",
                        "reviewed_at": "2026-06-23",
                        "decision": "accepted",
                        "reason": "Reviewed for app distribution.",
                        "license_file": "lgplpkg-LICENSE.txt",
                    }
                ],
            )
            _write_dist(site, name="mitpkg", version="1.0.0", metadata_lines=["License-Expression: MIT"])
            _write_dist(site, name="lgplpkg", version="1.0.0", metadata_lines=["License-Expression: LGPL-3.0-or-later"])

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--output-dir",
                    str(output),
                    "--dependency-set-id",
                    "fixture-deps",
                    "--site-path",
                    str(site),
                    "--python-runtime-root",
                    str(runtime),
                    "--manual-overrides",
                    str(overrides),
                    "--fail-on-unresolved",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            inventory = json.loads((output / "third_party_license_inventory.json").read_text(encoding="utf-8"))
            self.assertEqual(inventory["schema"], "totalsegmentator_wrapper_mac.third_party_license_inventory.v1")
            self.assertEqual(inventory["dependency_set_id"], "fixture-deps")
            self.assertEqual(inventory["unresolved_count"], 0)
            self.assertEqual(inventory["first_party_packages"], [])
            self.assertTrue(all(package["scope"] == "third-party" for package in inventory["packages"]))
            self.assertTrue((output / "THIRD_PARTY_LICENSES.txt").is_file())
            self.assertTrue((output / "python-packages" / "mitpkg-1.0.0" / "LICENSE").is_file())
            self.assertTrue((output / "python-packages" / "lgplpkg-1.0.0" / "manual-lgplpkg-LICENSE.txt").is_file())
            self.assertTrue((output / "python-runtime" / "LICENSE.txt").is_file())

    def test_unknown_license_metadata_fails_release_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = root / "site"
            output = root / "out"
            site.mkdir()
            overrides = root / "manual-overrides.json"
            _write_overrides(overrides, [])
            _write_dist(
                site,
                name="unknownpkg",
                version="1.0.0",
                metadata_lines=["License: UNKNOWN"],
                license_text=None,
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--output-dir",
                    str(output),
                    "--site-path",
                    str(site),
                    "--manual-overrides",
                    str(overrides),
                    "--fail-on-unresolved",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 1)
            inventory = json.loads((output / "third_party_license_inventory.json").read_text(encoding="utf-8"))
            codes = {item["code"] for item in inventory["unresolved"]}
            self.assertIn("license_metadata_unknown", codes)
            self.assertIn("license_text_missing", codes)

    def test_attention_license_fails_without_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = root / "site"
            output = root / "out"
            site.mkdir()
            overrides = root / "manual-overrides.json"
            _write_overrides(overrides, [])
            _write_dist(
                site,
                name="lgplpkg",
                version="1.0.0",
                metadata_lines=["License-Expression: LGPL-3.0-or-later"],
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--output-dir",
                    str(output),
                    "--site-path",
                    str(site),
                    "--manual-overrides",
                    str(overrides),
                    "--fail-on-unresolved",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 1)
            inventory = json.loads((output / "third_party_license_inventory.json").read_text(encoding="utf-8"))
            codes = {item["code"] for item in inventory["unresolved"]}
            self.assertIn("attention_license_requires_review", codes)

    def test_first_party_package_is_classified_without_weakening_license_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = root / "site"
            output = root / "out"
            site.mkdir()
            overrides = root / "manual-overrides.json"
            _write_overrides(overrides, [])
            _write_dist(
                site,
                name="totalsegmentator-wrapper-mac",
                version="0.2.1",
                metadata_lines=["License-Expression: Apache-2.0"],
                license_text="Apache License 2.0\n",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--output-dir",
                    str(output),
                    "--site-path",
                    str(site),
                    "--manual-overrides",
                    str(overrides),
                    "--first-party-package",
                    "totalsegmentator-wrapper-mac",
                    "--fail-on-unresolved",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            inventory = json.loads(
                (output / "third_party_license_inventory.json").read_text(encoding="utf-8")
            )
            self.assertEqual(inventory["first_party_packages"], ["totalsegmentator-wrapper-mac"])
            package = inventory["packages"][0]
            self.assertEqual(package["scope"], "first-party")
            self.assertEqual(package["license"], "Apache-2.0")
            summary = (output / "THIRD_PARTY_LICENSES.txt").read_text(encoding="utf-8")
            self.assertIn("First-party packages (classified separately)", summary)


if __name__ == "__main__":
    unittest.main()
