//go:build linux || darwin

package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
)

// detach starts qemu in a session of its own, so closing the terminal that ran `start`
// does not take the guest with it, and returns its pid. qemu's own output goes to log.
func detach(binary string, argv []string, log string) (int, error) {
	out, err := os.OpenFile(log, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		return 0, err
	}
	defer out.Close()
	cmd := exec.Command(binary, argv...)
	cmd.Stdout, cmd.Stderr = out, out
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		return 0, err
	}
	pid := cmd.Process.Pid
	return pid, cmd.Process.Release()
}

func alive(pid int) bool { return syscall.Kill(pid, 0) == nil }

func kill(pid int) { _ = syscall.Kill(pid, syscall.SIGTERM) }

// running is qemu's pid while the machine runs. A pid alone could be reused by anything,
// so on Linux its command line has to name the machine's /var disk.
func running(dir string) (int, bool) {
	b, err := os.ReadFile(filepath.Join(dir, "qemu.pid"))
	if err != nil {
		return 0, false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || !alive(pid) {
		return 0, false
	}
	if cmdline, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/cmdline"); err == nil {
		abs, _ := filepath.Abs(filepath.Join(dir, "var.img"))
		if !strings.Contains(string(cmdline), abs) {
			return 0, false
		}
	}
	return pid, true
}

// rawTerminal puts the terminal in raw mode for the console, and returns what undoes it.
// stty, since the standard library has no termios.
func rawTerminal() func() {
	save := exec.Command("stty", "-g")
	save.Stdin = os.Stdin
	state, err := save.Output()
	if err != nil {
		return func() {}
	}
	raw := exec.Command("stty", "raw", "-echo")
	raw.Stdin = os.Stdin
	_ = raw.Run()
	done := false
	return func() {
		if done {
			return
		}
		done = true
		restore := exec.Command("stty", strings.TrimSpace(string(state)))
		restore.Stdin = os.Stdin
		_ = restore.Run()
	}
}
