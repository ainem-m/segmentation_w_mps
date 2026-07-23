#include <algorithm>
#include <array>
#include <cmath>
#include <cctype>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>
#include <sys/wait.h>
#include <unistd.h>

#include "gdcm_import.h"
#include "rescue_stack.h"
#include "sha256.h"

namespace fs = std::filesystem;

namespace {

constexpr std::string_view kVersion = "0.2.0";
constexpr std::size_t kDicomdirReadLimitBytes = 64 * 1024 * 1024;
constexpr std::size_t kPreviewReadLimitBytes = 512ULL * 1024ULL * 1024ULL;
constexpr int kMinVolumeSlices = 32;

struct Args {
    std::string command;
    fs::path dicom_dir;
    fs::path output;
    std::optional<int> series_number;
    std::string series_key;
    std::string group_id;
    std::array<double, 3> patched_spacing{0.0, 0.0, 0.0};
    fs::path dcm2niix;
};

struct OptionalTools {
    std::optional<fs::path> gdcmconv;
    std::optional<fs::path> dcmdjpeg;
    std::optional<fs::path> dcmconv;

    bool any_transcoder() const {
        return gdcmconv.has_value() || dcmdjpeg.has_value() || dcmconv.has_value();
    }
};

struct Classification {
    std::string status;
    std::string grade;
    std::string rescue_grade;
    std::vector<std::string> reasons;
    std::string reject_reason;
    std::string recommendation;
    std::string next_action;
    bool requires_external_tool = false;
};

struct MprPreviewInfo {
    std::string plane;
    fs::path path;
    int width = 0;
    int height = 0;
    double min_value = 0.0;
    double max_value = 0.0;
    bool uniform_or_empty = true;
};

struct DicomMeta {
    bool parsed = false;
    fs::path source_path;
    std::string error;
    std::string parser_backend;
    bool has_dicm_prefix = false;
    bool has_file_meta = false;
    std::string transfer_syntax_uid;
    std::string series_instance_uid;
    std::string sop_instance_uid;
    std::optional<int> series_number;
    std::optional<int> instance_number;
    std::string series_description;
    std::string modality;
    std::string sop_class_uid;
    std::string sop_class_name;
    std::vector<std::string> image_type;
    std::optional<int> rows;
    std::optional<int> columns;
    std::optional<int> number_of_frames;
    std::optional<int> samples_per_pixel;
    std::string photometric_interpretation;
    std::optional<int> bits_allocated;
    std::optional<int> pixel_representation;
    bool has_pixel_spacing = false;
    bool has_image_position_patient = false;
    bool has_image_orientation_patient = false;
    std::array<double, 2> pixel_spacing{0.0, 0.0};
    std::array<double, 3> image_position_patient{0.0, 0.0, 0.0};
    std::array<double, 6> image_orientation_patient{0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
    bool has_pixel_data = false;
    bool pixel_decode_attempted = false;
    bool pixel_decode_ok = false;
    bool geometry_from_functional_groups = false;
    std::uint64_t decoded_bytes = 0;
    std::uint64_t decoded_fnv1a64 = 0;
    bool has_slice_thickness = false;
    bool has_spacing_between_slices = false;
    std::optional<double> slice_thickness;
    std::optional<double> spacing_between_slices;
    std::string burned_in_annotation;
    std::string content_sha256;
};

struct SeriesSummary {
    std::string key;
    std::vector<DicomMeta> files;
};

struct DicomdirSummary {
    int dicomdir_file_count = 0;
    std::vector<std::string> referenced_file_ids;
    int resolved_reference_count = 0;
    int missing_reference_count = 0;
};

struct ViewerExportGroup {
    std::string id;
    std::string plane_label;
    std::string recommendation;
    std::string ai_eligibility;
    std::vector<std::string> reasons;
    std::vector<const DicomMeta*> files;
    std::array<int, 2> shape{0, 0};
    std::array<double, 2> pixel_spacing{0.0, 0.0};
    std::array<double, 6> orientation{0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
    std::array<double, 3> normal{0.0, 0.0, 0.0};
    double slice_spacing_median = 0.0;
    double slice_spacing_min = 0.0;
    double slice_spacing_max = 0.0;
    double fov_row_mm = 0.0;
    double fov_column_mm = 0.0;
    double fov_through_plane_mm = 0.0;
    int instance_min = 0;
    int instance_max = 0;
    bool instance_contiguous = false;
    bool volume_like = false;
    bool uniform_spacing = false;
    bool duplicate_positions = false;
    bool in_plane_drift_ok = false;
    bool non_parallel_slices = false;
};

struct NiftiHeaderInfo {
    bool ok = false;
    std::string error;
    std::array<int, 3> shape{0, 0, 0};
    std::array<double, 3> spacing{0.0, 0.0, 0.0};
    int datatype = 0;
    int bitpix = 0;
    int qform_code = 0;
    int sform_code = 0;
    double vox_offset = 0.0;
};

std::string trim_nulls(std::string value) {
    while (!value.empty() && (value.back() == '\0' || value.back() == ' ')) {
        value.pop_back();
    }
    return value;
}

std::string upper(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::toupper(ch));
    });
    return value;
}

std::vector<std::string> split_backslash(const std::string& value) {
    std::vector<std::string> parts;
    std::string current;
    for (char ch : value) {
        if (ch == '\\') {
            parts.push_back(current);
            current.clear();
        } else {
            current.push_back(ch);
        }
    }
    parts.push_back(current);
    parts.erase(
        std::remove_if(parts.begin(), parts.end(), [](const std::string& item) {
            return item.empty();
        }),
        parts.end());
    return parts;
}

std::vector<std::string> split_char(const std::string& value, char delimiter) {
    std::vector<std::string> parts;
    std::string current;
    for (char ch : value) {
        if (ch == delimiter) {
            parts.push_back(current);
            current.clear();
        } else {
            current.push_back(ch);
        }
    }
    parts.push_back(current);
    return parts;
}

std::array<double, 3> parse_spacing(const std::string& text) {
    const auto parts = split_char(text, ',');
    if (parts.size() != 3) {
        throw std::runtime_error("--patched-spacing must be X,Y,Z");
    }
    std::array<double, 3> spacing{};
    for (std::size_t index = 0; index < 3; ++index) {
        spacing[index] = std::stod(parts[index]);
        if (!(spacing[index] > 0.0)) {
            throw std::runtime_error("--patched-spacing values must be positive");
        }
    }
    return spacing;
}

std::string join(const std::vector<std::string>& values, std::string_view separator = " ") {
    std::ostringstream out;
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index > 0) {
            out << separator;
        }
        out << values[index];
    }
    return out.str();
}

std::string shell_quote(const fs::path& path) {
    std::string input = path.string();
    std::string out = "'";
    for (char ch : input) {
        if (ch == '\'') {
            out += "'\\''";
        } else {
            out.push_back(ch);
        }
    }
    out += "'";
    return out;
}

std::string shell_quote_string(const std::string& value) {
    std::string out = "'";
    for (char ch : value) {
        if (ch == '\'') {
            out += "'\\''";
        } else {
            out.push_back(ch);
        }
    }
    out += "'";
    return out;
}

std::string json_escape(const std::string& value) {
    std::ostringstream out;
    for (unsigned char ch : value) {
        switch (ch) {
            case '"':
                out << "\\\"";
                break;
            case '\\':
                out << "\\\\";
                break;
            case '\b':
                out << "\\b";
                break;
            case '\f':
                out << "\\f";
                break;
            case '\n':
                out << "\\n";
                break;
            case '\r':
                out << "\\r";
                break;
            case '\t':
                out << "\\t";
                break;
            default:
                if (ch < 0x20) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                        << static_cast<int>(ch);
                } else {
                    out << static_cast<char>(ch);
                }
                break;
        }
    }
    return out.str();
}

std::string json_string(const std::string& value) {
    return "\"" + json_escape(value) + "\"";
}

std::string json_optional_string(const std::string& value) {
    return value.empty() ? "null" : json_string(value);
}

std::string json_optional_int(const std::optional<int>& value) {
    if (!value.has_value()) {
        return "null";
    }
    return std::to_string(*value);
}

std::string json_bool(bool value) {
    return value ? "true" : "false";
}

std::string hex_u64(std::uint64_t value) {
    std::ostringstream out;
    out << std::hex << value;
    return out.str();
}

std::string json_path_optional(const std::optional<fs::path>& path) {
    return path.has_value() ? json_string(path->string()) : "null";
}

std::string json_number_array3(const std::array<double, 3>& values) {
    std::ostringstream out;
    out << "[" << values[0] << ", " << values[1] << ", " << values[2] << "]";
    return out.str();
}

std::string json_number_array2(const std::array<double, 2>& values) {
    std::ostringstream out;
    out << "[" << values[0] << ", " << values[1] << "]";
    return out.str();
}

std::string json_number_array6(const std::array<double, 6>& values) {
    std::ostringstream out;
    out << "[";
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index > 0) {
            out << ", ";
        }
        out << values[index];
    }
    out << "]";
    return out.str();
}

std::string json_int_array3(const std::array<int, 3>& values) {
    std::ostringstream out;
    out << "[" << values[0] << ", " << values[1] << ", " << values[2] << "]";
    return out.str();
}

std::optional<fs::path> find_executable(const std::string& name) {
    if (const char* disable = std::getenv("TOTALSEGMENTATOR_WRAPPER_MAC_DISABLE_EXTERNAL_DICOM_TOOLS")) {
        if (std::string(disable) == "1") {
            return std::nullopt;
        }
    }

    std::vector<fs::path> candidates;
    if (const char* path_env = std::getenv("PATH")) {
        for (const auto& part : split_char(path_env, ':')) {
            if (!part.empty()) {
                candidates.push_back(fs::path(part) / name);
            }
        }
    }
    candidates.push_back(fs::path("/opt/homebrew/bin") / name);
    candidates.push_back(fs::path("/usr/local/bin") / name);

    for (const auto& candidate : candidates) {
        if (fs::exists(candidate) && access(candidate.c_str(), X_OK) == 0) {
            return fs::absolute(candidate);
        }
    }
    return std::nullopt;
}

OptionalTools detect_optional_tools() {
    OptionalTools tools;
    tools.gdcmconv = find_executable("gdcmconv");
    tools.dcmdjpeg = find_executable("dcmdjpeg");
    tools.dcmconv = find_executable("dcmconv");
    return tools;
}

std::string path_hash_fnv1a64(const fs::path& path) {
    // Stable non-cryptographic hash to avoid recording PHI-bearing full paths
    // in the initial standalone audit JSON. A future release can replace this
    // with SHA-256 when a small vetted hash implementation is introduced.
    const auto text = path.lexically_normal().string();
    uint64_t hash = 1469598103934665603ULL;
    for (unsigned char ch : text) {
        hash ^= static_cast<uint64_t>(ch);
        hash *= 1099511628211ULL;
    }
    std::ostringstream out;
    out << std::hex << hash;
    return out.str();
}

uint16_t read_u16_le(const std::vector<uint8_t>& data, std::size_t offset) {
    if (offset + 2 > data.size()) {
        throw std::out_of_range("u16 beyond buffer");
    }
    return static_cast<uint16_t>(data[offset]) | (static_cast<uint16_t>(data[offset + 1]) << 8U);
}

uint32_t read_u32_le(const std::vector<uint8_t>& data, std::size_t offset) {
    if (offset + 4 > data.size()) {
        throw std::out_of_range("u32 beyond buffer");
    }
    return static_cast<uint32_t>(data[offset])
        | (static_cast<uint32_t>(data[offset + 1]) << 8U)
        | (static_cast<uint32_t>(data[offset + 2]) << 16U)
        | (static_cast<uint32_t>(data[offset + 3]) << 24U);
}

int16_t read_i16_le(const std::vector<uint8_t>& data, std::size_t offset) {
    return static_cast<int16_t>(read_u16_le(data, offset));
}

float read_f32_le(const std::vector<uint8_t>& data, std::size_t offset) {
    if (offset + 4 > data.size()) {
        throw std::out_of_range("f32 beyond buffer");
    }
    uint32_t raw = read_u32_le(data, offset);
    float value = 0.0F;
    static_assert(sizeof(float) == sizeof(uint32_t));
    std::memcpy(&value, &raw, sizeof(float));
    return value;
}

bool long_vr(const std::string& vr) {
    static const std::vector<std::string> values = {
        "OB", "OD", "OF", "OL", "OW", "SQ", "UC", "UR", "UT", "UN"
    };
    return std::find(values.begin(), values.end(), vr) != values.end();
}

std::string rounded_double_key(double value, int decimals = 4) {
    const double scale = std::pow(10.0, decimals);
    const double rounded = std::round(value * scale) / scale;
    std::ostringstream out;
    out << std::fixed << std::setprecision(decimals) << rounded;
    return out.str();
}

std::string rounded_array_key(const std::array<double, 2>& values) {
    return rounded_double_key(values[0]) + "\\" + rounded_double_key(values[1]);
}

std::string rounded_array_key(const std::array<double, 6>& values) {
    std::ostringstream out;
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index > 0) {
            out << "\\";
        }
        out << rounded_double_key(values[index]);
    }
    return out.str();
}

double dot3(const std::array<double, 3>& lhs, const std::array<double, 3>& rhs) {
    return lhs[0] * rhs[0] + lhs[1] * rhs[1] + lhs[2] * rhs[2];
}

double norm3(const std::array<double, 3>& value) {
    return std::sqrt(dot3(value, value));
}

std::array<double, 3> normalize3(std::array<double, 3> value) {
    const double n = norm3(value);
    if (!(n > 0.0)) {
        return {0.0, 0.0, 0.0};
    }
    return {value[0] / n, value[1] / n, value[2] / n};
}

std::array<double, 3> cross3(const std::array<double, 3>& lhs, const std::array<double, 3>& rhs) {
    return {
        lhs[1] * rhs[2] - lhs[2] * rhs[1],
        lhs[2] * rhs[0] - lhs[0] * rhs[2],
        lhs[0] * rhs[1] - lhs[1] * rhs[0],
    };
}

std::array<double, 3> row_cosines(const std::array<double, 6>& iop) {
    return {iop[0], iop[1], iop[2]};
}

std::array<double, 3> column_cosines(const std::array<double, 6>& iop) {
    return {iop[3], iop[4], iop[5]};
}

