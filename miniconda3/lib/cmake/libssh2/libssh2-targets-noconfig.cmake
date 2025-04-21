#----------------------------------------------------------------
# Generated CMake target import file.
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "libssh2::libssh2_shared" for configuration ""
set_property(TARGET libssh2::libssh2_shared APPEND PROPERTY IMPORTED_CONFIGURATIONS NOCONFIG)
set_target_properties(libssh2::libssh2_shared PROPERTIES
  IMPORTED_LOCATION_NOCONFIG "${_IMPORT_PREFIX}/lib/libssh2.so.1.0.1"
  IMPORTED_SONAME_NOCONFIG "libssh2.so.1"
  )

list(APPEND _cmake_import_check_targets libssh2::libssh2_shared )
list(APPEND _cmake_import_check_files_for_libssh2::libssh2_shared "${_IMPORT_PREFIX}/lib/libssh2.so.1.0.1" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
