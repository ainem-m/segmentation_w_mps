# dcm2niix: macOS 14 / arm64 source build

The release source path is deliberately pinned to the official dcm2niix
release archive, not to the Homebrew executable installed on the build Mac.
The builder is [scripts/build_dcm2niix_macos14_arm64.sh](../scripts/build_dcm2niix_macos14_arm64.sh).

| Field | Fixed value |
| --- | --- |
| Official upstream release tag | `v1.0.20250506` |
| Official archive | `https://github.com/rordenlab/dcm2niix/archive/refs/tags/v1.0.20250506.tar.gz` |
| Archive SHA-256 | `1b24658678b6c24141e58760dbea9fe2786ffdd736bcc37a36d9cdabc731bafa` |
| Exact upstream `license.txt` SHA-256 | `a423e1c074ff39d9c22843489dd81bbaf42d4fa243fd785f8e96ce084db2e503` |
| Expected source-root directory | `dcm2niix-1.0.20250506` |
| Expected CLI version from that source | `v1.0.20250505` |
| Release target | arm64, macOS 14.0 or later |

The current legacy app input was supplied by Homebrew formula `1.0.20250506`,
whose formula pins the archive and SHA-256 above. Its `dcm2niix -h` output says
`v1.0.20250505`; this is the version string embedded by the upstream source
release, not an upgrade or a substitute source version. The builder checks
both values, so changing either requires an explicit compatibility review.

Running the builder is intentionally separate from app, wheel, DMG, signing,
and notarization steps. It uses the repository's safe source-archive fetcher:
partial archives and their identity sidecar are retained in
`build/source-cache/`, and a subsequent invocation resumes through HTTP Range
when the server supports it. GitHub did not provide a content length to a
HEAD-only check on 2026-08-01; no archive download was performed while adding
this path. The fetcher caps archives at 1 GiB and verifies the fixed SHA-256
before extraction.

The build sets `MACOSX_DEPLOYMENT_TARGET=14.0`,
`CMAKE_OSX_DEPLOYMENT_TARGET=14.0`, and
`CMAKE_OSX_ARCHITECTURES=arm64`. It uses a release build, a fixed
`SOURCE_DATE_EPOCH=1746489600` (an externally supplied different value fails
before fetching or building), path-remapping compiler flags, an isolated CMake package
search environment that ignores `/opt/homebrew` and `/usr/local`, and no CMake
rpaths. Afterwards it rejects a binary unless
the Mach-O verifier finds an arm64 slice whose minimum OS is at most 14.0. A
second verifier rejects all non-system dylibs and every `LC_RPATH`; in
particular, a `/opt/homebrew`, `/usr/local`, `@rpath`, or `@loader_path`
runtime dependency cannot enter a release artifact.

The upstream `license.txt` must have SHA-256
`a423e1c074ff39d9c22843489dd81bbaf42d4fa243fd785f8e96ce084db2e503`
and be byte-identical to
`resources/third_party/licenses/dcm2niix-license.txt`. The builder stops on a
mismatch rather than guessing how to update the app's redistribution notice.
The pinned notice is the upstream 2014–2021 three-clause BSD text; it must not
be replaced with a newer-looking notice from a different source tag.

After validating the cached archive's SHA-256, the builder extracts it into a
new owner-controlled source staging directory for every invocation. It does
not trust or reuse a previously extracted source tree. It also uses a new
owner-controlled CMake staging directory for every invocation and does not
reuse a CMake cache. A complete output is published as
`artifacts/<binary-sha256>/` containing only the executable, its license, and
a v2 provenance receipt. Only after all hashes, the Mach-O deployment target,
the system-only linkage, and the CLI version have been checked is the artifact
directory atomically renamed into place. Finally an atomically written,
non-symlink `current-artifact.json` points to that exact relative artifact
directory. A crash therefore leaves either the previously valid pointer or an
unreferenced complete staging/artifact directory; it cannot pair a new binary
with an old or missing receipt. Existing pointer/artifact data is strictly
verified and then skipped, or rejected. The app integration must resolve only
this pointer and then run the complete app-level Mach-O and license checks.
