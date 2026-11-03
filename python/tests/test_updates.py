"""tools/upstream.py, release-notes.py and check-updates.py, against a fake web.

The packages are the real ones, read from their env.sh; what upstream "released" is
built from each one's own tarball name and pins, so a version bump changes nothing here.
The bash versions' output was compared live, on every package (python/README.md has
no fixture for a moving target): identical but for the order of glib and glibc, which
the bash glob took from the host's locale.
"""

import json
import re

import pytest

from kaetzli import repo
from kaetzli.updates import check, release_notes, upstream
from kaetzli.updates.http import FetchError


class FakeHttp:
    """URL → body, by regex; a HEAD succeeds for any URL in `present`."""

    def __init__(self, pages=None, present=()):
        self.pages = dict(pages or {})
        self.present = set(present)
        self.asked: list[str] = []

    def _find(self, url):
        self.asked.append(url)
        for pattern, body in self.pages.items():
            if re.fullmatch(pattern, url):
                return body
        raise FetchError(f"{url}: HTTP Error 404: Not Found")

    def get(self, url):
        return self._find(url)

    def github(self, url):
        body = self._find(url)
        return body if isinstance(body, str) else json.dumps(body)

    def exists(self, url):
        return url in self.present


def listing(*names):
    return "\n".join(f'<a href="{n}">{n}</a>  2026-01-01' for n in names)


def bumped(pkg, version, vendor=False):
    """The tarball name the package would have at version."""
    src = upstream.Source.of(pkg, vendor)
    return src.tarball.replace(src.version, version)


@pytest.fixture(autouse=True)
def _root(monkeypatch):
    monkeypatch.chdir(repo.ROOT)


class TestDefaultRegex:
    def test_the_version_becomes_a_group(self):
        rx = upstream.default_regex("coreutils-9.8.tar.xz", "9.8", upstream.NUM)
        m = re.fullmatch(rx, "coreutils-10.0.tar.xz")
        assert m and m[1] == "10.0"
        assert not re.fullmatch(rx, "coreutils-10.0.tar.gz")
        assert not re.fullmatch(rx, "coreutilsX10.0.tar.xz")

    def test_openssh_portable_suffix(self):
        rx = upstream.default_regex("openssh-10.2p1.tar.gz", "10.2p1", upstream.NUM)
        assert re.fullmatch(rx, "openssh-10.3p1.tar.gz")[1] == "10.3p1"


