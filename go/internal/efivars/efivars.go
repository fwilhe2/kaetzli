// Package efivars writes UEFI Secure Boot keys into an OVMF variable store (the
// OVMF_VARS file qemu boots with), as virt-fw-vars does, with the standard library only:
// the launcher gives each machine its own store, with the user's certificate in db
// (docs/guides/secure-boot.md).
//
// The file is an edk2 firmware volume holding an authenticated variable store: a header,
// then variables back to back, each a header, a UTF-16 name and its data, aligned to four
// bytes, then erased flash (0xff). What follows the store (the fault-tolerant-write area)
// is left as it is. The store is written compacted and sorted by name, as virt-fw-vars
// writes it, so the same template and certificates give the same bytes.
package efivars

import (
	"bytes"
	"crypto/sha256"
	"crypto/x509"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"slices"
	"strings"
	"unicode/utf16"
)

// GUID is an EFI GUID as it is stored: the first three fields little-endian.
type GUID [16]byte

// ParseGUID reads the 8-4-4-4-12 form.
func ParseGUID(s string) (GUID, error) {
	var g GUID
	h, err := hex.DecodeString(strings.ReplaceAll(s, "-", ""))
	if err != nil || len(h) != 16 || len(s) != 36 || s[8] != '-' || s[13] != '-' || s[18] != '-' || s[23] != '-' {
		return g, fmt.Errorf("not a GUID: %q", s)
	}
	copy(g[:], h)
	slices.Reverse(g[0:4])
	slices.Reverse(g[4:6])
	slices.Reverse(g[6:8])
	return g, nil
}

// MustGUID is ParseGUID for constants.
func MustGUID(s string) GUID {
	g, err := ParseGUID(s)
	if err != nil {
		panic(err)
	}
	return g
}

func (g GUID) String() string {
	b := g
	slices.Reverse(b[0:4])
	slices.Reverse(b[4:6])
	slices.Reverse(b[6:8])
	h := hex.EncodeToString(b[:])
	return h[:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:]
}

func (g GUID) bytes() []byte { return g[:] }

var (
	nvDataFV              = MustGUID("fff12b8d-7696-4c8b-a985-2747075b4f50") // EFI_SYSTEM_NV_DATA_FV
	authStore             = MustGUID("aaf32c78-947b-439a-a180-2e144ec37792") // gEfiAuthenticatedVariableGuid
	globalVariable        = MustGUID("8be4df61-93ca-11d2-aa0d-00e098032b8c") // PK, KEK
	imageSecurityDatabase = MustGUID("d719b2cb-3d3a-4596-a3bc-dad00e67656f") // db, dbx
	secureBootEnable      = MustGUID("f0a30bc7-af08-4556-99c4-001009c93a44") // edk2's SecureBootEnable
	customMode            = MustGUID("c076ec0c-7028-4399-a072-71ee5c448b9f") // edk2's CustomMode
	certX509              = MustGUID("a5c059a1-94e4-4aa7-87b5-ab155c2bf072") // EFI_CERT_X509_GUID
	certSHA256            = MustGUID("c1c41626-504c-4092-aca9-41f936934328") // EFI_CERT_SHA256_GUID
)

const (
	attrNV       = 0x01
	attrBS       = 0x02
	attrRT       = 0x04
	attrTimeAuth = 0x20 // EFI_VARIABLE_TIME_BASED_AUTHENTICATED_WRITE_ACCESS

	startID                = 0x55aa
	varAdded               = 0x3f
	varInDeletedTransition = 0xfe
	headerSize             = 60 // AUTHENTICATED_VARIABLE_HEADER
	storeHeaderSize        = 28 // VARIABLE_STORE_HEADER
)

// Variable is one variable in the store.
type Variable struct {
	Name           string
	GUID           GUID
	Attributes     uint32
	MonotonicCount uint64
	Time           [16]byte // EFI_TIME of the last authenticated write
	PubKeyIndex    uint32
	Data           []byte

	timeOffset, dataOffset int // where in the file it was read from
}

