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

Prepare the three source-built native artifacts explicitly before wheel or app
packaging:

```bash
scripts/build_gdcm_macos14_arm64.sh
scripts/build_dicom_normalizer_mac.sh
scripts/build_dcm2niix_macos14_arm64.sh
```

The first command publishes the verified GDCM 3.2.7 source artifact for arm64
and a macOS 14 deployment target. The second command consumes that already
verified GDCM artifact to build the normalizer; it will not download or build
GDCM implicitly. GDCM and the enabled internal codecs are statically linked;
no `bin/lib` dylib directory or `gdcmconv` is distributed. The third command
builds the pinned official dcm2niix `v1.0.20250506` source (embedded CLI version
`v1.0.20250505`). Each builder publishes an immutable, receipt-bearing
artifact. Wheel/app packaging verifies those prepared artifacts and never
downloads or builds them implicitly.

`scripts/build_mac_wheel.sh` stages the verified normalizer into
`totalsegmentator_wrapper_mac/bin/` and writes the Python wheel to `dist/`.
Every Mach-O must contain arm64, declare a minimum macOS no newer than 14, have
only macOS system-library dependencies, and contain no `LC_RPATH`. No Homebrew
install is required on the destination Mac.

The current preview wheel is intentionally Python 3.12 / macOS arm64
specific, for example:

```text
totalsegmentator_wrapper_mac-0.4.1-cp312-cp312-macosx_14_0_arm64.whl
```

This matches the app launcher’s Python 3.12 gate and avoids pretending the
bundled native helper is a pure Python artifact.

The runtime binary lookup order is:

```text
explicit --binary
TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_NORMALIZER
packaged totalsegmentator_wrapper_mac/bin/totalsegmentator-wrapper-dicom-normalizer
repo build/dicom_normalizer-macos14-arm64/totalsegmentator-wrapper-dicom-normalizer
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
  Contents/Resources/wheels/fpsample-1.0.2-cp312-cp312-macosx_13_0_arm64.whl
  Contents/Resources/wheels/acvl_utils-0.2.6-py3-none-any.whl
  Contents/Resources/constraints/macos-arm64-py312.txt
  Contents/Resources/constraints/macos-arm64-py312.requirements.lock
  Contents/Resources/constraints/macos-arm64-py312.lock.json
  Contents/Resources/bin/totalsegmentator-wrapper-dicom-normalizer
  Contents/Resources/bin/dcm2niix
  Contents/Resources/python/cpython-3.12/        optional
  Contents/Resources/totalseg_setup_weights_manifest.json
  Contents/Resources/setup_manifest.json
  Contents/Resources/licenses/dicom-normalizer-build-provenance.json
  Contents/Resources/licenses/gdcm-build-provenance.json
  Contents/Resources/licenses/dcm2niix-build-provenance.json
  Contents/Resources/licenses/dcm2niix-current-artifact.json
```

The managed environment install uses `--only-binary=:all:`. `fpsample 1.0.2`
is therefore bundled as a signed arm64 wheel, and the sdist-only
`acvl-utils 0.2.6` is built once during release packaging as a pinned
pure-Python wheel. A missing wheel fails setup instead of compiling code on a
user Mac. The acvl-utils build input is the official PyPI sdist with a pinned
SHA-256; packaging rejects native members and verifies its Apache-2.0 metadata,
license text, and wheel RECORD integrity.

When Developer ID/notarized packaging generates the third-party license
inventory, it installs the same canonical `--require-hashes` requirements lock
that Setup uses. `fpsample 1.0.2` and `acvl-utils 0.2.6` are deliberately
different: they are graph-resolution inputs and separately bundled local
overrides, so their two exact requirement blocks are removed from the published
install lock. The lock metadata records the complete graph in
`resolved_distribution_names`, the actually installed lock inventory in
`install_distribution_names`, and the exact excluded-override role/version,
input filename, and pre-sign resolver-input SHA-256 in
`excluded_bundled_overrides`.

At first setup and during the release inventory, the two manifest-bound local
component wheels install first with `--no-deps`; the canonical lock then
installs with both `--require-hashes` and `--no-deps`; the wrapper wheel also
installs with `--no-deps`; and `pip check` must pass afterward. This prevents
the lock install from resolving/replacing the two local overrides while still
checking the complete environment. The final `fpsample` bytes can change when
Developer ID signing updates its archive and RECORD. Therefore the final wheel
SHA-256 is bound only by `setup_manifest.json` and verified during packaging;
it is intentionally not copied into the pre-sign lock metadata. The manifest
records this as `third_party_licenses.inventory_mode=release_hashed_lock` and
`release_eligible=true`.

