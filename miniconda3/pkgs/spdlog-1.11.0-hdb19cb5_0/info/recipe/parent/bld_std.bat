:: cmd
echo "Building %PKG_NAME%."


:: Isolate the build.
mkdir Build-%PKG_NAME%
cd Build-%PKG_NAME%
if errorlevel 1 exit /b 1


:: Generate the build files.
echo "Generating the build files."
cmake .. -G"Ninja" %CMAKE_ARGS% ^
      -DCMAKE_PREFIX_PATH=%LIBRARY_PREFIX% ^
      -DCMAKE_INSTALL_PREFIX=%LIBRARY_PREFIX% ^
      -DSPDLOG_FMT_EXTERNAL=ON ^
      -DSPDLOG_BUILD_SHARED=ON ^
      -DSPDLOG_BUILD_TESTS=ON ^
      -DCMAKE_BUILD_TYPE=Release


:: Build.
echo "Building..."
ninja
if errorlevel 1 exit 1


:: Perforem tests.
echo "Testing..."
::ninja test
tests\spdlog-utests.exe
if errorlevel 1 exit 1


:: Install.
echo "Installing..."
ninja install
if errorlevel 1 exit 1


:: Error free exit.
echo "Error free exit!"
exit 0
