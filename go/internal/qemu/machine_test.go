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
