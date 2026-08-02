#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace dicom_normalizer {

struct RescueStackInput {
    std::filesystem::path path;
    std::optional<int> instance_number;
    std::string content_sha256;
};

struct RescueStackEntry {
    int ordinal = 0;
    int instance_number = 0;
    int frame_number = 1;
    std::string content_sha256;
};

struct RescueStackResult {
    int size_x = 0;
    int size_y = 0;
    int size_z = 0;
    std::string dtype;
    std::string photometric_interpretation;
    bool multiframe_source = false;
    std::vector<RescueStackEntry> entries;
};

RescueStackResult export_rescue_stack_npy(
    std::vector<RescueStackInput> inputs,
    const std::filesystem::path& output);

}  // namespace dicom_normalizer