std::string sop_class_name(const std::string& uid) {
    if (uid == "1.2.840.10008.1.3.10") {
        return "Media Storage Directory Storage";
    }
    if (uid == "1.2.840.10008.5.1.4.1.1.2") {
        return "CT Image Storage";
    }
    if (uid == "1.2.840.10008.5.1.4.1.1.2.1") {
        return "Enhanced CT Image Storage";
    }
    if (uid.rfind("1.2.840.10008.5.1.4.1.1.7", 0) == 0) {
        return "Secondary Capture Image Storage";
    }
    if (uid.find("88.67") != std::string::npos || uid.find("SR") != std::string::npos) {
        return "Structured Report";
    }
    return {};
}

std::string transfer_syntax_name(const std::string& uid) {
    if (uid == "1.2.840.10008.1.2") {
        return "Implicit VR Little Endian";
    }
    if (uid == "1.2.840.10008.1.2.1") {
        return "Explicit VR Little Endian";
    }
    if (uid == "1.2.840.10008.1.2.2") {
        return "Explicit VR Big Endian";
    }
    if (uid == "1.2.840.10008.1.2.1.99") {
        return "Deflated Explicit VR Little Endian";
    }
    if (uid == "1.2.840.10008.1.2.5") {
        return "RLE Lossless";
    }
    if (uid.rfind("1.2.840.10008.1.2.4.", 0) == 0) {
        return "JPEG-family compressed transfer syntax";
    }
    return {};
}

bool is_compressed_transfer_syntax(const std::string& uid) {
    return uid == "1.2.840.10008.1.2.1.99"
        || uid == "1.2.840.10008.1.2.5"
        || uid.rfind("1.2.840.10008.1.2.4.", 0) == 0;
}

DicomMeta parse_dicom_file(const fs::path& path) {
    const auto probe = dicom_normalizer::probe_dicom_file(path);
    DicomMeta meta;
    meta.parsed = probe.parsed;
    meta.source_path = path;
    meta.error = probe.error;
    meta.parser_backend = "gdcm";
    meta.has_dicm_prefix = probe.has_dicm_prefix;
    meta.has_file_meta = probe.has_file_meta;
    meta.transfer_syntax_uid = probe.transfer_syntax_uid;
    meta.series_instance_uid = probe.series_instance_uid;
    meta.sop_instance_uid = probe.sop_instance_uid;
    meta.series_number = probe.series_number;
    meta.instance_number = probe.instance_number;
    meta.series_description = probe.series_description;
    meta.modality = probe.modality;
    meta.sop_class_uid = probe.sop_class_uid;
    meta.sop_class_name = probe.sop_class_name.empty()
        ? sop_class_name(probe.sop_class_uid) : probe.sop_class_name;
    meta.image_type = probe.image_type;
    meta.rows = probe.rows;
    meta.columns = probe.columns;
    meta.number_of_frames = probe.number_of_frames;
    meta.samples_per_pixel = probe.samples_per_pixel;
    meta.photometric_interpretation = probe.photometric_interpretation;
    meta.bits_allocated = probe.bits_allocated;
    meta.pixel_representation = probe.pixel_representation;
    meta.has_pixel_spacing = probe.has_pixel_spacing;
    meta.has_image_position_patient = probe.has_image_position_patient;
    meta.has_image_orientation_patient = probe.has_image_orientation_patient;
    meta.pixel_spacing = probe.pixel_spacing;
    meta.image_position_patient = probe.image_position_patient;
    meta.image_orientation_patient = probe.image_orientation_patient;
    meta.has_pixel_data = probe.has_pixel_data;
    meta.pixel_decode_attempted = probe.pixel_decode_attempted;
    meta.pixel_decode_ok = probe.pixel_decode_ok;
    meta.geometry_from_functional_groups = probe.geometry_from_functional_groups;
    meta.decoded_bytes = probe.decoded_bytes;
    meta.decoded_fnv1a64 = probe.decoded_fnv1a64;
    meta.has_slice_thickness = probe.has_slice_thickness;
    meta.has_spacing_between_slices = probe.has_spacing_between_slices;
    meta.slice_thickness = probe.slice_thickness;
    meta.spacing_between_slices = probe.spacing_between_slices;
    meta.burned_in_annotation = probe.burned_in_annotation;
    if (meta.parsed && meta.sop_class_uid.empty() && meta.series_instance_uid.empty()) {
        meta.parsed = false;
        meta.error = "missing_dicom_identity_tags";
    }
    if (meta.parsed) {
        try {
            meta.content_sha256 = dicom_normalizer::sha256_file_hex(path);
        } catch (...) {
            meta.parsed = false;
            meta.error = "content_sha256_failed";
        }
    }
    return meta;
}

std::string series_key(const DicomMeta& meta) {
    if (!meta.series_instance_uid.empty()) {
        return meta.series_instance_uid;
    }
    std::ostringstream out;
    out << "series-number:";
    if (meta.series_number.has_value()) {
        out << *meta.series_number;
    } else {
        out << "unknown";
    }
    out << ":" << meta.series_description;
    return out.str();
}

bool contains_word(const std::string& text, std::string_view word) {
    return upper(text).find(std::string(word)) != std::string::npos;
}

std::vector<std::string> upper_vector(const std::vector<std::string>& values) {
    std::vector<std::string> result;
    result.reserve(values.size());
    for (const auto& value : values) {
        result.push_back(upper(value));
    }
    return result;
}

std::string viewer_export_group_key(const DicomMeta& meta) {
    if (!meta.rows.has_value() || !meta.columns.has_value()
        || !meta.has_pixel_spacing
        || !meta.has_image_position_patient
        || !meta.has_image_orientation_patient) {
        return {};
    }
    std::ostringstream out;
    out << *meta.rows << "x" << *meta.columns
        << "|ps=" << rounded_array_key(meta.pixel_spacing)
        << "|iop=" << rounded_array_key(meta.image_orientation_patient)
        << "|bits=" << meta.bits_allocated.value_or(0)
        << "|repr=" << meta.pixel_representation.value_or(-1)
        << "|samples=" << meta.samples_per_pixel.value_or(0)
        << "|photo=" << meta.photometric_interpretation;
    return out.str();
}

std::string plane_label_from_normal(const std::array<double, 3>& normal) {
    const double ax = std::abs(normal[0]);
    const double ay = std::abs(normal[1]);
    const double az = std::abs(normal[2]);
    if (az >= 0.90 && az >= ax && az >= ay) {
        return "axial_like";
    }
    if (ay >= 0.90 && ay >= ax && ay >= az) {
        return "coronal_like";
    }
    if (ax >= 0.90 && ax >= ay && ax >= az) {
        return "sagittal_like";
    }
    if (az >= 0.75 && az >= ax && az >= ay) {
        return "oblique_axial_like";
    }
    if (ay >= 0.75 && ay >= ax && ay >= az) {
        return "oblique_coronal_like";
    }
    if (ax >= 0.75 && ax >= ay && ax >= az) {
        return "oblique_sagittal_like";
    }
    return "oblique_unknown";
}

double median_sorted(std::vector<double> values) {
    if (values.empty()) {
        return 0.0;
    }
    std::sort(values.begin(), values.end());
    const std::size_t mid = values.size() / 2;
    if (values.size() % 2 == 1) {
        return values[mid];
    }
    return (values[mid - 1] + values[mid]) * 0.5;
}

std::vector<ViewerExportGroup> build_viewer_export_groups(const SeriesSummary& series) {
    std::map<std::string, std::vector<const DicomMeta*>> grouped;
    for (const auto& file : series.files) {
        const std::string key = viewer_export_group_key(file);
        if (!key.empty()) {
            grouped[key].push_back(&file);
        }
    }
    if (grouped.size() < 2) {
        return {};
    }

    std::vector<ViewerExportGroup> result;
    for (auto& [_, files] : grouped) {
        if (files.empty()) {
            continue;
        }
        ViewerExportGroup group;
        group.files = files;
        const DicomMeta& first = *files.front();
        group.shape = {first.rows.value_or(0), first.columns.value_or(0)};
        group.pixel_spacing = first.pixel_spacing;
        group.orientation = first.image_orientation_patient;
        const auto row = normalize3(row_cosines(group.orientation));
        const auto column = normalize3(column_cosines(group.orientation));
        group.normal = normalize3(cross3(row, column));
        group.non_parallel_slices = !(norm3(group.normal) > 0.0);
        group.plane_label = plane_label_from_normal(group.normal);

        std::sort(group.files.begin(), group.files.end(), [&](const DicomMeta* lhs, const DicomMeta* rhs) {
            const double lhs_pos = dot3(lhs->image_position_patient, group.normal);
            const double rhs_pos = dot3(rhs->image_position_patient, group.normal);
            if (std::abs(lhs_pos - rhs_pos) > 1e-5) {
                return lhs_pos < rhs_pos;
            }
            return lhs->source_path < rhs->source_path;
        });

        std::vector<double> slice_positions;
        std::vector<double> row_positions;
        std::vector<double> column_positions;
        std::vector<int> instances;
        slice_positions.reserve(group.files.size());
        for (const DicomMeta* file : group.files) {
            slice_positions.push_back(dot3(file->image_position_patient, group.normal));
            row_positions.push_back(dot3(file->image_position_patient, row));
            column_positions.push_back(dot3(file->image_position_patient, column));
            if (file->instance_number.has_value()) {
                instances.push_back(*file->instance_number);
            }
        }

        std::vector<double> deltas;
        for (std::size_t index = 1; index < slice_positions.size(); ++index) {
            const double delta = slice_positions[index] - slice_positions[index - 1];
            if (std::abs(delta) <= 1e-4) {
                group.duplicate_positions = true;
            }
            if (delta > 1e-4) {
                deltas.push_back(delta);
            }
        }
        group.slice_spacing_median = median_sorted(deltas);
        if (!deltas.empty()) {
            auto [min_it, max_it] = std::minmax_element(deltas.begin(), deltas.end());
            group.slice_spacing_min = *min_it;
            group.slice_spacing_max = *max_it;
            const double tolerance = std::max(0.05, 0.05 * group.slice_spacing_median);
            group.uniform_spacing = std::all_of(deltas.begin(), deltas.end(), [&](double value) {
                return std::abs(value - group.slice_spacing_median) <= tolerance;
            });
            group.fov_through_plane_mm = slice_positions.back() - slice_positions.front();
        }

        if (!row_positions.empty() && !column_positions.empty()) {
            auto [row_min, row_max] = std::minmax_element(row_positions.begin(), row_positions.end());
            auto [col_min, col_max] = std::minmax_element(column_positions.begin(), column_positions.end());
            const double drift = std::max(*row_max - *row_min, *col_max - *col_min);
            const double tolerance = std::max(0.10, 0.5 * std::min(group.pixel_spacing[0], group.pixel_spacing[1]));
            group.in_plane_drift_ok = drift <= tolerance;
        }
        if (!instances.empty()) {
            auto [min_it, max_it] = std::minmax_element(instances.begin(), instances.end());
            group.instance_min = *min_it;
            group.instance_max = *max_it;
            group.instance_contiguous =
                static_cast<int>(instances.size()) == (group.instance_max - group.instance_min + 1);
        }
        group.fov_row_mm = static_cast<double>(group.shape[0]) * group.pixel_spacing[0];
        group.fov_column_mm = static_cast<double>(group.shape[1]) * group.pixel_spacing[1];
        group.volume_like = static_cast<int>(group.files.size()) >= kMinVolumeSlices
            && group.uniform_spacing
            && !group.duplicate_positions
            && group.in_plane_drift_ok
            && !group.non_parallel_slices;

        if (!group.volume_like) {
            group.recommendation = "no_go";
            group.ai_eligibility = "no_go";
            group.reasons.push_back("group_geometry_not_volume_like");
        } else if (group.plane_label == "axial_like" || group.plane_label == "oblique_axial_like") {
            group.recommendation = "recommended";
            group.ai_eligibility = "rescue_go_with_warning";
            group.reasons.push_back("axial_like");
            group.reasons.push_back("uniform_spacing");
        } else {
            group.recommendation = "alternative";
            group.ai_eligibility = "preview_only";
            group.reasons.push_back("not_axial_like_for_v1");
        }
        result.push_back(std::move(group));
    }

    std::sort(result.begin(), result.end(), [](const ViewerExportGroup& lhs, const ViewerExportGroup& rhs) {
        if (lhs.instance_min != rhs.instance_min) {
            return lhs.instance_min < rhs.instance_min;
        }
        return lhs.plane_label < rhs.plane_label;
    });
    for (std::size_t index = 0; index < result.size(); ++index) {
        std::ostringstream id;
        id << "g" << std::setw(3) << std::setfill('0') << (index + 1);
        result[index].id = id.str();
    }
    return result;
}

bool has_viewer_export_rescue_candidate(const SeriesSummary& series) {
    const auto groups = build_viewer_export_groups(series);
    return std::any_of(groups.begin(), groups.end(), [](const ViewerExportGroup& group) {
        return group.ai_eligibility == "rescue_go_with_warning";
    });
}

bool same_array2(const std::array<double, 2>& lhs, const std::array<double, 2>& rhs, double tolerance = 1e-4) {
    for (std::size_t index = 0; index < lhs.size(); ++index) {
        if (std::abs(lhs[index] - rhs[index]) > tolerance) {
            return false;
        }
    }
    return true;
}

bool same_array6(const std::array<double, 6>& lhs, const std::array<double, 6>& rhs, double tolerance = 1e-4) {
    for (std::size_t index = 0; index < lhs.size(); ++index) {
        if (std::abs(lhs[index] - rhs[index]) > tolerance) {
            return false;
        }
    }
    return true;
}

