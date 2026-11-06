# EGL (Wayland and surfaceless platforms) and GLES 2/3, through softpipe, the software
# driver that needs no LLVM. No desktop GL, no GLX, no Vulkan, no video.
meson_install \
    -Dplatforms=wayland \
    -Dgallium-drivers=softpipe \
    -Dvulkan-drivers= \
    -Dvideo-codecs= \
    -Degl=enabled \
    -Dgbm=enabled \
    -Dgles1=disabled \
    -Dgles2=enabled \
    -Dopengl=false \
    -Dglx=disabled \
    -Dglvnd=disabled \
    -Dllvm=disabled \
    -Dlibunwind=disabled \
    -Dvalgrind=disabled \
    -Dlmsensors=disabled \
    -Dselinux=false \
    -Dbuild-tests=false \
    -Dtools=
rm -rf "$ROOTFS/usr/share/drirc.d"
