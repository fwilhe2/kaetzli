package main

import (
	"fmt"
	"os"

	"github.com/fwilhe2/kaetzli/go/internal/uki"
)

// cmdUKICmdline is what tools/build-disk.sh runs (not in the usage: a tool's, not a
// user's command), so a disk image's command line is rewritten by the same code `start`
// uses:
//
//	kaetzli uki-cmdline --uki IN.efi                          # print IN's command line
//	kaetzli uki-cmdline --uki IN.efi --set CMDLINE --out OUT  # OUT: IN with CMDLINE
//
// IN must be unsigned: a signature covers the command line, so a signed image would boot
// nowhere once it changed (sign after, as tools/sign-uki.sh does).
func cmdUKICmdline(args []string) {
	var in, out, set string
	haveSet := false
	for i := 0; i < len(args); i++ {
		next := func() string {
			if i+1 >= len(args) {
				die("%s needs a value", args[i])
			}
			i++
			return args[i]
		}
		switch args[i] {
		case "--uki":
			in = next()
		case "--out":
			out = next()
		case "--set":
			set, haveSet = next(), true
		default:
			die("uki-cmdline: unknown option '%s'", args[i])
		}
	}
	if in == "" || haveSet != (out != "") {
		die("uki-cmdline needs --uki, and --set with --out or neither")
	}
	b, err := os.ReadFile(in)
	if err != nil {
		die("%v", err)
	}
	if !haveSet {
		c, err := uki.Cmdline(b)
		if err != nil {
			die("%s: %v", in, err)
		}
		fmt.Println(c)
		return
	}
	nb, err := uki.SetCmdline(b, set)
	if err != nil {
		die("%s: %v", in, err)
	}
	if err := os.WriteFile(out, nb, 0o644); err != nil {
		die("%v", err)
	}
}
