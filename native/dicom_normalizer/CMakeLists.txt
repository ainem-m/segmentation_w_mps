cmake_minimum_required(VERSION 3.20)

project(totalsegmentator_wrapper_dicom_normalizer VERSION 0.3.0 LANGUAGES CXX)

find_package(
  GDCM 3.0 REQUIRED CONFIG
  HINTS
    "$ENV{GDCM_DIR}"
    "/opt/homebrew/opt/gdcm/lib/gdcm-3.2"
    "/usr/local/opt/gdcm/lib/gdcm-3.2"
)

add_executable(totalsegmentator-wrapper-dicom-normalizer
  src/main.cpp
  src/gdcm_import.cpp
  src/rescue_stack.cpp
  src/sha256.cpp
)

target_compile_features(totalsegmentator-wrapper-dicom-normalizer PRIVATE cxx_std_20)
target_link_libraries(totalsegmentator-wrapper-dicom-normalizer PRIVATE gdcmMSFF)

if(APPLE)
  target_compile_definitions(totalsegmentator-wrapper-dicom-normalizer PRIVATE TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_MAC=1)
endif()

if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|AppleClang|GNU")
  target_compile_options(totalsegmentator-wrapper-dicom-normalizer PRIVATE
    -Wall
    -Wextra
    -Wpedantic
  )
endif()

install(TARGETS totalsegmentator-wrapper-dicom-normalizer RUNTIME DESTINATION bin)

include(CTest)
if(BUILD_TESTING)
  find_package(Python3 COMPONENTS Interpreter)
  if(Python3_Interpreter_FOUND)
    add_test(
      NAME dicom_normalizer_synthetic
      COMMAND
        ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_normalizer.py
        $<TARGET_FILE:totalsegmentator-wrapper-dicom-normalizer>
        $<TARGET_FILE:gdcmconv>
    )
  endif()
endif()
