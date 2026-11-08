package snapshot

import (
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func sum(b string) string {
	h := sha256.Sum256([]byte(b))
	return hex.EncodeToString(h[:])
}

// A snapshot of two arches, as tools/release.py assembles one.
var assets = map[string]string{
	"rootfs-amd64.erofs":                 "root amd64",
	"rootfs-amd64.cmdline":               "root=/dev/dm-0\n",
	"kernel-amd64":                       "kernel amd64",
	"uki-amd64.efi":                      "MZ uki amd64",
	"podman-amd64.sysext.raw":            "podman",
	"podman-amd64.sysext.dm-verity":      "0 8 verity\n",
	"kubernetes-amd64.confext.raw":       "conf",
	"kubernetes-amd64.confext.dm-verity": "0 8 verity\n",
	"rootfs-arm64.erofs":                 "root arm64",
	"rootfs-arm64.cmdline":               "root=/dev/dm-0\n",
	"kernel-arm64":                       "kernel arm64",
	"sbom-amd64.json":                    "{}",
}

func sums(a map[string]string) string {
	var b strings.Builder
	for name, content := range a {
		b.WriteString(sum(content) + "  " + name + "\n")
	}
	return b.String()
}

// serve is a releases URL with one snapshot, whose files can be overridden.
func serve(t *testing.T, name string, files map[string]string, latest string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/latest" {
			http.Redirect(w, r, latest, http.StatusFound)
			return
		}
		f, ok := strings.CutPrefix(r.URL.Path, "/download/"+name+"/")
		content, there := files[f]
		if !ok || !there {
			http.NotFound(w, r)
			return
		}
		_, _ = io.WriteString(w, content)
	}))
	t.Cleanup(srv.Close)
	return srv
}

func withSums(a map[string]string) map[string]string {
	out := map[string]string{"SHA256SUMS": sums(a)}
	for k, v := range a {
		out[k] = v
	}
	return out
}

const name = "20261104.163955-812"

func TestLatestFollowsTheRedirectToATag(t *testing.T) {
	srv := serve(t, name, nil, "/fwilhe2/kaetzli/releases/tag/"+name)
	got, err := New(srv.URL).Latest()
	if err != nil || got != name {
		t.Fatalf("Latest() = %q, %v", got, err)
	}
}

func TestLatestIsEmptyWithoutASnapshot(t *testing.T) {
	srv := serve(t, name, nil, "/fwilhe2/kaetzli/releases")
	got, err := New(srv.URL).Latest()
	if err != nil || got != "" {
		t.Fatalf("Latest() = %q, %v", got, err)
	}
}

func TestFetchLaysOutWhatAnArchBoots(t *testing.T) {
	srv := serve(t, name, withSums(assets), "")
	cache := t.TempDir()
	dir, err := New(srv.URL).Fetch(cache, name, "amd64", io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	for f, want := range map[string]string{
		"rootfs.erofs": "root amd64", "bzImage": "kernel amd64", "extensions/podman.raw": "podman",
		"confexts/kubernetes.dm-verity": "0 8 verity\n", "snapshot": name + "\n",
		"kaetzli.efi": "MZ uki amd64",
	} {
		b, err := os.ReadFile(filepath.Join(dir, f))
		if err != nil || string(b) != want {
			t.Errorf("%s = %q, %v; want %q", f, b, err, want)
		}
	}
	if _, err := os.Stat(filepath.Join(dir, "sbom-amd64.json")); err == nil {
		t.Error("an asset a boot does not need was downloaded")
	}
	if got := Cached(cache, "amd64"); len(got) != 1 || got[0] != name {
		t.Errorf("Cached = %v", got)
	}
}

func TestATamperedAssetLeavesNothingBehind(t *testing.T) {
	files := withSums(assets)
	files["podman-amd64.sysext.raw"] = "podmaN"
	srv := serve(t, name, files, "")
	cache := t.TempDir()
	_, err := New(srv.URL).Fetch(cache, name, "amd64", io.Discard)
	if err == nil || !strings.Contains(err.Error(), "checksum mismatch: podman-amd64.sysext.raw") {
		t.Fatalf("err = %v", err)
	}
	if entries, _ := os.ReadDir(cache); len(entries) != 0 {
		t.Errorf("the cache holds %v", entries)
	}
}

func TestSumsRefusesAPathOrAMalformedLine(t *testing.T) {
	for _, bad := range []string{
		sum("x") + "  ../../evil-amd64.sysext.raw\n",
		sum("x") + "  .hidden\n",
		"abc  rootfs-amd64.erofs\n",
		sum("x") + "  two words\n",
	} {
		if _, err := Sums([]byte(bad)); err == nil {
			t.Errorf("Sums accepted %q", bad)
		}
	}
}

func TestWantedRequiresTheRootAndKernel(t *testing.T) {
	a := map[string]string{"rootfs-amd64.erofs": "x", "rootfs-amd64.cmdline": "y"}
	if _, err := Wanted(a, "amd64"); err == nil || !strings.Contains(err.Error(), "kernel-amd64") {
		t.Fatalf("err = %v", err)
	}
}

// Snapshots before #284 have no UKI: they still boot, only not with --secure-boot.
func TestTheUKIIsOptional(t *testing.T) {
	a := map[string]string{"rootfs-arm64.erofs": "x", "rootfs-arm64.cmdline": "y", "kernel-arm64": "z"}
	w, err := Wanted(a, "arm64")
	if err != nil || len(w) != 3 {
		t.Fatalf("Wanted = %v, %v", w, err)
	}
}

func TestANameThatIsNotASnapshotsIsRefused(t *testing.T) {
	if _, err := New("http://127.0.0.1:1").Fetch(t.TempDir(), "../x", "amd64", io.Discard); err == nil {
		t.Fatal("fetched ../x")
	}
}
