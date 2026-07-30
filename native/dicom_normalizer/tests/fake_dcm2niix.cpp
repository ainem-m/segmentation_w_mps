#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <type_traits>
#include <vector>

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

namespace fs = std::filesystem;

namespace {

std::wstring environment_value(const wchar_t* name, const wchar_t* fallback) {
    wchar_t* value = nullptr;
    std::size_t size = 0;
    if (_wdupenv_s(&value, &size, name) != 0 || value == nullptr) {
        return fallback;
    }
    const std::wstring result(value);
    std::free(value);
    return result;
}

template <typename Value, std::size_t Size>
std::array<Value, Size> parse_values(const std::wstring& text) {
    std::array<Value, Size> values{};
    std::wistringstream input(text);
    std::wstring part;
    for (std::size_t index = 0; index < Size; ++index) {
        if (!std::getline(input, part, L',')) {
            throw std::runtime_error("invalid fake dcm2niix test values");
        }
        if constexpr (std::is_same_v<Value, int>) {
            values[index] = std::stoi(part);
        } else {
            values[index] = std::stod(part);
        }
    }
    if (std::getline(input, part, L',')) {
        throw std::runtime_error("invalid fake dcm2niix test values");
    }
    return values;
}

template <typename Value>
void put(std::vector<std::uint8_t>& output, std::size_t offset, Value value) {
    std::memcpy(output.data() + offset, &value, sizeof(value));
}

int run_child() {
    const int sleep_seconds = std::stoi(environment_value(
        L"DICOM_NORMALIZER_TEST_FAKE_CHILD_SLEEP_SECONDS",
        L"0"));
    if (sleep_seconds > 0) {
        std::this_thread::sleep_for(std::chrono::seconds(sleep_seconds));
    }
    const fs::path marker = environment_value(
        L"DICOM_NORMALIZER_TEST_FAKE_CHILD_MARKER",
        L"");
    if (!marker.empty()) {
        std::ofstream output(marker, std::ios::binary | std::ios::trunc);
        output << "survived";
    }
    return 0;
}

void maybe_spawn_child() {
    const int sleep_seconds = std::stoi(environment_value(
        L"DICOM_NORMALIZER_TEST_FAKE_CHILD_SLEEP_SECONDS",
        L"0"));
    if (sleep_seconds <= 0) {
        return;
    }
    std::vector<wchar_t> executable(32768, L'\0');
    const DWORD length = GetModuleFileNameW(
        nullptr,
        executable.data(),
        static_cast<DWORD>(executable.size()));
    if (length == 0 || length >= executable.size()) {
        throw std::runtime_error("cannot resolve fake dcm2niix executable");
    }
    const std::wstring executable_path(executable.data(), length);
    std::wstring command_line = L"\"" + executable_path + L"\" --test-child";
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(
            executable_path.c_str(),
            command_line.data(),
            nullptr,
            nullptr,
            FALSE,
            CREATE_NO_WINDOW,
            nullptr,
            nullptr,
            &startup,
            &process)) {
        throw std::runtime_error("cannot spawn fake dcm2niix child");
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
}

int run(int argc, wchar_t** argv) {
    if (argc == 2 && std::wstring(argv[1]) == L"--test-child") {
        return run_child();
    }
    fs::path output_dir;
    std::wstring output_name;
    for (int index = 1; index < argc; ++index) {
        const std::wstring argument(argv[index]);
        if (argument == L"-o" && index + 1 < argc) {
            output_dir = fs::path(argv[++index]);
        } else if (argument == L"-f" && index + 1 < argc) {
            output_name = argv[++index];
        }
    }
    if (output_dir.empty() || output_name.empty()) {
        throw std::runtime_error("fake dcm2niix requires -o and -f");
    }

    const auto shape = parse_values<int, 3>(
        environment_value(L"DICOM_NORMALIZER_TEST_FAKE_SHAPE", L"4,5,6"));
    const auto spacing = parse_values<double, 3>(
        environment_value(L"DICOM_NORMALIZER_TEST_FAKE_SPACING", L"1,1,1"));
    const int sleep_seconds = std::stoi(
        environment_value(L"DICOM_NORMALIZER_TEST_FAKE_SLEEP_SECONDS", L"0"));
    maybe_spawn_child();
    if (sleep_seconds > 0) {
        std::this_thread::sleep_for(std::chrono::seconds(sleep_seconds));
    }

    fs::create_directories(output_dir);
    std::vector<std::uint8_t> header(352, 0);
    put<std::uint32_t>(header, 0, 348);
    put<std::int16_t>(header, 40, 3);
    put<std::int16_t>(header, 42, static_cast<std::int16_t>(shape[0]));
    put<std::int16_t>(header, 44, static_cast<std::int16_t>(shape[1]));
    put<std::int16_t>(header, 46, static_cast<std::int16_t>(shape[2]));
    put<std::int16_t>(header, 70, 4);
    put<std::int16_t>(header, 72, 16);
    put<float>(header, 76, 1.0F);
    put<float>(header, 80, static_cast<float>(spacing[0]));
    put<float>(header, 84, static_cast<float>(spacing[1]));
    put<float>(header, 88, static_cast<float>(spacing[2]));
    put<float>(header, 108, 352.0F);
    header[344] = 'n';
    header[345] = '+';
    header[346] = '1';

    const fs::path output_path = output_dir / fs::path(output_name + L".nii");
    std::ofstream output(output_path, std::ios::binary | std::ios::trunc);
    if (!output) {
        throw std::runtime_error("cannot write fake NIfTI");
    }
    output.write(
        reinterpret_cast<const char*>(header.data()),
        static_cast<std::streamsize>(header.size()));
    const std::size_t payload_size = static_cast<std::size_t>(shape[0])
        * static_cast<std::size_t>(shape[1])
        * static_cast<std::size_t>(shape[2])
        * 2U;
    const std::array<char, 64 * 1024> zeros{};
    std::size_t remaining = payload_size;
    while (remaining > 0) {
        const std::size_t count = std::min(remaining, zeros.size());
        output.write(zeros.data(), static_cast<std::streamsize>(count));
        remaining -= count;
    }
    if (!output) {
        throw std::runtime_error("cannot write fake NIfTI");
    }
    std::cout << "fake dcm2niix wrote nifti\n";
    return 0;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    try {
        return run(argc, argv);
    } catch (const std::exception& exception) {
        std::cerr << "error: " << exception.what() << "\n";
        return 1;
    }
}
