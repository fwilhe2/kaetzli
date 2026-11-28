package main

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/fwilhe2/kaetzli/go/internal/uki"
)

func TestUKICmdlineSetsTheCommandLine(t *testing.T) {
	dir := t.TempDir()
	in, out := filepath.Join(dir, "in.efi"), filepath.Join(dir, "out.efi")
	if err := os.WriteFile(in, testUKI(t, "root=/dev/vda console=ttyS0"), 0o644); err != nil {
		t.Fatal(err)
	}
	want := "root=/dev/vda2 console=tty0 console=ttyS0 kaetzli.user-data=openstack"
	cmdUKICmdline([]string{"--uki", in, "--set", want, "--out", out})
	b, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	got, err := uki.Cmdline(b)
	if err != nil {
		t.Fatal(err)
	}
	if got != want {
		t.Fatalf("command line %q, want %q", got, want)
	}
	if orig, _ := os.ReadFile(in); string(orig) == string(b) {
		t.Fatal("the input was not copied with a new command line")
	}
}
