# logind only: no seatd server or its backend, no builtin seat manager.
meson_install \
    -Dlibseat-logind=systemd \
    -Dlibseat-seatd=disabled \
    -Dlibseat-builtin=disabled \
    -Dserver=disabled \
    -Dexamples=disabled \
    -Dman-pages=disabled
