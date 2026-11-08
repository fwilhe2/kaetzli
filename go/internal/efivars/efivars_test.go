package efivars

import (
	"bytes"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
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

var owner = MustGUID("8d5b5c0e-6b8f-4c5a-9d2e-4b6174a7a1e1")

// template is an empty OVMF variable store as the 4M build lays it out, shrunk: the
// firmware volume header, the authenticated variable store header, the rest 0xff (erased
// flash), then the fault-tolerant-write area this package must not touch.
func template(t *testing.T, storeGUID GUID) []byte {
	t.Helper()
	const fvLen, storeSize = 0x20000, 0xffb8
	b := bytes.Repeat([]byte{0xff}, fvLen)
	clear(b[:16])
	copy(b[16:], MustGUID("fff12b8d-7696-4c8b-a985-2747075b4f50").bytes())
	binary.LittleEndian.PutUint64(b[32:], fvLen)
	copy(b[40:], "_FVH")
	binary.LittleEndian.PutUint32(b[44:], 0x4feff)
	binary.LittleEndian.PutUint16(b[48:], 0x48) // HeaderLength
	binary.LittleEndian.PutUint16(b[52:], 0)    // no extended header
	b[54], b[55] = 0, 2
	binary.LittleEndian.PutUint32(b[56:], fvLen/0x1000)
	binary.LittleEndian.PutUint32(b[60:], 0x1000)
	clear(b[64:72])
	var sum uint16
	for i := 0; i < 0x48; i += 2 {
		sum += binary.LittleEndian.Uint16(b[i:])
	}
	binary.LittleEndian.PutUint16(b[50:], -sum)
	copy(b[0x48:], storeGUID.bytes())
	binary.LittleEndian.PutUint32(b[0x48+16:], storeSize)
	b[0x48+20], b[0x48+21] = 0x5a, 0xfe
	clear(b[0x48+22 : 0x48+28])
	copy(b[0x48+storeSize:], "FTW area, not ours")
	return b
}

func testCert(t *testing.T, cn string) *x509.Certificate {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(7),
		Subject:      pkix.Name{CommonName: cn},
		NotBefore:    time.Unix(0, 0),
		NotAfter:     time.Unix(0, 0).AddDate(100, 0, 0),
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	c, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

type keys struct{ pk, kek, db *x509.Certificate }

func testKeys(t *testing.T) keys {
	return keys{testCert(t, "PK"), testCert(t, "KEK"), testCert(t, "db")}
}

func enroll(t *testing.T, tmpl []byte, k keys) []byte {
	t.Helper()
	out, err := EnrollSecureBoot(tmpl, k.pk, k.kek, k.db, owner)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func byName(t *testing.T, b []byte) map[string]Variable {
	t.Helper()
	vs, err := Variables(b)
	if err != nil {
		t.Fatal(err)
	}
	m := map[string]Variable{}
	for _, v := range vs {
		if _, dup := m[v.Name]; dup {
			t.Fatalf("%s is in the store twice", v.Name)
		}
		m[v.Name] = v
	}
	return m
}

func TestEnrollingWritesTheSecureBootVariables(t *testing.T) {
	k := testKeys(t)
	vars := byName(t, enroll(t, template(t, authStore), k))
	auth := uint32(attrNV | attrBS | attrRT | attrTimeAuth)
	for name, want := range map[string]struct {
		guid  GUID
		attrs uint32
		cert  *x509.Certificate
	}{
		"PK":  {globalVariable, auth, k.pk},
		"KEK": {globalVariable, auth, k.kek},
		"db":  {imageSecurityDatabase, auth, k.db},
	} {
		v, ok := vars[name]
		if !ok {
			t.Fatalf("no %s", name)
		}
		if v.GUID != want.guid || v.Attributes != want.attrs {
			t.Errorf("%s: GUID %s attributes %#x, want %s %#x", name, v.GUID, v.Attributes, want.guid, want.attrs)
		}
		// One EFI_SIGNATURE_LIST of one X.509 certificate, owned by owner.
		d := v.Data
		if GUID(d[:16]) != certX509 || int(binary.LittleEndian.Uint32(d[16:])) != len(d) ||
			binary.LittleEndian.Uint32(d[20:]) != 0 || int(binary.LittleEndian.Uint32(d[24:])) != 16+len(want.cert.Raw) ||
			GUID(d[28:44]) != owner || !bytes.Equal(d[44:], want.cert.Raw) {
			t.Errorf("%s is not one signature list of its certificate", name)
		}
	}
	if v := vars["SecureBootEnable"]; !bytes.Equal(v.Data, []byte{1}) || v.Attributes != attrNV|attrBS {
		t.Errorf("SecureBootEnable %v", v)
	}
	if v := vars["CustomMode"]; !bytes.Equal(v.Data, []byte{0}) || v.Attributes != attrNV|attrBS {
		t.Errorf("CustomMode %v", v)
	}
	if v, ok := vars["dbx"]; !ok || GUID(v.Data[:16]) != certSHA256 {
		t.Error("no dbx with a SHA-256 entry (tools expect one to exist)")
	}
}

func TestOnlyTheVariableStoreChanges(t *testing.T) {
	tmpl := template(t, authStore)
	out := enroll(t, tmpl, testKeys(t))
	if len(out) != len(tmpl) {
		t.Fatalf("%d bytes, want the template's %d", len(out), len(tmpl))
	}
	start, end := 0x48+28, 0x48+0xffb8
	if !bytes.Equal(out[:start], tmpl[:start]) || !bytes.Equal(out[end:], tmpl[end:]) {
		t.Fatal("bytes outside the variable store changed")
	}
}

func TestEnrollingIsDeterministic(t *testing.T) {
	k := testKeys(t)
	if !bytes.Equal(enroll(t, template(t, authStore), k), enroll(t, template(t, authStore), k)) {
		t.Fatal("two enrolments of the same keys differ")
	}
}

func TestEnrollingAgainReplacesTheKeys(t *testing.T) {
	first := enroll(t, template(t, authStore), testKeys(t))
	k := testKeys(t)
	vars := byName(t, enroll(t, first, k)) // fails on a name in the store twice
	if !bytes.HasSuffix(vars["db"].Data, k.db.Raw) || !bytes.HasSuffix(vars["PK"].Data, k.pk.Raw) {
		t.Fatal("the second enrolment's keys are not the ones in the store")
	}
}

func TestWhatCannotBeEnrolledIsRefused(t *testing.T) {
	k := testKeys(t)
	full := template(t, authStore)
	binary.LittleEndian.PutUint32(full[0x48+16:], 0x400) // a store too small for three certificates
	notFV := template(t, authStore)
	copy(notFV[40:], "XXXX")
	for name, c := range map[string]struct {
		b    []byte
		says string
	}{
		"not a firmware volume":       {notFV, "_FVH"},
		"a store without auth fields": {template(t, MustGUID("ddcf3616-3275-4164-98b6-fe85707ffe7d")), "authenticated"},
		"no room":                     {full, "room"},
		"truncated":                   {template(t, authStore)[:0x100], "past the end"},
	} {
		_, err := EnrollSecureBoot(c.b, k.pk, k.kek, k.db, owner)
		if err == nil || !strings.Contains(err.Error(), c.says) {
			t.Errorf("%s: got %v, want an error saying %q", name, err, c.says)
		}
	}
}

// virt-fw-vars (python3-virt-firmware, what tools/sign-uki.sh enrolls with) as the
// independent check: given the same template, certificates and owner, its store and ours
// are the same bytes but for the authenticated variables' timestamps, which it takes from
// the clock. CI's go job installs it and sets KAETZLI_REQUIRE_VIRT_FW_VARS=1.
func TestVirtFwVarsWritesTheSameStore(t *testing.T) {
	tool, err := exec.LookPath("virt-fw-vars")
	if err != nil {
		if os.Getenv("KAETZLI_REQUIRE_VIRT_FW_VARS") == "1" {
			t.Fatal("virt-fw-vars not found, and KAETZLI_REQUIRE_VIRT_FW_VARS=1")
		}
		t.Skip("virt-fw-vars not found (python3-virt-firmware)")
	}
	k := testKeys(t)
	dir := t.TempDir()
	write := func(name string, b []byte) string {
		p := filepath.Join(dir, name)
		if err := os.WriteFile(p, b, 0o644); err != nil {
			t.Fatal(err)
		}
		return p
	}
	pemOf := func(c *x509.Certificate) []byte {
		return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: c.Raw})
	}
	tmpl := template(t, authStore)
	in := write("template.fd", tmpl)
	ref := filepath.Join(dir, "ref.fd")
	o := owner.String()
	cmd := exec.Command(tool, "--input", in, "--output", ref,
		"--set-pk", o, write("pk.pem", pemOf(k.pk)),
		"--add-kek", o, write("kek.pem", pemOf(k.kek)),
		"--add-db", o, write("db.pem", pemOf(k.db)),
		"--secure-boot")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("virt-fw-vars: %v\n%s", err, out)
	}
	want, err := os.ReadFile(ref)
	if err != nil {
		t.Fatal(err)
	}
	got := enroll(t, tmpl, k)
	// Its dbx entry's owner is its own; ours is the owner we were given.
	wantVars, gotVars := byName(t, want), byName(t, got)
	for _, name := range []string{"PK", "KEK", "db"} {
		copy(want[wantVars[name].timeOffset:], got[gotVars[name].timeOffset:gotVars[name].timeOffset+16])
	}
	dbx := wantVars["dbx"]
	copy(want[dbx.dataOffset+28:], owner.bytes())
	if !bytes.Equal(got, want) {
		for i := range got {
			if got[i] != want[i] {
				t.Fatalf("first difference at %#x: ours % x, virt-fw-vars' % x", i, got[i:min(i+16, len(got))], want[i:min(i+16, len(want))])
			}
		}
	}
	if out, err := exec.Command(tool, "--input", write("ours.fd", got), "--print").CombinedOutput(); err != nil {
		t.Fatalf("virt-fw-vars cannot read our store: %v\n%s", err, out)
	}
}

// db can hold image digests instead of certificates: the firmware then boots exactly
// those unsigned images (the launcher enrolls the one UKI it boots).
func TestDbCanHoldAnImageDigest(t *testing.T) {
	k := testKeys(t)
	digest := bytes.Repeat([]byte{0xab}, 32)
	out, err := Enroll(template(t, authStore), k.pk, k.kek, SHA256List(digest, owner), owner)
	if err != nil {
		t.Fatal(err)
	}
	d := byName(t, out)["db"].Data
	if GUID(d[:16]) != certSHA256 || int(binary.LittleEndian.Uint32(d[16:])) != len(d) ||
		binary.LittleEndian.Uint32(d[24:]) != 48 || GUID(d[28:44]) != owner || !bytes.Equal(d[44:], digest) {
		t.Fatalf("db is not one EFI_CERT_SHA256 list of the digest: % x", d)
	}
	if _, err := Enroll(template(t, authStore), k.pk, k.kek, SHA256List([]byte{1, 2}, owner), owner); err == nil {
		t.Fatal("a digest that is not 32 bytes was enrolled")
	}
}
