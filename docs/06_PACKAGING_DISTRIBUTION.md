# 06 Packaging and Distribution

## Packaging goal

The first public preview should minimize user setup while avoiding overengineering.

Distribution packaging is now a required project track, not a deferred
nice-to-have. The package should be installable before any `.app` or DMG work.

Preferred order:

```text
1. CLI + scripts for internal proof
2. Lightweight Mac Preview app calling local backend
3. Notarized DMG only after benchmark proof
4. Full bundled runtime only if public demand justifies it
```

## Distribution phases

### Phase A: Script-only proof

```text
- GitHub repo
- README
- scripts
- user installs dependencies manually or via setup script
```

Best for rapid validation.

Status:

```text
completed for engineering proof
```

### Phase B: Managed Python environment

```text
- app or CLI creates its own venv
- installs pinned dependencies
- downloads model weights on demand
```

Best balance for preview.

Status:

```text
current target
```

The repository now has a `pyproject.toml` package definition with console
scripts:

```text
totalsegmentator-wrapper-mac
```

Recommended local install during preview packaging:

```bash
python -m pip install -e '.[dicom,mps,dentalseg,toothseg]'
```

Recommended local wheel build on Mac:

```bash
scripts/build_mac_wheel.sh
```

The script builds `native/dicom_normalizer`, stages
`totalsegmentator-wrapper-dicom-normalizer` into `totalsegmentator_wrapper_mac/bin/`, bundles its
GDCM runtime under `totalsegmentator_wrapper_mac/bin/lib/`, and writes the Python
wheel to `dist/`. Mach-O load commands use `@loader_path`; no Homebrew install is
required on the destination Mac. The runtime binary lookup order is:

The current thin-app alpha wheel is intentionally Python 3.12 / macOS arm64
specific, for example:

```text
totalsegmentator_wrapper_mac-0.2.1-cp312-cp312-macosx_11_0_arm64.whl
```

This matches the app launcher’s Python 3.12 gate and avoids pretending the
bundled native helper is a pure Python artifact.

The runtime binary lookup order is:

```text
explicit --binary
TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER
packaged totalsegmentator_wrapper_mac/bin/totalsegmentator-wrapper-dicom-normalizer
repo build/dicom_normalizer/totalsegmentator-wrapper-dicom-normalizer
PATH
```

Recommended thin `.app` build on Mac:

```bash
scripts/build_mac_app.sh
```

This creates:

```text
dist/TotalSegmentator Wrapper for Mac.app/
  Contents/MacOS/TotalSegmentatorWrapperForMac
  Contents/Resources/wheels/totalsegmentator_wrapper_mac-*.whl
  Contents/Resources/constraints/macos-arm64-py312.txt
  Contents/Resources/bin/totalsegmentator-wrapper-dicom-normalizer
  Contents/Resources/bin/lib/*.dylib
  Contents/Resources/python/cpython-3.12/        optional
  Contents/Resources/setup_manifest.json
```

The native DICOM helper, GDCM, JPEG codecs, OpenJPEG, CharLS, json-c, and
OpenSSL are signed from the inner dylibs outward and included in the app's
notarization boundary. Their license texts are copied into
`Contents/Resources/licenses/`.

`Contents/MacOS/TotalSegmentatorWrapperForMac` is now a SwiftUI executable. It owns both the
Setup window and the main workflow, while invoking the existing Python backend
through argv-list subprocess calls. It is intentionally not a shell-script
entrypoint.

Local development `.app` builds are ad-hoc signed by default. Public release
builds use Developer ID signing and notarized DMGs through
`scripts/notarize_mac_dmg.sh`.

Recommended DMG build for test-account installation:

```bash
scripts/build_mac_dmg.sh
```

This creates:

```text
dist/TotalSegmentator Wrapper for Mac-0.2.1-20260728-surface-preview-arm64.dmg
```

The DMG contains the app, an `/Applications` symlink, and a short README. Users
without admin rights can copy the app to `~/Applications` instead. It also
contains `Verify Test Account Install.command` for internal alpha validation
after Setup has completed in a separate test account.

By default, `scripts/build_mac_app.sh` builds the test-account friendly app: it
discovers the build Python's `sys.base_prefix` and bundles that Python 3.12
runtime. To force a specific runtime root, pass a directory containing
`bin/python3.12`:

```bash
TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_PYTHON_RUNTIME_DIR=/path/to/cpython-3.12-runtime \
  scripts/build_mac_app.sh
```

The script copies that runtime to `Contents/Resources/python/cpython-3.12/`
and writes `python_runtime.strategy=bundled_python312` with a relative
`python_runtime.python_executable` in `setup_manifest.json`.

`Contents/MacOS/TotalSegmentatorWrapperForMac` is built from
`native/macos/TotalSegmentatorWrapperForMac/` and requires full Xcode on the build machine.
Command Line Tools alone are not accepted because SwiftUI builds require a
matching Swift compiler and macOS SDK. End users do not need Xcode.

Bundled Python files are made read-only before signing while runtime
directories remain traversable/copyable, and Python bytecode cache is
redirected to App Support so first setup does not mutate sealed resources inside
the copied app bundle.

The older external-Python alpha shape is now opt-in only:

```bash
TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_EXTERNAL_PYTHON_RUNTIME=1 scripts/build_mac_app.sh
```

Recommended public install shape:

```bash
pip install totalsegmentator-wrapper-mac
pip install 'totalsegmentator-wrapper-mac[dicom]'
pip install 'totalsegmentator-wrapper-mac[mps]'
```

### Phase C: Embedded Python/PyTorch app

```text
- .app contains arm64 Python runtime
- PyTorch included
- TotalSegmentator included
- weights downloaded on first run
```

Best user experience, larger distribution.

### Current Thin App Setup Policy

The first app distribution uses a thin launcher plus one-click setup:

```text
- no sudo
- no Homebrew install
- no writes to /usr/local, /opt/homebrew, /Library, or /System
- no global DICOM scan
- only user-selected input folders/files are read
- runtime state lives under ~/Library/Application Support/TotalSegmentatorWrapperMac/
```

Setup state is written to:

```text
~/Library/Application Support/TotalSegmentatorWrapperMac/setup_state.json
```

Failure reasons are intentionally coarse and user-facing:

```text
python312_missing
python_version_unsupported
constraints_missing
wheel_missing
needs_network
runtime_install_failed
mps_unavailable
normalizer_missing
setup_exception
```

Normal `.app` launch shows a Japanese SwiftUI setup window when setup has not
completed. It displays the current step, an indeterminate progress bar, elapsed
time, and a live tail of `logs/launcher.log` so long dependency installs are not
silent.
It also includes a `3Dサンプルを開く` button that opens the bundled offline
Sample 1 surface-preview HTML in the default browser. After setup, the SwiftUI
main window defaults to a two-choice flow around the bundled Sample 1 NIfTI and
writes default runs under App Support. Sample 1 is Slicer SampleData-derived
NIfTI plus precomputed preview artifacts for non-clinical UI inspection; it is
not DICOM, a diagnostic asset, or an accuracy-evaluation asset. The app bundle includes
`sample1/THIRD_PARTY_NOTICES.txt` with the Slicer unrestricted-use source note,
source SHA256, TotalSegmentator Apache-2.0 attribution, and non-clinical
limitation.
The alpha launcher does not silently use the host `python3` for the runtime
venv. It requires an explicit Python 3.12 executable from one of:

```text
TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312
bundled setup_manifest.json python_runtime.python_executable
setup_manifest.json python_runtime.python_executable
```

If the configured executable is missing, setup state records
`python312_missing`. If it is Python 3.14 or any non-3.12 runtime, setup state
records `python_version_unsupported` before the private venv is created.

Launcher startup also checks whether the setup state matches the current app
bundle fingerprint. If the bundled wheel changed but the dependency set did not,
the launcher performs an offline `pip install --force-reinstall --no-deps` into
the existing App Support venv before opening the UI. If the dependency set or
constraints changed, it shows Setup again and waits for user action before any
network install.

Update checking runs only when the user presses `更新を確認`. When
`setup_manifest.json update_manifest_url` is configured, SwiftUI fetches that
HTTPS static manifest. If a newer build is available, the UI asks before
downloading the notarized DMG, verifies the manifest SHA256, mounts it, validates
the replacement app with Gatekeeper, replaces the current app when the install
location is writable, and reopens the app. Update links must use the same origin
as the manifest unless explicitly allowlisted in the local app manifest via
`TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_ALLOWED_HOSTS` at build time. The request does not include
DICOM/CT paths, logs, processing output, or user identifiers. Startup and Setup
do not contact the update endpoint.

