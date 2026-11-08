package main

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
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

// The Secure Boot model (docs/guides/secure-boot.md): the project holds no key. Each user
// has one, made here once, and it signs the UKI of the snapshot the launcher verified
// against SHA256SUMS, with the machine's extensions' verity tables (verified with it)
// written into its command line first, so the kernel verifies them as it verifies the root. Each machine's firmware trusts that key alone: a variable store
// written from the firmware's template before every start, with the user's certificate in
// db and a PK and KEK made for it and thrown away (nothing ever needs to update the
// store in place; changing the key means writing it again).

// keyDir is where the user's Secure Boot key is: beside the machines, not in any one.
func keyDir() string { return filepath.Join(dataDir(), "secureboot") }

// userKey is the user's signing key and its certificate, made on first use.
func userKey() (crypto.Signer, *x509.Certificate, error) {
	keyPath, certPath := filepath.Join(keyDir(), "db.key"), filepath.Join(keyDir(), "db.crt")
	if isFile(keyPath) && isFile(certPath) {
		return loadKey(keyPath, certPath)
	}
	if isFile(keyPath) || isFile(certPath) {
		return nil, nil, fmt.Errorf("%s holds half a key pair (db.key and db.crt go together); move it out of the way", keyDir())
	}
	key, cert, err := newCert("kaetzli Secure Boot db", 10)
	if err != nil {
		return nil, nil, err
	}
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return nil, nil, err
	}
	if err := os.MkdirAll(keyDir(), 0o700); err != nil {
		return nil, nil, err
	}
	if err := writeFileAtomic(keyPath, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}), 0o600); err != nil {
		return nil, nil, err
	}
	if err := writeFileAtomic(certPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert.Raw}), 0o644); err != nil {
		return nil, nil, err
	}
	say("made your Secure Boot key: %s (keep it private; %s is its certificate)", keyPath, certPath)
	return key, cert, nil
}

func loadKey(keyPath, certPath string) (crypto.Signer, *x509.Certificate, error) {
	kb, err := os.ReadFile(keyPath)
	if err != nil {
		return nil, nil, err
	}
	cb, err := os.ReadFile(certPath)
	if err != nil {
		return nil, nil, err
	}
	kblk, _ := pem.Decode(kb)
	cblk, _ := pem.Decode(cb)
	if kblk == nil || cblk == nil {
		return nil, nil, fmt.Errorf("%s or %s is not PEM", keyPath, certPath)
	}
	k, err := x509.ParsePKCS8PrivateKey(kblk.Bytes)
	if err != nil {
		return nil, nil, fmt.Errorf("%s: %w", keyPath, err)
	}
	signer, ok := k.(crypto.Signer)
	if !ok {
		return nil, nil, fmt.Errorf("%s is not a signing key", keyPath)
	}
	cert, err := x509.ParseCertificate(cblk.Bytes)
	if err != nil {
		return nil, nil, fmt.Errorf("%s: %w", certPath, err)
	}
	return signer, cert, nil
}

// newCert is a self-signed RSA-2048 certificate, the size every UEFI firmware verifies.
func newCert(cn string, years int) (*rsa.PrivateKey, *x509.Certificate, error) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, nil, err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 127))
	if err != nil {
		return nil, nil, err
	}
	now := time.Now().UTC()
	tmpl := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: cn},
		NotBefore:    now.Add(-time.Hour),
		NotAfter:     now.AddDate(years, 0, 0),
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		return nil, nil, err
	}
	cert, err := x509.ParseCertificate(der)
	return key, cert, err
}

// sealedFiles are what a signature over img's UKI vouches for: the UKI, and the verity
// tables of m's extensions, which go into its command line and pin the extensions' bytes.
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

// prepareSecureBoot signs img's UKI, with m's extensions sealed into its command line,
// with the user's key and writes the machine's variable store, both into dir/secureboot/,
// and says which firmware boots them. A snapshot's files are checked against its
// SHA256SUMS first (fromSnapshot); an --image directory is the user's own build.
func prepareSecureBoot(dir, img string, m qemu.Machine, fromSnapshot bool) (*qemu.SecureBoot, error) {
	if fromSnapshot {
		if err := snapshot.Verify(img, m.Arch, sealedFiles(img, m)...); err != nil {
			return nil, fmt.Errorf("refusing to sign: %w (delete %s, and the next start fetches it again)", err, img)
		}
	}
	unsigned, err := os.ReadFile(filepath.Join(img, "kaetzli.efi"))
	if errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("%s has no kaetzli.efi, the UKI Secure Boot boots (snapshots before 20261108 have none: kaetzli update; or start --no-secure-boot)", img)
	} else if err != nil {
		return nil, err
	}
	if len(m.Sysexts) > 0 {
		base, err := uki.Cmdline(unsigned)
		if err != nil {
			return nil, err
		}
		sealed, err := m.SealedCmdline(base)
		if err != nil {
			return nil, err
		}
		if unsigned, err = uki.SetCmdline(unsigned, sealed); err != nil {
			return nil, fmt.Errorf("the extensions' tables: %w", err)
		}
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
	key, cert, err := userKey()
	if err != nil {
		return nil, err
	}
	signed, err := authenticode.Sign(unsigned, cert, key)
	if err != nil {
		return nil, fmt.Errorf("signing %s: %w", filepath.Join(img, "kaetzli.efi"), err)
	}
	_, pk, err := newCert("kaetzli machine PK", 10)
	if err != nil {
		return nil, err
	}
	_, kek, err := newCert("kaetzli machine KEK", 10)
	if err != nil {
		return nil, err
	}
	vars, err := efivars.EnrollSecureBoot(template, pk, kek, cert, owner)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", fw.VarsTemplate, err)
	}
	sb := filepath.Join(dir, "secureboot")
	if err := os.MkdirAll(sb, 0o755); err != nil {
		return nil, err
	}
	out := &qemu.SecureBoot{UKI: filepath.Join(sb, "kaetzli.efi"), Code: fw.Code, Vars: filepath.Join(sb, "ovmf-vars.fd")}
	if err := writeFileAtomic(out.UKI, signed, 0o644); err != nil {
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
