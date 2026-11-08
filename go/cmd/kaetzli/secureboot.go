package main

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"errors"
	"fmt"
	"math/big"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"github.com/fwilhe2/kaetzli/go/internal/authenticode"
	"github.com/fwilhe2/kaetzli/go/internal/efivars"
	"github.com/fwilhe2/kaetzli/go/internal/firmware"
	"github.com/fwilhe2/kaetzli/go/internal/qemu"
	"github.com/fwilhe2/kaetzli/go/internal/snapshot"
	"github.com/fwilhe2/kaetzli/go/internal/uki"
)

// The owner GUID of what the launcher enrolls, as tools/sign-uki.sh's.
var owner = efivars.MustGUID("8d5b5c0e-6b8f-4c5a-9d2e-4b6174a7a1e1")

// The Secure Boot model (docs/guides/secure-boot.md): nobody holds a key. Before each
// start the launcher takes the UKI of the snapshot it verified against SHA256SUMS, writes
// the machine's extensions' verity tables (verified with it) into its command line, and
// writes the machine's variable store fresh from the firmware's template with that one
// image's Authenticode digest in db, and a PK and KEK made for it and thrown away. The
// firmware then boots exactly that UKI and nothing else: not another image, not this
// machine's UKI of an earlier start, which a signing key would still let in.

// newCert is a self-signed RSA-2048 certificate, the size every UEFI firmware verifies:
// the machine's PK and KEK, whose keys nothing ever uses again.
func newCert(cn string) (*x509.Certificate, error) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 127))
	if err != nil {
		return nil, err
	}
	now := time.Now().UTC()
	tmpl := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: cn},
		NotBefore:    now.Add(-time.Hour),
		NotAfter:     now.AddDate(10, 0, 0),
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		return nil, err
	}
	return x509.ParseCertificate(der)
}

// sealedFiles are what the enrolled digest vouches for: the UKI, and the verity tables of
// m's extensions, which go into its command line and pin the extensions' bytes.
func sealedFiles(img string, m qemu.Machine) []string {
	files := []string{"kaetzli.efi"}
	for _, e := range m.Sysexts {
		for _, d := range []string{"extensions", "confexts"} {
			f := d + "/" + e + ".dm-verity"
			if isFile(filepath.Join(img, f)) {
				files = append(files, f)
			}
		}
	}
	return files
}

// prepareSecureBoot writes the machine's UKI (img's, with m's extensions sealed into its
// command line) and its variable store, which admits that UKI alone, into dir/secureboot/,
// and says which firmware boots them. A snapshot's files are checked against its
// SHA256SUMS first (fromSnapshot); an --image directory is the user's own build.
func prepareSecureBoot(dir, img string, m qemu.Machine, fromSnapshot bool) (*qemu.SecureBoot, error) {
	if fromSnapshot {
		if err := snapshot.Verify(img, m.Arch, sealedFiles(img, m)...); err != nil {
			return nil, fmt.Errorf("refusing to enroll it: %w (delete %s, and the next start fetches it again)", err, img)
		}
	}
	image, err := os.ReadFile(filepath.Join(img, "kaetzli.efi"))
	if errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("%s has no kaetzli.efi, the UKI Secure Boot boots (snapshots before 20261108 have none: kaetzli update; or start --no-secure-boot)", img)
	} else if err != nil {
		return nil, err
	}
	if len(m.Sysexts) > 0 {
		base, err := uki.Cmdline(image)
		if err != nil {
			return nil, err
		}
		sealed, err := m.SealedCmdline(base)
		if err != nil {
			return nil, err
		}
		if image, err = uki.SetCmdline(image, sealed); err != nil {
			return nil, fmt.Errorf("the extensions' tables: %w", err)
		}
	}
	digest, err := authenticode.Digest(image)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", filepath.Join(img, "kaetzli.efi"), err)
	}
	bin, err := exec.LookPath(m.Binary())
	if err != nil {
		return nil, fmt.Errorf("%s not found: install qemu", m.Binary())
	}
	fw, err := firmware.SecureBoot(firmware.Dirs(bin), "x86_64")
	if err != nil {
		return nil, err
	}
	template, err := os.ReadFile(fw.VarsTemplate)
	if err != nil {
		return nil, fmt.Errorf("the firmware's variable store template (%s): %w", fw.Descriptor, err)
	}
	pk, err := newCert("kaetzli machine PK")
	if err != nil {
		return nil, err
	}
	kek, err := newCert("kaetzli machine KEK")
	if err != nil {
		return nil, err
	}
	vars, err := efivars.Enroll(template, pk, kek, efivars.SHA256List(digest, owner), owner)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", fw.VarsTemplate, err)
	}
	sb := filepath.Join(dir, "secureboot")
	if err := os.MkdirAll(sb, 0o755); err != nil {
		return nil, err
	}
	out := &qemu.SecureBoot{UKI: filepath.Join(sb, "kaetzli.efi"), Code: fw.Code, Vars: filepath.Join(sb, "ovmf-vars.fd")}
	if err := writeFileAtomic(out.UKI, image, 0o644); err != nil {
		return nil, err
	}
	if err := writeFileAtomic(out.Vars, vars, 0o644); err != nil {
		return nil, err
	}
	return out, nil
}

// writeFileAtomic writes beside path and renames, so a reader never sees half a file.
func writeFileAtomic(path string, b []byte, mode os.FileMode) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, mode); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
