package qemu

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

func image(t *testing.T) string {
	t.Helper()
	d := t.TempDir()
	for f, c := range map[string]string{
		"rootfs.cmdline":                  `root=/dev/dm-0 dm-mod.waitfor=/dev/vda dm-mod.create="root,,,ro,0 8 verity x"` + "\n",
		"extensions/podman.raw":           "",
		"extensions/podman.dm-verity":     "0 8 verity 1 @DEV@ @DEV@\n",
		"extensions/kubernetes.raw":       "",
		"extensions/kubernetes.dm-verity": "0 9 verity 1 @DEV@ @DEV@\n",
		"confexts/kubernetes.raw":         "",
		"confexts/kubernetes.dm-verity":   "0 1 verity 1 @DEV@ @DEV@\n",
		"var.img.machine-id":              "0123",
	} {
		p := filepath.Join(d, f)
		_ = os.MkdirAll(filepath.Dir(p), 0o755)
		if err := os.WriteFile(p, []byte(c), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return d
}

func TestExtensionsJoinTheRootsTableAfterVar(t *testing.T) {
	d := image(t)
	m := Machine{Arch: "amd64", Mem: "2048", CPUs: "2", Kernel: "k", Rootfs: "r", Init: "/init",
		Cmdline: filepath.Join(d, "rootfs.cmdline"), VarDisk: filepath.Join(d, "var.img"),
		Sysexts: []string{"podman", "kubernetes"}, ExtDir: d}
	argv, err := m.Argv()
	if err != nil {
		t.Fatal(err)
	}
	app := argv[slices.Index(argv, "-append")+1]
	for _, want := range []string{
		"dm-mod.waitfor=/dev/vda,/dev/vdc,/dev/vdd,/dev/vde ",
		`;ext-podman,,,ro,0 8 verity 1 /dev/vdc /dev/vdc;ext-kubernetes,,,ro,0 9 verity 1 /dev/vdd /dev/vdd;conf-kubernetes,,,ro,0 1 verity 1 /dev/vde /dev/vde"`,
		"systemd.mount-extra=/dev/vdb:/var:ext4:x-systemd.makefs,x-systemd.growfs systemd.machine_id=0123",
		"systemd.mount-extra=/dev/mapper/conf-kubernetes:/run/confexts/kubernetes:erofs:ro",
	} {
		if !strings.Contains(app, want) {
			t.Errorf("-append lacks %q:\n%s", want, app)
		}
	}
	if argv[len(argv)-1] != "-no-reboot" || argv[len(argv)-2] != "-nographic" {
		t.Errorf("the tail is %v", argv[len(argv)-2:])
	}
}

func TestAMissingExtensionIsAnError(t *testing.T) {
	d := image(t)
	m := Machine{Cmdline: filepath.Join(d, "rootfs.cmdline"), Sysexts: []string{"nope"}, ExtDir: d}
	if _, err := m.Argv(); err == nil {
		t.Fatal("no error for a missing extension")
	}
}

func TestPanickedSeesAKernelThatGaveUp(t *testing.T) {
	for _, log := range []string{
		"[    0.746561] ---[ end Kernel panic - not syncing: VFS: Unable to mount root fs ]---",
		"[    1.2] Attempted to kill init! exitcode=0x00000100",
	} {
		if !Panicked([]byte(log)) {
			t.Errorf("Panicked(%q) = false", log)
		}
	}
	if Panicked([]byte("kaetzli login: root\nWelcome")) {
		t.Error("a healthy console is not a panic")
	}
}

func secureBoot(t *testing.T, d string) Machine {
	t.Helper()
	return Machine{Arch: "amd64", KVM: true, Mem: "2048", CPUs: "2", Kernel: "unused", Rootfs: "r",
		Init: "/init", Cmdline: filepath.Join(d, "rootfs.cmdline"), VarDisk: filepath.Join(d, "var.img"),
		PubKey: []byte("ssh-ed25519 AAAA\n"), PubKeyFile: "/m/ssh/id_ed25519.pub", Hostname: "dev",
		SecureBoot: &SecureBoot{UKI: "/m/kaetzli.efi", Code: "/fw/code.fd", Vars: "/m/ovmf-vars.fd"}}
}

func after(argv []string, flag string) []string {
	var out []string
	for i, a := range argv {
		if a == flag && i+1 < len(argv) {
			out = append(out, argv[i+1])
		}
	}
	return out
}

// The UKI's stub ignores -append, so what the guest needs goes by fw_cfg, as kaetzli.qemu
// passes it (python/tests/test_launcher.py holds the two equal).
func TestSecureBootBootsTheSignedUKIUnderSMMFirmware(t *testing.T) {
	d := image(t)
	argv, err := secureBoot(t, d).Argv()
	if err != nil {
		t.Fatal(err)
	}
	if k := after(argv, "-kernel"); len(k) != 1 || k[0] != "/m/kaetzli.efi" {
		t.Fatalf("-kernel %v", k)
	}
	if !slices.Contains(after(argv, "-machine"), "q35,smm=on") ||
		!slices.Contains(after(argv, "-global"), "driver=cfi.pflash01,property=secure,value=on") {
		t.Fatalf("no q35 with SMM and secure flash: %v", argv)
	}
	drives := after(argv, "-drive")
	for _, want := range []string{"if=pflash,format=raw,unit=0,file=/fw/code.fd,readonly=on",
		"if=pflash,format=raw,unit=1,file=/m/ovmf-vars.fd"} {
		if !slices.Contains(drives, want) {
			t.Errorf("no -drive %s", want)
		}
	}
	fw := after(argv, "-fw_cfg")
	for _, want := range []string{
		"name=opt/io.systemd.credentials/login.noauth,string=yes",
		"name=opt/io.systemd.credentials/fstab.extra,file=" + filepath.Join(d, "var.img.fstab"),
		"name=opt/io.systemd.credentials/system.machine_id,file=" + filepath.Join(d, "var.img.machine-id"),
		"name=opt/io.systemd.credentials/ssh.authorized_keys.root,file=/m/ssh/id_ed25519.pub",
		"name=opt/io.systemd.credentials/system.hostname,string=dev",
	} {
		if !slices.Contains(fw, want) {
			t.Errorf("no -fw_cfg %s in %v", want, fw)
		}
	}
	b, err := os.ReadFile(filepath.Join(d, "var.img.fstab"))
	if err != nil || string(b) != "/dev/vdb /var ext4 x-systemd.makefs,x-systemd.growfs 0 0\n" {
		t.Errorf("var.img.fstab = %q, %v", b, err)
	}
	if app := after(argv, "-append")[0]; strings.Contains(app, "/var") {
		t.Errorf("-append mounts /var, which a boot with Secure Boot off would do twice: %s", app)
	}
}

func TestSecureBootRefusesArm64(t *testing.T) {
	arm := secureBoot(t, image(t))
	arm.Arch = "arm64"
	if _, err := arm.Argv(); err == nil {
		t.Error("no error")
	}
}

// Under Secure Boot the extensions' tables go into the UKI's own command line, which the
// launcher signs: the same merge as -append's, onto the UKI's command line, and the disks
// as without Secure Boot.
func TestSecureBootSealsTheExtensionsIntoTheUKIsCommandLine(t *testing.T) {
	d := image(t)
	m := secureBoot(t, d)
	m.Sysexts, m.ExtDir = []string{"podman", "kubernetes"}, d
	ukiCmdline := `root=/dev/dm-0 dm-mod.waitfor=/dev/vda dm-mod.create="root,,,ro,0 8 verity x" console=ttyS0`
	got, err := m.SealedCmdline(ukiCmdline)
	if err != nil {
		t.Fatal(err)
	}
	want := `root=/dev/dm-0 dm-mod.waitfor=/dev/vda,/dev/vdc,/dev/vdd,/dev/vde dm-mod.create="root,,,ro,0 8 verity x` +
		`;ext-podman,,,ro,0 8 verity 1 /dev/vdc /dev/vdc;ext-kubernetes,,,ro,0 9 verity 1 /dev/vdd /dev/vdd;conf-kubernetes,,,ro,0 1 verity 1 /dev/vde /dev/vde"` +
		` console=ttyS0` +
		` systemd.mount-extra=/dev/mapper/ext-podman:/run/extensions/podman:erofs:ro` +
		` systemd.mount-extra=/dev/mapper/ext-kubernetes:/run/extensions/kubernetes:erofs:ro` +
		` systemd.mount-extra=/dev/mapper/conf-kubernetes:/run/confexts/kubernetes:erofs:ro`
	if got != want {
		t.Fatalf("SealedCmdline:\n got %s\nwant %s", got, want)
	}
	argv, err := m.Argv()
	if err != nil {
		t.Fatal(err)
	}
	drives := after(argv, "-drive")
	for _, id := range []string{"ext-podman", "ext-kubernetes", "conf-kubernetes"} {
		if !slices.ContainsFunc(drives, func(dr string) bool { return strings.HasSuffix(dr, ",id="+id) }) {
			t.Errorf("no disk for %s", id)
		}
	}
	// Without extensions the UKI's command line is left as it is.
	plain := secureBoot(t, d)
	if same, _ := plain.SealedCmdline(ukiCmdline); same != ukiCmdline {
		t.Errorf("no extensions changed it: %s", same)
	}
}
