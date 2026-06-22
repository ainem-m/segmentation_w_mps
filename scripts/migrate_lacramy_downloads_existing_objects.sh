#!/usr/bin/env bash
set -euo pipefail

SOURCE_ORIGIN="${SOURCE_ORIGIN:-https://downloads.lacramy.com}"
TARGET_BUCKET="${TARGET_BUCKET:-lacramy-downloads}"
WORK_DIR="${WORK_DIR:-${TMPDIR:-/tmp}/lacramy-downloads-migration}"
CHOIOKI_ROOT="${CHOIOKI_ROOT:-/Users/ainem/choioki}"

download_and_put() {
  local key="$1"
  local content_type="$2"
  local cache_control="$3"
  local local_path="${WORK_DIR}/${key}"

  mkdir -p "$(dirname "${local_path}")"
  curl -fsSL "${SOURCE_ORIGIN}/${key}" -o "${local_path}"
  put_local "${key}" "${local_path}" "${content_type}" "${cache_control}"
}

put_local() {
  local key="$1"
  local local_path="$2"
  local content_type="$3"
  local cache_control="$4"

  npx wrangler r2 object put "${TARGET_BUCKET}/${key}" \
    --file "${local_path}" \
    --content-type "${content_type}" \
    --cache-control "${cache_control}" \
    --remote \
    --force
}

short_cache="public, max-age=300"
asset_cache="public, max-age=14400"

download_and_put "redact/openai-privacy-filter-q4.signed.json" "application/json; charset=utf-8" "${short_cache}"

download_and_put "choioki/beta/2026-06-06-paid-beta-2/ChoiokiBeta.zip" "application/zip" "${asset_cache}"
download_and_put "choioki/beta/2026-06-06-paid-beta-2/RedactModelInstaller.zip" "application/zip" "${asset_cache}"
download_and_put "choioki/beta/2026-06-06-paid-beta-2/SHA256SUMS.txt" "text/plain; charset=utf-8" "${short_cache}"
download_and_put "choioki/beta/2026-06-06-paid-beta-2/OPERATION_GUIDE.ja.md" "text/markdown; charset=utf-8" "${short_cache}"
put_local \
  "choioki/beta/2026-06-06-paid-beta-2/REFERENCE_MANUAL.ja.md" \
  "${CHOIOKI_ROOT}/docs/verification/PAID_BETA_REFERENCE_MANUAL_2026-06-15.ja.md" \
  "text/markdown; charset=utf-8" \
  "${short_cache}"
put_local \
  "choioki/beta/2026-06-06-paid-beta-2/OPERATION_GUIDE.ja.html" \
  "${CHOIOKI_ROOT}/site/paid-beta-operation-guide.ja.html" \
  "text/html; charset=utf-8" \
  "${short_cache}"
put_local \
  "choioki/beta/2026-06-06-paid-beta-2/REFERENCE_MANUAL.ja.html" \
  "${CHOIOKI_ROOT}/site/paid-beta-reference-manual.ja.html" \
  "text/html; charset=utf-8" \
  "${short_cache}"
put_local \
  "choioki/beta/2026-06-06-paid-beta-2/LANDING.ja.html" \
  "${CHOIOKI_ROOT}/site/index.html" \
  "text/html; charset=utf-8" \
  "${short_cache}"
put_local \
  "choioki/beta/2026-06-06-paid-beta-2/index.html" \
  "${CHOIOKI_ROOT}/site/index.html" \
  "text/html; charset=utf-8" \
  "${short_cache}"
put_local \
  "choioki/beta/2026-06-06-paid-beta-2/assets/hero-choioki-tray.png" \
  "${CHOIOKI_ROOT}/site/assets/hero-choioki-tray.png" \
  "image/png" \
  "${asset_cache}"

for image in \
  capture-controls.png \
  capture-recording-cards.png \
  diagnostics-pack.png \
  file-card-in-tray.png \
  finder-visible-files.png \
  folder-access-dialog.png \
  item-details-ocr.png \
  markdown-note-card.png \
  mixed-work-tray.png \
  model-installer-initial.png \
  model-installer-installed.png \
  operation-history.png \
  quick-note-editor.png \
  recording-in-progress.png \
  redact-image-review.png \
  redact-model-status.png \
  redact-shelf.png \
  redact-text-review.png \
  right-edge-tab.png \
  risk-review.png \
  screen-recording-dialog.png \
  settings-features.png \
  storage-setup.png \
  system-settings-screen-recording.png \
  tray-export.png
do
  put_local \
    "choioki/beta/2026-06-06-paid-beta-2/guide-assets/${image}" \
    "${CHOIOKI_ROOT}/site/assets/paid-beta-guide/${image}" \
    "image/png" \
    "${asset_cache}"
done

echo "Migrated known Choioki/Redact objects to ${TARGET_BUCKET}."
