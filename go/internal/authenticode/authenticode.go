// Package authenticode signs a PE image (an EFI application: the unified kernel image) for
// UEFI Secure Boot, as sbsign does, with the standard library only: the launcher signs the
// UKI it fetched with the user's own key (docs/guides/secure-boot.md).
//
// A signature is a PKCS#7 SignedData over the image's Authenticode digest, appended to the
// image as a WIN_CERTIFICATE that the certificate table data directory points at. The
// digest leaves out the PE checksum, that directory entry and the certificate table
// itself, so appending the signature does not change it. RSA and SHA-256 only, which is
// what every UEFI firmware verifies.
package authenticode

import (
	"bytes"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/asn1"
	"encoding/binary"
	"errors"
	"fmt"
	"slices"
	"unicode/utf16"
)

var (
	oidSignedData      = asn1.ObjectIdentifier{1, 2, 840, 113549, 1, 7, 2}
	oidContentType     = asn1.ObjectIdentifier{1, 2, 840, 113549, 1, 9, 3}
	oidMessageDigest   = asn1.ObjectIdentifier{1, 2, 840, 113549, 1, 9, 4}
	oidRSAEncryption   = asn1.ObjectIdentifier{1, 2, 840, 113549, 1, 1, 1}
	oidSHA256          = asn1.ObjectIdentifier{2, 16, 840, 1, 101, 3, 4, 2, 1}
	oidSpcIndirectData = asn1.ObjectIdentifier{1, 3, 6, 1, 4, 1, 311, 2, 1, 4}
	oidSpcPEImageData  = asn1.ObjectIdentifier{1, 3, 6, 1, 4, 1, 311, 2, 1, 15}
	oidSpcSpOpusInfo   = asn1.ObjectIdentifier{1, 3, 6, 1, 4, 1, 311, 2, 1, 12}
)

// image is where in a PE file the parts Authenticode treats specially are.
type image struct {
	checksum      int // offset of the optional header's CheckSum
	certDir       int // offset of the certificate table's data directory entry
	sizeOfHeaders int
	sections      [][2]int // PointerToRawData, SizeOfRawData; in file order
}

func parse(b []byte) (*image, error) {
	u16 := func(at int) int { return int(binary.LittleEndian.Uint16(b[at:])) }
	u32 := func(at int) int { return int(binary.LittleEndian.Uint32(b[at:])) }
	if len(b) < 0x40 || string(b[:2]) != "MZ" {
		return nil, errors.New("not a PE image: no MZ header")
	}
	pe := u32(0x3c)
	if pe < 0x40 || pe+24 > len(b) || string(b[pe:pe+4]) != "PE\x00\x00" {
		return nil, errors.New("not a PE image: no PE signature where e_lfanew points")
	}
	coff := pe + 4
	nsec, optSize := u16(coff+2), u16(coff+16)
	opt := coff + 20
	if opt+optSize > len(b) || optSize < 2 {
		return nil, errors.New("the optional header runs past the end of the file")
	}
	var dirs, nrva int
	switch u16(opt) {
	case 0x20b: // PE32+
		dirs, nrva = opt+112, opt+108
	case 0x10b: // PE32
		dirs, nrva = opt+96, opt+92
	default:
		return nil, fmt.Errorf("unknown optional header magic %#x", u16(opt))
	}
	if nrva+4 > opt+optSize || u32(nrva) <= 4 || dirs+5*8 > opt+optSize {
		return nil, errors.New("the optional header has no certificate table entry")
	}
	img := &image{checksum: opt + 64, certDir: dirs + 4*8, sizeOfHeaders: u32(opt + 60)}
	table := opt + optSize
	if table+40*nsec > len(b) || img.sizeOfHeaders > len(b) || img.sizeOfHeaders < table+40*nsec {
		return nil, errors.New("the headers run past the end of the file")
	}
	for i := range nsec {
		h := table + 40*i
		ptr, size := u32(h+20), u32(h+16)
		if size == 0 {
			continue
		}
		if ptr+size > len(b) {
			return nil, fmt.Errorf("section %d runs past the end of the file", i)
		}
		img.sections = append(img.sections, [2]int{ptr, size})
	}
	slices.SortFunc(img.sections, func(a, b [2]int) int { return a[0] - b[0] })
	return img, nil
}

// Digest is the image's Authenticode SHA-256, as edk2's DxeImageVerificationLib computes
// it: the headers without the checksum and the certificate table entry, every section's
// raw data in file order, then whatever follows SizeOfHeaders plus the sections' sizes, up
// to the certificate table.
func Digest(b []byte) ([]byte, error) {
	img, err := parse(b)
	if err != nil {
		return nil, err
	}
	return img.digest(b)
}

func (img *image) digest(b []byte) ([]byte, error) {
	end := len(b)
	if size := int(binary.LittleEndian.Uint32(b[img.certDir+4:])); size != 0 {
		at := int(binary.LittleEndian.Uint32(b[img.certDir:]))
		if at+size != len(b) {
			return nil, errors.New("the certificate table is not at the end of the file")
		}
		end = at
	}
	h := sha256.New()
	h.Write(b[:img.checksum])
	h.Write(b[img.checksum+4 : img.certDir])
	h.Write(b[img.certDir+8 : img.sizeOfHeaders])
	hashed := img.sizeOfHeaders
	for _, s := range img.sections {
		h.Write(b[s[0] : s[0]+s[1]])
		hashed += s[1]
	}
	if hashed < end {
		h.Write(b[hashed:end])
	}
	return h.Sum(nil), nil
}

