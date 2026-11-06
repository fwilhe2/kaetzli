for f in mono/AdwaitaMono-*.ttf; do
    install -D -m 644 "$f" "$ROOTFS/usr/share/fonts/Adwaita/$(basename "$f")"
done