Classification classify_series(const SeriesSummary& series, const OptionalTools& tools) {
    (void)tools;
    const DicomMeta& first = series.files.front();
    const int file_count = static_cast<int>(series.files.size());

    bool shape_consistent = true;
    bool pixel_spacing_consistent = true;
    bool orientation_consistent = true;
    for (const auto& item : series.files) {
        if (item.rows != first.rows || item.columns != first.columns) {
            shape_consistent = false;
        }
        if (item.has_pixel_spacing && first.has_pixel_spacing
            && !same_array2(item.pixel_spacing, first.pixel_spacing)) {
            pixel_spacing_consistent = false;
        }
        if (item.has_image_orientation_patient && first.has_image_orientation_patient
            && !same_array6(item.image_orientation_patient, first.image_orientation_patient)) {
            orientation_consistent = false;
        }
    }

    int pixel_spacing_count = 0;
    int position_count = 0;
    int orientation_count = 0;
    int max_number_of_frames = 0;
    int compressed_count = 0;
    int pixel_decode_failure_count = 0;
    for (const auto& item : series.files) {
        pixel_spacing_count += item.has_pixel_spacing ? 1 : 0;
        position_count += item.has_image_position_patient ? 1 : 0;
        orientation_count += item.has_image_orientation_patient ? 1 : 0;
        max_number_of_frames = std::max(max_number_of_frames, item.number_of_frames.value_or(0));
        compressed_count += is_compressed_transfer_syntax(item.transfer_syntax_uid) ? 1 : 0;
        pixel_decode_failure_count += item.has_pixel_data && !item.pixel_decode_ok ? 1 : 0;
    }
    const int effective_frame_count = std::max(file_count, max_number_of_frames);

    const auto image_type_upper = upper_vector(first.image_type);
    const std::string image_type_text = join(image_type_upper);
    const std::string description_upper = upper(first.series_description);
    const std::string sop_name_upper = upper(first.sop_class_name);
    const std::string modality_upper = upper(first.modality);

    auto make = [](
        std::string status,
        std::string grade,
        std::string rescue_grade,
        std::vector<std::string> reasons,
        std::string reject_reason,
        std::string next_action,
        bool requires_external_tool,
        std::string recommendation
    ) {
        Classification classification;
        classification.status = std::move(status);
        classification.grade = std::move(grade);
        classification.rescue_grade = std::move(rescue_grade);
        classification.reasons = std::move(reasons);
        classification.reject_reason = std::move(reject_reason);
        classification.next_action = std::move(next_action);
        classification.requires_external_tool = requires_external_tool;
        classification.recommendation = std::move(recommendation);
        return classification;
    };

    if (first.sop_class_uid == "1.2.840.10008.1.3.10"
        || contains_word(sop_name_upper, "MEDIA STORAGE DIRECTORY")) {
        return make(
            "dicomdir_only",
            "index only",
            "none",
            {"dicomdir_index_object"},
            "dicomdir_not_image_series",
            "audit_referenced_files",
            false,
            "DICOMDIR is an index, not image pixel data. Audit referenced image files instead.");
    }

    const std::string dose_haystack = description_upper + " " + sop_name_upper + " " + image_type_text;
    if (contains_word(dose_haystack, "DOSE") || contains_word(dose_haystack, "REPORT")) {
        return make(
            "reject",
            "reject",
            "none",
            {"dose_report_or_structured_report"},
            "dose_report_or_structured_report",
            "exclude_series",
            false,
            "Do not use for volume segmentation.");
    }

    const std::string scout_haystack = description_upper + " " + image_type_text;
    if (contains_word(scout_haystack, "SCOUT")
        || contains_word(scout_haystack, "LOCALIZER")
        || contains_word(scout_haystack, "TOPOGRAM")) {
        return make(
            "reject",
            "reject",
            "none",
            {"scout_or_localizer"},
            "scout_or_localizer",
            "exclude_series",
            false,
            "Do not use for dental volume segmentation.");
    }

    if (pixel_decode_failure_count > 0) {
        return make(
            "pixel_decode_failed",
            "reject",
            "none",
            {"gdcm_pixel_decode_failed"},
            "gdcm_pixel_decode_failed",
            "inspect_decode_error_or_request_original_export",
            false,
            "GDCM could parse metadata but could not decode all pixel data. No fallback was used.");
    }

    const bool is_secondary_capture =
        first.sop_class_uid.rfind("1.2.840.10008.5.1.4.1.1.7", 0) == 0
        || contains_word(sop_name_upper, "SECONDARY CAPTURE")
        || contains_word(image_type_text, "SECONDARY")
        || contains_word(image_type_text, "SCREEN SAVE");
    const bool axial_like = contains_word(description_upper, "AXIAL")
        || contains_word(image_type_text, "AXIAL");
    const bool coronal_like = contains_word(description_upper, "CORONAL")
        || contains_word(image_type_text, "CORONAL");
    const bool sagittal_like = contains_word(description_upper, "SAGITTAL")
        || contains_word(image_type_text, "SAGITTAL");
    const bool non_axial_mpr = coronal_like || sagittal_like;

    if (is_secondary_capture) {
        if (effective_frame_count >= kMinVolumeSlices && shape_consistent && axial_like && !non_axial_mpr) {
            std::vector<std::string> reasons{
                "secondary_capture", "geometry_not_trusted", "manual_spacing_required",
                "not_segmentation_grade_original_ct"};
            if (compressed_count > 0) {
                reasons.push_back("compressed_transfer_syntax");
                reasons.push_back("native_gdcm_decode_ok");
            }
            return make(
                "secondary_capture_rescue_candidate",
                "C: rescue only",
                "C: rescue only",
                reasons,
                "",
                "prepare_rescue_with_explicit_spacing",
                false,
                "Manual pseudo-volume rescue may be possible, but output must remain rescue/non-diagnostic.");
        }
        const bool explicit_reference_plane =
            !axial_like && (coronal_like != sagittal_like);
        if (effective_frame_count >= kMinVolumeSlices
            && shape_consistent
            && explicit_reference_plane) {
            return make(
                "secondary_capture_reference_candidate",
                "C: reference only",
                "C: reference only",
                {
                    "secondary_capture",
                    "geometry_not_trusted",
                    "reference_only",
                    coronal_like ? "explicit_coronal" : "explicit_sagittal",
                    "not_primary_axial_volume",
                },
                "",
                "use_as_rescue_reference_series",
                false,
                "Use only as a reference series for geometry estimation and preview alignment.");
        }
        return make(
            "reject",
            "reject",
            "none",
            {"secondary_capture_not_axial_volume_candidate"},
            "secondary_capture_not_axial_volume_candidate",
            "exclude_series",
            false,
            "Reject. Secondary-capture rescue is allowed only for explicit axial-looking stacks.");
    }

    const bool is_ct = modality_upper == "CT";
    const bool image_type_original_or_unspecified =
        contains_word(image_type_text, "ORIGINAL") || image_type_text.empty();
    const bool is_enhanced_ct = first.sop_class_uid == "1.2.840.10008.5.1.4.1.1.2.1"
        || contains_word(sop_name_upper, "ENHANCED CT");

    if (is_enhanced_ct || (modality_upper == "CT" && max_number_of_frames >= kMinVolumeSlices)) {
        std::vector<std::string> reasons{"enhanced_or_multiframe_ct", "native_gdcm_decode_ok"};
        if (compressed_count > 0) {
            reasons.push_back("compressed_transfer_syntax");
        }
        if (first.geometry_from_functional_groups) {
            reasons.push_back("functional_group_geometry_detected");
        }
        return make(
            "enhanced_ct_geometry_unverified",
            "geometry validation required",
            "none",
            reasons,
            "per_frame_geometry_not_fully_validated",
            "validate_all_per_frame_functional_groups_or_request_original_export",
            false,
            "GDCM decoded the image, but every per-frame position/orientation must be validated before clean conversion.");
    }

    const bool geometry_ok =
        pixel_spacing_count == file_count
        && position_count == file_count
        && orientation_count == file_count
        && file_count >= kMinVolumeSlices
        && shape_consistent
        && pixel_spacing_consistent
        && orientation_consistent;

    if (is_ct && geometry_ok) {
        std::vector<std::string> reasons{"ct_geometry_tags_present"};
        if (compressed_count > 0) {
            reasons.push_back("compressed_transfer_syntax");
            reasons.push_back("native_gdcm_decode_ok");
        }
        if (!image_type_original_or_unspecified) {
            reasons.push_back("image_type_not_original_but_geometry_complete");
        }
        return make(
            "original_ct_geometry_ok",
            "A: clean CT geometry",
            "A: clean CT",
            reasons,
            "",
            "convert_clean",
            false,
            compressed_count > 0
                ? "Transcode losslessly with embedded GDCM, convert with dcm2niix, then review MPR/FOV."
                : "Convert with dcm2niix, review MPR/FOV, then run the NIfTI path.");
    }

    if (modality_upper == "CT") {
        std::vector<std::string> reasons;
        if (pixel_spacing_count != file_count) {
            reasons.push_back("missing_pixel_spacing");
        }
        if (position_count != file_count) {
            reasons.push_back("missing_or_incomplete_image_position_patient");
        }
        if (orientation_count != file_count) {
            reasons.push_back("missing_or_incomplete_image_orientation_patient");
        }
        if (!shape_consistent) {
            reasons.push_back("mixed_rows_or_columns");
        }
        if (!pixel_spacing_consistent) {
            reasons.push_back("mixed_pixel_spacing");
        }
        if (!orientation_consistent) {
            reasons.push_back("mixed_image_orientation_patient");
        }
        if (file_count < kMinVolumeSlices) {
            reasons.push_back("too_few_slices");
        }
        if (has_viewer_export_rescue_candidate(series)) {
            std::vector<std::string> viewer_reasons = reasons;
            viewer_reasons.push_back("viewer_export_mpr_mixed_candidate");
            viewer_reasons.push_back("not_original_axial_ct");
            return make(
                "viewer_export_mpr_mixed_candidate",
                "C: viewer export rescue candidate",
                "C: rescue only",
                viewer_reasons,
                "",
                "select_viewer_export_group",
                false,
                "Viewer/MPR export-like mixed geometry. Select one geometry group for non-diagnostic preview rescue; prefer original axial CT if available.");
        }
        return make(
            "reject",
            "reject",
            "none",
            reasons.empty() ? std::vector<std::string>{"ct_geometry_incomplete"} : reasons,
            reasons.empty() ? "ct_geometry_incomplete" : join(reasons, ","),
            "request_original_axial_ct_export",
            false,
            "Prefer a fresh original axial CT export or handle in a dedicated normalizer path.");
    }

    return make(
        "reject",
        "reject",
        "none",
        {"unsupported_modality"},
        "unsupported_modality",
        "exclude_series",
        false,
        "Unsupported for the dental DICOM normalizer intake path.");
}

std::string json_array_strings(const std::vector<std::string>& values) {
    std::ostringstream out;
    out << "[";
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index > 0) {
            out << ", ";
        }
        out << json_string(values[index]);
    }
    out << "]";
    return out.str();
}

std::vector<std::string> mpr_preview_paths(const std::vector<MprPreviewInfo>& previews) {
    std::vector<std::string> paths;
    for (const auto& preview : previews) {
        if (!preview.path.empty()) {
            paths.push_back(preview.path.string());
        }
    }
    return paths;
}

std::string mpr_preview_info_array_json(const std::vector<MprPreviewInfo>& previews, int indent) {
    const std::string pad(indent, ' ');
    const std::string pad2(indent + 2, ' ');
    std::ostringstream out;
    out << "[";
    for (std::size_t index = 0; index < previews.size(); ++index) {
        const auto& preview = previews[index];
        if (index > 0) {
            out << ",";
        }
        out << "\n" << pad2 << "{";
        out << "\"plane\": " << json_string(preview.plane) << ", ";
        out << "\"path\": " << json_string(preview.path.string()) << ", ";
        out << "\"width\": " << preview.width << ", ";
        out << "\"height\": " << preview.height << ", ";
        out << "\"min\": " << preview.min_value << ", ";
        out << "\"max\": " << preview.max_value << ", ";
        out << "\"uniform_or_empty\": " << json_bool(preview.uniform_or_empty);
        out << "}";
    }
    if (!previews.empty()) {
        out << "\n" << pad;
    }
    out << "]";
    return out.str();
}

std::vector<uint8_t> read_file_with_limit(const fs::path& path, std::size_t limit_bytes) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open file: " + path.string());
    }
    std::vector<uint8_t> data;
    in.seekg(0, std::ios::end);
    const auto size = in.tellg();
    if (size < 0) {
        throw std::runtime_error("cannot size file: " + path.string());
    }
    if (static_cast<std::uintmax_t>(size) > limit_bytes) {
        throw std::runtime_error("file exceeds read limit: " + path.string());
    }
    data.resize(static_cast<std::size_t>(size));
    in.seekg(0, std::ios::beg);
    in.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(data.size()));
    return data;
}

std::string normalize_dicomdir_file_id(std::string value) {
    value = trim_nulls(std::move(value));
    while (!value.empty() && (value.back() == ' ' || value.back() == '\0')) {
        value.pop_back();
    }
    while (!value.empty() && (value.front() == ' ' || value.front() == '\0')) {
        value.erase(value.begin());
    }
    return value;
}

std::vector<std::string> parse_dicomdir_referenced_file_ids(const fs::path& dicomdir_path) {
    std::vector<std::string> result;
    std::set<std::string> seen;
    std::vector<uint8_t> data;
    try {
        data = read_file_with_limit(dicomdir_path, kDicomdirReadLimitBytes);
    } catch (...) {
        return result;
    }

    for (std::size_t offset = 0; offset + 8 <= data.size(); ++offset) {
        if (read_u16_le(data, offset) != 0x0004 || read_u16_le(data, offset + 2) != 0x1500) {
            continue;
        }

        uint32_t length = 0;
        std::size_t value_offset = 0;
        const bool explicit_vr =
            std::isalpha(data[offset + 4]) && std::isalpha(data[offset + 5]);
        if (explicit_vr) {
            const std::string vr(reinterpret_cast<const char*>(data.data() + offset + 4), 2);
            if (long_vr(vr)) {
                if (offset + 12 > data.size()) {
                    continue;
                }
                length = read_u32_le(data, offset + 8);
                value_offset = offset + 12;
            } else {
                length = read_u16_le(data, offset + 6);
                value_offset = offset + 8;
            }
        } else {
            length = read_u32_le(data, offset + 4);
            value_offset = offset + 8;
        }
        if (length == 0 || length == 0xFFFFFFFFU || value_offset + length > data.size()) {
            continue;
        }
        std::string value = normalize_dicomdir_file_id(
            std::string(reinterpret_cast<const char*>(data.data() + value_offset), length));
        if (!value.empty() && seen.insert(value).second) {
            result.push_back(value);
        }
    }
    return result;
}

fs::path resolve_dicomdir_file_id(const fs::path& base_dir, const std::string& file_id) {
    fs::path path = base_dir;
    for (const auto& part : split_backslash(file_id)) {
        path /= part;
    }
    if (fs::exists(path)) {
        return path;
    }
    fs::path slash_path = base_dir / fs::path(file_id);
    if (fs::exists(slash_path)) {
        return slash_path;
    }
    return {};
}

