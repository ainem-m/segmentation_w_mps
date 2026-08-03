from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import verify_license_distribution
from scripts.verify_macos_binary_linkage import (
    MacOSBinaryLinkageError,
    assert_no_runtime_search_paths,
    assert_only_system_macos_dependencies,
    parse_otool_libraries,
    parse_otool_rpaths,
    verify_app_bundle_macos_linkage,
    verify_system_macos_linkage,
    verify_wheel_system_macos_linkage,
)


SYSTEM_ONLY_OTOOL = """/tmp/dcm2niix:
\t/usr/lib/libc++.1.dylib (compatibility version 1.0.0, current version 1900.1.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1351.0.0)
"""
MACHO_PREFIX = b"\xcf\xfa\xed\xfe"


def write_wheel(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


class MacOSBinaryLinkageTests(unittest.TestCase):
    @staticmethod
    def _write_app_macho(app: Path, relative: str) -> Path:
        path = app / "Contents" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(MACHO_PREFIX + relative.encode("utf-8"))
        return path

    @staticmethod
    def _bundle_runner(
        dependencies: dict[Path, tuple[str, ...]],
        rpaths: dict[Path, tuple[str, ...]] | None = None,
        install_names: dict[Path, str] | None = None,
        load_commands: dict[Path, str] | None = None,
    ):
        dependencies = {path.resolve(): value for path, value in dependencies.items()}
        rpaths = {
            path.resolve(): value for path, value in (rpaths or {}).items()
        }
        install_names = {
            path.resolve(): value for path, value in (install_names or {}).items()
        }
        load_commands = {
            path.resolve(): value for path, value in (load_commands or {}).items()
        }

        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            path = Path(command[-1]).resolve()
            operation = command[-2]
            if operation == "-L":
                lines = [f"{path}:"]
                install_name = install_names.get(path)
                if install_name is not None:
                    lines.append(f"\t{install_name} (compatibility version 1.0.0)")
                lines.extend(
                    f"\t{dependency} (compatibility version 1.0.0)"
                    for dependency in dependencies.get(path, ())
                )
                return subprocess.CompletedProcess(command, 0, "\n".join(lines) + "\n", "")
            if operation == "-D":
                install_name = install_names.get(path)
                output = f"{path}:\n"
                if install_name is not None:
                    output += install_name + "\n"
                return subprocess.CompletedProcess(command, 0, output, "")
            if path in load_commands:
                return subprocess.CompletedProcess(command, 0, load_commands[path], "")
            commands = ["Load command 0"]
            for rpath in rpaths.get(path, ()):
                commands.extend(
                    [
                        "Load command 6",
                        "          cmd LC_RPATH",
                        "      cmdsize 40",
                        f"         path {rpath} (offset 12)",
                    ]
                )
            return subprocess.CompletedProcess(command, 0, "\n".join(commands) + "\n", "")

        return runner

    def test_app_bundle_resolves_in_bundle_rpath_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            helper = self._write_app_macho(app, "Frameworks/libhelper.dylib")
            nested = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/lib/python3.12/site-packages/pkg/native.so",
            )
            python = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/bin/python3.12",
            )
            runner = self._bundle_runner(
                {
                    executable: (
                        "@rpath/libhelper.dylib",
                        "@loader_path/../Resources/python/cpython-3.12/lib/python3.12/site-packages/pkg/native.so",
                    ),
                    helper: ("/usr/lib/libSystem.B.dylib",),
                    nested: (
                        "@loader_path/../../../../../../../Frameworks/libhelper.dylib",
                    ),
                    python: ("/usr/lib/libSystem.B.dylib",),
                },
                {executable: ("@executable_path/../Frameworks",)},
                {helper: "@rpath/libhelper.dylib"},
            )

            verified = verify_app_bundle_macos_linkage(
                app,
                executable_name="Fixture",
                runner=runner,
            )

            self.assertEqual(
                set(verified),
                {
                    executable.resolve(),
                    helper.resolve(),
                    nested.resolve(),
                    python.resolve(),
                },
            )

    def test_app_bundle_allows_sealed_system_swift_rpath(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            runner = self._bundle_runner(
                {executable: ("@rpath/libswiftCore.dylib",)},
                {executable: ("/usr/lib/swift",)},
            )

            self.assertEqual(
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                ),
                (executable.resolve(),),
            )

    def test_app_bundle_rejects_generic_system_rpath_resolution(self) -> None:
        """A system directory alone cannot prove an arbitrary @rpath target exists."""

        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            runner = self._bundle_runner(
                {executable: ("@rpath/libmissing-or-evil.dylib",)},
                {executable: ("/usr/lib",)},
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "could not resolve"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_resolves_nested_python_executable_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            python = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/bin/python3.12",
            )
            libpython = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/lib/libpython3.12.dylib",
            )
            runner = self._bundle_runner(
                {
                    executable: ("/usr/lib/libSystem.B.dylib",),
                    python: ("@executable_path/../lib/libpython3.12.dylib",),
                    libpython: ("/usr/lib/libSystem.B.dylib",),
                },
                install_names={
                    libpython: "@rpath/libpython3.12.dylib",
                },
            )

            verified = verify_app_bundle_macos_linkage(
                app,
                executable_name="Fixture",
                runner=runner,
            )
            self.assertEqual(
                set(verified),
                {executable.resolve(), python.resolve(), libpython.resolve()},
            )

    def test_app_bundle_scans_nested_extension_and_rejects_homebrew(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            nested = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/lib/python3.12/site-packages/pkg/native.so",
            )
            python = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/bin/python3.12",
            )
            runner = self._bundle_runner(
                {
                    executable: ("/usr/lib/libSystem.B.dylib",),
                    nested: ("/opt/homebrew/lib/libjpeg.dylib",),
                    python: ("/usr/lib/libSystem.B.dylib",),
                }
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "/opt/homebrew"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_rejects_unresolved_rpath_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            (app / "Contents" / "Frameworks").mkdir()
            runner = self._bundle_runner(
                {executable: ("@rpath/libmissing.dylib",)},
                {executable: ("@executable_path/../Frameworks",)},
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "could not resolve"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_rejects_loader_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            outside = root / "outside.dylib"
            outside.write_bytes(MACHO_PREFIX + b"outside")
            runner = self._bundle_runner(
                {executable: ("@loader_path/../../../outside.dylib",)}
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "escapes app Contents"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            outside = root / "outside.dylib"
            outside.write_bytes(MACHO_PREFIX + b"outside")
            link = app / "Contents" / "Frameworks" / "outside.dylib"
            link.parent.mkdir(parents=True)
            link.symlink_to(outside)
            runner = self._bundle_runner(
                {executable: ("/usr/lib/libSystem.B.dylib",)}
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "symlink escapes"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_rejects_dot_segment_system_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            runner = self._bundle_runner(
                {executable: ("/usr/lib/../local/libescape.dylib",)}
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "system"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_rejects_nonexistent_system_rpath(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            runner = self._bundle_runner(
                {executable: ("/usr/lib/libSystem.B.dylib",)},
                {executable: ("/usr/lib/totalsegmentator-wrapper-not-a-directory",)},
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "LC_RPATH"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_rejects_dot_segment_system_rpath(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            runner = self._bundle_runner(
                {executable: ("@rpath/libescape.dylib",)},
                {executable: ("/usr/lib/../local",)},
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "LC_RPATH"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_rejects_custom_dylinker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            runner = self._bundle_runner(
                {executable: ("/usr/lib/libSystem.B.dylib",)},
                load_commands={
                    executable: """Load command 0
          cmd LC_LOAD_DYLINKER
      cmdsize 32
         name /tmp/evil-dyld (offset 12)
""",
                },
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "LC_LOAD_DYLINKER"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_rejects_dyld_environment_load_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            runner = self._bundle_runner(
                {executable: ("/usr/lib/libSystem.B.dylib",)},
                load_commands={
                    executable: """Load command 0
          cmd LC_DYLD_ENVIRONMENT
      cmdsize 64
         name DYLD_LIBRARY_PATH=/tmp/evil (offset 12)
""",
                },
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "LC_DYLD_ENVIRONMENT"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_inherits_root_rpath_through_a_dependency_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            first = self._write_app_macho(app, "Frameworks/libfirst.dylib")
            second = self._write_app_macho(app, "Frameworks/libsecond.dylib")
            runner = self._bundle_runner(
                {
                    executable: ("@rpath/libfirst.dylib",),
                    first: ("@rpath/libsecond.dylib",),
                    second: ("@rpath/libfirst.dylib",),
                },
                {executable: ("@executable_path/../Frameworks",)},
                {
                    first: "@rpath/libfirst.dylib",
                    second: "@rpath/libsecond.dylib",
                },
            )

            verified = verify_app_bundle_macos_linkage(
                app,
                executable_name="Fixture",
                runner=runner,
            )

            self.assertEqual(set(verified), {executable.resolve(), first.resolve(), second.resolve()})

    def test_app_bundle_uses_first_resolvable_rpath_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            first = self._write_app_macho(app, "FrameworksFirst/libhelper.dylib")
            second = self._write_app_macho(app, "Resources/bin/libhelper.dylib")
            runner = self._bundle_runner(
                {
                    executable: ("@rpath/libhelper.dylib",),
                    first: ("/usr/lib/libSystem.B.dylib",),
                    second: ("/usr/lib/libSystem.B.dylib",),
                },
                {
                    executable: (
                        "@executable_path/../FrameworksFirst",
                        "@executable_path/../Resources/bin",
                    ),
                },
                {first: "@rpath/libhelper.dylib", second: "@rpath/libhelper.dylib"},
            )

            verified = verify_app_bundle_macos_linkage(
                app,
                executable_name="Fixture",
                runner=runner,
            )

            self.assertEqual(set(verified), {executable.resolve(), first.resolve(), second.resolve()})

    def test_app_bundle_does_not_use_another_entrypoint_for_executable_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            parent = self._write_app_macho(app, "Frameworks/libparent.dylib")
            self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/bin/python3.12",
            )
            self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/bin/libchild.dylib",
            )
            runner = self._bundle_runner(
                {
                    executable: ("@loader_path/../Frameworks/libparent.dylib",),
                    parent: ("@executable_path/libchild.dylib",),
                }
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "could not resolve"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_rejects_unreachable_nested_macho(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            stray = self._write_app_macho(app, "Frameworks/libstray.dylib")
            runner = self._bundle_runner(
                {
                    executable: ("/usr/lib/libSystem.B.dylib",),
                    stray: ("/usr/lib/libSystem.B.dylib",),
                },
                install_names={stray: "@rpath/libstray.dylib"},
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "not reachable"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_does_not_root_nested_resources_bin_dylib(self) -> None:
        """Only direct ``Resources/bin`` executables are independent roots."""

        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            nested = self._write_app_macho(
                app,
                "Resources/bin/lib/libstray.dylib",
            )
            runner = self._bundle_runner(
                {
                    executable: ("/usr/lib/libSystem.B.dylib",),
                    nested: ("/usr/lib/libSystem.B.dylib",),
                },
                install_names={nested: "@rpath/libstray.dylib"},
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "not reachable"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_accepts_canonical_python_dynamic_extension(self) -> None:
        """A standard-library extension uses the interpreter's executable path."""

        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            python = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/bin/python3.12",
            )
            plugin = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/lib/python3.12/lib-dynload/_fixture.so",
            )
            helper = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/lib/libfixture.dylib",
            )
            runner = self._bundle_runner(
                {
                    executable: ("/usr/lib/libSystem.B.dylib",),
                    python: ("/usr/lib/libSystem.B.dylib",),
                    plugin: ("@executable_path/../lib/libfixture.dylib",),
                    helper: ("/usr/lib/libSystem.B.dylib",),
                },
            )

            verified = verify_app_bundle_macos_linkage(
                app,
                executable_name="Fixture",
                runner=runner,
            )

            self.assertEqual(
                set(verified),
                {
                    executable.resolve(),
                    python.resolve(),
                    plugin.resolve(),
                    helper.resolve(),
                },
            )

    def test_python_dynamic_extension_uses_its_rpath_and_python_rpath_stack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            python = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/bin/python3.12",
            )
            plugin = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/lib/python3.12/lib-dynload/_fixture.so",
            )
            plugin_local = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/lib/python3.12/lib-dynload/liblocal.dylib",
            )
            interpreter_rpath_target = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/Frameworks/libinterpreter.dylib",
            )
            runner = self._bundle_runner(
                {
                    executable: ("/usr/lib/libSystem.B.dylib",),
                    python: ("/usr/lib/libSystem.B.dylib",),
                    plugin: (
                        "@rpath/liblocal.dylib",
                        "@rpath/libinterpreter.dylib",
                    ),
                    plugin_local: ("/usr/lib/libSystem.B.dylib",),
                    interpreter_rpath_target: ("/usr/lib/libSystem.B.dylib",),
                },
                {
                    python: ("@loader_path/../Frameworks",),
                    plugin: ("@loader_path",),
                },
            )

            verified = verify_app_bundle_macos_linkage(
                app,
                executable_name="Fixture",
                runner=runner,
            )

            self.assertEqual(
                set(verified),
                {
                    executable.resolve(),
                    python.resolve(),
                    plugin.resolve(),
                    plugin_local.resolve(),
                    interpreter_rpath_target.resolve(),
                },
            )

    def test_python_dynamic_extension_cannot_use_the_main_app_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            python = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/bin/python3.12",
            )
            wrong_context_target = self._write_app_macho(
                app,
                "MacOS/libchild.dylib",
            )
            plugin = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/lib/python3.12/lib-dynload/_fixture.so",
            )
            runner = self._bundle_runner(
                {
                    executable: ("/usr/lib/libSystem.B.dylib",),
                    python: ("/usr/lib/libSystem.B.dylib",),
                    wrong_context_target: ("/usr/lib/libSystem.B.dylib",),
                    plugin: ("@executable_path/libchild.dylib",),
                },
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "Python dynamic plugin"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_python_dynamic_extension_rejects_an_external_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            python = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/bin/python3.12",
            )
            plugin = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/lib/python3.12/lib-dynload/_fixture.so",
            )
            runner = self._bundle_runner(
                {
                    executable: ("/usr/lib/libSystem.B.dylib",),
                    python: ("/usr/lib/libSystem.B.dylib",),
                    plugin: ("/opt/homebrew/lib/libfixture.dylib",),
                },
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "/opt/homebrew"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_app_bundle_rejects_a_stale_noncanonical_python_runtime_plugin(self) -> None:
        """Only the declared copied CPython 3.12 layout has a plugin exception."""

        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            executable = self._write_app_macho(app, "MacOS/Fixture")
            python = self._write_app_macho(
                app,
                "Resources/python/cpython-3.12/bin/python3.12",
            )
            stale_plugin = self._write_app_macho(
                app,
                "Resources/python/cpython-3.11/lib/python3.11/lib-dynload/_stale.so",
            )
            runner = self._bundle_runner(
                {
                    executable: ("/usr/lib/libSystem.B.dylib",),
                    python: ("/usr/lib/libSystem.B.dylib",),
                    stale_plugin: ("/usr/lib/libSystem.B.dylib",),
                },
            )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "not reachable"):
                verify_app_bundle_macos_linkage(
                    app,
                    executable_name="Fixture",
                    runner=runner,
                )

    def test_system_only_dependencies_pass(self) -> None:
        dependencies = parse_otool_libraries(SYSTEM_ONLY_OTOOL)
        self.assertEqual(
            dependencies,
            ("/usr/lib/libc++.1.dylib", "/usr/lib/libSystem.B.dylib"),
        )
        assert_only_system_macos_dependencies(dependencies)

    def test_homebrew_dependency_is_rejected(self) -> None:
        with self.assertRaisesRegex(MacOSBinaryLinkageError, "non-system"):
            assert_only_system_macos_dependencies(
                ("/opt/homebrew/opt/libjpeg/lib/libjpeg.8.dylib",)
            )

    def test_loader_path_dependency_is_rejected(self) -> None:
        with self.assertRaisesRegex(MacOSBinaryLinkageError, "non-system"):
            assert_only_system_macos_dependencies(("@loader_path/libcodec.dylib",))

    def test_rpath_is_rejected(self) -> None:
        load_commands = """Load command 6
          cmd LC_RPATH
      cmdsize 40
         path /opt/homebrew/lib (offset 12)
"""
        rpaths = parse_otool_rpaths(load_commands)
        self.assertEqual(rpaths, ("/opt/homebrew/lib",))
        with self.assertRaisesRegex(MacOSBinaryLinkageError, "LC_RPATH"):
            assert_no_runtime_search_paths(rpaths)

    def test_malformed_rpath_is_rejected(self) -> None:
        with self.assertRaisesRegex(MacOSBinaryLinkageError, "LC_RPATH command"):
            parse_otool_rpaths("cmd LC_RPATH\ncmdsize 40\n")

    def test_malformed_otool_dependency_output_is_rejected(self) -> None:
        with self.assertRaisesRegex(MacOSBinaryLinkageError, "binary header"):
            parse_otool_libraries("not otool output")

    def test_verifier_invokes_both_otool_views_for_a_regular_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "dcm2niix"
            binary.write_bytes(b"fixture")
            invocations: list[tuple[str, ...]] = []

            def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                invocations.append(tuple(command))
                if command[-2] == "-L":
                    return subprocess.CompletedProcess(command, 0, SYSTEM_ONLY_OTOOL, "")
                return subprocess.CompletedProcess(command, 0, "Load command 0\n", "")

            verify_system_macos_linkage(binary, runner=runner)
            self.assertEqual(
                invocations,
                [
                    ("otool", "-arch", "arm64", "-L", str(binary)),
                    ("otool", "-arch", "arm64", "-l", str(binary)),
                ],
            )

    def test_system_linkage_rejects_dot_segment_escape(self) -> None:
        with self.assertRaisesRegex(MacOSBinaryLinkageError, "system"):
            assert_only_system_macos_dependencies(
                ("/System/Library/../../tmp/escape.dylib",)
            )

    def test_system_linkage_accepts_only_the_standard_dylinker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "dcm2niix"
            binary.write_bytes(b"fixture")

            def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                if command[-2] == "-L":
                    return subprocess.CompletedProcess(command, 0, SYSTEM_ONLY_OTOOL, "")
                return subprocess.CompletedProcess(
                    command,
                    0,
                    """Load command 0
          cmd LC_LOAD_DYLINKER
      cmdsize 32
         name /usr/lib/dyld (offset 12)
""",
                    "",
                )

            verify_system_macos_linkage(binary, runner=runner)

    def test_verifier_rejects_a_homebrew_link_from_otool_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "dcm2niix"
            binary.write_bytes(b"fixture")

            def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                if command[-2] == "-L":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        f"{binary}:\n\t/opt/homebrew/lib/libjpeg.dylib (compatibility version 1.0.0)\n",
                        "",
                    )
                return subprocess.CompletedProcess(command, 0, "Load command 0\n", "")

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "/opt/homebrew"):
                verify_system_macos_linkage(binary, runner=runner)

    def test_wheel_extracts_and_checks_every_macho_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel = root / "fixture.whl"
            write_wheel(
                wheel,
                {
                    "package/wrapper.dylib": MACHO_PREFIX + b"wrapper",
                    "package/fpsample.so": MACHO_PREFIX + b"fpsample",
                    "package/plain.py": b"not native",
                },
            )
            invocations: list[tuple[str, ...]] = []

            def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                invocations.append(tuple(command))
                if command[-2] == "-L":
                    return subprocess.CompletedProcess(command, 0, SYSTEM_ONLY_OTOOL, "")
                return subprocess.CompletedProcess(command, 0, "Load command 0\n", "")

            members = verify_wheel_system_macos_linkage(wheel, runner=runner)
            self.assertEqual(
                members,
                (
                    f"{wheel}!/package/wrapper.dylib",
                    f"{wheel}!/package/fpsample.so",
                ),
            )
            self.assertEqual(len(invocations), 4)
            self.assertTrue(
                all("totalsegmentator-wrapper-wheel-linkage." in command[-1] for command in invocations)
            )

    def test_wheel_aggregates_homebrew_and_rpath_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel = root / "fixture.whl"
            write_wheel(
                wheel,
                {
                    "package/wrapper.dylib": MACHO_PREFIX + b"homebrew",
                    "package/fpsample.so": MACHO_PREFIX + b"rpath",
                },
            )

            def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                payload = Path(command[-1]).read_bytes()
                if command[-2] == "-L":
                    dependency = (
                        "/opt/homebrew/lib/libjpeg.dylib"
                        if b"homebrew" in payload
                        else "/usr/lib/libSystem.B.dylib"
                    )
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        f"{command[-1]}:\n\t{dependency} (compatibility version 1.0.0)\n",
                        "",
                    )
                load_commands = (
                    "Load command 0\n"
                    if b"rpath" not in payload
                    else "Load command 6\n  cmd LC_RPATH\n     path /opt/homebrew/lib (offset 12)\n"
                )
                return subprocess.CompletedProcess(command, 0, load_commands, "")

            with self.assertRaises(MacOSBinaryLinkageError) as raised:
                verify_wheel_system_macos_linkage(wheel, runner=runner)
            message = str(raised.exception)
            self.assertIn("package/wrapper.dylib", message)
            self.assertIn("/opt/homebrew/lib/libjpeg.dylib", message)
            self.assertIn("package/fpsample.so", message)
            self.assertIn("LC_RPATH", message)

    def test_wheel_rejects_embedded_dyld_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel = root / "fixture.whl"
            write_wheel(wheel, {"package/native.so": MACHO_PREFIX + b"environment"})

            def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                payload = Path(command[-1]).read_bytes()
                operation = command[-2]
                if operation == "-L":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        f"{command[-1]}:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n",
                        "",
                    )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    """Load command 0
          cmd LC_DYLD_ENVIRONMENT
      cmdsize 64
         name DYLD_INSERT_LIBRARIES=/tmp/evil.dylib (offset 12)
"""
                    if b"environment" in payload
                    else "Load command 0\n",
                    "",
                )

            with self.assertRaisesRegex(MacOSBinaryLinkageError, "LC_DYLD_ENVIRONMENT"):
                verify_wheel_system_macos_linkage(wheel, runner=runner)

    def test_wheel_rejects_path_traversal_before_extracting_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "unsafe.whl"
            write_wheel(wheel, {"../escape.dylib": MACHO_PREFIX + b"unsafe"})
            with self.assertRaisesRegex(MacOSBinaryLinkageError, "unsafe wheel member"):
                verify_wheel_system_macos_linkage(wheel)

    def test_mounted_dmg_routes_through_the_app_linkage_gate(self) -> None:
        """The mounted-DMG verifier must delegate to the full app verifier."""

        readme = "\n".join(
            (
                "Apache License 2.0",
                "DentalSegmentator-NOTICE.txt",
                "ToothSeg-NOTICE.txt",
                "MeshSegNet-Teeth3DS-Checkpoint-NOTICE.txt",
                "TGNet-User-Provided-Checkpoint-NOTICE.txt",
                "https://forms.gle/QFPwF1Pi5C8bmSuw6",
            )
        )
        commands: list[tuple[str, ...]] = []

        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(tuple(command))
            if command[:2] == ["hdiutil", "attach"]:
                mount = Path(command[5])
                app = mount / "TotalSegmentator Wrapper for Mac.app"
                resources = app / "Contents" / "Resources"
                resources.mkdir(parents=True)
                (resources / "setup_manifest.json").write_text(
                    json.dumps({"python_runtime": {"bundled": False}}),
                    encoding="utf-8",
                )
                (mount / "Applications").symlink_to(
                    "/Applications", target_is_directory=True
                )
                for name in verify_license_distribution.DMG_ROOT_ALLOWLIST - {
                    "Applications",
                    "TotalSegmentator Wrapper for Mac.app",
                }:
                    (mount / name).write_text("fixture", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            dmg = Path(tmp) / "fixture.dmg"
            dmg.write_bytes(b"fixture")
            with (
                patch.object(
                    verify_license_distribution.subprocess,
                    "run",
                    side_effect=runner,
                ),
                patch.object(verify_license_distribution, "verify_apache_license"),
                patch.object(verify_license_distribution, "verify_notice"),
                patch.object(verify_license_distribution, "text", return_value=readme),
                patch.object(
                    verify_license_distribution,
                    "verified_authorized_sample_nifti_paths",
                    return_value=frozenset(),
                ),
                patch.object(verify_license_distribution, "verify_app") as verify_app,
            ):
                verify_license_distribution.verify_dmg(
                    dmg,
                    expected_version="0.4.1",
                    expected_source_commit="abc123",
                )

        self.assertEqual(commands[0][:5], ("hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint"))
        self.assertEqual(commands[-1][:2], ("hdiutil", "detach"))
        verify_app.assert_called_once()
        app, version, source_commit = verify_app.call_args.args
        self.assertEqual(app.name, "TotalSegmentator Wrapper for Mac.app")
        self.assertEqual(app.parent, Path(commands[0][5]))
        self.assertEqual(version, "0.4.1")
        self.assertEqual(source_commit, "abc123")
        distribution_source = Path(verify_license_distribution.__file__).read_text(
            encoding="utf-8"
        )
        app_verifier_source = distribution_source[
            distribution_source.index("def verify_app(") : distribution_source.index(
                "def verify_dmg("
            )
        ]
        self.assertIn("verify_app_bundle_macos_linkage(app)", app_verifier_source)


if __name__ == "__main__":
    unittest.main()
