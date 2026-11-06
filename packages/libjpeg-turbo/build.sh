# libjpeg only: no TurboJPEG API, no cjpeg/djpeg/jpegtran. The x86 SIMD code needs nasm,
# which the builder does not have, so amd64 gets the C paths; arm64's SIMD is intrinsics.
cmake -S . -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DENABLE_STATIC=OFF \
    -DWITH_TURBOJPEG=OFF \
    -DWITH_TOOLS=OFF
cmake --build build -j"$(nproc)"
DESTDIR=$ROOTFS cmake --install build
rm -rf "$ROOTFS/usr/bin"
