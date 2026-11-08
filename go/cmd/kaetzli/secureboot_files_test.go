package main

import (
	"bytes"
	"crypto/x509"
	"encoding/binary"
	"encoding/pem"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/fwilhe2/kaetzli/go/internal/authenticode"
	"github.com/fwilhe2/kaetzli/go/internal/efivars"
	"github.com/fwilhe2/kaetzli/go/internal/uki"
)

// testUKI is a PE32+ laid out as ukify lays one out, with .cmdline before .linux.
func testUKI(t *testing.T, cmdline string) []byte {
	t.Helper()
	sections := [][2]string{{".text", strings.Repeat("\x90", 0x300)}, {".cmdline", cmdline}, {".linux", strings.Repeat("\xab", 0x900)}}
	const peOff, optSize, hdr, falign, salign = 0x80, 240, 0x400, 0x200, 0x1000
	b := make([]byte, hdr)
	copy(b, "MZ")
	binary.LittleEndian.PutUint32(b[0x3c:], peOff)
	copy(b[peOff:], "PE\x00\x00")
	coff := peOff + 4
	binary.LittleEndian.PutUint16(b[coff:], 0x8664)
	binary.LittleEndian.PutUint16(b[coff+2:], uint16(len(sections)))
	binary.LittleEndian.PutUint16(b[coff+16:], optSize)
	opt := coff + 20
	binary.LittleEndian.PutUint16(b[opt:], 0x20b)
	binary.LittleEndian.PutUint32(b[opt+32:], salign)
	binary.LittleEndian.PutUint32(b[opt+36:], falign)
	binary.LittleEndian.PutUint32(b[opt+60:], hdr)
	binary.LittleEndian.PutUint32(b[opt+108:], 16)
	raw, va := hdr, salign
	var data []byte
	for i, s := range sections {
		h := b[opt+optSize+40*i:]
		copy(h, s[0])
		rs := (len(s[1]) + falign - 1) / falign * falign
		binary.LittleEndian.PutUint32(h[8:], uint32(len(s[1])))
		binary.LittleEndian.PutUint32(h[12:], uint32(va))
		binary.LittleEndian.PutUint32(h[16:], uint32(rs))
		binary.LittleEndian.PutUint32(h[20:], uint32(raw))
		raw += rs
		va += (len(s[1]) + salign - 1) / salign * salign
		data = append(data, s[1]...)
		data = append(data, make([]byte, rs-len(s[1]))...)
	}
	binary.LittleEndian.PutUint32(b[opt+56:], uint32(va))
	return append(b, data...)
}

// varsTemplate is an empty edk2 authenticated variable store, as OVMF_VARS ships.
func varsTemplate() []byte {
	const fvLen, storeSize = 0x20000, 0xffb8
	b := bytes.Repeat([]byte{0xff}, fvLen)
	clear(b[:16])
	copy(b[16:], []byte{0x8d, 0x2b, 0xf1, 0xff, 0x96, 0x76, 0x8b, 0x4c, 0xa9, 0x85, 0x27, 0x47, 0x07, 0x5b, 0x4f, 0x50})
	binary.LittleEndian.PutUint64(b[32:], fvLen)
	copy(b[40:], "_FVH")
	binary.LittleEndian.PutUint16(b[48:], 0x48)
	clear(b[50:0x48])
	copy(b[0x48:], []byte{0x78, 0x2c, 0xf3, 0xaa, 0x7b, 0x94, 0x9a, 0x43, 0xa1, 0x80, 0x2e, 0x14, 0x4e, 0xc3, 0x77, 0x92})
	binary.LittleEndian.PutUint32(b[0x48+16:], storeSize)
	b[0x48+20], b[0x48+21] = 0x5a, 0xfe
	clear(b[0x48+22 : 0x48+28])
	return b
}

const rootCmdline = `root=/dev/dm-0 ro dm-mod.waitfor=/dev/vda dm-mod.create="root,,,ro,0 8 verity 1 /dev/vda /dev/vda 4096 4096 1 1 sha256 ab cd" console=ttyS0`

func inputs(t *testing.T) (string, string, string) {
	t.Helper()
	d := t.TempDir()
	in, tmpl := filepath.Join(d, "in.efi"), filepath.Join(d, "vars.fd")
	_ = os.WriteFile(in, testUKI(t, rootCmdline), 0o644)
	_ = os.WriteFile(tmpl, varsTemplate(), 0o644)
	ext := filepath.Join(d, "ext")
	_ = os.MkdirAll(filepath.Join(ext, "extensions"), 0o755)
	_ = os.WriteFile(filepath.Join(ext, "extensions", "podman.raw"), nil, 0o644)
	_ = os.WriteFile(filepath.Join(ext, "extensions", "podman.dm-verity"), []byte("0 8 verity 1 @DEV@ @DEV@ 4096 4096 1 1 sha256 ef 01\n"), 0o644)
	return in, tmpl, ext
}