DicomdirSummary audit_dicomdirs(const fs::path& dir) {
    DicomdirSummary summary;
    std::set<std::string> referenced;
    std::set<fs::path> resolved;
    for (const auto& entry : fs::recursive_directory_iterator(dir)) {
        if (!entry.is_regular_file()) {
            continue;
        }
        if (upper(entry.path().filename().string()) != "DICOMDIR") {
            continue;
        }
        DicomMeta meta = parse_dicom_file(entry.path());
        if (meta.sop_class_uid != "1.2.840.10008.1.3.10"
            && !contains_word(upper(meta.sop_class_name), "MEDIA STORAGE DIRECTORY")) {
            continue;
        }
        ++summary.dicomdir_file_count;
        for (const auto& file_id : parse_dicomdir_referenced_file_ids(entry.path())) {
            referenced.insert(file_id);
            fs::path resolved_path = resolve_dicomdir_file_id(entry.path().parent_path(), file_id);
            if (!resolved_path.empty()) {
                resolved.insert(fs::weakly_canonical(resolved_path));
            }
        }
    }
    summary.referenced_file_ids.assign(referenced.begin(), referenced.end());
    summary.resolved_reference_count = static_cast<int>(resolved.size());
    summary.missing_reference_count =
        static_cast<int>(summary.referenced_file_ids.size()) - summary.resolved_reference_count;
    if (summary.missing_reference_count < 0) {
        summary.missing_reference_count = 0;
    }
    return summary;
}

std::string viewer_export_group_json(const ViewerExportGroup& group, int indent) {
    const std::string pad(indent, ' ');
    const std::string pad2(indent + 2, ' ');
    const std::string pad4(indent + 4, ' ');
    const auto row = normalize3(row_cosines(group.orientation));
    const auto column = normalize3(column_cosines(group.orientation));
    const double spacing_ratio = std::min(group.pixel_spacing[0], group.pixel_spacing[1]) > 0.0
        ? group.slice_spacing_median / std::min(group.pixel_spacing[0], group.pixel_spacing[1])
        : 0.0;

    std::ostringstream out;
    out << pad << "{\n";
    out << pad2 << "\"group_id\": " << json_string(group.id) << ",\n";
    out << pad2 << "\"plane_label\": " << json_string(group.plane_label) << ",\n";
    out << pad2 << "\"recommendation\": " << json_string(group.recommendation) << ",\n";
    out << pad2 << "\"ai_eligibility\": {\n";
    out << pad4 << "\"status\": " << json_string(group.ai_eligibility) << ",\n";
    out << pad4 << "\"requires_user_confirmation\": "
        << json_bool(group.ai_eligibility == "rescue_go_with_warning") << ",\n";
    out << pad4 << "\"reasons\": " << json_array_strings(group.reasons) << "\n";
    out << pad2 << "},\n";
    out << pad2 << "\"file_count\": " << group.files.size() << ",\n";
    out << pad2 << "\"shape\": {\"rows\": " << group.shape[0]
        << ", \"columns\": " << group.shape[1] << "},\n";
    out << pad2 << "\"pixel_spacing_mm\": {\"row\": " << group.pixel_spacing[0]
        << ", \"column\": " << group.pixel_spacing[1] << "},\n";
    out << pad2 << "\"pixel_spacing_mm_array\": " << json_number_array2(group.pixel_spacing) << ",\n";
    out << pad2 << "\"slice_spacing_mm\": {\"median\": " << group.slice_spacing_median
        << ", \"min\": " << group.slice_spacing_min
        << ", \"max\": " << group.slice_spacing_max
        << ", \"source\": \"computed_from_ipp\"},\n";
    out << pad2 << "\"spacing_ratio\": {\"slice_to_min_inplane\": " << spacing_ratio << "},\n";
    out << pad2 << "\"fov_mm\": {\"row\": " << group.fov_row_mm
        << ", \"column\": " << group.fov_column_mm
        << ", \"through_plane_center_to_center\": " << group.fov_through_plane_mm << "},\n";
    out << pad2 << "\"orientation\": {\n";
    out << pad4 << "\"iop\": " << json_number_array6(group.orientation) << ",\n";
    out << pad4 << "\"row_cosines\": " << json_number_array3(row) << ",\n";
    out << pad4 << "\"column_cosines\": " << json_number_array3(column) << ",\n";
    out << pad4 << "\"normal\": " << json_number_array3(group.normal) << "\n";
    out << pad2 << "},\n";
    out << pad2 << "\"instances\": {\"instance_number_min\": " << group.instance_min
        << ", \"instance_number_max\": " << group.instance_max
        << ", \"instance_number_contiguous\": " << json_bool(group.instance_contiguous) << "},\n";
    out << pad2 << "\"geometry_checks\": {\n";
    out << pad4 << "\"volume_like\": " << json_bool(group.volume_like) << ",\n";
    out << pad4 << "\"uniform_spacing\": " << json_bool(group.uniform_spacing) << ",\n";
    out << pad4 << "\"duplicate_positions\": " << json_bool(group.duplicate_positions) << ",\n";
    out << pad4 << "\"in_plane_drift_ok\": " << json_bool(group.in_plane_drift_ok) << ",\n";
    out << pad4 << "\"non_parallel_slices\": " << json_bool(group.non_parallel_slices) << "\n";
    out << pad2 << "},\n";
    out << pad2 << "\"quality_warnings\": "
        << json_array_strings(group.ai_eligibility == "rescue_go_with_warning"
                                  ? std::vector<std::string>{"viewer_export_rescue",
                                                             "not_original_axial_ct",
                                                             "non_diagnostic_preview"}
                                  : std::vector<std::string>{"not_original_axial_ct",
                                                             "preview_only_or_no_go"})
        << "\n";
    out << pad << "}";
    return out.str();
}

std::string viewer_export_groups_json(const std::vector<ViewerExportGroup>& groups, int indent) {
    const std::string pad(indent, ' ');
    std::ostringstream out;
    out << pad << "[";
    if (!groups.empty()) {
        out << "\n";
        for (std::size_t index = 0; index < groups.size(); ++index) {
            if (index > 0) {
                out << ",\n";
            }
            out << viewer_export_group_json(groups[index], indent + 2);
        }
        out << "\n" << pad;
    }
    out << "]";
    return out.str();
}

std::string numeric_tag_evidence_json(
    const SeriesSummary& series,
    bool DicomMeta::*has_value,
    const std::optional<double> DicomMeta::*value
) {
    int present_count = 0;
    std::vector<double> values;
    for (const auto& item : series.files) {
        present_count += item.*has_value ? 1 : 0;
        if ((item.*value).has_value()) {
            values.push_back(*(item.*value));
        }
    }
    std::sort(values.begin(), values.end());
    std::vector<double> unique_values;
    for (const double candidate : values) {
        if (unique_values.empty() || std::abs(candidate - unique_values.back()) > 1e-6) {
            unique_values.push_back(candidate);
        }
    }
    const bool consistent =
        present_count > 0
        && static_cast<int>(values.size()) == present_count
        && unique_values.size() == 1;

    std::ostringstream out;
    out << "{"
        << "\"present_count\": " << present_count
        << ", \"valid_numeric_count\": " << values.size()
        << ", \"consistent\": " << json_bool(consistent)
        << ", \"values_mm\": [";
    for (std::size_t index = 0; index < unique_values.size(); ++index) {
        if (index > 0) {
            out << ", ";
        }
        out << unique_values[index];
    }
    out << "]}";
    return out.str();
}

std::string ordered_content_manifest_json(const SeriesSummary& series, int indent) {
    struct Entry {
        std::optional<int> instance_number;
        std::string content_sha256;
    };
    std::vector<Entry> entries;
    entries.reserve(series.files.size());
    for (const auto& file : series.files) {
        entries.push_back({file.instance_number, file.content_sha256});
    }
    std::sort(entries.begin(), entries.end(), [](const Entry& lhs, const Entry& rhs) {
        if (lhs.instance_number.has_value() != rhs.instance_number.has_value()) {
            return lhs.instance_number.has_value();
        }
        if (lhs.instance_number != rhs.instance_number) {
            return lhs.instance_number < rhs.instance_number;
        }
        return lhs.content_sha256 < rhs.content_sha256;
    });

    bool ordering_ambiguous = false;
    std::set<int> seen_instances;
    std::ostringstream canonical;
    for (const auto& entry : entries) {
        if (!entry.instance_number.has_value()
            || !seen_instances.insert(*entry.instance_number).second) {
            ordering_ambiguous = true;
        }
        canonical << "I:";
        if (entry.instance_number.has_value()) {
            canonical << *entry.instance_number;
        } else {
            canonical << "null";
        }
        canonical << "\tH:" << entry.content_sha256 << "\n";
    }

    const std::string pad(indent, ' ');
    const std::string pad2(indent + 2, ' ');
    const std::string pad4(indent + 4, ' ');
    std::ostringstream out;
    out << "{\n";
    out << pad2 << "\"algorithm\": \"sha256\",\n";
    out << pad2 << "\"ordering\": \"instance_number_then_content_sha256\",\n";
    out << pad2 << "\"ordering_ambiguous\": " << json_bool(ordering_ambiguous) << ",\n";
    out << pad2 << "\"entry_count\": " << entries.size() << ",\n";
    out << pad2 << "\"manifest_sha256\": "
        << json_string(dicom_normalizer::sha256_hex(canonical.str())) << ",\n";
    out << pad2 << "\"entries\": [";
    if (!entries.empty()) {
        out << "\n";
        for (std::size_t index = 0; index < entries.size(); ++index) {
            out << pad4 << "{\"ordinal\": " << (index + 1)
                << ", \"instance_number\": " << json_optional_int(entries[index].instance_number)
                << ", \"content_sha256\": " << json_string(entries[index].content_sha256)
                << "}";
            if (index + 1 != entries.size()) {
                out << ",";
            }
            out << "\n";
        }
        out << pad2;
    }
    out << "]\n";
    out << pad << "}";
    return out.str();
}

std::string summary_json(const SeriesSummary& series, const OptionalTools& tools, int indent) {
    const DicomMeta& first = series.files.front();
    const auto classification = classify_series(series, tools);
    const auto viewer_groups = build_viewer_export_groups(series);
    bool shape_consistent = true;
    bool pixel_spacing_consistent = true;
    bool orientation_consistent = true;
    int pixel_spacing_count = 0;
    int position_count = 0;
    int orientation_count = 0;
    int dicm_prefix_count = 0;
    int file_meta_count = 0;
    int pixel_data_count = 0;
    int compressed_transfer_syntax_count = 0;
    int pixel_decode_attempted_count = 0;
    int pixel_decode_ok_count = 0;
    int functional_group_geometry_count = 0;
    int max_number_of_frames = 0;
    std::map<std::string, int> burned_in_counts;
    std::map<std::string, int> transfer_syntax_counts;
    for (const auto& item : series.files) {
        if (item.rows != first.rows || item.columns != first.columns) {
            shape_consistent = false;
        }
        if (item.has_pixel_spacing && first.has_pixel_spacing
            && !same_array2(item.pixel_spacing, first.pixel_spacing)) {
            pixel_spacing_consistent = false;
        }
        if (item.has_image_orientation_patient && first.has_image_orientation_patient
            && !same_array6(item.image_orientation_patient, first.image_orientation_patient)) {
            orientation_consistent = false;
        }
        dicm_prefix_count += item.has_dicm_prefix ? 1 : 0;
        file_meta_count += item.has_file_meta ? 1 : 0;
        pixel_spacing_count += item.has_pixel_spacing ? 1 : 0;
        position_count += item.has_image_position_patient ? 1 : 0;
        orientation_count += item.has_image_orientation_patient ? 1 : 0;
        pixel_data_count += item.has_pixel_data ? 1 : 0;
        compressed_transfer_syntax_count += is_compressed_transfer_syntax(item.transfer_syntax_uid) ? 1 : 0;
        pixel_decode_attempted_count += item.pixel_decode_attempted ? 1 : 0;
        pixel_decode_ok_count += item.pixel_decode_ok ? 1 : 0;
        functional_group_geometry_count += item.geometry_from_functional_groups ? 1 : 0;
        max_number_of_frames = std::max(max_number_of_frames, item.number_of_frames.value_or(0));
        burned_in_counts[item.burned_in_annotation.empty() ? "unknown" : item.burned_in_annotation] += 1;
        transfer_syntax_counts[item.transfer_syntax_uid.empty() ? "unknown" : item.transfer_syntax_uid] += 1;
    }

    const std::string pad(indent, ' ');
    const std::string pad2(indent + 2, ' ');
    const std::string pad4(indent + 4, ' ');
    std::ostringstream out;
    out << pad << "{\n";
    out << pad2 << "\"series_key\": " << json_string(series.key) << ",\n";
    out << pad2 << "\"series_instance_uid\": " << json_optional_string(first.series_instance_uid) << ",\n";
    out << pad2 << "\"series_number\": " << json_optional_int(first.series_number) << ",\n";
    out << pad2 << "\"series_description\": " << json_optional_string(first.series_description) << ",\n";
    out << pad2 << "\"modality\": " << json_optional_string(first.modality) << ",\n";
    out << pad2 << "\"sop_class_uid\": " << json_optional_string(first.sop_class_uid) << ",\n";
    out << pad2 << "\"sop_class_name\": " << json_optional_string(first.sop_class_name) << ",\n";
    out << pad2 << "\"transfer_syntax_uid\": " << json_optional_string(first.transfer_syntax_uid) << ",\n";
    out << pad2 << "\"transfer_syntax_name\": " << json_optional_string(transfer_syntax_name(first.transfer_syntax_uid)) << ",\n";
    out << pad2 << "\"compressed_transfer_syntax\": " << json_bool(is_compressed_transfer_syntax(first.transfer_syntax_uid)) << ",\n";
    out << pad2 << "\"parser_backend\": " << json_string(first.parser_backend) << ",\n";
    out << pad2 << "\"pixel_decode_attempted_count\": " << pixel_decode_attempted_count << ",\n";
    out << pad2 << "\"pixel_decode_ok_count\": " << pixel_decode_ok_count << ",\n";
    out << pad2 << "\"pixel_decode_failure_count\": "
        << (pixel_decode_attempted_count - pixel_decode_ok_count) << ",\n";
    out << pad2 << "\"decoded_bytes_first\": " << first.decoded_bytes << ",\n";
    out << pad2 << "\"decoded_fnv1a64_first\": "
        << json_optional_string(first.pixel_decode_ok ? hex_u64(first.decoded_fnv1a64) : std::string{}) << ",\n";
    out << pad2 << "\"functional_group_geometry_file_count\": "
        << functional_group_geometry_count << ",\n";
    out << pad2 << "\"image_type\": " << json_array_strings(first.image_type) << ",\n";
    out << pad2 << "\"file_count\": " << series.files.size() << ",\n";
    out << pad2 << "\"dicm_prefix_count\": " << dicm_prefix_count << ",\n";
    out << pad2 << "\"file_meta_count\": " << file_meta_count << ",\n";
    out << pad2 << "\"rows\": " << json_optional_int(first.rows) << ",\n";
    out << pad2 << "\"columns\": " << json_optional_int(first.columns) << ",\n";
    out << pad2 << "\"number_of_frames_max\": " << max_number_of_frames << ",\n";
    out << pad2 << "\"effective_frame_count\": " << std::max(static_cast<int>(series.files.size()), max_number_of_frames) << ",\n";
    out << pad2 << "\"has_pixel_data_count\": " << pixel_data_count << ",\n";
    out << pad2 << "\"compressed_transfer_syntax_count\": " << compressed_transfer_syntax_count << ",\n";
    out << pad2 << "\"samples_per_pixel\": " << json_optional_int(first.samples_per_pixel) << ",\n";
    out << pad2 << "\"photometric_interpretation\": " << json_optional_string(first.photometric_interpretation) << ",\n";
    out << pad2 << "\"bits_allocated\": " << json_optional_int(first.bits_allocated) << ",\n";
    out << pad2 << "\"pixel_representation\": " << json_optional_int(first.pixel_representation) << ",\n";
    out << pad2 << "\"shape_consistent\": " << json_bool(shape_consistent) << ",\n";
    out << pad2 << "\"pixel_spacing_consistent\": " << json_bool(pixel_spacing_consistent) << ",\n";
    out << pad2 << "\"image_orientation_patient_consistent\": " << json_bool(orientation_consistent) << ",\n";
    out << pad2 << "\"has_pixel_spacing\": " << json_bool(pixel_spacing_count == static_cast<int>(series.files.size())) << ",\n";
    out << pad2 << "\"pixel_spacing_count\": " << pixel_spacing_count << ",\n";
    out << pad2 << "\"image_position_patient_count\": " << position_count << ",\n";
    out << pad2 << "\"image_orientation_patient_count\": " << orientation_count << ",\n";
    out << pad2 << "\"slice_thickness\": "
        << numeric_tag_evidence_json(
               series, &DicomMeta::has_slice_thickness, &DicomMeta::slice_thickness)
        << ",\n";
    out << pad2 << "\"spacing_between_slices\": "
        << numeric_tag_evidence_json(
               series,
               &DicomMeta::has_spacing_between_slices,
               &DicomMeta::spacing_between_slices)
        << ",\n";
    out << pad2 << "\"ordered_content_manifest\": "
        << ordered_content_manifest_json(series, indent + 2) << ",\n";
    out << pad2 << "\"burned_in_annotation_counts\": {";
    bool first_count = true;
    for (const auto& [key, value] : burned_in_counts) {
        if (!first_count) {
            out << ", ";
        }
        first_count = false;
        out << json_string(key) << ": " << value;
    }
    out << "},\n";
    out << pad2 << "\"transfer_syntax_counts\": {";
    bool first_transfer = true;
    for (const auto& [key, value] : transfer_syntax_counts) {
        if (!first_transfer) {
            out << ", ";
        }
        first_transfer = false;
        out << json_string(key) << ": " << value;
    }
    out << "},\n";
    out << pad2 << "\"classification\": {\n";
    out << pad4 << "\"status\": " << json_string(classification.status) << ",\n";
    out << pad4 << "\"grade\": " << json_string(classification.grade) << ",\n";
    out << pad4 << "\"rescue_grade\": " << json_string(classification.rescue_grade) << ",\n";
    out << pad4 << "\"reasons\": " << json_array_strings(classification.reasons) << ",\n";
    out << pad4 << "\"reject_reason\": " << json_optional_string(classification.reject_reason) << ",\n";
    out << pad4 << "\"next_action\": " << json_string(classification.next_action) << ",\n";
    out << pad4 << "\"requires_external_tool\": " << json_bool(classification.requires_external_tool) << ",\n";
    out << pad4 << "\"recommendation\": " << json_string(classification.recommendation) << "\n";
    out << pad2 << "},\n";
    out << pad2 << "\"viewer_export_groups\": "
        << viewer_export_groups_json(viewer_groups, indent + 2) << "\n";
    out << pad << "}";
    return out.str();
}

