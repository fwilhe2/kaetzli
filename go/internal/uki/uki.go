// Package uki reads and rewrites the kernel command line of a unified kernel image: its
// .cmdline section, which systemd's stub hands the kernel and, under Secure Boot, is the
// only command line there is. The launcher writes a machine's extension verity tables
// into it before signing, so the user's signature covers them (docs/guides/secure-boot.md).
//
// The section keeps its address in memory; only the file offsets of the sections after
// it move, so nothing that refers to addresses changes. The image must be unsigned: a
// signature covers the old bytes.
package uki

import (
	"encoding/binary"
	"errors"
	"fmt"
	"slices"
)

// MaxCmdline is x86's COMMAND_LINE_SIZE (2048) without its terminating NUL: what the
// kernel keeps of a longer one is cut off.
const MaxCmdline = 2047

type section struct {
	name                    string
	header                  int // offset of its header in the file
	vsize, va, rawSize, raw int
}

type image struct {
	falign   int
	certDir  int
	sections []section
}

func parse(b []byte) (*image, error) {
	if len(b) < 0x40 || string(b[:2]) != "MZ" {
		return nil, errors.New("not a PE image: no MZ header")
	}
	u16 := func(at int) int { return int(binary.LittleEndian.Uint16(b[at:])) }
	u32 := func(at int) int { return int(binary.LittleEndian.Uint32(b[at:])) }
	pe := u32(0x3c)
	if pe < 0x40 || pe+24 > len(b) || string(b[pe:pe+4]) != "PE\x00\x00" {
		return nil, errors.New("not a PE image: no PE signature where e_lfanew points")
	}
	coff := pe + 4
	n, optSize := u16(coff+2), u16(coff+16)
	opt := coff + 20
	if opt+optSize+40*n > len(b) || optSize < 112 || u16(opt) != 0x20b {
		return nil, errors.New("not a PE32+ image with its headers in the file")
	}
	img := &image{falign: u32(opt + 36), certDir: opt + 112 + 4*8}
	if img.falign == 0 || img.falign&(img.falign-1) != 0 {
		return nil, fmt.Errorf("FileAlignment %#x is not a power of two", img.falign)
	}
	for i := range n {
		h := opt + optSize + 40*i
		s := section{
			name:   string(trimNUL(b[h : h+8])),
			header: h,
			vsize:  u32(h + 8), va: u32(h + 12), rawSize: u32(h + 16), raw: u32(h + 20),
		}
		if s.raw+s.rawSize > len(b) {
			return nil, fmt.Errorf("section %s runs past the end of the file", s.name)
		}
		img.sections = append(img.sections, s)
	}
	return img, nil
}

func trimNUL(b []byte) []byte {
	if i := slices.Index(b, 0); i >= 0 {
		return b[:i]
	}
	return b
}

func (img *image) cmdline() (int, error) {
	for i, s := range img.sections {
		if s.name == ".cmdline" {
			return i, nil
		}
	}
	return 0, errors.New("the image has no .cmdline section")
}

// Cmdline is the image's command line.
func Cmdline(b []byte) (string, error) {
	img, err := parse(b)
	if err != nil {
		return "", err
	}
	i, err := img.cmdline()
	if err != nil {
		return "", err
	}
	s := img.sections[i]
	return string(trimNUL(b[s.raw : s.raw+min(s.vsize, s.rawSize)])), nil
}

// SetCmdline is the image with cmdline as its .cmdline section.
func SetCmdline(b []byte, cmdline string) ([]byte, error) {
	if len(cmdline) > MaxCmdline {
		return nil, fmt.Errorf("the command line is %d bytes; the kernel takes %d", len(cmdline), MaxCmdline)
	}
	img, err := parse(b)
	if err != nil {
		return nil, err
	}
	if binary.LittleEndian.Uint32(b[img.certDir+4:]) != 0 {
		return nil, errors.New("the image is signed, and its signature covers the command line it has")
	}
	i, err := img.cmdline()
	if err != nil {
		return nil, err
	}
	c := img.sections[i]
	// Room in memory: up to the next section's address.
	for _, s := range img.sections {
		if s.va > c.va && c.va+len(cmdline) > s.va {
			return nil, fmt.Errorf("a %d-byte command line runs into %s at %#x", len(cmdline), s.name, s.va)
		}
	}
	rawSize := (len(cmdline) + img.falign - 1) &^ (img.falign - 1)
	delta := rawSize - c.rawSize
	out := make([]byte, 0, len(b)+delta)
	out = append(out, b[:c.raw]...)
	out = append(out, cmdline...)
	out = append(out, make([]byte, rawSize-len(cmdline))...)
	out = append(out, b[c.raw+c.rawSize:]...)
	binary.LittleEndian.PutUint32(out[c.header+8:], uint32(len(cmdline)))
	binary.LittleEndian.PutUint32(out[c.header+16:], uint32(rawSize))
	for _, s := range img.sections {
		if s.raw > c.raw {
			binary.LittleEndian.PutUint32(out[s.header+20:], uint32(s.raw+delta))
		}
	}
	return out, nil
}
