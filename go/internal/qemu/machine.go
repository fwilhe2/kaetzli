// Package qemu builds the launcher's qemu command line: the pc profile of
// test/qemu-lib.sh and python/src/kaetzli/qemu/machine.py, a third copy that
// python/tests/test_launcher.py holds equal to the Python one for the same machine.
package qemu

import (
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

const letters = "bcdefghijklmnopqrstuvwxyz"

// Machine is what one boot needs. Paths are as given; the caller makes them absolute.
type Machine struct {
	Arch    string // amd64 or arm64
	KVM     bool
	Mem     string
	CPUs    string
	Kernel  string
	Rootfs  string
	Cmdline string // rootfs.cmdline, the kernel arguments that carry the root hash
	Init    string
	VarDisk string // the raw /var disk; its machine ID is VarDisk + ".machine-id"
	Sysexts []string
	ExtDir  string // where extensions/ and confexts/ are
	SSHPort int
	PubKey  []byte // the login key, as ssh-keygen wrote it
	// Where PubKey is: under Secure Boot it goes by fw_cfg, which reads a file.
	PubKeyFile string
	Hostname   string
	// Replace -nographic: the launcher's console is a socket, not its terminal.
	Display []string
	Reboot  bool // a guest's reboot restarts qemu in place rather than ending it
	// Boot the signed UKI under UEFI with Secure Boot instead of Kernel
	// (docs/guides/secure-boot.md): amd64. Its command line must be SealedCmdline's.
	SecureBoot *SecureBoot
}

// SecureBoot is a signed UKI and the firmware that boots only what its db trusts.
type SecureBoot struct {
	UKI  string // signed with the key in Vars' db
	Code string // the firmware's code, read-only
	Vars string // its variable store, written by the firmware
}

const credentials = "name=opt/io.systemd.credentials/"

// Binary is the qemu for the machine's architecture.
func (m Machine) Binary() string {
	if m.Arch == "arm64" {
		return "qemu-system-aarch64"
	}
	return "qemu-system-x86_64"
}

func (m Machine) console() string {
	if m.Arch == "arm64" {
		return "ttyAMA0"
	}
	return "ttyS0"
}

func (m Machine) accel() []string {
	if m.Arch == "arm64" {
		if m.KVM {
			return []string{"-machine", "virt,accel=kvm", "-cpu", "host"}
		}
		return []string{"-machine", "virt", "-cpu", "max"}
	}
	if m.KVM {
		return []string{"-enable-kvm", "-cpu", "host"}
	}
	return []string{"-cpu", "max"}
}

func (m Machine) varDisk() ([]string, string, error) {
	if m.VarDisk == "" {
		return nil, "", nil
	}
	id, err := os.ReadFile(m.VarDisk + ".machine-id")
	if err != nil {
		return nil, "", err
	}
	drive := []string{
		"-drive", "id=var,file=" + m.VarDisk + ",format=raw,if=none",
		"-device", "virtio-blk-pci,drive=var",
	}
	if m.SecureBoot != nil {
		// The stub ignores the command line: the same two settings go by fw_cfg
		// (secureBoot), fstab.extra as systemd.mount-extra=, system.machine_id as
		// systemd.machine_id=.
		fstab := []byte("/dev/vdb /var ext4 x-systemd.makefs,x-systemd.growfs 0 0\n")
		if err := os.WriteFile(m.VarDisk+".fstab", fstab, 0o644); err != nil {
			return nil, "", err
		}
		return drive, "", nil
	}
	appendArgs := " systemd.mount-extra=/dev/vdb:/var:ext4:x-systemd.makefs,x-systemd.growfs" +
		" systemd.machine_id=" + string(id)
	return drive, appendArgs, nil
}

func (m Machine) sysexts() (drives []string, appendArgs, tables, waitfor string, err error) {
	next := 0
	if m.VarDisk != "" {
		next = 1
	}
	for _, name := range m.Sysexts {
		if _, e := os.Stat(filepath.Join(m.ExtDir, "extensions", name+".raw")); e != nil {
			return nil, "", "", "", fmt.Errorf("no extension %q in %s", name, m.ExtDir)
		}
		for _, kind := range [][2]string{{"ext", "extensions"}, {"conf", "confexts"}} {
			file := m.ExtDir + "/" + kind[1] + "/" + name + ".raw"
			if _, e := os.Stat(file); e != nil {
				continue
			}
			verity := strings.TrimSuffix(file, ".raw") + ".dm-verity"
			t, e := os.ReadFile(verity)
			if e != nil {
				return nil, "", "", "", fmt.Errorf("%s has no %s beside it", file, verity)
			}
			ident := kind[0] + "-" + name
			dev := "/dev/vd" + string(letters[next])
			next++
			table := strings.ReplaceAll(strings.TrimSuffix(string(t), "\n"), "@DEV@", dev)
			drives = append(drives,
				"-drive", "file="+file+",format=raw,if=none,readonly=on,id="+ident,
				"-device", "virtio-blk-pci,drive="+ident)
			tables += ";" + ident + ",,,ro," + table
			waitfor += "," + dev
			appendArgs += " systemd.mount-extra=/dev/mapper/" + ident + ":/run/" + kind[1] + "/" + name + ":erofs:ro"
		}
	}
	return drives, appendArgs, tables, waitfor, nil
}

// secureBoot is kaetzli.qemu's _secureboot: q35 with SMM (OVMF's Secure Boot build needs
// it), the code and the variable store as flash, and the credentials by fw_cfg.
func (m Machine) secureBoot() []string {
	sb := m.SecureBoot
	creds := []string{"-fw_cfg", credentials + "login.noauth,string=yes"}
	if m.VarDisk != "" {
		creds = append(creds,
			"-fw_cfg", credentials+"fstab.extra,file="+m.VarDisk+".fstab",
			"-fw_cfg", credentials+"system.machine_id,file="+m.VarDisk+".machine-id")
	}
	if m.PubKeyFile != "" {
		creds = append(creds, "-fw_cfg", credentials+"ssh.authorized_keys.root,file="+m.PubKeyFile)
	}
	return append([]string{
		"-machine", "q35,smm=on",
		"-global", "driver=cfi.pflash01,property=secure,value=on",
		"-drive", "if=pflash,format=raw,unit=0,file=" + sb.Code + ",readonly=on",
		"-drive", "if=pflash,format=raw,unit=1,file=" + sb.Vars,
	}, creds...)
}

var (
	lastQuote = regexp.MustCompile(`"$`)
	waitFor   = regexp.MustCompile(`dm-mod\.waitfor=([^ ]*)`)
)

// mergeTables joins the extensions' tables to the root's dm-mod.create= and their disks
// to what the kernel waits for (test/qemu-lib.sh's two sed expressions, line by line).
func mergeTables(root, tables, wait string) string {
	if tables == "" {
		return root
	}
	lines := strings.Split(root, "\n")
	for i, line := range lines {
		if loc := lastQuote.FindStringIndex(line); loc != nil {
			line = line[:loc[0]] + tables + `"`
		}
		if loc := waitFor.FindStringSubmatchIndex(line); loc != nil {
			line = line[:loc[1]] + wait + line[loc[1]:]
		}
		lines[i] = line
	}
	return strings.Join(lines, "\n")
}

// SealedCmdline is the command line for the machine's UKI, given the one it was built
// with (the root's table and the console): the extensions' tables and mounts, as Argv
// puts them on -append. Under Secure Boot the stub ignores -append, so the launcher
// writes this into the UKI before signing it, and the kernel verifies the extensions
// from it as it does without Secure Boot.
func (m Machine) SealedCmdline(ukiCmdline string) (string, error) {
	_, extAppend, tables, wait, err := m.sysexts()
	if err != nil {
		return "", err
	}
	if tables == "" {
		return ukiCmdline, nil
	}
	// Not mergeTables: the UKI's table is followed by the console, so its closing quote is
	// not the line's last character.
	open := strings.Index(ukiCmdline, `dm-mod.create="`)
	if open < 0 {
		return "", errors.New("the UKI's command line has no dm-mod.create= table to join the extensions' to")
	}
	start := open + len(`dm-mod.create="`)
	end := strings.IndexByte(ukiCmdline[start:], '"')
	loc := waitFor.FindStringSubmatchIndex(ukiCmdline)
	if end < 0 || loc == nil {
		return "", errors.New("the UKI's command line has no closed dm-mod.create= table and dm-mod.waitfor=")
	}
	end += start
	out := ukiCmdline[:end] + tables + ukiCmdline[end:]
	if loc[1] > end { // dm-mod.waitfor= after the table: past what was just inserted
		loc[1] += len(tables)
	}
	out = out[:loc[1]] + wait + out[loc[1]:]
	return out + extAppend, nil
}

// Argv is the command line after the binary.
func (m Machine) Argv() ([]string, error) {
	if m.SecureBoot != nil {
		if m.Arch != "amd64" {
			return nil, fmt.Errorf("Secure Boot is amd64 only for now")
		}
	}
	raw, err := os.ReadFile(m.Cmdline)
	if err != nil {
		return nil, err
	}
	root := strings.TrimRight(string(raw), "\n")
	varDrive, varAppend, err := m.varDisk()
	if err != nil {
		return nil, err
	}
	extDrives, extAppend, tables, wait, err := m.sysexts()
	if err != nil {
		return nil, err
	}
	root = mergeTables(root, tables, wait)

	extra := ""
	if len(m.PubKey) > 0 {
		extra += " systemd.set_credential_binary=ssh.authorized_keys.root:" +
			base64.StdEncoding.EncodeToString(m.PubKey)
	}
	if m.Hostname != "" {
		extra += " systemd.hostname=" + m.Hostname
	}
	nic := "user,model=virtio-net-pci"
	if m.SSHPort != 0 {
		nic += fmt.Sprintf(",hostfwd=tcp:127.0.0.1:%d-:22", m.SSHPort)
	}

	argv := append([]string{}, m.accel()...)
	kernel := m.Kernel
	if m.SecureBoot != nil {
		argv = append(argv, m.secureBoot()...)
		kernel = m.SecureBoot.UKI
	}
	argv = append(argv, "-m", m.Mem, "-smp", m.CPUs, "-kernel", kernel,
		"-drive", "id=root,file="+m.Rootfs+",format=raw,if=none,readonly=on",
		"-device", "virtio-blk-pci,drive=root")
	argv = append(argv, varDrive...)
	argv = append(argv, extDrives...)
	argv = append(argv, "-nic", nic)
	if m.SecureBoot != nil && m.Hostname != "" {
		// systemd.hostname= is on the command line the stub ignores; the credential takes,
		// as the image ships no /etc/hostname. After the nic, where kaetzli.qemu puts its
		// fwcfg_credentials.
		argv = append(argv, "-fw_cfg", credentials+"system.hostname,string="+m.Hostname)
	}
	argv = append(argv,
		"-append", root+" console="+m.console()+" init="+m.Init+
			" systemd.set_credential=login.noauth:yes"+extra+varAppend+extAppend)
	if len(m.Display) > 0 {
		argv = append(argv, m.Display...)
	} else {
		argv = append(argv, "-nographic")
	}
	if !m.Reboot {
		argv = append(argv, "-no-reboot")
	}
	return argv, nil
}

// panicRE is a guest that will never come up: the part of kaetzli.qemu.console's
// QEMU_DIED that a booting guest can print.
var panicRE = regexp.MustCompile(`Kernel panic|Attempted to kill init`)

// Panicked reports whether a console log shows the kernel giving up. Such a guest leaves
// qemu running and its forwarded ports accepting, so waiting for ssh would wait for ever.
func Panicked(console []byte) bool { return panicRE.Match(console) }
