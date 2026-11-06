# The three libraries (common, dec, enc); no brotli tool. CMAKE_INSTALL_LIBDIR=lib for
# the reason packages/json-c gives.
cmake -S . -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DBUILD_SHARED_LIBS=ON \
    -DBROTLI_BUILD_TOOLS=OFF \
    -DBUILD_TESTING=OFF
cmake --build build -j"$(nproc)"
DESTDIR=$ROOTFS cmake --install build
