package uki

import (
	"bytes"
	"encoding/binary"
	"strings"
	"testing"
)

type testSection struct {
	name string
	data []byte
}

// testUKI lays a PE out as ukify does: sections back to back in the file from
// SizeOfHeaders, at FileAlignment, each at its own SectionAlignment-aligned address.
func testUKI(t *testing.T, sections ...testSection) []byte {
	t.Helper()
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
	for i, s := range sections {
		h := b[opt+optSize+40*i:]
		copy(h, s.name)
		rs := (len(s.data) + falign - 1) / falign * falign
		binary.LittleEndian.PutUint32(h[8:], uint32(len(s.data)))
		binary.LittleEndian.PutUint32(h[12:], uint32(va))
		binary.LittleEndian.PutUint32(h[16:], uint32(rs))
		binary.LittleEndian.PutUint32(h[20:], uint32(raw))
		binary.LittleEndian.PutUint32(h[36:], 0x40000040)
		raw += rs
		va += (len(s.data) + salign - 1) / salign * salign
	}
	binary.LittleEndian.PutUint32(b[opt+56:], uint32(va))
	for _, s := range sections {
		rs := (len(s.data) + falign - 1) / falign * falign
		b = append(b, s.data...)
		b = append(b, make([]byte, rs-len(s.data))...)
	}
	return b
}

func sample(t *testing.T) []byte {
	return testUKI(t,
		testSection{".text", bytes.Repeat([]byte{0x90}, 0x300)},
		testSection{".osrel", []byte("ID=kaetzli\n")},
		testSection{".cmdline", []byte("root=/dev/dm-0 ro console=ttyS0")},
		testSection{".uname", []byte("6.18")},
		testSection{".linux", bytes.Repeat([]byte{0xab}, 0x1234)},
	)
}

func sectionData(t *testing.T, b []byte, name string) []byte {
	t.Helper()
	img, err := parse(b)
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range img.sections {
		if s.name == name {
			return b[s.raw : s.raw+s.vsize]
		}
	}
	t.Fatalf("no %s", name)
	return nil
}

func TestCmdlineReadsTheSection(t *testing.T) {
	got, err := Cmdline(sample(t))
	if err != nil || got != "root=/dev/dm-0 ro console=ttyS0" {
		t.Fatalf("Cmdline = %q, %v", got, err)
	}
}

func TestALongerCmdlineMovesWhatFollowsInTheFileOnly(t *testing.T) {
	in := sample(t)
	long := "root=/dev/dm-0 ro " + strings.Repeat("x", 1500) + " console=ttyS0"
	out, err := SetCmdline(in, long)
	if err != nil {
		t.Fatal(err)
	}
	if got, _ := Cmdline(out); got != long {
		t.Fatal("the new command line is not what Cmdline reads back")
	}
	for _, name := range []string{".text", ".osrel", ".uname", ".linux"} {
		if !bytes.Equal(sectionData(t, out, name), sectionData(t, in, name)) {
			t.Errorf("%s changed", name)
		}
	}
	before, _ := parse(in)
	after, _ := parse(out)
	for i := range before.sections {
		b, a := before.sections[i], after.sections[i]
		if a.va != b.va {
			t.Errorf("%s moved in memory: %#x → %#x", a.name, b.va, a.va)
		}
		if a.raw%0x200 != 0 || a.rawSize%0x200 != 0 {
			t.Errorf("%s is not at FileAlignment", a.name)
		}
	}
	if !bytes.Equal(out[:0x80], in[:0x80]) {
		t.Error("the DOS header changed")
	}
}

func TestAShorterCmdlineShrinksTheFile(t *testing.T) {
	in := sample(t)
	long, err := SetCmdline(in, strings.Repeat("y", 1000))
	if err != nil {
		t.Fatal(err)
	}
	out, err := SetCmdline(long, "root=/dev/dm-0 ro console=ttyS0")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(out, in) {
		t.Fatal("setting the command line back does not give back the image")
	}
}

func TestWhatCannotBeRewrittenIsRefused(t *testing.T) {
	noCmdline := testUKI(t, testSection{".text", []byte{1}}, testSection{".linux", []byte{2}})
	for name, c := range map[string]struct {
		img     []byte
		cmdline string
		says    string
	}{
		// x86's COMMAND_LINE_SIZE is 2048, the terminating NUL included.
		"longer than the kernel takes": {sample(t), strings.Repeat("z", 2048), "2047"},
		"no .cmdline":                  {noCmdline, "x", ".cmdline"},
		"not a PE":                     {[]byte("MZ but nothing"), "x", "PE"},
	} {
		_, err := SetCmdline(c.img, c.cmdline)
		if err == nil || !strings.Contains(err.Error(), c.says) {
			t.Errorf("%s: got %v, want an error saying %q", name, err, c.says)
		}
	}
	signed := sample(t)
	img, _ := parse(signed)
	binary.LittleEndian.PutUint32(signed[img.certDir+4:], 8)
	if _, err := SetCmdline(signed, "x"); err == nil || !strings.Contains(err.Error(), "signed") {
		t.Errorf("a signed image: got %v", err)
	}
}

// A command line that would run into the next section's address has no room in memory,
// whatever the file allows.
func TestTheNextSectionsAddressBoundsTheCmdline(t *testing.T) {
	in := testUKI(t, testSection{".cmdline", []byte("a")}, testSection{".linux", []byte{1}})
	img, err := parse(in)
	if err != nil {
		t.Fatal(err)
	}
	c, l := img.sections[0], img.sections[1]
	binary.LittleEndian.PutUint32(in[l.header+12:], uint32(c.va+0x100)) // .linux 256 bytes after
	if _, err := SetCmdline(in, strings.Repeat("b", 0x100)); err != nil {
		t.Fatalf("256 bytes fit: %v", err)
	}
	if _, err := SetCmdline(in, strings.Repeat("b", 0x101)); err == nil || !strings.Contains(err.Error(), ".linux") {
		t.Fatalf("257 bytes: got %v, want running into .linux", err)
	}
}
