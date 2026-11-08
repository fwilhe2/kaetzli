package authenticode

import (
	"bytes"
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"encoding/binary"
	"encoding/pem"
	"math/big"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// testPE is a PE32+ EFI application as ukify lays one out: headers, then sections back to
// back from SizeOfHeaders, then trailing bytes no section claims.
func testPE(t *testing.T, sections [][]byte, trailing []byte) []byte {
	t.Helper()
	const peOff, optSize, hdrSize, align = 0x80, 240, 0x400, 0x200
	b := make([]byte, hdrSize)
	copy(b, "MZ")
	binary.LittleEndian.PutUint32(b[0x3c:], peOff)
	copy(b[peOff:], "PE\x00\x00")
	coff := peOff + 4
	binary.LittleEndian.PutUint16(b[coff:], 0x8664) // x86-64
	binary.LittleEndian.PutUint16(b[coff+2:], uint16(len(sections)))
	binary.LittleEndian.PutUint16(b[coff+16:], optSize)
	binary.LittleEndian.PutUint16(b[coff+18:], 0x2022)
	opt := coff + 20
	binary.LittleEndian.PutUint16(b[opt:], 0x20b)
	binary.LittleEndian.PutUint32(b[opt+32:], 0x1000)  // SectionAlignment
	binary.LittleEndian.PutUint32(b[opt+36:], align)   // FileAlignment
	binary.LittleEndian.PutUint32(b[opt+60:], hdrSize) // SizeOfHeaders
	binary.LittleEndian.PutUint32(b[opt+64:], 0xdeadbeef)
	binary.LittleEndian.PutUint16(b[opt+68:], 10) // EFI application
	binary.LittleEndian.PutUint32(b[opt+108:], 16)
	sec := opt + optSize
	off, rva := hdrSize, 0x1000
	for i, s := range sections {
		size := (len(s) + align - 1) / align * align
		h := b[sec+40*i:]
		copy(h, ".s"+string(rune('a'+i)))
		binary.LittleEndian.PutUint32(h[8:], uint32(len(s)))
		binary.LittleEndian.PutUint32(h[12:], uint32(rva))
		binary.LittleEndian.PutUint32(h[16:], uint32(size))
		binary.LittleEndian.PutUint32(h[20:], uint32(off))
		binary.LittleEndian.PutUint32(h[36:], 0x40000040)
		off += size
		rva += (size + 0xfff) / 0x1000 * 0x1000
	}
	binary.LittleEndian.PutUint32(b[opt+56:], uint32(rva)) // SizeOfImage
	for _, s := range sections {
		size := (len(s) + align - 1) / align * align
		b = append(b, s...)
		b = append(b, make([]byte, size-len(s))...)
	}
	return append(b, trailing...)
}

func testKey(t *testing.T) (*x509.Certificate, *rsa.PrivateKey) {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(0x1234567890),
		Subject:      pkix.Name{CommonName: "kaetzli test db"},
		NotBefore:    time.Unix(0, 0),
		NotAfter:     time.Unix(0, 0).AddDate(100, 0, 0),
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return cert, key
}

func sample(t *testing.T) []byte {
	return testPE(t, [][]byte{bytes.Repeat([]byte{0xcc}, 700), []byte("cmdline root=/dev/dm-0")}, []byte("tail"))
}

// certTable is the signed image's attribute certificate: WIN_CERTIFICATE's header and the
// PKCS#7 in it.
func certTable(t *testing.T, signed []byte) (offset, size int, revision, typ uint16, pkcs7 []byte) {
	t.Helper()
	img, err := parse(signed)
	if err != nil {
		t.Fatal(err)
	}
	offset = int(binary.LittleEndian.Uint32(signed[img.certDir:]))
	size = int(binary.LittleEndian.Uint32(signed[img.certDir+4:]))
	w := signed[offset:]
	length := int(binary.LittleEndian.Uint32(w))
	return offset, size, binary.LittleEndian.Uint16(w[4:]), binary.LittleEndian.Uint16(w[6:]), w[8:length]
}

func TestTheSignatureIsAppendedAsAWinCertificateOnAnEightByteBoundary(t *testing.T) {
	cert, key := testKey(t)
	unsigned := sample(t)
	signed, err := Sign(unsigned, cert, key)
	if err != nil {
		t.Fatal(err)
	}
	offset, size, rev, typ, _ := certTable(t, signed)
	if offset%8 != 0 || size%8 != 0 || offset+size != len(signed) {
		t.Fatalf("certificate table at %d, %d bytes, in a %d-byte file", offset, size, len(signed))
	}
	if rev != 0x0200 || typ != 0x0002 {
		t.Fatalf("WIN_CERTIFICATE revision %#x type %#x, want 0x200 (2.0) and 2 (PKCS_SIGNED_DATA)", rev, typ)
	}
	if !bytes.Equal(unsigned, unsignedWithoutCertDir(t, signed, len(unsigned))) {
		t.Fatal("signing changed bytes of the image other than the certificate table entry")
	}
}

// unsignedWithoutCertDir is the signed image's first n bytes with its certificate table
// entry zeroed again, which has to give back the unsigned image.
func unsignedWithoutCertDir(t *testing.T, signed []byte, n int) []byte {
	img, err := parse(signed)
	if err != nil {
		t.Fatal(err)
	}
	out := bytes.Clone(signed[:n])
	clear(out[img.certDir : img.certDir+8])
	return out
}

func TestTheDigestDoesNotChangeWhenTheSignatureIsAppended(t *testing.T) {
	cert, key := testKey(t)
	unsigned := sample(t)
	signed, err := Sign(unsigned, cert, key)
	if err != nil {
		t.Fatal(err)
	}
	padded := append(bytes.Clone(unsigned), make([]byte, (8-len(unsigned)%8)%8)...)
	before, err := Digest(padded)
	if err != nil {
		t.Fatal(err)
	}
	after, err := Digest(signed)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(before, after) {
		t.Fatal("the Authenticode digest of the signed image is not the unsigned one's")
	}
}

func TestTheDigestCoversSectionsAndTrailingDataButNotTheChecksum(t *testing.T) {
	base := sample(t)
	d0, _ := Digest(base)
	img, err := parse(base)
	if err != nil {
		t.Fatal(err)
	}
	for name, at := range map[string]int{
		"a section":     len(base) - 4 - 512,
		"trailing data": len(base) - 1,
		"a header":      0x3c - 1,
	} {
		c := bytes.Clone(base)
		c[at] ^= 0xff
		d, _ := Digest(c)
		if bytes.Equal(d, d0) {
			t.Errorf("changing %s left the digest as it was", name)
		}
	}
	c := bytes.Clone(base)
	c[img.checksum] ^= 0xff
	if d, _ := Digest(c); !bytes.Equal(d, d0) {
		t.Error("the PE checksum is in the digest; Authenticode leaves it out")
	}
}

// The structure, by what each verifier reads from it.
type contentInfo struct {
	Type    asn1.ObjectIdentifier
	Content asn1.RawValue `asn1:"explicit,tag:0"`
}

type pkcs7SignedData struct {
	Version     int
	Digests     asn1.RawValue
	Content     contentInfo
	Certs       asn1.RawValue `asn1:"tag:0"`
	SignerInfos []signerInfo  `asn1:"set"`
}

type signerInfo struct {
	Version   int
	IssuerSN  asn1.RawValue
	DigestAlg asn1.RawValue
	Attrs     asn1.RawValue `asn1:"tag:0"`
	SigAlg    asn1.RawValue
	Signature []byte
}

type attribute struct {
	Type   asn1.ObjectIdentifier
	Values asn1.RawValue `asn1:"set"`
}

func TestThePKCS7IsAuthenticodeSignedDataOverTheImageDigest(t *testing.T) {
	cert, key := testKey(t)
	signed, err := Sign(sample(t), cert, key)
	if err != nil {
		t.Fatal(err)
	}
	_, _, _, _, p7 := certTable(t, signed)
	var ci contentInfo
	// What follows the PKCS#7 in the WIN_CERTIFICATE is its padding to eight bytes.
	if rest, err := asn1.Unmarshal(p7, &ci); err != nil || len(rest) >= 8 || len(bytes.Trim(rest, "\x00")) != 0 {
		t.Fatalf("ContentInfo: %v (% x after it)", err, rest)
	}
	if !ci.Type.Equal(oidSignedData) {
		t.Fatalf("content type %v, want signedData", ci.Type)
	}
	var sd pkcs7SignedData
	if _, err := asn1.Unmarshal(ci.Content.Bytes, &sd); err != nil {
		t.Fatal(err)
	}
	if !sd.Content.Type.Equal(oidSpcIndirectData) {
		t.Fatalf("inner content %v, want SPC_INDIRECT_DATA", sd.Content.Type)
	}
	// The image digest is the last bytes of SpcIndirectDataContent (edk2 compares there).
	idc := sd.Content.Content.Bytes
	digest, _ := Digest(signed)
	var seq asn1.RawValue
	if _, err := asn1.Unmarshal(idc, &seq); err != nil {
		t.Fatal(err)
	}
	if !bytes.HasSuffix(seq.Bytes, digest) {
		t.Fatal("SpcIndirectDataContent does not end with the image's digest")
	}
	if len(sd.SignerInfos) != 1 {
		t.Fatalf("%d signers, want 1", len(sd.SignerInfos))
	}
	si := sd.SignerInfos[0]
	// messageDigest is over SpcIndirectDataContent's contents, without its own tag and length.
	var attrs []attribute
	rest := si.Attrs.Bytes
	for len(rest) > 0 {
		var a attribute
		if rest, err = asn1.Unmarshal(rest, &a); err != nil {
			t.Fatal(err)
		}
		attrs = append(attrs, a)
	}
	want := sha256.Sum256(seq.Bytes)
	found := false
	for _, a := range attrs {
		if a.Type.Equal(oidMessageDigest) {
			var md []byte
			if _, err := asn1.Unmarshal(a.Values.Bytes, &md); err != nil {
				t.Fatal(err)
			}
			found = bytes.Equal(md, want[:])
		}
	}
	if !found {
		t.Fatal("no messageDigest attribute over SpcIndirectDataContent's contents")
	}
	// The signature is over the attributes as a SET (tag 0x31), not as the [0] they are stored as.
	set := append([]byte{0x31}, si.Attrs.FullBytes[1:]...)
	h := sha256.Sum256(set)
	if err := rsa.VerifyPKCS1v15(cert.PublicKey.(*rsa.PublicKey), crypto.SHA256, h[:], si.Signature); err != nil {
		t.Fatalf("the signature does not verify with the certificate: %v", err)
	}
	if !bytes.Contains(sd.Certs.Bytes, cert.Raw) {
		t.Fatal("the signing certificate is not in the SignedData")
	}
}

// edk2's DxeImageVerificationLib finds the digest algorithm at a fixed offset of the PKCS#7
// (AuthData + 32), with two-byte lengths before it: an encoding that moves it fails there.
func TestEdk2FindsSHA256AtOffset32(t *testing.T) {
	cert, key := testKey(t)
	signed, err := Sign(sample(t), cert, key)
	if err != nil {
		t.Fatal(err)
	}
	_, _, _, _, p7 := certTable(t, signed)
	sha256OID := []byte{0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01}
	if p7[1] != 0x82 || !bytes.Equal(p7[32:32+len(sha256OID)], sha256OID) {
		t.Fatalf("PKCS#7 starts % x; edk2 wants 0x82 at 1 and the SHA-256 OID at 32", p7[:48])
	}
}

func TestSigningIsDeterministic(t *testing.T) {
	cert, key := testKey(t)
	a, err := Sign(sample(t), cert, key)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := Sign(sample(t), cert, key)
	if !bytes.Equal(a, b) {
		t.Fatal("two signatures of one image with one key differ")
	}
}

func TestWhatCannotBeSignedIsRefused(t *testing.T) {
	cert, key := testKey(t)
	signed, err := Sign(sample(t), cert, key)
	if err != nil {
		t.Fatal(err)
	}
	ecKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	truncated := sample(t)[:0x200]
	notPE := bytes.Clone(sample(t))
	copy(notPE[0x80:], "XX")
	cases := map[string]struct {
		img  []byte
		key  crypto.Signer
		says string
	}{
		"already signed":          {signed, key, "already signed"},
		"not a PE":                {notPE, key, "PE signature"},
		"sections past the end":   {truncated, key, "past the end"},
		"not RSA (UEFI needs it)": {sample(t), ecKey, "RSA"},
		"key not the cert's":      {sample(t), mustRSA(t), "certificate"},
	}
	for name, c := range cases {
		_, err := Sign(c.img, cert, c.key)
		if err == nil || !strings.Contains(err.Error(), c.says) {
			t.Errorf("%s: got %v, want an error saying %q", name, err, c.says)
		}
	}
}

func mustRSA(t *testing.T) *rsa.PrivateKey {
	k, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	return k
}

// sbverify (sbsigntools, what tools/sign-uki.sh signs with) as the independent check: it
// recomputes the digest its own way and verifies the PKCS#7 with OpenSSL. CI's go job
// installs it and sets KAETZLI_REQUIRE_SBVERIFY=1, so there it cannot be skipped.
func TestSbverifyAcceptsTheSignature(t *testing.T) {
	sbverify, err := exec.LookPath("sbverify")
	if err != nil {
		if os.Getenv("KAETZLI_REQUIRE_SBVERIFY") == "1" {
			t.Fatal("sbverify not found, and KAETZLI_REQUIRE_SBVERIFY=1")
		}
		t.Skip("sbverify not found (sbsigntool)")
	}
	cert, key := testKey(t)
	dir := t.TempDir()
	certPath := filepath.Join(dir, "db.crt")
	if err := os.WriteFile(certPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert.Raw}), 0o644); err != nil {
		t.Fatal(err)
	}
	for name, img := range map[string][]byte{
		"trailing data":     sample(t),
		"none, odd length":  testPE(t, [][]byte{bytes.Repeat([]byte{1}, 513)}, []byte{1, 2, 3}),
		"many sections":     testPE(t, [][]byte{{1}, {2}, {3}, {4}, {5}}, nil),
		"one byte trailing": testPE(t, [][]byte{{9}}, []byte{7}),
	} {
		signed, err := Sign(img, cert, key)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		path := filepath.Join(dir, "signed.efi")
		if err := os.WriteFile(path, signed, 0o644); err != nil {
			t.Fatal(err)
		}
		out, err := exec.Command(sbverify, "--cert", certPath, path).CombinedOutput()
		if err != nil || !strings.Contains(string(out), "Signature verification OK") {
			t.Errorf("%s: sbverify: %v\n%s", name, err, out)
		}
		other, _ := testKey(t)
		otherPath := filepath.Join(dir, "other.crt")
		_ = os.WriteFile(otherPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: other.Raw}), 0o644)
		if out, err := exec.Command(sbverify, "--cert", otherPath, path).CombinedOutput(); err == nil {
			t.Errorf("%s: sbverify accepted the signature with another certificate:\n%s", name, out)
		}
	}
}
