#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build/dicom_normalizer"

cmake -S "${ROOT_DIR}/native/dicom_normalizer" -B "${BUILD_DIR}"
cmake --build "${BUILD_DIR}" --parallel

echo "${BUILD_DIR}/totalsegmentator-wrapper-dicom-normalizer"
