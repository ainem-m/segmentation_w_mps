#pragma once

#include <array>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace dicom_normalizer {

struct GdcmPixelValueTransformInfo {
    bool rescale_slope_present = false;
    bool rescale_intercept_present = false;
    bool modality_lut_present = false;
    bool shared_pixel_value_transform_present = false;
    bool per_frame_pixel_value_transform_present = false;
    // Empty only when raw decoded samples are safe for the rescue-to-inference path.
    // This is intentionally a fixed diagnostic token, never a raw DICOM value.
    std::string rescue_reject_reason;
};

struct GdcmProbe {
    bool parsed = false;
    bool has_dicm_prefix = false;
    bool has_file_meta = false;
    bool has_pixel_data = false;
    bool pixel_decode_attempted = false;
    bool pixel_decode_ok = false;
    bool geometry_from_functional_groups = false;
    std::uint64_t decoded_bytes = 0;
    std::uint64_t decoded_fnv1a64 = 0;
    std::string error;
    std::string transfer_syntax_uid;
    std::string study_instance_uid;
    std::string frame_of_reference_uid;
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
    bool has_slice_thickness = false;
    bool has_spacing_between_slices = false;
    std::optional<double> slice_thickness;
    std::optional<double> spacing_between_slices;
    std::string burned_in_annotation;
};

struct GdcmDecodedImage {
    bool ok = false;
    std::string error;
    int rows = 0;
    int columns = 0;
    int number_of_frames = 0;
    int samples_per_pixel = 0;
    int bits_allocated = 0;
    int bits_stored = 0;
    int high_bit = 0;
    int pixel_representation = 0;
    std::string photometric_interpretation;
    GdcmPixelValueTransformInfo pixel_value_transform;
    std::vector<std::uint8_t> pixels;
};

GdcmProbe probe_dicom_file(const std::filesystem::path& path);
GdcmDecodedImage decode_dicom_image(const std::filesystem::path& path);

bool transcode_to_explicit_little_endian(
    const std::filesystem::path& input,
    const std::filesystem::path& output,
    std::string& error);

std::string gdcm_version();

}  // namespace dicom_normalizer
