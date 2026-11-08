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
	"path/filepath"
	"strings"
	"time"

	"github.com/fwilhe2/kaetzli/go/internal/authenticode"
	"github.com/fwilhe2/kaetzli/go/internal/efivars"
	"github.com/fwilhe2/kaetzli/go/internal/qemu"
	"github.com/fwilhe2/kaetzli/go/internal/uki"
)

// cmdSecureBootFiles is what tools/sign-uki.sh runs (not in the usage: a tool's, not a
// user's command), the one implementation of signing and enrolling, shared with start:
//
//	kaetzli secureboot-files --uki IN.efi --template OVMF_VARS.fd --out DIR
//	                         [--key K.pem --cert C.pem] [--ext NAME --extensions DIR [--var]]
//
// DIR gets kaetzli.efi (IN, with the extensions' tables sealed into its command line,
// signed), ovmf-vars.fd (the template with a throwaway PK and KEK, the certificate in db
// and Secure Boot on), ovmf-vars-off.fd (the template as it is: Secure Boot off), db.crt
// and sealed (what the command line was made for, which kaetzli.qemu holds a boot to).
// Without --key the key is made for the run and never written: it proves the mechanism,
// not who built the image. --var says the boot attaches a /var disk, which comes before
// the extensions' disks and so changes their names in the tables.
func cmdSecureBootFiles(args []string) {
	var in, template, out, keyPath, certPath, extDir string
	var exts []string
	withVar := false
	for i := 0; i < len(args); i++ {
		next := func() string {
			if i+1 >= len(args) {
				die("%s needs a value", args[i])
			}
			i++
			return args[i]
		}
		switch args[i] {
		case "--uki":
			in = next()
		case "--template":
			template = next()
		case "--out":
			out = next()
		case "--key":
			keyPath = next()
		case "--cert":
			certPath = next()
		case "--ext":
			e := next()
			if !nameRE.MatchString(e) {
				die("'%s' is not an extension name", e)
			}
			exts = append(exts, e)
		case "--extensions":
			extDir = next()
		case "--var":
			withVar = true
		default:
			die("secureboot-files: unknown option '%s'", args[i])
		}
	}
	if in == "" || template == "" || out == "" {
		die("secureboot-files needs --uki, --template and --out")
	}
	if (keyPath == "") != (certPath == "") {
		die("--key and --cert go together")
	}
	if len(exts) > 0 && extDir == "" {
		die("--ext needs --extensions, the directory with extensions/ and confexts/")
	}
	if err := secureBootFiles(in, template, out, keyPath, certPath, exts, extDir, withVar); err != nil {
		die("%v", err)
	}
}

func secureBootFiles(in, template, out, keyPath, certPath string, exts []string, extDir string, withVar bool) error {
	image, err := os.ReadFile(in)
	if err != nil {
		return err
	}
	if len(exts) > 0 {
		m := qemu.Machine{Arch: "amd64", Sysexts: exts, ExtDir: extDir}
		if withVar {
			m.VarDisk = "var" // only its presence counts: it is the disk before the extensions'
		}
		base, err := uki.Cmdline(image)
		if err != nil {
			return err
		}
		sealed, err := m.SealedCmdline(base)
		if err != nil {
			return err
		}
		if image, err = uki.SetCmdline(image, sealed); err != nil {
			return fmt.Errorf("the extensions' tables: %w", err)
		}
	}
	var key crypto.Signer
	var cert *x509.Certificate
	if keyPath != "" {
		if key, cert, err = loadKeyPair(keyPath, certPath); err != nil {
			return err
		}
	} else {
		fmt.Println(">> no --key: signing with a throwaway key, for tests and local use only")
		if key, cert, err = throwawayKey("kaetzli throwaway Secure Boot key"); err != nil {
			return err
		}
	}
	signed, err := authenticode.Sign(image, cert, key)
	if err != nil {
		return fmt.Errorf("signing %s: %w", in, err)
	}
	tmpl, err := os.ReadFile(template)
	if err != nil {
		return err
	}
	pk, err := newCert("kaetzli machine PK")
	if err != nil {
		return err
	}
	kek, err := newCert("kaetzli machine KEK")
	if err != nil {
		return err
	}
	vars, err := efivars.EnrollSecureBoot(tmpl, pk, kek, cert, owner)
	if err != nil {
		return fmt.Errorf("%s: %w", template, err)
	}
	var sealedFor strings.Builder
	fmt.Fprintf(&sealedFor, "extensions=%s\nvar=%t\n", strings.Join(exts, " "), withVar)
	if err := os.MkdirAll(out, 0o755); err != nil {
		return err
	}
	for name, b := range map[string][]byte{
		"kaetzli.efi":      signed,
		"ovmf-vars.fd":     vars,
		"ovmf-vars-off.fd": tmpl,
		"db.crt":           pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert.Raw}),
		"sealed":           []byte(sealedFor.String()),
	} {
		if err := writeFileAtomic(filepath.Join(out, name), b, 0o644); err != nil {
			return err
		}
	}
	return nil
}

// loadKeyPair reads a PEM private key (PKCS#8, as openssl req writes it, or PKCS#1) and
// its certificate, and checks they belong together.
func loadKeyPair(keyPath, certPath string) (crypto.Signer, *x509.Certificate, error) {
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
		return nil, nil, fmt.Errorf("%s and %s must be PEM", keyPath, certPath)
	}
	var signer crypto.Signer
	if k, err := x509.ParsePKCS8PrivateKey(kblk.Bytes); err == nil {
		s, ok := k.(crypto.Signer)
		if !ok {
			return nil, nil, fmt.Errorf("%s is not a signing key", keyPath)
		}
		signer = s
	} else if k, err1 := x509.ParsePKCS1PrivateKey(kblk.Bytes); err1 == nil {
		signer = k
	} else {
		return nil, nil, fmt.Errorf("%s: %w", keyPath, err)
	}
	cert, err := x509.ParseCertificate(cblk.Bytes)
	if err != nil {
		return nil, nil, fmt.Errorf("%s: %w", certPath, err)
	}
	if pub, ok := cert.PublicKey.(*rsa.PublicKey); !ok || !pub.Equal(signer.Public()) {
		return nil, nil, errors.New("the key is not the certificate's (or not RSA, which UEFI needs)")
	}
	return signer, cert, nil
}

// throwawayKey is a key and its self-signed certificate that live only as long as the run.
func throwawayKey(cn string) (crypto.Signer, *x509.Certificate, error) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, nil, err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 127))
	if err != nil {
		return nil, nil, err
	}
	now := time.Now().UTC()
	tmpl := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: cn},
		NotBefore: now.Add(-time.Hour), NotAfter: now.AddDate(0, 1, 0)}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		return nil, nil, err
	}
	cert, err := x509.ParseCertificate(der)
	return key, cert, err
}