An ad-hoc development build has a deliberately separate, explicit route:
either a supplied development site-packages path or
`TOTALSEGMENTATOR_WRAPPER_MAC_ALLOW_DEVELOPMENT_LICENSE_INVENTORY=1` permits a
constraints-based inventory. Those routes are recorded as development-only and
`release_eligible=false`; they cannot be reused by a Developer ID/notarized
build. All inventory and setup pip subprocesses use the managed virtual
environment with `python -I`, `pip --isolated`, `PIP_CONFIG_FILE=/dev/null`,
and a scrubbed `PIP_*`/Python-path environment. This prevents host pip config,
user-site packages, or `PYTHONPATH` from silently changing the inventory or
release install graph.

The native DICOM helper contains statically linked pinned GDCM, IJG JPEG,
OpenJPEG, CharLS, Expat, zlib, and UUID components. json-c and OpenSSL are not
compiled into this static configuration. Exact upstream license files and a
source-path/SHA-256 inventory are copied into
`Contents/Resources/licenses/`. The normalizer and GDCM build receipts bind the
native source hash, fixed archive SHA-256, build options, static-library
hashes, and license-inventory hash. dcm2niix has a separate content-addressed
artifact pointer and source-build receipt.

The wrapper's own Apache-2.0 `LICENSE` and scope `NOTICE` are stored separately
at `Contents/Resources/LICENSE` and `Contents/Resources/NOTICE`. They do not
replace or absorb the third-party inventory. DentalSegmentator and ToothSeg
model notices are bundled even though their CC BY 4.0 checkpoints are downloaded
only on first use.

`Contents/MacOS/TotalSegmentatorWrapperForMac` is now a SwiftUI executable. It owns both the
Setup window and the main workflow, while invoking the existing Python backend
through argv-list subprocess calls. It is intentionally not a shell-script
entrypoint.

For 0.4.1 and later, the distributed app supports Apple Silicon Macs running
macOS 14 or later. The release build compiles the SwiftUI executable with a
macOS 14 deployment target and records the same requirement in both
`Info.plist` and `setup_manifest.json`; app, DMG, and notarized-release
verification reject a mismatch. The bundled `fpsample` wheel keeps its
`macosx_13_0` compatibility tag because that tag describes the wheel itself,
not the minimum OS version of the application bundle.

Local development `.app` builds are ad-hoc signed by default. Public release
builds use Developer ID signing and notarized DMGs through
`scripts/notarize_mac_dmg.sh`.
All app/DMG version values are derived from `[project].version` in
`pyproject.toml`; a conflicting environment override is rejected. Developer ID
builds also require a clean tracked and untracked worktree and record
`source_commit`, `source_tree_dirty=false`, the bundle identifier, and the Team
ID in `setup_manifest.json`. Ad-hoc builds may be dirty but record that state as
degraded provenance.

Developer ID builds for 0.4.1 and later embed only the canonical stable-v2 update endpoint
`https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable-v2/update.json`
and the nonempty `downloads.lacramy.com` host allowlist. They also require the
canonical bundle identifier `jp.chino.totalsegmentator.wrapper.mac`; an
override to another identifier is rejected. The packaged
`totalseg_setup_weights_manifest.json` is hashed into
`setup_weights_manifest_sha256`; it contains metadata only, not model assets.

The legacy `releases/stable/update.json` endpoint is permanently frozen and
read-only at its live 0.3.0 payload. The local 0.4.0 release record is
withdrawn and must never be re-uploaded or used as a download/update target.
Until a verified 0.4.1+ `stable-v2` manifest is published, `stable-v2` returns
HTTP 404 and 0.4.1 is installed only from a manually downloaded DMG. Only a
published 0.4.1+ client reads `stable-v2`.

Developer ID/notarized packaging additionally fails closed until there is a
separate, fully resolved and fully SHA-256-hashed requirements lock whose
metadata binds the source constraints, complete resolved distribution set,
platform/Python resolver evidence, and root wrapper extra. Setup consumes that
exact lock using pip `--require-hashes`; syntactically hashed entries in a
partial constraints file are not accepted as a complete lock. Every
setup-weight manifest entry must also have completed required official-asset
revalidation. Python itself is the existing bundled CPython 3.12 copy path:
the candidate app must contain a non-symlink runtime root and executable at
safe relative paths under `Contents/Resources/python/cpython-3.12/`, declare
Python 3.12 in `setup_manifest.json`, and pass the copied-runtime smoke test.
The runtime fingerprint records this copied payload before signing; app-relative
Mach-O linkage, code signing, notarization, and DMG verification cover the
candidate app afterwards.

