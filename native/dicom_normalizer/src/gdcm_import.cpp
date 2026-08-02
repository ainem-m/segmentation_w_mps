#include "gdcm_import.h"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>

#include <gdcmByteValue.h>
#include <gdcmDataSet.h>
#include <gdcmFile.h>
#include <gdcmFileMetaInformation.h>
#include <gdcmImage.h>
#include <gdcmImageChangeTransferSyntax.h>
#include <gdcmImageReader.h>
#include <gdcmImageWriter.h>
#include <gdcmItem.h>
#include <gdcmPhotometricInterpretation.h>
#include <gdcmPixelFormat.h>
#include <gdcmReader.h>
#include <gdcmSequenceOfItems.h>
#include <gdcmStringFilter.h>
#include <gdcmTag.h>
#include <gdcmTransferSyntax.h>
#include <gdcmVersion.h>

namespace fs = std::filesystem;

namespace dicom_normalizer {
namespace {

constexpr unsigned long kMaxDecodedBytes = 2UL * 1024UL * 1024UL * 1024UL;

std::string trim(std::string value) {
    while (!value.empty()
           && (value.back() == '\0' || value.back() == ' ' || value.back() == '\r'
               || value.back() == '\n' || value.back() == '\t')) {
        value.pop_back();
    }
    std::size_t first = 0;
    while (first < value.size() && value[first] == ' ') {
        ++first;
    }
    return value.substr(first);
}

std::vector<std::string> split_backslash(const std::string& value) {
    std::vector<std::string> result;
    std::string current;
    for (const char ch : value) {
        if (ch == '\\') {
            if (!current.empty()) {
                result.push_back(trim(current));
            }
            current.clear();
        } else {
            current.push_back(ch);
        }
    }
    if (!current.empty()) {
        result.push_back(trim(current));
    }
    return result;
}

std::optional<int> parse_int(const std::string& value) {
    try {
        if (const auto cleaned = trim(value); !cleaned.empty()) {
            return std::stoi(cleaned);
        }
    } catch (...) {
    }
    return std::nullopt;
}

std::optional<double> parse_double(const std::string& value) {
    try {
        if (const auto cleaned = trim(value); !cleaned.empty()) {
            std::size_t consumed = 0;
            const double parsed = std::stod(cleaned, &consumed);
            if (consumed == cleaned.size() && std::isfinite(parsed)) {
                return parsed;
            }
        }
    } catch (...) {
    }
    return std::nullopt;
}

std::vector<double> parse_doubles(const std::string& value) {
    std::vector<double> result;
    for (const auto& item : split_backslash(value)) {
        try {
            result.push_back(std::stod(item));
        } catch (...) {
            return {};
        }
    }
    return result;
}

std::string top_level_string(const gdcm::File& file, const gdcm::Tag& tag) {
    const auto& data_set = file.GetDataSet();
    if (!data_set.FindDataElement(tag)) {
        return {};
    }
    gdcm::StringFilter filter;
    filter.SetFile(file);
    return trim(filter.ToString(tag));
}

std::string byte_value_string(const gdcm::DataElement& element) {
    const gdcm::ByteValue* value = element.GetByteValue();
    if (value == nullptr || value->GetLength() == 0) {
        return {};
    }
    return trim(std::string(value->GetPointer(), value->GetLength()));
}

void collect_nested_values(
    const gdcm::DataSet& data_set,
    const gdcm::Tag& target,
    std::vector<std::string>& values) {
    if (data_set.FindDataElement(target)) {
        const auto value = byte_value_string(data_set.GetDataElement(target));
        if (!value.empty()) {
            values.push_back(value);
        }
    }
    for (auto iterator = data_set.Begin(); iterator != data_set.End(); ++iterator) {
        const gdcm::SmartPointer<gdcm::SequenceOfItems> sequence = iterator->GetValueAsSQ();
        if (!sequence) {
            continue;
        }
        for (std::size_t index = 1; index <= sequence->GetNumberOfItems(); ++index) {
            collect_nested_values(sequence->GetItem(index).GetNestedDataSet(), target, values);
        }
    }
}

std::vector<std::string> nested_values(const gdcm::File& file, const gdcm::Tag& tag) {
    std::vector<std::string> values;
    collect_nested_values(file.GetDataSet(), tag, values);
    return values;
}

bool data_set_contains_tag_recursively(
    const gdcm::DataSet& data_set,
    const gdcm::Tag& target
) {
    if (data_set.FindDataElement(target)) {
        return true;
    }
    for (auto iterator = data_set.Begin(); iterator != data_set.End(); ++iterator) {
        const gdcm::SmartPointer<gdcm::SequenceOfItems> sequence = iterator->GetValueAsSQ();
        if (!sequence) {
            continue;
        }
        for (std::size_t index = 1; index <= sequence->GetNumberOfItems(); ++index) {
            if (data_set_contains_tag_recursively(
                    sequence->GetItem(index).GetNestedDataSet(), target)) {
                return true;
            }
        }
    }
    return false;
}

bool functional_group_contains_pixel_value_transform(
    const gdcm::DataSet& data_set,
    const gdcm::Tag& functional_group_sequence
) {
    if (!data_set.FindDataElement(functional_group_sequence)) {
        return false;
    }
    const gdcm::SmartPointer<gdcm::SequenceOfItems> groups =
        data_set.GetDataElement(functional_group_sequence).GetValueAsSQ();
    if (!groups) {
        // Parsed-but-opaque functional groups must not be treated as evidence that
        // raw samples are safe for the rescue-to-inference path.
        return true;
    }
    const gdcm::Tag pixel_value_transform_sequence(0x0028, 0x9145);
    for (std::size_t index = 1; index <= groups->GetNumberOfItems(); ++index) {
        if (data_set_contains_tag_recursively(
                groups->GetItem(index).GetNestedDataSet(), pixel_value_transform_sequence)) {
            return true;
        }
    }
    return false;
}

GdcmPixelValueTransformInfo inspect_pixel_value_transform(const gdcm::File& file) {
    GdcmPixelValueTransformInfo result;
    const auto& data_set = file.GetDataSet();
    const gdcm::Tag rescale_intercept(0x0028, 0x1052);
    const gdcm::Tag rescale_slope(0x0028, 0x1053);
    result.rescale_intercept_present = data_set.FindDataElement(rescale_intercept);
    result.rescale_slope_present = data_set.FindDataElement(rescale_slope);
    result.modality_lut_present = data_set.FindDataElement(gdcm::Tag(0x0028, 0x3000));
    result.shared_pixel_value_transform_present =
        functional_group_contains_pixel_value_transform(
            data_set, gdcm::Tag(0x5200, 0x9229));
    result.per_frame_pixel_value_transform_present =
        functional_group_contains_pixel_value_transform(
            data_set, gdcm::Tag(0x5200, 0x9230));

    if (result.modality_lut_present) {
        result.rescue_reject_reason = "rescue_modality_lut_unsupported";
        return result;
    }
    if (result.shared_pixel_value_transform_present) {
        result.rescue_reject_reason = "rescue_shared_pixel_value_transform_unsupported";
        return result;
    }
    if (result.per_frame_pixel_value_transform_present) {
        result.rescue_reject_reason = "rescue_per_frame_pixel_value_transform_unsupported";
        return result;
    }
    if (result.rescale_slope_present != result.rescale_intercept_present) {
        result.rescue_reject_reason = "rescue_incomplete_rescale_transform";
        return result;
    }
    if (!result.rescale_slope_present) {
        return result;
    }

    const auto slope = parse_double(top_level_string(file, rescale_slope));
    const auto intercept = parse_double(top_level_string(file, rescale_intercept));
    if (!slope.has_value() || !intercept.has_value()) {
        result.rescue_reject_reason = "rescue_invalid_rescale_transform";
        return result;
    }
    if (*slope != 1.0 || *intercept != 0.0) {
        result.rescue_reject_reason = "rescue_nonidentity_rescale_transform";
    }
    return result;
}

bool has_dicm_prefix(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return false;
    }
    std::array<char, 132> header{};
    input.read(header.data(), static_cast<std::streamsize>(header.size()));
    return input.gcount() == static_cast<std::streamsize>(header.size())
        && header[128] == 'D' && header[129] == 'I' && header[130] == 'C' && header[131] == 'M';
}

template <std::size_t Size>
bool assign_array(const std::string& text, std::array<double, Size>& output) {
    const auto values = parse_doubles(text);
    if (values.size() != Size) {
        return false;
    }
    std::copy(values.begin(), values.end(), output.begin());
    return true;
}

template <std::size_t Size>
bool assign_first_nested(
    const gdcm::File& file,
    const gdcm::Tag& tag,
    std::array<double, Size>& output,
    bool& came_from_functional_group) {
    const auto top_level = top_level_string(file, tag);
    if (!top_level.empty() && assign_array(top_level, output)) {
        return true;
    }
    const auto values = nested_values(file, tag);
    for (const auto& value : values) {
        if (assign_array(value, output)) {
            came_from_functional_group = true;
            return true;
        }
    }
    return false;
}

}  // namespace

