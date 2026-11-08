// Package firmware finds the host's UEFI firmware with Secure Boot through qemu's firmware
// descriptors (qemu's docs/interop/firmware.json), as libvirt does: JSON files the qemu
// and edk2 packages install, so the launcher needs no path of its own per distribution.
package firmware

import (
	"encoding/json"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"slices"
	"sort"
)

// Firmware is a split flash build: the code, read-only, and the template its variable
// store is copied from.
type Firmware struct {
	Descriptor   string
	Code         string
	VarsTemplate string
}

type descriptor struct {
	InterfaceTypes []string `json:"interface-types"`
	Mapping        struct {
		Device     string `json:"device"`
		Mode       string `json:"mode"`
		Executable struct {
			Filename string `json:"filename"`
			Format   string `json:"format"`
		} `json:"executable"`
		NVRAMTemplate struct {
			Filename string `json:"filename"`
			Format   string `json:"format"`
		} `json:"nvram-template"`
	} `json:"mapping"`
	Targets []struct {
		Architecture string   `json:"architecture"`
		Machines     []string `json:"machines"`
	} `json:"targets"`
	Features []string `json:"features"`
}

// Dirs are where descriptors are, in qemu's order (a later one overrides a file of the
// same name): the qemu installation's own share directory, then the system's and the
// user's.
func Dirs(qemuBinary string) []string {
	var dirs []string
	if bin, err := filepath.EvalSymlinks(qemuBinary); err == nil {
		dirs = append(dirs, filepath.Join(filepath.Dir(bin), "..", "share", "qemu", "firmware"))
	}
	dirs = append(dirs, "/usr/share/qemu/firmware", "/etc/qemu/firmware")
	if cfg, err := os.UserConfigDir(); err == nil {
		dirs = append(dirs, filepath.Join(cfg, "qemu", "firmware"))
	}
	return slices.Compact(dirs)
}

// SecureBoot is the first descriptor, in file name order, of a raw split-flash UEFI build
// for arch on q35 with Secure Boot and SMM; one with an empty store before one with keys
// enrolled (the launcher writes its own).
func SecureBoot(dirs []string, arch string) (Firmware, error) {
	files := map[string]string{} // file name → path, later directories winning
	for _, d := range dirs {
		entries, err := os.ReadDir(d)
		if err != nil {
			continue
		}
		for _, e := range entries {
			if filepath.Ext(e.Name()) == ".json" {
				files[e.Name()] = filepath.Join(d, e.Name())
			}
		}
	}
	names := make([]string, 0, len(files))
	for n := range files {
		names = append(names, n)
	}
	sort.Strings(names)
	var enrolled *Firmware
	for _, n := range names {
		b, err := os.ReadFile(files[n])
		if err != nil || len(b) == 0 {
			continue // an empty file masks one of the same name
		}
		var d descriptor
		if json.Unmarshal(b, &d) != nil || !d.suits(arch) {
			continue
		}
		f := Firmware{Descriptor: files[n], Code: d.Mapping.Executable.Filename, VarsTemplate: d.Mapping.NVRAMTemplate.Filename}
		if !slices.Contains(d.Features, "enrolled-keys") {
			return f, nil
		}
		if enrolled == nil {
			enrolled = &f
		}
	}
	if enrolled != nil {
		return *enrolled, nil
	}
	return Firmware{}, fmt.Errorf("no UEFI firmware with Secure Boot for %s among qemu's firmware descriptors (%v): install edk2's OVMF (Debian, Ubuntu: ovmf; Fedora: edk2-ovmf)", arch, dirs)
}

func (d descriptor) suits(arch string) bool {
	m := d.Mapping
	if !slices.Contains(d.InterfaceTypes, "uefi") || m.Device != "flash" || (m.Mode != "" && m.Mode != "split") {
		return false
	}
	raw := func(f string) bool { return f == "" || f == "raw" }
	if m.Executable.Filename == "" || m.NVRAMTemplate.Filename == "" || !raw(m.Executable.Format) || !raw(m.NVRAMTemplate.Format) {
		return false
	}
	// Builds for confidential VMs are "memory" devices without a store, so the checks
	// above leave them out; a feature like amd-sev only says a build supports it
	// (Debian's ordinary Secure Boot build lists it).
	if !slices.Contains(d.Features, "secure-boot") || !slices.Contains(d.Features, "requires-smm") {
		return false
	}
	for _, t := range d.Targets {
		if t.Architecture != arch {
			continue
		}
		for _, glob := range t.Machines {
			if ok, _ := path.Match(glob, "pc-q35-10.0"); ok {
				return true
			}
		}
	}
	return false
}
