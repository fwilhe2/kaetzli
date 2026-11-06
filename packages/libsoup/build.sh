# No GSSAPI or NTLM authentication, no sysprof, no introspection. tls_check off: it asks
# at build time whether glib-networking is installed, which in the builder it is not;
# at run time the extension has it.
meson_install \
    -Dbrotli=enabled \
    -Dgssapi=disabled \
    -Dntlm=disabled \
    -Dsysprof=disabled \
    -Dtls_check=false \
    -Dintrospection=disabled \
    -Dvapi=disabled \
    -Ddocs=disabled \
    -Dtests=false \
    -Dautobahn=disabled \
    -Dpkcs11_tests=disabled
rm -rf "$ROOTFS/usr/share/locale"