GdcmProbe probe_dicom_file(const fs::path& path) {
    GdcmProbe result;
    try {
        gdcm::Reader reader;
        reader.SetFileName(path.c_str());
        if (!reader.Read()) {
            result.error = "gdcm_reader_failed";
            return result;
        }

        const gdcm::File& file = reader.GetFile();
        const auto& data_set = file.GetDataSet();
        result.parsed = true;
        result.has_dicm_prefix = has_dicm_prefix(path);
        result.has_file_meta = !file.GetHeader().IsEmpty();
        result.transfer_syntax_uid = file.GetHeader().GetDataSetTransferSyntax().GetString();
        result.study_instance_uid = top_level_string(file, gdcm::Tag(0x0020, 0x000d));
        result.frame_of_reference_uid = top_level_string(file, gdcm::Tag(0x0020, 0x0052));
        result.series_instance_uid = top_level_string(file, gdcm::Tag(0x0020, 0x000e));
        result.sop_instance_uid = top_level_string(file, gdcm::Tag(0x0008, 0x0018));
        result.series_number = parse_int(top_level_string(file, gdcm::Tag(0x0020, 0x0011)));
        result.instance_number = parse_int(top_level_string(file, gdcm::Tag(0x0020, 0x0013)));
        result.series_description = top_level_string(file, gdcm::Tag(0x0008, 0x103e));
        result.modality = top_level_string(file, gdcm::Tag(0x0008, 0x0060));
        result.sop_class_uid = top_level_string(file, gdcm::Tag(0x0008, 0x0016));
        result.sop_class_name = file.GetHeader().GetMediaStorageAsString();
        result.image_type = split_backslash(top_level_string(file, gdcm::Tag(0x0008, 0x0008)));
        result.number_of_frames = parse_int(top_level_string(file, gdcm::Tag(0x0028, 0x0008)));
        const auto slice_thickness = top_level_string(file, gdcm::Tag(0x0018, 0x0050));
        result.has_slice_thickness = !slice_thickness.empty();
        result.slice_thickness = parse_double(slice_thickness);
        const auto spacing_between_slices =
            top_level_string(file, gdcm::Tag(0x0018, 0x0088));
        result.has_spacing_between_slices = !spacing_between_slices.empty();
        result.spacing_between_slices = parse_double(spacing_between_slices);
        result.burned_in_annotation = top_level_string(file, gdcm::Tag(0x0028, 0x0301));
        result.has_pixel_data = data_set.FindDataElement(gdcm::Tag(0x7fe0, 0x0010))
            || data_set.FindDataElement(gdcm::Tag(0x7fe0, 0x0008))
            || data_set.FindDataElement(gdcm::Tag(0x7fe0, 0x0009));

        bool functional_group_geometry = false;
        result.has_pixel_spacing = assign_first_nested(
            file, gdcm::Tag(0x0028, 0x0030), result.pixel_spacing, functional_group_geometry);
        result.has_image_position_patient = assign_first_nested(
            file, gdcm::Tag(0x0020, 0x0032), result.image_position_patient, functional_group_geometry);
        result.has_image_orientation_patient = assign_first_nested(
            file, gdcm::Tag(0x0020, 0x0037), result.image_orientation_patient, functional_group_geometry);
        result.geometry_from_functional_groups = functional_group_geometry;

        if (!result.has_pixel_data) {
            return result;
        }

        result.pixel_decode_attempted = true;
        gdcm::ImageReader image_reader;
        image_reader.SetFileName(path.c_str());
        if (!image_reader.Read()) {
            result.error = "gdcm_image_reader_failed";
            return result;
        }
        const gdcm::Image& image = image_reader.GetImage();
        const auto& pixel_format = image.GetPixelFormat();
        result.columns = static_cast<int>(image.GetColumns());
        result.rows = static_cast<int>(image.GetRows());
        if (!result.number_of_frames.has_value() && image.GetNumberOfDimensions() >= 3) {
            result.number_of_frames = static_cast<int>(image.GetDimension(2));
        }
        result.samples_per_pixel = static_cast<int>(pixel_format.GetSamplesPerPixel());
        result.bits_allocated = static_cast<int>(pixel_format.GetBitsAllocated());
        result.pixel_representation = static_cast<int>(pixel_format.GetPixelRepresentation());
        result.photometric_interpretation =
            trim(image.GetPhotometricInterpretation().GetString());

        const auto buffer_length = image.GetBufferLength();
        if (buffer_length == 0 || buffer_length > kMaxDecodedBytes
            || buffer_length > static_cast<unsigned long>(std::numeric_limits<std::size_t>::max())) {
            result.error = "gdcm_invalid_decoded_buffer_length";
            return result;
        }
        std::vector<char> decoded(static_cast<std::size_t>(buffer_length));
        if (!image.GetBuffer(decoded.data())) {
            result.error = "gdcm_pixel_decode_failed";
            return result;
        }
        result.pixel_decode_ok = true;
        result.decoded_bytes = static_cast<std::uint64_t>(buffer_length);
        std::uint64_t checksum = 1469598103934665603ULL;
        for (const unsigned char byte : decoded) {
            checksum ^= static_cast<std::uint64_t>(byte);
            checksum *= 1099511628211ULL;
        }
        result.decoded_fnv1a64 = checksum;
        return result;
    } catch (const std::exception& exception) {
        result.error = std::string("gdcm_exception:") + exception.what();
        return result;
    } catch (...) {
        result.error = "gdcm_unknown_exception";
        return result;
    }
}