The dependency-lock v4 receipt is intentionally exact. It records the static
resolver identity (`pip-compile` 7.5.0, `platform=macos-14-arm64`, and
`python=3.12`) and the observed `pip` version, complete CPython version, macOS
version, and `sysconfig` platform. It also stores a UUID generation ID in both
the metadata and a comment header in the hash lock. The release gate,
packaged-app audit, and installed setup path reject a mixed generation, any
other resolver identity, a changed source-constraints digest, an incomplete
resolved distribution inventory, or a different root extra. The app build ID
also includes the lock and metadata SHA-256 prefixes. The packaged-app audit
reads `setup_manager.py` from the wrapper wheel actually embedded in the app
and structurally verifies the hashed-lock install path; it does not trust a
checkout copy or comments. This describes the required future lock artifact;
it does not make the current broad constraints file a release-ready lock.

The reproducible lock generator is
`scripts/generate_macos_arm64_py312_lock.py`. It intentionally refuses to run
unless its interpreter is **CPython 3.12 on an Apple Silicon Mac running macOS
14.x** and its installed resolver is exactly `pip-tools 7.5.0`. This is stricter
than the application's macOS 14-or-later support floor: `pip-compile` cannot
faithfully cross-resolve wheel compatibility for a macOS 14 target from a
macOS 15/26 host, so the generator refuses to label such a lock as macOS 14.

On that dedicated macOS 14 resolver host, the first toolchain lock is an
explicit, offline bootstrap rather than a guessed repository file. Generate a
source-identity receipt, review a declaration containing normalized exact
name/version choices for `pip`, `build`, `setuptools`, `wheel`,
`scikit-build-core`, `pybind11`, `cmake`, `ninja`, and every required
transitive, then place exactly one approved local wheel for each declaration
entry in a private source wheelhouse. The bootstrap command only copies and
hashes those local bytes; it never resolves or downloads.

The source-identity v2 inventory stores only portable basenames and SHA-256
digests. It binds `pyproject.toml`, the source constraints, both component
builders, `release_build_toolchain.py`, `run_release_component_build.sh`,
`sign_fpsample_wheel_macos.py`, `verify_license_distribution.py`, and
`generate_macos_arm64_py312_lock.py`. A change to the authorization/receipt
implementation, sealed runner, signer, acvl-utils verifier, or canonical-lock
producer after declaration generation therefore revokes bootstrap
authorization (and final readiness through the same verifier) instead of
silently changing the recorded wheel bytes or lock provenance.

The operator flow is:

```bash
mkdir -m 700 build/release-bootstrap

python3.12 scripts/release_build_toolchain.py \
  --generate-source-identity \
  --source-identity-output build/release-bootstrap/source-identity.json \
  --project-file pyproject.toml \
  --constraints constraints/macos-arm64-py312.txt

python3.12 scripts/release_build_toolchain.py \
  --bootstrap-declaration build/release-bootstrap/toolchain-declaration.json \
  --source-identity build/release-bootstrap/source-identity.json \
  --bootstrap-source-wheelhouse /private/reviewed-release-toolchain-wheels \
  --bootstrap-output-directory build/release-bootstrap/toolchain \
  --python /path/to/cpython-3.12-arm64/bin/python3.12 \
  --uv /path/to/approved/uv
```

The declaration is an operator-reviewed artifact, not a fallback for missing
hashes. If the approved versions, local wheel bytes, or their upstream
provenance are unavailable, release preparation stops; do not invent a lock or
consult an index. The resulting metadata is bound to the source identity and
requires CPython 3.12/macOS 14/arm64. Prepare its sealed venv and receipt from
that copied wheelhouse, then run only `fpsample` and `acvl-utils` once with
`scripts/run_release_component_build.sh --bootstrap-pre-sign`. The runner
creates a short-lived authorization only after revalidating the identity,
declaration, hashed wheelhouse, sealed receipt, prepared Python, and selected
full Xcode boundary. Each component build emits a pre-sign wheel receipt with
the wheel SHA-256 plus METADATA/WHEEL hashes.

Seal the two component receipts into one pre-sign wheel receipt, then supply it
alongside the `dist/` directory to the canonical resolver:

