#!/bin/bash
echo "Building ${PKG_NAME}."


# Isolate the build.
mkdir -p Build-${PKG_NAME}
cd Build-${PKG_NAME} || exit 1


# Generate the build files.
echo "Generating the build files."
cmake .. -G"Ninja" ${CMAKE_ARGS} \
      -DCMAKE_PREFIX_PATH=$PREFIX \
      -DCMAKE_INSTALL_PREFIX=$PREFIX \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DSPDLOG_FMT_EXTERNAL=ON \
      -DSPDLOG_BUILD_SHARED=ON \
      -DSPDLOG_BUILD_TESTS=ON \
      -DCMAKE_BUILD_TYPE=Release


# Build.
echo "Building..."
ninja || exit 1


# Perform tests.
echo "Testing..."
ninja test || exit 1


# Installing
echo "Installing..."
ninja install || exit 1


# Error free exit!
echo "Error free exit!"
exit 0
