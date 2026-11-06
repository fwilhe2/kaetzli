# WPE WebKit with only the Wayland platform (cage is the compositor), and its
# MiniBrowser. What is off, and why:
#   media           video, Web Audio, media stream/recorder, Web Codecs and EME are all
#                   GStreamer, which is its own decision (docs/proposals/kiosk.md, Media)
#   sandbox         bubblewrap and xdg-dbus-proxy are not in the image; the web process
#                   runs unconfined, as the kiosk user. A spike, not the kiosk.
#   legacy API      libwpe and its FDO backend: WPEPlatform replaces them
#   DRM, headless   platforms the kiosk does not run on
#   XSLT, spellcheck, speech, WebDriver, gamepad, hyphenation, accessibility (ATK),
#   AVIF, JPEG XL, LCMS, libwoff2 (FreeType decodes WOFF2 with brotli), sysprof,
#   libbacktrace, introspection, documentation
# GBM is off as well: with it WebKit would try the render node, which has no driver
# here (mesa is softpipe only); without it, rendering is Skia on the CPU into shared
# memory buffers.
#
# Memory: a WebKit translation unit (unified sources) can take 2 GB to compile, so the
# jobs are what the memory allows, not what the CPUs do.
jobs=$(( $(awk '/^MemAvailable:/ { print $2 }' /proc/meminfo) / (2 * 1024 * 1024) ))
[ "$jobs" -ge 1 ] || jobs=1
[ "$jobs" -le "$(nproc)" ] || jobs=$(nproc)
echo "wpewebkit: $jobs compile jobs"

# CMAKE_PREFIX_PATH: some of CMake's own find modules (ICU) search paths rather than ask
# pkg-config, and the dependencies are in the sysroot build.sh mounts.
cmake -S . -B build -G Ninja \
    -DPORT=WPE \
    -DCMAKE_PREFIX_PATH=/usr/local/sysroot/usr \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_INSTALL_LIBEXECDIR=libexec \
    -DENABLE_MINIBROWSER=ON \
    -DENABLE_WPE_PLATFORM=ON \
    -DENABLE_WPE_PLATFORM_WAYLAND=ON \
    -DENABLE_WPE_PLATFORM_DRM=OFF \
    -DENABLE_WPE_PLATFORM_HEADLESS=OFF \
    -DENABLE_WPE_LEGACY_API=OFF \
    -DENABLE_WPE_QT_API=OFF \
    -DENABLE_BUBBLEWRAP_SANDBOX=OFF \
    -DUSE_GSTREAMER=OFF \
    -DENABLE_VIDEO=OFF \
    -DENABLE_WEB_AUDIO=OFF \
    -DENABLE_MEDIA_STREAM=OFF \
    -DENABLE_MEDIA_RECORDER=OFF \
    -DENABLE_MEDIA_SESSION=OFF \
    -DENABLE_WEB_CODECS=OFF \
    -DENABLE_ENCRYPTED_MEDIA=OFF \
    -DENABLE_SPEECH_SYNTHESIS=OFF \
    -DENABLE_SPELLCHECK=OFF \
    -DENABLE_WEBDRIVER=OFF \
    -DENABLE_XSLT=OFF \
    -DENABLE_GAMEPAD=OFF \
    -DENABLE_DOCUMENTATION=OFF \
    -DENABLE_INTROSPECTION=OFF \
    -DENABLE_JOURNALD_LOG=ON \
    -DUSE_GBM=OFF \
    -DUSE_LIBDRM=ON \
    -DUSE_ATK=OFF \
    -DUSE_FLITE=OFF \
    -DUSE_LIBBACKTRACE=OFF \
    -DUSE_LIBHYPHEN=OFF \
    -DUSE_AVIF=OFF \
    -DUSE_JPEGXL=OFF \
    -DUSE_LCMS=OFF \
    -DUSE_WOFF2=OFF \
    -DUSE_SYSPROF_CAPTURE=OFF \
    -DUSE_SYSTEM_UNIFDEF=OFF
cmake --build build -j"$jobs"
DESTDIR=$ROOTFS cmake --install build

# ---------------------------------------------------------------------------------
# The kiosk session runs the browser instead of foot (packages/cage ships the unit).
# MiniBrowser has no toolbar: the start page has the address field, and the patch in
# patches/ makes Ctrl+L go back to it. Software rendering throughout: Skia paints on the
# CPU, no accelerated compositing, and mesa's softpipe only for the EGL display WebKit
# insists on having.
install -d "$ROOTFS/usr/share/kiosk" "$ROOTFS/usr/lib/systemd/system/kiosk.service.d"
# An address goes to https:// unless it names a scheme, or is localhost or an IP address
# (http://); anything with a space in it, or no dot, is a search.
cat > "$ROOTFS/usr/share/kiosk/start.html" <<'HTML'
<!doctype html>
<meta charset="utf-8">
<title>Start</title>
<style>
  html { height: 100%; font: 18px/1.4 "Adwaita Sans", sans-serif; background: #f6f5f4; color: #241f31; }
  body { margin: 0; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; }
  form { width: min(48rem, 90vw); }
  input { box-sizing: border-box; width: 100%; padding: .7rem 1rem; font: inherit; border: 2px solid #99c1f1; border-radius: .6rem; outline: none; }
  input:focus { border-color: #3584e4; }
  p { color: #5e5c64; font-size: 15px; }
  kbd { font-family: "Adwaita Mono", monospace; background: #deddda; border-radius: .3rem; padding: 0 .3rem; }
</style>
<form id="go">
  <input id="address" name="address" autofocus autocomplete="off" spellcheck="false"
         placeholder="Address or search">
</form>
<p><kbd>Ctrl</kbd>+<kbd>L</kbd> address &nbsp; <kbd>Alt</kbd>+<kbd>←</kbd> back &nbsp;
   <kbd>Alt</kbd>+<kbd>→</kbd> forward &nbsp; <kbd>Ctrl</kbd>+<kbd>R</kbd> reload</p>
<script>
  const field = document.getElementById("address");
  const current = new URLSearchParams(location.search).get("u") || "";
  if (current && !current.startsWith(location.origin + location.pathname)) {
    field.value = current;
    field.select();
  }
  function target(text) {
    text = text.trim();
    if (/^[a-z][a-z0-9+.-]*:/i.test(text) && !/^[^\s\/]+:\d+(\/|$)/.test(text))
      return text;
    if (!/\s/.test(text) && /^(localhost|\d{1,3}(\.\d{1,3}){3}|\[[0-9a-f:]+\])(:\d+)?(\/|$)/i.test(text))
      return "http://" + text;
    if (!/\s/.test(text) && /^[^\/]+\.[^\/]+/.test(text))
      return "https://" + text;
    return "https://duckduckgo.com/html/?q=" + encodeURIComponent(text);
  }
  document.getElementById("go").addEventListener("submit", event => {
    event.preventDefault();
    if (field.value.trim())
      location.href = target(field.value);
  });
</script>
HTML
cat > "$ROOTFS/usr/lib/systemd/system/kiosk.service.d/10-browser.conf" <<'UNIT'
[Service]
Environment=MINIBROWSER_START_PAGE=file:///usr/share/kiosk/start.html
Environment=MINIBROWSER_HARDWARE_ACCELERATION=never
Environment=WEBKIT_SKIA_ENABLE_CPU_RENDERING=1
Environment=LIBGL_ALWAYS_SOFTWARE=1
ExecStart=
ExecStart=/usr/bin/cage -s -- /usr/libexec/wpe-webkit-2.0/MiniBrowser file:///usr/share/kiosk/start.html
UNIT
