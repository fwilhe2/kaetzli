package firmware

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// descriptorJSON is a firmware descriptor as distributions ship them (qemu's
// docs/interop/firmware.json), with the given features and formats.
func descriptorJSON(code, vars, format, arch string, features ...string) string {
	q := func(s []string) string { return `"` + strings.Join(s, `", "`) + `"` }
	return `{
    "description": "test",
    "interface-types": ["uefi"],
    "mapping": {
        "device": "flash",
        "mode": "split",
        "executable": {"filename": "` + code + `", "format": "` + format + `"},
        "nvram-template": {"filename": "` + vars + `", "format": "` + format + `"}
    },
    "targets": [{"architecture": "` + arch + `", "machines": ["pc-q35-*"]}],
    "features": [` + q(features) + `],
    "tags": []
}`
}

func write(t *testing.T, dir, name, content string) {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

// Fedora's set, shortened: the 4M builds are qcow2, so the raw 2M one is the answer.
func TestTheRawSecureBootBuildWithoutEnrolledKeysIsChosen(t *testing.T) {
	d := t.TempDir()
	write(t, d, "30-4m-qcow2-sb-enrolled.json", descriptorJSON("/c4.qcow2", "/v4e.qcow2", "qcow2", "x86_64", "requires-smm", "secure-boot", "enrolled-keys"))
	write(t, d, "31-2m-raw-sb-enrolled.json", descriptorJSON("/c2sb.fd", "/v2e.fd", "raw", "x86_64", "requires-smm", "secure-boot", "enrolled-keys"))
	write(t, d, "40-4m-qcow2-sb.json", descriptorJSON("/c4sb.qcow2", "/v4.qcow2", "qcow2", "x86_64", "requires-smm", "secure-boot"))
	write(t, d, "41-2m-raw-sb.json", descriptorJSON("/c2sb.fd", "/v2.fd", "raw", "x86_64", "requires-smm", "secure-boot"))
	write(t, d, "51-2m-raw-nosb.json", descriptorJSON("/c2.fd", "/v2.fd", "raw", "x86_64"))
	write(t, d, "61-amdsev.json", `{"interface-types": ["uefi"], "mapping": {"device": "memory", "filename": "/sev.fd"},
		"targets": [{"architecture": "x86_64", "machines": ["pc-q35-*"]}], "features": ["amd-sev", "secure-boot", "requires-smm"]}`)
	write(t, d, "60-aarch64.json", descriptorJSON("/a.fd", "/av.fd", "raw", "aarch64", "secure-boot"))
	f, err := SecureBoot([]string{d}, "x86_64")
	if err != nil {
		t.Fatal(err)
	}
	if f.Code != "/c2sb.fd" || f.VarsTemplate != "/v2.fd" {
		t.Fatalf("chose %+v, want the raw secure-boot build with the empty store", f)
	}
}

// Debian's (and Ubuntu's) ovmf, verbatim: no "mode", and amd-sev among the features of
// the ordinary build.
func TestDebiansSecureBootBuildIsChosen(t *testing.T) {
	d := t.TempDir()
	write(t, d, "40-edk2-x86_64-secure-enrolled.json", descriptorJSON("/usr/share/OVMF/OVMF_CODE_4M.ms.fd", "/usr/share/OVMF/OVMF_VARS_4M.ms.fd", "raw", "x86_64", "acpi-s3", "amd-sev", "enrolled-keys", "requires-smm", "secure-boot", "verbose-dynamic"))
	write(t, d, "50-edk2-x86_64-secure.json", `{
    "description": "UEFI firmware for x86_64, with Secure Boot, required SMM, empty varstore",
    "interface-types": ["uefi"],
    "mapping": {
        "device": "flash",
        "executable": {"filename": "/usr/share/OVMF/OVMF_CODE_4M.secboot.fd", "format": "raw"},
        "nvram-template": {"filename": "/usr/share/OVMF/OVMF_VARS_4M.fd", "format": "raw"}
    },
    "targets": [{"architecture": "x86_64", "machines": ["pc-q35-*"]}],
    "features": ["acpi-s3", "amd-sev", "requires-smm", "secure-boot", "verbose-dynamic"],
    "tags": []
}`)
	f, err := SecureBoot([]string{d}, "x86_64")
	if err != nil {
		t.Fatal(err)
	}
	if f.Code != "/usr/share/OVMF/OVMF_CODE_4M.secboot.fd" || f.VarsTemplate != "/usr/share/OVMF/OVMF_VARS_4M.fd" {
		t.Fatalf("chose %+v", f)
	}
}

func TestEnrolledKeysAreTakenWhenNothingElseIs(t *testing.T) {
	d := t.TempDir()
	write(t, d, "40-sb-enrolled.json", descriptorJSON("/c.fd", "/ve.fd", "raw", "x86_64", "requires-smm", "secure-boot", "enrolled-keys"))
	f, err := SecureBoot([]string{d}, "x86_64")
	if err != nil {
		t.Fatal(err)
	}
	if f.VarsTemplate != "/ve.fd" {
		t.Fatalf("chose %+v", f)
	}
}

// qemu's rule: a file in a later directory replaces the one of the same name in an earlier,
// an empty one masks it, and the order is by file name across all of them.
func TestLaterDirectoriesOverrideAndEmptyFilesMask(t *testing.T) {
	system, user := t.TempDir(), t.TempDir()
	write(t, system, "40-sb.json", descriptorJSON("/system.fd", "/sv.fd", "raw", "x86_64", "requires-smm", "secure-boot"))
	write(t, system, "50-sb.json", descriptorJSON("/other.fd", "/ov.fd", "raw", "x86_64", "requires-smm", "secure-boot"))
	write(t, user, "40-sb.json", descriptorJSON("/user.fd", "/uv.fd", "raw", "x86_64", "requires-smm", "secure-boot"))
	if f, _ := SecureBoot([]string{system, user}, "x86_64"); f.Code != "/user.fd" {
		t.Fatalf("chose %+v, want the user's override", f)
	}
	write(t, user, "40-sb.json", "")
	if f, _ := SecureBoot([]string{system, user}, "x86_64"); f.Code != "/other.fd" {
		t.Fatalf("chose %+v, want the next one once 40 is masked", f)
	}
}

func TestNoSecureBootFirmwareSaysWhatToInstall(t *testing.T) {
	d := t.TempDir()
	write(t, d, "51-nosb.json", descriptorJSON("/c.fd", "/v.fd", "raw", "x86_64"))
	write(t, d, "60-broken.json", "{not json")
	_, err := SecureBoot([]string{d, filepath.Join(d, "missing")}, "x86_64")
	if err == nil || !strings.Contains(err.Error(), "ovmf") {
		t.Fatalf("got %v, want an error naming the package", err)
	}
}