// authTime is the timestamp of every authenticated variable written here: fixed, so a
// store is reproducible, and old, so any later authenticated write is newer (virt-fw-vars
// takes the clock, and this date for its dbx). EFI_TIME 2010-01-01 00:00:00.
var authTime = [16]byte{0xda, 0x07, 1, 1}

// store is where the variable store is in the file: [start, end) holds the variables.
func store(b []byte) (start, end int, err error) {
	if len(b) < 0x48 || string(b[40:44]) != "_FVH" {
		return 0, 0, errors.New("not an edk2 firmware volume: no _FVH signature")
	}
	if GUID(b[16:32]) != nvDataFV {
		return 0, 0, fmt.Errorf("the firmware volume is %s, not the NV data volume", GUID(b[16:32]))
	}
	h := int(binary.LittleEndian.Uint16(b[48:]))
	if h+storeHeaderSize > len(b) {
		return 0, 0, errors.New("the variable store header runs past the end of the file")
	}
	if GUID(b[h:h+16]) != authStore {
		return 0, 0, errors.New("the variable store is not the authenticated format Secure Boot needs")
	}
	size := int(binary.LittleEndian.Uint32(b[h+16:]))
	if b[h+20] != 0x5a || b[h+21] != 0xfe {
		return 0, 0, errors.New("the variable store is not formatted and healthy")
	}
	if h+size > len(b) || size < storeHeaderSize {
		return 0, 0, errors.New("the variable store runs past the end of the file")
	}
	return align4(h + storeHeaderSize), h + size, nil
}

func align4(n int) int { return (n + 3) &^ 3 }

// Variables are the store's live variables: added, or in transition to deleted with no
// added one of the same name replacing it.
func Variables(b []byte) ([]Variable, error) {
	start, end, err := store(b)
	if err != nil {
		return nil, err
	}
	var added, transition []Variable
	for o := start; o+headerSize <= end; {
		if binary.LittleEndian.Uint16(b[o:]) != startID {
			break
		}
		state := b[o+2]
		ns, ds := int(binary.LittleEndian.Uint32(b[o+36:])), int(binary.LittleEndian.Uint32(b[o+40:]))
		next := o + headerSize + ns + ds
		if ns%2 != 0 || next > end {
			return nil, fmt.Errorf("the variable at %#x runs past the store", o)
		}
		v := Variable{
			GUID:           GUID(b[o+44 : o+60]),
			Attributes:     binary.LittleEndian.Uint32(b[o+4:]),
			MonotonicCount: binary.LittleEndian.Uint64(b[o+8:]),
			Time:           [16]byte(b[o+16 : o+32]),
			PubKeyIndex:    binary.LittleEndian.Uint32(b[o+32:]),
			Data:           slices.Clone(b[o+headerSize+ns : next]),
			timeOffset:     o + 16,
			dataOffset:     o + headerSize + ns,
		}
		u := make([]uint16, ns/2)
		for i := range u {
			u[i] = binary.LittleEndian.Uint16(b[o+headerSize+2*i:])
		}
		v.Name = strings.TrimRight(string(utf16.Decode(u)), "\x00")
		switch state {
		case varAdded:
			added = append(added, v)
		case varAdded & varInDeletedTransition:
			transition = append(transition, v)
		}
		o = align4(next)
	}
	for _, t := range transition {
		if !slices.ContainsFunc(added, func(a Variable) bool { return a.Name == t.Name && a.GUID == t.GUID }) {
			added = append(added, t)
		}
	}
	return added, nil
}

