# A kiosk image

**Status: proposal.** Nothing here is implemented. It plans a `kiosk` system extension: boot straight
into a locked-down Firefox on a minimal Wayland stack, play audio and video, take
per-deployment configuration and secrets from outside, and offer a password-protected
admin terminal. It also covers where that image should live.

**Decided so far:**

- The first release targets VMs only.
- It is an interactive kiosk: people browse, so there is a toolbar, but no settings and
  no other windows. It is not single-URL signage.
- Site logins come in as an encrypted, KeePass-style list of entries (see *Secrets*).
- A one-day WPE WebKit spike happens before Firefox is committed to (see *The spike*).

## What it is

- It boots into Firefox, fullscreen, as an unprivileged `kiosk` user. Nothing else is on
  screen. There is no desktop, no other window, no settings UI and no way out of the
  browser.
- Configuration comes in from outside: the start URL, allowed sites, site logins, Wi-Fi
  or proxy settings and the admin password are systemd credentials or a confext. They are
  never baked into the image.
- It plays media: HTML5 video and audio, with software decode at first.
- It has an admin mode: a password unlocks a root terminal on the same screen, and the
  kiosk comes back afterwards.
- It inherits everything the base image already has: the verity-protected EROFS root,
  `/var` in RAM or on a disk, hardening, reproducibility, the SBOM and the CVE reports.

## As a system extension

The kiosk is **a `kiosk` sysext plus confext on the unchanged base image**, like
`podman` and `kubernetes`. It is not a second image. An earlier draft of this proposal
made it a second image, which brought in everything in
[`image-variants.md`](image-variants.md): a second assembly, a second size budget, a
second OCI flavour nobody wants, and a second release stream. As an extension, none of
that is needed:

- **One base, one kernel, one release.** A kiosk is `--ext kiosk` on the same image a
  server uses. The base stays at about 90 MB for everyone else.
- **Integrity is unchanged.** An attached extension is verified by dm-verity from the
  kernel command line, like the root, so everything that runs is still verified.
- **"Where it lives" mostly answers itself.** It is one more extension in this
  repository, with its own `test/kiosk.sh`, just as `kubernetes` has `test/k8s.sh`.

**What it requires:**

1. **Packages that depend on each other**:
   [`extension-dependencies.md`](extension-dependencies.md), which is now a prerequisite,
   not an option. The GUI stack is about 25 libraries deep, and the vendor-it-static
   trick that serves the debug extension does not scale to GTK. Its rules fit as they
   stand: a `kiosk` package may `DEPENDS=` on base packages (glib, expat, zlib, dbus) and
   on other `kiosk` packages, and never on another extension. Our own headers then come
   from the dependency sysroot, not from sid, so the GUI libraries add **no** `-dev`
   lines to `deps.txt` and no sid couplings.
2. **CI by level, not one job per extension.** That proposal suggests building an
   extension's packages in order in one job. For `kiosk` that would be Firefox's 2–3 h
   on top of GTK, mesa and the rest, all in one job and close to the 6 h limit. Instead,
   generate the matrix in topological levels, roughly:
   - libpng, freetype, pixman, fribidi, wayland, libxkbcommon
   - harfbuzz, fontconfig, cairo, libdrm
   - pango, gdk-pixbuf, libepoxy, mesa, libinput
   - gtk3, wlroots
   - firefox, cage

   Each level unpacks the earlier levels' staging artifacts as its sysroot. A cache key
   includes its dependencies' keys, transitively. Locally, `./build.sh firefox` builds
   what is missing first, or refuses with the list.
3. **A kernel flavour.** An extension cannot bring kernel config, so the kernel is built
   in several configs, the way Debian ships `-cloud-amd64` beside `-amd64`. See *Kernel
   flavours*. The headless default stays as it is, and the kiosk boots a `display`
   flavour.