std::string optional_tools_json(const OptionalTools& tools, int indent) {
    const std::string pad(indent, ' ');
    const std::string pad2(indent + 2, ' ');
    std::ostringstream out;
    out << pad << "{\n";
    out << pad2 << "\"gdcmconv\": {\"available\": " << json_bool(tools.gdcmconv.has_value())
        << ", \"path\": " << json_path_optional(tools.gdcmconv) << "},\n";
    out << pad2 << "\"dcmdjpeg\": {\"available\": " << json_bool(tools.dcmdjpeg.has_value())
        << ", \"path\": " << json_path_optional(tools.dcmdjpeg) << "},\n";
    out << pad2 << "\"dcmconv\": {\"available\": " << json_bool(tools.dcmconv.has_value())
        << ", \"path\": " << json_path_optional(tools.dcmconv) << "},\n";
    out << pad2 << "\"any_transcoder\": " << json_bool(tools.any_transcoder()) << "\n";
    out << pad << "}";
    return out.str();
}

std::string doctor_json(const OptionalTools& tools) {
    std::ostringstream out;
    out << "{\n";
    out << "  \"schema\": \"totalsegmentator_wrapper_mac.dicom_normalizer.doctor.v1\",\n";
    out << "  \"tool\": {\"name\": \"totalsegmentator-wrapper-dicom-normalizer\", \"version\": "
        << json_string(std::string(kVersion)) << "},\n";
    out << "  \"dicom_backend\": {\"name\": \"GDCM\", \"version\": "
        << json_string(dicom_normalizer::gdcm_version()) << "},\n";
    out << "  \"optional_tools\": " << optional_tools_json(tools, 2) << ",\n";
    out << "  \"capabilities\": {\n";
    out << "    \"audit\": true,\n";
    out << "    \"convert_clean\": true,\n";
    out << "    \"prepare_rescue\": true,\n";
    out << "    \"export_rescue_stack\": true,\n";
    out << "    \"prepare_viewer_export\": true,\n";
    out << "    \"native_compressed_pixel_decode\": true,\n";
    out << "    \"native_lossless_transcode\": true,\n";
    out << "    \"enhanced_ct_per_frame_geometry_validation\": false,\n";
    out << "    \"external_transcode_adapter_available\": "
        << json_bool(tools.any_transcoder()) << "\n";
    out << "  },\n";
    out << "  \"status\": \"ok\"\n";
    out << "}\n";
    return out.str();
}

std::string dicomdir_json(const DicomdirSummary& summary, int indent) {
    const std::string pad(indent, ' ');
    const std::string pad2(indent + 2, ' ');
    std::ostringstream out;
    out << pad << "{\n";
    out << pad2 << "\"dicomdir_file_count\": " << summary.dicomdir_file_count << ",\n";
    out << pad2 << "\"referenced_file_ids\": " << json_array_strings(summary.referenced_file_ids) << ",\n";
    out << pad2 << "\"resolved_reference_count\": " << summary.resolved_reference_count << ",\n";
    out << pad2 << "\"missing_reference_count\": " << summary.missing_reference_count << "\n";
    out << pad << "}";
    return out.str();
}

std::string audit_json(
    const fs::path& dicom_dir,
    const std::vector<SeriesSummary>& series,
    int skipped,
    const OptionalTools& tools,
    const DicomdirSummary& dicomdir
) {
    std::map<std::string, int> counts;
    int file_count = 0;
    for (const auto& item : series) {
        file_count += static_cast<int>(item.files.size());
        counts[classify_series(item, tools).status] += 1;
    }

    std::ostringstream out;
    out << "{\n";
    out << "  \"schema\": \"totalsegmentator_wrapper_mac.dicom_normalizer.audit.v1\",\n";
    out << "  \"tool\": {\"name\": \"totalsegmentator-wrapper-dicom-normalizer\", \"version\": "
        << json_string(std::string(kVersion)) << "},\n";
    out << "  \"dicom_backend\": {\"name\": \"GDCM\", \"version\": "
        << json_string(dicom_normalizer::gdcm_version()) << "},\n";
    out << "  \"dicom_dir\": {\n";
    out << "    \"basename\": " << json_string(dicom_dir.filename().string()) << ",\n";
    out << "    \"path_hash_fnv1a64\": " << json_string(path_hash_fnv1a64(dicom_dir)) << "\n";
    out << "  },\n";
    out << "  \"file_count\": " << file_count << ",\n";
    out << "  \"skipped_file_count\": " << skipped << ",\n";
    out << "  \"series_count\": " << series.size() << ",\n";
    out << "  \"optional_tools\": " << optional_tools_json(tools, 2) << ",\n";
    out << "  \"dicomdir\": " << dicomdir_json(dicomdir, 2) << ",\n";
    out << "  \"classification_counts\": {";
    bool first_count = true;
    for (const auto& [key, value] : counts) {
        if (!first_count) {
            out << ", ";
        }
        first_count = false;
        out << json_string(key) << ": " << value;
    }
    out << "},\n";
    out << "  \"series\": [\n";
    for (std::size_t index = 0; index < series.size(); ++index) {
        out << summary_json(series[index], tools, 4);
        if (index + 1 != series.size()) {
            out << ",";
        }
        out << "\n";
    }
    out << "  ],\n";
    out << "  \"product_boundary\": {\n";
    out << "    \"phase\": \"gdcm_robust_intake\",\n";
    out << "    \"pixel_data_inspected\": true,\n";
    out << "    \"volume_written\": false,\n";
    out << "    \"secondary_capture_rescue_written\": false\n";
    out << "  }\n";
    out << "}\n";
    return out.str();
}

std::vector<SeriesSummary> audit_directory(const fs::path& dir, int& skipped) {
    if (!fs::exists(dir)) {
        throw std::runtime_error("DICOM directory does not exist: " + dir.string());
    }
    if (!fs::is_directory(dir)) {
        throw std::runtime_error("Not a directory: " + dir.string());
    }

    std::map<std::string, SeriesSummary> grouped;
    skipped = 0;
    for (const auto& entry : fs::recursive_directory_iterator(dir)) {
        if (!entry.is_regular_file()) {
            continue;
        }
        DicomMeta meta = parse_dicom_file(entry.path());
        if (!meta.parsed) {
            ++skipped;
            continue;
        }
        const std::string key = series_key(meta);
        auto& summary = grouped[key];
        summary.key = key;
        summary.files.push_back(std::move(meta));
    }

    std::vector<SeriesSummary> result;
    result.reserve(grouped.size());
    for (auto& [_, summary] : grouped) {
        result.push_back(std::move(summary));
    }
    std::sort(result.begin(), result.end(), [](const SeriesSummary& lhs, const SeriesSummary& rhs) {
        const auto lhs_number = lhs.files.front().series_number.value_or(999999);
        const auto rhs_number = rhs.files.front().series_number.value_or(999999);
        if (lhs_number != rhs_number) {
            return lhs_number < rhs_number;
        }
        return lhs.files.front().series_description < rhs.files.front().series_description;
    });
    return result;
}

void write_text(const fs::path& path, const std::string& text) {
    if (!path.parent_path().empty()) {
        fs::create_directories(path.parent_path());
    }
    std::ofstream out(path);
    if (!out) {
        throw std::runtime_error("cannot write output: " + path.string());
    }
    out << text;
}

std::optional<SeriesSummary> find_series_by_number(
    const std::vector<SeriesSummary>& series,
    int series_number
) {
    std::vector<SeriesSummary> matches;
    for (const auto& item : series) {
        if (item.files.front().series_number == series_number) {
            matches.push_back(item);
        }
    }
    if (matches.empty()) {
        return std::nullopt;
    }
    std::sort(matches.begin(), matches.end(), [](const SeriesSummary& lhs, const SeriesSummary& rhs) {
        return lhs.files.size() > rhs.files.size();
    });
    return matches.front();
}

std::optional<SeriesSummary> find_series_by_key(
    const std::vector<SeriesSummary>& series,
    const std::string& key
) {
    if (key.empty()) {
        return std::nullopt;
    }
    for (const auto& item : series) {
        if (item.key == key) {
            return item;
        }
    }
    return std::nullopt;
}

std::optional<SeriesSummary> find_requested_series(
    const std::vector<SeriesSummary>& series,
    const Args& args
) {
    if (args.series_number.has_value()) {
        return find_series_by_number(series, *args.series_number);
    }
    return find_series_by_key(series, args.series_key);
}

std::optional<ViewerExportGroup> find_viewer_export_group(
    const SeriesSummary& series,
    const std::string& group_id
) {
    const auto groups = build_viewer_export_groups(series);
    for (const auto& group : groups) {
        if (group.id == group_id) {
            return group;
        }
    }
    return std::nullopt;
}

std::string requested_series_description(const Args& args) {
    if (args.series_number.has_value()) {
        return "series number " + std::to_string(*args.series_number);
    }
    if (!args.series_key.empty()) {
        return "series key " + args.series_key;
    }
    return "no series selector";
}

fs::path find_dcm2niix(const fs::path& explicit_path) {
    if (!explicit_path.empty()) {
        return explicit_path;
    }
    if (const char* env = std::getenv("TOTALSEGMENTATOR_WRAPPER_MAC_DCM2NIIX")) {
        return fs::path(env);
    }
    for (const auto& candidate : {
             fs::path("/opt/homebrew/bin/dcm2niix"),
             fs::path("/usr/local/bin/dcm2niix"),
         }) {
        if (fs::exists(candidate)) {
            return candidate;
        }
    }
    return fs::path("dcm2niix");
}

std::vector<fs::path> isolate_series(const SeriesSummary& series, const fs::path& isolated_dir) {
    fs::create_directories(isolated_dir);
    std::vector<fs::path> copied;
    int index = 1;
    for (const auto& file : series.files) {
        std::ostringstream name;
        name << std::setw(6) << std::setfill('0') << index++ << ".dcm";
        fs::path destination = isolated_dir / name.str();
        if (is_compressed_transfer_syntax(file.transfer_syntax_uid)) {
            std::string error;
            if (!dicom_normalizer::transcode_to_explicit_little_endian(
                    file.source_path, destination, error)) {
                throw std::runtime_error(
                    "GDCM lossless transcode failed for selected series: " + error);
            }
        } else {
            fs::copy_file(
                file.source_path,
                destination,
                fs::copy_options::overwrite_existing);
        }
        copied.push_back(destination);
    }
    return copied;
}

std::vector<fs::path> isolate_viewer_export_group(
    const ViewerExportGroup& group,
    const fs::path& isolated_dir
) {
    fs::create_directories(isolated_dir);
    std::vector<fs::path> copied;
    int index = 1;
    for (const auto* file : group.files) {
        std::ostringstream name;
        name << std::setw(6) << std::setfill('0') << index++ << ".dcm";
        fs::path destination = isolated_dir / name.str();
        fs::copy_file(
            file->source_path,
            destination,
            fs::copy_options::overwrite_existing);
        copied.push_back(destination);
    }
    return copied;
}