```bash
python3.12 scripts/release_build_toolchain.py \
  --seal-pre-sign-wheel-receipt \
  --lock build/release-bootstrap/toolchain/release-build-toolchain.requirements.lock \
  --metadata build/release-bootstrap/toolchain/release-build-toolchain.lock.json \
  --wheelhouse build/release-bootstrap/toolchain/wheelhouse \
  --bootstrap-declaration build/release-bootstrap/toolchain-declaration.json \
  --source-identity build/release-bootstrap/source-identity.json \
  --receipt build/release-bootstrap/release-build-toolchain-receipt.json \
  --pre-sign-wheel-directory dist \
  --fpsample-component-receipt build/release-bootstrap/fpsample-pre-sign.json \
  --acvl-utils-component-receipt build/release-bootstrap/acvl-utils-pre-sign.json \
  --pre-sign-wheel-receipt build/release-bootstrap/pre-sign-wheels.json

python3.12 scripts/generate_macos_arm64_py312_lock.py \
  --bundled-override-wheel-directory dist \
  --pre-sign-wheel-receipt build/release-bootstrap/pre-sign-wheels.json

# Build-side only: download every canonical-lock dependency as a target wheel.
# The output contains lock distributions only; fpsample, acvl-utils, and the
# wrapper wheel remain separately packaged and SHA-256-bound.
python3.12 scripts/build_offline_dependency_wheelhouse.py

# Read-only check used again by app packaging; it never downloads.
python3.12 scripts/build_offline_dependency_wheelhouse.py --verify-existing
```

The generated canonical-lock metadata records the pre-sign receipt digest,
source identity, and sealed toolchain binding. Final readiness must revalidate
all of those artifacts, and final component invocations must pass the same
pre-sign receipt through `run_release_component_build.sh`; setting an
environment variable alone cannot enable bootstrap mode or replace the final
expected hashes.

It compiles the five distributed wrapper extras from `pyproject.toml` under
`constraints/macos-arm64-py312.txt`, with backtracking, SHA-256 hashes, a
binary-only pip policy, the explicit local `dist/` wheel directory for only the
two overrides, and PyPI for all other binary dependencies. It rejects a missing,
symlinked, wrong-name, wrong-version, or wrong-tag local resolution wheel.
Other files in `dist/` are not exposed to the resolver: it copies only the two
validated override wheels into an owned temporary wheel view before invoking
`pip-compile`.
After generating the complete graph, it removes only the exact `fpsample` and
`acvl-utils` blocks from the published install lock. It rejects any resolver
output that leaks a `file://`, `--find-links`, or absolute local resolver path;
it does not rewrite such output heuristically.

It validates every resulting pin/hash, both inventories, the component-wheel
versions, and the excluded-override metadata before using Darwin's atomic
directory-swap primitive to publish the lock and its metadata together. The
operation never exposes a half-written pair: an interruption can leave either
the previous complete generation or the newly swapped complete generation.
Consumers additionally require the lock-header and metadata generation IDs to
match, so a mixed pair is rejected rather than used. It never falls back to a
non-atomic two-file overwrite.

The supported app/test-account target remains **Apple Silicon macOS 14 or
later**. In particular, a macOS 15.7.3 clean-account install and runtime smoke
is a valid distribution verification target. The separate 14.x-only condition
above is solely for canonical lock generation: until a dedicated macOS-14
targeted wheel-resolution attestation exists, generating on a newer host could
select a macOS-15-only artifact and falsely label it compatible with macOS 14.

The canonical dependency lock/metadata and the reviewed bootstrap declaration,
local toolchain wheelhouse, sealed toolchain receipt, and pre-sign wheel
receipt have not yet been produced for this checkout. The current broad
requirements are therefore an explicit release blocker; this repository must
not treat the presence of the generator as release readiness.

Recommended DMG build for test-account installation:

```bash
scripts/build_mac_dmg.sh
```

This creates:

```text
dist/TotalSegmentator Wrapper for Mac-0.4.1-release-arm64.dmg
```

The DMG contains the app, an `/Applications` symlink, and a short README. Users
without admin rights can copy the app to `~/Applications` instead. It also
contains `Verify Test Account Install.command` for internal release validation
after Setup has completed in a separate test account.

`scripts/build_mac_app.sh` copies the existing Python 3.12 runtime selected by
`TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_PYTHON_RUNTIME_DIR`, or by the build
interpreter's `sys.base_prefix` when that override is absent. This is a local
build input, not a new runtime supply chain. The selected root and its
`bin/python3.12` executable must be regular, non-symlink filesystem entries.
Packaging executes it in isolated mode and rejects anything other than Python
3.12 before copying it into the candidate app.

```bash
TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_PYTHON_RUNTIME_DIR=/path/to/cpython-3.12-runtime \
  scripts/build_mac_app.sh
```

