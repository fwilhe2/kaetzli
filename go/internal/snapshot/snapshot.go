// Package snapshot fetches a rolling snapshot release (docs/proposals/release.md) into a
// cache directory, as tools/fetch-image.sh does: the latest resolved once from the
// redirect of releases/latest, every file from that snapshot's own URL, and nothing kept
// until each file the architecture needs matches SHA256SUMS.
package snapshot

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// Releases is where snapshots are published.
const Releases = "https://github.com/fwilhe2/kaetzli/releases"

var (
	nameRE  = regexp.MustCompile(`^[0-9]{8}\.[0-9]{6}-[0-9]+$`)
	assetRE = regexp.MustCompile(`^[A-Za-z0-9_][A-Za-z0-9._-]*$`)
	digest  = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

// IsName says whether s is a snapshot's name.
func IsName(s string) bool { return nameRE.MatchString(s) }

// Client talks to a releases URL.
type Client struct {
	Base string
	HTTP *http.Client
}

// New is a client for base (Releases unless a fork or mirror is wanted).
func New(base string) *Client {
	return &Client{Base: strings.TrimSuffix(base, "/"), HTTP: &http.Client{Timeout: 10 * time.Minute}}
}

// Latest is the newest snapshot's name, or "" when none is published: GitHub redirects
// releases/latest to releases/tag/<name>, or to the releases page when there is none.
func (c *Client) Latest() (string, error) {
	noFollow := *c.HTTP
	noFollow.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	resp, err := noFollow.Head(c.Base + "/latest")
	if err != nil {
		return "", err
	}
	resp.Body.Close()
	loc := resp.Header.Get("Location")
	if i := strings.Index(loc, "/releases/tag/"); i >= 0 {
		name := loc[i+len("/releases/tag/"):]
		if !IsName(name) {
			return "", fmt.Errorf("the latest release, %q, is not a snapshot", name)
		}
		return name, nil
	}
	return "", nil
}

// Sums parses SHA256SUMS strictly: a digest and a plain file name per line.
func Sums(data []byte) (map[string]string, error) {
	out := map[string]string{}
	s := bufio.NewScanner(bytes.NewReader(data))
	for s.Scan() {
		f := strings.Fields(s.Text())
		if len(f) == 0 {
			continue
		}
		if len(f) != 2 || !digest.MatchString(f[0]) {
			return nil, fmt.Errorf("malformed SHA256SUMS line: %q", s.Text())
		}
		if !assetRE.MatchString(f[1]) {
			return nil, fmt.Errorf("refusing asset name in SHA256SUMS: %q", f[1])
		}
		out[f[1]] = f[0]
	}
	return out, s.Err()
}

// Wanted maps the assets one architecture boots from to where they go in a bundle laid
// out as tools/fetch-image.sh lays it out (rootfs.erofs, bzImage, kaetzli.efi when the
// snapshot has one, extensions/, confexts/).
func Wanted(sums map[string]string, arch string) (map[string]string, error) {
	out := map[string]string{}
	for asset := range sums {
		switch {
		case asset == "rootfs-"+arch+".erofs":
			out[asset] = "rootfs.erofs"
		case asset == "rootfs-"+arch+".cmdline":
			out[asset] = "rootfs.cmdline"
		case asset == "kernel-"+arch:
			out[asset] = "bzImage"
		case asset == "uki-"+arch+".efi":
			out[asset] = "kaetzli.efi" // unsigned; start --secure-boot signs it
		default:
			for _, k := range [][2]string{{"sysext", "extensions"}, {"confext", "confexts"}} {
				for _, suffix := range []string{"raw", "dm-verity"} {
					tail := "-" + arch + "." + k[0] + "." + suffix
					if name, ok := strings.CutSuffix(asset, tail); ok && name != "" {
						out[asset] = k[1] + "/" + name + "." + suffix
					}
				}
			}
		}
	}
	for _, need := range []string{"rootfs-" + arch + ".erofs", "rootfs-" + arch + ".cmdline", "kernel-" + arch} {
		if _, ok := out[need]; !ok {
			return nil, fmt.Errorf("the snapshot lists no %s", need)
		}
	}
	return out, nil
}

func (c *Client) get(url string, w io.Writer) error {
	resp, err := c.HTTP.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s: %s", url, resp.Status)
	}
	_, err = io.Copy(w, resp.Body)
	return err
}

