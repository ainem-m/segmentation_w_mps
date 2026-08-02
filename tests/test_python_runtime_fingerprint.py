from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.python_runtime_fingerprint import (
    RuntimeFingerprintError,
    _regular_file_digest,
    fingerprint_runtime_tree,
)


class PythonRuntimeFingerprintTests(unittest.TestCase):
    def _write_runtime(self, root: Path, *, reverse_creation_order: bool = False) -> None:
        entries = [
            ("dir", "bin", None),
            ("dir", "lib", None),
            ("file", "bin/python3.12", b"python runtime executable\n"),
            ("file", "lib/stdlib.py", b"VALUE = 'stdlib'\n"),
            ("symlink", "lib/stdlib_alias.py", "stdlib.py"),
        ]
        if reverse_creation_order:
            # Directories must exist before their children, but their creation
            # order is deliberately different from the ordinary fixture.
            entries = [entries[1], entries[0], *reversed(entries[2:])]

        root.mkdir()
        for kind, relative, payload in entries:
            path = root / relative
            if kind == "dir":
                path.mkdir()
            elif kind == "file":
                path.write_bytes(payload)
            else:
                os.symlink(payload, path)

        (root / "bin").chmod(0o755)
        (root / "lib").chmod(0o755)
        (root / "bin/python3.12").chmod(0o755)
        (root / "lib/stdlib.py").chmod(0o644)
        root.chmod(0o755)

    def test_fingerprint_is_independent_of_creation_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            self._write_runtime(first)
            self._write_runtime(second, reverse_creation_order=True)

            self.assertEqual(fingerprint_runtime_tree(first), fingerprint_runtime_tree(second))

    def test_content_symlink_and_mode_changes_change_the_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            self._write_runtime(root)
            original = fingerprint_runtime_tree(root)

            (root / "lib/stdlib.py").write_bytes(b"VALUE = 'changed'\n")
            content_changed = fingerprint_runtime_tree(root)
            self.assertNotEqual(original, content_changed)

            (root / "lib/stdlib.py").write_bytes(b"VALUE = 'stdlib'\n")
            alias = root / "lib/stdlib_alias.py"
            alias.unlink()
            os.symlink("../bin/python3.12", alias)
            symlink_changed = fingerprint_runtime_tree(root)
            self.assertNotEqual(original, symlink_changed)

            alias.unlink()
            os.symlink("stdlib.py", alias)
            (root / "bin/python3.12").chmod(0o644)
            mode_changed = fingerprint_runtime_tree(root)
            self.assertNotEqual(original, mode_changed)

    def test_unsafe_symlink_targets_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            absolute_root = Path(tmp) / "absolute"
            absolute_root.mkdir()
            os.symlink("/tmp/not-a-runtime-member", absolute_root / "escaped")
            with self.assertRaisesRegex(RuntimeFingerprintError, "absolute"):
                fingerprint_runtime_tree(absolute_root)

            escaping_root = Path(tmp) / "escaping"
            escaping_root.mkdir()
            os.symlink("../../outside", escaping_root / "escaped")
            with self.assertRaisesRegex(RuntimeFingerprintError, "escapes"):
                fingerprint_runtime_tree(escaping_root)

    def test_unsupported_filesystem_nodes_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            root.mkdir()
            fifo = root / "unsupported-fifo"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(RuntimeFingerprintError, "unsupported filesystem entry"):
                fingerprint_runtime_tree(root)

    def test_regular_file_metadata_race_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp) / "payload.py"
            payload.write_bytes(b"value = 1\n")
            expected = os.lstat(payload)
            real_fstat = os.fstat

            def changed_mtime(descriptor: int) -> SimpleNamespace:
                actual = real_fstat(descriptor)
                return SimpleNamespace(
                    st_mode=actual.st_mode,
                    st_dev=actual.st_dev,
                    st_ino=actual.st_ino,
                    st_size=actual.st_size,
                    st_mtime_ns=actual.st_mtime_ns + 1,
                )

            with patch(
                "scripts.python_runtime_fingerprint.os.fstat",
                side_effect=changed_mtime,
            ):
                with self.assertRaisesRegex(RuntimeFingerprintError, "metadata changed before"):
                    _regular_file_digest(str(payload), expected)
