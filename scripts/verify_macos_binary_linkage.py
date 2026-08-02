#!/usr/bin/env python3
"""Reject packaged macOS executables that depend on unsafe runtime libraries.

This is intentionally narrower than a general Mach-O parser.  It consumes the
``otool`` view used by the macOS release build and fails closed.  Standalone
binaries and wheels may link only libraries supplied by macOS itself.  A
complete app bundle may additionally use tokenized install names which resolve
to existing Mach-O files inside that same app's ``Contents`` directory.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable, Sequence


TARGET_ARCHITECTURE = "arm64"
SYSTEM_LIBRARY_ROOTS = (
    PurePosixPath("/System/Library"),
    PurePosixPath("/usr/lib"),
)
CANONICAL_PYTHON_RUNTIME = Path("Resources/python/cpython-3.12")
CANONICAL_PYTHON_EXECUTABLE = Path("bin/python3.12")
CANONICAL_PYTHON_LIBRARY = Path("lib/python3.12")
RPATH_LINE = re.compile(r"^\s*path (.+) \(offset [0-9]+\)$")
LOAD_COMMAND_LINE = re.compile(r"^\s*Load command [0-9]+\s*$")
LOAD_COMMAND_NAME_LINE = re.compile(r"^\s*name (.+) \(offset [0-9]+\)$")
MACHO_MAGICS = frozenset(
    {
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }
)
MAX_WHEEL_NATIVE_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_WHEEL_TOTAL_NATIVE_BYTES = 4 * 1024 * 1024 * 1024


class MacOSBinaryLinkageError(RuntimeError):
    """The binary has a dependency that must not enter the release bundle."""


@dataclass(frozen=True)
class DyldLoadCommands:
    """The dyld-affecting load commands emitted by ``otool -l``."""

    rpaths: tuple[str, ...]
    load_dylinkers: tuple[str, ...]
    dyld_environments: tuple[str, ...]


@dataclass(frozen=True)
class _BundleRPath:
    """One canonical ordered element of a dyld run-path stack."""

    kind: str
    path: Path


@dataclass(frozen=True)
class _BundleBinaryMetadata:
    path: Path
    dependencies: tuple[str, ...]
    rpath_values: tuple[str, ...]


def parse_otool_libraries(output: str) -> tuple[str, ...]:
    """Return dependency install names from ``otool -L`` output."""

    lines = output.splitlines()
    if not lines or not lines[0].rstrip().endswith(":"):
        raise MacOSBinaryLinkageError("otool -L output has no binary header")
    dependencies: list[str] = []
    for line in lines[1:]:
        if not line.startswith((" ", "\t")):
            raise MacOSBinaryLinkageError(
                f"otool -L output has an unexpected dependency line: {line!r}"
            )
        dependency = line.strip().split(" (", 1)[0]
        if not dependency:
            raise MacOSBinaryLinkageError("otool -L output contains an empty dependency")
        dependencies.append(dependency)
    return tuple(dependencies)


def _parse_otool_load_command_values(
    output: str,
    *,
    command: str,
    field_pattern: re.Pattern[str],
    field_label: str,
) -> tuple[str, ...]:
    """Return one strictly parsed value for each matching ``otool -l`` command."""

    lines = output.splitlines()
    values: list[str] = []
    expected = f"cmd {command}"
    for index, line in enumerate(lines):
        if line.strip() != expected:
            continue
        block_end = len(lines)
        for candidate_index in range(index + 1, len(lines)):
            if LOAD_COMMAND_LINE.fullmatch(lines[candidate_index]):
                block_end = candidate_index
                break
        matches = [
            match.group(1)
            for candidate in lines[index + 1 : block_end]
            if (match := field_pattern.fullmatch(candidate)) is not None
        ]
        if len(matches) != 1:
            raise MacOSBinaryLinkageError(
                f"otool -l {command} command has no unique {field_label}"
            )
        values.append(matches[0])
    return tuple(values)


def parse_otool_rpaths(output: str) -> tuple[str, ...]:
    """Return LC_RPATH values from ``otool -l`` output, rejecting malformed data."""

    return _parse_otool_load_command_values(
        output,
        command="LC_RPATH",
        field_pattern=RPATH_LINE,
        field_label="path",
    )


def parse_otool_dyld_load_commands(output: str) -> DyldLoadCommands:
    """Parse every load command that can alter dyld's library resolution."""

    return DyldLoadCommands(
        rpaths=parse_otool_rpaths(output),
        load_dylinkers=_parse_otool_load_command_values(
            output,
            command="LC_LOAD_DYLINKER",
            field_pattern=LOAD_COMMAND_NAME_LINE,
            field_label="name",
        ),
        dyld_environments=_parse_otool_load_command_values(
            output,
            command="LC_DYLD_ENVIRONMENT",
            field_pattern=LOAD_COMMAND_NAME_LINE,
            field_label="name",
        ),
    )