// Fetch downloads snapshot name for arch into cache/<name>/, unless it is there already,
// and returns that directory. A partial or refused download leaves nothing behind.
func (c *Client) Fetch(cache, name, arch string, log io.Writer) (string, error) {
	if !IsName(name) {
		return "", fmt.Errorf("%q is not a snapshot name", name)
	}
	dir := filepath.Join(cache, name+"-"+arch)
	if _, err := os.Stat(filepath.Join(dir, "SHA256SUMS")); err == nil {
		return dir, nil
	}
	if err := os.MkdirAll(cache, 0o755); err != nil {
		return "", err
	}
	tmp, err := os.MkdirTemp(cache, ".fetch-")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(tmp)

	base := c.Base + "/download/" + name
	var sumsBuf bytes.Buffer
	if err := c.get(base+"/SHA256SUMS", &sumsBuf); err != nil {
		return "", fmt.Errorf("snapshot %s has no SHA256SUMS (expired?): %w", name, err)
	}
	sums, err := Sums(sumsBuf.Bytes())
	if err != nil {
		return "", err
	}
	wanted, err := Wanted(sums, arch)
	if err != nil {
		return "", fmt.Errorf("snapshot %s: %w", name, err)
	}
	assets := make([]string, 0, len(wanted))
	for a := range wanted {
		assets = append(assets, a)
	}
	sort.Strings(assets)
	for _, asset := range assets {
		fmt.Fprintf(log, "   %s\n", asset)
		dst := filepath.Join(tmp, wanted[asset])
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return "", err
		}
		f, err := os.Create(dst)
		if err != nil {
			return "", err
		}
		h := sha256.New()
		err = c.get(base+"/"+asset, io.MultiWriter(f, h))
		if cerr := f.Close(); err == nil {
			err = cerr
		}
		if err != nil {
			return "", fmt.Errorf("could not download %s: %w", asset, err)
		}
		if got := hex.EncodeToString(h.Sum(nil)); got != sums[asset] {
			return "", fmt.Errorf("checksum mismatch: %s", asset)
		}
	}
	if err := os.WriteFile(filepath.Join(tmp, "SHA256SUMS"), sumsBuf.Bytes(), 0o644); err != nil {
		return "", err
	}
	if err := os.WriteFile(filepath.Join(tmp, "snapshot"), []byte(name+"\n"), 0o644); err != nil {
		return "", err
	}
	if err := os.Chmod(tmp, 0o755); err != nil {
		return "", err
	}
	if err := os.Rename(tmp, dir); err != nil {
		if errors.Is(err, os.ErrExist) {
			return dir, nil // another fetch got there first
		}
		return "", err
	}
	return dir, nil
}

// Cached lists the snapshots in cache for arch, newest first.
func Cached(cache, arch string) []string {
	entries, _ := os.ReadDir(cache)
	var out []string
	for _, e := range entries {
		name, ok := strings.CutSuffix(e.Name(), "-"+arch)
		if !ok || !IsName(name) {
			continue
		}
		if _, err := os.Stat(filepath.Join(cache, e.Name(), "SHA256SUMS")); err == nil {
			out = append(out, name)
		}
	}
	sort.Sort(sort.Reverse(sort.StringSlice(out)))
	return out
}

// Verify checks files of a cached snapshot (paths in dir, as Wanted lays them out) against
// the SHA256SUMS Fetch kept beside them. Fetch checked them once; what --secure-boot signs
// is checked again before each start, so the signature covers what the snapshot holds,
// not whatever the cache holds by then.
func Verify(dir, arch string, files ...string) error {
	data, err := os.ReadFile(filepath.Join(dir, "SHA256SUMS"))
	if err != nil {
		return fmt.Errorf("%s has no SHA256SUMS to check against: %w", dir, err)
	}
	sums, err := Sums(data)
	if err != nil {
		return err
	}
	wanted, err := Wanted(sums, arch)
	if err != nil {
		return err
	}
	asset := map[string]string{}
	for a, local := range wanted {
		asset[local] = a
	}
	for _, f := range files {
		a, ok := asset[f]
		if !ok {
			return fmt.Errorf("%s: SHA256SUMS lists nothing it would be", f)
		}
		file, err := os.Open(filepath.Join(dir, f))
		if err != nil {
			return err
		}
		h := sha256.New()
		_, err = io.Copy(h, file)
		file.Close()
		if err != nil {
			return err
		}
		if hex.EncodeToString(h.Sum(nil)) != sums[a] {
			return fmt.Errorf("%s does not match SHA256SUMS (%s): the cached snapshot changed since it was fetched", f, a)
		}
	}
	return nil
}