4. **Nothing in the extension may write `/etc` at boot** (the base's first boot happens
   at build time).
   - **Users**: `kiosk` and `admin` cannot come from `sysusers.d`, because the extension
     merges after `/etc` is sealed. Ship them as userdb records in `/usr/lib/userdb/`,
     served by `nss-systemd`. *Verify that the drop-in directory and its shadow fields
     behave as expected.*
   - **`/etc` files**: fontconfig's `/etc/fonts`, logind's `NAutoVTs=` and the tty2
     getty either move under `/usr` (`--sysconfdir`, `/usr/lib/systemd/logind.conf.d/`)
     or go into the `kiosk` confext. That is the mechanism `kubernetes` already uses.
   - **udev and hwdb**: libinput and mesa ship udev rules, and udevd has already started
     when the merge happens. The kiosk needs a reload and trigger after merge. hwdb
     should be avoided, because `hwdb.bin` cannot be regenerated on a read-only `/usr`.
5. **Starting at boot.** Units from an extension appear when the merge reloads PID 1, so
   `kiosk.service` needs `UpheldBy=` as well as `WantedBy=`, as Quadlet units do. Without
   the extension, the machine is the plain base with its getty.
6. **The base trim has to keep what the extension reaches.** Today the trim removes
   "what nothing in the image can reach", and an extension's consumers are outside the
   image. CI's existing extension check (each extension's libraries resolved against the
   base) catches libraries. Data the trim removes (terminfo, gconv modules, the PAM
   modules that `login` on tty2 needs) gets `present` lines.
7. **Detaching it is a host decision.** Someone who can change the VM's command line can
   boot without the kiosk. That fits the VM threat model, where the host is trusted, and
   should be said in the docs.

An extension will be large, about 400 MB, so it needs its own size budget. The size
check covers only images today.

### Kernel flavours

This is a kernel build matrix: arch × flavour. Each flavour is the default config **plus
one additive fragment**, and nothing else.

