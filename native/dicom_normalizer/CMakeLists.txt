cmake_minimum_required(VERSION 3.20)

project(totalsegmentator_wrapper_dicom_normalizer VERSION 0.1.0 LANGUAGES CXX)

add_executable(totalsegmentator-wrapper-dicom-normalizer
  src/main.cpp
)

target_compile_features(totalsegmentator-wrapper-dicom-normalizer PRIVATE cxx_std_20)

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
    )
  endif()
endif()