Cloudflare distribution prep uses Pages for the public download/support page
and R2 for the DMG plus update manifest. The DMG is larger than the Cloudflare
Pages single-asset limit, and the updater should keep the manifest and DMG on
the same R2 custom domain to avoid extra update host allowlisting. See
`docs/35_CLOUDFLARE_DISTRIBUTION.md`.

Headless validation can use:

```bash
scripts/verify_zero_env_mac_app.sh
scripts/verify_zero_env_mac_dmg.sh
```

Those scripts create a temporary clean `HOME`, clear the inherited environment,
launch the app headlessly, require `setup_state.json status=success`, check that
MPS doctor recorded `actual_device=mps`, verify that the DICOM normalizer came
from the app bundle, require pip and Python bytecode caches to stay under App
Support, and re-run `codesign --verify` after setup. They also collect the same
evidence JSON used by the manual test-account gate. The DMG variant mounts the
DMG and copies the app into clean `~/Applications` before setup.

For offline-only validation, the expected alpha behavior is to create the
private venv, install the bundled wheel without dependencies, write
`setup_state.json`, and stop with `needs_network`.

When network setup is allowed, dependency resolution must use the bundled
constraints file:

```text
Contents/Resources/constraints/macos-arm64-py312.txt
```

The first alpha pins the tested MPS stack there instead of relying on broad
`torch>=2.3` resolution.

### Phase D: Native/ONNX app

Deferred.

## Estimated size considerations

Rules:

```text
- Do not bundle model weights in the initial app.
- Download weights on demand.
- Do not bundle Slicer.
- Do not bundle every TotalSegmentator task.
- Apple Silicon only for first app.
```

Expected size classes:

```text
Script repo: small
Managed environment: user downloads hundreds of MB during setup
Thin app + bundled Python 3.12 only: about 55 MB in the current alpha build
Embedded Python/PyTorch app: hundreds of MB to 1GB+ possible
Model weights: separate, task-dependent, can add hundreds of MB
```

The preview can tolerate a large download if the public story is strong, but do not make the first GitHub artifact huge.

## Version pinning policy

Pin only after successful smoke tests.

Candidate dependency categories:

```text
- Python 3.12 for the distributed thin app alpha
- PyTorch version that passes ConvTranspose3D MPS smoke test
- TotalSegmentator version tested with target PyTorch
- nibabel/SimpleITK only if needed for metadata
```

Current package dependency split:

```text
base:
  nibabel
  numpy
  scikit-image

dicom extra:
  pydicom

mps extra:
  torch
  totalsegmentator==2.14.0

dev extra:
  build
```

Keep `pydicom` optional because the package does not normalize DICOM. It only
metadata-audits DICOM folders before users convert clean series externally with
`dcm2niix`.

The C++ normalizer binary may be bundled in the Mac wheel. DCMTK/GDCM are not
bundled in the first package; they are detected as optional external tools and
reported by `doctor`/audit JSON.

Do not assume that every user’s existing Python works.
The current thin app rejects non-3.12 runtimes explicitly and records the
resolved executable/version in `setup_state.json`.

## Model download policy

Use on-demand download.

```text
On first task run:
  check if required weights exist
  if missing, download through TotalSegmentator’s normal mechanism
  record weight download status in log
```

Do not redistribute weights unless license and storage implications are confirmed.

## Codesigning / notarization

For public Mac Preview:

```text
- sign the app with Developer ID and hardened runtime
- notarize the DMG before distribution
- validate the stapled DMG and mounted app with Gatekeeper
```

Avoid App Store for MVP.

## App support directory

Use:

```text
~/Library/Application Support/TotalSegmentatorWrapperMac/
  env/
  models/
  cases/
  logs/
  cache/
    pip/
    pycache/
  setup_state.json
```

Do not write into Slicer directories.

## Update policy

The preview should show its own version and backend versions:

```text
TotalSegmentator Wrapper for Mac: 0.2.1
Python: x.y.z
Torch: x.y.z
TotalSegmentator: x.y.z
macOS: x.y.z
DICOM normalizer: x.y.z or missing
optional DICOM transcoders: gdcmconv/dcmdjpeg/dcmconv availability
```

These should be saved into every benchmark log.