// EnrollSecureBoot returns the store with pk, kek and db enrolled, each as the one
// certificate of its list, owned by owner, and Secure Boot on: what virt-fw-vars
// --set-pk/--add-kek/--add-db/--secure-boot writes into an empty store. Variables of the
// same names already there are replaced; the others stay.
func EnrollSecureBoot(template []byte, pk, kek, db *x509.Certificate, owner GUID) ([]byte, error) {
	start, end, err := store(template)
	if err != nil {
		return nil, err
	}
	existing, err := Variables(template)
	if err != nil {
		return nil, err
	}
	auth := uint32(attrNV | attrBS | attrRT | attrTimeAuth)
	empty := sha256.Sum256(nil)
	ours := []Variable{
		{Name: "PK", GUID: globalVariable, Attributes: auth, Time: authTime, Data: x509List(pk, owner)},
		{Name: "KEK", GUID: globalVariable, Attributes: auth, Time: authTime, Data: x509List(kek, owner)},
		{Name: "db", GUID: imageSecurityDatabase, Attributes: auth, Time: authTime, Data: x509List(db, owner)},
		// A dbx that exists, revoking nothing real (the SHA-256 of nothing): tools that
		// update it expect one, as virt-fw-vars writes it.
		{Name: "dbx", GUID: imageSecurityDatabase, Attributes: auth, Time: authTime, Data: sigList(certSHA256, owner, empty[:])},
		{Name: "SecureBootEnable", GUID: secureBootEnable, Attributes: attrNV | attrBS, Data: []byte{1}},
		{Name: "CustomMode", GUID: customMode, Attributes: attrNV | attrBS, Data: []byte{0}},
	}
	vars := slices.DeleteFunc(existing, func(e Variable) bool {
		return slices.ContainsFunc(ours, func(v Variable) bool { return v.Name == e.Name && v.GUID == e.GUID })
	})
	vars = append(vars, ours...)
	slices.SortFunc(vars, func(a, b Variable) int {
		if c := strings.Compare(a.Name, b.Name); c != 0 {
			return c
		}
		return bytes.Compare(a.GUID[:], b.GUID[:])
	})
	out := slices.Clone(template)
	for i := start; i < end; i++ {
		out[i] = 0xff
	}
	o := start
	for _, v := range vars {
		name := utf16.Encode([]rune(v.Name + "\x00"))
		next := o + headerSize + 2*len(name) + len(v.Data)
		if next > end {
			return nil, fmt.Errorf("no room in the %d-byte variable store for %s", end-start, v.Name)
		}
		binary.LittleEndian.PutUint16(out[o:], startID)
		out[o+2], out[o+3] = varAdded, 0
		binary.LittleEndian.PutUint32(out[o+4:], v.Attributes)
		binary.LittleEndian.PutUint64(out[o+8:], v.MonotonicCount)
		copy(out[o+16:], v.Time[:])
		binary.LittleEndian.PutUint32(out[o+32:], v.PubKeyIndex)
		binary.LittleEndian.PutUint32(out[o+36:], uint32(2*len(name)))
		binary.LittleEndian.PutUint32(out[o+40:], uint32(len(v.Data)))
		copy(out[o+44:], v.GUID[:])
		for i, r := range name {
			binary.LittleEndian.PutUint16(out[o+headerSize+2*i:], r)
		}
		copy(out[o+headerSize+2*len(name):], v.Data)
		o = align4(next)
	}
	return out, nil
}

func x509List(c *x509.Certificate, owner GUID) []byte { return sigList(certX509, owner, c.Raw) }

// sigList is an EFI_SIGNATURE_LIST of one EFI_SIGNATURE_DATA: owner, then the signature.
func sigList(typ, owner GUID, sig []byte) []byte {
	size := 16 + len(sig)
	b := make([]byte, 28, 28+size)
	copy(b, typ[:])
	binary.LittleEndian.PutUint32(b[16:], uint32(28+size))
	binary.LittleEndian.PutUint32(b[20:], 0)
	binary.LittleEndian.PutUint32(b[24:], uint32(size))
	b = append(b, owner[:]...)
	return append(b, sig...)
}
