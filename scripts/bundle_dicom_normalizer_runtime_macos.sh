#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 /path/to/totalsegmentator-wrapper-dicom-normalizer" >&2
  exit 2
fi

BINARY="$1"
LIB_DIR="$(dirname "${BINARY}")/lib"
GDCM_LIB_DIR="${GDCM_LIB_DIR:-/opt/homebrew/opt/gdcm/lib}"

if [[ ! -x "${BINARY}" ]]; then
  echo "DICOM normalizer is not executable: ${BINARY}" >&2
  exit 2
fi
if ! command -v otool >/dev/null 2>&1 || ! command -v install_name_tool >/dev/null 2>&1; then
  echo "otool and install_name_tool are required to bundle the macOS DICOM runtime." >&2
  exit 2
fi

rm -rf "${LIB_DIR}"
mkdir -p "${LIB_DIR}"

QUEUE_FILE="$(mktemp "${TMPDIR:-/tmp}/dicom-normalizer-libs.XXXXXX")"
trap 'rm -f "${QUEUE_FILE}"' EXIT

list_dependencies() {
  otool -L "$1" | tail -n +2 | sed -E 's/^[[:space:]]+([^[:space:]]+).*/\1/'
}

is_system_dependency() {
  case "$1" in
    /System/*|/usr/lib/*) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_dependency() {
  local dependency="$1"
  local owner="$2"
  local basename_dependency
  basename_dependency="$(basename "${dependency}")"
  if [[ "${dependency}" = /* && -f "${dependency}" ]]; then
    printf '%s\n' "${dependency}"
    return 0
  fi
  for candidate in \
    "$(dirname "${owner}")/${basename_dependency}" \
    "${LIB_DIR}/${basename_dependency}" \
    "${GDCM_LIB_DIR}/${basename_dependency}" \
    "/opt/homebrew/lib/${basename_dependency}" \
    "/usr/local/lib/${basename_dependency}"; do
    if [[ -f "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

printf '%s\n' "${BINARY}" > "${QUEUE_FILE}"
queue_index=1
while [[ "${queue_index}" -le "$(wc -l < "${QUEUE_FILE}" | tr -d ' ')" ]]; do
  owner="$(sed -n "${queue_index}p" "${QUEUE_FILE}")"
  queue_index=$((queue_index + 1))
  while IFS= read -r dependency; do
    [[ -z "${dependency}" ]] && continue
    if is_system_dependency "${dependency}"; then
      continue
    fi
    if ! resolved="$(resolve_dependency "${dependency}" "${owner}")"; then
      echo "Could not resolve non-system dependency ${dependency} required by ${owner}" >&2
      exit 1
    fi
    destination="${LIB_DIR}/$(basename "${resolved}")"
    if [[ ! -f "${destination}" ]]; then
      cp -L "${resolved}" "${destination}"
      chmod 755 "${destination}"
      printf '%s\n' "${destination}" >> "${QUEUE_FILE}"
    fi
  done < <(list_dependencies "${owner}")
done

patch_dependencies() {
  local target="$1"
  local dependency
  local replacement
  while IFS= read -r dependency; do
    [[ -z "${dependency}" ]] && continue
    if is_system_dependency "${dependency}"; then
      continue
    fi
    if [[ "${target}" == "${BINARY}" ]]; then
      replacement="@loader_path/lib/$(basename "${dependency}")"
    else
      replacement="@loader_path/$(basename "${dependency}")"
    fi
    install_name_tool -change "${dependency}" "${replacement}" "${target}"
  done < <(list_dependencies "${target}")
}

if command -v codesign >/dev/null 2>&1; then
  codesign --remove-signature "${BINARY}" >/dev/null 2>&1 || true
  for library in "${LIB_DIR}"/*.dylib; do
    codesign --remove-signature "${library}" >/dev/null 2>&1 || true
  done
fi

patch_dependencies "${BINARY}"
for library in "${LIB_DIR}"/*.dylib; do
  install_name_tool -id "@loader_path/$(basename "${library}")" "${library}"
  patch_dependencies "${library}"
done

while IFS= read -r rpath; do
  case "${rpath}" in
    /opt/homebrew/*|/usr/local/*)
      install_name_tool -delete_rpath "${rpath}" "${BINARY}"
      ;;
  esac
done < <(otool -l "${BINARY}" | awk '$1 == "path" {print $2}')

if ! otool -l "${BINARY}" | awk '$1 == "path" {print $2}' | grep -Fx '@loader_path/lib' >/dev/null; then
  install_name_tool -add_rpath '@loader_path/lib' "${BINARY}"
fi

for target in "${BINARY}" "${LIB_DIR}"/*.dylib; do
  if otool -L "${target}" | grep -E '^[[:space:]]+(/opt/homebrew|/usr/local)/' >/dev/null; then
    echo "Unbundled dependency remains in ${target}:" >&2
    otool -L "${target}" >&2
    exit 1
  fi
done

# Mach-O load-command edits invalidate existing signatures. Re-sign from the
# innermost code outward so the locally built runtime is executable; release
# builds replace these ad-hoc signatures with Developer ID signatures.
if command -v codesign >/dev/null 2>&1; then
  for library in "${LIB_DIR}"/*.dylib; do
    codesign --force --sign - "${library}" >/dev/null
  done
  codesign --force --sign - "${BINARY}" >/dev/null
fi

echo "${LIB_DIR}"