std::vector<fs::path> find_niftis(const fs::path& dir) {
    std::vector<fs::path> candidates;
    if (!fs::exists(dir)) {
        return candidates;
    }
    for (const auto& entry : fs::directory_iterator(dir)) {
        if (!entry.is_regular_file()) {
            continue;
        }
        const auto path = entry.path();
        if (path.extension() == ".nii") {
            candidates.push_back(path);
        }
    }
    std::sort(candidates.begin(), candidates.end());
    return candidates;
}

fs::path find_first_nifti(const fs::path& dir) {
    const auto candidates = find_niftis(dir);
    return candidates.empty() ? fs::path{} : candidates.front();
}

NiftiHeaderInfo read_nifti_header(const fs::path& path) {
    NiftiHeaderInfo info;
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        info.error = "cannot_open";
        return info;
    }
    std::vector<uint8_t> header(352);
    in.read(reinterpret_cast<char*>(header.data()), static_cast<std::streamsize>(header.size()));
    if (in.gcount() < 348) {
        info.error = "header_too_small";
        return info;
    }
    if (read_u32_le(header, 0) != 348) {
        info.error = "not_little_endian_nifti1";
        return info;
    }
    info.shape = {
        read_i16_le(header, 42),
        read_i16_le(header, 44),
        read_i16_le(header, 46),
    };
    info.datatype = read_i16_le(header, 70);
    info.bitpix = read_i16_le(header, 72);
    info.spacing = {
        read_f32_le(header, 80),
        read_f32_le(header, 84),
        read_f32_le(header, 88),
    };
    info.vox_offset = read_f32_le(header, 108);
    info.qform_code = read_i16_le(header, 252);
    info.sform_code = read_i16_le(header, 254);
    info.ok = info.shape[0] > 0 && info.shape[1] > 0 && info.shape[2] > 0
        && info.spacing[0] > 0.0 && info.spacing[1] > 0.0 && info.spacing[2] > 0.0;
    if (!info.ok) {
        info.error = "invalid_shape_or_spacing";
    }
    return info;
}

bool spacing_matches(const NiftiHeaderInfo& info, const std::array<double, 3>& expected) {
    if (!info.ok) {
        return false;
    }
    for (std::size_t index = 0; index < 3; ++index) {
        if (std::abs(info.spacing[index] - expected[index]) > 1e-4) {
            return false;
        }
    }
    return true;
}

std::string nifti_header_json(const fs::path& path, const NiftiHeaderInfo& info, int indent) {
    const std::string pad(indent, ' ');
    const std::string pad2(indent + 2, ' ');
    std::ostringstream out;
    out << pad << "{\n";
    out << pad2 << "\"path\": " << json_optional_string(path.string()) << ",\n";
    out << pad2 << "\"ok\": " << json_bool(info.ok) << ",\n";
    out << pad2 << "\"error\": " << json_optional_string(info.error) << ",\n";
    out << pad2 << "\"shape\": " << json_int_array3(info.shape) << ",\n";
    out << pad2 << "\"spacing\": " << json_number_array3(info.spacing) << ",\n";
    out << pad2 << "\"datatype\": " << info.datatype << ",\n";
    out << pad2 << "\"bitpix\": " << info.bitpix << ",\n";
    out << pad2 << "\"qform_code\": " << info.qform_code << ",\n";
    out << pad2 << "\"sform_code\": " << info.sform_code << ",\n";
    out << pad2 << "\"vox_offset\": " << info.vox_offset << "\n";
    out << pad << "}";
    return out.str();
}

double nifti_voxel_value(
    const std::vector<uint8_t>& data,
    const NiftiHeaderInfo& info,
    std::size_t voxel_index
) {
    const std::size_t bytes_per_voxel = static_cast<std::size_t>(std::max(info.bitpix, 1)) / 8U;
    const std::size_t offset = static_cast<std::size_t>(std::max(info.vox_offset, 0.0))
        + voxel_index * bytes_per_voxel;
    if (offset + bytes_per_voxel > data.size()) {
        return 0.0;
    }
    if (info.datatype == 2 && bytes_per_voxel == 1) {
        return static_cast<double>(data[offset]);
    }
    if (info.datatype == 4 && bytes_per_voxel == 2) {
        return static_cast<double>(read_i16_le(data, offset));
    }
    if (info.datatype == 512 && bytes_per_voxel == 2) {
        return static_cast<double>(read_u16_le(data, offset));
    }
    if (info.datatype == 16 && bytes_per_voxel == 4) {
        return static_cast<double>(read_f32_le(data, offset));
    }
    return 0.0;
}

MprPreviewInfo write_pgm(
    const std::string& plane,
    const fs::path& path,
    int width,
    int height,
    const std::vector<double>& values
) {
    MprPreviewInfo info;
    info.plane = plane;
    info.path = path;
    info.width = width;
    info.height = height;
    if (values.empty() || width <= 0 || height <= 0) {
        return info;
    }
    auto [min_it, max_it] = std::minmax_element(values.begin(), values.end());
    double min_value = *min_it;
    double max_value = *max_it;
    info.min_value = min_value;
    info.max_value = max_value;
    info.uniform_or_empty = !(max_value > min_value);
    if (!(max_value > min_value)) {
        max_value = min_value + 1.0;
    }
    fs::create_directories(path.parent_path());
    std::ofstream out(path, std::ios::binary);
    if (!out) {
        throw std::runtime_error("cannot write PGM: " + path.string());
    }
    out << "P5\n" << width << " " << height << "\n255\n";
    for (double value : values) {
        const double scaled = (value - min_value) / (max_value - min_value);
        const auto byte = static_cast<uint8_t>(std::clamp(scaled, 0.0, 1.0) * 255.0);
        out.write(reinterpret_cast<const char*>(&byte), 1);
    }
    return info;
}

std::vector<MprPreviewInfo> write_nifti_mpr_pgm(const fs::path& nifti, const fs::path& output_dir) {
    const auto info = read_nifti_header(nifti);
    std::vector<MprPreviewInfo> written;
    if (!info.ok) {
        return written;
    }
    const std::uintmax_t file_size = fs::file_size(nifti);
    if (file_size > kPreviewReadLimitBytes) {
        return written;
    }
    if (!(info.datatype == 2 || info.datatype == 4 || info.datatype == 16 || info.datatype == 512)) {
        return written;
    }

    std::vector<uint8_t> data = read_file_with_limit(nifti, kPreviewReadLimitBytes);
    const int nx = info.shape[0];
    const int ny = info.shape[1];
    const int nz = info.shape[2];
    auto index = [nx, ny](int x, int y, int z) {
        return static_cast<std::size_t>(x + nx * (y + ny * z));
    };

    const int mid_z = nz / 2;
    std::vector<double> axial(static_cast<std::size_t>(nx * ny));
    for (int y = 0; y < ny; ++y) {
        for (int x = 0; x < nx; ++x) {
            axial[static_cast<std::size_t>(x + nx * y)] =
                nifti_voxel_value(data, info, index(x, y, mid_z));
        }
    }
    fs::path axial_path = output_dir / "mpr_axial_mid.pgm";
    written.push_back(write_pgm("axial", axial_path, nx, ny, axial));

    const int mid_y = ny / 2;
    std::vector<double> coronal(static_cast<std::size_t>(nx * nz));
    for (int z = 0; z < nz; ++z) {
        for (int x = 0; x < nx; ++x) {
            coronal[static_cast<std::size_t>(x + nx * z)] =
                nifti_voxel_value(data, info, index(x, mid_y, z));
        }
    }
    fs::path coronal_path = output_dir / "mpr_coronal_mid.pgm";
    written.push_back(write_pgm("coronal", coronal_path, nx, nz, coronal));

    const int mid_x = nx / 2;
    std::vector<double> sagittal(static_cast<std::size_t>(ny * nz));
    for (int z = 0; z < nz; ++z) {
        for (int y = 0; y < ny; ++y) {
            sagittal[static_cast<std::size_t>(y + ny * z)] =
                nifti_voxel_value(data, info, index(mid_x, y, z));
        }
    }
    fs::path sagittal_path = output_dir / "mpr_sagittal_mid.pgm";
    written.push_back(write_pgm("sagittal", sagittal_path, ny, nz, sagittal));

    return written;
}

void put_i16_le(std::vector<uint8_t>& data, std::size_t offset, int16_t value) {
    if (offset + 2 > data.size()) {
        throw std::runtime_error("NIfTI header too small for int16 write");
    }
    const auto raw = static_cast<uint16_t>(value);
    data[offset] = static_cast<uint8_t>(raw & 0xFFU);
    data[offset + 1] = static_cast<uint8_t>((raw >> 8U) & 0xFFU);
}

void put_f32_le(std::vector<uint8_t>& data, std::size_t offset, float value) {
    if (offset + 4 > data.size()) {
        throw std::runtime_error("NIfTI header too small for float write");
    }
    uint32_t raw = 0;
    static_assert(sizeof(float) == sizeof(uint32_t));
    std::memcpy(&raw, &value, sizeof(float));
    data[offset] = static_cast<uint8_t>(raw & 0xFFU);
    data[offset + 1] = static_cast<uint8_t>((raw >> 8U) & 0xFFU);
    data[offset + 2] = static_cast<uint8_t>((raw >> 16U) & 0xFFU);
    data[offset + 3] = static_cast<uint8_t>((raw >> 24U) & 0xFFU);
}

void patch_nifti_spacing_identity_affine(
    const fs::path& source_nii,
    const fs::path& output_nii,
    const std::array<double, 3>& spacing
) {
    std::ifstream in(source_nii, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open raw NIfTI: " + source_nii.string());
    }
    std::vector<uint8_t> data(
        (std::istreambuf_iterator<char>(in)),
        std::istreambuf_iterator<char>());
    if (data.size() < 352) {
        throw std::runtime_error("raw NIfTI is too small to patch");
    }
    const uint32_t sizeof_hdr = read_u32_le(data, 0);
    if (sizeof_hdr != 348) {
        throw std::runtime_error("raw NIfTI header is not little-endian NIfTI-1");
    }

    put_f32_le(data, 76, 1.0F);
    put_f32_le(data, 80, static_cast<float>(spacing[0]));
    put_f32_le(data, 84, static_cast<float>(spacing[1]));
    put_f32_le(data, 88, static_cast<float>(spacing[2]));

    put_i16_le(data, 252, 1);
    put_i16_le(data, 254, 1);
    put_f32_le(data, 256, 0.0F);
    put_f32_le(data, 260, 0.0F);
    put_f32_le(data, 264, 0.0F);
    put_f32_le(data, 268, 0.0F);
    put_f32_le(data, 272, 0.0F);
    put_f32_le(data, 276, 0.0F);

    put_f32_le(data, 280, static_cast<float>(spacing[0]));
    put_f32_le(data, 284, 0.0F);
    put_f32_le(data, 288, 0.0F);
    put_f32_le(data, 292, 0.0F);
    put_f32_le(data, 296, 0.0F);
    put_f32_le(data, 300, static_cast<float>(spacing[1]));
    put_f32_le(data, 304, 0.0F);
    put_f32_le(data, 308, 0.0F);
    put_f32_le(data, 312, 0.0F);
    put_f32_le(data, 316, 0.0F);
    put_f32_le(data, 320, static_cast<float>(spacing[2]));
    put_f32_le(data, 324, 0.0F);

    if (!output_nii.parent_path().empty()) {
        fs::create_directories(output_nii.parent_path());
    }
    std::ofstream out(output_nii, std::ios::binary);
    if (!out) {
        throw std::runtime_error("cannot write patched NIfTI: " + output_nii.string());
    }
    out.write(reinterpret_cast<const char*>(data.data()), static_cast<std::streamsize>(data.size()));
}

std::string spacing_text(const std::array<double, 3>& spacing) {
    std::ostringstream out;
    out << spacing[0] << "," << spacing[1] << "," << spacing[2];
    return out.str();
}

int run_dcm2niix(
    const fs::path& dcm2niix,
    const fs::path& isolated_dir,
    const fs::path& output_dir,
    const fs::path& log_path,
    const std::string& output_name
) {
    fs::create_directories(output_dir);
    fs::create_directories(log_path.parent_path());
    const std::string command =
        shell_quote(dcm2niix)
        + " -z n -b n -f "
        + shell_quote_string(output_name)
        + " -o "
        + shell_quote(output_dir)
        + " "
        + shell_quote(isolated_dir)
        + " > "
        + shell_quote(log_path)
        + " 2>&1";
    const int raw_status = std::system(command.c_str());
    if (raw_status == -1) {
        return 127;
    }
    if (WIFEXITED(raw_status)) {
        return WEXITSTATUS(raw_status);
    }
    return raw_status;
}

std::string rescue_metadata_json(
    const fs::path& dicom_dir,
    const SeriesSummary& series,
    const Classification& classification,
    const fs::path& isolated_dir,
    const fs::path& dcm2niix_dir,
    const fs::path& dcm2niix_log,
    const fs::path& raw_nii,
    const fs::path& patched_nii,
    const std::array<double, 3>& spacing,
    int dcm2niix_returncode
) {
    std::ostringstream out;
    out << "{\n";
    out << "  \"schema\": \"totalsegmentator_wrapper_mac.dicom_normalizer.rescue.v1\",\n";
    out << "  \"tool\": {\"name\": \"totalsegmentator-wrapper-dicom-normalizer\", \"version\": "
        << json_string(std::string(kVersion)) << "},\n";
    out << "  \"dicom_dir\": {\n";
    out << "    \"basename\": " << json_string(dicom_dir.filename().string()) << ",\n";
    out << "    \"path_hash_fnv1a64\": " << json_string(path_hash_fnv1a64(dicom_dir)) << "\n";
    out << "  },\n";
    out << "  \"selected_series\": {\n";
    out << "    \"series_number\": " << json_optional_int(series.files.front().series_number) << ",\n";
    out << "    \"series_description\": " << json_optional_string(series.files.front().series_description) << ",\n";
    out << "    \"series_instance_uid\": " << json_optional_string(series.key) << ",\n";
    out << "    \"file_count\": " << series.files.size() << ",\n";
    out << "    \"classification\": " << json_string(classification.status) << "\n";
    out << "  },\n";
    out << "  \"warnings\": {\n";
    out << "    \"secondary_capture\": true,\n";
    out << "    \"geometry_inferred\": true,\n";
    out << "    \"burned_in_annotation\": true,\n";
    out << "    \"not_segmentation_grade_original_ct\": true,\n";
    out << "    \"manual_spacing_required\": true\n";
    out << "  },\n";
    out << "  \"patched_spacing\": [" << spacing[0] << ", " << spacing[1] << ", " << spacing[2] << "],\n";
    out << "  \"outputs\": {\n";
    out << "    \"isolated_series_dir\": " << json_string(isolated_dir.string()) << ",\n";
    out << "    \"dcm2niix_dir\": " << json_string(dcm2niix_dir.string()) << ",\n";
    out << "    \"dcm2niix_log\": " << json_string(dcm2niix_log.string()) << ",\n";
    out << "    \"raw_nifti\": " << json_optional_string(raw_nii.string()) << ",\n";
    out << "    \"patched_nifti\": " << json_optional_string(patched_nii.string()) << "\n";
    out << "  },\n";
    out << "  \"dcm2niix\": {\"returncode\": " << dcm2niix_returncode << "},\n";
    out << "  \"status\": " << json_string(patched_nii.empty() ? "failed" : "success") << "\n";
    out << "}\n";
    return out.str();
}