The script copies the selected runtime to
`Contents/Resources/python/cpython-3.12/` and writes
`python_runtime.strategy=bundled_python312`, relative `bundle_path` and
`python_executable` paths, and required major/minor `3`/`12` into
`setup_manifest.json`.

Immediately after the copy, packaging runs the copied interpreter in an empty
environment (`env -i`, `-I`) to confirm that it does not resolve `PYTHONHOME`,
the build machine's Python paths, or user site packages. It checks
`ensurepip --version`, creates a temporary venv *outside* the app bundle, and
checks that venv's `pip --version` without a network index. The temporary
directory is owned and path-validated before it is removed; a failing smoke
test retains it for diagnosis and stops packaging before license inventory or
signing.

The manifest's top-level `python_runtime_fingerprint` and matching
`python_runtime.fingerprint` are a deterministic
`copied-runtime-payload-pre-sign-v1` fingerprint: sorted relative paths, node
types, POSIX mode bits, safe relative symlink targets, and each regular file's
content digest. It rejects absolute/escaping symlinks and special filesystem
nodes. This fingerprint is for semantic bundled-runtime/venv compatibility and
is included in `build_id`; it is explicitly **not** an attestation of final
post-sign Mach-O bytes, because code signatures can legitimately change those
bytes. Final distributed-byte integrity remains the responsibility of the
codesign, notarization, and DMG verification gates. The supplementary
`python_runtime_executable_sha256` recorded by the installed state remains a
same-path migration detector for older manifests, not the canonical runtime
fingerprint.

`Contents/MacOS/TotalSegmentatorWrapperForMac` is built from
`native/macos/TotalSegmentatorWrapperForMac/` and requires full Xcode on the build machine.
Command Line Tools alone are not accepted because SwiftUI builds require a
matching Swift compiler and macOS SDK. End users do not need Xcode.

Bundled Python files are made read-only before signing while runtime
directories remain traversable/copyable, and Python bytecode cache is
redirected to App Support so first setup does not mutate sealed resources inside
the copied app bundle.


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
writes default runs under App Support. Sample 1 is a rights-holder-authorized
derived NIfTI plus precomputed ToothSeg preview artifacts for non-clinical UI
inspection; it is not DICOM, a diagnostic asset, or an accuracy-evaluation
asset. The raw DICOM is not included. The app bundle includes
`sample1/THIRD_PARTY_NOTICES.txt` with the authorization scope, artifact
SHA-256 values, ToothSeg attribution, and non-clinical limitation.
The distributed launcher does not silently use the host `python3` for the runtime
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
constraints or the bundled wheelhouse changed, it shows Setup again and waits
for user action before reinstalling the bundled dependencies offline.

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

For offline-only validation, the release-build behavior is to create the
private venv, install every Python dependency from the hash-locked bundled
wheelhouse, install the separately bound component and wrapper wheels, run
`pip check`, write `setup_state.json`, and then stop with `needs_network` before
model download.

`--allow-network` permits only model-weight downloads. Python dependency
installation always uses the bundled hashed lock and wheelhouse; the end-user
Mac does not run an index resolver or source build. The tested MPS stack is
declared by:

```text
Contents/Resources/constraints/macos-arm64-py312.txt
```

The release preview pins the tested MPS stack there instead of relying on broad
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
- Expose only the audited open tasks `craniofacial_structures` (115) and `teeth`
  (113). The 297 weight is the open 3 mm `total` helper used by robust crop, not
  another user-selectable task.
- Apple Silicon only for first app.
```

Expected size classes:

```text
Script repo: small
Managed environment: user downloads hundreds of MB during setup
Thin app + bundled Python 3.12 only: about 55 MB in the current preview build
Embedded Python/PyTorch app: hundreds of MB to 1GB+ possible
Model weights: separate, task-dependent, can add hundreds of MB
```

The preview can tolerate a large download if the public story is strong, but do not make the first GitHub artifact huge.

## Version pinning policy

Pin only after successful smoke tests.

Candidate dependency categories:

```text
- Python 3.12 for the distributed thin app
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

Keep `pydicom` optional for Python-side metadata audit. Packaged Mac builds also
contain the source-built dcm2niix executable and the C++ normalizer with GDCM
3.2.7 statically linked. Neither helper depends on Homebrew or an external
GDCM installation at runtime.

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
TotalSegmentator Wrapper for Mac: 0.4.1
Python: x.y.z
Torch: x.y.z
TotalSegmentator: x.y.z
macOS: x.y.z
DICOM normalizer: x.y.z or missing
optional DICOM transcoders: gdcmconv/dcmdjpeg/dcmconv availability
```

These should be saved into every benchmark log.
