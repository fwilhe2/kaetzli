# nginx on kaetzli

A template for putting C software that kaetzli does not package into an image built `FROM`
the published kaetzli container image. nginx is the worked example. The pattern carries
over to anything with a `configure && make install`.

```sh
podman build -t localhost/kaetzli-nginx -f examples/nginx/Containerfile examples/nginx
podman run --rm -p 8080:8080 localhost/kaetzli-nginx
curl http://localhost:8080/
```

`docker build` works the same way. The final image is about 2 MB larger than the kaetzli
image it is built on.

This is meant for a host with a real disk. Building it inside a kaetzli VM works too, but
the build stage installs a compiler into Debian, which needs more room than a default VM
has: its container storage is RAM, capped at half of 1 GiB. Boot the VM with memory and a
`/var` disk; container storage then lives on that disk, root's and `user`'s alike:

```sh
MEM=4096 VAR_DISK=output/var.img VAR_SIZE=20G SYSEXTS=podman ./tools/boot-qemu.py
./tools/ssh.sh --root    # or without --root: /home is a symlink into /var
```

## Why this needs a template at all

kaetzli has no package manager, so `apt install nginx` has no equivalent. You can't simply
copy Debian's nginx binary across either. A binary records which glibc and library
versions it was linked against, and it only starts on a system that has those versions
or newer. So the software has to be compiled somewhere whose libraries are *no newer*
than the ones kaetzli ships, and only the result is copied in.

The [`Containerfile`](Containerfile) does this in three stages:

1. **`runtime`** is the kaetzli image, given a name so the other stages can read from it.
2. **`build`** is Debian *stable* (trixie) with a compiler and the `-dev` packages. nginx
   is downloaded, its checksum checked, and it is compiled and installed into `/out`.
3. **The final stage** is kaetzli with `/out` copied over it.

### Why Debian stable, and not sid or testing

glibc is backward compatible: a binary compiled against an older glibc runs on a newer
one, but not the other way round. Debian stable never changes its glibc during its
lifetime (trixie stays at 2.41), and kaetzli only ever moves forward (2.44 at the time of
writing). So a stable builder can't drift ahead of the runtime. sid moves constantly
and eventually would.

The same reasoning covers the other libraries nginx links against, as long as kaetzli ships
a newer version:

| library | compiled against (trixie) | runs against (kaetzli) |
| --- | --- | --- |
| glibc | 2.41 | 2.44 |
| OpenSSL | 3.5.x | 3.5.9 |
| zlib | 1.3.1 | 1.3.2 |
| pcre2 | 10.45 | *not in the container image*, so bundled |

`nginx -V` in the built image says this in its own words: *built with OpenSSL 3.5.7 …
(running with OpenSSL 3.5.9 …)*.

## Libraries kaetzli does not have

pcre2 is in the kaetzli *disk* image, but the *container* image removes it together with
podman and glib, which are its only users there. nginx needs it for regular expressions,
so the build stage copies Debian's `libpcre2-8.so.0` into `/out/usr/lib`, and the final
stage runs `ldconfig`. Debian's copy was compiled against trixie's glibc, so the reasoning
above covers it as well.

That is the general recipe for a library kaetzli lacks. The alternatives:

- **Bundle it**, as here. Add the library to the `cp -L` line. Use `-L` because the name a
  binary asks for is a symlink to the versioned file.
- **Link it statically.** nginx can do this itself with `--with-pcre=<source dir>` (and
  likewise `--with-openssl=`, `--with-zlib=`). This needs the library's source tarball in
  the build stage, and you get one binary with nothing to bundle.
- **Configure it out.** For nginx, `--without-http_rewrite_module` drops the need for pcre2
  entirely, at the cost of `rewrite` and regex `location`s.

You don't have to work out which libraries are missing: the build tells you (see below).

## The two checks

The build fails if the result would not run, rather than leaving that to `docker run`.

**In the build stage**, every ELF file under `/out` is checked against kaetzli's `/usr/lib`
(copied in from the `runtime` stage):

- every library it needs (`NEEDED`) has to exist in kaetzli or in the bundle, and
- every symbol version it asks for (`GLIBC_2.38`, `OPENSSL_3.0.0`, …) has to be defined
  by the copy it will actually get.

A failure names the file and the problem:

```
/usr/bin/nginx: no libpcre2-8.so.0 in kaetzli or the bundle
```

These are the repo's `test/check-rootfs-deps.py` and `test/check-symbol-versions.py`, cut
down to one image.

**In the final stage**, `LD_BIND_NOW=1 nginx -V && nginx -t` runs the binary on kaetzli's own
loader and libraries during the build, which is the check that can't be wrong.
`LD_BIND_NOW` makes the loader resolve every symbol at startup instead of on first use.
Without it, a missing *unversioned* symbol (not every library versions its symbols) would
only fail on the first request that happened to call it.

That `RUN` works because kaetzli ships bash, so the final stage can run commands like any
other image.

## Runtime layout

| path | |
| --- | --- |
| `/usr/bin/nginx` | the binary |
| `/etc/nginx/nginx.conf` | written by the `Containerfile`; replace it with a bind mount or a `COPY` |
| `/usr/share/nginx/html` | the document root |
| `/var/cache/nginx`, `/run/nginx` | temp files and the pid file, owned by uid 1000 |
| stdout / stderr | access and error logs, so `podman logs` shows them |

nginx runs as the image's existing `user` account (uid 1000), never as root, and so
listens on 8080 rather than 80. It listens on both IPv4 and IPv6, because rootless
podman's port forwarding reaches the container over IPv6. With only `listen 8080;` the
connection is reset from the host while working from inside the container.

`USER` is given as `1000:1000` rather than `user`, so the template works with any kaetzli
image. Images published before the account files were made comment-free have comment
lines in `/etc/passwd`, which glibc skips but buildah's own passwd parser (what
`podman build` uses) rejects, so `USER user` fails there with *unknown user*. On a current
image either form works.

## Adapting it

- **Pin both base images by digest.** `KAETZLI` and `DEBIAN` are build arguments; set them to
  `…/kaetzli:<commit>@sha256:…` and `debian:trixie-slim@sha256:…` so that a rebuild uses the
  same inputs. `latest` is fine for trying the template out, not for anything you deploy.
- **Bumping nginx** is two lines, `NGINX_VERSION` and `NGINX_SHA256`. The checksum is
  checked before anything is unpacked.
- **Another program** keeps the structure: change the `apt-get install` list, the download
  and the `configure` line, list any bundled libraries, and keep both checks.
- **When Debian stable becomes the next one** (forky), compare its glibc with kaetzli's
  before switching. The approach only holds while the builder's glibc is the older of
  the two.

## Limits

- **Architecture**: the build is native. Building for arm64 on an amd64 machine needs
  qemu-user, because both checks run binaries.
- **When stable is not new enough.** If a program needs a newer compiler or newer headers
  than Debian stable has, this approach runs out. The fix is to compile against kaetzli's
  own glibc with `--sysroot`, the way `builder/build-package.sh` does for every package
  here. That can't be done from the published image, which has no headers, `crt*.o` or
  static libraries. It would need an SDK image (the untrimmed staging tree) published
  next to `kaetzli`, which does not exist yet.
- **Unversioned libraries with drifting ABIs.** The symbol version check can't see an
  ABI change in a library that does not version its symbols. glib is the example the repo
  itself is careful about. `LD_BIND_NOW` catches a missing symbol, but not a changed
  struct layout.
