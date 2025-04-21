#!/bin/bash

set -x

cd ${SRC_DIR}

$PYTHON -m pip install . --no-deps --no-build-isolation \
        --global-option="--with-libyaml" \
        --global-option="build_ext" \
        --global-option="-I$PREFIX/include" \
        --global-option="-L$PREFIX/lib"
