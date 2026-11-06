# The tools (libevdev-tweak-device, touchpad-edge-detector, mouse-dpi-tool) are for
# tuning hardware.
meson_install -Dtests=disabled -Dtools=disabled -Ddocumentation=disabled