GdcmDecodedImage decode_dicom_image(const fs::path& path) {
    GdcmDecodedImage result;
    try {
        gdcm::ImageReader reader;
        reader.SetFileName(path.c_str());
        if (!reader.Read()) {
            result.error = "gdcm_image_reader_failed";
            return result;
        }
        const auto& image = reader.GetImage();
        result.pixel_value_transform = inspect_pixel_value_transform(reader.GetFile());
        const auto& pixel_format = image.GetPixelFormat();
        result.columns = static_cast<int>(image.GetColumns());
        result.rows = static_cast<int>(image.GetRows());
        result.number_of_frames = image.GetNumberOfDimensions() >= 3
            ? static_cast<int>(image.GetDimension(2))
            : 1;
        result.samples_per_pixel = static_cast<int>(pixel_format.GetSamplesPerPixel());
        result.bits_allocated = static_cast<int>(pixel_format.GetBitsAllocated());
        result.bits_stored = static_cast<int>(pixel_format.GetBitsStored());
        result.high_bit = static_cast<int>(pixel_format.GetHighBit());
        result.pixel_representation = static_cast<int>(pixel_format.GetPixelRepresentation());
        result.photometric_interpretation =
            trim(image.GetPhotometricInterpretation().GetString());

        const auto length = image.GetBufferLength();
        if (length == 0 || length > kMaxDecodedBytes
            || length > static_cast<unsigned long>(std::numeric_limits<std::size_t>::max())) {
            result.error = "gdcm_invalid_decoded_buffer_length";
            return result;
        }
        result.pixels.resize(static_cast<std::size_t>(length));
        if (!image.GetBuffer(reinterpret_cast<char*>(result.pixels.data()))) {
            result.error = "gdcm_pixel_decode_failed";
            result.pixels.clear();
            return result;
        }
        result.ok = true;
        return result;
    } catch (...) {
        result.error = "gdcm_decode_exception";
        return result;
    }
}

bool transcode_to_explicit_little_endian(
    const fs::path& input,
    const fs::path& output,
    std::string& error) {
    try {
        gdcm::ImageReader reader;
        reader.SetFileName(input.c_str());
        if (!reader.Read()) {
            error = "gdcm_image_reader_failed";
            return false;
        }

        gdcm::ImageChangeTransferSyntax changer;
        changer.SetTransferSyntax(gdcm::TransferSyntax::ExplicitVRLittleEndian);
        changer.SetInput(reader.GetImage());
        if (!changer.Change()) {
            error = "gdcm_transcode_failed";
            return false;
        }

        gdcm::ImageWriter writer;
        writer.SetFile(reader.GetFile());
        writer.SetImage(changer.GetOutput());
        writer.SetFileName(output.c_str());
        if (!writer.Write()) {
            error = "gdcm_image_writer_failed";
            return false;
        }
        return true;
    } catch (const std::exception& exception) {
        error = std::string("gdcm_transcode_exception:") + exception.what();
        return false;
    } catch (...) {
        error = "gdcm_transcode_unknown_exception";
        return false;
    }
}

std::string gdcm_version() {
    return gdcm::Version::GetVersion();
}

}  // namespace dicom_normalizer