std::string validation_json(
    const std::string& schema,
    const SeriesSummary& series,
    const Classification& classification,
    const fs::path& dcm2niix_log,
    const fs::path& raw_nii,
    const fs::path& patched_nii,
    const std::array<double, 3>& requested_spacing,
    const std::vector<MprPreviewInfo>& mpr_previews
) {
    const auto raw_header = raw_nii.empty() ? NiftiHeaderInfo{} : read_nifti_header(raw_nii);
    const auto patched_header = patched_nii.empty() ? NiftiHeaderInfo{} : read_nifti_header(patched_nii);
    std::vector<std::string> mpr_strings = mpr_preview_paths(mpr_previews);

    std::ostringstream out;
    out << "{\n";
    out << "  \"schema\": " << json_string(schema) << ",\n";
    out << "  \"tool\": {\"name\": \"totalsegmentator-wrapper-dicom-normalizer\", \"version\": "
        << json_string(std::string(kVersion)) << "},\n";
    out << "  \"selected_series\": {\n";
    out << "    \"series_number\": " << json_optional_int(series.files.front().series_number) << ",\n";
    out << "    \"series_description\": " << json_optional_string(series.files.front().series_description) << ",\n";
    out << "    \"file_count\": " << series.files.size() << ",\n";
    out << "    \"classification\": " << json_string(classification.status) << "\n";
    out << "  },\n";
    out << "  \"dcm2niix_log\": " << json_string(dcm2niix_log.string()) << ",\n";
    out << "  \"requested_spacing\": " << json_number_array3(requested_spacing) << ",\n";
    out << "  \"raw_nifti\": " << nifti_header_json(raw_nii, raw_header, 2) << ",\n";
    out << "  \"patched_nifti\": " << nifti_header_json(patched_nii, patched_header, 2) << ",\n";
    out << "  \"patched_spacing_matches_requested\": "
        << json_bool(spacing_matches(patched_header, requested_spacing)) << ",\n";
    out << "  \"mpr_preview\": {\n";
    out << "    \"format\": \"pgm\",\n";
    out << "    \"paths\": " << json_array_strings(mpr_strings) << ",\n";
    out << "    \"previews\": " << mpr_preview_info_array_json(mpr_previews, 4) << ",\n";
    out << "    \"written\": " << json_bool(!mpr_strings.empty()) << "\n";
    out << "  },\n";
    out << "  \"status\": "
        << json_string(patched_header.ok && spacing_matches(patched_header, requested_spacing)
                           ? "success"
                           : "failed")
        << "\n";
    out << "}\n";
    return out.str();
}

std::string convert_clean_metadata_json(
    const fs::path& dicom_dir,
    const SeriesSummary& series,
    const Classification& classification,
    const fs::path& isolated_dir,
    const fs::path& dcm2niix_dir,
    const fs::path& dcm2niix_log,
    const fs::path& nifti,
    int dcm2niix_returncode
) {
    std::ostringstream out;
    out << "{\n";
    out << "  \"schema\": \"totalsegmentator_wrapper_mac.dicom_normalizer.convert_clean.v1\",\n";
    out << "  \"tool\": {\"name\": \"totalsegmentator-wrapper-dicom-normalizer\", \"version\": "
        << json_string(std::string(kVersion)) << "},\n";
    out << "  \"dicom_dir\": {\n";
    out << "    \"basename\": " << json_string(dicom_dir.filename().string()) << ",\n";
    out << "    \"path_hash_fnv1a64\": " << json_string(path_hash_fnv1a64(dicom_dir)) << "\n";
    out << "  },\n";
    out << "  \"selected_series\": {\n";
    out << "    \"series_number\": " << json_optional_int(series.files.front().series_number) << ",\n";
    out << "    \"series_description\": " << json_optional_string(series.files.front().series_description) << ",\n";
    out << "    \"series_instance_uid\": " << json_optional_string(series.key) << ",\n";
    out << "    \"file_count\": " << series.files.size() << ",\n";
    out << "    \"classification\": " << json_string(classification.status) << "\n";
    out << "  },\n";
    out << "  \"outputs\": {\n";
    out << "    \"isolated_series_dir\": " << json_string(isolated_dir.string()) << ",\n";
    out << "    \"dcm2niix_dir\": " << json_string(dcm2niix_dir.string()) << ",\n";
    out << "    \"dcm2niix_log\": " << json_string(dcm2niix_log.string()) << ",\n";
    out << "    \"nifti\": " << json_optional_string(nifti.string()) << "\n";
    out << "  },\n";
    out << "  \"dcm2niix\": {\"returncode\": " << dcm2niix_returncode << "},\n";
    out << "  \"product_boundary\": {\n";
    out << "    \"segmentation_started\": false,\n";
    out << "    \"secondary_capture_rescue\": false\n";
    out << "  },\n";
    out << "  \"status\": " << json_string(nifti.empty() ? "failed" : "success") << "\n";
    out << "}\n";
    return out.str();
}

bool nifti_shape_matches_viewer_group(const NiftiHeaderInfo& info, const ViewerExportGroup& group) {
    if (!info.ok) {
        return false;
    }
    const int rows = group.shape[0];
    const int columns = group.shape[1];
    const int slices = static_cast<int>(group.files.size());
    const bool in_plane_match =
        (info.shape[0] == columns && info.shape[1] == rows)
        || (info.shape[0] == rows && info.shape[1] == columns);
    return in_plane_match && info.shape[2] == slices;
}

bool nifti_spacing_matches_viewer_group(const NiftiHeaderInfo& info, const ViewerExportGroup& group) {
    if (!info.ok) {
        return false;
    }
    const bool in_plane_match =
        (std::abs(info.spacing[0] - group.pixel_spacing[0]) <= 1e-4
         && std::abs(info.spacing[1] - group.pixel_spacing[1]) <= 1e-4)
        || (std::abs(info.spacing[0] - group.pixel_spacing[1]) <= 1e-4
            && std::abs(info.spacing[1] - group.pixel_spacing[0]) <= 1e-4);
    return in_plane_match && std::abs(info.spacing[2] - group.slice_spacing_median) <= 1e-4;
}

std::string viewer_export_metadata_json(
    const fs::path& dicom_dir,
    const SeriesSummary& series,
    const Classification& classification,
    const ViewerExportGroup& group,
    const fs::path& isolated_dir,
    const fs::path& dcm2niix_dir,
    const fs::path& dcm2niix_log,
    const std::vector<fs::path>& niftis,
    const fs::path& nifti,
    const std::vector<MprPreviewInfo>& mpr_previews,
    int dcm2niix_returncode
) {
    const auto header = nifti.empty() ? NiftiHeaderInfo{} : read_nifti_header(nifti);
    const bool exactly_one_nifti = niftis.size() == 1;
    const bool shape_matches = nifti_shape_matches_viewer_group(header, group);
    const bool spacing_matches_group = nifti_spacing_matches_viewer_group(header, group);
    const bool success = dcm2niix_returncode == 0
        && exactly_one_nifti
        && header.ok
        && shape_matches
        && spacing_matches_group
        && group.ai_eligibility != "no_go";

    std::vector<std::string> nifti_strings;
    for (const auto& path : niftis) {
        nifti_strings.push_back(path.string());
    }
    std::vector<std::string> mpr_strings = mpr_preview_paths(mpr_previews);

    std::ostringstream out;
    out << "{\n";
    out << "  \"schema\": \"totalsegmentator_wrapper_mac.dicom_normalizer.viewer_export.v1\",\n";
    out << "  \"tool\": {\"name\": \"totalsegmentator-wrapper-dicom-normalizer\", \"version\": "
        << json_string(std::string(kVersion)) << "},\n";
    out << "  \"dicom_dir\": {\n";
    out << "    \"basename\": " << json_string(dicom_dir.filename().string()) << ",\n";
    out << "    \"path_hash_fnv1a64\": " << json_string(path_hash_fnv1a64(dicom_dir)) << "\n";
    out << "  },\n";
    out << "  \"selected_series\": {\n";
    out << "    \"series_number\": " << json_optional_int(series.files.front().series_number) << ",\n";
    out << "    \"series_description\": " << json_optional_string(series.files.front().series_description) << ",\n";
    out << "    \"series_instance_uid\": " << json_optional_string(series.key) << ",\n";
    out << "    \"file_count\": " << series.files.size() << ",\n";
    out << "    \"classification\": " << json_string(classification.status) << "\n";
    out << "  },\n";
    out << "  \"selected_group\": " << viewer_export_group_json(group, 2) << ",\n";
    out << "  \"computed_geometry\": {\n";
    out << "    \"rows\": " << group.shape[0] << ",\n";
    out << "    \"columns\": " << group.shape[1] << ",\n";
    out << "    \"slices\": " << group.files.size() << ",\n";
    out << "    \"voxel_spacing_mm\": ["
        << group.pixel_spacing[0] << ", " << group.pixel_spacing[1] << ", "
        << group.slice_spacing_median << "],\n";
    out << "    \"sorted_by\": \"dot(ImagePositionPatient, normal)\"\n";
    out << "  },\n";
    out << "  \"outputs\": {\n";
    out << "    \"isolated_group_dir\": " << json_string(isolated_dir.string()) << ",\n";
    out << "    \"dcm2niix_dir\": " << json_string(dcm2niix_dir.string()) << ",\n";
    out << "    \"dcm2niix_log\": " << json_string(dcm2niix_log.string()) << ",\n";
    out << "    \"niftis\": " << json_array_strings(nifti_strings) << ",\n";
    out << "    \"nifti\": " << json_optional_string(nifti.string()) << ",\n";
    out << "    \"mpr_preview_paths\": " << json_array_strings(mpr_strings) << ",\n";
    out << "    \"mpr_preview\": " << mpr_preview_info_array_json(mpr_previews, 4) << "\n";
    out << "  },\n";
    out << "  \"dcm2niix\": {\"returncode\": " << dcm2niix_returncode
        << ", \"selected_files_only\": true},\n";
    out << "  \"validation\": {\n";
    out << "    \"exactly_one_nifti\": " << json_bool(exactly_one_nifti) << ",\n";
    out << "    \"nifti_header\": " << nifti_header_json(nifti, header, 4) << ",\n";
    out << "    \"shape_matches_group\": " << json_bool(shape_matches) << ",\n";
    out << "    \"spacing_matches_group\": " << json_bool(spacing_matches_group) << "\n";
    out << "  },\n";
    out << "  \"provenance\": {\n";
    out << "    \"viewer_export_rescue\": true,\n";
    out << "    \"not_original_axial_ct\": true,\n";
    out << "    \"non_diagnostic_preview\": true,\n";
    out << "    \"source_series_was_mixed_geometry\": true,\n";
    out << "    \"other_groups_excluded\": true,\n";
    out << "    \"selected_group_plane\": " << json_string(group.plane_label) << "\n";
    out << "  },\n";
    out << "  \"product_boundary\": {\n";
    out << "    \"segmentation_started\": false,\n";
    out << "    \"ai_eligibility\": " << json_string(group.ai_eligibility) << "\n";
    out << "  },\n";
    out << "  \"status\": " << json_string(success ? "success" : "failed") << "\n";
    out << "}\n";
    return out.str();
}

int prepare_viewer_export(const Args& args) {
    if (!args.series_number.has_value() && args.series_key.empty()) {
        throw std::runtime_error("--series-number or --series-key is required for prepare-viewer-export");
    }
    if (args.group_id.empty()) {
        throw std::runtime_error("--group-id is required for prepare-viewer-export");
    }
    int skipped = 0;
    const auto series = audit_directory(args.dicom_dir, skipped);
    auto selected = find_requested_series(series, args);
    if (!selected.has_value()) {
        throw std::runtime_error("series not found: " + requested_series_description(args));
    }
    const auto tools = detect_optional_tools();
    const auto classification = classify_series(*selected, tools);
    if (classification.status != "viewer_export_mpr_mixed_candidate") {
        throw std::runtime_error(
            "prepare-viewer-export requires viewer_export_mpr_mixed_candidate, got "
            + classification.status);
    }
    auto selected_group = find_viewer_export_group(*selected, args.group_id);
    if (!selected_group.has_value()) {
        throw std::runtime_error("viewer export group not found: " + args.group_id);
    }
    if (!selected_group->volume_like || selected_group->ai_eligibility == "no_go") {
        throw std::runtime_error("selected viewer export group is not volume-like enough for rescue");
    }

    const fs::path root = args.output;
    const fs::path isolated_dir = root / "isolated_viewer_export_group";
    const fs::path dcm2niix_dir = root / "dcm2niix";
    const fs::path dcm2niix_log = root / "logs" / "dcm2niix_viewer_export.log";
    const fs::path metadata_path = root / "viewer_export_metadata.json";
    const fs::path mpr_dir = root / "mpr_preview";
    isolate_viewer_export_group(*selected_group, isolated_dir);

    const fs::path dcm2niix = find_dcm2niix(args.dcm2niix);
    const int returncode = run_dcm2niix(
        dcm2niix, isolated_dir, dcm2niix_dir, dcm2niix_log, "viewer_export_rescue");
    const std::vector<fs::path> niftis = returncode == 0 ? find_niftis(dcm2niix_dir) : std::vector<fs::path>{};
    const fs::path nifti = niftis.size() == 1 ? niftis.front() : fs::path{};
    std::vector<MprPreviewInfo> mpr_paths;
    if (!nifti.empty()) {
        try {
            mpr_paths = write_nifti_mpr_pgm(nifti, mpr_dir);
        } catch (...) {
            mpr_paths.clear();
        }
    }
    write_text(
        metadata_path,
        viewer_export_metadata_json(
            args.dicom_dir,
            *selected,
            classification,
            *selected_group,
            isolated_dir,
            dcm2niix_dir,
            dcm2niix_log,
            niftis,
            nifti,
            mpr_paths,
            returncode));

    const auto header = nifti.empty() ? NiftiHeaderInfo{} : read_nifti_header(nifti);
    const bool success = returncode == 0
        && niftis.size() == 1
        && header.ok
        && nifti_shape_matches_viewer_group(header, *selected_group)
        && nifti_spacing_matches_viewer_group(header, *selected_group);

    std::cout << "wrote " << metadata_path << "\n";
    if (success) {
        std::cout << "nifti=" << nifti << "\n";
        std::cout << "ai_eligibility=" << selected_group->ai_eligibility << "\n";
        return 0;
    }
    std::cout << "viewer export rescue failed; see " << dcm2niix_log << "\n";
    return 1;
}