def parse_otool_install_name(output: str) -> str | None:
    """Return an LC_ID_DYLIB value from ``otool -D`` output, if present."""

    lines = output.splitlines()
    if not lines or not lines[0].rstrip().endswith(":"):
        raise MacOSBinaryLinkageError("otool -D output has no binary header")
    values = [line.strip() for line in lines[1:] if line.strip()]
    if len(values) > 1:
        raise MacOSBinaryLinkageError("otool -D output contains multiple install names")
    if not values:
        return None
    value = values[0]
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise MacOSBinaryLinkageError("otool -D output contains an unsafe install name")
    return value


def _sealed_system_path(
    value: str,
    *,
    label: str,
    allow_root: bool,
) -> Path:
    """Return a normalized path below an immutable macOS system root.

    ``otool`` emits install-name strings, not canonical filesystem paths.  A
    raw prefix check is unsafe because `/usr/lib/../local/...` is an external
    path at dyld resolution time.  Keep the install name lexical and canonical
    before any optional filesystem inspection.
    """

    if (
        not value
        or not value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise MacOSBinaryLinkageError(f"{label} is not a normalized sealed system path: {value!r}")
    raw_parts = value.split("/")
    if (
        raw_parts[0] != ""
        or any(part in ("", ".", "..") for part in raw_parts[1:])
    ):
        raise MacOSBinaryLinkageError(f"{label} is not a normalized sealed system path: {value}")
    candidate = PurePosixPath(value)
    if str(candidate) != value:
        raise MacOSBinaryLinkageError(f"{label} is not a normalized sealed system path: {value}")
    for root in SYSTEM_LIBRARY_ROOTS:
        if candidate == root:
            if allow_root:
                return Path(str(candidate))
            break
        if root in candidate.parents:
            return Path(str(candidate))
    raise MacOSBinaryLinkageError(
        f"{label} is not under a sealed macOS system root: {value}"
    )


def _sealed_system_rpath(value: str) -> Path:
    """Return an existing, canonical sealed-system run-path directory."""

    path = _sealed_system_path(value, label="LC_RPATH", allow_root=True)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MacOSBinaryLinkageError(
            f"LC_RPATH is not an existing sealed system directory: {value}"
        ) from exc
    if not resolved.is_dir():
        raise MacOSBinaryLinkageError(
            f"LC_RPATH is not an existing sealed system directory: {value}"
        )
    _sealed_system_path(str(resolved), label="LC_RPATH", allow_root=True)
    return resolved


def assert_only_system_macos_dependencies(dependencies: Sequence[str]) -> None:
    """Fail when a dependency is not loaded from the sealed macOS runtime."""

    unexpected: list[str] = []
    for dependency in dependencies:
        try:
            _sealed_system_path(
                dependency,
                label="Mach-O dependency",
                allow_root=False,
            )
        except MacOSBinaryLinkageError:
            unexpected.append(dependency)
    if unexpected:
        rendered = ", ".join(unexpected)
        raise MacOSBinaryLinkageError(
            "non-system Mach-O dependency is not allowed in the release binary: "
            + rendered
        )


def assert_no_runtime_search_paths(rpaths: Sequence[str]) -> None:
    """Fail closed instead of allowing an rpath to resolve an unexpected dylib."""

    if rpaths:
        raise MacOSBinaryLinkageError(
            "LC_RPATH is not allowed in the self-contained release binary: "
            + ", ".join(rpaths)
        )


def _validate_dyld_load_commands(commands: DyldLoadCommands) -> None:
    """Reject load commands that bypass the dependency policy."""

    if len(commands.load_dylinkers) > 1:
        raise MacOSBinaryLinkageError("multiple LC_LOAD_DYLINKER commands are not allowed")
    if commands.load_dylinkers and commands.load_dylinkers[0] != "/usr/lib/dyld":
        raise MacOSBinaryLinkageError(
            "LC_LOAD_DYLINKER must be exactly /usr/lib/dyld: "
            + commands.load_dylinkers[0]
        )
    if commands.dyld_environments:
        raise MacOSBinaryLinkageError(
            "LC_DYLD_ENVIRONMENT is not allowed in a release Mach-O: "
            + ", ".join(commands.dyld_environments)
        )


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run_otool(
    path: Path,
    arguments: Sequence[str],
    *,
    runner: Runner = subprocess.run,
) -> str:
    try:
        completed = runner(
            ["otool", "-arch", TARGET_ARCHITECTURE, *arguments, str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise MacOSBinaryLinkageError(f"could not execute otool: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise MacOSBinaryLinkageError(f"otool {' '.join(arguments)} failed: {detail}")
    return completed.stdout


def verify_system_macos_linkage(
    path: Path,
    *,
    runner: Runner = subprocess.run,
) -> None:
    """Verify that a Mach-O has only system dependencies and no LC_RPATH."""

    if not path.is_file() or path.is_symlink():
        raise MacOSBinaryLinkageError(
            f"binary must be a regular non-symlink file: {path}"
        )
    dependencies = parse_otool_libraries(_run_otool(path, ["-L"], runner=runner))
    commands = parse_otool_dyld_load_commands(
        _run_otool(path, ["-l"], runner=runner)
    )
    _validate_dyld_load_commands(commands)
    assert_only_system_macos_dependencies(dependencies)
    assert_no_runtime_search_paths(commands.rpaths)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_safe_bundle_symlink(path: Path, contents: Path) -> None:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MacOSBinaryLinkageError(f"app bundle contains a broken symlink: {path}: {exc}") from exc
    if not _is_relative_to(resolved, contents):
        raise MacOSBinaryLinkageError(
            f"app bundle symlink escapes app Contents: {path} -> {resolved}"
        )


def _collect_app_bundle_machos(contents: Path) -> tuple[Path, ...]:
    """Enumerate every regular Mach-O while rejecting bundle symlink escapes."""

    pending = [contents]
    machos: list[Path] = []
    errors: list[str] = []
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            errors.append(f"could not enumerate app bundle directory {directory}: {exc}")
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink():
                    _assert_safe_bundle_symlink(path, contents)
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    errors.append(f"app bundle contains a non-regular filesystem entry: {path}")
                    continue
                with path.open("rb") as handle:
                    prefix = handle.read(4)
            except (MacOSBinaryLinkageError, OSError, RuntimeError) as exc:
                errors.append(str(exc))
                continue
            if prefix in MACHO_MAGICS:
                machos.append(path)
    if errors:
        raise MacOSBinaryLinkageError("app bundle scan failed:\n" + "\n".join(errors))
    return tuple(sorted(machos))


def _token_suffix(value: str, token: str, *, label: str) -> str | None:
    if value == token:
        return ""
    prefix = token + "/"
    if not value.startswith(prefix):
        return None
    suffix = value[len(prefix) :]
    if (
        not suffix
        or "\\" in suffix
        or "\x00" in suffix
        or any(ord(character) < 32 or ord(character) == 127 for character in suffix)
        or any(part == "" for part in suffix.split("/"))
    ):
        raise MacOSBinaryLinkageError(f"{label} contains an unsafe path: {value}")
    return suffix


def _canonical_bundle_candidate(
    candidate: Path,
    *,
    contents: Path,
    label: str,
    require_directory: bool,
) -> Path | None:
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        try:
            unresolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise MacOSBinaryLinkageError(f"could not normalize {label}: {candidate}: {exc}") from exc
        if not _is_relative_to(unresolved, contents):
            raise MacOSBinaryLinkageError(
                f"{label} escapes app Contents: {candidate} -> {unresolved}"
            )
        return None
    except (OSError, RuntimeError) as exc:
        raise MacOSBinaryLinkageError(f"could not resolve {label}: {candidate}: {exc}") from exc
    if not _is_relative_to(resolved, contents):
        raise MacOSBinaryLinkageError(
            f"{label} escapes app Contents: {candidate} -> {resolved}"
        )
    if require_directory:
        if not resolved.is_dir():
            raise MacOSBinaryLinkageError(f"{label} is not a directory: {resolved}")
    elif not resolved.is_file():
        raise MacOSBinaryLinkageError(f"{label} is not a regular file: {resolved}")
    return resolved


def _resolve_bundle_rpaths(
    values: Sequence[str],
    *,
    binary: Path,
    root_executable: Path,
    contents: Path,
) -> tuple[_BundleRPath, ...]:
    """Resolve one image's LC_RPATH values for one concrete process root."""

    resolved: list[_BundleRPath] = []
    for value in values:
        if value.startswith("/"):
            system_directory = _sealed_system_rpath(value)
            entry = _BundleRPath("system", system_directory)
            if entry not in resolved:
                resolved.append(entry)
            continue
        loader_suffix = _token_suffix(value, "@loader_path", label="LC_RPATH")
        executable_suffix = _token_suffix(
            value,
            "@executable_path",
            label="LC_RPATH",
        )
        if loader_suffix is not None:
            candidate = binary.parent / loader_suffix
        elif executable_suffix is not None:
            candidate = root_executable.parent / executable_suffix
        else:
            raise MacOSBinaryLinkageError(
                f"LC_RPATH must be relative to @loader_path or @executable_path: {value}"
            )
        directory = _canonical_bundle_candidate(
            candidate,
            contents=contents,
            label="LC_RPATH",
            require_directory=True,
        )
        if directory is None:
            raise MacOSBinaryLinkageError(f"LC_RPATH does not exist: {value}")
        entry = _BundleRPath("bundle", directory)
        if entry not in resolved:
            resolved.append(entry)
    return tuple(resolved)


def _validate_bundle_install_name(value: str | None, *, binary: Path) -> None:
    if value is None:
        return
    for token in ("@loader_path", "@executable_path", "@rpath"):
        suffix = _token_suffix(value, token, label="LC_ID_DYLIB")
        if suffix is None:
            continue
        if not suffix or any(part in (".", "..") for part in suffix.split("/")):
            raise MacOSBinaryLinkageError(
                f"unsafe LC_ID_DYLIB in {binary}: {value}"
            )
        return
    raise MacOSBinaryLinkageError(
        f"LC_ID_DYLIB must use an app-relative dyld token in {binary}: {value}"
    )


def _prepend_rpaths(
    local: Sequence[_BundleRPath],
    inherited: Sequence[_BundleRPath],
) -> tuple[_BundleRPath, ...]:
    """Push local dyld run paths ahead of the parent dependency chain."""

    ordered: list[_BundleRPath] = []
    for entry in (*local, *inherited):
        if entry not in ordered:
            ordered.append(entry)
    return tuple(ordered)


def _require_bundle_macho_target(
    target: Path,
    *,
    metadata: dict[Path, _BundleBinaryMetadata],
    dependency: str,
) -> Path:
    if target not in metadata:
        raise MacOSBinaryLinkageError(
            f"in-bundle dependency is not a recognized Mach-O: {dependency} -> {target}"
        )
    return target


def _resolve_required_bundle_dependency(
    candidate: Path,
    *,
    contents: Path,
    metadata: dict[Path, _BundleBinaryMetadata],
    dependency: str,
) -> Path:
    resolved = _canonical_bundle_candidate(
        candidate,
        contents=contents,
        label=f"Mach-O dependency {dependency}",
        require_directory=False,
    )
    if resolved is None:
        raise MacOSBinaryLinkageError(
            f"could not resolve in-bundle Mach-O dependency {dependency}"
        )
    return _require_bundle_macho_target(
        resolved,
        metadata=metadata,
        dependency=dependency,
    )


def _resolve_bundle_dependency(
    dependency: str,
    *,
    binary: Path,
    root_executable: Path,
    contents: Path,
    rpaths: Sequence[_BundleRPath],
    metadata: dict[Path, _BundleBinaryMetadata],
) -> Path | None:
    """Resolve a dependency using the exact process root and dyld run-path stack.

    ``None`` represents a sealed macOS dependency.  A bundle target is returned
    only after it is known to be one of the regular Mach-O files enumerated from
    this app's Contents directory.
    """

    if dependency.startswith("/"):
        _sealed_system_path(
            dependency,
            label="Mach-O dependency",
            allow_root=False,
        )
        return None
    loader_suffix = _token_suffix(
        dependency,
        "@loader_path",
        label="Mach-O dependency",
    )
    executable_suffix = _token_suffix(
        dependency,
        "@executable_path",
        label="Mach-O dependency",
    )
    rpath_suffix = _token_suffix(
        dependency,
        "@rpath",
        label="Mach-O dependency",
    )
    if loader_suffix is not None:
        return _resolve_required_bundle_dependency(
            binary.parent / loader_suffix,
            contents=contents,
            metadata=metadata,
            dependency=dependency,
        )
    if executable_suffix is not None:
        return _resolve_required_bundle_dependency(
            root_executable.parent / executable_suffix,
            contents=contents,
            metadata=metadata,
            dependency=dependency,
        )
    if rpath_suffix is None:
        raise MacOSBinaryLinkageError(
            f"non-system Mach-O dependency is external or unsupported in {binary}: {dependency}"
        )
    if not rpath_suffix or any(part in (".", "..") for part in rpath_suffix.split("/")):
        raise MacOSBinaryLinkageError(
            f"Mach-O @rpath dependency contains an unsafe path: {dependency}"
        )
    if not rpaths:
        raise MacOSBinaryLinkageError(
            f"could not resolve {dependency} for {binary}: no safe LC_RPATH"
        )
    for rpath in rpaths:
        if rpath.kind == "system":
            # The run-path directory itself was strictly validated as an existing
            # sealed macOS directory.  Individual dylibs can reside in dyld's
            # shared cache and therefore are not reliably materialized as files.
            return None
        resolved = _canonical_bundle_candidate(
            rpath.path / rpath_suffix,
            contents=contents,
            label=f"Mach-O dependency {dependency}",
            require_directory=False,
        )
        if resolved is None:
            continue
        return _require_bundle_macho_target(
            resolved,
            metadata=metadata,
            dependency=dependency,
        )
    raise MacOSBinaryLinkageError(
        f"could not resolve in-bundle Mach-O dependency {dependency} for {binary}"
    )


def _bundle_entrypoints(
    machos: Sequence[Path],
    *,
    contents: Path,
) -> tuple[Path, ...]:
    """Return explicit process roots whose dyld context is independently valid."""

    macos_root = contents / "MacOS"
    resources_bin_root = contents / "Resources" / "bin"
    roots: set[Path] = set()
    for binary in machos:
        if _is_relative_to(binary, macos_root) or binary.parent == resources_bin_root:
            roots.add(binary)
    return tuple(sorted(roots))


def _canonical_python_runtime_executable(contents: Path) -> Path:
    """Return the one bundled interpreter that defines Python plugin context."""

    return contents / CANONICAL_PYTHON_RUNTIME / CANONICAL_PYTHON_EXECUTABLE


def _python_dynamic_plugin_roots(
    machos: Sequence[Path],
    *,
    contents: Path,
) -> tuple[Path, ...]:
    """Return ordinary dynamically loaded Python extension modules.

    Python imports extension modules with an already-running interpreter, so
    they cannot be required to appear in the interpreter's static ``otool -L``
    closure.  This deliberately narrow exception is restricted to canonical
    ``.so`` modules in the copied CPython 3.12 runtime.  Their dependencies are
    still resolved with the interpreter's executable and run-path context.
    Support ``.dylib`` files remain ordinary dependencies and must be reached
    by one of those closures.
    """

    runtime = contents / CANONICAL_PYTHON_RUNTIME
    library = runtime / CANONICAL_PYTHON_LIBRARY
    dynamic_load = library / "lib-dynload"
    site_packages = library / "site-packages"
    roots: set[Path] = set()
    for binary in machos:
        if binary.suffix != ".so":
            continue
        if _is_relative_to(binary, dynamic_load) or _is_relative_to(
            binary,
            site_packages,
        ):
            roots.add(binary)
    return tuple(sorted(roots))


def _bundle_binary_metadata(
    binary: Path,
    *,
    runner: Runner,
) -> _BundleBinaryMetadata:
    dependencies = list(
        parse_otool_libraries(_run_otool(binary, ["-L"], runner=runner))
    )
    install_name = parse_otool_install_name(
        _run_otool(binary, ["-D"], runner=runner)
    )
    _validate_bundle_install_name(install_name, binary=binary)
    if install_name is not None:
        occurrences = dependencies.count(install_name)
        if occurrences != 1:
            raise MacOSBinaryLinkageError(
                f"LC_ID_DYLIB is not represented exactly once by otool -L in {binary}: "
                f"{install_name}"
            )
        dependencies.remove(install_name)
    commands = parse_otool_dyld_load_commands(
        _run_otool(binary, ["-l"], runner=runner)
    )
    _validate_dyld_load_commands(commands)
    # Absolute install names have no root-context ambiguity.  Validate them
    # even for a later-unreachable image so an external dylib is never hidden by
    # a reachability diagnostic.
    for dependency in dependencies:
        if dependency.startswith("/"):
            _sealed_system_path(
                dependency,
                label="Mach-O dependency",
                allow_root=False,
            )
    return _BundleBinaryMetadata(
        path=binary,
        dependencies=tuple(dependencies),
        rpath_values=commands.rpaths,
    )


class _EntrypointDependencyVerifier:
    """Resolve one static dependency closure in dyld's process-root context."""

    def __init__(
        self,
        *,
        root_executable: Path,
        contents: Path,
        metadata: dict[Path, _BundleBinaryMetadata],
    ) -> None:
        self.root_executable = root_executable
        self.contents = contents
        self.metadata = metadata
        self.reachable: set[Path] = set()
        self._seen: set[tuple[Path, tuple[_BundleRPath, ...]]] = set()

    def verify(self) -> set[Path]:
        self._visit(self.root_executable, ())
        return self.reachable

    def verify_python_plugin(self, plugin: Path) -> set[Path]:
        """Verify a Python extension under the interpreter's dyld context.

        The module is loaded dynamically by the already-running CPython
        process.  It is *not* treated as an executable: ``@executable_path``
        remains the canonical ``bin/python3.12`` location, while the plugin's
        own ``@loader_path`` and LC_RPATH values are applied by ``_visit``.
        """

        interpreter = self.metadata[self.root_executable]
        inherited_rpaths = _resolve_bundle_rpaths(
            interpreter.rpath_values,
            binary=self.root_executable,
            root_executable=self.root_executable,
            contents=self.contents,
        )
        self.reachable.add(self.root_executable)
        self._visit(plugin, inherited_rpaths)
        return self.reachable

    def _visit(
        self,
        binary: Path,
        inherited_rpaths: tuple[_BundleRPath, ...],
    ) -> None:
        info = self.metadata[binary]
        local_rpaths = _resolve_bundle_rpaths(
            info.rpath_values,
            binary=binary,
            root_executable=self.root_executable,
            contents=self.contents,
        )
        effective_rpaths = _prepend_rpaths(local_rpaths, inherited_rpaths)
        state = (binary, effective_rpaths)
        if state in self._seen:
            return
        self._seen.add(state)
        self.reachable.add(binary)
        for dependency in info.dependencies:
            target = _resolve_bundle_dependency(
                dependency,
                binary=binary,
                root_executable=self.root_executable,
                contents=self.contents,
                rpaths=effective_rpaths,
                metadata=self.metadata,
            )
            if target is not None:
                self._visit(target, effective_rpaths)


def verify_app_bundle_macos_linkage(
    app: Path,
    *,
    executable_name: str = "TotalSegmentatorWrapperForMac",
    runner: Runner = subprocess.run,
) -> tuple[Path, ...]:
    """Verify all Mach-O dependencies in a relocatable, self-contained app."""

    if not app.is_dir() or app.is_symlink():
        raise MacOSBinaryLinkageError(
            f"app must be a regular non-symlink directory: {app}"
        )
    contents_path = app / "Contents"
    if not contents_path.is_dir() or contents_path.is_symlink():
        raise MacOSBinaryLinkageError(f"app Contents directory is missing or unsafe: {contents_path}")
    try:
        contents = contents_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MacOSBinaryLinkageError(f"could not resolve app Contents: {exc}") from exc
    executable = contents / "MacOS" / executable_name
    machos = _collect_app_bundle_machos(contents)
    if executable not in machos:
        raise MacOSBinaryLinkageError(
            f"app main executable is missing or is not a regular Mach-O: {executable}"
        )
    python_executable = _canonical_python_runtime_executable(contents)
    python_plugins = _python_dynamic_plugin_roots(machos, contents=contents)
    if python_plugins and python_executable not in machos:
        raise MacOSBinaryLinkageError(
            "canonical bundled Python executable is missing or is not a regular Mach-O "
            f"for dynamic Python modules: {python_executable}"
        )
    entrypoints = set(_bundle_entrypoints(machos, contents=contents))
    entrypoints.add(executable)
    if python_executable in machos:
        entrypoints.add(python_executable)
    errors: list[str] = []
    metadata: dict[Path, _BundleBinaryMetadata] = {}
    for binary in machos:
        try:
            metadata[binary] = _bundle_binary_metadata(binary, runner=runner)
        except (MacOSBinaryLinkageError, OSError, RuntimeError) as exc:
            errors.append(f"{binary}: {exc}")
    reachable: set[Path] = set()
    if not errors:
        for entrypoint in sorted(entrypoints):
            try:
                verifier = _EntrypointDependencyVerifier(
                    root_executable=entrypoint,
                    contents=contents,
                    metadata=metadata,
                )
                reachable.update(verifier.verify())
            except (MacOSBinaryLinkageError, OSError, RuntimeError, RecursionError) as exc:
                errors.append(f"{entrypoint}: {exc}")
        if not errors and python_plugins:
            for plugin in python_plugins:
                try:
                    verifier = _EntrypointDependencyVerifier(
                        root_executable=python_executable,
                        contents=contents,
                        metadata=metadata,
                    )
                    reachable.update(verifier.verify_python_plugin(plugin))
                except (MacOSBinaryLinkageError, OSError, RuntimeError, RecursionError) as exc:
                    errors.append(
                        f"Python dynamic plugin {plugin} (root {python_executable}): {exc}"
                    )
        unreachable = sorted(set(machos) - reachable)
        if unreachable:
            errors.append(
                "regular Mach-O file(s) are not reachable from a legitimate entrypoint: "
                + ", ".join(str(path) for path in unreachable)
            )
    if errors:
        raise MacOSBinaryLinkageError(
            "app bundle linkage verification failed:\n" + "\n".join(errors)
        )
    return machos


def _safe_wheel_member_path(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name or any(ord(char) < 32 for char in name):
        raise MacOSBinaryLinkageError(f"unsafe wheel member path: {name!r}")
    path = PurePosixPath(name.rstrip("/"))
    if (
        not path.parts
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise MacOSBinaryLinkageError(f"unsafe wheel member path: {name!r}")
    return path


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_IFMT(mode) == stat.S_IFLNK


def _owned_temporary_directory(prefix: str) -> tempfile.TemporaryDirectory[str]:
    temporary = tempfile.TemporaryDirectory(prefix=prefix)
    path = Path(temporary.name)
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        temporary.cleanup()
        raise MacOSBinaryLinkageError(
            f"wheel extraction directory is not owner-controlled: {path}"
        )
    return temporary


def _extract_wheel_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
) -> None:
    if info.file_size < 0 or info.file_size > MAX_WHEEL_NATIVE_MEMBER_BYTES:
        raise MacOSBinaryLinkageError(
            f"wheel native member exceeds size limit: {info.filename}"
        )
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o700,
    )
    received = 0
    try:
        with archive.open(info) as source, os.fdopen(descriptor, "wb") as target:
            while chunk := source.read(1024 * 1024):
                received += len(chunk)
                if received > info.file_size:
                    raise MacOSBinaryLinkageError(
                        f"wheel member exceeds declared size: {info.filename}"
                    )
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise
    if received != info.file_size:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise MacOSBinaryLinkageError(
            f"wheel member ended early: {info.filename}"
        )


def verify_wheel_system_macos_linkage(
    wheel: Path,
    *,
    runner: Runner = subprocess.run,
) -> tuple[str, ...]:
    """Extract and verify every Mach-O member in a wheel without trusting paths.

    Member failures are accumulated so a release check reports every offending
    native extension rather than fixing the first one and missing another.
    """

    if not wheel.is_file() or wheel.is_symlink():
        raise MacOSBinaryLinkageError(
            f"wheel must be a regular non-symlink file: {wheel}"
        )
    try:
        wheel_metadata = wheel.lstat()
    except OSError as exc:
        raise MacOSBinaryLinkageError(f"could not inspect wheel: {exc}") from exc
    if not stat.S_ISREG(wheel_metadata.st_mode):
        raise MacOSBinaryLinkageError(f"wheel must be a regular file: {wheel}")

    try:
        archive_context = zipfile.ZipFile(wheel)
    except (OSError, zipfile.BadZipFile) as exc:
        raise MacOSBinaryLinkageError(f"invalid wheel ZIP: {wheel}: {exc}") from exc
    with archive_context as archive:
        infos = archive.infolist()
        seen: set[PurePosixPath] = set()
        native_infos: list[zipfile.ZipInfo] = []
        total_native_bytes = 0
        for info in infos:
            relative = _safe_wheel_member_path(info.filename)
            if relative in seen:
                raise MacOSBinaryLinkageError(
                    f"wheel contains a duplicate member path: {info.filename}"
                )
            seen.add(relative)
            if _zip_member_is_symlink(info):
                raise MacOSBinaryLinkageError(
                    f"wheel must not contain symlink members: {info.filename}"
                )
            if info.is_dir():
                continue
            try:
                with archive.open(info) as source:
                    prefix = source.read(4)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise MacOSBinaryLinkageError(
                    f"could not inspect wheel member {info.filename}: {exc}"
                ) from exc
            if prefix not in MACHO_MAGICS:
                continue
            if info.file_size > MAX_WHEEL_NATIVE_MEMBER_BYTES:
                raise MacOSBinaryLinkageError(
                    f"wheel native member exceeds size limit: {info.filename}"
                )
            total_native_bytes += info.file_size
            if total_native_bytes > MAX_WHEEL_TOTAL_NATIVE_BYTES:
                raise MacOSBinaryLinkageError(
                    f"wheel native members exceed total size limit: {wheel}"
                )
            native_infos.append(info)

        labels: list[str] = []
        failures: list[str] = []
        with _owned_temporary_directory("totalsegmentator-wrapper-wheel-linkage.") as temporary:
            staging = Path(temporary)
            for index, info in enumerate(native_infos):
                label = f"{wheel}!/{info.filename}"
                destination = staging / f"member-{index:06d}.macho"
                try:
                    _extract_wheel_member(archive, info, destination)
                    verify_system_macos_linkage(destination, runner=runner)
                except (MacOSBinaryLinkageError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    failures.append(f"{label}: {exc}")
                    continue
                labels.append(label)
        if failures:
            raise MacOSBinaryLinkageError(
                "wheel linkage verification failed:\n" + "\n".join(failures)
            )
        return tuple(labels)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reject non-system dylib dependencies and LC_RPATH in macOS binaries."
    )
    parser.add_argument("--path", type=Path, action="append", default=[])
    parser.add_argument("--wheel", type=Path, action="append", default=[])
    parser.add_argument("--app", type=Path, action="append", default=[])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.path and not args.wheel and not args.app:
        raise MacOSBinaryLinkageError(
            "at least one of --path, --wheel, or --app is required"
        )
    for path in args.path:
        candidate = path.expanduser()
        verify_system_macos_linkage(candidate)
        print(f"PASS {candidate}: system-only Mach-O dependencies, no LC_RPATH")
    for wheel in args.wheel:
        candidate = wheel.expanduser()
        members = verify_wheel_system_macos_linkage(candidate)
        print(
            f"PASS {candidate}: {len(members)} Mach-O wheel member(s) have system-only dependencies and no LC_RPATH"
        )
    for app in args.app:
        candidate = app.expanduser()
        members = verify_app_bundle_macos_linkage(candidate)
        print(
            f"PASS {candidate}: {len(members)} in-bundle Mach-O file(s) have safe resolved dependencies"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
