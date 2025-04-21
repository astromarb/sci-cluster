#!/bin/bash

set -euxo pipefail

export CXXFLAGS="${CXXFLAGS} -D_LIBCPP_DISABLE_AVAILABILITY=1"

if [[ $PKG_NAME == "libmamba" ]]; then

    cmake -B build-lib/ \
        -G Ninja \
        ${CMAKE_ARGS} \
        -D CMAKE_INSTALL_PREFIX=$PREFIX  \
        -D CMAKE_PREFIX_PATH=$PREFIX     \
        -D CMAKE_BUILD_TYPE=Release      \
        -D BUILD_SHARED=ON \
        -D BUILD_LIBMAMBA=ON \
        -D BUILD_MAMBA_PACKAGE=ON \
        -D BUILD_LIBMAMBAPY=OFF \
        -D BUILD_MAMBA=OFF \
        -D BUILD_MICROMAMBA=OFF \
        -D MAMBA_WARNING_AS_ERROR=OFF
    cmake --build build-lib/ --parallel ${CPU_COUNT}
    cmake --install build-lib/

elif [[ $PKG_NAME == "libmambapy" ]]; then

    export CMAKE_ARGS="-G Ninja ${CMAKE_ARGS}"
    "${PYTHON}" -m pip install --no-deps --no-build-isolation --config-settings="--build-type=Release" --config-settings="--generator=Ninja" -vv ./libmambapy

elif [[ $PKG_NAME == "mamba" ]]; then

    cmake -B build-mamba/ \
        -G Ninja \
        ${CMAKE_ARGS} \
        -D CMAKE_INSTALL_PREFIX=$PREFIX  \
        -D CMAKE_PREFIX_PATH=$PREFIX     \
        -D CMAKE_BUILD_TYPE=Release      \
        -D BUILD_LIBMAMBA=OFF \
        -D BUILD_MAMBA_PACKAGE=OFF \
        -D BUILD_LIBMAMBAPY=OFF \
        -D BUILD_MAMBA=ON \
        -D BUILD_MICROMAMBA=OFF \
        -D MAMBA_WARNING_AS_ERROR=OFF
    cmake --build build-mamba/ --parallel ${CPU_COUNT}
    cmake --install build-mamba/

    # Add symlink to condabin
    mkdir -p "${PREFIX}/condabin"
    ln -s "${PREFIX}/bin/mamba" "${PREFIX}/condabin/mamba"
fi