class TestLatest:
    def test_an_index_listing(self):
        src = upstream.Source.of("coreutils")
        names = [bumped("coreutils", v) for v in ("9.1", "999.0", "999.1-rc1", "10.0")]
        http = FakeHttp({re.escape(src.url.rsplit("/", 1)[0] + "/"): listing(*names)})
        assert upstream.latest(src, http) == "999.0"

    def test_nothing_matching_is_an_error(self):
        src = upstream.Source.of("coreutils")
        http = FakeHttp({".*": listing("README")})
        with pytest.raises(upstream.UpstreamError, match="coreutils: found no versions upstream"):
            upstream.latest(src, http)

    def test_an_unreachable_listing_is_an_error(self):
        with pytest.raises(upstream.UpstreamError, match="404"):
            upstream.latest(upstream.Source.of("coreutils"), FakeHttp())

    def test_subdirectories_newest_first_until_one_holds_a_release(self):
        src = upstream.Source.of("kernel")
        major = src.version.split(".")[0]
        nxt = int(major) + 1
        http = FakeHttp({
            re.escape(src.index): listing(f"v{major}.x/", f"v{nxt}.x/", "v2.6/"),
            # the next series' directory holds only a release candidate so far
            re.escape(f"{src.index}v{nxt}.x/"): listing(f"linux-{nxt}.0-rc1.tar.xz"),
            re.escape(f"{src.index}v{major}.x/"): listing(
                f"linux-{major}.9.tar.xz", f"linux-{major}.10.tar.xz", f"linux-{major}.11.tar.xz",
                f"linux-{major}.11.2.tar.xz", f"linux-{major}.11.10.tar.xz",
            ),
        })  # fmt: skip
        # UPSTREAM_IGNORE drops x.y, the first release of a series (CLAUDE.md: from x.y.1)
        assert upstream.latest(src, http) == f"{major}.11.10"

    def test_github_tags_and_releases_with_sed(self):
        src = upstream.Source.of("expat")  # UPSTREAM_SED: R_2_8_2 → 2.8.2
        api = f"https://api.github.com/repos/{src.github}"
        http = FakeHttp({
            re.escape(f"{api}/tags?per_page=100"): [{"name": "R_2_8_2"}, {"name": "R_99_0_0"}],
            re.escape(f"{api}/releases?per_page=100"): [
                {"tag_name": "R_99_1_0", "draft": False, "prerelease": True},
                {"tag_name": "R_99_0_1", "draft": False, "prerelease": False},
                {"tag_name": "R_99_2_0", "draft": True, "prerelease": False},
            ],
        })  # fmt: skip
        assert upstream.latest(src, http) == "99.0.1"

    def test_a_leading_v_is_dropped(self):
        src = upstream.Source.of("strace")
        api = f"https://api.github.com/repos/{src.github}"
        http = FakeHttp({re.escape(f"{api}/tags?per_page=100"): [{"name": "v99.1"}],
                         re.escape(f"{api}/releases?per_page=100"): []})  # fmt: skip
        assert upstream.latest(src, http) == "99.1"

    def test_ignore_is_anchored_as_given(self):
        # shadow's UPSTREAM_IGNORE carries its own ^ and $: ^^[0-9]{8}$$ still matches
        src = upstream.Source.of("shadow")
        assert src.ignore.startswith("^")
        api = f"https://api.github.com/repos/{src.github}"
        http = FakeHttp({re.escape(f"{api}/tags?per_page=100"): [{"name": "20991231"},
                                                                 {"name": "99.0"}],
                         re.escape(f"{api}/releases?per_page=100"): []})  # fmt: skip
        assert upstream.latest(src, http) == "99.0"

    def test_a_number_pattern_of_its_own(self):
        src = upstream.Source.of("tzdata")  # 2026e, from GitHub
        tags = [{"name": v} for v in ("2099b", "2099a", "2099", "2099b-rc")]
        http = FakeHttp({r".*/tags\?.*": tags, r".*/releases\?.*": []})
        assert upstream.latest(src, http) == "2099b"

    def test_vendor_without_a_version_of_its_own(self):
        with pytest.raises(upstream.UpstreamError, match="no VENDOR_VERSION"):
            upstream.Source.of("coreutils", vendor=True)

    def test_local_source_has_no_upstream(self):
        with pytest.raises(upstream.UpstreamError, match="fetch: local source, no upstream"):
            upstream.Source.of("fetch")

    def test_package_name(self):
        assert upstream.package_name("packages/bash/") == "bash"
        with pytest.raises(upstream.UpstreamError, match="unknown package 'nope'"):
            upstream.package_name("nope")


class TestReleaseNotes:
    def test_kernel(self):
        assert release_notes.url("kernel", "7.4.1", FakeHttp()) == (
            "https://cdn.kernel.org/pub/linux/kernel/v7.x/ChangeLog-7.4.1"
        )

    def test_github_release_by_normalized_tag(self):
        (gh,) = repo.env_get("expat", "UPSTREAM_GITHUB")
        http = FakeHttp({".*": [
            {"tag_name": "R_99_0_0", "html_url": "https://x/rc", "prerelease": True},
            {"tag_name": "R_99_0_0", "html_url": "https://x/final"},
        ]})  # fmt: skip
        assert release_notes.url("expat", "99.0.0", http) == "https://x/final"
        assert http.asked == [f"https://api.github.com/repos/{gh}/releases?per_page=100"]

    def test_none(self):
        assert release_notes.url("coreutils", "99.0", FakeHttp()) is None
        assert release_notes.url("expat", "99.0.0", FakeHttp()) is None


