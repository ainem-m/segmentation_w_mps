from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import subprocess
import sys

from scripts.validate_cmake_package_discovery import find_forbidden_package_discovery_paths


class CMakePackageDiscoveryTests(unittest.TestCase):
    def test_allows_cmake_tool_paths_but_rejects_dependency_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "CMakeCache.txt"
            cache.write_text(
                "\n".join(
                    (
                        "CMAKE_COMMAND:INTERNAL=/opt/homebrew/bin/cmake",
                        "CMAKE_MAKE_PROGRAM:FILEPATH=/opt/homebrew/bin/gmake",
                        "CMAKE_INSTALL_PREFIX:PATH=/usr/local",
                        "CMAKE_IGNORE_PREFIX_PATH:UNINITIALIZED=/opt/homebrew;/usr/local",
                        "CMAKE_SYSTEM_IGNORE_PATH:UNINITIALIZED=/opt/homebrew;/usr/local",
                        "GDCM_DIR:PATH=/opt/homebrew/lib/gdcm-3.2",
                        "CMAKE_PREFIX_PATH:UNINITIALIZED=/usr/local",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                find_forbidden_package_discovery_paths(cache),
                [
                    "GDCM_DIR=/opt/homebrew/lib/gdcm-3.2",
                    "CMAKE_PREFIX_PATH=/usr/local",
                ],
            )

    def test_cli_accepts_tool_paths_and_rejects_a_discovery_path(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "validate_cmake_package_discovery.py"
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "CMakeCache.txt"
            cache.write_text("CMAKE_COMMAND:INTERNAL=/opt/homebrew/bin/cmake\n", encoding="utf-8")
            accepted = subprocess.run([sys.executable, str(script), str(cache)], capture_output=True, text=True, check=False)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            cache.write_text("GDCM_DIR:PATH=/opt/homebrew/lib/gdcm-3.2\n", encoding="utf-8")
            rejected = subprocess.run([sys.executable, str(script), str(cache)], capture_output=True, text=True, check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("forbidden package-discovery path", rejected.stderr)
