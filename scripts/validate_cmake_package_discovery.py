#!/usr/bin/env python3
"""Reject package-discovery paths in a CMake cache without rejecting CMake itself."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


ALLOWED_TOOLCHAIN_CACHE_KEYS = {
    "CMAKE_COMMAND",
    "CMAKE_CPACK_COMMAND",
    "CMAKE_CTEST_COMMAND",
    "CMAKE_EDIT_COMMAND",
    "CMAKE_IGNORE_PREFIX_PATH",
    "CMAKE_INSTALL_PREFIX",
    "CMAKE_MAKE_PROGRAM",
    "CMAKE_ROOT",
    "CMAKE_SYSTEM_IGNORE_PATH",
}
FORBIDDEN_PREFIXES = ("/opt/homebrew", "/usr/local")


def find_forbidden_package_discovery_paths(cache_path: Path) -> list[str]:
    """Return cache values that can influence dependency discovery.

    The allowlist is limited to CMake's own commands and its conventional
    install prefix.  Those values are metadata about the selected build tool,
    not inputs used to find GDCM or other link dependencies.
    """
    bad: list[str] = []
    for line in cache_path.read_text(encoding="utf-8", errors="strict").splitlines():
        if not line or line.startswith(("#", "//")) or "=" not in line:
            continue
        key_type, value = line.split("=", 1)
        key = key_type.split(":", 1)[0]
        if key in ALLOWED_TOOLCHAIN_CACHE_KEYS:
            continue
        if any(prefix in value for prefix in FORBIDDEN_PREFIXES):
            bad.append(f"{key}={value}")
    return bad


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reject Homebrew or /usr/local package-discovery paths in CMake cache."
    )
    parser.add_argument("cache", type=Path)
    args = parser.parse_args(argv)
    bad = find_forbidden_package_discovery_paths(args.cache)
    if bad:
        raise SystemExit("forbidden package-discovery path in CMake cache: " + ", ".join(bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
