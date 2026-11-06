# The kernel again, as the `display` flavour (docs/proposals/kiosk.md, *Kernel flavours*):
# the default config plus kernel/configs/display.config, which only adds — virtio-gpu
# with a text console on it, USB HID for a tablet. For an extension with a screen (kiosk);
# the default kernel stays headless. No build.sh of its own: build.sh runs the followed
# package's, which reads KERNEL_FLAVOUR. It installs boot/bzImage-display and
# boot/config-display, and identifies itself as <version>-display in `uname -r`.
source packages/kernel/env.sh
FOLLOWS=kernel
KERNEL_FLAVOUR=display
# NVD files every kernel CVE against linux_kernel; a second CPE counts each one twice.
unset CPE