std::string rescue_stack_source_manifest_json(
    const Classification& classification,
    const dicom_normalizer::RescueStackResult& stack
) {
    std::ostringstream canonical;
    for (const auto& entry : stack.entries) {
        canonical << "I:" << entry.instance_number
            << "\tH:" << entry.content_sha256 << "\n";
    }
    std::ostringstream out;
    out << "{\n";
    out << "  \"schema\": \"totalsegmentator_wrapper_mac.rescue_stack.v1\",\n";
    out << "  \"status\": \"success\",\n";
    out << "  \"classification\": " << json_string(classification.status) << ",\n";
    out << "  \"array\": {\n";
    out << "    \"file\": \"preview_stack.npy\",\n";
    out << "    \"shape_xyz\": [" << stack.size_x << ", " << stack.size_y
        << ", " << stack.size_z << "],\n";
    out << "    \"size_x\": " << stack.size_x << ",\n";
    out << "    \"size_y\": " << stack.size_y << ",\n";
    out << "    \"size_z\": " << stack.size_z << ",\n";
    out << "    \"axis_order\": [\"x\", \"y\", \"z\"],\n";
    out << "    \"storage_order\": \"x_fastest\",\n";
    out << "    \"fortran_order\": true,\n";
    out << "    \"dtype\": " << json_string(stack.dtype) << ",\n";
    out << "    \"photometric_interpretation\": "
        << json_string(stack.photometric_interpretation) << "\n";
    out << "  },\n";
    out << "  \"ordering\": {\n";
    out << "    \"source\": \"instance_number\",\n";
    out << "    \"ambiguous\": false,\n";
    out << "    \"direction\": \"ascending\"\n";
    out << "  },\n";
    out << "  \"source\": {\n";
    out << "    \"algorithm\": \"sha256\",\n";
    out << "    \"manifest_sha256\": "
        << json_string(dicom_normalizer::sha256_hex(canonical.str())) << ",\n";
    out << "    \"entry_count\": " << stack.entries.size() << ",\n";
    out << "    \"entries\": [";
    if (!stack.entries.empty()) {
        out << "\n";
        for (std::size_t index = 0; index < stack.entries.size(); ++index) {
            const auto& entry = stack.entries[index];
            out << "      {\"ordinal\": " << entry.ordinal
                << ", \"instance_number\": " << entry.instance_number
                << ", \"content_sha256\": " << json_string(entry.content_sha256)
                << "}";
            if (index + 1 != stack.entries.size()) {
                out << ",";
            }
            out << "\n";
        }
        out << "    ";
    }
    out << "]\n";
    out << "  }\n";
    out << "}\n";
    return out.str();
}

int export_rescue_stack(const Args& args) {
    if (!args.series_number.has_value() && args.series_key.empty()) {
        throw std::runtime_error(
            "--series-number or --series-key is required for export-rescue-stack");
    }
    int skipped = 0;
    const auto series = audit_directory(args.dicom_dir, skipped);
    const auto selected = find_requested_series(series, args);
    if (!selected.has_value()) {
        throw std::runtime_error("series not found: " + requested_series_description(args));
    }
    const auto classification = classify_series(*selected, detect_optional_tools());
    if (classification.status != "secondary_capture_rescue_candidate"
        && classification.status != "secondary_capture_reference_candidate") {
        throw std::runtime_error(
            "export-rescue-stack requires a Secondary Capture rescue or reference candidate");
    }

    std::vector<dicom_normalizer::RescueStackInput> inputs;
    inputs.reserve(selected->files.size());
    for (const auto& file : selected->files) {
        inputs.push_back({
            file.source_path,
            file.instance_number,
            file.content_sha256,
        });
    }
    const fs::path stack_path = args.output / "preview_stack.npy";
    const auto result =
        dicom_normalizer::export_rescue_stack_npy(std::move(inputs), stack_path);
    const fs::path manifest_path = args.output / "source_manifest.json";
    write_text(
        manifest_path,
        rescue_stack_source_manifest_json(classification, result));
    std::cout << "wrote " << stack_path << "\n";
    std::cout << "wrote " << manifest_path << "\n";
    return 0;
}

int prepare_rescue(const Args& args) {
    if (!args.series_number.has_value() && args.series_key.empty()) {
        throw std::runtime_error("--series-number or --series-key is required for prepare-rescue");
    }
    if (!(args.patched_spacing[0] > 0.0 && args.patched_spacing[1] > 0.0 && args.patched_spacing[2] > 0.0)) {
        throw std::runtime_error("--patched-spacing X,Y,Z is required for prepare-rescue");
    }
    int skipped = 0;
    const auto series = audit_directory(args.dicom_dir, skipped);
    auto selected = find_requested_series(series, args);
    if (!selected.has_value()) {
        throw std::runtime_error("series not found: " + requested_series_description(args));
    }
    const auto tools = detect_optional_tools();
    const auto classification = classify_series(*selected, tools);
    if (classification.status != "secondary_capture_rescue_candidate") {
        throw std::runtime_error(
            "prepare-rescue requires secondary_capture_rescue_candidate, got "
            + classification.status);
    }

    const fs::path root = args.output;
    const fs::path isolated_dir = root / "isolated_series";
    const fs::path dcm2niix_dir = root / "dcm2niix";
    const fs::path dcm2niix_log = root / "logs" / "dcm2niix_rescue.log";
    const fs::path metadata_path = root / "rescue_metadata.json";
    const fs::path validation_path = root / "rescue_validation.json";
    const fs::path mpr_dir = root / "mpr_preview";
    isolate_series(*selected, isolated_dir);

    const fs::path dcm2niix = find_dcm2niix(args.dcm2niix);
    const int returncode = run_dcm2niix(
        dcm2niix, isolated_dir, dcm2niix_dir, dcm2niix_log, "rescue_raw");
    const fs::path raw_nii = returncode == 0 ? find_first_nifti(dcm2niix_dir) : fs::path{};
    fs::path patched_nii;
    std::vector<MprPreviewInfo> mpr_paths;
    if (!raw_nii.empty()) {
        std::string spacing_safe = spacing_text(args.patched_spacing);
        std::replace(spacing_safe.begin(), spacing_safe.end(), '.', 'p');
        std::replace(spacing_safe.begin(), spacing_safe.end(), ',', '_');
        patched_nii = root / ("rescue_patched_spacing_" + spacing_safe + ".nii");
        patch_nifti_spacing_identity_affine(raw_nii, patched_nii, args.patched_spacing);
        try {
            mpr_paths = write_nifti_mpr_pgm(patched_nii, mpr_dir);
        } catch (...) {
            mpr_paths.clear();
        }
    }
    write_text(
        metadata_path,
        rescue_metadata_json(
            args.dicom_dir,
            *selected,
            classification,
            isolated_dir,
            dcm2niix_dir,
            dcm2niix_log,
            raw_nii,
            patched_nii,
            args.patched_spacing,
            returncode));
    write_text(
        validation_path,
        validation_json(
            "totalsegmentator_wrapper_mac.dicom_normalizer.rescue_validation.v1",
            *selected,
            classification,
            dcm2niix_log,
            raw_nii,
            patched_nii,
            args.patched_spacing,
            mpr_paths));

    std::cout << "wrote " << metadata_path << "\n";
    std::cout << "wrote " << validation_path << "\n";
    if (!patched_nii.empty()) {
        std::cout << "patched_nifti=" << patched_nii << "\n";
        return 0;
    }
    std::cout << "rescue failed; see " << dcm2niix_log << "\n";
    return 1;
}

int convert_clean(const Args& args) {
    if (!args.series_number.has_value() && args.series_key.empty()) {
        throw std::runtime_error("--series-number or --series-key is required for convert-clean");
    }
    int skipped = 0;
    const auto series = audit_directory(args.dicom_dir, skipped);
    auto selected = find_requested_series(series, args);
    if (!selected.has_value()) {
        throw std::runtime_error("series not found: " + requested_series_description(args));
    }
    const auto tools = detect_optional_tools();
    const auto classification = classify_series(*selected, tools);
    if (classification.status != "original_ct_geometry_ok") {
        throw std::runtime_error(
            "convert-clean requires original_ct_geometry_ok, got " + classification.status);
    }

    const fs::path root = args.output;
    const fs::path isolated_dir = root / "isolated_series";
    const fs::path dcm2niix_dir = root / "dcm2niix";
    const fs::path dcm2niix_log = root / "logs" / "dcm2niix_clean.log";
    const fs::path metadata_path = root / "convert_clean_metadata.json";
    isolate_series(*selected, isolated_dir);

    const fs::path dcm2niix = find_dcm2niix(args.dcm2niix);
    const int returncode = run_dcm2niix(
        dcm2niix, isolated_dir, dcm2niix_dir, dcm2niix_log, "clean_raw");
    const fs::path nifti = returncode == 0 ? find_first_nifti(dcm2niix_dir) : fs::path{};
    write_text(
        metadata_path,
        convert_clean_metadata_json(
            args.dicom_dir,
            *selected,
            classification,
            isolated_dir,
            dcm2niix_dir,
            dcm2niix_log,
            nifti,
            returncode));

    std::cout << "wrote " << metadata_path << "\n";
    if (!nifti.empty()) {
        std::cout << "nifti=" << nifti << "\n";
        return 0;
    }
    std::cout << "clean conversion failed; see " << dcm2niix_log << "\n";
    return 1;
}

void print_usage() {
    std::cout
        << "totalsegmentator-wrapper-dicom-normalizer " << kVersion << "\n\n"
        << "Usage:\n"
        << "  totalsegmentator-wrapper-dicom-normalizer doctor [--output <doctor.json>]\n"
        << "  totalsegmentator-wrapper-dicom-normalizer audit --dicom-dir <dir> --output <audit.json>\n"
        << "  totalsegmentator-wrapper-dicom-normalizer convert-clean --dicom-dir <dir> (--series-number <n>|--series-key <key>) "
        << "--output <artifact_dir>\n"
        << "  totalsegmentator-wrapper-dicom-normalizer prepare-rescue --dicom-dir <dir> (--series-number <n>|--series-key <key>) "
        << "--patched-spacing X,Y,Z --output <artifact_dir>\n\n"
        << "  totalsegmentator-wrapper-dicom-normalizer export-rescue-stack --dicom-dir <dir> "
        << "(--series-number <n>|--series-key <key>) --output <artifact_dir>\n"
        << "  totalsegmentator-wrapper-dicom-normalizer prepare-viewer-export --dicom-dir <dir> "
        << "(--series-number <n>|--series-key <key>) --group-id <gNNN> --output <artifact_dir>\n\n"
        << "Current phase:\n"
        << "  audit, clean CT conversion, secondary-capture rescue, and viewer/MPR export rescue.\n";
}

Args parse_args(int argc, char** argv) {
    if (argc <= 1) {
        print_usage();
        std::exit(0);
    }
    Args args;
    args.command = argv[1];
    if (args.command == "--help" || args.command == "-h") {
        print_usage();
        std::exit(0);
    }
    if (args.command == "--version") {
        std::cout << kVersion << "\n";
        std::exit(0);
    }
    if (args.command != "doctor"
        && args.command != "audit"
        && args.command != "convert-clean"
        && args.command != "prepare-rescue"
        && args.command != "export-rescue-stack"
        && args.command != "prepare-viewer-export") {
        throw std::runtime_error("unsupported command: " + args.command);
    }
    for (int index = 2; index < argc; ++index) {
        const std::string flag = argv[index];
        if (flag == "--dicom-dir" && index + 1 < argc) {
            args.dicom_dir = argv[++index];
        } else if (flag == "--output" && index + 1 < argc) {
            args.output = argv[++index];
        } else if (flag == "--series-number" && index + 1 < argc) {
            args.series_number = std::stoi(argv[++index]);
        } else if (flag == "--series-key" && index + 1 < argc) {
            args.series_key = argv[++index];
        } else if (flag == "--group-id" && index + 1 < argc) {
            args.group_id = argv[++index];
        } else if (flag == "--patched-spacing" && index + 1 < argc) {
            args.patched_spacing = parse_spacing(argv[++index]);
        } else if (flag == "--dcm2niix" && index + 1 < argc) {
            args.dcm2niix = argv[++index];
        } else if (flag == "--help" || flag == "-h") {
            print_usage();
            std::exit(0);
        } else {
            throw std::runtime_error("unknown or incomplete argument: " + flag);
        }
    }
    if (args.command == "doctor") {
        return args;
    }
    if (args.dicom_dir.empty()) {
        throw std::runtime_error("--dicom-dir is required");
    }
    if (args.output.empty()) {
        throw std::runtime_error("--output is required");
    }
    return args;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const Args args = parse_args(argc, argv);
        if (args.command == "doctor") {
            const auto json = doctor_json(detect_optional_tools());
            if (!args.output.empty()) {
                write_text(args.output, json);
                std::cout << "wrote " << args.output << "\n";
            } else {
                std::cout << json;
            }
            return 0;
        }
        if (args.command == "prepare-rescue") {
            return prepare_rescue(args);
        }
        if (args.command == "export-rescue-stack") {
            return export_rescue_stack(args);
        }
        if (args.command == "prepare-viewer-export") {
            return prepare_viewer_export(args);
        }
        if (args.command == "convert-clean") {
            return convert_clean(args);
        }
        int skipped = 0;
        const auto series = audit_directory(args.dicom_dir, skipped);
        const auto tools = detect_optional_tools();
        const auto dicomdir = audit_dicomdirs(args.dicom_dir);
        const auto json = audit_json(args.dicom_dir, series, skipped, tools, dicomdir);
        write_text(args.output, json);
        std::cout << "wrote " << args.output << "\n";
        std::cout << "series_count=" << series.size() << " skipped_file_count=" << skipped << "\n";
        return 0;
    } catch (const std::exception& exc) {
        std::cerr << "error: " << exc.what() << "\n";
        return 1;
    }
}