| Flavour | Fragment | For |
| --- | --- | --- |
| `default` | none (today's kernel) | everything headless: server, podman, kubernetes, Firecracker |
| `display` | `display.config`: `virtio-gpu` with fbdev emulation, `virtio-snd` and intel-hda, HID with `usb-tablet`/virtio-input | the kiosk, and any later GUI extension |

The TPM drivers (`TCG_TPM`, `TCG_CRB`, `TCG_TIS`) go into **`default`**, not `display`.
`systemd-creds` sealed to a vTPM is useful for any machine, not only a kiosk. Name
flavours after what they add (`display`), not after who uses them (`kiosk`), so a second
consumer does not need a rename.

**The rules:**

- **A flavour only adds.** Every flavour has every feature `default` has, so every
  extension and every test that runs on `default` runs on all of them. Debian's flavours
  subtract, from a hardware-heavy base. Ours add, because our base is already the trimmed
  one. A check, either a mode of `test/kernel-config-diff.py` or a small
  `test/kernel-flavours.py`, fails a flavour whose difference from `default` is anything
  other than symbols turned on. `kernel-caps.py` (what a container needs) and
  `kernel-parity.py` (amd64 and arm64 agree) run per flavour.
- **One source of truth for the fragments.** They stay as heredocs in
  `packages/kernel/build.sh`, which applies `display.config` when `$KERNEL_FLAVOUR` says
  so. The same post-merge check covers the flavour fragment: what it turns on must be
  `=y`, and every symbol it names must exist.
- **A flavour is a package**, so the rest of the machinery works on it unchanged: CI
  matrix, cache key, staging, SBOM, `check-updates.py` and `bump-version.sh`.
  `packages/kernel-display/env.sh` is `FOLLOWS=kernel` plus `KERNEL_FLAVOUR=display`, with
  no `build.sh` of its own. The driver mounts the followed package's `build.sh` when the
  follower has none. That is a small `build.sh` change, and the only new mechanism here.
- **The paths must not collide**, because `merge-staging.sh` fails on any path that two
  packages install:
  - Each flavour gets its own `LOCALVERSION` (`7.3.1-display`), so `uname -r` says which
    flavour is running.
  - The kernel installs as `boot/bzImage-<flavour>` and `boot/config-<flavour>`, and
    `default` keeps `boot/bzImage` and `boot/config`, so nothing downstream changes.
  - Each flavour has its own `usr/lib/modules/<release>/config` link. kubeadm finds the
    running kernel's config through `uname -r` either way.

  The image ships every flavour's config and modules directory, which are a few KB each,
  and none of the kernels. `build-rootfs.sh`'s `rm -f boot/bzImage` becomes
  `boot/bzImage*`.
- **Extension and flavour are bound together**, because systemd's extension-release
  metadata cannot express a kernel requirement:
  - The kiosk extension declares `KERNEL_FLAVOUR=display`, as metadata the tools read.
  - `vm.sh`, the launcher and `qemu-lib.sh` pick the matching kernel when `--ext kiosk`
    is given.
  - Inside the guest, `kiosk.service` has `AssertPathExists=/dev/dri/card0`, so the wrong
    kernel shows up as a failed unit, which `test/systemd.py` reports. A condition would
    skip the unit and say nothing.
- **CI**: the `kernel` job becomes a matrix over the kernel packages, with one artifact
  each (`package-kernel-display-<arch>`).
  - The config report against main, the 17:00 UTC new-driver review and the bundle
    manifest all cover each flavour.
  - A release carries `bzImage-display-<arch>`, which `kaetzli.release`'s `collect` and
    `fetch-image.sh` must know about.
  - The cost is one more kernel build per arch, in parallel, so it adds runner minutes
    but little wall time. An edit to `packages/kernel/build.sh` rebuilds every flavour,
    which is correct, because they share it.

## Decide first: VM or hardware?

The whole plan depends on this question, and it is the one the idea skipped.

Constraint 2 says VMs are the only target. Digital signage, though, usually means a screen
bolted to a small box. Each answer leads somewhere very different:

| | **Kiosk in a VM** | **Kiosk on hardware** |
| --- | --- | --- |
| Display | `virtio-gpu` (qemu, Virtualization.framework). Firecracker and cloud-hypervisor have **no display**, so the VMM matrix shrinks | i915/amdgpu/vc4 drivers, KMS, EDID quirks, multiple monitors, rotation |
| Firmware | none | i915 GuC/DMC, amdgpu, Wi-Fi: mostly `firmware-nonfree` in Debian, which **conflicts with constraint 6** |
| Boot | as today: external kernel and cmdline | UKI + systemd-boot, a partitioned disk, Secure Boot keys, maybe TPM. `image-variants.md` already calls this a separate platform |
| Updates | the host swaps the image | A/B partitions with `systemd-sysupdate`, rollback, a watchdog |
| Video | software decode only; GPU passthrough would mean hardware drivers again | VA-API hardware decode, which mesa supports |
| Who is the kiosk? | the *host*, whose own display and lockdown are someone else's problem | this image |

**Decided: VM only for now.** It keeps every constraint intact. It is testable in CI
with qemu screendumps, it is a real use case (signage VMs on a mini-PC hypervisor, a Mac
running vz, demos), and it proves out the userspace, which is most of the work. Treat bare
metal as a later and separate decision. It brings bootloader, firmware and update
machinery that this project does not have yet, and its firmware licensing may rule out
common hardware altogether.

## The stack

Every choice below is held to *Adding a package*. Debian, Fedora and Alpine all ship each
one.

| Layer | Choice | Why, and what it drags in |
| --- | --- | --- |
| Compositor | **cage** (wlroots) | Built for exactly this: one fullscreen app, and the session ends when the app does. VT switching is **off unless `-s`** (see *Admin mode*). Drags in wlroots, libinput, libxkbcommon, xkeyboard-config, libdrm, pixman, seatd/logind. Weston's kiosk-shell is the alternative; it is heavier and less focused. |
| Seat | logind (we have it) | cage runs as a systemd service on tty1 with `PAMName=`, the way greetd-less kiosks do it. No display manager. |
| GL | mesa: **virgl + softpipe only, no llvmpipe** | llvmpipe means libLLVM, about 100 MB. Firefox's software WebRender (SWGL) needs no GL at all, so mesa only matters for virgl hosts. Could even start without mesa. Building it needs python3 and mako, which is fine because that is builder-side. |
| Toolkit | GTK 3, Wayland backend only | Firefox still needs GTK 3. Drags in pango, cairo, harfbuzz, fribidi, freetype, fontconfig, gdk-pixbuf, libepoxy, at-spi2-core (set `NO_AT_BRIDGE=1`), libpng, libjpeg-turbo. |
| Browser | **Firefox ESR**, `--enable-default-toolkit=cairo-gtk3-wayland-only` | ESR suits signage: a yearly feature bump, with security releases through the existing update flow. Firefox bundles NSS, NSPR and ICU. |
| Audio | **ALSA direct** (`--enable-alsa`, cubeb's ALSA backend) at first | A kiosk has one audio client, so no daemon is needed. PipeWire would bring WirePlumber, which **embeds Lua** (constraint 5). Move to PipeWire + pipewire-pulse only if mixing or Bluetooth turns out to be needed. Verify early, because cubeb ALSA is not Mozilla's tier 1. |
| Codecs | Firefox's bundled ffvpx (VP8/VP9/AV1/Opus/FLAC/MP3) + **ffmpeg's libavcodec** (LGPL build, no `--enable-nonfree`) for H.264/AAC | ffmpeg is in Debian main, so it is DFSG-fine. Patent exposure in some jurisdictions is a separate question, noted under *Licensing*. |
| Fonts | a deliberate set: DejaVu or Noto Sans/Serif/Mono, Noto Color Emoji; CJK is a decision | CJK alone is over 100 MB. Pick what the content needs, guard it with `present` lines and put the result in the size budget. |
| Kernel | `virtio-gpu` (DRM + fbdev emulation for the text console), `virtio-snd` and/or `snd-hda-intel` for qemu's intel-hda, `HID` + `usb-tablet`/virtio-input | Today `CONFIG_DRM`, `SOUND`, `FB` and `HID_SUPPORT` are all off on purpose. They are VM devices, so they sit within constraint 2, but a headless VM does not need them, so they go into the `display` kernel flavour (see *Kernel flavours*). TPM goes into `default`. |

**Size, roughly:** libxul alone is about 130 MB. Firefox, the GTK stack, wlroots, fonts and
ffmpeg come to an estimated 350–500 MB, against today's 90 MB. That is fine for this
image, but its budget line should be its own and argued.

**Considered, not chosen:** Chromium is the signage incumbent. It takes much longer to
build (gn, about 8 h a build), and you asked for Firefox. **WPE WebKit + cog** (Igalia, in
Debian and Yocto) was designed for exactly this use: no GTK, Wayland-native and much
smaller. It uses GStreamer for media.

### The spike

Spend one day on WPE WebKit + cog against Firefox, in the qemu harness, and decide on
these, in this order:

1. **Browser UI.** cog is a launcher with **no chrome**: no URL bar, no back button. An
   interactive kiosk needs one, so WPE means writing and maintaining a small browser UI.
   That cost is probably decisive, so look at it first.
2. **Secrets.** WPE has no password manager, so *Secrets* step 3 would be ours to build
   (injecting into forms through a WebKit extension). Firefox's AutoConfig gets it for
   free.
3. **Lockdown.** Is there an equivalent of `policies.json`? With WPE, everything not
   built simply does not exist, which helps.
4. **Media.** GStreamer's plugin set against ffmpeg: size, codecs, and whether any
   GStreamer element brings in an interpreter.
5. **Measured, not estimated:** installed size, build time on a 4-core runner, idle RSS,
   and CPU during 1080p software video playback.

Firefox is the default. WPE has to win on 1–3 and be much smaller to replace it.

## Lockdown

Firefox and the OS each have escape hatches, and each one needs closing.

**Firefox: an enterprise `policies.json`**, shipped in the image (read-only) and merged with
per-deployment settings from a confext:

- Interactive kiosk, so **not** `--kiosk`, which removes the URL bar. Use a single
  fullscreen window with the navigation toolbar only: back, forward, reload, home and the
  URL bar. `userChrome.css` (locked with `toolkit.legacyUserProfileCustomizations`) hides
  the hamburger menu, tabs, sidebar and context-menu entries that lead to settings. Lock
  `browser.tabs` behaviour to one tab, or allow tabs; that is a product choice.
- `BlockAboutConfig`, `BlockAboutProfiles`, `BlockAboutAddons`, `DisableDeveloperTools`,
  `DisableFirefoxAccounts`, `DisableTelemetry`, `DisableAppUpdate` (updates come with
  the image), `ExtensionSettings` `*: blocked` plus whatever we allow, and
  `DontCheckDefaultBrowser`.
- `WebsiteFilter`: an allowlist where the deployment wants one. Block `file://`,
  `about:*`, `chrome://` and `view-source:`.
- `DownloadRestrictions`, `PromptForDownloadLocation=false` and the download directory on
  tmpfs; `PrintingEnabled=false`; and disable the file picker, because the GTK file
  chooser browses the filesystem.
- Pop-ups and `target=_blank`: keep them in one window
  (`browser.link.open_newwindow`, locked prefs).

**The OS:**

- **VT switching.** cage blocks Ctrl+Alt+Fn by default. Admin mode relies on that being
  a deliberate exception (below). Set `NAutoVTs=0`, so tty2 is the only getty there is.
- Mask `ctrl-alt-del.target`, set `kernel.sysrq=0`, and set logind's
  `HandlePowerKey=` and the like to taste.
- No USB storage (no `usb-storage` or automount in the kernel), no xdg-desktop-portal,
  and no session bus beyond what Firefox needs.
- The `kiosk` user owns nothing in `/var` except the Firefox profile directory.

## Admin mode

The design needs **no new code and no setuid**:

1. The kiosk runs `cage -s -- firefox …` on tty1. `-s` allows VT switching, which cage
   otherwise blocks.
2. tty2 has `agetty` → `login` for one account, `admin`. Logging in there needs the
   admin password, and that password **is** the gate. Ctrl+Alt+F2 is not a secret, and it
   does not need to be.
3. tty3 and up do not exist (`NAutoVTs=0`).
4. `TMOUT=` in the admin shell, and a unit that runs `chvt 1` when the session ends.
5. Text console on the display: DRM fbdev emulation, so tty2 is visible on the same
   screen.

A GUI terminal (foot) can come later. It needs a root-side way to start a privileged
session, because there is no setuid `su`, and the text console needs nothing.

**The password breaks a current invariant**: `shadow` is all `*` and the build fails on
a hash. Keep that rule. Bring the hash in at boot as a credential
(`kiosk.admin-password-hash`) and serve it without writing `/etc`: a userdb record in
`/run/userdb/admin.user`, which `nss-systemd` serves as a shadow entry to `pam_unix`
(`shadow: files systemd`). *Verify that nss-systemd's shadow support covers this before
building on it.* With no credential given, the admin account stays locked, and that is
the safe default.

## Taking credentials in

The existing machinery already does most of this: systemd credentials over fw_cfg/SMBIOS
(qemu), confexts on the `/var` disk, and `tools/vm.sh` and the launcher passing both.

- **Deployment config**: the start URL, allowlist and policy additions. A confext
  carrying `/etc/firefox/policies/…`, or a credential a oneshot renders into
  `/run/…/policies.json`, with Firefox pointed there.
- **Site logins**: an encrypted list, below.
- **Network**: as today.

**State:** the Firefox profile lives in RAM, so every boot and every idle reset starts a
clean browser. **Idle reset** uses wlroots' idle-notify protocol + `swayidle` to restart
the kiosk unit with a fresh profile. The secrets are injected again on each start, so
logins survive a reset and nothing a visitor did does. A `/var` disk is optional, for the
TPM state and logs, not for the profile.

## Secrets

The secrets are a list of entries (origin, username, password, optionally a note or TOTP
seed), encrypted at rest and handed to the VM, the way KeePass handles a database. Three
parts:

**1. Format and authoring: host-side.** Do not put a KDBX parser in the image. The only
established ones are KeePassXC, which is Qt and enormous, and libraries in Python or
Perl, which constraint 5 rules out. Instead, a host tool (`tools/kiosk-secrets.py`)
reads a KDBX file (`pykeepass`, which is maintained and in Debian; argue the dependency
like any other) or a CSV. It writes a plain JSON list and encrypts that as **one systemd
credential**, `kiosk.logins`. The KeePass database stays the authoring format on the
admin's machine, and the guest only ever sees systemd's format.

**2. Encryption and the key: `systemd-creds`, sealed to a vTPM.**

- The kiosk unit declares `LoadCredentialEncrypted=kiosk.logins`. systemd decrypts it
  into that service's `$CREDENTIALS_DIRECTORY` (RAM, readable only by the service) and
  nowhere else. That is already in the image and needs no new code.
- **Key: a virtual TPM** (`swtpm` on the host, `-tpmdev emulator` in qemu). The
  credential is sealed to that VM's TPM, so a copy of the file is useless elsewhere.
  Encrypting on the host without the TPM in hand is what `systemd-creds encrypt
  --tpm2-device-key=` (the guest's public SRK key, which `systemd-tpm2-setup` writes on
  first boot) is for. *Verify this against the systemd version we ship before building
  on it.* `vm.sh` and the launcher fetch that public key once per machine and hand it to
  the host tool.
- Kernel: `TCG_TPM`, `TCG_CRB` and `TCG_TIS` (VM devices: qemu's `tpm-crb`/`tpm-tis`).
  The TPM state lives with the VM on the host, alongside its `/var` disk.
- **Without a TPM** (Virtualization.framework has none), fall back to systemd's host
  key on the `/var` disk. Then whoever holds the disk holds the secrets. Say so in the
  docs rather than pretend.
- A **typed master password at boot** (`systemd-ask-password` on the console) is the
  third option. It is real KeePass behaviour, but it means a kiosk cannot come back
  unattended after a reboot. Offer it, but do not make it the default.

**3. Into the browser: Firefox AutoConfig.** `mozilla.cfg` is a supported enterprise
mechanism that runs privileged JavaScript at startup. It can read
`$CREDENTIALS_DIRECTORY/kiosk.logins` and call `Services.logins.addLoginsAsync()` into
the fresh in-RAM profile. Firefox then autofills on the matching origins. It is
JavaScript inside the JavaScript runtime the image already has, so it adds no
interpreter. It ships read-only in the image next to `policies.json`.

**Be honest about what this protects.** Encryption protects the list at rest and in
transit, and outside the VM. *Inside* a running interactive kiosk, the visitor can use
every login. That is the purpose of the kiosk, and with enough effort they can probably
extract one: an allowlisted site that echoes form input, or a password field's value read
through some page feature. Mitigations:

- Lock autofill to exact origins.
- Keep `layout.forms.reveal-password-button.enabled` false.
- Block `about:logins`.
- Keep `DisableDeveloperTools` set.
- Keep `WebsiteFilter` tight on any origin that has a login.

Use kiosk-specific, low-privilege accounts only, and say that in the docs.

## Where it lives

Three options, judged on what actually couples the two images.

**What the two images share** is most of what is hard here: glibc (everything compiles
against it), systemd, openssl and glib (coupled to sid), the kernel source, `build.sh`,
the builder, `builder/build-package.sh`, image assembly, the SBOM, the checks, the qemu
harness, the Python tooling, CI and the update/auto-merge machinery. **What differs**: a
GUI package set, a kernel config, `image/files`, tests, size budgets, and two of the
constraints.

### A. One repository, two images

This option as written below is the second-image form, which the extension form under
"As a system extension" supersedes. It is kept for comparison.

The variant model in [`image-variants.md`](image-variants.md) already anticipates this:
packages build once and an image selects them at assembly.

- `packages/` gains the GUI stack. `packages/kernel-display` `FOLLOWS=kernel` (the
  mechanism exists) and adds a `display.config` fragment.
- `image/` grows per-image file trees and package lists. Each image gets its own budget
  line, `test/image-files/` topics, tests and release assets.
- CLAUDE.md states the constraints **per image**: the kiosk has a JavaScript runtime by
  definition (constraint 5) and may not need to run containers (constraint 3).
- A glibc, systemd or openssl bump is tested against **both** images in the one PR that
  makes it. That is what neither of the other options can do, and it is what this
  project's auto-merge depends on.

The real cost is focus. The README, CLAUDE.md and CI all get a second subject, and the
kiosk's build time and noise land on every PR that touches shared files. CI path filters
help here, and so do separate release streams, so a red kiosk need not block the server
image.

### B. A fork

This is cheap on day one and the most expensive option after that. Every glibc, systemd,
openssl, kernel and patch-series bump, and every fix to the tooling, lands twice, and one
copy lags. The update bots halve the work, but merging upstream into the fork is a
permanent chore that gets harder as the two diverge. **Not recommended.**

### C. Common infrastructure in a third repository

This is attractive as a diagram, and premature in practice. The shared part is not a
library with an interface: it is the build driver, the builder image, the package
definitions, the checks and CI, all edited together today. Splitting them means
versioning a framework, coordinating releases across repos, and a glibc bump that cannot
be tested against both images until two more PRs land. Extract it only when the interface
has stopped moving, and only once two images *exist* and you can see what they actually
share.

**A middle way, if focus really matters:** a kiosk repository *downstream* of this one,
like `examples/` but bigger. It would consume a pinned kaetzli snapshot's published
package artifacts (glibc sysroot, staging trees, kernel source) and build only the GUI
packages and its own image. That keeps this repository clean, and it means base bumps
reach the kiosk only when it moves its pin. Today that would need package artifacts
published with snapshots (they are CI artifacts now) and a way to build against them, so
it is option A's assembly work plus a release interface.

**Recommendation:** A, in its simplest form: an **extension** in this repository (see
*As a system extension*), not a second image. The kiosk adds packages, a confext and a
test, and does not add a variant. The README gains one line in the extensions list rather
than a second subject.

## What you forgot to think about

**Build and infrastructure**

- **Packages build against each other.** Firefox needs GTK, which needs pango, which
  needs harfbuzz. Today every package compiles against *sid's* `-dev` headers and runs on
  our library, and that holds only while we are at least sid's version. Doing that for
  about 25 GUI libraries means 25 new `-dev` lines in `deps.txt` and 25 couplings to
  sid. [`extension-dependencies.md`](extension-dependencies.md) (`DEPENDS=`, a
  dependency sysroot) is the cleaner path and becomes a prerequisite.
- **The builder grows.** Firefox wants clang (present), nodejs, cbindgen and nasm, and a
  wasi sysroot unless built `--without-wasm-sandboxed-libraries`. `deps.txt` is *one*
  builder for every package. Either everyone pays for nodejs, or there is a second builder
  image, and that breaks "the one builder".
- **CI time.** Firefox takes about 2–3 h per arch on a 4-core runner, close to the
  6 h job limit with LTO. The cache serves it for two weeks, and then the scheduled fresh
  run rebuilds it. arm64 too.
- **Reproducibility.** The byte-identical promise has to hold for Firefox, mesa and
  fontconfig caches. Tor Browser shows that Firefox can do it, with effort. Budget for
  this, or relax the promise for this image and say so.
- **Hardening checks.** mozbuild and the Rust parts treat flags their own way.
  Expect `known-unhardened.txt` entries and arguments for each.
- **CVE volume.** Firefox has advisories every four weeks, so grype will be loud. ESR
  plus the auto-merge flow handles it, but the CVE reports need to stay readable.

**Constraints that have to be rewritten, not bent**

- Constraint 5: Firefox *is* a JavaScript runtime. State the exception: it is the
  payload. Also guard against Python and Lua sneaking in (WirePlumber, mesa's build
  scripts in `DESTDIR`, GTK's helper scripts).
- Constraint 3: does a kiosk need podman? Probably not. Say so per image.
- Constraint 2: virtio-gpu and virtio-snd are VM devices and fit. Hardware does not.

**Licensing and trademarks**

- **Firefox branding.** Mozilla's trademark policy governs shipping a build *named*
  Firefox, especially a modified one. Debian went through Iceweasel to get here. Build
  unbranded at first, and read the current policy before shipping official branding.
- **No DRM-protected content.** Widevine is non-free, so Netflix, Spotify's web player
  and some streaming players will not work. Make this a stated limit.
- **H.264/AAC patents.** ffmpeg is DFSG-free, but patent licensing is a deployment
  question in some jurisdictions. OpenH264 (Cisco's binary) is not something we can
  build and ship the same way.
- Hardware firmware, if bare metal ever happens (see above).

**Running it in the field**

- **Crash and leak recovery.** `Restart=always` on the kiosk unit, plus a scheduled
  restart, because a browser running for weeks leaks. Use systemd's watchdog (qemu's
  `i6300esb`) for a hung guest.
- **Display power.** Screen-off schedules, DPMS (wlr-output-power protocol) and blanking
  behaviour. A VM display has none of this, but hardware does.
- **Remote management.** ssh is there. Add a screenshot of what is actually on screen
  (`grim`, or qemu's `screendump` from the host) and a way to change the URL without a
  reboot (reload the confext).
- **Clock.** TLS fails on a wrong clock. timesyncd is there, but captive portals and
  proxies are kiosk-network classics.
- **Touch and keyboards.** In a VM, input is the host's keyboard and mouse, so no
  on-screen keyboard is needed. Once touchscreens or hardware come in, one is needed:
  Firefox has none on Linux, and whether cage hosts wvkbd or squeekboard (layer-shell,
  virtual-keyboard) needs checking. If it cannot, that decides between cage, labwc and
  sway.
- **Rotation, scaling, multiple monitors.** cage does one output well. Signage
  sometimes means a portrait screen.
- **Accessibility and locale.** The image is C-locale only. Firefox carries its own
  language packs, but fonts, hyphenation and the input method follow from the target
  languages.

**Testing**

- Today's tests read the serial console. A kiosk needs **visual tests**: qemu
  `screendump` over QMP compared against an expected frame, audio captured through qemu's
  `wav` audiodev, and a Marionette/WebDriver check (geckodriver in the debug extension,
  not the image).
- Escape tests: each lockdown item above gets a test that tries the escape and fails it.

## What else to decide

1. **Firefox or WPE WebKit**, from the spike.
2. **Audio**: ALSA direct vs PipeWire (Lua).
3. **Fonts and languages**, since they decide most of the size beyond Firefox itself.
4. **Tabs**: one tab, or several.
5. **Where it lives**: option A unless there is a reason against it.

## Order

1. [`extension-dependencies.md`](extension-dependencies.md) (`DEPENDS=`), piloted on its
   own suggested first edge, libpcap into tcpdump in `debug`. Then add CI by
   topological level. This is useful without the kiosk, and it is most of the
   infrastructure risk.
2. Kernel flavours. The TPM drivers go into `default`. `kernel-display` gets the
   additive-only check, the CI matrix and the release asset. Add boot tests that see
   `/dev/tpmrm0` on both flavours, and `/dev/dri/card0` plus an ALSA card on `display`.
3. The Wayland stack up to cage running a trivial client (`foot` or a test client), with a
   screendump test.
4. The browser spike, Firefox vs WPE, and the decision.
5. The browser, policies, kiosk unit and admin mode, with an escape test for each
   lockdown item.
6. Media: ffmpeg, audio, a playback test with an audio capture.
7. Secrets: vTPM in the harness (`swtpm`), `tools/kiosk-secrets.py`, the AutoConfig
   loader, and a test that a login from the list autofills on its origin and nowhere
   else.
8. Confext plumbing for deployment config, and idle reset.
9. A size budget for extensions, plus the SBOM, CVE report and release asset for
   `kiosk.raw`. These already exist for extensions, so check them at 400 MB.