class TestCheck:
    def test_up_to_date(self):
        src = upstream.Source.of("coreutils")
        http = FakeHttp({".*": listing(src.tarball)})
        r = check.check("coreutils", http)
        assert r.lines == [f"{'coreutils':<12} {src.version:<10} up to date"]
        assert (r.updates, r.failed) == ([], False)

    def test_an_update_is_confirmed_downloadable(self):
        src = upstream.Source.of("coreutils")
        url = check.url_for_version("coreutils", "999.0")
        assert url == src.url.replace(src.version, "999.0")
        http = FakeHttp({".*": listing(bumped("coreutils", "999.0"))}, present=[url])
        r = check.check("coreutils", http)
        assert r.lines == [f"{'coreutils':<12} {src.version:<10} -> {'999.0':<10} {url}"]
        assert r.updates == [{"package": "coreutils", "kind": "version",
                              "current": src.version, "latest": "999.0", "url": url}]  # fmt: skip

    def test_an_announced_release_that_is_not_there(self):
        src = upstream.Source.of("coreutils")
        r = check.check("coreutils", FakeHttp({".*": listing(bumped("coreutils", "999.0"))}))
        url = check.url_for_version("coreutils", "999.0")
        assert r.lines == [
            f"{'coreutils':<12} {src.version:<10} ERROR: 999.0 announced but {url}"
            " is not downloadable"
        ]
        assert r.failed and not r.updates

    def test_a_failed_lookup(self):
        r = check.check("coreutils", FakeHttp())
        assert r.lines[0].endswith("ERROR: upstream lookup failed")
        assert r.errors[0].startswith("error: coreutils: ")
        assert r.failed

    def test_local_source(self):
        (version,) = repo.env_get("fetch", "VERSION")
        r = check.check("fetch", FakeHttp())
        assert r.lines == [f"{'fetch':<12} {version:<10} local source, no upstream"]

    def test_follows_and_its_own_vendor_tarball(self):
        version, follows, name, vendor = repo.env_get(
            "linux-tools", "VERSION", "FOLLOWS", "VENDOR_NAME", "VENDOR_VERSION"
        )
        url = check.url_for_version("linux-tools", "99.0", "VENDOR_")
        assert url != check.url_for_version("linux-tools", "99.0")
        http = FakeHttp({".*": listing(bumped("linux-tools", "99.0", vendor=True))}, present=[url])
        r = check.check("linux-tools", http)
        label = f"linux-tools/{name}"
        assert r.lines == [
            f"{'linux-tools':<12} {version:<10} follows {follows}",
            f"{label:<12} {vendor:<10} -> {'99.0':<10} {url}",
        ]
        assert r.updates == [{"package": "linux-tools", "kind": "vendor", "current": vendor,
                              "latest": "99.0", "url": url, "component": name}]  # fmt: skip

    def test_an_unknown_package(self):
        r = check.check("nope", FakeHttp())
        assert r.failed and r.errors == ["error: unknown package 'nope' (no packages/nope/env.sh)"]

    def test_json_is_compact_in_package_order(self, capsys):
        a, b = upstream.Source.of("acl"), upstream.Source.of("attr")
        urls = [check.url_for_version(p, "99.0") for p in ("acl", "attr")]
        http = FakeHttp(
            {
                re.escape(a.url.rsplit("/", 1)[0] + "/"): listing(bumped("acl", "99.0")),
                re.escape(b.url.rsplit("/", 1)[0] + "/"): listing(bumped("attr", "99.0")),
            },
            present=urls,
        )
        assert check.main(["--json", "attr", "acl"], http=http) == 0
        out = capsys.readouterr().out
        assert out.startswith('[{"package":"attr","kind":"version",')
        assert [u["package"] for u in json.loads(out)] == ["attr", "acl"]

    def test_json_with_nothing_is_an_empty_list(self, capsys):
        src = upstream.Source.of("coreutils")
        assert check.main(["--json", "coreutils"], http=FakeHttp({".*": listing(src.tarball)})) == 0
        assert capsys.readouterr().out == "[]\n"

    def test_an_unknown_option(self, capsys):
        assert check.main(["--nope"]) == 1
        assert "unknown option: --nope" in capsys.readouterr().err
