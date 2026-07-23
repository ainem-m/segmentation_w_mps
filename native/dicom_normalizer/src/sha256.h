#pragma once

#include <filesystem>
#include <string>
#include <string_view>

namespace dicom_normalizer {

std::string sha256_hex(std::string_view value);
std::string sha256_file_hex(const std::filesystem::path& path);

}  // namespace dicom_normalizer
