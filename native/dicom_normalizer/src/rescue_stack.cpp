#include "rescue_stack.h"

#include "gdcm_import.h"

#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <fstream>
#include <set>
#include <sstream>
#include <stdexcept>

namespace dicom_normalizer {
namespace {

std::string numpy_dtype(const GdcmDecodedImage& image) {
    if (image.bits_allocated == 8) {
        return image.pixel_representation == 0 ? "|u1" : "|i1";
    }
    if (image.bits_allocated == 16) {
        if constexpr (std::endian::native != std::endian::little) {
            throw std::runtime_error("unsupported_host_endian");
        }
        return image.pixel_representation == 0 ? "<u2" : "<i2";
    }
    throw std::runtime_error("unsupported_bits_allocated");
}

void validate_allowed_image(const GdcmDecodedImage& image) {
    if (!image.ok) {
        throw std::runtime_error(
            image.error.empty() ? "gdcm_pixel_decode_failed" : image.error);
    }
    if (image.number_of_frames <= 0) {
        throw std::runtime_error("invalid_frame_count");
    }
    if (!image.pixel_value_transform.rescue_reject_reason.empty()) {
        throw std::runtime_error(image.pixel_value_transform.rescue_reject_reason);
    }
    if (image.samples_per_pixel != 1) {
        throw std::runtime_error("unsupported_samples_per_pixel");
    }
    if (image.photometric_interpretation == "MONOCHROME1") {
        throw std::runtime_error("rescue_monochrome1_unsupported");
    }
    if (image.photometric_interpretation != "MONOCHROME2") {
        throw std::runtime_error("unsupported_photometric_interpretation");
    }
    if (image.bits_allocated != 8 && image.bits_allocated != 16) {
        throw std::runtime_error("unsupported_bits_allocated");
    }
    if (image.bits_stored != image.bits_allocated
        || image.high_bit != image.bits_allocated - 1) {
        throw std::runtime_error("unsupported_stored_bit_layout");
    }
    if (image.pixel_representation != 0 && image.pixel_representation != 1) {
        throw std::runtime_error("unsupported_pixel_representation");
    }
    if (image.bits_allocated == 8 && image.pixel_representation == 1) {
        throw std::runtime_error("rescue_signed_8bit_unsupported");
    }
    if (image.rows <= 0 || image.columns <= 0) {
        throw std::runtime_error("unexpected_decoded_buffer_layout");
    }
    const auto expected = static_cast<std::size_t>(image.rows)
        * static_cast<std::size_t>(image.columns)
        * static_cast<std::size_t>(image.number_of_frames)
        * static_cast<std::size_t>(image.bits_allocated / 8);
    if (image.pixels.size() != expected) {
        throw std::runtime_error("unexpected_decoded_buffer_layout");
    }
}

void write_npy_header(
    std::ofstream& output,
    const std::string& dtype,
    int size_x,
    int size_y,
    int size_z
) {
    std::ostringstream dictionary;
    dictionary << "{'descr': '" << dtype
        << "', 'fortran_order': True, 'shape': ("
        << size_x << ", " << size_y << ", " << size_z << "), }";
    std::string header = dictionary.str();
    const std::size_t padding = (64U - ((10U + header.size() + 1U) % 64U)) % 64U;
    header.append(padding, ' ');
    header.push_back('\n');
    if (header.size() > 65535U) {
        throw std::runtime_error("npy_header_too_large");
    }

    const std::array<std::uint8_t, 8> prefix{
        0x93U, 'N', 'U', 'M', 'P', 'Y', 1U, 0U,
    };
    output.write(
        reinterpret_cast<const char*>(prefix.data()),
        static_cast<std::streamsize>(prefix.size()));
    const auto length = static_cast<std::uint16_t>(header.size());
    const std::array<std::uint8_t, 2> length_bytes{
        static_cast<std::uint8_t>(length & 0xffU),
        static_cast<std::uint8_t>((length >> 8U) & 0xffU),
    };
    output.write(
        reinterpret_cast<const char*>(length_bytes.data()),
        static_cast<std::streamsize>(length_bytes.size()));
    output.write(header.data(), static_cast<std::streamsize>(header.size()));
}

}  // namespace

RescueStackResult export_rescue_stack_npy(
    std::vector<RescueStackInput> inputs,
    const std::filesystem::path& output_path
) {
    if (inputs.empty()) {
        throw std::runtime_error("empty_rescue_stack");
    }
    if (inputs.size() > 1) {
        for (const auto& input : inputs) {
            if (!input.instance_number.has_value()) {
                throw std::runtime_error("ambiguous_instance_order");
            }
        }
    }
    std::sort(inputs.begin(), inputs.end(), [](const auto& lhs, const auto& rhs) {
        if (lhs.instance_number != rhs.instance_number) {
            return lhs.instance_number < rhs.instance_number;
        }
        return lhs.content_sha256 < rhs.content_sha256;
    });
    std::set<int> instances;
    if (inputs.size() > 1) {
        for (const auto& input : inputs) {
            if (!instances.insert(*input.instance_number).second) {
                throw std::runtime_error("ambiguous_instance_order");
            }
        }
    }

    const auto first = decode_dicom_image(inputs.front().path);
    validate_allowed_image(first);
    if (inputs.size() > 1 && first.number_of_frames != 1) {
        throw std::runtime_error("mixed_multiframe_series");
    }
    RescueStackResult result;
    result.size_x = first.columns;
    result.size_y = first.rows;
    result.size_z = inputs.size() == 1
        ? first.number_of_frames : static_cast<int>(inputs.size());
    result.dtype = numpy_dtype(first);
    result.photometric_interpretation = first.photometric_interpretation;
    result.multiframe_source = inputs.size() == 1 && first.number_of_frames > 1;

    if (!output_path.parent_path().empty()) {
        std::filesystem::create_directories(output_path.parent_path());
    }
    std::filesystem::path partial_path = output_path;
    partial_path += ".partial";
    if (std::filesystem::exists(output_path) || std::filesystem::exists(partial_path)) {
        throw std::runtime_error("rescue_stack_output_already_exists");
    }
    std::ofstream output(partial_path, std::ios::binary);
    if (!output) {
        throw std::runtime_error("cannot_write_rescue_stack");
    }
    try {
        write_npy_header(
            output, result.dtype, result.size_x, result.size_y, result.size_z);

        for (std::size_t index = 0; index < inputs.size(); ++index) {
            const auto image =
                index == 0 ? first : decode_dicom_image(inputs[index].path);
            validate_allowed_image(image);
            if (inputs.size() > 1 && image.number_of_frames != 1) {
                throw std::runtime_error("mixed_multiframe_series");
            }
            if (image.rows != first.rows
                || image.columns != first.columns
                || image.samples_per_pixel != first.samples_per_pixel
                || image.bits_allocated != first.bits_allocated
                || image.bits_stored != first.bits_stored
                || image.high_bit != first.high_bit
                || image.pixel_representation != first.pixel_representation
                || image.photometric_interpretation != first.photometric_interpretation) {
                throw std::runtime_error("mixed_pixel_format");
            }
            output.write(
                reinterpret_cast<const char*>(image.pixels.data()),
                static_cast<std::streamsize>(image.pixels.size()));
            if (!output) {
                throw std::runtime_error("cannot_write_rescue_stack");
            }
            const int instance_number = inputs[index].instance_number.value_or(1);
            for (int frame = 1; frame <= image.number_of_frames; ++frame) {
                result.entries.push_back({
                    static_cast<int>(result.entries.size() + 1),
                    instance_number,
                    frame,
                    inputs[index].content_sha256,
                });
            }
        }
        output.close();
        if (!output) {
            throw std::runtime_error("cannot_write_rescue_stack");
        }
        std::filesystem::rename(partial_path, output_path);
    } catch (...) {
        output.close();
        std::error_code ignored;
        std::filesystem::remove(partial_path, ignored);
        throw;
    }
    return result;
}

}  // namespace dicom_normalizer