func dbCert(t *testing.T, vars []byte) []byte {
	t.Helper()
	vs, err := efivars.Variables(vars)
	if err != nil {
		t.Fatal(err)
	}
	got := map[string][]byte{}
	for _, v := range vs {
		if v.Name == "PK" || v.Name == "db" {
			got[v.Name] = v.Data[44:] // the one EFI_SIGNATURE_DATA's certificate
		}
	}
	if got["db"] == nil || got["PK"] == nil {
		t.Fatal("no db or PK")
	}
	if bytes.Equal(got["PK"], got["db"]) {
		t.Error("PK is the signing certificate: a machine's owner key must be its own")
	}
	return got["db"]
}

func TestAThrowawayKeySignsAndOnlyItsCertificateIsInDb(t *testing.T) {
	in, tmpl, _ := inputs(t)
	out := t.TempDir()
	if err := secureBootFiles(in, tmpl, out, "", "", nil, "", true); err != nil {
		t.Fatal(err)
	}
	for _, f := range []string{"kaetzli.efi", "ovmf-vars.fd", "ovmf-vars-off.fd", "db.crt", "sealed"} {
		if _, err := os.Stat(filepath.Join(out, f)); err != nil {
			t.Errorf("no %s", f)
		}
	}
	crt, _ := os.ReadFile(filepath.Join(out, "db.crt"))
	blk, _ := pem.Decode(crt)
	vars, _ := os.ReadFile(filepath.Join(out, "ovmf-vars.fd"))
	if blk == nil || !bytes.Equal(dbCert(t, vars), blk.Bytes) {
		t.Fatal("db does not hold db.crt")
	}
	off, _ := os.ReadFile(filepath.Join(out, "ovmf-vars-off.fd"))
	if !bytes.Equal(off, varsTemplate()) {
		t.Error("ovmf-vars-off.fd is not the template")
	}
	signed, _ := os.ReadFile(filepath.Join(out, "kaetzli.efi"))
	unsigned, _ := os.ReadFile(in)
	ds, _ := authenticode.Digest(signed)
	du, _ := authenticode.Digest(append(unsigned, make([]byte, (8-len(unsigned)%8)%8)...))
	if !bytes.Equal(ds, du) || len(signed) <= len(unsigned) {
		t.Fatal("kaetzli.efi is not the input with a signature appended")
	}
	if s, _ := os.ReadFile(filepath.Join(out, "sealed")); string(s) != "extensions=\nvar=true\n" {
		t.Errorf("sealed = %q", s)
	}
}

func TestExtensionsAreSealedForTheDisksTheBootAttaches(t *testing.T) {
	in, tmpl, ext := inputs(t)
	for _, c := range []struct {
		withVar bool
		dev     string
	}{{true, "/dev/vdc"}, {false, "/dev/vdb"}} {
		out := t.TempDir()
		if err := secureBootFiles(in, tmpl, out, "", "", []string{"podman"}, ext, c.withVar); err != nil {
			t.Fatal(err)
		}
		signed, _ := os.ReadFile(filepath.Join(out, "kaetzli.efi"))
		cmdline, err := uki.Cmdline(signed)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(cmdline, ";ext-podman,,,ro,0 8 verity 1 "+c.dev+" "+c.dev) ||
			!strings.Contains(cmdline, "dm-mod.waitfor=/dev/vda,"+c.dev) {
			t.Errorf("var=%t: the tables are not for %s: %s", c.withVar, c.dev, cmdline)
		}
		s, _ := os.ReadFile(filepath.Join(out, "sealed"))
		if want := "extensions=podman\nvar=" + map[bool]string{true: "true", false: "false"}[c.withVar] + "\n"; string(s) != want {
			t.Errorf("sealed = %q, want %q", s, want)
		}
	}
}

func TestAKeyThatIsNotTheCertificatesIsRefused(t *testing.T) {
	in, tmpl, _ := inputs(t)
	d := t.TempDir()
	k1, c1, _ := throwawayKey("one")
	_, c2, _ := throwawayKey("two")
	der, _ := x509.MarshalPKCS8PrivateKey(k1)
	_ = os.WriteFile(filepath.Join(d, "k.pem"), pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}), 0o600)
	_ = os.WriteFile(filepath.Join(d, "c1.pem"), pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: c1.Raw}), 0o644)
	_ = os.WriteFile(filepath.Join(d, "c2.pem"), pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: c2.Raw}), 0o644)
	if err := secureBootFiles(in, tmpl, t.TempDir(), filepath.Join(d, "k.pem"), filepath.Join(d, "c1.pem"), nil, "", false); err != nil {
		t.Fatalf("a matching pair: %v", err)
	}
	if err := secureBootFiles(in, tmpl, t.TempDir(), filepath.Join(d, "k.pem"), filepath.Join(d, "c2.pem"), nil, "", false); err == nil ||
		!strings.Contains(err.Error(), "not the certificate's") {
		t.Fatalf("a mismatched pair: got %v", err)
	}
}
