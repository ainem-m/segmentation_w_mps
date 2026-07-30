cmake_minimum_required(VERSION 3.20)

project(totalsegmentator_wrapper_dicom_normalizer VERSION 0.3.0 LANGUAGES CXX)

if(WIN32)
  find_package(GDCM 3.2.6 EXACT REQUIRED CONFIG HINTS "$ENV{GDCM_DIR}")
  set(DICOM_NORMALIZER_GDCM_STATIC_LIB_DIR "" CACHE PATH
    "Directory containing the exact GDCM 3.2.6 static libraries"
  )
  if(NOT IS_DIRECTORY "${DICOM_NORMALIZER_GDCM_STATIC_LIB_DIR}")
    message(FATAL_ERROR
      "Windows requires the GDCM 3.2.6 static libraries from the approved "
      "binary wheel; the official VS2013 DLL build is not ABI-compatible "
      "with the supported MSVC toolset."
    )
  endif()
  foreach(gdcm_library IN ITEMS
      gdcmMSFF
      gdcmIOD
      gdcmDSED
      gdcmDICT
      gdcmCommon
      gdcmcharls
      gdcmjpeg8
      gdcmjpeg12
      gdcmjpeg16
      gdcmopenjp2
      gdcmzlib
      gdcmexpat)
    set(gdcm_library_path
      "${DICOM_NORMALIZER_GDCM_STATIC_LIB_DIR}/${gdcm_library}.lib"
    )
    if(NOT EXISTS "${gdcm_library_path}")
      message(FATAL_ERROR "Missing required static library: ${gdcm_library}.lib")
    endif()
    list(APPEND DICOM_NORMALIZER_GDCM_LIBRARIES "${gdcm_library_path}")
  endforeach()
  get_target_property(
    DICOM_NORMALIZER_GDCM_INCLUDE_DIRS
    gdcmMSFF
    INTERFACE_INCLUDE_DIRECTORIES
  )
else()
  find_package(
    GDCM 3.0 REQUIRED CONFIG
    HINTS
      "$ENV{GDCM_DIR}"
      "/opt/homebrew/opt/gdcm/lib/gdcm-3.2"
      "/usr/local/opt/gdcm/lib/gdcm-3.2"
  )
endif()

add_executable(totalsegmentator-wrapper-dicom-normalizer
  src/main.cpp
  src/gdcm_import.cpp
  src/rescue_stack.cpp
  src/sha256.cpp
)

target_compile_features(totalsegmentator-wrapper-dicom-normalizer PRIVATE cxx_std_20)
if(WIN32)
  target_include_directories(totalsegmentator-wrapper-dicom-normalizer SYSTEM PRIVATE
    ${DICOM_NORMALIZER_GDCM_INCLUDE_DIRS}
  )
  target_link_libraries(totalsegmentator-wrapper-dicom-normalizer PRIVATE
    ${DICOM_NORMALIZER_GDCM_LIBRARIES}
    rpcrt4
    ws2_32
  )
else()
  target_link_libraries(totalsegmentator-wrapper-dicom-normalizer PRIVATE gdcmMSFF)
endif()

if(APPLE)
  target_compile_definitions(totalsegmentator-wrapper-dicom-normalizer PRIVATE TOTALSEGMENTATOR_WRAPPER_MAC_DICOM_MAC=1)
endif()

if(MSVC)
  target_compile_definitions(totalsegmentator-wrapper-dicom-normalizer PRIVATE
    NOMINMAX
    WIN32_LEAN_AND_MEAN
  )
  target_compile_options(totalsegmentator-wrapper-dicom-normalizer PRIVATE
    /EHsc
    /permissive-
    /utf-8
    /W4
  )
  target_sources(totalsegmentator-wrapper-dicom-normalizer PRIVATE
    windows-long-path.manifest
  )
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
    if(WIN32)
      add_executable(dicom-normalizer-fake-dcm2niix
        tests/fake_dcm2niix.cpp
        windows-long-path.manifest
      )
      target_compile_features(dicom-normalizer-fake-dcm2niix PRIVATE cxx_std_20)
      if(MSVC)
        target_compile_definitions(dicom-normalizer-fake-dcm2niix PRIVATE
          NOMINMAX
          WIN32_LEAN_AND_MEAN
        )
        target_compile_options(dicom-normalizer-fake-dcm2niix PRIVATE
          /EHsc
          /permissive-
          /utf-8
          /W4
        )
      endif()
      set(DICOM_NORMALIZER_TEST_DCM2NIIX
        $<TARGET_FILE:dicom-normalizer-fake-dcm2niix>
      )
      set(DICOM_NORMALIZER_TEST_REAL_DCM2NIIX "" CACHE FILEPATH
        "Optional real dcm2niix executable for the Windows integration test"
      )
    endif()
    add_test(
      NAME dicom_normalizer_synthetic
      COMMAND
        ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_normalizer.py
        $<TARGET_FILE:totalsegmentator-wrapper-dicom-normalizer>
        $<TARGET_FILE:gdcmconv>
        ${DICOM_NORMALIZER_TEST_DCM2NIIX}
        ${DICOM_NORMALIZER_TEST_REAL_DCM2NIIX}
    )
  endif()
endif()
