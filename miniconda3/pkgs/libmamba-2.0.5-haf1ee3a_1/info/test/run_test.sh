

set -ex



test -d ${PREFIX}/include/mamba
test -f ${PREFIX}/include/mamba/version.hpp
test -f ${PREFIX}/lib/cmake/libmamba/libmambaConfig.cmake
test -f ${PREFIX}/lib/cmake/libmamba/libmambaConfigVersion.cmake
test -e ${PREFIX}/lib/libmamba${SHLIB_EXT}
cat $PREFIX/include/mamba/version.hpp | grep "LIBMAMBA_VERSION_MAJOR 2"
cat $PREFIX/include/mamba/version.hpp | grep "LIBMAMBA_VERSION_MINOR 0"
cat $PREFIX/include/mamba/version.hpp | grep "LIBMAMBA_VERSION_PATCH 5"
exit 0
