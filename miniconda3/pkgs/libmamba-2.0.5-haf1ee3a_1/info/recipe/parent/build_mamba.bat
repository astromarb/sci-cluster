@echo ON

if /I "%PKG_NAME%" == "libmamba" (

    cmake -B build-lib/ ^
        %CMAKE_ARGS% ^
        -G Ninja ^
		-D CMAKE_INSTALL_PREFIX=%LIBRARY_PREFIX% ^
		-D CMAKE_PREFIX_PATH=%PREFIX% ^
		-D CMAKE_BUILD_TYPE=Release ^
        -D BUILD_SHARED=ON ^
        -D BUILD_LIBMAMBA=ON ^
        -D BUILD_MAMBA_PACKAGE=ON ^
        -D BUILD_LIBMAMBAPY=OFF ^
        -D BUILD_MAMBA=OFF ^
        -D BUILD_MICROMAMBA=OFF
    if errorlevel 1 exit 1
    cmake --build build-lib/ --parallel %CPU_COUNT%
    if errorlevel 1 exit 1
    cmake --install build-lib/

)
if /I "%PKG_NAME%" == "libmambapy" (

    %PYTHON% -m pip install --no-deps --no-build-isolation --config-settings="--build-type=Release" --config-settings="--generator=Ninja" -vv ./libmambapy

)
if /I "%PKG_NAME%" == "mamba" (

    cmake -B build-mamba/ ^
        %CMAKE_ARGS% ^
        -G Ninja ^
		-D CMAKE_BUILD_TYPE=Release ^
		-D CMAKE_INSTALL_PREFIX=%LIBRARY_PREFIX% ^
		-D CMAKE_PREFIX_PATH=%PREFIX% ^
		-D CMAKE_BUILD_TYPE=Release ^
        -D BUILD_LIBMAMBA=OFF ^
        -D BUILD_MAMBA_PACKAGE=OFF ^
        -D BUILD_LIBMAMBAPY=OFF ^
        -D BUILD_MAMBA=ON ^
        -D BUILD_MICROMAMBA=OFF
    if errorlevel 1 exit 1
    cmake --build build-mamba/ --parallel %CPU_COUNT%
    if errorlevel 1 exit 1
    cmake --install build-mamba/
    :: Install BAT hooks in condabin/
    CALL "%LIBRARY_BIN%\mamba.exe" shell hook --shell cmd.exe "%PREFIX%"
    if errorlevel 1 exit 1
)