// Sign returns the image with a signature by key, whose certificate is cert, appended. The
// image must be unsigned: the UKI build leaves it so.
func Sign(b []byte, cert *x509.Certificate, key crypto.Signer) ([]byte, error) {
	pub, ok := key.Public().(*rsa.PublicKey)
	if !ok {
		return nil, errors.New("the key is not RSA, the only kind UEFI firmware verifies")
	}
	if certPub, ok := cert.PublicKey.(*rsa.PublicKey); !ok || !certPub.Equal(pub) {
		return nil, errors.New("the key is not the one the certificate is for")
	}
	img, err := parse(b)
	if err != nil {
		return nil, err
	}
	if binary.LittleEndian.Uint32(b[img.certDir+4:]) != 0 {
		return nil, errors.New("the image is already signed")
	}
	// The table starts on an eight-byte boundary; the padding before it is image data,
	// and hashed as such, as sbsign does.
	out := append(slices.Clone(b), make([]byte, pad8(len(b))-len(b))...)
	digest, err := img.digest(out)
	if err != nil {
		return nil, err
	}
	p7, err := signedData(digest, cert, key)
	if err != nil {
		return nil, err
	}
	// WIN_CERTIFICATE: dwLength, wRevision 2.0, wCertificateType PKCS_SIGNED_DATA.
	size := pad8(8 + len(p7))
	w := make([]byte, size)
	binary.LittleEndian.PutUint32(w, uint32(size))
	binary.LittleEndian.PutUint16(w[4:], 0x0200)
	binary.LittleEndian.PutUint16(w[6:], 0x0002)
	copy(w[8:], p7)
	binary.LittleEndian.PutUint32(out[img.certDir:], uint32(len(out)))
	binary.LittleEndian.PutUint32(out[img.certDir+4:], uint32(size))
	return append(out, w...), nil
}

func pad8(n int) int { return (n + 7) &^ 7 }

// signedData is the PKCS#7 ContentInfo Authenticode carries. edk2 reads the digest
// algorithm at a fixed offset (32) of it, so the outer lengths are long-form and the
// AlgorithmIdentifiers carry their NULL parameters, as sbsign and signtool encode them.
func signedData(digest []byte, cert *x509.Certificate, key crypto.Signer) ([]byte, error) {
	sha256Alg := der(0x30, oid(oidSHA256), null)
	// SpcIndirectDataContent: the image is a PE (SpcPeImageData: no flags, the obsolete
	// file link every signer writes) and its digest, last, where edk2 compares it.
	peImage := der(0x30, der(0x03, []byte{0}), der(0xa0, der(0xa2, der(0x80, bmp("<<<Obsolete>>>")))))
	idc := der(0x30, der(0x30, oid(oidSpcPEImageData), peImage), der(0x30, sha256Alg, der(0x04, digest)))
	// messageDigest is over its contents, without the SEQUENCE's own tag and length.
	_, idcContents := split(idc)
	md := sha256.Sum256(idcContents)
	attrs := [][]byte{
		der(0x30, oid(oidContentType), der(0x31, oid(oidSpcIndirectData))),
		der(0x30, oid(oidSpcSpOpusInfo), der(0x31, der(0x30))),
		der(0x30, oid(oidMessageDigest), der(0x31, der(0x04, md[:]))),
	}
	slices.SortFunc(attrs, bytes.Compare) // DER: a SET OF in the order of its encodings
	attrBytes := bytes.Join(attrs, nil)
	h := sha256.Sum256(der(0x31, attrBytes)) // signed as a SET, stored as [0]
	sig, err := key.Sign(rand.Reader, h[:], crypto.SHA256)
	if err != nil {
		return nil, fmt.Errorf("signing: %w", err)
	}
	serial, err := asn1.Marshal(cert.SerialNumber)
	if err != nil {
		return nil, err
	}
	signer := der(0x30,
		integer(1),
		der(0x30, cert.RawIssuer, serial),
		sha256Alg,
		der(0xa0, attrBytes),
		der(0x30, oid(oidRSAEncryption), null),
		der(0x04, sig),
	)
	sd := der(0x30,
		integer(1),
		der(0x31, sha256Alg),
		der(0x30, oid(oidSpcIndirectData), der(0xa0, idc)),
		der(0xa0, cert.Raw),
		der(0x31, signer),
	)
	return der(0x30, oid(oidSignedData), der(0xa0, sd)), nil
}

var null = []byte{0x05, 0x00}

// der is one DER element: tag, minimal length, contents.
func der(tag byte, contents ...[]byte) []byte {
	body := bytes.Join(contents, nil)
	n := len(body)
	out := []byte{tag}
	switch {
	case n < 0x80:
		out = append(out, byte(n))
	case n < 0x100:
		out = append(out, 0x81, byte(n))
	case n < 0x10000:
		out = append(out, 0x82, byte(n>>8), byte(n))
	default:
		out = append(out, 0x83, byte(n>>16), byte(n>>8), byte(n))
	}
	return append(out, body...)
}

// split is an element's header and its contents.
func split(e []byte) (header, contents []byte) {
	n := 2
	if e[1]&0x80 != 0 {
		n += int(e[1] & 0x7f)
	}
	return e[:n], e[n:]
}

func oid(o asn1.ObjectIdentifier) []byte {
	b, err := asn1.Marshal(o)
	if err != nil {
		panic(err) // only the constants above
	}
	return b
}

func integer(i int) []byte {
	b, err := asn1.Marshal(i)
	if err != nil {
		panic(err)
	}
	return b
}

func bmp(s string) []byte {
	var b []byte
	for _, r := range utf16.Encode([]rune(s)) {
		b = append(b, byte(r>>8), byte(r))
	}
	return b
}
