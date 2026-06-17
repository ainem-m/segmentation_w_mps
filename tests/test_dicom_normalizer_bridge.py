from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import totalsegmentator_wrapper_mac.dicom_normalizer_bridge as bridge
from totalsegmentator_wrapper_mac.dicom_normalizer_bridge import (
    DICOM_NORMALIZER_ENV,
    build_dicom_normalizer_audit_command,
    build_dicom_normalizer_convert_clean_command,
    build_dicom_normalizer_doctor_command,
    build_dicom_normalizer_prepare_rescue_command,
    build_dicom_normalizer_prepare_viewer_export_command,
    find_dicom_normalizer_binary,
    inspect_dicom_normalizer,
    run_dicom_normalizer_audit,
    run_dicom_normalizer_convert_clean,
    run_dicom_normalizer_doctor,
    run_dicom_normalizer_prepare_viewer_export,
)


class DicomNormalizerBridgeTests(unittest.TestCase):
    def test_build_command_uses_explicit_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_fake_binary(root / "totalsegmentator-wrapper-dicom-normalizer")
            command = build_dicom_normalizer_audit_command(
                dicom_dir=root / "dicom",
                output_json=root / "audit.json",
                binary=binary,
                project_root=root / "unused",
            )

            self.assertEqual(command[0], str(binary.resolve()))
            self.assertEqual(command[1], "audit")
            self.assertIn("--dicom-dir", command)
            self.assertIn("--output", command)

    def test_build_prepare_rescue_command_requires_spacing_and_series(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_fake_binary(root / "totalsegmentator-wrapper-dicom-normalizer")
            command = build_dicom_normalizer_prepare_rescue_command(
                dicom_dir=root / "dicom",
                output_dir=root / "rescue",
                series_number=200,
                patched_spacing="0.6,0.6,0.9375",
                binary=binary,
                project_root=root / "unused",
            )

            self.assertEqual(command[1], "prepare-rescue")
            self.assertIn("--series-number", command)
            self.assertIn("200", command)
            self.assertIn("--patched-spacing", command)
            self.assertIn("0.6,0.6,0.9375", command)

    def test_build_convert_clean_command_uses_series_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_fake_binary(root / "totalsegmentator-wrapper-dicom-normalizer")
            command = build_dicom_normalizer_convert_clean_command(
                dicom_dir=root / "dicom",
                output_dir=root / "clean",
                series_number=3,
                binary=binary,
                project_root=root / "unused",
            )

            self.assertEqual(command[1], "convert-clean")
            self.assertIn("--series-number", command)
            self.assertIn("3", command)
            self.assertIn("--output", command)

    def test_build_convert_clean_command_can_use_series_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_fake_binary(root / "totalsegmentator-wrapper-dicom-normalizer")
            command = build_dicom_normalizer_convert_clean_command(
                dicom_dir=root / "dicom",
                output_dir=root / "clean",
                series_key="1.2.3.clean",
                binary=binary,
                project_root=root / "unused",
            )

            self.assertEqual(command[1], "convert-clean")
            self.assertIn("--series-key", command)
            self.assertIn("1.2.3.clean", command)
            self.assertNotIn("--series-number", command)

    def test_build_prepare_viewer_export_command_uses_group_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_fake_binary(root / "totalsegmentator-wrapper-dicom-normalizer")
            command = build_dicom_normalizer_prepare_viewer_export_command(
                dicom_dir=root / "dicom",
                output_dir=root / "viewer",
                series_number=1002002,
                group_id="g003",
                binary=binary,
                project_root=root / "unused",
                dcm2niix=root / "dcm2niix",
            )

            self.assertEqual(command[1], "prepare-viewer-export")
            self.assertIn("--group-id", command)
            self.assertIn("g003", command)
            self.assertIn("--dcm2niix", command)

    def test_find_binary_uses_environment_variable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_fake_binary(root / "normalizer")
            with mock.patch.dict(os.environ, {DICOM_NORMALIZER_ENV: str(binary)}):
                found = find_dicom_normalizer_binary(project_root=root / "unused")

            self.assertEqual(found, binary.resolve())

    def test_find_binary_uses_packaged_bin_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package_dir = root / "pkg" / "totalsegmentator_wrapper_mac"
            binary = _write_fake_binary(package_dir / "bin" / "totalsegmentator-wrapper-dicom-normalizer")
            fake_module_file = package_dir / "dicom_normalizer_bridge.py"
            fake_module_file.parent.mkdir(parents=True, exist_ok=True)
            fake_module_file.write_text("# fake", encoding="utf-8")

            with mock.patch.object(bridge, "__file__", str(fake_module_file)):
                found = find_dicom_normalizer_binary(project_root=root / "unused")

            self.assertEqual(found, binary.resolve())

    def test_build_doctor_command_has_optional_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_fake_binary(root / "totalsegmentator-wrapper-dicom-normalizer")

            stdout_command = build_dicom_normalizer_doctor_command(
                binary=binary,
                project_root=root / "unused",
            )
            file_command = build_dicom_normalizer_doctor_command(
                output_json=root / "doctor.json",
                binary=binary,
                project_root=root / "unused",
            )

            self.assertEqual(stdout_command[1], "doctor")
            self.assertNotIn("--output", stdout_command)
            self.assertIn("--output", file_command)

    def test_run_audit_invokes_binary_and_writes_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_fake_binary(root / "normalizer")
            dicom_dir = root / "dicom"
            dicom_dir.mkdir()
            output_json = root / "logs" / "audit.json"

            result = run_dicom_normalizer_audit(
                dicom_dir=dicom_dir,
                output_json=output_json,
                binary=binary,
                project_root=root / "unused",
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.returncode, 0)
            self.assertTrue(output_json.exists())
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "fake")

    def test_run_audit_timeout_writes_failure_json_without_full_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_slow_binary(root / "normalizer")
            dicom_dir = root / "private patient dicom"
            dicom_dir.mkdir()
            output_json = root / "logs" / "audit.json"

            result = run_dicom_normalizer_audit(
                dicom_dir=dicom_dir,
                output_json=output_json,
                binary=binary,
                project_root=root / "unused",
                timeout_sec=1,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.returncode, 124)
            self.assertIn("timed out", result.error or "")
            self.assertTrue(output_json.exists())
            json_text = output_json.read_text(encoding="utf-8")
            payload = json.loads(json_text)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["reason"], "timeout")
            self.assertEqual(payload["timeout_sec"], 1)
            self.assertEqual(payload["dicom_dir"]["basename"], dicom_dir.name)
            self.assertIn("path_hash", payload["dicom_dir"])
            self.assertNotIn(str(dicom_dir), json_text)
            self.assertIn("possible_causes", payload)
            self.assertIn("next_actions", payload)

    def test_run_audit_nonzero_without_output_writes_failure_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_failing_binary(root / "normalizer")
            dicom_dir = root / "dicom"
            dicom_dir.mkdir()
            output_json = root / "logs" / "audit.json"
            output_json.parent.mkdir(parents=True)
            output_json.write_text('{"status": "stale"}', encoding="utf-8")

            result = run_dicom_normalizer_audit(
                dicom_dir=dicom_dir,
                output_json=output_json,
                binary=binary,
                project_root=root / "unused",
                timeout_sec=30,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.returncode, 2)
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["reason"], "normalizer_failed")
            self.assertIn("stderr boom", payload["stderr_tail"])
            self.assertIn("<dicom_dir>", payload["stderr_tail"])
            self.assertIn("<output_json>", payload["stderr_tail"])
            self.assertNotIn(str(dicom_dir), output_json.read_text(encoding="utf-8"))
            self.assertNotIn(str(output_json), output_json.read_text(encoding="utf-8"))

    def test_run_doctor_parses_binary_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_fake_binary(root / "normalizer")

            result = run_dicom_normalizer_doctor(
                binary=binary,
                project_root=root / "unused",
            )
            inspected = inspect_dicom_normalizer(
                binary=binary,
                project_root=root / "unused",
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(inspected["status"], "success")
            self.assertEqual(inspected["doctor"]["tool"]["version"], "fake")

    def test_run_convert_clean_invokes_binary_and_writes_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_fake_binary(root / "normalizer")
            dicom_dir = root / "dicom"
            dicom_dir.mkdir()
            output_dir = root / "clean"

            result = run_dicom_normalizer_convert_clean(
                dicom_dir=dicom_dir,
                output_dir=output_dir,
                series_number=1,
                binary=binary,
                project_root=root / "unused",
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.returncode, 0)
            self.assertTrue((output_dir / "convert_clean_metadata.json").exists())

    def test_run_prepare_viewer_export_invokes_binary_and_writes_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = _write_fake_binary(root / "normalizer")
            dicom_dir = root / "dicom"
            dicom_dir.mkdir()
            output_dir = root / "viewer_export"

            result = run_dicom_normalizer_prepare_viewer_export(
                dicom_dir=dicom_dir,
                output_dir=output_dir,
                series_number=1002002,
                group_id="g003",
                binary=binary,
                project_root=root / "unused",
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.returncode, 0)
            self.assertTrue((output_dir / "viewer_export_metadata.json").exists())


def _write_fake_binary(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'doctor':\n"
        "    payload = {'schema': 'fake.doctor', 'tool': {'version': 'fake'}, 'status': 'ok'}\n"
        "    if '--output' in sys.argv:\n"
        "        open(sys.argv[sys.argv.index('--output') + 1], 'w', encoding='utf-8').write(json.dumps(payload))\n"
        "    else:\n"
        "        print(json.dumps(payload))\n"
        "    raise SystemExit(0)\n"
        "out = sys.argv[sys.argv.index('--output') + 1]\n"
        "if sys.argv[1] == 'convert-clean':\n"
        "    import os\n"
        "    os.makedirs(out, exist_ok=True)\n"
        "    out = os.path.join(out, 'convert_clean_metadata.json')\n"
        "elif sys.argv[1] == 'prepare-viewer-export':\n"
        "    import os\n"
        "    os.makedirs(out, exist_ok=True)\n"
        "    out = os.path.join(out, 'viewer_export_metadata.json')\n"
        "elif sys.argv[1] == 'prepare-rescue':\n"
        "    import os\n"
        "    os.makedirs(out, exist_ok=True)\n"
        "    out = os.path.join(out, 'rescue_metadata.json')\n"
        "open(out, 'w', encoding='utf-8').write(json.dumps({'status': 'fake'}))\n"
        "print('fake normalizer ok')\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _write_slow_binary(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        "print('starting slow audit')\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _write_failing_binary(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('stdout before failure')\n"
        "print('stderr boom', file=sys.stderr)\n"
        "print('args: ' + ' '.join(sys.argv), file=sys.stderr)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


if __name__ == "__main__":
    unittest.main()
